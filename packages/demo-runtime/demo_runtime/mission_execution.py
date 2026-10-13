# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.mission_execution: the ROBOT-SIDE execution of a two-cube mission. The proven single-cube controller runs
beneath the persisted mission coordinator, and the hybrid state machine is refused on hardware.

THE OPERATOR'S RULINGS THIS FILE CARRIES, in their words:
  - "proven controller beneath the persisted coordinator; hybrid kept for rehearsal only";
  - "Record the precise GRASP_POSITION request, the guard that refuses it and the incompatible precondition. Keep that
    refusal intact." (HYBRID_GRASP_REQUEST and refuse_hybrid_arm_request below);
  - "Keep LIVE execution unavailable until this real path is connected and verified. Never map the rehearsal's
    confirmation answers into physical permission." Nothing here answers a judgment gate: HardwareExecution.confirm
    RAISES, and this module never imports demo_runtime.mission_rehearsal (a test reads its imports to keep it so). Whether
    a LIVE runtime builds any of this is decided by demo_runtime.mission_service, and it ships refusing;
  - "Stopping the mission must prevent a late perception or activation result from starting further execution" (THE
    STOP, below);
  - "Desktop operation must go through the authenticated robot-side service and its existing session, ownership and
    command-admission checks": every run here is started by supervisor.start_mission_run, which is the submit path's
    own admission (the resident read twice through the local admin socket on this robot, whose current session is what
    says which deployment answered, and whose build identity, which identifies SOFTWARE, is pinned only to refuse a
    different build; block(); the lease), and nothing else in this file starts a process;
  - "Place the capability gate before any movement that requires mobile_approach. [...] The need for the capability must
    follow the target's measured reachability, not merely its ordinal position in the mission." (THE CAPABILITY, below.)

WHAT ONE TARGET IS (run_proven_target, the coordinator's injected run_target):
  0. REFUSED with no run while the configuration names no claim and match radii (mission.radii): every target fails
     mission_radii_unqualified (autonomy/manipulation/target_binding.py, THE TWO RADII, says what they decide). The
     section is either the operator's own numbers with their stated basis or the word "cube_half_edge", the chosen
     association rule of half a cube edge (demo_runtime.config resolves it through target_binding.radii_from_cube_geometry, with its
     basis); absent, nothing is spawned and no gate is passed;
  1. THE CAPABILITY, decided on the MEASURED mode, never on the declaration alone or the position. A target whose cube the
     first execution's SEARCH bound as BASE_ADVANCE needs it; a first-execution target nothing has measured yet is spawned
     WITHOUT it, and its own SEARCH measures and, for BASE_ADVANCE, refuses mission_capability_required before anything
     is picked up. An AUTO target (requires_capability "auto", mission.REQUIRES_CAPABILITY_AUTO: the target takes the
     mode its SEARCH measures) is that first-execution case every time: spawned without the capability, placed by that
     one run when it measured ARM_ONLY, and otherwise measured, admitted and respawned exactly as below; the only thing
     the declaration decides is whether the capability MAY be needed, which is what asks for configured endpoints
     before any run. Under the controller's additive placement words an auto target's SEARCH may also bind the ARM ALONE
     and still ask for the capability (FALLBACK_CAPABILITY_REQUIRED, the selection ARM_ONLY): the lead's decision 3 of
     2026-09-30 goes on in the same run with a policy that moves the base when the arm alone is refused and the loaded
     arm came back to the carry pose (a loaded arm the controller's way back cannot bring back is held where it stands,
     the cube held), so the need follows that measurement too, and it is admitted and respawned exactly as below (source SOURCE_SEARCH_FALLBACK). In both cases the runtime then asks target_observation_source.capability_observation_available()
     FIRST: without an actual observation of the target the capability could be fed, the target FAILS
     (mission_capability_observation_unavailable) with no gate and no spawn. Under the v2 observation contract
     (kc.target_observation/2: the held cube's remaining bounded advance in the executor's own anchor_reach frame, from
     the pad frame of a stationary capture) a mapping EXISTS, so that source answers (True, None) and per-run
     availability is decided inside the controller by target_observation_source.resolve, at the advance and never
     before; this runtime does not read the answer's value beyond available or not, and its tests stand in both. Only
     when one is available is the coordinator's capability gate passed, BEFORE the child is spawned and therefore never
     with a cube held (operator decision D1), and the run is started carrying the capability's endpoints; the SEARCH
     refusal is answered by exactly one such respawn, and a second refusal is the target's failure. The need is written,
     with where it was measured, to the mission's evidence file (mission.target_capability_required). The gate is the
     coordinator's own: it records the wait and the resume, and it raises MissionCancelled once a stop was asked for,
     whatever the slot now carries;
  2. the UNCHANGED `run_single_cube_cycle.py --do cycle` is started as a supervised child through
     supervisor.start_mission_run. The one addition is the mission target file in the run directory, named to the child
     by KC_MISSION_TARGET (autonomy/manipulation/target_binding.py owns its schema);
  3. the run is waited for with cancel.wait(WAIT_POLL_S), and a stop seen there is passed to supervisor.stop; every turn
     of that wait pauses WAIT_POLL_S, a stop or not, and a daemon shutdown is never passed on as a stop (_wait_for_run);
  4. the outcome is MAPPED, never assumed. PLACED only when ALL hold: exit status 0; outcome placed_new; the three
     placement predicates object_secured, placed and base_returned true in the run's own completion record; and the
     child's mission result file, which must echo THIS mission_id, target_id and run_id (target_binding refuses one that
     does not), claims the placement (target_binding.result_claims_placement: selected by its binding, a block on the
     pad at the run's own release point (the pad policy of 2026-09-29), and for a capability target at least one relayed command
     APPLIED by the motion gateway, `applied` in the result's capability evidence, since the relay counts a command as
     accepted when it submits it and the gateway may still reject every one of a pulse as expired or replayed).
     already_placed_noop, refused, failed and unknown are FAILED with the reason. A run that ended STOPPED
     while this mission's cancel is set is a stop (RunStopped, a MissionCancelled carrying where the run left the robot),
     never a failure;
  5. the attempt is CLASSIFIED for the coordinator's hold (classify_outcome, mission.OUTCOME_*), from the run's completion
     record, its mission result and the STOP STATE its own events give (stop_state_from_events: whether a cube may be held,
     whether the base is ahead, whether anything moved). The controller's reason code, phase, measurements and observation
     ids ride with it (failure_facts) and are kept in the target's evidence. Nothing here decides to hold or to retry; the
     coordinator does, and only a retryable or operator-needed class with nothing held is ever offered a Retry.

THE ASSOCIATION. The run id is the supervisor's, which is also the controller's scan id and its observation store
session. It is written to the mission's own file BEFORE the spawn (mission.target_run_started, through
MissionCoordinator.on_run_started), the first execution's bindings are written per target (mission_target_bound), and the
outcome carries an `evidence` object of ids and numbers only. Between targets this adapter carries the bound and placed
positions forward in memory (target_binding.targets_after_placement), because a later target's file must say where every
earlier target was placed; a mission is never resumed after a restart, so memory is where they belong.

THE STOP, in three layers, the first two outside this file:
  - the daemon sets the mission's cancel event FIRST (MissionCoordinator.cancel_now, no lock, no fsync), then calls the
    supervisor's stop, then the coordinator's;
  - the supervisor reads that event inside the lock that publishes a mission's run, so a stop that came first starts
    nothing, and a stop that came after finds the run and signals it through its pidfd;
  - here, the wait reads the event every WAIT_POLL_S and calls supervisor.stop when it sees it, so a stop that raced
    past both is still delivered to the child this adapter started. A shutdown also sets the event, AFTER it detached
    the supervisor; the wait reads the event before `detached`, so a shutdown ends the mission RUNTIME_DETACHED and
    sends the controller nothing.
A perception already running in the child cannot be interrupted, and its late result cannot move anything: the proven
controller checks its abort flag before every arm segment, gripper waypoint and base pulse, and in mission mode refuses
to START a perception once aborted (run_single_cube_cycle.py, MISSION MODE). A late ACTIVATION answer cannot resume
anything either: the capability gate raises after a stop, and only the carrying thread can spawn. The capability's
admission reads the stop before it asks the observation source, so a stop that follows a measuring run ends the mission
STOPPED, never FAILED or respawned, whatever the source would have answered.

SOFTWARE COVERAGE DOES NOT TRANSFER PHYSICAL QUALIFICATION. Every test of this file runs the synthetic fake controller.
Whether the relayed pulses, the arm-only placement and the stop latency hold on the robot is for an attended test.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import mission
from .journal import Journal, read_json_lines, utc_now_text
from .mission_coordinator import ExecutionAdapter
from .mission_errors import ExecutionRefused, HardwareMappingUnavailable, MissionCancelled
from .supervisor import CONTROLLER_EVENTS_FILE, MISSION_RESULT_FILE, MISSION_TARGET_FILE, STOP_EXIT_STATUS, MissionRun

#: What this adapter calls itself in the mission's first record. It performs motion, and it says which controller does.
LABEL = "proven-single-cube-controller"

#: How often the wait for a run re-reads the mission's cancel event. It is the bound on how long a stop that raced past
#: the daemon's own stop order can go undelivered to the child this adapter started.
WAIT_POLL_S = 0.2

#: The origin of the stop this adapter passes on. contract v1's stop.requested has one origin for a person pressing Stop,
#: and a mission's stop is one.
STOP_ORIGIN = "operator_console"

#: The capability a target that needs one waits for, in the name the hybrid machine and the coordinator's gate use.
CAPABILITY_NAME = "mobile_approach"

#: The reason codes a target's outcome can carry. Codes, never sentences with values in them.
RUN_NOT_STARTED = "mission_run_not_started"
RESULT_REFUSED = "mission_result_refused"
PLACEMENT_NOT_CLAIMED = "mission_placement_not_claimed"
RUNTIME_DETACHED = "mission_runtime_detached"
RADII_UNQUALIFIED = "mission_radii_unqualified"
CAPABILITY_OBSERVATION_UNAVAILABLE = "mission_capability_observation_unavailable"
MEASURED_MODE_NOT_DECLARED = "mission_measured_mode_not_declared"
PLACEMENT_PREDICATES = ("object_secured", "placed", "base_returned")

#: The controller's SEARCH refusal of a cube that measures BASE_ADVANCE in a run that carries no capability, and the state
#: its run ends in (run_single_cube_cycle.Cycle._mission_capability_gate, after TARGET_SELECTED). Spelled here, because
#: this module never imports the controller; autonomy/tests/test_cycle_mission_mode.py keeps both equal to what the
#: controller really writes.
CAPABILITY_REQUIRED = "mission_capability_required"
SEARCH_REFUSAL_FINAL_STATE = "TARGET_SELECTED"
#: The controller's SEARCH refusal, under the additive placement words only, of an auto target bound to the ARM ALONE (its
#: selection ARM_ONLY) whose in-run fallback moves the base, in a run that carries no capability
#: (run_single_cube_cycle.Cycle._mission_fallback_capability_gate): the lead's decision 3 of 2026-09-30 goes on in the SAME
#: run with a policy that moves the base when the arm alone is refused, and the capability is only ever given before a spawn
#: (D1). autonomy/tests/test_reach_policies_execution.py holds the controller's result to this reading.
FALLBACK_CAPABILITY_REQUIRED = "mission_fallback_capability_required"
#: The controller's exit status for a CycleFailed (the arm holds): the SEARCH refusal ends that way.
CYCLE_FAILED_EXIT_STATUS = 5
#: target_binding.MODE_BASE_ADVANCE, the measured mode that needs the capability; target_binding.MODE_ARM_ONLY, the mode of a
#: selection whose in-run fallback needs it.
MODE_BASE_ADVANCE, MODE_ARM_ONLY = "BASE_ADVANCE", "ARM_ONLY"
#: Where the need for the capability was measured (mission.target_capability_required): the first execution's binding, this
#: target's own SEARCH, or this target's own SEARCH for the fallback of an arm-alone binding.
SOURCE_BINDING, SOURCE_SEARCH, SOURCE_SEARCH_FALLBACK = "binding", "search", "search_fallback"
#: What a measuring run's SEARCH refusal asks the capability for: its code, the selection's mode it comes with, and the
#: source its need is recorded under.
SEARCH_CAPABILITY_REFUSALS = {CAPABILITY_REQUIRED: (MODE_BASE_ADVANCE, SOURCE_SEARCH), FALLBACK_CAPABILITY_REQUIRED: (MODE_ARM_ONLY, SOURCE_SEARCH_FALLBACK)}

# ---- what an attempt came to: the class the hold decides on ---------------------------------------------------------
#
# The controller's own reason codes (run_single_cube_cycle.py, Cycle.failed), spelled here because this module never
# imports the controller. A code missing from these lists is FATAL: the mission ends as it always did, and a Retry is
# never offered for a failure nobody classified.
#
# RETRYABLE: the run failed at a stage that leaves nothing held (the jaws never closed on anything, or reopened after an
# empty close) and the base where it started, with the controller's CycleFailed exit (the arm holds where it stopped). A
# Retry parks the arm with the unchanged EXIT and starts a fresh cycle whose SEARCH observes anew.
#
# The grasp stage's words (policy_grasp.py) add endings of the same kind: after an empty close the jaws reopened and, under
# grasp_measured_retry, lifted open to the look height, or under grasp_recover the block was set down and the jaws opened
# and lifted off it. Each says "nothing is held"; the stop state below is what decides it, never the code alone.
RETRYABLE_CODES = frozenset({"pregrasp_no_non_contact_pose", "straddling_not_reached", "grasp_missed",
                             "search_no_pad_frame", "search_cube_not_in_pad_frame",
                             "grasp_retry_cube_lost", "grasp_retry_not_aligned", "grasp_retry_no_untried_radius",
                             "retention_not_demonstrated_set_down"})
RETRYABLE_PREFIXES = ("align_", "search_no_cube")
# OPERATOR NEEDED, AND NO RETRY: nothing is held, and still a Retry may not follow. Checked before the lists above (an
# align_ code here is not retryable by its prefix).
#   - The park would start where no recorded exit started. A lift or an approach was refused part way, so the arm stands
#     low at the block with the jaws open, where the block stands between or beside them: grasp_retry_lift_refused and
#     grasp_set_down_lift_refused (the open lift off the floor refused), grasp_retry_approach_refused (the arm holds where
#     the retry's descent stopped). The park is an in-plane rise with the pan held (`--do exit`), not a vertical lift.
#   - ALIGN refused the block as nearer than it may reach (align_inside_grasp_radius): a fresh cycle would reach toward
#     the same block and be refused again.
#   - The target binding refused this SEARCH (mission_binding_no_candidate, mission_binding_candidate_not_placeable): the
#     block has to be placed by a person, and the controller's sentence (the deciding limit, its shortfall and the hint)
#     is what the hold says. The operator's action is the Stop, then a new mission.
NO_RETRY_CODES = frozenset({"grasp_retry_lift_refused", "grasp_set_down_lift_refused", "grasp_retry_approach_refused",
                            "align_inside_grasp_radius", "mission_binding_no_candidate", "mission_binding_candidate_not_placeable"})
# OPERATOR NEEDED before any motion: the placement envelope refused the block where it lies. The block has to be moved
# before a retry can do anything else.
ENVELOPE_REFUSAL_CODES = frozenset({"search_placement_infeasible", "search_cube_beyond_return_bound",
                                    "search_cube_beyond_pregrasp_entry_bound", "search_cube_inside_pregrasp_reach"})
# OPERATOR NEEDED after motion, nothing held: near_grasp refused the block where ALIGN measured it, outside this run's grasp
# range, before the approach. A fresh cycle would find it where it is, so the block has to be moved before a retry.
# The scene scan of SEARCH (policy_search.py, under scene_scan) ended back at the saved low pose having seen no block, or
# more than one block that is not on the pad: a fresh cycle would see the same scene, so the blocks have to be arranged
# first.
BLOCK_MOVE_CODES = frozenset({"approach_cube_beyond_grasp_range", "search_scene_no_block", "search_scene_not_one_unplaced_block"})
# BASE ONLY: the cube is placed, and the base is not back within its tolerance.
BASE_ONLY_CODES = frozenset({"base_not_returned"})

#: The stop state of a run that never started: nothing moved and nothing is held.
NOTHING_RAN = {"holding": False, "base_ahead": False, "moved": False}
#: The stop state when the run's events cannot be read: every part unknown, which is never read as safe.
UNKNOWN_STATE = {"holding": None, "base_ahead": None, "moved": None}


def stop_state_from_events(events) -> dict:
    """Where a run left the robot, from the run's OWN contract events, in order: {holding, base_ahead, moved}.

      - moved: an action.started was recorded (the controller states it at the first command of an action);
      - holding: the jaws closed on something (a closed_on_something predicate that measured true, or object_secured true)
        and no RELEASED followed, nor a GRASP_SET_DOWN (grasp_recover) whose jaws then measured open (a gripper_opened
        predicate true after it). A GRASP_CLOSE with no verdict after it is None, unknown, never read as empty;
      - base_ahead: a BASE_ADVANCE was entered and no base_returned predicate measured true after it.
    Numbers are not read here: this is the state a person must know before anything else moves."""
    moved, holding, base_advanced, base_returned, open_close, set_down = False, False, False, False, False, False
    for event in events:
        if not isinstance(event, dict):
            continue
        kind, payload = event.get("kind"), event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind == "action.started":
            moved = True
        elif kind == "state.transition":
            to_state = payload.get("to_state")
            if to_state == "GRASP_CLOSE":
                open_close, set_down = True, False
            elif to_state == "RELEASED":
                holding, open_close, set_down = False, False, False
            elif to_state == "GRASP_SET_DOWN":
                set_down = True
            elif to_state == "BASE_ADVANCE":
                base_advanced, base_returned = True, False
        elif kind == "predicate.evaluated":
            name, value = payload.get("predicate"), payload.get("value")
            if name == "closed_on_something" and isinstance(value, bool):
                holding, open_close = holding or value, False
            elif name == "object_secured" and value is True:
                holding, open_close = True, False
            elif name == "gripper_opened" and value is True and set_down:
                holding, open_close, set_down = False, False, False
            elif name == "base_returned" and value is True:
                base_returned = True
    return {"holding": None if open_close and not holding else holding, "base_ahead": base_advanced and not base_returned,
            "moved": moved}


def read_stop_state(run_dir) -> dict:
    """stop_state_from_events over the run's controller events file, or UNKNOWN_STATE when there is no file to read or a
    line of it is damaged: what a damaged line said is not known, so the stream is never read as complete."""
    if run_dir is None:
        return dict(UNKNOWN_STATE)
    path = os.path.join(os.fspath(run_dir), CONTROLLER_EVENTS_FILE)
    try:
        events, damaged = read_json_lines(path)
    except (OSError, ValueError):
        return dict(UNKNOWN_STATE)
    if not events or damaged:
        return dict(UNKNOWN_STATE)
    return stop_state_from_events(events)


def failure_facts(completion: Optional[dict], result: Optional[dict]) -> dict:
    """{reason_code, phase, measurements, observation_ids, detail}: what the controller said about why its run ended, from
    its run.finished (the completion record's `result`), the mission result file standing in for the code and the state
    when the event did not carry them. The codes and the numbers are what the class is decided on; the sentence (`detail`,
    the failure's own, or None) is never read to decide anything and is kept only for the hold to say."""
    payload = (completion or {}).get("result") or {}
    failure = payload.get("failure") if isinstance(payload.get("failure"), dict) else {}
    result = result or {}
    reason = payload.get("reason_code") or result.get("reason_code")
    phase = payload.get("phase") or result.get("final_state")
    return {"reason_code": reason if isinstance(reason, str) else None, "phase": phase if isinstance(phase, str) else None,
            "measurements": mission._bounded_measurements(failure.get("measurements")),
            "observation_ids": [item for item in (failure.get("observation_ids") or []) if isinstance(item, str)][:mission.MAX_ATTEMPT_ITEMS],
            "detail": failure.get("detail")[:mission.MAX_ATTEMPT_DETAIL_CHARS] if isinstance(failure.get("detail"), str) else None}


def _retryable(code) -> bool:
    return isinstance(code, str) and code not in NO_RETRY_CODES and (code in RETRYABLE_CODES or code.startswith(RETRYABLE_PREFIXES))


def retry_allowed(reason_code) -> bool:
    """Whether a Retry may follow an ending with this code at all (NO_RETRY_CODES say no). The class and the stop state
    decide the rest (mission.next_actions)."""
    return reason_code not in NO_RETRY_CODES


def classify_outcome(completion: Optional[dict], *, result: Optional[dict] = None, stop_state: Optional[dict] = None) -> str:
    """The class (mission.OUTCOME_*) of ONE finished run of the cycle, from its completion record, its mission result and
    the stop state its events give. In this order, the first that holds:
      stopped          the run ended by a stop;
      placed           exit 0, placed_new and the three placement predicates true (the mission's claim is checked apart);
      operator_needed  a cube is held (a Retry would park an arm that holds a cube);
      fatal            the stop state is not known in full (never a guess that nothing is held);
      base_only        the cube was placed and the base did not return (base_not_returned);
      operator_needed  the base is ahead of where it started;
      retryable        a RETRYABLE code with the controller's CycleFailed exit;
      operator_needed  a BLOCK_MOVE or a NO_RETRY code with the CycleFailed exit (the block has to be moved, or the
                       operator decides: no Retry follows a NO_RETRY code);
      operator_needed  an envelope refusal before any motion;
      fatal            anything else."""
    completion = completion or {}
    payload = completion.get("result") or {}
    outcome, exit_status = completion.get("outcome"), completion.get("exit_status")
    if outcome == "stopped" or exit_status == STOP_EXIT_STATUS:
        return mission.OUTCOME_STOPPED
    predicates = payload.get("predicates") or {}
    if exit_status == 0 and outcome == "placed_new" and all(predicates.get(name) is True for name in PLACEMENT_PREDICATES):
        return mission.OUTCOME_PLACED
    state = stop_state if isinstance(stop_state, dict) else UNKNOWN_STATE
    reason = failure_facts(completion, result)["reason_code"]
    if state.get("holding") is True:
        return mission.OUTCOME_OPERATOR_NEEDED
    if state.get("holding") is None or state.get("base_ahead") is None or state.get("moved") is None:
        return mission.OUTCOME_FATAL
    if reason in BASE_ONLY_CODES:
        return mission.OUTCOME_BASE_ONLY
    if state.get("base_ahead") is True:
        return mission.OUTCOME_OPERATOR_NEEDED
    if _retryable(reason) and exit_status == CYCLE_FAILED_EXIT_STATUS:
        return mission.OUTCOME_RETRYABLE
    if reason in (BLOCK_MOVE_CODES | NO_RETRY_CODES) and exit_status == CYCLE_FAILED_EXIT_STATUS:
        return mission.OUTCOME_OPERATOR_NEEDED
    if reason in ENVELOPE_REFUSAL_CODES and state.get("moved") is False:
        return mission.OUTCOME_OPERATOR_NEEDED
    return mission.OUTCOME_FATAL


class RunStopped(MissionCancelled):
    """A stop ended the run this adapter started for the mission: MissionCancelled, carrying the run id and where the run
    left the robot (its stop state), so the stop's record can say what the operator does next."""

    def __init__(self, run_id, stop_state: dict):
        super().__init__()
        self.run_id, self.stop_state = run_id, dict(stop_state)


# ---- the GRASP_POSITION record --------------------------------------------------------------------------------------
#
# THE REQUEST (autonomy/manipulation/hybrid_pick_place.py, _state_grasp_position). After PRECHECK, which checks only that
# the base reads stationary and sets arm_frozen, the hybrid machine logs ARM_MOVE_REQUEST and calls
#
#     move_arm({"shoulder_pan": -10.549, "shoulder_lift": 43.121, "elbow_flex": 54.286, "wrist_flex": -33.714}, 2.0, 0.0)
#
# The pose is KNOWN_GOOD_GRASP_POSE_DEG, the 2.0 s is DEFAULT_MOVE_DURATION_S, and the default pose is the one used
# because the coordinator's _run_hybrid_target passes none. It is an ABSOLUTE JOINT TARGET, pan included, with no
# Cartesian delta. A test pins these values against that module, so a change there is seen here.
HYBRID_GRASP_POSE_DEG = {"shoulder_pan": -10.549, "shoulder_lift": 43.121, "elbow_flex": 54.286, "wrist_flex": -33.714}
HYBRID_GRASP_DURATION_S = 2.0
HYBRID_GRASP_SPEED = 0.0
HYBRID_GRASP_REQUEST = (HYBRID_GRASP_POSE_DEG, HYBRID_GRASP_DURATION_S, HYBRID_GRASP_SPEED)

GRASP_REFUSAL = ("GRASP_POSITION refused: an absolute joint target with no Cartesian delta cannot pass through the proven "
                 "builders, and its CUBE READY precondition is not the proven cycle's")
ARM_REFUSAL = ("hybrid arm request refused: only Cartesian deltas may reach the proven builders, and this machine's "
               "requests are joint targets")


def refuse_hybrid_arm_request(target, duration_s, speed) -> dict:
    """THE GUARD. It backs HardwareExecution.move_arm and raises HardwareMappingUnavailable before any read or send.

    WHY (section 7 of docs/autonomous-demo/cake-console-handoff-2026-09-23.md). Only Cartesian deltas may reach the proven
    builders, which then solve and gate them. The nearest builder, run_single_cube_cycle.proven_grasp_pose, keeps the
    MEASURED pan, caps tool travel at 30 mm and never goes below grasp height, and a pan step near contact is refused.
    Handing the proven mover the joints the hybrid machine already solved would keep only the final result check and drop
    the straight-arm elbow guard, the interpolated-path sampling, the tilt check and the floor limits. That substitution
    is forbidden, and no Cartesian delta exists in this request to hand over instead.

    THE INCOMPATIBLE PRECONDITION. The hybrid machine expects a person to put the cube between the jaws and answer
    "CUBE READY" (GRASP_CLOSE). The proven cycle needs a start at the low scan pose, a perceived cube with a contact point
    and a PAD_FRAME position, a non-contact PREGRASP, ALIGNED, and bounded approach steps to within 25 mm. Meeting the
    hybrid precondition would take either a blind move to contact height or a confirmation used as permission, and
    section 7 forbids both.

    The mission is then recorded FAILED with this class name and sentence (MissionCoordinator), never as an operator
    ABORT and never as a stop. The request is not recorded: its values are a motion command, and nothing a mission
    persists could be replayed at a robot. The sentence says which request it was, and this docstring says the rest.
    """
    if isinstance(target, dict) and dict(target) == HYBRID_GRASP_POSE_DEG and duration_s == HYBRID_GRASP_DURATION_S \
            and speed == HYBRID_GRASP_SPEED:
        raise HardwareMappingUnavailable(GRASP_REFUSAL)
    raise HardwareMappingUnavailable(ARM_REFUSAL)


class HardwareExecution:
    """The execution adapter a hardware mission is built with. Its five machine steps REFUSE; its seams carry a mission.

    ITS FIVE STEPS ARE NOT A HARDWARE MAPPING, and each raises HardwareMappingUnavailable before it reads or sends anything:
      - move_arm is refuse_hybrid_arm_request (the GRASP_POSITION record above);
      - read_pose and read_base refuse, so the hybrid machine on hardware stops at its FIRST callable (PRECHECK's
        read_pose) and never reaches a move at all; the GRASP_POSITION pin exercises the guard with read-only fakes;
      - move_base_sustained refuses: the base is driven by the proven controller's own executor, never by this adapter;
      - confirm refuses: a confirmation is never an answer on hardware, and no constant stands in for one.
    A hardware mission never calls any of them. The coordinator is built with run_proven_target (mission_service), which
    passes the capability gate and starts a supervised run; the steps exist so that if anything ever handed this adapter to
    the hybrid machine, it would refuse rather than move.

    ITS SEAMS carry the mission: attach_mission records the mission id and its declared targets, attach_cancel the
    mission's cancel event, and the bound and placed positions and each target's MEASURED mode are carried from one
    target to the next.

    `radii` is the configuration's mission.radii (demo_runtime.config.MissionRadii, or a mapping with r_claim_mm, r_match_mm
    and basis), or None: then every target fails RADII_UNQUALIFIED before anything is spawned.
    """

    def __init__(self, *, radii=None):
        self._lock = threading.Lock()
        self._cancel: Optional[threading.Event] = None
        self._mission_id: Optional[str] = None
        self._targets: list = []
        self._measured: dict = {}
        self._radii = _radii_document(radii)

    def adapter(self) -> ExecutionAdapter:
        return ExecutionAdapter(read_pose=self._refuse_read_pose, read_base=self._refuse_read_base,
                                move_arm=refuse_hybrid_arm_request, move_base_sustained=self._refuse_base,
                                confirm=self._refuse_confirm, performs_motion=True, label=LABEL,
                                attach_cancel=self._attach_cancel, attach_mission=self._attach_mission)

    @staticmethod
    def _refuse_read_pose():
        raise HardwareMappingUnavailable("read_pose refused: the hybrid machine does not run on hardware; the proven controller reads the arm itself")

    @staticmethod
    def _refuse_read_base():
        raise HardwareMappingUnavailable("read_base refused: the hybrid machine does not run on hardware; the proven controller reads the base itself")

    @staticmethod
    def _refuse_base(vx, duration_s):
        raise HardwareMappingUnavailable("move_base_sustained refused: the base is driven only by the proven controller's executor")

    @staticmethod
    def _refuse_confirm(prompt):
        raise HardwareMappingUnavailable("confirm refused: on hardware a judgment gate is a measured predicate, never an answer")

    def _attach_cancel(self, cancel: threading.Event) -> None:
        with self._lock:
            self._cancel = cancel

    def _attach_mission(self, mission_id: str, targets: list) -> None:
        """A new mission: its id, and its declared targets with nothing bound and nothing placed yet."""
        binding = target_binding_module()
        with self._lock:
            self._mission_id = mission_id
            self._targets = binding.declared_targets(targets)
            self._measured = {}
            self._cancel = None

    @property
    def cancel(self) -> Optional[threading.Event]:
        with self._lock:
            return self._cancel

    @property
    def mission_id(self) -> Optional[str]:
        with self._lock:
            return self._mission_id

    def declared_targets(self) -> list:
        """The `targets` of the next mission target file: every declared target, bound and placed as known so far."""
        with self._lock:
            return [dict(target) for target in self._targets]

    def carry_forward(self, targets: list) -> None:
        """After a PLACED target: the bound positions and every placed position so far (targets_after_placement)."""
        with self._lock:
            self._targets = [dict(target) for target in targets]

    @property
    def radii(self) -> Optional[dict]:
        return None if self._radii is None else dict(self._radii)

    def record_measured(self, bindings) -> None:
        """The modes a SEARCH measured when it bound the targets (a result's `bindings`): target_binding binds a declared
        target only to a cube whose measured mode is its declared one, and an auto target to the one cube there is in
        whichever mode it measured, so each is a measurement, taken before anything moved."""
        with self._lock:
            for entry in bindings or ():
                self._measured[entry["target_id"]] = entry["mode"]

    def measured_mode(self, target_id: str) -> Optional[str]:
        """What a SEARCH of this mission measured for this target, or None when nothing has measured it yet."""
        with self._lock:
            return self._measured.get(target_id)


def _radii_document(radii) -> Optional[dict]:
    """{r_claim_mm, r_match_mm, basis} from a MissionRadii or a mapping, or None. Shape only; the mission target file's
    parser checks the values, and refuses what the configuration's own check would."""
    if radii is None:
        return None
    if isinstance(radii, dict):
        return {name: radii.get(name) for name in ("r_claim_mm", "r_match_mm", "basis")}
    return {"r_claim_mm": radii.r_claim_mm, "r_match_mm": radii.r_match_mm, "basis": radii.basis}


def target_observation_module():
    """autonomy/motion/target_observation_source.py, imported as a package module, HERE and not at module scope.

    The repository root joins the END of sys.path (autonomy/motion holds modules named clock and transport, which must
    not shadow anything this daemon imports), and the module imports only mobile_approach, which is standard library."""
    root = str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.append(root)
    from autonomy.motion import target_observation_source  # noqa: E402

    return target_observation_source


class EvidenceJournal:
    """mission.MISSION_EVIDENCE_FILE_NAME, created by its FIRST record: a runtime that never records a capability decision
    leaves no file behind. Fsynced like every mission record, one writer at a time under this lock.

    EACH RECORD OPENS, APPENDS AND CLOSES the file, so no descriptor outlives a record and there is nothing to close. The
    coordinator this runner serves is closed by the daemon through MissionCoordinator.close, which knows nothing of this
    file; a journal held open would stay open until the process ends. A capability decision is written at most once per
    target, so the open per record costs nothing that matters."""

    def __init__(self, path):
        self.path = os.fspath(path)
        self._lock = threading.Lock()

    def __call__(self, record: dict) -> None:
        with self._lock:
            journal = Journal(self.path)
            try:
                journal.append(record)
            finally:
                journal.close()


def target_binding_module():
    """autonomy/manipulation/target_binding.py, imported HERE rather than at module scope.

    `demo_runtime` is a packaged runtime and `autonomy` is a checkout layout; the robot runs this daemon from a pinned
    git-archive release, which has both. target_binding imports the standard library only, so this loads no numpy, no
    OpenCV and no controller."""
    root = Path(__file__).resolve().parents[3]
    directory = str(root / "autonomy" / "manipulation")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import target_binding  # noqa: E402

    return target_binding


class ProvenTargetRunner:
    """The coordinator's run_target for a hardware mission: run_proven_target bound to this runtime's supervisor, its
    capability endpoints and the coordinator that records the runs.

    `hardware_run_target` says what this is, so the REHEARSAL builder can refuse it (mission_service): a rehearsal must
    never be handed a run_target that starts a controller. `recorder` is bound after the coordinator exists, because the
    coordinator is built with this runner."""

    hardware_run_target = True

    def __init__(self, *, hardware: HardwareExecution, supervisor, capability_endpoints: Optional[dict],
                 wait_poll_s: float = WAIT_POLL_S, evidence: Optional[Callable] = None,
                 observation_available: Optional[Callable] = None):
        self.hardware, self.supervisor, self.capability_endpoints = hardware, supervisor, capability_endpoints
        self.wait_poll_s = wait_poll_s
        self.recorder = None
        # Where a capability decision is written (an EvidenceJournal in the runtime), and who says whether the capability
        # can be fed an observation at all, that is whether a mapping exists (target_observation_source.
        # capability_observation_available unless a test stands one in); per-run availability is the controller's, at
        # the advance (target_observation_source.resolve), never this runtime's.
        self.evidence, self.observation_available = evidence, observation_available

    def bind_recorder(self, recorder) -> None:
        self.recorder = recorder

    def can_park(self) -> bool:
        """Whether the supervisor beneath can start the park before a retry (supervisor.start_mission_park). A supervisor
        without it answers every Retry mission_park_unavailable, and a hold then offers only the Stop."""
        return callable(getattr(self.supervisor, "start_mission_park", None))

    def retry_refusal(self):
        """Why the supervisor beneath admits no Retry (supervisor.mission_retry_refusal: a LIVE runtime without mission.retry
        and the attended label), or None. A supervisor that cannot say is refused, never read as admitting one."""
        gate = getattr(self.supervisor, "mission_retry_refusal", None)
        return gate() if callable(gate) else "mission_retry_not_enabled"

    def park(self, mission_id: str, target_id: str) -> dict:
        return run_park(target_id, hardware=self.hardware, supervisor=self.supervisor, recorder=self.recorder,
                        wait_poll_s=self.wait_poll_s)

    def __call__(self, execution, capability_available, wait_for_capability_change, target: dict) -> dict:
        return run_proven_target(execution, capability_available, wait_for_capability_change, target,
                                 hardware=self.hardware, supervisor=self.supervisor, recorder=self.recorder,
                                 capability_endpoints=self.capability_endpoints, wait_poll_s=self.wait_poll_s,
                                 evidence=self.evidence, observation_available=self.observation_available)


def run_proven_target(execution, capability_available: Callable, wait_for_capability_change: Callable, target: dict, *,
                      hardware: HardwareExecution, supervisor, recorder, capability_endpoints: Optional[dict],
                      wait_poll_s: float = WAIT_POLL_S, evidence: Optional[Callable] = None,
                      observation_available: Optional[Callable] = None) -> dict:
    """Carry ONE target with the unchanged proven controller as a supervised child. See the module docstring.

    `execution` is the coordinator's wrapped adapter and is deliberately NOT used: nothing here calls a hybrid machine
    step. Returns {target_state, run_id, detail, final_state, outcome, evidence}, or raises MissionCancelled."""
    cancel, mission_id = hardware.cancel, hardware.mission_id
    if cancel is None or mission_id is None:
        raise ExecutionRefused("the mission was not attached to the hardware adapter, so no run can be started for it")
    if recorder is None:
        raise ExecutionRefused("no mission record was bound to the hardware adapter, so no run can be associated with its target")
    target_id = target.get("target_id")
    # Whether this target MAY need the capability: declared as needing it, or auto (its SEARCH decides). A target declared
    # as needing nothing is never gated and never respawned carrying it.
    declared = target.get("requires_capability") in (True, mission.REQUIRES_CAPABILITY_AUTO)
    if declared and not capability_endpoints:
        raise HardwareMappingUnavailable("this target may need the capability and no capability endpoints are configured")
    # 0. THE RADII: no qualified claim and match radii, no run at all (target_binding, THE TWO RADII).
    radii = hardware.radii
    if radii is None:
        return _outcome(mission.TARGET_FAILED, None, RADII_UNQUALIFIED)
    ask = observation_available or target_observation_module().capability_observation_available

    def admit_capability(source: str, run_id, measured_mode: str = MODE_BASE_ADVANCE) -> Optional[dict]:
        """THE CAPABILITY for a target that MEASURED BASE_ADVANCE, or whose arm-alone binding's fallback moves the base
        (`measured_mode` ARM_ONLY, source SOURCE_SEARCH_FALLBACK): None when the run may be spawned carrying it (the
        observation source answered available and the gate was passed), else the FAILED outcome. Asked BEFORE the gate:
        a target the capability could not be fed never waits for it and never gets a run.

        A STOP OUTRANKS EVERY ANSWER HERE, and is read first. The SEARCH source reaches this after a measuring run that
        ended on its own (exit 5, not a stop), and a stop that arrived since then must end the mission STOPPED, whatever
        the source would have answered (FAILED for an unavailable one, a passed gate and a respawn for an available one):
        nothing is asked and nothing is recorded. It is read again after the ask and the evidence write, so a stop that
        landed during either still wins over what they said."""
        if cancel.is_set():
            raise MissionCancelled()
        available, reason = ask()
        if evidence is None:
            raise ExecutionRefused("no evidence journal was bound to the hardware adapter, so the need for the capability cannot be recorded")
        evidence(mission.target_capability_required(mission_id, target_id, source=source, run_id=run_id, measured_mode=measured_mode,
                                                     observation_available=bool(available), reason=reason, t_wall_utc=utc_now_text()))
        if cancel.is_set():
            raise MissionCancelled()
        if not available:
            return _outcome(mission.TARGET_FAILED, run_id, f"{CAPABILITY_OBSERVATION_UNAVAILABLE}:{reason}")
        # D1: THE CAPABILITY GATE BEFORE THE SPAWN, never with a cube held. The coordinator's own gate: it records the wait
        # and the resume, and it raises MissionCancelled after a stop even when the slot now carries the capability.
        while not capability_available(CAPABILITY_NAME):
            wait_for_capability_change()
        if cancel.is_set():
            raise MissionCancelled()
        return None

    # 1. THE MEASURED MODE, never the declaration alone: what the first execution's SEARCH bound this target as.
    measured = hardware.measured_mode(target_id)
    carry = False
    if measured == MODE_BASE_ADVANCE:
        if not declared:  # target_binding never binds so; refused rather than trusted if it ever does
            return _outcome(mission.TARGET_FAILED, None, MEASURED_MODE_NOT_DECLARED)
        refused = admit_capability(SOURCE_BINDING, None)
        if refused is not None:
            return refused
        carry = True
    if cancel.is_set():
        raise MissionCancelled()
    outcome, result, exit_status = _run_once(target_id, carry, hardware=hardware, supervisor=supervisor, recorder=recorder,
                                             capability_endpoints=capability_endpoints, radii=radii, cancel=cancel,
                                             mission_id=mission_id, wait_poll_s=wait_poll_s)
    if carry or not declared or not _search_measured_capability(result, exit_status):
        return outcome
    # A run WITHOUT the capability whose own SEARCH measured BASE_ADVANCE, or bound the arm alone with a fallback that moves
    # the base, and refused before anything was picked up: the same admission, then exactly one respawn carrying the
    # capability. A second refusal is that run's own FAILED outcome.
    # RESIDUAL (named, and reachable now that the source can answer available): the respawn is a fresh first execution.
    # Its binding is not held within the match radius of where the measuring run bound the cube; that it selects the
    # same cube rests only on there being exactly one cube of that mode.
    measured_mode, source = SEARCH_CAPABILITY_REFUSALS[result["reason_code"]]
    refused = admit_capability(source, outcome.get("run_id"), measured_mode)
    if refused is not None:
        return refused
    outcome, _result, _status = _run_once(target_id, True, hardware=hardware, supervisor=supervisor, recorder=recorder,
                                          capability_endpoints=capability_endpoints, radii=radii, cancel=cancel,
                                          mission_id=mission_id, wait_poll_s=wait_poll_s)
    return outcome


#: Where a park that a stop ended left the robot: a park is only started with nothing held and the base home, and it moved
#: the arm.
PARKED_STOP_STATE = {"holding": False, "base_ahead": False, "moved": True}


def run_park(target_id: str, *, hardware: HardwareExecution, supervisor, recorder, wait_poll_s: float = WAIT_POLL_S) -> dict:
    """THE PARK BEFORE A RETRY: the UNCHANGED `--do exit` (contract command return_arm), which returns the arm to the low
    scan pose, started as a supervised run of THIS mission through supervisor.start_mission_park and waited for.
    {ok, run_id, reason_code}, or raises MissionCancelled (RunStopped once a run exists) when a stop came first.

    THE SUPERVISOR'S PART, and the interface it is held to: start_mission_park(MissionRun) takes a MissionRun whose
    `write_target` is None (a park carries NO mission target file: the controller refuses one for anything but `--do
    cycle`), admits the run through the same identity gate, block() and lease as start_mission_run, reads the mission's
    cancel event inside the lock that publishes the run, calls `on_run_started(run_id)` before the spawn and answers with
    start_mission_run's shape {ok, reason_code, command_id, run_id, status}. A supervisor without it gets no park.

    ONLY EVER CALLED WITH NOTHING HELD AND THE BASE HOME: the hold offers a Retry only then (mission.next_actions). The
    park is OK only when the run exited 0 and was not stopped; anything else is the retry's failure, recorded by the
    coordinator, and nothing further is started."""
    cancel, mission_id = hardware.cancel, hardware.mission_id
    if cancel is None or mission_id is None or recorder is None:
        raise ExecutionRefused("the mission was not attached to the hardware adapter, so no park can be started for it")
    if cancel.is_set():
        raise MissionCancelled()
    start = getattr(supervisor, "start_mission_park", None)
    if not callable(start):
        return {"ok": False, "run_id": None, "reason_code": "park_not_supported"}

    def run_started(run_id: str) -> None:
        recorder.on_run_started(mission_id, target_id, run_id, run_kind=mission.RUN_KIND_PARK)

    answer = start(MissionRun(mission_id=mission_id, target_id=target_id, cancel=cancel, write_target=None,
                              on_run_started=run_started))
    run_id = answer.get("run_id")
    if not answer.get("ok"):
        if cancel.is_set():
            raise MissionCancelled()
        return {"ok": False, "run_id": run_id, "reason_code": f"{RUN_NOT_STARTED}:{answer.get('reason_code')}"}
    state = _wait_for_run(supervisor, answer["command_id"], cancel, wait_poll_s)
    completion = state.get("completion") or {}
    outcome_word, exit_status = completion.get("outcome"), completion.get("exit_status")
    if cancel.is_set() and (outcome_word == "stopped" or exit_status == STOP_EXIT_STATUS):
        raise RunStopped(run_id, PARKED_STOP_STATE)
    if exit_status == 0 and outcome_word != "stopped":
        return {"ok": True, "run_id": run_id, "reason_code": None}
    return {"ok": False, "run_id": run_id, "reason_code": f"park_outcome:{outcome_word}:{exit_status}"}


def _search_measured_capability(result: Optional[dict], exit_status) -> bool:
    """The run ended with one of the controller's SEARCH refusals that ask for the capability (SEARCH_CAPABILITY_REFUSALS: a
    cube that measured BASE_ADVANCE, or, under the additive placement words, a cube bound to the arm alone whose in-run
    fallback moves the base) in a run that carried no capability, and nothing else: that code with a selection in its
    mode, nothing placed and no placement predicate evaluated (nothing was picked up), the state the refusal ends in, and
    the exit status of a CycleFailed."""
    if not isinstance(result, dict):
        return False
    selection = result.get("selection") or {}
    asked = SEARCH_CAPABILITY_REFUSALS.get(result.get("reason_code"))
    return (result.get("stage") == "finished" and result.get("outcome") == "failed" and asked is not None
            and selection.get("mode") == asked[0] and result.get("placement") is None
            and all(value is None for value in (result.get("predicates") or {}).values())
            and result.get("final_state") == SEARCH_REFUSAL_FINAL_STATE and exit_status == CYCLE_FAILED_EXIT_STATUS)


def _run_once(target_id: str, carry: bool, *, hardware: HardwareExecution, supervisor, recorder, capability_endpoints,
              radii: dict, cancel: threading.Event, mission_id: str, wait_poll_s: float) -> tuple:
    """ONE supervised run of the controller for this target: (the outcome, the result it wrote or None, its exit status).
    `carry` decides whether its mission target file carries the capability's endpoints."""
    binding = target_binding_module()
    expected: dict = {}

    def write_target(run_dir, run_id: str) -> str:
        directory = os.path.abspath(os.fspath(run_dir))
        expected["run_dir"] = directory
        path = os.path.join(directory, MISSION_TARGET_FILE)
        document = binding.mission_target_document(
            mission_id=mission_id, target_id=target_id, run_id=run_id, targets=hardware.declared_targets(),
            capability=dict(capability_endpoints) if carry else None, radii=dict(radii),
            result_path=os.path.join(directory, MISSION_RESULT_FILE))
        expected["target"] = binding.write_mission_target(path, document)
        return path

    def run_started(run_id: str) -> None:
        recorder.on_run_started(mission_id, target_id, run_id)

    answer = supervisor.start_mission_run(MissionRun(mission_id=mission_id, target_id=target_id, cancel=cancel,
                                                     write_target=write_target, on_run_started=run_started))
    run_id = answer.get("run_id")
    if not answer.get("ok"):
        if cancel.is_set():
            raise MissionCancelled()
        return _outcome(mission.TARGET_FAILED, run_id, f"{RUN_NOT_STARTED}:{answer.get('reason_code')}",
                        recovery=_recovery(mission.OUTCOME_FATAL, {"reason_code": answer.get("reason_code")}, NOTHING_RAN)), None, None

    state = _wait_for_run(supervisor, answer["command_id"], cancel, wait_poll_s)
    completion = state.get("completion") or {}
    outcome_word, exit_status = completion.get("outcome"), completion.get("exit_status")
    stop_state = read_stop_state(expected.get("run_dir"))
    if cancel.is_set() and (outcome_word == "stopped" or exit_status == STOP_EXIT_STATUS):
        raise RunStopped(run_id, stop_state)

    result, result_refused = None, None
    if "target" in expected:
        try:
            result = binding.load_result(expected["target"])
        except binding.MissionFileRefused as refusal:
            result_refused = refusal.reason_code
    else:
        result_refused = "result_target_not_written"
    if result is not None and result.get("bindings"):
        hardware.record_measured(result["bindings"])
        for entry in result["bindings"]:
            recorder.on_target_bound(mission_id, entry["target_id"], run_id=run_id, observation_id=entry.get("observation_id"),
                                     bound_pad_m=entry["bound_pad_m"], mode=entry["mode"])
    facts = failure_facts(completion, result)
    evidence = _evidence(run_id, outcome_word, exit_status, completion, result, result_refused, facts=facts, stop_state=stop_state)
    final_state = result.get("final_state") if result is not None else None
    # The class the hold decides on. A result the mission could not read, and a placement it does not claim, are FATAL:
    # nothing about them is a failure a retry answers.
    outcome_class = classify_outcome(completion, result=result, stop_state=stop_state)
    if result_refused is not None or outcome_class == mission.OUTCOME_PLACED:
        outcome_class = mission.OUTCOME_FATAL
    recovery = _recovery(outcome_class, facts, stop_state)

    predicates = (completion.get("result") or {}).get("predicates") or {}
    run_placed = exit_status == 0 and outcome_word == "placed_new" and all(predicates.get(name) is True for name in PLACEMENT_PREDICATES)
    if run_placed and result is not None and binding.result_claims_placement(result, expected["target"]):
        hardware.carry_forward(binding.targets_after_placement(expected["target"], result))
        return _outcome(mission.TARGET_PLACED, run_id, None, final_state, outcome_word, evidence,
                        recovery=_recovery(mission.OUTCOME_PLACED, facts, stop_state)), result, exit_status
    if result_refused is not None:
        detail = f"{RESULT_REFUSED}:{result_refused}"
    elif outcome_word != "placed_new" or exit_status != 0:
        reason = result.get("reason_code") if result is not None else None
        detail = f"run_outcome:{outcome_word}" + (f":{reason}" if reason else "")
    else:
        detail = PLACEMENT_NOT_CLAIMED
    return _outcome(mission.TARGET_FAILED, run_id, detail, final_state, outcome_word, evidence, recovery=recovery), result, exit_status


run_proven_target.hardware_run_target = True


def _wait_for_run(supervisor, command_id: str, cancel: threading.Event, wait_poll_s: float) -> dict:
    """Until the run is FINISHED, reading the mission's cancel event every `wait_poll_s` and passing a stop it sees to
    the supervisor, once. The supervisor itself repeats a SIGINT nothing acknowledged, and never escalates.

    EVERY TURN WAITS `wait_poll_s`, a stop or not. Before a stop the wait is cancel.wait, which a stop cuts short. Once
    the event is set, cancel.wait returns AT ONCE and would never pause again, so a stopping turn sleeps instead: without
    that this loop read the command table (under the supervisor's state lock) millions of times a second for as long as
    the child took to end, and on the robot that is up to one frame in flight, on the same CPU the child's perception
    needs to finish that frame. A finished run is therefore noticed within one `wait_poll_s`, the bound design 3.4 set.

    A DAEMON SHUTDOWN IS NOT A STOP. RuntimeCore.close calls supervisor.detach() and only THEN the coordinator's close,
    which sets this same event. So the event is read FIRST in each turn and `detached` after it: when the event was set by
    the shutdown, `detached` is already true by the time it is read, and the turn raises RUNTIME_DETACHED without calling
    supervisor.stop. Reading them the other way round let a shutdown wake this wait and send the controller a SIGINT
    recorded with a person's origin (daemon.RuntimeCore.close promises not to touch a running controller;
    MissionCoordinator.close promises its stop is not written down as an operator's). The one remaining overlap, an
    operator's Stop that set the event before a shutdown began, still sends the stop: a person did ask for it."""
    stop_sent = False
    while True:
        stopping = cancel.is_set()  # BEFORE `detached`: the shutdown sets `detached` first and this event second
        state = supervisor.command_state(command_id) or {}
        if state.get("status") == "finished":
            return state
        if supervisor.detached:
            # The daemon is shutting down and let go of its runs: nothing will end this one here. The controller keeps
            # running by itself (the supervisor never kills it), and this mission ends with its cause named.
            raise ExecutionRefused(f"{RUNTIME_DETACHED}: the runtime let go of the run while it was being carried")
        if not stopping:
            cancel.wait(wait_poll_s)  # a stop cuts this short; the next turn reads the event again, then `detached`
            continue
        if not stop_sent:
            active = supervisor.active_run()
            if active is not None and active.get("command_id") == command_id:
                supervisor.stop(origin=STOP_ORIGIN)
                stop_sent = True
        time.sleep(wait_poll_s)


def _evidence(run_id, outcome_word, exit_status, completion: dict, result: Optional[dict], result_refused, *, facts=None,
              stop_state=None) -> dict:
    """Ids and numbers only: what the outcome rests on. Nothing in it is a velocity, a pose or a path.

    Beside the placement's ids and numbers it keeps WHY a run failed as the controller said it (its reason code, the
    state it was in, the values that decided and the frames in scope, from its run.finished) and where the run left the
    robot (the stop state its events give). They used to be dropped here, so a failed target's record could name its run
    and nothing about its failure."""
    predicates = (completion.get("result") or {}).get("predicates") or {}
    facts = facts or {}
    evidence = {"run_id": run_id, "outcome": outcome_word, "exit_status": exit_status,
                "predicates": {name: predicates.get(name) for name in PLACEMENT_PREDICATES},
                "result_refused": result_refused, "reason_code": facts.get("reason_code"), "phase": facts.get("phase"),
                "failure_measurements": list(facts.get("measurements") or []),
                "failure_observation_ids": list(facts.get("observation_ids") or []),
                "stop_state": mission._stop_state_copy(stop_state) if stop_state is not None else None}
    if result is not None:
        selection, placement = result.get("selection") or {}, result.get("placement") or {}
        evidence.update({
            "selection_observation_id": selection.get("observation_id"), "bound_pad_m": selection.get("bound_pad_m"),
            "seen_pad_m": selection.get("seen_pad_m"), "distance_mm": selection.get("distance_mm"),
            "mode": selection.get("mode"), "placed_pad_m": placement.get("placed_pad_m"),
            "placement_observation_id": placement.get("observation_id"),
            "on_pad_count": placement.get("on_pad_count"), "release_distance_mm": placement.get("release_distance_mm"),
            "release_tolerance_mm": placement.get("release_tolerance_mm"),
            "placement_reason_code": placement.get("reason_code"),
            "capability": dict(result["capability"]) if isinstance(result.get("capability"), dict) else None})
    return evidence


def _recovery(outcome_class: str, facts: Optional[dict], stop_state: Optional[dict]) -> dict:
    """What the coordinator's hold reads from an outcome: the class, the controller's facts, the stop state and whether a
    Retry may follow this ending at all (retry_allowed)."""
    facts = facts or {}
    return {"outcome_class": outcome_class, "reason_code": facts.get("reason_code"), "phase": facts.get("phase"),
            "measurements": list(facts.get("measurements") or []), "observation_ids": list(facts.get("observation_ids") or []),
            "stop_state": mission._stop_state_copy(stop_state), "retry": retry_allowed(facts.get("reason_code")),
            "detail": facts.get("detail")}


def _outcome(target_state: str, run_id, detail, final_state=None, outcome_word=None, evidence=None, *, recovery=None) -> dict:
    """One target's outcome. `recovery` is what the hold reads; an outcome with none (a refusal before any run) is FATAL
    with nothing run."""
    if recovery is None:
        recovery = _recovery(mission.OUTCOME_FATAL, {"reason_code": detail}, NOTHING_RAN)
    return {"target_state": target_state, "run_id": run_id, "detail": detail, "final_state": final_state,
            "outcome": outcome_word, "evidence": evidence, "recovery": recovery}
