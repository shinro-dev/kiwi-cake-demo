# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.supervisor: ONE command authority for the robot, and the life of the controller process.

WHAT IT OWNS. The active run and its identity, command arbitration, stop, the completion record, and the merge of the
controller's event file into the runtime event log. It is NOT in the motion path: the controller talks to the robot by
itself and keeps running if this process dies.

COMMANDS. A typed allowlist, validated by demo_contracts (the numeric bounds live in the schema) and mapped to the
controller's argv. No shell, no free text. While a run is active only stop is accepted. Nothing is queued for later.

WHAT A SNAPSHOT SAYS ABOUT THE ROBOT. block() is the one arbitration and it answers both a submit and the capability list
of a snapshot. They differ in exactly one way, on purpose. A submit asks the robot computer itself, fresh, because a start
may not rest on anything older than the moment it is asked. A snapshot reads instead what a background reader last found,
with its age, because an ssh on a poll that happens every few seconds would stall every open console for the whole ssh
timeout whenever the robot computer is off. That is what stops an enabled software capability from being shown as a ready
robot: the control is drawn off with the real cause on it (not reachable, no host running, host in scan mode) rather than
looking ready until Start is pressed. A reading too old to stand for the robot now is reported as no answer at all, and
the cached reading has no vote in a submit, in either direction.

STATES. accepted -> spawning -> running -> finished, plus rejected and uncertain. 'accepted' is fsynced to the journal BEFORE
any spawn, 'spawning' before the child is created, and 'running' carries the child's identity (pid, /proc start time, the
unique scan id that appears in the child's command line). The same command_id submitted twice returns the recorded result.

RECOVERY. On start every command left in accepted/spawning/running is looked up by process identity AND by scanning
/proc/*/cmdline for its scan id AND by the files of its run directory. A live child is ADOPTED (tailing resumes, stop works
through a pidfd, the exit status is reported as unavailable, the outcome comes from the controller's run.finished). A dead
child that left a run.finished record is finished. Anything else becomes 'uncertain' and the owner becomes 'unresolved':
every command but stop stays blocked until an operator resolves it. The supervisor NEVER respawns.

RESOLUTION RECONCILES; IT DOES NOT CLEAR. resolve() re-reads the evidence itself (is a process with that identity alive
now, does the run's event file hold a terminal record, did the observation store gain captures after the command was
accepted, does the journal hold a child identity) and classifies what it finds: ran_and_finished, ran_and_was_interrupted,
never_started or still_unknown. The operator's declared finding is recorded as an ATTESTATION beside that evidence, never
instead of it: a finding that contradicts what is found (declaring that nothing runs while a live child is there) is
REFUSED with the contradiction stated, and still_unknown leaves every command blocked. Nothing in this path starts,
signals or retries anything: the ambiguous command is never run a second time, and this module has no motor path at all.

STOP. A priority path: SIGINT to the controller PID only, through its pidfd, sent before anything is logged and without
waiting for the tailer, the journal or a submit in progress. Never the process group: the controller's ssh and scp children
share it and killing them turns a clean stop into a crash. Requested, acknowledged and the observed outcome are recorded
separately with measured latencies. There is NO escalation to SIGTERM or SIGKILL anywhere in this module.

THE TWO EXECUTION BACKENDS. config.ExecutionBackend says how a run reaches the robot, and this module does exactly one
thing differently between them.
  - baseline_normal_host is the PROVEN path: the same controller, the same arguments, against the motor host in its
    NORMAL mode, which is what every recorded hardware run used. No lease is taken and no lease metadata is attached,
    because the NORMAL host asks for none. The ONLY ownership of the robot under this backend is this runtime's own
    single run slot on this computer: one run at a time, refused here, by this process. NOTHING ON THE ROBOT refuses a
    command from anywhere else, and any other process on that computer can still open the servo bus. Everything else the
    proven path does is kept: the preflight, the allowlist, the action text, the command identity, the reconciliation,
    the pinned bundle and the scan id;
  - boundary_lease_host is the DEFERRED path, unchanged below, and it stays whole: it needs the host boundary, it takes
    that boundary's lease before it spawns, and a command whose lease cannot be taken is refused with nothing started.
No lease is ever faked for the baseline, and no check of the boundary path is relaxed to let the baseline through.

THE HOST COMMAND LEASE (the boundary backend only). A host with its boundary on (bin/pi/host_boundary.py) drops every
command that does not carry the live lease, so a controller started without one would work through a whole cycle with
nothing reaching a motor. This module therefore TAKES the lease before it spawns a controller that could command motion,
hands the generation and the host session to the child through the environment, renews it while the run lives, and gives
it back when the run ends, however it ends.
Four rules, each of them load bearing:
  - a command whose lease cannot be taken is REFUSED with reason_code lease_unavailable and NOTHING is spawned. A controller
    whose every command would be dropped must never be started;
  - a renewal that fails is REPORTED and tried again. It never ends a run and it never releases the lease: the host extends
    the lease on every valid command, so a controller that is still commanding keeps it even while this side cannot reach
    the control socket, and taking it away would be the one thing that really stops the robot mid-cycle;
  - the release happens on the run's ONE exit path (_release), so a crash, a stop and a clean finish all reach it. A daemon
    that DETACHES (its own shutdown) stops renewing and does NOT release: the controller runs on and keeps commanding;
  - what the snapshot says is what this daemon HOLDS, not what its lease client last heard. A lease the daemon does not
    hold is never shown as held.
Nothing in this path constructs a robot client or sends an action: the lease client speaks JSON to a loopback socket, and
the controller alone talks to the robot.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
import uuid as uuid_module
from pathlib import Path
from typing import Callable, Optional

from . import bundle as bundle_module
from .config import (CAMERA_SERVICE_MISSION_PORT, CaptureProvider, ExecutionBackend, execution_backend_of, model_files_digest, perception_remote_of,
                     robot_link_is_local)
from .journal import (ACTIVE_STATUSES, CONTRACT_VERSION, EventLog, InvalidEvent, Journal, command_result, fold_commands, make_event, read_json_lines, utc_compact,
                      utc_now_text)
from .lease_client import DEFAULT_TTL_S as LEASE_TTL_S  # the constant only: this module never constructs a lease client
from .read_models import MOTION_CAPABILITIES as MOTION_COMMAND_TYPES  # the constant only: the snapshot's gate reads the same tuple

import demo_contracts  # importable once .journal has been imported

LIVE, SYNTHETIC = "live", "synthetic"
HISTORY_DIR = "history"  # DATA/history/<run_id>/: the run directory
CONTROLLER_EVENTS_FILE, STDOUT_FILE, STDERR_FILE, MANIFEST_FILE, COMPLETION_FILE = "controller-events.jsonl", "stdout.log", "stderr.log", "manifest.json", "completion.json"
FAULT_ENV_VAR = "KC_RUNTIME_FAULT_AFTER"  # honored in synthetic mode only: the recovery tests kill the daemon at an exact state
FAULT_POINTS = ("accepted", "spawning", "spawned", "running")
FINDINGS = ("no_controller_running", "controller_finished", "controller_stopped_by_operator")
# What the daemon itself concludes from the evidence when an uncertain command is reconciled. The first two are decided by
# the machine alone; 'ran_and_was_interrupted' needs the operator's attestation on top; 'still_unknown' clears nothing.
FINISHED_CLASS, INTERRUPTED_CLASS, NEVER_STARTED_CLASS, UNKNOWN_CLASS = "ran_and_finished", "ran_and_was_interrupted", "never_started", "still_unknown"
CLASSIFICATIONS = (FINISHED_CLASS, INTERRUPTED_CLASS, NEVER_STARTED_CLASS, UNKNOWN_CLASS)
CONCLUSIVE_CLASSES = (FINISHED_CLASS, NEVER_STARTED_CLASS)
COMMANDS_BLOCKED_WHILE_UNRESOLVED = ("start_cycle", "observe", "return_arm", "scan_scene", "manual_pan", "manual_move", "manual_grip", "manual_base_pulse")
_MAX_OBSERVATION_SCAN = 4096  # a bound on the store walk: an evidence read must end, whatever the store holds
_MTIME_SLACK_S = 2.0  # a file written in the same second the command was accepted counts as after it
_MAX_NOTE = 200
EXIT_OUTCOMES = {3: "refused", 4: "stopped", 5: "failed"}  # the controller's documented exit statuses; 0 alone does not say placed or no-op
STOP_EXIT_STATUS = 4
STOP_REPEAT_INTERVAL_NS, STOP_REPEAT_LIMIT = 400_000_000, 10  # an unacknowledged SIGINT is repeated every 0.4 s, ten times at most. SIGINT only, PID only
AUTONOMOUS_TYPES = ("start_cycle",)
_EVENT_KINDS = frozenset(demo_contracts.load_schema("event#/$defs/kind")["enum"])
_MAX_LINE = 1 << 20

# The two variables a controller reads to put the host lease on every action it sends (demo_runtime.lease_client builds
# kc.lease and kc.session out of them). They are the ONE place this runtime and the controller meet, and each side pins
# the other: bin/laptop/manipulation/run_single_cube_cycle.py documents and reads exactly this pair, its own test parses
# this literal assignment, and tests/test_supervisor.py reads that controller. A controller that took any other pair
# would get nothing from this runtime, and a host with its boundary on would drop every command it sent.
LEASE_ENV_GENERATION, LEASE_ENV_SESSION = "KC_HOST_LEASE", "KC_HOST_SESSION"
# THE MISSION TARGET FILE of a mission's run (autonomy/manipulation/target_binding.py owns its schema and spells the same
# name; a test keeps the two equal rather than importing that module into this one). It is REMOVED from every child's
# environment first and without a condition, so a legacy command never inherits one from the shell that started the
# daemon, and it is set only for a run start_mission_run admitted, to the file written into that run's own directory.
MISSION_TARGET_ENV = "KC_MISSION_TARGET"
# run_stage1_scan.CAMERA_SERVICE_LOCAL_ENV, spelled here for the same reason (a test keeps the two equal).
CAMERA_SERVICE_LOCAL_ENV = "KC_CAMERA_SERVICE_LOCAL"
# THE DESKTOP PERCEPTION of a LIVE run on the robot (config perception.remote; autonomy/perception/remote_perception.py and
# bin/laptop/manipulation/run_single_cube_cycle.py read these, and a test keeps the spellings equal). All four are REMOVED
# from every child's environment first and without a condition, like the lease pair: a value inherited from the shell that
# started the daemon would send a controller's frames somewhere nobody configured. They are set only for a LIVE runtime
# whose robot link is local and whose configuration names perception.remote.
PERCEPTION_PORT_ENV, PERCEPTION_DEADLINE_ENV = "KC_PERCEPTION_REMOTE_PORT", "KC_PERCEPTION_DEADLINE_S"
PERCEPTION_MODEL_REVISION_ENV, PERCEPTION_MODEL_FILES_ENV = "KC_PERCEPTION_MODEL_REVISION", "KC_PERCEPTION_MODEL_FILES_DIGEST"
PERCEPTION_ENV_NAMES = (PERCEPTION_PORT_ENV, PERCEPTION_DEADLINE_ENV, PERCEPTION_MODEL_REVISION_ENV, PERCEPTION_MODEL_FILES_ENV)
# The robot resident's session the run was ADMITTED under (the identity gate's reading, journaled on the accept record),
# for the controller to name in every desktop perception request. Removed first; set only when an admission named one.
RESIDENT_SESSION_ENV = "KC_RESIDENT_SESSION"
# The runtime's data mode, "live" or "synthetic", for a controller that must know which one it runs under. Removed first
# and then always set from THIS supervisor's mode, so a child never reads a mode another process exported.
RUNTIME_MODE_ENV = "KC_RUNTIME_MODE"
# THE PLACEMENT ENVELOPE (config.placement_envelope): the words reach the child through these two only, from the ONE config,
# never from the shell the daemon was started in. The controller reads them as run_single_cube_cycle.PLACEMENT_ENVELOPE_ENV
# and PLACEMENT_ENVELOPE_ATTENDED_ENV (a test keeps the names equal).
PLACEMENT_ENVELOPE_ENV, PLACEMENT_ENVELOPE_ATTENDED_ENV = "KC_PLACEMENT_ENVELOPE", "KC_PLACEMENT_ENVELOPE_ATTENDED"
# The forced release offset of side_offset's qualification runs (config.placement_side_offset_force_deg), read by the
# controller as run_single_cube_cycle.PLACEMENT_SIDE_OFFSET_FORCE_ENV (a test keeps the names equal). Removed first and
# without a condition like the two above: a value left in the daemon's shell used to reach every child, refusing every
# run with no word and taken silently as the forced offset under side_offset.
PLACEMENT_SIDE_OFFSET_FORCE_ENV = "KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG"
MISSION_TARGET_FILE = "mission-target.json"
MISSION_RESULT_FILE = "mission-result.json"
# Why a mission's run was not started. Codes, like every other refusal here; none names a path or a value.
MISSION_STOPPING = "mission_stopping"
MISSION_STOPPED_AT_PUBLICATION = "mission_stopped_before_publication"
# A supervisor that has let go of its runs (detach(): the daemon is shutting down) starts no mission run: the unit has
# Restart=no, so a controller spawned now would run on with nobody supervising it.
MISSION_RUNTIME_DETACHED = "mission_runtime_detached"
MISSION_TARGET_REFUSED = "mission_target_refused"
MISSION_MOTION_GATE_ABSENT = "robot_motion_gate_absent"
# A LIVE runtime's Retry of a held mission (the park, then a fresh cycle) is motion with no qualification record: it is
# refused unless the configuration switches it on (mission.retry) and names the attended label (Supervisor.
# mission_retry_refusal). Each code says which of the two is missing.
MISSION_RETRY_NOT_ENABLED = "mission_retry_not_enabled"
MISSION_RETRY_NOT_ATTENDED = "mission_retry_not_attended"
LEASE_UNAVAILABLE = "lease_unavailable"
LEASE_RENEW_FRACTION = 1.0 / 3.0  # renew three times per TTL: two consecutive failures still leave a third of the TTL
LEASE_RENEW_MIN_S = 1.0
LEASE_OWNER_LABEL = "kiwi-console-runtime"  # shown on the host's tap and in its terminal: a role, never an identity
BASELINE_BACKEND, BOUNDARY_BACKEND = ExecutionBackend.BASELINE_NORMAL_HOST, ExecutionBackend.BOUNDARY_LEASE_HOST
# Why this runtime holds no host lease under the baseline backend. It is not a fault and it is not a lease that was tried
# for and refused: that host asks for none, so none is taken.
BASELINE_NO_LEASE_REASON = "baseline_backend_takes_no_host_lease"
OWNERSHIP_RUNTIME_SLOT, OWNERSHIP_HOST_LEASE = "runtime_single_run_slot", "host_command_lease"
BASELINE_OWNERSHIP_TEXT = ("the only thing that owns the robot is this runtime's single run slot on this computer: one run at a time, refused here, by this process. "
                           "Nothing on the robot refuses a command from anywhere else, and any other process on that computer can still open the servo bus")
BOUNDARY_HELD_TEXT = ("the robot computer's host boundary granted this runtime the command lease, and it drops every command that does not carry the live generation")
BOUNDARY_UNHELD_TEXT = ("this backend needs the robot computer's host boundary and its command lease; this runtime holds none, so nothing that could move a motor is started")


def lease_required(mode: str, hardware_enabled: bool, command: dict, backend=BOUNDARY_BACKEND) -> bool:
    """Could the controller this command would start send an action to a robot THAT WOULD DROP IT WITHOUT A LEASE? Only
    then is a lease taken.

    The baseline backend runs against the motor host in its NORMAL mode, which asks for no lease and drops nothing: a
    lease there would be a number this runtime took from a boundary that is not running, so none is taken and none is
    attached. Otherwise, as before: live mode with hardware commands enabled and not a plan-only run (a plan-only run
    prints its plan and sends nothing; a synthetic run never reaches a robot; a runtime without
    --enable-hardware-commands refuses the command earlier).

    The DEFAULT is the boundary backend, so a caller that forgets to say which path it is on is told a lease is needed
    and its command is refused rather than started blind. The default of a CONFIGURATION is the opposite and says so out
    loud (config.DEFAULT_EXECUTION_BACKEND): a file that names no backend gets the path with a record on hardware."""
    if backend != BOUNDARY_BACKEND:
        return False
    if mode != LIVE or not hardware_enabled or not isinstance(command, dict):
        return False
    arguments = command.get("args")
    return not (isinstance(arguments, dict) and arguments.get("plan_only") is True)


# ---- the command allowlist ------------------------------------------------------------------------------------------------

def _number(value) -> str:
    return repr(float(value))


def action_text(command: dict) -> str:
    """The controller's --do text of a VALIDATED command. A type outside this table has no mapping and cannot be run."""
    kind, arguments = command["type"], command["args"]
    if kind == "start_cycle":
        return "cycle"
    if kind == "observe":
        return "observe"
    if kind == "return_arm":
        return "exit"
    if kind == "scan_scene":
        return "scan"
    if kind == "manual_pan":
        return f"pan:{_number(arguments['deg'])}"
    if kind == "manual_move":
        return f"move:{_number(arguments['forward_mm'])},{_number(arguments['dz_mm'])}"
    if kind == "manual_grip":
        return f"grip:{arguments['action']}"
    if kind == "manual_base_pulse":
        return f"base:{_number(arguments['seconds'])}"
    raise KeyError(kind)


def controller_arguments(command: dict, scan_id: str, *, address: str, robot_id: str, pi_ssh: str,
                         capture_provider=None, mission_port=None) -> list:
    """argv after the entry file. Flags come from the command's own booleans, never from a default.

    THE LEGACY ARGV IS UNCHANGED, BYTE FOR BYTE. The provider is appended only when the configuration names the camera
    service, so a runtime configured the way every recorded run was configured spawns exactly the argv those runs were
    produced with, and the proven command line keeps its meaning. A caller that passes nothing gets what this function
    returned before the managed provider existed.
    """
    arguments = ["--do", action_text(command)]
    if command["args"].get("holding") is True:
        arguments.append("--holding")
    if command["args"].get("plan_only") is True:
        arguments.append("--plan-only")
    arguments += ["--remote-ip", address, "--robot-id", robot_id]
    if pi_ssh:
        arguments += ["--pi-ssh", pi_ssh]
    arguments += ["--scan-id", scan_id]
    if capture_provider is not None and capture_provider != CaptureProvider.LEGACY_PER_CAPTURE.value:
        arguments += ["--capture-provider", str(capture_provider)]
        if mission_port is not None:
            arguments += ["--mission-port", str(int(mission_port))]
    return arguments


def scan_id_for(command_id: str) -> str:
    return f"console-{utc_compact()}-{command_id[:8]}"


# ---- pidfd: a handle on ONE process that a recycled pid cannot satisfy -----------------------------------------------------------

# Some interpreter builds lack os.pidfd_open although the kernel has the call (a Python built against old kernel headers: seen
# with a conda build of 3.13 on a 6.8 kernel). The system call is then made directly. Numbers from the kernel's unified table
# (asm-generic, shared by x86_64 and aarch64 from 424 on); an architecture that is not listed gets no pidfd rather than a guess.
_PIDFD_SYSCALLS = {"x86_64": (434, 424), "aarch64": (434, 424)}  # machine -> (pidfd_open, pidfd_send_signal)


def _raw_syscall(number: int, *arguments) -> int:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return int(libc.syscall(ctypes.c_long(number), *arguments))


def pidfd_mechanism() -> Optional[str]:
    """'pidfd' (the interpreter has os.pidfd_open), 'pidfd_syscall' (the kernel call is made directly) or None."""
    if hasattr(os, "pidfd_open"):
        return "pidfd"
    return "pidfd_syscall" if os.uname().machine in _PIDFD_SYSCALLS else None


def pidfd_open(pid: int) -> Optional[int]:
    """A pidfd for pid, or None when none can be had. NEVER raises: a caller that has just spawned a child must not lose
    track of it because of this call."""
    try:
        if hasattr(os, "pidfd_open"):
            return os.pidfd_open(pid)
        numbers = _PIDFD_SYSCALLS.get(os.uname().machine)
        if numbers is None:
            return None
        import ctypes

        descriptor = _raw_syscall(numbers[0], ctypes.c_int(pid), ctypes.c_uint(0))
        return descriptor if descriptor >= 0 else None
    except Exception:  # noqa: BLE001 -- see the docstring
        return None


def pidfd_sigint(pidfd: int) -> None:
    """SIGINT to exactly the process behind pidfd. Raises ProcessLookupError when it is gone, OSError otherwise. The signal is
    NOT a parameter: this module has no way to send anything stronger, so an escalation cannot be written by accident."""
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(pidfd, signal.SIGINT)
        return
    import ctypes

    numbers = _PIDFD_SYSCALLS.get(os.uname().machine)
    if numbers is None:
        raise OSError("no pidfd_send_signal on this platform")
    if _raw_syscall(numbers[1], ctypes.c_int(pidfd), ctypes.c_int(int(signal.SIGINT)), None, ctypes.c_uint(0)) < 0:
        error = ctypes.get_errno()
        raise ProcessLookupError(error, os.strerror(error)) if error == 3 else OSError(error, os.strerror(error))


# ---- process identity -----------------------------------------------------------------------------------------------------

def proc_stat(pid: int, proc_root: str = "/proc") -> Optional[tuple]:
    """(state, start time in clock ticks) from /proc/<pid>/stat, or None. The command name can hold spaces and brackets, so
    the fields are counted from the LAST closing bracket."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), "rb") as handle:
            text = handle.read().decode("ascii", "replace")
    except OSError:
        return None
    fields = text[text.rfind(")") + 1:].split()
    try:
        return fields[0], int(fields[19])
    except (IndexError, ValueError):
        return None


def proc_cmdline(pid: int, proc_root: str = "/proc") -> list:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as handle:
            return [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
    except OSError:
        return []


def _carries_scan_id(arguments: list, scan_id: str) -> bool:
    return any(flag == "--scan-id" and value == scan_id for flag, value in zip(arguments, arguments[1:]))


def is_alive(pid: int, proc_root: str = "/proc") -> bool:
    stat = proc_stat(pid, proc_root)
    return stat is not None and stat[0] not in ("Z", "X")


def proc_environ_value(pid: int, name: str, proc_root: str = "/proc") -> Optional[str]:
    """One variable of the environment a process was started with, or None (absent, or not ours to read)."""
    try:
        with open(os.path.join(proc_root, str(pid), "environ"), "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    prefix = name.encode("ascii") + b"="
    for part in data.split(b"\0"):
        if part.startswith(prefix):
            return part[len(prefix):].decode("utf-8", "replace")
    return None


def is_our_controller(pid: int, scan_id: str, events_path, proc_root: str = "/proc") -> bool:
    """A scan id alone does NOT identify a controller: it is unique per second and per first eight characters of a command
    id the CLIENT minted, and two daemons on one machine produced the same one (found by running this package's tests in
    parallel: a live child was not adopted, and with a single match a daemon would have adopted, and then stopped, ANOTHER
    daemon's controller). The process must also have been started with KC_EVENTS_PATH naming THIS run's file inside THIS
    daemon's data directory. An environment that cannot be read is not ours: fail closed, the command becomes uncertain."""
    if not _carries_scan_id(proc_cmdline(pid, proc_root), scan_id):
        return False
    named = proc_environ_value(pid, "KC_EVENTS_PATH", proc_root)
    return bool(named) and os.path.realpath(named) == os.path.realpath(os.fspath(events_path))  # real paths: the same directory reached through a link is the same directory


def find_by_scan_id(scan_id: str, events_path, proc_root: str = "/proc") -> list:
    found = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return found
    for entry in entries:
        if entry.isdigit() and int(entry) != os.getpid() and is_our_controller(int(entry), scan_id, events_path, proc_root) and is_alive(int(entry), proc_root):
            found.append(int(entry))
    return found


def _process_table_readable(proc_root: str = "/proc") -> bool:
    """Can the process table be listed at all? A table that cannot be read proves NOTHING about a live controller, and
    that is the difference between 'nothing is running' and 'nobody looked'."""
    try:
        os.listdir(proc_root)
        return True
    except OSError:
        return False


def _wall_epoch(text) -> Optional[float]:
    """The journal's wall stamp as POSIX seconds, or None. Both this and a file's mtime are THIS machine's wall clock."""
    if not isinstance(text, str):
        return None
    from datetime import datetime, timezone

    for shape in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, shape).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _clean_note(note) -> tuple:
    """(the note to record, was it acceptable). An operator note is durable and reaches a screen: it is bounded and
    printable ASCII only, so it can carry no control character and no wall of text."""
    if note is None:
        return None, True
    if not isinstance(note, str) or len(note) > _MAX_NOTE:
        return None, False
    text = note.strip()
    if not text:
        return None, True
    return (text, True) if all(32 <= ord(character) < 127 for character in text) else (None, False)


def classify_evidence(evidence: dict) -> tuple:
    """(classification, the list of evidence that could not be read). The ORDER is the point:

      - a process table that could not be read means a live controller cannot be ruled out: still_unknown, whatever else
        was found, because the dangerous case is exactly the one that cannot be seen;
      - the controller's own terminal record is the strongest thing there is: ran_and_finished, with its outcome;
      - anything that only a started controller leaves behind (a journalled child identity, records in its event file,
        its output file, captures written after the command was accepted) means it ran: ran_and_was_interrupted;
      - no trace at all, with every check readable: never_started;
      - no trace, and a check that failed: still_unknown. Absence of evidence counts only where evidence could be read."""
    process, files, observations = evidence["process"], evidence["run_files"], evidence["observations"]
    unreadable = []
    if not process["readable"]:
        unreadable.append("process_table")
    if not files["readable"]:
        unreadable.append("run_files")
    if observations["checked"] and not observations["readable"]:
        unreadable.append("observation_store")
    if not process["readable"]:
        return UNKNOWN_CLASS, unreadable
    if files["terminal_record"]:
        return FINISHED_CLASS, unreadable
    ran = evidence["journal"]["child_identity"] or bool(files["event_records"]) or files["output_present"] or bool(observations["files_after_accept"])
    if ran:
        return INTERRUPTED_CLASS, unreadable
    return (UNKNOWN_CLASS if unreadable else NEVER_STARTED_CLASS), unreadable


def question_for(evidence: dict) -> str:
    """What the operator is being asked to confirm, in words that carry no path, no address and no device id."""
    classification, outcome = evidence["classification"], evidence["run_files"]["outcome"]
    if classification == FINISHED_CLASS:
        return (f"the controller's own record says this run ended ({outcome or 'outcome not stated'}) and no process of it is alive now; "
                "confirm what you found at the robot")
    if classification == INTERRUPTED_CLASS:
        return ("this command started a controller that left no terminal record, and no process of it is alive now; confirm that none is running "
                "and say how it ended, after looking at the robot")
    if classification == NEVER_STARTED_CLASS:
        return "nothing on this machine shows that a controller ever started for this command; confirm that none is running"
    return (f"the evidence could not be read ({', '.join(evidence['unreadable']) or 'reason not stated'}); commands stay blocked until it can be. "
            "Your statement is recorded either way")


def find_external_controllers(entry_name: str, known_scan_ids, proc_root: str = "/proc") -> list:
    """Processes that run the controller entry and were not started by this runtime (their scan id is not one of ours)."""
    found = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        arguments = proc_cmdline(int(entry), proc_root)
        if any(os.path.basename(argument) == entry_name for argument in arguments[:3]) and not any(_carries_scan_id(arguments, scan_id) for scan_id in known_scan_ids):
            if is_alive(int(entry), proc_root):
                found.append(int(entry))
    return found


# ---- the controller's event file ------------------------------------------------------------------------------------------

class Tailer(threading.Thread):
    """Follows one file by byte offset. A line is handed on only when its newline has arrived; what is left without one stays
    in `partial` and is retried. finish() asks for one LAST drain and then the thread ends; detach() ends it without one.
    Every private attribute is prefixed _tail_: threading.Thread keeps its own private state on the same object (3.13 added
    _handle), and an attribute of ours with one of its names silently breaks is_alive() and join()."""

    def __init__(self, path, offset: int, on_line: Callable, on_oversize: Callable, on_batch: Callable, interval_s: float):
        super().__init__(name="controller-events-tailer", daemon=True)
        self.path, self.offset, self.partial = os.fspath(path), offset, b""
        self._tail_on_line, self._tail_on_oversize, self._tail_on_batch, self._tail_interval = on_line, on_oversize, on_batch, interval_s
        self._tail_finish, self._tail_detach, self._tail_handle = threading.Event(), threading.Event(), None

    def finish(self) -> None:
        self._tail_finish.set()

    def detach(self) -> None:
        self._tail_detach.set()
        self._tail_finish.set()

    def close(self) -> None:
        """Release the file of a tailer whose THREAD was never started (a standalone drain(), as in recovery). Closing
        it twice is harmless; calling it on a running tailer would not be, so only a caller that owns the drain does."""
        handle, self._tail_handle = self._tail_handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def drain(self) -> None:
        if self._tail_handle is None:
            try:
                self._tail_handle = open(self.path, "rb")
            except OSError:
                return
            self._tail_handle.seek(self.offset)
        handled = False
        while True:
            chunk = self._tail_handle.read(1 << 16)
            if not chunk:
                break
            self.partial += chunk
            while True:
                line, newline, rest = self.partial.partition(b"\n")
                if not newline:
                    break
                self.partial, self.offset = rest, self.offset + len(line) + 1
                if line.strip():
                    self._tail_on_line(line, self.offset)
                    handled = True
            if len(self.partial) > _MAX_LINE:
                self.offset, self.partial = self.offset + len(self.partial), b""
                self._tail_on_oversize(self.offset)
        if handled:
            self._tail_on_batch()

    def run(self) -> None:
        while True:
            finishing = self._tail_finish.is_set()  # read BEFORE draining: a finish request is always followed by one full drain
            if self._tail_detach.is_set():
                break
            self.drain()
            if finishing:
                break
            self._tail_finish.wait(self._tail_interval)
        if self._tail_handle is not None:
            self._tail_handle.close()


def scan_run_finished(path) -> Optional[dict]:
    """The LAST run.finished record of a controller event file, or None. Used when the file was not tailed to its end."""
    found = None
    try:
        with open(path, "rb") as handle:
            for raw in handle:
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("kind") == "run.finished" and isinstance(record.get("payload"), dict):
                    found = record
    except OSError:
        return None
    return found


class _StoppedBeforeSpawn(Exception):
    pass


class _MissionTargetRefused(Exception):
    """The mission target file could not be written into the run directory, so the run is never started."""


class MissionRun:
    """What makes a run a MISSION's run, handed to start_mission_run by the robot-side execution adapter.

    `write_target(run_dir, run_id)` writes the mission target file into the run directory that was just created and
    returns its absolute path; it is called after that directory exists and before anything is spawned, and whatever it
    raises ends the run as never started. `on_run_started(run_id)` records, on the mission's own file, that this run was
    started for this target; it is called after the target file is written and before the spawn, so the mission's record
    names the run id before a child that could move anything exists. `cancel` is THE MISSION'S cancel event: it is read
    inside the lock that publishes the run, so a stop that set it before that point starts nothing. A mission's PARK
    (start_mission_park) carries no target file: its `write_target` is None.
    """

    __slots__ = ("mission_id", "target_id", "cancel", "write_target", "on_run_started")

    def __init__(self, *, mission_id: str, target_id: str, cancel: threading.Event, write_target: Optional[Callable],
                 on_run_started: Optional[Callable] = None):
        self.mission_id, self.target_id, self.cancel = mission_id, target_id, cancel
        self.write_target, self.on_run_started = write_target, on_run_started

    def fields(self) -> dict:
        return {"mission_id": self.mission_id, "target_id": self.target_id}


class _Run:
    def __init__(self, *, run_id: str, command_id: str, command_type: str, run_dir: Path, adopted: bool = False, lease_hold_id: Optional[int] = None):
        self.run_id, self.command_id, self.command_type, self.run_dir, self.adopted = run_id, command_id, command_type, run_dir, adopted
        self.lease_hold_id = lease_hold_id  # which acquisition of the host lease this run was started under, if any
        self.mission: Optional["MissionRun"] = None  # set only for a run start_mission_run admitted
        self.robot_admission: Optional[dict] = None  # the identity gate's admission of THIS run (session, build, host instance), if any
        self.mission_target_path: Optional[str] = None  # the mission target file, once it was written into run_dir
        self.events_path = run_dir / CONTROLLER_EVENTS_FILE
        self.status, self.state, self.started_seq = "spawning", None, 0
        self.started_wall_utc = utc_now_text()
        self.proc: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self.pidfd: Optional[int] = None
        self.start_ticks: Optional[int] = None
        self.bundle_digest: Optional[str] = None
        self.tailer: Optional[Tailer] = None
        self.finalized = False
        # stop bookkeeping: requested, signalled, acknowledged are three different moments
        self.stop_pending = False
        self.stop_requested_mono_ns: Optional[int] = None
        self.stop_signal_mono_ns: Optional[int] = None
        self.stop_signal_failed = False
        self.stop_mechanism: Optional[str] = None  # pidfd | own_child_handle | pid_verified: HOW the signal was sent, recorded with the stop
        self.stop_signals_sent = 0  # SIGINTs sent for this stop: 1, plus the bounded repeats while nothing acknowledged it
        self.stop_last_signal_mono_ns: Optional[int] = None
        self.stop_origin, self.stop_command_id = None, None
        self.ack_mono_ns: Optional[int] = None
        self.ack_source: Optional[str] = None
        self.ack_basis: Optional[str] = None  # controller_event_clock | runtime_observed: which clock reading ack_latency_ns rests on
        self.pre_handler_evidence: Optional[str] = None  # killed_by_sigint | keyboard_interrupt_traceback: WHY a stop without an ack still counts as the stop
        # what the controller's file said
        self.controller_finished: Optional[dict] = None
        self.controller_finished_origin: Optional[dict] = None
        self.controller_run_started = False
        self.last_source_seq: Optional[int] = None
        self.daemon_gaps = 0


class Supervisor:
    def __init__(self, *, data_dir, journal: Journal, event_log: EventLog, runtime_session: str, mode: str, source_root, controller_entry: str,
                 config=None, config_path: Optional[str] = None, hardware_enabled: bool = False, sanitizer=None, python: str = sys.executable,
                 extra_env: Optional[dict] = None, external_probe: Optional[Callable] = None, proc_root: str = "/proc", tail_interval_s: float = 0.05,
                 preflight: Optional[Callable] = None, lease=None, readiness=None, motion_gate: Optional[Callable] = None,
                 run_slot_gate: Optional[Callable] = None):
        if mode not in (LIVE, SYNTHETIC):
            raise ValueError("mode must be live or synthetic")
        if mode == LIVE and config is None:
            raise ValueError("a live supervisor needs the runtime config")
        self.data_dir, self.journal, self.log, self.session, self.mode = Path(data_dir), journal, event_log, runtime_session, mode
        self.source_root, self.controller_entry, self.config, self.config_path = Path(source_root), controller_entry, config, config_path
        # WHICH PATH TO THE ROBOT this supervisor is on, decided once, from the configuration, and never inferred later.
        self.backend = execution_backend_of(config)
        self.hardware_enabled, self.sanitizer, self.python, self.extra_env = hardware_enabled, sanitizer, python, dict(extra_env or {})
        self.proc_root, self.tail_interval_s = proc_root, tail_interval_s
        self._external_probe = external_probe
        self._preflight = preflight
        self._preflight_cache = None
        # The background reading of the robot computer, or None when there is nothing to read (synthetic, no usable ssh
        # target, or a backend whose submit does not ask the question either). This supervisor only ever READS it; whoever
        # built it owns its thread. It is consulted for a SNAPSHOT and never for a submit: see _preflight_report.
        self.readiness = readiness
        # The robot resident's identity gate (demo_runtime.robot_link.MotionPermission, asked by the daemon with a FRESH
        # read of the robot's build identity, resident session and supervisor host instance EVERY call; the build identity
        # identifies the SOFTWARE and refuses a different build, and which deployment is read is established by the
        # authenticated connection to the intended robot and the session that endpoint's resident reports now): None, or a
        # callable answering (refusal, admitted), exactly one of them None. refusal is (reason_code, sentence) when a
        # command that MOVES the robot must be refused now; admitted is the identity it may be admitted under, which the
        # accept record journals. Only MOTION_COMMAND_TYPES are asked about; observe and stop never are.
        self._motion_gate = motion_gate
        # THE SINGLE RUN SLOT, SHARED HONESTLY. None, or a callable answering a reason code while something that is not a
        # controller run holds the slot (the daemon's mission, carried on its own thread), else None. It is asked by
        # block(), so a submit is refused by it AND a snapshot shows every command but stop blocked by it. The reverse
        # direction is start_if_run_slot_free below. Stop never asks it.
        self._run_slot_gate = run_slot_gate
        self._evidence_cache = None  # (command_id, monotonic, evidence): a snapshot asks often and this reads files and /proc
        self._fault_after = os.environ.get(FAULT_ENV_VAR) if mode == SYNTHETIC else None
        self._submit_lock = threading.Lock()  # serializes accept/spawn/resolve/reconcile; held across I/O; stop never takes it
        self._state_lock = threading.Lock()  # guards the table and the active-run pointer; never held across I/O
        self._stop_lock = threading.Lock()  # idempotency of stop COMMANDS only
        # The lease client is one instance, one thread, and each of its calls blocks for about a timeout: _lease_lock
        # serializes them and IS held across that I/O. What the daemon believes it holds lives under _hold_lock instead,
        # which is never held across I/O, so a snapshot asking whether the lease is held cannot wait for a renewal.
        # The one order, never the reverse: _lease_lock, then _hold_lock.
        self._lease = lease
        self._lease_lock, self._hold_lock = threading.Lock(), threading.Lock()
        self._lease_hold: Optional[dict] = None
        self._lease_holds = 0  # every acquisition gets an id, so one run can never give another run's lease back
        self._lease_halt = threading.Event()
        self._renewer: Optional[threading.Thread] = None
        self.lease_renew_failures = 0
        self.lease_last_reason: Optional[str] = None  # why no lease is held: the host's refusal, or how this side failed
        records, self.journal_damaged = journal.read()
        self._table = fold_commands(records)
        self._active: Optional[_Run] = None
        self._last_exit: Optional[dict] = None
        self._detached = threading.Event()
        self.launches = 0
        self.pidfd_mechanism = pidfd_mechanism()  # 'pidfd', 'pidfd_syscall' or None; reported by status, never assumed
        (self.data_dir / HISTORY_DIR).mkdir(mode=0o700, exist_ok=True)

    # ------------------------------------------------------------------ small helpers
    def _fault(self, point: str) -> None:
        if self._fault_after == point:
            os.kill(os.getpid(), signal.SIGKILL)

    def _record(self, command_id: str, **fields) -> dict:
        """Journal first (on disk when this returns), memory second."""
        self.journal.append({"rec": "command", "command_id": command_id, "t_wall_utc": utc_now_text(), **fields})
        with self._state_lock:
            state = self._table.setdefault(command_id, {"command_id": command_id, "status": None, "reason_code": None, "run_id": None, "resolved": False})
            state.update(fields)
            return dict(state)

    def _emit(self, kind: str, payload: dict, *, run: Optional[_Run] = None, run_id: Optional[str] = None, reason_code: Optional[str] = None, origin: Optional[dict] = None,
              sync: bool = True) -> Optional[dict]:
        event = make_event(kind, payload, source_session=self.session, run_id=run.run_id if run else run_id, reason_code=reason_code, artifact_digest=run.bundle_digest if run else None)
        try:
            return self.log.append(event, origin=origin, sync=sync)
        except InvalidEvent:
            try:  # the runtime's own event failed its contract: say so with an event that cannot fail, never stay silent
                return self.log.append(make_event("evidence.gap", {"gap": "other", "detail": "a runtime event failed its own contract and was not recorded", "observation_ids": []},
                                                  source_session=self.session, run_id=run.run_id if run else run_id), sync=sync)
            except InvalidEvent:
                return None

    def _say(self, reason_code: str, text: str, *, run: Optional[_Run] = None, run_id: Optional[str] = None, level: str = "info") -> Optional[dict]:
        return self._emit("diagnostic.text", {"stream": "log", "level": level, "text": text, "redacted": False}, run=run, run_id=run_id, reason_code=reason_code)

    def _gap(self, run: Optional[_Run], gap: str, detail: str, *, origin: Optional[dict] = None, run_id: Optional[str] = None) -> None:
        if run is not None:
            run.daemon_gaps += 1
        self._emit("evidence.gap", {"gap": gap, "detail": detail, "observation_ids": []}, run=run, run_id=run_id, origin=origin)

    # ------------------------------------------------------------------ views
    def result(self, command_id) -> Optional[dict]:
        with self._state_lock:
            state = self._table.get(command_id) if isinstance(command_id, str) else None
            return command_result(state) if state else None

    def table(self) -> dict:
        with self._state_lock:
            return {command_id: dict(state) for command_id, state in self._table.items()}

    def unresolved(self) -> list:
        with self._state_lock:
            return [state["command_id"] for state in self._table.values() if state.get("status") == "uncertain" and not state.get("resolved")]

    def known_scan_ids(self) -> list:
        with self._state_lock:
            return [state["run_id"] for state in self._table.values() if state.get("run_id")]

    def active_run(self) -> Optional[dict]:
        with self._state_lock:
            run = self._active
            if run is None:
                return None
            return {"run_id": run.run_id, "command_id": run.command_id, "command_type": run.command_type, "status": run.status, "state": run.state, "started_seq": run.started_seq}

    def last_exit(self) -> Optional[dict]:
        with self._state_lock:
            return dict(self._last_exit) if self._last_exit else None

    def _external(self) -> bool:
        if self._external_probe is None:
            return False
        try:
            return bool(self._external_probe())
        except Exception:  # noqa: BLE001 -- a probe that fails proves nothing either way; ownership is then decided by the rest
            return False

    def owner(self) -> dict:
        unresolved = self.unresolved()
        lease = self.snapshot_lease()  # taken BEFORE the state lock: the hold lock is never nested inside another one
        with self._state_lock:
            run = self._active
            if unresolved:
                return {"kind": "unresolved", "run_id": self._table[unresolved[0]].get("run_id"), "lease": lease}
            if run is not None:
                return {"kind": "autonomous" if run.command_type in AUTONOMOUS_TYPES else "manual", "run_id": run.run_id, "lease": lease}
        if self._external():
            return {"kind": "external_detected", "run_id": None, "lease": lease}
        return {"kind": "free", "run_id": None, "lease": lease}

    # ------------------------------------------------------------------ the host command lease
    def lease_hold(self) -> Optional[dict]:
        """A copy of what this daemon HOLDS right now, or None. Never blocks on the lease client."""
        with self._hold_lock:
            return dict(self._lease_hold) if self._lease_hold is not None else None

    def lease_report(self) -> dict:
        """Everything the runtime can honestly say about the host command lease.

        held is true ONLY while this daemon holds it. A lease it never took, lost on a renewal or already gave back is
        reported as not held, with the reason beside it; a lease client that merely believes it holds one is not
        evidence, because the acquisition this daemon made is what a controller's metadata was built from. The time left
        is the host's own expires_in_ms from its last reply, LESS how long ago that reply arrived, floored at zero: it
        is null when either of the two is unavailable, because a number invented here would be worse than none.

        Under the BASELINE backend there is no host lease to speak of: required is false, nothing is ever held, and the
        reason says that this backend takes none rather than reporting a missing lease client, which would read as a
        fault instead of the state this path runs in."""
        baseline = self.backend != BOUNDARY_BACKEND
        required = self.mode == LIVE and self.hardware_enabled and not baseline
        attached = self._lease is not None
        with self._hold_lock:
            hold = dict(self._lease_hold) if self._lease_hold is not None else None
            last_reason, renew_failures = self.lease_last_reason, self.lease_renew_failures
        base = {"required": required, "attached": attached, "renew_failures": renew_failures}
        if hold is None:
            reason = last_reason or (BASELINE_NO_LEASE_REASON if baseline and self.mode == LIVE
                                     else "lease_client_not_attached" if not attached else "no_lease_taken" if required else "lease_not_required")
            return {"held": False, "generation": None, "host_session": None, "expires_in_ms": None, "reason": reason,
                    "run_id": None, "adopted": None, "last_reply_age_ms": None, **base}
        expires, age = None, None
        try:  # plain reads on the client: no request, no lock, nothing that can block a snapshot
            reported, age = self._lease.snapshot_lease(), self._lease.last_reply_age_ms()
        except Exception:  # noqa: BLE001 -- a client that cannot describe itself must not break a snapshot
            reported = None
        if isinstance(reported, dict) and reported.get("generation") == hold["generation"]:
            stated = reported.get("expires_in_ms")
            if isinstance(stated, (int, float)) and not isinstance(stated, bool) and isinstance(age, (int, float)):
                expires = max(0.0, round(float(stated) - float(age), 1))
        return {"held": True, "generation": hold["generation"], "host_session": hold["host_session"], "expires_in_ms": expires,
                "reason": None, "run_id": hold["run_id"], "adopted": hold["adopted"],
                "last_reply_age_ms": round(float(age), 1) if isinstance(age, (int, float)) else None, **base}

    def execution_report(self) -> dict:
        """WHICH path to the robot this runtime is on, and WHAT actually owns the robot while it runs one.

        This exists because the two are not the same question and the second one is easy to overstate. Under the
        baseline backend the answer is plain: the single run slot of THIS process, on this computer, and nothing else.
        robot_side_enforcement is false there as a statement of fact, not as an unknown: no boundary was asked for
        anything, so nothing on the robot is refusing anybody. Under the boundary backend it is true only while this
        runtime actually holds the lease the host granted, and null otherwise, because a boundary that has answered
        nothing has established nothing."""
        held = self.lease_hold() is not None
        if self.backend != BOUNDARY_BACKEND:
            return {"backend": BASELINE_BACKEND.value, "ownership_basis": OWNERSHIP_RUNTIME_SLOT, "robot_side_enforcement": False, "detail": BASELINE_OWNERSHIP_TEXT}
        return {"backend": BOUNDARY_BACKEND.value, "ownership_basis": OWNERSHIP_HOST_LEASE, "robot_side_enforcement": True if held else None,
                "detail": BOUNDARY_HELD_TEXT if held else BOUNDARY_UNHELD_TEXT}

    def snapshot_lease(self) -> Optional[dict]:
        """The contract's owner.lease: three fields, and only while this daemon holds the lease. Otherwise null."""
        report = self.lease_report()
        if not report["held"]:
            return None
        return {"host_session": report["host_session"], "generation": report["generation"], "expires_in_ms": report["expires_in_ms"]}

    def _lease_call(self, operation: str) -> tuple:
        """One operation on the lease client, serialized and unable to raise. (the reply, whether the client still holds
        the lease afterwards). It sends JSON to a loopback socket: it constructs no robot client and sends no action."""
        with self._lease_lock:
            try:
                reply = getattr(self._lease, operation)()
            except Exception as error:  # noqa: BLE001 -- LeaseClient promises never to raise; a broken one must not stop a run either
                reply = {"ok": False, "reason": "transport_error", "error_class": type(error).__name__}
            return (reply if isinstance(reply, dict) else {"ok": False, "reason": "bad_reply"}), bool(getattr(self._lease, "held", False))

    @staticmethod
    def _lease_reason(reply: dict) -> str:
        reason = reply.get("reason")
        return reason[:64] if isinstance(reason, str) and reason else "no_reason_given"

    def _acquire_lease(self, command_type: str) -> dict:
        """Take the lease BEFORE anything is spawned. {"ok": True, "hold_id": n}, or {"ok": False, "reason_code":
        lease_unavailable}. A refusal here means the command is rejected and no controller is started at all.

        THE ONE AWKWARD CASE. A run that has just ended clears the active slot BEFORE it gives the lease back (nothing may
        sit between a dead controller and the release of the robot), so the next command can arrive while this runtime's
        own lease is still out. The client then refuses its own acquire with already_held_by_this_client. That is not a
        robot somebody else owns, so the HOST is asked: a lease it still reports as held is adopted for the new command,
        and a lease it does not have makes the client stop believing in it, after which a plain acquire works. Nothing is
        adopted on this side's belief alone: an acquire whose reply was lost would otherwise start a controller under a
        generation the host had already dropped, and every one of its commands would be discarded in silence."""
        if self._lease is None:
            with self._hold_lock:
                self.lease_last_reason = "lease_client_not_attached"
            self._say(LEASE_UNAVAILABLE, "hardware commands are enabled and this runtime has no lease client, so nothing was started", level="error")
            return {"ok": False, "reason_code": LEASE_UNAVAILABLE}
        reply, held = self._lease_call("acquire")
        adopted = False
        if not reply.get("ok") and held and reply.get("reason") == "already_held_by_this_client":
            confirmed, held = self._lease_call("status")
            if held and confirmed.get("ok") and confirmed.get("state") == "held":
                reply, adopted = confirmed, True
            else:  # the host does not have it; the client has stopped believing it does, so it can ask again
                reply, held = self._lease_call("acquire")
        if not (bool(reply.get("ok")) and held):
            reason = self._lease_reason(reply)
            with self._hold_lock:
                self._lease_hold, self.lease_last_reason = None, reason
            self._say(LEASE_UNAVAILABLE, f"the host command lease could not be taken ({reason}); nothing was started, because every command "
                                         f"of a controller without it would be dropped by the host", level="warning")
            return {"ok": False, "reason_code": LEASE_UNAVAILABLE}
        with self._hold_lock:
            self._lease_holds += 1
            self._lease_hold = {"hold_id": self._lease_holds, "generation": getattr(self._lease, "generation", None),
                                "host_session": getattr(self._lease, "host_session", None), "adopted": adopted or bool(reply.get("adopted")),
                                "run_id": None, "acquired_mono_ns": time.monotonic_ns()}
            self.lease_last_reason, hold = None, dict(self._lease_hold)
        self._say("lease_acquired", f"the runtime holds the host command lease (generation {hold['generation']})"
                                    + (", adopted from a lease it already held" if hold["adopted"] else "")
                                    + f"; the controller of this {command_type} will carry it")
        return {"ok": True, "hold_id": hold["hold_id"]}

    def _note_run_on_lease(self, run: _Run) -> None:
        with self._hold_lock:
            if self._lease_hold is not None and self._lease_hold["hold_id"] == run.lease_hold_id:
                self._lease_hold["run_id"] = run.run_id

    def _start_renewals(self, run: _Run) -> None:
        hold = self.lease_hold()
        if self._lease is None or hold is None or run.lease_hold_id is None or hold["hold_id"] != run.lease_hold_id:
            return
        self._lease_halt.clear()
        self._renewer = threading.Thread(target=self._renew_loop, args=(run,), name="host-lease-renewer", daemon=True)
        self._renewer.start()

    def _renew_loop(self, run: _Run) -> None:
        """Renew while this run is the active one. A renewal that fails is REPORTED and tried again at the next interval:
        it never ends the run, and it never releases the lease. The host extends the lease on every valid command, so a
        controller that is still commanding keeps it while this side cannot reach the control socket; releasing it here
        would be the one action that really would stop the robot in the middle of a cycle."""
        interval = max(LEASE_RENEW_MIN_S, float(getattr(self._lease, "ttl_s", LEASE_TTL_S) or LEASE_TTL_S) * LEASE_RENEW_FRACTION)
        while not self._lease_halt.wait(interval):
            with self._state_lock:
                if self._active is not run:
                    return  # the run ended; _release_lease gives the lease back, not this thread
            hold = self.lease_hold()
            if hold is None or hold["hold_id"] != run.lease_hold_id:
                return
            reply, held = self._lease_call("renew")
            if bool(reply.get("ok")) and held:
                continue
            reason = self._lease_reason(reply)
            with self._hold_lock:
                self.lease_renew_failures += 1
                lost = not held
                # The client has evidence the lease is no longer its own. Stop CLAIMING it (the snapshot must not show a
                # lease this runtime does not hold) and keep the run: the controller is still there, and what the host
                # does with its commands is the host's decision, not a reason to end a run from here.
                if lost and self._lease_hold is not None and self._lease_hold["hold_id"] == run.lease_hold_id:
                    self._lease_hold, self.lease_last_reason = None, getattr(self._lease, "lost_reason", None) or reason
            self._say("lease_renew_failed", f"renewing the host command lease failed ({reason}); the run goes on and the lease was NOT released",
                      run=run, level="warning")
            if lost:
                self._say("lease_lost", "this runtime no longer holds the host command lease; the controller keeps whatever the host still grants it, "
                                        "and the runtime stops claiming a lease it does not hold", run=run, level="error")
                return

    def _release_lease(self, why: str, hold_id: Optional[int] = None) -> None:
        """Give ONE hold back. Reached only when its run is over, or when a command never started a controller: the
        renewer never calls it, so a controller that is still commanding cannot have the lease taken from under it.

        hold_id names WHICH hold this is: the run's own. A release always arrives after its run left the active slot, so
        the next command may already hold a lease of its own by the time this runs, and giving that one back would strand
        a controller that is commanding under it. A hold_id that is not the one on record therefore releases nothing and
        does not even stop the renewer. hold_id None means 'whatever is held', which only a caller that knows no other
        hold can exist may use."""
        with self._hold_lock:
            current = self._lease_hold
            if hold_id is not None and (current is None or current["hold_id"] != hold_id):
                return  # already given back, lost on a renewal, or a later command holds a lease of its own
        self._lease_halt.set()
        renewer, self._renewer = self._renewer, None
        if renewer is not None and renewer is not threading.current_thread():
            renewer.join(timeout=5.0)
        with self._hold_lock:
            hold = self._lease_hold
            if hold is not None and hold_id is not None and hold["hold_id"] != hold_id:
                return  # it changed while the renewer was being stopped
            self._lease_hold = None
        if hold is None or self._lease is None:
            return
        reply, _held = self._lease_call("release")
        if not reply.get("ok"):
            reason = self._lease_reason(reply)
            with self._hold_lock:
                self.lease_last_reason = reason
            self._say("lease_release_failed", f"giving the host command lease back failed ({reason}); the runtime no longer claims it and the host "
                                              f"expires it by itself", run_id=hold.get("run_id"), level="warning")
            return
        self._say("lease_released", f"the runtime gave the host command lease back (generation {hold.get('generation')}, {why})", run_id=hold.get("run_id"))

    def block(self, action: Optional[str] = None, *, run_slot_gate: bool = True) -> Optional[tuple]:
        """(reason_code, detail) for why a command other than stop is refused right now, or None. The SAME function decides
        a submit and the capabilities of a snapshot.

        `run_slot_gate=False` is for a mission ONLY: the daemon's admission of a mission that can move the robot, and the
        mission's own runs, start_mission_run (its cycle) and start_mission_park (its park before a retry), both through
        _start_for_mission. It leaves out exactly one check: the gate that answers mission_running while the daemon's
        mission holds the run slot. That slot is the mission's own (held, or about to be), so the mission would otherwise
        be refused by its own hold. Every other refusal (an unresolved owner, an active run, an external controller, hardware
        commands off, the preflight) is asked exactly as a submit asks it."""
        if self.unresolved():
            return "owner_unresolved", None
        with self._state_lock:
            if self._active is not None:
                return "run_active", None
        if run_slot_gate and self._run_slot_gate is not None:
            held = self._run_slot_gate()
            if held is not None:
                return held, None
        if self._external():
            return "owner_external", None
        if self.mode == LIVE:
            if not self.hardware_enabled:
                return "hardware_commands_not_enabled", None
            if self._preflight is not None:
                report = self._preflight_report(action)
                if not report.ok:
                    failed = report.failed()[0]
                    return failed.reason_code, failed.detail
            if action is None:
                # A SNAPSHOT ONLY, and the `action is None` above is what makes that true rather than a comment saying
                # so. A submit has already asked the robot computer itself, fresh, a few lines up; letting a cached
                # reading answer for it as well would mean a start could rest on something read half a minute ago.
                not_ready = self._robot_not_ready()
                if not_ready is not None:
                    return not_ready
        return None

    def _robot_not_ready(self) -> Optional[tuple]:
        """(reason_code, sentence) when the background reading says the robot is not ready to be commanded, else None.

        An enabled software capability is not a ready robot, and this is the difference: everything above this point is a
        fact about this laptop, and a robot that is off the network or has no motor host running passes every one of them.
        The reading names WHICH of those it is, so a control is off with the real cause on it instead of looking ready and
        refusing when it is pressed. A reading nobody has taken yet, or one too old to stand for the robot now, is its own
        reason and is never read as readiness."""
        if self.readiness is None:
            return None
        reading = self.readiness.reading()
        return None if reading.ready else (reading.reason_code, reading.detail)

    def readiness_report(self) -> Optional[dict]:
        """The last reading and its age for `status`, or None when this runtime reads nothing about the robot."""
        return self.readiness.reading().to_dict() if self.readiness is not None else None

    def _preflight_report(self, action: Optional[str]):
        """Fresh for a submit (its own action text). For a snapshot (no action) a report a few seconds old is good enough:
        the preflight reads files and asks git, and a snapshot is requested often.

        THE SNAPSHOT'S REPORT NEVER ASKS THE ROBOT COMPUTER. The submit's report does, which is the moment the answer
        decides anything: the proven launcher asks the same question immediately before it commands. A snapshot is taken
        every few seconds by every open console, and an ssh that has to time out because the robot computer is off would
        stall each one of them for as long as it takes to give up. So the remote check is handed on for a submit and
        switched off for a snapshot. What a snapshot says about the robot comes from the background reading instead
        (_robot_not_ready), which is that same question asked off the request path."""
        if action is not None:
            return self._preflight(self.config, action)
        now = time.monotonic()
        if self._preflight_cache is None or now - self._preflight_cache[0] > 5.0:
            self._preflight_cache = (now, self._preflight(self.config, "observe", motor_host=None))
        return self._preflight_cache[1]

    def start_if_run_slot_free(self, start: Callable[[], dict]) -> tuple:
        """(reason_code, None) while a controller run holds the single run slot, else (None, start()).

        The other half of the run slot shared with the daemon's mission. It runs `start` UNDER THE SUBMIT LOCK, which
        every accept and spawn holds from its arbitration to the moment the run is registered, so a command and a
        mission cannot both pass their checks and both start: whichever takes the lock second sees the other. `start`
        must be short (the mission's start writes one record and starts a thread) because a submit waits for it."""
        with self._submit_lock:
            with self._state_lock:
                if self._active is not None:
                    return "run_active", None
            return None, start()

    # ------------------------------------------------------------------ submit
    def submit(self, command) -> dict:
        errors = demo_contracts.validate("command", command)
        command_id = command.get("command_id") if isinstance(command, dict) else None
        if errors:
            if isinstance(command_id, str) and not demo_contracts.validate("common#/$defs/id", command_id):
                known = self.result(command_id)
                return known or self._reject(command_id, None, "command_invalid")
            return {"ok": False, "reason_code": "command_invalid"}  # no usable command_id: there is nothing to key a result on
        if command["type"] == "stop":
            return self._submit_stop(command)
        with self._submit_lock:
            known = self.result(command_id)
            if known is not None:  # idempotent: the recorded result, never a second spawn
                return known
            # FIRST among the refusals, before the preflight's ssh: a robot whose resident session and host instance are
            # not the ones the operator confirmed must not be asked anything on behalf of a command that would move it.
            gated = self._motion_gate is not None and command["type"] in MOTION_COMMAND_TYPES
            if gated:
                refused, _admitted = self._motion_gate()
                if refused is not None:
                    return self._reject(command_id, command["type"], refused[0])
            blocked = self.block(action_text(command))
            if blocked is not None:
                return self._reject(command_id, command["type"], blocked[0])
            # The lease is taken HERE: after arbitration, before the command is accepted and long before anything is
            # spawned. A refusal ends the command as rejected with nothing started, which is the whole point.
            hold_id = None
            if lease_required(self.mode, self.hardware_enabled, command, self.backend):
                acquired = self._acquire_lease(command["type"])
                if not acquired["ok"]:
                    return self._reject(command_id, command["type"], acquired["reason_code"])
                hold_id = acquired["hold_id"]
            try:
                admitted = None
                if gated:
                    # AGAIN, FRESH, IMMEDIATELY BEFORE ADMISSION. The preflight and the lease above can take seconds of
                    # ssh; the robot's identity, session and host instance are read once more HERE, with nothing but this
                    # read between it and the accept record, and the command is journalled under what it returned.
                    refused, admitted = self._motion_gate()
                    if refused is not None:
                        if hold_id is not None:
                            self._release_lease("the robot's identity gate refused the command at admission", hold_id)
                        return self._reject(command_id, command["type"], refused[0])
                return self._accept_and_spawn(command, hold_id, robot_admission=admitted)
            except BaseException:
                # Nothing is active, so no controller exists and none ever will for this command: the lease must not be
                # left held by a runtime that is not commanding anything with it.
                with self._state_lock:
                    orphaned = self._active is None
                if orphaned and hold_id is not None:
                    self._release_lease("the command never started a controller", hold_id)
                raise

    # ------------------------------------------------------------------ a mission's run
    def start_mission_run(self, mission: MissionRun) -> dict:
        """Start the proven single-cube controller for ONE mission target, through the same admission a submit goes through.

        WHAT IT IS. The robot-side mission (demo_runtime.mission_execution.run_proven_target) carries each target by running
        the UNCHANGED controller as a supervised child, exactly as a start_cycle command does: the same allowlisted argv
        (`--do cycle`, byte for byte), the same pinned bundle, the same accept, spawning and running records, the same
        pidfd stop and the same completion record. Three things are added and nothing is taken away:
          - the mission's own fields (mission_id, target_id) on the accept record, so the command journal says which
            target a run was for;
          - the MISSION TARGET FILE, written into the run directory after that directory exists and before the spawn,
            and named to the child by KC_MISSION_TARGET (see _child_environment). The controller refuses before any
            motion when that file is missing or malformed;
          - the MISSION'S CANCEL EVENT, read inside the lock that publishes the run (_accept_and_spawn).

        THE SAME ADMISSION AS A SUBMIT, in the same order and under the same submit lock:
          - the robot's identity gate FIRST, before anything asks the robot computer anything (a mission's run is always a
            motion command, start_cycle), and AGAIN, fresh, immediately before the accept record, which journals the
            identity it admitted the run under. In a LIVE runtime a missing gate is itself a refusal: a mission's run is
            never started on the strength of nothing;
          - block(), with exactly ONE check left out: the run-slot gate that answers mission_running while the mission
            holds the slot, because that mission is the caller (see block). An unresolved owner, an active run, an
            external controller, hardware commands off and the preflight all refuse exactly as they refuse a submit;
          - the host lease, when the backend needs one, taken and given back exactly as a submit takes and gives it.

        THE STOP. The daemon sets the mission's cancel event BEFORE it calls stop() (daemon.RuntimeCore._stop_run_and_mission).
        So a stop either finds this run already published and active (and signals it, or parks the signal until the spawn,
        exactly as for a command), or it lands before publication, finds no run, and the cancel check at publication then
        refuses to publish: nothing is started either way. The cancel event is also read on entry and after each gate.

        Returns {ok, reason_code, command_id, run_id, status}. A refusal journals the command as rejected with its reason
        code and the mission's fields, exactly as a refused submit is journalled; nothing is spawned.
        """
        return self._start_for_mission(mission, "start_cycle")

    def start_mission_park(self, mission: MissionRun) -> dict:
        """THE PARK BEFORE A MISSION'S RETRY: the UNCHANGED `--do exit` (contract command return_arm, argv byte for byte the
        Return arm command's), started as a run of THIS mission through exactly the admission start_mission_run goes
        through (the identity gate twice, block() minus the mission's own run-slot gate, the host lease, the cancel read on
        entry, after each gate and inside the lock that publishes the run). demo_runtime.mission_execution.run_park is
        the caller and says when a park is asked for: only with nothing held and the base home.

        What differs from start_mission_run, and nothing else: the command type, and NO mission target file. The controller
        refuses a mission target on anything but `--do cycle` (run_single_cube_cycle.mission_refusal), so `mission.write_target`
        must be None; KC_MISSION_TARGET is then absent from the child's environment (_child_environment removes an inherited
        one). `mission.on_run_started(run_id)` is still called before the spawn, so the mission's record names the park run
        before a child that could move anything exists. The accept record carries the mission's fields.

        Returns start_mission_run's shape {ok, reason_code, command_id, run_id, status}."""
        if mission.write_target is not None:
            raise ValueError("a mission's park carries no mission target file: the controller refuses one for anything but --do cycle")
        return self._start_for_mission(mission, "return_arm", retry_gate=True)

    def mission_retry_refusal(self) -> Optional[str]:
        """Why this runtime refuses a mission's Retry and the park before it, or None when it admits them. A LIVE runtime
        admits them only when its configuration sets mission.retry AND names the attended label (placement_envelope_
        attended): the park and the fresh cycle after it are motion no qualification record covers, and the attended label
        is how a LIVE run is let past that, exactly as for an unqualified envelope word. Any other mode admits them."""
        if self.mode != LIVE:
            return None
        section = getattr(self.config, "mission", None) if self.config is not None else None
        if section is None or getattr(section, "retry", False) is not True:
            return MISSION_RETRY_NOT_ENABLED
        if getattr(self.config, "placement_envelope_attended", None) is None:
            return MISSION_RETRY_NOT_ATTENDED
        return None

    def _start_for_mission(self, mission: MissionRun, command_type: str, *, retry_gate: bool = False) -> dict:
        """start_mission_run's admission and spawn for one command type of a mission (start_cycle or return_arm). With
        `retry_gate` (the park before a Retry) mission_retry_refusal is asked first, before any other gate."""
        command_id = "m" + uuid_module.uuid4().hex[:15]
        command = {"contract_version": CONTRACT_VERSION, "command_id": command_id, "type": command_type, "args": {"plan_only": False},
                   "client": {"session_id": self.session, "contract_version": CONTRACT_VERSION}}
        if command_type not in ("start_cycle", "return_arm") or demo_contracts.validate("command", command):
            raise ValueError(f"a mission's run command must be a valid start_cycle or return_arm command, not {command_type!r}")
        fields = {"mission": mission.fields()}

        def answer(state: dict) -> dict:
            return {"ok": state.get("status") not in ("rejected", None) and state.get("reason_code") is None,
                    "reason_code": state.get("reason_code"), "command_id": command_id, "run_id": state.get("run_id"),
                    "status": state.get("status")}

        def refused(reason_code: str) -> dict:
            return answer(self._reject(command_id, command_type, reason_code, **fields))

        with self._submit_lock:
            if retry_gate:
                retry_refused = self.mission_retry_refusal()
                if retry_refused is not None:
                    return refused(retry_refused)
            if self._detached.is_set():
                return refused(MISSION_RUNTIME_DETACHED)
            if mission.cancel.is_set():
                return refused(MISSION_STOPPING)
            if self._motion_gate is None and self.mode == LIVE:
                return refused(MISSION_MOTION_GATE_ABSENT)
            if self._motion_gate is not None:
                refusal, _admitted = self._motion_gate()
                if refusal is not None:
                    return refused(refusal[0])
            blocked = self.block(action_text(command), run_slot_gate=False)
            if blocked is not None:
                return refused(blocked[0])
            if mission.cancel.is_set():
                return refused(MISSION_STOPPING)
            hold_id = None
            if lease_required(self.mode, self.hardware_enabled, command, self.backend):
                acquired = self._acquire_lease(command["type"])
                if not acquired["ok"]:
                    return refused(acquired["reason_code"])
                hold_id = acquired["hold_id"]
            try:
                admitted = None
                if self._motion_gate is not None:
                    refusal, admitted = self._motion_gate()
                    if refusal is not None:
                        if hold_id is not None:
                            self._release_lease("the robot's identity gate refused the mission's run at admission", hold_id)
                        return refused(refusal[0])
                state = self._accept_and_spawn(command, hold_id, robot_admission=admitted, mission=mission)
            except BaseException:
                with self._state_lock:
                    orphaned = self._active is None
                if orphaned and hold_id is not None:
                    self._release_lease("the mission's run never started a controller", hold_id)
                raise
        with self._state_lock:
            recorded = dict(self._table.get(command_id) or state)
        return answer(recorded)

    @property
    def detached(self) -> bool:
        """Whether this supervisor let go of its runs (the daemon's own shutdown): nothing it follows will end here."""
        return self._detached.is_set()

    def command_state(self, command_id) -> Optional[dict]:
        """A copy of one command's folded state (its status, run id and, once it ended, its completion record), or None."""
        with self._state_lock:
            state = self._table.get(command_id) if isinstance(command_id, str) else None
            return dict(state) if state else None

    def _reject(self, command_id: str, command_type: Optional[str], reason_code: str, **fields) -> dict:
        state = self._record(command_id, status="rejected", reason_code=reason_code, command_type=command_type, **fields)
        self._say(f"command_rejected.{reason_code}"[:64], f"command rejected ({command_type or 'not a valid command'})", level="warning")
        return command_result(state)

    def _submit_stop(self, command: dict) -> dict:
        with self._stop_lock:
            known = self.result(command["command_id"])
            if known is not None:
                return known
            stopped = self.stop(origin="operator_console", command_id=command["command_id"])
            state = self._record(command["command_id"], status="finished", reason_code=None if stopped else "no_active_run", run_id=stopped["run_id"] if stopped else None, command_type="stop")
            return command_result(state)

    def _accept_and_spawn(self, command: dict, lease_hold_id: Optional[int] = None, robot_admission: Optional[dict] = None,
                          mission: Optional[MissionRun] = None) -> dict:
        command_id, command_type = command["command_id"], command["type"]
        run_id = scan_id_for(command_id)
        suffix = 1
        while (self.data_dir / HISTORY_DIR / run_id).exists():  # same second, same first eight characters: keep the id unique
            suffix += 1
            run_id = f"{scan_id_for(command_id)}-{suffix}"
        run = _Run(run_id=run_id, command_id=command_id, command_type=command_type, run_dir=self.data_dir / HISTORY_DIR / run_id, lease_hold_id=lease_hold_id)
        run.mission = mission
        run.robot_admission = dict(robot_admission) if isinstance(robot_admission, dict) else None  # the child's environment names its session
        # The run becomes the ACTIVE one before the accept record is written, not after. stop() only ever looks at the
        # active run, and writing that record means a write plus an fsync: a stop that arrived inside that window found no
        # run at all, answered no_active_run and was dropped, while the same command went on to spawn and place the cube.
        # MEASURED, not reasoned: with the accept append held open, stop() returned None and the run finished placed_new.
        # Publishing first makes the window empty, and the spawn path already refuses to start a child whose stop is
        # pending. A journal append that fails must still not leave the robot held, so the publication is undone then and
        # the command ends exactly as it did before.
        #
        # A MISSION'S RUN IS PUBLISHED ONLY WHILE ITS MISSION IS NOT STOPPING, decided INSIDE this lock. The daemon's stop
        # sets the mission's cancel event before it calls stop(), and stop() reads _active under this same lock, so the two
        # orders are the only two: the stop comes first, finds nothing active, and this check refuses to publish; or this
        # publication comes first, and the stop finds the run and signals it (or parks the signal for the spawn). There is
        # no third order in which a stop is answered and a run it did not see is started afterwards.
        # The same lock decides a DETACHED supervisor for a mission run: detach() may land between the entry check and
        # here, and a run published after it would be started by a supervisor that has already let go of its runs.
        stopped_at_publication = detached_at_publication = False
        with self._state_lock:
            if mission is not None and self._detached.is_set():
                detached_at_publication = True
            elif mission is not None and mission.cancel.is_set():
                stopped_at_publication = True
            else:
                self._active = run
        if stopped_at_publication or detached_at_publication:
            if lease_hold_id is not None:
                self._release_lease("the mission's run was not published", lease_hold_id)
            return self._reject(command_id, command_type, MISSION_RUNTIME_DETACHED if detached_at_publication else MISSION_STOPPED_AT_PUBLICATION,
                                mission=mission.fields())
        self._note_run_on_lease(run)  # a lease taken for this command now names the run it was taken for
        try:
            # A motion command admitted through the robot's identity gate carries the identity it was admitted under:
            # the robot resident's session (which, with the connection it was read over, says which deployment), its
            # build identity (which software) and the supervisor host instance, as read just now.
            admission = {} if robot_admission is None else {"robot_admission": robot_admission}
            if mission is not None:  # the mission and the target this run is for, on the record before anything is spawned
                admission["mission"] = mission.fields()
            self._record(command_id, status="accepted", reason_code=None, run_id=run_id, scan_id=run_id, command_type=command_type, command=command,
                         **admission)  # fsynced BEFORE any spawn
        except BaseException:
            with self._state_lock:
                if self._active is run:
                    self._active = None
            raise
        self._say("command_accepted", f"command accepted ({command_type})", run=run)
        self._say(f"owner.{'autonomous' if command_type in AUTONOMOUS_TYPES else 'manual'}", "the runtime holds the robot for this run", run=run)
        self._fault("accepted")
        self._record(command_id, status="spawning")
        self._fault("spawning")
        # INVARIANT: _never_started (which releases the robot) is reachable ONLY while no child exists. _start_child ends at
        # Popen and records the child's handle as its very next statement; whatever raises, run.proc decides which side we are on.
        # A first build got this wrong: a missing os.pidfd_open raised AFTER the spawn, the run was recorded as "never
        # started" and the owner was released while the controller ran a whole cycle.
        try:
            self._start_child(run, command)
        except _StoppedBeforeSpawn:
            return self._never_started(run, "stopped_before_spawn", "stopped", "a stop arrived before the controller was started, so it never was")
        except _MissionTargetRefused:
            if command_type == "return_arm":  # a mission's park carries no target file: only naming the run on the mission's record can refuse it
                return self._never_started(run, MISSION_TARGET_REFUSED, "refused", "the mission could not name its park run on its record, so the controller was never started")
            return self._never_started(run, MISSION_TARGET_REFUSED, "refused", "the mission target file could not be written into the run directory, so the controller was never started")
        except Exception as error:  # noqa: BLE001 -- whatever stopped the spawn, the command must end in a recorded state
            if run.proc is None:
                return self._never_started(run, "spawn_failed", "failed", f"the controller could not be started ({type(error).__name__})")
            self._gap(run, "other", f"the controller was started and a step right after the spawn failed ({type(error).__name__}); the run is followed")
        try:
            self._identify_child(run)
            self._after_spawn(run)
        except Exception as error:  # noqa: BLE001 -- a child EXISTS: a failure here is a gap in the record, never a reason to let go of the run
            self._gap(run, "other", f"the controller is running and its identity could not be fully recorded ({type(error).__name__}); the run is followed")
            self._say("child_identity_incomplete", "the controller is running; recording its identity failed, so a restart would find it by its scan id only", run=run, level="error")
        finally:  # a child exists from here on: whatever else fails, a parked stop is delivered and the child is followed and waited for
            with self._state_lock:
                if run.stop_pending and run.stop_signal_mono_ns is None:
                    self._signal_locked(run)
            self._follow(run, offset=0)
            self._start_renewals(run)  # only now: a lease is renewed while a CONTROLLER lives, not while one is being started
        with self._state_lock:
            state = dict(self._table[command_id])
        return command_result(state)

    def _never_started(self, run: _Run, reason_code: str, outcome: str, text: str) -> dict:
        self._say(reason_code, text, run=run, level="warning" if outcome == "stopped" else "error")
        self._gap(run, "other", "the controller was never started, so the run has no controller record")
        if run.stop_requested_mono_ns is not None:
            self._emit("stop.outcome", {"outcome": "ceased_unverified", "motion_ceased": None, "evidence": "the stop arrived before the controller was started; no controller ran for this command"},
                       run=run, reason_code="stop_before_spawn")
        payload = {"outcome": outcome, "exit_status": None, "predicates": {"object_secured": None, "placed": None, "clearance_target_met": None, "base_returned": None},
                   "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": False, "evidence_gaps": run.daemon_gaps}
        finished = self._emit("run.finished", payload, run=run, reason_code=reason_code)
        completion = self._completion(run, payload, None, None, finished)
        state = self._record(run.command_id, status="finished", reason_code=reason_code, completion=completion)
        self._release(run, None)
        return command_result(state)

    def _child_environment(self, run: _Run, bundle: bundle_module.Bundle) -> dict:
        environment = {key: value for key, value in os.environ.items() if key != FAULT_ENV_VAR}
        environment.update(self.extra_env)
        # The pin: the child imports demo_runtime and demo_contracts from the bundle, never from the checkout the daemon runs in.
        environment["PYTHONPATH"] = os.pathsep.join([str(bundle.root / "packages" / "demo-runtime"), str(bundle.root / "packages" / "demo-contracts" / "python")])
        environment.update({"KC_RUNTIME_CONFIG": self.config_path or "", "KC_EVENTS_PATH": str(run.events_path), "KC_RUN_ID": run.run_id,
                            "KC_ARTIFACT_DIGEST": bundle.digest, "PYTHONUNBUFFERED": "1"})
        if self.mode == LIVE:  # the variables today's capture code reads, taken from the ONE config instead of the operator's shell
            environment["KC_PI_SSH"] = self.config.pi_ssh_target
            environment["KC_WRIST_CAMERA_ID"] = self.config.cameras.wrist_id
            if self.config.cameras.front_id:
                environment["KC_FRONT_CAMERA_ID"] = self.config.cameras.front_id
            else:
                environment.pop("KC_FRONT_CAMERA_ID", None)
        # ON THE ROBOT (cake.robot.link local): the controller asks the camera service on loopback and reads its frames
        # from the store on this machine (run_stage1_scan.ServiceCamera, local_fetch), because there is no other computer
        # to forward from or copy over. Removed first and without a condition, like every variable a child must not
        # inherit, so a desktop runtime never passes it on from its shell.
        environment.pop(CAMERA_SERVICE_LOCAL_ENV, None)
        if self.mode == LIVE and robot_link_is_local(self.config):
            environment[CAMERA_SERVICE_LOCAL_ENV] = "1"
            environment["KC_MISSION_LOCAL_PORT"] = str(CAMERA_SERVICE_MISSION_PORT)
        # The lease this runtime holds, for this run, and nothing else. The two variables are REMOVED first and without a
        # condition: they are inherited from whatever shell started the daemon, and a controller that read a stale pair
        # would attach a generation the host rejects, which looks exactly like a robot that is not listening.
        environment.pop(LEASE_ENV_GENERATION, None)
        environment.pop(LEASE_ENV_SESSION, None)
        # The mission target, for a mission's run only. Removed first for the same reason as the lease pair: a stale value
        # inherited from the daemon's shell would make a legacy command a mission's run, or name another run's file.
        environment.pop(MISSION_TARGET_ENV, None)
        if run.mission_target_path is not None:
            environment[MISSION_TARGET_ENV] = run.mission_target_path
        # THE DESKTOP PERCEPTION (the four names above): only a LIVE runtime ON THE ROBOT that configured it hands it on. A
        # desktop runtime never does, whatever its configuration says: the reverse forward's port exists on the robot only.
        for name in PERCEPTION_ENV_NAMES:
            environment.pop(name, None)
        remote = perception_remote_of(self.config) if self.mode == LIVE and robot_link_is_local(self.config) else None
        if remote is not None:
            environment.update({PERCEPTION_PORT_ENV: str(remote.port), PERCEPTION_DEADLINE_ENV: repr(float(remote.deadline_s)),
                                PERCEPTION_MODEL_REVISION_ENV: remote.model_revision, PERCEPTION_MODEL_FILES_ENV: model_files_digest(remote.model_files_sha256)})
        environment.pop(RESIDENT_SESSION_ENV, None)
        session = (run.robot_admission or {}).get("session_uuid") if self.mode == LIVE else None
        if isinstance(session, str) and session:
            environment[RESIDENT_SESSION_ENV] = session
        environment.pop(RUNTIME_MODE_ENV, None)
        environment[RUNTIME_MODE_ENV] = self.mode
        # The placement envelope: removed first and without a condition, set only from the config, and only when it names a word.
        environment.pop(PLACEMENT_ENVELOPE_ENV, None)
        environment.pop(PLACEMENT_ENVELOPE_ATTENDED_ENV, None)
        environment.pop(PLACEMENT_SIDE_OFFSET_FORCE_ENV, None)
        if self.config is not None and self.config.placement_envelope:
            environment[PLACEMENT_ENVELOPE_ENV] = ",".join(self.config.placement_envelope)
            if self.config.placement_envelope_attended is not None:
                environment[PLACEMENT_ENVELOPE_ATTENDED_ENV] = self.config.placement_envelope_attended
            if self.config.placement_side_offset_force_deg is not None:
                environment[PLACEMENT_SIDE_OFFSET_FORCE_ENV] = repr(float(self.config.placement_side_offset_force_deg))
        hold = self.lease_hold()
        if hold is not None and hold.get("generation") is not None and hold.get("host_session") is not None:
            environment[LEASE_ENV_GENERATION] = str(hold["generation"])
            environment[LEASE_ENV_SESSION] = str(hold["host_session"])
        return environment

    def _start_child(self, run: _Run, command: dict) -> None:
        bundle = bundle_module.build_bundle(self.source_root, self.data_dir, self.controller_entry)
        run.bundle_digest = bundle.digest
        run.run_dir.mkdir(mode=0o700, parents=True)
        manifest = bundle_module.run_manifest(bundle=bundle, source_root=self.source_root, contract_version=CONTRACT_VERSION, config=self.config, run_id=run.run_id,
                                              command_id=run.command_id, command_type=run.command_type, data_mode=self.mode)
        (run.run_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        if run.mission is not None:
            # THE MISSION TARGET FILE, written now that the run directory exists and before anything is spawned. The
            # manifest is contract v1's (manifest.schema.json refuses a property it does not list), so the mission's fields
            # are not added to it: this file, beside it in the same run directory, is the run's statement of which mission
            # and which target it carries, and the accept record says the same in the journal. A file that cannot be
            # written, or a callable that refuses the document, ends the run as never started.
            # A mission's PARK (start_mission_park, command return_arm) writes no target file and names none to the child;
            # its run is still named on the mission's record before the spawn.
            try:
                if run.mission.write_target is not None or run.command_type != "return_arm":
                    path = run.mission.write_target(run.run_dir, run.run_id)
                    if not isinstance(path, str) or not os.path.isabs(path) or not os.path.isfile(path):
                        raise ValueError("the mission target writer named no file")
                    run.mission_target_path = path
                if run.mission.on_run_started is not None:
                    run.mission.on_run_started(run.run_id)
            except Exception:  # noqa: BLE001 -- whatever refused the file, no child exists yet and none will for this run
                raise _MissionTargetRefused() from None
        if self.config is not None:
            address, robot_id, pi_ssh = self.config.host.address, self.config.host.robot_id, self.config.pi_ssh_target
            # The provider the CONFIGURATION names, carried to the controller rather than left as a number in a digest.
            # A configuration that names the legacy path passes nothing and the argv is the proven one.
            provider, mission_port = self.config.capture_provider.value, CAMERA_SERVICE_MISSION_PORT
        else:  # synthetic without a config: reserved documentation values, only ever read by the fake controller
            address, robot_id, pi_ssh = "192.0.2.1", "synthetic-robot", ""
            provider, mission_port = None, None
        argv = [self.python, str(bundle.root / self.controller_entry),
                *controller_arguments(command, run.run_id, address=address, robot_id=robot_id, pi_ssh=pi_ssh,
                                      capture_provider=provider, mission_port=mission_port)]
        started = self._emit("run.started", {"command_id": run.command_id, "command_type": run.command_type, "data_mode": self.mode,
                                             "plan_only": command["args"].get("plan_only") is True, "holding": command["args"].get("holding")}, run=run)
        run.started_seq = started["seq"] if started else 0
        with self._state_lock:
            if run.stop_pending:  # a stop arrived between accept and spawn: the controller is never started
                raise _StoppedBeforeSpawn()
        # stdout and stderr go to FILES: with pipes a dead daemon would break the controller on its next print.
        environment = self._child_environment(run, bundle)  # built BEFORE the spawn: nothing that can raise sits between Popen and the handle
        with open(run.run_dir / STDOUT_FILE, "ab") as out, open(run.run_dir / STDERR_FILE, "ab") as err:
            proc = subprocess.Popen(argv, cwd=str(bundle.root), env=environment, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True, close_fds=True)
            run.proc, run.pid = proc, proc.pid  # the child exists from this statement on, and the run knows it
        self.launches += 1

    def _identify_child(self, run: _Run) -> None:
        """The pidfd and the start time of our own child. pidfd_open() never raises: without a pidfd the run is still stopped
        (through its Popen handle, PID only) and still waited for."""
        pidfd = pidfd_open(run.pid)  # our own unreaped child: the pid cannot have been reused yet
        stat = proc_stat(run.pid, self.proc_root)
        with self._state_lock:
            run.pidfd, run.start_ticks = pidfd, stat[1] if stat else None
            if run.stop_pending:
                self._signal_locked(run)

    def _after_spawn(self, run: _Run) -> None:
        self._fault("spawned")
        self._record(run.command_id, status="running", child={"pid": run.pid, "start_ticks": run.start_ticks, "scan_id": run.run_id}, adopted=False, bundle_digest=run.bundle_digest)
        with self._state_lock:
            run.status = "stopping" if run.stop_requested_mono_ns is not None else "running"
        self._say("controller_spawned", "the controller process was started from the pinned bundle and its identity is recorded", run=run)
        self._fault("running")

    def _follow(self, run: _Run, *, offset: int) -> None:
        run.tailer = Tailer(run.events_path, offset, lambda line, end: self._ingest(run, line, end), lambda end: self._gap(run, "controller_output_unparsed", "a line of the controller's event file is longer than the runtime accepts", origin={"run_id": run.run_id, "source_session": None, "seq": None, "end_offset": end}),
                            self.log.sync, self.tail_interval_s)
        run.tailer.start()
        threading.Thread(target=self._wait, args=(run,), name="controller-waiter", daemon=True).start()

    # ------------------------------------------------------------------ the controller's events
    def _ingest(self, run: _Run, line: bytes, end_offset: int) -> None:
        origin = {"run_id": run.run_id, "source_session": None, "seq": None, "end_offset": end_offset}
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            return self._gap(run, "controller_output_unparsed", "a line of the controller's event file is not a JSON object", origin=origin)
        source_seq, source_session = record.get("seq"), record.get("source_session")
        origin["seq"] = source_seq if isinstance(source_seq, int) and not isinstance(source_seq, bool) else None
        origin["source_session"] = source_session if isinstance(source_session, str) and len(source_session) <= 64 else None
        if origin["seq"] is not None:
            if run.last_source_seq is not None and origin["seq"] != run.last_source_seq + 1:
                self._gap(run, "other", f"the controller's own sequence went from {run.last_source_seq} to {origin['seq']}")
            run.last_source_seq = origin["seq"]
        if record.get("run_id") is None:
            record["run_id"] = run.run_id
        elif record["run_id"] != run.run_id:
            return self._gap(run, "other", "a record of the controller's event file names another run and was not merged", origin=origin)
        changed = False
        if self.sanitizer is not None:
            record, changed = self.sanitizer.scrub(record, generic=record.get("kind") == "diagnostic.text")
        kind = record.get("kind")
        if changed and kind == "diagnostic.text" and isinstance(record.get("payload"), dict):
            record["payload"]["redacted"] = True
        if kind == "run.started":  # the runtime owns the run's identity and already said run.started; the controller's copy is folded
            run.controller_run_started = True
            return None
        if kind == "run.finished":  # held until the process is gone: a run is over when its process is, not when it says so
            run.controller_finished, run.controller_finished_origin = record, origin
            return None
        if kind == "state.transition" and isinstance(record.get("payload"), dict) and isinstance(record["payload"].get("to_state"), str):
            with self._state_lock:
                run.state = record["payload"]["to_state"]
        if kind == "stop.acknowledged" and run.ack_mono_ns is None:
            # WHEN it was acknowledged is the controller's own timestamp, not the moment the tailer got round to reading the
            # line (that can be after the process has exited). Both processes read the same CLOCK_MONOTONIC when the event says
            # laptop_monotonic; any other clock domain cannot be subtracted from ours, so the observed time is used and LABELLED.
            stamped = record.get("t_mono_ns")
            same_clock = record.get("clock_domain") == "laptop_monotonic" and isinstance(stamped, int) and not isinstance(stamped, bool)
            if same_clock and run.stop_requested_mono_ns is not None and stamped >= run.stop_requested_mono_ns:
                run.ack_mono_ns, run.ack_basis = stamped, "controller_event_clock"
            else:
                run.ack_mono_ns, run.ack_basis = time.monotonic_ns(), "runtime_observed"
            run.ack_source = "event"
        try:
            self.log.append(record, origin=origin, sync=False)
        except InvalidEvent as invalid:
            where = invalid.errors[0].split(":", 1)[0] if invalid.errors else "$"  # the PATH of the first error only: the rest can quote a value
            shown = kind if kind in _EVENT_KINDS else "unknown kind"
            self._gap(run, "controller_output_unparsed", f"a controller record ({shown}) failed the contract at {where[:80]} and was not merged", origin=origin)
        return None

    # ------------------------------------------------------------------ stop: the priority path
    def _signal_locked(self, run: _Run) -> None:
        """SIGINT to the controller PID ONLY. Called with the state lock held (a system call, not a wait). In order of preference:
        the pidfd (immune to pid reuse); for our OWN child the Popen handle (an unreaped child's pid cannot be reused, and Popen
        refuses to signal a child it already reaped); for an ADOPTED process without a pidfd, the pid after its identity (start
        time and scan id) was checked again. That last path has a residual window between the check and the signal and is
        named as such in the completion record (stop.mechanism = pid_verified). Never the process group, never another signal."""
        try:
            if run.pidfd is not None:
                pidfd_sigint(run.pidfd)
                run.stop_mechanism = "pidfd"
            elif run.proc is not None:
                run.proc.send_signal(signal.SIGINT)
                run.stop_mechanism = "own_child_handle"
            else:
                stat = proc_stat(run.pid, self.proc_root)
                if stat is None or stat[0] in ("Z", "X") or (run.start_ticks is not None and stat[1] != run.start_ticks) or not is_our_controller(run.pid, run.run_id, run.events_path, self.proc_root):
                    raise ProcessLookupError()  # not provably our controller any more: signalling it could hit a stranger
                os.kill(run.pid, signal.SIGINT)
                run.stop_mechanism = "pid_verified"
        except ProcessLookupError:
            pass  # already gone: the waiter reports the exit
        except Exception:  # noqa: BLE001 -- a stop that could not be delivered is RECORDED as failed, never swallowed as sent
            run.stop_signal_failed = True
        now = time.monotonic_ns()
        run.stop_signals_sent, run.stop_last_signal_mono_ns = run.stop_signals_sent + 1, now
        if run.stop_signal_mono_ns is None:
            run.stop_signal_mono_ns = now  # latencies are measured from the FIRST signal

    def stop(self, origin: str = "operator_console", command_id: Optional[str] = None) -> Optional[dict]:
        requested = time.monotonic_ns()
        with self._state_lock:
            run = self._active
            first = False
            if run is not None:
                first = run.stop_requested_mono_ns is None
                if first:
                    run.stop_requested_mono_ns, run.stop_origin, run.stop_command_id = requested, origin, command_id
                    run.status = "stopping"
                if run.pid is not None:  # a child EXISTS (with or without a pidfd): the signal goes out now, BEFORE anything is logged
                    self._signal_locked(run)
                else:  # accepted, not spawned yet: the spawn path sees this flag and either never starts the child or signals it at once
                    run.stop_pending = True
                answer = {"requested_mono_ns": run.stop_requested_mono_ns, "run_id": run.run_id}
        if run is None:
            self._emit("stop.requested", {"origin": origin, "command_id": command_id})
            self._emit("stop.outcome", {"outcome": "no_active_run", "motion_ceased": None, "evidence": "no run was active when the stop was requested"}, reason_code="no_active_run")
            return None
        if first:
            self._emit("stop.requested", {"origin": origin, "command_id": command_id}, run=run)
        return answer

    # ------------------------------------------------------------------ the end of a run
    def _gone(self, run: _Run, turn_s: float = 0.2) -> bool:
        """One bounded look (about 0.2 s) at whether the controller's process has ended. Three ways, best first; none of them
        may raise, because a waiter that dies leaves a run that never ends: owner held forever, no run.finished."""
        if run.pidfd is not None:
            try:
                return bool(select.select([run.pidfd], [], [], turn_s)[0])
            except (OSError, ValueError, TypeError):
                pass  # the descriptor is unusable: fall through to the other ways instead of giving up on the run
        if run.proc is not None:
            try:
                run.proc.wait(timeout=turn_s)
                return True
            except subprocess.TimeoutExpired:
                return False
        time.sleep(turn_s)
        stat = proc_stat(run.pid, self.proc_root)
        return stat is None or stat[0] in ("Z", "X") or (run.start_ticks is not None and stat[1] != run.start_ticks)

    def _repeat_unacknowledged_stop(self, run: _Run) -> None:
        """MEASURED: a SIGINT sent in the first milliseconds of a controller's life can be LOST: about 1 in 360 under load, the
        run then went on to place the cube with a stop on record. The mechanism was not established (it is not only the window
        between fork and exec: a repeat tied to the exec did not catch it), so the remedy does not depend on one. While a stop
        is requested, the process is alive and NOTHING has acknowledged it, the SAME signal goes to the SAME PID again, slowly
        and a bounded number of times. This is a repeat, not an escalation: a controller that ignores SIGINT ignores the
        repeats as well and is still never sent anything stronger. The real controller's handler only sets a flag, so a repeat
        that arrives after an acknowledgement nobody has read yet changes nothing."""
        with self._state_lock:
            if (run.stop_requested_mono_ns is None or run.stop_signal_failed or run.ack_mono_ns is not None or run.pid is None or run.stop_last_signal_mono_ns is None
                    or run.stop_signals_sent > STOP_REPEAT_LIMIT or time.monotonic_ns() - run.stop_last_signal_mono_ns < STOP_REPEAT_INTERVAL_NS):
                return
            first_repeat = run.stop_signals_sent == 1
            self._signal_locked(run)
        if first_repeat:
            self._say("stop_repeated", "the stop was not acknowledged yet and the controller is still running: the same signal is being repeated", run=run, level="warning")

    def _wait(self, run: _Run) -> None:
        while not self._detached.is_set():
            self._repeat_unacknowledged_stop(run)
            if self._gone(run):
                break
        else:
            return  # detached on purpose (daemon shutdown): the controller runs on and the next daemon adopts it
        exit_status = None
        if run.proc is not None:
            exit_status = run.proc.wait()
        elif run.pidfd is not None:
            try:  # works only when the process is a child of THIS process; an adopted one is not, and its status is unavailable
                info = os.waitid(os.P_PIDFD, run.pidfd, os.WEXITED | os.WNOHANG)
                exit_status = None if info is None else -info.si_status if info.si_code in (os.CLD_KILLED, os.CLD_DUMPED) else info.si_status
            except (ChildProcessError, OSError, AttributeError):
                exit_status = None
        try:
            self._finalize(run, exit_status, time.monotonic_ns())
        except Exception as error:  # noqa: BLE001 -- the process IS gone, but how the run ended could not be recorded
            # Not knowing how a run ended is exactly what 'uncertain' means: the owner becomes unresolved and an operator clears
            # it. Only when even THAT cannot be written is the run simply let go, so that a dead process never holds the robot.
            # EVERY announcement below is best effort and NONE of them may stop the release. This block used to say the words
            # first and release afterwards, and the words go through the very event log whose failure caused the finalize to
            # fail: MEASURED, with the log refusing writes, _say raised, this thread died, and a controller that had already
            # exited kept the robot for the life of the daemon (owner autonomous, every command refused run_active, nothing
            # for an operator to resolve because the uncertain record was never written either). The release is in a finally.
            try:
                self._say("finalize_failed", f"recording the end of the run failed ({type(error).__name__})", run_id=run.run_id, level="error")
                self._gap(None, "record_truncated", "the end of a run could not be fully recorded", run_id=run.run_id)
                self._record(run.command_id, status="uncertain", reason_code="finalize_failed")
            except Exception:  # noqa: BLE001 -- whatever broke the finalize usually breaks the record of it as well
                pass
            finally:
                self._release(run, exit_status)

    def _terminal_line_says_stopped(self, run: _Run) -> bool:
        try:
            with open(run.run_dir / STDOUT_FILE, "rb") as handle:
                handle.seek(max(0, os.fstat(handle.fileno()).st_size - 4096))
                lines = handle.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return False
        return any(line.startswith("STOPPED:") for line in lines[-5:])

    def _died_of_keyboard_interrupt(self, run: _Run) -> bool:
        """The controller's error output ends in the interpreter's own KeyboardInterrupt traceback. Measured, not assumed: a
        SIGINT that lands AFTER Python installed its default handler and BEFORE the controller installed its own (while it is
        still importing, which takes the real controller seconds) raises KeyboardInterrupt inside an import and the process
        exits with status 1, not with the signal. Without this check a stop that worked reads as a crash.
        Two shapes were measured. A traceback whose last line is `KeyboardInterrupt`; and, when the signal lands while the
        interpreter imports `site`, "Fatal Python error: init_import_site", then the `KeyboardInterrupt` line, then a thread
        dump AFTER it. So the test is a line that IS the exception line, anywhere in the tail. A source line that merely
        mentions the name (`    except KeyboardInterrupt:`) is indented code and does not match."""
        try:
            with open(run.run_dir / STDERR_FILE, "rb") as handle:
                handle.seek(max(0, os.fstat(handle.fileno()).st_size - 4096))
                lines = handle.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return False
        return any(line == "KeyboardInterrupt" or line.startswith("KeyboardInterrupt:") for line in lines)

    def _classify_stop(self, run: _Run, exit_status: Optional[int], outcome: str) -> str:
        if run.ack_mono_ns is not None:
            return "acknowledged"
        if exit_status == STOP_EXIT_STATUS or self._terminal_line_says_stopped(run):
            run.ack_source = "exit_status_or_terminal_line"
            return "acknowledged"
        if exit_status == 0 or outcome in ("placed_new", "already_placed_noop"):
            return "completed_anyway"
        if exit_status in (-signal.SIGINT, 128 + signal.SIGINT):
            run.pre_handler_evidence = "killed_by_sigint"
            return "pre_handler_exit"
        if run.stop_signal_mono_ns is not None and not run.stop_signal_failed and self._died_of_keyboard_interrupt(run):
            run.pre_handler_evidence = "keyboard_interrupt_traceback"  # our SIGINT, taken by the interpreter's default handler
            return "pre_handler_exit"
        if exit_status is None:
            return "exit_unobserved"
        return "crash"

    def _finalize(self, run: _Run, exit_status: Optional[int], exit_mono_ns: int) -> None:
        with self._state_lock:
            if run.finalized:
                return
            run.finalized = True
        if run.tailer is not None:
            if run.tailer.is_alive():
                run.tailer.finish()
                run.tailer.join(timeout=15.0)
            else:
                run.tailer.drain()
            if run.tailer.partial.strip():
                self._gap(run, "record_truncated", "the controller's event file ends in an incomplete line")
        controller = run.controller_finished or scan_run_finished(run.events_path)
        if controller is None:
            self._gap(run, "record_truncated", "the controller ended without a run.finished record")
            # An operator's stop that ended the process by SIGINT before the controller installed its handler IS a stop: the
            # process ended because it was asked to. 'failed' would show a working stop as a failure. How thin the evidence is
            # (no acknowledgement, no record of what the robot was doing) is carried by the stop classification and the gaps.
            # The rule is the stop CLASSIFICATION (one place decides what counts as "ended by the requested stop"), not a list
            # of exit statuses: the same stop ends a Python process with -2, 130 or 1 depending on the millisecond it lands in.
            provisional = EXIT_OUTCOMES.get(exit_status, "unknown" if exit_status in (0, None) else "failed")
            ended_by_requested_stop = run.stop_requested_mono_ns is not None and self._classify_stop(run, exit_status, provisional) == "pre_handler_exit"
            payload = {"outcome": "stopped" if ended_by_requested_stop else provisional, "exit_status": exit_status,
                       "predicates": {"object_secured": None, "placed": None, "clearance_target_met": None, "base_returned": None},
                       "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": False, "evidence_gaps": run.daemon_gaps}
        else:
            payload = dict(controller["payload"])
            gaps = int(payload.get("evidence_gaps") or 0) + run.daemon_gaps
            payload.update({"exit_status": exit_status, "evidence_gaps": gaps, "evidence_complete": bool(payload.get("evidence_complete")) and gaps == 0})
        stop_record = None
        if run.stop_requested_mono_ns is not None:
            classification = self._classify_stop(run, exit_status, payload.get("outcome"))
            ack_latency = None if run.ack_mono_ns is None else max(0, run.ack_mono_ns - run.stop_requested_mono_ns)
            exit_latency = max(0, exit_mono_ns - run.stop_requested_mono_ns)
            if classification == "acknowledged" and run.ack_source != "event":  # the controller said it in its last line or its exit status, not in an event
                self._emit("stop.acknowledged", {"acknowledged_by": "cycle-controller", "command_id": run.stop_command_id, "latency_ns": exit_latency}, run=run, reason_code="inferred_from_exit")
            evidence = {
                "acknowledged": "the controller acknowledged the stop and its process ended; nothing measured whether motion ceased",
                "completed_anyway": "the controller finished its run without acknowledging the stop; its process ended; nothing measured whether motion ceased",
                "pre_handler_exit": "the stop reached the controller before it installed its handler and ended the process; what the robot was doing is not known",
                "exit_unobserved": "the controller's process ended without an acknowledgement and its exit status is unavailable",
                "crash": "the controller's process ended abnormally without acknowledging the stop; what the robot was doing is not known",
            }[classification]
            if run.stop_signal_failed:
                evidence = "the stop signal could not be delivered to the controller; " + evidence
            outcome = "failed" if run.stop_signal_failed else "ceased_unverified" if classification in ("acknowledged", "completed_anyway") else "unknown"
            self._emit("stop.outcome", {"outcome": outcome, "motion_ceased": None, "evidence": f"{evidence} (exit observed {exit_latency // 1_000_000} ms after the request)"},
                       run=run, reason_code=f"stop_{classification}")
            stop_record = {"requested_mono_ns": run.stop_requested_mono_ns, "signal_mono_ns": run.stop_signal_mono_ns, "signal_delivered": not run.stop_signal_failed,
                           "mechanism": run.stop_mechanism, "signals_sent": run.stop_signals_sent, "origin": run.stop_origin, "command_id": run.stop_command_id, "acknowledged": classification == "acknowledged", "acknowledged_by": run.ack_source,
                           "ack_latency_ns": ack_latency, "ack_latency_basis": run.ack_basis, "exit_latency_ns": exit_latency, "classification": classification,
                           "pre_handler_evidence": run.pre_handler_evidence if classification == "pre_handler_exit" else None}
        try:
            validation = demo_contracts.validate("event#/$defs/payload_run_finished", payload)
        except demo_contracts.SchemaError:
            validation = ["schema"]
        if validation:  # the controller's own summary does not fit the contract: keep the exit status, drop the rest
            self._gap(run, "controller_output_unparsed", "the controller's run.finished payload failed the contract and was replaced by what the runtime observed")
            payload = {"outcome": EXIT_OUTCOMES.get(exit_status, "unknown" if exit_status in (0, None) else "failed"), "exit_status": exit_status,
                       "predicates": {"object_secured": None, "placed": None, "clearance_target_met": None, "base_returned": None},
                       "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": False, "evidence_gaps": run.daemon_gaps}
        shown = "unavailable" if exit_status is None else str(exit_status)
        self._say("controller_exited", f"the controller process ended; exit status {shown}", run=run)
        finished = self._emit("run.finished", payload, run=run, origin=run.controller_finished_origin)
        completion = self._completion(run, payload, exit_status, stop_record, finished)
        self._record(run.command_id, status="finished", reason_code=None, completion=completion)
        self._release(run, exit_status)

    def _completion(self, run: _Run, payload: dict, exit_status: Optional[int], stop_record: Optional[dict], finished_event: Optional[dict]) -> dict:
        completion = {
            "schema": "demo-runtime-completion/1", "run_id": run.run_id, "command_id": run.command_id, "command_type": run.command_type, "adopted": run.adopted,
            "started_wall_utc": run.started_wall_utc, "finished_wall_utc": utc_now_text(), "started_seq": run.started_seq, "finished_seq": finished_event["seq"] if finished_event else None,
            "exit_status": exit_status, "exit_status_available": exit_status is not None, "outcome": payload["outcome"], "evidence_complete": payload["evidence_complete"],
            "evidence_gaps": payload["evidence_gaps"], "runtime_detected_gaps": run.daemon_gaps, "controller_run_finished_present": run.controller_finished is not None,
            "controller_run_started_folded": run.controller_run_started, "artifact_digest": run.bundle_digest, "stop": stop_record, "result": payload,
        }
        try:
            if run.run_dir.is_dir():
                (run.run_dir / COMPLETION_FILE).write_text(json.dumps(completion, indent=1, sort_keys=True) + "\n")
        except OSError:
            pass  # the journal record below is the durable copy
        return completion

    def _release(self, run: _Run, exit_status: Optional[int]) -> None:
        """Let go of the robot. The state change happens FIRST, under the lock, and nothing after it may undo or prevent it:
        a failing event log is the most likely reason a run had to be released this way, so saying so is best effort and its
        failure must not leave the owner held by a run that is over."""
        with self._state_lock:
            if self._active is run:
                self._active = None
            self._last_exit = {"run_id": run.run_id, "exit_status": exit_status}
            pidfd, run.pidfd = run.pidfd, None
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError:
                pass
        # The run's ONE exit path, so a clean finish, a stop and a crash all give the lease back. It is safe here and
        # only here: the process that could still be commanding under it is gone (or was never started). Best effort,
        # like everything after the state change: a release that fails leaves the host to expire the lease by itself.
        if run.lease_hold_id is not None:
            try:
                self._release_lease(f"the run ended (exit status {'unavailable' if exit_status is None else exit_status})", run.lease_hold_id)
            except Exception:  # noqa: BLE001 -- the robot is already free; nothing may stop this method from finishing
                pass
        try:
            self._say("owner.free", "the run is over and the runtime released the robot", run_id=run.run_id)
        except Exception:  # noqa: BLE001 -- the robot is already free; the record of it is all that could be lost here
            pass

    # ------------------------------------------------------------------ recovery
    def reconcile(self) -> list:
        """Decide every command a previous daemon left unfinished. Returns [(command_id, decision)]. Never spawns."""
        decisions = []
        with self._submit_lock:
            for state in list(self.table().values()):
                if state.get("status") in ACTIVE_STATUSES and state.get("command_type") != "stop":
                    try:
                        decision = self._reconcile_one(state)
                    except Exception as error:  # noqa: BLE001 -- one command must not abort the recovery of the others, nor the daemon's start
                        decision = "uncertain"  # the conservative answer: blocked until an operator looks
                        self._say("reconcile_failed", f"reconciling an interrupted command failed ({type(error).__name__}); it is treated as uncertain", run_id=state.get("run_id"), level="error")
                        with self._state_lock:
                            adopted_meanwhile = self._active is not None and self._active.command_id == state["command_id"]
                        if not adopted_meanwhile:
                            self._record(state["command_id"], status="uncertain", reason_code="reconcile_failed")
                        else:
                            decision = "adopted"
                    decisions.append((state["command_id"], decision))
        return decisions

    def _open_identified(self, pid: int, scan_id: str, start_ticks: Optional[int], events_path) -> Optional[tuple]:
        """(pidfd or None, start ticks) when pid is alive, carries the scan id and (when recorded) has the recorded start time;
        None when it is not provably our controller. The identity is checked AFTER the pidfd is open, so a pid that was reused
        in between is not adopted. Without a pidfd the process is still adopted, and a stop re-checks its identity first."""
        pidfd = pidfd_open(pid)  # never raises; None when this platform gives no pidfd (the identity below is still checked)
        stat = proc_stat(pid, self.proc_root)
        if stat is None or stat[0] in ("Z", "X") or (start_ticks is not None and stat[1] != start_ticks) or not is_our_controller(pid, scan_id, events_path, self.proc_root):
            if pidfd is not None:
                os.close(pidfd)
            return None
        return pidfd, stat[1]

    def _reconcile_one(self, state: dict) -> str:
        command_id, run_id, status = state["command_id"], state.get("run_id"), state["status"]
        child = state.get("child") if isinstance(state.get("child"), dict) else None
        run = _Run(run_id=run_id, command_id=command_id, command_type=state.get("command_type"), run_dir=self.data_dir / HISTORY_DIR / str(run_id), adopted=True)
        run.bundle_digest = state.get("bundle_digest")
        identified, how = None, None
        if child and isinstance(child.get("pid"), int):
            identified, how = self._open_identified(child["pid"], run_id, child.get("start_ticks"), run.events_path), "its recorded process identity"
        if identified is None and run_id:
            candidates = find_by_scan_id(run_id, run.events_path, self.proc_root)
            if len(candidates) == 1:
                identified, how = self._open_identified(candidates[0], run_id, None, run.events_path), "its scan id and its event file in the process table"
                child = {"pid": candidates[0]}
        with self._state_lock:
            slot_free = self._active is None
        if identified is not None and slot_free:
            run.pidfd, run.start_ticks = identified
            run.pid, run.status = child["pid"], "running"
            with self._state_lock:
                self._active = run
            try:
                self._record(command_id, status="running", reason_code=None, child={"pid": run.pid, "start_ticks": run.start_ticks, "scan_id": run_id}, adopted=True)
                started = self._say("reconcile_adopted", f"a live controller was found by {how} and adopted; its exit status will be unavailable", run=run)
                run.started_seq = started["seq"] if started else 0
            finally:  # the run is active from the assignment above: it is followed and waited for whatever else fails
                self._follow(run, offset=self.log.resume_offset(run_id))
            return "adopted"
        if identified is not None and identified[0] is not None:
            os.close(identified[0])
        has_events, has_output = run.events_path.is_file(), (run.run_dir / STDOUT_FILE).is_file()
        if scan_run_finished(run.events_path) is not None:
            self._say("reconcile_finished", "the controller of an interrupted command is gone and left a run.finished record", run=run)
            run.tailer = Tailer(run.events_path, self.log.resume_offset(run_id), lambda line, end: self._ingest(run, line, end), lambda end: None, self.log.sync, self.tail_interval_s)
            self._finalize(run, None, time.monotonic_ns())
            run.tailer.close()  # the tailer's THREAD was never started here, so its file is this call's to release
            return "finished"
        if has_events:  # what the controller recorded before it went is evidence: merge it, then say the record stops short
            run.tailer = Tailer(run.events_path, self.log.resume_offset(run_id), lambda line, end: self._ingest(run, line, end), lambda end: None, self.log.sync, self.tail_interval_s)
            run.tailer.drain()
            run.tailer.close()
        reason = {"accepted": "no_child_evidence", "spawning": "child_identity_not_recorded", "running": "child_gone_without_terminal_record"}[status]
        self._gap(None, "record_truncated", "a command was interrupted by a runtime restart and nothing proves how it ended", run_id=run_id)
        self._say("reconcile_uncertain", f"no live controller carries the command's scan id; controller events present: {'yes' if has_events else 'no'}; controller output present: "
                  f"{'yes' if has_output else 'no'}. The command is uncertain and the owner is unresolved", run_id=run_id, level="warning")
        self._record(command_id, status="uncertain", reason_code=reason)
        self._say("owner.unresolved", "commands are blocked until an operator resolves the uncertain command", run_id=run_id, level="warning")
        return "uncertain"

    # ------------------------------------------------------------------ reconciling an uncertain command
    def _accepted_wall_utc(self, command_id: str) -> Optional[str]:
        """The wall time of the FIRST journal record of this command: when it was accepted, not when it was last touched."""
        for record in self.journal.read()[0]:
            if record.get("rec") == "command" and record.get("command_id") == command_id:
                return record.get("t_wall_utc")
        return None

    def _live_child(self, state: dict, run_dir: Path) -> dict:
        """Is a process with this command's identity alive NOW? Checked twice: by the recorded pid (plus its recorded
        start time, its command line and the events path it was started with) and by scanning the process table for the
        scan id. readable false means the process table could not be read at all, which proves nothing either way."""
        found = {"readable": _process_table_readable(self.proc_root), "alive": False, "found_by": None, "pid": None, "candidates": 0,
                 "recorded_pid_present": False, "recorded_identity_matches": None}
        run_id, child = state.get("run_id"), state.get("child") if isinstance(state.get("child"), dict) else None
        events_path = run_dir / CONTROLLER_EVENTS_FILE
        if child and isinstance(child.get("pid"), int):
            found["recorded_pid_present"] = True
            stat = proc_stat(child["pid"], self.proc_root)
            matches = bool(stat and stat[0] not in ("Z", "X") and (child.get("start_ticks") is None or stat[1] == child["start_ticks"])
                           and run_id and is_our_controller(child["pid"], run_id, events_path, self.proc_root))
            found["recorded_identity_matches"] = matches
            if matches:
                found.update({"alive": True, "found_by": "recorded_identity", "pid": child["pid"]})
        if run_id:
            candidates = find_by_scan_id(run_id, events_path, self.proc_root)
            found["candidates"] = len(candidates)
            if candidates and not found["alive"]:
                found.update({"alive": True, "found_by": "scan_id", "pid": candidates[0]})
        return found

    def _run_files(self, run_dir: Path) -> dict:
        """What the run directory holds: the controller's event file, whether it carries a TERMINAL record, and its output."""
        events_path, out = run_dir / CONTROLLER_EVENTS_FILE, {"readable": True, "events_present": False, "event_records": None, "terminal_record": False,
                                                              "outcome": None, "output_present": False, "output_bytes": None}
        try:
            out["events_present"] = events_path.is_file()
            if out["events_present"]:
                records, _ = read_json_lines(str(events_path))
                out["event_records"] = len(records)
                terminal = scan_run_finished(events_path)
                out["terminal_record"] = terminal is not None
                if terminal is not None:
                    outcome = terminal.get("payload", {}).get("outcome")
                    out["outcome"] = outcome if isinstance(outcome, str) and len(outcome) <= 64 else None
            output = run_dir / STDOUT_FILE
            if output.is_file():
                out["output_present"], out["output_bytes"] = True, output.stat().st_size
        except OSError:
            out["readable"] = False
        return out

    def _observation_captures(self, state: dict, accepted_wall_utc: Optional[str]) -> dict:
        """Did the observation store gain anything for this run AFTER the command was accepted? Both times are this
        machine's wall clock (the store is written here), so they may be compared. Counting stops at a bound."""
        out = {"checked": False, "readable": True, "session_present": False, "files_after_accept": None, "files_total": None}
        root = getattr(self.config, "observation_root", None) if self.config is not None else None
        run_id = state.get("run_id")
        if root is None or not run_id:
            return out
        out["checked"] = True
        since = _wall_epoch(accepted_wall_utc)
        session = Path(root) / str(run_id)
        try:
            if not session.is_dir():
                return out
            out["session_present"], total, after = True, 0, 0
            for path in session.rglob("*"):
                if total >= _MAX_OBSERVATION_SCAN:
                    break
                if path.is_file():
                    total += 1
                    if since is not None and path.stat().st_mtime >= since - _MTIME_SLACK_S:
                        after += 1
            out["files_total"], out["files_after_accept"] = total, (after if since is not None else None)
        except OSError:
            out["readable"] = False
        return out

    def reconcile_evidence(self, command_id) -> dict:
        """Look at the world AGAIN for one command: the process table, the run's own files and the observation store.
        It READS. It starts nothing, signals nothing, opens no socket to the robot and never touches a motor path."""
        with self._state_lock:
            state = dict(self._table.get(command_id) or {}) if isinstance(command_id, str) else {}
        run_id = state.get("run_id")
        run_dir = self.data_dir / HISTORY_DIR / str(run_id) if run_id else self.data_dir / HISTORY_DIR / "-"
        accepted = self._accepted_wall_utc(command_id) if isinstance(command_id, str) else None
        evidence = {
            "command_id": command_id if isinstance(command_id, str) else None, "run_id": run_id, "command_type": state.get("command_type"),
            "journal": {"status": state.get("status"), "reason_code": state.get("reason_code"), "child_identity": bool(state.get("child")),
                        "adopted": bool(state.get("adopted")), "accepted_wall_utc": accepted, "resolved": bool(state.get("resolved"))},
            "process": self._live_child(state, run_dir),
            "run_files": self._run_files(run_dir),
            "observations": self._observation_captures(state, accepted),
            "checked_wall_utc": utc_now_text(),
        }
        evidence["classification"], evidence["unreadable"] = classify_evidence(evidence)
        return evidence

    def unresolved_detail(self, *, max_age_s: float = 2.0) -> Optional[dict]:
        """What a snapshot says about the one command that holds the block: the machine evidence, what it classifies as
        and the question the operator is being asked. Cached briefly: a snapshot is asked for often and this reads files."""
        pending = self.unresolved()
        if not pending:
            return None
        command_id = pending[0]
        now = time.monotonic()
        cached = self._evidence_cache  # read ONCE into a local: another thread's resolve may drop the cache meanwhile
        if cached is None or cached[0] != command_id or now - cached[1] > max_age_s:
            cached = (command_id, now, self.reconcile_evidence(command_id))
            self._evidence_cache = cached
        evidence = cached[2]
        with self._state_lock:
            state = dict(self._table.get(command_id) or {})
        return {"command_id": command_id, "run_id": state.get("run_id"), "command_type": state.get("command_type"), "reason_code": state.get("reason_code"),
                "classification": evidence["classification"], "block_reason_code": "owner_unresolved", "evidence": evidence,
                "question": question_for(evidence), "findings": list(FINDINGS), "attestations": list(state.get("attestations") or []),
                "blocked_commands": list(COMMANDS_BLOCKED_WHILE_UNRESOLVED)}

    def resolve(self, command_id, finding, note=None) -> dict:
        """RECONCILE an uncertain command, do not clear it. The daemon re-checks the evidence itself; the operator's
        declared finding is recorded as an ATTESTATION beside that evidence and never instead of it. A finding that
        contradicts what is found is refused. Nothing is ever respawned, retried or signalled here."""
        if finding not in FINDINGS:
            return {"ok": False, "reason_code": "finding_not_allowed", "findings": list(FINDINGS)}
        clean_note, note_ok = _clean_note(note)
        if not note_ok:
            return {"ok": False, "reason_code": "note_not_allowed"}
        with self._submit_lock:
            with self._state_lock:
                state = dict(self._table.get(command_id) or {}) if isinstance(command_id, str) else {}
            if state.get("status") != "uncertain":
                return {"ok": False, "reason_code": "command_not_uncertain"}
            evidence = self.reconcile_evidence(command_id)
            classification = evidence["classification"]
            if state.get("resolved"):
                return {"ok": True, "command_id": command_id, "owner": self.owner()["kind"], "already_resolved": True, "cleared": True,
                        "cleared_by": "earlier_resolution", "classification": classification, "evidence": evidence}
            self._evidence_cache = None
            if evidence["process"]["alive"]:
                # The one hard contradiction: every allowed finding states that nothing is running, and something is.
                contradiction = {"declared": finding, "found": "a process with this command's identity is alive now",
                                 "found_by": evidence["process"]["found_by"]}
                self._attest(command_id, finding, clean_note, evidence, clears_block=False, refused="controller_alive")
                self._say("resolve_refused", f"a resolution declaring {finding} was refused: a controller of that command is running", run_id=state.get("run_id"), level="warning")
                return {"ok": False, "reason_code": "controller_alive", "command_id": command_id, "classification": None,
                        "contradiction": contradiction, "evidence": evidence, "cleared": False}
            cleared_by = "machine_evidence" if classification in CONCLUSIVE_CLASSES else None if classification == UNKNOWN_CLASS else "operator_attestation"
            attestation = self._attest(command_id, finding, clean_note, evidence, clears_block=cleared_by is not None, refused=None)
            if cleared_by is None:
                self._say("resolve_inconclusive", "an operator declared a finding for an uncertain command, and the evidence could not be read: the block stays",
                          run_id=state.get("run_id"), level="warning")
                return {"ok": True, "command_id": command_id, "owner": self.owner()["kind"], "cleared": False, "cleared_by": None,
                        "reason_code": "evidence_unreadable", "classification": classification, "evidence": evidence, "attestation": attestation}
            self.journal.append({"rec": "resolution", "command_id": command_id, "finding": finding, "note": clean_note, "classification": classification,
                                 "cleared_by": cleared_by, "t_wall_utc": utc_now_text()})
            with self._state_lock:
                self._table[command_id].update({"resolved": True, "finding": finding, "classification": classification, "cleared_by": cleared_by})
            self._say("owner_resolved", f"an uncertain command was reconciled as {classification} and resolved with the finding {finding} ({cleared_by})",
                      run_id=state.get("run_id"))
            owner = self.owner()["kind"]
            if owner == "free":
                self._say("owner.free", "no unresolved command is left", run_id=state.get("run_id"))
            return {"ok": True, "command_id": command_id, "owner": owner, "cleared": True, "cleared_by": cleared_by, "classification": classification,
                    "evidence": evidence, "attestation": attestation}

    def _attest(self, command_id: str, finding: str, note: Optional[str], evidence: dict, *, clears_block: bool, refused: Optional[str]) -> dict:
        """The operator's statement, recorded BESIDE the machine evidence. An attestation never resolves by itself:
        demo_runtime.journal.fold_commands only ever treats a 'resolution' record as resolving, so clears_block is the
        DECISION this statement led to and the resolution record next to it is what actually unblocked the runtime. A
        process that dies between the two therefore leaves the block on, with the statement still on the record."""
        attestation = {"finding": finding, "note": note, "classification": evidence["classification"], "clears_block": clears_block, "refused": refused,
                       "t_wall_utc": utc_now_text()}
        self.journal.append({"rec": "attestation", "command_id": command_id, **attestation, "evidence": evidence})
        with self._state_lock:
            table_state = self._table.get(command_id)
            if table_state is not None:
                table_state.setdefault("attestations", []).append(attestation)
        return attestation

    def detach(self) -> None:
        """Let go of the child without touching it (daemon shutdown, tests): the controller keeps running on its own.

        The renewer stops and THE LEASE IS NOT RELEASED. A controller that is still commanding would lose the robot the
        moment this daemon ended, which is exactly the accident the lease exists to prevent; the host extends the lease
        on every valid command, so a live controller keeps it without this runtime, and a controller that has stopped
        commanding lets it expire. The next daemon adopts the controller and finds the lease already held by the host."""
        self._detached.set()
        self._lease_halt.set()
        renewer, self._renewer = self._renewer, None
        if renewer is not None and renewer is not threading.current_thread():
            renewer.join(timeout=5.0)
        with self._state_lock:
            run = self._active
        if run is not None and run.tailer is not None:
            run.tailer.detach()
