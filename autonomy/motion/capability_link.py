#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""capability_link: how a robot-side consumer reaches the mobile_approach capability that Cake launched.

EXTRACTED FROM bin/pi/approach_probe.py, the non-motion probe that qualified this path against the real capability
process, so that autonomy/motion/capability_pulse.py opens the capability exactly the way that probe does and not
the way a second author would guess. Two halves, and nothing else:

  - THE CAKE READS that decide whether the capability's output may be used at all: list-slots and get-health through
    the robot's admin-probe, read at one moment and kept together (snapshot), so that a command can later be bound to
    the slot, generation, capsule and launched pid that were believed to be running;
  - THE TWO UNIX-DOMAIN ZEROMQ SOCKETS the capability speaks on (mobile-approach-spec.md chose them; Cake's admin
    socket is not a data channel): a SUB connected to its command endpoint and an XPUB bound at its observation
    endpoint. The XPUB is what lets the link WAIT FOR THE CAPABILITY'S OWN SUBSCRIPTION instead of sleeping and hoping:
    a publication before that subscription arrives is silently dropped by ZeroMQ.

It moves nothing and decides nothing. It opens no servo bus and no camera, it imports no lerobot and no
lerobot_transport, it never parses a command and never judges one: accepting a command is the caller's decision,
through cake_capability_adapter.bind_command_to_launched_process against the snapshot taken here. pyzmq is imported
only when a link is opened, so that every other part of this module is usable where pyzmq is not installed.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cake_capability_adapter import parse_health, parse_slots  # noqa: E402

#: The capability's name: its slot's name, and the first word of every command_id its launched process publishes.
CAPABILITY = "mobile_approach"
#: A settle for OUR subscription at the capability's publisher once its own has reached us. A wait, not a measurement:
#: the handshake below says the connection exists, not that the subscription travelled over it.
SUBSCRIPTION_SETTLE_S = 0.05
#: How long close() waits for the observation endpoint to stop accepting connections, and how often it looks. ZeroMQ
#: closes a bound listener ASYNCHRONOUSLY, in its I/O thread, after unbind() and close() have returned. Measured on the
#: development laptop (libzmq 4.3.5): right after close() a connection to the endpoint still succeeded in 283 of 300
#: trials, and under full CPU load the listener was gone within 7 ms in every one. The timeout is a bound, not that
#: measurement; the robot's figure is not measured.
RELEASE_TIMEOUT_S = 1.0
RELEASE_POLL_S = 0.002


class Refused(Exception):
    """A refusal before anything was published."""


def admin_reader(probe: str, admin_socket: str, timeout_s: float):
    """read(op) -> the text admin-probe printed. Only the two read operations this probe needs are ever run."""

    def read(op: str) -> str:
        if op not in ("list-slots", "get-health"):
            raise ValueError(f"{op} is not a read this probe performs")
        done = subprocess.run([probe, "request", admin_socket, op], capture_output=True, text=True, timeout=timeout_s)
        return done.stdout

    return read


def snapshot(read, slot_uuid: str) -> dict:
    """The capability slot and its health row, as the resident reported them at one moment."""
    listing, health = read("list-slots"), read("get-health")
    ok = "status ok" in listing.splitlines() and "status ok" in health.splitlines()
    slot = parse_slots(listing).get(slot_uuid) if ok else None
    row = parse_health(health).get(slot_uuid) if ok else None
    return {"ok": ok, "slot": slot, "health": row}


def describe(snap: dict) -> dict:
    slot, row = snap["slot"], snap["health"]
    return {
        "admin_reads_ok": snap["ok"],
        "slot": None if slot is None else {"uuid": slot.uuid, "name": slot.name, "state": slot.state,
                                           "capsule": slot.capsule, "generation": slot.generation},
        "health": None if row is None else {"surface": row.surface, "state_name": row.state_name, "arg1": row.arg1},
    }


def read_manifest(path: str) -> tuple:
    values = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            name, _, value = line.rstrip("\n").partition(" ")
            values.setdefault(name, value)
    return values.get("mobile_slot_uuid", ""), values.get("mobile_capsule_digest", "")


def ipc_path(endpoint: str) -> str:
    if not endpoint.startswith("ipc:///"):
        raise Refused(f"{endpoint.split('://')[0]} endpoint given; only an absolute ipc:// endpoint is supported")
    return endpoint[len("ipc://"):]


def someone_listens(path: str) -> bool:
    """True when a process accepts connections on the Unix socket at PATH (a stale file is not a listener)."""
    if not os.path.exists(path):
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


class CapabilityLink:
    """The two sockets, opened in the order the probe proved, and open only once the capability has subscribed.

    Constructing one CONNECTS a SUB to the capability's command endpoint and waits for the ZeroMQ handshake (so our
    subscription can reach the capability's publisher), then BINDS an XPUB at the observation endpoint and waits for
    the capability's subscriber to reconnect and subscribe, then settles. Any wait that runs out raises Refused, with
    every socket closed. `deadline` is the one monotonic deadline those waits share, which the probe then spends on
    its single answer. The caller has already checked that nothing else listens on the observation endpoint: binding
    it here would otherwise take it from its owner.
    """

    def __init__(self, observations: str, commands: str, timeout_s: float):
        import zmq

        self._observations, self._bound = observations, False
        context = zmq.Context.instance()
        self._inbound = context.socket(zmq.SUB)
        self._inbound.setsockopt(zmq.LINGER, 0)
        self._inbound.setsockopt(zmq.SUBSCRIBE, b"")
        self._monitor = self._inbound.get_monitor_socket(zmq.EVENT_HANDSHAKE_SUCCEEDED)
        self._outbound = context.socket(zmq.XPUB)
        self._outbound.setsockopt(zmq.LINGER, 0)
        try:
            self._inbound.connect(commands)
            self.deadline = time.monotonic() + timeout_s
            # Our subscription reaches the capability's publisher after the handshake; wait for it rather than guess.
            if not self._monitor.poll(int(timeout_s * 1000)):
                raise Refused("the capability's command endpoint did not complete a handshake: is the capability running?")
            self._monitor.recv_multipart()
            self._outbound.bind(observations)
            self._bound = True
            # The capability's subscriber reconnects to the endpoint we just bound; its subscription arrives here.
            while True:
                left = self.left_ms()
                if left <= 0 or not self._outbound.poll(left):
                    raise Refused("the capability never subscribed to the observation endpoint within the timeout")
                if self._outbound.recv()[:1] == b"\x01":
                    break
            time.sleep(SUBSCRIPTION_SETTLE_S)  # a settle for our own subscription at the capability's publisher, not a measurement
        except BaseException:
            self.close()
            raise

    def left_ms(self) -> int:
        """Whole milliseconds left before the link's own deadline, as the probe counted them."""
        return int((self.deadline - time.monotonic()) * 1000)

    def publish(self, payload: bytes) -> None:
        """One observation to the capability. ZeroMQ drops it if the capability has unsubscribed; nothing blocks."""
        self._outbound.send(payload)

    def receive(self, timeout_ms: int):
        """The next message the capability published, or None when none arrived within timeout_ms. A timeout of zero
        or less is not polled at all, exactly as the probe's last wait was not."""
        if timeout_ms <= 0 or not self._inbound.poll(timeout_ms):
            return None
        return self._inbound.recv()

    def close(self) -> bool:
        """Close the three sockets and wait until the observation endpoint this link bound is RELEASED. True when nothing
        listens on it any more (or this link never bound it), False when RELEASE_TIMEOUT_S ran out first.

        WHY IT WAITS, where the probe does not: the probe is a process that exits after one answer, while a relay closes
        its link and opens the next one a pulse later. ZeroMQ releases a bound endpoint in its I/O thread after close()
        has returned, so right after closing, the endpoint can still accept a connection; the next pulse's own check that
        nobody else owns the observation endpoint would then refuse on this link's leftover listener, and a check that
        the relay released it would pass or fail with the machine's load. So the endpoint is unbound explicitly first and
        close() returns only once a connection to it is refused (someone_listens, the same test those checks use). A
        False return leaves the refusal where it belongs: the next open finds the endpoint held and refuses to take it."""
        import zmq

        bound, self._bound = self._bound, False
        if bound:
            try:
                self._outbound.unbind(self._observations)
            except zmq.ZMQError:
                pass  # closing below releases it all the same; the wait decides whether it did
        self._inbound.close()
        self._outbound.close()
        self._monitor.close()
        if not bound or not self._observations.startswith("ipc://"):
            return True
        path, deadline = self._observations[len("ipc://"):], time.monotonic() + RELEASE_TIMEOUT_S
        while someone_listens(path):
            if time.monotonic() >= deadline:
                return False
            time.sleep(RELEASE_POLL_S)
        return True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False
