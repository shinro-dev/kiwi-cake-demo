#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""bin/pi/host_boundary.py: command ownership where commands ENTER the stock LeKiwi host, a stamp on every observation
it sends, and a read-only tap. Installed by bin/pi/lekiwi_host_safe_attach.py, and only when KC_HOST_BOUNDARY=1.
See docs/autonomous-demo/host-boundary.md. Written against lerobot at commit b4e2d0b6.

WHY AT INGESTION. The stock loop reads ONE message per iteration from a PULL socket created with CONFLATE=1, hands it to
robot.send_action() and only then refreshes last_cmd_time, the 500 ms watchdog that stops the BASE. With CONFLATE a
second client's message can displace the owner's newest command inside the socket, before any check in send_action
could run, and an exception raised there is caught, logged and costs the owner its command. So the check sits where the
loop calls recv_string(): the stock socket is replaced by CommandGate, over a PULL socket WITHOUT conflate. Each call
drains what is pending, keeps the NEWEST VALID command, removes every kc.* key and returns the rest as the JSON text.
With no valid command it raises zmq.Again, exactly as an empty socket does, and the stock loop takes its "no command"
branch: no motor write, no watchdog refresh, no exception, no shutdown. Rejections are COUNTED by reason, never raised.

OWNERSHIP METADATA, on every command, as NUMBERS (the stock client coerces every action value to float):
  kc.lease    the lease generation
  kc.session  the host session, a random whole number below 2**48 chosen at install (exact as a float)
Both must equal the live lease. The same session is written into observations and replies as its DECIMAL TEXT, because
the console contract (common.schema.json, source_stamp.session) and the freshness client take a string there.

LEASE, over a separate REP socket bound to the loopback address only. It is not the command socket: nothing received on
it can reach robot.send_action() or last_cmd_time. acquire is refused while a lease is live; every acquisition
increments the generation, so commands of an earlier owner stop being valid; a valid owner command extends the lease;
an EXPIRED lease rejects commands and does nothing else: it transfers nothing, writes no motor, leaves the gripper
alone. The stock watchdog then stops the base and the arm holds its goal. A new acquisition is always explicit.

STAMP AND TAP. get_observation() is wrapped: the dict the stock loop sends on PUSH gains kc.session, kc.seq and
kc.t_mono_ns (this computer's monotonic clock, read immediately BEFORE the bus read, so a sample is never older than
its stamp says). The same sample, with wall time, lease status, rejection counters and command timing measured here,
at the source, is published on a PUB socket (loopback, small SNDHWM, NOBLOCK). PUB/SUB cannot change what the
PUSH/PULL controller receives. publisher_drop_count counts sends that FAILED in this process; what libzmq drops for a
slow subscriber is visible only to that subscriber, as a gap in seq. The two are different facts.

NO MOTOR ACCESS. This module never touches the robot's bus and calls nothing on the robot except the stock
get_observation it wraps. It runs entirely on the host loop's own thread: no thread, no lock.

This is cooperative ownership, not authentication: a client that copies the two numbers is accepted, and a process on
this computer can open the servo bus without asking anyone. Both residuals are stated in the document above.
"""
from __future__ import annotations

import ast
import functools
import hashlib
import json
import math
import os
import re
import sys
import time

ENV_FLAG = "KC_HOST_BOUNDARY"
CONTROL_PORT_ENV, TAP_PORT_ENV = "KC_HOST_BOUNDARY_CONTROL_PORT", "KC_HOST_BOUNDARY_TAP_PORT"
DEFAULT_CONTROL_PORT, DEFAULT_TAP_PORT = 5557, 5558
LOOPBACK = "127.0.0.1"

META_PREFIX = "kc."
LEASE_KEY, SESSION_KEY = "kc.lease", "kc.session"  # on commands, as numbers
SEQ_KEY, SAMPLE_KEY = "kc.seq", "kc.t_mono_ns"  # on observations, next to kc.session as text
TAP_VERSION = 1

# The longest quiet perception phase of a cycle is about 90 s: no command is sent during it, so nothing extends the lease.
DEFAULT_TTL_S, MIN_TTL_S, MAX_TTL_S = 180.0, 1.0, 900.0
COMMAND_RCVHWM = 16  # per connected client; the loop drains every iteration
TAP_SNDHWM = 8
MAX_DRAIN_PER_CALL = 256  # a flood costs the owner latency, never its place
MAX_COMMAND_BYTES = 16384
MAX_CONTROL_PER_TICK = 8
MAX_CONTROL_BYTES = 4096
REBIND_WAIT_S = 5.0
REJECTION_LOG_INTERVAL_S = 5.0

NO_LEASE, WRONG_GENERATION, WRONG_SESSION, MALFORMED = "no_lease", "wrong_generation", "wrong_session", "malformed"
REJECTION_REASONS = (NO_LEASE, WRONG_GENERATION, WRONG_SESSION, MALFORMED)
FREE, HELD, EXPIRED = "free", "held", "expired"

LABEL_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"  # the contract's id shape; a label is shown on the tap, so it is never free text
_LABEL = re.compile(LABEL_PATTERN)
_EXACT_FLOAT = 2 ** 53


def new_host_session() -> int:
    """A random whole number in [1, 2**48): it survives float() and JSON unchanged. It identifies a host process; it is
    sent with every observation and is NOT a secret."""
    return int.from_bytes(os.urandom(6), "big") or 1


def whole_number(value):
    """int for a JSON number that is a whole number a float holds exactly, else None. bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or value != math.floor(value)):
        return None
    return int(value) if abs(value) <= _EXACT_FLOAT else None


def numeric_field(value):
    """whole_number, and additionally the PLAIN DECIMAL TEXT of one: the host reports its session as text and the
    client sends that text back. Nothing else, and never int() on an unchecked string: str.isdigit() is true for
    characters int() rejects (a superscript two, for one), and that ValueError used to leave the control socket
    unanswerable for the life of the process."""
    if isinstance(value, str):
        return whole_number(int(value)) if value.isascii() and value.isdecimal() and len(value) <= 16 else None
    return whole_number(value)


class Counters:
    def __init__(self):
        self.rejections = dict.fromkeys(REJECTION_REASONS, 0)
        self.received = 0  # every message read from the command socket
        self.superseded = 0  # valid, but a newer valid command was pending in the same drain
        self.drain_cap_hits = 0
        self.internal_errors = 0  # a fault of this module, swallowed so that the host loop never sees it
        self.control_requests = 0
        self.control_reply_failures = 0

    def reject(self, reason: str) -> None:
        self.rejections[reason] += 1

    def report(self) -> dict:
        return {"received": self.received, "superseded": self.superseded, "drain_cap_hits": self.drain_cap_hits, "internal_errors": self.internal_errors,
                "control_requests": self.control_requests, "control_reply_failures": self.control_reply_failures}


class Lease:
    """The ownership state. Pure: a clock and no socket. Expiry is noticed when the state is next looked at, and looking
    is all that happens: nothing is handed over and nothing is commanded."""

    def __init__(self, host_session: int, clock=time.monotonic, on_event=None):
        self.host_session = int(host_session)
        self.session_text = str(self.host_session)
        self.generation = 0  # 0 = never acquired; the first acquisition is generation 1
        self._clock = clock
        self._on_event = on_event or (lambda name, detail: None)
        self._owner = None  # {"label", "ttl_s", "expires_at", "request_id"} while held
        self._lapsed = False
        self.acquisitions = self.releases = self.expiries = 0

    def state(self) -> str:
        if self._owner is None:
            return EXPIRED if self._lapsed else FREE
        if self._clock() >= self._owner["expires_at"]:
            label, self._owner, self._lapsed = self._owner["label"], None, True
            self.expiries += 1
            self._on_event("expired", f"generation {self.generation} held by {label}: commands are rejected until an explicit new acquisition")
            return EXPIRED
        return HELD

    def status(self) -> dict:
        state, owner = self.state(), self._owner
        return {"state": state, "host_session": self.session_text, "generation": self.generation,
                "owner_label": owner["label"] if owner else None, "request_id": owner["request_id"] if owner else None,
                "ttl_s": owner["ttl_s"] if owner else None,
                "expires_in_ms": max(0.0, (owner["expires_at"] - self._clock()) * 1000.0) if owner else None,
                "acquisitions": self.acquisitions, "releases": self.releases, "expiries": self.expiries}

    def _refuse(self, reason: str) -> dict:
        return {"ok": False, "reason": reason, **self.status()}

    def acquire(self, owner_label, ttl_s=None, request_id=None) -> dict:
        if not isinstance(owner_label, str) or not _LABEL.fullmatch(owner_label):
            return self._refuse("malformed_request")
        if request_id is not None and (not isinstance(request_id, str) or not _LABEL.fullmatch(request_id)):
            return self._refuse("malformed_request")
        if ttl_s is None:
            ttl_s = DEFAULT_TTL_S
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float)) or not math.isfinite(ttl_s) or ttl_s <= 0:
            return self._refuse("malformed_request")
        if self.state() == HELD:
            return self._refuse("lease_held")
        ttl_s = min(max(float(ttl_s), MIN_TTL_S), MAX_TTL_S)  # the reply states what was granted
        self.generation += 1
        self.acquisitions += 1
        self._owner, self._lapsed = {"label": owner_label, "ttl_s": ttl_s, "expires_at": self._clock() + ttl_s, "request_id": request_id}, False
        self._on_event("acquired", f"generation {self.generation} by {owner_label}, ttl {ttl_s:g} s")
        return {"ok": True, **self.status()}

    def _owner_check(self, host_session, generation):
        session, generation = numeric_field(host_session), whole_number(generation)
        if session is None or generation is None:
            return "malformed_request"
        if session != self.host_session:
            return WRONG_SESSION
        if generation != self.generation:
            return WRONG_GENERATION
        return None

    def renew(self, host_session, generation) -> dict:
        problem = self._owner_check(host_session, generation)
        if problem is None:
            problem = {FREE: NO_LEASE, EXPIRED: "lease_expired"}.get(self.state())
        if problem is not None:
            return self._refuse(problem)
        self.extend()
        return {"ok": True, **self.status()}

    def release(self, host_session, generation) -> dict:
        problem = self._owner_check(host_session, generation)
        if problem is None and self.state() == FREE:
            problem = NO_LEASE
        if problem is not None:
            return self._refuse(problem)
        label = self._owner["label"] if self._owner else None
        self._owner, self._lapsed = None, False  # an expired lease may be released too: it ends up free either way
        self.releases += 1
        self._on_event("released", f"generation {self.generation}" + (f" by {label}" if label else " (it had expired)"))
        return {"ok": True, **self.status()}

    def extend(self) -> None:
        if self._owner is not None:
            self._owner["expires_at"] = self._clock() + self._owner["ttl_s"]

    def admit(self, message: dict):
        """None when the command belongs to the live lease, else the rejection reason. Order: a host without a live lease
        and a command without metadata are both no_lease; then malformed metadata; then the session; then the generation."""
        if self.state() != HELD:
            return NO_LEASE
        if LEASE_KEY not in message and SESSION_KEY not in message:
            return NO_LEASE
        generation, session = whole_number(message.get(LEASE_KEY)), whole_number(message.get(SESSION_KEY))
        if generation is None or session is None:
            return MALFORMED
        if session != self.host_session:
            return WRONG_SESSION
        if generation != self.generation:
            return WRONG_GENERATION
        return None


class CommandTiming:
    """Timing of the commands DISPATCHED to the host loop, on this computer's monotonic clock. Counted per lease."""

    def __init__(self, clock_ns=time.monotonic_ns):
        self._clock_ns = clock_ns
        self.dispatched_total = 0
        self._last_ns = None
        self.lease_started()

    def lease_started(self) -> None:
        self._lease_start_ns = self._clock_ns()
        self.dispatched_in_lease = 0
        self._last_in_lease_ns = self._last_gap_ns = self._max_gap_ns = self._first_after_lease_ns = None

    def dispatched(self) -> None:
        now = self._clock_ns()
        if self._last_in_lease_ns is None:
            self._first_after_lease_ns = now - self._lease_start_ns
        else:
            self._last_gap_ns = now - self._last_in_lease_ns
            self._max_gap_ns = self._last_gap_ns if self._max_gap_ns is None else max(self._max_gap_ns, self._last_gap_ns)
        self._last_in_lease_ns = self._last_ns = now
        self.dispatched_total += 1
        self.dispatched_in_lease += 1

    def report(self) -> dict:
        def ms(value):
            return None if value is None else value / 1e6
        # since_last_ms is the OPEN interval at the moment of this report; max_gap_ms_in_lease holds closed gaps only.
        return {"dispatched_total": self.dispatched_total, "dispatched_in_lease": self.dispatched_in_lease, "last_gap_ms": ms(self._last_gap_ns),
                "max_gap_ms_in_lease": ms(self._max_gap_ns), "first_after_lease_ms": ms(self._first_after_lease_ns),
                "since_last_ms": ms(None if self._last_ns is None else self._clock_ns() - self._last_ns)}


class CommandGate:
    """Stands where host.zmq_cmd_socket stood. The stock loop calls recv_string(zmq.NOBLOCK) and, at shutdown, close()."""

    def __init__(self, socket, lease: Lease, counters: Counters, timing: CommandTiming, zmq_module, also_close=()):
        self._socket, self._lease, self._counters, self._timing, self._zmq = socket, lease, counters, timing, zmq_module
        self._also_close = list(also_close)
        self.closed = False

    def recv_string(self, flags=0, encoding="utf-8"):
        """The newest valid command without its kc.* keys, as JSON text; zmq.Again when there is none. Never blocks,
        whatever the flags: the stock loop passes NOBLOCK and a blocking read would stall the observations."""
        try:
            newest = self._drain()
        except Exception:  # a fault here must look like an empty socket: no dispatch, no watchdog refresh, no shutdown
            self._counters.internal_errors += 1
            newest = None
        if newest is None:
            raise self._zmq.Again()
        return newest

    def _drain(self):
        newest = None
        for _ in range(MAX_DRAIN_PER_CALL):
            try:
                frames = self._socket.recv_multipart(self._zmq.NOBLOCK)
            except self._zmq.Again:
                break
            self._counters.received += 1
            command = self._admit(frames)
            if command is not None:
                if newest is not None:
                    self._counters.superseded += 1
                newest = command
        else:
            self._counters.drain_cap_hits += 1
        if newest is None:
            return None
        text = json.dumps(newest)
        self._timing.dispatched()
        return text

    def _admit(self, frames):
        if len(frames) != 1 or len(frames[0]) > MAX_COMMAND_BYTES:
            return self._counters.reject(MALFORMED)
        try:
            message = json.loads(bytes(frames[0]).decode("utf-8"))
        except (ValueError, RecursionError):  # UnicodeDecodeError and json.JSONDecodeError are ValueErrors
            return self._counters.reject(MALFORMED)
        if not isinstance(message, dict):
            return self._counters.reject(MALFORMED)
        reason = self._lease.admit(message)
        if reason is not None:
            return self._counters.reject(reason)
        self._lease.extend()
        return {key: value for key, value in message.items() if not key.startswith(META_PREFIX)}

    def close(self, *args, **kwargs) -> None:
        self.closed = True
        for socket in [self._socket] + self._also_close:
            try:
                socket.close(0)
            except Exception:
                pass


class ControlService:
    """acquire / renew / release / status over the REP socket. Served from the observation path, never from recv_string:
    a lease request can neither return a command to the loop nor keep the watchdog quiet."""

    def __init__(self, socket, lease: Lease, counters: Counters, zmq_module):
        self._socket, self._lease, self._counters, self._zmq = socket, lease, counters, zmq_module
        self._unsent = None  # a REP socket must answer before it may read again

    def serve(self) -> None:
        for _ in range(MAX_CONTROL_PER_TICK):
            if self._unsent is None:
                try:
                    frames = self._socket.recv_multipart(self._zmq.NOBLOCK)
                except self._zmq.Again:
                    return
                self._counters.control_requests += 1
                self._unsent = self._reply_bytes(frames)
            try:
                self._socket.send(self._unsent, self._zmq.NOBLOCK)
                self._unsent = None
            except self._zmq.Again:
                self._counters.control_reply_failures += 1
                return

    def _reply_bytes(self, frames) -> bytes:
        """EVERY received request becomes a reply. A REP socket that has received and not answered refuses to read
        again (EFSM), and that error is not zmq.Again: one handler fault would otherwise wedge the control socket and
        the tap with it, for the life of the host, since serve() runs on the observation path. So a fault here is
        counted and answered instead of raised."""
        try:
            return json.dumps(self.handle_frames(frames)).encode("utf-8")
        except Exception:
            self._counters.internal_errors += 1
            return json.dumps(self._fault_reply()).encode("utf-8")

    def _fault_reply(self) -> dict:
        reply = {"ok": False, "reason": "internal_error"}
        try:  # the lease status if it can be read: a holder must not mistake this reply for a restarted host
            reply.update(self._lease.status())
        except Exception:
            pass
        return reply

    def handle_frames(self, frames) -> dict:
        try:
            if len(frames) != 1 or len(frames[0]) > MAX_CONTROL_BYTES:
                raise ValueError("frames")
            request = json.loads(bytes(frames[0]).decode("utf-8"))
        except (ValueError, RecursionError):
            return {"ok": False, "reason": "malformed_request", **self._lease.status()}
        return self.handle(request)

    def handle(self, request) -> dict:
        if not isinstance(request, dict):
            return {"ok": False, "reason": "malformed_request", **self._lease.status()}
        op = request.get("op")
        if op == "acquire":
            return self._lease.acquire(request.get("owner_label"), request.get("ttl_s"), request.get("request_id"))
        if op == "renew":
            return self._lease.renew(request.get("host_session"), request.get("generation"))
        if op == "release":
            return self._lease.release(request.get("host_session"), request.get("generation"))
        if op == "status":
            return {"ok": True, **self._lease.status()}
        return {"ok": False, "reason": "unknown_op", **self._lease.status()}


class TapPublisher:
    def __init__(self, socket, zmq_module):
        self._socket, self._zmq = socket, zmq_module
        self.sent_count = 0
        self.drop_count = 0  # sends that failed HERE. A subscriber's own losses never show up in this number.

    def publish(self, sample: dict) -> bool:
        try:
            self._socket.send(json.dumps(sample).encode("utf-8"), self._zmq.NOBLOCK)
        except Exception:  # zmq.Again, any other ZMQError, a value json cannot encode: the sample was not published
            self.drop_count += 1
            return False
        self.sent_count += 1
        return True


def _scalars(observation: dict) -> dict:
    return {key: value for key, value in observation.items()
            if isinstance(key, str) and not key.startswith(META_PREFIX) and (value is None or isinstance(value, (bool, int, float, str)))}


class HostBoundary:
    def __init__(self, zmq_module, control_socket, tap_socket, host_session=None, clock=time.monotonic, clock_ns=time.monotonic_ns, wall_clock=time.time,
                 log=None):
        self._zmq, self._clock, self._clock_ns, self._wall_clock = zmq_module, clock, clock_ns, wall_clock
        self._log = log or (lambda line: print(line, flush=True))
        self._control_socket, self._tap_socket = control_socket, tap_socket
        self.counters = Counters()
        self.timing = CommandTiming(clock_ns)
        self.lease = Lease(new_host_session() if host_session is None else host_session, clock, self._lease_event)
        self.control = ControlService(control_socket, self.lease, self.counters, zmq_module)
        self.tap = TapPublisher(tap_socket, zmq_module)
        self.gate = None
        self.seq = 0
        self._logged_rejections, self._next_rejection_log = dict(self.counters.rejections), 0.0

    def _lease_event(self, name: str, detail: str) -> None:
        if name == "acquired":
            self.timing.lease_started()
        self._log(f"HOST BOUNDARY: lease {name}: {detail}")

    # --- ingestion ---------------------------------------------------------------------------------------------------
    def take_over_command_socket(self, host) -> CommandGate:
        """Replace the conflating stock command socket of a constructed LeKiwiHost with a CommandGate on the SAME endpoint.
        Raises when the endpoint cannot be bound again: the host then fails to start, as it does when its port is busy."""
        zmq, stock = self._zmq, host.zmq_cmd_socket
        endpoint = stock.getsockopt_string(zmq.LAST_ENDPOINT)
        stock.unbind(endpoint)
        stock.close(0)
        socket = host.zmq_context.socket(zmq.PULL)  # no CONFLATE: every pending message is looked at
        socket.setsockopt(zmq.RCVHWM, COMMAND_RCVHWM)
        socket.setsockopt(zmq.LINGER, 0)
        deadline = self._clock() + REBIND_WAIT_S
        while True:
            try:
                socket.bind(endpoint)
                break
            except zmq.ZMQError:
                if self._clock() >= deadline:
                    socket.close(0)
                    self.close()
                    raise RuntimeError("host boundary: the command endpoint could not be bound again; the host does not start without it")
                time.sleep(0.05)
        self.gate = CommandGate(socket, self.lease, self.counters, self.timing, zmq, also_close=(self._control_socket, self._tap_socket))
        host.zmq_cmd_socket = self.gate
        self._log("HOST BOUNDARY: command ownership is enforced at ingestion; commands without a live lease are dropped and counted")
        return self.gate

    # --- observation -------------------------------------------------------------------------------------------------
    def observe(self, read):
        """read() is the stock get_observation. Its exceptions pass through untouched; a fault of THIS module is counted
        and the observation is returned as the stock method made it."""
        sampled_ns = self._clock_ns()
        observation = read()
        try:
            self._after_read(observation, sampled_ns)
        except Exception:
            self.counters.internal_errors += 1
        return observation

    def _after_read(self, observation, sampled_ns: int) -> None:
        if not isinstance(observation, dict):
            raise TypeError("observation")
        read_ns = self._clock_ns() - sampled_ns
        self.seq += 1
        observation[SESSION_KEY], observation[SEQ_KEY], observation[SAMPLE_KEY] = self.lease.session_text, self.seq, sampled_ns
        self.control.serve()  # after this iteration's commands were drained, before the lease status is published
        self.tap.publish({
            "kc.tap": TAP_VERSION, "session": self.lease.session_text, "seq": self.seq, "t_mono_ns": sampled_ns, "read_ns": read_ns,
            "wall_time_s": self._wall_clock(), "observation": _scalars(observation), "lease": self.lease.status(),
            "rejections": dict(self.counters.rejections), "commands": self.timing.report(), "ingestion": self.counters.report(),
            "publisher": {"sent_count": self.tap.sent_count, "drop_count": self.tap.drop_count}})
        self._log_rejections()

    def _log_rejections(self) -> None:
        now = self._clock()
        if now >= self._next_rejection_log and self.counters.rejections != self._logged_rejections:
            self._logged_rejections, self._next_rejection_log = dict(self.counters.rejections), now + REJECTION_LOG_INTERVAL_S
            self._log(f"HOST BOUNDARY: commands rejected so far, by reason: {self._logged_rejections}")

    def close(self) -> None:
        for socket in (self._control_socket, self._tap_socket):
            try:
                socket.close(0)
            except Exception:
                pass


def enabled(environ=None) -> bool:
    return (os.environ if environ is None else environ).get(ENV_FLAG) == "1"


def _port(environ, name: str, default: int) -> int:
    text = environ.get(name)
    if text is None or text == "":
        return default
    if not (text.isascii() and text.isdecimal()) or not 1024 <= int(text) <= 65535:  # isdigit() would let int() raise here
        raise ValueError(f"host boundary: {name} must be a port number between 1024 and 65535")
    return int(text)


def open_boundary(zmq_module, context, control_endpoint: str, tap_endpoint: str, **options) -> HostBoundary:
    control, tap = context.socket(zmq_module.REP), context.socket(zmq_module.PUB)
    try:
        for socket in (control, tap):
            socket.setsockopt(zmq_module.LINGER, 0)
        tap.setsockopt(zmq_module.SNDHWM, TAP_SNDHWM)
        control.bind(control_endpoint)
        tap.bind(tap_endpoint)
    except Exception:
        control.close(0)
        tap.close(0)
        raise
    return HostBoundary(zmq_module, control, tap, **options)


def install(robot_cls, host_cls, environ=None, *, control_endpoint=None, tap_endpoint=None, context=None, zmq_module=None, log=None):
    """With KC_HOST_BOUNDARY=1: bind the control and tap sockets NOW (a busy port ends the process before the robot is
    touched), then wrap host_cls.__init__ and robot_cls.get_observation. Otherwise: return None and change nothing."""
    environ = os.environ if environ is None else environ
    if not enabled(environ):
        return None
    if zmq_module is None:
        import zmq as zmq_module
    control_port, tap_port = _port(environ, CONTROL_PORT_ENV, DEFAULT_CONTROL_PORT), _port(environ, TAP_PORT_ENV, DEFAULT_TAP_PORT)
    if control_endpoint is None and tap_endpoint is None and control_port == tap_port:
        raise ValueError("host boundary: the control port and the tap port must differ")
    boundary = open_boundary(zmq_module, context or zmq_module.Context.instance(), control_endpoint or f"tcp://{LOOPBACK}:{control_port}",
                             tap_endpoint or f"tcp://{LOOPBACK}:{tap_port}", log=log)
    stock_init, stock_get_observation = host_cls.__init__, robot_cls.get_observation

    @functools.wraps(stock_init)
    def init_with_gate(self, *args, **kwargs):
        stock_init(self, *args, **kwargs)
        boundary.take_over_command_socket(self)

    @functools.wraps(stock_get_observation)
    def get_observation_stamped(self, *args, **kwargs):
        return boundary.observe(lambda: stock_get_observation(self, *args, **kwargs))

    host_cls.__init__ = init_with_gate
    robot_cls.get_observation = get_observation_stamped
    boundary._log(f"HOST BOUNDARY ON ({ENV_FLAG}=1): lease control and tap are bound to the loopback address only, ports {control_port} and {tap_port}")
    return boundary


# --- the assumptions about the stock host, checkable against the INSTALLED source before deployment -------------------
def _attribute_chain(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join(reversed(parts + [node.id])) if isinstance(node, ast.Name) else ""


def verify_stock_host_source(text: str) -> list:
    """Problems (an empty list means none) found when the text of lerobot's lekiwi_host.py is held against what this
    module relies on. It reads text; it imports nothing and runs nothing."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ["the source does not parse"]
    problems = []
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    classes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    main, host_cls = functions.get("main"), classes.get("LeKiwiHost")
    if main is None or host_cls is None:
        return ["main() or class LeKiwiHost is missing"]
    assigned = {_attribute_chain(t) for n in ast.walk(host_cls) if isinstance(n, ast.Assign) for t in n.targets}
    for name in ("self.zmq_context", "self.zmq_cmd_socket"):
        if name not in assigned:
            problems.append(f"LeKiwiHost.__init__ no longer assigns {name}")
    uses = sorted({_attribute_chain(n) for n in ast.walk(tree) if isinstance(n, ast.Attribute) and "zmq_cmd_socket." in _attribute_chain(n)})
    allowed = {"self.zmq_cmd_socket.setsockopt", "self.zmq_cmd_socket.bind", "self.zmq_cmd_socket.close", "host.zmq_cmd_socket.recv_string"}
    if set(uses) - allowed:
        problems.append(f"the command socket is used in a way the gate does not provide: {sorted(set(uses) - allowed)}")
    calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)]
    reads = [c for c in calls if _attribute_chain(c.func) == "host.zmq_cmd_socket.recv_string"]
    if len(reads) != 1:
        return problems + [f"main() reads the command socket {len(reads)} times, expected once"]
    guarded = [n for n in ast.walk(main) if isinstance(n, ast.Try) and any(reads[0] in list(ast.walk(s)) for s in n.body)]
    if not guarded:
        return problems + ["the command read is not inside a try block"]
    block = max(guarded, key=lambda node: node.lineno)  # the innermost one: main() wraps its whole loop in another
    handled = [_attribute_chain(h.type) if isinstance(h.type, ast.Attribute) else getattr(h.type, "id", None) for h in block.handlers]
    if handled[:1] != ["zmq.Again"] or "Exception" not in handled:
        problems.append(f"the command read is not guarded by 'except zmq.Again' first and 'except Exception' after it: {handled}")
    order = {}
    for index, statement in enumerate(block.body):
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and _attribute_chain(node.func) in ("host.zmq_cmd_socket.recv_string", "robot.send_action"):
                order.setdefault(_attribute_chain(node.func), index)
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "last_cmd_time" for t in node.targets):
                order.setdefault("last_cmd_time", index)
    if not order.get("host.zmq_cmd_socket.recv_string", 99) < order.get("robot.send_action", -1) < order.get("last_cmd_time", -1):
        problems.append("inside the try block the order is no longer: read the command, robot.send_action, refresh last_cmd_time")
    loops = [n for n in ast.walk(main) if isinstance(n, ast.While)]
    refreshes = [n for loop in loops for n in ast.walk(loop) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "last_cmd_time" for t in n.targets)]
    if len(refreshes) != 1 or refreshes[0] not in [n for statement in block.body for n in ast.walk(statement)]:
        problems.append("last_cmd_time is refreshed somewhere else than after a dispatched command: a rejected command could feed the watchdog")
    if len([c for c in calls if _attribute_chain(c.func) == "robot.get_observation"]) != 1:
        problems.append("main() does not call robot.get_observation() exactly once per iteration")
    if not [c for c in calls if _attribute_chain(c.func) == "host.zmq_observation_socket.send_string"]:
        problems.append("main() no longer sends the observation with host.zmq_observation_socket.send_string")
    return problems


# --- the assumptions about the DEPLOYED robot source, checkable the same way ------------------------------------------
# The three arm servo gains, as (the configuration field, the register it is written to, the value the deployed
# configuration resolves it to). Those three numbers are the ones the stock LeKiwi.configure() held as literals at
# b4e2d0b, so a configuration that resolves to them configures the arm exactly as an unpatched lerobot does: they are
# recorded here as the deployed reference, not as a tuning anyone chose. bin/pi/apply-lerobot-patches.sh writes the
# fields with these defaults and reads this same table, so the applier and this check cannot drift apart.
ARM_GAIN_FIELDS = (("arm_p_coefficient", "P_Coefficient", 16), ("arm_i_coefficient", "I_Coefficient", 0), ("arm_d_coefficient", "D_Coefficient", 32))
CONFIG_FILENAME = "config_lekiwi.py"  # it sits beside lekiwi.py in the installed package, and holds LeKiwiConfig
ABSENT = "absent"  # what a gain report says where the field is not declared, which is never a value a field could hold


def _bus_writes(node) -> list:
    """Every self.bus.sync_write(...) call under node, earliest line first."""
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and _attribute_chain(n.func) == "self.bus.sync_write"]
    return sorted(calls, key=lambda call: (call.lineno, call.col_offset))


def _register(call):
    """The register a bus call names, when it names one as a plain string."""
    first = call.args[0] if call.args else None
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _statement_bodies(node) -> list:
    """Every list of statements under node, so that "at the same level" is a question this can answer."""
    bodies = []
    for inner in ast.walk(node):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(inner, field, None)
            if isinstance(block, list) and block and all(isinstance(item, ast.stmt) for item in block):
                bodies.append(block)
    return bodies


def _guarded_position_write(send_action):
    """(the body it sits in, its index in that body) of the "if arm_goal_pos_raw:" that holds the Goal_Position write,
    or None when no such guard holds it."""
    for body in _statement_bodies(send_action):
        for index, statement in enumerate(body):
            if (isinstance(statement, ast.If) and isinstance(statement.test, ast.Name) and statement.test.id == "arm_goal_pos_raw"
                    and any(_register(call) == "Goal_Position" for call in _bus_writes(statement))):
                return body, index
    return None


def _guarded_position_writes(send_action) -> set:
    """The ids of every Goal_Position write that sits inside the BODY of an "if arm_goal_pos_raw:". Anything not in
    this set is a write the empty arm dictionary still reaches. A write in the ELSE branch of such a statement is not
    guarded by it either, because that branch is precisely the one an empty dictionary takes."""
    guarded = set()
    for node in ast.walk(send_action):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "arm_goal_pos_raw":
            for statement in node.body:
                guarded.update(id(call) for call in _bus_writes(statement) if _register(call) == "Goal_Position")
    return guarded


def _present_position_lookups(send_action) -> tuple:
    """(the present_pos[...] lookups that strip a ".pos" suffix off the key, the ones that do not). The clamp branch
    makes one of these, and which of the two it makes is the whole of lerobot commit f66e512."""
    stripped, plain = [], []
    for node in ast.walk(send_action):
        if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "present_pos"):
            continue
        index = node.slice
        strips = (isinstance(index, ast.Call) and isinstance(index.func, ast.Attribute) and index.func.attr == "removesuffix"
                  and len(index.args) == 1 and isinstance(index.args[0], ast.Constant) and index.args[0].value == ".pos")
        (stripped if strips else plain).append(node)
    return stripped, plain


def _zeroed_base_motors(node) -> bool:
    """True for dict.fromkeys(self.base_motors, 0), the whole base written to a standstill in one message."""
    return (isinstance(node, ast.Call) and _attribute_chain(node.func) == "dict.fromkeys" and len(node.args) == 2
            and _attribute_chain(node.args[0]) == "self.base_motors"
            and isinstance(node.args[1], ast.Constant) and type(node.args[1].value) is int and node.args[1].value == 0)


def _plain_default(node):
    """The default of an annotated field as an int when it is a plain whole number, else the source text of whatever
    stands there. A caller comparing the result against an expected int therefore reports anything else as a
    difference, and can name what it found, instead of treating an unreadable default as an absent one."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    return "no default" if node is None else ast.unparse(node)


def arm_gain_configuration(config_text: str):
    """({field name: the defaults it is declared with, in source order}, None), or (None, why it could not be read).

    The LAST entry of each list is the EFFECTIVE default, because a dataclass body that binds a name twice keeps the
    second binding, exactly as any other class body does. A list longer than one is therefore not a different
    configuration; it is the same configuration written twice. The deployed install carries exactly that: two adjacent
    comment-and-field blocks, the residue of two overlapping hand edits, whose annotations differ and whose values do
    not. This reads text; it imports nothing and opens nothing."""
    try:
        tree = ast.parse(config_text)
    except SyntaxError:
        return None, "the source does not parse"
    config = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}.get("LeKiwiConfig")
    if config is None:
        return None, "class LeKiwiConfig is missing"
    declared = {name: [] for name, _, _ in ARM_GAIN_FIELDS}
    for statement in config.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.target.id in declared:
            declared[statement.target.id].append(_plain_default(statement.value))
        elif isinstance(statement, ast.Assign):
            # A BINDING WITHOUT AN ANNOTATION COUNTS, and it has to. "arm_p_coefficient = 20" written after the
            # annotated field is not a field declaration, so dataclasses does not read it as one; but the decorator
            # takes each default from the class attribute, and that plain assignment has already replaced it. The
            # default really is 20 while the annotated line above it still reads 16. Collecting only the annotated
            # lines would report 16 and pass, which is the one thing this check exists to refuse: a robot running
            # gains that nothing recorded.
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in declared:
                    declared[target.id].append(_plain_default(statement.value))
                elif isinstance(target, ast.Tuple):  # arm_p_coefficient, arm_i_coefficient = 20, 5
                    for element, value in zip(target.elts, getattr(statement.value, "elts", [])):
                        if isinstance(element, ast.Name) and element.id in declared:
                            declared[element.id].append(_plain_default(value))
    return declared, None


def resolved_arm_gains(config_text: str) -> list:
    """[(field name, register, what the field resolves to)] for the report, with "absent" where the field is not
    declared at all. It states what was READ; whether that is what the robot computer carries is what the problems and
    the notes of verify_deployed_configuration say."""
    declared, problem = arm_gain_configuration(config_text)
    if problem is not None:
        return []
    return [(name, register, declared[name][-1] if declared[name] else ABSENT) for name, register, _ in ARM_GAIN_FIELDS]


def verify_deployed_configuration(config_text: str):
    """(problems, notes) found when the text of the INSTALLED lerobot config_lekiwi.py is held against the arm gain
    configuration the robot computer carries. A problem refuses the start; a note does not.

    WHY THIS EXISTS. The third local change of the deployed checkout added these three fields and made
    LeKiwi.configure write them instead of the literals 16, 0 and 32 that stood in its place. It changed no gain: the
    defaults ARE those literals. What it did change is that bin/pi/lekiwi_host_safe_attach.py can read
    self.config.arm_p_coefficient and its two neighbours to check the live registers of every arm motor before it
    attaches to a holding arm. On a stock LeKiwiConfig there is no such field and that read raises AttributeError,
    after the arm has been read and before any register is written.

    A field declared twice whose effective value is still the recorded one is a NOTE and not a refusal, because the
    robot computer is in exactly that state and the operator has ruled that install stays as it is until a planned
    deployment session. A field whose effective value is NOT the recorded one is a refusal, because a host started
    against it would be running a configuration nobody wrote down, and the attach would compare the live registers
    against numbers this repository never recorded."""
    declared, problem = arm_gain_configuration(config_text)
    if problem is not None:
        return [f"{CONFIG_FILENAME}: {problem}"], []
    problems, notes = [], []
    for name, register, expected in ARM_GAIN_FIELDS:
        defaults = declared[name]
        if not defaults:
            problems.append(f"LeKiwiConfig declares no {name}: bin/pi/lekiwi_host_safe_attach.py reads self.config.{name} to check the live {register} "
                            f"of every arm motor before it attaches, so on this install it raises \"AttributeError: 'LeKiwiConfig' object has no attribute "
                            f"'{name}'\", at a point where the arm has been read and no register has been written. Run bin/pi/apply-lerobot-patches.sh "
                            "--apply, which writes the field with the recorded default and changes no gain")
            continue
        effective = defaults[-1]
        if effective != expected or isinstance(effective, bool):
            problems.append(f"LeKiwiConfig resolves {name} to {effective!r}, and the configuration recorded in LIMITATIONS.md resolves it to {expected}: "
                            f"this install is configured differently from the one the demo was run on, the attach would check the live {register} of "
                            f"every arm motor against {effective!r}, and nothing here knows who chose that number or why")
        elif len(defaults) > 1:
            notes.append(f"LeKiwiConfig declares {name} {len(defaults)} times, with {defaults}; Python keeps the last, so it resolves to {effective}, "
                         "which is the recorded value and the stock one. The robot computer carries exactly this duplication, the residue of two "
                         "overlapping hand edits (see LIMITATIONS.md); bin/pi/apply-lerobot-patches.sh writes the field once, and a clean install "
                         "differing from the robot in that way is not a configuration difference")
    return problems, notes


def _literal_gain_writes(configure) -> list:
    """The lines of configure that write one of the three gain registers with a plain whole number: the stock shape,
    where the value stands in lekiwi.py and the configuration field is not consulted at all."""
    registers = {register for _, register, _ in ARM_GAIN_FIELDS}
    lines = []
    for call in ast.walk(configure):
        if not (isinstance(call, ast.Call) and _attribute_chain(call.func) == "self.bus.write" and len(call.args) >= 3):
            continue
        if _register(call) in registers and isinstance(call.args[2], ast.Constant) and type(call.args[2].value) is int:
            lines.append(call.lineno)
    return sorted(lines)


def verify_deployed_robot_source(text: str) -> list:
    """Problems (an empty list means none) found when the text of the INSTALLED lerobot lekiwi.py is held against what
    the robot computer runs today. It reads text; it imports nothing, opens no bus and moves nothing.

    WHY THIS EXISTS. LeKiwi.send_action on the robot computer carries two changes that were written by hand into the
    installed checkout, and a third one reaches LeKiwi.configure; the configuration side of that third one is read by
    verify_deployed_configuration above, from the file beside this one. The two in send_action are not the same kind
    of change as each other. The clamp fix IS lerobot's own later commit f66e512
    (issue 4309), applied to a checkout at b4e2d0b that predates it; the empty-arm guard is in no lerobot commit at all,
    and this repository is the only place it is written down. Both live in that checkout and nowhere else, so a git
    checkout, a pull or a reinstall takes them away again in silence. bin/pi/apply-lerobot-patches.sh writes both of
    them into such a checkout, but having written one is not the same fact as the file the robot will import still
    carrying it, and that second fact is what this reads, for each of them.

    WITHOUT THE CLAMP FIX, on a checkout that predates f66e512, the clamp branch indexes the present positions with the
    goal's own keys. The bus keys its reading by bare motor name and the goal keys end in ".pos", so the lookup raises
    KeyError before any register is written. The host is started with --robot.max_relative_target=15.0 (see
    bin/pi/start-host.sh), so that branch is taken on EVERY command and nothing the laptop sends ever reaches a motor.

    WITHOUT THE GUARD a velocity-only action (no ".pos" key at all, which is exactly what
    autonomy/motion/lerobot_transport.py sends for every base move) reaches self.bus.sync_write("Goal_Position", {}) and
    the motors bus raises StopIteration at "model = next(iter(models))", BEFORE Goal_Velocity is written, because it
    takes the model of the first id it was handed. The stock host loop catches that with its bare "except Exception",
    never refreshes last_cmd_time, and 500 ms later the watchdog stops the base. Nothing is raised on the laptop: it
    presents as a base that does not move, with no error anywhere. So the deployed file is read again before it runs, and
    autonomy/tests/test_host_boundary.py proves each of those consequences against a fake bus.

    The three base velocity keys are the opposite case: send_action indexing base_goal_vel["x.vel"] and its two
    neighbours unconditionally is a CONTRACT the laptop relies on (autonomy/manipulation/action_adapter.py merges the
    three zeros into every arm command because of it), so their ABSENCE is what is reported here, not their presence.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ["the source does not parse"]
    # The LAST definition of the name, the way verify_stock_host_source above resolves its classes and the way Python
    # itself binds a name: a module that defines LeKiwi twice runs the second one, so reading the first would be
    # reading dead code and calling a robot sound on the strength of a class nothing imports.
    lekiwi = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}.get("LeKiwi")
    if lekiwi is None:
        return ["class LeKiwi is missing"]
    methods = {node.name: node for node in lekiwi.body if isinstance(node, ast.FunctionDef)}
    send_action, stop_base = methods.get("send_action"), methods.get("stop_base")
    if send_action is None or stop_base is None:
        return ["LeKiwi.send_action or LeKiwi.stop_base is missing"]
    problems = []
    writes = _bus_writes(send_action)
    if not any(_register(call) == "Goal_Position" for call in writes):
        problems.append("LeKiwi.send_action no longer writes Goal_Position at all")
    if not any(_register(call) == "Goal_Velocity" for call in writes):
        problems.append("LeKiwi.send_action no longer writes Goal_Velocity at all")
    guard = _guarded_position_write(send_action)
    if guard is None:
        problems.append("the Goal_Position write of LeKiwi.send_action does not sit inside 'if arm_goal_pos_raw:': a command without a .pos key makes "
                        "the bus raise StopIteration before Goal_Velocity is written, the host swallows it, and the watchdog stops the base")
    else:
        body, index = guard
        after = [statement for statement in body[index + 1:]
                 if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                 and _attribute_chain(statement.value.func) == "self.bus.sync_write" and _register(statement.value) == "Goal_Velocity"]
        if not after:
            problems.append("the Goal_Velocity write of LeKiwi.send_action does not follow the guarded Goal_Position write, unguarded, at the same level: "
                            "a velocity-only command would reach no register at all")
        # A guarded write EXISTING is not the same fact as every write being guarded. A botched merge that keeps the
        # stock unguarded line next to the patched one satisfies the patch script, which greps for the guard's text,
        # and satisfies the check above, which looks for a guard that holds a write; the stray line still raises.
        stray = [call.lineno for call in writes if _register(call) == "Goal_Position" and id(call) not in _guarded_position_writes(send_action)]
        if stray:
            problems.append(f"LeKiwi.send_action writes Goal_Position outside 'if arm_goal_pos_raw:' as well as inside it, at line(s) {stray}: a command "
                            "without a .pos key reaches that write with an empty dictionary and the bus raises StopIteration there, exactly as it does "
                            "with no guard at all")
    stripped, plain = _present_position_lookups(send_action)
    if not stripped:
        problems.append("the clamp branch of LeKiwi.send_action does not index the present positions with present_pos[key.removesuffix('.pos')]: the host "
                        "is started with --robot.max_relative_target=15.0, so that branch runs on EVERY command, and the bus keys its reading by bare "
                        "motor name while the goal keys end in '.pos', so the lookup raises KeyError before any register is written and nothing moves at "
                        "all. That one line is lerobot's own commit f66e512 (issue 4309), written by hand into a checkout that predates it")
    if plain:
        problems.append(f"LeKiwi.send_action indexes the present positions at line(s) {[node.lineno for node in plain]} with a key it does not strip "
                        "'.pos' off: the bus keys its reading by bare motor name, so that lookup raises KeyError as soon as the clamp branch reaches it, "
                        "whatever else the method also does correctly")
    first_write = min([call.lineno for call in writes], default=None)
    read_before_writing = {node.slice.value for node in ast.walk(send_action)
                           if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "base_goal_vel"
                           and isinstance(node.slice, ast.Constant) and (first_write is None or node.lineno < first_write)}
    missing = [key for key in ("x.vel", "y.vel", "theta.vel") if key not in read_before_writing]
    if missing:
        problems.append(f"LeKiwi.send_action no longer reads base_goal_vel{missing} before it writes: the laptop sends those three keys on EVERY "
                        "command because this method has always required them, and that contract would no longer be the deployed one")
    stops = [call for call in _bus_writes(stop_base) if _register(call) == "Goal_Velocity"]
    zeroed = [call for call in stops if len(call.args) >= 2 and _zeroed_base_motors(call.args[1])]
    if not stops:
        problems.append("LeKiwi.stop_base no longer writes Goal_Velocity: that write is the whole of the watchdog's base stop")
    elif not zeroed:
        problems.append("LeKiwi.stop_base no longer writes dict.fromkeys(self.base_motors, 0): the base stop must reach every wheel, in one message")
    elif not any(keyword.arg == "num_retry" for call in zeroed for keyword in call.keywords):
        problems.append("LeKiwi.stop_base no longer passes num_retry to the base stop: it would be sent once, and a lost packet would leave the wheels turning")
    # The writing half of the third local change: configure() takes the three arm gains from the configuration fields
    # instead of holding them as literals. This is a check that ONE source of those three numbers is left, not a check
    # of what the numbers are: their values are read from config_lekiwi.py by verify_deployed_configuration. Two
    # sources part company the moment either one is set, and the attach of bin/pi/lekiwi_host_safe_attach.py compares
    # the live registers against the FIELD, so the field is the one that has to be written.
    configure = methods.get("configure")
    if configure is None:
        problems.append("LeKiwi.configure is missing: it is the method that writes the three arm gain registers on connect, and the deployed one writes "
                        "them from the configuration fields bin/pi/lekiwi_host_safe_attach.py checks the live registers against")
    else:
        used = {_attribute_chain(node) for node in ast.walk(configure) if isinstance(node, ast.Attribute)}
        for name, register, _ in ARM_GAIN_FIELDS:
            if f"self.config.{name}" not in used:
                problems.append(f"LeKiwi.configure does not take {register} from self.config.{name}: the deployed configure() writes the three arm gains "
                                "from the configuration, which is also where bin/pi/lekiwi_host_safe_attach.py reads them to check the live registers "
                                "before it attaches, so a configure() holding its own copy of those numbers would disagree with the attach as soon as "
                                "either copy was ever set")
        literals = _literal_gain_writes(configure)
        if literals:
            problems.append(f"LeKiwi.configure writes an arm gain register with a plain number at line(s) {literals}: that is the stock shape, where the "
                            "value lives in lekiwi.py and the configuration field is not consulted, so the number the host writes and the number the "
                            "attach checks against are two different facts")
    return problems


# --- reading those files, and saying which files were read ------------------------------------------------------------
def _file_identity(path: str):
    """(the sha256 of the bytes on disk, the text of the file). The digest is taken over the bytes, so it is the one
    sha256sum prints for the same file, and it is what makes a qualification bundle say WHICH source was checked
    rather than only that some source passed."""
    with open(path, "rb") as handle:
        data = handle.read()
    return hashlib.sha256(data).hexdigest(), data.decode("utf-8")


def _read_stock_host(path: str):
    """(problems, notes, [(file, sha256)], [(field, register, value)]) for the stock host loop, which has no
    configuration side: the last two are what the report prints, and only the first refuses."""
    digest, text = _file_identity(path)
    return verify_stock_host_source(text), [], [(os.path.basename(path), digest)], []


def _read_deployed_robot(path: str):
    """The same four, for the installed lekiwi.py AND the config_lekiwi.py beside it. Only the path of lekiwi.py is
    given, as it always was: the configuration is found next to it, because that is where the package puts it and
    because the two have to be the same install for either answer to mean anything. Nothing but the two basenames is
    ever printed: the directory is the operator's and belongs in no report."""
    digest, text = _file_identity(path)
    identities, problems = [(os.path.basename(path), digest)], verify_deployed_robot_source(text)
    try:
        config_digest, config_text = _file_identity(os.path.join(os.path.dirname(path), CONFIG_FILENAME))
    except OSError:
        return problems + [f"{CONFIG_FILENAME} could not be read beside {os.path.basename(path)}, so the three arm gain fields LeKiwiConfig has to declare "
                           "could not be checked at all; this install is not the one the demo was run on until they are"], [], identities, []
    identities.append((CONFIG_FILENAME, config_digest))
    config_problems, notes = verify_deployed_configuration(config_text)
    return problems + config_problems, notes, identities, resolved_arm_gains(config_text)


VERIFIERS = {  # mode: (what reads the file, what the file is called in the report, what a mismatch means)
    "--verify-stock-host": (_read_stock_host, "stock host loop", f"do not enable {ENV_FLAG}"),
    "--verify-robot": (_read_deployed_robot, "deployed robot source", "do not drive the robot until the local changes are back in place"),
}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in VERIFIERS:
        sys.exit("usage: host_boundary.py --verify-stock-host <path to the installed lerobot/robots/lekiwi/lekiwi_host.py>\n"
                 "       host_boundary.py --verify-robot <path to the installed lerobot/robots/lekiwi/lekiwi.py>\n"
                 f"   (each one reads the file; --verify-robot reads the {CONFIG_FILENAME} beside it as well, and nothing else)")
    read, subject, consequence = VERIFIERS[sys.argv[1]]
    found, remarks, identities, gains = read(sys.argv[2])
    for line in found:
        print(f"MISMATCH: {line}")
    # A NOTE is a difference this check looked at and decided not to refuse over. It is printed whether or not anything
    # else failed, because the point of it is that the reader sees the difference rather than discovering it later.
    for line in remarks:
        print(f"NOTE: {line}")
    # The identities and the resolved values are RECORDED EVIDENCE, not a verdict: whatever reads this output (the step
    # 0 report of bin/laptop/qualify-robot.sh, the log of a host start) then says which bytes were checked and which
    # numbers this install configures, instead of assuming both.
    for name, digest in identities:
        print(f"READ: {name} sha256 {digest}")
    for name, register, value in gains:
        print(f"RESOLVED: {name} is not declared at all, so nothing here configures {register}" if value == ABSENT
              else f"RESOLVED: {name} = {value}, written to {register} of every arm motor")
    print(f"{subject}: the assumptions of host_boundary.py hold" if not found else f"{subject}: {len(found)} assumption(s) do NOT hold; {consequence}")
    sys.exit(1 if found else 0)
