// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RUNTIME'S DECISION AFTER A MISSION RUN, AS THE SIMULATION PLAYS IT. Each case is a case of the runtime's own tests
// (packages/demo-runtime/tests: test_mission_retry.py, test_mission_grasp_endings.py and the classify_outcome cases of
// test_mission_execution.py), written here as run ends and event lists.

import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { classifyOutcome, nextActions, retryAllowed, stopStateFromEvents } from './syntheticRuntime';
import type { RunEnd } from './syntheticRuntime';

function ev(kind: string, payload: Record<string, unknown>): ContractEvent {
  return { kind, payload } as unknown as ContractEvent;
}
const moved = ev('action.started', { action_type: 'arm_pose' });
const to = (state: string): ContractEvent => ev('state.transition', { machine: 'cycle', from_state: null, to_state: state });
const verdict = (predicate: string, value: boolean | null): ContractEvent => ev('predicate.evaluated', { predicate, value });

const failed = (reason: string, exit = 5): RunEnd => ({ outcome: 'failed', exit_status: exit, reason_code: reason, predicates: null });

describe('where a run left the robot', () => {
  it('reads an empty close that measured nothing held as nothing held', () => {
    expect(stopStateFromEvents([moved, to('GRASP_CLOSE'), verdict('closed_on_something', false)])).toEqual({ holding: false, base_ahead: false, moved: true });
  });

  it('reads a close with no verdict after it as unknown, never as empty', () => {
    expect(stopStateFromEvents([moved, to('GRASP_CLOSE')]).holding).toBeNull();
  });

  it('reads a secured cube as held until it is released', () => {
    expect(stopStateFromEvents([moved, verdict('object_secured', true)]).holding).toBe(true);
    expect(stopStateFromEvents([moved, verdict('object_secured', true), to('RELEASED')]).holding).toBe(false);
  });

  it('reads the base as ahead from BASE_ADVANCE until a base_returned verdict measures true', () => {
    expect(stopStateFromEvents([moved, to('BASE_ADVANCE')]).base_ahead).toBe(true);
    expect(stopStateFromEvents([moved, to('BASE_ADVANCE'), verdict('base_returned', true)]).base_ahead).toBe(false);
  });

  it('reads a refusal before any action as nothing moved', () => {
    expect(stopStateFromEvents([to('SEARCH'), to('TARGET_SELECTED')])).toEqual({ holding: false, base_ahead: false, moved: false });
  });
});

describe('the class an attempt comes to', () => {
  const home = { holding: false, base_ahead: false, moved: true };
  const still = { holding: false, base_ahead: false, moved: false };

  it('holds a lost block at ALIGN and an empty close as retryable', () => {
    expect(classifyOutcome(failed('align_cube_not_detected'), home)).toBe('retryable');
    expect(classifyOutcome(failed('grasp_missed'), home)).toBe('retryable');
  });

  it('holds a near block refused before any motion as needing the operator', () => {
    expect(classifyOutcome(failed('search_cube_inside_pregrasp_reach'), still)).toBe('operator_needed');
    expect(classifyOutcome(failed('search_cube_inside_pregrasp_reach'), home)).toBe('fatal');
  });

  it("holds the binding's refusals for the operator, with no Retry after them (mission_execution.NO_RETRY_CODES)", () => {
    expect(classifyOutcome(failed('mission_binding_candidate_not_placeable'), still)).toBe('operator_needed');
    expect(classifyOutcome(failed('mission_binding_no_candidate'), still)).toBe('operator_needed');
    expect(retryAllowed('mission_binding_candidate_not_placeable')).toBe(false);
    expect(retryAllowed('mission_binding_no_candidate')).toBe(false);
  });

  it('holds a lift or approach refused part way, and a block inside the grasp radius, with no Retry after them', () => {
    for (const code of ['grasp_retry_lift_refused', 'grasp_set_down_lift_refused', 'grasp_retry_approach_refused', 'align_inside_grasp_radius']) {
      expect(classifyOutcome(failed(code), home), code).toBe('operator_needed');
      expect(retryAllowed(code), code).toBe(false);
    }
    expect(retryAllowed('align_cube_not_detected')).toBe(true);
    expect(retryAllowed('retention_not_demonstrated_set_down')).toBe(true);
    expect(classifyOutcome(failed('retention_not_demonstrated_set_down'), home)).toBe('retryable');
  });

  it("holds the scene scan's block endings for the block to be arranged first", () => {
    expect(classifyOutcome(failed('search_scene_no_block'), home)).toBe('operator_needed');
    expect(classifyOutcome(failed('search_scene_not_one_unplaced_block'), home)).toBe('operator_needed');
  });

  it('never retries with a cube that may be held, and ends on an unknown state', () => {
    expect(classifyOutcome(failed('grasp_missed'), { ...home, holding: true })).toBe('operator_needed');
    expect(classifyOutcome(failed('grasp_missed'), { ...home, holding: null })).toBe('fatal');
  });

  it('reads a stop and a placed run as they are', () => {
    expect(classifyOutcome({ outcome: 'stopped', exit_status: null, reason_code: null, predicates: null }, home)).toBe('stopped');
    expect(classifyOutcome({ outcome: 'placed_new', exit_status: 0, reason_code: null, predicates: { object_secured: true, placed: true, base_returned: true } }, home)).toBe('placed');
  });
});

describe('what a press can do next', () => {
  const home = { holding: false, base_ahead: false, moved: true };

  it('offers one retry of the right kind while the budget lasts, then only Stop', () => {
    expect(nextActions('retryable', home, true)).toEqual(['retry', 'stop']);
    expect(nextActions('operator_needed', { ...home, moved: false }, true)).toEqual(['move_block_then_retry', 'stop']);
    expect(nextActions('retryable', home, false)).toEqual(['stop']);
    expect(nextActions('operator_needed', { ...home, holding: true }, true)).toEqual(['stop']);
  });

  it('after the end, lists what is done by hand or by command, the worst reading taken for an unknown part', () => {
    expect(nextActions('fatal', { holding: false, base_ahead: false, moved: false }, false)).toEqual(['start']);
    expect(nextActions('stopped', home, false)).toEqual(['return_arm', 'start']);
    expect(nextActions('fatal', null, false)).toEqual(['open_gripper_by_hand', 'return_arm', 'start']);
  });
});
