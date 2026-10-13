// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// What a recorded verdict says, and how old the evidence behind it was when it was taken.
//
// Two facts about a predicate are shown side by side on the screen and they are NOT the same fact. The verdict is
// historical: it was taken at a sequence number, on evidence that existed then, and it never changes afterwards. The
// freshness belongs to the EVIDENCE the verdict consumed, and it goes on changing after the verdict was written. A
// console that shows one and calls it the other tells an operator that a stale reading is a current one.
//
// Nothing here subtracts two clocks that were not read on the same machine. Every age this file produces comes with
// the session and the clock domain it was measured in, or it comes back null with the reason it could not be taken.

import type { SourceStamp } from '@kiwi/demo-contracts';
import type { PredicateState, PredicateView } from './types';

/**
 * Which of the four states a recorded verdict is in.
 *
 * A record that carries `state` is taken at its word. A record written before the field existed carries none, and for
 * those the older reading holds and is stated here once rather than in every panel that asks: a value is an
 * evaluation, and a null value is a predicate that was not evaluated. The one thing this never does is turn a missing
 * value into `false`.
 */
export function predicateState(predicate: Pick<PredicateView, 'state' | 'value'>): Exclude<PredicateState, null> {
  if (predicate.state !== null) return predicate.state;
  return predicate.value === null ? 'not_evaluated' : 'evaluated';
}

/** True when the record itself said which state it is in, rather than the reading being derived from the value. */
export function predicateStateWasRecorded(predicate: Pick<PredicateView, 'state'>): boolean {
  return predicate.state !== null;
}

/** Why a decision age could not be taken. Each one is a different fact and the screen says which it has. */
export type DecisionAgeRefusal =
  | 'the verdict names no source sample'
  | 'no sample of that source has arrived since'
  | 'the newest sample is from another session of that source'
  | 'the newest sample is read in another clock domain'
  | 'neither sample carries a source time';

/**
 * HOW OLD THE EVIDENCE WAS WHEN THE VERDICT WAS TAKEN, in nanoseconds of the source's own clock.
 *
 * It is measured between the sample the verdict consumed and the newest sample of THE SAME SESSION of the same source,
 * read in the same clock domain. That is a subtraction inside one clock on one machine, which is the only kind this
 * console performs. It is deliberately not the difference between the verdict's own `t_mono_ns` and the sample's:
 * those are the laptop's clock and the host's, and nothing has measured a relation between them.
 *
 * A session change means the source restarted, and two monotonic readings across a restart are not comparable either,
 * so that is a refusal with its own words rather than a number.
 */
export function decisionAgeNs(
  predicate: Pick<PredicateView, 'consumed_source_stamp'>,
  newest: SourceStamp | null,
): { age_ns: number | null; refusal: DecisionAgeRefusal | null } {
  const consumed = predicate.consumed_source_stamp;
  if (consumed === null) return { age_ns: null, refusal: 'the verdict names no source sample' };
  if (newest === null) return { age_ns: null, refusal: 'no sample of that source has arrived since' };
  if (newest.session !== consumed.session) {
    return { age_ns: null, refusal: 'the newest sample is from another session of that source' };
  }
  if (newest.clock_domain !== consumed.clock_domain) {
    return { age_ns: null, refusal: 'the newest sample is read in another clock domain' };
  }
  if (consumed.sample_mono_ns === null || newest.sample_mono_ns === null) {
    return { age_ns: null, refusal: 'neither sample carries a source time' };
  }
  return { age_ns: Math.max(0, newest.sample_mono_ns - consumed.sample_mono_ns), refusal: null };
}

/**
 * PREDICATES WHOSE FALSE IS THE PASSING ANSWER. `already_placed` is false for every block not yet on the pad
 * (`policy_search.py`: start_inside >= PLACED_MIN_EDGE_MARGIN_MM), and that false is what lets the run go on. It is
 * never read as a failing predicate, and its frames are never taken as the frame a failure decided on.
 */
export const PASSES_WHEN_FALSE: ReadonlySet<string> = new Set(['already_placed']);

/** True when this predicate's recorded value is a failing answer: false, and not one whose false is the pass. */
export function predicateFailed(predicate: Pick<PredicateView, 'predicate' | 'value'>): boolean {
  return predicate.value === false && !PASSES_WHEN_FALSE.has(predicate.predicate);
}
