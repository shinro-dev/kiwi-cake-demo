# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.tap_client: a read-only subscriber of the host tap (bin/pi/host_boundary.py on the robot computer).

The host publishes, for every observation it sends to the controller, the same sample with its stamp (session, seq,
the host's monotonic sample time), the host's wall time, the lease status, the rejection counters and the command
timing it measured at the source. This module reads that PUB socket through an endpoint it is given (the runtime owns
the ssh forward) and hands out samples. It sends nothing: a SUB socket cannot, and nothing a subscriber does changes
which observations the PUSH/PULL controller receives. See docs/autonomous-demo/host-boundary.md.

The endpoint must be a loopback or in-process one (check_endpoint), as in demo_runtime.preview_client: the host binds
the tap to its own loopback address, so an endpoint naming another machine can only be a mistake, and a subscriber
would take whatever answered there for the robot. The refused endpoint is not repeated in the error.

TWO KINDS OF LOSS, REPORTED SEPARATELY.
  missed_before            seq numbers absent between the previous sample THIS subscriber received and this one.
  publisher_drops_before   how many of those the PUBLISHER says it failed to send (the growth of its own drop counter).
The difference was lost after a successful send: by libzmq for a slow subscriber, or on the way. A subscriber that
connects late, or reconnects, sees such a gap too. Neither number says anything about what the controller received.

CLOCKS. rx_mono_ns and every age computed here are the LAPTOP's monotonic clock. host_sample_mono_ns is the robot
computer's monotonic clock and host_wall_time_s its wall clock; they are handed on as the host wrote them and are
never subtracted from a laptop value, here or downstream.

Never raises into the caller: a transport fault is counted, the socket is reopened on the next poll, and a message
that is not a tap sample is counted and skipped. zmq is imported on first use. One instance, one thread.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Iterator, Optional

TAP_VERSION = 1  # mirrors bin/pi/host_boundary.py, pinned by tests/test_tap_client.py
SESSION_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"  # common.schema.json, $defs/source_stamp/properties/session/pattern; pinned by the same test
CLOCK_PI = "pi_monotonic"  # one of common.schema.json $defs/clock_domain/enum
DEFAULT_RCVHWM = 64
_SESSION = re.compile(SESSION_PATTERN)
# The host binds its tap to its own loopback address and the runtime reaches it through an ssh forward, so the only
# endpoints that can name that socket are a local one and this process's own. An address of another machine is a
# configuration mistake, and this subscriber would believe whatever answered there; it is refused, and never repeated.
_LOOPBACK_ENDPOINT = re.compile(r"^tcp://(?:127\.0\.0\.1|localhost):([0-9]{1,5})$")
_INPROC_ENDPOINT = re.compile(r"^inproc://[A-Za-z0-9_.:-]{1,128}$")


def check_endpoint(endpoint: str) -> str:
    """The endpoint unchanged, or ValueError. The same rule as demo_runtime.preview_client.check_endpoint, with the
    in-process transport allowed as well: it cannot leave this process, let alone this machine."""
    if isinstance(endpoint, str):
        loopback = _LOOPBACK_ENDPOINT.fullmatch(endpoint)
        if _INPROC_ENDPOINT.fullmatch(endpoint) or (loopback and 1 <= int(loopback.group(1)) <= 65535):
            return endpoint
    raise ValueError("the tap endpoint must be tcp://127.0.0.1:<port> or inproc://<name>: the host tap is reached "
                     "through an ssh forward the runtime owns, never by addressing another machine")


def _count(value) -> Optional[int]:
    return value if type(value) is int and value >= 0 else None


def parse_tap_message(raw) -> Optional[dict]:
    """The message as a dict when it is a tap sample this version understands, else None. Never raises."""
    try:
        message = json.loads(bytes(raw).decode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(message, dict) or message.get("kc.tap") != TAP_VERSION:
        return None
    session, seq = message.get("session"), _count(message.get("seq"))
    if not isinstance(session, str) or not _SESSION.fullmatch(session) or seq is None:
        return None
    return message


def source_stamp(sample: dict) -> dict:
    """The console contract's source_stamp for a sample. The time is the HOST's, and the clock domain says so."""
    return {"session": sample["session"], "seq": sample["seq"], "sample_mono_ns": sample["host_sample_mono_ns"], "clock_domain": CLOCK_PI}


class TapClient:
    def __init__(self, endpoint: str, *, context=None, socket_factory: Optional[Callable[[], object]] = None, clock_ns: Callable[[], int] = time.monotonic_ns,
                 rcvhwm: int = DEFAULT_RCVHWM):
        self._endpoint, self._context, self._socket_factory = check_endpoint(endpoint), context, socket_factory
        self._clock_ns, self._rcvhwm = clock_ns, rcvhwm
        self._socket = None
        self._previous = None  # (session, seq, publisher drop count) of the last sample received
        self.received_count = 0
        self.malformed_count = 0
        self.transport_errors = 0
        self.session_changes = 0  # the host restarted; its seq starts over, and that is not a gap
        self.out_of_order_count = 0  # a seq that repeats or goes back inside one session
        self.subscriber_gap_events = 0
        self.subscriber_missed_count = 0  # samples this subscriber did not receive, whoever lost them
        self.publisher_drop_count: Optional[int] = None  # the host's own number, as last reported; not a laptop measurement
        self.last_sample: Optional[dict] = None

    def _open(self):
        if self._socket is None:
            if self._socket_factory is not None:
                self._socket = self._socket_factory()
            else:
                import zmq
                socket = (self._context or zmq.Context.instance()).socket(zmq.SUB)
                socket.setsockopt(zmq.LINGER, 0)
                socket.setsockopt(zmq.RCVHWM, self._rcvhwm)
                socket.connect(self._endpoint)
                socket.subscribe(b"")
                self._socket = socket
        return self._socket

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                socket.close(0)
            except Exception:
                pass

    def poll(self, timeout_ms: int = 0, max_messages: int = 512) -> list:
        """Every sample that is available, oldest first, after waiting up to timeout_ms for the first one."""
        samples = []
        try:
            socket = self._open()
            if not socket.poll(timeout_ms):
                return samples
            for _ in range(max_messages):  # bounded by messages READ, so a stream of junk cannot hold the caller either
                if not socket.poll(0):
                    break
                frames = socket.recv_multipart()
                sample = self._accept(frames[0] if len(frames) == 1 else None)
                if sample is not None:
                    samples.append(sample)
        except Exception:
            self.transport_errors += 1
            self.close()
        return samples

    def samples(self, poll_timeout_ms: int = 100, should_stop: Optional[Callable[[], bool]] = None) -> Iterator[dict]:
        """Yields samples as they arrive, until should_stop() is true (it is asked between polls)."""
        while should_stop is None or not should_stop():
            yield from self.poll(poll_timeout_ms)

    def receive_age_ns(self, sample: Optional[dict] = None) -> Optional[int]:
        """How long ago the sample ARRIVED here, by the laptop's monotonic clock. Not the age of the measurement."""
        sample = self.last_sample if sample is None else sample
        return None if sample is None else max(0, self._clock_ns() - sample["rx_mono_ns"])

    def _accept(self, raw) -> Optional[dict]:
        rx_mono_ns = self._clock_ns()
        message = parse_tap_message(raw) if raw is not None else None
        if message is None:
            self.malformed_count += 1
            return None
        session, seq = message["session"], message["seq"]
        publisher = message.get("publisher") if isinstance(message.get("publisher"), dict) else {}
        drops = _count(publisher.get("drop_count"))
        missed = publisher_drops = None
        seq_floor = 0
        session_changed = self._previous is not None and self._previous[0] != session
        if session_changed:
            self.session_changes += 1
        elif self._previous is not None:
            if seq <= self._previous[1]:
                self.out_of_order_count += 1
                missed, seq_floor = 0, self._previous[1]  # the baseline does not go back: the next real sample is not a false gap
            else:
                missed = seq - self._previous[1] - 1
                if missed:
                    self.subscriber_gap_events += 1
                    self.subscriber_missed_count += missed
            if drops is not None and self._previous[2] is not None and drops >= self._previous[2]:
                publisher_drops = drops - self._previous[2]
        self._previous = (session, max(seq, seq_floor), drops)
        self.received_count += 1
        self.publisher_drop_count = drops
        self.last_sample = {
            "session": session, "seq": seq, "host_sample_mono_ns": _count(message.get("t_mono_ns")), "host_read_ns": _count(message.get("read_ns")),
            "host_wall_time_s": message.get("wall_time_s"), "rx_mono_ns": rx_mono_ns, "rx_index": self.received_count, "session_changed": session_changed,
            "missed_before": missed, "publisher_drops_before": publisher_drops,
            "lost_after_send_before": None if missed is None or publisher_drops is None else max(0, missed - publisher_drops),
            "observation": message.get("observation"), "lease": message.get("lease"), "rejections": message.get("rejections"),
            "commands": message.get("commands"), "ingestion": message.get("ingestion"), "publisher": publisher}
        return self.last_sample

    def stats(self) -> dict:
        return {"received_count": self.received_count, "malformed_count": self.malformed_count, "transport_errors": self.transport_errors,
                "session_changes": self.session_changes, "out_of_order_count": self.out_of_order_count, "subscriber_gap_events": self.subscriber_gap_events,
                "subscriber_missed_count": self.subscriber_missed_count, "publisher_drop_count": self.publisher_drop_count}
