#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""camera_service: the single camera capture owner, run ON THE PI.

One process opens each enabled camera ONCE, by stable identity, and serves two
consumers from the same stream:

  MISSION CAPTURE  request/reply (REP, 127.0.0.1): one full-resolution frame
                   per request, published atomically under a unique name, with
                   metadata. This is the evidence path and it has PRIORITY.
  PREVIEW TAP      publish (PUB, 127.0.0.1): a downscaled JPEG of the latest
                   frame at a few frames per second. Latest-only. It is a
                   picture for a human and never evidence.

Both sockets bind 127.0.0.1 and nothing else; the laptop reaches them through
an ssh forward. See docs/autonomous-demo/camera-service.md for the procedure
that must pass before this provider replaces the legacy one.

SAME FRAME AS THE LEGACY PATH. The device is opened by the very code the legacy
path uses (camera_lifecycle.OpenCvBackend: V4L2, MJPG requested, 1280x720
requested, a delivered frame of another size raises), the frame is unrotated
and uncropped, the default settle count is the legacy one and the image is a
PNG. What differs is that the stream stays open between captures.

CAPTURED AFTER THE REQUEST is decided by capture timestamps, never by read
order: a read can return a frame that was buffered before the request.
  * When the capture API gives a trustworthy driver buffer timestamp, a frame
    counts only when that timestamp is later than the request time plus an
    exposure guard, and the returned frame is the first one after the settle
    frames, all in ONE clock domain (the Pi's CLOCK_MONOTONIC).
    The property is CAP_PROP_POS_MSEC of OpenCV's V4L2 backend, which returns
    the timestamp of the dequeued v4l2 buffer. For a UVC camera the kernel
    driver fills it from CLOCK_MONOTONIC and flags it "start of exposure"; with
    hardware timestamps off (the kernel default) the value is the host time at
    which the first packet of the frame arrived, so it marks the START OF FRAME
    RECEPTION and the exposure began up to one frame period earlier. That is
    what the exposure guard (one source frame period) is for. OpenCV exposes
    neither the clock flag nor the source flag, so neither is taken on faith:
    a stream's timestamps are used only while every frame's timestamp is
    strictly increasing and lies within a few seconds BEFORE its own dequeue
    time on time.monotonic_ns(). A timestamp from another clock fails that test.
  * Otherwise the metadata says timestamp_semantics "dequeue_time_only" and the
    service drains the driver buffer depth plus the settle count before it
    keeps a frame. That is WEAKER: it rests on the buffer depth being what the
    API reports (or the documented default), not on a measurement.
Timestamps describe capture. Nothing here stamps publication or transfer.

THE PREVIEW CANNOT DELAY A MISSION CAPTURE. The capture thread hands each frame
to the preview through a one-slot holder (a reference swap; an unread frame is
overwritten and counted, never queued). Scaling, JPEG encoding and the socket
send happen on the preview thread, which also stands aside while a mission
capture is in flight. A preview consumer that stalls can only stall previews.

A GAP IN THE PREVIEW SAYS WHY. Standing aside publishes no picture for as long
as the capture takes: the stale and settle frames at the source rate plus the
PNG encoding and the two fsyncs of the publication. A reader that hears nothing
for its freshness window (2 s in demo_runtime.sources) cannot tell that from a
dead feed. So every preview header carries the service's counters (published,
skipped_for_mission, dropped_at_socket: as of that message, not counting it),
and a stand-aside that lasts YIELD_NOTICE_AFTER_S is announced by a picture-less
yield notice (schema kc.camera_service.preview_yield/1) once every
YIELD_NOTICE_INTERVAL_S until the previews resume. A short stand-aside, which is
the ordinary capture, sends nothing extra. A notice is sent only when a frame
arrived from the camera, so a camera that delivers nothing stays silent and
reads as a stall. A notice consumes no preview number.
A preview of 2 to 5 fps does NOT reduce USB traffic: the SOURCE frame rate
does. The negotiated pixel format, resolution and source frame rate (reported
and measured) are in the status and in every frame's metadata.

SHARED LOCK. The service holds the per-role camera lock
(autonomy/perception/camera_lock.py) from before it opens a device until it is
stopped, also when that camera has failed: handing a camera to the legacy path
is an explicit stop of this service, never a side effect, never during a run.

THE FRONT CAMERA IS OFF unless the service is started with --enable-front. Off
means: its identity is not read, its lock is not taken, its device is never
opened, and a capture request for it is refused.

No credential: the sockets accept any process on the Pi's loopback. Identities
come only from the environment (KC_WRIST_CAMERA_ID, KC_FRONT_CAMERA_ID), never
a /dev/videoN name, and no output carries one: fingerprints only.

Never imports the robot package, never opens the motor bus.

NEVER RUN ON HARDWARE as of 2026-09-20. The offline tests prove the selection
rule, the publication, the lock and the isolation of the preview against a fake
capture device. They do not establish what CAP_PROP_POS_MSEC returns on these
cameras, that a source frame rate request is honored, or USB behavior.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))

from camera_lifecycle import (  # noqa: E402
    DEFAULT_SETTLE_FRAMES,
    ROLES,
    CameraIdentity,
    CameraIdentityUnavailable,
    InvalidCameraIdentity,
    LifecycleRecord,
    OpenCvBackend,
    resolve_camera_identity,
)
from camera_lifecycle import _describe as describe_error  # noqa: E402 -- the ONE rule that keeps device paths out of error text
from camera_lock import DEFAULT_WAIT_S, CameraLock, CameraLockError, lock_status  # noqa: E402

LOCK_HOLDER = "camera_service"
PROVIDER = "camera_service"
FRAME_SCHEMA = "kc.camera_service.frame/1"
PREVIEW_SCHEMA = "kc.camera_service.preview/1"
PREVIEW_YIELD_SCHEMA = "kc.camera_service.preview_yield/1"  # a picture-less notice: the preview stands aside for a mission capture
PREVIEW_MAGIC = b"KCPV1"
# A stand-aside is announced once it has lasted this long, and then once per interval. An ordinary capture (about
# 0.45 s at the measured 19.9 fps: 2 stale and 6 settle frames, docs/autonomous-demo/camera-service.md) stays below the
# bound and sends nothing; the first notice lands well inside the reader's 2 s freshness window.
YIELD_NOTICE_AFTER_S = 1.0
YIELD_NOTICE_INTERVAL_S = 0.5

BIND_HOST = "127.0.0.1"  # not an option: the service is reachable from the Pi's loopback only
DEFAULT_MISSION_PORT = 5570
# One PUB socket per role, and each role's port WRITTEN OUT. A latest-only subscriber keeps one message per socket, so the
# two roles must never share one: a base port plus an offset made an explicitly given port collapse both roles onto the
# same socket, which would have served the wrist picture under the front camera's name. Mirrored in demo_runtime.forwards.
DEFAULT_PREVIEW_PORTS = {"wrist": 5571, "front": 5572}

CLOCK_PI = "pi_monotonic"  # packages/demo-contracts/schema/v1/common.schema.json $defs/clock_domain
SESSION_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")  # the same file, $defs/source_stamp/properties/session
SEMANTICS_DRIVER, SEMANTICS_DEQUEUE = "driver_buffer_timestamp", "dequeue_time_only"
DRIVER_TIMESTAMP_PROPERTY = "CAP_PROP_POS_MSEC"
DRIVER_TIMESTAMP_CLOCK = "CLOCK_MONOTONIC"
DRIVER_TIMESTAMP_MARKS = "start_of_frame_reception"  # see the module text: flagged start of exposure by the driver, host receive time in practice

ASSUMED_BUFFER_DEPTH = 4  # OpenCV's V4L2 backend requests 4 buffers unless told otherwise
DEFAULT_EXPOSURE_GUARD_NS = 100_000_000  # when no source frame rate is known: one period of a 10 fps stream
MAX_SETTLE_FRAMES = 120
DEFAULT_CAPTURE_TIMEOUT_S, MAX_CAPTURE_TIMEOUT_S = 10.0, 60.0

_PROP_POS_MSEC, _PROP_WIDTH, _PROP_HEIGHT, _PROP_FPS, _PROP_FOURCC, _PROP_BUFFERSIZE = 0, 3, 4, 5, 6, 38


# ---------------------------------------------------------------------------------------------------------------------
# Capture device
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Grabbed:
    """One delivered frame. dequeue_mono_ns is time.monotonic_ns() right after
    the read returned. driver_timestamp_ns is the driver's buffer timestamp as
    the capture API reported it, unjudged, or None when it reported none."""

    frame: np.ndarray
    dequeue_mono_ns: int
    driver_timestamp_ns: Optional[int]


def _fourcc_text(value) -> Optional[str]:
    try:
        code = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    text = "".join(chr((code >> shift) & 0xFF) for shift in (0, 8, 16, 24))
    return text if code > 0 and all(32 < ord(ch) < 127 for ch in text) else None


class OpenCvCaptureDevice(OpenCvBackend):
    """The legacy backend, kept open. open() and read() are inherited, so the
    device is opened and a delivered frame is checked exactly as on the legacy
    path. Added: an optional source frame rate request, the negotiated format
    as the API reports it, and the driver timestamp of each frame.
    UNVALIDATED ON HARDWARE as of 2026-09-20."""

    def __init__(self, width: int, height: int, source_fps: Optional[float] = None, video_capture_factory=None,
                 clock_ns: Callable[[], int] = time.monotonic_ns):
        super().__init__(width=width, height=height, video_capture_factory=video_capture_factory)
        self.source_fps, self._clock_ns = source_fps, clock_ns

    def open(self, identity: CameraIdentity) -> None:
        super().open(identity)
        if self.source_fps:
            try:
                self._capture.set(_PROP_FPS, float(self.source_fps))  # a REQUEST; negotiated() reports what came of it
            except BaseException:
                self.close()
                raise

    def negotiated(self) -> dict:
        capture = self._capture
        if capture is None:
            raise OSError("camera device is not open")

        def number(prop):
            try:
                value = float(capture.get(prop))
            except Exception:  # noqa: BLE001 -- a property the backend does not know is "not reported", not a failure
                return None
            return value if value == value and value > 0 else None

        depth = number(_PROP_BUFFERSIZE)
        return {
            "pixel_format": _fourcc_text(number(_PROP_FOURCC)),
            "width": int(number(_PROP_WIDTH) or 0) or None,
            "height": int(number(_PROP_HEIGHT) or 0) or None,
            "source_fps_reported": number(_PROP_FPS),
            "buffer_depth": int(depth) if depth else ASSUMED_BUFFER_DEPTH,
            "buffer_depth_source": "reported" if depth else "assumed",
        }

    def grab(self) -> Optional[Grabbed]:
        frame = self.read()
        dequeued = self._clock_ns()
        if frame is None:
            return None
        try:
            stamp_ms = float(self._capture.get(_PROP_POS_MSEC))
        except Exception:  # noqa: BLE001
            stamp_ms = 0.0
        driver_ns = int(round(stamp_ms * 1_000_000)) if stamp_ms == stamp_ms and stamp_ms > 0 else None
        return Grabbed(frame=frame, dequeue_mono_ns=dequeued, driver_timestamp_ns=driver_ns)


# ---------------------------------------------------------------------------------------------------------------------
# Which timestamps can be believed, and which frame answers a request
# ---------------------------------------------------------------------------------------------------------------------
class DriverClockAssessor:
    """Judges a stream's driver timestamps against the only clock this process
    can read. A frame's timestamp is plausible when it is strictly later than
    the previous frame's and lies no later than its own dequeue time and no
    more than MAX_LAG_NS before it. The stream is trusted after MIN_CONSECUTIVE
    plausible frames in a row; one implausible frame withdraws the trust."""

    MAX_LAG_NS = 5_000_000_000
    SLACK_NS = 1_000_000
    MIN_CONSECUTIVE = 5

    def __init__(self):
        self.consecutive, self.violations, self.last_violation, self._previous = 0, 0, None, None

    @property
    def trusted(self) -> bool:
        return self.consecutive >= self.MIN_CONSECUTIVE

    def assess(self, grabbed: Grabbed) -> Optional[int]:
        """The capture time of this frame in the Pi monotonic clock, or None."""
        stamp, problem = grabbed.driver_timestamp_ns, None
        if stamp is None:
            problem = "the capture API reported no driver timestamp"
        elif stamp > grabbed.dequeue_mono_ns + self.SLACK_NS:
            problem = "driver timestamp is later than its own dequeue time: not the monotonic clock"
        elif grabbed.dequeue_mono_ns - stamp > self.MAX_LAG_NS:
            problem = "driver timestamp is far before its own dequeue time: not the monotonic clock, or a stalled stream"
        elif self._previous is not None and stamp <= self._previous:
            problem = "driver timestamp did not increase"
        self._previous = stamp
        if problem is not None:
            self.consecutive, self.last_violation = 0, problem
            self.violations += 1
            return None
        self.consecutive += 1
        return stamp if self.trusted else None


@dataclass(frozen=True)
class Stamped:
    seq: int  # the source frame number of this stream, from 1
    frame: np.ndarray
    dequeue_mono_ns: int
    capture_mono_ns: Optional[int]  # set ONLY when the driver timestamp is trusted


class FreshFrameSelector:
    """Decides which frame answers ONE request. Pure: no clock, no device.

    driver mode   a frame counts when capture_mono_ns > request + exposure
                  guard; the first settle_frames such frames are discarded and
                  the next one is kept.
    dequeue mode  entered at the first frame without a trusted capture time
                  (and never left): buffer_depth + settle_frames frames
                  dequeued after the request are discarded, the next is kept.
    A frame dequeued before the request is never considered in either mode."""

    def __init__(self, request_mono_ns: int, settle_frames: int, exposure_guard_ns: int, buffer_depth: int):
        if settle_frames < 0 or exposure_guard_ns < 0 or buffer_depth < 0:
            raise ValueError("settle_frames, exposure_guard_ns and buffer_depth must be >= 0")
        self.request_mono_ns, self.settle_frames = request_mono_ns, settle_frames
        self.exposure_guard_ns, self.buffer_depth = exposure_guard_ns, buffer_depth
        self.fresh_after_mono_ns = request_mono_ns + exposure_guard_ns
        self.semantics = SEMANTICS_DRIVER
        self.fallback_reason: Optional[str] = None
        self.frames_offered = self.frames_before_request = self.frames_stale = self.frames_settle = self.frames_drained = 0
        self.settle_boundary_mono_ns: Optional[int] = None

    def offer(self, stamped: Stamped) -> bool:
        self.frames_offered += 1
        if stamped.dequeue_mono_ns <= self.request_mono_ns:
            self.frames_before_request += 1
            return False
        if self.semantics == SEMANTICS_DRIVER and stamped.capture_mono_ns is None:
            seen = self.frames_stale + self.frames_settle
            self.semantics = SEMANTICS_DEQUEUE
            self.fallback_reason = ("no trustworthy driver timestamp on this stream" if seen == 0
                                    else "a frame of this capture had no trustworthy driver timestamp; the count was restarted")
        if self.semantics == SEMANTICS_DRIVER:
            if stamped.capture_mono_ns <= self.fresh_after_mono_ns:
                self.frames_stale += 1  # buffered, or exposed across the request: read after it, captured before it
                return False
            if self.frames_settle < self.settle_frames:
                self.frames_settle += 1
                self.settle_boundary_mono_ns = stamped.capture_mono_ns
                return False
            return stamped.capture_mono_ns > (self.settle_boundary_mono_ns or self.fresh_after_mono_ns)
        if self.frames_drained < self.buffer_depth + self.settle_frames:
            self.frames_drained += 1
            return False
        return True


class RateMeter:
    """Frames per second over the last `window` frames, from the best timestamp
    each frame has. None until two frames were seen."""

    def __init__(self, window: int = 60):
        self._times = deque(maxlen=window)

    def add(self, time_ns: int) -> None:
        self._times.append(time_ns)

    def fps(self) -> Optional[float]:
        if len(self._times) < 2 or self._times[-1] <= self._times[0]:
            return None
        return round((len(self._times) - 1) * 1e9 / (self._times[-1] - self._times[0]), 3)


# ---------------------------------------------------------------------------------------------------------------------
# Preview: one slot, its own thread
# ---------------------------------------------------------------------------------------------------------------------
class LatestSlot:
    """Holds at most ONE item. put() never waits for a consumer: an unread item
    is replaced and counted as dropped. This is the whole coupling between the
    capture thread and the preview."""

    def __init__(self):
        self._condition = threading.Condition()
        self._item = None
        self.dropped = 0

    def put(self, item) -> None:
        with self._condition:
            if self._item is not None:
                self.dropped += 1
            self._item = item
            self._condition.notify()

    def take(self, timeout_s: float):
        with self._condition:
            if self._item is None:
                self._condition.wait(timeout_s)
            item, self._item = self._item, None
            return item

    def depth(self) -> int:
        with self._condition:
            return 0 if self._item is None else 1


def pack_preview(header: dict, jpeg: bytes) -> bytes:
    """ONE message part (a latest-only subscriber cannot take multipart):
    magic, big-endian header length, JSON header, JPEG bytes."""
    head = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PREVIEW_MAGIC + struct.pack(">I", len(head)) + head + jpeg


def pack_yield_notice(header: dict) -> bytes:
    """The preview wire format with no picture after the header: the service is standing aside for a mission capture."""
    return pack_preview(header, b"")


def encode_preview_jpeg(frame: np.ndarray, width: int, quality: int) -> tuple:
    """(jpeg bytes, width, height). Downscaled, never upscaled."""
    import cv2

    height_in, width_in = frame.shape[:2]
    if width_in > width:
        size = (width, max(1, round(height_in * width / width_in)))
        frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return encoded.tobytes(), frame.shape[1], frame.shape[0]


def encode_png(frame: np.ndarray) -> bytes:
    import cv2

    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise ValueError("PNG encoding failed")
    return encoded.tobytes()


class ZmqPublisher:
    """PUB on 127.0.0.1. A PUB socket drops for a subscriber that is over its
    high-water mark instead of blocking; with SNDHWM 1 that is one message."""

    def __init__(self, port: int, context=None):
        import zmq

        self._zmq = zmq
        self._socket = (context or zmq.Context.instance()).socket(zmq.PUB)
        self._socket.setsockopt(zmq.SNDHWM, 1)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(f"tcp://{BIND_HOST}:{port}")

    def publish(self, message: bytes) -> bool:
        try:
            self._socket.send(message, self._zmq.NOBLOCK)
            return True
        except self._zmq.Again:
            return False

    def close(self) -> None:
        self._socket.close(0)


class PreviewWorker:
    """Takes the latest frame from the slot at most max_fps times a second,
    encodes it and publishes it. Stands aside while a mission capture is in
    flight, and says so with a picture-less notice when the stand-aside is long.
    Nothing it does, and nothing that happens to it, reaches the capture thread."""

    def __init__(self, role: str, session: str, slot: LatestSlot, publisher, *, max_fps: float, width: int, quality: int,
                 mission_active: threading.Event, stream_facts: Callable[[], dict], encoder=encode_preview_jpeg,
                 yield_notice_after_s: float = YIELD_NOTICE_AFTER_S, yield_notice_interval_s: float = YIELD_NOTICE_INTERVAL_S):
        self.role, self.session, self.slot, self.publisher = role, session, slot, publisher
        self.max_fps, self.width, self.quality = max_fps, width, quality
        self._mission_active, self._stream_facts, self._encoder = mission_active, stream_facts, encoder
        self._notice_after_s, self._notice_interval_s = yield_notice_after_s, yield_notice_interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"preview-{role}", daemon=True)
        self.preview_seq = self.published = self.dropped_at_socket = self.skipped_for_mission = self.encode_errors = 0
        self.yield_notices = self.yield_notices_dropped = 0
        self._yield_started: Optional[float] = None
        self._last_notice: Optional[float] = None

    def start(self) -> None:
        self._thread.start()

    def stop(self, join_s: float = 2.0) -> None:
        self._stop.set()
        self._thread.join(join_s)  # a worker stalled inside a consumer is abandoned (daemon), never waited for

    def counters(self) -> dict:
        """The service's own counts, as of now. Carried by every preview header and every notice."""
        return {"published": self.published, "skipped_for_mission": self.skipped_for_mission, "dropped_at_socket": self.dropped_at_socket}

    def _run(self) -> None:
        period = 1.0 / self.max_fps
        while not self._stop.is_set():
            stamped = self.slot.take(0.25)
            if stamped is None:
                continue  # no frame from the camera: nothing is published, not even a notice
            if self._mission_active.is_set():
                self.skipped_for_mission += 1
                self._stand_aside(stamped)
                continue
            self._yield_started = self._last_notice = None
            started = time.monotonic()
            try:
                jpeg, width, height = self._encoder(stamped.frame, self.width, self.quality)
            except Exception:  # noqa: BLE001 -- a preview that cannot be encoded is a missing preview, nothing more
                self.encode_errors += 1
                continue
            self.preview_seq += 1
            header = {
                "schema": PREVIEW_SCHEMA, "role": self.role, "session": self.session, "preview_seq": self.preview_seq,
                "source_seq": stamped.seq, "capture_mono_ns": stamped.capture_mono_ns, "dequeue_mono_ns": stamped.dequeue_mono_ns,
                "clock_domain": CLOCK_PI,
                "timestamp_semantics": SEMANTICS_DRIVER if stamped.capture_mono_ns is not None else SEMANTICS_DEQUEUE,
                "encoding": "jpeg", "width": width, "height": height,
                "source_width": int(stamped.frame.shape[1]), "source_height": int(stamped.frame.shape[0]),
            }
            header.update(self._stream_facts())
            header.update(self.counters())
            if self.publisher.publish(pack_preview(header, jpeg)):
                self.published += 1
            else:
                self.dropped_at_socket += 1
            self._stop.wait(max(0.0, period - (time.monotonic() - started)))

    def _stand_aside(self, stamped: Stamped) -> None:
        """No picture while a mission capture is in flight. Once the stand-aside has lasted the bound, a notice says so,
        at most once per interval. Durations only, all in this process's monotonic clock."""
        now = time.monotonic()
        if self._yield_started is None:
            self._yield_started = now
        if now - self._yield_started < self._notice_after_s:
            return
        if self._last_notice is not None and now - self._last_notice < self._notice_interval_s:
            return
        self._last_notice = now
        header = {"schema": PREVIEW_YIELD_SCHEMA, "role": self.role, "session": self.session, "preview_seq": self.preview_seq,
                  "source_seq": stamped.seq, "clock_domain": CLOCK_PI, "reason": "mission_capture_in_flight",
                  "yielding_for_ms": int(round((now - self._yield_started) * 1000)), **self.counters()}
        if self.publisher.publish(pack_yield_notice(header)):
            self.yield_notices += 1
        else:
            self.yield_notices_dropped += 1

    def status(self) -> dict:
        return {"max_fps": self.max_fps, "width": self.width, "published": self.published, "dropped_obsolete": self.slot.dropped,
                "dropped_at_socket": self.dropped_at_socket, "skipped_for_mission": self.skipped_for_mission, "encode_errors": self.encode_errors,
                "yield_notices": self.yield_notices, "yield_notices_dropped": self.yield_notices_dropped}


# ---------------------------------------------------------------------------------------------------------------------
# One camera: lock, device, capture thread
# ---------------------------------------------------------------------------------------------------------------------
class CaptureFailed(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


class _Pending:
    def __init__(self, selector: FreshFrameSelector):
        self.selector, self.done = selector, threading.Event()
        self.selected: Optional[Stamped] = None
        self.error: Optional[str] = None
        self.reads_without_frame = 0


STATE_OFF, STATE_RUNNING, STATE_FAILED, STATE_STOPPED = "off", "running", "failed", "stopped"


class CameraWorker:
    """Owns ONE camera from start() to stop(): lock first, then the device; the
    device closed first, then the lock released. A camera that fails stays
    locked until stop(): the handoff to another provider is never implicit."""

    MAX_CONSECUTIVE_EMPTY_READS = 60
    STOP_JOIN_S = 12.0  # longer than the capture API's own read timeout (10 s in OpenCV's V4L2 backend)

    def __init__(self, role: str, identity: CameraIdentity, device, lock: CameraLock, *, tap: Optional[LatestSlot] = None):
        self.role, self.identity, self.device, self.lock, self.tap = role, identity, device, lock, tap
        self.state, self.error = STATE_OFF, None
        self.negotiated: dict = {}
        self.open_latency_s: Optional[float] = None
        self.assessor, self.rate = DriverClockAssessor(), RateMeter()
        self.frames_seen = self.reads_without_frame = 0
        self._condition = threading.Condition()
        self._pending: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"capture-{role}", daemon=True)

    def start(self, lock_wait_s: float = DEFAULT_WAIT_S) -> None:
        self.lock.acquire(wait_s=lock_wait_s)  # CameraLockError: nothing was opened
        started = time.monotonic()
        try:
            self.device.open(self.identity)
            self.open_latency_s = time.monotonic() - started
            self.negotiated = dict(self.device.negotiated())
        except BaseException as error:
            try:
                self.device.close()
            finally:
                self.lock.release()
            if isinstance(error, Exception):
                raise CaptureFailed("camera_open_failed", describe_error(error, self.identity)) from None
            raise
        self.state = STATE_RUNNING
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(self.STOP_JOIN_S)
        if self._thread.is_alive():
            # Still inside a read. The capture object is not safe to close from another thread, and a lock released while
            # the device is open would be a lie. Both stay as they are; the kernel drops them together when the process ends.
            self.error = self.error or "the capture thread did not stop; the device and its lock are left to process exit"
            self._fail_pending(self.error)
            return
        if self.state in (STATE_RUNNING, STATE_FAILED):
            if self.state == STATE_RUNNING:
                self._close_device()
            self._fail_pending("the camera service is stopping")
            self.state = STATE_STOPPED
        self.lock.release()

    def _close_device(self) -> None:
        try:
            self.device.close()
        except Exception as error:  # noqa: BLE001
            self.error = self.error or describe_error(error, self.identity)

    def _fail_pending(self, text: str) -> None:
        with self._condition:
            pending, self._pending = self._pending, []
        for item in pending:
            item.error = text
            item.done.set()

    def _run(self) -> None:
        empty_in_a_row = 0
        while not self._stop.is_set():
            try:
                grabbed = self.device.grab()
            except Exception as error:  # noqa: BLE001 -- a failed stream is evidence; it ends THIS camera, not the process
                self._fail(describe_error(error, self.identity))
                return
            if grabbed is None:
                self.reads_without_frame += 1
                empty_in_a_row += 1
                with self._condition:
                    for item in self._pending:
                        item.reads_without_frame += 1
                if empty_in_a_row >= self.MAX_CONSECUTIVE_EMPTY_READS:
                    self._fail(f"no frame in {empty_in_a_row} consecutive reads")
                    return
                continue
            empty_in_a_row = 0
            self.frames_seen += 1
            capture_ns = self.assessor.assess(grabbed)
            stamped = Stamped(seq=self.frames_seen, frame=grabbed.frame, dequeue_mono_ns=grabbed.dequeue_mono_ns, capture_mono_ns=capture_ns)
            self.rate.add(capture_ns if capture_ns is not None else grabbed.dequeue_mono_ns)
            # The first frames of a stream only establish whether its driver timestamps can be believed. A request that
            # arrives that early waits for them instead of being answered in the weaker mode for no reason.
            if self.frames_seen >= DriverClockAssessor.MIN_CONSECUTIVE:
                with self._condition:  # mission first
                    accepted = [item for item in list(self._pending) if item.selector.offer(stamped)]
                    for item in accepted:
                        self._pending.remove(item)
                if accepted:
                    # A capture API may hand back a VIEW of a driver buffer it reuses on the next read, and the mission
                    # thread still has to hash and encode this frame while this loop reads on. What answers a request is
                    # therefore copied HERE, in the capture thread, before the next grab: the published image is the
                    # frame the timestamps in its metadata describe, whatever the backend does with its buffers.
                    kept = replace(stamped, frame=np.array(stamped.frame, copy=True))
                    for item in accepted:
                        item.selected = kept
                        item.done.set()
            if self.tap is not None:
                self.tap.put(stamped)  # a reference swap; it cannot wait for the preview

    def _fail(self, text: str) -> None:
        self.error, self.state = text, STATE_FAILED
        self._close_device()
        self._fail_pending(text)

    def exposure_guard(self) -> tuple:
        """(nanoseconds, source): one period of the source frame rate, taken as the LONGEST period the two candidate
        rates allow. The rate a driver REPORTS is often the rate that was asked for; when the stream really runs slower,
        that period is shorter than a frame and the guard would stop covering the exposure that precedes the timestamp.
        The measured rate alone is no better: it is low while a stream starts or stalls. So both are considered and the
        longer period wins, which can only make a capture wait, never make it accept an older frame."""
        candidates = [(int(1e9 / fps), source)
                      for source, fps in (("source_fps_reported", self.negotiated.get("source_fps_reported")), ("source_fps_measured", self.rate.fps()))
                      if fps and 1.0 <= fps <= 240.0]
        return max(candidates) if candidates else (DEFAULT_EXPOSURE_GUARD_NS, "default")

    def capture(self, request_mono_ns: int, settle_frames: int, timeout_s: float, exposure_guard_ns: Optional[int] = None) -> _Pending:
        try:
            self.lock.verify()
        except CameraLockError as error:
            raise CaptureFailed("camera_lock_lost", str(error)) from None
        guard_ns = self.exposure_guard()[0] if exposure_guard_ns is None else exposure_guard_ns
        pending = _Pending(FreshFrameSelector(request_mono_ns, settle_frames, guard_ns, int(self.negotiated.get("buffer_depth") or ASSUMED_BUFFER_DEPTH)))
        with self._condition:
            if self.state != STATE_RUNNING:
                raise CaptureFailed("camera_failed", self.error or f"the {self.role} camera is {self.state}")
            self._pending.append(pending)
        if not pending.done.wait(timeout_s):
            with self._condition:
                if pending in self._pending:
                    self._pending.remove(pending)
            if not pending.done.is_set():
                raise CaptureFailed("capture_timeout", f"no frame captured after the request within {timeout_s:.1f} s "
                                                       f"({pending.selector.frames_offered} frames seen, {pending.reads_without_frame} reads without a frame)")
        if pending.selected is None:
            raise CaptureFailed("camera_failed", pending.error or "the capture ended without a frame")
        return pending

    def status(self) -> dict:
        try:
            self.lock.verify()
            lock_ok = True
        except CameraLockError:
            lock_ok = False
        return {
            "state": self.state, "error": self.error, "identity_fingerprint": self.identity.fingerprint,
            "lock": {"holder": self.lock.holder, "held": self.lock.held, "excludes": lock_ok, "directory_source": self.lock.directory_source},
            "negotiated": self.negotiated, "source_fps_measured": self.rate.fps(), "frames_seen": self.frames_seen,
            "reads_without_frame": self.reads_without_frame, "open_latency_s": self.open_latency_s,
            "driver_timestamp": {"trusted": self.assessor.trusted, "violations": self.assessor.violations, "last_violation": self.assessor.last_violation,
                                 "property": DRIVER_TIMESTAMP_PROPERTY, "clock": DRIVER_TIMESTAMP_CLOCK, "marks": DRIVER_TIMESTAMP_MARKS},
        }


# ---------------------------------------------------------------------------------------------------------------------
# Atomic publication
# ---------------------------------------------------------------------------------------------------------------------
_PUBLIC_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}")


def publish_atomically(directory: Path, name: str, data: bytes, _before_link: Optional[Callable[[Path], None]] = None) -> Path:
    """Write data under a hidden temporary name, flush it to disk, then give it
    its final name in one step that REFUSES to replace an existing file (a hard
    link, then the temporary name is removed). A reader that lists the
    directory sees the final name only when the content is complete; temporary
    names start with a dot and end in .tmp."""
    if not _PUBLIC_NAME.fullmatch(name):
        raise ValueError("a published name is a plain file name that does not start with a dot")
    final, temporary = directory / name, directory / f".{name}.{secrets.token_hex(4)}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        if _before_link is not None:
            _before_link(temporary)
        os.link(temporary, final)  # FileExistsError: a name is never reused
    finally:
        os.unlink(temporary)
    return final


class FrameStore:
    """<root>/<session>/<seq>-<role>-<token>.png and the same stem with .json.
    The image first, the metadata last: a metadata file implies a complete
    image, whose size and sha256 it states."""

    def __init__(self, root: Path, png_encoder: Callable[[np.ndarray], bytes] = encode_png):
        self.root, self._encode = Path(root), png_encoder
        self._seq, self._seq_lock = 0, threading.Lock()

    def publish(self, session: str, role: str, frame: np.ndarray, metadata: dict) -> dict:
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        if not SESSION_PATTERN.fullmatch(session) or session in (".", "..") or role not in ROLES:
            raise ValueError("a frame is stored under a session label [A-Za-z0-9_.-]{1,64} and a known role")
        directory = self.root / session
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stem = f"{seq:06d}-{role}-{secrets.token_hex(4)}"
        png = self._encode(frame)
        publish_atomically(directory, f"{stem}.png", png)
        metadata = dict(metadata, session=session, frame_name=f"{stem}.png", metadata_name=f"{stem}.json",
                        relative_path=f"{session}/{stem}.png", encoding="png", bytes=len(png), sha256=hashlib.sha256(png).hexdigest())
        publish_atomically(directory, f"{stem}.json", (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        return metadata


# ---------------------------------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------------------------------
def new_session_id() -> str:
    return f"cs-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}"


class CameraService:
    """workers: role -> CameraWorker for the ENABLED roles only. A role without
    a worker is off. handle() answers one request; serve() is the REP loop."""

    def __init__(self, workers: dict, store: FrameStore, *, width: int, height: int, settle_frames: int = DEFAULT_SETTLE_FRAMES,
                 requested: Optional[dict] = None, previews: Optional[dict] = None, session: Optional[str] = None,
                 mission_active: Optional[threading.Event] = None, clock_ns: Callable[[], int] = time.monotonic_ns):
        self.workers, self.store, self.width, self.height, self.settle_frames = workers, store, width, height, settle_frames
        self.requested, self.previews = requested or {}, previews or {}
        self.session = session or new_session_id()
        self.mission_active = mission_active or threading.Event()
        self._clock_ns = clock_ns
        self._last_digest: dict = {}
        self.captures_ok = self.captures_refused = 0
        self._stop = threading.Event()

    # -- lifecycle --------------------------------------------------------------------------------------------------
    def start(self, lock_wait_s: float = DEFAULT_WAIT_S) -> None:
        started = []
        try:
            for worker in self.workers.values():
                worker.start(lock_wait_s)
                started.append(worker)
            for preview in self.previews.values():
                preview.start()
        except BaseException:
            for worker in started:
                worker.stop()
            raise

    def stop(self) -> None:
        self._stop.set()
        for preview in self.previews.values():
            preview.stop()
        for worker in self.workers.values():
            worker.stop()

    # -- requests ---------------------------------------------------------------------------------------------------
    def handle(self, request, request_mono_ns: Optional[int] = None) -> dict:
        request_mono_ns = self._clock_ns() if request_mono_ns is None else request_mono_ns
        if not isinstance(request, dict) or not isinstance(request.get("op"), str):
            return self._refuse("invalid_request", 'a request is a JSON object with an "op"')
        op = request["op"]
        if op == "ping":
            return {"ok": True, "provider": PROVIDER, "service_session": self.session}
        if op == "status":
            return {"ok": True, "status": self.status()}
        if op == "capture":
            return self._capture(request, request_mono_ns)
        return self._refuse("invalid_request", "op must be one of capture, status, ping")

    def _refuse(self, reason: str, detail: str) -> dict:
        self.captures_refused += 1
        return {"ok": False, "reason": reason, "detail": detail, "provider": PROVIDER}

    def _capture(self, request: dict, request_mono_ns: int) -> dict:
        role = request.get("role")
        if role not in ROLES:
            return self._refuse("invalid_request", f"role must be one of {list(ROLES)}")
        worker = self.workers.get(role)
        if worker is None:
            return self._refuse("camera_not_enabled", f"the {role} camera is off in this service and was never opened; "
                                                      "the front camera is opened only by an explicit --enable-front at start, after qualification")
        session = request.get("session", self.session)
        if not isinstance(session, str) or not SESSION_PATTERN.fullmatch(session) or session in (".", ".."):
            return self._refuse("invalid_request", "session must match [A-Za-z0-9_.-]{1,64}")
        settle = request.get("settle_frames", self.settle_frames)
        if type(settle) is not int or not 0 <= settle <= MAX_SETTLE_FRAMES:
            return self._refuse("invalid_request", f"settle_frames must be an integer from 0 to {MAX_SETTLE_FRAMES}")
        timeout_s = request.get("timeout_s", DEFAULT_CAPTURE_TIMEOUT_S)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= timeout_s <= MAX_CAPTURE_TIMEOUT_S:
            return self._refuse("invalid_request", f"timeout_s must be a number from 0.1 to {MAX_CAPTURE_TIMEOUT_S}")

        self.mission_active.set()  # the preview stands aside until the frame is published
        try:
            guard_ns, guard_source = worker.exposure_guard()
            try:
                pending = worker.capture(request_mono_ns, settle, float(timeout_s), guard_ns)
            except CaptureFailed as error:
                return self._refuse(error.reason, error.detail)
            stamped, selector = pending.selected, pending.selector
            frame = stamped.frame
            if not (isinstance(frame, np.ndarray) and frame.ndim == 3 and frame.size > 0):
                return self._refuse("frame_invalid", "the selected frame is not a non-empty 3-dimensional array")
            if frame.shape[:2] != (self.height, self.width):
                return self._refuse("resolution_mismatch", f"delivered frame {frame.shape[1]}x{frame.shape[0]} differs from the requested {self.width}x{self.height}")
            digest = hashlib.sha1(np.ascontiguousarray(frame).tobytes()).hexdigest()
            identical = self._last_digest.get(role) == digest
            self._last_digest[role] = digest
            driver = selector.semantics == SEMANTICS_DRIVER
            metadata = {
                "schema": FRAME_SCHEMA, "provider": PROVIDER, "service_session": self.session, "role": role,
                "identity_fingerprint": worker.identity.fingerprint,
                "width": int(frame.shape[1]), "height": int(frame.shape[0]), "channels": int(frame.shape[2]),
                "orientation": "unrotated", "crop": "none",
                "requested": dict(self.requested.get(role, {}), width=self.width, height=self.height, pixel_format="MJPG"),
                "negotiated": worker.negotiated, "source_fps_measured": worker.rate.fps(),
                "clock_domain": CLOCK_PI, "timestamp_semantics": selector.semantics,
                "timestamp_property": DRIVER_TIMESTAMP_PROPERTY if driver else None,
                "timestamp_clock": DRIVER_TIMESTAMP_CLOCK if driver else None,
                "timestamp_marks": DRIVER_TIMESTAMP_MARKS if driver else None,
                "fallback_reason": selector.fallback_reason,
                "request_mono_ns": request_mono_ns, "exposure_guard_ns": guard_ns, "exposure_guard_source": guard_source,
                "fresh_after_mono_ns": selector.fresh_after_mono_ns if driver else None,
                "settle_frames": settle, "settle_boundary_mono_ns": selector.settle_boundary_mono_ns if driver else None,
                "capture_mono_ns": stamped.capture_mono_ns if driver else None,
                "dequeue_mono_ns": stamped.dequeue_mono_ns,
                "frames_before_request": selector.frames_before_request, "frames_discarded_stale": selector.frames_stale,
                "frames_discarded_settle": selector.frames_settle, "frames_drained": selector.frames_drained,
                "buffer_depth": selector.buffer_depth, "reads_without_frame": pending.reads_without_frame,
                "identical_to_previous_mission_frame": identical,
                "source_stamp": {"session": self.session, "seq": stamped.seq, "sample_mono_ns": stamped.capture_mono_ns if driver else None,
                                 "clock_domain": CLOCK_PI},
                # For a human reading the file. The wall-clock second in which the frame was selected; never compared with anything.
                "approx_wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                metadata = self.store.publish(session, role, frame, metadata)
            except (OSError, ValueError) as error:
                return self._refuse("publication_failed", describe_error(error, worker.identity))
            record = LifecycleRecord(
                role=role, identity_fingerprint=worker.identity.fingerprint, settle_frames_requested=settle, open_ok=True,
                open_latency_s=worker.open_latency_s, frames_read=selector.frames_offered, reads_without_frame=pending.reads_without_frame,
                frame_valid=True, frame_shape=list(frame.shape), stale_suspect=identical,
                close_ok=False,  # true to the fact: this provider does not close the device between captures
                total_latency_s=max(0, stamped.dequeue_mono_ns - request_mono_ns) / 1e9)
            self.captures_ok += 1
            return {"ok": True, "provider": PROVIDER, "frame": metadata, "lifecycle_record": record.to_dict()}
        finally:
            self.mission_active.clear()

    def status(self) -> dict:
        cameras = {role: {"state": STATE_OFF} for role in ROLES}
        for role, worker in self.workers.items():
            cameras[role] = worker.status()
            if role in self.previews:
                cameras[role]["preview"] = self.previews[role].status()
        return {"provider": PROVIDER, "service_session": self.session, "pid": os.getpid(), "cameras": cameras,
                "requested_resolution": [self.width, self.height], "settle_frames": self.settle_frames,
                "captures_ok": self.captures_ok, "requests_refused": self.captures_refused}

    # -- REP loop ---------------------------------------------------------------------------------------------------
    def serve(self, socket, poll_ms: int = 200) -> None:
        """socket: a bound REP socket. One request at a time; every request
        gets exactly one reply, also a malformed one."""
        import zmq

        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)
        while not self._stop.is_set():
            if not dict(poller.poll(poll_ms)):
                continue
            raw = socket.recv()
            request_mono_ns = self._clock_ns()  # as early as this process can know of the request
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                request = None
            try:
                reply = self.handle(request, request_mono_ns)
            except Exception as error:  # noqa: BLE001 -- REP must reply or the socket is wedged for good
                reply = {"ok": False, "reason": "internal_error", "detail": type(error).__name__, "provider": PROVIDER}
            socket.send(json.dumps(reply).encode("utf-8"))

    def request_stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="camera_service.py", description="The single camera capture owner (mission capture + latest-only preview).")
    p.add_argument("--enable-front", action="store_true",
                   help="ALSO own the front camera. Off by default: enable only after the qualification in docs/autonomous-demo/camera-service.md")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--settle-frames", type=int, default=DEFAULT_SETTLE_FRAMES)
    p.add_argument("--source-fps", type=float, default=None, help="REQUEST this source frame rate from the wrist camera (what the USB bus carries)")
    p.add_argument("--front-source-fps", type=float, default=None, help="the same for the front camera")
    p.add_argument("--mission-port", type=int, default=DEFAULT_MISSION_PORT)
    p.add_argument("--wrist-preview-port", type=int, default=DEFAULT_PREVIEW_PORTS["wrist"], help="the wrist preview PUB socket")
    p.add_argument("--front-preview-port", type=int, default=DEFAULT_PREVIEW_PORTS["front"], help="the front preview PUB socket; never derived from the wrist one")
    p.add_argument("--no-preview", action="store_true")
    p.add_argument("--preview-fps", type=float, default=4.0)
    p.add_argument("--preview-width", type=int, default=320)
    p.add_argument("--preview-quality", type=int, default=70)
    p.add_argument("--store-dir", default=None, help="default: ~/kc-camera-service/store (keep it outside any git checkout)")
    p.add_argument("--lock-wait-s", type=float, default=DEFAULT_WAIT_S)
    p.add_argument("--lock-status", action="store_true", help="print who holds each camera lock and exit; opens nothing")
    return p


def enabled_roles(args) -> tuple:
    return ("wrist", "front") if args.enable_front else ("wrist",)


def _opencv_device(role, width, height, source_fps):
    return OpenCvCaptureDevice(width=width, height=height, source_fps=source_fps)


def build_service(args, env, device_factory=_opencv_device, publisher_factory=None, store: Optional[FrameStore] = None) -> CameraService:
    """Everything except the sockets' REP side and the start. Reads the
    identity of ENABLED roles only: a camera that is off is not even named."""
    if not 0.2 <= args.preview_fps <= 15.0:
        raise ValueError("--preview-fps must be from 0.2 to 15")
    session, mission_active = new_session_id(), threading.Event()
    workers, previews, requested = {}, {}, {}
    for role in enabled_roles(args):
        identity = resolve_camera_identity(role, env=env)
        source_fps = args.source_fps if role == "wrist" else args.front_source_fps
        requested[role] = {"source_fps": source_fps}
        tap = None if args.no_preview else LatestSlot()
        worker = CameraWorker(role, identity, device_factory(role, args.width, args.height, source_fps), CameraLock(role, LOCK_HOLDER, env=env), tap=tap)
        workers[role] = worker
        if tap is not None and publisher_factory is not None:
            previews[role] = PreviewWorker(
                role, session, tap, publisher_factory(role, {"wrist": args.wrist_preview_port, "front": args.front_preview_port}[role]),
                max_fps=args.preview_fps, width=args.preview_width, quality=args.preview_quality, mission_active=mission_active,
                stream_facts=lambda worker=worker: {"pixel_format": worker.negotiated.get("pixel_format"), "source_fps_reported": worker.negotiated.get("source_fps_reported"),
                                                    "source_fps_measured": worker.rate.fps()})
    if store is None:
        store = FrameStore(Path(args.store_dir) if args.store_dir else Path(os.path.expanduser("~")) / "kc-camera-service" / "store")
    return CameraService(workers, store, width=args.width, height=args.height, settle_frames=args.settle_frames, requested=requested,
                         previews=previews, session=session, mission_active=mission_active)


def main(argv=None, env=None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    if args.lock_status:
        try:
            print(json.dumps(lock_status(ROLES, env=env), indent=2))
        except CameraLockError as error:
            sys.exit(f"camera_service: {error}")
        return 0

    import zmq

    context = zmq.Context.instance()
    try:
        service = build_service(args, env, publisher_factory=lambda role, port: ZmqPublisher(port, context))
    except (CameraIdentityUnavailable, InvalidCameraIdentity, ValueError, zmq.ZMQError) as error:
        sys.exit(f"camera_service: {error}")
    mission = context.socket(zmq.REP)
    mission.setsockopt(zmq.LINGER, 0)
    try:
        mission.bind(f"tcp://{BIND_HOST}:{args.mission_port}")
        service.start(args.lock_wait_s)
    except (CameraLockError, CaptureFailed, zmq.ZMQError) as error:
        mission.close(0)
        sys.exit(f"camera_service: REFUSED to start, no camera is owned: {error}")

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: service.request_stop())
    print(f"camera_service: session {service.session} owns {list(service.workers)}; front {'ENABLED' if args.enable_front else 'off'}; "
          f"preview {'off' if args.no_preview else 'on'}", file=sys.stderr)
    try:
        service.serve(mission)
    finally:
        service.stop()
        mission.close(0)
        for preview in service.previews.values():
            preview.publisher.close()
    print("camera_service: stopped; every camera is closed and every lock released", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
