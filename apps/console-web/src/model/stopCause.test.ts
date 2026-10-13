// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE STOP'S OWN CAUSE, joined by action id (spec rule 6.3.4, audit C4). The events are run
// console-20260927T142146Z-m6ed2dd9 of 2026-09-27, trimmed to the records the join reads: an earlier false predicate
// with no action id (straddle_far_edge_row, 333 px against 420 px, 31 s before the stop), then under act-0007 the
// planned CLEARANCE lift, the false base_zero predicate, the measured state and the refusal, then run.finished whose
// failure carries no measurement and no frame. The cause is the refusal's own records and never the earlier predicate.

import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { stopCause } from './stopCause';

const RUN = 'console-20260927T142146Z-m6ed2dd9';

function ev(seq: number, kind: string, t: string, payload: Record<string, unknown>, extra: { action_id?: string | null; reason_code?: string | null } = {}): ContractEvent {
  return {
    schema_version: '1',
    seq,
    run_id: RUN,
    action_id: extra.action_id ?? null,
    observation_id: null,
    producer: { name: 'demo-runtime', version: '0.1.0', artifact_digest: null, contract_version: '1' },
    source_session: 'rt-20260927T141418Z-89540562',
    t_mono_ns: seq * 1000,
    clock_domain: 'laptop_monotonic',
    t_wall_utc: t,
    reason_code: extra.reason_code ?? null,
    provenance: null,
    kind,
    payload,
  } as unknown as ContractEvent;
}

const FAR_EDGE = ev(3825, 'predicate.evaluated', '2026-09-27T14:24:08.006700Z', {
  predicate: 'straddle_far_edge_row',
  value: false,
  threshold: 420.0,
  measured: 333.0,
  unit: 'px',
  evidence: [{ type: 'frame', observation_ids: ['obs-20260927T142354Z-wrist-0001'], scalar_name: null }],
  rule_ref: 'grasp.proven',
  rule_digest: 'sha256:66eb',
});
const LIFT_PLANNED = ev(3892, 'action.planned', '2026-09-27T14:24:38.287829Z', {
  action_type: 'arm_pose',
  plan_only: false,
  parameters: [{ name: 'label', value: 'CLEARANCE lift', unit: null }],
}, { action_id: 'act-0007' });
const EARLIER_MEASURED = ev(3881, 'measured.state', '2026-09-27T14:24:33.734110Z', {
  joints: { gripper: 68.11594202898551 },
  base_velocity: { x_mps: 0.0022, y_mps: 0.0013, theta_dps: 0.5859375 },
}, { action_id: 'act-0006' });
const BASE_ZERO = ev(3902, 'predicate.evaluated', '2026-09-27T14:24:39.397375Z', {
  predicate: 'base_zero',
  value: false,
  threshold: 3.0,
  measured: 0.0,
  unit: 'reads',
  evidence: [],
  rule_ref: null,
  rule_digest: null,
}, { action_id: 'act-0007' });
const MEASURED = ev(3903, 'measured.state', '2026-09-27T14:24:39.412961Z', {
  joints: { shoulder_pan: -8.08, gripper: 68.11594202898551 },
  base_velocity: { x_mps: -6.984728289775616e-19, y_mps: 0.0025566346464760688, theta_dps: 1.171875 },
}, { action_id: 'act-0007' });
const REFUSED = ev(3904, 'action.refused', '2026-09-27T14:24:39.415011Z', {
  action_type: 'arm_pose',
  before_motion: false,
  detail: 'base velocity not confirmed zero before an arm segment: stopping all motion',
}, { action_id: 'act-0007', reason_code: 'base_not_zero_before_segment' });
const FINISHED = ev(3906, 'run.finished', '2026-09-27T14:24:40.112Z', {
  outcome: 'stopped',
  exit_status: 4,
  reason_code: 'base_not_zero_before_segment',
  phase: 'GRASP_CLOSE',
  failure: { detail: 'base velocity not confirmed zero before an arm segment: stopping all motion', measurements: [], observation_ids: [] },
});

const EVENTS = [EARLIER_MEASURED, FAR_EDGE, LIFT_PLANNED, BASE_ZERO, MEASURED, REFUSED, FINISHED];

describe("the stop's own cause", () => {
  it('joins the refusal with the same-action false predicate, the newest measured state and the planned label', () => {
    const cause = stopCause(EVENTS, '2026-09-27');
    expect(cause?.reason_code).toBe('base_not_zero_before_segment');
    expect(cause?.refusal?.action_id).toBe('act-0007');
    expect(cause?.lines).toEqual([
      'Refused CLEARANCE lift (arm_pose): base velocity not confirmed zero before an arm segment: stopping all motion',
      'base_zero: 0 reads against 3 reads, 14:24:39Z',
      'Base velocity in that reading: x 0.000 m/s, y 0.003 m/s, rotation 1.17 deg/s',
    ]);
    expect(cause?.gripper).toBe(68.11594202898551);
    expect(cause?.measured_at).toBe('2026-09-27T14:24:39.412961Z');
  });

  it('ignores an earlier false predicate that carries no action id, and never shows its pixels or its frame', () => {
    const cause = stopCause(EVENTS, '2026-09-27');
    const text = (cause?.lines ?? []).join(' ');
    expect(text).not.toContain('straddle_far_edge_row');
    expect(text).not.toContain('px');
    expect(cause?.frame_id).toBeNull();
    expect(cause?.predicates_recorded).toBe(2);
    expect(cause?.predicates_tied).toBe(1);
  });

  it('shows the failure measurements as recorded when no refusal matches the reason code', () => {
    const failed = ev(10, 'run.finished', '2026-09-27T14:24:40.112Z', {
      outcome: 'failed',
      exit_status: 5,
      reason_code: 'align_cube_not_detected',
      phase: 'ALIGN',
      failure: {
        detail: 'ALIGN: the cube is not detected in the pregrasp view.',
        measurements: [
          { name: 'attempt', value: 2, unit: null },
          { name: 'recognizer_reason', value: 'no detector proposal: nothing to verify', unit: null },
        ],
        observation_ids: ['obs-descent'],
      },
    });
    const cause = stopCause([FAR_EDGE, REFUSED, failed], '2026-09-27');
    expect(cause?.refusal).toBeNull();
    expect(cause?.lines).toEqual(['attempt 2', 'recognizer_reason no detector proposal: nothing to verify']);
    expect(cause?.frame_id).toBe('obs-descent');
  });

  it('says the record carries no measurement when neither a refusal nor the failure has one', () => {
    const cause = stopCause([FAR_EDGE, FINISHED], '2026-09-27');
    expect(cause?.lines).toEqual(['The record carries no measurement for this stop.']);
  });

  it('has no cause for a run that has not finished or finished without a reason code', () => {
    expect(stopCause([FAR_EDGE, LIFT_PLANNED], '2026-09-27')).toBeNull();
    const placed = ev(11, 'run.finished', '2026-09-27T14:24:40.112Z', { outcome: 'placed_new', exit_status: 0, reason_code: null });
    expect(stopCause([placed], '2026-09-27')).toBeNull();
  });
});
