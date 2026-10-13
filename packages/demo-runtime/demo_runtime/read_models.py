# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.read_models: what a client is allowed to see, computed on the server.

Everything here is a VIEW. Capabilities, components, the Cake ladder and the evidence counters are decided by the runtime
and sent as contract objects; a browser renders them and never recomputes them. The rules:

  - capabilities are server-side truth. A command capability is 'blocked' with a reason until the daemon was started with
    --enable-hardware-commands, and even then ownership and the preflight decide;
  - components say what KIND of thing each part is (a Python function inside the controller process, a process, a device).
    There are NO cake modules today, so none is listed;
  - the Cake ladder comes from real local probes (is a binary on disk, is a resident process running, does an admin socket
    exist) and each rung carries the evidence it rests on. mission_participation is read from the newest mission record
  (cake_admin.mission_participation) when this runtime has a mission surface, and is false otherwise;
  - an observation is served as an ALLOWLISTED projection: a field that is not named here is not sent. Never a path, an
    address or a camera identity fingerprint;
  - Sanitizer is the last line: it knows the private values of this machine's config and removes them from any text that is
    about to leave the process.
"""
from __future__ import annotations

import base64
import collections
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import cake_admin, sources
from .journal import CONTRACT_VERSION

import demo_contracts  # importable once .journal has been imported

COMMAND_CAPABILITIES = ("start_cycle", "observe", "return_arm", "scan_scene", "manual_pan", "manual_move", "manual_grip", "manual_base_pulse")
# The command types that MOVE the robot: every command capability except observe, which reads the robot and moves
# nothing. scan_scene (Scan scene: pan-only views from the low scan pose) is one of them, which is why it is a type of
# its own rather than a new meaning for observe. The robot resident's session gate (demo_runtime.robot_link.MotionPermission) refuses exactly these, and the
# supervisor reads this same tuple, so the capability a snapshot shows and the refusal a submit gets cannot disagree.
MOTION_CAPABILITIES = tuple(capability for capability in COMMAND_CAPABILITIES if capability != "observe")
REDACTION = "[redacted]"
_ID_SHAPE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}")

# Generic shapes that never belong in text for an operator's screen, whatever this machine's config says.
_GENERIC = (
    re.compile(r"(?<![\w.])(?:~|/)(?:[\w.+@-]+/)+[\w.+@-]*"),  # an absolute or home-relative path of two or more components
    re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])"),  # a dotted quad
    re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}"),  # a hardware address
    re.compile(r"[\w.+-]+@[\w.-]+"),  # user@host
)
_UNSCRUBBED_KEYS = frozenset({"bytes_b64"})  # base64 can spell anything by chance; it is image bytes, not text


class Sanitizer:
    """Removes this machine's private values from outgoing data. tokens come from the runtime config and from the
    daemon's own paths; longer tokens are replaced first so that a path is removed whole, not in pieces."""

    def __init__(self, tokens=()):
        unique = {token for token in tokens if isinstance(token, str) and len(token) >= 4}
        self._tokens = sorted(unique, key=len, reverse=True)

    @classmethod
    def for_runtime(cls, config=None, extra=()) -> "Sanitizer":
        tokens = [os.path.expanduser("~"), *extra]
        if config is not None:
            private = config.to_private_dict()
            tokens += [private["host"]["address"], private["host"]["robot_id"], private["pi_ssh_target"], *private["pi_ssh_target"].split("@"),
                       private["cameras"]["wrist_id"], private["cameras"]["front_id"] or "",
                       private["data_root"], private["saved_pose_file"], private["pad_template_dir"], private["observation_root"]]
            robot = (private.get("cake") or {}).get("robot") or {}
            tokens += [robot.get("remote_admin_socket_path") or "", robot.get("local_admin_socket_path") or ""]
        return cls(tokens)

    def text(self, value: str, *, generic: bool = False) -> str:
        for token in self._tokens:
            if token in value:
                value = value.replace(token, REDACTION)
        if generic:
            for shape in _GENERIC:
                value = shape.sub(REDACTION, value)
        return value

    def scrub(self, value, *, generic: bool = False):
        """(clean copy, changed). Keys are scrubbed as well: a badly built record can carry a value as a key."""
        changed = [False]

        def walk(node):
            if isinstance(node, str):
                clean = self.text(node, generic=generic)
                changed[0] = changed[0] or clean != node
                return clean
            if isinstance(node, list):
                return [walk(item) for item in node]
            if isinstance(node, dict):
                return {walk(key) if isinstance(key, str) else key: (item if key in _UNSCRUBBED_KEYS else walk(item)) for key, item in node.items()}
            return node

        return walk(value), changed[0]


# ---- session statistics (an EventLog listener) ------------------------------------------------------------------------------

class SessionStats:
    """Counters of this runtime session, fed by every appended event. observe() runs under the log's lock: arithmetic only.

    TWO SCOPES, KEPT APART AND NEVER MIXED. gaps, dropped_telemetry, captures, perception and transitions count the whole
    SESSION and are never reset, which is exactly what the snapshot's `evidence` object says it is. The run counters and
    the timing figures belong to ONE run and start again when a run starts, because a session counter answers a different
    question from "was THIS run recorded", and because a figure that pooled two runs would describe neither of them.

    WHY A RUN IS COUNTED AT ALL. A session that recorded nothing has no gap either, and a consumer that reads "no gaps"
    as "a complete record" would report a mission that never happened as one that went well. The runtime's own emitter
    already refuses that reading for an empty record (emit.NullEmitter.status), so the same reasoning is applied here:
    the run carries how many of its records were appended, and its completeness stays None until the run's own
    run.finished record states it."""

    def __init__(self):
        self.gaps = 0
        self.dropped_telemetry = 0
        self.captures: dict = {}  # role -> {"seq", "retained", "count", "unretained"}
        self.perception = {"count": 0, "errors": 0, "last_seq": None}
        self.transitions = 0
        self.run: Optional[dict] = None  # the NEWEST run only: {"run_id", "records", "gaps", "dropped_telemetry", "complete"}
        self.spans: dict = {}  # (span name, clock domain) -> one timing figure of that run
        # The newest retained observation that carries an image, held as the snapshot states it. It is ONE reference and
        # never a growing list, so a session of any length costs the same here as a session with one capture in it.
        self.latest_frame: Optional[dict] = None
        # The newest measured.state record, held the same way and for the same reason. A controller writes one when it
        # reads the robot, which a non-motion observe does as much as a cycle does, and a console that connected or
        # reloaded after the command folded none of them and would otherwise have nothing to show of a reading this
        # runtime is holding. It is ONE record and never a list.
        self.latest_measured: Optional[dict] = None
        self._lock = threading.Lock()

    def observe(self, event: dict) -> None:
        kind, payload = event.get("kind"), event.get("payload") or {}
        if kind == "evidence.gap":
            self.gaps += 1
        elif kind == "telemetry.dropped":
            self.dropped_telemetry += int(payload.get("count") or 0)
        elif kind == "observation.captured":
            entry = self.captures.setdefault(payload.get("source_role"), {"seq": None, "count": 0, "unretained": 0})
            entry["seq"], entry["count"] = event.get("seq"), entry["count"] + 1
            entry["unretained"] += 0 if payload.get("retained") else 1
            self._note_frame(event, payload)
        elif kind == "perception.result":
            self.perception["count"] += 1
            self.perception["errors"] += 1 if payload.get("result") == "error" else 0
            self.perception["last_seq"] = event.get("seq")
        elif kind == "state.transition":
            self.transitions += 1
        elif kind == "measured.state":
            self._note_measured_state(event, payload)
        self._fold_run(event, kind, payload)

    def _fold_run(self, event: dict, kind, payload: dict) -> None:
        """The counters and the figures of ONE run, under this object's own lock.

        The lock is here and not on the session counters because these two are the ones a reader ITERATES: the snapshot
        thread builds timings() while an append is adding a span to the same dict, and iterating a dict another thread is
        growing raises instead of answering. It is taken while the event log's lock is already held, and nothing under it
        ever reaches back for the log, so the two can only ever be taken in that order."""
        run_id = event.get("run_id")
        with self._lock:
            if kind == "run.started" and isinstance(run_id, str):
                # The newest run replaces the previous one. Only one is ever held, so a session of any length costs the same.
                self.run, self.spans = {"run_id": run_id, "records": 0, "gaps": 0, "dropped_telemetry": 0, "complete": None}, {}
            if self.run is None or run_id != self.run["run_id"]:
                return
            self.run["records"] += 1
            if kind == "evidence.gap":
                self.run["gaps"] += 1
            elif kind == "telemetry.dropped":
                self.run["dropped_telemetry"] += int(payload.get("count") or 0)
            elif kind == "run.finished":
                self.run["complete"] = bool(payload.get("evidence_complete"))
            elif kind == "timing.span":
                self._span(event, payload)

    def _span(self, event: dict, payload: dict) -> None:
        """One measured duration, kept per span name AND clock domain. A duration measured in another clock is another
        measurement and is never added to this one. The key set is bounded by the contract: the nine span names it
        allows, in the clock domains it names. Called with the lock held, for the run that is open."""
        duration = payload.get("duration_ns")
        if isinstance(duration, bool) or not isinstance(duration, int):
            return
        key, seq = (payload.get("span"), payload.get("clock_domain")), int(event.get("seq") or 0)
        entry = self.spans.get(key)
        if entry is None:
            entry = self.spans[key] = {"span": key[0], "clock_domain": key[1], "count": 0, "total_ns": 0, "max_ns": 0, "last_ns": 0, "first_seq": seq, "last_seq": seq}
        entry["count"] += 1
        entry["total_ns"] += duration
        entry["max_ns"] = max(entry["max_ns"], duration)
        entry["last_ns"], entry["last_seq"] = duration, seq

    def _note_frame(self, event: dict, payload: dict) -> None:
        """Remember the newest RETAINED capture that carries an image, exactly as the record states it.

        WHY A SNAPSHOT HAS TO CARRY THIS AT ALL. A console builds its list of observations by folding observation.captured
        records off the event stream. A tab that connects or reloads AFTER a capture folds none of them, so it holds no
        reference to any frame and can only report that nothing was captured, while the frame is on disk and the frame
        endpoint serves it. Every other durable fact of the session is already on the snapshot for that reason; this is
        the one the camera card needed and did not have.

        Only a RETAINED observation with an image is remembered, because the only use of this field is to name a picture
        somebody can fetch: an observation that was not kept has no frame to serve, and a scalar_set capture has no
        picture in it, so letting either displace the newest frame would put the card back to saying it has nothing.

        Nothing is guessed. A field the record did not state, or stated as a type the contract does not allow, is carried
        as null rather than repaired, and a record whose observation id is missing is not remembered at all: an entry
        that named no observation could not be fetched by anybody.

        The dict is built whole and then published by one assignment, and it is never mutated afterwards, so a snapshot
        thread that reads the attribute either sees the previous capture or this one and never a half-written one."""
        if not payload.get("retained") or payload.get("media") not in ("frame", "frame_pair"):
            return
        observation_id = event.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            return

        def whole(value):
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        def string(value):
            return value if isinstance(value, str) else None

        stamp = payload.get("source_stamp")
        entry = {
            "observation_id": observation_id,
            "source_role": payload.get("source_role"),
            "media": payload.get("media"),
            "width_px": whole(payload.get("width_px")),
            "height_px": whole(payload.get("height_px")),
            "content_digest": string(payload.get("content_digest")),
            "source_stamp": dict(stamp) if isinstance(stamp, dict) else None,
            "retained": True,
            "seq": whole(event.get("seq")) or 0,
            "t_wall_utc": string(event.get("t_wall_utc")),
        }
        with self._lock:
            self.latest_frame = entry

    def _note_measured_state(self, event: dict, payload: dict) -> None:
        """Remember the newest measured.state record, exactly as it was written.

        WHY THE SNAPSHOT HAS TO CARRY THIS AT ALL. This is the same hole the frame reference closed, in a second place.
        A console folds measured.state off the event stream, so a tab that connects or reloads after the command that
        produced it folds none of it and holds no measurement whatever. The screen that resulted said the host was not
        reporting measurements and named the absent telemetry tap as the reason, while a controller measurement of the
        whole pose sat in this session's log: two different facts, one of them printed in place of the other, and the
        measurement thrown away. A continuous tap is still genuinely absent and nothing here claims otherwise.

        Nothing is guessed and nothing is repaired. The three payload fields are carried only when the record states
        them in the shape the contract declares, and a record missing any of them is not remembered at all rather than
        remembered with a hole in it: a partial pose is not a pose anybody measured.

        The dict is built whole and published by one assignment under the lock, so a snapshot thread reading the
        attribute sees either the previous record or this one and never a half-written one."""
        joints, velocity, stamp = payload.get("joints"), payload.get("base_velocity"), payload.get("consumed_source_stamp")
        if not isinstance(joints, dict) or not isinstance(velocity, dict) or not isinstance(stamp, dict):
            return
        entry = {
            "joints": dict(joints),
            "base_velocity": dict(velocity),
            "consumed_source_stamp": dict(stamp),
            "seq": event.get("seq") if isinstance(event.get("seq"), int) and not isinstance(event.get("seq"), bool) else 0,
            # When the RUNTIME WROTE THE RECORD, carried unaltered. It is not the instant the servo bus was sampled,
            # and the consumer is told which of the two it is rather than left to assume the flattering one.
            "t_wall_utc": event.get("t_wall_utc") if isinstance(event.get("t_wall_utc"), str) else None,
        }
        with self._lock:
            self.latest_measured = entry

    def latest_measured_state(self) -> Optional[dict]:
        """The snapshot's `latest_measured_state`, or None when no measured.state record was appended in this session.
        None is the runtime saying no controller read the robot here, which is a different statement from a telemetry
        tap that is not configured, and the two are never printed as one."""
        with self._lock:
            return dict(self.latest_measured) if self.latest_measured is not None else None

    def latest_retained_observation(self) -> Optional[dict]:
        """The snapshot's `latest_retained_observation`, or None when this session has retained no frame yet. None is the
        runtime saying nothing was captured here, which is a different statement from a capture that could not be served
        and is the reason the console can stop calling both of them the same thing."""
        with self._lock:
            return dict(self.latest_frame) if self.latest_frame is not None else None

    def evidence(self) -> dict:
        return {"complete": self.gaps == 0 and self.dropped_telemetry == 0, "gaps": self.gaps, "dropped_telemetry": self.dropped_telemetry}

    def run_evidence(self) -> Optional[dict]:
        """The snapshot's `run_evidence`: what was recorded for the newest run, or None when no run has started here."""
        with self._lock:
            return dict(self.run) if self.run is not None else None

    def timings(self) -> dict:
        """The snapshot's `timings`. A span with no sample is absent rather than reported as zero, so an empty list says
        that nothing was measured yet and never that everything took no time."""
        with self._lock:
            return {"run_id": self.run["run_id"] if self.run is not None else None, "spans": [dict(entry) for _, entry in sorted(self.spans.items())]}


# ---- capabilities -----------------------------------------------------------------------------------------------------------

def _capability(capability_id: str, state: str, reason_code=None, reason=None, dependency=None, planned=False) -> dict:
    return {"id": capability_id, "state": state, "reason_code": reason_code, "reason": reason, "dependency": dependency, "planned": planned}


BLOCK_REASONS = {
    "replay_only": ("disabled", "this runtime serves recorded runs only: it has no command path", "runtime_mode"),
    "hardware_commands_not_enabled": ("blocked", "the runtime was started without --enable-hardware-commands", "runtime_flag"),
    "owner_unresolved": ("blocked", "a command was left unresolved by a runtime restart: an operator resolution is required first", "owner"),
    "owner_external": ("blocked", "a controller that this runtime did not start is running", "owner"),
    "run_active": ("blocked", "a run is active: only stop is accepted", "owner"),
    # The daemon's mission holds the same single run slot a controller run does (daemon.MISSION_HOLDS_RUN_SLOT). Named
    # here so the sentence says so, instead of the fallback below blaming a preflight that never ran.
    "mission_running": ("blocked", "a mission is being carried: only stop is accepted", "owner"),
    # A capability slot check (demo_runtime.capability_check) holds the run slot while its one ApplyPlan and its read-backs
    # are in flight, so nothing that could move the robot starts under it. It moves nothing itself.
    "capability_check_in_flight": ("blocked", "a capability slot check is in flight (no motion): only stop is accepted", "owner"),
}

# A command capability can also be off because of a fact about the ROBOT rather than anything on this laptop. The sentence
# always arrives with the block, from the reading that took it, so it is not written a second time here; what this table
# decides is the capability STATE each code implies and the dependency to name beside it.
#
# The four motor-host codes are things the robot computer SAID, so the capability is blocked and says which. The two
# readiness codes mean this runtime has no answer at all - nothing read yet, or a reading too old to stand for the robot
# now - which is 'unknown' and must not be drawn as ready either. The literals mirror demo_runtime.preflight, which owns
# them, and a test holds this table against that module so a code renamed there cannot quietly stop being recognized here.
ROBOT_BLOCK_STATES = {
    "motor_host_unreachable": "blocked",
    "motor_host_not_running": "blocked",
    "motor_host_in_scan_mode": "blocked",
    "motor_host_mode_unknown": "blocked",
    "robot_readiness_not_read": "unknown",
    "robot_readiness_stale": "unknown",
}


def capabilities(*, block_code: Optional[str], block_detail: Optional[str] = None, run_active: bool, replay_only: bool,
                 cake_connected: bool = False, motion_block: Optional[tuple] = None, mission_carried: bool = False) -> list:
    """block_code is the ONE reason every command capability shares right now (None: enabled). It is computed by the same
    arbitration a submit goes through, so a capability never promises what a submit would refuse.

    `cake_connected` says whether this runtime is reading a real resident. It changes only the SENTENCE beside the Cake
    update capability, never its state: once a resident is connected, "nothing connects this runtime to a resident"
    is false, and the true reason the operation is unavailable is that contract v1 has no command that could submit one.
    The capability stays disabled either way, because nothing was built that could carry it.

    `motion_block` is (reason_code, sentence) when the robot resident's session gate refuses motion right now. It
    applies to MOTION_CAPABILITIES only, and only when nothing shared blocks every command first: observe stays as the
    shared arbitration leaves it.

    `mission_carried` says a mission is being carried with no controller run. Stop is ENABLED then, because the
    runtime's one stop path ends that mission: a Stop drawn as "no run to stop" while a mission moves on would be a
    control that says nothing can be stopped when something can."""
    out = []
    for capability_id in COMMAND_CAPABILITIES:
        if block_code is None and motion_block is not None and capability_id in MOTION_CAPABILITIES:
            out.append(_capability(capability_id, "blocked", motion_block[0], motion_block[1], "robot"))
        elif block_code is None:
            out.append(_capability(capability_id, "enabled"))
        elif block_code in ROBOT_BLOCK_STATES:
            out.append(_capability(capability_id, ROBOT_BLOCK_STATES[block_code], block_code, block_detail, "robot"))
        else:
            state, reason, dependency = BLOCK_REASONS.get(block_code, ("blocked", block_detail or "the preflight did not pass", "preflight"))
            out.append(_capability(capability_id, state, block_code, reason, dependency))
    if replay_only:
        out.append(_capability("stop", "disabled", "replay_only", BLOCK_REASONS["replay_only"][1], "runtime_mode"))
    elif run_active or mission_carried:
        out.append(_capability("stop", "enabled"))
    else:
        out.append(_capability("stop", "disabled", "no_active_run", "there is no run to stop", "owner"))
    out.append(_capability(
        "cake_update", "disabled", "not_built",
        "this runtime reads a resident read-only; no command of this contract can submit an update to one" if cake_connected
        else "nothing connects this runtime to a resident",
        "cake_resident", planned=True))
    return out


# ---- components -------------------------------------------------------------------------------------------------------------

def _component(component_id: str, layer: str, role: str, health: str, active, evidence: str, version=None) -> dict:
    return {"id": component_id, "layer": layer, "role": role, "health": health, "active": active, "version": version, "evidence": evidence}


def components(*, stats: SessionStats, run_active: bool, last_exit: Optional[dict], replay_only: bool, runtime_version: str) -> list:
    """Logical Python components, processes and devices, kept apart. No cake_module is listed: there is none today."""
    out = [_component("runtime-daemon", "process", "owns the active run, command arbitration, stop and the event log", "ok", True, "this process answered the request", runtime_version)]
    if replay_only:
        controller = _component("cycle-controller", "process", "the single-cube cycle controller, one process per command", "unknown", False, "replay-only runtime: no controller is ever started")
    elif run_active:
        controller = _component("cycle-controller", "process", "the single-cube cycle controller, one process per command", "ok", True, "its process identity is recorded and the process is alive")
    elif last_exit is None:
        controller = _component("cycle-controller", "process", "the single-cube cycle controller, one process per command", "unknown", False, "no controller was started in this runtime session")
    else:
        status = last_exit.get("exit_status")
        health = "ok" if status == 0 else "unknown" if status is None else "degraded" if status in (3, 4) else "failed"
        shown = "unavailable (the process was adopted, not started, by this runtime)" if status is None else str(status)
        controller = _component("cycle-controller", "process", "the single-cube cycle controller, one process per command", health, False, f"the last controller process ended; exit status {shown}")
    out.append(controller)
    out.append(_component("robot-host", "process", "the hardware host process on the robot", "unknown", None, "the runtime never contacts the robot host and no telemetry source is attached"))
    transitions = stats.transitions
    out.append(_component("cycle-state-machine", "logical_python", "sequences the cycle inside the controller process", "ok" if transitions else "unknown", run_active if transitions else None,
                          f"{transitions} state transition(s) recorded in this session" if transitions else "no state transition was recorded in this session"))
    perception = stats.perception
    perception_health = "unknown" if not perception["count"] else "degraded" if perception["errors"] else "ok"
    out.append(_component("perception", "logical_python", "pad and cube perception inside the controller process", perception_health, None,
                          f"{perception['count']} perception result(s) in this session, {perception['errors']} of them an error" if perception["count"] else "no perception result was recorded in this session"))
    for role in ("wrist", "front"):
        capture = stats.captures.get(role)
        if capture is None:
            out.append(_component(f"{role}-camera", "device", f"the {role} camera", "unknown", None, "no capture from this camera was recorded in this session"))
        else:
            out.append(_component(f"{role}-camera", "device", f"the {role} camera", "degraded" if capture["unretained"] else "ok", None,
                                  f"{capture['count']} capture(s) recorded in this session, {capture['unretained']} not retained; the last at seq {capture['seq']}"))
    return out


# ---- the Cake ladder, from local probes -------------------------------------------------------------------------------------

def probe_cake(source_root, *, proc_root: str = "/proc", runtime_dirs=None, inventory=None) -> dict:
    """Three local facts. Reads directory listings and /proc/<pid>/comm only; opens no socket and starts nothing."""
    artifacts = False
    binaries = Path(source_root) / "release"
    try:
        artifacts = any(os.access(candidate, os.X_OK) for candidate in binaries.glob("*/bin/cake-resident"))
    except OSError:
        artifacts = None
    resident = False
    try:
        for entry in os.listdir(proc_root):
            if entry.isdigit():
                try:
                    with open(os.path.join(proc_root, entry, "comm"), encoding="utf-8", errors="replace") as handle:
                        if handle.read().strip() == "cake-resident":
                            resident = True
                            break
                except OSError:
                    continue
    except OSError:
        resident = None
    if runtime_dirs is None:
        base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(tempfile_root(), f"kiwi-cake-demo-{os.getuid()}")
        runtime_dirs = [os.path.join(base, "kiwi-cake-demo")]
    admin_socket = False
    for directory in runtime_dirs:
        try:
            admin_socket = admin_socket or any(name.startswith("admin-") and name.endswith(".sock") for name in os.listdir(directory))
        except OSError:
            continue
    # THIS LAPTOP, said in the answer: every fact above is about the machine this runtime runs on. A runtime configured
    # with the robot's resident never calls this function (demo_runtime.robot_link.probe_robot answers instead).
    out = {"scope": "this_laptop", "artifacts": artifacts, "resident": resident, "admin_socket": admin_socket}
    # A REAL ANSWER BEATS A LOCAL GUESS. The three probes above are things this machine can see about itself: a
    # binary among extracted release files, a process name in a process table, a socket file. When the runtime is
    # configured with an admin-probe path and a socket, a resident is ASKED instead, and what it says replaces the
    # inference. `inventory` is that answer, read by demo_runtime.cake_admin, and it is read-only.
    if isinstance(inventory, dict) and inventory.get("reachable"):
        out["resident"] = True
        out["admin_socket"] = True
        out["inventory"] = inventory
    elif isinstance(inventory, dict):
        out["inventory"] = inventory
    return out


def tempfile_root() -> str:
    import tempfile

    return tempfile.gettempdir()


def _resident_evidence(inventory: dict, scope: str = "this_laptop") -> str:
    """What the resident itself answered, in its own numbers. Digests are quoted short: the full value is the
    resident's and is not this console's to restate in a sentence. WHOSE resident it was is said first."""
    identity = inventory.get("identity") or {}
    build = str(identity.get("build_identity") or "")
    session = str(identity.get("session_uuid") or "")
    whose = ("the robot's resident answered this runtime's read-only get-status through the ssh-forwarded admin socket"
             if scope == "robot_resident" else "a resident on this laptop answered this runtime's read-only get-status over its admin socket")
    return (whose
            + (f"; build {build[:16]}" if build else "")
            + (f", session {session[:16]}" if session else ""))


def _slots_evidence(inventory: dict) -> str:
    """ZERO SLOTS IS AN ANSWER. It is what a resident with no module installed reports, and it is not the same fact
    as not having been able to read one."""
    counts = inventory.get("counts") or {}
    if "slots" not in counts:
        return "the resident answered, but its status carried no slot count"
    slots = counts["slots"]
    if slots == 0:
        return ("the resident reports 0 slots, which is its own answer and not a failed read: nothing is installed "
                "in it, so no module of it is active")
    return f"the resident reports {slots} slot(s); this runtime reads their identities from no supported field"


NO_CAKE_ADAPTER = "the controller has no Cake adapter"


def _participation_rung(participation) -> dict:
    """The mission_participation rung: cake_admin.mission_participation's value and evidence when this runtime read a
    mission record, else the sentence it has always said."""
    if isinstance(participation, dict) and isinstance(participation.get("evidence"), str) and participation["evidence"]:
        value = participation.get("value")
        return {"value": value if isinstance(value, bool) else None, "evidence": participation["evidence"]}
    return {"value": False, "evidence": NO_CAKE_ADAPTER}


def cake_view(probe: dict, *, participation: Optional[dict] = None) -> dict:
    """The contract 'cake' object. synthetic is true by the contract's own definition: nothing here was READ FROM a resident.
    The rungs are still real: each states the local probe it rests on, and a rung nothing checked is null, not false.
    `participation` is cake_admin.mission_participation of the newest mission view, or None without a mission surface."""
    def rung(value, evidence):
        return {"value": value, "evidence": evidence}

    artifacts, resident = probe.get("artifacts"), probe.get("resident")
    inventory = probe.get("inventory") if isinstance(probe.get("inventory"), dict) else {}
    if probe.get("scope") == "robot_resident":
        return _robot_cake_view(inventory, participation)
    ladder = {
        "synthetic": True,
        "artifacts_available": rung(artifacts, "probed on this laptop: an executable cake-resident binary is present among the checkout's extracted binaries" if artifacts
                                    else "probed on this laptop: the extracted binaries could not be listed" if artifacts is None
                                    else "probed on this laptop: no cake-resident binary is present among the checkout's extracted binaries"),
        "resident_reachable": rung(resident, _resident_evidence(inventory) if inventory.get("reachable")
                                   else "probed on this laptop: a process named cake-resident is running on this laptop; the runtime did not speak to it" if resident
                                   else "probed on this laptop: the process table could not be read" if resident is None
                                   else "probed on this laptop: the process table holds no process named cake-resident"),
        "admin_interface_compatible": rung(
            True if inventory.get("reachable") else None,
            "a resident on this laptop answered a read-only get-status over its admin socket" if inventory.get("reachable")
            else "probed on this laptop: an admin socket exists; this runtime has no admin client, so compatibility was not checked" if probe.get("admin_socket")
            else "probed on this laptop: no admin socket was found, so there was nothing to check"),
        "modules_active": rung(
            (inventory["counts"].get("slots", 0) > 0) if inventory.get("reachable") and isinstance(inventory.get("counts"), dict) and "slots" in inventory["counts"] else None,
            _slots_evidence(inventory)) if inventory.get("reachable")
        else rung(None, "a resident is running and this runtime reads no slot from it") if resident
        else rung(False, "no resident is running and no component of this system is a Cake module"),
        "mission_participation": _participation_rung(participation),
    }
    # `synthetic` is the contract's own word for "nothing here was READ FROM a resident". Once one has answered, it
    # is false, and the slot count is the resident's own: zero slots is a real answer from a real resident, not a
    # failure to read. The slot LIST stays empty because get-status reports a count and not a per-slot inventory, and
    # inventing entries to fill a list is exactly what this object exists not to do.
    # CONTRACT V1 IS NOT WIDENED FOR THIS. `cake.schema.json` sets additionalProperties false, so the resident's
    # identity and counts travel inside the ladder rungs' own evidence text, which is free-form and already the place
    # each rung states what it rests on. A new key here would have been a contract change, and a read-only milestone
    # is not a reason to make one.
    return {"synthetic": not bool(inventory.get("reachable")), "ladder": ladder, "slots": [], "transactions": [],
            "update_preview": None, "continuity": None}


def _robot_cake_view(inventory: dict, participation: Optional[dict] = None) -> dict:
    """The contract 'cake' object when the resident is the ROBOT's. Every rung says it is about the robot, and none of
    them rests on anything this laptop holds: its release directory, its process table and its sockets are not the
    robot's and are not consulted. A robot that cannot be reached is null on every rung that needs an answer."""
    def rung(value, evidence):
        return {"value": value, "evidence": evidence}

    reachable = bool(inventory.get("reachable"))
    reason = inventory.get("reason_code") or "robot_unreachable"
    unreachable = f"the robot's resident did not answer this runtime ({reason}); nothing on this laptop is consulted in its place"
    counts = inventory.get("counts") if isinstance(inventory.get("counts"), dict) else {}
    ladder = {
        "synthetic": True,
        "artifacts_available": rung(None, "not probed: the resident runs on the robot, and this laptop's extracted binaries say nothing about what the robot runs"),
        "resident_reachable": rung(reachable, _resident_evidence(inventory, "robot_resident") if reachable else unreachable),
        "admin_interface_compatible": rung(True if reachable else None,
                                           "the robot's resident answered a read-only get-status through the forwarded admin socket" if reachable else unreachable),
        "modules_active": rung((counts.get("slots", 0) > 0) if reachable and "slots" in counts else None,
                               _slots_evidence(inventory) if reachable else unreachable),
        "mission_participation": _participation_rung(participation),
    }
    return {"synthetic": not reachable, "ladder": ladder, "slots": [], "transactions": [], "update_preview": None, "continuity": None}


# ---- the Cake resident status, a surface versioned apart from contract v1 ----------------------------------------------------

CAKE_STATUS_IDENTITY_FIELDS = ("session_uuid", "build_identity", "config_identity", "target_profile_digest", "plan_digest")
CAKE_STATUS_COUNT_FIELDS = ("epoch", "slots", "bindings", "resources", "replay", "snapshot_present")
# A digest is passed through only when it looks like one. The resident prints hex; anything else is somebody else's
# string and is reported as absent rather than forwarded into a typed field.
_HEX_DIGEST = re.compile(r"^[0-9a-f]{12,64}$")
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _digest_or_none(value):
    return value if isinstance(value, str) and _HEX_DIGEST.match(value) else None


def _count_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _observation_view(inventory: dict, now_mono_ns: int) -> Optional[dict]:
    """One successful read, with the time IT was taken and how long ago that was on the same clock.

    The age is the only thing computed here, and it is computed from the stamp the read carries rather than from when
    this projection ran: that is what makes a retained observation legible as retained instead of as a fresh one.
    """
    if not isinstance(inventory, dict) or not inventory.get("reachable"):
        return None
    stamp = inventory.get("observed_mono_ns")
    wall = inventory.get("observed_wall_utc")
    if not isinstance(stamp, int) or not isinstance(wall, str):
        return None
    identity, counts = inventory.get("identity") or {}, inventory.get("counts") or {}
    flight = inventory.get("flight")
    transaction = inventory.get("transaction_state")
    return {
        "wall_utc": wall,
        "mono_ns": stamp,
        # Monotonic, so it cannot go backwards; clamped at zero anyway, because a negative age is not a fact about
        # anything and the schema refuses one.
        "age_ms": max(0.0, round((now_mono_ns - stamp) / 1e6, 3)),
        "resident": {name: _digest_or_none(identity.get(name)) for name in CAKE_STATUS_IDENTITY_FIELDS},
        "counts": {name: _count_or_none(counts.get(name)) for name in CAKE_STATUS_COUNT_FIELDS},
        "transaction_state": transaction if isinstance(transaction, str) and _TOKEN.match(transaction) else None,
        "flight": None if not isinstance(flight, dict) else {
            "records": _count_or_none(flight.get("records")),
            "oldest": _count_or_none(flight.get("oldest")),
            "next": _count_or_none(flight.get("next")),
        },
    }


def _activity_finding(reachable: bool, observation: Optional[dict], stale: bool) -> dict:
    """WHETHER ANYTHING IS RUNNING IN THE RESIDENT, which get-status does not answer.

    It reports counts. Zero slots supports "nothing is installed in it"; a positive count on its own does not establish
    that anything is executing, and this runtime reads no per-slot lifecycle state from any supported field. The flight
    recorder is not a way around that: THIS runtime's own read-only probe writes a record every time it connects, so a
    flight sequence that advances between two polls is evidence of the polling and of nothing else.
    """
    if observation is None:
        return {"value": None, "evidence": "no resident has answered this runtime, so nothing was established about what runs in one"}
    slots = observation["counts"].get("slots")
    when = "the resident's last answer before contact was lost" if stale else "the resident's answer"
    if slots == 0:
        return {"value": False, "evidence": f"{when} reports 0 slots, which is its own answer and not a failed read: nothing is installed "
                                            "in it, so no module of it is running"}
    if isinstance(slots, int):
        return {"value": None, "evidence": f"{when} reports {slots} slot(s) and no execution state for them; a slot count is not execution "
                                           "status, and the flight recorder only shows this runtime's own read-only connections"}
    return {"value": None, "evidence": f"{when} carried no slot count, so nothing was established about what runs in it"}


def cake_status_view(probe: dict, *, now_mono_ns: int, participation: Optional[dict] = None) -> dict:
    """The `cake_status` surface: what a real resident answered, structured, so a console never parses evidence prose.

    It is built from the SAME cached probe result the contract's cake ladder is built from. There is no second poller
    and no second child process: this is a projection of a value that was already read.

    RETAINED IS NOT CURRENT AND NOT EMPTY. `observation` is always the most recent read that SUCCEEDED. When the
    resident stops answering, that observation stays and `stale` turns true, because a resident that went away did not
    become a resident holding nothing; and when none has ever answered, `observation` is null, because an inventory of
    zeroes is a claim this runtime has no reading for.
    """
    configured = bool(probe.get("cake_configured"))
    inventory = probe.get("inventory") if isinstance(probe.get("inventory"), dict) else None
    retained = probe.get("cake_retained") if isinstance(probe.get("cake_retained"), dict) else None
    reachable = bool(inventory and inventory.get("reachable"))
    source = inventory if reachable else retained
    observation = _observation_view(source, now_mono_ns) if source is not None else None
    stale = observation is not None and not reachable
    if not configured:
        reason_code = "not_configured"
    elif reachable:
        reason_code = None
    else:
        reason_code = (inventory or {}).get("reason_code") or "admin_answer_not_ok"
    return {
        "cake_status_version": demo_contracts.CAKE_STATUS_VERSION,
        "configured": configured,
        "reachable": reachable,
        "reason_code": reason_code,
        "stale": stale,
        # Decided by the daemon across consecutive answers, never inferred here from reachability: a resident that was
        # briefly unreachable and came back as the SAME session did not restart, and one this runtime has only seen
        # once is a resident it cannot say either way about.
        "session_changed": probe.get("cake_session_changed") if isinstance(probe.get("cake_session_changed"), bool) else None,
        "clock_domain": cake_admin.CLOCK_DOMAIN,
        "observation": observation,
        "activity": _activity_finding(reachable, observation, stale),
        # NOT derived and not derivable from an admin socket. It is the third of the three facts this surface keeps
        # apart: read from the newest mission record when this runtime has a mission surface, else false.
        "mission_participation": _participation_rung(participation) if participation is not None else {
            "value": False, "evidence": "the controller has no Cake adapter, so no module of any resident is carrying any part of this robot's task"},
    }


class CakeRetention:
    """What this runtime still knows about a resident that has stopped answering, and whether the one answering now is
    the one it was answering before.

    IT FOLDS ON EVERY PROBE REFRESH, not when a client asks. A resident that was reachable at one poll and gone at the
    next must leave its last reading behind whoever happened to trigger the polls; folding in a request handler would
    retain only what a browser was lucky enough to ask across.

    `session_changed` is STICKY once it is true. A browser polls every couple of seconds and a restart is instantaneous:
    a flag that were true only for the single poll that spanned the restart would be missed almost every time, and the
    fact it reports is durable anyway. It says this runtime is no longer talking to the resident session it started with.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_good = None
        self._session = None
        self._reachable_readings = 0
        self._changed = False

    def fold(self, inventory) -> None:
        if not isinstance(inventory, dict) or not inventory.get("reachable"):
            return  # a failed read retains nothing and REPLACES nothing: that is what keeps a lost resident from reading as an empty one
        session = (inventory.get("identity") or {}).get("session_uuid")
        with self._lock:
            self._last_good = inventory
            self._reachable_readings += 1
            if isinstance(session, str) and session:
                if self._session is not None and session != self._session:
                    self._changed = True
                self._session = session

    def note_session(self, session) -> None:
        """A session seen by a ONE-QUESTION read (the fresh get-status a robot session confirmation or a motion gate
        takes). It moves the session tracking exactly as a full reading does and retains NOTHING: last_good stays the
        last full reading, because a single answer is not an inventory and must not replace one."""
        if not isinstance(session, str) or not session:
            return
        with self._lock:
            self._reachable_readings += 1
            if self._session is not None and session != self._session:
                self._changed = True
            self._session = session

    def last_good(self):
        with self._lock:
            return self._last_good

    def session_changed(self):
        """True once a different session has answered, False while every answer named the same one, None before this
        runtime has had two answers to compare. Null is not a no."""
        with self._lock:
            if self._changed:
                return True
            return False if self._reachable_readings >= 2 else None


class CachedProbe:
    """A probe result kept for a few seconds: a snapshot is asked for often and the process table does not need rereading each time."""

    def __init__(self, probe: Callable[[], dict], ttl_s: float = 5.0):
        self._probe, self._ttl, self._lock = probe, ttl_s, threading.Lock()
        self._value, self._taken = None, 0.0

    def get(self):
        with self._lock:
            if self._value is None or time.monotonic() - self._taken > self._ttl:
                self._value, self._taken = self._probe(), time.monotonic()
            return self._value

    def invalidate(self) -> None:
        with self._lock:
            self._value = None


# ---- the host command lease -------------------------------------------------------------------------------------------------

LEASE_VIEW_KEYS = ("held", "generation", "host_session", "expires_in_ms", "reason", "required", "attached", "renew_failures",
                   "run_id", "adopted", "last_reply_age_ms")


def lease_view(report: dict) -> dict:
    """The snapshot's additive 'lease' key and the same object in `status`, projected from what the supervisor reports.

    An ALLOWLIST, like every other view here: a field the runtime grows later does not reach a browser by accident. What
    it does carry is deliberate. held is what the DAEMON holds, never what its lease client last believed; the generation
    and the host session are not secrets (the session travels in every observation and the console needs both to explain
    a refused command); and the reason is the machine-readable code for why no lease is held. No endpoint, no port, no
    ssh target and no owner label: none of those is the browser's business, and two of them name a machine."""
    projected = {key: report.get(key) for key in LEASE_VIEW_KEYS}
    projected["held"] = bool(projected["held"])
    if not projected["held"]:  # nothing measured about a lease that is not held is ever shown beside it
        projected.update({"generation": None, "host_session": None, "expires_in_ms": None, "run_id": None, "adopted": None})
    return projected


# ---- the execution backend and what owns the robot under it -----------------------------------------------------------------

EXECUTION_VIEW_KEYS = ("backend", "ownership_basis", "robot_side_enforcement", "detail")


def execution_view(report: dict) -> dict:
    """The snapshot's additive 'execution' key and the same object in `status`, projected from what the supervisor
    reports. An ALLOWLIST like every other view here, and the four fields answer two different questions on purpose:
    WHICH path to the robot a command takes (backend), and WHAT is actually stopping anybody else from commanding it
    (ownership_basis, robot_side_enforcement, and the plain sentence beside them). The second one is the one that is
    easy to overstate, so robot_side_enforcement is carried as its own boolean and is false, not null, when this runtime
    knows that nothing on the robot is refusing anybody. No endpoint, no target, no generation: a lease that IS held is
    the lease view's business."""
    return {key: report.get(key) for key in EXECUTION_VIEW_KEYS}


def build_snapshot(*, runtime_session: str, seq_cursor: int, retained_from_seq: int, data_mode: str, link: dict, owner: dict, active_run, last_outcome,
                   capability_list: list, component_list: list, cake: dict, evidence: dict, timings=None, run_evidence=None,
                   latest_retained_observation=None, latest_measured_state=None) -> dict:
    """The ONE snapshot assembler. timings, run_evidence, latest_retained_observation and latest_measured_state are
    OPTIONAL properties of the contract object, so a snapshot recorded by a runtime that had none of them stays valid;
    this runtime always states all four, which is why a client that reads a null timings knows it is talking to a producer
    that aggregates none rather than to a run that measured nothing. They default to null for exactly that reason: a
    caller that has no aggregate to give says so, and never omits it.

    latest_retained_observation is a REFERENCE to the newest frame this session retained and never its bytes. It is here
    because a console folds its observations off the event stream, so a tab opened or reloaded after a capture has folded
    none and would otherwise have to say nothing was captured while the frame sits on disk and the API serves it.

    latest_measured_state is the newest measured.state record of this session and closes the same hole for the pose the
    controller read. It is not a telemetry tap and it names nothing continuous: a tap that is not configured and a
    controller measurement that was recorded are two different facts, and a consumer that prints the first in place of
    the second throws away a reading this runtime is holding."""
    return {"contract_version": CONTRACT_VERSION, "runtime_session": runtime_session, "seq_cursor": seq_cursor, "retained_from_seq": retained_from_seq,
            "data_mode": data_mode, "link": link, "owner": owner, "active_run": active_run, "last_outcome": last_outcome,
            "capabilities": capability_list, "components": component_list, "cake": cake, "evidence": evidence,
            "timings": timings, "run_evidence": run_evidence, "latest_retained_observation": latest_retained_observation,
            "latest_measured_state": latest_measured_state}


# ---- observations: allowlisted projections ----------------------------------------------------------------------------------

RECORD_ALLOW = {
    "observation_id": True, "schema_version": True, "timestamp_utc": True, "gripper_state": True, "cube_held": True,
    "camera": {"role": True, "image_orientation": True, "width_px": True, "height_px": True},  # never identity_fingerprint
    "robot_state": {"source": True, "measured_joints_deg": True, "measured_joints_post_capture_deg": True, "joint_stability_max_delta_deg": True, "base_velocity": True},
    "camera_pose": {"status": True, "frame": True, "reason": True, "missing_calibration": True},
    "scan": {"pose_id": True, "band": True, "sector_index": True, "nominal_tool_depression_deg": True, "tool_azimuth_deg": True, "frame": True},  # never scan_id
}
PERCEPTION_ALLOW = {
    "camera": True, "pad_note": True,
    "pad": {"detected": True, "confidence": True, "visible_fraction": True, "clipped_sides": True, "orientation_resolved": True, "boundary_rms_px": True,
            "camera_height_m": True, "view_elevation_deg": True, "corners_px": True},
    "pad_by_template": {"pad_identity": True, "landmark_inliers": True, "pad_frame_valid": True, "reason": True},
    "cube": {"detected": True, "confidence": True, "clipped": True, "box_px": True, "contact_px": True},
    "cube_by_template": {"cube_center_pad_m": True, "inside_pad_outline": True},
}
_MAX_TEXT, _MAX_ITEMS, _MAX_DEPTH = 256, 64, 4


def _plain(value, depth: int = 0):
    """Numbers, booleans, null and short strings, in lists and objects of bounded size and depth. Anything else is dropped."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return value[:_MAX_TEXT]
    if depth >= _MAX_DEPTH:
        return None
    if isinstance(value, list):
        return [_plain(item, depth + 1) for item in value[:_MAX_ITEMS]]
    if isinstance(value, dict):
        return {str(key)[:64]: _plain(item, depth + 1) for key, item in list(value.items())[:_MAX_ITEMS]}
    return None


def project(record, allow: dict) -> dict:
    out = {}
    if not isinstance(record, dict):
        return out
    for key, rule in allow.items():
        if key not in record:
            continue
        if rule is True:
            out[key] = _plain(record[key])
        elif isinstance(record[key], dict):
            out[key] = project(record[key], rule)
        else:
            out[key] = None
    return out


def safe_observation_id(observation_id) -> bool:
    return isinstance(observation_id, str) and bool(_ID_SHAPE.fullmatch(observation_id)) and ".." not in observation_id


class StoreObservationSource:
    """Reads the controller's observation store: <root>/<session>/<id>.json, frames/<id>.png, perception/<id>.json and
    perception/<id>-overlay.png. sessions() names the store sessions this runtime started (the scan ids of its runs); a
    session it did not start is never served. Files are only ever read."""

    def __init__(self, observation_root, sessions: Callable[[], list]):
        self._root, self._sessions = Path(observation_root), sessions

    def _locate(self, observation_id: str, *relative) -> Optional[Path]:
        if not safe_observation_id(observation_id):
            return None
        root = self._root.resolve()
        for session in self._sessions():
            if not safe_observation_id(session):
                continue
            candidate = (root / session).joinpath(*relative)
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if root in resolved.parents and resolved.is_file():
                return resolved
        return None

    def _json(self, path: Optional[Path]):
        import json

        if path is None:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def record(self, observation_id: str):
        return self._json(self._locate(observation_id, f"{observation_id}.json"))

    def perception(self, observation_id: str):
        return self._json(self._locate(observation_id, "perception", f"{observation_id}.json"))

    def image(self, observation_id: str, what: str):
        import cv2

        path = self._locate(observation_id, "frames", f"{observation_id}.png") if what == "raw" else self._locate(observation_id, "perception", f"{observation_id}-overlay.png")
        return None if path is None else cv2.imread(str(path))

    def frame_bytes(self, observation_id: str) -> Optional[dict]:
        """The RECORDED frame as it lies on disk, for an export: read whole, never decoded and never re-encoded, so what
        an export carries is the picture the decision was taken on rather than a rendition of it. None when this store
        has no frame for that id, which an export reports as a stated gap."""
        path = self._locate(observation_id, "frames", f"{observation_id}.png")
        if path is None:
            return None
        try:
            return {"content_type": "image/png", "bytes_b64": base64.b64encode(path.read_bytes()).decode("ascii")}
        except OSError:
            return None


class FrameService:
    """JPEG renditions on demand, cached in memory. The stored frame is the decision evidence: it is read, never rewritten,
    and a rendition is never written next to it."""

    def __init__(self, source, cache_size: int = 64):
        self._source, self._cache, self._size, self._lock = source, collections.OrderedDict(), cache_size, threading.Lock()

    def get(self, observation_id: str, what: str, max_width: int) -> Optional[dict]:
        import cv2

        key = (observation_id, what, max_width)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        image = self._source.image(observation_id, what)
        if image is None:
            return None
        height, width = image.shape[:2]
        if width > max_width:
            image = cv2.resize(image, (max_width, max(1, round(height * max_width / width))), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        result = {"content_type": "image/jpeg", "bytes_b64": base64.b64encode(encoded.tobytes()).decode("ascii"), "width_px": int(image.shape[1]), "height_px": int(image.shape[0])}
        with self._lock:
            self._cache[key] = result
            while len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return result


# ---- telemetry: replaceable, latest-only ------------------------------------------------------------------------------------

class TelemetryHub:
    """One slot per stream ('tap', 'preview'). publish() replaces the slot and returns at once; a reader asks for whatever
    is newer than what it last saw. Nothing is queued, so a slow reader costs nothing and simply misses the values in between."""

    def __init__(self):
        self._condition = threading.Condition()
        self._slots: dict = {}
        self._versions: dict = {}
        self._received: dict = {}
        self.readers = 0

    def add_reader(self, delta: int) -> None:
        with self._condition:
            self.readers += delta

    def publish(self, stream: str, record: dict) -> None:
        with self._condition:
            self._slots[stream] = record
            self._versions[stream] = self._versions.get(stream, 0) + 1
            self._received[stream] = time.monotonic()
            self._condition.notify_all()

    def newer_than(self, seen: dict, timeout: float) -> dict:
        """{stream: record} for every stream whose version moved past seen[stream]; seen is updated. Empty after timeout."""
        with self._condition:
            if not any(version > seen.get(stream, 0) for stream, version in self._versions.items()):
                self._condition.wait(timeout)
            fresh = {stream: self._slots[stream] for stream, version in self._versions.items() if version > seen.get(stream, 0)}
            seen.update({stream: self._versions[stream] for stream in fresh})
            return fresh

    def latest(self, stream: str) -> tuple:
        """(record, age in ms) or (None, None)."""
        with self._condition:
            if stream not in self._slots:
                return None, None
            return self._slots[stream], (time.monotonic() - self._received[stream]) * 1000.0

    def link(self) -> dict:
        record, age_ms = self.latest("tap")
        if record is None or not record.get("source_stamp"):
            return {"state": "unknown", "source_stamp": None, "receive_age_ms": None}
        return {"state": "connected" if age_ms < 2000.0 else "disconnected", "source_stamp": record["source_stamp"], "receive_age_ms": round(age_ms, 1)}


# The shapes a client sees when nothing is attached come from demo_runtime.sources, so an unattached slot and a slot with a
# real source behind it have ONE shape and the browser reads one thing. Neither ever carries a measured number.
UNAVAILABLE_TAP = sources.unavailable_tap()
UNAVAILABLE_PREVIEW = sources.unavailable_preview()


class SyntheticTelemetry(threading.Thread):
    """A synthetic tap for --synthetic: a slow sine on the pan joint, a base at rest. It runs only while somebody reads.
    It publishes the SAME record shape a real tap source publishes, with its own clock domain named: the values are this
    laptop's, invented here, and nothing in them pretends to come from a robot."""

    def __init__(self, hub: TelemetryHub, session: str, period_s: float = 0.1):
        super().__init__(name="synthetic-telemetry", daemon=True)
        self._hub, self._session, self._period, self._halt = hub, session, period_s, threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        import math

        seq = 0
        while not self._halt.wait(self._period):
            if self._hub.readers <= 0:
                continue
            seq += 1
            now = time.monotonic_ns()
            record = sources.unavailable_tap()
            record.update({
                "available": True, "state": sources.CONNECTED, "reason": None,
                "source_stamp": {"session": self._session, "seq": seq, "sample_mono_ns": now, "clock_domain": "laptop_monotonic"},
                "joints": {"shoulder_pan": round(8.0 * math.sin(seq / 25.0), 3), "shoulder_lift": -42.0, "elbow_flex": 61.5, "wrist_flex": 70.0, "wrist_roll": 0.0, "gripper": 12.0},
                "base_velocity": {"x_mps": 0.0, "y_mps": 0.0, "theta_dps": 0.0},
                "receive_mono_ns": now, "receive_age_ms": 0.0, "clock_domains": {"sample": "laptop_monotonic", "receive": "laptop_monotonic"},
                "measured_rate_hz": round(1.0 / self._period, 3), "subscriber_gap_count": 0, "publisher_drop_count": 0,
                "session_changes": 0, "malformed_count": 0, "transport_errors": 0})
            self._hub.publish("tap", record)
