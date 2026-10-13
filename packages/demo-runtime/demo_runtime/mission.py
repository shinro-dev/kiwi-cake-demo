# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.mission: what a two-cube mission is, and the durable record of one.

SEPARATELY VERSIONED, AND DELIBERATELY NOT CONTRACT v1. Contract v1's command enum, event kinds and snapshot are
sealed and each is pinned by a test. A mission is not a command and a target is not a run, so nothing here widens
any of them. These records live in their own append-only file under their own MISSION_RECORD_VERSION, and a reader
that does not know this version is expected to say so rather than guess.

THE SAME DURABILITY AS THE COMMAND JOURNAL, reused rather than reinvented: `journal.Journal` appends one JSON line
and fsyncs before it returns, tolerates a torn last line, and is read back by folding the records in order. What is
written here is written BEFORE the thing it describes is attempted, for the same reason the command journal writes
'accepted' before it spawns: after a crash the file may say less than happened, and must never say more.

WHAT IS PERSISTED, and why each one is not derivable from the others:
  - the MISSION identity, because child runs carry their own run ids and an outcome has to find its way back to the
    mission and the target it belongs to;
  - the SELECTED TARGET, because a mission that resumes has to resume the same one, not the next one;
  - the WAITING REASON, because "waiting" with no reason cannot be told from "stuck", and an operator is owed the
    difference;
  - the ACTIVATION OPERATION reference: its uuid, the candidate key and the payload digest it was bound to. After a
    reconnect the operation's state is RETRIEVED by re-reading the slot; it is never resubmitted on a guess, and a
    retry that does happen must carry the same uuid and the same bytes, which is what the digest records.

A MISSION IS RESUMED FROM FRESH OBSERVATIONS, NEVER FROM A SAVED MOTION COMMAND. Nothing in these records is a
velocity, a pose target or anything else that could be replayed at a robot. They say what was decided, not what was
commanded.

AN END IS AN END. Once a mission is COMPLETE, STOPPED or FAILED the fold does not let a later record change that, or
change what its targets came to. The case this exists for is a late activation answer: it is still RECORDED, because a
slot may really have moved, and it appears in the activations like any other; it never resumes the mission and never
changes its terminal state. Anything else that arrives after the end is counted and shown rather than folded, because
a record that was ignored silently would be a record nobody could find.

EXTENDED ADDITIVELY WITHIN VERSION 1. The stop request (`mission_stop`), the resume of a waiting target, the target
state STOPPED, the activation state COMPLETED_NOT_OBSERVED, the slot read-back carried by an activation, the run a target
started (`target_run_started`), where a target was bound (`mission_target_bound`) and the ids and numbers a target's
outcome rests on (`evidence`), the HOLD for the operator (the waiting reason `operator_decision`, what an attempt came to
in `mission_target_attempt`, a Retry asked for in `mission_retry`, and `run_kind` "park" on the run that precedes a retry)
are all new records or new fields beside the ones that were there. A version 1 reader that predates them skips a record kind it
does not know and ignores a field it does not read, so none of them changes what an older record means; the version
does not move for them.

EVIDENCE THAT IS NOT PART OF THE VIEW. Why a target needed the capability (`target_capability_required`) is written, by
the robot-side execution, to its own append-only file beside this one (MISSION_EVIDENCE_FILE_NAME), under the same
version and the same durability. The fold below never reads that file, so nothing in it can change what a console is
shown; it is there for whoever audits a run.
"""
from __future__ import annotations

import collections
import uuid as uuid_module
from typing import Optional

#: This file's own version, moved when the shape below changes. It is not the contract version and never travels
#: with one.
MISSION_RECORD_VERSION = 1

MISSIONS_FILE_NAME = "missions.jsonl"
#: The robot-side execution's evidence file, beside the missions file and never folded into the view.
MISSION_EVIDENCE_FILE_NAME = "mission-evidence.jsonl"

# ---- mission and target state ---------------------------------------------------------------------------------

#: A mission is running, waiting on something it cannot supply itself, finished, or stopped. These are the mission's
#: own states and are NOT controller states: the controller's own vocabulary stays where it is.
MISSION_RUNNING, MISSION_WAITING, MISSION_COMPLETE, MISSION_STOPPED, MISSION_FAILED = (
    "RUNNING", "WAITING_FOR_CAPABILITY", "COMPLETE", "STOPPED", "FAILED")

#: The mission states nothing follows. Once the fold has read one of these, a later record changes neither the mission's
#: state nor any target's; see fold_missions.
MISSION_TERMINAL = (MISSION_COMPLETE, MISSION_STOPPED, MISSION_FAILED)

#: What one target came to. PENDING has not been started, ACTIVE is being carried now.
TARGET_PENDING, TARGET_ACTIVE, TARGET_PLACED, TARGET_WAITING, TARGET_FAILED = (
    "PENDING", "ACTIVE", "PLACED", "WAITING_FOR_CAPABILITY", "FAILED")

#: A target the mission was carrying, or waiting on, when the mission was stopped. Added beside the five above rather
#: than left reading ACTIVE: a stopped mission whose target still said ACTIVE would read as a robot still working on it.
TARGET_STOPPED = "STOPPED"

#: Why a mission is waiting, named rather than implied, so a reader never has to infer which is meant.
WAITING_ON_CAPABILITY = "capability_unavailable"

#: The second reason: a target's run failed and the mission HOLDS for a person's decision instead of ending. The
#: mission's state is the same WAITING state as a capability wait (MISSION_WAITING, spelled WAITING_FOR_CAPABILITY for
#: the records written before this reason existed); the reason is what tells the two apart, and an activation is refused
#: for any reason but WAITING_ON_CAPABILITY (MissionCoordinator.submit_activation). NOTHING MOVES IN A HOLD: the only
#: ways out are a Retry pressed by the operator (retry_requested below) and a Stop.
WAITING_ON_OPERATOR = "operator_decision"

#: What one attempt at a target came to, as a class the hold decides on. Classified by the robot-side execution from the
#: run's own record (demo_runtime.mission_execution.classify_outcome), never from a sentence.
#:   placed           the target is placed and claimed;
#:   retryable        the run failed at a stage that leaves nothing held and the base where it started (a lost block at
#:                    ALIGN, an empty close with the gripper reopened, no block found): the mission holds and a Retry is
#:                    offered while the budget lasts;
#:   operator_needed  the run left something only a person can resolve (a cube may be held, the base is displaced) or was
#:                    refused before motion for the placement envelope: the mission holds, and a Retry is offered only
#:                    when nothing is held and the base is home, as "move the block, then retry";
#:   base_only        the cube was placed and the base did not return: the mission ends, and the base is returned by hand
#:                    or by command;
#:   fatal            identity, lease, runtime, result file or anything not classified above: the mission ends as before;
#:   stopped          a stop ended the run.
OUTCOME_PLACED, OUTCOME_RETRYABLE, OUTCOME_OPERATOR_NEEDED, OUTCOME_BASE_ONLY, OUTCOME_FATAL, OUTCOME_STOPPED = (
    "placed", "retryable", "operator_needed", "base_only", "fatal", "stopped")
OUTCOME_CLASSES = (OUTCOME_PLACED, OUTCOME_RETRYABLE, OUTCOME_OPERATOR_NEEDED, OUTCOME_BASE_ONLY, OUTCOME_FATAL, OUTCOME_STOPPED)
#: The classes after which the mission HOLDS for the operator rather than ending.
HOLDING_CLASSES = (OUTCOME_RETRYABLE, OUTCOME_OPERATOR_NEEDED)

#: What the operator can do next, as words a console glosses. The words are the whole list.
NEXT_RETRY, NEXT_MOVE_BLOCK_THEN_RETRY, NEXT_RETURN_ARM, NEXT_OPEN_GRIPPER_BY_HAND, NEXT_RETURN_BASE, NEXT_STOP, NEXT_START = (
    "retry", "move_block_then_retry", "return_arm", "open_gripper_by_hand", "return_base", "stop", "start")
NEXT_ACTIONS = (NEXT_RETRY, NEXT_MOVE_BLOCK_THEN_RETRY, NEXT_RETURN_ARM, NEXT_OPEN_GRIPPER_BY_HAND, NEXT_RETURN_BASE, NEXT_STOP,
                NEXT_START)
#: The actions a Retry press answers: both park the arm and start a fresh attempt.
RETRY_ACTIONS = (NEXT_RETRY, NEXT_MOVE_BLOCK_THEN_RETRY)

#: The kind of a run started for a target: the cycle (no kind on the record, as before) or the PARK that precedes a retry
#: (the unchanged `--do exit`, which returns the arm to the low scan pose).
RUN_KIND_PARK = "park"

#: The most measurements and observation ids one attempt record keeps. A record is a statement, not a log.
MAX_ATTEMPT_ITEMS = 32
#: The longest controller sentence an attempt keeps (the controller's own bound on it, run_single_cube_cycle._text).
MAX_ATTEMPT_DETAIL_CHARS = 1024

#: The third value a target's `requires_capability` takes in these records, beside true and false: the target takes
#: whichever placement mode its own SEARCH measures, and needs the capability only when that is BASE_ADVANCE. The word is
#: carried through every record copy as it was declared (a console reads true, false or "auto"). It is spelled the same
#: as autonomy/manipulation/target_binding.AUTO, which owns its meaning on the controller side (a test keeps them equal).
REQUIRES_CAPABILITY_AUTO = "auto"

#: What an activation attempt came to, in the mission's own record. These mirror cake_activation's outcomes and add
#: the one thing an answer cannot tell you: that the slot was read back and carries the capability.
ACTIVATION_SUBMITTED, ACTIVATION_REJECTED, ACTIVATION_FAILED, ACTIVATION_OBSERVED = (
    "SUBMITTED", "REJECTED_BY_CAKE", "REQUEST_FAILED", "OBSERVED")

#: The transaction COMPLETED and the slot, read back, does NOT carry the capability. It used to be recorded as
#: SUBMITTED, which also meant "in flight", so a reader could not tell an operation still running from one that finished
#: without moving the slot. SUBMITTED now means in flight and nothing else: the attempt was written and either no answer
#: has come back or the answer says the transaction is still running. Neither of the two is an activation; only
#: OBSERVED is.
ACTIVATION_COMPLETED_NOT_OBSERVED = "COMPLETED_NOT_OBSERVED"

#: Cake REFUSED the candidate before any transaction existed (the resident answered status mutation_refused with its
#: refusal code: an integrity or admission refusal of the Plan it was handed). That is Cake judging, so it is neither
#: REQUEST_FAILED (Cake judged nothing) nor REJECTED_BY_CAKE (a transaction ran and aborted), and it ends the operation:
#: nothing is in flight after it.
ACTIVATION_REFUSED_BEFORE_TRANSACTION = "REFUSED_BEFORE_TRANSACTION"


def new_mission_id() -> str:
    return "mission-" + uuid_module.uuid4().hex[:12]


# ---- the records ----------------------------------------------------------------------------------------------


def mission_started(mission_id: str, targets: list, *, t_wall_utc: str, data_mode: str, performs_motion: bool,
                    execution_label: str) -> dict:
    """The first record of a mission. The targets are named HERE and never grow later: a mission that could gain a
    target while it ran could not say what completing it meant.

    `performs_motion` and `execution_label` are the EXECUTION ADAPTER's own answers, recorded before anything is
    attempted, so no later reader can take a rehearsal outcome for a physical one. Both are required: a record that
    omitted them would be read as if it had said something."""
    return {"rec": "mission", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "state": MISSION_RUNNING,
            "targets": [dict(target) for target in targets], "data_mode": data_mode,
            "performs_motion": bool(performs_motion), "execution_label": str(execution_label), "t_wall_utc": t_wall_utc}


def target_selected(mission_id: str, target_id: str, *, t_wall_utc: str) -> dict:
    return {"rec": "mission_target", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
            "target_state": TARGET_ACTIVE, "t_wall_utc": t_wall_utc}


def target_waiting(mission_id: str, target_id: str, reason: str, *, t_wall_utc: str) -> dict:
    """The mission stopped being able to proceed on its own, and says why."""
    return {"rec": "mission_target", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
            "target_state": TARGET_WAITING, "waiting_reason": reason, "mission_state": MISSION_WAITING,
            "t_wall_utc": t_wall_utc}


def target_resumed(mission_id: str, target_id: str, *, t_wall_utc: str) -> dict:
    """The SAME target left the capability gate after a wait: it is ACTIVE again and the mission RUNNING.

    Written by the thread carrying the target, at the moment the capability slot, read back, carries the capability.
    NEVER by an activation's answer: an answer is not an observation, and a mission that resumed on an answer would
    resume on something nobody had checked. Without this record a resumed target read WAITING_FOR_CAPABILITY for as long
    as it was being carried, and an operator was offered an activation for a capability that was already there."""
    return {"rec": "mission_target", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
            "target_state": TARGET_ACTIVE, "mission_state": MISSION_RUNNING, "resumed": True, "t_wall_utc": t_wall_utc}


def mission_stop_requested(mission_id: str, *, origin: str, t_wall_utc: str) -> dict:
    """A stop was asked for. A REQUEST, NOT AN OUTCOME: the mission is STOPPED only when mission_ended says so.

    Written so that the request survives the process that received it. Before this record a stop lived only in an
    in-memory event, so a daemon that restarted between the request and the end forgot it had ever been asked, and an
    activation could still be submitted for a mission somebody had stopped. `origin` names the runtime path the stop
    arrived by (the plain stop, the mission stop or a submitted stop command); it is the runtime's own word, never a
    caller's."""
    return {"rec": "mission_stop", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "stop_requested": True,
            "origin": origin, "t_wall_utc": t_wall_utc}


def target_outcome(mission_id: str, target_id: str, target_state: str, *, run_id=None, detail=None,
                   t_wall_utc: str, evidence=None) -> dict:
    """A child run's outcome, kept ASSOCIATED WITH ITS MISSION AND TARGET. The run keeps its own id, which is what
    the command journal and the run history know it by; this is the record that says which target it was for.

    `evidence` (optional, additive) is what the outcome RESTS ON, as ids and numbers only: the selection observation,
    the bound and placed positions in PAD_FRAME, the placement predicates, and the capability's generation, pid and
    command counts. It is absent from the record when there is none, so a rehearsal's records read as they always did.
    Nothing in it is a velocity or a pose target: it says what was measured and decided, never what was commanded."""
    record = {"rec": "mission_target", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
              "target_state": target_state, "run_id": run_id, "detail": detail, "t_wall_utc": t_wall_utc}
    if evidence is not None:
        record["evidence"] = dict(evidence)
    return record


def target_run_started(mission_id: str, target_id: str, run_id: str, *, t_wall_utc: str, run_kind=None) -> dict:
    """A supervised run was started for this target, written BEFORE that run's child is spawned.

    It carries the run id and no target state: the target is already ACTIVE and stays so until its outcome is recorded.
    Written first so that a crash, a stop or a refused spawn after this point still leaves the run associated with the
    target it was for; after a crash the file may say a run was started that never spawned, and never the reverse.

    `run_kind` is RUN_KIND_PARK for the park that precedes a retry, and absent for a cycle, so a record written before
    parks existed reads exactly as it did."""
    record = {"rec": "mission_target", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
              "run_id": run_id, "run_started": True, "t_wall_utc": t_wall_utc}
    if run_kind is not None:
        record["run_kind"] = str(run_kind)
    return record


def target_attempt(mission_id: str, target_id: str, *, attempt: int, run_id, park_run_id, outcome_class: str, reason_code,
                   phase, measurements, observation_ids, stop_state, next_actions, budget_left: int, t_wall_utc: str,
                   evidence=None, detail=None) -> dict:
    """What ONE attempt at a target came to, written when its run ended without placing it, BEFORE the mission holds or ends.

    `attempt` counts from 1 per target; `run_id` is that attempt's cycle run (None when no run was started) and
    `park_run_id` the park that prepared it (None for the first). `reason_code` and `phase` are the controller's own (its
    run.finished), `measurements` the values that decided the failure as named numbers and `observation_ids` the frames in
    scope, each list bounded by MAX_ATTEMPT_ITEMS. `stop_state` is where the run left the robot, derived from the run's own
    events ({holding, base_ahead, moved}, each true, false or None for unknown); `next_actions` what a press can do now and
    `budget_left` the retries this target has left. Ids, codes and numbers only: nothing here can be replayed at a robot.

    `evidence` (optional, additive) is what the attempt's run RESTED ON, the same ids and numbers a target outcome carries
    (target_outcome): a held attempt that is stopped or retried never gets an outcome, and this keeps what it used, the
    capability included. Absent from the record when there is none.

    `detail` (optional, additive) is the controller's own sentence for the ending (its run.finished failure.detail, which
    the controller already bounds and strips of paths and addresses), at most MAX_ATTEMPT_DETAIL_CHARS. It is what a hold
    that offers no Retry says to the operator: for a target binding's refusal, the deciding limit, its shortfall and the
    hint. Absent from the record when the run carried none."""
    record = {"rec": "mission_target_attempt", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
            "attempt": int(attempt), "run_id": run_id, "park_run_id": park_run_id, "outcome_class": outcome_class,
            "reason_code": reason_code, "phase": phase, "measurements": _bounded_measurements(measurements),
            "observation_ids": [item for item in (observation_ids or []) if isinstance(item, str)][:MAX_ATTEMPT_ITEMS],
            "stop_state": _stop_state_copy(stop_state), "next_actions": [str(action) for action in (next_actions or [])],
            "budget_left": int(budget_left), "t_wall_utc": t_wall_utc}
    if evidence is not None:
        record["evidence"] = dict(evidence)
    if isinstance(detail, str) and detail:
        record["detail"] = detail[:MAX_ATTEMPT_DETAIL_CHARS]
    return record


def retry_requested(mission_id: str, target_id: str, *, attempt: int, origin: str, t_wall_utc: str) -> dict:
    """A Retry of a held target was asked for, written BEFORE anything is started for it. A REQUEST, NOT AN OUTCOME: the park
    and the fresh attempt are recorded as runs of the target when they are started. `attempt` is the attempt being retried;
    `origin` the runtime's own word for who asked (the operator's console, or the automatic retry)."""
    return {"rec": "mission_retry", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
            "attempt": int(attempt), "origin": str(origin), "t_wall_utc": t_wall_utc}


def next_actions(outcome_class: str, stop_state, *, retry_left: bool) -> list:
    """What the operator can do after an attempt of `outcome_class` left the robot in `stop_state`, in the order a console
    offers them.

    WHILE THE MISSION HOLDS (HOLDING_CLASSES) the list is what a press can do NOW: a Retry only when the budget has one
    left and the recorded state says nothing is held AND the base is where it started (None, unknown, is never read as
    either), then the Stop. A retry is `retry` for a retryable failure and `move_block_then_retry` for an operator-needed
    one: the block has to be moved first, and the retry observes anew either way.

    AFTER THE END the list is what the operator does by hand or by command, from the same state, the worst reading taken
    for an unknown one: open the gripper by hand when a cube may be held, return the base when it is ahead, return the
    arm when it may have moved, then start a new mission."""
    state = stop_state if isinstance(stop_state, dict) else {}
    holding, base_ahead, moved = state.get("holding"), state.get("base_ahead"), state.get("moved")
    if outcome_class in HOLDING_CLASSES:
        actions = []
        if retry_left and holding is False and base_ahead is False:
            actions.append(NEXT_RETRY if outcome_class == OUTCOME_RETRYABLE else NEXT_MOVE_BLOCK_THEN_RETRY)
        return actions + [NEXT_STOP]
    actions = []
    if holding is not False:
        actions.append(NEXT_OPEN_GRIPPER_BY_HAND)
    if base_ahead is True:
        actions.append(NEXT_RETURN_BASE)
    if moved is not False or holding is not False:
        actions.append(NEXT_RETURN_ARM)
    return actions + [NEXT_START]


def _stop_state_copy(stop_state) -> dict:
    """{holding, base_ahead, moved}, each True, False or None; anything else is None (unknown), never a guess."""
    state = stop_state if isinstance(stop_state, dict) else {}
    return {name: state.get(name) if isinstance(state.get(name), bool) else None for name in ("holding", "base_ahead", "moved")}


def _bounded_measurements(measurements) -> list:
    """The named numbers of a failure, {name, value, unit}, at most MAX_ATTEMPT_ITEMS of them. Anything else is dropped."""
    kept = []
    for item in measurements or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        value = item.get("value")
        if not (value is None or isinstance(value, (bool, int, float, str))):
            continue
        unit = item.get("unit")
        kept.append({"name": item["name"], "value": value, "unit": unit if isinstance(unit, str) else None})
        if len(kept) == MAX_ATTEMPT_ITEMS:
            break
    return kept


def mission_target_bound(mission_id: str, target_id: str, *, run_id: str, observation_id, bound_pad_m, mode: str,
                         t_wall_utc: str) -> dict:
    """Where the FIRST execution's SEARCH bound this target, in PAD_FRAME, and in which placement mode.

    One per declared target, all written from the one run whose SEARCH bound them. Every later execution of this mission
    carries its target by this binding (autonomy/manipulation/target_binding.py), so the record says what each target
    WAS before anything was placed. Numbers and ids only."""
    return {"rec": "mission_target_bound", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "target_id": target_id,
            "run_id": run_id, "observation_id": observation_id,
            "bound_pad_m": [float(bound_pad_m[0]), float(bound_pad_m[1])], "mode": mode, "t_wall_utc": t_wall_utc}


def target_capability_required(mission_id: str, target_id: str, *, source: str, run_id, measured_mode: str,
                               observation_available: bool, reason, t_wall_utc: str) -> dict:
    """Why this target needed the mobile_approach capability, and what the runtime found when it asked for an actual
    observation of it. EVIDENCE ONLY: written to MISSION_EVIDENCE_FILE_NAME, never folded, never served.

    `source` is where the need was MEASURED: "binding" (the first execution's SEARCH bound this target to a cube that
    measured BASE_ADVANCE), "search" (this target's own run measured it at its SEARCH and refused before anything was
    picked up) or "search_fallback" (this target's own run bound it to the arm alone at its SEARCH, `measured_mode`
    ARM_ONLY, with an in-run fallback that moves the base, and refused before anything was picked up). It is never the
    declaration or the target's place in the mission. `run_id` is the run whose SEARCH measured it. `observation_available` and `reason` are target_observation_source.capability_observation_available's
    answer, asked before any gate is passed or run spawned. Ids, codes and one boolean: nothing in it can be replayed."""
    return {"rec": "mission_capability_required", "v": MISSION_RECORD_VERSION, "mission_id": mission_id,
            "target_id": target_id, "source": source, "run_id": run_id, "measured_mode": measured_mode,
            "observation_available": bool(observation_available), "reason": reason, "t_wall_utc": t_wall_utc}


def activation_attempt(mission_id: str, target_id: str, operation_uuid: str, candidate_key: str,
                       payload_digest: str, *, t_wall_utc: str, slot_before=None) -> dict:
    """Written BEFORE the operation is submitted, so a crash mid-submit leaves the uuid and the bytes it was bound
    to on the record. That is what makes a retry a retry rather than a new operation the resident will refuse.

    `slot_before` is the capability slot as it was read just before the submit (cake_activation.slot_reading), or None
    when the capability adapter reads no slot detail. It is what the read-back after the answer is compared against: a
    generation that did not move says more than a boolean that stayed False."""
    return {"rec": "mission_activation", "v": MISSION_RECORD_VERSION, "mission_id": mission_id,
            "target_id": target_id, "operation_uuid": operation_uuid, "candidate_key": candidate_key,
            "payload_digest": payload_digest, "activation_state": ACTIVATION_SUBMITTED,
            "slot_before": _reading_or_none(slot_before), "t_wall_utc": t_wall_utc}


def activation_answer(mission_id: str, operation_uuid: str, activation_state: str, *, outcome=None,
                      transaction_phase=None, transaction_uuid=None, detail=None, t_wall_utc: str,
                      transaction=None, slot_after=None, stopped_during_activation=None) -> dict:
    """What came back, with the transaction kept apart from the observation. `status ok` is not success and is not
    recorded as one: the phase is recorded, and OBSERVED is written only after the slot was read back.

    THE EVIDENCE IS KEPT, NOT ONLY THE VERDICT. `transaction` is what the resident's answer said about the operation
    (its identity, handle, phase, status and the epoch the answer printed, when it printed one). `slot_after` is the ONE
    list-slots read that decided the verdict: the slot's uuid, name, lifecycle state, capsule digest, generation and
    instance, and the resident's session, Plan digest and epoch from that same read. A boolean alone could not say
    which resident answered, under which Plan, or whether the generation moved. `stopped_during_activation` is whether
    the mission this was for had been asked to stop by the time the answer came back."""
    return {"rec": "mission_activation", "v": MISSION_RECORD_VERSION, "mission_id": mission_id,
            "operation_uuid": operation_uuid, "activation_state": activation_state, "outcome": outcome,
            "transaction_phase": transaction_phase, "transaction_uuid": transaction_uuid, "detail": detail,
            "transaction": dict(transaction) if isinstance(transaction, dict) else None,
            "slot_after": _reading_or_none(slot_after),
            "stopped_during_activation": stopped_during_activation if isinstance(stopped_during_activation, bool) else None,
            "t_wall_utc": t_wall_utc}


def _reading_or_none(reading):
    """A slot reading as a plain copy, or None. A reading is data read from the resident and is never shared by reference
    with the thing that read it."""
    return dict(reading) if isinstance(reading, dict) else None


def mission_ended(mission_id: str, state: str, *, detail=None, t_wall_utc: str) -> dict:
    return {"rec": "mission", "v": MISSION_RECORD_VERSION, "mission_id": mission_id, "state": state,
            "detail": detail, "t_wall_utc": t_wall_utc}


# ---- the fold -------------------------------------------------------------------------------------------------


def fold_missions(records: list) -> dict:
    """The mission file folded into one state per mission id, in first-seen order.

    A record of a version this function does not know is SKIPPED and counted, never guessed at. A reader that
    silently folded an unknown shape would report a mission it had not understood.

    THE END IS STICKY. Once a mission record sets COMPLETE, STOPPED or FAILED, a later mission, stop or target record
    changes nothing and is counted in `records_after_end` instead; an activation record is still folded into the
    activations, because an answer that arrived late is a fact about the slot and must stay findable. At the end record
    every target still ACTIVE or WAITING_FOR_CAPABILITY is given the end it met: STOPPED for a stopped mission, FAILED
    for a failed one. That is derived here, at the end record, so a file written before the rule reads the same way.
    """
    table: dict = collections.OrderedDict()
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("v") != MISSION_RECORD_VERSION:
            continue
        mission_id, kind = record.get("mission_id"), record.get("rec")
        if not isinstance(mission_id, str):
            continue
        if kind == "mission":
            state = table.setdefault(mission_id, _empty(mission_id))
            if record.get("targets") is not None and not state["targets"]:
                state["targets"] = [dict(target) for target in record["targets"]]
                for target in state["targets"]:
                    state["target_state"].setdefault(target.get("target_id"), TARGET_PENDING)
                state["started_wall_utc"] = record.get("t_wall_utc")
                state["data_mode"] = record.get("data_mode")
                # None for a record written before these were recorded: unknown, and never read as False.
                state["performs_motion"] = record.get("performs_motion") if isinstance(record.get("performs_motion"), bool) else None
                state["execution_label"] = record.get("execution_label") if isinstance(record.get("execution_label"), str) else None
            if state["state"] in MISSION_TERMINAL:
                state["records_after_end"] += 1
                continue
            if record.get("state") is not None:
                state["state"] = record["state"]
            if record.get("detail") is not None:
                state["detail"] = record["detail"]
            if state["state"] in MISSION_TERMINAL:
                _close_open_targets(state)
        elif kind == "mission_stop" and mission_id in table:
            state = table[mission_id]
            if state["state"] in MISSION_TERMINAL:
                state["records_after_end"] += 1
                continue
            if record.get("stop_requested") is True and not state["stop_requested"]:
                state["stop_requested"] = True
                state["stop_requested_wall_utc"] = record.get("t_wall_utc")
                state["stop_origin"] = record.get("origin") if isinstance(record.get("origin"), str) else None
        elif kind == "mission_target" and mission_id in table:
            state = table[mission_id]
            target_id = record.get("target_id")
            if not isinstance(target_id, str):
                continue
            if state["state"] in MISSION_TERMINAL:
                state["records_after_end"] += 1
                continue
            if record.get("target_state") is not None:
                state["target_state"][target_id] = record["target_state"]
            if record.get("target_state") == TARGET_ACTIVE:
                state["selected_target"] = target_id
            if record.get("waiting_reason") is not None:
                state["waiting_reason"] = record["waiting_reason"]
                state["selected_target"] = target_id
            if record.get("mission_state") is not None:
                state["state"] = record["mission_state"]
                # A mission that is RUNNING again is not waiting on anything. The resume record carries this, and the
                # reason is cleared with it rather than left behind to be read as the mission's current reason.
                if record["mission_state"] == MISSION_RUNNING:
                    state["waiting_reason"] = None
            # A child run's outcome, kept against the target it was for. The run id stays the run's own.
            if record.get("run_id") is not None:
                state["target_runs"].setdefault(target_id, [])
                if record["run_id"] not in state["target_runs"][target_id]:
                    state["target_runs"][target_id].append(record["run_id"])
                if record.get("run_kind") == RUN_KIND_PARK:
                    parks = state["target_park_runs"].setdefault(target_id, [])
                    if record["run_id"] not in parks:
                        parks.append(record["run_id"])
            if record.get("target_state") in (TARGET_PLACED, TARGET_FAILED):
                state["waiting_reason"] = None
            if isinstance(record.get("evidence"), dict):
                state["target_evidence"][target_id] = dict(record["evidence"])
        elif kind in ("mission_target_attempt", "mission_retry") and mission_id in table:
            # The hold's two records: what an attempt came to, and a Retry asked for. Sticky at the end like a target record.
            state = table[mission_id]
            target_id = record.get("target_id")
            if not isinstance(target_id, str):
                continue
            if state["state"] in MISSION_TERMINAL:
                state["records_after_end"] += 1
                continue
            if kind == "mission_target_attempt":
                entry = {name: record.get(name) for name in _ATTEMPT_FIELDS}
                if isinstance(record.get("detail"), str):
                    entry["detail"] = record["detail"][:MAX_ATTEMPT_DETAIL_CHARS]
                state["target_attempts"].setdefault(target_id, []).append(entry)
                if isinstance(record.get("evidence"), dict):
                    state["target_evidence"][target_id] = dict(record["evidence"])
            else:
                state["target_retries"].setdefault(target_id, []).append(
                    {name: record.get(name) for name in ("attempt", "origin", "t_wall_utc")})
        elif kind == "mission_target_bound" and mission_id in table:
            state = table[mission_id]
            target_id = record.get("target_id")
            if not isinstance(target_id, str):
                continue
            if state["state"] in MISSION_TERMINAL:
                state["records_after_end"] += 1
                continue
            state["target_bindings"][target_id] = {name: record.get(name) for name in ("run_id", "observation_id", "bound_pad_m", "mode")}
        elif kind == "mission_activation" and mission_id in table:
            # FOLDED WHATEVER BECAME OF THE MISSION, including after its end: see the docstring.
            state = table[mission_id]
            operation_uuid = record.get("operation_uuid")
            if not isinstance(operation_uuid, str):
                continue
            entry = state["activations"].get(operation_uuid)
            if entry is None:
                entry = {"operation_uuid": operation_uuid, "target_id": record.get("target_id"),
                         "candidate_key": record.get("candidate_key"), "payload_digest": record.get("payload_digest"),
                         "activation_state": None, "outcome": None, "transaction_phase": None,
                         "transaction_uuid": None, "detail": None, "transaction": None, "slot_before": None,
                         "slot_after": None, "stopped_during_activation": None,
                         "t_wall_utc": record.get("t_wall_utc")}
                state["activations"][operation_uuid] = entry
                state["activation_order"].append(operation_uuid)
            for field in ("activation_state", "outcome", "transaction_phase", "transaction_uuid", "detail",
                          "transaction", "slot_after", "stopped_during_activation"):
                if record.get(field) is not None:
                    entry[field] = record[field]
            for field in ("candidate_key", "payload_digest", "target_id", "slot_before"):
                if entry.get(field) is None and record.get(field) is not None:
                    entry[field] = record[field]
    return table


def _copy_or_none(value):
    return dict(value) if isinstance(value, dict) else None


def _close_open_targets(state: dict) -> None:
    """At a mission's end, a target that was still being carried or waited on takes the end the mission met.

    COMPLETE leaves nothing open by construction; a target still open then is left as it reads, rather than given an
    outcome nothing recorded."""
    end = {MISSION_STOPPED: TARGET_STOPPED, MISSION_FAILED: TARGET_FAILED}.get(state["state"])
    if end is None:
        return
    for target_id, target_state in list(state["target_state"].items()):
        if target_state in (TARGET_ACTIVE, TARGET_WAITING):
            state["target_state"][target_id] = end


def _empty(mission_id: str) -> dict:
    return {"mission_id": mission_id, "state": MISSION_RUNNING, "targets": [], "target_state": {},
            "target_runs": {}, "selected_target": None, "waiting_reason": None, "detail": None,
            "activations": collections.OrderedDict(), "activation_order": [], "started_wall_utc": None,
            "data_mode": None, "performs_motion": None, "execution_label": None,
            "stop_requested": False, "stop_requested_wall_utc": None, "stop_origin": None, "records_after_end": 0,
            "target_bindings": {}, "target_evidence": {}, "target_attempts": {}, "target_retries": {}, "target_park_runs": {}}


#: The fields of an attempt record the fold keeps, in the record's own order.
_ATTEMPT_FIELDS = ("attempt", "run_id", "park_run_id", "outcome_class", "reason_code", "phase", "measurements", "observation_ids",
                   "stop_state", "next_actions", "budget_left", "t_wall_utc")


def latest_attempt(state: dict, target_id) -> Optional[dict]:
    """The newest attempt record of a target in a folded mission state, or None."""
    attempts = state.get("target_attempts", {}).get(target_id) or []
    return attempts[-1] if attempts else None


def retry_asked_after(state: dict, target_id, attempt: Optional[dict]) -> bool:
    """Whether a Retry was asked for after `attempt` (the target's newest), that is for the hold it ended."""
    if attempt is None:
        return False
    return any(entry.get("attempt") == attempt.get("attempt") for entry in state.get("target_retries", {}).get(target_id) or [])


def _hold_view(state: dict) -> Optional[dict]:
    """The hold a console shows, or None: only while the mission WAITS FOR THE OPERATOR, from the held target's newest
    attempt. Everything in it was written down before the hold began."""
    if state["state"] != MISSION_WAITING or state.get("waiting_reason") != WAITING_ON_OPERATOR:
        return None
    target_id = state.get("selected_target")
    attempt = latest_attempt(state, target_id)
    if attempt is None:
        return None
    return {"target_id": target_id, **{name: _plain(attempt.get(name)) for name in _ATTEMPT_FIELDS},
            "detail": attempt.get("detail"), "retry_requested": retry_asked_after(state, target_id, attempt)}


def _mission_next_actions(state: dict) -> list:
    """What the operator can do now, for the whole mission. While it holds: the held attempt's own list. While it runs or
    waits on a capability: the Stop. After a COMPLETE end: a new mission. After a STOPPED or FAILED end: derived from the
    newest attempt's recorded state (a stop during a run records one), where a Retry asked for after that attempt means a
    park may have moved the arm since; with no attempt on the record, nothing is known and the worst reading is taken."""
    hold = _hold_view(state)
    if hold is not None:
        return list(hold["next_actions"])
    if state["state"] not in MISSION_TERMINAL:
        return [NEXT_STOP]
    if state["state"] == MISSION_COMPLETE:
        return [NEXT_START]
    target_id = state.get("selected_target")
    attempt = latest_attempt(state, target_id)
    if attempt is None:
        return next_actions(OUTCOME_FATAL, None, retry_left=False)
    stop_state = dict(attempt.get("stop_state") or {})
    if retry_asked_after(state, target_id, attempt):
        stop_state["moved"] = True
    ended = OUTCOME_STOPPED if state["state"] == MISSION_STOPPED else (
        attempt.get("outcome_class") if attempt.get("outcome_class") not in HOLDING_CLASSES else OUTCOME_FATAL)
    return next_actions(ended, stop_state, retry_left=False)


def _plain(value):
    """A copy of a record value: lists and dicts are copied one level, so a view never shares a list with the fold."""
    if isinstance(value, list):
        return [dict(item) if isinstance(item, dict) else item for item in value]
    if isinstance(value, dict):
        return dict(value)
    return value


def unknown_version_records(records: list) -> int:
    """How many records were skipped for carrying a version this fold does not know. Shown, never hidden: a file
    half of which could not be read is not a mission with half the events."""
    return sum(1 for record in records
               if isinstance(record, dict) and record.get("rec", "").startswith("mission")
               and record.get("v") != MISSION_RECORD_VERSION)


# ---- the view a console reads ---------------------------------------------------------------------------------


def mission_view(state: dict) -> dict:
    """What a console may see about one mission. Ids, states and digests; no path, no bytes, no motion command.

    A BROWSER RELOAD READS THIS. It starts nothing and resubmits nothing: every field is something that already
    happened and was written down before this was assembled.
    """
    if not state:
        return None
    activations = [dict(state["activations"][uuid]) for uuid in state["activation_order"]]
    return {
        "mission_record_version": MISSION_RECORD_VERSION,
        "mission_id": state["mission_id"],
        "state": state["state"],
        "data_mode": state.get("data_mode"),
        # What the execution adapter said about itself when the mission started: a rehearsal says performs_motion
        # false, and a view that dropped it would let a rehearsal outcome be read as a physical one.
        "performs_motion": state.get("performs_motion"),
        "execution_label": state.get("execution_label"),
        "started_wall_utc": state.get("started_wall_utc"),
        "selected_target": state.get("selected_target"),
        "waiting_reason": state.get("waiting_reason"),
        "detail": state.get("detail"),
        # A stop that was ASKED FOR, and when. It is a request: `state` says whether the mission has ended, and nothing
        # here says that motion ceased.
        "stop_requested": bool(state.get("stop_requested")),
        "stop_requested_wall_utc": state.get("stop_requested_wall_utc"),
        # Records that arrived after the mission ended and were not allowed to change it. Shown, never hidden.
        "records_after_end": state.get("records_after_end", 0),
        "targets": [
            {
                "target_id": target.get("target_id"),
                "label": target.get("label"),
                "reachable": target.get("reachable"),
                "requires_capability": target.get("requires_capability"),
                "state": state["target_state"].get(target.get("target_id"), TARGET_PENDING),
                # The child runs that carried THIS target, kept associated with it. Each keeps its own run id.
                "runs": list(state["target_runs"].get(target.get("target_id"), [])),
                # Where the first execution bound this target, and what its outcome rests on; None when nothing was.
                "binding": _copy_or_none(state.get("target_bindings", {}).get(target.get("target_id"))),
                "evidence": _copy_or_none(state.get("target_evidence", {}).get(target.get("target_id"))),
                # Every attempt that did not place this target, the Retries asked for and the parks that preceded them.
                "attempts": [{name: _plain(entry.get(name)) for name in _ATTEMPT_FIELDS}
                             for entry in state.get("target_attempts", {}).get(target.get("target_id"), [])],
                "retries": [dict(entry) for entry in state.get("target_retries", {}).get(target.get("target_id"), [])],
                "park_runs": list(state.get("target_park_runs", {}).get(target.get("target_id"), [])),
            }
            for target in state["targets"]
        ],
        "activations": activations,
        # The failure the mission holds on, with its stage, its measurements, its frames and what a press can do; None
        # unless the mission waits for the operator.
        "hold": _hold_view(state),
        "next_actions": _mission_next_actions(state),
    }
