// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The search stage file, read the way the screen reads it. It is imported FIRST, before any model file, on purpose: the
// stage files and the three tables that read them must not import each other, and a stage file that did would fail
// here. The track that owns this stage adds its own cases to this file.

import { describe, expect, it } from 'vitest';
import { SEARCH_STAGE } from './search';
import { describeFailureCode, describeFailureCodeWith } from '../failure';
import { isTerminalState, phaseOfState, presentationStepOfState } from '../phase';
import { describeState } from '../reason';

describe('the search stage file', () => {
  it('is the search stage', () => {
    expect(SEARCH_STAGE.stage).toBe('search');
  });

  it('is what the failure table reads for each of its codes', () => {
    for (const [code, template] of Object.entries(SEARCH_STAGE.failures)) {
      expect(describeFailureCode(code), code).toBe(template.gloss);
      expect(describeFailureCodeWith(code, {}), code).toBe(template.gloss);
      expect(template.gloss.length, code).toBeGreaterThan(0);
    }
  });

  it('is what the state sentence, the phase and the step read for each of its states', () => {
    for (const [state, entry] of Object.entries(SEARCH_STAGE.states)) {
      expect(describeState(state), state).toBe(entry.sentence);
      expect(phaseOfState(state), state).toBe(entry.phase);
      expect(isTerminalState(state), state).toBe(entry.phase === 'terminal');
      expect(presentationStepOfState(state), state).toBe(entry.step ?? 'terminal');
      expect(entry.step === null, state).toBe(entry.phase === 'terminal');
    }
  });
});

describe('the pad policy of 2026-09-29 in the search stage', () => {
  it('mission_binding_no_candidate says a block on the pad in the first view ends the sweep (a named residual)', () => {
    const sweep = 'when SEARCH sweeps, a block on the pad in its first view (an earlier block, or the pad\'s printed logo read as one) ends the sweep, so a block off the pad outside that view may not have been looked for';
    expect(describeFailureCode('mission_binding_no_candidate')).toContain(sweep);
    expect(describeFailureCodeWith('mission_binding_no_candidate', { cubes_seen: { value: 1, unit: null }, placed_cubes: { value: 1, unit: null } })).toBe(
      `no cube in view could be taken as this target: 1 cube(s) were seen and 1 of them are already placed on the pad; nothing was picked up; ${sweep}`,
    );
  });

  it('mission_binding_candidate_not_placeable speaks of every block off the pad, since an auto target weighs them all', () => {
    const gloss = describeFailureCode('mission_binding_candidate_not_placeable') ?? '';
    expect(gloss).not.toContain('the one cube in view');
    expect(gloss).toBe('no cube off the pad in view has a bounded placement from here: neither the arm alone nor the bounded base advance reaches the pad from it, so nothing was picked up');
  });
});
