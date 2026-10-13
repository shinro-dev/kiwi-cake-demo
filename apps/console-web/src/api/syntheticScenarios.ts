// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SCENARIO CATALOGUE OF THE SIMULATION (docs/autonomous-demo/overnight-plan-2026-09-26.md, track U2).
//
// Each scenario is chosen by the address, `?source=simulation&scenario=<name>`, and plays NOTHING until the operator
// presses what starts it (Start, Look, or a capability check). There is one scenario per failure mode of the live
// session of 2026-09-26 and per success of that session, and one per behaviour the overnight tracks add. They exist so
// the new screen can be verified offline in a browser, in every state the robot put it in, without the robot.
//
// WHAT IS REAL AND WHAT IS NOT. In every scenario but `one-block`, the state names, the [STATE] field shapes, the
// perception detectors and their measurement names, the predicate names and the reason codes are the controller's own,
// read from `bin/laptop/manipulation/run_single_cube_cycle.py`, `autonomy/manipulation/target_binding.py` and the
// recorded event streams of the day. `one-block` is the older rehearsal of `syntheticScript.ts` and keeps that file's
// own invented shapes. For the four recorded runs (m8c5f9e9, mb3c1048, m2476137 and the Observe 0i5xpdmx), the [STATE]
// lines, the predicate booleans, thresholds, units and scalar names, and the run.finished payloads are the recorded
// ones, with three differences: PLACED's cube_pad_m is rounded here to 4 decimals, some predicate measured values are
// rounded where the controller emits them unrounded, and the base_zero predicates are left out. The two measuring runs
// m31e0b20 and m07e475e play their cycle records' [STATE] lines and their SEARCH frames' recorded perception. The
// frames are not exact: several frame boxes are drawn rather than the recorded ones, PREGRASP's two recorded frames
// (the waypoint view and the view ALIGN decides from) are one frame here, the descent boxes are not the recorded ones,
// and predicate evidence is of type frame or scalar where the records also use frame_pair, anchor and pad_measurement.
// Everything else is invented and says so: every run is `data_mode: synthetic`, every mission `performs_motion: false`,
// the frames are drawn and never served as pictures, and every diagnostic line that is not a [STATE] line starts with
// "synthetic script". The records of 21:24 and 21:31 are on the robot and not in the local evidence; those two
// scenarios carry what the first-hand record of each gives (the block position, the PREGRASP coarse values, the ALIGN
// attempts, the reason code, the sentence and the attempt) around invented frames, and any cause beyond the
// recognizer's own note is a labelled console inference. The shapes the overnight tracks have not built yet (the scene
// scan's `scene-*` results, the operator hold's attempt record, the candidate roles) follow the plan's interface table
// (section 2.4); where the plan names no spelling, the one used here is a constant below, so integration changes it in
// one place.

import type { PayloadPredicateEvaluated, PayloadRunFinished } from '@kiwi/demo-contracts';
import type { ObservationRecordDto, PerceptionDto } from '../model/observation';
import type { MissionCandidate } from '../model/missionRecord';
import type { SyntheticScenario } from '../panels/sourceChoice';
import type { PreviewSample } from './types';
import { Builder, FIXTURE, policySelections, provenanceFor, stateLineText, buildCarryingRun, buildMeasuringRun } from './syntheticScript';
import type { SyntheticRun } from './syntheticScript';
import { BINDING_CONSTRAINT } from '../model/sceneMap';
import type { RuntimeStopState } from './syntheticRuntime';

/* ---- the names ------------------------------------------------------------------------------------------------- */

/** Every scenario, in the order the catalogue lists them, and the address that opens one: kept in `panels/sourceChoice.ts`. */
export { isScenarioName, SCENARIO_NAMES, scenarioUrl } from '../panels/sourceChoice';
export type ScenarioName = SyntheticScenario;

/**
 * THE OPERATOR HOLD (track R, plan section 2.4), in the runtime's own spelling (`demo_runtime/mission.py`): the mission
 * state is MISSION_WAITING, written WAITING_FOR_CAPABILITY for both waits, and the waiting reason `operator_decision`
 * is what tells this hold from a capability wait. The simulation writes exactly that; no other spelling reaches the page.
 */
export const HOLD_FOR_OPERATOR_STATE = 'WAITING_FOR_CAPABILITY';
export const OPERATOR_DECISION = 'operator_decision';

/**
 * The class of an attempt that failed with nothing held and the base home: the runtime's OUTCOME_RETRYABLE
 * (`demo_runtime/mission.py`), which is how `mission_execution.classify_outcome` classes a lost block at ALIGN and an
 * empty close with the gripper reopened.
 */
export const RETRYABLE_CLASS = 'retryable';

/** Where a failed attempt left the robot, as the runtime derives it from the run's own events (`syntheticRuntime.ts`). */
export type ScenarioStopState = RuntimeStopState;

/**
 * One attempt at a mission target, with the fields of the runtime's record `mission_target_attempt`: the attempt, the
 * run, the park run that preceded a retry, the class, the reason code, the phase, the measurements, the observations,
 * the stop state, the next actions and the budget left.
 */
export interface ScenarioAttemptRecord {
  attempt: number;
  run_id: string;
  park_run_id: string | null;
  outcome_class: string;
  reason_code: string | null;
  phase: string | null;
  measurements: Array<{ name: string; value: string | number | boolean | null; unit: string | null }>;
  observation_ids: string[];
  stop_state: ScenarioStopState;
  next_actions: string[];
  budget_left: number;
  t_wall_utc: string;
}

/* ---- what a scenario is ----------------------------------------------------------------------------------------- */

/** One scripted run, built from where the runtime's single ordered stream has got to. */
export type RunFactory = (index: number, startSeq: number, startMonoNs: number) => SyntheticRun;

/**
 * ONE RUN OF A MISSION'S FLOW, in the order the mission carries them. What the mission does after a run is not scripted
 * here: it is what the head's runtime decides from the run's own record (`syntheticRuntime.ts`, the ports of
 * classify_outcome and next_actions), and the capability wait when the run ends with mission_capability_required, as the
 * robot-side execution waits. A retry parks the arm first (the controller's `--do exit`, in a run of its own, as the runtime always does), then runs the
 * next step of the flow.
 */
export interface FlowStep {
  build: RunFactory;
}

/**
 * What the resident does with one prepared candidate: aborts it (the abort-swap), observes it (the swapped Plan),
 * refuses it before any transaction (the tampered twin, an INTEGRITY refusal), or completes it back to the inert
 * capsule (the undo candidate, which leaves the capability inactive).
 */
export type CandidateOutcome = 'rejected' | 'observed' | 'refused_before_transaction' | 'undo';

/**
 * A prepared candidate with the view track C gives it (plan section 2.4: key, payload digest, role, label, expect) and
 * what the simulated resident does with it. The list is served in the runtime's order, keys sorted, and the page never
 * preselects one; that rule is the screen's, and this list does nothing to help or hinder it.
 */
export interface ScenarioCandidate extends MissionCandidate {
  /**
   * The view the runtime's configuration gives the candidate (`cake.candidate_roles`). Optional: a runtime that names no
   * roles, as 8771 on 2026-09-27, serves key and digest only, and a scenario can serve them the same way.
   */
  role?: 'activate' | 'refused-demo' | 'tampered-demo' | 'undo';
  label?: string;
  expect?: string;
  outcome: CandidateOutcome;
}

export interface ScenarioSpec {
  name: ScenarioName;
  /** A few words: what the scenario is. */
  title: string;
  /** Which run of 2026-09-26 it reproduces, or which new behaviour, by its word or its track. */
  reproduces: string;
  /** What the screen has to show while it plays: the stage, the sentence, the numbers, the next actions. */
  shows: string;
  /** What the operator presses first. Nothing plays before it. `scan`: Scan scene, then Start. */
  trigger: 'start' | 'look' | 'check' | 'scan';
  /** The runs a mission plays in order, and what the mission does after each. Empty: Start is refused. */
  flow: FlowStep[];
  /** Why Start is refused in a scenario whose first press is not Start, in the runtime's code and words. */
  start_refusal?: { reason_code: string; detail: string };
  /** What Look plays. Absent: Look is refused as the simulation has always refused it. */
  look?: RunFactory;
  /** What the scene scan command plays on its own. */
  scan?: RunFactory;
  /** The prepared candidates a held mission offers. */
  candidates?: ScenarioCandidate[];
  /** The prepared Plans the capability slot check offers, with no mission. */
  check_candidates?: ScenarioCandidate[];
  /**
   * The robot the runtime reports; absent: the simulation reports no robot, as it always has. `bench`: a confirmed robot
   * whose resident lists the capability slot and its holder, the capability slot following the scenario's own slot (the
   * activations of a mission, or the capability check), with its host held in a check scenario.
   */
  robot?: 'not_confirmed' | 'host_outside_cake' | 'bench';
  /** The signature check the runtime serves: one run of an earlier runtime session, or of this one. Absent: none. */
  signature?: 'earlier-session' | 'this-session';
  /** The capability slot's generation before any activation of the mission. Absent: 3. */
  slot_generation?: number;
  /** The wrist preview the runtime reports; absent: no preview source is attached, as it always has. */
  preview?: 'stall' | 'not_configured';
  /**
   * Why the runtime admits no Retry at all (supervisor.mission_retry_refusal): a LIVE runtime without mission.retry
   * answers `mission_retry_not_enabled`, one with it and no attended label `mission_retry_not_attended`. Its holds then
   * serve only Stop and a Retry is refused with this code. Absent: the Retry is admitted (the switch on, or a rehearsal).
   */
  retry_refusal?: 'mission_retry_not_enabled' | 'mission_retry_not_attended';
}

/* ---- the frames ------------------------------------------------------------------------------------------------- */

const WIDTH = 1280;
const HEIGHT = 720;

/** A pad seen from the low scan pose in a 1280 x 720 frame: drawn, never measured. */
const PAD_CORNERS: [number, number][] = [
  [250, 250],
  [1030, 250],
  [1190, 700],
  [90, 700],
];

interface FrameSpec {
  cube: { box: [number, number, number, number]; contact: [number, number] | null; confidence: number; clipped?: Partial<Record<'top' | 'bottom' | 'left' | 'right', boolean>> } | null;
  /** The print-landmark inliers; the pad frame is valid from 12 on, as the recorded frames show (4 to 9 were not). */
  landmarks: number;
  cube_pad_m?: [number, number] | null;
  inside?: boolean | null;
  held?: boolean;
  pan_deg?: number;
  /** The pad outline fit, when it succeeded on this frame, as the recorded run carries it. Absent: the fit failed. */
  outline?: { confidence: number; boundary_rms: number; visible_fraction: number; view_elevation: number };
}

const PAD_VALID_FROM = 12;

function frameRecord(observationId: string, spec: FrameSpec): ObservationRecordDto {
  const held = spec.held ?? false;
  return {
    observation_id: observationId,
    timestamp_utc: null,
    gripper_state: held ? 'latched' : 'open',
    cube_held: held,
    camera: { role: 'wrist', image_orientation: 'upright', width_px: WIDTH, height_px: HEIGHT },
    robot_state: {
      source: 'host observation',
      measured_joints_deg: { shoulder_pan: spec.pan_deg ?? -9.67, shoulder_lift: -79.43, elbow_flex: 37.85, wrist_flex: 67.21, wrist_roll: -96.66, gripper: held ? 67.9 : 79.92 },
      joint_stability_max_delta_deg: 0.12,
    },
    camera_pose: { status: 'resolved', frame: 'PAD_FRAME', reason: null, missing_calibration: false },
    scan: { pose_id: 'low-scan', band: 'low', sector_index: 0, nominal_tool_depression_deg: -18, tool_azimuth_deg: spec.pan_deg ?? -9.67 },
  };
}

function framePerception(spec: FrameSpec): PerceptionDto {
  const valid = spec.landmarks >= PAD_VALID_FROM;
  const cube = spec.cube;
  const clipped = cube?.clipped ?? {};
  const anyClipped = Object.values(clipped).some((value) => value === true);
  return {
    pad_note: valid ? `synthetic: the pad located by its print, ${String(spec.landmarks)} landmarks` : 'synthetic: the pad frame was not resolved for this frame',
    pad: {
      detected: spec.outline !== undefined,
      confidence: spec.outline?.confidence ?? 0,
      visible_fraction: spec.outline?.visible_fraction ?? (valid ? 0.54 : 0),
      clipped_sides: valid ? ['bottom'] : null,
      orientation_resolved: valid,
      boundary_rms_px: spec.outline?.boundary_rms ?? (valid ? 1.04 : null),
      camera_height_m: null,
      view_elevation_deg: spec.outline?.view_elevation ?? (valid ? 54.6 : null),
      corners_px: valid ? PAD_CORNERS : null,
    },
    pad_by_template: { pad_identity: valid ? 'pad-print' : null, landmark_inliers: spec.landmarks, pad_frame_valid: valid, reason: valid ? null : 'too few print landmarks matched at this range' },
    cube:
      cube === null
        ? { detected: false, confidence: null, clipped: null, box_px: null, contact_px: null, mask_polygon_px: null }
        : {
            detected: true,
            confidence: cube.confidence,
            clipped: anyClipped,
            box_px: cube.box,
            contact_px: cube.contact,
            mask_polygon_px: [
              [cube.box[0], cube.box[1]],
              [cube.box[2], cube.box[1]],
              [cube.box[2], cube.box[3]],
              [cube.box[0], cube.box[3]],
            ],
          },
    cube_by_template: { cube_center_pad_m: valid ? (spec.cube_pad_m ?? null) : null, inside_pad_outline: valid ? (spec.inside ?? null) : null },
    homography_pad_to_image: null,
    jaw_window_px: [560, 520, 720, 700],
    landmarks_px: null,
  };
}

/* ---- the kit a run is written with ------------------------------------------------------------------------------- */

type Evidence = PayloadPredicateEvaluated['evidence'];
type Named = { name: string; value: string | number | boolean | null; unit: string | null };

const SYNTHETIC_DIGEST = `sha256:${'5e'.repeat(32)}`;

/**
 * The emitters of one scripted run in the controller's real shapes: a state is a `state.transition` followed by its
 * `[STATE]` line when it has fields, a look is one `observation.captured` followed by the four perception results the
 * controller records for a frame, and a failure is the `run.finished` its `failed()` writes.
 */
class Kit {
  readonly builder: Builder;
  readonly observations: SyntheticRun['observations'] = {};
  private current: string | null = null;
  private actions = 0;

  constructor(
    readonly index: number,
    readonly runId: string,
    startSeq: number,
    startMonoNs: number,
  ) {
    this.builder = new Builder(runId, startSeq, startMonoNs);
  }

  obs(name: string): string {
    return `synthetic-obs-${String(this.index).padStart(2, '0')}-${name}`;
  }

  start(commandType: 'start_cycle' | 'observe' | 'return_arm' | 'scan_scene' | null, actionKind: string, words: string[] = []): void {
    const commandId = `synthetic-command-${String(this.index).padStart(4, '0')}`;
    this.builder.add(400, 'run.started', { ...FIXTURE.runStarted, command_id: commandId, command_type: commandType, data_mode: 'synthetic', plan_only: false, holding: false }, {});
    const summary: Named[] = [
      { name: 'controller', value: 'single-cube-cycle', unit: null },
      { name: 'action_kind', value: actionKind, unit: null },
      { name: 'plan_only', value: false, unit: null },
      { name: 'holding', value: false, unit: null },
      ...policySelections(words),
    ];
    this.builder.add(150, 'run.config', { config_digest: SYNTHETIC_DIGEST, summary }, {});
    this.note('no process, no robot and no camera were involved in producing this run');
  }

  note(text: string): void {
    this.builder.add(120, 'diagnostic.text', { ...FIXTURE.diagnostic, text: `synthetic script: ${text}` }, {});
  }

  /** A transition, then the controller's [STATE] line for it when the state carries fields. */
  state(name: string, fields: Record<string, unknown> | null, afterMs = 450): void {
    this.builder.add(afterMs, 'state.transition', { ...FIXTURE.transition, machine: 'cycle', from_state: this.current, to_state: name }, {});
    this.current = name;
    if (fields !== null) this.builder.add(40, 'diagnostic.text', { ...FIXTURE.diagnostic, text: stateLineText(name, fields) }, {});
  }

  /** A [STATE] line with no transition: a state the controller prints but the machine does not enter. */
  line(name: string, fields: Record<string, unknown>): void {
    this.builder.add(40, 'diagnostic.text', { ...FIXTURE.diagnostic, text: stateLineText(name, fields) }, {});
  }

  /** One motion the controller planned and completed: an arm pose, the gripper, or a base pulse. */
  action(type: 'arm_pose' | 'gripper' | 'base_pulse', parameters: Named[], durationMs: number): void {
    this.actions += 1;
    const actionId = `synthetic-act-${String(this.index).padStart(2, '0')}-${String(this.actions).padStart(3, '0')}`;
    this.builder.add(300, 'action.planned', { ...FIXTURE.actionPlanned, action_type: type, plan_only: false, parameters }, { action_id: actionId });
    this.builder.add(150, 'action.started', { ...FIXTURE.actionStarted, action_type: type }, { action_id: actionId });
    this.builder.add(durationMs, 'action.completed', { ...FIXTURE.actionCompleted, action_type: type, result: 'ok', duration_ns: durationMs * 1_000_000 }, { action_id: actionId });
  }

  pose(label: string, durationMs = 1400): void {
    this.action('arm_pose', [{ name: 'label', value: label, unit: null }], durationMs);
  }

  grip(label: 'grip latch' | 'grip open', target: number): void {
    this.action('gripper', [{ name: 'label', value: label, unit: null }, { name: 'target', value: target, unit: 'gripper_units' }], 900);
  }

  pulse(vxMps: number, durationS: number): void {
    this.action('base_pulse', [{ name: 'vx_mps', value: vxMps, unit: 'm/s' }, { name: 'duration_s', value: durationS, unit: 's' }, { name: 'holding', value: vxMps > 0, unit: null }], Math.round(durationS * 1000) + 800);
  }

  /** One wrist frame and the four perception results the controller records for it. */
  look(name: string, spec: FrameSpec, afterMs = 600): string {
    const id = this.obs(name);
    this.observations[id] = { record: frameRecord(id, spec), perception: framePerception(spec) };
    this.builder.add(afterMs, 'observation.captured', { ...FIXTURE.observation, width_px: WIDTH, height_px: HEIGHT, source_stamp: null }, { observation_id: id });
    const provenance = provenanceFor([id], false);
    const cube = spec.cube;
    const clipped = cube?.clipped ?? {};
    const cubeMeasurements: Named[] =
      cube === null
        ? [
            { name: 'bbox_x0', value: null, unit: 'px' },
            { name: 'bbox_y0', value: null, unit: 'px' },
            { name: 'bbox_x1', value: null, unit: 'px' },
            { name: 'bbox_y1', value: null, unit: 'px' },
            { name: 'contact_x', value: null, unit: 'px' },
            { name: 'contact_y', value: null, unit: 'px' },
            { name: 'contact_available', value: false, unit: null },
          ]
        : [
            { name: 'bbox_x0', value: cube.box[0], unit: 'px' },
            { name: 'bbox_y0', value: cube.box[1], unit: 'px' },
            { name: 'bbox_x1', value: cube.box[2], unit: 'px' },
            { name: 'bbox_y1', value: cube.box[3], unit: 'px' },
            { name: 'contact_x', value: cube.contact?.[0] ?? null, unit: 'px' },
            { name: 'contact_y', value: cube.contact?.[1] ?? null, unit: 'px' },
            { name: 'contact_available', value: cube.contact !== null, unit: null },
            { name: 'clipped_any_border', value: Object.values(clipped).some((value) => value === true), unit: null },
            { name: 'clipped_bottom', value: clipped.bottom === true, unit: null },
            { name: 'clipped_left', value: clipped.left === true, unit: null },
            { name: 'clipped_occluded_hint', value: false, unit: null },
            { name: 'clipped_right', value: clipped.right === true, unit: null },
            { name: 'clipped_top', value: clipped.top === true, unit: null },
          ];
    const perceive = (detector: string, result: 'detected' | 'not_detected' | 'error', confidence: number | null, measurements: Named[]): void => {
      this.builder.add(90, 'perception.result', { detector, observation_ids: [id], result, confidence, measurements }, { observation_id: id, provenance });
    };
    perceive('cube-recognizer', cube === null ? 'not_detected' : 'detected', cube === null ? 0.0 : cube.confidence, cubeMeasurements);
    const valid = spec.landmarks >= PAD_VALID_FROM;
    const outline = spec.outline;
    perceive(
      'pad-outline-fit',
      outline === undefined ? 'not_detected' : 'detected',
      outline === undefined ? 0.0 : outline.confidence,
      outline === undefined
        ? [
            { name: 'boundary_rms', value: valid ? 1.04 : null, unit: 'px' },
            { name: 'visible_fraction', value: valid ? 0.54 : 0.0, unit: null },
            { name: 'view_elevation', value: valid ? 54.6 : null, unit: 'deg' },
          ]
        : [
            { name: 'boundary_rms', value: outline.boundary_rms, unit: 'px' },
            { name: 'visible_fraction', value: outline.visible_fraction, unit: null },
            { name: 'view_elevation', value: outline.view_elevation, unit: 'deg' },
          ],
    );
    perceive('pad-print-landmarks', valid ? 'detected' : 'not_detected', null, [
      { name: 'template_available', value: true, unit: null },
      { name: 'pad_identity', value: valid, unit: null },
      { name: 'pad_frame_valid', value: valid, unit: null },
      { name: 'landmark_inliers', value: spec.landmarks, unit: null },
      { name: 'reprojection_rms', value: valid ? 1.02 : 0.0, unit: 'px' },
    ]);
    const padded = valid && spec.cube_pad_m !== undefined && spec.cube_pad_m !== null;
    // The controller's rule: detected where the cube was placed in PAD_FRAME, not_detected in a valid PAD_FRAME that could
    // not place this frame's cube, and error only where there was no PAD_FRAME for the layer to run in.
    perceive('cube-in-pad-frame', padded ? 'detected' : valid ? 'not_detected' : 'error', null, [
      { name: 'cube_center_pad_x', value: padded ? (spec.cube_pad_m?.[0] ?? null) : null, unit: 'm' },
      { name: 'cube_center_pad_y', value: padded ? (spec.cube_pad_m?.[1] ?? null) : null, unit: 'm' },
      { name: 'inside_pad_outline', value: padded ? (spec.inside ?? null) : null, unit: null },
    ]);
    return id;
  }

  predicate(name: string, value: boolean | null, measured: number | null, threshold: number | null, unit: string | null, frames: string[], scalars: string[] = []): void {
    const evidence: Evidence = [
      ...frames.map((id) => ({ type: 'frame' as const, observation_ids: [id], scalar_name: null })),
      ...scalars.map((scalar) => ({ type: 'scalar' as const, observation_ids: [], scalar_name: scalar })),
    ];
    this.builder.add(
      120,
      'predicate.evaluated',
      { ...FIXTURE.predicate, predicate: name, value, measured, threshold, unit, evidence, consumed_source_stamp: null },
      { provenance: frames.length === 0 ? null : provenanceFor(frames, false) },
    );
  }

  /** An error line the controller printed, with its reason code, prefixed as every scripted line is. */
  error(text: string, reasonCode: string): void {
    this.builder.add(120, 'diagnostic.text', { ...FIXTURE.diagnostic, level: 'error', text: `synthetic script: ${text}` }, { reason_code: reasonCode });
  }

  /**
   * A MOTION THE CONTROLLER PLANNED AND THEN REFUSED, under one action id: the planned action, a false predicate of that
   * action, the measured state it was read from, and the refusal with its reason code. The shapes of run m6ed2dd9's
   * CLEARANCE lift (act-0007).
   */
  refusedAction(
    label: string,
    type: 'arm_pose',
    predicate: { name: string; measured: number; threshold: number; unit: string },
    reading: { gripper: number; base_velocity: { x_mps: number; y_mps: number; theta_dps: number } },
    reasonCode: string,
    detail: string,
  ): void {
    this.actions += 1;
    const actionId = `synthetic-act-${String(this.index).padStart(2, '0')}-${String(this.actions).padStart(3, '0')}`;
    this.builder.add(300, 'action.planned', { ...FIXTURE.actionPlanned, action_type: type, plan_only: false, parameters: [{ name: 'label', value: label, unit: null }] }, { action_id: actionId });
    this.builder.add(1100, 'predicate.evaluated', {
      ...FIXTURE.predicate,
      predicate: predicate.name,
      value: false,
      measured: predicate.measured,
      threshold: predicate.threshold,
      unit: predicate.unit,
      evidence: [{ type: 'scalar' as const, observation_ids: [], scalar_name: 'base_velocity_reads' }],
      consumed_source_stamp: null,
    }, { action_id: actionId });
    this.builder.add(15, 'measured.state', { ...FIXTURE.measuredState, joints: { ...FIXTURE.measuredState.joints, gripper: reading.gripper }, base_velocity: reading.base_velocity }, { action_id: actionId });
    this.builder.add(5, 'action.refused', { action_type: type, before_motion: false, detail }, { action_id: actionId, reason_code: reasonCode });
  }

  /** A scene result: one record of the scan's fused scene (track S), with its named measurements. */
  scene(detector: 'scene-view' | 'scene-block' | 'scene-pad', observationIds: string[], measurements: Named[]): void {
    this.builder.add(160, 'perception.result', { detector, observation_ids: observationIds, result: 'detected', confidence: null, measurements }, { provenance: provenanceFor(observationIds, true) });
  }

  finished(payload: Partial<PayloadRunFinished> & Pick<PayloadRunFinished, 'outcome'>): SyntheticRun {
    this.builder.add(500, 'run.finished', {
      exit_status: 0,
      predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
      automatic_retries: 0,
      task_control_interventions: 0,
      evidence_complete: false,
      evidence_gaps: 1,
      reason_code: null,
      phase: null,
      failure: null,
      ...payload,
    }, {});
    return { run_id: this.runId, steps: this.builder.steps, observations: this.observations, resync: null };
  }
}

function kitFor(index: number, startSeq: number, startMonoNs: number): Kit {
  return new Kit(index, `synthetic-run-${String(index).padStart(4, '0')}`, startSeq, startMonoNs);
}

/* ---- the controller's constants the day's numbers were decided against ------------------------------------------- */

const PLACED_MIN_EDGE_MARGIN_MM = 17.960512242138307;
const PLACE_REQUIRED_CENTRE_MM = 37.96051224213831;
const EMPTY_LATCH_THRESHOLD = 67.39999999999999;
const GRIPPER_LATCH = 66.8;
const GRIPPER_OPEN = 79.917;

/** The evidence scalar names the recorded predicates carry, where more than one predicate names them. */
const GRIPPER_SCALARS = ['gripper_readback', 'gripper_latch_value', 'gripper_empty_latch_margin', 'gripper_holding_band_max'];
const REACH_CONSTANTS = ['constant.visual_anchor_uncertainty_mm.empirical', 'constant.fk_travel_overstatement.empirical', 'constant.arm_loaded_reach_out_mm.empirical'];

/* ---- the first half of a cycle: SEARCH to ALIGN ------------------------------------------------------------------ */

interface AlignAttempt {
  lateral_mm: number;
  radial_mm: number;
  aligned: boolean;
  tool: string;
  /** The frame ALIGN decided from. */
  frame: FrameSpec;
  /** What ALIGN did after this attempt. */
  then: 'trim' | 'descend' | 'accept';
  /** A trim's commanded pan from the saved low pose's pan, the measured value of trim_pan_within_return_bound. */
  trim_from_home_deg?: number;
}

/** SEARCH and the selection: the low-pose frame, the block taken as the target, and the two checks before PREGRASP. */
interface SearchSelection {
  pose: { shoulder_pan: number; shoulder_lift: number; elbow_flex: number; wrist_flex: number };
  search: FrameSpec;
  seen_pad_m: [number, number];
  mode: 'BASE_ADVANCE' | 'ARM_ONLY';
  start_inside_mm: number;
  pan_from_home_deg: number;
}

interface FrontHalf extends SearchSelection {
  coarse: { pan_deg: number; forward_mm: number; cube_out_mm: number };
  pregrasp: FrameSpec;
  align: AlignAttempt[];
}

/** SEARCH, TARGET_SELECTED and the two predicates after it, as the controller records them. Returns the SEARCH observation. */
function searchAndSelect(kit: Kit, selection: SearchSelection, sceneSearch = false): string {
  kit.state('SEARCH', { pose: selection.pose }, 500);
  const search = kit.look('search', { ...selection.search, pan_deg: selection.pose.shoulder_pan });
  // Under scene_search, policy_search._search_from_scene asks first whether the low-pose frame decides: one listed cube,
  // detected, its contact found, nothing clipped, ARM_ONLY or BASE_ADVANCE. Here it does, so no view of the scene is
  // taken and SEARCH goes on from that frame.
  if (sceneSearch) kit.state('SEARCH_SCENE', { early_exit: true, centre_listing: 'complete', centre_class: selection.mode });
  kit.state('TARGET_SELECTED', {
    target_id: 'cube',
    mode: selection.mode,
    seen_pad_m: selection.seen_pad_m,
    bound_pad_m: selection.seen_pad_m,
    distance_to_binding_mm: 0.0,
    cubes_listed: 1,
    listed_pass: 1,
    targets_bound: 1,
    // the pad policy of 2026-09-29: the one block off the pad, and none on it
    candidates: 1,
    on_pad_ignored: 0,
  });
  kit.predicate('already_placed', false, selection.start_inside_mm, PLACED_MIN_EDGE_MARGIN_MM, 'mm', [search], ['cube_centre_to_pad_edge_mm']);
  kit.predicate('pregrasp_pan_within_return_bound', true, selection.pan_from_home_deg, 25.0, 'deg', [search], ['coarse_pan_deg', 'home_pan_deg']);
  return search;
}

/** SEARCH, the selection, PREGRASP and ALIGN, as the controller records them. Returns the SEARCH observation. */
function frontHalf(kit: Kit, front: FrontHalf, sceneSearch = false): string {
  const search = searchAndSelect(kit, front, sceneSearch);
  kit.state('PREGRASP', { coarse: front.coarse, cube_inside_pad_mm: Math.round(front.start_inside_mm * 10) / 10 });
  kit.pose('PREGRASP non-contact', 2400);
  let view = kit.look('pregrasp', { ...front.pregrasp, pan_deg: front.coarse.pan_deg });
  front.align.forEach((attempt, index) => {
    kit.state('ALIGN', { attempt: index, lateral_mm: attempt.lateral_mm, radial_mm: attempt.radial_mm, aligned: attempt.aligned, tool: attempt.tool });
    kit.predicate('aligned', attempt.aligned, attempt.lateral_mm, 9.0, 'mm', [view], ['lateral_mm', 'radial_mm', 'capture_window_mm']);
    if (attempt.then === 'trim') {
      kit.predicate('trim_pan_within_return_bound', true, attempt.trim_from_home_deg ?? null, 25.0, 'deg', [view], ['commanded_pan_deg', 'home_pan_deg']);
      kit.pose('ALIGN pan trim');
    } else if (attempt.then === 'descend') {
      kit.pose('ALIGN non-contact descent');
    }
    const next = front.align[index + 1];
    if (next !== undefined) view = kit.look(`align-${String(index + 1)}`, next.frame);
  });
  return search;
}

/* ---- the second half of a placed cycle ---------------------------------------------------------------------------- */

interface Pulse {
  needed_mm: number;
  duration_s: number;
  assumed_rate_mm_s: number;
  axis_pad: [number, number];
  advance_mm: number;
  lateral_mm: number;
  distortion_mm: number;
  rate_mm_s: number;
}

interface ReturnPulse {
  remaining_mm: number;
  duration_s: number;
  advance_mm: number;
  lateral_mm: number;
  distortion_mm: number;
  rate_mm_s: number;
}

interface PlacedHalf {
  approach_radial_mm: number;
  straddling_tool: string;
  straddling_up_mm: number;
  close: { gripper: number; tool: string };
  retention: number;
  plan: { base_advance_mm: number; anchor_pad_m: [number, number]; anchor_margin_mm: number; reach_direction_pad: [number, number] };
  carry_tool: string;
  pulses: Pulse[];
  admission: { worst_mm: number; worst_exact: number; anchor_pad_m: [number, number]; fk_travel_mm: number; advance_measured_mm: number; height_mm: number; tool: string };
  placed: { cube_pad_m: [number, number]; centre_to_edge_mm: number; exact: number; footprint_mm: number };
  returns: ReturnPulse[];
  returned: { along_mm: number; sideways_mm: number; exact: number };
}

function placedHalf(kit: Kit, search: string, half: PlacedHalf): void {
  kit.state('APPROACH', { radial_mm: half.approach_radial_mm });
  kit.pose('APPROACH descent');
  kit.pose('APPROACH proven grasp geometry');
  kit.predicate('straddling', true, half.straddling_up_mm, 8.0, 'mm', [], ['tool_up_mm_vs_proven_grasp']);
  kit.state('STRADDLING', { tool: half.straddling_tool });
  kit.grip('grip latch', GRIPPER_LATCH);
  kit.state('GRASP_CLOSE', { attempt: 0, gripper: half.close.gripper, closed_on_something: true, tool: half.close.tool });
  kit.predicate('closed_on_something', true, half.close.gripper, EMPTY_LATCH_THRESHOLD, 'gripper_units', [], GRIPPER_SCALARS);
  kit.pose('VERIFICATION lift', 900);
  const second = kit.look('clearance', { cube: null, landmarks: 0, held: true });
  kit.pose('CONFIRM lift', 700);
  kit.state('OBJECT_SECURED', { value: true, retention_score: half.retention, threshold: 0.5, gripper: half.close.gripper });
  kit.predicate('object_secured', true, half.retention, 0.5, null, [second], ['retention_score', 'constant.retention_min_score.empirical']);
  kit.state('PLACEMENT_PLAN', {
    mode: 'BASE_ADVANCE',
    fk_travel_mm: 0.0,
    base_advance_mm: half.plan.base_advance_mm,
    anchor_pad_m: half.plan.anchor_pad_m,
    anchor_margin_mm: half.plan.anchor_margin_mm,
    reach_direction_pad: half.plan.reach_direction_pad,
    required_centre_margin_mm: 38.0,
    bound_mode: 'BASE_ADVANCE',
  });
  kit.predicate('placement_plan', true, half.plan.anchor_margin_mm, PLACE_REQUIRED_CENTRE_MM, 'mm', [search], ['anchor_margin_mm', 'grasp_out_mm', ...REACH_CONSTANTS]);
  kit.state('CARRY', { mode: 'BASE_ADVANCE', tool: half.carry_tool });
  kit.state('HOLD_OBSERVE', { pad_frame_valid: true, anchor_pad_m: half.plan.anchor_pad_m, anchor_margin_mm: half.plan.anchor_margin_mm });
  kit.look('hold', { cube: null, landmarks: 94, held: true });
  kit.predicate('pad_frame_valid_while_holding', true, null, null, null, []);
  half.pulses.forEach((pulse, index) => {
    kit.state('BASE_ADVANCE', { pulse: index, needed_mm: pulse.needed_mm, duration_s: pulse.duration_s, assumed_rate_mm_s: pulse.assumed_rate_mm_s, axis_pad: pulse.axis_pad });
    kit.pulse(0.05, pulse.duration_s);
    kit.predicate('base_stop_confirmed_by_primitive', true, null, null, null, [], ['base_velocity_reads', 'base_velocity_fresh_reads']);
    kit.look(`pulse-${String(index + 1)}`, { cube: null, landmarks: 92, held: true });
    kit.state('BASE_MEASURED', { vx_mps: 0.05, duration_s: pulse.duration_s, advance_mm: pulse.advance_mm, lateral_mm: pulse.lateral_mm, distortion_mm: pulse.distortion_mm, rate_mm_s: pulse.rate_mm_s });
    kit.predicate('base_measured', true, pulse.distortion_mm, 4.0, 'mm', [], ['pad_cross_distortion_mm', 'advance_mm', 'lateral_mm', 'pulse_duration_s']);
  });
  kit.pose('PLACE_APPROACH over the release point', 1800);
  kit.pose('PLACE lower');
  const admission = half.admission;
  kit.state('RELEASE_ADMISSION', {
    admitted: true,
    worst_case_centre_margin_mm: admission.worst_mm,
    required_mm: 38.0,
    anchor_pad_m: admission.anchor_pad_m,
    fk_travel_from_grasp_mm: admission.fk_travel_mm,
    base_advance_measured_mm: admission.advance_measured_mm,
    release_height_mm: admission.height_mm,
    max_release_height_mm: 15.0,
    tool: admission.tool,
  });
  kit.predicate('release_margin', true, admission.worst_exact, PLACE_REQUIRED_CENTRE_MM, 'mm', [search], ['fk_travel_from_grasp_mm', 'base_advance_measured_mm', ...REACH_CONSTANTS]);
  kit.predicate('release_height', true, admission.height_mm, 15.0, 'mm', [], ['tool_up_mm_vs_proven_grasp']);
  kit.predicate('release_admitted', true, null, null, null, [], ['release_margin', 'release_height']);
  kit.grip('grip open', GRIPPER_OPEN);
  kit.state('RELEASED', { gripper: GRIPPER_OPEN, fk_travel_from_grasp_mm: admission.fk_travel_mm, tool: admission.tool });
  kit.pose('RETREAT back', 900);
  kit.pose('EXIT to the low scan pose', 1800);
  const exit = kit.look('placed', { cube: { box: [560, 330, 650, 430], contact: [604, 425], confidence: 0.88 }, landmarks: 99, cube_pad_m: half.placed.cube_pad_m, inside: true });
  kit.pose('SETTLE goals on the measured pose', 600);
  kit.state('PLACED', {
    value: true,
    cube_pad_m: half.placed.cube_pad_m,
    centre_to_edge_mm: half.placed.centre_to_edge_mm,
    footprint_margin_any_yaw_mm: half.placed.footprint_mm,
    clearance_target_mm: 20.0,
    clearance_target_met: true,
    predicted_worst_case_centre_margin_mm: admission.worst_mm,
    target_id: 'cube',
    attributed: true,
    // the release agreement (the pad policy of 2026-09-29): the one block on the pad, credited at this run's release. The
    // distance is a synthetic value inside the 26.0 mm tolerance, not derived from the scenario's geometry.
    on_pad_cubes: 1,
    release_distance_mm: 4.0,
    release_tolerance_mm: 26.0,
    attribution_reason: null,
  });
  kit.predicate('placed', true, half.placed.exact, PLACED_MIN_EDGE_MARGIN_MM, 'mm', [exit], ['cube_centre_to_pad_edge_mm', 'inside_pad_outline']);
  kit.predicate('clearance_target_met', true, half.placed.exact, PLACE_REQUIRED_CENTRE_MM, 'mm', [exit], ['cube_centre_to_pad_edge_mm']);
  half.returns.forEach((pulse, index) => {
    kit.state('BASE_RETURN', { pulse: index, remaining_mm: pulse.remaining_mm, duration_s: pulse.duration_s });
    kit.pulse(-0.05, pulse.duration_s);
    kit.predicate('base_stop_confirmed_by_primitive', true, null, null, null, [], ['base_velocity_reads', 'base_velocity_fresh_reads']);
    kit.look(`return-${String(index + 1)}`, { cube: { box: [560, 330, 650, 430], contact: [604, 425], confidence: 0.88 }, landmarks: 98, cube_pad_m: half.placed.cube_pad_m, inside: true });
    kit.state('BASE_MEASURED', { vx_mps: -0.05, duration_s: pulse.duration_s, advance_mm: pulse.advance_mm, lateral_mm: pulse.lateral_mm, distortion_mm: pulse.distortion_mm, rate_mm_s: pulse.rate_mm_s });
    kit.predicate('base_measured', true, pulse.distortion_mm, 4.0, 'mm', [], ['pad_cross_distortion_mm', 'advance_mm', 'lateral_mm', 'pulse_duration_s']);
  });
  kit.state('BASE_RETURNED', { value: true, remaining_along_axis_mm: half.returned.along_mm, sideways_mm: half.returned.sideways_mm, tolerance_mm: 10.0 });
  kit.predicate('base_returned', true, half.returned.exact, 10.0, 'mm', [], ['remaining_along_axis_mm', 'sideways_mm']);
}

const PLACED_PREDICATES = { object_secured: true, placed: true, clearance_target_met: true, base_returned: true };

/* ---- the recorded runs of 2026-09-26 ---------------------------------------------------------------------------- */

/** Start 1 of the far block, run console-20260926T201540Z-mb3c1048: 108 mm planned, placed at 47.5 mm, returned. */
const FAR_BLOCK_FRONT: FrontHalf = {
  pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 37.85, wrist_flex: 67.21 },
  search: { cube: { box: [499, 452, 596, 569], contact: [533.2, 556.5], confidence: 0.907 }, landmarks: 97, cube_pad_m: [-0.0201, -0.196], inside: false },
  seen_pad_m: [-0.0201, -0.196],
  mode: 'BASE_ADVANCE',
  start_inside_mm: -48.462592583922905,
  pan_from_home_deg: 2.205054945054945,
  coarse: { pan_deg: -11.26, forward_mm: 20.1, cube_out_mm: 7.1 },
  pregrasp: { cube: { box: [560, 300, 700, 470], contact: [622, 462], confidence: 0.9 }, landmarks: 96, cube_pad_m: [-0.0204, -0.1962], inside: false },
  align: [
    { lateral_mm: 7.1, radial_mm: 3.2, aligned: true, tool: '+18.1 mm out / +69.0 mm up vs the proven grasp, pitch 64.5', frame: { cube: null, landmarks: 0 }, then: 'descend' },
    { lateral_mm: 7.2, radial_mm: 6.7, aligned: true, tool: '+7.9 mm out / +37.3 mm up vs the proven grasp, pitch 66.9', frame: { cube: { box: [470, 300, 780, 640], contact: [620, 630], confidence: 0.86 }, landmarks: 0 }, then: 'accept' },
  ],
};

const FAR_BLOCK_PLACED: PlacedHalf = {
  approach_radial_mm: 6.7,
  straddling_tool: '+22.7 mm out / +0.3 mm up vs the proven grasp, pitch 65.0',
  straddling_up_mm: 0.3296899507342216,
  close: { gripper: 67.909, tool: '+23.4 mm out / +0.6 mm up vs the proven grasp, pitch 64.7' },
  retention: 0.915,
  plan: { base_advance_mm: 108.0, anchor_pad_m: [-0.0201, -0.196], anchor_margin_mm: -48.5, reach_direction_pad: [-0.042, 0.999] },
  carry_tool: '-3.0 mm out / +28.1 mm up vs the proven grasp, pitch 72.7',
  pulses: [
    { needed_mm: 108.0, duration_s: 0.54, assumed_rate_mm_s: 100.0, axis_pad: [-0.042, 0.999], advance_mm: 26.6, lateral_mm: -0.8, distortion_mm: 0.17, rate_mm_s: 49.3 },
    { needed_mm: 81.0, duration_s: 0.8, assumed_rate_mm_s: 49.4, axis_pad: [-0.01, 1.0], advance_mm: 40.1, lateral_mm: -2.3, distortion_mm: 0.93, rate_mm_s: 50.2 },
    { needed_mm: 41.0, duration_s: 0.8, assumed_rate_mm_s: 50.3, axis_pad: [0.047, 0.999], advance_mm: 39.7, lateral_mm: -0.7, distortion_mm: 0.29, rate_mm_s: 49.7 },
  ],
  admission: { worst_mm: 43.8, worst_exact: 43.767834654633134, anchor_pad_m: [-0.016, -0.0895], fk_travel_mm: -8.3, advance_measured_mm: 106.6, height_mm: 5.0, tool: '+14.5 mm out / +5.0 mm up vs the proven grasp, pitch 65.9' },
  placed: { cube_pad_m: [-0.0158, -0.1], centre_to_edge_mm: 47.5, exact: 47.46321281015431, footprint_mm: 29.5 },
  returns: [
    { remaining_mm: 106.6, duration_s: 0.8, advance_mm: -40.0, lateral_mm: -0.8, distortion_mm: 0.49, rate_mm_s: 50.0 },
    { remaining_mm: 66.6, duration_s: 0.8, advance_mm: -40.3, lateral_mm: -0.1, distortion_mm: 0.48, rate_mm_s: 50.4 },
    { remaining_mm: 26.4, duration_s: 0.464, advance_mm: -23.5, lateral_mm: -1.3, distortion_mm: 0.1, rate_mm_s: 50.7 },
  ],
  returned: { along_mm: 2.9, sideways_mm: -2.2, exact: 2.8519365382843347 },
};

export function buildFarBlockRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  kit.start('start_cycle', 'cycle');
  const search = frontHalf(kit, FAR_BLOCK_FRONT);
  placedHalf(kit, search, FAR_BLOCK_PLACED);
  return kit.finished({ outcome: 'placed_new', predicates: PLACED_PREDICATES, phase: 'BASE_RETURNED' });
}

/** Start 2, run console-20260926T203744Z-m2476137: the block far left and yawed, one pan trim, placed at 51.1 mm. */
const FAR_LEFT_FRONT: FrontHalf = {
  pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 38.02, wrist_flex: 67.21 },
  search: { cube: { box: [330, 440, 440, 560], contact: [378, 548], confidence: 0.83 }, landmarks: 96, cube_pad_m: [-0.0936, -0.1698], inside: false },
  seen_pad_m: [-0.0936, -0.1698],
  mode: 'BASE_ADVANCE',
  start_inside_mm: -22.326682291925138,
  pan_from_home_deg: 20.945054945054945,
  coarse: { pan_deg: -30.0, forward_mm: 47.1, cube_out_mm: 34.1 },
  pregrasp: { cube: { box: [520, 310, 660, 470], contact: [590, 462], confidence: 0.84 }, landmarks: 95, cube_pad_m: [-0.0906, -0.179], inside: false },
  align: [
    { lateral_mm: 13.7, radial_mm: 0.7, aligned: false, tool: '+44.5 mm out / +68.0 mm up vs the proven grasp, pitch 64.7', frame: { cube: null, landmarks: 0 }, then: 'trim', trim_from_home_deg: 17.64 },
    { lateral_mm: 4.6, radial_mm: 1.2, aligned: true, tool: '+42.2 mm out / +65.3 mm up vs the proven grasp, pitch 65.6', frame: { cube: { box: [560, 300, 700, 470], contact: [630, 462], confidence: 0.85 }, landmarks: 90 }, then: 'descend' },
    { lateral_mm: 5.0, radial_mm: -0.4, aligned: true, tool: '+40.2 mm out / +42.8 mm up vs the proven grasp, pitch 65.4', frame: { cube: { box: [470, 300, 790, 650], contact: [630, 640], confidence: 0.84 }, landmarks: 0 }, then: 'accept' },
  ],
};

const FAR_LEFT_PLACED: PlacedHalf = {
  approach_radial_mm: -0.4,
  straddling_tool: '+47.8 mm out / -0.4 mm up vs the proven grasp, pitch 65.1',
  straddling_up_mm: -0.3790918729365267,
  close: { gripper: 68.185, tool: '+48.0 mm out / -0.3 mm up vs the proven grasp, pitch 64.9' },
  retention: 0.863,
  plan: { base_advance_mm: 82.0, anchor_pad_m: [-0.0936, -0.1698], anchor_margin_mm: -22.3, reach_direction_pad: [-0.086, 0.996] },
  carry_tool: '+17.5 mm out / +20.6 mm up vs the proven grasp, pitch 73.8',
  pulses: [
    { needed_mm: 82.0, duration_s: 0.41, assumed_rate_mm_s: 100.0, axis_pad: [-0.086, 0.996], advance_mm: 23.5, lateral_mm: -1.5, distortion_mm: 0.18, rate_mm_s: 57.3 },
    { needed_mm: 58.0, duration_s: 0.8, assumed_rate_mm_s: 57.4, axis_pad: [-0.021, 1.0], advance_mm: 42.1, lateral_mm: -2.0, distortion_mm: 0.21, rate_mm_s: 52.6 },
    { needed_mm: 16.0, duration_s: 0.304, assumed_rate_mm_s: 52.7, axis_pad: [0.025, 1.0], advance_mm: 14.9, lateral_mm: -2.2, distortion_mm: 0.33, rate_mm_s: 49.1 },
  ],
  admission: { worst_mm: 43.1, worst_exact: 43.06871250168654, anchor_pad_m: [-0.0905, -0.0893], fk_travel_mm: -9.2, advance_measured_mm: 80.8, height_mm: 5.4, tool: '+38.6 mm out / +5.4 mm up vs the proven grasp, pitch 65.5' },
  placed: { cube_pad_m: [-0.0925, -0.0964], centre_to_edge_mm: 51.1, exact: 51.12532609832274, footprint_mm: 33.2 },
  returns: [
    { remaining_mm: 80.8, duration_s: 0.8, advance_mm: -40.2, lateral_mm: -5.0, distortion_mm: 0.33, rate_mm_s: 50.2 },
    { remaining_mm: 40.6, duration_s: 0.75, advance_mm: -40.7, lateral_mm: -4.1, distortion_mm: 0.66, rate_mm_s: 54.3 },
  ],
  returned: { along_mm: -0.1, sideways_mm: -9.1, exact: 0.07705129083609563 },
};

export function buildFarLeftRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  return farLeftRun(null)(index, startSeq, startMonoNs);
}

/** The run of start 2, with a synthetic note after its start when the scenario needs one said. */
function farLeftRun(note: string | null): RunFactory {
  return (index, startSeq, startMonoNs) => {
    const kit = kitFor(index, startSeq, startMonoNs);
    kit.start('start_cycle', 'cycle');
    if (note !== null) kit.note(note);
    const search = frontHalf(kit, FAR_LEFT_FRONT);
    placedHalf(kit, search, FAR_LEFT_PLACED);
    return kit.finished({ outcome: 'placed_new', predicates: PLACED_PREDICATES, phase: 'BASE_RETURNED', automatic_retries: 1 });
  };
}

/**
 * THE NEAR BLOCK OF 19:19, run console-20260926T191919Z-m8c5f9e9. On the day the coarse pregrasp put the block at -44.5 mm
 * and clamped the forward target at 0, ALIGN measured it at about -18 mm, and three empty closes retried 14 mm OUTWARD
 * (grasp_missed). The head's controller decides that block at SEARCH instead: policy_align.refuse_pickup_band, after the
 * selection and before PREGRASP, refuses a coarse cube out nearer than the pickup band (-13 mm with no word) with the
 * distance the block must move, and nothing is commanded. This plays that refusal on the day's SEARCH frame: its pose, its
 * recorded perception and its coarse value; the sentence, the values and the predicate are the head's for that value
 * (autonomy/tests/test_cycle_mission_mode.py, TheNearBlockRefusal, runs the same -44.5).
 */
const NEAR_BLOCK: SearchSelection & { coarse_cube_out_mm: number } = {
  pose: { shoulder_pan: -8.7, shoulder_lift: -79.25, elbow_flex: 34.51, wrist_flex: 68.7 },
  search: {
    cube: { box: [452, 594, 537, 697], contact: [495, 696], confidence: 0.9 },
    landmarks: 122,
    cube_pad_m: [-0.0205, -0.18],
    inside: false,
    outline: { confidence: 0.716, boundary_rms: 1.39, visible_fraction: 0.757, view_elevation: 59.2 },
  },
  seen_pad_m: [-0.0205, -0.18],
  mode: 'BASE_ADVANCE',
  start_inside_mm: -32.5498082227241,
  pan_from_home_deg: 4.475054945054945,
  coarse_cube_out_mm: -44.5,
};

/** The pickup band's bound with no word (policy_align.pickup_band_min_cube_out_mm: PREGRASP_MIN_CUBE_OUT_MM + 0.0). */
const PICKUP_BAND_MIN_CUBE_OUT_MM = -13.0;
/** PREGRASP_OUTWARD_BIAS_MM less PREGRASP_MIN_CUBE_OUT_MM, which the refusal adds to the distance the block must move. */
const PICKUP_BAND_MOVE_MARGIN_MM = 26.0;

export function buildNearBlockRefusedRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  kit.start('start_cycle', 'cycle');
  kit.note(
    "this plays the SEARCH of run m8c5f9e9 (its pose, frame and coarse cube out of -44.5 mm) as the head's controller decides it: refused by the pickup band before any motion; on the day, before that refusal existed, the block was driven and the grasp missed three times",
  );
  const search = searchAndSelect(kit, NEAR_BLOCK);
  const out = NEAR_BLOCK.coarse_cube_out_mm;
  const bound = PICKUP_BAND_MIN_CUBE_OUT_MM;
  const move = Math.round((bound + PICKUP_BAND_MOVE_MARGIN_MM - out) * 10) / 10;
  kit.predicate('pregrasp_cube_within_reach', false, out, bound, 'mm', [search], ['coarse_cube_out_mm']);
  return kit.finished({
    outcome: 'failed',
    exit_status: 5,
    evidence_complete: false,
    evidence_gaps: 1,
    reason_code: 'search_cube_inside_pregrasp_reach',
    phase: 'TARGET_SELECTED',
    failure: {
      detail: `SEARCH: the block stands ${(bound - out).toFixed(1)} mm nearer than the pregrasp can reach (coarse cube_out ${out.toFixed(1)} mm, the pregrasp clamps at ${bound.toFixed(0)}); move it at least ${move.toFixed(1)} mm away from the robot, or let the base reverse (T6 step 2b); nothing was picked up`,
      measurements: [
        { name: 'cube_out_mm', value: out, unit: 'mm' },
        { name: 'min_cube_out_mm', value: bound, unit: 'mm' },
        { name: 'move_block_at_least_mm', value: move, unit: 'mm' },
      ],
      observation_ids: [search],
    },
  });
}

/**
 * THE TWO REFUSALS AT SEARCH (start 3 at 20:58 and 21:08; the block at 21:17), as the head's controller records them. On
 * the day the binding refused them with counts only. At the head (d801068) the binding's refusal carries the SeenCube's
 * placement explanation (target_binding.bind_targets, `**explained`): its scalar values sorted by name, then the counts,
 * and its sentence and hint inside the BINDING sentence. The values are track P's placement_envelope.explain_refusal for
 * each anchor on the reach axis of start 1 with the run's side drift allowance, recomputed on the head.
 */
const NOT_PLACEABLE_SENTENCE =
  'BINDING: the one cube for target cube has no bounded placement from this SEARCH (neither the arm alone nor the bounded base advance reaches the pad from it)';

function buildNotPlaceableRun(kind: 'side' | 'budget'): RunFactory {
  return (index, startSeq, startMonoNs) => {
    const kit = kitFor(index, startSeq, startMonoNs);
    kit.start('start_cycle', 'cycle');
    const side = kind === 'side';
    const pad: [number, number] = side ? [0.163, -0.21] : [0.064, -0.22];
    kit.state('SEARCH', { pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 38.02, wrist_flex: 67.21 } }, 500);
    const search = kit.look('search', { cube: side ? { box: [1020, 470, 1110, 580], contact: [1062, 572], confidence: 0.81 } : { box: [760, 500, 850, 610], contact: [803, 602], confidence: 0.86 }, landmarks: 95, cube_pad_m: pad, inside: false });
    const counts: Named[] = [
      { name: 'candidates', value: 1, unit: null },
      { name: 'bound_earlier', value: 0, unit: null },
      { name: 'cubes_seen', value: 1, unit: null },
      { name: 'placed_cubes', value: 0, unit: null },
    ];
    // explain_refusal(cycle, anchor, (-0.042, 0.999), 0.0, PLACE_SIDE_DRIFT_ALLOWANCE_MM, ()) on the head, sorted by name.
    const explained: Named[] = side
      ? [
          { name: 'along_budget_deficit_mm', value: 1.6, unit: 'mm' },
          { name: 'axis_advance_needed_unbounded_mm', value: 121.58209433647478, unit: 'mm' },
          { name: 'binding_edge', value: 'right', unit: null },
          { name: 'constraint', value: BINDING_CONSTRAINT.side_edge, unit: null },
          { name: 'deficit_mm', value: 9.4, unit: 'mm' },
          { name: 'explanation', value: 'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance', unit: null },
          { name: 'hint', value: 'move the block 10 mm to the left as seen from the robot and 3 mm toward the pad', unit: null },
          { name: 'hint_places_block', value: true, unit: null },
          { name: 'model_admits_under', value: 'projected+reach_after_advance', unit: null },
          { name: 'required_axis_margin_mm', value: 58.96051224213831, unit: 'mm' },
          { name: 'required_side_margin_mm', value: 58.96051224213831, unit: 'mm' },
          { name: 'side_margin_mm', value: 44.5, unit: 'mm' },
        ]
      : [
          { name: 'axis_advance_needed_unbounded_mm', value: 131.59210434648477, unit: 'mm' },
          { name: 'constraint', value: BINDING_CONSTRAINT.along_budget, unit: null },
          { name: 'deficit_mm', value: 11.6, unit: 'mm' },
          { name: 'explanation', value: 'the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm', unit: null },
          { name: 'hint', value: 'move the block 12 mm toward the pad', unit: null },
          { name: 'hint_places_block', value: true, unit: null },
          { name: 'model_admits_under', value: 'projected+reach_after_advance', unit: null },
          { name: 'required_axis_margin_mm', value: 58.96051224213831, unit: 'mm' },
          { name: 'required_side_margin_mm', value: 58.96051224213831, unit: 'mm' },
          { name: 'side_margin_mm', value: 143.5, unit: 'mm' },
        ];
    kit.note(
      "on the day the binding refused this block with the counts only; this plays the head's record of the same refusal, whose limit values are track P's explain_refusal for this anchor on the reach axis of start 1",
    );
    const explanation = String(explained.find((item) => item.name === 'explanation')?.value ?? '');
    const hint = String(explained.find((item) => item.name === 'hint')?.value ?? '');
    // A SEARCH binding refusal is a CycleFailed (exit 5, the arm holds), and the controller records exit 5 as
    // 'failed' (run_single_cube_cycle.py: TargetRefused -> failed, CycleFailed -> mission_finished(5)). "Before
    // motion" is carried by the phase SEARCH and by the absence of any planned action.
    return kit.finished({
      outcome: 'failed',
      exit_status: 5,
      reason_code: 'mission_binding_candidate_not_placeable',
      phase: 'SEARCH',
      failure: {
        detail: `${NOT_PLACEABLE_SENTENCE}: ${explanation}; ${hint}; nothing was picked up`,
        measurements: [...explained, ...counts],
        observation_ids: [search],
      },
    });
  };
}

/**
 * The pan of the saved low scan pose on 2026-09-26: each recorded run's coarse pregrasp pan plus its recorded
 * pan_from_home_deg, which is |coarse - home| and every recorded coarse pan lay below home (mb3c1048, m2476137 and
 * m8c5f9e9 all give -9.054945...). The two ALIGN losses are assumed to share it.
 */
const SAVED_LOW_POSE_PAN_DEG = -9.054945054945055;

/** The pad's half extent along PAD_FRAME y (the short edge of 0.295 m), for the signed distance of a block beyond it. */
const PAD_HALF_Y_M = 0.1475;

/** The recognizer's own note for a frame in which the detector proposed nothing (autonomy/perception/cube_recognizer.py). */
const NO_PROPOSAL_NOTE = 'no detector proposal: nothing to verify';

interface AlignLoss {
  run: string;
  block_pad_m: [number, number];
  coarse: { pan_deg: number; forward_mm: number; cube_out_mm: number };
  align: AlignAttempt[];
}

/** An ALIGN-loss front: the recorded block, coarse values and ALIGN attempts, around invented frames. */
function alignLossFront(loss: AlignLoss): FrontHalf {
  const y = loss.block_pad_m[1];
  const invented = (box: [number, number, number, number]): FrameSpec => ({
    cube: { box, contact: [(box[0] + box[2]) / 2, box[3] - 4], confidence: 0.86 },
    landmarks: 95,
    cube_pad_m: loss.block_pad_m,
    inside: false,
  });
  return {
    pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 37.85, wrist_flex: 67.21 },
    search: invented([760, 430, 860, 545]),
    seen_pad_m: loss.block_pad_m,
    mode: 'BASE_ADVANCE',
    // Beyond the pad along y only (|x| is inside the long half of 0.2075 m), so the signed distance is along y.
    start_inside_mm: Math.round((PAD_HALF_Y_M - Math.abs(y)) * 10000) / 10,
    pan_from_home_deg: Math.abs(loss.coarse.pan_deg - SAVED_LOW_POSE_PAN_DEG),
    coarse: loss.coarse,
    pregrasp: invented([560, 300, 700, 470]),
    align: loss.align,
  };
}

/** The run of 21:24 UTC: one pan trim, then accepted at radial +14.5, descended, and no proposal at attempt 2. */
const ALIGN_NO_PROPOSAL: AlignLoss = {
  run: 'console-20260926T212418Z-m76fe86c',
  block_pad_m: [0.075, -0.205],
  coarse: { pan_deg: 11.9, forward_mm: 9.6, cube_out_mm: -3.4 },
  align: [
    { lateral_mm: 9.2, radial_mm: 13.9, aligned: false, tool: '+9.4 mm out / +68.6 mm up vs the proven grasp, pitch 64.6', frame: { cube: null, landmarks: 0 }, then: 'trim', trim_from_home_deg: 22.86 },
    { lateral_mm: 3.4, radial_mm: 14.5, aligned: true, tool: '+9.1 mm out / +68.4 mm up vs the proven grasp, pitch 64.7', frame: { cube: { box: [575, 300, 715, 470], contact: [645, 466], confidence: 0.86 }, landmarks: 0 }, then: 'descend' },
  ],
};

/** The run of 21:31 UTC: accepted at radial +12.4 on the first attempt, descended, and no proposal at attempt 1. */
const ALIGN_TOP_EDGE: AlignLoss = {
  run: 'console-20260926T213112Z-m66738ff',
  block_pad_m: [0.082, -0.179],
  coarse: { pan_deg: 13.2, forward_mm: 41.0, cube_out_mm: 28.0 },
  align: [{ lateral_mm: 1.4, radial_mm: 12.4, aligned: true, tool: '+38.0 mm out / +68.2 mm up vs the proven grasp, pitch 64.8', frame: { cube: null, landmarks: 0 }, then: 'descend' }],
};

/**
 * THE TWO ALIGN LOSSES (21:24 and 21:31). Their full records are on the robot. The first-hand record of each gives the
 * block position, the PREGRASP coarse values, every ALIGN attempt, the reason code, the controller's sentence and the
 * attempt, and those are what this plays. The recognizer's reason in both is its own note, NO_PROPOSAL_NOTE, and nothing
 * else. The descent frame of 21:31 showed the block cut by the top edge of the view and blurred, with the arm's shadow
 * on the floor below it: that is the console's inference about why the detector proposed nothing, and it is stated as
 * such in a note, never as the recognizer's reason.
 */
function buildAlignLostRun(kind: 'no-proposal' | 'clipped-top', replay = false): RunFactory {
  return (index, startSeq, startMonoNs) => {
    const kit = kitFor(index, startSeq, startMonoNs);
    kit.start('start_cycle', 'cycle');
    const loss = kind === 'no-proposal' ? ALIGN_NO_PROPOSAL : ALIGN_TOP_EDGE;
    // THE FRESH ATTEMPT AFTER A RETRY. The day has no record of one, so it is the same run again, and it says so.
    if (replay) kit.note(`this fresh attempt after the retry replays ${loss.run}: no record of a second attempt at this block exists, so nothing here says what one would have done`);
    kit.note(
      `this plays ${loss.run}: the block position, the PREGRASP coarse values, the ALIGN attempts, the reason code, the sentence and the attempt are that run's first-hand record; its full record is on the robot, so the frames, the frame boxes and the tool strings here are invented` +
        (kind === 'no-proposal' ? `, and so is the trim's measured pan from home (22.86 deg)` : '') +
        `; the pan from home is the recorded coarse pan less the day's saved low-pose pan (${SAVED_LOW_POSE_PAN_DEG.toFixed(2)} deg)` +
        `; the SEARCH pose and TARGET_SELECTED's mode (BASE_ADVANCE) and counts are assumed, not that run's record` +
        `; PREGRASP's cube_inside_pad_mm is derived from the recorded block position with the controller's placed_margin_mm and distance_outside_pad_mm`,
    );
    frontHalf(kit, alignLossFront(loss));
    const attempt = loss.align.length;
    // align_cube_not_detected is raised only when the recognizer reported NO detection (ALIGN's _cube is None): a
    // detected box, clipped or not, would have gone on to alignment_from_bbox. So the descent frame's recognizer result
    // is not_detected in both cases, with the controller's seven measurements and no reason: the reason travels in the
    // failure's recognizer_reason and its sentence only, as the controller writes it.
    const lost = kit.look('descent', { cube: null, landmarks: 0 });
    kit.predicate('cube_detected_in_the_pregrasp_view', false, null, null, null, [lost], ['cube_detected']);
    if (kind === 'clipped-top') {
      kit.note(
        `console inference, not the recognizer's words: the recorded descent frame of this run shows the block cut by the top edge of the view, with motion blur, and the arm's shadow on the floor below it, after ALIGN accepted on the lateral value alone with the radial at +12.4 mm; the recognizer said only "${NO_PROPOSAL_NOTE}". This frame draws no block, because the recognizer reported none.`,
      );
    }
    return kit.finished({
      outcome: 'failed',
      exit_status: 5,
      reason_code: 'align_cube_not_detected',
      phase: 'ALIGN',
      automatic_retries: loss.align.filter((step) => step.then === 'trim').length,
      failure: {
        detail: `ALIGN: the cube is not detected in the pregrasp view. The recognizer's own reason: ${NO_PROPOSAL_NOTE}`,
        measurements: [
          { name: 'attempt', value: attempt, unit: null },
          { name: 'recognizer_reason', value: NO_PROPOSAL_NOTE, unit: null },
        ],
        observation_ids: [lost],
      },
    });
  };
}

/** The Observe of 20:33, run console-20260926T203349Z-0i5xpdmx: the outline fit failed, the print located the pad. */
export function buildObserveLandmarksRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  kit.start('observe', 'observe');
  kit.look('look', { cube: { box: [263, 408, 349, 490], contact: [312.2, 483.7], confidence: 0.78 }, landmarks: 97, cube_pad_m: [-0.0929, -0.1694], inside: false }, 900);
  return kit.finished({ outcome: 'unknown' });
}

/** The park before a retry: the controller's `--do exit`, EXIT then SETTLE, in a run of its own. */
export function buildParkRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  kit.start('return_arm', 'exit');
  kit.pose('EXIT to the low scan pose', 1800);
  kit.look('exit', { cube: { box: [560, 320, 660, 430], contact: [610, 424], confidence: 0.84 }, landmarks: 97, cube_pad_m: [-0.0936, -0.1698], inside: false });
  kit.pose('SETTLE goals on the measured pose', 600);
  return kit.finished({ outcome: 'unknown' });
}

/* ---- the scene scan (track S) ------------------------------------------------------------------------------------ */

/** The pan axis in PAD_FRAME, metres, as the search review measured it on the recorded pair of run m2476137. */
const PAN_AXIS_M: [number, number] = [-0.0125, -0.4132];
const HOME_PAN_DEG = -9.67;

/** The ground a view at `panOffsetDeg` from home sees, in PAD_FRAME metres: the home footprint turned about the axis. */
function footprint(panOffsetDeg: number): Array<[number, number]> {
  const home: Array<[number, number]> = [
    [-0.16, -0.06],
    [0.13, -0.06],
    [0.09, -0.27],
    [-0.12, -0.27],
  ];
  // The shoulder pan reads positive clockwise seen from above, so a positive offset turns the view clockwise.
  const angle = (-panOffsetDeg * Math.PI) / 180;
  const [ax, ay] = PAN_AXIS_M;
  return home.map(([x, y]) => {
    const dx = x - ax;
    const dy = y - ay;
    return [Math.round((ax + dx * Math.cos(angle) - dy * Math.sin(angle)) * 10000) / 10000, Math.round((ay + dx * Math.sin(angle) + dy * Math.cos(angle)) * 10000) / 10000];
  });
}

/**
 * `--do scan` under `scene_scan`: pan-only views at the low-pose shape (home, -15, home, +15, home), each a frame, a
 * `scene-view` result per view, then the fused scene as one `scene-pad` result (the pan-axis fit) and one `scene-block`
 * result per block with its class. The measurement names are the plan's (section 2.4) spelled as this file spells
 * them; `model/sceneMap.ts` reads exactly these.
 */
export function buildSceneScanRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  // The scan is the contract's scan_scene command (track P0b: command.schema.json, supervisor.py), and the run names it.
  kit.start('scan_scene', 'scan', ['scene_scan']);
  const views: Array<{ view: string; offset: number }> = [
    { view: 'home', offset: 0 },
    { view: 'minus_15', offset: -15 },
    { view: 'home', offset: 0 },
    { view: 'plus_15', offset: 15 },
    { view: 'home', offset: 0 },
  ];
  const seen: string[] = [];
  const nearView: string[] = [];
  const farView: string[] = [];
  views.forEach((view, position) => {
    const pan = Math.round((HOME_PAN_DEG + view.offset) * 100) / 100;
    const observationId = kit.obs(`view-${String(position + 1)}`);
    kit.state('SCENE_SCAN', { view: view.view, pan_deg: pan, observation_id: observationId });
    if (position > 0) kit.pose(`SCAN view ${view.view}`, 1300);
    const showsFar = view.offset === 15;
    kit.look(`view-${String(position + 1)}`, {
      cube: showsFar ? { box: [300, 470, 390, 580], contact: [345, 572], confidence: 0.8 } : { box: [499, 452, 596, 569], contact: [533.2, 556.5], confidence: 0.9 },
      landmarks: showsFar ? 88 : 97,
      cube_pad_m: showsFar ? [0.163, -0.21] : [-0.0201, -0.196],
      inside: false,
      pan_deg: pan,
    });
    seen.push(observationId);
    if (showsFar) farView.push(observationId);
    else nearView.push(observationId);
    const corners = footprint(view.offset);
    kit.scene('scene-view', [observationId], [
      { name: 'view_index', value: position, unit: null },
      { name: 'view', value: view.view, unit: null },
      { name: 'pan_deg', value: pan, unit: 'deg' },
      { name: 'pad_frame_valid', value: true, unit: null },
      { name: 'landmark_inliers', value: showsFar ? 88 : 97, unit: null },
      ...corners.flatMap(([x, y], corner) => [
        { name: `footprint_${String(corner)}_x`, value: x, unit: 'm' },
        { name: `footprint_${String(corner)}_y`, value: y, unit: 'm' },
      ]),
      { name: 'accepted', value: true, unit: null },
    ]);
  });
  kit.scene('scene-pad', seen, [
    { name: 'axis_x', value: PAN_AXIS_M[0], unit: 'm' },
    { name: 'axis_y', value: PAN_AXIS_M[1], unit: 'm' },
    { name: 'home_pan_deg', value: HOME_PAN_DEG, unit: 'deg' },
    { name: 'home_bearing_deg', value: 0.0, unit: 'deg' },
    { name: 'pan_band_deg', value: 25, unit: 'deg' },
    { name: 'fit_rotation_deg', value: 15.03, unit: 'deg' },
    { name: 'measured_pan_change_deg', value: 15.0, unit: 'deg' },
    { name: 'fit_residual_mm', value: 0.8, unit: 'mm' },
    { name: 'fit_accepted', value: true, unit: null },
    { name: 'views_seen', value: seen.length, unit: null },
  ]);
  kit.scene('scene-block', nearView, [
    { name: 'pad_x', value: -0.0201, unit: 'm' },
    { name: 'pad_y', value: -0.196, unit: 'm' },
    { name: 'class', value: 'BASE_ADVANCE', unit: null },
    { name: 'best_observation', value: nearView[0] ?? null, unit: null },
    { name: 'views_seen', value: nearView.length, unit: null },
    { name: 'spread_mm', value: 1.4, unit: 'mm' },
    { name: 'inferred', value: false, unit: null },
    { name: 'base_advance_mm', value: 108.0, unit: 'mm' },
  ]);
  kit.scene('scene-block', farView, [
    { name: 'pad_x', value: 0.163, unit: 'm' },
    { name: 'pad_y', value: -0.21, unit: 'm' },
    { name: 'class', value: 'OUTSIDE_PAN_BAND', unit: null },
    { name: 'best_observation', value: farView[0] ?? null, unit: null },
    { name: 'views_seen', value: farView.length, unit: null },
    { name: 'spread_mm', value: 0.0, unit: 'mm' },
    // Measured in the pad-valid +15 view (88 landmarks), so its position is not inferred; its pan angle from home is,
    // from one pan-axis fit (plan section 1, start 3).
    { name: 'inferred', value: false, unit: null },
    { name: 'pan_from_home_deg', value: 41, unit: 'deg' },
    { name: 'pan_inferred', value: true, unit: null },
  ]);
  kit.pose('SETTLE goals on the measured pose', 600);
  return kit.finished({ outcome: 'unknown' });
}

/** The cycle under `scene_search`: the low-pose frame decides SEARCH (SEARCH_SCENE, early exit), then start 1's pick. */
export function buildFromSceneRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  kit.start('start_cycle', 'cycle', ['scene_search']);
  kit.note('SEARCH under scene_search: the low-pose frame lists one block and decides, so SEARCH takes no view of its own; it does not read the Scan scene before it');
  const search = frontHalf(kit, FAR_BLOCK_FRONT, true);
  placedHalf(kit, search, FAR_BLOCK_PLACED);
  return kit.finished({ outcome: 'placed_new', predicates: PLACED_PREDICATES, phase: 'BASE_RETURNED' });
}

/* ---- the measuring runs of the day's one-block missions ----------------------------------------------------------- */

/** The controller's sentence for a target that measures BASE_ADVANCE in a run that carries no capability. */
const CAPABILITY_REQUIRED_SENTENCE =
  "SEARCH: this target's cube measures BASE_ADVANCE, which needs the mobile_approach capability, and this run carries none; nothing was picked up";

interface MeasuringRun {
  run: string;
  mission: string;
  selection: SearchSelection;
}

/**
 * THE MEASURING RUN of a one-block mission: the first run of an auto target carries no capability, its SEARCH selects the
 * block, the block measures BASE_ADVANCE, and the capability gate ends the run before PREGRASP with
 * mission_capability_required (run_single_cube_cycle.py, _mission_capability_gate). The [STATE] lines are the run's
 * cycle record; the SEARCH frame's box, contact, landmark count and PAD_FRAME position are its recorded perception; the
 * two predicate values are the controller's own arithmetic on that frame (placed_margin_mm, distance_outside_pad_mm and
 * coarse_pregrasp_from_low_pose against the saved low-pose pan).
 */
function buildMeasuringRunOf(measuring: MeasuringRun): RunFactory {
  return (index, startSeq, startMonoNs) => {
    const kit = kitFor(index, startSeq, startMonoNs);
    kit.start('start_cycle', 'cycle');
    kit.note(
      `this plays ${measuring.run}, the first run of ${measuring.mission}: its [STATE] lines, its SEARCH frame's box, contact, landmark count and pad position, and its reason code are that run's record; the two predicate values are the controller's arithmetic on that frame; the pad outline values are not the recorded ones`,
    );
    const search = searchAndSelect(kit, measuring.selection);
    return kit.finished({
      outcome: 'failed',
      exit_status: 5,
      reason_code: 'mission_capability_required',
      phase: 'TARGET_SELECTED',
      failure: {
        detail: CAPABILITY_REQUIRED_SENTENCE,
        measurements: [
          { name: 'declared_mode', value: 'auto', unit: null },
          { name: 'measured_mode', value: 'BASE_ADVANCE', unit: null },
        ],
        observation_ids: [search],
      },
    });
  };
}

/** Run console-20260926T201525Z-m31e0b20, the first run of mission-88094cda64e1, whose second run is mb3c1048. */
const MEASURING_M31E0B20: MeasuringRun = {
  run: 'console-20260926T201525Z-m31e0b20',
  mission: 'mission-88094cda64e1',
  selection: {
    pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 37.85, wrist_flex: 67.21 },
    search: { cube: { box: [499, 451, 596, 569], contact: [533.8, 556.5], confidence: 0.912 }, landmarks: 93, cube_pad_m: [-0.019854931825185083, -0.19603474664812026], inside: false },
    seen_pad_m: [-0.0199, -0.196],
    mode: 'BASE_ADVANCE',
    start_inside_mm: -48.53474664812027,
    pan_from_home_deg: 2.155054945054946,
  },
};

/** Run console-20260926T203727Z-m07e475e, the first run of mission-c350ab3ee492, whose second run is m2476137. */
const MEASURING_M07E475E: MeasuringRun = {
  run: 'console-20260926T203727Z-m07e475e',
  mission: 'mission-c350ab3ee492',
  selection: {
    pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 38.02, wrist_flex: 67.21 },
    search: { cube: { box: [263, 408, 348, 491], contact: [311.7, 483.8], confidence: 0.77 }, landmarks: 91, cube_pad_m: [-0.0933966084784635, -0.16972856252765275], inside: false },
    seen_pad_m: [-0.0934, -0.1697],
    mode: 'BASE_ADVANCE',
    start_inside_mm: -22.228562527652763,
    pan_from_home_deg: 20.975054945054946,
  },
};

export const buildMeasuringRunM31e0b20 = buildMeasuringRunOf(MEASURING_M31E0B20);
export const buildMeasuringRunM07e475e = buildMeasuringRunOf(MEASURING_M07E475E);

/* ---- the record of 2026-09-27 on 8771 (mission-f65c3cf8e99f) ------------------------------------------------------ */

/**
 * RUN 1 OF THE 27TH (console-20260927T141638Z-m2fc135a): SEARCH, TARGET_SELECTED BASE_ADVANCE, the error line with the
 * capability gate's sentence, and the run failed with mission_capability_required at TARGET_SELECTED. The SEARCH frame
 * and the numbers are invented; the states, the sentence, the reason code, the exit status and the phase are the record's.
 */
export function buildMeasuringRunM2fc135a(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  kit.start('start_cycle', 'cycle');
  kit.note('this plays console-20260927T141638Z-m2fc135a, the first run of mission-f65c3cf8e99f: its states, its error line, its reason code, exit status and phase are that run\'s record; the frame and the numbers are invented');
  const search = searchAndSelect(kit, MEASURING_M07E475E.selection);
  kit.error(CAPABILITY_REQUIRED_SENTENCE, 'mission_capability_required');
  return kit.finished({
    outcome: 'failed',
    exit_status: 5,
    reason_code: 'mission_capability_required',
    phase: 'TARGET_SELECTED',
    failure: {
      detail: CAPABILITY_REQUIRED_SENTENCE,
      measurements: [
        { name: 'declared_mode', value: 'auto', unit: null },
        { name: 'measured_mode', value: 'BASE_ADVANCE', unit: null },
      ],
      observation_ids: [search],
    },
  });
}

/** The front half of run m6ed2dd9: SEARCH, the selection, PREGRASP and two ALIGN attempts, the second accepted. */
const STOPPED_HOLDING_FRONT: FrontHalf = {
  ...MEASURING_M07E475E.selection,
  coarse: { pan_deg: -8.26, forward_mm: 196.0, cube_out_mm: 18.0 },
  pregrasp: { cube: { box: [560, 330, 660, 440], contact: [610, 434], confidence: 0.86 }, landmarks: 0 },
  align: [
    { lateral_mm: 11.2, radial_mm: 6.1, aligned: false, tool: 'trim', frame: { cube: { box: [560, 330, 660, 440], contact: [610, 434], confidence: 0.86 }, landmarks: 0 }, then: 'trim', trim_from_home_deg: 1.4 },
    { lateral_mm: 2.3, radial_mm: 4.0, aligned: true, tool: 'accept', frame: { cube: { box: [590, 330, 690, 440], contact: [640, 434], confidence: 0.88 }, landmarks: 0 }, then: 'accept' },
  ],
};

/**
 * RUN 2 OF THE 27TH (console-20260927T142146Z-m6ed2dd9), in its shapes: the [STATE] lines SEARCH, TARGET_SELECTED,
 * PREGRASP, ALIGN, ALIGN, APPROACH, STRADDLING, GRASP_CLOSE; a false `straddle_far_edge_row` (333 against 420 px) with no
 * action id and a frame, 31 s before the stop; the close on the block (67.91 against 67.4); the VERIFICATION lift; then,
 * under one action id, the CLEARANCE lift planned, `base_zero` false (0 against 3 reads), the measured state (rotation
 * 1.171875 deg/s, gripper 68.116) and the refusal base_not_zero_before_segment. The run ends stopped, exit 4, at
 * GRASP_CLOSE, and its failure record carries no measurement and no frame. The frames and the positions are invented.
 */
export function buildStoppedHoldingRun(index: number, startSeq: number, startMonoNs: number): SyntheticRun {
  const kit = kitFor(index, startSeq, startMonoNs);
  kit.start('start_cycle', 'cycle');
  kit.note('this plays console-20260927T142146Z-m6ed2dd9, the second run of mission-f65c3cf8e99f: its states, the far-edge predicate, the close, the refused CLEARANCE lift with its predicate, reading and reason code, and its end are that run\'s record; the frames and positions are invented');
  frontHalf(kit, STOPPED_HOLDING_FRONT);
  kit.state('APPROACH', { radial_mm: 4.0 });
  kit.pose('APPROACH descent');
  kit.pose('APPROACH proven grasp geometry');
  kit.predicate('straddling', true, 3.1, 8.0, 'mm', [], ['tool_up_mm_vs_proven_grasp']);
  const straddle = kit.look('straddle', { cube: { box: [520, 300, 700, 470], contact: [610, 460], confidence: 0.8 }, landmarks: 0 });
  kit.predicate('straddle_far_edge_row', false, 333, 420, 'px', [straddle], ['far_edge_row_px']);
  kit.state('STRADDLING', { tool: 'fwd+35 dz+2' });
  kit.grip('grip latch', GRIPPER_LATCH);
  kit.state('GRASP_CLOSE', { attempt: 0, gripper: 67.91, closed_on_something: true, tool: 'fwd+35 dz+2' });
  kit.predicate('closed_on_something', true, 67.91, 67.4, 'gripper_units', [], GRIPPER_SCALARS);
  kit.pose('VERIFICATION lift', 900);
  kit.refusedAction(
    'CLEARANCE lift',
    'arm_pose',
    { name: 'base_zero', measured: 0, threshold: 3, unit: 'reads' },
    { gripper: 68.116, base_velocity: { x_mps: 0, y_mps: 0.0025566346464760688, theta_dps: 1.171875 } },
    'base_not_zero_before_segment',
    'base velocity not confirmed zero before an arm segment: stopping all motion',
  );
  return kit.finished({
    outcome: 'stopped',
    exit_status: 4,
    reason_code: 'base_not_zero_before_segment',
    phase: 'GRASP_CLOSE',
    failure: { detail: 'base velocity not confirmed zero before an arm segment: stopping all motion', measurements: [], observation_ids: [] },
  });
}

/* ---- the candidates ---------------------------------------------------------------------------------------------- */

// THE ROLES, LABELS AND EXPECTATIONS ARE THE RUNTIME'S (demo_runtime/cake_activation.py, ROLE_LABELS and ROLE_EXPECT):
// `expect` is the activation state code the role expects, which the page compares with the state the runtime records.
const ABORT_SWAP: ScenarioCandidate = {
  key: 'mobile-demo-active-successor-abort-swap',
  payload_digest: `sha256:${'c3'.repeat(32)}`,
  role: 'refused-demo',
  label: 'Abort-swap: Cake opens the transaction and aborts it, the slot unchanged',
  expect: 'REJECTED_BY_CAKE',
  outcome: 'rejected',
};

const SWAPPED: ScenarioCandidate = {
  key: 'mobile-demo-swapped',
  payload_digest: `sha256:${'d4'.repeat(32)}`,
  role: 'activate',
  label: 'Activate the capability (the swapped Plan)',
  expect: 'OBSERVED',
  outcome: 'observed',
};

const TAMPERED: ScenarioCandidate = {
  key: 'mobile-demo-swapped-tampered',
  payload_digest: `sha256:${'e5'.repeat(32)}`,
  role: 'tampered-demo',
  label: 'Tampered twin: one byte flipped, refused by an integrity check before any transaction',
  expect: 'REFUSED_BEFORE_TRANSACTION',
  outcome: 'refused_before_transaction',
};

const UNDO: ScenarioCandidate = {
  key: 'mobile-demo-starting',
  payload_digest: `sha256:${'8b'.repeat(32)}`,
  role: 'undo',
  label: 'Undo: back to the starting Plan',
  expect: 'COMPLETED_NOT_OBSERVED',
  outcome: 'undo',
};

/** The abort-swap and the swap as 8771 served them on 2026-09-27: key and digest, and no role, label or expect. */
const ABORT_SWAP_BARE: ScenarioCandidate = { key: ABORT_SWAP.key, payload_digest: ABORT_SWAP.payload_digest, outcome: 'rejected' };
const SWAPPED_BARE: ScenarioCandidate = { key: SWAPPED.key, payload_digest: SWAPPED.payload_digest, outcome: 'observed' };

/** The refusal code a tampered Plan gets from the resident before any transaction opens. Invented for the scenario. */
export const TAMPERED_REFUSAL_CODE = 'plan_integrity_mismatch';

/* ---- the catalogue ----------------------------------------------------------------------------------------------- */

const LOOK_FIRST = { reason_code: 'scenario_starts_with_look', detail: 'this scenario is a Look: press Observe scene, which moves nothing' };
const CHECK_FIRST = {
  reason_code: 'scenario_starts_with_the_capability_check',
  detail: 'this scenario is the capability slot check with the host held and no mission: apply a prepared Plan from the check',
};

/**
 * The Retry of hold-then-retry-placed plays start 2's placed run, whose block lies elsewhere than the one ALIGN lost at
 * 21:31; the scenario says so rather than let a Retry appear to find the block somewhere else.
 */
const RETRY_MOVED_BLOCK_NOTE =
  'the block was moved by hand before this Retry, from (+0.082, -0.179) where ALIGN lost it at 21:31 to (-0.094, -0.170), because the placed attempt played here is run m2476137 of start 2';

export const SCENARIO_CATALOGUE: readonly ScenarioSpec[] = [
  {
    name: 'one-block',
    title: 'The one-block demo, rehearsed',
    reproduces: 'the one-block demo of docs/autonomous-demo/one-block-demo-2026-09-26.md, section 6',
    shows: 'the sweep under the word search_sweep, the hold for the capability, the abort-swap refused, the swapped Plan observed, the resume, the pick, the pulses, the placement',
    trigger: 'start',
    flow: [
      { build: buildMeasuringRun },
      { build: buildCarryingRun },
    ],
    candidates: [ABORT_SWAP, SWAPPED],
  },
  {
    name: 'near-block-grasp-miss',
    title: 'Near block: refused at SEARCH, where the day missed the grasp',
    reproduces: "run console-20260926T191919Z-m8c5f9e9 at 19:19 UTC (grasp_missed on the day), as the head's controller decides it: search_cube_inside_pregrasp_reach",
    shows:
      "Find refused at SEARCH before any motion: the block stands 31.5 mm nearer than the pregrasp can reach (the coarse estimate said 44.5 mm in, and the pregrasp clamps at -13 mm), so it must move at least 57.5 mm away; the mission holds for your decision: move the block, then Try again (the park, then a fresh attempt), or Stop; a rehearsal moves no block, so the fresh attempt is refused the same way and the second hold offers only Stop. On the day this block was driven: ALIGN measured it about 18 mm inside the proven radius and three empty closes (67.288, 67.150, 67.357 against 67.4) retried 14 mm outward",
    trigger: 'start',
    flow: [{ build: buildNearBlockRefusedRun }, { build: buildNearBlockRefusedRun}],
  },
  {
    name: 'far-block-success',
    title: 'Far block, placed from start 1',
    reproduces: 'run console-20260926T201540Z-mb3c1048 at 20:15 UTC, placed_new',
    shows: 'every stage passed: 108 mm planned, pulses 26.6, 40.1 and 39.7 mm, admitted at 43.8 mm, placed 47.5 mm from the edge, returned to 2.9 mm along and 2.2 mm sideways',
    trigger: 'start',
    flow: [{ build: buildFarBlockRun }],
  },
  {
    name: 'far-left-yawed-success',
    title: 'Far-left yawed block, placed from start 2',
    reproduces: 'run console-20260926T203744Z-m2476137 at 20:37 UTC, placed_new',
    shows: 'one pan trim inside Pick, pulses 23.5, 42.1 and 14.9 mm, placed 51.1 mm from the edge, returned to 0.1 mm along the axis and 9.1 mm sideways, which no gate checks',
    trigger: 'start',
    flow: [{ build: buildFarLeftRun }],
  },
  {
    name: 'not-placeable-side',
    title: 'Refused before motion: the side limit',
    reproduces: 'start 3 refused at 20:58 and 21:08 UTC, mission_binding_candidate_not_placeable, block at (+0.163, -0.210)',
    shows: 'Find refused before motion: the right edge along the reach axis 9.4 mm short of 58.96 mm after 120 mm of advance, the along-axis budget also 1.6 mm short, the hint to move the block 10 mm left and 3 mm toward the pad; the mission holds for the operator and the runtime offers only Stop',
    trigger: 'start',
    flow: [{ build: buildNotPlaceableRun('side') }],
  },
  {
    name: 'not-placeable-budget',
    title: 'Refused before motion: the advance budget',
    reproduces: 'the block at 21:17 UTC, mission_binding_candidate_not_placeable, block at (+0.064, -0.220)',
    shows: 'Find refused before motion: 131.6 mm of base advance needed, 11.6 mm past the 120 mm along-axis budget, with the hint to move the block 12 mm toward the pad; the mission holds for the operator and the runtime offers only Stop',
    trigger: 'start',
    flow: [{ build: buildNotPlaceableRun('budget') }],
  },
  {
    name: 'align-no-proposal',
    title: 'ALIGN lost the block: no proposal',
    reproduces: 'the run of 21:24 UTC, console-20260926T212418Z-m76fe86c, align_cube_not_detected at attempt 2 (the full record is on the robot)',
    shows: "Pick failed at ALIGN: one pan trim at lateral 9.2 mm, then accepted at lateral 3.4 mm on the lateral value alone with the radial at +14.5 mm, descended, and the recognizer's own reason: no detector proposal: nothing to verify; the mission holds for your decision with one Try again offered (the park, then a fresh attempt); the fresh attempt replays the same loss, so the second hold offers only Stop",
    trigger: 'start',
    flow: [{ build: buildAlignLostRun('no-proposal') }, { build: buildAlignLostRun('no-proposal', true)}],
  },
  {
    name: 'align-clipped-top',
    title: 'ALIGN lost the block at the top edge',
    reproduces: 'the run of 21:31 UTC, console-20260926T213112Z-m66738ff, align_cube_not_detected at attempt 1 (the full record is on the robot)',
    shows: "Pick failed at ALIGN: accepted at lateral 1.4 mm with the radial at +12.4 mm, descended, and the recognizer's own reason: no detector proposal: nothing to verify; the console's inference, labelled as one: the recorded frame showed the block cut by the top edge and blurred, the arm's shadow on the floor below it; the mission holds for your decision with one Try again offered (the park, then a fresh attempt); the fresh attempt replays the same loss, so the second hold offers only Stop",
    trigger: 'start',
    flow: [{ build: buildAlignLostRun('clipped-top') }, { build: buildAlignLostRun('clipped-top', true)}],
  },
  {
    name: 'observe-landmarks-only',
    title: 'Look: the pad found by its print',
    reproduces: 'the Observe of run console-20260926T203349Z-0i5xpdmx at 20:33 UTC',
    shows: 'Pad located by its print, 97 landmarks; the outline fit failed (a diagnostic, never a decision); the block at (-0.093, -0.169) m outside the pad',
    trigger: 'look',
    flow: [],
    start_refusal: LOOK_FIRST,
    look: buildObserveLandmarksRun,
  },
  {
    name: 'preview-stall',
    title: 'The wrist preview stalls',
    reproduces: 'the 8770 preview stall of 2026-09-26 (its cause is UNKNOWN)',
    shows: 'the wrist preview yielding to decision captures, then Stalled, with the reason no_sample_within_window and its sequence frozen, never an age, while the run places the block; no picture is drawn, because the rehearsal serves no frames (a live runtime\'s kept picture is drawn through a gap at the fixed time it was decoded)',
    trigger: 'start',
    flow: [{ build: buildFarBlockRun }],
    preview: 'stall',
  },
  {
    name: 'preview-not-configured',
    title: 'No preview configured',
    reproduces: 'the 8771 runtime of 2026-09-26, whose configuration says preview null',
    shows: 'the live view says the runtime was given no preview, in its code preview_not_configured; the run places the block',
    trigger: 'start',
    flow: [{ build: buildFarBlockRun }],
    preview: 'not_configured',
  },
  {
    name: 'abort-swap-repeated',
    title: 'The abort-swap pressed again and again',
    reproduces:
      'the activation beat of mission-e96d6f4083e2: fourteen abort-swap requests REJECTED_BY_CAKE, then the swapped Plan OBSERVED; its runs are those of mission-88094cda64e1 (m31e0b20 held for the capability, mb3c1048 placed), whose measuring run is on record',
    shows: 'no candidate preselected; each abort-swap a transactional refusal with the slot unchanged; the swapped Plan observed at the next generation; the mission resumed and placed',
    trigger: 'start',
    flow: [
      { build: buildMeasuringRunM31e0b20 },
      { build: buildFarBlockRun },
    ],
    candidates: [ABORT_SWAP, SWAPPED],
  },
  {
    name: 'bring-up-not-confirmed',
    title: 'Bring-up: the robot not confirmed',
    reproduces: 'the bring-up of 2026-09-26: nothing on screen named the missing prerequisite',
    shows: 'the Ready chip names the first unmet prerequisite (confirm this robot), "arm acquired" is not readable here, Start refused until the confirmation; then the mission runs',
    trigger: 'start',
    flow: [{ build: buildFarBlockRun }],
    robot: 'not_confirmed',
  },
  {
    name: 'host-not-supervised',
    title: 'Bring-up: a host outside Cake',
    reproduces: 'the bring-up of 2026-09-26: the arm not acquired after a power cycle and a stand-alone host started by hand',
    shows: 'the supervisor slot has no running child while a host runs on the robot: a host outside Cake, nothing to confirm, Start refused',
    trigger: 'start',
    flow: [],
    robot: 'host_outside_cake',
  },
  {
    name: 'scene-scan',
    title: 'Scan the scene, then pick from it',
    reproduces: 'the words scene_scan and scene_search (track S)',
    shows: 'Scan scene first: the scene map with five pan-only views, the pan axis and the 25 deg wedge from an accepted fit, one block BASE_ADVANCE and one OUTSIDE_PAN_BAND (its pan angle inferred); then Start: the block picked and placed in a mission of its own',
    // THE HEAD'S MISSION CARRIES CYCLES AND PARKS ONLY: the scan is the operator's own Scan scene, pressed before Start.
    trigger: 'scan',
    flow: [{ build: buildFromSceneRun }],
    scan: buildSceneScanRun,
  },
  {
    name: 'hold-then-retry-placed',
    title: 'Hold for the operator with the Retry switch on, Retry, placed',
    reproduces:
      'the operator hold and Retry (track R, ruling 1) after a failure like the run of 21:31 UTC, on a runtime whose configuration sets mission.retry and names the attended label',
    shows: 'the mission held with the stage, the measurement, the frame and the next actions; Retry parks the arm in a run of its own and a fresh attempt places the block',
    trigger: 'start',
    flow: [
      { build: buildAlignLostRun('clipped-top') },
      { build: farLeftRun(RETRY_MOVED_BLOCK_NOTE)},
    ],
  },
  {
    name: 'hold-live-default',
    title: 'Hold for the operator as the LIVE runtime holds by default: Stop only',
    reproduces:
      'the hold a LIVE runtime serves with mission.retry off, as it ships: the same failure as hold-then-retry-placed, held with next actions [stop], and a Retry answered mission_retry_not_enabled',
    shows: 'the mission held with the stage, the measurement and the frame; only Stop is offered, and Try again is off with the runtime offering only Stop',
    trigger: 'start',
    flow: [{ build: buildAlignLostRun('clipped-top') }],
    retry_refusal: 'mission_retry_not_enabled',
  },
  {
    name: 'tampered-refused-before-transaction',
    title: 'The tampered twin refused',
    reproduces:
      'the tampered twin of the swapped Plan (track C): an integrity refusal before any transaction; the runs are those of mission-c350ab3ee492 (m07e475e held for the capability, m2476137 placed)',
    shows: 'the tampered twin refused before any transaction with the slot, the session and the generation unchanged, called an integrity refusal and never a signature refusal; then the swapped Plan observed and the mission placed',
    trigger: 'start',
    flow: [
      { build: buildMeasuringRunM07e475e },
      { build: buildFarLeftRun },
    ],
    candidates: [SWAPPED, TAMPERED],
  },
  {
    name: 'undo-to-inert',
    title: 'Activate, then undo to inert',
    reproduces: 'the undo candidate (track C): the starting Plan applied again, the slot back to the inert capsule',
    shows: 'the capability slot check with the host held and no mission: the swapped Plan observed at the next generation, then the starting Plan back to the inert capsule, the capability inactive',
    trigger: 'check',
    flow: [],
    start_refusal: CHECK_FIRST,
    check_candidates: [UNDO, SWAPPED],
    // A robot is reported, its host held, so the live slot says which check set it (audit C1).
    robot: 'bench',
  },
  {
    name: 'swap-then-stopped-holding',
    title: 'Swapped, then stopped holding the block',
    reproduces:
      'mission-f65c3cf8e99f on 8771, 2026-09-27: run m2fc135a needed the capability, the abort-swap was refused with the slot unchanged 1 -> 1, the swapped Plan activated 1 -> 2, and run m6ed2dd9 stopped at GRASP_CLOSE on base_not_zero_before_segment with the block held',
    shows:
      'the mission failed and its run stopped by the controller, the stop joined to its own reading (base_zero 0 against 3 reads, rotation 1.17 deg/s) and never to the far-edge pixels, holding the block (gripper 68.12), the steps open the gripper by hand, return arm, start; two Cake cards, refused by Cake 1 -> 1 then activated 1 -> 2, with no expected outcome served; a signature check from an earlier runtime session',
    trigger: 'start',
    flow: [{ build: buildMeasuringRunM2fc135a }, { build: buildStoppedHoldingRun }],
    candidates: [ABORT_SWAP_BARE, SWAPPED_BARE],
    robot: 'bench',
    signature: 'earlier-session',
    slot_generation: 1,
  },
];

export function scenarioSpec(name: string | null): ScenarioSpec | null {
  return SCENARIO_CATALOGUE.find((spec) => spec.name === name) ?? null;
}

/* ---- the robot the runtime reports -------------------------------------------------------------------------------- */

const SYNTHETIC_ROBOT_SESSION = '5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e';
const SYNTHETIC_HOST_PID = 4242;
const SYNTHETIC_HOST_SLOT = '5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a';
const SYNTHETIC_CAPABILITY_SLOT = '5b5b5b5b5b5b5b5b5b5b5b5b5b5b5b5b';
export const SYNTHETIC_HOST_INSTANCE = { slot_instance: '7003298303284871169', spawn_sequence: 2, pid: SYNTHETIC_HOST_PID };

/**
 * The robot status body, in `robot_status.schema.json` v2's shape, for the two bring-up scenarios. Every value is
 * invented; the display label says it is the simulation's. `confirmed` is whether the operator confirmed the session.
 */
export interface BenchSlot {
  generation: number;
  active: boolean;
  capsule: string;
  /** Whether the host is held (a capability check scenario): no host process under the supervisor slot. */
  hostHeld: boolean;
}

export function syntheticRobotBody(kind: 'not_confirmed' | 'host_outside_cake' | 'bench', confirmed: boolean, bench: BenchSlot | null = null): Record<string, unknown> {
  if (kind === 'bench' && bench !== null) return benchRobotBody(bench);
  const observed = { wall_utc: null, age_ms: 800 };
  const outside = kind === 'host_outside_cake';
  const permitted = !outside && confirmed;
  return {
    robot_status_version: '2',
    configured: true,
    deployment: 'robot_resident',
    reachable: true,
    reason_code: null,
    stale: false,
    clock_domain: 'laptop_monotonic',
    display_label: 'Rehearsal robot of the simulation',
    identity: {
      is_robot: true,
      reason_code: null,
      expected_build_identity: '5e5e5e5e5e5e5e5e',
      answering_build_identity: '5e5e5e5e5e5e5e5e',
      answering_target_profile_digest: '5f5f5f5f5f5f5f5f',
      display_label: 'Rehearsal robot of the simulation',
      labelled: true,
      build_identity: '5e5e5e5e5e5e5e5e',
      session_uuid: SYNTHETIC_ROBOT_SESSION,
      stale: false,
      observed,
    },
    connection: {
      deployment: 'robot_resident',
      forward: { state: 'connected', reason: null, since_wall_utc: null, since_age_ms: 600_000, starts: 1, deaths: 0, restarts: 0, next_attempt_in_ms: null },
      observed,
    },
    resident: {
      reachable: true,
      stale: false,
      reason_code: null,
      session_uuid: SYNTHETIC_ROBOT_SESSION,
      build_identity: '5e5e5e5e5e5e5e5e',
      plan_digest: '8b8b8b8b8b8b8b8b',
      target_profile_digest: '5f5f5f5f5f5f5f5f',
      epoch: 4,
      session_changed: false,
      observed,
    },
    slots: {
      stale: false,
      reason_code: null,
      observed,
      items: [
        { name: 'supervisor.lekiwi', uuid: SYNTHETIC_HOST_SLOT, capsule: '1e1e1e1e1e1e1e1e', generation: 1, instance: SYNTHETIC_HOST_INSTANCE.slot_instance, state: 'ACTIVE' },
        { name: 'mobile_approach', uuid: SYNTHETIC_CAPABILITY_SLOT, capsule: '1f1f1f1f1f1f1f1f', generation: 1, instance: '811746368697286402', state: 'ACTIVE' },
      ],
    },
    health: {
      stale: false,
      reason_code: null,
      observed,
      items: [
        outside
          ? { slot: SYNTHETIC_HOST_SLOT, state_name: 'STOPPED', child_pid: 0, restarts: 0, spawns: 1, last_status: 1 }
          : { slot: SYNTHETIC_HOST_SLOT, state_name: 'HEALTHY', child_pid: SYNTHETIC_HOST_PID, restarts: 1, spawns: 2, last_status: 0 },
        { slot: SYNTHETIC_CAPABILITY_SLOT, state_name: 'HEALTHY', child_pid: 0, restarts: 0, spawns: 0, last_status: 0 },
      ],
    },
    host: outside
      ? {
          supervised: false,
          reason_code: 'supervisor_child_not_running',
          slot_uuid: SYNTHETIC_HOST_SLOT,
          slot_state: 'ACTIVE',
          slot_instance: SYNTHETIC_HOST_INSTANCE.slot_instance,
          child_pid: 0,
          spawn_sequence: 3,
          health_state: 'STOPPED',
          readiness_state: 'running_normal',
          readiness_age_ms: 900,
          observed,
        }
      : {
          supervised: true,
          reason_code: null,
          slot_uuid: SYNTHETIC_HOST_SLOT,
          slot_state: 'ACTIVE',
          slot_instance: SYNTHETIC_HOST_INSTANCE.slot_instance,
          child_pid: SYNTHETIC_HOST_PID,
          spawn_sequence: 2,
          health_state: 'HEALTHY',
          readiness_state: 'running_normal',
          readiness_age_ms: 900,
          observed,
        },
    capability: { configured: true, name: 'mobile_approach', active: false, reason_code: 'slot_not_active', capsule: '1f1f1f1f1f1f1f1f', generation: 1, child_pid: null, stale: false, observed },
    updates: { stale: false, reason_code: null, dropped: 0, excluded_admin_records: 0, other_records: 0, observed, items: [] },
    motion_permission: {
      applies: true,
      permitted,
      reason_code: permitted ? null : 'robot_session_not_confirmed',
      detail: permitted
        ? "the robot's resident reports the session and host instance the operator confirmed"
        : 'no robot resident session and host instance have been confirmed by the operator in this runtime, so no command that moves the robot is accepted; confirm the connected robot first (observe stays available)',
      confirmable: !outside,
      changed_component: null,
      confirmed_session_uuid: permitted ? SYNTHETIC_ROBOT_SESSION : null,
      confirmed_host_instance: permitted ? { ...SYNTHETIC_HOST_INSTANCE } : null,
      confirmed_wall_utc: null,
      current_session_uuid: SYNTHETIC_ROBOT_SESSION,
      current_host_instance: outside ? null : { ...SYNTHETIC_HOST_INSTANCE },
      current_host_reason_code: outside ? 'robot_host_not_running' : null,
      observed,
    },
  };
}

const SYNTHETIC_HOLDER_SLOT = '5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c';

/**
 * THE BENCH ROBOT: the robot of 2026-09-27 on 8771, in the runtime's shape and marked as the simulation's in its label.
 * Confirmed and permitted; its resident lists the capability slot, which carries the scenario's own slot (the swap moves
 * it to the next generation with the mobile capsule), and the holder slot, which keeps the inert capsule with health
 * UNKNOWN and no process, as the live resident listed it. In a check scenario the host is held (no host process).
 */
function benchRobotBody(slot: BenchSlot): Record<string, unknown> {
  const observed = { wall_utc: null, age_ms: 800 };
  const host = slot.hostHeld
    ? { supervised: true, reason_code: 'supervisor_child_not_running', slot_uuid: SYNTHETIC_HOST_SLOT, slot_state: 'ACTIVE', slot_instance: SYNTHETIC_HOST_INSTANCE.slot_instance, child_pid: 0, spawn_sequence: 2, health_state: 'STOPPED', readiness_state: null, readiness_age_ms: null, observed }
    : { supervised: true, reason_code: null, slot_uuid: SYNTHETIC_HOST_SLOT, slot_state: 'ACTIVE', slot_instance: SYNTHETIC_HOST_INSTANCE.slot_instance, child_pid: SYNTHETIC_HOST_PID, spawn_sequence: 2, health_state: 'HEALTHY', readiness_state: 'running_normal', readiness_age_ms: 900, observed };
  const base = syntheticRobotBody('not_confirmed', true);
  return {
    ...base,
    display_label: 'Rehearsal bench robot of the simulation',
    identity: { ...(base['identity'] as Record<string, unknown>), display_label: 'Rehearsal bench robot of the simulation' },
    slots: {
      stale: false,
      reason_code: null,
      observed,
      items: [
        { name: 'supervisor.lekiwi', uuid: SYNTHETIC_HOST_SLOT, capsule: '1e1e1e1e1e1e1e1e', generation: 1, instance: SYNTHETIC_HOST_INSTANCE.slot_instance, state: 'ACTIVE' },
        { name: 'mobile_approach', uuid: SYNTHETIC_CAPABILITY_SLOT, capsule: slot.capsule, generation: slot.generation, instance: String(slot.generation), state: 'ACTIVE' },
        { name: 'mobile_approach.holder', uuid: SYNTHETIC_HOLDER_SLOT, capsule: INERT_CAPSULE, generation: slot.generation, instance: String(slot.generation + 100), state: 'ACTIVE' },
      ],
    },
    health: {
      stale: false,
      reason_code: null,
      observed,
      items: [
        slot.hostHeld
          ? { slot: SYNTHETIC_HOST_SLOT, state_name: 'STOPPED', child_pid: 0, restarts: 0, spawns: 2, last_status: 0 }
          : { slot: SYNTHETIC_HOST_SLOT, state_name: 'HEALTHY', child_pid: SYNTHETIC_HOST_PID, restarts: 0, spawns: 1, last_status: 0 },
        { slot: SYNTHETIC_CAPABILITY_SLOT, state_name: 'HEALTHY', child_pid: slot.active ? 4343 : 0, restarts: 0, spawns: slot.active ? 1 : 0, last_status: 0 },
        { slot: SYNTHETIC_HOLDER_SLOT, state_name: 'UNKNOWN', child_pid: 0, restarts: 0, spawns: 0, last_status: 0 },
      ],
    },
    host,
    capability: { configured: true, name: 'mobile_approach', active: slot.active, reason_code: slot.active ? null : 'slot_not_active', capsule: slot.capsule, generation: slot.generation, child_pid: slot.active ? 4343 : null, stale: false, observed },
  };
}

/* ---- the wrist preview the runtime reports ---------------------------------------------------------------------- */

const PREVIEW_ROLE = 'wrist';

function previewBase(): PreviewSample {
  return {
    available: false,
    state: 'disconnected',
    reason: null,
    role: PREVIEW_ROLE,
    seq: null,
    source_seq: null,
    capture_clock: null,
    capture_semantics: null,
    capture_age_ms: null,
    capture_age_reason: 'no_clock_relation_between_source_and_laptop',
    service_latency_ms: null,
    receive_age_ms: null,
    measured_fps: null,
    frame: null,
    stream: null,
    gaps: null,
    session_changes: null,
    capture_time_stalls: null,
    malformed: null,
  };
}

/** How long the one stall of the `preview-stall` scenario lasts, and after how many ticks of a run it begins. */
const STALL_BEGINS_AFTER_TICKS = 40;
const STALL_TICKS = 24;

/**
 * The preview one scenario reports, one line per telemetry tick (four a second). `not_configured` is the runtime given
 * no preview. `stall` is a preview that advances, yields to every decision capture (`skipped_for_mission`, the source
 * standing aside, which is not a stall) and stalls once, a fixed time into the first run, in the runtime's shape: no
 * sample within the window, the sequence frozen, the receive age growing, and no measured value. Its capture clock
 * never stops while frames arrive, so `capture_time_stalls` stays 0. The picture itself is never served: the
 * simulation serves no frames.
 */
export class PreviewScript {
  private seq = 100;
  private skipped = 0;
  private runTicks = 0;
  private stalledFor = 0;
  private stallDone = false;
  private sinceFrameMs = 0;

  constructor(private readonly kind: 'stall' | 'not_configured') {}

  next(context: { running: boolean; capturing: boolean }): PreviewSample {
    const base = previewBase();
    if (this.kind === 'not_configured') return { ...base, reason: 'preview_not_configured', capture_age_reason: 'no_clock_relation_between_source_and_laptop' };
    if (context.running) this.runTicks += 1;
    let state: 'connected' | 'stalled' = 'connected';
    if (!this.stallDone && this.runTicks >= STALL_BEGINS_AFTER_TICKS) {
      this.stalledFor += 1;
      state = 'stalled';
      if (this.stalledFor >= STALL_TICKS) this.stallDone = true;
    } else if (context.capturing) {
      this.skipped += 1;
    } else {
      this.seq += 1;
      this.sinceFrameMs = 0;
    }
    if (state === 'stalled' || context.capturing) this.sinceFrameMs += 250;
    if (state === 'stalled') {
      // The runtime's stalled slot (demo_runtime/sources.py: _freshen, slot, unavailable_preview): unavailable, the
      // runtime's reason, the last seq and how long ago it arrived kept, and every measured field null. Only a
      // connected source reports values.
      return {
        ...base,
        state,
        reason: 'no_sample_within_window',
        seq: this.seq,
        receive_age_ms: 40 + this.sinceFrameMs,
        stream: { pixel_format: null, source_fps_reported: null, source_fps_measured: null, source_width: null, source_height: null },
      };
    }
    return {
      ...base,
      available: true,
      state,
      reason: null,
      seq: this.seq,
      source_seq: this.seq + 9000,
      capture_clock: { session: 'synthetic-camera-session', seq: this.seq + 9000, sample_mono_ns: 3_000_000_000 + this.seq * 250_000_000, clock_domain: 'pi_monotonic' },
      capture_semantics: 'driver_capture_time',
      service_latency_ms: 18,
      receive_age_ms: 40 + this.sinceFrameMs,
      measured_fps: 4,
      frame: { width_px: 640, height_px: 360 },
      stream: { pixel_format: 'MJPEG', source_fps_reported: 30, source_fps_measured: 29.8, source_width: 1280, source_height: 720 },
      gaps: 0,
      session_changes: 0,
      capture_time_stalls: 0,
      malformed: 0,
      skipped_for_mission: this.skipped,
    };
  }
}

/* ---- the capability slot check, with no mission -------------------------------------------------------------------- */

export const INERT_CAPSULE = `sha256:${'1e'.repeat(32)}`;
export const MOBILE_CAPSULE = `sha256:${'a7'.repeat(32)}`;

/** The capability slot as the simulated resident reads it back at one generation. Every value invented. */
export function syntheticSlot(generation: number, active: boolean): Record<string, unknown> {
  return {
    read: 'ok',
    slot_uuid: 'synthetic-capability-slot',
    name: 'mobile_approach',
    state: 'ACTIVE',
    capsule: active ? MOBILE_CAPSULE : INERT_CAPSULE,
    generation: String(generation),
    instance: String(generation),
    session_uuid: 'synthetic-resident-session',
    plan_digest: active ? SWAPPED.payload_digest : UNDO.payload_digest,
    epoch: String(generation + 3),
    capability_active: active,
  };
}

/**
 * Candidates without the simulation's own outcome field: what the runtime would serve. A candidate with no role is served
 * with key and digest only, as a runtime that configures no roles serves it; nothing is filled in for it.
 */
export function servedCandidates(candidates: readonly ScenarioCandidate[]): MissionCandidate[] {
  return candidates.map(({ key, payload_digest, role, label, expect }) =>
    role === undefined ? { key, payload_digest } : { key, payload_digest, role, label: label ?? key, expect: expect ?? null },
  );
}
