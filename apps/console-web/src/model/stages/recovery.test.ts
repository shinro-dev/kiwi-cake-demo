// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The recovery stage file, read the way the screen reads it. It is imported FIRST, before any model file, on purpose: the
// stage files and the three tables that read them must not import each other, and a stage file that did would fail
// here. The track that owns this stage adds its own cases to this file.

import { describe, expect, it } from 'vitest';
import { RECOVERY_STAGE } from './recovery';
import { describeFailureCode, describeFailureCodeWith } from '../failure';
import { isTerminalState, phaseOfState, presentationStepOfState } from '../phase';
import { describeState } from '../reason';

describe('the recovery stage file', () => {
  it('is the recovery stage', () => {
    expect(RECOVERY_STAGE.stage).toBe('recovery');
  });

  it('is what the failure table reads for each of its codes', () => {
    for (const [code, template] of Object.entries(RECOVERY_STAGE.failures)) {
      expect(describeFailureCode(code), code).toBe(template.gloss);
      expect(describeFailureCodeWith(code, {}), code).toBe(template.gloss);
      expect(template.gloss.length, code).toBeGreaterThan(0);
    }
  });

  it('is what the state sentence, the phase and the step read for each of its states', () => {
    for (const [state, entry] of Object.entries(RECOVERY_STAGE.states)) {
      expect(describeState(state), state).toBe(entry.sentence);
      expect(phaseOfState(state), state).toBe(entry.phase);
      expect(isTerminalState(state), state).toBe(entry.phase === 'terminal');
      expect(presentationStepOfState(state), state).toBe(entry.step ?? 'terminal');
      expect(entry.step === null, state).toBe(entry.phase === 'terminal');
    }
  });
});
