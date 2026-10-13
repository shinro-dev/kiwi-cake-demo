// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// TWO RUNS OF A MISSION, SYNTHETIC, IN THE SHAPE THE RUNTIME SERVES THEM (GET /api/history/<run>/events). Every id,
// session, digest, time, joint, pixel and measurement here is invented; nothing is copied from a robot or a runtime. The
// shape is the one the Scene's pins need (redesign r4 verify-r3 must_fix 2): run 1 whole (31 records: accepted, started,
// its config, SEARCH, one wrist frame and its four perception layers, TARGET_SELECTED, three predicates, the controller's
// failure for a target that needs the base, its end), run 2 from its start to its first sighting (22 records, its two
// cube-layer answers last), and run 2's own run.finished (a stop in GRASP_CLOSE), apart. It replaces a fixture of raw
// runtime records (round 7 item 5): AGENTS.md forbids committing raw runtime state.

import type { ContractEvent } from '../contracts/envelope';

export const SYNTHETIC_RUN_1 = 'synthetic-mission-run-0001';
export const SYNTHETIC_RUN_2 = 'synthetic-mission-run-0002';
const SESSION = 'synthetic-session-scene-0001';
const BUNDLE = 'sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const CONFIG = 'sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210';
const SEARCH_RULE = 'sha256:1111111111111111111111111111111111111111111111111111111111111111';
const ALIGN_RULE = 'sha256:2222222222222222222222222222222222222222222222222222222222222222';
const DAY = '2026-01-15';

interface Stamp {
  seq: number;
  run: string;
  /** `HH:MM:SS.mmm` on DAY. */
  at: string;
  producer: 'runtime' | 'controller';
  observation?: string | null;
  reason?: string | null;
  bundle?: boolean;
}

function event(stamp: Stamp, kind: ContractEvent['kind'], payload: unknown): ContractEvent {
  const controller = stamp.producer === 'controller';
  return {
    schema_version: '1',
    seq: stamp.seq,
    run_id: stamp.run,
    action_id: null,
    observation_id: stamp.observation ?? null,
    kind,
    producer: {
      name: controller ? 'synthetic-controller' : 'synthetic-runtime',
      version: '0',
      artifact_digest: controller || stamp.bundle === true ? BUNDLE : null,
      contract_version: '1',
    },
    source_session: SESSION,
    t_mono_ns: stamp.seq * 1_000_000,
    clock_domain: 'laptop_monotonic',
    t_wall_utc: `${DAY}T${stamp.at}Z`,
    reason_code: stamp.reason ?? null,
    payload,
    provenance: null,
  } as unknown as ContractEvent;
}

function log(text: string, level = 'info', stream = 'log'): unknown {
  return { stream, level, text, redacted: false };
}

function span(name: string, duration_ns: number, clock_domain = 'laptop_monotonic'): unknown {
  return { span: name, duration_ns, clock_domain };
}

interface Look {
  obs: string;
  box: [number, number, number, number];
  confidence: number;
  pad_m: [number, number];
}

/** One run's opening, up to its SEARCH frame and its four perception layers: 22 records from `first`. */
function opening(run: string, first: number, clock: (index: number) => string, look: Look): ContractEvent[] {
  const s = (index: number, producer: Stamp['producer'], extra: Partial<Stamp> = {}): Stamp => ({ seq: first + index, run, at: clock(index), producer, ...extra });
  const [x0, y0, x1, y1] = look.box;
  return [
    event(s(0, 'runtime', { reason: 'command_accepted' }), 'diagnostic.text', log('command accepted (start_cycle)')),
    event(s(1, 'runtime', { reason: 'owner.autonomous' }), 'diagnostic.text', log('the runtime holds the robot for this run')),
    event(s(2, 'runtime', { bundle: true }), 'run.started', { command_id: `cmd-${run}`, command_type: 'start_cycle', data_mode: 'live', plan_only: false, holding: null }),
    event(s(3, 'runtime', { reason: 'controller_spawned', bundle: true }), 'diagnostic.text', log('the controller process was started')),
    event(s(4, 'controller'), 'run.config', {
      config_digest: CONFIG,
      summary: [
        { name: 'controller', value: 'synthetic-controller', unit: null },
        { name: 'action_kind', value: 'cycle', unit: null },
        { name: 'plan_only', value: false, unit: null },
        { name: 'constant.cube_edge_mm', value: 25, unit: 'mm' },
      ],
    }),
    event(s(5, 'controller', { reason: 'camera_setup_remote_command' }), 'timing.span', span('waiting', 4000)),
    event(s(6, 'controller', { reason: 'camera_setup_copy' }), 'timing.span', span('transfer', 800_000)),
    event(s(7, 'controller', { reason: 'host_does_not_stamp' }), 'evidence.gap', { gap: 'source_stamp_missing', detail: 'synthetic: the host does not stamp its observations', observation_ids: [] }),
    event(s(8, 'controller'), 'measured.state', {
      joints: { shoulder_pan: -5, shoulder_lift: -80, elbow_flex: 35, wrist_flex: 70, wrist_roll: -95, gripper: 80 },
      base_velocity: { x_mps: 0, y_mps: 0, theta_dps: 0 },
      consumed_source_stamp: { session: 'synthetic-rx-0001', seq: 1, sample_mono_ns: first * 1_000_000, clock_domain: 'laptop_monotonic' },
    }),
    event(s(9, 'controller'), 'state.transition', { machine: 'cycle', from_state: null, to_state: 'SEARCH' }),
    event(s(10, 'controller'), 'diagnostic.text', log('[STATE] SEARCH {"pose": "synthetic"}', 'info', 'stdout')),
    event(s(11, 'controller', { observation: look.obs, reason: 'capture_remote_lifecycle' }), 'timing.span', span('capture', 500_000_000)),
    event(s(12, 'controller', { observation: look.obs, reason: 'capture_copy_back' }), 'timing.span', span('transfer', 550_000)),
    event(s(13, 'controller', { reason: 'detector_constructed' }), 'timing.span', span('model_init', 14_000)),
    event(s(14, 'controller', { observation: look.obs }), 'observation.captured', {
      source_role: 'wrist', media: 'frame', width_px: 1280, height_px: 720, content_digest: `sha256:${'3'.repeat(64)}`, source_stamp: null, retained: true,
    }),
    event(s(15, 'controller', { observation: look.obs, reason: 'camera_lifecycle_on_the_robot' }), 'timing.span', span('capture', 420_000_000, 'pi_monotonic')),
    event(s(16, 'controller', { observation: look.obs, reason: 'camera_open_on_the_robot' }), 'timing.span', span('waiting', 160_000_000, 'pi_monotonic')),
    event(s(17, 'controller', { observation: look.obs, reason: 'perceive' }), 'timing.span', span('inference', 2_000_000_000)),
    event(s(18, 'controller', { observation: look.obs }), 'perception.result', {
      detector: 'cube-recognizer', observation_ids: [look.obs], result: 'detected', confidence: look.confidence,
      measurements: [
        { name: 'bbox_x0', value: x0, unit: 'px' }, { name: 'bbox_y0', value: y0, unit: 'px' },
        { name: 'bbox_x1', value: x1, unit: 'px' }, { name: 'bbox_y1', value: y1, unit: 'px' },
        { name: 'contact_x', value: (x0 + x1) / 2, unit: 'px' }, { name: 'contact_y', value: y1 - 3, unit: 'px' },
        { name: 'contact_available', value: true, unit: null }, { name: 'clipped_any_border', value: false, unit: null },
      ],
    }),
    event(s(19, 'controller', { observation: look.obs }), 'perception.result', {
      detector: 'pad-outline-fit', observation_ids: [look.obs], result: 'detected', confidence: 0.7,
      measurements: [{ name: 'boundary_rms', value: 1.2, unit: 'px' }, { name: 'visible_fraction', value: 0.6, unit: null }, { name: 'view_elevation', value: 55, unit: 'deg' }],
    }),
    event(s(20, 'controller', { observation: look.obs }), 'perception.result', {
      detector: 'pad-print-landmarks', observation_ids: [look.obs], result: 'detected', confidence: null,
      measurements: [{ name: 'template_available', value: true, unit: null }, { name: 'pad_frame_valid', value: true, unit: null }, { name: 'landmark_inliers', value: 120, unit: null }],
    }),
    event(s(21, 'controller', { observation: look.obs }), 'perception.result', {
      detector: 'cube-in-pad-frame', observation_ids: [look.obs], result: 'detected', confidence: null,
      measurements: [{ name: 'cube_center_pad_x', value: look.pad_m[0], unit: 'm' }, { name: 'cube_center_pad_y', value: look.pad_m[1], unit: 'm' }, { name: 'inside_pad_outline', value: false, unit: null }],
    }),
  ];
}

const OBS_1 = 'synthetic-obs-run1-wrist-0001';
const OBS_2 = 'synthetic-obs-run2-wrist-0001';

function run1(): ContractEvent[] {
  const head = opening(SYNTHETIC_RUN_1, 101, (index) => (index < 14 ? `10:00:${String(index).padStart(2, '0')}.100` : `10:00:${String(20 + index).padStart(2, '0')}.200`), {
    obs: OBS_1, box: [520, 450, 600, 545], confidence: 0.93, pad_m: [0.012, -0.164],
  });
  const s = (index: number, producer: Stamp['producer'], extra: Partial<Stamp> = {}): Stamp => ({ seq: 123 + index, run: SYNTHETIC_RUN_1, at: `10:00:${String(50 + index).padStart(2, '0')}.300`, producer, ...extra });
  const frame = [{ type: 'frame', observation_ids: [OBS_1], scalar_name: null }];
  return [
    ...head,
    event(s(0, 'controller', { observation: OBS_1 }), 'state.transition', { machine: 'cycle', from_state: 'SEARCH', to_state: 'TARGET_SELECTED' }),
    event(s(1, 'controller'), 'diagnostic.text', log('[STATE] TARGET_SELECTED {"target_id": "cube", "mode": "BASE_ADVANCE", "seen_pad_m": [0.012, -0.164]}', 'info', 'stdout')),
    event(s(2, 'controller', { observation: OBS_1 }), 'predicate.evaluated', {
      predicate: 'already_placed', value: false, threshold: 18, measured: -24, unit: 'mm',
      evidence: [...frame, { type: 'scalar', observation_ids: [], scalar_name: 'cube_centre_to_pad_edge_mm' }], consumed_source_stamp: null, rule_ref: 'search.proven', rule_digest: SEARCH_RULE,
    }),
    event(s(3, 'controller', { observation: OBS_1 }), 'predicate.evaluated', {
      predicate: 'pregrasp_pan_within_return_bound', value: true, threshold: 25, measured: 2, unit: 'deg',
      evidence: [...frame, { type: 'scalar', observation_ids: [], scalar_name: 'coarse_pan_deg' }], consumed_source_stamp: null, rule_ref: 'search.proven', rule_digest: SEARCH_RULE,
    }),
    event(s(4, 'controller', { observation: OBS_1 }), 'predicate.evaluated', {
      predicate: 'pregrasp_cube_within_reach', value: true, threshold: -13, measured: 9, unit: 'mm',
      evidence: [...frame, { type: 'scalar', observation_ids: [], scalar_name: 'coarse_cube_out_mm' }], consumed_source_stamp: null, rule_ref: 'align.proven', rule_digest: ALIGN_RULE,
    }),
    event(s(5, 'controller', { reason: 'mission_capability_required' }), 'diagnostic.text', log("SEARCH: this target's cube measures BASE_ADVANCE, which needs the mobile_approach capability, and this run carries none", 'error')),
    event(s(6, 'runtime', { reason: 'controller_exited', bundle: true }), 'diagnostic.text', log('the controller process ended; exit status 5')),
    event(s(7, 'runtime', { bundle: true }), 'run.finished', {
      outcome: 'failed', exit_status: 5, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
      automatic_retries: 0, task_control_interventions: 0, evidence_complete: false, evidence_gaps: 1, reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED',
      failure: {
        detail: "SEARCH: this target's cube measures BASE_ADVANCE, which needs the mobile_approach capability, and this run carries none",
        measurements: [{ name: 'declared_mode', value: 'auto', unit: null }, { name: 'measured_mode', value: 'BASE_ADVANCE', unit: null }],
        observation_ids: [OBS_1],
      },
    }),
    event(s(8, 'runtime', { reason: 'owner.free' }), 'diagnostic.text', log('the run is over and the runtime released the robot')),
  ];
}

/** Run 1 of the mission, whole: SEARCH saw the block, TARGET_SELECTED BASE_ADVANCE, then the run needed the capability. */
export const SYNTHETIC_MISSION_RUN_1: ContractEvent[] = run1();

/** Run 2 of the mission, from its start to its first sighting (its cube-layer answers at seq 150 and 153). */
export const SYNTHETIC_MISSION_RUN_2_TO_FIRST_SIGHTING: ContractEvent[] = opening(
  SYNTHETIC_RUN_2,
  132,
  (index) => (index < 14 ? `10:05:${String(10 + index).padStart(2, '0')}.100` : `10:06:40.${String(100 + index)}`),
  { obs: OBS_2, box: [521, 451, 601, 546], confidence: 0.95, pad_m: [0.0118, -0.1646] },
);

/** Run 2's own end: stopped in GRASP_CLOSE because the base was not confirmed at rest before an arm segment. */
export const SYNTHETIC_MISSION_RUN_2_FINISHED: ContractEvent = event({ seq: 414, run: SYNTHETIC_RUN_2, at: '10:09:00.500', producer: 'runtime', bundle: true }, 'run.finished', {
  outcome: 'stopped', exit_status: 4, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
  automatic_retries: 0, task_control_interventions: 0, evidence_complete: false, evidence_gaps: 1, reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE',
  failure: { detail: 'base velocity not confirmed zero before an arm segment: stopping all motion', measurements: [], observation_ids: [] },
});
