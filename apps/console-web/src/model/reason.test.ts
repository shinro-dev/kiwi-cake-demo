// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The sentence a screen prints for a state name or a reason code, and the one rule that decides whether a number goes
// into it: the record carried that number, under that name, or the sentence states none. These tests pin the rule in
// both directions, pin that a code this console does not know still reaches the reader as itself, and hold the state
// glossary against the state names the controller actually emits, which is the only place that pairing is checked.

import { describe, expect, it } from 'vitest';
import { CONTROLLER_STATES } from './phase';
import {
  argsFromNamedValues,
  argText,
  buildReason,
  describeReasonCode,
  describeReasonCodeWith,
  describeState,
  fillTemplate,
  labelWithCode,
  NO_ARGS,
  reasonCodeLabel,
  STATES_WITH_A_SENTENCE,
} from './reason';
import type { CodeTemplate } from './reason';
import { initialState } from './reducer';
import type { ActionView, ViewState } from './types';

function action(extra: Partial<ActionView> = {}): ActionView {
  return {
    action_id: 'a1',
    action_type: 'pan_step',
    plan_only: false,
    parameters: [],
    phase: 'refused',
    result: null,
    duration_ns: null,
    refusal: { reason_code: 'step_out_of_bounds', detail: 'a pan step must be non-zero', before_motion: true },
    seq: 12,
    ...extra,
  };
}

function viewWith(extra: Partial<ViewState>): ViewState {
  return { ...initialState(), ...extra };
}

const TEMPLATE: CodeTemplate = {
  gloss: 'the margin was not met',
  needs: ['worst_mm', 'required_mm'],
  measured: (args) => `the margin was ${argText(args, 'worst_mm')}, against the ${argText(args, 'required_mm')} required`,
};

describe('a sentence that states a number', () => {
  it('states it only when the record carried every name the sentence uses', () => {
    const args = argsFromNamedValues([
      { name: 'worst_mm', value: 21.4, unit: 'mm' },
      { name: 'required_mm', value: 37.96, unit: 'mm' },
    ]);
    expect(fillTemplate(TEMPLATE, args)).toBe('the margin was 21.4 mm, against the 37.96 mm required');
  });

  it('falls back to the sentence without numbers when one of the names is not on the record', () => {
    const args = argsFromNamedValues([{ name: 'worst_mm', value: 21.4, unit: 'mm' }]);
    expect(fillTemplate(TEMPLATE, args)).toBe('the margin was not met');
  });

  it('treats a name that is present with a null value as not recorded, rather than printing a blank', () => {
    const args = argsFromNamedValues([
      { name: 'worst_mm', value: null, unit: 'mm' },
      { name: 'required_mm', value: 37.96, unit: 'mm' },
    ]);
    expect(fillTemplate(TEMPLATE, args)).toBe('the margin was not met');
  });

  it('says nothing at all for a code no glossary knows', () => {
    expect(fillTemplate(undefined, NO_ARGS)).toBeNull();
  });

  it('writes a value the way the record wrote it, with the unit the record named', () => {
    const args = argsFromNamedValues([
      { name: 'worst_mm', value: 21.4, unit: 'mm' },
      { name: 'closes', value: 3, unit: null },
      { name: 'inside', value: true, unit: null },
    ]);
    expect(argText(args, 'worst_mm')).toBe('21.4 mm');
    expect(argText(args, 'closes')).toBe('3');
    expect(argText(args, 'inside')).toBe('true');
    expect(argText(args, 'a_name_nobody_recorded')).toBe('unavailable');
  });
});

describe('a runtime reason code', () => {
  it('carries the sentence and the code, so the machine-readable word is never hidden', () => {
    expect(reasonCodeLabel('run_active')).toContain('run_active');
    expect(reasonCodeLabel('run_active')).toContain('a run is already in progress');
  });

  it('renders as the bare code when this console has no sentence for it', () => {
    expect(describeReasonCode('a_code_from_a_newer_runtime')).toBeNull();
    expect(reasonCodeLabel('a_code_from_a_newer_runtime')).toBe('a_code_from_a_newer_runtime');
    expect(labelWithCode(null, 'a_code_from_a_newer_runtime')).toBe('a_code_from_a_newer_runtime');
  });

  it('says nothing at all about a record that carries no code', () => {
    expect(reasonCodeLabel(null)).toBeNull();
    expect(describeReasonCodeWith(null, NO_ARGS)).toBeNull();
  });

  it('glosses the block reasons the runtime really writes, including the ones an operator meets first', () => {
    for (const code of ['owner_unresolved', 'owner_external', 'run_active', 'no_active_run', 'replay_only', 'hardware_commands_not_enabled']) {
      expect(describeReasonCode(code), code).not.toBeNull();
    }
  });
});

describe('the controller state glossary', () => {
  it('has a sentence for every state name the controller emits', () => {
    for (const state of CONTROLLER_STATES) expect(describeState(state), state).not.toBeNull();
  });

  it('glosses no state the controller does not emit', () => {
    for (const state of STATES_WITH_A_SENTENCE) expect(CONTROLLER_STATES, state).toContain(state);
  });

  it('has the operator words for the sweep and the selection', () => {
    expect(describeState('SEARCH_SWEEP')).toContain('Scanning for the block');
    expect(describeState('TARGET_SELECTED')).toContain('target');
  });

  it('no longer carries STRADDLE_FAILED, which the controller never emitted', () => {
    expect(describeState('STRADDLE_FAILED')).toBeNull();
  });

  it('prints a state name from a newer controller as itself', () => {
    const view = viewWith({ machine: { machine: 'single_cube', state: 'SOME_NEW_STATE', from_state: null, since_seq: 9, history: [] } });
    expect(buildReason(view).headline).toBe('The controller is in state SOME_NEW_STATE.');
  });
});

describe('the sentence on the task card', () => {
  it('lets a refusal explain itself, with the code and the record it was built from', () => {
    const reason = buildReason(viewWith({ current_action: action() }));
    expect(reason.headline).toBe('The controller refused pan_step.');
    expect(reason.detail).toContain('step_out_of_bounds');
    expect(reason.detail).toContain('Nothing had moved in this run when it refused.');
    expect(reason.basis).toEqual(['action.refused at seq 12']);
  });

  it('says there is no run rather than inventing a state', () => {
    expect(buildReason(initialState()).headline).toBe('No run is in progress.');
  });
});
