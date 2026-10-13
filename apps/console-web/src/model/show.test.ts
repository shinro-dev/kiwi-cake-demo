// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import { describe, expect, it } from 'vitest';
import { parseMission } from './missionRecord';
import type { MissionRecord } from './missionRecord';
import { enableVla, mission1Stages, mission2Stages, showPhase, showWords, startRoute, vlaStartOff } from './show';
import { foldEvents, initialState } from './reducer';
import { enter, finished, started, verdict } from './stageFixtures';
import { stageTimeline } from './stageTimeline';
import type { VlaEvent, VlaStatus } from './vla';

const ID = 'mission-7f6e958dd257';
const HOLDING = { attempt: 1, run_id: 'h', park_run_id: null, outcome_class: 'operator_needed', reason_code: 'hold_observe_no_pad_frame', phase: 'HOLD_OBSERVE',
  measurements: [], observation_ids: [], stop_state: { base_ahead: false, holding: true, moved: true }, next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-10-11T15:15:47Z' };

function mission(state: string, attempt: Record<string, unknown> | null = HOLDING): MissionRecord {
  const record = parseMission({
    mission_record_version: '1', mission_id: ID, state, data_mode: 'live', performs_motion: true, execution_label: null, started_wall_utc: '2026-10-11T15:12:40Z',
    selected_target: 'cube', waiting_reason: null, detail: null, activations: [], stop_requested: state === 'STOPPED',
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state, runs: ['h'], attempts: attempt === null ? [] : [attempt] }],
  });
  if (record === null) throw new Error('fixture');
  return record;
}

/** The controller's own rows for a run that picked the block and ended at HOLD_OBSERVE, as the live record of 2026-10-11 did. */
function holdObserveRows() {
  const events = [
    started('h'), enter('h', 'SEARCH'), enter('h', 'TARGET_SELECTED', 'SEARCH'), enter('h', 'PREGRASP', 'TARGET_SELECTED'), enter('h', 'ALIGN', 'PREGRASP'),
    enter('h', 'GRASP_CLOSE', 'ALIGN'), verdict('h', 'closed_on_something', { value: true }), enter('h', 'OBJECT_SECURED', 'GRASP_CLOSE'),
    verdict('h', 'object_secured', { value: true, measured: 0.965, threshold: 0.5 }), enter('h', 'PLACEMENT_PLAN', 'OBJECT_SECURED'), enter('h', 'HOLD_OBSERVE', 'PLACEMENT_PLAN'),
    verdict('h', 'pad_frame_valid_while_holding', { value: false }),
    finished('h', 'failed', { code: 'hold_observe_no_pad_frame', phase: 'HOLD_OBSERVE', detail: 'HOLD_OBSERVE: PAD_FRAME is not valid', measurements: [{ name: 'anchor_margin_mm', value: -88.5, unit: 'mm' }], frames: [] }),
  ];
  const view = foldEvents(initialState(), events);
  return stageTimeline([{ run_id: 'h', view, events }], null, null).rows;
}

const statuses = (rows: { status: string }[]) => rows.map((row) => row.status);
const ev = (phase: string, extra: Record<string, unknown> = {}): VlaEvent => ({ t: null, phase, ...extra }) as VlaEvent;

describe('mission 1 stages', () => {
  it('reads the designed stop at HOLD_OBSERVE as the hand-over to the carry, never as a failed carry', () => {
    const controller = holdObserveRows();
    expect(statuses(controller)).toEqual(['entered', 'verified', 'failed', 'not_reached', 'not_reached']);
    const rows = mission1Stages(controller, mission('WAITING_FOR_CAPABILITY'), { state: 'standby' });
    expect(statuses(rows)).toEqual(['entered', 'verified', 'entered', 'not_reached', 'not_reached']);
    expect(rows[2]?.words).toBe('Picked; the carry takes over');
    expect(rows[2]?.failure_code).toBeNull();
  });

  it('follows the runner carry: forward pulses, the release, the return', () => {
    const controller = holdObserveRows();
    const carrying: VlaStatus = { state: 'moving', events: [ev('moving_to_pose', { pose: 'pad' }), ev('carry_start', { jaws: 67.77 }), ev('carry_base', { direction: 'forward', pulse: 1, measured_m: 0.051 })] };
    const mid = mission1Stages(controller, mission('STOPPED'), carrying);
    expect(statuses(mid)).toEqual(['entered', 'verified', 'active', 'not_reached', 'not_reached']);
    expect(mid[2]?.words).toBe('Forward pulse 1: 0.05 m');
    const done: VlaStatus = { state: 'standby', carry: { result: 'done', mission_id: ID }, events: [...(carrying.events ?? []), ev('carry_base', { direction: 'forward', pulse: 2, measured_m: 0.05 }), ev('carry_released', { jaws: 79.8 }),
      ev('carry_base', { direction: 'backward', pulse: 1, measured_m: 0.05 }), ev('carry_base', { direction: 'backward', pulse: 2, measured_m: 0.049 }), ev('carry_done')] };
    expect(statuses(mission1Stages(controller, mission('STOPPED'), done))).toEqual(['entered', 'verified', 'verified', 'verified', 'verified']);
    const stalled: VlaStatus = { state: 'standby', carry: { result: 'refused', code: 'carry_base_stalled', mission_id: ID }, events: [ev('moving_to_pose', { pose: 'pad' }), ev('carry_start'), ev('carry_base', { direction: 'forward', pulse: 1, measured_m: 0 }), ev('carry_refused', { code: 'carry_base_stalled', message: 'the wheels did not take the forward pulse' })] };
    const rows = mission1Stages(controller, mission('STOPPED'), stalled);
    expect(rows[2]?.status).toBe('refused');
    expect(rows[2]?.words).toBe('the wheels did not take the forward pulse');
  });

  it('after a reload with no events left, a recorded carry of this mission still verifies the last three stages', () => {
    const rows = mission1Stages(holdObserveRows(), mission('STOPPED'), { state: 'standby', carry: { result: 'done', mission_id: ID }, events: [] });
    expect(statuses(rows)).toEqual(['entered', 'verified', 'verified', 'verified', 'verified']);
    const other = mission1Stages(holdObserveRows(), mission('STOPPED'), { state: 'standby', carry: { result: 'done', mission_id: 'mission-old' }, events: [] });
    expect(statuses(other)).toEqual(['entered', 'verified', 'entered', 'not_reached', 'not_reached']);
  });
});

describe('mission 2 stages', () => {
  it('follows the episode: looking, found, grip confirmed, carried, released, returned', () => {
    const events = [ev('moving_to_start'), ev('at_start'), ev('thinking', { chunk: 1 }), ev('moving', { chunk: 1 }), ev('latched'), ev('object_secured', { value: true, score: 0.978 }),
      ev('thinking', { chunk: 9 }), ev('released', { by: 'rule' }), ev('returning'), ev('done')];
    const rows = mission2Stages({ state: 'ready', events });
    expect(statuses(rows)).toEqual(['verified', 'verified', 'verified', 'verified', 'verified']);
    expect(rows[1]?.words).toBe('Grip confirmed (0.98)');
    const midway = mission2Stages({ state: 'running', events: events.slice(0, 7) });
    expect(statuses(midway)).toEqual(['verified', 'verified', 'active', 'not_reached', 'not_reached']);
    expect(midway[2]?.words).toBe('Carrying, chunk 9');
  });

  it('says an empty close as a retry inside Pick, and a refusal on the stage it happened in', () => {
    const retry = mission2Stages({ state: 'running', events: [ev('at_start'), ev('latched'), ev('grip_empty', { gripper: 67.357 }), ev('thinking', { chunk: 4 })] });
    expect(statuses(retry)).toEqual(['verified', 'active', 'not_reached', 'not_reached', 'not_reached']);
    expect(retry[1]?.words).toBe('Trying again, chunk 4');
    const refused = mission2Stages({ state: 'ready', events: [ev('at_start'), ev('thinking', { chunk: 40 }), ev('refused', { code: 'vla_time_bound', message: 'the stage time bound passed' })] });
    expect(refused[0]?.status).toBe('refused');
    expect(refused[0]?.failure_code).toBe('vla_time_bound');
  });
});

describe('the words, the Enable VLA button and the Start route', () => {
  it('names mission 1 by the director step and mission 2 by the runner', () => {
    expect(showWords({ phase: 'mission1', mission: null, vla: { state: 'standby' }, step: null, runtimeIdle: true, vlaRefusal: null })).toEqual({ title: 'Mission 1: scripted pick and place', status: 'Press Start.', tone: 'neutral' });
    expect(showWords({ phase: 'mission1', mission: mission('STOPPED'), vla: { state: 'standby' }, step: { kind: 'carry' }, runtimeIdle: true, vlaRefusal: null }).status).toBe('Carrying the block to the pad.');
    expect(showWords({ phase: 'mission1', mission: mission('STOPPED'), vla: { state: 'standby' }, step: { kind: 'finished' }, runtimeIdle: true, vlaRefusal: null }).title).toBe('Mission 1 complete');
    expect(showWords({ phase: 'mission2', mission: null, vla: { state: 'ready', model_sha256: 'a'.repeat(64) }, step: null, runtimeIdle: true, vlaRefusal: null }).status).toContain('Put the block back');
    const done = showWords({ phase: 'mission2', mission: null, vla: { state: 'ready', outcome: { result: 'done', release_by: 'rule', object_secured: true, chunks: 18, seconds: 271 } }, step: null, runtimeIdle: true, vlaRefusal: null });
    expect(done.title).toBe('Mission 2 complete');
    expect(done.tone).toBe('good');
    for (const words of [done, showWords({ phase: 'mission1', mission: null, vla: null, step: null, runtimeIdle: true, vlaRefusal: null })]) expect(`${words.title} ${words.status}`.toLowerCase()).not.toContain('cake swapped');
  });

  it('offers Enable VLA only in standby with the runtime idle, then records it as enabled', () => {
    expect(enableVla({ vla: { state: 'standby' }, missionRunning: false, runActive: false, vlaBusy: false })).toEqual({ label: 'Enable VLA', off: null, busy: false, done: false });
    expect(enableVla({ vla: { state: 'standby' }, missionRunning: true, runActive: false, vlaBusy: false }).off).toContain('mission is running');
    expect(enableVla({ vla: { state: 'loading' }, missionRunning: false, runActive: false, vlaBusy: false })).toMatchObject({ label: 'Enabling VLA', busy: true });
    expect(enableVla({ vla: { state: 'ready' }, missionRunning: false, runActive: false, vlaBusy: false })).toMatchObject({ label: 'VLA enabled', done: true });
    expect(enableVla({ vla: null, missionRunning: false, runActive: false, vlaBusy: false }).off).not.toBeNull();
  });

  it('routes Start to the runner once the module is enabled', () => {
    expect(startRoute({ state: 'standby' })).toBe('mission');
    expect(startRoute({ state: 'ready' })).toBe('vla');
    expect(vlaStartOff({ state: 'ready' }, true, false)).toBeNull();
    expect(vlaStartOff({ state: 'ready' }, false, false)).toContain('holds the arm');
    expect(vlaStartOff({ state: 'loading' }, true, false)).toContain('loading');
    expect(vlaStartOff({ state: 'running' }, true, false)).toContain('driving the arm');
    expect(showPhase({ state: 'loading' })).toBe('mission2');
    expect(showPhase({ state: 'standby' })).toBe('mission1');
  });
});
