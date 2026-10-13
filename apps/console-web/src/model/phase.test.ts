// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The grouping is a presentation decision taken over a controller enum, and a wrong group mislabels the mission
// without anybody noticing. Nothing in the contract pins the state names, so these tests are the only place a
// controller rename or a new state is caught: the 25 names are written out here on purpose.

import { describe, expect, it } from 'vitest';
import { CONTROLLER_STATES, isTerminalState, PHASES, phaseOfState, phaseProgress, presentationStepOfState } from './phase';
import type { PhaseInput, PhaseProgressView } from './phase';
import type { ActionView, MachineView, PredicateView } from './types';

/** The state names the controller emits, taken from its own `self.state(...)` call sites. */
const EMITTED_STATES = [
  'SEARCH',
  // The two mission-mode states: the sweep through the wrist's sectors when the low-pose frame shows no cube
  // (docs/autonomous-demo/one-block-demo-2026-09-26.md, T2), and the selection of the cube in view as the target with
  // its measured placement mode (run_single_cube_cycle.py, _mission_search).
  'SEARCH_SWEEP',
  'TARGET_SELECTED',
  'ARM_ONLY_INSUFFICIENT',
  'PREGRASP',
  'PREGRASP_BIAS_REDUCED',
  'ALIGN',
  'APPROACH',
  'STRADDLING',
  'GRASP_CLOSE',
  'GRASP_RETRY_ADVANCE',
  'HOLD_OBSERVE',
  'OBJECT_SECURED',
  'CARRY',
  'CARRY_REACH_LIMIT',
  'BASE_ADVANCE',
  'BASE_MEASURED',
  'PLACEMENT_PLAN',
  'RELEASE_ADMISSION',
  'PLACE_LOWER_REFUSED',
  'RELEASED',
  'PLACED',
  'BASE_RETURN',
  'BASE_RETURNED',
  'DONE_ALREADY_PLACED',
  // The stage policies' words (autonomy/manipulation/policy_*.py), each emitted only under the word that opens it:
  // align_v2 (track A), grasp_measured_retry and grasp_recover (track G), scene_scan and scene_search (track S).
  'ALIGN_RADIAL_STEP',
  'ALIGN_DESCENT',
  'ALIGN_RECAPTURE',
  'ALIGN_REACQUIRE',
  'GRASP_REOBSERVE',
  'GRASP_RETRY',
  'GRASP_SET_DOWN',
  'SCENE_SCAN',
  'SCENE',
  'SEARCH_SCENE',
  'SEARCH_SCENE_SELECTED',
  // SEARCH from the scene under the word multi_block (autonomy/manipulation/block_selection.py).
  'SEARCH_SCENE_LEAST_MOTION',
  // The additive placement policies of 2026-09-30 (autonomy/manipulation/placement_executor.py, reach_carry.py), each
  // emitted only under the words arm_reach and advance_then_reach.
  'PLACEMENT_POLICY',
  'REACH_CARRY',
  'REACH_CARRY_STOPPED',
  'REACH_REPLAN',
  'REACH_RETRACT',
  // Each bounded step of the way back of a loaded arm (reach_carry.py, THE WAY BACK UNDER LOAD, the lead's decision A of
  // 2026-10-01), emitted under the same words.
  'REACH_RETRACT_STEP',
  'PLACEMENT_FALLBACK',
  'RELEASE_HOLD',
];

function machine(states: [string, number][]): MachineView {
  const last = states[states.length - 1];
  return {
    machine: 'single_cube_cycle',
    state: last === undefined ? 'SEARCH' : last[0],
    from_state: null,
    since_seq: last === undefined ? 0 : last[1],
    history: states.map(([state, seq]) => ({ state, seq })),
  };
}

function predicate(name: string, value: boolean | null, seq = 50): PredicateView {
  return {
    predicate: name,
    value,
    state: null,
    reason_code: null,
    rule_ref: null,
    rule_digest: null,
    threshold: null,
    measured: null,
    unit: null,
    evidence: [],
    consumed_source_stamp: null,
    measurements: [],
    seq,
    t_mono_ns: seq * 1_000_000,
    clock_domain: 'laptop_monotonic',
  };
}

function labelledAction(label: string, seq: number): ActionView {
  return {
    action_id: `action-${String(seq)}`,
    action_type: 'arm_pose',
    plan_only: false,
    parameters: [{ name: 'label', value: label, unit: null }],
    phase: 'completed',
    result: 'ok',
    duration_ns: null,
    refusal: null,
    seq,
  };
}

/** A run in progress unless the case says otherwise, because that is when a phase can be in progress at all. The
 *  default fold SAW the run start, which is the ordinary case; a tab that opened midway sets run_started to null. */
function input(extra: Partial<PhaseInput> = {}): PhaseInput {
  return {
    machine: null,
    predicates: [],
    action_history: [],
    active_run: { run_id: 'run-a', command_id: null, command_type: null, status: 'running', state: null, started_seq: 1 },
    run_started: { command_id: 'c-a', command_type: 'start_cycle' as const, plan_only: false, holding: null, started_wall_utc: null },
    ...extra,
  };
}

describe('a tab that opened after the run had started', () => {
  it('does not claim a phase was never reached when it simply did not watch', () => {
    // The snapshot carries the state the controller is in NOW and no transitions before the tab's cursor. Reading
    // that absence as "Not yet" states something about the robot out of this tab's own ignorance.
    const joined = phaseProgress(input({ run_started: null, machine: machine([['CARRY', 400]]) }));
    expect(joined.rows.find((row) => row.phase === 'place')?.status).toBe('active');
    expect(joined.rows.find((row) => row.phase === 'locate')?.status).toBe('not_observed');
    expect(joined.rows.find((row) => row.phase === 'locate')?.words).toBe('Not seen from here');
  });

  it('still says Not yet when the fold did watch the run start', () => {
    const watched = phaseProgress(input({ machine: machine([['SEARCH', 2]]) }));
    expect(watched.rows.find((row) => row.phase === 'return')?.status).toBe('not_reached');
  });
});

function row(view: PhaseProgressView, phase: string) {
  const found = view.rows.find((item) => item.phase === phase);
  if (found === undefined) throw new Error(`no row for ${phase}`);
  return found;
}

describe('every state the controller emits has a place on the screen', () => {
  it('assigns all 45 of them and knows no others', () => {
    expect([...CONTROLLER_STATES].sort()).toEqual([...EMITTED_STATES].sort());
    expect(CONTROLLER_STATES).toHaveLength(45);
  });

  it('puts each one in the group the mission reads it in', () => {
    expect(phaseOfState('SEARCH')).toBe('locate');
    // The sweep and the selection are still the search: the rail chip stays on Find through both.
    expect(phaseOfState('SEARCH_SWEEP')).toBe('locate');
    expect(phaseOfState('TARGET_SELECTED')).toBe('locate');
    expect(presentationStepOfState('SEARCH_SWEEP')).toBe('find');
    expect(presentationStepOfState('TARGET_SELECTED')).toBe('find');
    expect(phaseOfState('PREGRASP')).toBe('align');
    expect(phaseOfState('PREGRASP_BIAS_REDUCED')).toBe('align');
    expect(phaseOfState('ALIGN')).toBe('align');
    expect(phaseOfState('APPROACH')).toBe('pick');
    expect(phaseOfState('STRADDLING')).toBe('pick');
    expect(phaseOfState('GRASP_CLOSE')).toBe('pick');
    expect(phaseOfState('GRASP_RETRY_ADVANCE')).toBe('pick');
    expect(phaseOfState('OBJECT_SECURED')).toBe('verify_grip');
    expect(phaseOfState('PLACEMENT_PLAN')).toBe('place');
    expect(phaseOfState('HOLD_OBSERVE')).toBe('place');
    expect(phaseOfState('CARRY')).toBe('place');
    expect(phaseOfState('CARRY_REACH_LIMIT')).toBe('place');
    expect(phaseOfState('ARM_ONLY_INSUFFICIENT')).toBe('place');
    expect(phaseOfState('BASE_MEASURED')).toBe('place');
    expect(phaseOfState('BASE_ADVANCE')).toBe('place');
    expect(phaseOfState('PLACE_LOWER_REFUSED')).toBe('place');
    expect(phaseOfState('RELEASE_ADMISSION')).toBe('place');
    expect(phaseOfState('RELEASED')).toBe('place');
    expect(phaseOfState('PLACED')).toBe('place');
    expect(phaseOfState('BASE_RETURN')).toBe('return');
    expect(phaseOfState('BASE_RETURNED')).toBe('return');
  });

  it('keeps the already placed no-op terminal, which is not one of the six', () => {
    expect(phaseOfState('DONE_ALREADY_PLACED')).toBe('terminal');
    expect(isTerminalState('DONE_ALREADY_PLACED')).toBe(true);
    expect(PHASES).not.toContain('terminal');
  });

  it('reports a state from a newer controller as unknown instead of guessing a group for it', () => {
    expect(phaseOfState('A_STATE_A_NEWER_CONTROLLER_EMITS')).toBe('unknown');
  });
});

describe('what the phase rows say about a run', () => {
  it('says not yet for every phase before anything has been recorded', () => {
    const view = phaseProgress(input({ active_run: null }));
    expect(view.rows.map((item) => item.status)).toEqual([
      'not_reached',
      'not_reached',
      'not_reached',
      'not_reached',
      'not_reached',
      'not_reached',
    ]);
    expect(view.rows[0]?.words).toBe('Not yet');
    expect(view.current).toBeNull();
    expect(view.terminal).toBeNull();
  });

  it('calls a phase entered, and not completed, while nothing has verified it', () => {
    const view = phaseProgress(input({ machine: machine([['SEARCH', 10], ['PREGRASP', 12], ['ALIGN', 14]]) }));
    expect(row(view, 'locate').status).toBe('entered');
    expect(row(view, 'locate').words).toBe('Entered');
    expect(row(view, 'locate').entered_at_seq).toBe(10);
    expect(row(view, 'align').status).toBe('active');
    expect(view.current).toBe('align');
    expect(row(view, 'pick').status).toBe('not_reached');
  });

  it('calls no phase in progress once the run is over, however the machine was left', () => {
    const states: [string, number][] = [['PLACED', 70]];
    expect(phaseProgress(input({ machine: machine(states) })).current).toBe('place');
    const ended = phaseProgress(input({ machine: machine(states), active_run: null }));
    expect(ended.current).toBeNull();
    expect(row(ended, 'place').status).toBe('entered');
  });

  it('calls a phase verified only on the verdict the controller measured for it', () => {
    const states: [string, number][] = [['GRASP_CLOSE', 30], ['OBJECT_SECURED', 32], ['PLACEMENT_PLAN', 34]];
    const verified = phaseProgress(input({ machine: machine(states), predicates: [predicate('object_secured', true, 33)] }));
    expect(row(verified, 'verify_grip').status).toBe('verified');
    expect(row(verified, 'verify_grip').decided_by).toEqual({ predicate: 'object_secured', value: true, seq: 33 });

    const failed = phaseProgress(input({ machine: machine(states), predicates: [predicate('object_secured', false, 33)] }));
    expect(row(failed, 'verify_grip').status).toBe('failed');

    // A predicate that was never evaluated is not a verdict, and entry alone does not become one.
    const unevaluated = phaseProgress(input({ machine: machine(states), predicates: [predicate('object_secured', null, 33)] }));
    expect(row(unevaluated, 'verify_grip').status).toBe('entered');
    expect(row(unevaluated, 'verify_grip').decided_by).toBeNull();
  });

  it('keeps a retried phase in progress rather than calling it failed on the attempt that failed', () => {
    const view = phaseProgress(
      input({ machine: machine([['ALIGN', 20], ['ALIGN', 22]]), predicates: [predicate('aligned', false, 21)] }),
    );
    expect(row(view, 'align').status).toBe('active');
    expect(row(view, 'align').decided_by?.value).toBe(false);
  });

  it('shows an attempt count where the controller records one, and null where it records none', () => {
    const view = phaseProgress(
      input({
        machine: machine([
          ['ALIGN', 20],
          ['ALIGN', 22],
          ['APPROACH', 24],
          ['STRADDLING', 25],
          ['GRASP_CLOSE', 26],
          ['GRASP_RETRY_ADVANCE', 27],
          ['GRASP_CLOSE', 28],
        ]),
      }),
    );
    expect(row(view, 'align').attempts).toBe(2);
    expect(row(view, 'pick').attempts).toBe(2);
    expect(row(view, 'place').attempts).toBeNull();
    // A retry does not send the mission backwards: the phases it already went through keep what they had.
    expect(row(view, 'locate').status).toBe('not_reached');
    expect(row(view, 'align').status).toBe('entered');
    expect(row(view, 'align').entered_at_seq).toBe(20);
    expect(row(view, 'pick').basis).toContain('2 x state.transition to GRASP_CLOSE');
  });

  it('enters verify grip on the lifts, which the controller records as actions and not as states', () => {
    const actions = [labelledAction('VERIFICATION lift', 40), labelledAction('CLEARANCE lift', 41), labelledAction('CONFIRM lift', 42)];
    const view = phaseProgress(input({ machine: machine([['GRASP_CLOSE', 38]]), action_history: actions }));
    expect(row(view, 'verify_grip').status).toBe('entered');
    expect(row(view, 'verify_grip').entered_at_seq).toBe(40);
    expect(row(view, 'verify_grip').basis).toContain('action arm_pose labelled VERIFICATION lift at seq 40');
  });

  it('enters return on the arm return, which has no state of its own', () => {
    const view = phaseProgress(
      input({ machine: machine([['PLACED', 60]]), action_history: [labelledAction('EXIT to the low scan pose', 62)] }),
    );
    expect(row(view, 'return').status).toBe('entered');
    expect(row(view, 'return').entered_at_seq).toBe(62);
  });

  it('takes the base return verdict even when the base never advanced and no return state was recorded', () => {
    const view = phaseProgress(input({ machine: machine([['PLACED', 60]]), predicates: [predicate('base_returned', true, 61)] }));
    expect(row(view, 'return').status).toBe('verified');
    expect(row(view, 'return').entered_at_seq).toBeNull();
  });

  it('keeps a placement with a failed return a partial outcome and not a finished mission', () => {
    const view = phaseProgress(
      input({
        machine: machine([['PLACED', 70], ['BASE_RETURN', 72], ['BASE_RETURNED', 74]]),
        predicates: [predicate('placed', true, 71), predicate('base_returned', false, 75)],
        active_run: null,
      }),
    );
    expect(row(view, 'place').status).toBe('verified');
    expect(row(view, 'return').status).toBe('failed');
    expect(row(view, 'return').words).toBe('Failed');
  });

  it('reports the already placed no-op as terminal rather than as a mission that ran', () => {
    const view = phaseProgress(input({ machine: machine([['SEARCH', 10], ['DONE_ALREADY_PLACED', 11]]) }));
    expect(view.terminal).toEqual({ state: 'DONE_ALREADY_PLACED', seq: 11 });
    expect(view.current).toBeNull();
    expect(row(view, 'place').status).toBe('not_reached');
    expect(row(view, 'return').status).toBe('not_reached');
  });

  it('carries an unknown state out to be rendered as itself instead of dropping it', () => {
    const view = phaseProgress(input({ machine: machine([['SEARCH', 10], ['A_NEW_STATE', 11]]) }));
    expect(view.unmapped).toEqual([{ state: 'A_NEW_STATE', seq: 11 }]);
    expect(view.current).toBeNull();
    expect(row(view, 'locate').status).toBe('entered');
  });

  it('shows the phase the controller is in even when the kept history no longer reaches back to its transition', () => {
    const view = phaseProgress(
      input({ machine: { machine: 'single_cube_cycle', state: 'CARRY', from_state: null, since_seq: 300, history: [] } }),
    );
    expect(view.current).toBe('place');
    expect(row(view, 'place').status).toBe('active');
    expect(row(view, 'place').entered_at_seq).toBe(300);
  });

  it('names the records every row was built from', () => {
    const view = phaseProgress(input({ machine: machine([['PLACED', 70]]), predicates: [predicate('placed', true, 71)] }));
    expect(row(view, 'place').basis).toEqual(['state.transition to PLACED at seq 70', 'predicate.evaluated placed at seq 71']);
  });
});
