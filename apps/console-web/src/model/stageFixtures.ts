// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// SYNTHETIC RUNS FOR THE SIMPLIFIED SCREEN'S TESTS. Every record here is built by hand, in the controller's own state
// names and field shapes, with numbers of the size today's runs recorded; none of it is a robot record and none of it
// is shipped as data a screen shows. The render tests of the failure banner, the stage timeline and the Ready
// checklist fold these through the real reducer, so what they assert is what the reducer makes of the shapes.

import type { NamedValue } from '@kiwi/demo-contracts';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import { foldEvents, initialState } from './reducer';
import type { ViewState } from './types';

let seq = 0;

function envelope(runId: string, extra: Partial<EventEnvelope> = {}): EventEnvelope {
  seq += 1;
  return {
    schema_version: '1',
    seq,
    run_id: runId,
    action_id: null,
    observation_id: null,
    producer: { name: 'synthetic-fixture', version: '0', artifact_digest: null, contract_version: '1' },
    source_session: 'synthetic-session',
    t_mono_ns: seq * 1_000_000,
    clock_domain: 'laptop_monotonic',
    t_wall_utc: null,
    reason_code: null,
    provenance: null,
    ...extra,
  };
}

export function record<K extends ContractEvent['kind']>(
  runId: string,
  kind: K,
  payload: Extract<ContractEvent, { kind: K }>['payload'],
  extra: Partial<EventEnvelope> = {},
): ContractEvent {
  return { ...envelope(runId, extra), kind, payload } as ContractEvent;
}

export function started(runId: string): ContractEvent {
  return record(runId, 'run.started', { command_id: `cmd-${runId}`, command_type: 'start_cycle', data_mode: 'synthetic', plan_only: false, holding: false });
}

export function enter(runId: string, to: string, from: string | null = null): ContractEvent {
  return record(runId, 'state.transition', { machine: 'cycle', from_state: from, to_state: to });
}

export function captured(runId: string, observationId: string): ContractEvent {
  return record(
    runId,
    'observation.captured',
    { source_role: 'wrist', media: 'frame', width_px: 1280, height_px: 720, content_digest: null, source_stamp: null, retained: true },
    { observation_id: observationId },
  );
}

export interface PredicateInput {
  value: boolean | null;
  measured?: number | null;
  threshold?: number | null;
  unit?: string | null;
  frame?: string;
  measurements?: NamedValue[];
  rule_ref?: string | null;
  rule_digest?: string | null;
}

export function verdict(runId: string, name: string, input: PredicateInput): ContractEvent {
  return record(runId, 'predicate.evaluated', {
    predicate: name,
    value: input.value,
    state: input.value === null ? 'not_evaluated' : 'evaluated',
    threshold: input.threshold ?? null,
    measured: input.measured ?? null,
    unit: input.unit ?? null,
    evidence: input.frame === undefined ? [] : [{ type: 'frame', observation_ids: [input.frame], scalar_name: null }],
    consumed_source_stamp: null,
    measurements: input.measurements ?? [],
    rule_ref: input.rule_ref ?? null,
    rule_digest: input.rule_digest ?? null,
  });
}

export function finished(
  runId: string,
  outcome: 'placed_new' | 'failed' | 'stopped' | 'refused',
  failure: { code: string; phase: string; detail: string; measurements: NamedValue[]; frames: string[] } | null,
): ContractEvent {
  return record(
    runId,
    'run.finished',
    {
      outcome,
      exit_status: outcome === 'placed_new' ? 0 : 5,
      predicates: { object_secured: null, placed: outcome === 'placed_new' ? true : null, clearance_target_met: null, base_returned: null },
      automatic_retries: 0,
      task_control_interventions: 0,
      evidence_complete: true,
      evidence_gaps: 0,
      reason_code: failure?.code ?? null,
      phase: failure?.phase ?? null,
      failure: failure === null ? null : { detail: failure.detail, measurements: failure.measurements, observation_ids: failure.frames },
    },
    { reason_code: failure?.code ?? null },
  );
}

export const ALIGN_DIGEST = 'sha256:a11a000000000000000000000000000000000000000000000000000000000001';

/**
 * Today's item 7 in shape: ALIGN accepted on the lateral value at a radial of +12.4 mm, then the descent view lost the
 * block. Three ALIGN attempts, the last frame named by the failure.
 */
export function alignLostRun(runId = 'synthetic-align-lost'): ViewState {
  return foldEvents(initialState(), [
    started(runId),
    enter(runId, 'SEARCH'),
    captured(runId, `${runId}-obs-search`),
    verdict(runId, 'start_pose_is_the_low_scan_pose', { value: true, frame: `${runId}-obs-search` }),
    enter(runId, 'TARGET_SELECTED', 'SEARCH'),
    enter(runId, 'PREGRASP', 'TARGET_SELECTED'),
    enter(runId, 'ALIGN', 'PREGRASP'),
    enter(runId, 'ALIGN', 'ALIGN'),
    captured(runId, `${runId}-obs-align`),
    verdict(runId, 'aligned', {
      value: true,
      measured: 2.61,
      threshold: 6,
      unit: 'mm',
      frame: `${runId}-obs-align`,
      measurements: [{ name: 'radial_mm', value: 12.4, unit: 'mm' }],
      rule_ref: 'align.proven',
      rule_digest: ALIGN_DIGEST,
    }),
    enter(runId, 'ALIGN', 'ALIGN'),
    captured(runId, `${runId}-obs-descent`),
    finished(runId, 'failed', {
      code: 'align_cube_not_detected',
      phase: 'ALIGN',
      detail: 'ALIGN: the cube was not detected in the descent view; the arm holds at a non-contact clearance',
      measurements: [{ name: 'attempt', value: 3, unit: null }],
      frames: [`${runId}-obs-descent`],
    }),
  ]);
}

/** A placed run through the base advance, the shape of today's start 1. */
export function placedRun(runId = 'synthetic-placed'): ViewState {
  return foldEvents(initialState(), [
    started(runId),
    enter(runId, 'SEARCH'),
    captured(runId, `${runId}-obs-search`),
    verdict(runId, 'start_pose_is_the_low_scan_pose', { value: true, frame: `${runId}-obs-search` }),
    enter(runId, 'PREGRASP', 'SEARCH'),
    enter(runId, 'ALIGN', 'PREGRASP'),
    verdict(runId, 'aligned', { value: true, measured: 1.2, threshold: 6, unit: 'mm' }),
    enter(runId, 'GRASP_CLOSE', 'ALIGN'),
    verdict(runId, 'object_secured', { value: true, measured: 0.82, threshold: 0.5 }),
    enter(runId, 'PLACEMENT_PLAN', 'GRASP_CLOSE'),
    verdict(runId, 'placement_plan', { value: true, measured: 108, unit: 'mm' }),
    enter(runId, 'BASE_ADVANCE', 'PLACEMENT_PLAN'),
    verdict(runId, 'capability_output_applied', { value: true, measurements: [{ name: 'applied', value: 27, unit: null }] }),
    enter(runId, 'RELEASE_ADMISSION', 'BASE_ADVANCE'),
    verdict(runId, 'release_margin', { value: true, measured: 43.8, threshold: 37.96, unit: 'mm' }),
    enter(runId, 'PLACED', 'RELEASE_ADMISSION'),
    captured(runId, `${runId}-obs-exit`),
    verdict(runId, 'placed', { value: true, measured: 47.5, unit: 'mm', frame: `${runId}-obs-exit` }),
    enter(runId, 'BASE_RETURN', 'PLACED'),
    verdict(runId, 'base_returned', { value: true, measured: 3.1, threshold: 10, unit: 'mm' }),
    enter(runId, 'COMPLETE', 'BASE_RETURN'),
    finished(runId, 'placed_new', null),
  ]);
}

/** Today's item 1 in shape: three empty closes, then `grasp_missed`. */
export function graspMissedRun(runId = 'synthetic-grasp-missed'): ViewState {
  return foldEvents(initialState(), [
    started(runId),
    enter(runId, 'SEARCH'),
    enter(runId, 'PREGRASP', 'SEARCH'),
    enter(runId, 'ALIGN', 'PREGRASP'),
    verdict(runId, 'aligned', { value: true, measured: 2.6, threshold: 6, unit: 'mm' }),
    enter(runId, 'APPROACH', 'ALIGN'),
    enter(runId, 'GRASP_CLOSE', 'APPROACH'),
    enter(runId, 'GRASP_RETRY_ADVANCE', 'GRASP_CLOSE'),
    enter(runId, 'GRASP_CLOSE', 'GRASP_RETRY_ADVANCE'),
    enter(runId, 'GRASP_RETRY_ADVANCE', 'GRASP_CLOSE'),
    enter(runId, 'GRASP_CLOSE', 'GRASP_RETRY_ADVANCE'),
    verdict(runId, 'closed_on_something', { value: false, measured: 67.357, threshold: 67.4, unit: 'deg' }),
    finished(runId, 'failed', {
      code: 'grasp_missed',
      phase: 'GRASP_CLOSE',
      detail: 'GRASP_CLOSE: three closes found nothing between the jaws; the gripper re-opened',
      measurements: [{ name: 'gripper_pos', value: 67.357, unit: 'deg' }],
      frames: [],
    }),
  ]);
}
