// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// What the pad map may draw. The thresholds in these cases are the controller's own, computed the way it computes
// them (a 25.4 mm cube's half-diagonal is 17.96 mm, and the required centre margin adds the 20 mm clearance target for
// 37.96 mm), so a run recorded under a different cube or a different target draws a different rectangle and this
// console keeps no copy of either number.

import { describe, expect, it } from 'vitest';
import { admissibleCentreRules, admissionMarkFor, padFrameReading } from './padScene';
import type { PadMark } from './padScene';
import { initialState } from './reducer';
import type { PredicateView, ViewState } from './types';

function predicate(extra: Partial<PredicateView> = {}): PredicateView {
  return {
    predicate: 'release_margin',
    value: true,
    state: 'evaluated',
    reason_code: null,
    rule_ref: null,
    rule_digest: null,
    threshold: 37.96,
    measured: 41.2,
    unit: 'mm',
    evidence: [],
    consumed_source_stamp: null,
    measurements: [],
    seq: 40,
    t_mono_ns: 40_000_000,
    clock_domain: 'laptop_monotonic',
    ...extra,
  };
}

function mark(extra: Partial<PadMark> = {}): PadMark {
  return { observation_id: 'obs-0001', state: 'SEARCH', point_mm: [12, -4], seq: 10, ...extra };
}

function viewWith(extra: Partial<ViewState> = {}): ViewState {
  return { ...initialState(), ...extra };
}

describe('the admissible centre rules a run recorded', () => {
  it('takes the threshold off the predicate that was decided with it and never from a constant here', () => {
    const rules = admissibleCentreRules([predicate({ threshold: 41.5, rule_ref: 'placement/v3', rule_digest: 'sha256:abc' })]);
    expect(rules).toHaveLength(1);
    expect(rules[0]?.margin_mm).toBe(41.5);
    expect(rules[0]?.rule_ref).toBe('placement/v3');
    expect(rules[0]?.predicates).toEqual(['release_margin']);
  });

  it('keeps the acceptance rule and the planning rule apart, widest first', () => {
    const rules = admissibleCentreRules([
      predicate({ predicate: 'placed', threshold: 17.96, seq: 90 }),
      predicate({ predicate: 'release_margin', threshold: 37.96, seq: 40 }),
      predicate({ predicate: 'placement_plan', threshold: 37.96, seq: 30 }),
    ]);
    expect(rules.map((rule) => rule.margin_mm)).toEqual([37.96, 17.96]);
    expect(rules[0]?.predicates).toEqual(['release_margin', 'placement_plan']);
    expect(rules[1]?.predicates).toEqual(['placed']);
  });

  it('names the rule and the digest of the NEWEST predicate that carried the threshold', () => {
    const rules = admissibleCentreRules([
      predicate({ seq: 10, rule_ref: 'placement/v1', rule_digest: 'sha256:old' }),
      predicate({ predicate: 'clearance_target_met', seq: 99, rule_ref: 'placement/v2', rule_digest: 'sha256:new' }),
    ]);
    expect(rules[0]?.rule_ref).toBe('placement/v2');
    expect(rules[0]?.rule_digest).toBe('sha256:new');
    expect(rules[0]?.seq).toBe(99);
  });

  it('draws nothing for a predicate that measures something other than a centre margin', () => {
    const rules = admissibleCentreRules([
      predicate({ predicate: 'aligned', threshold: 6 }),
      predicate({ predicate: 'release_height', threshold: 12 }),
      predicate({ predicate: 'base_returned', threshold: 10 }),
      predicate({ predicate: 'straddling', threshold: 8 }),
    ]);
    expect(rules).toEqual([]);
  });

  it('draws nothing for a threshold whose unit the record did not state', () => {
    expect(admissibleCentreRules([predicate({ unit: null })])).toEqual([]);
    expect(admissibleCentreRules([predicate({ unit: 'm' })])).toEqual([]);
    expect(admissibleCentreRules([predicate({ threshold: null })])).toEqual([]);
  });

  it('refuses a margin no centre on this pad could satisfy rather than drawing it inside out', () => {
    expect(admissibleCentreRules([predicate({ threshold: 200 })])).toEqual([]);
  });
});

describe('whether anything on the map rests on a resolved pad frame', () => {
  it('reads one resolved position as proof that the frame was resolved', () => {
    const reading = padFrameReading(viewWith(), [mark()]);
    expect(reading.state).toBe('resolved');
    expect(reading.sentence).toContain('1 position(s)');
  });

  it('says the run stopped without a pad frame, with the runtime code, rather than drawing an empty pad', () => {
    const view = viewWith({ outcome: { ...initialState().outcome, reported: 'failed', reason_code: 'search_no_pad_frame' } });
    const reading = padFrameReading(view, []);
    expect(reading.state).toBe('absent');
    expect(reading.reason_code).toBe('search_no_pad_frame');
    expect(reading.sentence).toContain('nothing measured can be placed on this map');
  });

  it('reads a recorded pad frame check that came back false as an absence', () => {
    const view = viewWith({
      predicates: [predicate({ predicate: 'pad_frame_valid_while_holding', value: false, reason_code: 'pad_not_detected', threshold: null, unit: null })],
    });
    expect(padFrameReading(view, []).state).toBe('absent');
    expect(padFrameReading(view, []).reason_code).toBe('pad_not_detected');
  });

  it('separates a run that has not resolved one YET from a run that could not', () => {
    const reading = padFrameReading(viewWith(), []);
    expect(reading.state).toBe('not_yet');
    expect(reading.sentence).toContain('The outline is the configured pad, not a measurement');
  });

  it('never calls a run without a pad frame resolved just because a predicate failed for another reason', () => {
    const view = viewWith({ outcome: { ...initialState().outcome, reported: 'failed', reason_code: 'grasp_missed' } });
    expect(padFrameReading(view, []).state).toBe('not_yet');
  });
});

describe('where the release admission estimate may be drawn', () => {
  it('draws it at the observation the predicate consumed and not at the newest one', () => {
    const marks = [mark({ observation_id: 'obs-a', seq: 10 }), mark({ observation_id: 'obs-b', seq: 20 })];
    const admission = predicate({
      predicate: 'release_admitted',
      evidence: [{ type: 'anchor', observation_ids: ['obs-a'], scalar_name: null }],
    });
    expect(admissionMarkFor(admission, marks)?.observation_id).toBe('obs-a');
  });

  it('draws nothing when no mark was resolved for the evidence it consumed', () => {
    const admission = predicate({
      predicate: 'release_admitted',
      evidence: [{ type: 'anchor', observation_ids: ['obs-never-resolved'], scalar_name: null }],
    });
    expect(admissionMarkFor(admission, [mark({ observation_id: 'obs-a' })])).toBeNull();
    expect(admissionMarkFor(admission, [])).toBeNull();
  });

  it('draws nothing when the predicate names no evidence at all', () => {
    expect(admissionMarkFor(predicate({ evidence: [] }), [mark()])).toBeNull();
    expect(admissionMarkFor(null, [mark()])).toBeNull();
  });
});
