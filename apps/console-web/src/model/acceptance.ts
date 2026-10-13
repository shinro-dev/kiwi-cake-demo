// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// `placed_new` says a cube was placed and the base came back. Whether the placement cleared the pad edge by the margin
// ACCEPTANCE asks for is a separate measurement, and the runtime reports it as its own predicate rather than folding it
// into the word (run_single_cube_cycle.py:1190, _PLACEMENT_PREDICATES). So a run can be a real placement and still not
// be an acceptance pass, which is exactly what happened on 2026-09-22: the cube was placed, the base returned, and the
// footprint clearance fell 2.39 mm short.
//
// The screen used to describe that only in the negative, as four checks that "did not all measure true". Read against a
// run that did its job, that sounds like doubt about what happened. This builds the sentence that says what the run DID
// first, then the one thing it missed and what that thing measured, taken from the record that measured it. A tab that
// joined after the check was evaluated holds no such record and says so instead of printing a number it does not have.

import type { OutcomeView, PredicateView } from './types';

/**
 * The predicate records of ONE finished run, read from that run's own RETAINED evidence rather than from whatever this
 * tab happened to watch. A tab that opened after a run finished folded none of its records, so without this it can only
 * say the measurement is not in its fold, while the measurement sits in the retained run the whole time.
 *
 * `run_id` is what keeps it honest: a detail is used ONLY for the run it was read for, so one run's measurement can
 * never be drawn beside another run's outcome. `unavailable` is a real answer and is kept: an older run that never
 * carried these records must stay unavailable rather than borrow a value from anywhere.
 */
export interface ResultDetail {
  run_id: string;
  predicates: PredicateView[];
  state: 'retained' | 'unavailable';
}

/**
 * Which predicate records describe the result on the screen: the ones this tab folded live when it has them, and
 * otherwise the ones read back for THAT run. The live fold wins because it is the same records by a shorter path.
 */
export function resultPredicates(live: PredicateView[], runId: string | null, detail: ResultDetail | null): PredicateView[] {
  if (live.some((item) => item.predicate === 'clearance_target_met')) return live;
  if (runId === null || detail === null || detail.run_id !== runId) return live;
  return detail.predicates;
}

/** The four end checks, in the order an operator reads them, with the words the screen uses for each. */
const END_CHECKS: [keyof NonNullable<OutcomeView['predicates']>, string][] = [
  ['object_secured', 'object secured'],
  ['placed', 'placed'],
  ['clearance_target_met', 'clearance target met'],
  ['base_returned', 'base returned'],
];

/**
 * WHAT THESE TWO NUMBERS ARE, because naming them wrongly was a real defect on this screen.
 *
 * The clearance_target_met record carries the CUBE-CENTRE to pad-edge margin against the REQUIRED CENTRE margin:
 * run_single_cube_cycle.py:1142 emits it with `measured=margin`, `threshold=PLACE_REQUIRED_CENTRE_MM` and the scalar
 * name `cube_centre_to_pad_edge_mm`. The FOOTPRINT clearance is a different quantity, computed at :1135 by subtracting
 * the on-pad bar, and reported as `footprint_margin_any_yaw_mm` against `clearance_target_mm` on the PLACED state
 * record. That pair is NOT on this predicate, so this screen must not print it and must never reconstruct it by
 * subtracting a remembered constant. Once the run's footprint allowance arrives as bound data, the simplified
 * presentation can show the footprint clearance against its own target; until then these are named for what they are.
 *
 * The shortfall is arithmetic on the record's own two numbers, never a constant: a threshold that moves moves it too.
 * When the numbers do not show a shortfall the sentence stops after the measurement rather than inventing one.
 */
function measuredSentence(predicate: PredicateView | undefined): string | null {
  if (predicate === undefined || predicate.measured === null || predicate.threshold === null) return null;
  const measured = Number(predicate.measured);
  const threshold = Number(predicate.threshold);
  if (!Number.isFinite(measured) || !Number.isFinite(threshold)) return null;
  const unit = predicate.unit === null ? '' : ` ${predicate.unit}`;
  const head = `Cube-centre edge margin: ${measured}${unit}; required: ${threshold}${unit}.`;
  const shortfall = threshold - measured;
  return shortfall > 0 ? `${head} Clearance target missed by ${Number(shortfall.toFixed(2))}${unit}.` : head;
}

/**
 * The clearance measurement the run's clearance_target_met record carries, worded, whether the target was met or
 * missed; null when the run retained no such record or its numbers cannot be read. Used where a run's figures are
 * shown for what they are (a rehearsal's own records), not where they qualify a placement.
 */
export function clearanceMeasurement(predicates: PredicateView[]): string | null {
  return measuredSentence(predicates.find((item) => item.predicate === 'clearance_target_met'));
}

/**
 * WHAT THE RUN IS ESTABLISHED TO HAVE DONE, from the predicates it retained.
 *
 * The outcome WORD and the end predicates are two different records and they do not always agree. A run whose
 * `run.finished` payload carries `unknown` can still have retained `placed` and `base_returned` measured true, which is
 * the case on this console right now for the preserved browser run: the screen printed the single word "unknown" over
 * a run that demonstrably placed the cube and brought the base back, because it read the outcome word and nothing else.
 *
 * This derives the headline from the MEASUREMENTS and keeps the recorded word beside it rather than in place of it.
 * Nothing is rewritten: `outcome.reported` is untouched, the raw record is untouched, and the word the runtime wrote is
 * still shown, in `recorded`, for the panel that states what the record says. It is decided entirely from predicate
 * values, so it is the same derivation for every run and names no run id.
 */
export interface EstablishedResult {
  /** What the measurements establish, or the recorded word when they establish nothing. */
  headline: string;
  /** True when the headline came from predicates rather than from the outcome word. */
  from_predicates: boolean;
  /** The runtime's own outcome word, kept whenever it is not what the headline says. Null when there is nothing to add. */
  recorded: string | null;
  /** The clearance sentence, when a clearance target was measured and missed. */
  qualifier: string | null;
}

function evaluated(predicates: PredicateView[], name: string): boolean | null {
  const record = predicates.find((item) => item.predicate === name);
  if (record === undefined) return null;
  return record.value;
}

/**
 * `released` is whether the run's own records show the block let go: RELEASED reached in its transitions, a stop
 * phase at or after RELEASED, or a stop state that records the gripper empty. `placed` false with `object_secured`
 * true says only that the block was secured and not credited as placed; it does not say the block is still held. A
 * run that released it and was refused at PLACED (the m915d3e9 v2 record, every v3 no-block-at-the-release-point
 * refusal) read "Held, not placed" over a drawer that said "Gripper empty", so the release decides between the two.
 */
export function establishedResult(result: OutcomeView, predicates: PredicateView[], released = false): EstablishedResult {
  const reported = result.reported;
  // The run's own end predicates, from whichever source resolved them: the live fold or the retained read-back.
  const placed = evaluated(predicates, 'placed') ?? result.predicates?.placed ?? null;
  const returned = evaluated(predicates, 'base_returned') ?? result.predicates?.base_returned ?? null;
  const secured = evaluated(predicates, 'object_secured') ?? result.predicates?.object_secured ?? null;
  const clearance = predicates.find((item) => item.predicate === 'clearance_target_met');
  const qualifier =
    clearance !== undefined && clearance.value === false ? measuredSentence(clearance) : null;

  let headline: string | null = null;
  if (placed === true && returned === true) headline = 'Placed and returned';
  else if (placed === true) headline = 'Placed';
  else if (placed === false && secured === true) headline = released ? 'Released, not placed' : 'Held, not placed';

  if (headline === null) {
    return { headline: reported ?? 'no outcome recorded', from_predicates: false, recorded: null, qualifier };
  }
  // The recorded word is kept whenever it is not simply a restatement of what the measurements say.
  const restates = reported === 'placed_new' && headline === 'Placed and returned';
  return { headline, from_predicates: true, recorded: restates ? null : reported, qualifier };
}

/**
 * The note for a placement the screen will not call confirmed, or null when there is nothing to qualify: a run that met
 * every check, or an outcome that is not a placement at all and carries its own reason instead.
 */
export function acceptanceNote(result: OutcomeView, predicates: PredicateView[]): string | null {
  if (result.reported !== 'placed_new' || result.confirmed !== null) return null;
  const measured = result.predicates;
  const missed = measured === null ? [] : END_CHECKS.filter(([key]) => measured[key] !== true);
  const closing = 'Full acceptance needs all four end checks, so this is reported and not confirmed.';
  const lead = 'Placed and returned.';

  const only = missed.length === 1 ? missed[0] : undefined;
  if (only !== undefined && only[0] === 'clearance_target_met') {
    const sentence = measuredSentence(predicates.find((item) => item.predicate === 'clearance_target_met'));
    const detail = sentence ?? 'The clearance target was not met; the record that measured it is not in this fold.';
    return `${lead} ${detail} ${closing}`;
  }
  if (missed.length === 0) return `${lead} The record does not say which end check did not measure true. ${closing}`;
  return `${lead} These end checks did not measure true: ${missed.map(([, label]) => label).join(', ')}. ${closing}`;
}
