# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.capability_check: the robot's capability slot, moved by a PREPARED Plan and read back, with NO MOTION.

WHAT IT IS FOR. The operator's brief asks for the existing non-motion activation controls to be driven against the
LeKiwi resident: an invalid prepared candidate must leave the capability slot exactly as it was, and the valid prepared
Plan must produce an observed slot change WITHOUT a resident restart, with the supervised host HELD throughout. The
mission's own activation cannot do that today: it is offered only to a mission that is WAITING_FOR_CAPABILITY
(MissionCoordinator.submit_activation), and a LIVE runtime has no mission surface until the robot-side execution ships.
This is the smallest surface that can: one ApplyPlan of a prepared candidate, bracketed by fresh reads, and nothing else.

WHAT IT NEVER DOES. It starts no run, no mission and no controller, resumes nothing, waits on nothing a mission holds,
sends no command to the robot host and touches no lease. The ONLY mutation it can make is the one
demo_runtime.cake_activation makes, through the capability adapter mission_service already builds for the robot
(capability_adapter with robot_build_identity), which reads get-status and refuses before EVERY ApplyPlan unless the
answering resident reports cake.robot.expected_build_identity. The candidate bytes are the runtime's own, read once at
start-up; a caller names a KEY and the DIGEST it was shown, and supplies no bytes, no path and no operation.

ONE DECIDER, NARROWED ONLY DOWNWARD. The verdict is mission_coordinator.activation_verdict, the lines the mission's
activation decides with, moved out unchanged so the two surfaces cannot come to call the same answer different things: a
completed transaction is not an activation, and only the slot, read back, makes one OBSERVED. A check then narrows that
verdict in two ways of its own, and both only ever take OBSERVED DOWN to COMPLETED_NOT_OBSERVED, never anything up:
  - a resident session that changed across the operation (below);
  - a slot that did not CHANGE. The shared verdict asks only whether the slot read back carries the capability capsule,
    and a mission asks it only while its target waits for a capability the slot does not carry. A check can be sent to a
    slot that ALREADY carries it (the robot's slot is most likely still ACTIVE after the swap of handoff section 9), and a
    COMPLETED re-apply that leaves capsule, generation and instance exactly as they were would then read OBSERVED with
    nothing observed. The brief asks for an OBSERVED SLOT CHANGE, so a check is OBSERVED only when both readings answered
    and at least one of the three differs (slot_changed), and every answer records slot_changed beside its verdict.
One re-reading sits beside the two, and it moves nothing toward an activation: an answer the resident gave as `status
mutation_refused` with its refusal class is recorded REFUSED_BEFORE_TRANSACTION with that class (refused_before_transaction)
instead of the shared verdict's REQUEST_FAILED, because the resident JUDGED the candidate and opened no transaction. The
tampered twin of a prepared Plan is refused this way (class 100: its bytes do not read as a Plan artifact whose digests
hold), which is an integrity refusal and never a signature one.

WHAT A CHECK IS BOUND TO. Build identity identifies the SOFTWARE the resident runs, and the pin refuses a different build
and nothing more. Which deployment is observed is established by the authenticated connection to the intended robot (the
ssh forward to that robot's admin socket, or the local socket on the robot) and the session that endpoint's resident
reports now. So a check carries the resident SESSION the operator was shown, and is refused unless a FRESH read of that
endpoint reports exactly that session (and the pinned build), and unless the capability slot, read immediately before the
submit, reports the same session. A resident session that changes across the operation (the resident restarted) makes an
answer that would have read OBSERVED read COMPLETED_NOT_OBSERVED instead: a slot read from a different resident session is
not an observation of this operation.

THE HOST STAYS HELD, AND HELD MEANS NO HOST PROCESS. The brief keeps the supervised host held for the whole check. A fresh
read BEFORE the submit must say that NO host process is under the supervisor slot: the slot is absent, or its health row
reports child pid 0, or the slot is not ACTIVE AND its health row is missing or reports child pid 0 (host_held,
HELD_REASONS; a slot that is not ACTIVE says nothing by itself about a process under it, so its row decides, and a
health answer that could not be read leaves it unknown). The deployed robot reads child pid 0: DEGRADED after five
refused spawns, no child (handoff section 9). robot_link.binding_of's host_running False is NOT taken as held:
it is also False for a NONZERO child pid the supervisor reports in any state but HEALTHY (supervisor_host_not_healthy),
which is the cautious answer for motion permission (nothing to bind to) and the permissive one here (a host process is
there). A host that runs or is reported not HEALTHY refuses robot_host_not_held; one that cannot be read refuses
robot_host_unreadable; both before anything is submitted. The same read is taken again AFTER the answer and recorded as
host_after, with the same rule deciding `held`. Whether an ApplyPlan re-arms a DEGRADED supervisor slot is NOT known here;
if it does, the wrapper still refuses without an arm token (section 8 of the handoff), and host_after may catch that
wrapper alive for a moment, in whatever state the supervisor reports it: that reading is RECORDED as not held, never
hidden and never corrected. This check cannot see an arm token; that there is none is the operator's own check.

HELD THROUGHOUT IS MORE THAN TWO HELD READS, AND SAYS SO ONLY WHEN THE COUNTERS AGREE. Two point reads cannot see a
wrapper the supervisor spawned, that refused and exited between them: both read child pid 0. The supervisor's own
counters can. So both host reads carry the supervisor slot as read (robot_link.supervisor_slot_reading: its state,
instance handle and generation, and its health row's spawns, `sequence`, and restarts, `flags`), and a check is HELD
THROUGHOUT (held_throughout) only when both reads were held, the resident session is the same, and every one of those
components reads the same at both. Any difference is NOT held throughout, with a sentence naming each component and its
two values (spawns 5 to 10, ...). What it still cannot see is named too: a spawn the supervisor did not count. With no
counters on either read (a record without them) it says not known, never held.

ONE CHECK AT A TIME, REFUSED RATHER THAN QUEUED. A lock that is only ever TRIED: a second apply while one holds it is
answered capability_check_in_flight at once and submits nothing. While it is held the runtime's run-slot gate answers
capability_check_in_flight too (daemon.RuntimeCore._mission_holds_run_slot), so no command but stop and no mission start
is admitted during a check. Stop is not affected: the daemon's stop path takes neither this lock nor the supervisor's
submit lock. A PENDING answer (the resident said its transaction is still running) keeps refusing a new check for the life
of this runtime: the operation is retrieved by re-reading the slot, never replaced by a fresh ApplyPlan on a guess. An
ApplyPlan whose answer did not arrive in time (cake_activation.submit_apply_plan's admin_probe_timeout) is the same
case: THE OPERATION MAY STILL BE RUNNING (cake_activation's own rule), so the check keeps it pending too, records it as
`unresolved` beside the shared verdict's REQUEST_FAILED, and never says the request did not land.

THE RECORD. Its own append-only file, capability-checks.jsonl beside the mission file, written through journal.Journal
(one JSON line, fsynced before append returns). The attempt is written BEFORE the submit, binding the operation uuid to
the payload digest; the answer after the read-backs. Both mirror mission.activation_attempt and mission.activation_answer
and add what a check needs and a mission does not: the session the caller asked for, the host before and after (each
with `held`), whether the resident session changed, whether the slot changed, and a monotonic stamp per phase (this process's clock, for durations; the wall stamp is for
an operator to read). An attempt left without an answer by a runtime that ended is shown as exactly that. With the readers
configured, the attempt also carries ValidatePlan's verdict on the exact bytes (read-only, asked just before the submit)
and the answer the transaction's state path, rebuilt from the resident's flight ring read right after the answer
(cake_activation.transaction_state_path). Both are evidence beside the verdict and neither decides it.

SOFTWARE COVERAGE IS NOT EVIDENCE ABOUT HARDWARE. What the tests here drive is a fake admin-probe. The operator's run on
the robot is what shows the slot moving on LeKiwi.
"""
from __future__ import annotations

import collections
import re
import threading
import time
from typing import Callable, Optional

from . import cake_activation, mission, robot_link
from .journal import Journal, read_json_lines, utc_now_text
from .mission_coordinator import CapabilityAdapter, activation_verdict, transaction_evidence

#: This surface's own version, moved when the shape of its answer changes. It is not the contract version.
CAPABILITY_CHECK_VERSION = "1"
#: The record file's own version, moved when a record's shape changes.
CHECK_RECORD_VERSION = 1
CHECKS_FILE_NAME = "capability-checks.jsonl"
RECORD_KIND = "capability_check"
#: The checks a state answer carries, newest last. The file keeps every one; the answer says how many it left out.
MAX_CHECKS_SHOWN = 20
#: What close_wait_bound_s adds to the child-process bounds it sums: two fsynced appends and the Python between the
#: children. Each child's own bound already includes its start-up, because subprocess.run's timeout runs from the spawn.
CLOSE_MARGIN_S = 2.0


def close_wait_bound_s(*, read_timeout_s: float, submit_timeout_s: float, binding_timeout_s: float,
                       flight_timeout_s: float = 0.0) -> float:
    """How long a runtime that is ending waits for a check in flight to write its answer, DERIVED from every bound the
    check can still spend once it is past the closing test in front of its attempt record (CapabilityCheck.begin_close):
    the get-status the adapter reads before the ApplyPlan (read_timeout_s), the ApplyPlan (submit_timeout_s), the slot read
    back (read_timeout_s), and the host read again, which is get-status, list-slots and get-health (3 x
    binding_timeout_s, cake.timeout_s), plus the read-flight taken right after the answer when a flight reader is
    configured (flight_timeout_s, 0 without one), plus CLOSE_MARGIN_S. A check that has not reached that test when the runtime begins
    to close refuses there and submits nothing, and what it spends before refusing (its pre-submit reads) is less than
    this. The daemon passes the SAME figures it builds the adapter with, so the two cannot drift apart."""
    return read_timeout_s + submit_timeout_s + flight_timeout_s + read_timeout_s + 3 * binding_timeout_s + CLOSE_MARGIN_S

#: Why a check was refused. Each is a CODE: none names a path, a value or a machine.
UNAVAILABLE = "capability_check_unavailable"
IN_FLIGHT = "capability_check_in_flight"
UNKNOWN_CANDIDATE = "unknown_candidate"
DIGEST_MISMATCH = "payload_digest_mismatch"
HOST_NOT_HELD = "robot_host_not_held"
SESSION_MISMATCH = "robot_session_mismatch"
SLOT_SESSION_MISMATCH = "capability_slot_session_mismatch"
#: The `detail` beside UNAVAILABLE for a check refused because the runtime has begun to close.
RUNTIME_CLOSING = "runtime_closing"
#: Why a runtime has no check at all (the `detail` beside UNAVAILABLE).
NOT_LIVE = "capability_check_requires_live_mode"
ROBOT_NOT_CONFIGURED = "robot_not_configured"
CAKE_NOT_CONFIGURED = "cake_not_configured"
ROBOT_BUILD_NOT_PINNED = "robot_build_identity_not_configured"
NO_CANDIDATE = "no_activation_candidate_prepared"
NO_CAPABILITY = "capability_not_configured"
CAPABILITY_NOT_BINDABLE = "capability_binding_not_bindable"

#: The detail an OBSERVED answer is replaced with when the resident's session changed across the operation.
SESSION_CHANGED_DETAIL = ("the transaction completed and the slot reads active, but the resident's session changed across "
                          "the operation, so that read is a different resident session's and is not an observation of this one")
#: The detail an OBSERVED answer is replaced with when the slot read back is exactly the slot read before.
SLOT_UNCHANGED_DETAIL = ("the transaction completed and the slot reads active, but it read exactly the same before the "
                         "operation (capsule, generation and instance), so no change was observed and this operation is not "
                         "credited with one")
#: The detail an OBSERVED answer is replaced with when a reading lacks a component, so no change can be told apart.
SLOT_CHANGE_UNKNOWN_DETAIL = ("the transaction completed and the slot reads active, but a reading before or after lacks its "
                              "capsule, generation or instance, so whether this operation changed the slot is not known")

#: The supervisor-slot reasons, as robot_link.supervisor_host_instance answers them with host_running False, that CAN mean
#: no host process is under the supervisor slot: no slot, a slot that is not ACTIVE, or a health row with child pid 0.
#: SLOT_NOT_ACTIVE is held only with its health row (host_held): robot_link answers it before reading that row. The one
#: other reason it answers False for, supervisor_host_not_healthy, is a nonzero child pid in a state other than HEALTHY,
#: and that is NOT held. A test pins this split against robot_link's source, so a new reason there is decided here before
#: it is trusted; until then an unknown reason is not held.
SLOT_NOT_ACTIVE = "supervisor_slot_not_active"
HELD_REASONS = frozenset(("supervisor_slot_absent", SLOT_NOT_ACTIVE, "supervisor_child_not_running"))
#: What held_throughout compares between the two host reads, with the words its sentence names each one by. The
#: supervisor slot as robot_link.supervisor_slot_reading reads it: any difference means the supervisor did something
#: between the reads, which two held point reads do not show.
SUPERVISOR_COUNTERS = (("spawns", "spawns"), ("restarts", "restarts"), ("slot_generation", "supervisor slot generation"),
                       ("slot_instance", "supervisor slot instance"), ("slot_state", "supervisor slot state"),
                       ("slot_present", "supervisor slot present"), ("health_row", "supervisor health row present"),
                       ("health_state", "supervisor health state"), ("child_pid", "child pid"))
#: cake_activation.submit_apply_plan's reason when the ApplyPlan's answer did not arrive in time. Its own rule: THE
#: OPERATION MAY STILL BE RUNNING. A test drives a real timeout through the daemon, so a rename there fails here.
SUBMIT_TIMED_OUT = "admin_probe_timeout"
#: The detail a check records for that answer, in place of the bare code.
TIMED_OUT_DETAIL = ("no answer arrived in time (admin_probe_timeout); the operation may still be running, so its fate is "
                    "read from the slot, never guessed, and no other check is sent from this runtime")
#: The three components of a slot reading a change is judged on.
SLOT_COMPONENTS = ("capsule", "generation", "instance")

_SESSION = re.compile(r"[0-9a-f]{8,64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SLOT_UUID = re.compile(r"[0-9a-f]{32}")
_CAPSULE = re.compile(r"[0-9a-f]{64}")
_BUILD = re.compile(r"[0-9a-f]{64}")


def session_shape(value) -> bool:
    """Whether a caller's session is the shape a resident session has. Checked before anything is read."""
    return isinstance(value, str) and _SESSION.fullmatch(value) is not None


def digest_shape(value) -> bool:
    """Whether a caller's payload digest is the shape cake_activation.digest_of prints."""
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def unavailable_reason(*, live: bool, robot, probe_path, socket_path, candidates, binding) -> Optional[str]:
    """None when every condition a check needs holds, else the FIRST missing one, in this order:
      - a LIVE runtime (a synthetic one has the rehearsal and its fixture resident, and never the robot's);
      - cake.robot, with a pinned build identity (every ApplyPlan here goes only to a resident reporting it);
      - an admin-probe and a socket to reach the resident with;
      - at least one PREPARED candidate (configured candidates are what switch the check on; with none it is off);
      - a capability binding in the shape a slot uuid and a capsule digest have.
    Hardware commands are NOT a condition: a check moves no motor, and the observe-only console is where it runs."""
    if not live:
        return NOT_LIVE
    if robot is None:
        return ROBOT_NOT_CONFIGURED
    if not isinstance(getattr(robot, "expected_build_identity", None), str) or _BUILD.fullmatch(robot.expected_build_identity) is None:
        return ROBOT_BUILD_NOT_PINNED
    if probe_path is None or socket_path is None:
        return CAKE_NOT_CONFIGURED
    if not candidates.keys():
        return NO_CANDIDATE
    if binding is None:
        return NO_CAPABILITY
    _name, slot_uuid, capsule = binding
    if _SLOT_UUID.fullmatch(slot_uuid) is None or _CAPSULE.fullmatch(capsule) is None:
        return CAPABILITY_NOT_BINDABLE
    return None


def host_held(reading) -> Optional[bool]:
    """Whether ONE read says the supervised host is HELD: True when no host process is under the supervisor slot
    (host_running False for one of HELD_REASONS), False when one is (a HEALTHY host runs, or a child is reported in any
    other state, or a reason this check has not classified), None when it could not be read. A slot that is NOT ACTIVE
    is held only when the health row read beside it (supervisor_slot) is missing or reports child pid 0: robot_link
    answers that reason before it reads the row, and a live child under a slot in another state is a host process all
    the same. With no health read beside it, it is not known. It takes a binding (robot_link.binding_of) or the
    record's narrowed host (host_of): both carry host_running, reason_code and supervisor_slot. It is the ONE rule for
    held: the refusal, the recorded `held`, held_throughout and the state read all ask it."""
    if not isinstance(reading, dict):
        return None
    running = reading.get("host_running")
    if running is True:
        return False
    if running is not False:
        return None
    reason = reading.get("reason_code")
    if reason not in HELD_REASONS:
        return False
    if reason != SLOT_NOT_ACTIVE:
        return True
    supervisor = reading.get("supervisor_slot")
    if not isinstance(supervisor, dict) or supervisor.get("health_read") is not True:
        return None
    if supervisor.get("health_row") is not True:
        return True
    pid = supervisor.get("child_pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    return pid == 0


def _value_words(value) -> str:
    return "not read" if value is None else str(value).lower() if isinstance(value, bool) else str(value)


def held_throughout(before, after) -> tuple:
    """(held throughout, detail): whether the supervised host was HELD for the whole check, as far as two reads and the
    supervisor's own counters can say, and why not when it was not.
      - None when the read after did not answer (not known is not held), or when either read carries no supervisor
        counters to compare (two held point reads alone cannot say throughout);
      - False when either read was not held, when the resident session differs between them (its counters are then
        another session's), or when ANY of SUPERVISOR_COUNTERS differs: the supervisor spawned, restarted, re-created or
        re-applied something between the reads, which neither read shows on its own. The detail names every component
        that differs with its two values ("spawns 5 to 10");
      - True only when both reads were held and every counter reads the same.
    A spawn the supervisor did not count is the one thing this still cannot see."""
    held_before, held_after = host_held(before), host_held(after)
    if held_after is None:
        return None, "the host was not read after the operation, so whether it stayed held is not known"
    if held_before is not True or held_after is not True:
        return False, ("a host process was under the supervisor slot when it was read after the operation"
                       if held_after is False else
                       "the reads before and after do not both say that no host process was there")
    if before.get("session_uuid") != after.get("session_uuid"):
        return False, (f"the resident session changed between the two reads ({_value_words(before.get('session_uuid'))} to "
                       f"{_value_words(after.get('session_uuid'))}), so the supervisor's counters cannot be compared")
    counters = before.get("supervisor_slot"), after.get("supervisor_slot")
    if not all(isinstance(value, dict) for value in counters):
        return None, ("both reads were held, but a read carries no supervisor counters, so whether anything was spawned "
                      "between them is not known")
    changed = [f"{words} {_value_words(counters[0].get(name))} to {_value_words(counters[1].get(name))}"
               for name, words in SUPERVISOR_COUNTERS if counters[0].get(name) != counters[1].get(name)]
    if changed:
        return False, ("both reads were held, but the supervisor slot changed between them: " + "; ".join(changed)
                       + ". Something ran or was re-applied under it during the check")
    return True, None


def answer_unresolved(activation_state, transaction_reason) -> bool:
    """Whether an answer leaves the operation's fate OPEN, which keeps this runtime's check closed to another: the
    resident said its transaction is still running (SUBMITTED), or the ApplyPlan's answer did not arrive in time
    (REQUEST_FAILED with SUBMIT_TIMED_OUT: the operation may still be running)."""
    return activation_state == mission.ACTIVATION_SUBMITTED or (
        activation_state == mission.ACTIVATION_FAILED and transaction_reason == SUBMIT_TIMED_OUT)


def slot_changed(before, after) -> Optional[bool]:
    """Whether the capability slot CHANGED across the operation: True when both readings answered ok and the capsule, the
    generation or the instance differs, False when both answered and all three are equal, None when a reading did not
    answer or lacks one of the three (a component not read is not a component unchanged)."""
    readings = (before, after)
    if not all(isinstance(reading, dict) and reading.get("read") == cake_activation.READ_OK for reading in readings):
        return None
    if any(reading.get(name) is None for reading in readings for name in SLOT_COMPONENTS):
        return None
    return any(before[name] != after[name] for name in SLOT_COMPONENTS)


def host_of(binding: Optional[dict]) -> dict:
    """What one binding read said about the resident and its supervised host, for the record. robot_link.binding_of's
    shape, narrowed: the session and build that answered, whether a HEALTHY host runs (robot_link's own answer, shown as
    it is), why, the supervisor slot and its health counters as read (supervisor_slot, what held_throughout compares),
    and `held` (host_held: whether NO host process is under the supervisor slot, None when unread)."""
    binding = binding if isinstance(binding, dict) else {}
    instance = binding.get("host_instance")
    supervisor = binding.get("supervisor_slot")
    narrowed = {"session_uuid": binding.get("session_uuid"), "build_identity": binding.get("build_identity"),
                "host_running": binding.get("host_running") if isinstance(binding.get("host_running"), bool) else None,
                "host_instance": dict(instance) if isinstance(instance, dict) else None,
                "reason_code": binding.get("reason_code"), "observed_wall_utc": binding.get("observed_wall_utc"),
                "supervisor_slot": dict(supervisor) if isinstance(supervisor, dict) else None}
    narrowed["held"] = host_held(narrowed)
    return narrowed


def binding_refusal(binding: dict, session_uuid: str) -> Optional[tuple]:
    """(reason_code, detail) when a FRESH binding read refuses a check for `session_uuid`, else None. In this order:
    a resident that does not report the pinned build (robot_link.verify_identity already made the read unreachable), no
    readable session, another session, a host that cannot be read, a host that is NOT HELD (host_held: a HEALTHY host
    runs, or a child is reported under the supervisor slot in another state; the detail names which). """
    reason = binding.get("reason_code")
    if reason in robot_link.IDENTITY_REFUSALS:
        return reason, None
    if binding.get("session_uuid") is None:
        return robot_link.UNREACHABLE, reason
    if binding["session_uuid"] != session_uuid:
        return SESSION_MISMATCH, None
    held = host_held(binding)
    if held is None:
        return robot_link.HOST_UNREADABLE, reason
    if not held:
        return HOST_NOT_HELD, reason
    return None


def check_attempt(operation_uuid: str, candidate_key: str, payload_digest: str, *, requested_session_uuid: str,
                  runtime_session: str, host_before: dict, slot_before, phases_mono_ns: dict, t_wall_utc: str,
                  validation: Optional[dict] = None) -> dict:
    """Written BEFORE the submit, like mission.activation_attempt: a crash mid-submit leaves the uuid, the bytes it was
    bound to (their digest) and everything read before it on the record."""
    return {"rec": RECORD_KIND, "v": CHECK_RECORD_VERSION, "operation_uuid": operation_uuid, "candidate_key": candidate_key,
            "payload_digest": payload_digest, "activation_state": mission.ACTIVATION_SUBMITTED,
            "requested_session_uuid": requested_session_uuid, "runtime_session": runtime_session,
            "host_before": dict(host_before), "slot_before": dict(slot_before) if isinstance(slot_before, dict) else None,
            "phases_mono_ns": dict(phases_mono_ns), "t_wall_utc": t_wall_utc,
            "validation": dict(validation) if isinstance(validation, dict) else None}


def check_answer(operation_uuid: str, activation_state: str, *, outcome, transaction_phase, transaction_uuid, detail,
                 transaction: dict, slot_after, host_after: dict, resident_session_changed: Optional[bool],
                 slot_changed: Optional[bool], unresolved: bool, phases_mono_ns: dict, t_wall_utc: str,
                 refusal: Optional[dict] = None, state_path: Optional[dict] = None) -> dict:
    """What came back, like mission.activation_answer, with the host as read after the answer, whether the resident
    session changed across the operation (None when no read after it answered with a session), whether the slot
    changed (slot_changed; None when it cannot be told) and whether the operation's fate is still open
    (answer_unresolved)."""
    return {"rec": RECORD_KIND, "v": CHECK_RECORD_VERSION, "operation_uuid": operation_uuid,
            "activation_state": activation_state, "outcome": outcome, "transaction_phase": transaction_phase,
            "transaction_uuid": transaction_uuid, "detail": detail, "transaction": dict(transaction),
            "slot_after": dict(slot_after) if isinstance(slot_after, dict) else None, "host_after": dict(host_after),
            "resident_session_changed": resident_session_changed, "slot_changed": slot_changed,
            "unresolved": bool(unresolved), "phases_mono_ns": dict(phases_mono_ns), "t_wall_utc": t_wall_utc,
            "refusal": dict(refusal) if isinstance(refusal, dict) else None,
            "state_path": dict(state_path) if isinstance(state_path, dict) else None}


def refused_before_transaction(answer: dict, activation_state: str, detail) -> tuple:
    """(activation_state, detail, refusal): the shared verdict, with ONE re-reading of its own. An answer the resident gave
    as `status mutation_refused` with its refusal class (cake_activation.REFUSED_BEFORE_TRANSACTION) is Cake JUDGING the
    candidate before any transaction was opened. The shared verdict names it REFUSED_BEFORE_TRANSACTION (or, in a build
    whose verdict has no word for it, REQUEST_FAILED, which says Cake judged nothing); this names it for what it is, with
    the class and its words. It is never an activation and never moves anything up: the only states it accepts are those
    two. Every other answer is returned as it came, with refusal None."""
    if (answer.get("outcome") != cake_activation.REFUSED_BEFORE_TRANSACTION
            or activation_state not in (mission.ACTIVATION_FAILED, mission.ACTIVATION_REFUSED_BEFORE_TRANSACTION)):
        return activation_state, detail, None
    refusal = answer.get("refusal") if isinstance(answer.get("refusal"), dict) else cake_activation.refusal_label(
        answer.get("reason_code"))
    return cake_activation.REFUSED_BEFORE_TRANSACTION, answer.get("detail") or detail, dict(refusal)


def fold_checks(records: list) -> "collections.OrderedDict":
    """The file folded into one entry per operation uuid, in first-seen order. The answer's fields override the attempt's,
    and the two records' phase stamps are merged, so an entry carries every phase that was reached."""
    table: collections.OrderedDict = collections.OrderedDict()
    for record in records:
        if record.get("rec") != RECORD_KIND or record.get("v") != CHECK_RECORD_VERSION:
            continue
        uuid = record.get("operation_uuid")
        if not isinstance(uuid, str):
            continue
        entry = table.setdefault(uuid, {"operation_uuid": uuid, "answered": False, "phases_mono_ns": {}})
        phases = record.get("phases_mono_ns") if isinstance(record.get("phases_mono_ns"), dict) else {}
        entry.update({key: value for key, value in record.items() if key not in ("rec", "v", "phases_mono_ns")})
        entry["phases_mono_ns"].update(phases)
        # The answer record always carries `outcome` (None when the answer had none) and the attempt never does, so
        # that key is what tells them apart. A PENDING answer is an answer: the resident said its transaction runs.
        if "outcome" in record:
            entry["answered"] = True
        else:
            entry["attempt_wall_utc"] = record.get("t_wall_utc")
    return table


def check_view(entry: dict, *, runtime_session: str) -> dict:
    """One check as a console reads it: the fields of a mission activation (so the same component can draw it), and the
    check's own. An attempt with no answer that a PREVIOUS runtime wrote is said to be exactly that."""
    phases = entry.get("phases_mono_ns") or {}
    started, answered_at = phases.get("requested"), phases.get("host_after_read")
    orphaned = not entry.get("answered") and entry.get("runtime_session") != runtime_session
    # `held` is decided again HERE, by host_held, from what each read recorded, so every record is drawn by the one rule
    # whatever it was written with.
    host_before = ({**entry["host_before"], "held": host_held(entry["host_before"])}
                   if isinstance(entry.get("host_before"), dict) else None)
    host_after = ({**entry["host_after"], "held": host_held(entry["host_after"])}
                  if isinstance(entry.get("host_after"), dict) else None)
    throughout, throughout_detail = (held_throughout(host_before or {}, host_after) if host_after is not None
                                     else (None, None))
    transaction = entry.get("transaction") if isinstance(entry.get("transaction"), dict) else {}
    return {
        "operation_uuid": entry["operation_uuid"], "target_id": None,
        "candidate_key": entry.get("candidate_key"), "payload_digest": entry.get("payload_digest"),
        "activation_state": entry.get("activation_state"), "outcome": entry.get("outcome"),
        "transaction_phase": entry.get("transaction_phase"), "transaction_uuid": entry.get("transaction_uuid"),
        "detail": ("no answer was recorded: the runtime that sent this ended before its answer, and whether the resident "
                   "applied it is read from the slot, never guessed" if orphaned else entry.get("detail")),
        "t_wall_utc": entry.get("attempt_wall_utc"), "answer_wall_utc": entry.get("t_wall_utc") if entry.get("answered") else None,
        "slot_before": entry.get("slot_before"), "slot_after": entry.get("slot_after"), "transaction": entry.get("transaction"),
        "stopped_during_activation": None,
        "requested_session_uuid": entry.get("requested_session_uuid"),
        "host_before": host_before, "host_after": host_after,
        # HELD THROUGHOUT only when both reads said NO host process was there AND the supervisor's counters read the same
        # at both (held_throughout). None when the read after did not answer, or a read has no counters: not known is
        # not held. The detail says what differed, or why it is not known.
        "host_held_throughout": throughout, "host_throughout_detail": throughout_detail,
        # Whether the answer left the operation's fate open (answer_unresolved), for an answered check only.
        "unresolved": (answer_unresolved(entry.get("activation_state"), transaction.get("reason_code"))
                       if entry.get("answered") else False),
        "resident_session_changed": entry.get("resident_session_changed"),
        # Whether the slot changed, from the two readings on the record (slot_changed), for an answered check only.
        "slot_changed": slot_changed(entry.get("slot_before"), entry.get("slot_after")) if entry.get("answered") else None,
        "answered": bool(entry.get("answered")), "orphaned": orphaned,
        # The resident's refusal class when it refused the candidate before any transaction, else None.
        "refusal": entry.get("refusal") if isinstance(entry.get("refusal"), dict) else None,
        # ValidatePlan's verdict on the exact bytes, asked before the submit, and the transaction's state path read from
        # the resident's flight ring after the answer. Evidence beside the verdict; neither decides it.
        "validation": entry.get("validation") if isinstance(entry.get("validation"), dict) else None,
        "state_path": entry.get("state_path") if isinstance(entry.get("state_path"), dict) else None,
        "duration_ms": (round((answered_at - started) / 1e6, 3) if isinstance(started, int) and isinstance(answered_at, int)
                        and entry.get("runtime_session") == runtime_session else None),
        "phases_mono_ns": dict(phases) if entry.get("runtime_session") == runtime_session else None,
    }


class CapabilityCheck:
    """One slot, one set of prepared candidates, one check at a time, written down before it is attempted."""

    def __init__(self, path, *, capability: CapabilityAdapter, candidates, read_binding: Callable[[], dict],
                 runtime_session: str, close_wait_s: float, now_text: Callable[[], str] = utc_now_text,
                 clock_ns: Callable[[], int] = time.monotonic_ns,
                 validate: Optional[Callable[[bytes], dict]] = None, read_flight: Optional[Callable[[], str]] = None):
        self._path = str(path)
        # How long close() waits for a check in flight: close_wait_bound_s of the figures the adapter was built with.
        # REQUIRED, never a fixed default, so it cannot fall behind the bounds it covers.
        self.close_wait_s = float(close_wait_s)
        # Set once by begin_close (and close). A check that has not written its attempt record refuses when it sees it.
        self._closing = threading.Event()
        self._journal = Journal(self._path)
        self._capability = capability
        self.candidates = candidates
        # A FRESH read of what the robot's resident and its supervisor slot report now (daemon._current_robot_binding):
        # build identity checked first, then the session and the host. Injected so the daemon's one reader is used.
        self._read_binding = read_binding
        self._runtime_session = runtime_session
        self._now, self._clock = now_text, clock_ns
        # TRIED, NEVER WAITED ON. Held from the claim to the answer record.
        self._lock = threading.Lock()
        # An operation the resident answered PENDING (its transaction still running) in THIS runtime. It keeps refusing
        # a new check: a transaction still running is retrieved by reading the slot, never replaced on a guess.
        self._pending: Optional[str] = None
        # ValidatePlan of the exact bytes before every submit (cake_activation.plan_validator) and the read-flight right
        # after every answer (cake_activation.flight_reader). Both optional: without one, the record says `not_asked`.
        # Neither gates the submit: an invalid verdict is recorded and the resident still judges the apply itself, which is
        # what a refusal before any transaction shows.
        self._validate = validate
        self._read_flight = read_flight

    # ---- reads -----------------------------------------------------------------------------------------------------
    def in_flight(self) -> bool:
        """True while a check holds the slot. The daemon's run-slot gate reads this; it never blocks."""
        return self._lock.locked()

    @property
    def pending_operation(self) -> Optional[str]:
        return self._pending

    def checks(self) -> tuple:
        """(the checks on the record as a console reads them, newest last and bounded, how many were left out, damaged)."""
        records, damaged = read_json_lines(self._path)
        views = [check_view(entry, runtime_session=self._runtime_session) for entry in fold_checks(records).values()]
        dropped = max(0, len(views) - MAX_CHECKS_SHOWN)
        return views[dropped:], dropped, damaged

    # ---- the one mutation ------------------------------------------------------------------------------------------
    def apply(self, candidate_key: str, payload_digest: str, session_uuid: str, *, claim: Callable[[], Optional[str]]) -> dict:
        """Apply ONE prepared candidate to the capability slot, bracketed by fresh reads, and record all of it.

        THE ORDER, and every refusal before the attempt record submits nothing:
          1. the key must name a prepared candidate, and `payload_digest` must be that candidate's digest: the bytes a
             caller was shown are the bytes that would be submitted, or nothing is;
          2. this check's lock, TRIED: a check already in flight refuses (capability_check_in_flight);
          3. a runtime that has begun to close refuses (capability_check_unavailable, runtime_closing);
          4. `claim()`, the daemon's run-slot check, called with the lock held so that from here on no command but stop
             and no mission can start; it answers a reason code (a mission or a run holds the slot, an unresolved
             command) or None;
          5. a PENDING operation of this runtime refuses, naming it;
          6. a FRESH binding read: the pinned build, the session the caller named, and the host HELD, meaning no host
             process under the supervisor slot (binding_refusal, host_held);
          7. the capability slot, read just before the submit, must report the same session;
          8. the closing test again, the last thing before the attempt record.
        Then the attempt record, the submit (cake_activation, through the robot's capability adapter, which reads the
        build identity again before the ApplyPlan), the slot read back, the host read again, the verdict (narrowed down
        for a changed session or an unchanged slot) and the answer record. The answer is returned with both slot readings,
        whether the slot changed, both host readings, whether the host was held throughout (held_throughout) and whether
        the operation's fate is still open (answer_unresolved: that keeps the next check refused)."""
        requested = self._clock()
        payload = self.candidates.payload(candidate_key)
        if payload is None:
            return {"ok": False, "reason_code": UNKNOWN_CANDIDATE, "detail": self.candidates.problem(candidate_key)}
        if self.candidates.digest(candidate_key) != payload_digest:
            return {"ok": False, "reason_code": DIGEST_MISMATCH}
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "reason_code": IN_FLIGHT, "operation_uuid": self._pending}
        try:
            return self._apply_holding_lock(candidate_key, payload_digest, payload, session_uuid, claim, requested)
        finally:
            self._lock.release()

    def _apply_holding_lock(self, candidate_key, payload_digest, payload, session_uuid, claim, requested) -> dict:
        phases = {"requested": requested}
        if self._closing.is_set():
            return {"ok": False, "reason_code": UNAVAILABLE, "detail": RUNTIME_CLOSING}
        refused = claim()
        if refused is not None:
            return {"ok": False, "reason_code": refused}
        phases["claimed"] = self._clock()
        if self._pending is not None:
            return {"ok": False, "reason_code": IN_FLIGHT, "operation_uuid": self._pending}
        before = self._read_binding()
        phases["host_before_read"] = self._clock()
        refusal = binding_refusal(before, session_uuid)
        if refusal is not None:
            return {"ok": False, "reason_code": refusal[0], "detail": refusal[1]}
        slot_before = self._read_slot()
        phases["slot_before_read"] = self._clock()
        if slot_before.get("session_uuid") != session_uuid:
            # The slot was read from another resident session than the one the caller named and the binding read just
            # reported (or it could not be read at all): the reading this check would be compared against is not one.
            return {"ok": False, "reason_code": SLOT_SESSION_MISMATCH, "detail": slot_before.get("read")}
        # VALIDATEPLAN OF THE EXACT BYTES THAT WILL BE SUBMITTED, a read-only question, recorded in the attempt record.
        validation = self._validation(payload)
        phases["validated"] = self._clock()
        # THE LAST TEST BEFORE ANYTHING IS WRITTEN OR SENT. A runtime that began to close while the reads above ran gets
        # no new ApplyPlan: from here on the check can only spend what close_wait_bound_s covers.
        if self._closing.is_set():
            return {"ok": False, "reason_code": UNAVAILABLE, "detail": RUNTIME_CLOSING}
        operation_uuid = cake_activation.new_operation_uuid()
        host_before = host_of(before)
        # WRITTEN BEFORE THE SUBMIT, binding the uuid to the digest, with everything read before it.
        phases["attempt_written"] = self._clock()
        self._journal.append(check_attempt(operation_uuid, candidate_key, payload_digest, requested_session_uuid=session_uuid,
                                           runtime_session=self._runtime_session, host_before=host_before,
                                           slot_before=slot_before, phases_mono_ns=phases, t_wall_utc=self._now(),
                                           validation=validation))
        answered_phases = {"submitted": self._clock()}
        answer = self._capability.submit(operation_uuid, payload)
        answered_phases["answered"] = self._clock()
        # THE RING FIRST: every admin request writes a record into it, so it is read before the reads below add theirs.
        flight_text = self._flight_text()
        answered_phases["flight_read"] = self._clock()
        slot_after = self._read_slot()
        answered_phases["slot_after_read"] = self._clock()
        # AFTER THE SUBMIT NOTHING MAY RAISE before the answer is written: the resident may have applied the operation, and
        # losing its answer record would leave the attempt reading as in flight. A read that fails is recorded as one.
        try:
            after = self._read_binding()
        except Exception:  # noqa: BLE001 -- a failed read is a failed read, never a lost record
            after = {"reason_code": "host_read_failed"}
        answered_phases["host_after_read"] = self._clock()
        host_after = host_of(after)
        activation_state, detail = activation_verdict(answer, slot_after, self._capability.observe)
        # CAKE JUDGED IT BEFORE ANY TRANSACTION. The shared verdict has no word for this and answers REQUEST_FAILED, which
        # says Cake judged nothing; the resident's refusal class says it did (refused_before_transaction).
        activation_state, detail, refusal = refused_before_transaction(answer, activation_state, detail)
        sessions_after = [value for value in (slot_after.get("session_uuid"), host_after.get("session_uuid")) if value is not None]
        changed = (any(value != session_uuid for value in sessions_after) if sessions_after else None)
        moved = slot_changed(slot_before, slot_after)
        # THE TWO NARROWINGS, both only ever DOWN from OBSERVED (see the module docstring). The session first, because a
        # slot read from another resident session says nothing about this one, changed or not.
        if changed and activation_state == mission.ACTIVATION_OBSERVED:
            activation_state, detail = mission.ACTIVATION_COMPLETED_NOT_OBSERVED, SESSION_CHANGED_DETAIL
        elif moved is not True and activation_state == mission.ACTIVATION_OBSERVED:
            activation_state = mission.ACTIVATION_COMPLETED_NOT_OBSERVED
            detail = SLOT_UNCHANGED_DETAIL if moved is False else SLOT_CHANGE_UNKNOWN_DETAIL
        transaction = transaction_evidence(operation_uuid, answer)
        # AN OPEN FATE KEEPS THE CHECK CLOSED: a transaction the resident says still runs, or an ApplyPlan whose answer
        # did not arrive in time (it may still be running; cake_activation's rule). Never a second ApplyPlan on a guess.
        unresolved = answer_unresolved(activation_state, transaction.get("reason_code"))
        if unresolved:
            self._pending = operation_uuid
        if activation_state == mission.ACTIVATION_FAILED and transaction.get("reason_code") == SUBMIT_TIMED_OUT:
            detail = TIMED_OUT_DETAIL
        throughout, throughout_detail = held_throughout(host_before, host_after)
        state_path = self._state_path(flight_text, answer.get("transaction_uuid"))
        self._journal.append(check_answer(operation_uuid, activation_state, outcome=answer.get("outcome"),
                                          transaction_phase=answer.get("transaction_phase"),
                                          transaction_uuid=answer.get("transaction_uuid"), detail=detail,
                                          transaction=transaction, slot_after=slot_after, host_after=host_after,
                                          resident_session_changed=changed, slot_changed=moved, unresolved=unresolved,
                                          phases_mono_ns=answered_phases, t_wall_utc=self._now(), refusal=refusal,
                                          state_path=state_path))
        phases.update(answered_phases)
        return {"ok": True, "operation_uuid": operation_uuid, "candidate_key": candidate_key,
                "payload_digest": payload_digest, "activation_state": activation_state, "outcome": answer.get("outcome"),
                "transaction_phase": answer.get("transaction_phase"), "detail": detail, "transaction": transaction,
                "requested_session_uuid": session_uuid, "slot_before": slot_before, "slot_after": slot_after,
                "slot_changed": moved, "host_before": host_before, "host_after": host_after,
                "host_held_throughout": throughout, "host_throughout_detail": throughout_detail,
                "resident_session_changed": changed, "unresolved": unresolved, "refusal": refusal,
                "validation": validation, "state_path": state_path,
                "duration_ms": round((answered_phases["host_after_read"] - phases["requested"]) / 1e6, 3)}

    def _validation(self, payload: bytes) -> dict:
        """ValidatePlan's answer for these bytes. It NEVER raises and never gates: a validator that fails is recorded as
        unanswered, and one that is not configured as not asked."""
        if self._validate is None:
            return {"verdict": cake_activation.PLAN_NOT_ASKED}
        try:
            answer = self._validate(payload)
        except Exception:  # noqa: BLE001 -- a question that failed is unanswered, never a lost apply
            answer = None
        if not isinstance(answer, dict) or answer.get("verdict") not in (cake_activation.PLAN_VALID, cake_activation.PLAN_INVALID,
                                                                         cake_activation.PLAN_UNANSWERED):
            return {"verdict": cake_activation.PLAN_UNANSWERED, "reason_code": "validation_read_failed"}
        return dict(answer)

    def _flight_text(self):
        """The read-flight text after an answer, None when no reader is configured, "" for a read that failed. It NEVER
        raises: it runs after a submit, and an exception there would lose the answer record."""
        if self._read_flight is None:
            return None
        try:
            text = self._read_flight()
        except Exception:  # noqa: BLE001 -- a failed read is a failed read
            return ""
        return text if isinstance(text, str) else ""

    @staticmethod
    def _state_path(flight_text, transaction_uuid) -> dict:
        if flight_text is None:
            return {"read": cake_activation.PATH_NOT_ASKED, "states": [], "terminal": None}
        try:
            return cake_activation.transaction_state_path(flight_text, transaction_uuid)
        except Exception:  # noqa: BLE001 -- a path that cannot be rebuilt is a failed read, never a lost record
            return {"read": cake_activation.READ_FAILED, "states": [], "terminal": None}

    def _read_slot(self) -> dict:
        """One slot reading. It NEVER raises: it is also called after a submit, and an exception there would lose the
        record of an operation the resident may have applied. A read that failed is recorded as a failed read."""
        reader = self._capability.read_slot
        try:
            reading = reader() if reader is not None else None
        except Exception:  # noqa: BLE001 -- a read that failed is a failed read, recorded as one
            reading = None
        if not isinstance(reading, dict):
            return {"read": cake_activation.READ_FAILED, "capability_active": False, "session_uuid": None}
        return dict(reading)

    def begin_close(self) -> None:
        """From now on no check writes an attempt record or submits: one that has not passed the closing test in front of
        its attempt record refuses there (UNAVAILABLE, RUNTIME_CLOSING). A check already past it runs to its answer."""
        self._closing.set()

    def close(self, *, wait_s: Optional[float] = None) -> None:
        """Close the file, after a check in flight has written its answer, waiting at most `wait_s` (close_wait_s).

        THE ONE PLACE THIS LOCK IS WAITED ON, and only by a runtime that is ending. begin_close first, so the wait covers
        only what a check past its attempt record can still spend (close_wait_bound_s). Closing underneath an apply would
        make its answer record raise on the request thread, and the record would then say an operation was attempted and
        never answered when the resident may well have applied it. A check that outlives the wait (a child that overran
        its own bound, or the process killed before this returns) loses its answer record rather than holding the
        runtime's shutdown open, and its attempt record says what was in flight."""
        self.begin_close()
        took = self._lock.acquire(timeout=self.close_wait_s if wait_s is None else wait_s)
        try:
            self._journal.close()
        finally:
            if took:
                self._lock.release()
