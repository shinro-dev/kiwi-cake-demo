// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The scripted run the SYNTHETIC source replays.
//
// It is built from the contract fixtures in `packages/demo-contracts/fixtures`: the envelope and every payload start
// as a known-valid fixture and are varied from there, so the shapes on this screen are the shapes the runtime sends.
// The state names are the controller's real ones, read from `bin/laptop/manipulation/run_single_cube_cycle.py`.
//
// Nothing in here was measured. Every number is invented for a demonstration and the screen says SYNTHETIC over every
// camera panel while it plays. The fixtures themselves are marked synthetic in the contract for the same reason.

import actionCompletedFixture from '@kiwi/demo-contracts/fixtures/event/action.completed.json';
import actionPlannedFixture from '@kiwi/demo-contracts/fixtures/event/action.planned.json';
import actionStartedFixture from '@kiwi/demo-contracts/fixtures/event/action.started.json';
import diagnosticFixture from '@kiwi/demo-contracts/fixtures/event/diagnostic.text.json';
import evidenceGapFixture from '@kiwi/demo-contracts/fixtures/event/evidence.gap.json';
import measuredStateFixture from '@kiwi/demo-contracts/fixtures/event/measured.state.json';
import observationFixture from '@kiwi/demo-contracts/fixtures/event/observation.captured.json';
import perceptionFixture from '@kiwi/demo-contracts/fixtures/event/perception.result.json';
import predicateFixture from '@kiwi/demo-contracts/fixtures/event/predicate.evaluated.json';
import runConfigFixture from '@kiwi/demo-contracts/fixtures/event/run.config.json';
import runFinishedFixture from '@kiwi/demo-contracts/fixtures/event/run.finished.json';
import runStartedFixture from '@kiwi/demo-contracts/fixtures/event/run.started.json';
import stateTransitionFixture from '@kiwi/demo-contracts/fixtures/event/state.transition.json';
import stopAcknowledgedFixture from '@kiwi/demo-contracts/fixtures/event/stop.acknowledged.json';
import stopOutcomeFixture from '@kiwi/demo-contracts/fixtures/event/stop.outcome.json';
import stopRequestedFixture from '@kiwi/demo-contracts/fixtures/event/stop.requested.json';
import telemetryDroppedFixture from '@kiwi/demo-contracts/fixtures/event/telemetry.dropped.json';
import timingSpanFixture from '@kiwi/demo-contracts/fixtures/event/timing.span.json';
import idleSnapshotFixture from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';

import type {
  PayloadActionCompleted,
  PayloadActionPlanned,
  PayloadActionStarted,
  PayloadDiagnosticText,
  PayloadEvidenceGap,
  PayloadMeasuredState,
  PayloadObservationCaptured,
  PayloadPerceptionResult,
  PayloadPredicateEvaluated,
  PayloadRunConfig,
  PayloadRunFinished,
  PayloadRunStarted,
  PayloadStateTransition,
  PayloadStopAcknowledged,
  PayloadStopOutcome,
  PayloadStopRequested,
  PayloadTelemetryDropped,
  PayloadTimingSpan,
  Producer,
  CommandType,
  Provenance,
  Snapshot,
  SourceStamp,
} from '@kiwi/demo-contracts';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import type { ObservationRecordDto, PerceptionDto } from '../model/observation';

// One cast per fixture, at the boundary: the fixtures are validated by the contract's own test suite, and a JSON
// import widens every literal to `string` or `number`, which no payload type accepts.
export const FIXTURE = {
  runStarted: runStartedFixture.payload as PayloadRunStarted,
  runConfig: runConfigFixture.payload as PayloadRunConfig,
  runFinished: runFinishedFixture.payload as PayloadRunFinished,
  transition: stateTransitionFixture.payload as PayloadStateTransition,
  actionPlanned: actionPlannedFixture.payload as PayloadActionPlanned,
  actionStarted: actionStartedFixture.payload as PayloadActionStarted,
  actionCompleted: actionCompletedFixture.payload as PayloadActionCompleted,
  measuredState: measuredStateFixture.payload as PayloadMeasuredState,
  observation: observationFixture.payload as PayloadObservationCaptured,
  perception: perceptionFixture.payload as PayloadPerceptionResult,
  predicate: predicateFixture.payload as PayloadPredicateEvaluated,
  stopRequested: stopRequestedFixture.payload as PayloadStopRequested,
  stopAcknowledged: stopAcknowledgedFixture.payload as PayloadStopAcknowledged,
  stopOutcome: stopOutcomeFixture.payload as PayloadStopOutcome,
  timing: timingSpanFixture.payload as PayloadTimingSpan,
  evidenceGap: evidenceGapFixture.payload as PayloadEvidenceGap,
  telemetryDropped: telemetryDroppedFixture.payload as PayloadTelemetryDropped,
  diagnostic: diagnosticFixture.payload as PayloadDiagnosticText,
  provenance: perceptionFixture.provenance as Provenance,
  idleSnapshot: idleSnapshotFixture as unknown as Snapshot,
};

export const SYNTHETIC_SESSION = 'synthetic-console-session';
export const FRAME_WIDTH_PX = 640;
export const FRAME_HEIGHT_PX = 480;

const PRODUCER: Producer = {
  name: 'synthetic-console-script',
  version: '1.0.0',
  artifact_digest: `sha256:${'a1'.padStart(64, '0')}`,
  contract_version: '1',
};

export function hostStamp(seq: number): SourceStamp {
  return { session: 'synthetic-host-session', seq, sample_mono_ns: 2_000_000_000 + seq * 33_000_000, clock_domain: 'pi_monotonic' };
}

/** One step of the script: an event, and how long after the previous step it is emitted. */
export interface ScriptStep {
  afterMs: number;
  event: ContractEvent;
}

export interface SyntheticRun {
  run_id: string;
  steps: ScriptStep[];
  /** The record and perception served for each observation the run captured. */
  observations: Record<string, { record: ObservationRecordDto; perception: PerceptionDto }>;
  /**
   * A range of seq the script genuinely never emits, and the step after which the runtime announces it. The gap is
   * real: nothing is sent for those numbers, so the banner is not claiming a loss that did not happen. Null for a run
   * that skips nothing.
   */
  resync: { after_step: number; retained_from_seq: number; missed: [number, number] } | null;
}

export class Builder {
  private seq: number;
  private mono: number;
  readonly steps: ScriptStep[] = [];

  constructor(
    private readonly runId: string,
    startSeq: number,
    startMonoNs: number,
  ) {
    this.seq = startSeq;
    this.mono = startMonoNs;
  }

  get nextSeq(): number {
    return this.seq;
  }

  get nextMono(): number {
    return this.mono;
  }

  /** Burn `count` sequence numbers without emitting anything for them, and say which ones. */
  skip(count: number): [number, number] {
    const from = this.seq + 1;
    this.seq += count;
    return [from, this.seq];
  }

  add<K extends ContractEvent['kind']>(
    afterMs: number,
    kind: K,
    payload: Extract<ContractEvent, { kind: K }>['payload'],
    extra: Partial<EventEnvelope> = {},
  ): ContractEvent {
    this.seq += 1;
    this.mono += Math.max(afterMs, 1) * 1_000_000;
    const envelope: EventEnvelope = {
      schema_version: '1',
      seq: this.seq,
      run_id: this.runId,
      action_id: null,
      observation_id: null,
      producer: PRODUCER,
      source_session: SYNTHETIC_SESSION,
      t_mono_ns: this.mono,
      clock_domain: 'laptop_monotonic',
      t_wall_utc: null,
      reason_code: null,
      provenance: null,
      ...extra,
    };
    const event = { ...envelope, kind, payload } as ContractEvent;
    this.steps.push({ afterMs, event });
    return event;
  }
}

/** A synthetic wrist observation: what the runtime would serve for one stored frame. */
function record(observationId: string, gripper: string, held: boolean, sector: number): ObservationRecordDto {
  return {
    observation_id: observationId,
    timestamp_utc: null,
    gripper_state: gripper,
    cube_held: held,
    camera: { role: 'wrist', image_orientation: 'upright', width_px: FRAME_WIDTH_PX, height_px: FRAME_HEIGHT_PX },
    robot_state: {
      source: 'host observation',
      measured_joints_deg: { shoulder_pan: -10.4, shoulder_lift: -49.8, elbow_flex: 20.1, wrist_flex: 39.7, wrist_roll: 0.2, gripper: held ? 17.5 : 49.6 },
      joint_stability_max_delta_deg: 0.12,
    },
    camera_pose: { status: 'resolved', frame: 'PAD_FRAME', reason: null, missing_calibration: false },
    scan: { pose_id: 'low-scan', band: 'low', sector_index: sector, nominal_tool_depression_deg: -18, tool_azimuth_deg: -10.4 },
  };
}

/** A trapezoid that reads as a pad seen from above and in front; the homography is derived from these four points. */
const PAD_CORNERS_PX: [number, number][] = [
  [120, 150],
  [520, 150],
  [610, 430],
  [30, 430],
];

function perception(options: {
  padFrameValid: boolean;
  padReason: string | null;
  cubeBox: [number, number, number, number] | null;
  contact: [number, number] | null;
  centrePadM: [number, number] | null;
  insidePad: boolean | null;
}): PerceptionDto {
  const box = options.cubeBox;
  return {
    pad_note: options.padFrameValid ? 'four landmarks matched' : 'the pad frame was not resolved for this frame',
    pad: {
      detected: true,
      confidence: 0.82,
      visible_fraction: 0.74,
      clipped_sides: ['left'],
      orientation_resolved: options.padFrameValid,
      boundary_rms_px: 2.4,
      camera_height_m: 0.31,
      view_elevation_deg: 38.5,
      corners_px: options.padFrameValid ? PAD_CORNERS_PX : null,
    },
    pad_by_template: {
      pad_identity: 'pad-template-a',
      landmark_inliers: options.padFrameValid ? 4 : 1,
      pad_frame_valid: options.padFrameValid,
      reason: options.padReason,
    },
    cube: {
      detected: box !== null,
      confidence: box !== null ? 0.71 : 0.12,
      clipped: false,
      box_px: box,
      contact_px: options.contact,
      mask_polygon_px:
        box === null
          ? null
          : [
              [box[0], box[1]],
              [box[2], box[1]],
              [box[2] - 4, box[3]],
              [box[0] + 4, box[3]],
            ],
    },
    cube_by_template: { cube_center_pad_m: options.centrePadM, inside_pad_outline: options.insidePad },
    homography_pad_to_image: null, // derived from the corners, exactly as a live frame without a served matrix is
    jaw_window_px: [296, 286, 366, 372],
    landmarks_px: options.padFrameValid
      ? [
          [150, 178],
          [492, 178],
          [572, 404],
          [66, 404],
        ]
      : null,
  };
}

export function provenanceFor(observationIds: string[], reconstructed: boolean): Provenance {
  return { ...FIXTURE.provenance, observation_ids: observationIds, reconstructed };
}

/**
 * The controller's own state line, as one diagnostic.text record: `[STATE] <name> {fields}`, spelled the way Python's
 * json.dumps spells it (a space after every colon and comma), because that is the line the runtime relays and the
 * line `model/steps.ts` reads the sweep's sector and the selection's mode from. The contract's state.transition
 * carries no fields, so this is the only place they travel (run_single_cube_cycle.py, "the fields have no other place
 * in the contract").
 */
export function stateLineText(name: string, fields: Record<string, unknown>): string {
  const body = Object.entries(fields)
    .map(([key, value]) => `${JSON.stringify(key)}: ${JSON.stringify(value)}`)
    .join(', ');
  return `[STATE] ${name} {${body}}`;
}

/**
 * The emitters one scripted run is written with. Every record starts as a contract fixture and is varied from there,
 * exactly as before they were lifted out of `buildRun`; the three runs below share them so the shapes on the screen
 * are one set of shapes.
 */
export function emitters(index: number, builder: Builder, observations: SyntheticRun['observations']) {
  const capture = (
    observationId: string,
    afterMs: number,
    options: Parameters<typeof perception>[0],
    gripper: string,
    held: boolean,
    sector: number,
  ): void => {
    observations[observationId] = { record: record(observationId, gripper, held, sector), perception: perception(options) };
    builder.add(afterMs, 'observation.captured', { ...FIXTURE.observation, width_px: FRAME_WIDTH_PX, height_px: FRAME_HEIGHT_PX, source_stamp: hostStamp(40 + sector) }, {
      observation_id: observationId,
    });
  };

  const perceive = (observationId: string, detected: boolean, measurements: PayloadPerceptionResult['measurements'], afterMs: number): void => {
    builder.add(
      afterMs,
      'perception.result',
      { ...FIXTURE.perception, observation_ids: [observationId], result: detected ? 'detected' : 'not_detected', confidence: detected ? 0.71 : 0.12, measurements },
      { provenance: provenanceFor([observationId], false) },
    );
  };

  const transition = (to: string, from: string | null, afterMs: number): void => {
    builder.add(afterMs, 'state.transition', { ...FIXTURE.transition, machine: 'cycle', from_state: from, to_state: to }, {});
  };

  const action = (actionId: string, type: string, parameters: PayloadActionPlanned['parameters'], observationId: string | null, durationMs: number): void => {
    builder.add(
      320,
      'action.planned',
      { ...FIXTURE.actionPlanned, action_type: type, plan_only: false, parameters },
      { action_id: actionId, provenance: observationId === null ? null : provenanceFor([observationId], false) },
    );
    builder.add(180, 'action.started', { ...FIXTURE.actionStarted, action_type: type }, { action_id: actionId });
    builder.add(durationMs, 'action.completed', { ...FIXTURE.actionCompleted, action_type: type, result: 'ok', duration_ns: durationMs * 1_000_000 }, { action_id: actionId });
    builder.add(60, 'timing.span', { ...FIXTURE.timing, span: 'motion', duration_ns: durationMs * 1_000_000 }, {});
  };

  const predicate = (
    name: string,
    value: boolean | null,
    measured: number | null,
    threshold: number | null,
    unit: string | null,
    evidence: PayloadPredicateEvaluated['evidence'],
    afterMs: number,
  ): void => {
    builder.add(
      afterMs,
      'predicate.evaluated',
      { ...FIXTURE.predicate, predicate: name, value, measured, threshold, unit, evidence, consumed_source_stamp: hostStamp(48) },
      { provenance: provenanceFor(evidence.flatMap((piece) => piece.observation_ids), true) },
    );
  };

  const frameEvidence = (observationId: string): PayloadPredicateEvaluated['evidence'] => [
    { type: 'frame', observation_ids: [observationId], scalar_name: null },
  ];

  /** The controller's [STATE] line for a state with fields, as the one diagnostic record it travels in. */
  const stateLine = (name: string, fields: Record<string, unknown>, afterMs: number): void => {
    builder.add(afterMs, 'diagnostic.text', { ...FIXTURE.diagnostic, text: stateLineText(name, fields) }, {});
  };

  const obs = (name: string): string => `synthetic-obs-${String(index).padStart(2, '0')}-${name}`;

  // THE CUBE'S PAD-FRAME POSITION of a captured frame, as the controller records it for every frame (the detector
  // `cube-in-pad-frame`, as the newer builders of `syntheticScenarios.ts` emit it), from the position the capture already
  // carries: nothing new is invented (browser r2 N3). The Scene reads a block seen at SEARCH from it.
  const padPosition = (observationId: string, centrePadM: readonly [number, number], insidePad: boolean | null, afterMs: number): void => {
    builder.add(
      afterMs,
      'perception.result',
      {
        ...FIXTURE.perception,
        detector: 'cube-in-pad-frame',
        observation_ids: [observationId],
        result: 'detected',
        confidence: null,
        measurements: [
          { name: 'cube_center_pad_x', value: centrePadM[0], unit: 'm' },
          { name: 'cube_center_pad_y', value: centrePadM[1], unit: 'm' },
          { name: 'inside_pad_outline', value: insidePad, unit: null },
        ],
      },
      { observation_id: observationId, provenance: provenanceFor([observationId], false) },
    );
  };

  return { capture, perceive, transition, action, predicate, frameEvidence, stateLine, obs, padPosition };
}

/** The cube where the scripted runs first see it: on the wood beyond the pad, its centre outside the pad outline. */
const CUBE_FAR = { cubeBox: [300, 300, 352, 352] as [number, number, number, number], contact: [326, 352] as [number, number], centrePadM: [0.062, -0.028] as [number, number] };
/** Where the one-block rehearsal's carrying run records the placed block, in PAD_FRAME metres. */
const PLACED_PAD_M: [number, number] = [-0.041, 0.019];

/**
 * One full cycle: search, grasp, carry, place, return. `startSeq` continues the runtime session's single ordered
 * stream, so a second cycle never reuses a seq.
 */
export function buildRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const runId = `synthetic-run-${String(index).padStart(4, '0')}`;
  const commandId = `synthetic-command-${String(index).padStart(4, '0')}`;
  const builder = new Builder(runId, startSeq, startMonoNs);
  const observations: SyntheticRun['observations'] = {};
  const { capture, perceive, transition, action, predicate, frameEvidence } = emitters(index, builder, observations);

  const search = `synthetic-obs-${String(index).padStart(2, '0')}-search`;
  const align = `synthetic-obs-${String(index).padStart(2, '0')}-align`;
  const grasp = `synthetic-obs-${String(index).padStart(2, '0')}-grasp`;
  const hold = `synthetic-obs-${String(index).padStart(2, '0')}-hold`;
  const placed = `synthetic-obs-${String(index).padStart(2, '0')}-placed`;

  builder.add(400, 'run.started', { ...FIXTURE.runStarted, command_id: commandId, command_type: 'start_cycle', data_mode: 'synthetic', plan_only: false, holding: false }, {});
  builder.add(150, 'run.config', FIXTURE.runConfig, {});
  builder.add(150, 'diagnostic.text', { ...FIXTURE.diagnostic, text: 'synthetic script: no process, no robot and no camera were involved in producing this line' }, {});

  transition('SEARCH', null, 500);
  capture(search, 700, { padFrameValid: true, padReason: null, cubeBox: [300, 300, 352, 352], contact: [326, 352], centrePadM: [0.062, -0.028], insidePad: false }, 'open', false, 1);
  perceive(search, true, [{ name: 'cube_out', value: 214.0, unit: 'mm' }], 420);
  predicate('cube_visible', true, 0.71, 0.4, null, frameEvidence(search), 260);
  predicate('cube_inside_pad', false, -34.0, 0.0, 'mm', [{ type: 'pad_measurement', observation_ids: [search], scalar_name: null }], 200);

  transition('PREGRASP', 'SEARCH', 400);
  action(`synthetic-action-${String(index)}-01`, 'pan_step', [{ name: 'deg', value: -3.5, unit: 'deg' }], search, 1500);
  builder.add(200, 'measured.state', { ...FIXTURE.measuredState, consumed_source_stamp: hostStamp(52) }, {});

  transition('ALIGN', 'PREGRASP', 350);
  capture(align, 600, { padFrameValid: true, padReason: null, cubeBox: [306, 298, 358, 350], contact: [332, 350], centrePadM: [0.058, -0.03], insidePad: false }, 'open', false, 2);
  perceive(align, true, [{ name: 'lateral_mm', value: 8.4, unit: 'mm' }], 380);
  action(`synthetic-action-${String(index)}-02`, 'align_step', [{ name: 'lateral_mm', value: 8.4, unit: 'mm' }], align, 1200);

  transition('APPROACH', 'ALIGN', 350);
  action(`synthetic-action-${String(index)}-03`, 'tool_step', [{ name: 'forward_mm', value: 42, unit: 'mm' }, { name: 'dz_mm', value: -6, unit: 'mm' }], align, 1800);

  transition('STRADDLING', 'APPROACH', 300);
  // This frame has NO valid pad frame. The cube layers stay valid; the pad layers do not.
  capture(grasp, 500, { padFrameValid: false, padReason: 'too few landmarks matched at this range', cubeBox: [292, 282, 372, 368], contact: [332, 368], centrePadM: null, insidePad: null }, 'open', false, 3);
  perceive(grasp, true, [{ name: 'jaw_clearance_mm', value: 6.2, unit: 'mm' }], 320);
  predicate('jaws_around_cube', true, 6.2, 3.0, 'mm', frameEvidence(grasp), 220);

  transition('GRASP_CLOSE', 'STRADDLING', 300);
  action(`synthetic-action-${String(index)}-04`, 'grip_latch', [{ name: 'action', value: 'latch', unit: null }], grasp, 900);

  transition('OBJECT_SECURED', 'GRASP_CLOSE', 350);
  predicate('object_secured', true, 0.86, 0.6, null, [{ type: 'frame_pair', observation_ids: [grasp], scalar_name: null }, { type: 'scalar', observation_ids: [], scalar_name: 'retention_score' }], 400);

  transition('PLACEMENT_PLAN', 'OBJECT_SECURED', 350);
  builder.add(300, 'timing.span', { ...FIXTURE.timing, span: 'planning', duration_ns: 42_000_000 }, {});

  transition('BASE_MEASURED', 'PLACEMENT_PLAN', 400);
  builder.add(250, 'diagnostic.text', { ...FIXTURE.diagnostic, text: 'synthetic script: base speed measured from a timed pulse, 0.118 m/s' }, {});

  transition('BASE_ADVANCE', 'BASE_MEASURED', 400);
  action(`synthetic-action-${String(index)}-05`, 'base_pulse', [{ name: 'seconds', value: 0.6, unit: 's' }], null, 1600);
  builder.add(200, 'telemetry.dropped', FIXTURE.telemetryDropped, {});

  // The runtime's ring no longer covers these records. They are genuinely never sent, and the resync below says so.
  const missed = builder.skip(4);
  const resyncAfterStep = builder.steps.length;

  transition('HOLD_OBSERVE', 'BASE_ADVANCE', 400);
  capture(hold, 600, { padFrameValid: true, padReason: null, cubeBox: null, contact: null, centrePadM: null, insidePad: null }, 'latched', true, 4);
  perceive(hold, false, [{ name: 'anchor_age_s', value: 3.4, unit: 's' }], 350);
  builder.add(220, 'evidence.gap', { ...FIXTURE.evidenceGap, gap: 'frame_missing', detail: 'the cube is inside the jaws and not visible from the wrist while the base travels', observation_ids: [hold] }, {
    reason_code: 'frame_missing',
  });

  transition('CARRY', 'HOLD_OBSERVE', 400);
  action(`synthetic-action-${String(index)}-06`, 'carry_step', [{ name: 'forward_mm', value: 55, unit: 'mm' }], null, 1700);

  transition('RELEASE_ADMISSION', 'CARRY', 400);
  predicate('release_admitted', true, 31.0, 6.0, 'mm', [{ type: 'anchor', observation_ids: [hold], scalar_name: null }], 400);

  transition('RELEASED', 'RELEASE_ADMISSION', 350);
  action(`synthetic-action-${String(index)}-07`, 'grip_open', [{ name: 'action', value: 'open', unit: null }], null, 800);

  transition('PLACED', 'RELEASED', 400);
  capture(placed, 700, { padFrameValid: true, padReason: null, cubeBox: [268, 322, 318, 372], contact: [293, 372], centrePadM: [-0.041, 0.019], insidePad: true }, 'open', false, 5);
  perceive(placed, true, [{ name: 'centre_to_edge_mm', value: 31.0, unit: 'mm' }], 420);
  predicate('placed', true, 31.0, 0.0, 'mm', [{ type: 'pad_measurement', observation_ids: [placed], scalar_name: null }], 300);
  predicate('clearance_target_met', true, 31.0, 3.0, 'mm', [{ type: 'pad_measurement', observation_ids: [placed], scalar_name: null }], 260);

  transition('BASE_RETURN', 'PLACED', 400);
  action(`synthetic-action-${String(index)}-08`, 'base_pulse', [{ name: 'seconds', value: -0.6, unit: 's' }], null, 1600);

  transition('BASE_RETURNED', 'BASE_RETURN', 400);
  predicate('base_returned', true, 12.0, 25.0, 'mm', [{ type: 'scalar', observation_ids: [], scalar_name: 'remaining_along_axis_mm' }], 350);

  builder.add(500, 'run.finished', { ...FIXTURE.runFinished, outcome: 'placed_new', exit_status: 0, evidence_complete: false, evidence_gaps: 1 }, {});

  return { run_id: runId, steps: builder.steps, observations, resync: { after_step: resyncAfterStep, retained_from_seq: missed[1] + 1, missed } };
}

/** A stop, appended to whatever is playing. */
export function buildStop(runId: string, startSeq: number, startMonoNs: number, commandId: string | null): ScriptStep[] {
  const builder = new Builder(runId, startSeq, startMonoNs);
  builder.add(10, 'stop.requested', { ...FIXTURE.stopRequested, origin: 'operator_console', command_id: commandId }, {});
  builder.add(90, 'stop.acknowledged', { ...FIXTURE.stopAcknowledged, acknowledged_by: 'synthetic-runtime', command_id: commandId, latency_ns: 90_000_000 }, {});
  builder.add(700, 'stop.outcome', { ...FIXTURE.stopOutcome, outcome: 'holding', motion_ceased: true, evidence: 'synthetic: the script stopped emitting motion; nothing was measured' }, {});
  builder.add(200, 'run.finished', { ...FIXTURE.runFinished, outcome: 'stopped', exit_status: null, predicates: { object_secured: true, placed: null, clearance_target_met: null, base_returned: null }, evidence_complete: false, evidence_gaps: 3, task_control_interventions: 1 }, {});
  return builder.steps;
}

/* ---- the one-block demo, rehearsed ----------------------------------------------------------------------------- */

/**
 * THE MEASURING RUN of the one-block mission (docs/autonomous-demo/one-block-demo-2026-09-26.md, T1 and T2): the
 * low-pose frame shows no cube, the wrist looks through its sweep sectors and finds the block in the RIGHT one, the
 * block is taken as the mission's target and measures BASE_ADVANCE, and the run ends there, before anything is picked
 * up, with `mission_capability_required`, because it carries no capability. The mission then waits for it.
 *
 * The sector, pan and outcome fields of the sweep line are the shape this console reads; the sweep track names the
 * fields the controller writes, and `model/steps.ts` accepts the spellings it knows.
 */
export function buildMeasuringRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const runId = `synthetic-run-${String(index).padStart(4, '0')}`;
  const commandId = `synthetic-command-${String(index).padStart(4, '0')}`;
  const builder = new Builder(runId, startSeq, startMonoNs);
  const observations: SyntheticRun['observations'] = {};
  const { capture, perceive, transition, action, predicate, frameEvidence, stateLine, obs, padPosition } = emitters(index, builder, observations);

  builder.add(400, 'run.started', { ...FIXTURE.runStarted, command_id: commandId, command_type: 'start_cycle', data_mode: 'synthetic', plan_only: false, holding: false }, {});
  // THE SWEEP RUNS ONLY UNDER ITS WORD at the head (policy_search, search_sweep): this rehearsal names it, as a runtime
  // configured with it passes it to every run of the mission.
  builder.add(150, 'run.config', cycleConfig(ONE_BLOCK_WORDS), {});
  builder.add(150, 'diagnostic.text', { ...FIXTURE.diagnostic, text: 'synthetic script: the one-block rehearsal; no process, no robot and no camera were involved in producing this line' }, {});
  builder.add(60, 'diagnostic.text', { ...FIXTURE.diagnostic, text: 'synthetic script: this run names the word search_sweep, under which the head sweeps; with no word the head refuses a block the low-pose frame does not show, before anything moves' }, {});

  transition('SEARCH', null, 500);
  stateLine('SEARCH', { pose: { shoulder_pan: -10.4, shoulder_lift: -49.8, elbow_flex: 20.1, wrist_flex: 39.7, wrist_roll: 0.2 } }, 60);
  // The first frame: a valid pad frame, and no cube in it.
  capture(obs('search'), 700, { padFrameValid: true, padReason: null, cubeBox: null, contact: null, centrePadM: null, insidePad: null }, 'open', false, 1);
  perceive(obs('search'), false, [], 420);

  // The sweep: LEFT shows nothing, RIGHT shows the block.
  transition('SEARCH_SWEEP', 'SEARCH', 400);
  stateLine('SEARCH_SWEEP', { sector: 'LEFT', pan_deg: -30.0, outcome: 'no_cube' }, 60);
  action(`synthetic-action-${String(index)}-s1`, 'arm_pose', [{ name: 'label', value: 'sweep LEFT', unit: null }], obs('search'), 1400);
  capture(obs('left'), 600, { padFrameValid: true, padReason: null, cubeBox: null, contact: null, centrePadM: null, insidePad: null }, 'open', false, 2);
  perceive(obs('left'), false, [], 400);
  transition('SEARCH_SWEEP', 'SEARCH_SWEEP', 400);
  stateLine('SEARCH_SWEEP', { sector: 'RIGHT', pan_deg: 30.0, outcome: 'cube_seen' }, 60);
  action(`synthetic-action-${String(index)}-s2`, 'arm_pose', [{ name: 'label', value: 'sweep RIGHT', unit: null }], obs('left'), 1400);
  capture(obs('right'), 600, { padFrameValid: true, padReason: null, ...CUBE_FAR, insidePad: false }, 'open', false, 3);
  perceive(obs('right'), true, [{ name: 'cube_out', value: 214.0, unit: 'mm' }], 420);
  padPosition(obs('right'), CUBE_FAR.centrePadM, false, 90);
  predicate('cube_seen_with_a_contact_point', true, 0.71, 0.4, null, frameEvidence(obs('right')), 260);
  predicate('cube_inside_pad', false, -34.0, 0.0, 'mm', [{ type: 'pad_measurement', observation_ids: [obs('right')], scalar_name: null }], 200);

  // The block is the target, and it measures BASE_ADVANCE: the run carries no capability and ends before the pickup.
  transition('TARGET_SELECTED', 'SEARCH_SWEEP', 400);
  stateLine('TARGET_SELECTED', { target_id: 'cube', mode: 'BASE_ADVANCE', seen_pad_m: [0.062, -0.028], bound_pad_m: [0.062, -0.028], distance_to_binding_mm: 0.0, cubes_listed: 1, listed_pass: 1, targets_bound: 1, candidates: 1, on_pad_ignored: 0 }, 60);
  builder.add(500, 'run.finished', {
    ...FIXTURE.runFinished,
    outcome: 'failed',
    exit_status: 5,
    predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
    evidence_complete: true,
    evidence_gaps: 0,
    reason_code: 'mission_capability_required',
    phase: 'TARGET_SELECTED',
    failure: {
      detail: "SEARCH: this target's cube measures BASE_ADVANCE, which needs the mobile_approach capability, and this run carries none; nothing was picked up",
      measurements: [
        { name: 'declared_mode', value: 'auto', unit: null },
        { name: 'measured_mode', value: 'BASE_ADVANCE', unit: null },
      ],
      observation_ids: [obs('right')],
    },
  }, {});

  return { run_id: runId, steps: builder.steps, observations, resync: null };
}

/**
 * THE CARRYING RUN of the one-block mission: the same target, resumed with the capability in the slot. The block is
 * seen at once, selected again with its measured BASE_ADVANCE, picked, and carried by the base in bounded pulses with
 * the pad observed between them; then placed, and the base returns. It ends `placed_new`, and the mission completes.
 */
export function buildCarryingRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const runId = `synthetic-run-${String(index).padStart(4, '0')}`;
  const commandId = `synthetic-command-${String(index).padStart(4, '0')}`;
  const builder = new Builder(runId, startSeq, startMonoNs);
  const observations: SyntheticRun['observations'] = {};
  const { capture, perceive, transition, action, predicate, frameEvidence, stateLine, obs, padPosition } = emitters(index, builder, observations);
  const anchor = (id: string): PayloadPredicateEvaluated['evidence'] => [{ type: 'anchor', observation_ids: [id], scalar_name: null }];

  builder.add(400, 'run.started', { ...FIXTURE.runStarted, command_id: commandId, command_type: 'start_cycle', data_mode: 'synthetic', plan_only: false, holding: false }, {});
  builder.add(150, 'run.config', cycleConfig(ONE_BLOCK_WORDS), {});
  builder.add(150, 'diagnostic.text', { ...FIXTURE.diagnostic, text: 'synthetic script: the one-block rehearsal resumed with the capability; nothing here moved a robot' }, {});

  transition('SEARCH', null, 500);
  capture(obs('search'), 700, { padFrameValid: true, padReason: null, ...CUBE_FAR, insidePad: false }, 'open', false, 1);
  perceive(obs('search'), true, [{ name: 'cube_out', value: 214.0, unit: 'mm' }], 420);
  padPosition(obs('search'), CUBE_FAR.centrePadM, false, 90);
  predicate('cube_seen_with_a_contact_point', true, 0.71, 0.4, null, frameEvidence(obs('search')), 260);
  predicate('cube_inside_pad', false, -34.0, 0.0, 'mm', [{ type: 'pad_measurement', observation_ids: [obs('search')], scalar_name: null }], 200);
  transition('TARGET_SELECTED', 'SEARCH', 400);
  stateLine('TARGET_SELECTED', { target_id: 'cube', mode: 'BASE_ADVANCE', seen_pad_m: [0.062, -0.028], bound_pad_m: [0.062, -0.028], distance_to_binding_mm: 1.2, cubes_listed: 1, listed_pass: 1, targets_bound: 1, candidates: 1, on_pad_ignored: 0 }, 60);

  transition('PREGRASP', 'TARGET_SELECTED', 400);
  action(`synthetic-action-${String(index)}-01`, 'pan_step', [{ name: 'deg', value: -3.5, unit: 'deg' }], obs('search'), 1500);
  builder.add(200, 'measured.state', { ...FIXTURE.measuredState, consumed_source_stamp: hostStamp(52) }, {});

  transition('ALIGN', 'PREGRASP', 350);
  capture(obs('align'), 600, { padFrameValid: true, padReason: null, cubeBox: [306, 298, 358, 350], contact: [332, 350], centrePadM: [0.058, -0.03], insidePad: false }, 'open', false, 2);
  perceive(obs('align'), true, [{ name: 'lateral_mm', value: 8.4, unit: 'mm' }], 380);
  action(`synthetic-action-${String(index)}-02`, 'align_step', [{ name: 'lateral_mm', value: 8.4, unit: 'mm' }], obs('align'), 1200);

  transition('APPROACH', 'ALIGN', 350);
  action(`synthetic-action-${String(index)}-03`, 'tool_step', [{ name: 'forward_mm', value: 42, unit: 'mm' }, { name: 'dz_mm', value: -6, unit: 'mm' }], obs('align'), 1800);

  transition('STRADDLING', 'APPROACH', 300);
  capture(obs('grasp'), 500, { padFrameValid: false, padReason: 'too few landmarks matched at this range', cubeBox: [292, 282, 372, 368], contact: [332, 368], centrePadM: null, insidePad: null }, 'open', false, 3);
  perceive(obs('grasp'), true, [{ name: 'jaw_clearance_mm', value: 6.2, unit: 'mm' }], 320);
  predicate('jaws_around_cube', true, 6.2, 3.0, 'mm', frameEvidence(obs('grasp')), 220);

  transition('GRASP_CLOSE', 'STRADDLING', 300);
  action(`synthetic-action-${String(index)}-04`, 'grip_latch', [{ name: 'action', value: 'latch', unit: null }], obs('grasp'), 900);

  transition('OBJECT_SECURED', 'GRASP_CLOSE', 350);
  predicate('object_secured', true, 0.86, 0.6, null, [{ type: 'frame_pair', observation_ids: [obs('grasp')], scalar_name: null }, { type: 'scalar', observation_ids: [], scalar_name: 'retention_score' }], 400);

  transition('PLACEMENT_PLAN', 'OBJECT_SECURED', 350);
  stateLine('PLACEMENT_PLAN', { mode: 'BASE_ADVANCE', needed_mm: 74.0, anchor_margin_mm: 41.2, required_centre_margin_mm: 37.96 }, 60);
  builder.add(300, 'timing.span', { ...FIXTURE.timing, span: 'planning', duration_ns: 42_000_000 }, {});

  // The base, in bounded pulses, with the pad observed between them. The cube is inside the jaws and is not visible.
  transition('HOLD_OBSERVE', 'PLACEMENT_PLAN', 400);
  capture(obs('hold1'), 600, { padFrameValid: true, padReason: null, cubeBox: null, contact: null, centrePadM: null, insidePad: null }, 'latched', true, 4);
  perceive(obs('hold1'), false, [{ name: 'anchor_age_s', value: 1.1, unit: 's' }], 350);
  transition('BASE_MEASURED', 'HOLD_OBSERVE', 400);
  builder.add(250, 'diagnostic.text', { ...FIXTURE.diagnostic, text: 'synthetic script: base speed measured from a timed pulse, 0.118 m/s' }, {});
  transition('BASE_ADVANCE', 'BASE_MEASURED', 400);
  stateLine('BASE_ADVANCE', { pulse: 1, planned_mm: 40.0, advanced_mm: 0.0, remaining_mm: 74.0 }, 60);
  action(`synthetic-action-${String(index)}-05`, 'base_pulse', [{ name: 'seconds', value: 0.34, unit: 's' }], null, 1400);
  transition('HOLD_OBSERVE', 'BASE_ADVANCE', 400);
  capture(obs('hold2'), 600, { padFrameValid: true, padReason: null, cubeBox: null, contact: null, centrePadM: null, insidePad: null }, 'latched', true, 5);
  perceive(obs('hold2'), false, [{ name: 'anchor_age_s', value: 2.6, unit: 's' }], 350);
  transition('BASE_ADVANCE', 'HOLD_OBSERVE', 400);
  stateLine('BASE_ADVANCE', { pulse: 2, planned_mm: 34.0, advanced_mm: 41.0, remaining_mm: 33.0 }, 60);
  action(`synthetic-action-${String(index)}-06`, 'base_pulse', [{ name: 'seconds', value: 0.29, unit: 's' }], null, 1300);
  transition('HOLD_OBSERVE', 'BASE_ADVANCE', 400);
  capture(obs('hold3'), 600, { padFrameValid: true, padReason: null, cubeBox: null, contact: null, centrePadM: null, insidePad: null }, 'latched', true, 6);
  perceive(obs('hold3'), false, [{ name: 'anchor_age_s', value: 4.0, unit: 's' }], 350);

  transition('CARRY', 'HOLD_OBSERVE', 400);
  stateLine('CARRY', { mode: 'BASE_ADVANCE', tool: 'out 168.0 mm, up 41.0 mm' }, 60);
  action(`synthetic-action-${String(index)}-07`, 'carry_step', [{ name: 'forward_mm', value: 55, unit: 'mm' }], null, 1700);

  transition('RELEASE_ADMISSION', 'CARRY', 400);
  predicate('release_admitted', true, 31.0, 6.0, 'mm', anchor(obs('hold3')), 400);

  transition('RELEASED', 'RELEASE_ADMISSION', 350);
  action(`synthetic-action-${String(index)}-08`, 'grip_open', [{ name: 'action', value: 'open', unit: null }], null, 800);

  transition('PLACED', 'RELEASED', 400);
  capture(obs('placed'), 700, { padFrameValid: true, padReason: null, cubeBox: [268, 322, 318, 372], contact: [293, 372], centrePadM: PLACED_PAD_M, insidePad: true }, 'open', false, 7);
  perceive(obs('placed'), true, [{ name: 'centre_to_edge_mm', value: 31.0, unit: 'mm' }], 420);
  padPosition(obs('placed'), PLACED_PAD_M, true, 90);
  // THE PLACED LINE, in the controller's [STATE] shape, with the position and the edge distance this run's placed frame
  // records above (browser r2 N3): the Scene and the Complete card read the placement from it.
  stateLine('PLACED', { value: true, cube_pad_m: PLACED_PAD_M, centre_to_edge_mm: 31.0 }, 60);
  predicate('placed', true, 31.0, 0.0, 'mm', [{ type: 'pad_measurement', observation_ids: [obs('placed')], scalar_name: null }], 300);
  predicate('clearance_target_met', true, 31.0, 3.0, 'mm', [{ type: 'pad_measurement', observation_ids: [obs('placed')], scalar_name: null }], 260);

  transition('BASE_RETURN', 'PLACED', 400);
  stateLine('BASE_RETURN', { advanced_mm: 76.0 }, 60);
  action(`synthetic-action-${String(index)}-09`, 'base_pulse', [{ name: 'seconds', value: -0.34, unit: 's' }], null, 1400);
  action(`synthetic-action-${String(index)}-10`, 'base_pulse', [{ name: 'seconds', value: -0.29, unit: 's' }], null, 1300);

  transition('BASE_RETURNED', 'BASE_RETURN', 400);
  predicate('base_returned', true, 6.0, 25.0, 'mm', [{ type: 'scalar', observation_ids: [], scalar_name: 'remaining_along_axis_mm' }], 350);

  builder.add(500, 'run.finished', { ...FIXTURE.runFinished, outcome: 'placed_new', exit_status: 0, evidence_complete: true, evidence_gaps: 0 }, {});

  return { run_id: runId, steps: builder.steps, observations, resync: null };
}

/** The runtime's block code while its mission holds the one run slot (daemon.MISSION_HOLDS_RUN_SLOT), and its sentence. */
export const MISSION_RUNNING = 'mission_running';
export const MISSION_RUNNING_REASON = 'a mission is being carried: only stop is accepted';

/**
 * THE WORDS A RUN RAN UNDER, as the head's run.config states them (run_single_cube_cycle.stage_policy_summary): one
 * `policy.<stage>.selection` per stage in run_cycle's order, 'proven' for a stage run with none of its words, else its
 * words sorted and joined by '-' (stage_policy.selection_of). The stages' words are the head's (policy_*.WORDS). The
 * `policy.<stage>.digest` values beside them are the stage modules' sha256, which a script has none of, so they are left
 * out rather than invented.
 */
const STAGE_WORDS: ReadonlyArray<[string, readonly string[]]> = [
  ['search', ['scene_scan', 'scene_scan_25', 'scene_search', 'search_sweep']],
  ['align', ['coarse_v2', 'align_v2']],
  ['grasp', ['near_grasp', 'grasp_measured_retry', 'latch_midpoint', 'grasp_recover']],
  ['placement', ['projected', 'near_reverse', 'side_offset', 'base_lateral']],
  ['return', ['return_2d']],
];

export function policySelections(words: readonly string[]): { name: string; value: string; unit: null }[] {
  return STAGE_WORDS.map(([stage, own]) => {
    const chosen = [...new Set(words.filter((word) => own.includes(word)))].sort();
    return { name: `policy.${stage}.selection`, value: chosen.length === 0 ? 'proven' : chosen.join('-'), unit: null };
  });
}

/** The words the one-block rehearsal's runtime passes to every run of its mission. */
const ONE_BLOCK_WORDS = ['search_sweep'];

/** The run.config of a scripted cycle under `words`: the controller's own first fields, then the stage selections. */
function cycleConfig(words: readonly string[]): PayloadRunConfig {
  return {
    config_digest: FIXTURE.runConfig.config_digest,
    summary: [
      { name: 'controller', value: 'single-cube-cycle', unit: null },
      { name: 'action_kind', value: 'cycle', unit: null },
      { name: 'plan_only', value: false, unit: null },
      { name: 'holding', value: false, unit: null },
      ...policySelections(words),
    ],
  } as PayloadRunConfig;
}

/** The base snapshot, from the idle fixture, with the parts the player keeps current. */
export function syntheticSnapshot(current: {
  seq_cursor: number;
  retained_from_seq: number;
  running: boolean;
  run_id: string | null;
  command_id: string | null;
  state: string | null;
  started_seq: number;
  last_outcome: Snapshot['last_outcome'];
  /** The command the running run carries, as its own run.started names it. Absent: a cycle, as the plain script runs. */
  command_type?: CommandType | null;
  /**
   * True while a scenario's mission is carried (running, or held for the capability or the operator). The runtime then
   * takes only Stop: with no run active, every command capability is blocked with the mission's reason
   * (read_models.capabilities, block code mission_running), as the supervisor's block() answers it after run_active.
   */
  mission_carried?: boolean;
  /**
   * The robot's refusal of motion right now, (reason code, sentence), or null. As read_models.capabilities applies it: to
   * every command but Observe, and only when nothing shared blocks every command first.
   */
  motion_block?: { code: string; reason: string } | null;
  /**
   * THE COMMANDS THIS SCRIPT DOES NOT PLAY, each with the (reason code, sentence) its submit refuses them with: Look and
   * Scan scene where the scenario plays neither, Start where the scenario begins with something else. Applied after
   * what blocks every command and before the robot's motion refusal, in the order the source's submit refuses them, so
   * a command served on is a command the source admits.
   */
  refusals?: Partial<Record<string, { code: string; reason: string }>>;
}): Snapshot {
  const base = FIXTURE.idleSnapshot;
  const block: { code: string; reason: string } | null = current.running
    ? { code: 'run_active', reason: 'a run is already in progress' }
    : current.mission_carried === true
      ? { code: MISSION_RUNNING, reason: MISSION_RUNNING_REASON }
      : null;
  const motion = current.motion_block ?? null;
  const command = (id: string, dependency: string | null): Snapshot['capabilities'][number] => {
    const standing = current.refusals?.[id];
    return (block === null && standing !== undefined
      ? { id, state: 'blocked', reason_code: standing.code, reason: standing.reason, dependency: 'link', planned: false }
      : block === null && motion !== null && id !== 'observe'
        ? { id, state: 'blocked', reason_code: motion.code, reason: motion.reason, dependency: 'robot', planned: false }
        : { id, state: block === null ? 'enabled' : 'blocked', reason_code: block?.code ?? null, reason: block?.reason ?? null, dependency: block === null ? dependency : (id === 'observe' ? 'link' : 'owner'), planned: false }) as Snapshot['capabilities'][number];
  };
  // STOP, as read_models.capabilities serves it: on while a run is active or a mission is carried (the runtime's one stop
  // path ends that mission), off with its reason when there is nothing to stop.
  const stop = (current.running || current.mission_carried === true
    ? { id: 'stop', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false }
    : { id: 'stop', state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop', dependency: 'owner', planned: false }) as Snapshot['capabilities'][number];
  return {
    ...base,
    runtime_session: SYNTHETIC_SESSION,
    seq_cursor: current.seq_cursor,
    retained_from_seq: current.retained_from_seq,
    data_mode: 'synthetic',
    link: {
      state: 'connected',
      source_stamp: hostStamp(48),
      receive_age_ms: 38,
    },
    owner: current.running
      ? { kind: 'autonomous', run_id: current.run_id, lease: { host_session: 'synthetic-host-session', generation: 2, expires_in_ms: 450 } }
      : { kind: 'free', run_id: null, lease: null },
    active_run:
      current.running && current.run_id !== null
        ? { run_id: current.run_id, command_id: current.command_id, command_type: current.command_type ?? 'start_cycle', status: 'running', state: current.state, started_seq: current.started_seq }
        : null,
    last_outcome: current.last_outcome,
    capabilities: [
      command('start_cycle', null),
      command('observe', 'link'),
      command('return_arm', 'owner'),
      command('scan_scene', 'owner'),
      { id: 'manual_pan', state: 'blocked', reason_code: 'synthetic_source', reason: 'this console is replaying a script; there is no robot to pan', dependency: 'link', planned: false },
      { id: 'manual_move', state: 'blocked', reason_code: 'synthetic_source', reason: 'this console is replaying a script; there is no robot to move', dependency: 'link', planned: false },
      { id: 'manual_grip', state: 'blocked', reason_code: 'synthetic_source', reason: 'this console is replaying a script; there is no gripper', dependency: 'link', planned: false },
      { id: 'manual_base_pulse', state: 'blocked', reason_code: 'synthetic_source', reason: 'this console is replaying a script; there is no base to drive', dependency: 'link', planned: false },
      stop,
      ...base.capabilities.filter((capability) => capability.id === 'cake_update'),
    ] as Snapshot['capabilities'],
  };
}

export const SYNTHETIC_TAP_JOINTS = FIXTURE.measuredState.joints;
