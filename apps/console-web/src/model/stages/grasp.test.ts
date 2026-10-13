// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The grasp stage file, read the way the screen reads it. It is imported FIRST, before any model file, on purpose: the
// stage files and the three tables that read them must not import each other, and a stage file that did would fail
// here. The track that owns this stage adds its own cases to this file.

import { describe, expect, it } from 'vitest';
import { GRASP_STAGE } from './grasp';
import { fillTemplate } from './stage';
import { describeFailureCode, describeFailureCodeWith } from '../failure';
import { isTerminalState, phaseOfState, presentationStepOfState } from '../phase';
import { describeState } from '../reason';

describe('the grasp stage file', () => {
  it('is the grasp stage', () => {
    expect(GRASP_STAGE.stage).toBe('grasp');
  });

  it('is what the failure table reads for each of its codes', () => {
    for (const [code, template] of Object.entries(GRASP_STAGE.failures)) {
      expect(describeFailureCode(code), code).toBe(template.gloss);
      expect(describeFailureCodeWith(code, {}), code).toBe(template.gloss);
      expect(template.gloss.length, code).toBeGreaterThan(0);
    }
  });

  it('is what the state sentence, the phase and the step read for each of its states', () => {
    for (const [state, entry] of Object.entries(GRASP_STAGE.states)) {
      expect(describeState(state), state).toBe(entry.sentence);
      expect(phaseOfState(state), state).toBe(entry.phase);
      expect(isTerminalState(state), state).toBe(entry.phase === 'terminal');
      expect(presentationStepOfState(state), state).toBe(entry.step ?? 'terminal');
      expect(entry.step === null, state).toBe(entry.phase === 'terminal');
    }
  });
});

describe('a recorded float in a measured sentence', () => {
  it('prints to three decimals at most, never with its binary tail', () => {
    const missed = GRASP_STAGE.failures['grasp_missed'];
    const sentence = fillTemplate(missed, {
      closes: { value: 3, unit: null },
      gripper: { value: 67.3567977915804, unit: null },
      gripper_latch: { value: 66.8, unit: null },
    });
    expect(sentence).toContain('the jaw reached 67.357, against the empty-latch value of 66.8');
    expect(sentence).toContain('closed on nothing 3 times');
  });
});
