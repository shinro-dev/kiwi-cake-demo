// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE READINESS VERDICT, FROM THE GUARDS THAT DECIDE START (spec rule 4.2, audit C7). On 2026-09-27 the top bar said
// "Not ready" (the wrist preview was not configured) while Start was the highlighted primary button beside a block held
// in the gripper. The verdict is now computed from Start's own reason, so the two cannot disagree: Not ready if and only
// if Start is off for a reason of its own; the wrist and the arm, which the runtime does not gate Start on, never change
// it and are listed apart. The step words are spec rule 5.7.2.

import { describe, expect, it } from 'vitest';
import type { MissionRecord } from './missionRecord';
import type { ReadyItem } from './readyChecklist';
import { readiness } from './readiness';
import { missionSteps, nextStep } from './nextSteps';

function mission(state: string, extra: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1',
    mission_id: 'mission-f65c3cf8e99f',
    state,
    data_mode: 'live',
    performs_motion: true,
    execution_label: null,
    started_wall_utc: '2026-09-27T14:16:38.847Z',
    selected_target: 'cube',
    waiting_reason: null,
    detail: null,
    stop_requested: false,
    stop_requested_wall_utc: null,
    targets: [],
    activations: [],
    hold: null,
    next_actions: null,
    ...extra,
  };
}

const WRIST_UNMET: ReadyItem = { id: 'wrist', label: 'Wrist view', state: 'unmet', sentence: 'The wrist camera is not showing video: preview_not_configured.', hint: null };
const ARM_UNREAD: ReadyItem = { id: 'arm', label: 'Arm acquired', state: 'not_readable', sentence: 'not reported', hint: null };
const MOTION_MET: ReadyItem = { id: 'motion', label: 'Motion permitted', state: 'met', sentence: 'confirmed', hint: null };

describe('the readiness verdict', () => {
  it('is Ready, with Start primary, when Start is on and no mission is carried', () => {
    const verdict = readiness({ mission: null, runActive: false, startOff: null, checklist: [WRIST_UNMET, ARM_UNREAD, MOTION_MET] });
    expect(verdict.verdict.id).toBe('ready');
    expect(verdict.startPrimary).toBe(true);
  });

  it('is Not ready if and only if Start is off, with Start\'s own reason as the sentence', () => {
    const off = readiness({ mission: null, runActive: false, startOff: 'robot motion is held: robot_session_not_confirmed', checklist: [] });
    expect(off.verdict.id).toBe('not_ready');
    expect(off.sentence).toBe('Robot motion is held: robot_session_not_confirmed.');
    expect(off.startPrimary).toBe(false);
  });

  it('is never changed by the wrist view or the arm, which are listed as not checked by the runtime', () => {
    const without = readiness({ mission: null, runActive: false, startOff: null, checklist: [MOTION_MET] });
    const withWrist = readiness({ mission: null, runActive: false, startOff: null, checklist: [MOTION_MET, WRIST_UNMET, ARM_UNREAD] });
    expect(withWrist.verdict).toEqual(without.verdict);
    expect(withWrist.unchecked.map((item) => item.id)).toEqual(['wrist', 'arm']);
    expect(withWrist.checked.map((item) => item.id)).toEqual(['motion']);
  });

  it('says Steps first, never primary, for an ended mission whose list does not begin with start', () => {
    const failed = mission('FAILED', { next_actions: ['open_gripper_by_hand', 'return_arm', 'start'] });
    const verdict = readiness({ mission: failed, runActive: false, startOff: null, checklist: [] });
    expect(verdict.verdict.id).toBe('steps_first');
    expect(verdict.sentence).toBe('Open the gripper by hand and take the block out.');
    expect(verdict.startPrimary).toBe(false);
  });

  it('reads Ready again for a complete mission whose list begins with start', () => {
    const verdict = readiness({ mission: mission('COMPLETE', { next_actions: ['start'] }), runActive: false, startOff: null, checklist: [] });
    expect(verdict.verdict.id).toBe('ready');
    expect(verdict.startPrimary).toBe(true);
  });

  it('prefers Not ready to Steps first when Start is off', () => {
    const failed = mission('FAILED', { next_actions: ['open_gripper_by_hand', 'return_arm', 'start'] });
    expect(readiness({ mission: failed, runActive: false, startOff: 'the runtime is not answering', checklist: [] }).verdict.id).toBe('not_ready');
  });

  it('says Not ready while the runtime is not answering, even with a mission carried (rule 4.4, Disconnected)', () => {
    const verdict = readiness({ mission: mission('RUNNING'), runActive: true, startOff: 'the runtime is not answering since 14:21:14Z', checklist: [], disconnected: 'The runtime is not answering since 14:21:14Z.' });
    expect(verdict.verdict.id).toBe('not_ready');
    expect(verdict.sentence).toBe('The runtime is not answering since 14:21:14Z.');
  });

  it('names the mission word while a mission is carried or a run is active, and Start is never primary', () => {
    const off = 'a mission is carried';
    expect(readiness({ mission: mission('RUNNING'), runActive: true, startOff: off, checklist: [] }).verdict.id).toBe('running');
    expect(readiness({ mission: mission('WAITING_FOR_CAPABILITY', { waiting_reason: 'capability_unavailable' }), runActive: false, startOff: off, checklist: [] }).verdict.id).toBe('waiting_cake');
    expect(readiness({ mission: mission('WAITING_FOR_CAPABILITY', { waiting_reason: 'operator_decision' }), runActive: false, startOff: off, checklist: [] }).verdict.id).toBe('held_for_you');
    expect(readiness({ mission: null, runActive: true, startOff: off, checklist: [] }).verdict.id).toBe('running');
    expect(readiness({ mission: mission('RUNNING'), runActive: true, startOff: off, checklist: [] }).startPrimary).toBe(false);
  });
});

describe('the step words', () => {
  it('says each step of the runtime in the words of rule 5.7.2, with the by-hand tag and the control it names', () => {
    expect(nextStep('open_gripper_by_hand')).toEqual({ token: 'open_gripper_by_hand', words: 'Open the gripper by hand and take the block out.', byHand: true, control: null });
    expect(nextStep('return_base')).toEqual({ token: 'return_base', words: 'Move the base back to its start by hand.', byHand: true, control: null });
    expect(nextStep('return_arm')).toEqual({ token: 'return_arm', words: 'Return arm: the arm goes to the saved low scan pose.', byHand: false, control: 'return_arm' });
    expect(nextStep('start').words).toBe('Start a new mission with Start, at the top.');
    expect(nextStep('retry')).toEqual({ token: 'retry', words: 'Try again: the runtime parks the arm, then starts a fresh attempt.', byHand: false, control: 'retry' });
    expect(nextStep('move_block_then_retry')).toEqual({ token: 'move_block_then_retry', words: 'Move the block by hand, then Try again.', byHand: false, control: 'retry' });
    expect(nextStep('stop').words).toBe('Stop, at the top, ends the mission.');
    expect(nextStep('wave_flag').words).toBe('wave_flag');
  });

  it('reads the hold\'s list while held, else the mission\'s, in the runtime\'s order', () => {
    const held = mission('WAITING_FOR_CAPABILITY', {
      waiting_reason: 'operator_decision',
      next_actions: ['stop'],
      hold: { target_id: 'cube', attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'retryable', reason_code: 'x', phase: 'ALIGN', observation_ids: [], stop_state: null, next_actions: ['retry', 'stop'], budget_left: 1, retry_requested: false },
    });
    expect(missionSteps(held)).toEqual(['retry', 'stop']);
    expect(missionSteps(mission('FAILED', { next_actions: ['return_arm', 'start'] }))).toEqual(['return_arm', 'start']);
    expect(missionSteps(mission('FAILED'))).toEqual([]);
    expect(missionSteps(null)).toEqual([]);
  });
});
