// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE LIMIT A PLACEMENT REFUSAL NAMES, read from the values the record carries. The records below are the real shapes:
// track P's placement_envelope.explain_refusal for start 3 and for the block of 21:17 (computed on fix/ovn4-p with the
// run's side drift allowance), and the controller's placement_refusal_values at 9891d14, which names no constraint.

import { describe, expect, it } from 'vitest';
import { placementLimit } from './placementLimit';

type Value = string | number | boolean | null;
const record = (values: Record<string, Value>): { name: string; value: Value }[] => Object.entries(values).map(([name, value]) => ({ name, value }));

/** placement_envelope.explain_refusal(cycle, (0.163, -0.210), (-0.042, 0.999), 0.0, 6.0, ()) on fix/ovn4-p. */
const START_3_P = {
  side_margin_mm: 44.5,
  required_side_margin_mm: 58.96051224213831,
  required_axis_margin_mm: 58.96051224213831,
  axis_advance_needed_unbounded_mm: 121.58209433647478,
  constraint: 'side_edge',
  deficit_mm: 9.4,
  hint: 'move the block 10 mm to the left as seen from the robot and 3 mm toward the pad',
  hint_places_block: true,
  binding_edge: 'right',
  along_budget_deficit_mm: 1.6,
  model_admits_under: 'projected+reach_after_advance',
  explanation: 'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance',
};

/** The same for the block of 21:17, (0.064, -0.220). */
const LATE_2117_P = {
  side_margin_mm: 143.5,
  required_side_margin_mm: 58.96051224213831,
  required_axis_margin_mm: 58.96051224213831,
  axis_advance_needed_unbounded_mm: 131.59210434648477,
  constraint: 'along_budget',
  deficit_mm: 11.6,
  hint: 'move the block 12 mm toward the pad',
  hint_places_block: true,
  model_admits_under: 'projected+reach_after_advance',
  explanation: 'the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm',
};

/** search_placement_infeasible at 9891d14 for start 3: placement_refusal_values plus the bounded advance limit, no constraint. */
const START_3_LEGACY = {
  anchor_pad_x_m: 0.163,
  anchor_pad_y_m: -0.21,
  anchor_margin_mm: 44.5,
  required_centre_margin_mm: 58.96,
  bounded_base_advance_limit_mm: 120,
  side_margin_mm: 44.5,
  required_side_margin_mm: 58.96051224213831,
  required_axis_margin_mm: 58.96051224213831,
  axis_advance_needed_unbounded_mm: 121.58209433647478,
};

describe('the limit a placement refusal names', () => {
  it("states track P's explanation for start 3 with its hint, and no number the explanation was not computed from", () => {
    const limit = placementLimit(record(START_3_P));
    expect(limit).toBe(
      'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance; the along-axis budget is also short, by 1.6 mm; move the block 10 mm to the left as seen from the robot and 3 mm toward the pad',
    );
    // The side margin before any advance (44.5 mm) is not paired with the shortfall after 120 mm of it.
    expect(limit).not.toContain('44.5');
  });

  it("states track P's explanation for the block of 21:17, never the raw constraint code", () => {
    const limit = placementLimit(record(LATE_2117_P));
    expect(limit).toBe('the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm; move the block 12 mm toward the pad');
    expect(limit).not.toContain('along_budget');
  });

  it('derives both limits from the margins the 9891d14 record carries when it names no constraint', () => {
    expect(placementLimit(record(START_3_LEGACY))).toBe(
      'the side limit: a side margin of 44.5 mm against the 58.96 mm required before any advance, 14.46 mm short; and the along-axis advance: 121.58 mm needed against the 120 mm limit, 1.58 mm past it',
    );
    // A refusal whose margins are met names nothing it cannot support.
    expect(placementLimit(record({ side_margin_mm: 70, required_side_margin_mm: 58.96, axis_advance_needed_unbounded_mm: 80, bounded_base_advance_limit_mm: 120 }))).toBeNull();
    expect(placementLimit(record({ candidates: 1, cubes_seen: 1 }))).toBeNull();
  });

  it('pairs a shortfall with its constraint in words when the record carries no explanation', () => {
    const { explanation: _side, ...sideOnly } = START_3_P;
    const side = placementLimit(record(sideOnly));
    expect(side).toContain('the side limit on the right edge, 9.4 mm short at the best bounded advance');
    expect(side).not.toContain('44.5');
    const { explanation: _along, ...alongOnly } = LATE_2117_P;
    expect(placementLimit(record(alongOnly))).toContain('the along-axis advance budget: 131.59 mm of base advance needed, 11.6 mm past the budget');
    for (const [code, words] of [
      ['far_edge', 'the pad edge the reach axis runs toward'],
      ['arm_reach', 'the arm alone'],
      ['pickup_inside_grasp_radius', 'nearer than the pregrasp can reach'],
      ['lateral_needs_capability', 'only a sideways base move'],
    ] as const) {
      const limit = placementLimit(record({ constraint: code, deficit_mm: 3 }));
      expect(limit).toContain(words);
      expect(limit).toContain('3 mm');
      expect(limit).not.toContain(code);
    }
    // `binding_constraint` is track S's scene-block field, not an older spelling of this one: its deficit_mm is the
    // along-axis need less the budget, so it is never stated as the side limit's shortfall.
    const scene = placementLimit(record({ binding_constraint: 'side_edge', side_margin_mm: 44.5, required_side_margin_mm: 58.96, along_axis_need_mm: 121.58, advance_budget_mm: 120, deficit_mm: 1.6 }));
    expect(scene).toContain('14.46 mm short');
    expect(scene).not.toContain('1.6 mm short');
  });

  it("says when the hint is only the binding limit's own move", () => {
    const limit = placementLimit(record({ ...LATE_2117_P, hint_places_block: false }));
    expect(limit).toContain('for this limit only: move the block 12 mm toward the pad');
  });

  it('states the reason alone when the solver names no limit because a reading is not finite', () => {
    const reason = 'no limit is named because the anchor or the reach axis or the grasp radius is not finite';
    expect(placementLimit(record({ model_admits_under: null, explanation: reason }))).toBe(reason);
  });
});
