# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api.security: the per-launch credential and the guard every mutating request passes.

THE THREAT. A page on any other origin can make the browser send a POST to a loopback port. It cannot read the answer
without CORS headers (none are ever sent here), but a blind POST would still be a command. Four independent things must
all hold before a POST reaches the runtime, and a cross-origin page can produce none of them:

  Host       equals the address this listener is actually bound to (the socket's own sockname, not a configured guess),
             which is what stops a name that resolves to the loopback from passing as this address;
  Origin     equals http://<that address>: a page on another origin sends its own, and a form cannot forge one;
  Content-Type  application/json: a form, an image or a beacon cannot set it, so no simple cross-origin request qualifies;
  a custom header, which requires a CORS preflight that this server answers for nobody;
  the per-launch credential, which lives only in memory, in one 0600 file outside the repository, and in the index.html
             this server hands to a page it served itself.

THE CREDENTIAL IS NEVER LOGGED, never put in a URL (a URL reaches access logs and the browser's history) and never in a
JSON body. It travels in a request header and, once, in the served index.html. The file is the only way a command line
client can obtain it.

WHO OWNS THE CREDENTIAL. The RUNTIME SESSION does, not this process. It used to be minted per launch, which meant that a
tab left open across an API restart went on reading happily and had every POST answered 403 until somebody reloaded it:
the page was still correct, the runtime was still running, the controller was still driving the robot, and the one thing
that had changed was a random string in a process that owns none of it. CredentialStore keeps one credential per runtime
session, in a 0600 file in that runtime's own private directory, created atomically by whichever API gets there first;
an API that attaches to the same session reads the same value back and the open tab keeps commanding.

A DIFFERENT SESSION IS A DIFFERENT CREDENTIAL, deliberately. When the runtime is replaced the API binds to the new
session's credential and the previous one stops being accepted, so a tab cannot carry on commanding a runtime it never
did readiness against. It is refused by its own code, CREDENTIAL_SUPERSEDED, so the console can tell an expired
credential from a genuine refusal, reconcile, and ask for readiness again instead of replaying anything.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Optional

from . import LOOPBACK, repository_root

CONSOLE_HEADER = "X-Console-Request"
CONSOLE_HEADER_VALUE = "1"
CREDENTIAL_HEADER = "X-Console-Credential"
CREDENTIAL_BYTES = 32
SESSION_CREDENTIAL_PREFIX = "console-credential-"
# common.schema.json#/$defs/id. A runtime session names a FILE here, so it is checked against the contract's own shape
# before it is used: a value with a slash or a dot pair in it would name a file somewhere else entirely.
SESSION_SHAPE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

HOST_NOT_ALLOWED = "host_not_allowed"
ORIGIN_NOT_ALLOWED = "origin_not_allowed"
CONTENT_TYPE_NOT_JSON = "content_type_not_json"
CONSOLE_HEADER_MISSING = "console_header_missing"
CREDENTIAL_MISSING = "credential_missing"
CREDENTIAL_INVALID = "credential_invalid"
CREDENTIAL_SUPERSEDED = "credential_superseded"


class CredentialError(RuntimeError):
    """The credential file cannot be placed where it was asked for."""


def mint_credential() -> str:
    """A value that is random from its first character: no prefix, nothing to recognise it by, nothing derived."""
    return secrets.token_urlsafe(CREDENTIAL_BYTES)


def private_base_dir() -> Path:
    """A private per-user directory outside the repository, preferring the session's own runtime directory."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return Path(runtime_dir) / "kiwi-cake-demo"
    return Path(tempfile.gettempdir()) / f"kiwi-cake-demo-{os.getuid()}"


def default_credential_path(port: int) -> Path:
    return private_base_dir() / f"console-api-credential-{port}"


def write_credential(path, credential: str) -> Path:
    """Write the credential to a 0600 file outside the repository. The file is born private: a chmod afterwards would
    leave a window in which another user could open it."""
    target = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    root = repository_root()
    if target == root or root in target.parents:
        raise CredentialError("the credential file must live outside the repository")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = os.umask(0o177)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    finally:
        os.umask(previous)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(credential + "\n")
    os.chmod(target, 0o600)
    return target


def read_credential(path) -> str:
    return Path(os.fspath(path)).read_text(encoding="ascii").strip()


class CredentialStore:
    """The credential of one RUNTIME SESSION, in that runtime's own private directory.

    credential_for() is create-or-read and it is atomic in the way that matters: the value is written to a private
    temporary file in the same directory and fsynced FIRST, and only a complete file is ever linked into place. os.link
    fails when the name already exists, which is exactly the answer wanted, because it means another API process won
    the race and its value is the one to read. Nothing here ever truncates or replaces an existing credential: two APIs
    on the same runtime session must agree on one value, and a second one that overwrote the first would silently
    invalidate the tab the first one had already served.

    The directory is the runtime's data directory. That is where the credential belongs: it lives exactly as long as
    the runtime the browser is talking to, it is already private (0700) and outside the repository, and it goes away
    when that runtime's data directory does.
    """

    def __init__(self, directory):
        self.directory = Path(os.path.abspath(os.path.expanduser(os.fspath(directory))))

    def path_for(self, runtime_session: str) -> Path:
        if not isinstance(runtime_session, str) or SESSION_SHAPE.match(runtime_session) is None:
            raise CredentialError("a runtime session that is not an opaque contract id cannot name a credential file")
        target = self.directory / f"{SESSION_CREDENTIAL_PREFIX}{runtime_session}"
        root = repository_root()
        if target == root or root in target.parents:
            raise CredentialError("the credential file must live outside the repository")
        return target

    @staticmethod
    def _read(path: Path) -> Optional[str]:
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            return None
        return value or None

    def credential_for(self, runtime_session: str) -> str:
        """The credential this runtime session already has, or a new one placed atomically. Never two values."""
        target = self.path_for(runtime_session)
        existing = self._read(target)
        if existing is not None:
            return existing
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.new-{os.getpid()}")
        previous = os.umask(0o177)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        finally:
            os.umask(previous)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(mint_credential() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass  # another API process placed one first; its value is the one both of us must serve
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        placed = self._read(target)
        if placed is None:
            raise CredentialError("the session credential could not be read back after it was placed")
        return placed


def bound_authority(request) -> Optional[str]:
    """The address this connection was actually accepted on, as a Host header spells it. Taken from the socket, so a test
    on an ephemeral port and a launch on the configured port are checked by the same rule."""
    transport = getattr(request, "transport", None)
    sockname = transport.get_extra_info("sockname") if transport is not None else None
    if not isinstance(sockname, tuple) or len(sockname) < 2:
        return None
    return f"{sockname[0]}:{sockname[1]}"


class Guard:
    """The checks, in the order their reason codes are reported. Nothing here consults the runtime: a request that fails
    is refused before the socket is touched at all.

    ONE credential is accepted at a time. rotate() is how the API follows a runtime session it has just learned is a
    different one; the value it replaces is remembered so that the tab still holding it is told CREDENTIAL_SUPERSEDED
    rather than CREDENTIAL_INVALID. Those are two different facts and only one of them means somebody is guessing:
    a superseded credential is one this very server handed out, and the honest answer to it is that the runtime under
    it changed and readiness must be established again, not that the request looked like an attack.

    Superseded values are held in memory only, never written anywhere, and they never open anything: they are compared
    with compare_digest exactly like the live one and the only outcome they can produce is a 403 with a clearer code.
    The list is bounded: a runtime that is restarted in a loop must not grow this process, and a tab holding something
    older than the last few rotations learns it is out of date from CREDENTIAL_INVALID instead, which is still true."""

    SUPERSEDED_KEPT = 8

    def __init__(self, credential: str, *, host: str = LOOPBACK, port: int = 0):
        self.credential, self.host, self.port = credential, host, port
        self._superseded: list = []
        self._lock = threading.Lock()

    def rotate(self, credential: str) -> bool:
        """Serve and accept `credential` from now on. True when the value really changed."""
        if not isinstance(credential, str) or credential == "":
            return False
        with self._lock:
            if hmac.compare_digest(credential, self.credential):
                return False
            self._superseded = [*self._superseded, self.credential][-self.SUPERSEDED_KEPT:]
            self.credential = credential
            return True

    def _was_served(self, presented: str) -> bool:
        with self._lock:
            superseded = tuple(self._superseded)
        for value in superseded:
            try:
                if hmac.compare_digest(presented, value):
                    return True
            except TypeError:  # a header that is not ASCII was never one of ours
                return False
        return False

    def expected_authority(self, request) -> str:
        return bound_authority(request) or f"{self.host}:{self.port}"

    def expected_origin(self, request) -> str:
        return f"http://{self.expected_authority(request)}"

    def check_host(self, request) -> Optional[str]:
        """Every request, read or write. A page that reached this port under another name sends that name here."""
        return None if request.headers.get("Host") == self.expected_authority(request) else HOST_NOT_ALLOWED

    def check_command(self, request) -> Optional[str]:
        reason = self.check_host(request)
        if reason is not None:
            return reason
        if request.headers.get("Origin") != self.expected_origin(request):
            return ORIGIN_NOT_ALLOWED
        media_type = (request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if media_type != "application/json":
            return CONTENT_TYPE_NOT_JSON
        if request.headers.get(CONSOLE_HEADER) != CONSOLE_HEADER_VALUE:
            return CONSOLE_HEADER_MISSING
        presented = request.headers.get(CREDENTIAL_HEADER)
        if not presented:
            return CREDENTIAL_MISSING
        try:
            matched = hmac.compare_digest(presented, self.credential)
        except TypeError:  # a header that is not ASCII cannot be the credential, which is
            matched = False
        if matched:
            return None
        return CREDENTIAL_SUPERSEDED if self._was_served(presented) else CREDENTIAL_INVALID
