// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SCENE MAP'S FOLD. It reads the record only: the scan's `scene-*` results, the SEARCH frame's cube in the pad
// frame, the selection, the refusal, the measured pulses and the placement. It never draws the robot's base, because
// no record carries where the base is, and it never draws the pan band's wedge unless the scan's fit was accepted.

import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { buildFarBlockRun, buildSceneScanRun, SCENARIO_CATALOGUE } from '../api/syntheticScenarios';
import type { RunFactory } from '../api/syntheticScenarios';
import { BINDING_CONSTRAINT, BLOCK_CLASSES, applySceneEvent, blockSentence, emptySceneMap, foldSceneMap, sceneExtentMm } from './sceneMap';

function eventsOf(build: RunFactory, index = 1, startSeq = 0): ContractEvent[] {
  return build(index, startSeq, 1_000_000_000).steps.map((step) => step.event);
}

function flowRun(name: string, position = 0): RunFactory {
  const step = SCENARIO_CATALOGUE.find((spec) => spec.name === name)?.flow[position];
  if (step === undefined) throw new Error(`no run ${String(position)} in ${name}`);
  return step.build;
}

describe('the scene map fold', () => {
  it('starts empty and says why the base is not drawn', () => {
    const map = emptySceneMap();
    expect(map.blocks).toEqual([]);
    expect(map.views).toEqual([]);
    expect(map.fit).toBeNull();
    expect(map.wedge).toBeNull();
    expect(map.base_drawn).toBe(false);
    expect(map.base_note).toContain('no record carries');
  });

  it('folds a scan: the view footprints, the accepted fit and its wedge, the blocks by class', () => {
    const map = foldSceneMap(eventsOf(buildSceneScanRun));
    expect(map.views).toHaveLength(5);
    expect(map.views.map((view) => view.pan_deg)).toEqual([-9.67, -24.67, -9.67, 5.33, -9.67]);
    expect(map.views[0]?.footprint_mm?.[0]).toEqual([-160, -60]);
    expect(map.fit).toMatchObject({ axis_mm: [-12.5, -413.2], accepted: true, residual_mm: 0.8, band_deg: 25 });
    expect(map.wedge).toMatchObject({ apex_mm: [-12.5, -413.2], from_deg: -25, to_deg: 25 });
    expect(map.blocks.map((block) => [block.class, block.inferred, block.source])).toEqual([
      ['BASE_ADVANCE', false, 'scan'],
      ['OUTSIDE_PAN_BAND', false, 'scan'],
    ]);
    expect(map.blocks[0]?.point_mm).toEqual([-20.1, -196]);
    expect(map.blocks[0]?.sentence).toContain('placing it needs the base advance');
    // The capability words are the live slot's, drawn beside it (spec 5.7.5), never a fixed text in the fold.
    expect(map.blocks[0]?.sentence).not.toContain('Cake capability');
    expect(map.blocks[1]?.sentence).toContain('41 deg');
    // The far block was measured in a pad-valid +15 view; only its pan angle is inferred, not its position.
    expect(map.blocks[1]?.sentence).toContain('about 41 deg from home, the pan angle inferred');
    expect(map.blocks[1]?.sentence).not.toContain('carried from another view');
  });

  it('draws no wedge from a fit that was not accepted', () => {
    const scan = eventsOf(buildSceneScanRun).map((event) => {
      if (event.kind !== 'perception.result' || event.payload.detector !== 'scene-pad') return event;
      return { ...event, payload: { ...event.payload, measurements: event.payload.measurements.map((item) => (item.name === 'fit_accepted' ? { ...item, value: false } : item)) } } as ContractEvent;
    });
    const map = foldSceneMap(scan);
    expect(map.fit?.accepted).toBe(false);
    expect(map.wedge).toBeNull();
  });

  it('folds a placed cycle: the selected block, the measured pulses along their axes, the placement and its envelope', () => {
    const map = foldSceneMap(eventsOf(buildFarBlockRun));
    const block = map.blocks[0];
    expect(block).toMatchObject({ class: 'BASE_ADVANCE', source: 'search', selected: true, point_mm: [-20.1, -196] });
    expect(map.pulses.map((pulse) => pulse.advance_mm)).toEqual([26.6, 40.1, 39.7]);
    expect(map.pulses[0]?.from_mm).toEqual([-20.1, -196]);
    const last = map.pulses[2]?.to_mm ?? [0, 0];
    expect(last[0]).toBeCloseTo(-19.75, 1);
    expect(last[1]).toBeCloseTo(-89.67, 1);
    expect(map.returns.map((pulse) => pulse.advance_mm)).toEqual([-40.0, -40.3, -23.5]);
    expect(map.placed).toMatchObject({ point_mm: [-15.8, -100], centre_to_edge_mm: 47.5 });
    expect(map.placement_margins_mm).toEqual([37.96]);
  });

  it('classifies a block refused at SEARCH with the limits the refusal named', () => {
    const map = foldSceneMap(eventsOf(flowRun('not-placeable-side')));
    expect(map.blocks).toHaveLength(1);
    const block = map.blocks[0];
    expect(block?.class).toBe('NOT_PLACEABLE');
    expect(block?.point_mm).toEqual([163, -210]);
    expect(block?.sentence).toContain('the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance');
    expect(block?.sentence).toContain('move the block 10 mm to the left');
    // The hint is said once.
    expect(block?.sentence.split('move the block').length).toBe(2);
  });

  it("names the binding limit by the placement track's spelling of it (plan section 5, P Must 2)", () => {
    expect(BINDING_CONSTRAINT).toEqual({
      side_edge: 'side_edge',
      along_budget: 'along_budget',
      arm_reach: 'arm_reach',
      pickup_inside_grasp_radius: 'pickup_inside_grasp_radius',
      lateral_needs_capability: 'lateral_needs_capability',
    });
    const detail = (name: string, value: string | number): { name: string; value: string | number; unit: null } => ({ name, value, unit: null });
    // Track P's record with no explanation sentence: the constraint in words and its deficit, never the raw code.
    const side = blockSentence('NOT_PLACEABLE', 'search', [detail('constraint', 'side_edge'), detail('deficit_mm', 9.4), detail('binding_edge', 'right'), detail('side_margin_mm', 44.5), detail('required_side_margin_mm', 58.96)], false);
    expect(side).toContain('the side limit on the right edge, 9.4 mm short at the best bounded advance');
    const budget = blockSentence('NOT_PLACEABLE', 'search', [detail('constraint', 'along_budget'), detail('axis_advance_needed_unbounded_mm', 131.59), detail('deficit_mm', 11.6)], false);
    expect(budget).toContain('131.59 mm of base advance needed, 11.6 mm past the budget');
    expect(budget).not.toContain('along_budget');
  });

  it("pairs each deficit of track S's scene-block record with its own limit", () => {
    // policy_search._block_measurements at the head: binding_constraint is side_edge when the side margin is short, and
    // deficit_mm is always the along-axis need less the advance budget, whichever limit binds.
    const detail = (name: string, value: string | number, unit: string | null = 'mm'): { name: string; value: string | number; unit: string | null } => ({ name, value, unit });
    const start3 = blockSentence(
      'NOT_PLACEABLE',
      'scan',
      [detail('binding_constraint', 'side_edge', null), detail('side_margin_mm', 44.5), detail('required_side_margin_mm', 58.96), detail('along_axis_need_mm', 121.58), detail('advance_budget_mm', 120), detail('deficit_mm', 1.6)],
      false,
    );
    expect(start3).toContain('the side limit: a side margin of 44.5 mm against the 58.96 mm required before any advance, 14.46 mm short');
    expect(start3).toContain('the along-axis advance: 121.58 mm needed against the 120 mm budget, 1.6 mm past it');
    expect(start3).not.toContain('1.6 mm short');
    // A side-limited block well inside the budget: its along-axis deficit is negative and is never stated as a shortfall.
    const inside = blockSentence(
      'NOT_PLACEABLE',
      'scan',
      [detail('binding_constraint', 'side_edge', null), detail('side_margin_mm', 40), detail('required_side_margin_mm', 58.96), detail('along_axis_need_mm', 100), detail('advance_budget_mm', 120), detail('deficit_mm', -20)],
      false,
    );
    expect(inside).toContain('the side limit: a side margin of 40 mm against the 58.96 mm required before any advance, 18.96 mm short');
    expect(inside).not.toContain('-20');
    expect(inside).not.toContain('past it');
  });

  it('classifies the budget refusal by its deficit', () => {
    const map = foldSceneMap(eventsOf(flowRun('not-placeable-budget')));
    expect(map.blocks[0]?.class).toBe('NOT_PLACEABLE');
    expect(map.blocks[0]?.sentence).toContain('131.6');
    expect(map.blocks[0]?.sentence).toContain('11.6');
  });

  it('keeps the scan when the cycle that picks from it begins, and merges its block with the selection', () => {
    const scan = eventsOf(buildSceneScanRun, 1, 0);
    const lastSeq = scan[scan.length - 1]?.seq ?? 0;
    const cycle = eventsOf(buildFarBlockRun, 2, lastSeq);
    const map = foldSceneMap([...scan, ...cycle]);
    expect(map.views).toHaveLength(5);
    expect(map.blocks).toHaveLength(2);
    expect(map.blocks.find((block) => block.class === 'BASE_ADVANCE')?.selected).toBe(true);
    expect(map.pulses).toHaveLength(3);
  });

  it('forgets the pulses and the placement of an earlier cycle when a new one begins', () => {
    const first = eventsOf(buildFarBlockRun, 1, 0);
    const lastSeq = first[first.length - 1]?.seq ?? 0;
    const second = eventsOf(flowRun('not-placeable-budget'), 2, lastSeq);
    const map = foldSceneMap([...first, ...second]);
    expect(map.pulses).toEqual([]);
    expect(map.placed).toBeNull();
    expect(map.blocks.map((block) => block.class)).toEqual(['NOT_PLACEABLE']);
  });

  it('draws the block Look saw without classifying it', () => {
    const look = SCENARIO_CATALOGUE.find((spec) => spec.name === 'observe-landmarks-only')?.look;
    if (look === undefined) throw new Error('no look');
    const map = foldSceneMap(eventsOf(look));
    expect(map.blocks).toHaveLength(1);
    expect(map.blocks[0]).toMatchObject({ source: 'look', class: null, point_mm: [-92.9, -169.4] });
    expect(map.blocks[0]?.sentence).toContain('Look');
  });

  it('is a pure fold: an event of a kind it does not read returns the same map', () => {
    const map = emptySceneMap();
    const diagnostic = eventsOf(buildFarBlockRun).find((event) => event.kind === 'timing.span' || event.kind === 'action.started');
    if (diagnostic === undefined) throw new Error('no event');
    expect(applySceneEvent(map, diagnostic)).toBe(map);
  });

  it('spans the pad, the axis and every block in its extent', () => {
    const map = foldSceneMap(eventsOf(buildSceneScanRun));
    const extent = sceneExtentMm(map);
    expect(extent.min_y).toBeLessThan(-413.2);
    expect(extent.max_y).toBeGreaterThan(147.5);
    expect(extent.max_x).toBeGreaterThan(207.5);
    expect(extent.min_x).toBeLessThan(-207.5);
  });

  it('has a sentence for every class the plan names', () => {
    expect(Object.keys(BLOCK_CLASSES).sort()).toEqual(
      ['ALREADY_PLACED', 'ARM_ONLY', 'BASE_ADVANCE', 'NOT_PLACEABLE', 'TOO_NEAR', 'OUTSIDE_PAN_BAND', 'SEEN_NOT_MEASURABLE', 'NO_PAD_FRAME'].sort(),
    );
  });
});
