# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.sources: the daemon's two READ-ONLY sources, and the honest state of each.

WHAT THIS IS. demo_runtime.tap_client reads the host's tap and demo_runtime.preview_client reads the camera service's
preview. Both are subscribers: a SUB socket cannot send, so attaching either one commands nothing and changes nothing on
the robot. This module owns one of each, gives each its own state machine, and publishes what it knows into the telemetry
hub the console reads. It never touches the supervisor, the journal or a command.

FIVE STATES, EACH WITH A REASON CODE.
    disconnected  nothing is attached (not configured, or stopped).
    connecting    a subscriber exists and no sample has ever arrived.
    connected     a sample arrived inside the freshness window.
    stalled       samples arrived before, none inside the window; the age says how long ago.
    failed        the subscriber could not be opened, or the transport faulted since the last sample.
Only 'connected' reports values. In every other state the measured values are NULL and the slot carries a reason code:
a number is never shown because it happened to arrive a while ago, and a slot with nothing behind it invents nothing.

CLOCKS ARE NEVER MIXED. A tap sample carries the host's session, its own sequence number and the host's monotonic sample
time; those three are passed on VERBATIM, with the clock domain named (pi_monotonic). Everything this module measures
(receive time, receive age, the measured rate) is the LAPTOP's monotonic clock and says so. A Pi monotonic value is never
subtracted from a laptop one: the age of the PICTURE therefore cannot be computed here, capture_age_ms is null, and
capture_age_reason says why. What CAN be measured inside one domain is measured: capture to dequeue on the Pi, and the
frozen-capture-clock counter the preview reader keeps.

PREVIEW FRAMES REACH THE BROWSER through the telemetry slot itself: the record carries the latest JPEG as bytes_b64,
which is what the API's /api/preview.mjpg serves. Latest-only, one frame at a time, never a queue. A record WITHOUT
bytes ends that stream rather than repeating a stale picture, so a heartbeat is only ever published while the source is
not connected. Nothing in here can delay the supervisor, the tailer or a command: each source polls on its own thread
and the hub's publish replaces one slot under a short lock.

A GAP THE CAMERA SERVICE DECLARED IS NOT A STALL. The service stands aside while a mission capture is in flight and
publishes no picture until the frame is selected, encoded and published (bin/pi/camera_service.py). Before this, any
silence longer than FRESH_WINDOW_MS made the slot 'stalled' and published a record WITHOUT a picture, and such a record
ENDS the API's MJPEG response (apps/console-api/console_api/app.py, _stream_preview): the browser's image element then
keeps its last frame, the page reads 'Stalled at the runtime' and opens a new stream when frames return. When the
stalled record and the next frame reach the page inside one publication window of its store (TELEMETRY_FLUSH_MS, 120 ms),
the stalled record is replaced before it is drawn, the page never leaves 'streaming', the element is not replaced, and
the picture stays frozen on the ended response while the caption reads as live. Whether the stand-asides of 2026-09-26
lasted past 2 s is not recorded anywhere (tools/preview_stall_probe.py records it now); a capture does when the source
runs below about 5 fps (two stale, six settle and the kept frame are nine frame periods) or when its publication (the
PNG and two fsyncs) is slow. A gap made on the network path between the service and this runtime sends no notice and is
still a stall. Now the service announces a long stand-aside with a picture-less yield notice, and a notice of the SAME service session that arrives
while this source is connected keeps it connected with the reason 'paused_for_decision_capture' and republishes the
KEPT picture (the same frame, the same number, its receive age growing), so the stream stays open and the record says
why nothing new arrives. A notice never revives a stalled or connecting source, and silence with no notice is a stall
exactly as before. The slot carries the service's skipped_for_mission count (an optional contract field), null when the
service did not send one.

WHERE THE ENDPOINTS COME FROM. The tap is configured (config.tap: enabled, port). The runtime configuration has NO
preview field today and refuses unknown keys, so the preview endpoint is DERIVED: a config that grows a `preview`
section is read defensively with getattr, and otherwise the camera service's own documented default port is used, and
only when the tap is enabled, which is the switch that says the operator set up the loopback forwards this daemon reads
through. --preview-port overrides it. Both endpoints must be loopback (the subscribers refuse anything else), and a
refused endpoint is never repeated in a reason.
"""
from __future__ import annotations

import base64
import threading
import time
from collections import deque
from typing import Callable, Optional

from .preview_client import CLOCK_LAPTOP, CLOCK_UNKNOWN, PreviewSubscriber
from .tap_client import CLOCK_PI, TapClient, source_stamp

DISCONNECTED, CONNECTING, CONNECTED, STALLED, FAILED = "disconnected", "connecting", "connected", "stalled", "failed"

# bin/pi/camera_service.py: DEFAULT_MISSION_PORT = 5570 and DEFAULT_PREVIEW_PORTS per role, written out rather than
# derived. Pinned against that file by packages/demo-runtime/tests/test_sources.py, which reads it as text (no import,
# no camera). There is deliberately no base-plus-offset here: that arithmetic is what let one port serve both roles.
CAMERA_SERVICE_PREVIEW_PORTS = {"wrist": 5571, "front": 5572}
PREVIEW_ROLES = ("wrist", "front")
DEFAULT_ROLE = "wrist"


def preview_topic(role: str) -> str:
    """The telemetry topic ONE camera publishes on. The role is part of the topic, so a reader asking for the front
    camera can never be handed the wrist picture, and a stalled role stops publishing without silencing the other."""
    return f"preview:{role}"

FRESH_WINDOW_MS = 2000.0  # the same window the snapshot's link used before a source existed
RATE_WINDOW_S = 5.0
HEARTBEAT_S = 1.0  # how often a source that is NOT connected republishes, so the age on the screen keeps growing
TAP_MIN_INTERVAL_S = 0.1  # a connected tap publishes at most ten times a second: the host samples far faster than a screen reads
POLL_MS = 100
REOPEN_S = 2.0

# What a tap sample's observation calls the things the contract calls joints and a base velocity (lerobot LeKiwi keys).
ARM_KEYS = {"shoulder_pan": "arm_shoulder_pan.pos", "shoulder_lift": "arm_shoulder_lift.pos", "elbow_flex": "arm_elbow_flex.pos",
            "wrist_flex": "arm_wrist_flex.pos", "wrist_roll": "arm_wrist_roll.pos", "gripper": "arm_gripper.pos"}
BASE_KEYS = {"x_mps": "x.vel", "y_mps": "y.vel", "theta_dps": "theta.vel"}
# The host's lease report, projected. request_id is DELIBERATELY absent: it is the token an acquire is adopted by, and it
# must never leave this process on a screen feed.
LEASE_KEYS = ("state", "host_session", "generation", "owner_label", "ttl_s", "expires_in_ms", "acquisitions", "releases", "expiries")
_MAX_COUNTERS = 24

CAPTURE_AGE_REASON = "no_clock_relation_between_source_and_laptop"
# The reason a CONNECTED preview slot carries while the camera service has declared that it stands aside for a mission
# capture: the picture is the kept one, nothing is wrong with the feed, and the next frame ends the pause.
PAUSED_FOR_DECISION_CAPTURE = "paused_for_decision_capture"


def _finite(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _count(value, *, key: Optional[str] = None) -> Optional[int]:
    """A contract counter: a non-negative integer, or None for "not known".

    The preview tracker keeps its counters richer than contract v1 can carry. `gaps` is
    `{"events": n, "missing_previews": m}` there and `malformed` is a count per error code, while the
    schema declares both as `integer|null` with `minimum 0`. They were published as those objects, so
    every reader that typed them as numbers discarded them silently, and the one frame-progression
    fault signal this runtime computes never reached a screen.

    NOT KNOWN STAYS NOT KNOWN. A missing counter is None and never 0: "the feed skipped nothing" and
    "nobody counted" are different facts and only one of them is reassuring. A negative or
    non-integral value is None as well, rather than clamped, because a counter that cannot be read is
    not a counter that read zero.

    `key` picks one member of a mapping; without it a mapping is summed, which is what `malformed`
    needs, since its total across error codes is the number the contract field means.

    NAMED RESIDUAL: `gaps` is published as the number of gap EVENTS, which matches its neighbours
    `session_changes` and `capture_time_stalls`, both event counts. The MAGNITUDE of the loss,
    `missing_previews`, has no field in contract v1 and so is not carried here. The tracker still
    counts it.
    """
    if isinstance(value, dict):
        members = [value.get(key)] if key is not None else list(value.values())
        total = 0
        for member in members:
            counted = _count(member)
            if counted is None:
                return None
            total += counted
        return total
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _numbers(value) -> Optional[dict]:
    """A bounded projection of a counter object the host sent: short plain keys, finite numbers, nothing else."""
    if not isinstance(value, dict):
        return None
    out = {}
    for key, item in list(value.items())[:_MAX_COUNTERS]:
        if isinstance(key, str) and key.isascii() and len(key) <= 40 and _finite(item) is not None:
            out[key] = _finite(item)
    return out


def _lease(value) -> Optional[dict]:
    if not isinstance(value, dict):
        return None
    out = {}
    for key in LEASE_KEYS:
        item = value.get(key)
        out[key] = item if isinstance(item, str) and len(item) <= 64 else _finite(item)
    return out


def joints_of(observation) -> Optional[dict]:
    """One value per arm joint, in the host's own position units as it published them. Null marks a joint the sample
    did not carry; the six keys are always present, so a missing joint is visible instead of absent."""
    if not isinstance(observation, dict):
        return None
    return {name: _finite(observation.get(key)) for name, key in ARM_KEYS.items()}


def base_velocity_of(observation) -> Optional[dict]:
    if not isinstance(observation, dict):
        return None
    return {name: _finite(observation.get(key)) for name, key in BASE_KEYS.items()}


def unavailable_tap(reason: str = "tap_source_not_attached", state: str = DISCONNECTED) -> dict:
    """The tap slot when there is nothing behind it. Every measured field is null: no age, no rate, no count."""
    return {"available": False, "state": state, "reason": reason, "source_stamp": None, "joints": None, "base_velocity": None,
            "receive_mono_ns": None, "receive_age_ms": None, "clock_domains": {"sample": CLOCK_PI, "receive": CLOCK_LAPTOP},
            "measured_rate_hz": None, "subscriber_gap_count": None, "publisher_drop_count": None, "session_changes": None,
            "malformed_count": None, "transport_errors": None, "lease": None, "rejections": None, "commands": None, "ingestion": None}


def unavailable_preview(reason: str = "preview_source_not_attached", state: str = DISCONNECTED, role: str = DEFAULT_ROLE) -> dict:
    """The preview slot when there is nothing behind it. No seq, no age, no rate, and no picture."""
    return {"available": False, "state": state, "reason": reason, "role": role, "seq": None, "source_seq": None, "capture_clock": None,
            "capture_semantics": None, "capture_age_ms": None, "capture_age_reason": CAPTURE_AGE_REASON, "service_latency_ms": None,
            "receive_mono_ns": None, "receive_age_ms": None, "measured_fps": None, "frame": None,
            "stream": {"pixel_format": None, "source_fps_reported": None, "source_fps_measured": None, "source_width": None, "source_height": None},
            "gaps": None, "session_changes": None, "capture_time_stalls": None, "malformed": None}


class _Rate:
    """Receive times in the laptop clock and the rate measured over a window of them. Fewer than two inside the window
    is None, never the rate it used to have. Locked: a source thread appends while a snapshot reads."""

    def __init__(self, window_s: float = RATE_WINDOW_S, maxlen: int = 512):
        self._window_ns, self._times, self._lock = int(window_s * 1e9), deque(maxlen=maxlen), threading.Lock()

    def note(self, mono_ns: int) -> None:
        with self._lock:
            self._times.append(mono_ns)

    def measured(self, now_ns: int) -> Optional[float]:
        with self._lock:
            recent = [value for value in self._times if now_ns - value <= self._window_ns]
        if len(recent) < 2 or recent[-1] <= recent[0]:
            return None
        return round((len(recent) - 1) * 1e9 / (recent[-1] - recent[0]), 3)


class _Source:
    """The state machine both sources share. A state change is remembered until somebody takes it, so a publisher can
    send the change at once instead of waiting for the next heartbeat."""

    def __init__(self, *, clock_ns: Callable[[], int], fresh_window_ms: float, rate_window_s: float, not_configured: str):
        self._clock_ns, self._fresh_ms = clock_ns, fresh_window_ms
        self._rate = _Rate(rate_window_s)
        self._lock = threading.RLock()  # reentrant: slot() freshens the state, which sets it
        self.state, self.reason = DISCONNECTED, not_configured
        self._changed = True
        self.open_failures = 0
        self.transport_faults = 0

    def _set(self, state: str, reason: Optional[str]) -> None:
        """The state and its reason move TOGETHER, under the lock: a reader must never see one of the two updated."""
        with self._lock:
            if (state, reason) != (self.state, self.reason):
                self.state, self.reason, self._changed = state, reason, True

    def take_change(self) -> bool:
        with self._lock:
            changed, self._changed = self._changed, False
            return changed

    def _age_ms(self, rx_mono_ns: Optional[int], now_ns: int) -> Optional[float]:
        return None if rx_mono_ns is None else round(max(0, now_ns - rx_mono_ns) / 1e6, 1)

    def _freshen(self, rx_mono_ns: Optional[int], now_ns: int) -> None:
        """connected while the newest sample is inside the window, stalled once it is not. Only these two states move
        here: 'connecting', 'failed' and 'disconnected' are decided by what the subscriber did, not by an age."""
        if rx_mono_ns is None or self.state not in (CONNECTED, STALLED):
            return
        if (now_ns - rx_mono_ns) / 1e6 <= self._fresh_ms:
            self._set(CONNECTED, None)
        else:
            self._set(STALLED, "no_sample_within_window")


class TapSource(_Source):
    """The host tap. It reads; it cannot write. endpoint None (and no factory) means the tap is not configured at all."""

    def __init__(self, endpoint: Optional[str] = None, *, client_factory: Optional[Callable[[], object]] = None, clock_ns: Callable[[], int] = time.monotonic_ns,
                 fresh_window_ms: float = FRESH_WINDOW_MS, rate_window_s: float = RATE_WINDOW_S, not_configured: str = "tap_not_configured"):
        super().__init__(clock_ns=clock_ns, fresh_window_ms=fresh_window_ms, rate_window_s=rate_window_s, not_configured=not_configured)
        self.endpoint, self._factory = endpoint, client_factory
        self._client = None
        self._last: Optional[dict] = None

    @property
    def configured(self) -> bool:
        return self._factory is not None or self.endpoint is not None

    def start(self) -> bool:
        if not self.configured:
            return False
        try:
            # The subscriber is given THIS source's clock: its receive times and the ages computed here must be one clock.
            self._client = self._factory() if self._factory is not None else TapClient(self.endpoint, clock_ns=self._clock_ns)
        except Exception:  # noqa: BLE001 -- the failure can carry the endpoint; only the reason code leaves this module
            self.open_failures += 1
            self._set(FAILED, "subscriber_failed")
            return False
        self._set(CONNECTING, "no_sample_yet")
        return True

    def pump(self, timeout_ms: int = 0) -> int:
        """Take whatever arrived and move the state. Never raises: the subscriber counts its own transport faults."""
        if self._client is None:
            return 0
        before = self._client.transport_errors
        samples = self._client.poll(timeout_ms)
        for sample in samples:
            self._rate.note(sample["rx_mono_ns"])
            self._last = sample
        if samples:
            self._set(CONNECTED, None)
        elif self._client.transport_errors > before:
            self.transport_faults += self._client.transport_errors - before
            self._set(FAILED, "transport_error")
        self._freshen(None if self._last is None else self._last["rx_mono_ns"], self._clock_ns())
        return len(samples)

    def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()
        self._set(DISCONNECTED, "stopped")

    def slot(self, now_ns: Optional[int] = None) -> dict:
        now = self._clock_ns() if now_ns is None else now_ns
        # Both are read ONCE into locals: a telemetry reader can ask for this slot while stop() is dropping the client.
        sample, client = self._last, self._client
        self._freshen(None if sample is None else sample["rx_mono_ns"], now)
        if sample is None or client is None or self.state != CONNECTED:
            record = unavailable_tap(self.reason or "no_sample_yet", self.state)
            if sample is not None:  # stalled or faulted: the LAST stamp and how long ago it came, never its values
                record["source_stamp"] = source_stamp(sample)
                record["receive_mono_ns"] = sample["rx_mono_ns"]
                record["receive_age_ms"] = self._age_ms(sample["rx_mono_ns"], now)
            return record
        stats = client.stats()
        return {
            "available": True, "state": self.state, "reason": None,
            "source_stamp": source_stamp(sample),
            "joints": joints_of(sample.get("observation")), "base_velocity": base_velocity_of(sample.get("observation")),
            "receive_mono_ns": sample["rx_mono_ns"], "receive_age_ms": self._age_ms(sample["rx_mono_ns"], now),
            "clock_domains": {"sample": CLOCK_PI, "receive": CLOCK_LAPTOP},
            "measured_rate_hz": self._rate.measured(now),
            "subscriber_gap_count": stats["subscriber_missed_count"], "publisher_drop_count": stats["publisher_drop_count"],
            "session_changes": stats["session_changes"], "malformed_count": stats["malformed_count"], "transport_errors": stats["transport_errors"],
            "lease": _lease(sample.get("lease")), "rejections": _numbers(sample.get("rejections")),
            "commands": _numbers(sample.get("commands")), "ingestion": _numbers(sample.get("ingestion")),
        }

    def link(self, now_ns: Optional[int] = None) -> dict:
        """The snapshot's link, decided by THIS source. connected only while fresh samples are arriving; the reason is
        carried by the tap slot, since the contract's link object has no field for one."""
        now = self._clock_ns() if now_ns is None else now_ns
        self._freshen(None if self._last is None else self._last["rx_mono_ns"], now)
        if self.state == CONNECTED and self._last is not None:
            return {"state": "connected", "source_stamp": source_stamp(self._last), "receive_age_ms": self._age_ms(self._last["rx_mono_ns"], now)}
        if not self.configured:
            return {"state": "unknown", "source_stamp": None, "receive_age_ms": None}
        stamp = source_stamp(self._last) if self._last is not None else None
        return {"state": "disconnected", "source_stamp": stamp, "receive_age_ms": self._age_ms(None if self._last is None else self._last["rx_mono_ns"], now)}


class PreviewSource(_Source):
    """The camera service's preview of one role. A picture for a human: never evidence, never an input to a decision."""

    def __init__(self, endpoint: Optional[str] = None, role: str = DEFAULT_ROLE, *, subscriber_factory: Optional[Callable[[], object]] = None,
                 clock_ns: Callable[[], int] = time.monotonic_ns, fresh_window_ms: float = FRESH_WINDOW_MS, rate_window_s: float = RATE_WINDOW_S,
                 not_configured: str = "preview_not_configured"):
        super().__init__(clock_ns=clock_ns, fresh_window_ms=fresh_window_ms, rate_window_s=rate_window_s, not_configured=not_configured)
        self.endpoint, self.role, self._factory = endpoint, role, subscriber_factory
        self._subscriber = None
        self._last_rx_ns: Optional[int] = None
        self._alive_rx_ns: Optional[int] = None  # the newest picture, or the newest accepted pause notice after it
        self._seen_yield_rx_ns: Optional[int] = None
        self._paused = False
        self._notice_pending = False
        self.yield_notices = 0
        self._encoded: Optional[tuple] = None  # (rx_seq, base64 text): one frame is encoded once, however often it is read

    @property
    def configured(self) -> bool:
        return self._factory is not None or self.endpoint is not None

    def start(self) -> bool:
        if not self.configured:
            return False
        try:
            self._subscriber = self._factory() if self._factory is not None else PreviewSubscriber(self.endpoint, self.role, clock_ns=self._clock_ns)
            self._subscriber.open()
        except Exception:  # noqa: BLE001 -- as in TapSource.start: the reason code leaves, the message does not
            self._subscriber = None
            self.open_failures += 1
            self._set(FAILED, "subscriber_failed")
            return False
        self._set(CONNECTING, "no_frame_yet")
        return True

    def pump(self, timeout_ms: int = POLL_MS) -> bool:
        """One poll. True when a new frame became the latest. Never raises into the caller."""
        if self._subscriber is None:
            return False
        try:
            fresh = bool(self._subscriber.poll_once(timeout_ms))
        except Exception:  # noqa: BLE001 -- a faulted socket is dropped and reopened by the pump thread
            self.transport_faults += 1
            self._drop()
            self._set(FAILED, "transport_error")
            return False
        now = self._clock_ns()
        if fresh:
            self._last_rx_ns = self._alive_rx_ns = now
            self._paused = False
            self._rate.note(now)
            self._set(CONNECTED, None)
        self._freshen(self._alive_rx_ns, now)
        if not fresh:
            self._take_yield_notice(now)
        return fresh

    def _take_yield_notice(self, now: int) -> None:
        """A NEW notice from the service session of the kept picture, while connected, is a sign of life with a reason.
        Anything else about a notice changes nothing: a stalled or connecting source stays what it is."""
        latest_yield = getattr(self._subscriber, "latest_yield", None)
        notice = latest_yield() if callable(latest_yield) else None
        if notice is None or notice.rx_mono_ns == self._seen_yield_rx_ns:
            return
        self._seen_yield_rx_ns = notice.rx_mono_ns
        self.yield_notices += 1
        frame = self._subscriber.latest() if self._subscriber is not None else None
        if self.state != CONNECTED or frame is None or notice.session != frame.session:
            return
        self._alive_rx_ns, self._paused, self._notice_pending = max(self._alive_rx_ns or 0, notice.rx_mono_ns), True, True
        self._set(CONNECTED, PAUSED_FOR_DECISION_CAPTURE)

    def take_notice(self) -> bool:
        """True once per accepted pause notice: the supervisor republishes the kept picture so the stream stays open."""
        with self._lock:
            pending, self._notice_pending = self._notice_pending, False
            return pending

    def _freshen(self, rx_mono_ns: Optional[int], now_ns: int) -> None:
        """As _Source._freshen, with the pause kept as the connected reason while it lasts. A stall ends the pause."""
        if rx_mono_ns is None or self.state not in (CONNECTED, STALLED):
            return
        if (now_ns - rx_mono_ns) / 1e6 <= self._fresh_ms:
            self._set(CONNECTED, PAUSED_FOR_DECISION_CAPTURE if self._paused else None)
        else:
            self._paused = False
            self._set(STALLED, "no_sample_within_window")

    def service_counters(self) -> Optional[dict]:
        """The camera service's own counters, as its newest message stated them, or None when nothing stated any."""
        subscriber = self._subscriber
        stats = subscriber.stats() if subscriber is not None else None
        service = stats.get("service") if isinstance(stats, dict) else None
        return dict(service) if isinstance(service, dict) else None

    def _drop(self) -> None:
        subscriber, self._subscriber = self._subscriber, None
        if subscriber is not None:
            try:
                subscriber.stop()
            except Exception:  # noqa: BLE001 -- closing a broken socket must not become the caller's problem
                pass

    def stop(self) -> None:
        self._drop()
        self._paused = False
        self._set(DISCONNECTED, "stopped")

    def latest(self):
        return None if self._subscriber is None else self._subscriber.latest()

    def slot(self, now_ns: Optional[int] = None, *, with_bytes: bool = True) -> dict:
        now = self._clock_ns() if now_ns is None else now_ns
        self._freshen(self._alive_rx_ns, now)
        subscriber = self._subscriber  # read ONCE: stop() may drop it while a telemetry reader is asking for this slot
        frame = None if subscriber is None else subscriber.latest()
        if frame is None or self.state != CONNECTED:
            record = unavailable_preview(self.reason or "no_frame_yet", self.state, self.role)
            if frame is not None:
                record["seq"] = frame.preview_seq
                record["receive_mono_ns"] = frame.rx_mono_ns
                record["receive_age_ms"] = self._age_ms(frame.rx_mono_ns, now)
            return record
        stats = subscriber.stats()
        service_latency = None
        if frame.capture_mono_ns is not None and frame.dequeue_mono_ns is not None:  # both are the SAME clock: this one is measurable
            service_latency = round(max(0, frame.dequeue_mono_ns - frame.capture_mono_ns) / 1e6, 3)
        service = stats.get("service") if isinstance(stats.get("service"), dict) else {}
        record = {
            "available": True, "state": self.state, "reason": self.reason, "role": frame.role,
            "seq": frame.preview_seq, "source_seq": frame.source_seq,
            "capture_clock": {"session": frame.session, "seq": frame.preview_seq, "sample_mono_ns": frame.capture_mono_ns,
                              "clock_domain": frame.capture_clock_domain or CLOCK_UNKNOWN},
            "capture_semantics": frame.timestamp_semantics,
            "capture_age_ms": None, "capture_age_reason": CAPTURE_AGE_REASON, "service_latency_ms": service_latency,
            "receive_mono_ns": frame.rx_mono_ns, "receive_age_ms": self._age_ms(frame.rx_mono_ns, now),
            "measured_fps": stats.get("measured_fps"), "frame": {"width_px": frame.width, "height_px": frame.height},
            "stream": {key: _finite(frame.stream.get(key)) if key != "pixel_format" else (frame.stream.get(key) if isinstance(frame.stream.get(key), str) else None)
                       for key in ("pixel_format", "source_fps_reported", "source_fps_measured", "source_width", "source_height")},
            # Contract counters, not the tracker's richer objects. See _count: a value nobody counted stays null.
            "gaps": _count(stats.get("gaps"), key="events"), "session_changes": _count(stats.get("session_changes")),
            "capture_time_stalls": _count(stats.get("capture_time_stalls")), "malformed": _count(stats.get("malformed")),
            # The service's own count of previews it stood aside for (header or notice); null when it sent none.
            "skipped_for_mission": _count(service.get("skipped_for_mission")),
        }
        if with_bytes:
            record["bytes_b64"] = self._base64(frame)
        return record

    def _base64(self, frame) -> str:
        if self._encoded is not None and self._encoded[0] == frame.rx_seq:
            return self._encoded[1]
        text = base64.b64encode(frame.jpeg).decode("ascii")
        self._encoded = (frame.rx_seq, text)
        return text


# ---- the supervisor of the two sources ---------------------------------------------------------------------------------------

def tap_endpoint(config) -> Optional[str]:
    """tcp://127.0.0.1:<port> for an ENABLED tap, else None. Read defensively: this module does not own config.py."""
    tap = getattr(config, "tap", None)
    if tap is None or not getattr(tap, "enabled", False):
        return None
    port = getattr(tap, "port", 0)
    return f"tcp://127.0.0.1:{int(port)}" if isinstance(port, int) and 0 < port < 65536 else None


def preview_endpoint(config, role: str = DEFAULT_ROLE, *, port: Optional[int] = None) -> Optional[str]:
    """Where ONE role's preview is published, or None when that role has no preview.

    THE DEFECT THIS SHAPE EXISTS FOR: the endpoint used to be derived by adding a per-role offset to one number, while
    an explicitly given `port` was returned verbatim WITHOUT the offset. So an explicit port resolved identically for
    every role: one socket served both cameras, and a front preview would have carried the wrist picture with nothing
    anywhere saying so. A port is now per role and is never computed from another role's.

    The order is: an explicit port for THIS role (a caller that already knows it), then the `preview` section, which
    names each role's port on its own. Independent of the tap: a preview is a camera picture, the tap is the host's
    observation stream, and the baseline NORMAL host supports preview with no tap at all. Nothing here reaches a
    machine; it is always loopback.
    """
    if port is not None:
        return f"tcp://127.0.0.1:{int(port)}" if 0 < int(port) < 65536 else None
    section = getattr(config, "preview", None)
    if section is None or not getattr(section, "enabled", False):
        return None
    port_for = getattr(section, "port_for", None)
    configured = port_for(role) if callable(port_for) else None
    return f"tcp://127.0.0.1:{configured}" if isinstance(configured, int) and 0 < configured < 65536 else None


def preview_roles(config) -> tuple:
    """The roles that actually have a preview, in the order a screen reads them. Empty when none is configured, which
    is a state the screen says out loud rather than a failure."""
    section = getattr(config, "preview", None)
    if section is None or not getattr(section, "enabled", False):
        return ()
    roles = getattr(section, "roles", None)
    return tuple(roles()) if callable(roles) else ()


class SourceSupervisor:
    """Owns the two sources, one polling thread each, and publishes their slots into the telemetry hub.

    It is not the command supervisor and knows nothing about it: no journal, no run, no process, no signal. The daemon
    starts it, reads link() for a snapshot, and stops it. A source that is not configured still publishes ONE slot, so
    the console says 'not configured' with a reason instead of staying silent.
    """

    def __init__(self, hub, *, tap: Optional[TapSource] = None, preview: Optional[PreviewSource] = None,
                 previews: Optional[dict] = None, clock_ns: Callable[[], int] = time.monotonic_ns,
                 poll_ms: int = POLL_MS, heartbeat_s: float = HEARTBEAT_S, tap_min_interval_s: float = TAP_MIN_INTERVAL_S, reopen_s: float = REOPEN_S):
        # ONE SOURCE PER CAMERA ROLE, each with its own socket, its own thread, its own state and its own hub topic, so
        # that a camera that stalls or fails cannot clear, freeze or speak for the other one. `preview=` still names the
        # default role's source, which is what a caller with one camera means.
        if previews is None:
            previews = {DEFAULT_ROLE: preview if preview is not None else PreviewSource()}
        self.hub, self.tap = hub, tap if tap is not None else TapSource()
        self.previews = dict(previews)
        self._clock_ns, self._poll_ms, self._heartbeat_ns = clock_ns, poll_ms, int(heartbeat_s * 1e9)
        self._tap_min_ns, self._reopen_ns = int(tap_min_interval_s * 1e9), int(reopen_s * 1e9)
        self._halt = threading.Event()
        self._threads: list = []
        self._tap_published_ns = 0
        self._preview_published_ns = {role: 0 for role in self.previews}
        self.tap_publishes = 0
        self.preview_publishes = 0

    @property
    def preview(self) -> PreviewSource:
        """The default role's source. Kept because a caller with one camera means exactly this one."""
        return self.previews.get(DEFAULT_ROLE) or next(iter(self.previews.values()))

    @classmethod
    def from_config(cls, hub, config, **options) -> "SourceSupervisor":
        """One preview source per role the configuration NAMES. A configuration that names none still gets a single
        unconfigured source, so the console says 'not configured' with a reason instead of showing an empty panel."""
        roles = preview_roles(config) or (DEFAULT_ROLE,)
        previews = {role: PreviewSource(preview_endpoint(config, role), role) for role in roles}
        return cls(hub, tap=TapSource(tap_endpoint(config)), previews=previews, **options)

    # -- lifetime
    def start(self) -> None:
        self.tap.start()
        self.publish_tap()
        for role, preview in self.previews.items():
            preview.start()
            self.publish_preview(role=role)
        if self.tap.configured:
            self._threads.append(self._thread(self._run_tap, "tap-source"))
        for role, preview in self.previews.items():
            if preview.configured:
                # One thread per camera: a socket that blocks or a source that fails stalls its OWN role and no other.
                self._threads.append(self._thread(lambda role=role: self._run_preview(role), f"preview-source-{role}"))

    def _thread(self, target, name: str) -> threading.Thread:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._halt.set()
        for thread in self._threads:
            thread.join(2.0 + self._poll_ms / 1000.0)
        self._threads = []
        self.tap.stop()
        for preview in self.previews.values():
            preview.stop()

    # -- publishing
    def publish_tap(self, now_ns: Optional[int] = None) -> None:
        self.hub.publish("tap", self.tap.slot(now_ns))
        self.tap_publishes += 1

    def publish_preview(self, now_ns: Optional[int] = None, *, role: Optional[str] = None) -> None:
        name = DEFAULT_ROLE if role is None else role
        source = self.previews.get(name)
        if source is None:
            return
        self.hub.publish(preview_topic(name), source.slot(now_ns))
        self.preview_publishes += 1

    def pump_once(self, timeout_ms: int = 0) -> None:
        """One poll of each source with its publishing rule applied. The threads call the same code; a test calls this."""
        self._pump_tap(timeout_ms)
        for role in self.previews:
            self._pump_preview(timeout_ms, role)

    def _pump_tap(self, timeout_ms: int) -> None:
        received = self.tap.pump(timeout_ms)
        now = self._clock_ns()
        changed = self.tap.take_change()
        due = now - self._tap_published_ns >= (self._tap_min_ns if received else self._heartbeat_ns)
        if changed or due:
            self._tap_published_ns = now
            self.publish_tap(now)

    def _pump_preview(self, timeout_ms: int, role: str = DEFAULT_ROLE) -> None:
        source = self.previews.get(role)
        if source is None:
            return
        fresh = source.pump(timeout_ms)
        now = self._clock_ns()
        changed = source.take_change()
        noticed = source.take_notice()
        # A frame is published as it arrives. A record WITHOUT a picture ends the browser's stream, so one is only
        # published when the source is not connected: a heartbeat in between would cut a healthy stream. A pause notice
        # republishes the KEPT picture with the pause as its reason, which keeps the stream open and says why.
        if fresh or changed or noticed or (source.state != CONNECTED and now - self._preview_published_ns.get(role, 0) >= self._heartbeat_ns):
            self._preview_published_ns[role] = now
            self.publish_preview(now, role=role)

    def _run_tap(self) -> None:
        while not self._halt.is_set():
            self._pump_tap(self._poll_ms)

    def _run_preview(self, role: str = DEFAULT_ROLE) -> None:
        """One camera's loop. It reopens ITS OWN source after a failure and touches no other role, which is what keeps
        one dead camera from taking the other down with it."""
        source = self.previews.get(role)
        if source is None:
            return
        reopen_at = 0
        while not self._halt.is_set():
            if source.state == FAILED and source.configured:
                now = self._clock_ns()
                if now < reopen_at:
                    self._halt.wait(min(0.2, self._poll_ms / 1000.0))
                    continue
                reopen_at = now + self._reopen_ns
                source.start()
            self._pump_preview(self._poll_ms, role)

    # -- views
    def link(self, now_ns: Optional[int] = None) -> dict:
        return self.tap.link(now_ns)

    def latest_preview(self, *, with_bytes: bool = True, role: Optional[str] = None) -> dict:
        """The LATEST record of ONE camera, the same one that role's telemetry slot carries. Latest-only: there is no
        queue to fall behind on, and a slow reader simply misses the frames in between.

        A role that is not configured gets an honest unavailable record naming that role, never another role's picture.
        """
        name = DEFAULT_ROLE if role is None else role
        source = self.previews.get(name)
        if source is None:
            return unavailable_preview("preview_role_not_configured", DISCONNECTED, name)
        return source.slot(with_bytes=with_bytes)

    def roles(self) -> tuple:
        """The camera roles this supervisor owns, in the order a screen reads them."""
        return tuple(role for role in PREVIEW_ROLES if role in self.previews) or tuple(self.previews)

    def report(self) -> dict:
        """What `status` says about the sources: states, reason codes and the counters, never an endpoint. Each camera
        reports ON ITS OWN, so one that failed is visible beside one that is fine."""
        previews = {
            role: {"configured": source.configured, "role": source.role, "state": source.state, "reason": source.reason,
                   "open_failures": source.open_failures, "transport_faults": source.transport_faults,
                   "yield_notices": source.yield_notices, "service": source.service_counters()}
            for role, source in self.previews.items()}
        return {
            "tap": {"configured": self.tap.configured, "state": self.tap.state, "reason": self.tap.reason,
                    "open_failures": self.tap.open_failures, "transport_faults": self.tap.transport_faults, "publishes": self.tap_publishes},
            "preview": {**previews[self.preview.role], "publishes": self.preview_publishes} if self.preview.role in previews else dict(previews[next(iter(previews))]),
            "previews": previews,
        }
