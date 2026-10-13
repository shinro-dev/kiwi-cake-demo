// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// What the pad map is allowed to draw, decided from the record alone.
//
// Three rules run through this file. The THRESHOLD of every rule drawn on the map is read off the predicate that was
// decided with it, never held as a number in the web application: the controller's required centre margin is a
// computed quantity (a cube half-diagonal plus a clearance target), it has already changed once, and a console that
// kept its own copy would go on drawing last month's rule over this month's run. A mark is drawn at the observation
// the verdict CONSUMED and at no other, because an estimate moved to a later sighting is an estimate nobody computed.
// And where the run resolved no pad frame at all, nothing is placed on the map: the absence is drawn as an absence.

import { centreAdmissibleHalfExtentsMm } from '../geometry/pad';
import type { PredicateView, ViewState } from './types';

/**
 * One position this console established on the pad. Structurally the store's `SceneMark`, restated here so that this
 * module depends on nothing but the record it reads.
 */
export interface PadMark {
  observation_id: string;
  state: string | null;
  point_mm: [number, number];
  seq: number;
}

/**
 * The predicates whose threshold is a CUBE CENTRE to PAD EDGE margin in millimetres, and therefore the only ones whose
 * threshold may be drawn as a rectangle inset from the pad edge.
 *
 * Taken from the controller, `bin/laptop/manipulation/run_single_cube_cycle.py`: already_placed (:950), placement_plan
 * (:1069 and :1076), arm_only_sufficient (:1083), release_margin (:1116), placed (:1141) and clearance_target_met
 * (:1142). Every other predicate with a millimetre threshold measures something else entirely (a lateral offset at
 * ALIGN, a tool height at RELEASE, a base residual at BASE_RETURNED) and drawing one of those as a pad inset would
 * put a rule on the map that no part of the run ever applied to a cube centre.
 */
export const CENTRE_MARGIN_PREDICATES: readonly string[] = [
  'already_placed',
  'placement_plan',
  'arm_only_sufficient',
  'release_margin',
  'placed',
  'clearance_target_met',
];

/** One centre-margin rule the run actually recorded, with everything needed to say where it came from. */
export interface CentreRule {
  /** The threshold the controller decided with, in millimetres, exactly as the record carries it. */
  margin_mm: number;
  /** Which recorded predicates were decided with this margin, in the order they were recorded. */
  predicates: string[];
  /** The rule and the threshold set behind the newest of them, when the record named them. */
  rule_ref: string | null;
  rule_digest: string | null;
  /** The sequence number of the newest predicate that carried it. */
  seq: number;
}

/**
 * The centre-margin rules this run recorded, widest first.
 *
 * A predicate is only read here when it states the unit itself: a threshold whose unit the record does not give is a
 * number of something unknown, and a rectangle drawn from it would be a measurement this console invented. A margin
 * wider than the pad's short half is dropped too, because no centre on this pad could satisfy it and an inset drawn
 * from it would be an inside-out rectangle.
 */
export function admissibleCentreRules(predicates: readonly PredicateView[]): CentreRule[] {
  const byMargin = new Map<number, CentreRule>();
  for (const predicate of predicates) {
    if (!CENTRE_MARGIN_PREDICATES.includes(predicate.predicate)) continue;
    if (predicate.threshold === null || predicate.unit !== 'mm') continue;
    if (centreAdmissibleHalfExtentsMm(predicate.threshold) === null) continue;
    const held = byMargin.get(predicate.threshold);
    if (held === undefined) {
      byMargin.set(predicate.threshold, {
        margin_mm: predicate.threshold,
        predicates: [predicate.predicate],
        rule_ref: predicate.rule_ref,
        rule_digest: predicate.rule_digest,
        seq: predicate.seq,
      });
      continue;
    }
    if (!held.predicates.includes(predicate.predicate)) held.predicates.push(predicate.predicate);
    if (predicate.seq >= held.seq) {
      held.seq = predicate.seq;
      held.rule_ref = predicate.rule_ref;
      held.rule_digest = predicate.rule_digest;
    }
  }
  return [...byMargin.values()].sort((left, right) => right.margin_mm - left.margin_mm);
}

/**
 * The controller's reason codes that say a pad frame could not be resolved, from the same file: base_pulse_no_pad_frame_after
 * (:803), hold_observe_no_pad_frame (:832), base_return_no_pad_frame (:860), base_probe_no_pad_frame (:876),
 * search_cube_not_in_pad_frame (:943) and search_no_pad_frame (:946).
 */
export const PAD_FRAME_ABSENT_CODES: readonly string[] = [
  'base_pulse_no_pad_frame_after',
  'hold_observe_no_pad_frame',
  'base_return_no_pad_frame',
  'base_probe_no_pad_frame',
  'search_cube_not_in_pad_frame',
  'search_no_pad_frame',
];

/** The predicate the controller records about the pad frame while it holds the cube (run_single_cube_cycle.py:830). */
const PAD_FRAME_PREDICATE = 'pad_frame_valid_while_holding';

export type PadFrameState = 'resolved' | 'absent' | 'not_yet';

export interface PadFrameReading {
  state: PadFrameState;
  /** What is true, in the words the map prints. */
  sentence: string;
  /** The runtime's own code when one said the pad frame was missing. Null otherwise. */
  reason_code: string | null;
}

/**
 * Whether anything on this map rests on a pad frame that was actually resolved.
 *
 * A mark exists only where a frame's own perception resolved a pad-frame position, so one mark is proof that the frame
 * was resolved at least once. With no mark, the record still says which of the two other cases this is: a run that
 * stopped because no pad frame could be resolved, or a screen that has simply not seen one yet. The map draws the pad
 * outline in all three cases, because the outline is the CONFIGURED pad and not a measurement, and it says which case
 * it is rather than leaving an empty pad to be read as an empty table.
 */
export function padFrameReading(view: ViewState, marks: readonly PadMark[]): PadFrameReading {
  if (marks.length > 0) {
    return {
      state: 'resolved',
      sentence: `${String(marks.length)} position(s) on this pad were resolved from a frame in this run.`,
      reason_code: null,
    };
  }

  const code = view.outcome.reason_code;
  if (code !== null && PAD_FRAME_ABSENT_CODES.includes(code)) {
    return {
      state: 'absent',
      sentence: 'This run stopped without resolving a pad frame, so nothing measured can be placed on this map.',
      reason_code: code,
    };
  }

  const recorded = view.predicates.find((predicate) => predicate.predicate === PAD_FRAME_PREDICATE) ?? null;
  if (recorded?.value === false) {
    return {
      state: 'absent',
      sentence: 'The pad frame was checked while the cube was held and was not valid, so nothing measured can be placed on this map.',
      reason_code: recorded.reason_code,
    };
  }

  return {
    state: 'not_yet',
    sentence: 'No frame in this run has resolved a pad-frame position yet. The outline is the configured pad, not a measurement.',
    reason_code: null,
  };
}

/**
 * The mark the release admission was computed at, or null when this console holds none for it.
 *
 * The estimate is drawn at the position THE PREDICATE CONSUMED, found through that predicate's own evidence. The
 * newest mark is not that position: it can come from a later observation, and the estimate drawn there would sit on
 * evidence it was never computed from.
 */
export function admissionMarkFor(admission: PredicateView | null, marks: readonly PadMark[]): PadMark | null {
  if (admission === null) return null;
  const consumed = new Set<string>(admission.evidence.flatMap((piece) => piece.observation_ids));
  if (consumed.size === 0) return null;
  return [...marks].reverse().find((mark) => consumed.has(mark.observation_id)) ?? null;
}
