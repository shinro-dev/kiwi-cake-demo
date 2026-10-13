#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""camera_lock: one advisory lock per camera ROLE, shared by every process on
the Pi that may open a camera, so that two capture providers can never own the
same camera at the same time.

Why this exists: there are two ways to take a frame on the Pi. The legacy one
(bin/pi/camera_multiplex_probe.py, one open -> settle -> capture -> close per
frame) and the camera service (bin/pi/camera_service.py, one long-lived owner).
A V4L2 device can be opened twice and only the second STREAMON fails, late and
with a driver message; which of the two processes loses depends on timing. The
lock decides it before any device is touched, and the loser can say who holds
the camera.

What it is: flock(2) on a file under the user's runtime directory, one file per
role ("wrist", "front"). The kernel drops a flock when the holder's descriptor
closes, also when the holder is killed, so a crashed service never leaves a
camera locked. The lock is ADVISORY: it excludes the processes that take it and
nobody else. A program that opens the device without asking (a camera viewer, a
motor host configured with cameras) is not stopped by it.

Both sides MUST resolve the same directory, or the lock excludes nothing. The
order is fixed and the source is reported (never the path) so a qualification
run can compare the two processes:

  1. KC_CAMERA_LOCK_DIR           explicit override     ("env_override")
  2. XDG_RUNTIME_DIR              the session's runtime ("xdg_runtime_dir")
  3. /run/user/<uid>              when it exists        ("run_user")
  4. <tmp>/kc-camera-lock-<uid>   last resort           ("tmp_fallback")

A runtime directory that belongs to a login session is removed when the last
session of the user ends. A service that outlives its session would then hold a
lock on a file nobody can reach any more, and a later probe would lock a NEW
file of the same name without conflict. CameraLock.verify() detects exactly
that (the path no longer names the locked file) and the service refuses mission
captures when it fails. Run the service where the runtime directory outlives
it: a user service, or a user with lingering enabled.

The holder record in the lock file names a label, a process id and the role. It
never carries a device identity: device serials are private.

The lock file is never unlinked. Removing a flock file while another process
waits on it splits the lock in two (one holder per inode); an empty file of a
few bytes per role is the price of not having that race.

Standard library only. Linux only (fcntl).
"""
import fcntl
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

LOCK_DIR_ENV_VAR = "KC_CAMERA_LOCK_DIR"
LOCK_SUBDIR = "kc-camera-lock"
DEFAULT_WAIT_S = 2.0
DEFAULT_POLL_S = 0.05

SOURCE_OVERRIDE, SOURCE_XDG, SOURCE_RUN_USER, SOURCE_TMP = "env_override", "xdg_runtime_dir", "run_user", "tmp_fallback"

_ROLE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_HOLDER = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_MAX_RECORD_BYTES = 4096


class CameraLockError(RuntimeError):
    """The lock cannot be used at all (no usable directory, a directory that is
    not ours). Callers fail closed on it exactly as on CameraLockHeld."""


class CameraLockHeld(CameraLockError):
    """Another process holds the lock of this role. .holder is its record, or
    None when the record could not be read."""

    def __init__(self, role: str, holder: Optional[dict], waited_s: float):
        self.role, self.holder, self.waited_s = role, holder, waited_s
        super().__init__(f"the {role} camera is owned by {describe_holder(holder)}; waited {waited_s:.2f} s for it. "
                         "Switching capture provider is an explicit stop of the owner, never implicit")


class CameraLockLost(CameraLockError):
    """The lock file this process holds is no longer the file at the lock path,
    so the lock no longer excludes anybody."""


def describe_holder(holder: Optional[dict]) -> str:
    if not holder:
        return "another process (its holder record could not be read)"
    return f"{holder.get('holder', 'unknown')} (pid {holder.get('pid', 'unknown')})"


def resolve_lock_dir(env: Optional[dict] = None) -> tuple:
    """(directory, source). The directory is created (mode 0700) and checked.
    The source is what may be reported; the path is not."""
    env = os.environ if env is None else env
    uid = os.getuid()
    override, xdg, run_user = env.get(LOCK_DIR_ENV_VAR), env.get("XDG_RUNTIME_DIR"), f"/run/user/{uid}"
    if override:
        base, source = Path(override), SOURCE_OVERRIDE
    elif xdg and os.path.isdir(xdg):
        base, source = Path(xdg), SOURCE_XDG
    elif os.path.isdir(run_user) and os.access(run_user, os.W_OK):
        base, source = Path(run_user), SOURCE_RUN_USER
    else:
        base, source = Path(tempfile.gettempdir()), SOURCE_TMP
    directory = base / (LOCK_SUBDIR if source != SOURCE_TMP else f"{LOCK_SUBDIR}-{uid}")
    try:
        directory.mkdir(mode=0o700, parents=(source == SOURCE_OVERRIDE), exist_ok=True)
        info = os.lstat(directory)
    except OSError as error:
        raise CameraLockError(f"the camera lock directory ({source}) cannot be created: {type(error).__name__}") from None
    # A directory somebody else made under a shared tmp could be swapped under us: refuse it instead of locking in it.
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or (info.st_mode & 0o022):
        raise CameraLockError(f"the camera lock directory ({source}) is not a private directory of this user")
    return directory, source


def _lock_path(role: str, directory: Path) -> Path:
    if not isinstance(role, str) or not _ROLE.fullmatch(role):
        raise CameraLockError(f"camera lock role must be a short lower-case name, got {role!r}")
    return directory / f"{role}.lock"


def _read_record(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as handle:
            record = json.loads(handle.read(_MAX_RECORD_BYTES).decode("utf-8"))
    except (OSError, ValueError):
        return None  # missing, empty (free, or the holder is between truncate and write) or not ours
    return record if isinstance(record, dict) else None


class CameraLock:
    """The lock of ONE role. acquire() is non-blocking with a short bounded
    wait; it never blocks for longer than wait_s. Not re-entrant: two CameraLock
    objects of the same role exclude each other also inside one process, which
    is what lets a test (and a process that made a mistake) see the refusal."""

    def __init__(self, role: str, holder: str, *, directory: Optional[Path] = None, env: Optional[dict] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        if not isinstance(holder, str) or not _HOLDER.fullmatch(holder):
            raise CameraLockError("camera lock holder label must match [A-Za-z0-9_.-]{1,64}")
        self.role, self.holder = role, holder
        if directory is None:
            self.directory, self.directory_source = resolve_lock_dir(env)
        else:
            self.directory, self.directory_source = Path(directory), "explicit"
        self.path = _lock_path(role, self.directory)
        self._clock, self._sleep = clock, sleep
        self._fd: Optional[int] = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, wait_s: float = DEFAULT_WAIT_S, poll_s: float = DEFAULT_POLL_S) -> "CameraLock":
        if self._fd is not None:
            raise CameraLockError(f"this process already holds the {self.role} camera lock through this object")
        if wait_s < 0 or poll_s <= 0:
            raise ValueError("wait_s must be >= 0 and poll_s > 0")
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise CameraLockError(f"the {self.role} camera lock file cannot be opened: {type(error).__name__}") from None
        started = self._clock()
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    waited = self._clock() - started
                    if waited >= wait_s:
                        raise CameraLockHeld(self.role, _read_record(self.path), waited) from None
                    self._sleep(min(poll_s, wait_s - waited))
            record = {"holder": self.holder, "pid": os.getpid(), "role": self.role,
                      "acquired_wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            os.ftruncate(fd, 0)
            os.pwrite(fd, (json.dumps(record) + "\n").encode("utf-8"), 0)
        except BaseException:
            os.close(fd)  # closing drops the flock when it was taken
            raise
        self._fd = fd
        return self

    def verify(self) -> None:
        """Raises CameraLockLost unless the lock path still names the file this
        object locked. See the module text: a runtime directory that was
        removed and made again under a living holder."""
        if self._fd is None:
            raise CameraLockLost(f"the {self.role} camera lock is not held")
        try:
            mine, there = os.fstat(self._fd), os.stat(self.path)
        except OSError:
            raise CameraLockLost(f"the {self.role} camera lock file is gone from its directory; the lock excludes nobody") from None
        if (mine.st_dev, mine.st_ino) != (there.st_dev, there.st_ino):
            raise CameraLockLost(f"the {self.role} camera lock file was replaced; the lock excludes nobody")

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.ftruncate(fd, 0)  # while still locked: a reader sees either our record or an empty file
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self) -> "CameraLock":
        return self if self.held else self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()


def lock_status(roles, *, directory: Optional[Path] = None, env: Optional[dict] = None) -> dict:
    """role -> {"held": bool, "holder": record or None}, plus "directory_source".
    It probes with a non-blocking flock that is dropped at once, so for an
    instant it IS a holder: an acquirer that polls (every acquire() here does)
    is not disturbed by it."""
    if directory is None:
        directory, source = resolve_lock_dir(env)
    else:
        directory, source = Path(directory), "explicit"
    status = {"directory_source": source, "roles": {}}
    for role in roles:
        path = _lock_path(role, directory)
        held, holder = False, None
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        except FileNotFoundError:
            fd = None  # never locked by anybody
        except OSError as error:
            raise CameraLockError(f"the {role} camera lock file cannot be opened: {type(error).__name__}") from None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held, holder = True, _read_record(path)
            finally:
                os.close(fd)
        status["roles"][role] = {"held": held, "holder": holder}
    return status
