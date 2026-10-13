// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ATTEMPT RECORD AND THE CANDIDATE VIEW, read at the boundary (spec section 3.8). The runtime serves each target's
// attempts (`targets[].attempts[]`), its Retries and its park runs, and this console dropped them; the stop state the
// outcome card states and the run the attempt belongs to come from there. The candidates carry role, label and expect
// only when the runtime's configuration names them; absent is kept as absent, never filled in. The shapes below are the
// live record of mission-f65c3cf8e99f (2026-09-27), trimmed.

import { describe, expect, it } from 'vitest';
import { parseCandidates, parseMission } from './missionRecord';

const LIVE_TARGET = {
  target_id: 'cube',
  label: 'the block',
  reachable: true,
  requires_capability: 'auto',
  state: 'FAILED',
  runs: ['console-20260927T141638Z-m2fc135a', 'console-20260927T142146Z-m6ed2dd9'],
  attempts: [
    {
      attempt: 1,
      run_id: 'console-20260927T142146Z-m6ed2dd9',
      park_run_id: null,
      outcome_class: 'stopped',
      reason_code: 'base_not_zero_before_segment',
      phase: 'GRASP_CLOSE',
      measurements: [],
      observation_ids: [],
      stop_state: { base_ahead: false, holding: true, moved: true },
      next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
      budget_left: 1,
      t_wall_utc: '2026-09-27T14:24:40.265Z',
    },
  ],
  retries: [],
  park_runs: [],
};

function mission(target: Record<string, unknown>): Record<string, unknown> {
  return { mission_record_version: 1, mission_id: 'mission-f65c3cf8e99f', state: 'FAILED', targets: [target], activations: [] };
}

describe('the attempt record of a mission target', () => {
  it('keeps every attempt the runtime served, field by field', () => {
    const parsed = parseMission(mission(LIVE_TARGET));
    const attempts = parsed?.targets[0]?.attempts ?? null;
    expect(attempts).toEqual([
      {
        attempt: 1,
        run_id: 'console-20260927T142146Z-m6ed2dd9',
        park_run_id: null,
        outcome_class: 'stopped',
        reason_code: 'base_not_zero_before_segment',
        phase: 'GRASP_CLOSE',
        measurements: [],
        observation_ids: [],
        stop_state: { holding: true, base_ahead: false, moved: true },
        next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
        budget_left: 1,
        t_wall_utc: '2026-09-27T14:24:40.265Z',
      },
    ]);
  });

  it('keeps the Retries asked for and the park runs, and reads an absent list as none recorded', () => {
    const withRetry = parseMission(
      mission({
        ...LIVE_TARGET,
        retries: [{ attempt: 1, origin: 'operator_console', t_wall_utc: '2026-09-27T14:30:00.000Z' }],
        park_runs: ['run-park-1'],
      }),
    );
    expect(withRetry?.targets[0]?.retries).toEqual([{ attempt: 1, origin: 'operator_console', t_wall_utc: '2026-09-27T14:30:00.000Z' }]);
    expect(withRetry?.targets[0]?.park_runs).toEqual(['run-park-1']);
    const { attempts: _a, retries: _r, park_runs: _p, ...older } = LIVE_TARGET;
    const bare = parseMission(mission(older))?.targets[0];
    expect(bare?.attempts).toEqual([]);
    expect(bare?.retries).toEqual([]);
    expect(bare?.park_runs).toEqual([]);
  });

  it('reads an unknown stop state part as unknown and never as false', () => {
    const parsed = parseMission(
      mission({ ...LIVE_TARGET, attempts: [{ ...LIVE_TARGET.attempts[0], stop_state: { holding: null, moved: true }, measurements: [{ name: 'attempt', value: 2, unit: null }] }] }),
    );
    const attempt = parsed?.targets[0]?.attempts?.[0];
    expect(attempt?.stop_state).toEqual({ holding: null, base_ahead: null, moved: true });
    expect(attempt?.measurements).toEqual([{ name: 'attempt', value: 2, unit: null }]);
  });
});

describe('the prepared candidates', () => {
  it('keeps key and digest only when the runtime serves no role, as 8771 serves them', () => {
    const served = {
      ok: true,
      candidates: [
        { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:6c19' },
        { key: 'mobile-demo-swapped', payload_digest: 'sha256:4d72' },
      ],
    };
    expect(parseCandidates(served)).toEqual([
      { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:6c19', role: null, label: null, expect: null },
      { key: 'mobile-demo-swapped', payload_digest: 'sha256:4d72', role: null, label: null, expect: null },
    ]);
  });

  it('keeps the role, the label and the expected outcome when the runtime serves them', () => {
    const served = {
      candidates: [{ key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:c3', role: 'refused-demo', label: 'Abort-swap', expect: 'REJECTED_BY_CAKE' }],
    };
    expect(parseCandidates(served)).toEqual([
      { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:c3', role: 'refused-demo', label: 'Abort-swap', expect: 'REJECTED_BY_CAKE' },
    ]);
  });
});
