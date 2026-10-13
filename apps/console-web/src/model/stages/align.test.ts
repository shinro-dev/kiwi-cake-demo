// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The align stage file, read the way the screen reads it. It is imported FIRST, before any model file, on purpose: the
// stage files and the three tables that read them must not import each other, and a stage file that did would fail
// here. The track that owns this stage adds its own cases to this file.

import { describe, expect, it } from 'vitest';
import { ALIGN_STAGE, ALIGN_V2_ADDITIONS } from './align';
import type { TemplateArgs } from './stage';
import { fillTemplate } from './stage';
import { describeFailureCode, describeFailureCodeWith } from '../failure';
import { isTerminalState, phaseOfState, presentationStepOfState } from '../phase';
import { describeState } from '../reason';

describe('the align stage file', () => {
  it('is the align stage', () => {
    expect(ALIGN_STAGE.stage).toBe('align');
  });

  it('is what the failure table reads for each of its codes', () => {
    for (const [code, template] of Object.entries(ALIGN_STAGE.failures)) {
      expect(describeFailureCode(code), code).toBe(template.gloss);
      expect(describeFailureCodeWith(code, {}), code).toBe(template.gloss);
      expect(template.gloss.length, code).toBeGreaterThan(0);
    }
  });

  it('is what the state sentence, the phase and the step read for each of its states', () => {
    for (const [state, entry] of Object.entries(ALIGN_STAGE.states)) {
      expect(describeState(state), state).toBe(entry.sentence);
      expect(phaseOfState(state), state).toBe(entry.phase);
      expect(isTerminalState(state), state).toBe(entry.phase === 'terminal');
      expect(presentationStepOfState(state), state).toBe(entry.step ?? 'terminal');
      expect(entry.step === null, state).toBe(entry.phase === 'terminal');
    }
  });
});

describe('the align_v2 additions', () => {
  const values = (entries: Record<string, number | string>, unit: string | null = 'mm'): TemplateArgs =>
    Object.fromEntries(Object.entries(entries).map(([name, value]) => [name, { value, unit }]));

  it('are held by the align stage, so the screen reads them', () => {
    for (const [code, template] of Object.entries(ALIGN_V2_ADDITIONS.failures)) expect(describeFailureCode(code), code).toBe(template.gloss);
    for (const [state, entry] of Object.entries(ALIGN_V2_ADDITIONS.states)) {
      expect(describeState(state), state).toBe(entry.sentence);
      expect(presentationStepOfState(state), state).toBe('pick');
    }
  });

  it('state the recorded values when the record carries them, and the gloss when it does not', () => {
    const lost = ALIGN_V2_ADDITIONS.failures['align_cube_lost_after_reacquire'];
    expect(fillTemplate(lost, { ...values({ tool_up_mm: 37.3 }), ...values({ reacquires: 2 }, null) })).toBe(
      'the block left the view and did not come back after 2 steps back; the arm holds 37.3 mm above the grasp height, clear of it',
    );
    expect(fillTemplate(lost, {})).toBe(lost?.gloss);
    const view = ALIGN_V2_ADDITIONS.failures['align_descent_would_lose_view'];
    expect(fillTemplate(view, values({ predicted_top_px: -91, view_margin_px: 40 }, 'px'))).toBe(
      'the descent would have left the block -91 px from the top of the view, under the 40 px margin, so it was not commanded; nothing was picked up',
    );
    const band = ALIGN_V2_ADDITIONS.failures['align_radial_not_in_band'];
    expect(fillTemplate(band, values({ radial_mm: 12.4, radial_band_mm: 5 }))).toContain('12.4 mm radially, outside the 5 mm band');
    for (const template of Object.values(ALIGN_V2_ADDITIONS.failures)) expect(template.gloss.length).toBeGreaterThan(0);
  });

  it('put every new state in the align phase, drawn under the pick step', () => {
    for (const entry of Object.values(ALIGN_V2_ADDITIONS.states)) {
      expect(entry.phase).toBe('align');
      expect(entry.step).toBe('pick');
      expect(entry.sentence.length).toBeGreaterThan(0);
    }
  });
});

describe('the pad policy of 2026-09-29 in the align stage', () => {
  it('align_more_than_one_cube_verified says blocks on the pad count too (a named residual)', () => {
    expect(describeFailureCode('align_more_than_one_cube_verified')).toBe(
      'more than one block was verified in the pregrasp view (blocks on the pad count too), and a mission target is approached only when its block is the one block in view, so nothing was picked up',
    );
  });
});
