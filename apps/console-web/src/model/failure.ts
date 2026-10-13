// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Why a run did not end well, READ FROM THE RECORD, so that a refusal can be understood without a terminal.
//
// Three rules hold this file together:
//
//  1. THE SENTENCE COMES FROM THE CONTROLLER. `run.finished.failure.detail` is the text the controller itself decided
//     at the point of decision. It is shown as it arrived. This console never paraphrases it and never writes a
//     sentence of its own in its place.
//  2. A REASON CODE THIS CONSOLE DOES NOT KNOW IS SHOWN AS ITSELF. There is a gloss table below for the codes that were
//     read out of the controller; a code outside it renders as the bare code with everything the payload carried under
//     it. A blank, a dash or a plausible-sounding guess would each be worse than the code.
//  3. A MEASUREMENT IS PRINTED AGAINST ITS THRESHOLD ONLY WHEN BOTH WERE RECORDED. A measured value with no threshold
//     is a measured value, not a comparison, and it is shown as one.
//
// The gloss for a code is a TEMPLATE, and the template machinery is `reason.ts`, which holds the runtime's glossary:
// one shape, one fill, one way of writing a code for a reader, whichever of the two a code came out of. The argument
// names below are the keyword arguments of the controller's own raise sites, so a sentence that states a number states
// the number that site decided with. A record that carries none reads as the wordy sentence.

import { predicateFailed } from './predicate';
import type { Capability, NamedValue } from '@kiwi/demo-contracts';
import { nonPlacementCommandSucceeded, UNSCOPED_COMMAND } from './command';
import { outcomeIsOfFoldedRun } from './steps';
import type { CommandScope } from './command';
import { argsFromNamedValues, fillTemplate, labelWithCode } from './reason';
import type { CodeTemplate, TemplateArgs } from './reason';
import { STAGE_FAILURES } from './stages/index';
import { utcDayOf, utcStamp } from './utcTime';
import type { PredicateView, StopView, ViewState } from './types';

/**
 * A short gloss for the reason codes that exist in `bin/laptop/manipulation/run_single_cube_cycle.py`, read out of the
 * raise sites. It says WHERE the run stopped in words an operator uses; it never restates the controller's own
 * sentence, which is shown next to it in full. A code that is not here is not glossed and is not guessed at.
 *
 * `needs` names values that a raise site really passes. Where the values a site carries do not decide between two
 * causes on their own, the entry states no number at all rather than name one of them: the measurement table under the
 * sentence carries every value the record had, and that table is where a reader goes for the rest.
 *
 * THE ENTRIES LIVE IN THE STAGE FILES (./stages/search.ts, align.ts, grasp.ts, placement.ts, recovery.ts), one per stage
 * of the demo, so the track that owns a stage's policy edits its own sentences. This is the one table built from them.
 */
const REASON_GLOSS: Readonly<Record<string, CodeTemplate>> = STAGE_FAILURES;

/** The failure codes this console has a sentence for, so a test can hold them against the controller's raise sites. */
export const FAILURE_REASON_CODES: readonly string[] = Object.keys(REASON_GLOSS);

/**
 * CODE FAMILIES: a raise site that builds its code from a prefix and another module's code. The one there is is the
 * controller's refusal of a desktop perception answer (`run_single_cube_cycle.py`, _remote_perceive: the code is
 * "perception_remote_" and remote_perception.py's reason code, and its sentence says nothing is decided on that answer and
 * the arm holds). The gloss names the family; the code itself, with the refusal's own part, is shown beside it.
 */
export const FAILURE_REASON_PREFIXES: Readonly<Record<string, CodeTemplate>> = {
  perception_remote_: { gloss: 'the desktop perception answer for a view was refused, so nothing was decided on it and the arm held where it was', needs: [] },
};

function glossTemplate(code: string): CodeTemplate | undefined {
  const own = REASON_GLOSS[code];
  if (own !== undefined) return own;
  const prefix = Object.keys(FAILURE_REASON_PREFIXES).find((item) => code.startsWith(item) && code.length > item.length);
  return prefix === undefined ? undefined : FAILURE_REASON_PREFIXES[prefix];
}

/** One measured value from the record, and the threshold it was decided against when there was one. */
export interface MeasuredAgainst {
  name: string;
  value: number | string | boolean | null;
  unit: string | null;
  threshold: number | null;
  /** Where this line came from, so a reader can go and check it. */
  source: 'failure' | 'predicate';
}

/**
 * What an operator can do next, taken from the SERVER's capability list and from nothing else.
 *
 * The console has a table below of which capability is the right next step after which reason code, and that table
 * decides what is OFFERED. Whether it is available is the runtime's answer, not this console's: a capability the
 * snapshot does not carry is not offered at all, and one the runtime disabled or blocked is listed with the runtime's
 * own reason and no button.
 */
export interface NextAction {
  capability_id: string;
  /** The name this action carries on the controls, so the two read as the same thing. */
  label: string;
  /** Why it is the next step after this reason code. */
  why: string;
  enabled: boolean;
  /** The runtime's own reason when it is not enabled, and null when it is. */
  reason: string | null;
}

export interface FailureExplanation {
  /** The reported outcome that made this an explanation at all. */
  outcome: string;
  /**
   * WHICH RUN this explains. A snapshot carries the runtime's last finished run, so this panel can be about a run that
   * ended before the one now in progress, and a screen that does not say so is showing an old failure as a current
   * one. Null on a record that carried no run id.
   */
  run_id: string | null;
  /** The state machine's state when it stopped, or null when the record did not carry one. */
  phase: string | null;
  reason_code: string | null;
  /** The gloss for a code this console knows. Null for every other code, including null itself. */
  gloss: string | null;
  /**
   * The same gloss with the values the record carried stated in it, where the raise site carried every one the
   * sentence names. It falls back to the wordy gloss, and is null for a code this console has no gloss for.
   */
  sentence: string | null;
  /** False when the code is not one this console has a gloss for: the screen then shows the code and the payload. */
  recognised: boolean;
  /** The controller's own sentence, verbatim. Null when the record carried no failure object. */
  detail: string | null;
  measurements: MeasuredAgainst[];
  /**
   * The observations the stop's own record named (`failure.observation_ids`), and no other: the only ids a screen may
   * call the evidence the controller named (verdict 2026-09-27 item 9).
   */
  observation_ids: string[];
  /** The observations the false predicates read that the stop's record did not name, listed apart as such. */
  predicate_observation_ids: string[];
  /** Every predicate that measured false in this run, newest last. */
  failing_predicates: PredicateView[];
  /**
   * True when the run ended badly and the record carried no reason_code at all. The screen says the run failed and
   * that the record does not say why; it does not fill the gap.
   */
  unexplained: boolean;
  /**
   * THE RUN'S OWN STOP RECORDS, for a `stopped` ending whose records hold a `stop.requested` (round 7 item 2): the ending
   * is explained by them, in the record's words, and is never "a gap in the record". Null for every other ending.
   */
  stop_request: StopView | null;
  /** What the runtime will accept next, for a code this console has a next step for. Empty for every other code. */
  next_actions: NextAction[];
}

/** The outcomes that call for an explanation. `already_placed_noop` is a success and is not one of them. */
const EXPLAINED_OUTCOMES: ReadonlySet<string> = new Set(['failed', 'stopped', 'refused', 'unknown']);

/** The name each capability carries on the controls. The two say the same word for the same command. */
const CAPABILITY_LABEL: Record<string, string> = {
  observe: 'observe',
  return_arm: 'return arm',
  start_cycle: 'start a cycle',
};

/**
 * WHICH COMMAND IS THE NEXT STEP AFTER WHICH REASON CODE, and why.
 *
 * The division that decides this table is whether the cube is in the gripper when the run stops. Where the
 * controller's own sentence says the arm keeps the cube, the next step is to look and then to bring the arm back with
 * the cube still held; `start_cycle` is never offered there, because a cycle starts with an empty gripper (the
 * contract says so on its own arguments) and offering it would be offering to drive the arm into a cube it is holding.
 * Where nothing was picked up, or the cube is already down, the run can be looked at and started again.
 *
 * A code that is not in this table gets NO offer. That is the same rule the panel already follows for a code it has no
 * sentence for: an operator is shown what was recorded, and no action is invented for them.
 */
const NEXT_AFTER: Record<string, readonly { capability_id: string; why: string }[]> = (() => {
  const look = { capability_id: 'observe', why: 'capture and perceive from where the arm is, commanding no motion' };
  const bringBack = { capability_id: 'return_arm', why: 'bring the arm back to the saved low scan pose with the cube still held' };
  // A CYCLE STARTS FROM THE LOW SCAN POSE, AND A RUN THAT STOPPED PART WAY DID NOT LEAVE THE ARM THERE. The controller
  // checks it and refuses with `start_pose_not_low_scan` (`run_single_cube_cycle.py`, the SEARCH entry: the lowest link
  // must be at least 0.10 m and the gripper in its normal-open region). Offering only "start a cycle" after a failure
  // that left the arm mid-air offers something that will refuse, so the arm is brought back FIRST. It is listed second,
  // after looking, because looking commands no motion at all.
  const bringBackEmpty = { capability_id: 'return_arm', why: 'put the arm back at the saved low scan pose, which is where a cycle has to start from' };
  const again = { capability_id: 'start_cycle', why: 'the gripper is empty, so a cycle can be started again once the arm is back at its start pose' };
  const holding = [look, bringBack];
  const empty = [look, bringBackEmpty, again];
  return {
    // Nothing was picked up: the gripper is empty and the cycle can be run again.
    start_pose_not_low_scan: empty,
    search_no_cube: empty,
    search_no_cube_after_sweep: empty,
    mission_binding_no_candidate: empty,
    mission_binding_candidate_not_unique: empty,
    // Nothing was picked up, and the MISSION comes back to this target once the capability is there: a look is the
    // only step offered, because a cycle started by hand here would run beside the mission's own resume. The same for a
    // cube the arm alone places whose fallback moves the base (the additive placement words).
    mission_capability_required: [look],
    mission_fallback_capability_required: [look],
    search_cube_not_in_pad_frame: empty,
    search_no_pad_frame: empty,
    search_placement_infeasible: empty,
    // Nothing was picked up: the least-motion plan's policy has no executor in this controller (placement_selector's guard).
    search_placement_policy_without_executor: empty,
    search_cube_inside_pregrasp_reach: empty,
    pregrasp_no_non_contact_pose: empty,
    align_cube_not_detected: empty,
    align_not_in_window_after_trims: empty,
    align_no_non_contact_descent: empty,
    align_bounded_corrections_exhausted: empty,
    straddling_not_reached: empty,
    // The gripper re-opened and the arm holds its pose, so the cube is on the floor and not in the jaws.
    grasp_missed: empty,
    // The cube is released and measured, or measured and not where it should be. The gripper is empty either way.
    placed_not_demonstrated: empty,
    base_not_returned: empty,
    // The cube is in the gripper and the controller stopped with it there.
    retention_not_demonstrated: holding,
    placement_infeasible_while_held: holding,
    mission_arm_only_insufficient_while_held: holding,
    mission_mode_changed_while_held: holding,
    synthetic_observation_excluded_from_live: holding,
    place_approach_no_pose: holding,
    release_not_admitted: holding,
    // The additive placement policies (placement_executor.py, reach_carry.py): the cube stays held, the arm brought back to
    // the carry pose, or where it stands when the way back held it (a step would have left what the records show the loaded
    // arm doing, or a step measured the arm not following), when a step of it was refused or it did not come back.
    reach_placement_infeasible_while_held: holding,
    reach_mode_changed_while_held: holding,
    reach_approach_no_pose: holding,
    reach_arm_only_insufficient_while_held: holding,
    reach_policies_exhausted_while_held: holding,
    reach_base_advanced_past_bound: holding,
    reach_further_advance_past_bound: holding,
    reach_retract_refused: holding,
    reach_retract_incomplete: holding,
    reach_way_back_held: holding,
    // The gripper left its holding band while the lowered cube was held still: whether the cube is still in the jaws is
    // not known, and the controller refuses every arm move whose gripper is out of its declared phase, so only a look.
    reach_release_hold_lost: [look],
    hold_observe_no_pad_frame: holding,
    base_advance_exceeds_bound: holding,
    base_advanced_past_bound: holding,
    base_return_no_pad_frame: holding,
    base_pulse_no_pad_frame_after: holding,
    base_measurement_implausible: holding,
    base_not_stationary_before: holding,
    base_not_confirmed_after: holding,
    base_arm_drifted: holding,
    base_pulse_refused_gripper: holding,
    base_pulse_refused_posture: holding,
    base_pulse_outside_its_bounds: holding,
    operator_abort_base_path: holding,
    operator_abort_before_base_pulse: holding,
    base_probe_no_pad_frame: [look],
    base_primitive_not_wired: [look],
    step_refused_mid_cycle: [look],
  };
})();

export function describeFailureCode(code: string | null): string | null {
  if (code === null) return null;
  return glossTemplate(code)?.gloss ?? null;
}

/** The gloss with the values the raise site carried stated in it, where the record carried every one of them. */
export function describeFailureCodeWith(code: string | null, args: TemplateArgs): string | null {
  if (code === null) return null;
  return fillTemplate(glossTemplate(code), args);
}

/** `code` for a headline: the gloss and the code when there is one, the bare code when there is not. */
export function failureCodeLabel(code: string | null): string | null {
  if (code === null) return null;
  return labelWithCode(describeFailureCode(code), code);
}

/** The same label, with the record's own values inside the sentence. */
export function failureCodeLabelWith(code: string | null, args: TemplateArgs): string | null {
  if (code === null) return null;
  return labelWithCode(describeFailureCodeWith(code, args), code);
}

/**
 * The next steps for this reason code, answered by the capability list the runtime sent with this snapshot.
 *
 * A capability the runtime does not report is left out entirely: this console has no way to know whether a runtime
 * that never mentioned `return_arm` would accept it, and an offer that turns into a refusal is worse than no offer.
 */
export function nextActionsFor(code: string | null, capabilities: readonly Capability[]): NextAction[] {
  if (code === null) return [];
  const steps = NEXT_AFTER[code];
  if (steps === undefined) return [];
  const out: NextAction[] = [];
  for (const step of steps) {
    const capability = capabilities.find((item) => item.id === step.capability_id);
    if (capability === undefined) continue;
    const enabled = capability.state === 'enabled';
    out.push({
      capability_id: step.capability_id,
      label: CAPABILITY_LABEL[step.capability_id] ?? step.capability_id,
      why: step.why,
      enabled,
      // The runtime's own words first and its code second. Where it sent neither, the state itself is the answer, in
      // the same words the controls use for it: a runtime that has not determined this has said that much.
      reason: enabled
        ? null
        : (capability.reason ??
          capability.reason_code ??
          (capability.state === 'unknown'
            ? 'the runtime has not determined whether this is available'
            : 'the runtime turned it off and sent no reason')),
    });
  }
  return out;
}

function unique(ids: readonly string[]): string[] {
  const out: string[] = [];
  for (const id of ids) if (id !== '' && !out.includes(id)) out.push(id);
  return out;
}

function fromNamedValues(values: readonly NamedValue[]): MeasuredAgainst[] {
  return values.map((item) => ({ name: item.name, value: item.value, unit: item.unit, threshold: null, source: 'failure' as const }));
}

/**
 * The failing predicates, as measurements against their thresholds. A predicate with neither a measured value nor a
 * threshold carries no number and is left out of this list; it is still in `failing_predicates`, where it reads as a
 * predicate that is false, which is the whole of what was recorded about it.
 */
function fromPredicates(predicates: readonly PredicateView[]): MeasuredAgainst[] {
  const out: MeasuredAgainst[] = [];
  for (const predicate of predicates) {
    if (predicate.measured === null && predicate.threshold === null) continue;
    out.push({
      name: predicate.predicate,
      value: predicate.measured,
      unit: predicate.unit,
      threshold: predicate.threshold,
      source: 'predicate',
    });
  }
  return out;
}

/**
 * What the screen shows when a run ended badly, or null when there is nothing to explain: no outcome yet, or a run that
 * ended as it should. Everything returned came off the record; nothing here is computed from anything else.
 */
export function explainFailure(state: ViewState, scope: CommandScope = UNSCOPED_COMMAND): FailureExplanation | null {
  const outcome = state.outcome;
  if (outcome.reported === null || !EXPLAINED_OUTCOMES.has(outcome.reported)) return null;
  // AN EARLIER RUN'S END IS NOT THIS RUN'S FAILURE. While the fold is inside a later run, the outcome of the one before
  // it explains nothing on the screen.
  if (!outcomeIsOfFoldedRun(state)) return null;

  // A SUCCESSFUL COMMAND WITH NO PLACEMENT IN IT IS NOT A FAILURE AND IS NOT DRAWN AS ONE.
  //
  // `unknown` is in EXPLAINED_OUTCOMES because a CYCLE that ends without the runtime being able to say what became of
  // the cube is a cycle nobody may read as a success. For an Observe the same word means something else entirely:
  // there was no placement for the runtime to name an outcome after, so it named none, and the four placement
  // predicates are null because there was nothing to evaluate. A real non-motion Observe on hardware ran exactly that
  // way, and this console drew a large UNKNOWN FAILURE over a command that did what it was asked to do.
  //
  // This is only ever a narrowing, and it narrows on the COMMAND TYPE rather than on the outcome word. A cycle reaches
  // every branch below exactly as before, its predicates still decide its verdict, exit status 0 is still never read
  // as placement success, and an Observe the controller really did end badly is still explained: see
  // `nonPlacementCommandSucceeded`, which refuses a bad outcome, a reason code and a non-zero exit.
  if (!scope.requires_placement && nonPlacementCommandSucceeded(state)) return null;

  const failure = outcome.failure;
  // A false `already_placed` is the passing answer (`PASSES_WHEN_FALSE`), never a failing predicate or its frame.
  const failing = state.predicates.filter(predicateFailed);
  const predicateIds: string[] = [];
  for (const predicate of failing) for (const piece of predicate.evidence) for (const id of piece.observation_ids) predicateIds.push(id);

  // THE STOP'S OWN NAMES, AND ONLY THOSE, as what the controller named: a frame a false predicate read is a different
  // record and is never merged into them (the straddle frame of m6ed2dd9 was listed as named by a stop that named none).
  const named = unique(failure?.observation_ids ?? []);

  const code = outcome.reason_code;
  // A STOP SOMEBODY ASKED FOR IS ITS OWN EXPLANATION (round 7 item 2): the fold's stop records are this run's, because
  // `run.started` clears them and `outcomeIsOfFoldedRun` holds above.
  const stopRequest = outcome.reported === 'stopped' && state.stop.requested !== null ? state.stop : null;
  // The values the raise site itself carried, and only those: a predicate's numbers are a different record and are
  // shown in their own rows rather than substituted into the controller's sentence.
  const args = argsFromNamedValues(failure?.measurements ?? []);
  return {
    outcome: outcome.reported,
    run_id: outcome.run_id,
    phase: outcome.phase,
    reason_code: code,
    gloss: describeFailureCode(code),
    sentence: describeFailureCodeWith(code, args),
    recognised: code !== null && glossTemplate(code) !== undefined,
    detail: failure?.detail ?? null,
    measurements: [...fromNamedValues(failure?.measurements ?? []), ...fromPredicates(failing)],
    observation_ids: named,
    predicate_observation_ids: unique(predicateIds).filter((id) => !named.includes(id)),
    failing_predicates: failing,
    stop_request: stopRequest,
    unexplained: code === null && stopRequest === null,
    next_actions: nextActionsFor(code, state.capabilities),
  };
}

/**
 * THE STOP RECORDS OF A RUN STOPPED ON REQUEST, one line each, in the record's own words and times (round 7 item 2). A
 * record the run does not hold is said to be missing; nothing is inferred from its absence.
 */
export function stopRecordLines(stop: StopView): string[] {
  const requested = stop.requested;
  if (requested === null) return [];
  const day = utcDayOf(requested.t_wall_utc);
  const at = (text: string | null | undefined, seq: number): string => {
    const stamp = utcStamp(text, day);
    return stamp === null ? ` (record seq ${String(seq)}, no wall time on it)` : ` at ${stamp.text} (record seq ${String(seq)})`;
  };
  const lines = [`Stop asked for by ${requested.origin}${at(requested.t_wall_utc, requested.seq)}.`];
  const ack = stop.acknowledged;
  lines.push(ack === null ? 'No acknowledgement of the stop is on the record.' : `Acknowledged by ${ack.acknowledged_by}${at(ack.t_wall_utc, ack.seq)}.`);
  const out = stop.outcome;
  if (out === null) {
    lines.push('No stop outcome is on the record.');
  } else {
    const ceased = out.motion_ceased === null ? 'not stated' : out.motion_ceased ? 'yes' : 'no';
    const code = out.reason_code == null ? '' : ` (${out.reason_code})`;
    lines.push(`The stop's outcome: ${out.outcome}${code}${at(out.t_wall_utc, out.seq)}; motion ceased: ${ceased}.`);
    if (out.evidence !== '') lines.push(`Its evidence, as recorded: ${out.evidence}`);
  }
  return lines;
}
