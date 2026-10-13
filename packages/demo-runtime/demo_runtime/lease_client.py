# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.lease_client: the laptop side of the host command lease (bin/pi/host_boundary.py on the robot computer).

The host, when its boundary is on, hands its loop only commands that carry the live lease as two NUMERIC keys:
kc.lease (the generation) and kc.session (the host session). This module asks for that lease, keeps it, gives it back,
and builds the two keys a controller has to attach. See docs/autonomous-demo/host-boundary.md.

LeaseClient talks to the host's REP control socket. That socket is bound to the loopback address of the robot computer;
the runtime reaches it through an ssh forward that the RUNTIME owns. This module opens no ssh and knows no address: it
is given an endpoint, and it accepts only a loopback or in-process one (check_endpoint), because an acquire sent to a
machine that was not meant would take the lease of another robot host. The refused endpoint is not repeated back.

NEVER RAISES INTO A SUPERVISOR. acquire / renew / release / status always return a dict with a boolean "ok". A refusal
carries the host's "reason" (lease_held, wrong_generation, wrong_session, lease_expired, no_lease, malformed_request,
unknown_op); a failure on this side is "timeout", "transport_error" or "bad_reply". Each call blocks for at most about
timeout_ms twice (send, then wait). After a failure the REQ socket is thrown away and the next call opens a new one: a
REQ socket that missed its reply cannot be used again.

A TIMED OUT REQUEST MAY STILL BE SERVED. Closing the socket does not take back what already left it (a host loop that
stalls for longer than the timeout answers late, and an inproc pipe delivers even after its sender closed: a test shows
it). A late renew, release or status does what was meant or nothing. A late ACQUIRE would lock the robot, held by a
client that believes it holds nothing, until the lease expires. So every acquire carries a random request_id, which the
host keeps with the lease and repeats in every reply, and this client remembers the ids of its unanswered acquires:
when any later reply shows a live lease under one of them, the client ADOPTS it (an acquire() that adopts returns
ok true with "adopted": true). A lease under somebody else's id is never adopted.

WHO SENDS COMMANDS. The process that holds the lease and the process that commands the robot may differ: the runtime
can acquire and hand (generation, host_session) to a controller, which attaches them with attach_lease_metadata(). The
host extends the lease on every valid command, so a live controller keeps it even when the runtime is gone.

The lease is cooperative ownership, not authentication: the two numbers are not secrets (the session travels in every
observation). zmq is imported on first use, so importing this module needs nothing but the standard library.
One instance, one thread.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Mapping, Optional, Union

# Mirrors of bin/pi/host_boundary.py, pinned against that file by tests/test_lease_client.py.
LEASE_KEY, SESSION_KEY = "kc.lease", "kc.session"
DEFAULT_TTL_S = 180.0  # above the longest quiet perception phase of a cycle (about 90 s), during which no command extends the lease
DEFAULT_TIMEOUT_MS = 1000
LEASE_LOST_REASONS = ("wrong_generation", "wrong_session", "lease_expired", "no_lease")
MAX_PENDING_ACQUIRES = 16
_EXACT_FLOAT = 2 ** 53
# The host binds its control socket to its own loopback address, so only a local endpoint or this process's own can name
# it. An acquire sent to a machine that was not meant would take the lease of ANOTHER robot host, so the rule is checked
# here too, not only in tap_client and preview_client. Each of the three states it itself: each is imported on its own.
_LOOPBACK_ENDPOINT = re.compile(r"^tcp://(?:127\.0\.0\.1|localhost):([0-9]{1,5})$")
_INPROC_ENDPOINT = re.compile(r"^inproc://[A-Za-z0-9_.:-]{1,128}$")


def check_endpoint(endpoint: str) -> str:
    """The endpoint unchanged, or ValueError. The refused endpoint is not repeated in the message."""
    if isinstance(endpoint, str):
        loopback = _LOOPBACK_ENDPOINT.fullmatch(endpoint)
        if _INPROC_ENDPOINT.fullmatch(endpoint) or (loopback and 1 <= int(loopback.group(1)) <= 65535):
            return endpoint
    raise ValueError("the lease endpoint must be tcp://127.0.0.1:<port> or inproc://<name>: the host control socket is "
                     "reached through an ssh forward the runtime owns, never by addressing another machine")


def command_metadata(generation, host_session) -> dict:
    """{kc.lease, kc.session} as floats, which is what the stock client would turn them into anyway. host_session is
    the decimal text the host reports. Raises ValueError for values the host could never have issued."""
    try:
        generation_number, session_number = int(generation), int(host_session)
    except (TypeError, ValueError):
        raise ValueError("a lease generation and a host session are whole numbers") from None
    if isinstance(generation, bool) or generation_number < 1 or not 0 < session_number < _EXACT_FLOAT or generation_number >= _EXACT_FLOAT:
        raise ValueError("a lease generation starts at 1 and a host session is a positive number a float holds exactly")
    return {LEASE_KEY: float(generation_number), SESSION_KEY: float(session_number)}


def attach_lease_metadata(robot_client, metadata: Union[Mapping, Callable[[], Optional[Mapping]]]) -> Callable[[], None]:
    """Wrap robot_client.send_action so that every action carries the ownership keys. metadata is a mapping, or a
    callable returning a mapping or None (LeaseClient.command_metadata is one). With None the action goes out as it
    is, and a host whose boundary is on will drop it. The caller's dict is never modified. Returns detach()."""
    stock = robot_client.send_action

    def send_action(action, *args, **kwargs):
        extra = metadata() if callable(metadata) else metadata
        return stock({**action, **extra} if extra else action, *args, **kwargs)

    robot_client.send_action = send_action

    def detach() -> None:
        robot_client.send_action = stock

    return detach


class LeaseClient:
    def __init__(self, endpoint: str, owner_label: str, *, ttl_s: float = DEFAULT_TTL_S, timeout_ms: int = DEFAULT_TIMEOUT_MS, context=None,
                 socket_factory: Optional[Callable[[], object]] = None, clock_ns: Callable[[], int] = time.monotonic_ns):
        self._endpoint, self.owner_label, self.ttl_s, self.timeout_ms = check_endpoint(endpoint), owner_label, ttl_s, int(timeout_ms)
        self._context, self._socket_factory, self._clock_ns = context, socket_factory, clock_ns
        self._socket = None
        self._pending_request_ids: list = []  # acquires of this client that the host may or may not have served
        self.generation: Optional[int] = None
        self.host_session: Optional[str] = None
        self.lost_reason: Optional[str] = None  # why a lease this client held is no longer its own; None while held or never held
        self.last_reply: Optional[dict] = None  # the last dict the HOST sent, whatever it said
        self.last_reply_mono_ns: Optional[int] = None  # laptop clock

    # --- what a controller needs -------------------------------------------------------------------------------------
    @property
    def held(self) -> bool:
        """True while this client has no evidence that it lost the lease. The host is the authority: a lease can expire
        there without this side hearing of it until the next renew() or status()."""
        return self.generation is not None

    def command_metadata(self) -> Optional[dict]:
        return command_metadata(self.generation, self.host_session) if self.held else None

    def snapshot_lease(self) -> Optional[dict]:
        """The console contract's SnapshotLease ({host_session, generation, expires_in_ms}) from the last reply in which
        the host reported a live lease, else None. expires_in_ms is what the host said THEN; see last_reply_age_ms()."""
        reply = self.last_reply
        if not reply or reply.get("state") != "held":
            return None
        return {"host_session": reply.get("host_session"), "generation": reply.get("generation"), "expires_in_ms": reply.get("expires_in_ms")}

    def last_reply_age_ms(self) -> Optional[float]:
        return None if self.last_reply_mono_ns is None else max(0, self._clock_ns() - self.last_reply_mono_ns) / 1e6

    # --- the four operations -----------------------------------------------------------------------------------------
    def acquire(self, ttl_s: Optional[float] = None) -> dict:
        if self.held:
            return {"ok": False, "reason": "already_held_by_this_client"}
        request_id = os.urandom(8).hex()
        self._pending_request_ids = (self._pending_request_ids + [request_id])[-MAX_PENDING_ACQUIRES:]
        return self._request({"op": "acquire", "owner_label": self.owner_label, "ttl_s": self.ttl_s if ttl_s is None else ttl_s, "request_id": request_id})

    def renew(self) -> dict:
        if not self.held:
            return {"ok": False, "reason": "not_held_by_this_client"}
        return self._request({"op": "renew", "host_session": self.host_session, "generation": self.generation})

    def release(self) -> dict:
        if not self.held:
            return {"ok": False, "reason": "not_held_by_this_client"}
        return self._request({"op": "release", "host_session": self.host_session, "generation": self.generation})

    def status(self) -> dict:
        return self._request({"op": "status"})

    def close(self) -> None:
        """Closes the socket. It does NOT release the lease: a controller may still be commanding under it."""
        self._drop_socket()

    # --- transport ---------------------------------------------------------------------------------------------------
    def _open(self):
        if self._socket is None:
            if self._socket_factory is not None:
                self._socket = self._socket_factory()
            else:
                import zmq
                socket = (self._context or zmq.Context.instance()).socket(zmq.REQ)
                socket.setsockopt(zmq.LINGER, 0)  # a request that was never sent dies with the socket
                socket.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
                socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
                socket.connect(self._endpoint)
                self._socket = socket
        return self._socket

    def _drop_socket(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                socket.close(0)
            except Exception:
                pass

    def _request(self, payload: dict) -> dict:
        try:
            socket = self._open()
            socket.send(json.dumps(payload).encode("utf-8"))
            if not socket.poll(self.timeout_ms):
                self._drop_socket()
                return {"ok": False, "reason": "timeout"}
            reply = json.loads(bytes(socket.recv()).decode("utf-8"))
        except Exception as error:  # nothing may escape into the loop that supervises a robot
            self._drop_socket()
            return {"ok": False, "reason": "timeout" if type(error).__name__ == "Again" else "transport_error", "error": type(error).__name__}
        if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
            self._drop_socket()
            return {"ok": False, "reason": "bad_reply"}
        self.last_reply, self.last_reply_mono_ns = reply, self._clock_ns()
        self._absorb(payload["op"], reply)
        if payload["op"] == "acquire" and not reply["ok"] and self.held:  # "lease_held", and the holder turned out to be this client
            return {**{key: value for key, value in reply.items() if key != "reason"}, "ok": True, "adopted": True}
        return reply

    def _absorb(self, op: str, reply: dict) -> None:
        live = reply.get("state") == "held" and isinstance(reply.get("generation"), int) and isinstance(reply.get("host_session"), str)
        if not self.held:
            granted = op == "acquire" and reply["ok"]
            adopted = reply.get("request_id") is not None and reply.get("request_id") in self._pending_request_ids
            if live and (granted or adopted):
                try:
                    command_metadata(reply["generation"], reply["host_session"])
                except ValueError:
                    return
                self.generation, self.host_session, self.lost_reason, self._pending_request_ids = reply["generation"], reply["host_session"], None, []
            return
        if op == "release" and reply["ok"]:
            self.generation = self.host_session = None
            return
        if reply.get("host_session") != self.host_session:
            self._lose("host_restarted")
        elif not reply["ok"] and reply.get("reason") in LEASE_LOST_REASONS:
            self._lose(reply["reason"])
        elif reply.get("generation") != self.generation:
            self._lose("wrong_generation")
        elif reply.get("state") != "held":
            self._lose("lease_expired" if reply.get("state") == "expired" else "no_lease")

    def _lose(self, reason: str) -> None:
        self.generation = self.host_session = None
        self.lost_reason = reason
