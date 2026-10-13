#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""camera_lifecycle: time-multiplexed open -> settle -> capture -> close for
the wrist and front cameras, with every attempt kept as evidence.

Why this exists (autonomous-single-cube-plan.md section 6): concurrent
streaming from both cameras was operator-reported as unstable on this rig, so
the design evaluates time-multiplexing first, under the standing invariant
"at most one camera stream open at a time". CameraMultiplexer enforces that
invariant in software and records what each open/capture/close cycle did, so
repeated cycles on real hardware produce comparable numbers (open success,
frames delivered against reads that returned nothing, frame validity,
stale-frame suspicion, close success, switching latency). A cycle that ends
without a valid frame always carries an error text, also when nothing raised:
a camera that opens and then delivers nothing is a failed cycle, not a quiet
one.

Why it is separate from the motor host: in the stock hardware host every
configured camera is opened when the robot connects and closed only when the
robot disconnects, and that same disconnect disables torque. A camera that
the motor host owns therefore cannot be closed while the arm is holding
something. This module never touches the motor host: a camera is owned only
for the duration of one capture() call, by whichever process runs it.

Stable identity: a camera is named by role ("wrist", "front") and resolved to
a stable device identity (a /dev/v4l/by-id path or equivalent) from the
environment or an ignored local config. /dev/videoN and bare indices are
rejected because they do not survive re-enumeration. There is deliberately no
built-in default identity, and records carry a fingerprint of the identity,
never the identity itself (device serials are private).

What the offline tests of this module do NOT establish: USB stability, real
switching latency, re-enumeration behavior, exposure settling or whether a
frame is useful. Those need repeated cycles on the real cameras.
"""
import hashlib
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

import numpy as np

ROLES = ("wrist", "front")
IDENTITY_ENV_VAR = {"wrist": "KC_WRIST_CAMERA_ID", "front": "KC_FRONT_CAMERA_ID"}
DEFAULT_SETTLE_FRAMES = 6

_UNSTABLE_DEVICE = re.compile(r"^(/dev/video\d+|\d+)$")


class InvalidCameraIdentity(ValueError):
    pass


class CameraIdentityUnavailable(RuntimeError):
    pass


class CameraBusy(RuntimeError):
    """A capture was requested while another camera is open. The
    one-stream-at-a-time invariant is never relaxed silently."""


@dataclass(frozen=True)
class CameraIdentity:
    role: str
    stable_id: str

    def __post_init__(self):
        if self.role not in ROLES:
            raise InvalidCameraIdentity(f"unknown camera role {self.role!r}; expected one of {ROLES}")
        if not isinstance(self.stable_id, str) or not self.stable_id.strip():
            raise InvalidCameraIdentity(f"{self.role}: a stable device identity is required, got {self.stable_id!r}")
        if _UNSTABLE_DEVICE.match(self.stable_id.strip()):
            raise InvalidCameraIdentity(
                f"{self.role}: {self.stable_id!r} is a /dev/videoN name or bare index, which does not survive "
                "re-enumeration; use a /dev/v4l/by-id path or another stable identity"
            )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.role}:{self.stable_id}".encode()).hexdigest()[:12]


def resolve_camera_identity(role: str, env: Optional[dict] = None, local_config: Optional[dict] = None) -> CameraIdentity:
    """Environment first, then the ignored local config mapping role ->
    stable identity. Never falls back to a built-in device."""
    env = os.environ if env is None else env
    variable = IDENTITY_ENV_VAR.get(role)
    if variable is None:
        raise InvalidCameraIdentity(f"unknown camera role {role!r}; expected one of {ROLES}")
    stable_id = env.get(variable) or (local_config or {}).get(role)
    if not stable_id:
        raise CameraIdentityUnavailable(
            f"no stable identity configured for the {role} camera: set {variable} or add {role!r} to the "
            "ignored local camera config (never a tracked file)"
        )
    return CameraIdentity(role, stable_id)


@dataclass
class LifecycleRecord:
    role: str
    identity_fingerprint: str
    settle_frames_requested: int
    switched_from: Optional[str] = None
    since_previous_close_s: Optional[float] = None
    open_ok: bool = False
    open_latency_s: Optional[float] = None
    frames_read: int = 0  # reads that DELIVERED a frame, settle frames included; never the number of read() calls
    reads_without_frame: int = 0  # reads that returned nothing: how long the stream took to start, or that it never did
    frame_valid: bool = False
    frame_shape: Optional[list] = None
    stale_suspect: bool = False
    close_ok: bool = False
    total_latency_s: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CaptureResult:
    frame: Optional[np.ndarray]
    record: LifecycleRecord


def _is_valid_frame(frame) -> bool:
    return isinstance(frame, np.ndarray) and frame.ndim == 3 and frame.size > 0


_ANY_DEVICE_PATH = re.compile(r"/dev/\S+")


def _describe(error: BaseException, identity: CameraIdentity) -> str:
    """Error text for a record. Driver and OS messages often quote the device
    path, and a record must never carry one (device serials are private), so
    the identity is replaced by its fingerprint and any other /dev path is
    masked."""
    text = f"{type(error).__name__}: {error}".replace(identity.stable_id, f"<{identity.role}:{identity.fingerprint}>")
    return _ANY_DEVICE_PATH.sub("<device>", text)


@dataclass
class CameraMultiplexer:
    """backends: role -> object with open(identity) / read() -> frame or None
    / close(). identities: role -> CameraIdentity."""

    backends: dict
    identities: dict
    clock: Callable[[], float] = time.monotonic
    history: list = field(default_factory=list)
    open_role: Optional[str] = None
    _last_role: Optional[str] = None
    _last_close_time: Optional[float] = None
    _last_digest: dict = field(default_factory=dict)

    def capture(self, role: str, settle_frames: int = DEFAULT_SETTLE_FRAMES) -> CaptureResult:
        backend = self.backends[role]
        identity = self.identities[role]
        if settle_frames < 0:
            raise ValueError("settle_frames must be >= 0")
        if self.open_role is not None:
            raise CameraBusy(f"cannot open {role}: {self.open_role} is still open (one stream at a time)")

        record = LifecycleRecord(
            role=role,
            identity_fingerprint=identity.fingerprint,
            settle_frames_requested=settle_frames,
            switched_from=self._last_role,
        )
        errors = []
        frame = None
        capture_raised = False
        started = self.clock()
        if self._last_close_time is not None:
            record.since_previous_close_s = started - self._last_close_time

        self.open_role = role
        try:
            try:
                backend.open(identity)
                record.open_ok = True
                record.open_latency_s = self.clock() - started
                for _ in range(settle_frames + 1):
                    frame = backend.read()
                    if frame is None:
                        record.reads_without_frame += 1
                    else:
                        record.frames_read += 1
            except Exception as error:  # noqa: BLE001 -- a failed cycle is evidence, not a crash
                errors.append(_describe(error, identity))
                frame = None
                capture_raised = True
        finally:
            try:
                backend.close()
                record.close_ok = True
            except Exception as error:  # noqa: BLE001
                errors.append(_describe(error, identity))
            self.open_role = None
            closed = self.clock()
            record.total_latency_s = closed - started
            self._last_close_time = closed
            self._last_role = role

        if _is_valid_frame(frame):
            record.frame_valid = True
            record.frame_shape = list(frame.shape)
            digest = hashlib.sha1(np.ascontiguousarray(frame).tobytes()).hexdigest()
            record.stale_suspect = self._last_digest.get(role) == digest
            self._last_digest[role] = digest
        else:
            # A camera that opens and then delivers nothing raises nothing, and
            # that is the usual way a USB camera fails under load. Without this
            # the record would say error=None for the very failure under study.
            if not capture_raised:
                reads = record.frames_read + record.reads_without_frame
                malformed = "" if frame is None else "; the kept frame is malformed (not a non-empty 3-dimensional array)"
                errors.append(f"no valid frame: {record.reads_without_frame} of {reads} reads returned no frame{malformed}")
            frame = None

        record.error = "; ".join(errors) if errors else None
        self.history.append(record)
        return CaptureResult(frame=frame, record=record)


# OpenCV constants, spelled out so this module (and its tests) never need to
# import cv2 just to name them: CAP_V4L2, CAP_PROP_FRAME_WIDTH/HEIGHT/FOURCC.
_CAP_V4L2 = 200
_PROP_WIDTH, _PROP_HEIGHT, _PROP_FOURCC = 3, 4, 6
_FOURCC_MJPG = ord("M") | (ord("J") << 8) | (ord("P") << 16) | (ord("G") << 24)


class OpenCvBackend:
    """Direct V4L2 capture through OpenCV, for the process that can see the
    device (the Pi). Unrotated: any calibration made from these frames applies
    to this orientation and to the delivered resolution only.

    The resolution is REQUESTED, not guaranteed. MJPG, width and height are
    set after the device is already open, and whether these cameras honor a
    format change at that point is UNVERIFIED on hardware; OpenCV is known to
    fall back to a driver default without saying so, and the return value of
    set() is not a reliable signal, so it is not consulted. What is enforced
    instead is the result: read() raises when a delivered frame is not
    height x width as requested, which the multiplexer records as the error of
    that cycle, so a session can never be shot at the wrong resolution with a
    clean record. Whether MJPG itself took effect is not observable from a
    decoded frame and is not checked.
    UNVALIDATED ON HARDWARE as of 2026-09-19."""

    def __init__(self, width: int, height: int, video_capture_factory=None):
        self.width, self.height = width, height
        self._factory = video_capture_factory
        self._capture = None

    def open(self, identity: CameraIdentity) -> None:
        factory = self._factory
        if factory is None:
            import cv2

            factory = cv2.VideoCapture
        capture = factory(identity.stable_id, _CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            raise OSError(f"could not open the {identity.role} camera by its stable identity")
        try:
            capture.set(_PROP_FOURCC, _FOURCC_MJPG)
            capture.set(_PROP_WIDTH, self.width)
            capture.set(_PROP_HEIGHT, self.height)
        except BaseException:
            capture.release()  # not yet owned by close(): without this the device would stay held
            raise
        self._capture = capture

    def read(self):
        if self._capture is None:
            raise OSError("camera backend is not open; read() is only valid between open() and close()")
        ok, frame = self._capture.read()
        if not ok:
            return None
        if isinstance(frame, np.ndarray) and frame.ndim >= 2 and frame.shape[:2] != (self.height, self.width):
            raise OSError(f"delivered frame {frame.shape[1]}x{frame.shape[0]} differs from the requested {self.width}x{self.height}")
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
