// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import { describe, expect, it } from 'vitest';
import { chosenCandidate, directorStep } from './director';
import type { DirectorInput } from './director';
import { parseMission } from './missionRecord';
import type { MissionRecord } from './missionRecord';
import type { VlaStatus } from './vla';

const ID = 'mission-7f6e958dd257';
const IDLE = { activating: false, stopping: false, vlaBusy: false, commandInFlight: false };

function record(patch: Record<string, unknown>, attempt: Record<string, unknown> | null = null, activations: unknown[] = []): MissionRecord {
  const mission = parseMission({
    mission_record_version: '1', mission_id: ID, state: 'RUNNING', data_mode: 'live', performs_motion: true, execution_label: 'proven-single-cube-controller',
    started_wall_utc: '2026-10-11T15:12:40Z', selected_target: 'cube', waiting_reason: null, detail: null, activations,
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'ACTIVE', runs: ['run-1'], attempts: attempt === null ? [] : [attempt] }],
    ...patch,
  });
  if (mission === null) throw new Error('fixture did not parse');
  return mission;
}

const HOLDING = { attempt: 1, run_id: 'run-2', park_run_id: null, outcome_class: 'operator_needed', reason_code: 'hold_observe_no_pad_frame', phase: 'HOLD_OBSERVE',
  measurements: [], observation_ids: [], stop_state: { base_ahead: false, holding: true, moved: true }, next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-10-11T15:15:47Z' };
const EMPTY = { ...HOLDING, reason_code: 'grasp_missed', phase: 'GRASP_CLOSE', stop_state: { base_ahead: false, holding: false, moved: true } };

function input(patch: Partial<DirectorInput>): DirectorInput {
  return { armedMissionId: ID, mission: null, candidates: [{ key: 'mobile-demo-swapped', payload_digest: 'sha256:' + 'a'.repeat(64) }], vla: { state: 'standby' }, busy: IDLE, runActive: false, ...patch };
}

describe('the demo director', () => {
  it('directs nothing it did not start: no armed mission, or another mission', () => {
    expect(directorStep(input({ armedMissionId: null, mission: record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }) }))).toBeNull();
    expect(directorStep(input({ armedMissionId: 'mission-other', mission: record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }) }))).toBeNull();
    expect(directorStep(input({ mission: null }))).toBeNull();
  });

  it('activates the one prepared Plan while the mission waits for Cake, and only once', () => {
    const waiting = record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' });
    expect(directorStep(input({ mission: waiting }))).toEqual({ kind: 'activate', candidate_key: 'mobile-demo-swapped' });
    const submitted = record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }, null, [{ operation_uuid: 'op-1', candidate_key: 'mobile-demo-swapped', activation_state: 'SUBMITTED' }]);
    expect(directorStep(input({ mission: submitted }))?.kind).toBe('wait');
    const observed = record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }, null, [{ operation_uuid: 'op-1', candidate_key: 'mobile-demo-swapped', activation_state: 'OBSERVED' }]);
    expect(directorStep(input({ mission: observed }))?.kind).toBe('wait');
    expect(directorStep(input({ mission: waiting, busy: { ...IDLE, activating: true } }))?.kind).toBe('wait');
    expect(directorStep(input({ mission: waiting, candidates: null }))?.kind).toBe('wait');
  });

  it('picks the Plan served with the activate role, and refuses to guess between several', () => {
    expect(chosenCandidate([{ key: 'undo', payload_digest: 'x', role: 'undo' }, { key: 'swap', payload_digest: 'y', role: 'activate' }])?.key).toBe('swap');
    expect(chosenCandidate([{ key: 'a', payload_digest: 'x' }, { key: 'b', payload_digest: 'y' }])).toBeNull();
    const waiting = record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' });
    const step = directorStep(input({ mission: waiting, candidates: [{ key: 'a', payload_digest: 'x' }, { key: 'b', payload_digest: 'y' }] }));
    expect(step?.kind).toBe('failed');
  });

  it('stops the mission once it holds the block at the hold, and never a hold without the block', () => {
    const held = record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision' }, HOLDING);
    expect(directorStep(input({ mission: held }))).toEqual({ kind: 'stop' });
    expect(directorStep(input({ mission: record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision', stop_requested: true }, HOLDING) }))?.kind).toBe('wait');
    expect(directorStep(input({ mission: held, busy: { ...IDLE, stopping: true } }))?.kind).toBe('wait');
    expect(directorStep(input({ mission: record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision' }, EMPTY) }))?.kind).toBe('failed');
  });

  it('carries once the stopped mission holds the block and the runner is idle in standby', () => {
    const stopped = record({ state: 'STOPPED', stop_requested: true }, HOLDING);
    expect(directorStep(input({ mission: stopped }))).toEqual({ kind: 'carry' });
    expect(directorStep(input({ mission: stopped, busy: { ...IDLE, vlaBusy: true } }))?.kind).toBe('wait');
    expect(directorStep(input({ mission: stopped, runActive: true }))?.kind).toBe('wait');
    expect(directorStep(input({ mission: stopped, vla: { state: 'moving', events: [{ t: null, phase: 'moving_to_pose', pose: 'pad' }] } }))).toEqual({ kind: 'wait', why: 'carrying the block to the pad' });
    expect(directorStep(input({ mission: stopped, vla: { state: 'unreachable' } }))?.kind).toBe('wait');
    expect(directorStep(input({ mission: stopped, vla: { state: 'ready' } }))?.kind).toBe('failed');
    expect(directorStep(input({ mission: record({ state: 'STOPPED', stop_requested: true, detail: 'stopped while carrying a target' }, EMPTY) }))?.kind).toBe('failed');
  });

  it('is finished by the carry of THIS mission, and stops on a carry that did not finish', () => {
    const stopped = record({ state: 'STOPPED', stop_requested: true }, HOLDING);
    const done: VlaStatus = { state: 'standby', carry: { result: 'done', mission_id: ID, jaws_after_release: 79.8 } };
    expect(directorStep(input({ mission: stopped, vla: done }))).toEqual({ kind: 'finished' });
    const other: VlaStatus = { state: 'standby', carry: { result: 'done', mission_id: 'mission-old' } };
    expect(directorStep(input({ mission: stopped, vla: other }))).toEqual({ kind: 'carry' });
    const stalled: VlaStatus = { state: 'standby', carry: { result: 'refused', code: 'carry_base_stalled', message: 'the wheels did not take the forward pulse', mission_id: ID } };
    expect(directorStep(input({ mission: stopped, vla: stalled }))).toEqual({ kind: 'failed', why: 'the wheels did not take the forward pulse' });
    expect(directorStep(input({ mission: record({ state: 'COMPLETE' }) }))).toEqual({ kind: 'finished' });
  });
});
