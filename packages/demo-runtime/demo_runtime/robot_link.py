# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.robot_link: the ROBOT's Cake resident, and the permission to move that rests on it.

THE ARCHITECTURE THIS SERVES. The LeKiwi robot runs its own cake-resident as a systemd user unit; the resident's
supervisor slot launches and supervises the robot host, and its mobile_approach slot launches the capability. This
desktop runs the frontend, the console API and this runtime. The runtime reaches the robot's admin socket through ONE
ssh unix-socket forward it owns (demo_runtime.forwards.UnixSocketForward) and asks it the four read-only questions of
cake_admin.READ_OPERATIONS. That is the whole connection: the desktop RELAYS requests and CACHES readings, and nothing
here substitutes a resident, a process table, a release directory or a picture of this machine for the robot's.

WHAT IS NEVER CONSULTED WHEN A ROBOT IS CONFIGURED. read_models.probe_cake's local probes (a release directory under the
checkout, /proc on this machine, XDG_RUNTIME_DIR sockets) describe THIS laptop. With cake.robot configured they are not
called at all (probe_robot below does not import them), and a forward that is down answers 'robot_unreachable' with the
last good reading retained and marked stale, never a local answer in its place.

WHAT SAYS WHICH DEPLOYMENT IS OBSERVED, AND WHAT DOES NOT. The build identity identifies the SOFTWARE the resident runs;
the pin refuses a different build and nothing more. Which deployment is observed is established by the authenticated
connection to the intended robot (the ssh forward to that robot's admin socket, or the local socket on the robot) and the
session that endpoint's resident reports now. So every reading's build_identity is compared with
cake.robot.expected_build_identity, which the operator pins from the deployed robot's own get-status, and a resident
reporting another build identity, or none, is turned into an UNREACHABLE reading with the reason
robot_resident_not_the_robot (or robot_build_identity_unreadable) before anything else sees it: it is never retained,
never shown as the robot's, never notes a session and never lets anything move. The code keeps its name; what it means is
"not the pinned build", and a resident of that same build elsewhere would pass it (a laptop x86 resident built from the
same source reports the same value, handoff 12.4). The target profile refuses nothing either: every build for this
profile shares it, so it is carried as displayed information only.

PERMISSION TO MOVE IS BOUND TO THE HOST INSTANCE THE OPERATOR SAW, AND LIVES ONLY IN THIS DAEMON. MotionPermission holds,
IN MEMORY, the robot resident's session_uuid and the supervisor.lekiwi HOST INSTANCE as they were when the operator
confirmed them: the slot's instance handle (list-slots `instance`), the supervisor's spawn sequence (get-health
`sequence`) and the child pid (get-health `arg1`). A resident restart changes the session, a slot re-create changes the
instance handle, a host restart changes the spawn sequence even when the operating system reuses the pid. A change of
ANY component, seen in any reading taken after the confirmation, REVOKES it, and it stays revoked until the operator
confirms again; so does a resident that answers and does not report the pinned build. A daemon restart starts with
nothing: the last confirmation is written to the data directory as an AUDIT record and is never read back as permission.
Every motion command is refused while none is confirmed, while the resident cannot be read, or while it does not report
the pinned build, and the
supervisor re-reads all of it FRESH immediately before it admits a motion command and journals the identity it admitted
the command under (admission()).

POLL TRAFFIC IS NOT MODULE ACTIVITY. Every admin connection this runtime makes writes flight records of the resident's
security domain (0x0B, EVT_ADMIN_CONNECTION_ACCEPTED and friends) whose subject is the connecting peer: this runtime's
own probe, seen through the forward. `updates` shows ONLY the allowlisted transaction and supervisor lifecycle codes
below and counts the admin-domain records it left out, so a console can never read its own polling as the robot doing
something. Every other record outside the allowlist is COUNTED too (other_records), so nothing disappears unannounced.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import cake_activation, cake_admin, forwards
from .config import robot_of

# 2: identity.is_robot now rests on build_identity (it rested on the target profile, which refuses nothing), slot
# rows carry `instance` as a decimal string (a u64 handle a browser cannot hold as a number), and motion permission binds
# the host INSTANCE (slot instance handle, spawn sequence, pid) where it bound a pid alone.
ROBOT_STATUS_VERSION = "2"
DEPLOYMENT_ROBOT, DEPLOYMENT_LAPTOP, DEPLOYMENT_NONE = "robot_resident", "this_laptop", "not_configured"
SUPERVISOR_SLOT_NAME = "supervisor.lekiwi"
ACTIVE = cake_activation.ACTIVE
CHECK_INTERVAL_S = 0.5
MAX_UPDATES = 64
# The last confirmation, as an AUDIT record only. Nothing reads it back as permission (MotionPermission).
CONFIRMATION_AUDIT_FILE = "robot-session-confirmation-audit.json"
CONFIRMATION_AUDIT_SCHEMA = "robot-session-confirmation-audit/1"

# The reason codes this module gives a refused motion command. contract v1's reason_code is a PATTERN, not a closed
# enum (common.schema.json#/$defs/reason_code), so these are carried as they are; the precise sentence travels in the
# robot_status surface's motion_permission and in the capability's own reason text.
NOT_CONFIRMED, SESSION_CHANGED, UNREACHABLE = "robot_session_not_confirmed", "robot_session_changed", "robot_unreachable"
HOST_CHANGED, HOST_UNREADABLE, HOST_NOT_RUNNING = "robot_host_changed", "robot_host_unreadable", "robot_host_not_running"
# The answering resident is not the pinned build: it reported another build identity, or none that can be read. The code
# names are kept; the build identity identifies software, not which robot answered (see the module docstring).
NOT_THE_ROBOT, BUILD_UNREADABLE = "robot_resident_not_the_robot", "robot_build_identity_unreadable"
IDENTITY_REFUSALS = (NOT_THE_ROBOT, BUILD_UNREADABLE)
# A confirmation that a resident NOT reporting the pinned build answered after: revoked, and named so once it is back.
IDENTITY_CHANGED = "robot_identity_changed"
MOTION_REFUSAL_CODES = (NOT_CONFIRMED, SESSION_CHANGED, HOST_CHANGED, HOST_UNREADABLE, UNREACHABLE, IDENTITY_CHANGED, *IDENTITY_REFUSALS)
# What changed, when a confirmation was revoked: motion_permission.changed_component.
CHANGED_COMPONENTS = ("session", "slot_instance", "spawn_sequence", "pid", "host_stopped", "identity")
HOST_INSTANCE_FIELDS = ("slot_instance", "spawn_sequence", "pid")
HEALTHY = "HEALTHY"  # the supervisor module's health state while its child runs (cake-admin health_state::HEALTHY)
MAX_SLOT_INSTANCE = (1 << 64) - 1  # a Cake instance handle is a u64: carried as a DECIMAL STRING, never a JSON number
MAX_SPAWN_SEQUENCE = (1 << 53) - 1  # the largest integer a browser reads exactly; the supervisor's own counter is a u32
MAX_HOST_PID = 1 << 22  # PID_MAX_LIMIT on Linux

# The flight records `updates` shows, by code, with the names docs/event-code-registry.md of the Cake workspace gives
# them. An allowlist: a code outside it is not shown, whatever it is.
TRANSACTION_EVENTS = {
    0x03000001: "EVT_PLAN_ACTIVATED", 0x03000002: "EVT_PLAN_CANDIDATE_FAILURE", 0x03000003: "EVT_PLAN_REFUSED",
    0x03000004: "EVT_EPOCH_PUBLISHED", 0x03000006: "EVT_TXN_TRANSITION", 0x03000007: "EVT_TXN_TRANSITION_REFUSED",
    0x03000010: "EVT_TXN_ROLLBACK_REQUESTED", 0x03000011: "EVT_TXN_ROLLBACK_COMMITTED", 0x03000012: "EVT_TXN_ROLLBACK_REFUSED",
}
SUPERVISOR_EVENTS = {
    0x05FFFFFC: "EVT_SUPERVISOR_CHILD_STARTED", 0x05FFFFFB: "EVT_SUPERVISOR_CHILD_EXITED",
    0x05FFFFFA: "EVT_SUPERVISOR_OUTPUT_ACCOUNTED", 0x05FFFFF9: "EVT_SUPERVISOR_RESTART_BOUND_REACHED",
    0x05FFFFF8: "EVT_SUPERVISOR_HEALTH_CHANGED", 0x05FFFFF7: "EVT_SUPERVISOR_SPAWN_FAILED",
    0x05FFFFF6: "EVT_SUPERVISOR_SAFE_STOP_RAN", 0x05FFFFF5: "EVT_SUPERVISOR_SAFE_STOP_FAILED",
}
ADMIN_DOMAIN = 0x0B  # the resident's security domain: every admin connection, this runtime's own polls included

_HEX = re.compile(r"^[0-9a-f]{8,64}$")
_BUILD = re.compile(r"^[0-9a-f]{64}$")
_SLOT_INSTANCE = re.compile(r"^[1-9][0-9]{0,19}$")
_SESSION = re.compile(r"^[0-9a-f]{8,64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ---- the link: one forward, kept up ----------------------------------------------------------------------------------

class RobotLink:
    """The robot's admin-socket forward and the thread that keeps it up. Nothing else: no reading, no command.

    The thread only calls forward.check(), which notices a death and restarts after the backoff. A reader that wants
    the state NOW calls check() itself; both are serialized by the forward's own lock."""

    def __init__(self, config, *, forward=None, interval_s: float = CHECK_INTERVAL_S):
        robot = robot_of(config)
        if forward is None:
            forward = forwards.UnixSocketForward(ssh_target=getattr(config, "pi_ssh_target", "") or "",
                                                 remote_socket=robot.remote_admin_socket_path if robot else "",
                                                 local_socket=robot.local_admin_socket_path if robot else "")
        self.forward, self._interval = forward, interval_s
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "RobotLink":
        self.forward.start()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="robot-admin-forward", daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._halt.wait(self._interval):
            try:
                self.forward.check()
            except Exception:  # noqa: BLE001 -- the keeper must outlive one bad look; the state says what it saw
                continue

    def check(self) -> str:
        return self.forward.check()

    def connected(self) -> bool:
        return self.check() == forwards.CONNECTED

    def report(self) -> dict:
        return self.forward.report()

    def stop(self) -> None:
        self._halt.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.forward.stop()


class LocalRobotLink:
    """The robot's resident on THIS machine: the ROBOT-SIDE deployment (cake.robot.link local), with no forward at all.

    WHY IT EXISTS. On the robot this runtime runs beside the resident, and the resident's admin socket is a local file
    (mode 0600, this user's). An ssh forward from the robot to itself would add a process, a key and a failure mode and
    prove nothing more. So this link opens nothing: `connected()` is whether something is LISTENING on that socket now
    (local_socket_accepts, the same question probe_robot already asks after a failed read), and every reading still goes
    through cake_admin's read-only allowlist and is still checked against cake.robot.expected_build_identity by the
    readers, exactly as through a forward (verify_identity). Here the local socket on the robot is what establishes which
    deployment is observed, and the session its resident reports now is which resident; the build pin only refuses a
    different build.

    The same interface as RobotLink (start, check, connected, report, stop) and the same states in `report`, so the
    robot surface reads one or the other without knowing which. `report` names no path, like a forward's."""

    def __init__(self, config, *, accepts: Optional[Callable[[str], bool]] = None, clock_ns: Callable[[], int] = time.monotonic_ns):
        robot = robot_of(config)
        self._socket = robot.local_admin_socket_path if robot is not None else ""
        # local_socket_accepts is defined further down this module; a test hands its own.
        self._accepts, self._clock_ns = accepts or local_socket_accepts, clock_ns
        self._lock = threading.Lock()
        self.state, self.reason = forwards.STOPPED, None
        self.since_mono_ns = clock_ns()
        self.since_wall_utc = _wall_now()
        self.checks = 0

    @property
    def configured(self) -> bool:
        return self._socket.startswith("/")

    def start(self) -> "LocalRobotLink":
        self.check()
        return self

    def _set(self, state: str, reason: Optional[str]) -> None:
        with self._lock:
            if (state, reason) != (self.state, self.reason):
                self.since_mono_ns, self.since_wall_utc = self._clock_ns(), _wall_now()
            self.state, self.reason = state, reason

    def check(self) -> str:
        """Look once at the resident's socket. CONNECTED while something listens there, FAILED with a reason otherwise."""
        with self._lock:
            self.checks += 1
        if not self.configured:
            self._set(forwards.NOT_CONFIGURED, "local_socket_not_configured")
        elif self._accepts(self._socket):
            self._set(forwards.CONNECTED, None)
        else:
            self._set(forwards.FAILED, "local_resident_socket_not_listening")
        return self.state

    def connected(self) -> bool:
        return self.check() == forwards.CONNECTED

    def report(self) -> dict:
        """States, reason codes and times, in a forward's shape. No path: `link` says only that nothing is forwarded."""
        with self._lock:
            now = self._clock_ns()
            return {"purpose": forwards.ROBOT_ADMIN, "configured": self.configured, "state": self.state, "reason": self.reason,
                    "since_wall_utc": self.since_wall_utc, "since_mono_ns": self.since_mono_ns,
                    "since_age_ms": max(0.0, round((now - self.since_mono_ns) / 1e6, 3)),
                    "starts": 0, "deaths": 0, "restarts": 0, "forced_stops": 0, "exit_status": None,
                    "next_attempt_in_ms": None, "child": False, "link": "local"}

    def stop(self) -> None:
        self._set(forwards.STOPPED, None)


# ---- reading the robot's resident ------------------------------------------------------------------------------------

def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fields(line: str, prefix: str) -> dict:
    out = {}
    for token in line[len(prefix):].split():
        name, separator, value = token.partition("=")
        if separator:
            out[name] = value
    return out


def slot_instance_of(value) -> Optional[str]:
    """A slot's instance handle as list-slots printed it, as a canonical DECIMAL STRING, or None. A handle is a u64 and
    is session local (Cake CAKE-ID-006); 0 is no handle. A string, because a browser cannot hold a u64 as a number."""
    text = value if isinstance(value, str) else ""
    if not _SLOT_INSTANCE.match(text) or int(text) > MAX_SLOT_INSTANCE:
        return None
    return text


def parse_slot_rows(text: str) -> list:
    """list-slots, as rows in the order a screen reads them. The parse is cake_activation.parse_slots, the same one
    that decides an activation was observed, so the two cannot disagree about a slot."""
    rows = []
    for slot in cake_activation.parse_slots(text or "").values():
        if not _TOKEN.match(slot["name"]) or not _HEX.match(slot["uuid"].lower()) or not _TOKEN.match(slot["state"]):
            continue
        rows.append({"name": slot["name"], "uuid": slot["uuid"].lower(),
                     "capsule": slot["capsule"].lower() if _HEX.match(slot["capsule"].lower()) else None,
                     "generation": slot["generation"] if slot["generation"] >= 0 else None,
                     "instance": slot_instance_of(slot.get("instance")),
                     "state": slot["state"]})
    return sorted(rows, key=lambda row: row["name"])


def parse_health_rows(text: str) -> list:
    """get-health's `health ...` lines: arg1 is the child pid, flags the restarts, sequence the spawns, code the raw
    waitpid status of the last exit. A line missing any of them is skipped rather than half-read."""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("health "):
            continue
        fields = _fields(line, "health ")
        slot, state_name = fields.get("slot", "").lower(), fields.get("state_name", "")
        numbers = {name: _int(fields.get(key)) for name, key in (("child_pid", "arg1"), ("restarts", "flags"),
                                                                 ("spawns", "sequence"), ("last_status", "code"))}
        if not _HEX.match(slot) or not _TOKEN.match(state_name) or any(value is None or value < 0 for value in numbers.values()):
            continue
        rows.append({"slot": slot, "state_name": state_name, **numbers})
    return rows


def parse_updates(records: list) -> dict:
    """read-flight's records, as the transaction and supervisor lifecycle updates they are, newest last, bounded.

    The resident's admin-domain records are COUNTED and left out: every one of this runtime's own polls writes some,
    with the connecting peer as their subject, and showing them would report the polling as module activity."""
    items, excluded, other = [], 0, 0
    for record in records or ():
        fields = _fields("record " + record, "record ") if isinstance(record, str) else {}
        try:
            code = int(fields.get("event", ""), 16)
        except ValueError:
            continue
        if code >> 24 == ADMIN_DOMAIN:
            excluded += 1
            continue
        name = TRANSACTION_EVENTS.get(code) or SUPERVISOR_EVENTS.get(code)
        sequence = _int(fields.get("sequence"))
        if name is None or sequence is None or sequence < 0:
            other += 1
            continue
        items.append({"sequence": sequence, "event": f"0x{code:08X}", "name": name,
                      "kind": "transaction" if code in TRANSACTION_EVENTS else "supervisor",
                      "arg0": _int(fields.get("arg0")), "arg1": _int(fields.get("arg1")), "epoch": _int(fields.get("epoch"))})
    items.sort(key=lambda item: item["sequence"])
    dropped = max(0, len(items) - MAX_UPDATES)
    return {"items": items[dropped:], "dropped": dropped, "excluded_admin_records": excluded, "other_records": other}


def read_robot(probe_path: str, socket_path: str, *, timeout_s: float = cake_admin.DEFAULT_TIMEOUT_S) -> dict:
    """ONE reading of the robot's resident: get-status, then list-slots, get-health and read-flight, all through
    cake_admin.run_probe and so through its read-only allowlist. The inventory shape is cake_admin's own (the cake
    surfaces read it unchanged); what only the robot surface needs travels under `robot_detail`, which is INTERNAL and
    never leaves this runtime as it is."""
    observed = cake_admin.observed_now()
    status = cake_admin.run_probe(probe_path, socket_path, "get-status", timeout_s=timeout_s)
    if not status.get("ok"):
        return cake_admin.inventory_of(status, None, observed)
    slots = cake_admin.run_probe(probe_path, socket_path, "list-slots", timeout_s=timeout_s)
    health = cake_admin.run_probe(probe_path, socket_path, "get-health", timeout_s=timeout_s)
    flight = cake_admin.run_probe(probe_path, socket_path, "read-flight", timeout_s=timeout_s)
    inventory = cake_admin.inventory_of(status, flight, observed)
    inventory["robot_detail"] = _detail_of(slots, health, flight)
    return inventory


def _detail_of(slots: dict, health: dict, flight: Optional[dict]) -> dict:
    flight = flight if isinstance(flight, dict) else {"ok": False, "reason_code": "not_read"}
    return {
        "slots_reason": None if slots.get("ok") else (slots.get("reason_code") or "admin_answer_not_ok"),
        "slots_text": slots.get("text", "") if slots.get("ok") else "",
        "slots": parse_slot_rows(slots.get("text", "")) if slots.get("ok") else [],
        "health_reason": None if health.get("ok") else (health.get("reason_code") or "admin_answer_not_ok"),
        "health": parse_health_rows(health.get("text", "")) if health.get("ok") else [],
        "updates_reason": None if flight.get("ok") else (flight.get("reason_code") or "admin_answer_not_ok"),
        "updates": parse_updates(flight.get("records", [])) if flight.get("ok") else parse_updates([]),
    }


def verify_identity(inventory, expected_build_identity: str):
    """A reachable reading whose resident reports a build_identity other than `expected_build_identity` is refused.

    A BUILD PIN, NOT AN IDENTIFICATION OF A MACHINE. `expected_build_identity` is cake.robot.expected_build_identity,
    pinned by the operator from the deployed robot's own get-status. The build identity identifies the SOFTWARE the
    resident runs; the pin refuses a different build and nothing more. Which deployment is observed is established by the
    authenticated connection to the intended robot (the ssh forward to that robot's admin socket, or the local socket on
    the robot) and the session that endpoint's resident reports now. A reading that does not report exactly the pinned
    build is replaced by an UNREACHABLE reading, reason robot_resident_not_the_robot (or robot_build_identity_unreadable
    when none can be read), before any reader, the retention or the session tracking sees it: it is never shown as the
    robot's, never retained as its last reading and never lets anything move. What DID answer is kept under
    `answering_build_identity` and `answering_target_profile_digest`, INTERNAL, for the robot surface to name as the
    answering resident's and never as the robot's. The target profile refuses nothing (every build for this profile
    shares it)."""
    if not isinstance(inventory, dict) or not inventory.get("reachable"):
        return inventory
    identity = inventory.get("identity") or {}
    answered = identity.get("build_identity")
    if isinstance(expected_build_identity, str) and _BUILD.match(expected_build_identity) and answered == expected_build_identity:
        return inventory
    readable = isinstance(answered, str) and _BUILD.match(answered) is not None
    profile = identity.get("target_profile_digest")
    refused = {"reachable": False, "reason_code": NOT_THE_ROBOT if readable else BUILD_UNREADABLE, "identity": {}, "counts": {},
               "flight": None, "mission_participation": False,
               "answering_build_identity": answered if readable else None,
               "answering_target_profile_digest": profile if isinstance(profile, str) and _BUILD.match(profile) else None}
    refused.update({name: inventory[name] for name in ("observed_wall_utc", "observed_mono_ns", "clock_domain") if name in inventory})
    return refused


def local_socket_accepts(path: str, timeout_s: float = 0.5) -> bool:
    """Whether anything is LISTENING on the forward's local socket. Asked only after a read failed, to tell a stale
    socket file (nobody there: a connection failure) from a resident that answered and refused (a Cake answer)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout_s)
            probe.connect(path)
            return True
    except OSError:
        return False


def probe_robot(link: Optional[RobotLink], probe_path: Optional[str], socket_path: Optional[str], *, expected_build_identity: str,
                timeout_s: float = cake_admin.DEFAULT_TIMEOUT_S) -> dict:
    """The cake probe of a runtime whose resident is the ROBOT's. Nothing on this laptop is consulted: no release
    directory, no process table, no runtime-directory socket. The `scope` says whose resident the answer is about: the
    one at the end of this runtime's connection to the robot. An answer from a resident that does not report
    `expected_build_identity` is refused as another build (verify_identity); the pin says which software, not which
    machine."""
    out = {"scope": DEPLOYMENT_ROBOT, "artifacts": None, "resident": False, "admin_socket": False}
    if link is None:
        inventory = cake_admin.unavailable("robot_link_not_started")
    elif not link.connected():
        inventory = cake_admin.unavailable(UNREACHABLE)
    elif probe_path is None or socket_path is None:
        inventory = cake_admin.unavailable("not_configured")
    else:
        inventory = read_robot(probe_path, socket_path, timeout_s=timeout_s)
        if not inventory.get("reachable") and not local_socket_accepts(socket_path):
            # The forward said connected a moment ago and nothing is listening now: that is the connection failing,
            # not the resident refusing, and it is reported as the connection.
            inventory["reason_code"] = "robot_forward_socket_stale"
        elif not inventory.get("reachable") and inventory.get("reason_code") == "admin_probe_refused":
            # ssh accepted locally and the client still failed. ExitOnForwardFailure does not cover this: a unix-socket
            # forward reaches the remote socket per connection, so a resident that is down on the robot looks exactly
            # like this. It is a resident that did not answer, not an answer, and is named so. A resident that DID
            # answer and said no keeps admin_answer_not_ok.
            inventory["reason_code"] = "robot_resident_not_answering"
        inventory = verify_identity(inventory, expected_build_identity)
    if inventory.get("reachable"):
        out["resident"] = out["admin_socket"] = True
    out["inventory"] = inventory
    return out


# ---- the permission to move ------------------------------------------------------------------------------------------

def _wall_now() -> str:
    return cake_admin.observed_now()["observed_wall_utc"]


def supervisor_host_instance(detail: dict) -> tuple:
    """(host_instance, running, reason): the supervisor.lekiwi HOST INSTANCE, from list-slots and get-health.

    host_instance is {"slot_instance": "<decimal u64>", "spawn_sequence": N, "pid": N}, and it is given ONLY when a host
    runs under that slot now and all three components were read: the slot is ACTIVE with an instance handle, and its
    get-health row reports state HEALTHY, a nonzero child pid (arg1) and the supervisor's spawn count (sequence).

    running is False when NO host runs there now (the slot is absent or not ACTIVE, the child pid is 0, or the supervisor
    reports anything but HEALTHY, which is what a held or DEGRADED host looks like): a CHANGE from any instance an
    operator confirmed. running is None when it could not be READ (a listing or the health answer failed, or a component
    is missing or out of range): unknown, which refuses motion without revoking anything."""
    if not isinstance(detail, dict) or detail.get("slots_reason") is not None:
        return None, None, "slot_listing_unavailable"
    slot = next((row for row in detail.get("slots", []) if row["name"] == SUPERVISOR_SLOT_NAME), None)
    if slot is None:
        return None, False, "supervisor_slot_absent"
    if slot["state"] != ACTIVE:
        return None, False, "supervisor_slot_not_active"
    if detail.get("health_reason") is not None:
        return None, None, "health_unavailable"
    row = next((row for row in detail.get("health", []) if row["slot"] == slot["uuid"]), None)
    if row is None:
        return None, None, "supervisor_health_absent"
    if not row["child_pid"]:
        return None, False, "supervisor_child_not_running"
    if row["state_name"] != HEALTHY:
        return None, False, "supervisor_host_not_healthy"
    if slot.get("instance") is None:
        return None, None, "supervisor_slot_instance_unreadable"
    if not 1 <= row["spawns"] <= MAX_SPAWN_SEQUENCE:
        return None, None, "supervisor_spawn_sequence_unreadable"
    if not 1 <= row["child_pid"] <= MAX_HOST_PID:
        return None, None, "supervisor_child_pid_unreadable"
    return {"slot_instance": slot["instance"], "spawn_sequence": row["spawns"], "pid": row["child_pid"]}, True, None


def supervisor_slot_reading(detail: dict) -> Optional[dict]:
    """What ONE reading showed of the supervisor.lekiwi slot and its get-health row, WHATEVER supervisor_host_instance
    answers: that function stops at the first thing that decides whether a host runs, and a slot that is not ACTIVE, or a
    child pid 0, stops it before the counters are kept. These are the counters, kept for a caller that has to compare two
    readings (demo_runtime.capability_check: a host HELD at two reads is held THROUGHOUT only when the supervisor did not
    spawn or restart anything between them, and only its counters can say so).

    None when the slot listing could not be read. Otherwise {slot_present, slot_state, slot_instance (decimal string),
    slot_generation, health_read (the get-health answer was read), health_row (it had a row for this slot), child_pid
    (arg1), spawns (sequence), restarts (flags), health_state (state_name)}; a field not read is None. Read-only: it
    decides nothing about motion."""
    if not isinstance(detail, dict) or detail.get("slots_reason") is not None:
        return None
    slot = next((row for row in detail.get("slots", []) if row["name"] == SUPERVISOR_SLOT_NAME), None)
    health_read = detail.get("health_reason") is None
    out = {"slot_present": slot is not None, "slot_state": None, "slot_instance": None, "slot_generation": None,
           "health_read": health_read, "health_row": False, "child_pid": None, "spawns": None, "restarts": None,
           "health_state": None}
    if slot is None:
        return out
    out.update(slot_state=slot["state"], slot_instance=slot.get("instance"), slot_generation=slot.get("generation"))
    row = next((row for row in detail.get("health", []) if row["slot"] == slot["uuid"]), None) if health_read else None
    if row is not None:
        out.update(health_row=True, child_pid=row["child_pid"], spawns=row["spawns"], restarts=row["restarts"],
                   health_state=row["state_name"])
    return out


def binding_of(inventory) -> dict:
    """What motion permission binds, from ONE reading: {session_uuid, build_identity, host_instance, host_running,
    reason_code, observed_mono_ns, observed_wall_utc}, and beside them supervisor_slot (supervisor_slot_reading: the
    supervisor slot and its health counters as read, None when not read), which motion permission does not bind. A
    reading that is not current (or not the pinned build: verify_identity made it unreachable) binds nothing and carries
    its reason."""
    empty = {"session_uuid": None, "build_identity": None, "host_instance": None, "host_running": None,
             "observed_mono_ns": None, "observed_wall_utc": None, "supervisor_slot": None}
    if not isinstance(inventory, dict) or not inventory.get("reachable"):
        reason = inventory.get("reason_code") if isinstance(inventory, dict) else None
        stamp = inventory.get("observed_mono_ns") if isinstance(inventory, dict) and isinstance(inventory.get("observed_mono_ns"), int) else None
        return {**empty, "reason_code": reason or UNREACHABLE, "observed_mono_ns": stamp}
    stamp = inventory.get("observed_mono_ns") if isinstance(inventory.get("observed_mono_ns"), int) else None
    wall = inventory.get("observed_wall_utc") if isinstance(inventory.get("observed_wall_utc"), str) else None
    session = session_of(inventory)
    build = (inventory.get("identity") or {}).get("build_identity")
    build = build if isinstance(build, str) and _BUILD.match(build) else None
    if session is None:
        return {**empty, "build_identity": build, "reason_code": "robot_session_unreadable", "observed_mono_ns": stamp,
                "observed_wall_utc": wall}
    instance, running, reason = supervisor_host_instance(_detail(inventory))
    return {"session_uuid": session, "build_identity": build, "host_instance": instance, "host_running": running,
            "reason_code": reason, "observed_mono_ns": stamp, "observed_wall_utc": wall,
            "supervisor_slot": supervisor_slot_reading(_detail(inventory))}


def read_binding(probe_path: Optional[str], socket_path: Optional[str], *, expected_build_identity: str,
                 timeout_s: float = cake_admin.DEFAULT_TIMEOUT_S) -> dict:
    """A FRESH read of what motion permission binds: get-status (its build identity checked first), then list-slots and
    get-health, all through cake_admin's read-only allowlist. binding_of's shape."""
    observed = cake_admin.observed_now()
    status = cake_admin.run_probe(probe_path, socket_path, "get-status", timeout_s=timeout_s)
    inventory = verify_identity(cake_admin.inventory_of(status, None, observed), expected_build_identity)
    if inventory.get("reachable"):
        slots = cake_admin.run_probe(probe_path, socket_path, "list-slots", timeout_s=timeout_s)
        health = cake_admin.run_probe(probe_path, socket_path, "get-health", timeout_s=timeout_s)
        inventory["robot_detail"] = _detail_of(slots, health, None)
    return binding_of(inventory)


def host_instance_of(value) -> Optional[dict]:
    """A host instance as a CLIENT sent it, in its exact shape, or None: {"slot_instance": "<canonical decimal u64>",
    "spawn_sequence": 1..2**53-1, "pid": 1..2**22}, no other key, no bool standing in for a number."""
    if not isinstance(value, dict) or set(value) != set(HOST_INSTANCE_FIELDS):
        return None
    slot_instance, spawns, pid = value.get("slot_instance"), value.get("spawn_sequence"), value.get("pid")
    if not isinstance(slot_instance, str) or slot_instance_of(slot_instance) != slot_instance:
        return None
    for number, top in ((spawns, MAX_SPAWN_SEQUENCE), (pid, MAX_HOST_PID)):
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= top:
            return None
    return {"slot_instance": slot_instance, "spawn_sequence": spawns, "pid": pid}


_CHANGE_SENTENCES = {
    "session": (SESSION_CHANGED, "the robot's resident reports a different session than the one the operator confirmed, "
                                 "which is what a resident restart looks like; motion stays refused until the operator "
                                 "confirms again"),
    "slot_instance": (HOST_CHANGED, "the robot's supervisor slot has a different instance handle than the one the operator "
                                    "confirmed (the slot was re-created); motion stays refused until the operator confirms again"),
    "spawn_sequence": (HOST_CHANGED, "the robot's supervisor has spawned its host again since the operator confirmed it "
                                     "(the spawn sequence changed, whatever the pid); motion stays refused until the operator "
                                     "confirms again"),
    "pid": (HOST_CHANGED, "the robot's supervisor slot runs a different host process than the one the operator confirmed; "
                          "motion stays refused until the operator confirms again"),
    "host_stopped": (HOST_CHANGED, "no host runs under the robot's supervisor slot now (it stopped, is held or is degraded), "
                                   "so it is not the host the operator confirmed; motion stays refused until the operator "
                                   "confirms a running host again"),
    "identity": (IDENTITY_CHANGED, "a resident reporting a build other than the one pinned for the robot answered through "
                                   "the robot connection after the operator confirmed, so that confirmation was revoked; "
                                   "motion stays refused until the operator confirms again"),
}


class MotionPermission:
    """The robot resident session AND supervisor HOST INSTANCE the operator confirmed, and the refusal every motion command
    gets without them.

    IN MEMORY ONLY. Written only by confirm(), which the daemon calls only for an explicit operator confirmation naming
    the session and the host instance (slot instance handle, spawn sequence, pid) the robot reports NOW. A new daemon
    starts with no permission whatever is on disk: the last confirmation is written beside the data as an AUDIT record
    (grants_permission false) and never read back.

    REVOKED BY ANY CHANGE. A reading taken after the confirmation that shows another session, another slot instance
    handle, another spawn sequence, another child pid, no running host, or a resident not reporting the pinned build
    revokes it;
    it stays revoked, with that reason and the component that changed, until the operator confirms again. A reading that
    cannot be taken refuses motion and revokes nothing: a dropped link is not a change, and a change that happened during
    it is seen when the link returns. A reading taken BEFORE the confirmation (a cached one) refuses without revoking, so
    it cannot cancel a confirmation it predates."""

    def __init__(self, data_dir, *, wall: Callable[[], str] = _wall_now, clock_ns: Callable[[], int] = time.monotonic_ns):
        self._audit_path = Path(data_dir) / CONFIRMATION_AUDIT_FILE
        self._wall, self._clock_ns = wall, clock_ns
        self._lock = threading.Lock()
        self._grant: Optional[dict] = None
        self._revoked: Optional[tuple] = None
        self._changed: Optional[str] = None

    def confirmed(self) -> Optional[dict]:
        with self._lock:
            grant = self._grant
            if grant is None:
                return None
            return {"session_uuid": grant["session_uuid"], "build_identity": grant["build_identity"],
                    "host_instance": dict(grant["host_instance"]), "confirmed_wall_utc": grant["confirmed_wall_utc"]}

    def changed_component(self) -> Optional[str]:
        """Which component's change revoked the last confirmation, while it stays revoked; None otherwise."""
        with self._lock:
            return self._changed if self._grant is None else None

    def last_audit(self) -> Optional[dict]:
        """The audit record of the last confirmation, as written. A RECORD: nothing here treats it as permission."""
        try:
            raw = json.loads(self._audit_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) and raw.get("schema") == CONFIRMATION_AUDIT_SCHEMA else None

    def confirm(self, session_uuid: str, host_instance: dict, *, build_identity: Optional[str], runtime_session: str) -> dict:
        """Grant permission bound to (session_uuid, host_instance), after writing the audit record atomically, 0600. A
        confirmation whose record cannot be written raises and grants nothing."""
        wall = self._wall()
        instance = {name: host_instance[name] for name in HOST_INSTANCE_FIELDS}
        record = {"schema": CONFIRMATION_AUDIT_SCHEMA, "grants_permission": False, "runtime_session": runtime_session,
                  "session_uuid": session_uuid, "build_identity": build_identity, "host_instance": instance,
                  "confirmed_wall_utc": wall}
        temporary = self._audit_path.with_name(self._audit_path.name + ".tmp")
        with self._lock:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, sort_keys=True))
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
            os.replace(temporary, self._audit_path)
            self._grant = {"session_uuid": session_uuid, "build_identity": build_identity, "host_instance": instance,
                           "confirmed_wall_utc": wall, "granted_mono_ns": self._clock_ns()}
            self._revoked, self._changed = None, None
            return {"session_uuid": session_uuid, "build_identity": build_identity, "host_instance": dict(instance),
                    "confirmed_wall_utc": wall}

    def refusal(self, binding: Optional[dict]) -> Optional[tuple]:
        """(reason_code, sentence) when a motion command must be refused, else None. `binding` is binding_of's shape."""
        return self.admission(binding)[0]

    def admission(self, binding: Optional[dict]) -> tuple:
        """(refusal, admitted): exactly one is None. `admitted` is the identity a motion command is admitted under, for
        the journal: {session_uuid, build_identity, host_instance, confirmed_wall_utc, read_wall_utc}."""
        binding = binding if isinstance(binding, dict) else {}
        session, instance, running = binding.get("session_uuid"), binding.get("host_instance"), binding.get("host_running")
        reason, observed = binding.get("reason_code"), binding.get("observed_mono_ns")
        with self._lock:
            grant = self._grant
            after_grant = grant is not None and (observed is None or observed >= grant["granted_mono_ns"])
            if reason in IDENTITY_REFUSALS:
                if after_grant:
                    self._revoke_locked("identity")
                if reason == NOT_THE_ROBOT:
                    return (NOT_THE_ROBOT, ("the resident answering through the robot connection reports a build identity "
                                            "other than the one pinned for the robot, so it is not the pinned build; nothing "
                                            "that moves is accepted")), None
                return (BUILD_UNREADABLE, ("the resident answering through the robot connection reports no readable build "
                                           "identity, so the build pin cannot be checked; nothing that moves is accepted")), None
            if grant is None:
                if self._revoked is not None:
                    return self._revoked, None
                return (NOT_CONFIRMED, ("no robot resident session and host instance have been confirmed by the operator in "
                                        "this runtime, so no command that moves the robot is accepted; confirm the connected "
                                        "robot first (observe stays available)")), None
            if session is None:
                return (UNREACHABLE, (f"the robot's resident cannot be read ({reason or 'no answer'}), so nothing says it is "
                                      "still the session and host the operator confirmed; motion is refused until it answers")), None
            if session != grant["session_uuid"]:
                component = "session"
            elif running is False:
                component = "host_stopped"
            elif instance is None:
                return (HOST_UNREADABLE, (f"the robot's supervisor host cannot be read ({reason or 'no answer'}), so nothing "
                                          "says it is still the host instance the operator confirmed; motion is refused until "
                                          "it is")), None
            else:
                component = next((name for name in HOST_INSTANCE_FIELDS if instance.get(name) != grant["host_instance"][name]), None)
            if component is None:
                return None, {"session_uuid": session, "build_identity": grant["build_identity"],
                              "host_instance": dict(instance), "confirmed_wall_utc": grant["confirmed_wall_utc"],
                              "read_wall_utc": binding.get("observed_wall_utc")}
            change = _CHANGE_SENTENCES[component]
            if after_grant:
                self._revoke_locked(component)
            return change, None

    def _revoke_locked(self, component: str) -> None:
        self._grant, self._revoked, self._changed = None, _CHANGE_SENTENCES[component], component


# ---- the robot_status surface ----------------------------------------------------------------------------------------

def _observed(inventory: Optional[dict], now_mono_ns: int) -> Optional[dict]:
    if not isinstance(inventory, dict):
        return None
    stamp, wall = inventory.get("observed_mono_ns"), inventory.get("observed_wall_utc")
    if not isinstance(stamp, int) or not isinstance(wall, str):
        return None
    return {"wall_utc": wall, "age_ms": max(0.0, round((now_mono_ns - stamp) / 1e6, 3))}


def _now_observed(now_mono_ns: int, now_wall: str) -> dict:
    return {"wall_utc": now_wall, "age_ms": 0.0}


def _hex_or_none(value) -> Optional[str]:
    return value if isinstance(value, str) and _HEX.match(value) else None


def _count(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def session_of(inventory) -> Optional[str]:
    """The session a CURRENT reading reports, or None. A retained reading never answers for now."""
    if not isinstance(inventory, dict) or not inventory.get("reachable"):
        return None
    session = (inventory.get("identity") or {}).get("session_uuid")
    return session if isinstance(session, str) and _SESSION.match(session) else None


def _detail(inventory) -> dict:
    detail = inventory.get("robot_detail") if isinstance(inventory, dict) else None
    return detail if isinstance(detail, dict) else {}


def _section_source(current, retained):
    """(inventory, stale): the current reading when there is one, else the retained one marked stale, else none."""
    if isinstance(current, dict) and current.get("reachable"):
        return current, False
    if isinstance(retained, dict) and retained.get("reachable"):
        return retained, True
    return None, False


def _host_view(current, readiness, host_pids, now_observed, unreachable_reason=UNREACHABLE) -> dict:
    """supervised is TRUE ONLY when three separate witnesses agree: the robot's supervisor.lekiwi slot is ACTIVE, its
    get-health names a nonzero child pid in state HEALTHY, and the ssh readiness probe of the robot computer found a running safe-attach
    host with exactly that pid. Any one of them missing is `false` with the reason, never a guess."""
    out = {"supervised": False, "reason_code": None, "slot_uuid": None, "slot_state": None, "slot_instance": None,
           "child_pid": None, "spawn_sequence": None, "health_state": None,
           "readiness_state": getattr(readiness, "state", None) if readiness is not None else None,
           "readiness_age_ms": getattr(readiness, "age_ms", None) if readiness is not None else None,
           "observed": now_observed}
    detail = _detail(current)
    slot = next((row for row in detail.get("slots", []) if row["name"] == SUPERVISOR_SLOT_NAME), None)
    if current is None:
        out["reason_code"] = unreachable_reason or UNREACHABLE
        return out
    if slot is None:
        out["reason_code"] = "supervisor_slot_absent" if detail.get("slots_reason") is None else "slot_listing_unavailable"
        return out
    out["slot_uuid"], out["slot_state"], out["slot_instance"] = slot["uuid"], slot["state"], slot.get("instance")
    if slot["state"] != ACTIVE:
        out["reason_code"] = "supervisor_slot_not_active"
        return out
    row = next((row for row in detail.get("health", []) if row["slot"] == slot["uuid"]), None)
    if row is None:
        out["reason_code"] = "supervisor_health_absent" if detail.get("health_reason") is None else "health_unavailable"
        return out
    out["child_pid"], out["spawn_sequence"], out["health_state"] = row["child_pid"], row["spawns"], row["state_name"]
    if not row["child_pid"]:
        out["reason_code"] = "supervisor_child_not_running"
        return out
    if row["state_name"] != HEALTHY:
        out["reason_code"] = "supervisor_host_not_healthy"
        return out
    if readiness is None:
        out["reason_code"] = "host_readiness_not_read"
        return out
    if host_pids is None or readiness.state in ("never_read", "reading_stale"):
        out["reason_code"] = "host_readiness_not_current"
        return out
    if readiness.state not in ("running_normal", "scan_mode"):
        out["reason_code"] = "host_not_running"
        return out
    if row["child_pid"] not in host_pids:
        out["reason_code"] = "host_pid_mismatch"
        return out
    out["supervised"] = True
    return out


def _capability_view(binding, current, retained, now_mono_ns, unreachable_reason=UNREACHABLE) -> dict:
    """The configured capability, decided by cake_activation's reading of ONE slot on ONE capsule (the demo_runtime
    re-implementation of autonomy/motion/cake_capability_adapter's rule). A retained reading never makes it active."""
    source, stale = _section_source(current, retained)
    out = {"configured": binding is not None, "name": getattr(binding, "name", None), "active": False,
           "reason_code": None, "capsule": None, "generation": None, "child_pid": None, "stale": stale,
           "observed": _observed(source, now_mono_ns)}
    if binding is None:
        out["reason_code"] = "capability_not_configured"
        return out
    if source is None:
        out["reason_code"] = unreachable_reason or UNREACHABLE
        return out
    detail = _detail(source)
    slot = next((row for row in detail.get("slots", []) if row["uuid"] == binding.slot_uuid.lower()), None)
    if slot is not None:
        out["capsule"], out["generation"] = slot["capsule"], slot["generation"]
        row = next((row for row in detail.get("health", []) if row["slot"] == slot["uuid"]), None)
        out["child_pid"] = row["child_pid"] if row else None
    if stale:
        out["reason_code"] = unreachable_reason or UNREACHABLE
    elif slot is None:
        out["reason_code"] = "capability_slot_absent" if detail.get("slots_reason") is None else "slot_listing_unavailable"
    elif cake_activation.capability_is_active(detail.get("slots_text", ""), slot_uuid=binding.slot_uuid,
                                              active_capsule=binding.active_capsule):
        out["active"] = True
    else:
        out["reason_code"] = "capability_slot_not_active" if slot["state"] != ACTIVE else "capability_capsule_not_active"
    return out


def _identity_view(robot, inventory, current, source, stale, reason_code, now_mono_ns) -> dict:
    """WHICH robot: the operator's public display label (or unlabelled), whether the resident answering NOW reports the
    build pinned for the robot (`is_robot`, a kept name: the build identity identifies software, and which deployment is
    observed is established by the connection this runtime owns to the intended robot and the session its resident
    reports now), and the robot resident's build and session identity. Never the robot id. The answering resident's
    target profile is carried as INFORMATION: it decides nothing."""
    if robot is None:
        return {"is_robot": None, "reason_code": "robot_not_configured", "expected_build_identity": None,
                "answering_build_identity": None, "answering_target_profile_digest": None, "display_label": None,
                "labelled": False, "build_identity": None, "session_uuid": None, "stale": False, "observed": None}
    if current is not None:
        answered = current.get("identity") or {}
        is_robot = True
        answering, profile = _hex_or_none(answered.get("build_identity")), _hex_or_none(answered.get("target_profile_digest"))
    elif reason_code in IDENTITY_REFUSALS:
        is_robot = False
        answering = _hex_or_none(inventory.get("answering_build_identity")) if isinstance(inventory, dict) else None
        profile = _hex_or_none(inventory.get("answering_target_profile_digest")) if isinstance(inventory, dict) else None
    else:
        is_robot, answering, profile = None, None, None
    identity = (source or {}).get("identity") or {}
    return {"is_robot": is_robot, "reason_code": None if is_robot else reason_code,
            "expected_build_identity": _hex_or_none(robot.expected_build_identity),
            "answering_build_identity": answering, "answering_target_profile_digest": profile,
            "display_label": robot.display_label, "labelled": robot.display_label is not None,
            "build_identity": _hex_or_none(identity.get("build_identity")), "session_uuid": _hex_or_none(identity.get("session_uuid")),
            "stale": stale, "observed": _observed(source, now_mono_ns)}


def robot_status_view(*, config, probe: dict, retained: Optional[dict], session_changed, forward_report: Optional[dict],
                      readiness=None, host_pids=None, permission: Optional[MotionPermission] = None,
                      now_mono_ns: Optional[int] = None, now_wall: Optional[str] = None) -> dict:
    """The `robot_status` surface. A projection of the SAME cached probe the cake surfaces read, plus the forward's own
    report, the readiness reading and this runtime's permission. It starts no child process of its own."""
    now_mono_ns = time.monotonic_ns() if now_mono_ns is None else now_mono_ns
    now_wall = _wall_now() if now_wall is None else now_wall
    now_observed = _now_observed(now_mono_ns, now_wall)
    robot = robot_of(config)
    cake_configured = cake_admin.configured_paths(config) != (None, None)
    deployment = DEPLOYMENT_ROBOT if robot is not None else DEPLOYMENT_LAPTOP if cake_configured else DEPLOYMENT_NONE
    inventory = probe.get("inventory") if isinstance(probe, dict) and robot is not None else None
    current = inventory if isinstance(inventory, dict) and inventory.get("reachable") else None
    retained = retained if robot is not None else None
    reachable = current is not None
    reason_code = (None if reachable else ((inventory or {}).get("reason_code") or UNREACHABLE)) if robot is not None else "robot_not_configured"
    source, stale = _section_source(current, retained)
    forward = None
    if robot is not None and isinstance(forward_report, dict):
        forward = {name: forward_report.get(name) for name in ("state", "reason", "since_wall_utc", "since_age_ms", "starts",
                                                               "deaths", "restarts", "next_attempt_in_ms")}
    identity, counts = (source or {}).get("identity") or {}, (source or {}).get("counts") or {}
    detail = _detail(source)
    updates = detail.get("updates") or parse_updates([])
    binding = getattr(getattr(config, "cake", None), "capability", None) if robot is not None else None
    if permission is None:
        motion = {"applies": False, "permitted": None, "reason_code": "robot_not_configured",
                  "detail": "no robot resident is configured, so no robot session gates motion here; the runtime's other checks still do",
                  "confirmable": False, "changed_component": None,
                  "confirmed_session_uuid": None, "confirmed_host_instance": None, "confirmed_wall_utc": None,
                  "current_session_uuid": None, "current_host_instance": None, "current_host_reason_code": "robot_not_configured",
                  "observed": now_observed}
    else:
        bound = binding_of(current if current is not None else inventory)
        refused = permission.refusal(bound)
        confirmed = permission.confirmed() or {}
        # WHAT A CONFIRMATION HAS TO NAME, from a CURRENT reading, over this runtime's connection to the robot, of a
        # resident reporting the pinned build, and only
        # while a host instance RUNS under the supervisor slot and all three of its components were read. Otherwise
        # nothing is offered: no pid 0, no held or DEGRADED host, no unidentified or retained resident.
        session = bound["session_uuid"] if current is not None else None
        instance = bound["host_instance"] if session is not None else None
        host_reason = None if instance is not None else (
            HOST_NOT_RUNNING if session is not None and bound["host_running"] is False else
            HOST_UNREADABLE if session is not None else bound["reason_code"] or UNREACHABLE)
        motion = {"applies": True, "permitted": refused is None, "reason_code": refused[0] if refused else None,
                  "detail": refused[1] if refused else "the robot's resident reports the session and host instance the operator confirmed",
                  "confirmable": session is not None and instance is not None,
                  "changed_component": permission.changed_component() if refused else None,
                  "confirmed_session_uuid": confirmed.get("session_uuid"), "confirmed_host_instance": confirmed.get("host_instance"),
                  "confirmed_wall_utc": confirmed.get("confirmed_wall_utc"),
                  "current_session_uuid": session, "current_host_instance": dict(instance) if instance else None,
                  "current_host_reason_code": host_reason, "observed": now_observed}
    return {
        "robot_status_version": ROBOT_STATUS_VERSION,
        "configured": robot is not None,
        "deployment": deployment,
        "reachable": reachable,
        "reason_code": reason_code,
        "stale": stale,
        "clock_domain": cake_admin.CLOCK_DOMAIN,
        "connection": {"deployment": deployment, "forward": forward, "observed": now_observed},
        "identity": _identity_view(robot, inventory, current, source, stale, reason_code, now_mono_ns),
        "resident": {
            "reachable": reachable, "stale": stale, "reason_code": reason_code,
            "session_uuid": _hex_or_none(identity.get("session_uuid")), "build_identity": _hex_or_none(identity.get("build_identity")),
            "plan_digest": _hex_or_none(identity.get("plan_digest")), "target_profile_digest": _hex_or_none(identity.get("target_profile_digest")),
            "epoch": _count(counts.get("epoch")),
            "session_changed": session_changed if isinstance(session_changed, bool) and robot is not None else None,
            "observed": _observed(source, now_mono_ns)},
        "slots": {"stale": stale, "reason_code": detail.get("slots_reason") if source else reason_code,
                  "items": list(detail.get("slots", [])), "observed": _observed(source, now_mono_ns)},
        "health": {"stale": stale, "reason_code": detail.get("health_reason") if source else reason_code,
                   "items": list(detail.get("health", [])), "observed": _observed(source, now_mono_ns)},
        "host": _host_view(current, readiness, host_pids, now_observed, reason_code) if robot is not None else {
            "supervised": False, "reason_code": "robot_not_configured", "slot_uuid": None, "slot_state": None, "slot_instance": None,
            "child_pid": None, "spawn_sequence": None, "health_state": None,
            "readiness_state": None, "readiness_age_ms": None, "observed": now_observed},
        "capability": _capability_view(binding, current, retained, now_mono_ns, reason_code) if robot is not None else {
            "configured": False, "name": None, "active": False, "reason_code": "robot_not_configured", "capsule": None,
            "generation": None, "child_pid": None, "stale": False, "observed": None},
        "updates": {"stale": stale, "reason_code": detail.get("updates_reason") if source else reason_code,
                    "items": list(updates["items"]), "dropped": updates["dropped"],
                    "excluded_admin_records": updates["excluded_admin_records"], "other_records": updates["other_records"],
                    "observed": _observed(source, now_mono_ns)},
        "motion_permission": motion,
    }
