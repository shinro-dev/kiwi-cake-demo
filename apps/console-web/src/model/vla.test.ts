// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import { describe, expect, it } from 'vitest';
import { missionHold, vlaStartBlock, vlaView } from './vla';
import { parseMission } from './missionRecord';
import type { VlaStatus } from './vla';

const sha = 'a8e140720bf3cdfa' + '0'.repeat(48);

describe('vlaView', () => {
  it('before the mission offers the mission start pose; after it, the swap', () => {
    const before = vlaView({ state: 'standby' }, false, true);
    expect(before.visible).toBe(true);
    expect(before.actions).toEqual(['to_mission_pose']);
    expect(before.headline).toBe('Before the mission: bring the arm to its start pose');
    const v = vlaView({ state: 'standby', model_sha256: sha }, true, true);
    expect(v.actions).toEqual(['swap']);
    expect(v.headline).toBe('Swap to the VLA policy');
  });

  it('while the arm moves to a start pose: says which, offers Stop only, and keeps the mission controls off', () => {
    const v = vlaView({ state: 'moving', events: [{ t: 1, phase: 'moving_to_pose', pose: 'vla' } as never] }, true, true);
    expect(v.headline).toBe('Moving the arm to the VLA start pose');
    expect(v.actions).toEqual(['stop']);
    expect(vlaStartBlock({ state: 'moving' }, false)).toBe('the arm is moving to a start pose; wait for it');
  });

  it('never says Cake swapped the module', () => {
    for (const state of ['standby', 'loading', 'ready', 'running', 'moving'] as const) {
      const v = vlaView({ state, model_sha256: sha, events: [] }, true, true);
      expect(`${v.headline} ${v.detail}`.toLowerCase()).not.toContain('cake');
    }
  });

  it('offers Run only when ready and the runtime is idle', () => {
    expect(vlaView({ state: 'ready', model_sha256: sha }, true, true).actions).toEqual(['run', 'unswap']);
    expect(vlaView({ state: 'ready', model_sha256: sha }, true, false).actions).toEqual(['unswap']);
  });

  it('shows thinking and the chunk while running, with Stop only', () => {
    const v = vlaView({ state: 'running', model_sha256: sha, events: [{ t: 30, phase: 'thinking', chunk: 3 }] }, true, true);
    expect(v.actions).toEqual(['stop']);
    expect(v.detail).toContain('chunk 3');
    expect(v.tone).toBe('busy');
  });

  it('reports an imagined grasp as a failure, never as placed', () => {
    const v = vlaView({ state: 'ready', model_sha256: sha, outcome: { result: 'refused', code: 'retention_not_demonstrated', message: 'x' } }, true, true);
    expect(v.tone).toBe('bad');
    expect(v.headline.toLowerCase()).toContain('not in the jaws');
    expect(v.headline.toLowerCase()).not.toContain('placed');
  });

  it('says the runner is not answering and offers nothing when unreachable', () => {
    const v = vlaView({ state: 'unreachable' }, true, true);
    expect(v.actions).toEqual([]);
    expect(v.headline).toContain('not answering');
  });

  it('stays visible while running even if the mission record changes', () => {
    expect(vlaView({ state: 'running', events: [] }, false, false).visible).toBe(true);
  });
  it('says when the jaws were opened by rule, not by the policy', () => {
    const rule = vlaView({ state: 'ready', model_sha256: sha, outcome: { result: 'done', release_by: 'rule', object_secured: true, chunks: 9, seconds: 128 } }, true, true);
    expect(rule.tone).toBe('good');
    expect(rule.headline).toBe('The VLA policy carried the block to the pad');
    expect(rule.detail).toContain('jaws opened by rule');
    const own = vlaView({ state: 'ready', model_sha256: sha, outcome: { result: 'done', release_by: 'policy', object_secured: true, chunks: 9, seconds: 128 } }, true, true);
    expect(own.headline).toBe('The VLA policy picked and released the block');
  });
  it('says why the robot runner did not take the last press', () => {
    const v = vlaView({ state: 'ready', model_sha256: sha }, true, true, 'vla_runtime_busy: a mission run is active');
    expect(v.detail).toBe('Not taken: vla_runtime_busy: a mission run is active');
    expect(v.tone).toBe('bad');
    expect(v.actions).toEqual(['run', 'unswap']);
  });
});

describe('vlaStartBlock', () => {
  it('keeps the mission controls off while the VLA policy loads or drives the arm', () => {
    expect(vlaStartBlock({ state: 'running' }, false)).toBe('the VLA policy is driving the arm; stop it first');
    expect(vlaStartBlock({ state: 'loading' }, false)).toBe('the VLA policy is loading on the robot; wait for it');
    for (const state of ['standby', 'ready', 'error', 'absent'] as const) expect(vlaStartBlock({ state }, false)).toBeNull();
    expect(vlaStartBlock(null, false)).toBeNull();
  });

  it('a runner last seen driving that stops answering may still be driving: the controls stay off', () => {
    expect(vlaStartBlock({ state: 'unreachable' }, true)).toBe('the VLA runner stopped answering while it was driving the arm; it may still be driving');
    expect(vlaStartBlock({ state: 'unreachable' }, false)).toBeNull();
  });
});

describe('vlaView, the words of the robot-side codes', () => {
  it('draws nothing on a console without the VLA door', () => {
    expect(vlaView({ state: 'absent' }, true, true).visible).toBe(false);
  });

  it('says a regrasp as one', () => {
    const v = vlaView({ state: 'running', events: [{ t: 9, phase: 'reopened' }] }, true, true);
    expect(v.detail).toContain('reopened the jaws to try again');
  });

  it('says an unconfirmed grip, a runtime that took the arm and a dead-man stop in plain words', () => {
    const ready = (outcome: VlaStatus['outcome'], events: VlaStatus['events'] = []) => vlaView({ state: 'ready', model_sha256: sha, outcome, events }, true, true);
    expect(ready({ result: 'refused', code: 'vla_grip_not_verified', message: 'x' }).headline).toBe('The grip was never confirmed, so the block was not let go');
    expect(ready({ result: 'refused', code: 'vla_runtime_busy', message: 'x' }).headline).toBe('The mission runtime took the arm, so the VLA run stopped');
    expect(ready({ result: 'stopped', message: 'x' }, [{ t: null, phase: 'deadman' }]).headline).toBe('Stopped: the console stopped answering the robot');
  });
});

// MISSION 1's END (operator, 2026-10-11): the scripted mission picks the block and stops at HOLD_OBSERVE holding it; the
// operator stops it and presses Carry; the carry names the mission it finished, and only then does the panel say so.
function missionHolding(state: string, holding: boolean | null, missionId = 'mission-31e2813f6d21') {
  return parseMission({
    mission_record_version: '1', mission_id: missionId, state, data_mode: 'live', performs_motion: true, execution_label: null,
    started_wall_utc: '2026-10-11T12:35:39Z', selected_target: 'cube', waiting_reason: null, detail: null, activations: [],
    targets: [{
      target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: state === 'STOPPED' ? 'STOPPED' : 'WAITING_FOR_CAPABILITY',
      runs: ['console-20261011T123539Z-mc4a3f30', 'console-20261011T123601Z-m48fbbe3'],
      attempts: [{ attempt: 1, run_id: 'console-20261011T123601Z-m48fbbe3', park_run_id: null, outcome_class: 'operator_needed', reason_code: 'hold_observe_no_pad_frame',
        phase: 'HOLD_OBSERVE', measurements: [], observation_ids: [], stop_state: { base_ahead: false, holding, moved: true }, next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-10-11T12:38:27.657Z' }],
    }],
  });
}

describe('vlaView, mission 1 finished by the scripted carry', () => {
  it('reads from the mission record whether the mission ended holding the block', () => {
    expect(missionHold(missionHolding('STOPPED', true))).toEqual({ missionId: 'mission-31e2813f6d21', holding: true, ended: true });
    expect(missionHold(missionHolding('WAITING_FOR_CAPABILITY', true))).toEqual({ missionId: 'mission-31e2813f6d21', holding: true, ended: false });
    expect(missionHold(missionHolding('STOPPED', false)).holding).toBe(false);
    expect(missionHold(null)).toEqual({ missionId: null, holding: false, ended: false });
  });

  it('a stopped mission that holds the block offers the carry, and nothing else', () => {
    const v = vlaView({ state: 'standby', model_sha256: sha }, false, true, null, missionHold(missionHolding('STOPPED', true)));
    expect(v.headline).toBe('The mission holds the block');
    expect(v.actions).toEqual(['carry']);
  });

  it('while the mission still waits holding the block, it asks for Stop first and offers no press', () => {
    const v = vlaView({ state: 'standby' }, false, true, null, missionHold(missionHolding('WAITING_FOR_CAPABILITY', true)));
    expect(v.actions).toEqual([]);
    expect(v.detail).toContain('Stop the mission');
  });

  it('after the carry of THIS mission: mission 1 finished, then the swap', () => {
    const hold = missionHold(missionHolding('STOPPED', true));
    const v = vlaView({ state: 'standby', model_sha256: sha, carry: { result: 'done', mission_id: 'mission-31e2813f6d21', jaws_after_release: 79.8 } }, false, true, null, hold);
    expect(v.headline).toBe('Mission 1 finished');
    expect(v.actions).toEqual(['swap']);
    expect(v.tone).toBe('good');
    expect(`${v.headline} ${v.detail}`.toLowerCase()).not.toContain('cake');
  });

  it('a carry of another mission says nothing about this one', () => {
    const hold = missionHold(missionHolding('STOPPED', true, 'mission-new'));
    const v = vlaView({ state: 'standby', carry: { result: 'done', mission_id: 'mission-31e2813f6d21' } }, false, true, null, hold);
    expect(v.headline).toBe('The mission holds the block');
    expect(v.actions).toEqual(['carry']);
  });

  it('a carry that did not finish says why and offers the mission start pose', () => {
    const hold = missionHold(missionHolding('STOPPED', true));
    const v = vlaView({ state: 'standby', carry: { result: 'refused', code: 'carry_nothing_held', message: 'the jaws read 79.80: they hold nothing, so nothing is carried', mission_id: 'mission-31e2813f6d21' } }, false, true, null, hold);
    expect(v.headline).toBe('The carry did not finish');
    expect(v.detail).toContain('they hold nothing');
    expect(v.actions).toEqual(['to_mission_pose']);
    expect(v.tone).toBe('bad');
  });

  it('while carrying: says so, offers Stop, and the mission Start is off for that reason', () => {
    const status: VlaStatus = { state: 'moving', events: [{ t: null, phase: 'moving_to_pose', pose: 'pad' }] };
    const v = vlaView(status, false, true);
    expect(v.headline).toBe('Carrying the block to the pad');
    expect(v.actions).toEqual(['stop']);
    expect(vlaStartBlock(status, false)).toBe('the robot is carrying the block to the pad; wait for it');
  });

  it('a mission that completed by itself still offers the swap as before', () => {
    expect(vlaView({ state: 'standby', model_sha256: sha }, true, true, null, missionHold(null)).actions).toEqual(['swap']);
  });
});
