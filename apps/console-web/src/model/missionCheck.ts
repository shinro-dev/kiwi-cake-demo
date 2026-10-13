// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THE ROBOT DID, AND WHAT THE MISSION CHECK REFUSED (docs/autonomous-demo/console-redesign-2026-09-27.md, 3.2.1;
// operator ruling 2026-09-28).
//
// A mission run can do its physical job and still end FAILED: the mission check (`autonomy/manipulation/
// target_binding.py`, run by the controller at SEARCH and at PLACED) decides whether this target's block is the one that
// was placed, and it can refuse after the robot released the block on the pad and the base came back. The live case was
// mission-f9ac04941f32: grasped, released with 40.6 mm of predicted margin, base measured back, then refused
// mission_earlier_target_disturbed. The card said only "Mission failed" and the controller's sentence.
//
// THE PAD POLICY OF 2026-09-29 (docs/autonomous-demo/decision-log.md, ruling (ii)): PLACED credits any block on the pad
// within the release tolerance of this run's own release point, whatever was there before, and the other blocks on the
// pad decide nothing. Its one placement refusal is mission_placed_no_cube_at_release_point, which carries on_pad_count,
// release_distance_mm and release_tolerance_mm. The three codes of the retired claim rules (RETIRED_MISSION_CHECK_CODES)
// are no longer written; their sentences stay for the old records History still shows, mission-f9ac04941f32 among them.
//
// This module says both halves in plain words, each read from the record and from nothing else:
//
//   WHAT THE ROBOT DID: from the run's own records (the object_secured and base_returned predicates of run.finished, a
//   state.transition to RELEASED, which only a release admitted on a PREDICTED margin reaches and which is said as that
//   prediction, never as where the block rests, the release_margin predicate or the attempt's predicted margin); with the
//   run's events not read, only what the mission record's attempt states (its phase, its stop state, its margin measurement). A binding refusal is raised at SEARCH
//   before PREGRASP, and every one of its sentences in target_binding.py says nothing was picked up; that is what it says.
//   WHAT THE CHECK REFUSED: one sentence per reason code, with the counts the refusal carried as named measurements.
//   A placement refusal always ends "The record says placed: false.", because that is what every one of them records.
//
// THE CHECK THAT COULD NOT LOOK. Before target_binding decides anything the controller lists the blocks of the view it
// checks (run_single_cube_cycle.py, _mission_candidates) and reads each one's position (_mission_seen_cube). When there
// is no view, the frame cannot be read, the listing fails, is refused or stops with proposals left, or a listed block
// carries no readable position, it refuses with its own code and target_binding counts nothing. The same codes come from
// SEARCH (_mission_search: nothing was picked up) and from PLACED (_mission_placed, then policy_return.py: after the
// release and the base return), so which of the two it was is read from the record: a transition to RELEASED or PLACED
// among the run's events, a stop phase at or after RELEASED, or the release's predicted margin (which only
// policy_return.py's raise hands these codes) is PLACED; a stop phase of the locate phase is SEARCH; with
// neither, the account says only what the record states. At PLACED the attribution then carries placeholders
// (target_binding.Placement(False, None, None, 0, None, None, code) since the pad policy, and Placement(False, None, None,
// 0, False, code) in an older record): its on_pad_count of 0 (unclaimed_placed_count of 0 in an older record) was not
// counted, and the account says so rather than echo it as a count.
//
// The controller's own sentence stays on the card beside this (failure.ts rule 1): this is said next to it, never in its
// place. A code outside the table is not explained at all.

import type { ContractEvent } from '../contracts/envelope';
import type { MissionAttemptRecord } from './missionRecord';
import { phaseOfState } from './phase';

type Values = Readonly<Record<string, string | number | boolean | null>>;

interface CheckEntry {
  /**
   * `binding`: refused at SEARCH, nothing picked up. `placement`: refused at PLACED, after the release. `listing`: the
   * check could not list or read the view it had to check, raised at SEARCH or at PLACED; which one is read from the record.
   */
  stage: 'binding' | 'placement' | 'listing';
  /** True for a refusal in which the check did not look at the blocks at all: said as "could not", never as "refused". */
  couldNotLook?: true;
  /** True for a code nothing writes any more, kept only so an old record History shows is still explained. */
  retired?: true;
  /** The reason, after "The mission check ...: ", without the final full stop. `view` names the view it had to check. */
  reason: (values: Values, view: string) => string;
}

function count(values: Values, name: string): number | null {
  const value = values[name];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function mm(value: number): string {
  return `${value.toFixed(1)} mm`;
}

/** " (blocks seen: 2, already on the pad: 1)" from the counts the record carries, or nothing when it carries none. */
function seen(values: Values): string {
  const parts: string[] = [];
  const cubes = count(values, 'cubes_seen');
  const placed = count(values, 'placed_cubes');
  if (cubes !== null) parts.push(`blocks seen: ${String(cubes)}`);
  if (placed !== null) parts.push(`already on the pad: ${String(placed)}`);
  return parts.length === 0 ? '' : ` (${parts.join(', ')})`;
}

/** "; the record counts blocks seen: 1, ..." for the named counts the record carries, in this order, or nothing. */
function counted(values: Values, labels: readonly (readonly [string, string])[]): string {
  const parts: string[] = [];
  for (const [name, label] of labels) {
    const n = count(values, name);
    if (n !== null) parts.push(`${label}: ${String(n)}`);
  }
  return parts.length === 0 ? '' : `; the record counts ${parts.join(', ')}`;
}

const SEEN = [['cubes_seen', 'blocks seen'], ['placed_cubes', 'already on the pad']] as const;

function radius(values: Values): string {
  const r = count(values, 'r_match_mm');
  return r === null ? 'the match radius' : mm(r);
}

/**
 * THE LISTING BUDGET (run_single_cube_cycle.py, _mission_candidates): cube_candidates.MAX_PASSES verified blocks per view,
 * and a pass spent on a block already on the pad is free up to target_binding.LISTING_ON_PAD_EXEMPT_PASSES of them. A view
 * with more is refused as mission_candidates_incomplete: a named limit of the single-block version. A test holds both
 * numbers to their sources.
 */
export const LISTING_PASSES = 3;
export const LISTING_ON_PAD_EXEMPT_PASSES = 9;

/** The release agreement's refusal: what it measured, from the values the raise carries (policy_return.py). */
function releaseAgreement(values: Values): string {
  const tolerance = count(values, 'release_tolerance_mm');
  const onPad = count(values, 'on_pad_count');
  const nearest = count(values, 'release_distance_mm');
  let saw = '';
  if (onPad === 0) saw = '; it saw no block on the pad at all';
  else if (onPad !== null) {
    const where = nearest === null ? '' : `, ${onPad === 1 ? '' : 'the nearest '}${mm(nearest)} from the release point`;
    saw = `; it saw ${String(onPad)} block${onPad === 1 ? '' : 's'} on the pad${where}, and a block elsewhere on the pad does not count for this run`;
  }
  return (
    `after the release it found no block on the pad within ${tolerance === null ? 'the release tolerance' : mm(tolerance)} of the point where the robot released its block, so it credited no block to this run` +
    `${saw}. Check whether the block left the pad`
  );
}

function unclaimed(values: Values): string {
  const n = count(values, 'unclaimed_placed_count');
  if (n === null) return '';
  return ` It counted ${String(n)} placed block${n === 1 ? '' : 's'} that nothing else claims.`;
}

/**
 * THE REFUSAL AND ATTRIBUTION CODES of `target_binding.py` (its "refusal and attribution reason codes" block), and the
 * controller's own refusals to list or read the view the check looks at (`run_single_cube_cycle.py`, _mission_candidates
 * and _mission_placed), each with the sentence the card says. A test reads those sources and holds this table to them, so
 * a code added there fails here first.
 */
export const MISSION_CHECK_CODES: Readonly<Record<string, CheckEntry>> = {
  mission_targets_share_a_mode: {
    stage: 'binding',
    reason: () => 'two targets of this mission declare the same placement mode, so no measurement tells their blocks apart',
  },
  mission_binding_no_candidate: {
    stage: 'binding',
    // `target_binding.py` raises this when THIS target's candidates are none: for a declared target only the candidates of
    // its mode count, and a block an earlier target bound is nobody else's. So a block off the pad can be in view.
    // THE SWEEP RESIDUAL (the pad policy, named and pinned there): SEARCH sweeps only when its first view shows no block,
    // so a block on the pad in that view ends the search and a block off the pad outside it is not looked for.
    reason: (values) =>
      'it found no block this target can take, and it needs exactly one. A block this target can take has a measured position in the pad frame, is not already on the pad, was not bound by an earlier target of this mission and, when the target declares a placement mode, measures that mode' +
      counted(values, [...SEEN, ['candidates', 'candidate blocks'], ['bound_earlier', 'bound by earlier targets'], ['matching_candidates', 'matching this target']]) +
      ". When SEARCH sweeps, a block on the pad in its first view (an earlier block, or the pad's printed logo read as one) ends the sweep, so a block off the pad outside that view may not have been looked for",
  },
  mission_binding_candidate_not_unique: {
    stage: 'binding',
    reason: (values) => {
      const n = count(values, 'matching_candidates');
      return `it saw ${n === null ? 'more than one block' : `${String(n)} blocks`} that could be this target, and it takes one only when exactly one can${seen(values)}`;
    },
  },
  mission_binding_candidate_not_placeable: {
    stage: 'binding',
    // An auto target (the pad policy) weighs every block off the pad and refuses when none has a bounded placement; the
    // refusal then carries candidates above one and feasible_candidates 0.
    reason: (values) => {
      const n = count(values, 'candidates');
      if (n !== null && n > 1) {
        return `none of the ${String(n)} candidate blocks (each with a measured position in the pad frame, not already on the pad and not bound by an earlier target) has a bounded placement: neither the arm alone nor the bounded base advance reaches the pad from any of them${counted(values, SEEN)}`;
      }
      return (
        'the one candidate block (with a measured position in the pad frame, not already on the pad and not bound by an earlier target) has no bounded placement: neither the arm alone nor the bounded base advance reaches the pad from it' +
        counted(values, SEEN)
      );
    },
  },
  mission_target_not_found: {
    stage: 'binding',
    // `select_target` counts only candidates: a block on the pad, or one an earlier target claims, can lie within the radius.
    reason: (values) =>
      `no candidate block (one with a measured position in the pad frame, not already on the pad and not claimed by an earlier target of this mission) lay within ${radius(values)} of where this target was first bound` +
      counted(values, [...SEEN, ['candidates', 'candidate blocks']]),
  },
  mission_target_not_unique: {
    stage: 'binding',
    reason: (values) => {
      const n = count(values, 'within_r_match');
      return `${n === null ? 'more than one candidate block' : `${String(n)} candidate blocks`} (blocks with a measured position in the pad frame, not already on the pad and not claimed by an earlier target of this mission) lay within ${radius(values)} of where this target was first bound, and it needs exactly one`;
    },
  },
  mission_target_mode_mismatch: {
    stage: 'binding',
    reason: () => "the block where this target was bound no longer measures the target's placement mode",
  },
  // THE RELEASE AGREEMENT's one refusal (the pad policy of 2026-09-29). Its PLACED is "a block is on the pad at the release
  // point", never "this run's block is": the accepted residual (target_binding.py) is a false PLACED when this run's block
  // is missing and another block, or the printed logo, lies within the tolerance. So the refusal never says whose block.
  mission_placed_no_cube_at_release_point: {
    stage: 'placement',
    reason: releaseAgreement,
  },
  // THE RETIRED CLAIM RULES (before 2026-09-29). Nothing writes these codes any more; old records carry them.
  mission_earlier_target_disturbed: {
    stage: 'placement',
    retired: true,
    reason: (values) =>
      "a position it had to find undisturbed was not matched by a placed block when it looked (a block placed earlier in this mission, or one this run's SEARCH had already counted as placed on the pad, was not found where it was recorded)." +
      unclaimed(values).replace(/\.$/, ''),
  },
  mission_placed_no_unclaimed_cube: {
    stage: 'placement',
    retired: true,
    reason: () =>
      "after the release it found no placed block on the pad that was not already claimed, by a block placed earlier in this mission or by one this run's SEARCH had already counted as placed",
  },
  mission_placed_more_than_one_unclaimed_cube: {
    stage: 'placement',
    retired: true,
    reason: (values) => {
      const n = count(values, 'unclaimed_placed_count');
      return `after the release it found ${n === null ? 'more than one placed block' : `${String(n)} placed blocks`} that nothing claims, and it credits this target only when exactly one is`;
    },
  },
  // THE CONTROLLER'S OWN LISTING REFUSALS (run_single_cube_cycle.py, _mission_placed and _mission_candidates). Nothing was
  // counted for any of them, and no sentence below states a count.
  mission_placed_no_exit_view: {
    stage: 'placement',
    couldNotLook: true,
    reason: () => 'no view was taken after the release for it to look at, so it did not look at where the block came to rest and counted no blocks',
  },
  mission_candidates_frame_unreadable: {
    stage: 'listing',
    couldNotLook: true,
    reason: (_values, view) => `it could not read the frame of ${view}, so it listed no blocks and counted nothing`,
  },
  mission_candidates_failed: {
    stage: 'listing',
    couldNotLook: true,
    reason: (_values, view) => `listing the blocks of ${view} stopped with an error, so it counted nothing`,
  },
  mission_candidates_refused: {
    stage: 'listing',
    couldNotLook: true,
    reason: (_values, view) => `the listing of the blocks of ${view} was refused, so it counted nothing`,
  },
  mission_candidates_incomplete: {
    stage: 'listing',
    couldNotLook: true,
    reason: (_values, view) =>
      `the listing of the blocks of ${view} stopped with blocks it had not checked, and it decides nothing on a partial list, so it counted nothing. ` +
      `It checks at most ${String(LISTING_PASSES)} blocks in one view, and up to ${String(LISTING_ON_PAD_EXEMPT_PASSES)} more when they are on the pad; with more blocks than that in view, clear the pad or the area and retry`,
  },
  mission_candidate_unreadable: {
    stage: 'listing',
    couldNotLook: true,
    reason: (_values, view) => `a block it listed in ${view} carries no position it can read, so it decided nothing from that view and counted nothing`,
  },
};

/** The codes kept only for old records: nothing writes them since the pad policy of 2026-09-29. */
export const RETIRED_MISSION_CHECK_CODES: readonly string[] = Object.keys(MISSION_CHECK_CODES).filter((code) => MISSION_CHECK_CODES[code]?.retired === true);

/** The view a listing refusal names, by where it was raised. */
const VIEW = { placement: 'the view taken after the release', binding: 'the SEARCH view', unknown: 'the view it had to check' } as const;

/** The stop states at or after RELEASED: a listing refusal with one of these is the one raised at PLACED. */
export const AFTER_RELEASE: ReadonlySet<string> = new Set(['RELEASED', 'PLACED', 'BASE_RETURN', 'BASE_RETURNED']);

/** Where a listing refusal was raised, from the record: PLACED, SEARCH, or not stated. */
function listingStage(attempt: MissionAttemptRecord, events: readonly ContractEvent[], values: Values): 'placement' | 'binding' | 'unknown' {
  const released = events.some((event) => event.kind === 'state.transition' && AFTER_RELEASE.has(String(payloadOf(event)['to_state'])));
  // policy_return.py's raise is the one site that hands these codes the release's predicted margin.
  const fromReturn = count(values, 'predicted_worst_case_centre_margin_mm') !== null;
  if (released || fromReturn || (attempt.phase !== null && AFTER_RELEASE.has(attempt.phase))) return 'placement';
  if (attempt.phase !== null && phaseOfState(attempt.phase) === 'locate') return 'binding';
  return 'unknown';
}

/** The placeholder count a listing refusal at PLACED carries, said as what it is when the record carries it. */
function notCounted(values: Values): string {
  const onPad = count(values, 'on_pad_count');
  if (onPad !== null) return ` The ${String(onPad)} the record carries for blocks on the pad is not a count: nothing was counted.`;
  const n = count(values, 'unclaimed_placed_count'); // an older record's placeholder (before the pad policy)
  return n === null ? '' : ` The ${String(n)} the record carries for unclaimed placed blocks is not a count: nothing was counted.`;
}

export interface MissionCheck {
  code: string;
  /** `unknown`: a listing refusal whose record says neither SEARCH nor PLACED. */
  stage: 'binding' | 'placement' | 'unknown';
  /** What the robot did, as the record states it. */
  did: string;
  /** What the mission check refused, in plain words, ending with what the record says of PLACED for a placement. */
  refused: string;
}

function payloadOf(event: ContractEvent): Record<string, unknown> {
  const payload = (event as { payload?: unknown }).payload;
  return typeof payload === 'object' && payload !== null ? (payload as Record<string, unknown>) : {};
}

function valuesOf(measurements: readonly { name: string; value: unknown }[]): Values {
  const out: Record<string, string | number | boolean | null> = {};
  for (const item of measurements) {
    const value = item.value;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || value === null) out[item.name] = value;
  }
  return out;
}

function newestPredicate(events: readonly ContractEvent[], name: string): Record<string, unknown> | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event === undefined || event.kind !== 'predicate.evaluated') continue;
    const payload = payloadOf(event);
    if (payload['predicate'] === name) return payload;
  }
  return null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function joinClauses(parts: string[]): string {
  if (parts.length <= 1) return parts.join('');
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1] ?? ''}`;
}

/** Said after a release margin: the admission is a prediction, and the placement is what the mission check measured. */
const PREDICTION = ' That margin is a prediction made before the release; where the block came to rest is what the mission check measured after it.';

const BASE_BACK = 'the base was measured back where it started';
const BASE_NOT_DRIVEN = 'the base was not driven';
/** The states only a driven base reaches. */
const BASE_DRIVEN: ReadonlySet<string> = new Set(['BASE_ADVANCE', 'BASE_MEASURED']);

/** Whether the run's base_returned predicate names the scalar base_never_advanced (the controller's own statement). */
function neverAdvancedStated(events: readonly ContractEvent[]): boolean {
  const evidence = newestPredicate(events, 'base_returned')?.['evidence'];
  return Array.isArray(evidence) && evidence.some((item) => typeof item === 'object' && item !== null && (item as { scalar_name?: unknown }).scalar_name === 'base_never_advanced');
}

/** What the robot did before a placement refusal, from the run's own records, or from the attempt when they are not read. */
function didBeforePlacement(attempt: MissionAttemptRecord, events: readonly ContractEvent[], values: Values): string {
  const finished = [...events].reverse().find((event) => event.kind === 'run.finished');
  const predicates = finished === undefined ? {} : ((payloadOf(finished)['predicates'] ?? {}) as Record<string, unknown>);
  const released = events.some((event) => event.kind === 'state.transition' && payloadOf(event)['to_state'] === 'RELEASED');
  // A TRANSITION TO RELEASED FOLLOWS ONLY AN ADMITTED RELEASE (`policy_placement.py`: release_not_admitted is raised, the
  // cube held, unless the PREDICTED worst-case margin to the pad edge meets the required one). That admission is a
  // prediction made before the gripper opens; where the block came to rest is judged afterwards, from the exit view, by
  // the mission check. So the card says the release point was predicted over the pad, never that the block is on it (for
  // mission_placed_no_cube_at_release_point, and the retired mission_placed_no_unclaimed_cube, the check found none).
  // The margin is the release_margin predicate's when the page holds it, else the attempt's measurement.
  const margin = newestPredicate(events, 'release_margin');
  const admitted = released;
  const clauses: string[] = [];
  if (predicates['object_secured'] === true) clauses.push('grasped the block');
  if (released) clauses.push('released it at a point the release admission predicted was over the pad');
  // A BASE THAT WAS NEVER DRIVEN: the controller sets base_returned true for it once PLACED is reached
  // (run_single_cube_cycle.py, _mission_predicates) and states it with measured 0.0 and the scalar base_never_advanced
  // (cycle_returned). No return was measured then, so the account says the base was not driven: when the run's records
  // carry no transition to BASE_ADVANCE or BASE_MEASURED, or when the predicate names base_never_advanced.
  const driven = events.some((event) => event.kind === 'state.transition' && BASE_DRIVEN.has(String(payloadOf(event)['to_state'])));
  const neverAdvanced = neverAdvancedStated(events);
  if (predicates['base_returned'] === true) clauses.push(driven && !neverAdvanced ? BASE_BACK : BASE_NOT_DRIVEN);
  const measured = finite(margin?.['measured']);
  const required = finite(margin?.['threshold']);
  const predicted = count(values, 'predicted_worst_case_centre_margin_mm');
  if (clauses.length > 0) {
    const lead = clauses[0] === BASE_BACK || clauses[0] === BASE_NOT_DRIVEN ? `The robot ran and ${joinClauses(clauses)}.` : `The robot ${joinClauses(clauses)}.`;
    const worst = measured ?? predicted;
    const release =
      admitted && worst !== null
        ? ` The release was admitted with a predicted worst-case margin of ${mm(worst)} to the pad edge${measured !== null && required !== null ? ` (${mm(required)} required)` : ''}.${PREDICTION}`
        : '';
    return lead + release;
  }
  // THE RUN'S EVENTS ARE NOT READ: what the mission record's attempt states, and nothing more.
  const where = attempt.phase === null ? 'The run ended' : `The run ended at ${attempt.phase}`;
  const holding = attempt.stop_state?.holding;
  const gripper = holding === false ? ' with the gripper empty' : holding === true ? ' holding the block' : '';
  const release = predicted === null ? '' : ` The release was admitted with a predicted worst-case margin of ${mm(predicted)} to the pad edge.${PREDICTION}`;
  return `${where}${gripper}.${release}`;
}

/**
 * The plain account of a mission check's refusal for the card's run: the attempt the mission recorded for it and that
 * run's own events (empty while not read). Null for no attempt or a code outside the table.
 */
export function missionCheck(attempt: MissionAttemptRecord | null, events: readonly ContractEvent[]): MissionCheck | null {
  if (attempt === null || attempt.reason_code === null) return null;
  const code = attempt.reason_code;
  const entry = MISSION_CHECK_CODES[code];
  if (entry === undefined) return null;
  const finished = [...events].reverse().find((event) => event.kind === 'run.finished');
  const failure = finished === undefined ? null : (payloadOf(finished)['failure'] as { measurements?: { name: string; value: unknown }[] } | null | undefined);
  const values = attempt.measurements.length > 0 ? valuesOf(attempt.measurements) : valuesOf(failure?.measurements ?? []);
  if (entry.couldNotLook === true) {
    const stage = entry.stage === 'listing' ? listingStage(attempt, events, values) : 'placement';
    const reason = entry.reason(values, VIEW[stage]).replace(/\.$/, '');
    if (stage === 'binding') {
      return { code, stage, did: 'Nothing was picked up.', refused: `The mission check could not bind this target at SEARCH: ${reason}.` };
    }
    if (stage === 'placement') {
      return {
        code,
        stage,
        did: didBeforePlacement(attempt, events, values),
        refused: `The mission check could not check the placement: ${reason}.${notCounted(values)} The record says placed: false.`,
      };
    }
    return { code, stage, did: didBeforePlacement(attempt, events, values), refused: `The mission check could not decide: ${reason}.` };
  }
  const reason = entry.reason(values, VIEW.unknown).replace(/\.$/, '');
  if (entry.stage === 'binding') {
    return { code, stage: 'binding', did: 'Nothing was picked up.', refused: `The mission check refused this target at SEARCH: ${reason}.` };
  }
  return {
    code,
    stage: 'placement',
    did: didBeforePlacement(attempt, events, values),
    refused: `The mission check did not accept the placement: ${reason}. The record says placed: false.`,
  };
}
