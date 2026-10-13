// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE PERSISTENT, TIMESTAMPED MISSION TIMELINE (spec rules 6.3 and 6.7, audit T3 and T5). The record is
// mission-f65c3cf8e99f of 2026-09-27, trimmed: run 1 (m2fc135a) measured BASE_ADVANCE and needed the capability; the
// abort-swap was refused and the swapped Plan activated; run 2 (m6ed2dd9) went SEARCH ... GRASP_CLOSE and stopped on a
// refused CLEARANCE lift. The timeline is one list, oldest first, keyed; a key is inserted once and never removed.

import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import type { HistoryEntry } from '../api/types';
import type { MissionRecord } from './missionRecord';
import type { PressEntry } from '../store/presses';
import { composeTimeline, mergeTimeline, missionEndTime, TIMELINE_EVENT_KINDS } from './missionTimeline';

const RUN1 = 'console-20260927T141638Z-m2fc135a';
const RUN2 = 'console-20260927T142146Z-m6ed2dd9';

function ev(run: string, seq: number, kind: string, t: string, payload: Record<string, unknown>, extra: { action_id?: string; reason_code?: string } = {}): ContractEvent {
  return {
    schema_version: '1', seq, run_id: run, action_id: extra.action_id ?? null, observation_id: null,
    producer: { name: 'demo-runtime', version: '0.1.0', artifact_digest: null, contract_version: '1' },
    source_session: 'rt', t_mono_ns: seq, clock_domain: 'laptop_monotonic', t_wall_utc: t, reason_code: extra.reason_code ?? null, provenance: null,
    kind, payload,
  } as unknown as ContractEvent;
}

const state = (run: string, seq: number, t: string, from: string | null, to: string): ContractEvent => ev(run, seq, 'state.transition', t, { machine: 'cycle', from_state: from, to_state: to });

const RUN1_EVENTS = [
  ev(RUN1, 3595, 'run.started', '2026-09-27T14:16:39.507Z', { command_id: 'm2fc', command_type: 'start_cycle' }),
  state(RUN1, 3602, '2026-09-27T14:16:42.594737Z', null, 'SEARCH'),
  state(RUN1, 3615, '2026-09-27T14:17:08.274809Z', 'SEARCH', 'TARGET_SELECTED'),
  ev(RUN1, 3620, 'diagnostic.text', '2026-09-27T14:17:08.282488Z', { stream: 'log', level: 'error', text: "SEARCH: this target's cube measures BASE_ADVANCE" }, { reason_code: 'mission_capability_required' }),
  ev(RUN1, 3621, 'diagnostic.text', '2026-09-27T14:17:08.3Z', { stream: 'log', level: 'info', text: 'stdout chatter' }),
  ev(RUN1, 3622, 'run.finished', '2026-09-27T14:17:08.964Z', { outcome: 'failed', exit_status: 5, reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED' }),
];

const RUN2_EVENTS = [
  ev(RUN2, 3626, 'run.started', '2026-09-27T14:21:46.510Z', { command_id: 'm6ed', command_type: 'start_cycle' }),
  state(RUN2, 3633, '2026-09-27T14:21:50.097644Z', null, 'SEARCH'),
  state(RUN2, 3646, '2026-09-27T14:22:20.754962Z', 'SEARCH', 'TARGET_SELECTED'),
  state(RUN2, 3651, '2026-09-27T14:22:20.763357Z', 'TARGET_SELECTED', 'PREGRASP'),
  state(RUN2, 3708, '2026-09-27T14:23:07.787197Z', 'PREGRASP', 'ALIGN'),
  state(RUN2, 3747, '2026-09-27T14:23:29.894571Z', 'ALIGN', 'ALIGN'),
  state(RUN2, 3750, '2026-09-27T14:23:29.894969Z', 'ALIGN', 'APPROACH'),
  ev(RUN2, 3825, 'predicate.evaluated', '2026-09-27T14:24:08.0067Z', { predicate: 'straddle_far_edge_row', value: false, threshold: 420, measured: 333, unit: 'px' }),
  state(RUN2, 3826, '2026-09-27T14:24:08.006901Z', 'APPROACH', 'STRADDLING'),
  state(RUN2, 3851, '2026-09-27T14:24:26.235895Z', 'STRADDLING', 'GRASP_CLOSE'),
  ev(RUN2, 3892, 'action.planned', '2026-09-27T14:24:38.287829Z', { action_type: 'arm_pose', parameters: [{ name: 'label', value: 'CLEARANCE lift', unit: null }] }, { action_id: 'act-0007' }),
  ev(RUN2, 3902, 'predicate.evaluated', '2026-09-27T14:24:39.397375Z', { predicate: 'base_zero', value: false, threshold: 3, measured: 0, unit: 'reads' }, { action_id: 'act-0007' }),
  ev(RUN2, 3903, 'measured.state', '2026-09-27T14:24:39.412961Z', { joints: { gripper: 68.116 }, base_velocity: { x_mps: 0, y_mps: 0.0025566, theta_dps: 1.171875 } }, { action_id: 'act-0007' }),
  ev(RUN2, 3904, 'action.refused', '2026-09-27T14:24:39.415011Z', { action_type: 'arm_pose', detail: 'base velocity not confirmed zero before an arm segment: stopping all motion' }, { action_id: 'act-0007', reason_code: 'base_not_zero_before_segment' }),
  ev(RUN2, 3906, 'run.finished', '2026-09-27T14:24:40.112Z', { outcome: 'stopped', exit_status: 4, reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE', failure: { measurements: [], observation_ids: [] } }),
];

function history(run: string, started: string, finished: string, outcome: string): HistoryEntry {
  return { run_id: run, command_id: null, command_type: 'start_cycle', started_wall_utc: started, finished_wall_utc: finished, outcome, exit_status: null, evidence_complete: true, evidence_gaps: 0, artifact_digest: null, adopted: false, data_mode: 'live', bundle_digest: null } as unknown as HistoryEntry;
}

const HISTORY = [history(RUN1, '2026-09-27T14:16:38.938Z', '2026-09-27T14:17:08.967Z', 'failed'), history(RUN2, '2026-09-27T14:21:46.005Z', '2026-09-27T14:24:40.114Z', 'stopped')];

const MISSION: MissionRecord = {
  mission_record_version: '1', mission_id: 'mission-f65c3cf8e99f', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null,
  started_wall_utc: '2026-09-27T14:16:38.847Z', selected_target: 'cube', waiting_reason: null, detail: 'run_outcome:stopped:base_not_zero_before_segment',
  stop_requested: false, stop_requested_wall_utc: null,
  targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: [RUN1, RUN2], attempts: [], retries: [], park_runs: [] }],
  activations: [
    { operation_uuid: '40b94b8a', target_id: 'cube', candidate_key: 'mobile-demo-active-successor-abort-swap', payload_digest: null, activation_state: 'REJECTED_BY_CAKE', outcome: null, transaction_phase: 'ABORTED', transaction_uuid: null, detail: null, t_wall_utc: '2026-09-27T14:20:21.636Z' },
    { operation_uuid: '5d293b02', target_id: 'cube', candidate_key: 'mobile-demo-swapped', payload_digest: null, activation_state: 'OBSERVED', outcome: null, transaction_phase: 'COMPLETED', transaction_uuid: null, detail: null, t_wall_utc: '2026-09-27T14:21:45.496Z' },
  ],
  hold: null,
  next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
};

const INPUT = { mission: MISSION, history: HISTORY, runEvents: { [RUN1]: RUN1_EVENTS, [RUN2]: RUN2_EVENTS }, presses: [] as PressEntry[], links: [], candidates: null };

describe('the mission timeline', () => {
  it('lists the mission, run 1, the two Cake lines, run 2 and the mission end, oldest first', () => {
    const entries = composeTimeline(INPUT);
    const top = entries.filter((entry) => entry.group === null || entry.kind === 'run');
    expect(top.map((entry) => entry.key)).toEqual([
      'mission:mission-f65c3cf8e99f:started',
      `run:${RUN1}`,
      'activation:40b94b8a',
      'activation:5d293b02',
      `run:${RUN2}`,
      'mission:mission-f65c3cf8e99f:ended',
    ]);
  });

  it("heads each run with its number, its id and its recorded span, and closes it with the run's word", () => {
    const entries = composeTimeline(INPUT);
    const header = entries.find((entry) => entry.key === `run:${RUN2}`);
    expect(header?.text).toBe(`Run 2 of 2, ${RUN2}, 14:21:46Z to 14:24:40Z (2 min 54 s)`);
    const footer = entries.find((entry) => entry.key === `${RUN2}:3906`);
    expect(footer?.text).toBe('Run 2 of 2 stopped by the controller, base_not_zero_before_segment');
    expect(footer?.verdict?.id).toBe('stopped_by_controller');
    const footer1 = entries.find((entry) => entry.key === `${RUN1}:3622`);
    expect(footer1?.verdict?.id).toBe('needs_capability');
  });

  it("lists run 2's state transitions with a time each, a repeated state as again, then the joined cause", () => {
    const entries = composeTimeline(INPUT).filter((entry) => entry.group === RUN2 && entry.kind !== 'run');
    expect(entries.map((entry) => entry.text)).toEqual([
      'SEARCH 14:21:50Z',
      'TARGET_SELECTED 14:22:20Z',
      'PREGRASP 14:22:20Z',
      'ALIGN 14:23:07Z',
      'ALIGN again 14:23:29Z',
      'APPROACH 14:23:29Z',
      'STRADDLING 14:24:08Z',
      'GRASP_CLOSE 14:24:26Z',
      'base_zero: 0 reads against 3 reads, 14:24:39Z',
      'Base velocity in that reading: x 0.000 m/s, y 0.003 m/s, rotation 1.17 deg/s',
      'Refused CLEARANCE lift (arm_pose): base velocity not confirmed zero before an arm segment: stopping all motion',
      'Run 2 of 2 stopped by the controller, base_not_zero_before_segment',
    ]);
    for (const entry of entries) expect(entry.t).toMatch(/Z$/);
    // The earlier false predicate of another action never enters the timeline.
    expect(entries.map((entry) => entry.text).join(' ')).not.toContain('straddle_far_edge_row');
  });

  it('keeps an error line of a run and leaves info lines to Diagnostics', () => {
    const run1 = composeTimeline(INPUT).filter((entry) => entry.group === RUN1).map((entry) => entry.text);
    expect(run1.some((text) => text.includes("this target's cube measures BASE_ADVANCE"))).toBe(true);
    expect(run1.some((text) => text.includes('stdout chatter'))).toBe(false);
    expect(TIMELINE_EVENT_KINDS.has('observation.captured')).toBe(false);
  });

  it('says the mission ended at its last run\'s finish, labelled as derived', () => {
    const ended = composeTimeline(INPUT).find((entry) => entry.key === 'mission:mission-f65c3cf8e99f:ended');
    expect(ended?.text).toBe('Mission failed, ended 14:24:40Z');
    expect(ended?.t).toBe('2026-09-27T14:24:40.114Z');
    expect(missionEndTime(MISSION, HISTORY)).toEqual({ iso: '2026-09-27T14:24:40.114Z', title: "the time the mission's last run finished; the mission record carries no end time" });
  });

  it('uses the stop request time for a mission stopped while no run was active', () => {
    const stopped = { ...MISSION, state: 'STOPPED', stop_requested: true, stop_requested_wall_utc: '2026-09-27T14:30:00.000Z' };
    expect(missionEndTime(stopped, HISTORY)?.iso).toBe('2026-09-27T14:30:00.000Z');
    expect(missionEndTime({ ...MISSION, state: 'RUNNING' }, HISTORY)).toBeNull();
  });

  it('shows each press as its own line, on this display\'s clock', () => {
    const press: PressEntry = { id: 'press-1', control: 'stop', pressed_at: '2026-09-27T14:30:02.000Z', answer: 'refused', text: 'Stop refused: there is no run to stop.', answered_at: '2026-09-27T14:30:02.100Z', mission_id: null, run_id: null, record: null };
    const line = composeTimeline({ ...INPUT, presses: [press] }).find((entry) => entry.key === 'press:press-1');
    expect(line?.kind).toBe('press');
    expect(line?.text).toBe('You pressed Stop, 14:30:02Z (this display): Stop refused: there is no run to stop.');
  });

  it('never removes a key, whatever a later read returns', () => {
    const first = composeTimeline(INPUT);
    const fewer = composeTimeline({ ...INPUT, runEvents: {}, history: [] });
    const merged = mergeTimeline(first, fewer);
    for (const entry of first) expect(merged.map((item) => item.key)).toContain(entry.key);
    expect(mergeTimeline(merged, first).map((item) => item.key)).toEqual(merged.map((item) => item.key));
  });
});
