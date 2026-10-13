// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The placement stage file, read the way the screen reads it. It is imported FIRST, before any model file, on purpose: the
// stage files and the three tables that read them must not import each other, and a stage file that did would fail
// here. The track that owns this stage adds its own cases to this file.

import { describe, expect, it } from 'vitest';
import reachCarry from '../../../../../autonomy/manipulation/reach_carry.py?raw';
import { PLACEMENT_CONSTRAINT_WORDS, PLACEMENT_STAGE, REACH_REFUSAL_WORDS, placementLimitClause, reachRefusalClause } from './placement';
import { describeFailureCode, describeFailureCodeWith } from '../failure';
import { isTerminalState, phaseOfState, presentationStepOfState } from '../phase';
import { describeState } from '../reason';

describe('the placement stage file', () => {
  it('is the placement stage', () => {
    expect(PLACEMENT_STAGE.stage).toBe('placement');
  });

  it('is what the failure table reads for each of its codes', () => {
    for (const [code, template] of Object.entries(PLACEMENT_STAGE.failures)) {
      expect(describeFailureCode(code), code).toBe(template.gloss);
      expect(describeFailureCodeWith(code, {}), code).toBe(template.gloss);
      expect(template.gloss.length, code).toBeGreaterThan(0);
    }
  });

  it('is what the state sentence, the phase and the step read for each of its states', () => {
    for (const [state, entry] of Object.entries(PLACEMENT_STAGE.states)) {
      expect(describeState(state), state).toBe(entry.sentence);
      expect(phaseOfState(state), state).toBe(entry.phase);
      expect(isTerminalState(state), state).toBe(entry.phase === 'terminal');
      expect(presentationStepOfState(state), state).toBe(entry.step ?? 'terminal');
      expect(entry.step === null, state).toBe(entry.phase === 'terminal');
    }
  });

  it('names the binding limit, the deficit and the hint when the refusal record carries them', () => {
    const args = {
      anchor_margin_mm: { value: -72.5, unit: 'mm' },
      required_centre_margin_mm: { value: 37.96, unit: 'mm' },
      constraint: { value: 'along_budget', unit: null },
      deficit_mm: { value: 11.6, unit: 'mm' },
      hint: { value: 'move the block 12 mm toward the pad', unit: null },
    };
    const sentence = describeFailureCodeWith('placement_infeasible_while_held', args);
    expect(sentence).toContain('the binding limit: the base advance along the reach axis is past its budget by 11.6 mm; move the block 12 mm toward the pad');
    expect(describeFailureCodeWith('search_placement_infeasible', args)).toContain('by 11.6 mm; move the block 12 mm toward the pad');
  });

  it('states no limit it was not given, and none it does not know', () => {
    const base = { anchor_margin_mm: { value: -72.5, unit: 'mm' }, required_centre_margin_mm: { value: 37.96, unit: 'mm' } };
    const plain = describeFailureCodeWith('placement_infeasible_while_held', base);
    expect(plain).not.toContain('binding limit');
    expect(placementLimitClause({ constraint: { value: 'no_such_limit', unit: null } })).toBe('');
    expect(placementLimitClause({ constraint: { value: 'side_edge', unit: null } })).toBe('; the binding limit: an edge the reach axis runs along stays short');
    for (const constraint of ['side_edge', 'along_budget', 'far_edge', 'arm_reach', 'pickup_inside_grasp_radius', 'lateral_needs_capability']) {
      expect(PLACEMENT_CONSTRAINT_WORDS[constraint], constraint).toBeTruthy();
    }
  });

  it('states no deficit when the record states zero', () => {
    const clause = placementLimitClause({
      constraint: { value: 'side_edge', unit: null },
      deficit_mm: { value: 0, unit: 'mm' },
      hint: { value: 'move the block 1 mm to the right as seen from the robot', unit: null },
    });
    expect(clause).toBe('; the binding limit: an edge the reach axis runs along stays short; move the block 1 mm to the right as seen from the robot');
  });

  it('says a block inside the pickup band is nearer than the pregrasp reaches, and states the far edge shortfall as a shortfall', () => {
    expect(placementLimitClause({ constraint: { value: 'pickup_inside_grasp_radius', unit: null } })).toBe(
      '; the binding limit: the block stands nearer than the pregrasp can reach',
    );
    // the deficit is the shortfall against the required margin, not how far past the edge the block lies (it may be inside it)
    expect(placementLimitClause({ constraint: { value: 'far_edge', unit: null }, deficit_mm: { value: 211.5, unit: 'mm' } })).toBe(
      '; the binding limit: advancing only shortens the margin on the pad edge the reach axis runs toward, which is short by 211.5 mm',
    );
  });

  it('marks a hint the controller did not check as covering the named limit only', () => {
    const args = {
      constraint: { value: 'side_edge', unit: null },
      deficit_mm: { value: 4.2, unit: 'mm' },
      hint: { value: 'move the block 5 mm to the left as seen from the robot', unit: null },
    };
    expect(placementLimitClause({ ...args, hint_places_block: { value: false, unit: null } })).toBe(
      '; the binding limit: an edge the reach axis runs along stays short by 4.2 mm; for this limit only: move the block 5 mm to the left as seen from the robot',
    );
    expect(placementLimitClause({ ...args, hint_places_block: { value: true, unit: null } })).toBe(
      '; the binding limit: an edge the reach axis runs along stays short by 4.2 mm; move the block 5 mm to the left as seen from the robot',
    );
  });

  // THE ADDITIVE PLACEMENT POLICIES' REFUSALS WITH THE CUBE HELD say what refused the arm alone, and nothing it did not.
  it('says why the arm alone did not place the held block in a run without the capability, never that it fell short when it did not', () => {
    expect(describeFailureCode('reach_arm_only_insufficient_while_held')).not.toContain('fell short');
    const args = { fk_travel_from_grasp_mm: { value: 90.2, unit: 'mm' }, refused_by: { value: 'hold_not_steady', unit: null } };
    const sentence = describeFailureCodeWith('reach_arm_only_insufficient_while_held', args) ?? '';
    expect(sentence).toContain('the arm alone did not place the block (the lowered arm did not hold still before the release): it reached 90.2 mm beyond the grasp');
    expect(sentence).not.toContain('fell short');
    expect(reachRefusalClause({ refused_by: { value: 'no_such_refusal', unit: null } })).toBe('');
    expect(reachRefusalClause({})).toBe('');
  });

  it('has words for every refusal the reach carry and the policies name', () => {
    const line = reachCarry.split('\n').find((text) => text.startsWith('CARRY_SHORT, '));
    const names = [...(line ?? '').matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
    expect(names.length).toBeGreaterThanOrEqual(5);
    for (const name of names) expect(REACH_REFUSAL_WORDS[name ?? ''], name).toBeTruthy();
    expect(Object.keys(REACH_REFUSAL_WORDS).sort()).toEqual([...names].sort());
  });

  it('says a mode refusal with the cube held only of plans that lie on the other side of the line', () => {
    const gloss = describeFailureCode('reach_mode_changed_while_held') ?? '';
    expect(gloss).toContain('every plan that places the block from the measured grasp');
    expect(gloss).not.toContain('every placement of this run');
  });
});
