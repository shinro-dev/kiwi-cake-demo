# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.preview_client: the laptop side of the camera service's preview tap (bin/pi/camera_service.py).

WHAT IT IS. A SUB reader that holds the LATEST preview frame of one camera and says how good the feed is: the frame with the
clock domain its capture time belongs to, its receive age in the laptop clock, the measured frame rate, and the gaps this
subscriber observed. It is a picture for a human. It is never evidence and nothing here may be used to decide a motion: a
mission frame comes from the service's request/reply side, with its own metadata.

ONE FRAME, NEVER A QUEUE. The socket is a conflating subscriber (it keeps the last message only) and PreviewTracker keeps one
PreviewFrame. A reader that falls behind loses frames; it never shows an old one after a newer one, and it never grows.

CLOCK DOMAINS. capture_mono_ns and dequeue_mono_ns were read on the Pi (capture_clock_domain, normally pi_monotonic).
rx_mono_ns and every age and rate here are laptop monotonic. The two are never subtracted from each other: the age this module
reports is a RECEIVE age (how long ago the laptop got the frame), not the age of the picture. A capture-to-display latency
needs a clock relation this module does not have, and it does not invent one. A frozen capture clock IS detectable without
one: consecutive frames that carry the same capture time are counted (capture_time_stalls).

GAPS. The publisher numbers every preview it tries to send (preview_seq, per service session). A jump in that number is a
preview this subscriber did not get: dropped at the publisher's socket, lost with the forward, or replaced in the conflating
socket before it was read. source_seq (the camera's own frame number) jumps by design, since a preview of a few frames per
second skips source frames, so it is reported but never counted as a gap.

THE ENDPOINT IS LOOPBACK. The service binds the Pi's loopback and the laptop reaches it through an ssh forward, so the only
endpoint accepted is tcp://127.0.0.1:<port> (or localhost). An address of another machine is refused without being repeated.

WIRE FORMAT (one message part; a conflating socket cannot take multipart): the magic "KCPV1", a 4-byte big-endian header
length, a JSON header (schema kc.camera_service.preview/1), the JPEG bytes. packages/demo-runtime/tests/test_preview_client.py
decodes messages packed by the service's own pack_preview(), so the two ends cannot drift apart unnoticed.

WHY A GAP HAPPENED, AS THE SERVICE SAYS IT. A header may carry the service's counters (published, skipped_for_mission,
dropped_at_socket, as of that message); a header without them is a service older than them, and the counters are then
UNKNOWN, never zero. The same framing with schema kc.camera_service.preview_yield/1 and NO picture is a yield notice: the
service is standing aside for a mission capture that has lasted a while. A notice is counted and remembered
(latest_yield()), and it is never a frame: it does not replace the picture, move the rate, count as a gap or advance the
preview number. What a reader does with it is demo_runtime.sources' decision.

Standard library at import; zmq is imported when a subscriber is started, so the module imports (and PreviewTracker is fully
testable) on a machine without it.
"""
import json
import re
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

PREVIEW_MAGIC = b"KCPV1"
PREVIEW_SCHEMA = "kc.camera_service.preview/1"
PREVIEW_YIELD_SCHEMA = "kc.camera_service.preview_yield/1"  # bin/pi/camera_service.py: the picture-less stand-aside notice
SERVICE_COUNTERS = ("published", "skipped_for_mission", "dropped_at_socket")
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024

# Mirrors of packages/demo-contracts/schema/v1/common.schema.json, pinned against the schema file by the tests.
# Copied, not imported: this module describes a read and must not grow a dependency to do so.
SESSION_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"  # $defs/source_stamp/properties/session/pattern
CLOCK_LAPTOP, CLOCK_PI, CLOCK_WALL, CLOCK_UNKNOWN = "laptop_monotonic", "pi_monotonic", "wall_utc", "unknown"  # $defs/clock_domain/enum
CLOCK_DOMAINS = (CLOCK_LAPTOP, CLOCK_PI, CLOCK_WALL, CLOCK_UNKNOWN)

DEFAULT_RATE_WINDOW_S = 5.0
_SESSION = re.compile(SESSION_PATTERN)
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_LOOPBACK_ENDPOINT = re.compile(r"^tcp://(?:127\.0\.0\.1|localhost):([0-9]{1,5})$")


class MalformedPreview(ValueError):
    """A message that is not a preview of this wire format. .code is a stable word for the counter."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _positive_int(value) -> Optional[int]:
    return value if type(value) is int and value > 0 else None


def _optional_ns(value) -> Optional[int]:
    return value if type(value) is int and value >= 0 else None


def _split(raw) -> tuple:
    """(header dict, payload bytes) of one framed message, before its schema is looked at. Raises MalformedPreview."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise MalformedPreview("not_bytes")
    raw = bytes(raw)
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MalformedPreview("too_large")
    start = len(PREVIEW_MAGIC) + 4
    if len(raw) < start or raw[:len(PREVIEW_MAGIC)] != PREVIEW_MAGIC:
        raise MalformedPreview("bad_magic")
    (length,) = struct.unpack(">I", raw[len(PREVIEW_MAGIC):start])
    if length > MAX_HEADER_BYTES or start + length > len(raw):
        raise MalformedPreview("bad_header_length")
    try:
        header = json.loads(raw[start:start + length].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise MalformedPreview("header_not_json") from None
    if not isinstance(header, dict):
        raise MalformedPreview("unknown_schema")
    return header, raw[start + length:]


def _check_identity(header: dict) -> None:
    role, session = header.get("role"), header.get("session")
    if not isinstance(role, str) or not _ROLE.fullmatch(role) or not isinstance(session, str) or not _SESSION.fullmatch(session):
        raise MalformedPreview("bad_identity")


def service_counters(header) -> dict:
    """The service's own counters from a header or a notice: each a non-negative integer, or None when the message did
    not carry it in that shape. None is "not counted where this reader can see", never zero."""
    header = header if isinstance(header, dict) else {}
    return {key: value if type(value) is int and value >= 0 else None for key, value in ((key, header.get(key)) for key in SERVICE_COUNTERS)}


def unpack_yield(raw: bytes) -> dict:
    """The header of a yield notice. Raises MalformedPreview; a notice that carries a picture is malformed."""
    header, payload = _split(raw)
    if header.get("schema") != PREVIEW_YIELD_SCHEMA:
        raise MalformedPreview("unknown_schema")
    if payload:
        raise MalformedPreview("yield_with_payload")
    _check_identity(header)
    if _optional_ns(header.get("preview_seq")) is None or _optional_ns(header.get("yielding_for_ms")) is None:
        raise MalformedPreview("bad_numbers")
    return header


def unpack_preview(raw: bytes) -> tuple:
    """(header dict, jpeg bytes). Raises MalformedPreview; never returns a header it did not check."""
    header, jpeg = _split(raw)
    if header.get("schema") != PREVIEW_SCHEMA:
        raise MalformedPreview("unknown_schema")
    if header.get("encoding") != "jpeg" or jpeg[:2] != b"\xff\xd8":
        raise MalformedPreview("not_a_jpeg")
    _check_identity(header)
    if _positive_int(header.get("preview_seq")) is None or _positive_int(header.get("width")) is None or _positive_int(header.get("height")) is None:
        raise MalformedPreview("bad_numbers")
    return header, jpeg


@dataclass(frozen=True)
class PreviewFrame:
    """The one frame a reader holds. Pi-side times and laptop-side times sit in separate fields with their domain named."""

    jpeg: bytes
    role: str
    session: str
    preview_seq: int
    source_seq: Optional[int]
    width: int
    height: int
    capture_mono_ns: Optional[int]  # capture_clock_domain; None when the service had no trustworthy driver timestamp
    dequeue_mono_ns: Optional[int]  # capture_clock_domain
    capture_clock_domain: str
    timestamp_semantics: Optional[str]
    rx_mono_ns: int  # rx_clock_domain
    rx_seq: int
    rx_clock_domain: str = CLOCK_LAPTOP
    stream: dict = field(default_factory=dict)  # what the service said about the source: pixel format, source frame rates
    service: dict = field(default_factory=dict)  # the service's counters as of this frame (service_counters); None values are unknown

    def age_ns(self, now_mono_ns: int) -> int:
        """RECEIVE age in the laptop clock: how long ago this frame arrived here. Not the age of the picture."""
        return max(0, now_mono_ns - self.rx_mono_ns)

    def __repr__(self) -> str:  # the JPEG is kilobytes of noise in a log line
        return (f"PreviewFrame(role={self.role!r}, session={self.session!r}, preview_seq={self.preview_seq}, {self.width}x{self.height}, "
                f"{len(self.jpeg)} bytes, capture_clock_domain={self.capture_clock_domain!r})")


@dataclass(frozen=True)
class YieldNotice:
    """The newest stand-aside notice: when THIS laptop received it, and what the service said. Never a picture."""

    rx_mono_ns: int  # laptop monotonic
    session: str
    preview_seq: int  # the last preview the service published before it stood aside
    yielding_for_ms: int  # how long the service had stood aside, measured in ITS clock: a duration, never compared across clocks
    service: dict


class PreviewTracker:
    """The bookkeeping, without a socket: ingest(raw, rx_mono_ns) for every message, latest() and stats() for the reader.
    Thread safe: one thread ingests, any thread reads."""

    def __init__(self, role: Optional[str] = None, *, rate_window_s: float = DEFAULT_RATE_WINDOW_S):
        if role is not None and not _ROLE.fullmatch(role):
            raise ValueError("role must be a short lower-case name")
        if rate_window_s <= 0:
            raise ValueError("rate_window_s must be > 0")
        self.role, self._window_ns = role, int(rate_window_s * 1e9)
        self._lock = threading.Lock()
        self._latest: Optional[PreviewFrame] = None
        self._rx_times = deque(maxlen=512)
        self.received = self.gap_events = self.missing_previews = self.session_changes = self.regressions = self.capture_time_stalls = 0
        self.yield_notices = 0
        self.malformed: dict = {}
        self.longest_interval_ns: Optional[int] = None
        self._latest_yield: Optional[YieldNotice] = None
        self._service: Optional[dict] = None  # the counters of the newest message, frame or notice

    def _count_malformed(self, code: str) -> None:
        with self._lock:
            self.malformed[code] = self.malformed.get(code, 0) + 1

    def ingest(self, raw, rx_mono_ns: int) -> bool:
        """True when the message became the latest frame. A malformed or foreign message is counted and changes nothing.
        A yield notice is remembered (latest_yield) and answers False: it is not a frame."""
        try:
            header, jpeg = unpack_preview(raw)
            if self.role is not None and header["role"] != self.role:
                raise MalformedPreview("other_role")
        except MalformedPreview as error:
            if error.code == "unknown_schema" and self._ingest_yield(raw, rx_mono_ns):
                return False
            self._count_malformed(error.code)
            return False
        domain = header.get("clock_domain")
        with self._lock:
            previous = self._latest
            if previous is not None:
                interval = rx_mono_ns - previous.rx_mono_ns
                self.longest_interval_ns = interval if self.longest_interval_ns is None else max(self.longest_interval_ns, interval)
                if header["session"] != previous.session:
                    self.session_changes += 1  # the service was restarted: its numbering starts again, so no gap can be counted across it
                elif header["preview_seq"] > previous.preview_seq + 1:
                    self.gap_events += 1
                    self.missing_previews += header["preview_seq"] - previous.preview_seq - 1
                elif header["preview_seq"] <= previous.preview_seq:
                    self.regressions += 1
                capture = _optional_ns(header.get("capture_mono_ns"))
                if header["session"] == previous.session and capture is not None and capture == previous.capture_mono_ns:
                    self.capture_time_stalls += 1
            self.received += 1
            self._rx_times.append(rx_mono_ns)
            self._latest = PreviewFrame(
                jpeg=jpeg, role=header["role"], session=header["session"], preview_seq=header["preview_seq"], source_seq=_positive_int(header.get("source_seq")),
                width=header["width"], height=header["height"], capture_mono_ns=_optional_ns(header.get("capture_mono_ns")),
                dequeue_mono_ns=_optional_ns(header.get("dequeue_mono_ns")), capture_clock_domain=domain if domain in CLOCK_DOMAINS else CLOCK_UNKNOWN,
                timestamp_semantics=header.get("timestamp_semantics") if isinstance(header.get("timestamp_semantics"), str) else None,
                rx_mono_ns=rx_mono_ns, rx_seq=self.received,
                stream={key: header.get(key) for key in ("pixel_format", "source_fps_reported", "source_fps_measured", "source_width", "source_height")},
                service=service_counters(header))
            self._service = self._latest.service
        return True

    def _ingest_yield(self, raw, rx_mono_ns: int) -> bool:
        """True when raw was a yield notice, well formed or not (a malformed one is counted under its own code)."""
        try:
            header = unpack_yield(raw)
        except MalformedPreview as error:
            if error.code == "unknown_schema":
                return False  # not a notice either: the caller counts it as the frame reader's unknown schema
            self._count_malformed(error.code)
            return True
        if self.role is not None and header["role"] != self.role:
            self._count_malformed("other_role")
            return True
        counters = service_counters(header)
        with self._lock:
            self.yield_notices += 1
            self._latest_yield = YieldNotice(rx_mono_ns=rx_mono_ns, session=header["session"], preview_seq=header["preview_seq"],
                                             yielding_for_ms=header["yielding_for_ms"], service=counters)
            self._service = counters
        return True

    def latest(self) -> Optional[PreviewFrame]:
        with self._lock:
            return self._latest

    def latest_yield(self) -> Optional[YieldNotice]:
        with self._lock:
            return self._latest_yield

    def stats(self, now_mono_ns: int) -> dict:
        """Every time and rate in here is laptop monotonic. measured_fps is over the frames received within the rate window
        and None when fewer than two were: a feed that stopped reads as None with a growing rx_age_ns, never as its old rate."""
        with self._lock:
            recent = [t for t in self._rx_times if now_mono_ns - t <= self._window_ns]
            fps = None
            if len(recent) >= 2 and recent[-1] > recent[0]:
                fps = round((len(recent) - 1) * 1e9 / (recent[-1] - recent[0]), 3)
            latest = self._latest
            return {
                "clock_domain": CLOCK_LAPTOP, "received": self.received, "measured_fps": fps, "rate_window_s": self._window_ns / 1e9,
                "rx_age_ns": None if latest is None else latest.age_ns(now_mono_ns), "longest_interval_ns": self.longest_interval_ns,
                "gaps": {"events": self.gap_events, "missing_previews": self.missing_previews},
                "session_changes": self.session_changes, "regressions": self.regressions, "capture_time_stalls": self.capture_time_stalls,
                "malformed": dict(self.malformed), "buffered_frames": 0 if latest is None else 1,
                "yield_notices": self.yield_notices, "service": None if self._service is None else dict(self._service),
            }


def check_endpoint(endpoint: str) -> str:
    match = _LOOPBACK_ENDPOINT.fullmatch(endpoint) if isinstance(endpoint, str) else None
    if match is None or not 0 < int(match.group(1)) < 65536:
        # The refused value is not repeated: it may be the address of a machine.
        raise ValueError("the preview endpoint must be tcp://127.0.0.1:<port>: the service is reached through an ssh forward, never directly")
    return endpoint


class PreviewSubscriber:
    """The SUB socket and its reader thread around one PreviewTracker.

    subscriber = PreviewSubscriber("tcp://127.0.0.1:<forwarded port>", role="wrist"); subscriber.start()
    frame = subscriber.latest()            # PreviewFrame or None
    facts = subscriber.stats()             # rx age, measured fps, gaps: laptop clock
    subscriber.stop()
    """

    def __init__(self, endpoint: str, role: Optional[str] = None, *, clock_ns: Callable[[], int] = time.monotonic_ns, poll_ms: int = 200,
                 rate_window_s: float = DEFAULT_RATE_WINDOW_S, context=None):
        self.endpoint, self._clock_ns, self._poll_ms, self._context = check_endpoint(endpoint), clock_ns, poll_ms, context
        self.tracker = PreviewTracker(role, rate_window_s=rate_window_s)
        self._socket = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def open(self) -> None:
        """Connect without starting the thread (poll_once() then drives it)."""
        if self._socket is not None:
            return
        import zmq

        socket = (self._context or zmq.Context.instance()).socket(zmq.SUB)
        socket.setsockopt(zmq.CONFLATE, 1)  # before connect: the socket keeps the last message only
        socket.setsockopt(zmq.RCVHWM, 1)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.MAXMSGSIZE, MAX_MESSAGE_BYTES)
        socket.setsockopt(zmq.SUBSCRIBE, b"")
        socket.connect(self.endpoint)
        self._socket = socket

    def poll_once(self, timeout_ms: Optional[int] = None) -> bool:
        """Wait up to timeout_ms for a message and ingest it. True when a frame became the latest."""
        import zmq

        if self._socket is None:
            raise RuntimeError("the preview subscriber is not open")
        if not self._socket.poll(self._poll_ms if timeout_ms is None else timeout_ms, zmq.POLLIN):
            return False
        try:
            raw = self._socket.recv(zmq.NOBLOCK)
        except zmq.Again:
            return False
        return self.tracker.ingest(raw, self._clock_ns())  # stamped on receipt, in the laptop clock

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("the preview subscriber was already started")
        self.open()
        self._thread = threading.Thread(target=self._run, name="preview-subscriber", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0 + self._poll_ms / 1000.0)
        if self._socket is not None:
            self._socket.close(0)
            self._socket = None

    def latest(self) -> Optional[PreviewFrame]:
        return self.tracker.latest()

    def latest_yield(self) -> Optional[YieldNotice]:
        return self.tracker.latest_yield()

    def stats(self) -> dict:
        return self.tracker.stats(self._clock_ns())
