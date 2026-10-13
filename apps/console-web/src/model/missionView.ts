// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE MISSION, ON THE SCREEN THE CONSOLE ALREADY HAS.
//
// The five-step rail, the target, the progress figure and the mission clock were all derived from ONE run, because
// until now there was one: a single cube, a single cycle, and the fold of the contract records was the whole story. A
// mission carries two cubes one after the other, and every one of those four figures then has to answer a question it
// was never asked before, which is WHICH TARGET it is speaking about.
//
// Three rules decide everything in this file.
//
//   THE RAIL DESCRIBES THE ACTIVE TARGET. Not the newest run, not the run this tab happens to be folding. The mission
//   record names the target it is on, and that target is what the five steps are drawn for.
//
//   A RUN BELONGS TO THE TARGET THE MISSION RECORDED IT AGAINST, and to no other. `target.runs` is the only thing that
//   says so. When the live fold's run is not in the active target's list, its steps are that OTHER target's progress
//   and they are not drawn here: the rail says what this tab knows about the active target instead, in the words the
//   phase table already has for not knowing.
//
//   A COUNT IS A READ, NOT AN INFERENCE. The placed figure counts targets the MISSION recorded as placed. It does not
//   count predicates this tab folded, and it never fills a missing target state with a zero or a no.

import { ageFromWallUtc } from './measurement';
import { clockWords } from './mission';
import type { MissionRecord, MissionTargetRecord } from './missionRecord';

/**
 * HOW A MISSION EXECUTES, as its own record states it.
 *
 * `physical` ONLY when the runtime's data mode is live AND the execution adapter said it moves the robot. A rehearsal
 * adapter says `performs_motion: false`, and a SIMULATION runtime's data mode is synthetic; either one makes a target
 * the record calls PLACED a thing the rehearsal RECORDED, not a cube on a pad. `unstated` is a live record that does
 * not say whether anything moved (written before the field existed), and it is never promoted to physical.
 */
export type MissionExecution = 'physical' | 'rehearsal' | 'unstated';

export function missionExecution(mission: MissionRecord | null): MissionExecution {
  if (mission === null) return 'unstated';
  if (mission.data_mode === 'live' && mission.performs_motion === true) return 'physical';
  if (mission.performs_motion === false || mission.data_mode === 'synthetic' || mission.data_mode === 'replay') return 'rehearsal';
  return 'unstated';
}

/** What each target state says on the screen, so no panel writes its own word for one. */
const TARGET_STATE_WORDS: Record<string, string> = {
  PENDING: 'not started',
  ACTIVE: 'being carried now',
  PLACED: 'placed on the pad',
  FAILED: 'failed',
  // The end a target meets when its mission was stopped while it was being carried or waited on. A stop that was
  // asked for, and nothing about whether motion ceased: the run's own stop records say that.
  STOPPED: 'stopped with the mission',
};

/**
 * THE WORDS FOR A TARGET THAT WAS NOT PHYSICALLY CARRIED. A fixture result is never drawn as a placement: the word
 * "placed" is reserved for a physical execution, so a rehearsal's terminal target states what the rehearsal recorded.
 */
const REHEARSAL_STATE_WORDS: Record<string, string> = {
  ACTIVE: 'being rehearsed now, nothing is moving',
  PLACED: 'recorded done by the rehearsal, nothing was moved',
};
const UNSTATED_STATE_WORDS: Record<string, string> = {
  PLACED: 'recorded done; the record does not say the robot moved',
};

/**
 * THE WORDS FOR A HOLD, keyed on the runtime's reason and never on the state alone. The runtime writes the state
 * WAITING_FOR_CAPABILITY for a capability wait AND for the hold after a failed attempt; only `waiting_reason` tells
 * them apart, so a capability wait is said only for `capability_unavailable`. A reason this console has no words for
 * is quoted, and a hold with no reason on record says so.
 */
function heldStateWords(reason: string | null): string {
  if (reason === CAPABILITY_UNAVAILABLE) return 'held, waiting for a capability';
  if (reason === OPERATOR_DECISION) return "held for the operator's decision after a failed attempt";
  return `held: ${heldReasonWords(reason)}`;
}

/**
 * The words for one target state. A state this console has no words for is printed as the runtime wrote it. The
 * mission decides whether a terminal target was a physical placement or a rehearsal's record of one, and, for a held
 * target, what it is held for.
 */
export function targetStateWords(state: string, mission: MissionRecord | null = null): string {
  if (state === 'WAITING_FOR_CAPABILITY') return heldStateWords(mission?.waiting_reason ?? null);
  const execution = missionExecution(mission);
  if (execution === 'rehearsal' && REHEARSAL_STATE_WORDS[state] !== undefined) return REHEARSAL_STATE_WORDS[state] as string;
  if (execution === 'unstated' && mission !== null && UNSTATED_STATE_WORDS[state] !== undefined) return UNSTATED_STATE_WORDS[state] as string;
  return TARGET_STATE_WORDS[state] ?? state;
}

/**
 * The same, in two or three words, for the rail, where the full sentence does not fit. Same execution rule: "placed"
 * is said only of a physical execution, and a rehearsal's terminal target says it was done in the rehearsal.
 */
export function targetStateShort(state: string, mission: MissionRecord | null = null): string {
  const execution = missionExecution(mission);
  switch (state) {
    case 'PLACED':
      return execution === 'physical' ? 'placed' : execution === 'rehearsal' ? 'done in the rehearsal' : 'done, motion not recorded';
    case 'ACTIVE':
      return execution === 'rehearsal' ? 'being rehearsed' : 'being carried';
    case 'WAITING_FOR_CAPABILITY':
      return 'held';
    default:
      return TARGET_STATE_WORDS[state] ?? state;
  }
}

/** What each mission state says. Same rule: an unknown token is shown as itself. */
const MISSION_STATE_WORDS: Record<string, string> = {
  RUNNING: 'running',
  COMPLETE: 'complete',
  STOPPED: 'stopped',
  FAILED: 'failed',
};

/** The words for a mission state; a held mission is said by the reason it is held for (`heldStateWords`). */
export function missionStateWords(state: string, waitingReason: string | null): string {
  if (state === 'WAITING_FOR_CAPABILITY') return heldStateWords(waitingReason);
  return MISSION_STATE_WORDS[state] ?? state;
}

/**
 * The target the mission is on.
 *
 * `selected_target` first, because it is the mission's own answer. A selected id that names no target in the list is
 * not an answer at all and falls through rather than being drawn as a target that is not there. After that, the one
 * target whose own state says it is active or held.
 */
export function activeTarget(mission: MissionRecord | null): MissionTargetRecord | null {
  if (mission === null) return null;
  const named = mission.targets.find((target) => target.target_id === mission.selected_target) ?? null;
  if (named !== null) return named;
  return mission.targets.find((target) => target.state === 'ACTIVE' || target.state === 'WAITING_FOR_CAPABILITY') ?? null;
}

/** The target the mission recorded a run against, or null when it recorded that run against none. */
export function targetOfRun(mission: MissionRecord | null, runId: string | null): MissionTargetRecord | null {
  if (mission === null || runId === null) return null;
  return mission.targets.find((target) => target.runs.includes(runId)) ?? null;
}

/** The controller's two placement modes, as it names them (`autonomy/manipulation/target_binding.py`). */
const MODE_ARM_ONLY = 'ARM_ONLY';
const MODE_BASE_ADVANCE = 'BASE_ADVANCE';

/**
 * WHETHER THIS TARGET NEEDS THE CAPABILITY, from what the record says.
 *
 * A declared target says so itself. An `auto` target says nothing until the robot's SEARCH measured it: BASE_ADVANCE
 * needs the capability (the base moves only through it), ARM_ONLY needs none, and no measurement yet is null, which is
 * "not decided" and not a no. A mode word this console does not know decides nothing here either.
 */
export function targetRequiresCapability(target: MissionTargetRecord): boolean | null {
  if (target.requires_capability !== 'auto') return target.requires_capability;
  const mode = target.measured_mode ?? null;
  if (mode === MODE_BASE_ADVANCE) return true;
  if (mode === MODE_ARM_ONLY) return false;
  return null;
}

/**
 * The clause after "reachable: yes" in the mission drawer, for what this target needs. Null is not a no; an `auto`
 * target says the decision is the measurement's, and states the measured mode once there is one. An `auto` target
 * measured ARM_ONLY that the mission holds for the capability (`mission`, when given, waits for it on this target) was
 * bound to the arm alone with a fallback that moves the base (the controller's additive placement words,
 * mission_fallback_capability_required): the wait is for that fallback, and the clause says so rather than "no
 * capability is needed". The fallback is conditional (verify r6 policy MF2): should the release be refused, the base moves
 * only if the controller's way back brings the loaded arm to the carry pose, and otherwise the arm holds the cube where it
 * stands, so the clause never states it as a fact.
 */
export function targetCapabilityWords(target: MissionTargetRecord, capabilityName: string | null, mission: MissionRecord | null = null): string {
  const name = capabilityName ?? '(name not reported by the robot)';
  if (target.requires_capability === 'auto') {
    const mode = target.measured_mode ?? null;
    const waitedFor = mission !== null && mission.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason === CAPABILITY_UNAVAILABLE && mission.selected_target === target.target_id;
    if (mode === null) return 'whether it needs a capability is decided by measurement, and the robot has not measured it yet';
    if (mode === MODE_BASE_ADVANCE) return `decided by measurement: it measured ${mode}, so it needs the capability ${name}`;
    if (mode === MODE_ARM_ONLY && waitedFor)
      return `decided by measurement: it measured ${mode}, so the arm alone places it, and the mission waits for the capability ${name} that its base fallback needs: should the release be refused, the base moves only if the loaded arm comes back to the carry pose, otherwise the arm holds the cube where it stands for the operator`;
    if (mode === MODE_ARM_ONLY) return `decided by measurement: it measured ${mode}, so the arm alone places it and no capability is needed`;
    return `decided by measurement: it measured ${mode}`;
  }
  if (target.requires_capability === null) return 'whether it requires a capability is not recorded';
  return target.requires_capability ? `requires the capability ${name}` : 'requires no capability';
}

/** The runtime's reason for a hold that is a wait for the capability, the one hold an activation is admitted for. */
export const CAPABILITY_UNAVAILABLE = 'capability_unavailable';
/** The runtime's reason for a hold after a failed attempt, which waits for the operator's choice and never for Cake. */
export const OPERATOR_DECISION = 'operator_decision';

/**
 * True when the mission record says it is held FOR THE CAPABILITY. The mission state alone does not say so: the runtime
 * also holds a mission in WAITING_FOR_CAPABILITY after a failed attempt, with the reason `operator_decision`, and it
 * admits an activation only for `capability_unavailable`. Every sentence about a capability wait is keyed on this.
 */
export function heldForCapability(mission: MissionRecord | null): boolean {
  return mission !== null && mission.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason === CAPABILITY_UNAVAILABLE;
}

/**
 * WHAT THE HOLD OFFERS, read from the runtime's own record of the held attempt and from nothing else.
 *
 *   `retry`                  the hold's next actions carry `retry`: Try again parks the arm and starts a fresh attempt.
 *   `move_block_then_retry`  they carry `move_block_then_retry`: the block is moved by hand first, then Try again.
 *   `stop_only`              the runtime served the hold and it offers no retry (the budget is spent, something may be
 *                            held, the base is not home, or this runtime cannot park the arm): only Stop.
 *   `not_served`             the mission is held for a decision and the record carries no hold, so no retry is offered.
 *
 * Null when the mission is not held for the operator's decision at all.
 */
export type HoldOffer = 'retry' | 'move_block_then_retry' | 'stop_only' | 'not_served';

export function holdOffer(mission: MissionRecord | null): HoldOffer | null {
  if (mission === null || mission.state !== 'WAITING_FOR_CAPABILITY' || mission.waiting_reason !== OPERATOR_DECISION) return null;
  const hold = mission.hold ?? null;
  if (hold === null) return 'not_served';
  if (hold.next_actions.includes('retry')) return 'retry';
  if (hold.next_actions.includes('move_block_then_retry')) return 'move_block_then_retry';
  return 'stop_only';
}

/** The words a control is named by beside a hold. */
const CONTROL_WORDS: Record<string, string> = {
  return_arm: 'Return arm',
  scan_scene: 'Scan scene',
  observe: 'Observe',
  start_cycle: 'Start',
  start: 'Start',
  stop: 'Stop',
};

/**
 * WHY A COMMAND IS OFF BESIDE A HOLD FOR THE OPERATOR'S DECISION, or null. The runtime takes only Stop while a mission is
 * carried, a held one included (daemon._mission_holds_run_slot); what a press can do during the hold is the hold's own
 * `next_actions`. So a control is on beside a hold only when the hold lists it, and the runtime never lists Return arm,
 * Scan scene or Observe there. Null when the mission is not held for a decision: a hold a Stop ended is not one.
 * The Actions panel, the click-time re-check and the failure banner all read this one function.
 */
export function holdBlock(mission: MissionRecord | null, capabilityId: string): string | null {
  const offer = holdOffer(mission);
  if (offer === null) return null;
  const name = CONTROL_WORDS[capabilityId] ?? capabilityId;
  const hold = mission?.hold ?? null;
  if (hold === null) return `the mission is held for your decision and the runtime served no record of what its hold offers, so only Stop is offered, not ${name}`;
  if (hold.next_actions.includes(capabilityId)) return null;
  return `the runtime's hold offers ${hold.next_actions.join(', ') || 'nothing'}, not ${name}`;
}

/**
 * WHY THE MISSION'S OWN LIST TURNS AN ACTION OFF, or null. The runtime serves what the operator can do now for the whole
 * mission (`mission._mission_next_actions`: the hold's list while it holds, Stop while it runs, and after its end what is
 * done by hand or by command, then a new mission). An action it does not list is off in the list's words. Null when the
 * mission lists it, and when the record carries no list (a runtime older than the field), where nothing is said here.
 */
export function missionOfferBlock(mission: MissionRecord | null, action: string): string | null {
  const offered = mission?.next_actions;
  if (mission === null || !Array.isArray(offered) || offered.includes(action)) return null;
  return `the runtime's mission offers ${offered.join(', ') || 'nothing'}, not ${CONTROL_WORDS[action] ?? action}`;
}

/** The words for each action the runtime lists after a mission's end (`mission.next_actions`). */
const AFTER_END_WORDS: Record<string, string> = {
  open_gripper_by_hand: 'Open the gripper by hand: the record does not say it is empty, so a cube may still be held.',
  return_base: 'Return the base to where it started: the record has it ahead of its start. This screen has no control for it.',
  return_arm: 'Return arm: the arm may have moved.',
  start: 'Start a new mission.',
};

/** The actions after an end that are done by hand or by a command this screen does not draw. */
const BY_HAND: ReadonlySet<string> = new Set(['open_gripper_by_hand', 'return_base']);

/**
 * WHAT THE RUNTIME LISTS BEFORE A NEW MISSION, in its order and its words, or null. After a mission's end its
 * `next_actions` is what the operator does by hand or by command: open the gripper when a cube may be held, return the
 * base when it is ahead, return the arm when it may have moved, then start. The screen draws controls for Return arm and
 * Start; the steps it has no control for are said here so that none is dropped. Null while the mission is carried, when
 * the record carries no list, and when the list names no step done by hand (the controls then say it all).
 */
export function stepsAfterEnd(mission: MissionRecord | null): string[] | null {
  if (mission === null || missionIsRunning(mission) || !Array.isArray(mission.next_actions)) return null;
  const list = mission.next_actions;
  if (!list.some((action) => BY_HAND.has(action))) return null;
  return list.map((action) => AFTER_END_WORDS[action] ?? action);
}

/** The sentence for what the hold offers, for the header, the held panel and the banner. */
export function holdOfferWords(offer: HoldOffer): string {
  switch (offer) {
    case 'retry':
      return 'Try again parks the arm and starts a fresh attempt; Stop ends the mission.';
    case 'move_block_then_retry':
      return 'Move the block by hand first; Try again then parks the arm and starts a fresh attempt. Stop ends the mission.';
    case 'stop_only':
      return 'The runtime offers only Stop for this hold: it ends the mission. No retry is offered.';
    case 'not_served':
      return 'The runtime served no record of the held attempt, so no retry is offered; Stop ends the mission.';
  }
}

export interface MissionWaiting {
  /**
   * What the hold is for: the capability (`capability_unavailable`), the operator's decision after a failed attempt
   * (`operator_decision`), or a reason this console has no words for, which is quoted and never read as either.
   */
  kind: 'capability' | 'decision' | 'other';
  target: MissionTargetRecord | null;
  /** The runtime's own reason code, unaltered. */
  reasonCode: string | null;
  /** The heading. It says HELD, never failed and never stopped. */
  headline: string;
  /** Why, in one sentence, naming the target and the capability when the record names one. */
  sentence: string;
  /** What the operator can do about it, in one sentence. A held mission must never read as a dead one. */
  recovery: string;
  /** The capability the held target requires: the robot's configured name when read, null when it requires none. */
  capability: string | null;
  /** For a decision hold: what the runtime's hold record offers (`holdOffer`). Null for any other hold. */
  offer?: HoldOffer | null;
}

/** The reason codes this console has words for. An unknown one is quoted as the runtime wrote it. */
const WAITING_REASON_WORDS: Record<string, string> = {
  capability_unavailable: 'the capability it requires is not in the resident',
};

function heldReasonWords(code: string | null): string {
  return code === null ? 'the runtime did not say why' : WAITING_REASON_WORDS[code] ?? `the runtime gives the reason ${code}`;
}

/**
 * WHY THE MISSION IS HELD, and which target is waiting.
 *
 * Null unless the mission itself says it is waiting. A held mission is a mission that is fine and is not going on, and
 * every word here is chosen so that it cannot be read as a crash, a hang or a failure: nothing has failed, nothing is
 * being retried behind the operator's back, and the thing that lets it go on is named.
 */
export function missionWaiting(mission: MissionRecord | null, capabilityName: string | null = null): MissionWaiting | null {
  if (mission === null || mission.state !== 'WAITING_FOR_CAPABILITY') return null;
  const target = activeTarget(mission) ?? mission.targets.find((item) => item.state === 'WAITING_FOR_CAPABILITY') ?? null;
  const code = mission.waiting_reason;
  const why = heldReasonWords(code);
  // The label opens the sentence, so it opens with a capital; the runtime's own label is otherwise shown as written.
  const name = target === null ? 'The next target' : target.label.charAt(0).toUpperCase() + target.label.slice(1);
  const detail = mission.detail === null ? '' : ` ${mission.detail}`;
  // THE HOLD AFTER A FAILED ATTEMPT. The runtime writes it with the same mission state as a capability wait, and it is
  // not one: nothing about Cake or a capability is said of it, and an activation is not offered for it.
  if (code === OPERATOR_DECISION) {
    return {
      kind: 'decision',
      target,
      reasonCode: code,
      headline: 'Held for your decision',
      sentence: `${name}: the last attempt did not finish, and the mission is held for your decision.${detail}`,
      recovery: `Nothing moves until you choose. ${holdOfferWords(holdOffer(mission) ?? 'not_served')}`,
      capability: null,
      offer: holdOffer(mission) ?? 'not_served',
    };
  }
  // A REASON THIS CONSOLE HAS NO WORDS FOR, or none at all. Quoted, and never read as a capability wait.
  if (code !== CAPABILITY_UNAVAILABLE) {
    return {
      kind: 'other',
      target,
      reasonCode: code,
      headline: 'Held',
      sentence: `${name} is held: ${why}.${detail}`,
      recovery: 'The mission stays where it is until the runtime moves it on; Stop ends it.',
      capability: null,
    };
  }
  // THE RECORD SAYS WHETHER, THE ROBOT SAYS WHICH. `requires_capability` is a boolean or the word `auto`; the name is
  // the capability the robot's runtime is configured with (GET /api/robot). A target that requires one whose name was
  // not read says so. An `auto` target that is WAITING needs it whatever its record carries for the measured mode:
  // the runtime opens the wait only for a target that measured BASE_ADVANCE, or one bound to the arm alone whose base
  // fallback needs it (mission_fallback_capability_required), so the wait itself states the need.
  const needs = target === null ? null : target.requires_capability === 'auto' ? true : targetRequiresCapability(target);
  const capability = needs === true ? (capabilityName ?? 'a capability whose name the runtime has not reported') : null;
  return {
    kind: 'capability',
    target,
    reasonCode: code,
    headline: 'Held, waiting for a capability',
    sentence:
      `${name} cannot be carried yet: ${why}` +
      (capability === null ? '.' : ` (${capability}).`) +
      (mission.detail === null ? '' : ` ${mission.detail}`),
    recovery:
      'Nothing has failed and nothing is being retried. The mission stays where it is until the capability is in the ' +
      'resident, and activating one of the prepared candidates in the Cake panel is what puts it there.',
    capability,
  };
}

export interface MissionCount {
  /**
   * The sentence under Progress. It counts the mission's own targets and never a predicate this tab folded, and it
   * says "placed" ONLY for a physical execution: a rehearsal's count is what the rehearsal recorded.
   */
  words: string;
  /** The same count in a few words, for the rail. Never "placed" unless `physical`. */
  short: string;
  /** Targets the mission recorded as PLACED. A physical placement only when `physical` is true. */
  placed: number;
  total: number;
  physical: boolean;
  /** Where the count came from, for the title attribute. */
  basis: string;
}

/**
 * How many of the mission's targets the MISSION records as done, worded for how it executes.
 *
 * THE COUNT AGREES IN NUMBER WITH THE TOTAL. The one-block demo carries one target, and "0 of 1 cubes placed" was on
 * the header of every such mission; the single-run figure in `model/mission.ts` already says "of 1 cube placed", and the
 * two figures must not spell the same fact two ways.
 */
export function missionPlaced(mission: MissionRecord): MissionCount {
  const total = mission.targets.length;
  const placed = mission.targets.filter((target) => target.state === 'PLACED').length;
  const execution = missionExecution(mission);
  const physical = execution === 'physical';
  const cubes = total === 1 ? 'cube' : 'cubes';
  const targets = total === 1 ? 'target' : 'targets';
  const words =
    total === 0
      ? 'this mission carries no target'
      : physical
        ? `${String(placed)} of ${String(total)} ${cubes} placed`
        : execution === 'rehearsal'
          ? `Rehearsal: ${String(placed)} of ${String(total)} ${targets} recorded done, no physical placement`
          : `${String(placed)} of ${String(total)} ${targets} recorded done; the record does not say the robot moved`;
  const short =
    total === 0
      ? 'no target'
      : physical
        ? words
        : execution === 'rehearsal'
          ? `Rehearsal: ${String(placed)} of ${String(total)} done`
          : `${String(placed)} of ${String(total)} done, motion not recorded`;
  return {
    words,
    short,
    placed,
    total,
    physical,
    basis:
      `counted from the state of each target on mission ${mission.mission_id}, not from a predicate this tab folded; ` +
      `data mode ${mission.data_mode ?? 'not recorded'}, performs motion ${mission.performs_motion === null ? 'not recorded' : String(mission.performs_motion)}` +
      (mission.execution_label === null ? '' : `, execution ${mission.execution_label}`),
  };
}

export interface MissionClock {
  /** mm:ss, or null when the record carries no time this can be measured from. Null prints as unavailable. */
  words: string | null;
  /** What the figure is a measurement of. Always present, including for the null case. */
  meaning: string;
  /** True while it is still growing, so the screen knows it advances on the shared tick. */
  running: boolean;
}

/** Whether the mission is still going. A held mission IS still going: it is waiting, not finished. */
export function missionIsRunning(mission: MissionRecord): boolean {
  return mission.state === 'RUNNING' || mission.state === 'WAITING_FOR_CAPABILITY';
}

/**
 * HOW LONG THE MISSION HAS BEEN GOING.
 *
 * It is measured from the mission record's own wall time against THIS computer's clock, which crosses two machines'
 * clocks, and the meaning sentence says so rather than hiding it. The record carries no finish time, so a mission that
 * has stopped reports time SINCE IT STARTED and says that is what it is; it is never presented as a duration that
 * nothing measured.
 */
export function missionClock(mission: MissionRecord, nowMs: number): MissionClock {
  const ageMs = ageFromWallUtc(mission.started_wall_utc, nowMs);
  if (ageMs === null) {
    return {
      words: null,
      meaning: 'This mission record carries no start time that can be read, so there is no elapsed time to state.',
      running: false,
    };
  }
  const running = missionIsRunning(mission);
  return {
    words: clockWords(ageMs),
    meaning: running
      ? "Since the mission record's own wall time, measured against this computer's clock. The two clocks are on different machines and nothing here has measured their relation."
      : "Since the mission record's own wall time. The mission is no longer running and the record states no finish time, so this is time since it began and not its duration.",
    running,
  };
}

/**
 * Whether the prepared activation may be offered at all.
 *
 * ONLY while the mission says it is waiting. Offering it at any other moment would be offering to change what the
 * resident holds under a mission that is in the middle of moving the robot.
 *
 * AND NOT ONCE ITS STOP HAS BEEN ASKED FOR. A held mission whose stop was requested still reads WAITING_FOR_CAPABILITY
 * until the stop lands, and the runtime answers any activation for it `mission_stopping` with nothing submitted
 * (demo_runtime.mission_coordinator). Offering the button then offers a press that is certain to be refused, beside a
 * Start that already says the mission is stopping.
 */
export function activationOffered(mission: MissionRecord | null): boolean {
  return mission !== null && heldForCapability(mission) && mission.stop_requested !== true;
}

/** Why the prepared activation is not offered, in words, or null when it is. The reasons `activationOffered` reads. */
export function activationOffReason(mission: MissionRecord | null): string | null {
  if (mission !== null && mission.stop_requested === true && mission.state === 'WAITING_FOR_CAPABILITY') {
    return `mission ${mission.mission_id} is stopping: its stop was asked for, and the runtime submits nothing for a mission that is stopping (mission_stopping)`;
  }
  if (mission !== null && mission.state === 'WAITING_FOR_CAPABILITY' && !heldForCapability(mission)) {
    return `mission ${mission.mission_id} is held for ${mission.waiting_reason === OPERATOR_DECISION ? "the operator's decision after a failed attempt" : heldReasonWords(mission.waiting_reason)}, not for a capability, so the runtime activates nothing for it`;
  }
  return activationOffered(mission) ? null : 'the mission is not waiting for a capability, so there is nothing to activate for it';
}

/** The activations for one target, in the order the runtime recorded them. Scoped, like the runs are. */
export function activationsForTarget(mission: MissionRecord, targetId: string): MissionRecord['activations'] {
  return mission.activations.filter((activation) => activation.target_id === targetId);
}

/* ---- the operator-facing Start --------------------------------------------------------------------------------- */

/**
 * WHY A RUNTIME HAS NO MISSION SURFACE, in words, for the details the runtime sends with `mission_unavailable`
 * (demo_runtime.mission_service). Each is one requirement the runtime found missing. An unknown one is quoted as sent.
 */
const MISSION_UNAVAILABLE_WORDS: Record<string, string> = {
  rehearsal_requires_synthetic_mode:
    'the only mission execution it has is the rehearsal, which runs in a simulation runtime and nowhere else; a live mission stays refused until the robot-side execution adapter exists',
  cake_not_configured: 'no Cake resident is configured for it',
  rehearsal_refuses_robot_resident: 'a rehearsal is never run against the robot resident this runtime is configured with',
  no_activation_candidate_prepared: 'no activation candidate is prepared',
  capability_not_configured: 'no capability binding is configured',
};

/** The sentence for a runtime that answered `mission_unavailable`, with its own detail beside the words for it. */
export function missionUnavailableWords(detail: string | null): string {
  if (detail === null) return 'This runtime serves no mission, and did not say which requirement is missing (mission_unavailable).';
  const words = MISSION_UNAVAILABLE_WORDS[detail] ?? `the runtime gives the reason ${detail}`;
  return `This runtime serves no mission: ${words} (mission_unavailable, ${detail}).`;
}

const RUN_HOLDS_THE_SLOT = 'A controller run is active; the runtime starts no mission while one holds the run slot.';

export interface MissionStartReading {
  mission: MissionRecord | null;
  /** The last mission read's refusal, when it failed. `no_mission_recorded` is a surface with nothing started on it. */
  missionError: string | null;
  missionErrorDetail: string | null;
  /** True while a controller run holds the runtime's single run slot. */
  runActive: boolean;
  /**
   * False on a screen that draws no Try again control (the classic screen), so the decision hold's sentence says where
   * the retry is offered instead of naming a button that is not there. Absent means the screen draws it.
   */
  retryOnThisScreen?: boolean;
}

/**
 * WHY THE OPERATOR-FACING START IS OFF, as far as the MISSION is concerned, or null when a mission may be started.
 *
 * Start addresses the canonical mission start (POST /api/mission/start) and nothing else, so it is off whenever that
 * request would certainly be refused, and the sentence says why in the runtime's terms:
 *
 *   A RUNTIME WITH NO MISSION SURFACE. A live runtime answers `mission_unavailable` until a real execution adapter
 *   exists, and so does a simulation runtime that is missing a requirement. The runtime's own detail is shown.
 *   A SURFACE NOT READ. Nothing is offered on a guess: until one mission read has come back, Start is off.
 *   A MISSION ALREADY CARRIED, held or stopping. The runtime refuses a second one (`mission_already_running`), and Stop
 *   is what ends the one there is.
 *   A CONTROLLER RUN in the single run slot. The runtime refuses a mission while one is active (`run_active`).
 *
 * The command guards (the uncertain block, the capability list, the robot's motion hold) are applied beside this by
 * the screen, from the same functions every control uses; this decides only what is about missions.
 */
export function missionStartOff(reading: MissionStartReading): string | null {
  const { mission, missionError } = reading;
  if (missionError === 'mission_unavailable') return missionUnavailableWords(reading.missionErrorDetail);
  // THE SURFACE IS THERE AND NOTHING HAS BEEN STARTED ON IT. That is an answer, not a failed read, and it is the one
  // state in which a first mission may be started.
  if (missionError === 'no_mission_recorded') return reading.runActive ? RUN_HOLDS_THE_SLOT : null;
  if (mission === null) {
    if (missionError === null) return 'The mission surface has not been read yet, so nothing is offered on a guess.';
    // The source's own words: a runtime that did not answer, a route this runtime does not have, or the script inside
    // the page, which carries no mission. None of them is a surface a mission could be started on.
    return `No mission can be started from here: ${missionError}.`;
  }
  if (missionError !== null) return `The last mission read failed (${missionError}), so what the mission is doing now is not known here.`;
  // THE RUNTIME'S LIST DECIDES. A mission that lists `start` may be followed by a new one (the run slot aside, which the
  // capability list answers); one that does not is not, and the sentences below say why in the mission's terms.
  if (Array.isArray(mission.next_actions)) {
    if (mission.next_actions.includes('start')) return reading.runActive ? RUN_HOLDS_THE_SLOT : null;
    const offered = missionOfferBlock(mission, 'start') as string;
    return missionIsRunning(mission) ? carriedStartOff(mission, reading) : `${offered.charAt(0).toUpperCase()}${offered.slice(1)}.`;
  }
  if (missionIsRunning(mission)) return carriedStartOff(mission, reading);
  if (reading.runActive) return RUN_HOLDS_THE_SLOT;
  return null;
}

/** Why Start is off beside a mission that is carried: stopping, running, held for the capability or for a decision. */
function carriedStartOff(mission: MissionRecord, reading: MissionStartReading): string {
  if (mission.stop_requested === true) return `Mission ${mission.mission_id} is stopping: its stop was asked for and it has not ended yet.`;
  if (mission.state !== 'WAITING_FOR_CAPABILITY') return `Mission ${mission.mission_id} is being carried. Stop ends it; a second mission is not started beside it.`;
  if (heldForCapability(mission)) return `Mission ${mission.mission_id} is held, waiting for a capability. Activate a candidate in the Cake panel, or Stop to end it.`;
  if (mission.waiting_reason === OPERATOR_DECISION) {
    const offer = holdOffer(mission) ?? 'not_served';
    const retries = offer === 'retry' || offer === 'move_block_then_retry';
    return retries && reading.retryOnThisScreen === false
      ? `Mission ${mission.mission_id} is held for your decision after a failed attempt. The retry is offered on the default screen; Stop ends it.`
      : `Mission ${mission.mission_id} is held for your decision after a failed attempt. ${holdOfferWords(offer)}`;
  }
  return `Mission ${mission.mission_id} is held: ${heldReasonWords(mission.waiting_reason)}. Stop ends it; a second mission is not started beside it.`;
}

/** The word on the Start button: what the mission is doing, never a promise about the robot. */
export function missionStartLabel(reading: MissionStartReading, starting: boolean): string {
  if (starting) return 'Starting';
  const mission = reading.missionError === null ? reading.mission : null;
  if (mission !== null && missionIsRunning(mission)) {
    if (mission.stop_requested === true) return 'Stopping';
    return mission.state === 'WAITING_FOR_CAPABILITY' ? 'Held' : 'Running';
  }
  return reading.runActive ? 'Running' : 'Start';
}
