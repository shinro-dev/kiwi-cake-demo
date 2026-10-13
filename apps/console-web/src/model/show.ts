// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
/**
 * THE SHOW SCREEN'S MODEL (operator ruling 2026-10-11): the five stages and the words of the two missions of the
 * recorded demo, read from three records and nothing else: the runtime's mission record and folded runs (mission 1 up to
 * the pick), the robot runner's carry events (the rest of mission 1) and the runner's episode events (mission 2).
 */
import type { DirectorStep } from './director';
import type { MissionRecord } from './missionRecord';
import { PHASE_STATUS_WORDS, PRESENTATION_STEPS, PRESENTATION_STEP_LABEL } from './phase';
import type { PhaseStatus, PresentationStep } from './phase';
import type { StageRow } from './stageTimeline';
import { missionHold, vlaView } from './vla';
import type { VlaEvent, VlaStatus } from './vla';

export type ShowPhase = 'mission1' | 'mission2';
export type ShowTone = 'neutral' | 'busy' | 'good' | 'bad';

/** Mission 2 is on the screen from the moment the VLA module is enabled (loading) until it is dropped. */
export function showPhase(vla: VlaStatus | null): ShowPhase {
  return vla !== null && (vla.state === 'loading' || vla.state === 'ready' || vla.state === 'running') ? 'mission2' : 'mission1';
}

function blankRow(step: PresentationStep): StageRow {
  return { step, label: PRESENTATION_STEP_LABEL[step], status: 'not_reached', words: PHASE_STATUS_WORDS.not_reached, numbers: [], frame_id: null, failed_frame_id: null,
    rule: null, decided_by: null, retries: [], earlier: [], entered_at: null, failure_code: null };
}

function set(rows: StageRow[], step: PresentationStep, status: PhaseStatus, words: string, failureCode: string | null = null): void {
  const index = PRESENTATION_STEPS.indexOf(step);
  const row = rows[index];
  if (row === undefined) return;
  rows[index] = { ...row, status, words, failure_code: failureCode, numbers: failureCode === null ? [] : row.numbers };
}

function activeStep(rows: StageRow[]): PresentationStep {
  const active = rows.find((row) => row.status === 'active') ?? [...rows].reverse().find((row) => row.status === 'verified' || row.status === 'entered');
  return active?.step ?? 'find';
}

/** The runner's carry events belong to mission 1's record when its carry names this mission or is under way. */
function carryEvents(mission: MissionRecord | null, vla: VlaStatus | null): VlaEvent[] {
  if (mission === null || vla === null) return [];
  const carrying = vla.state === 'moving' && [...(vla.events ?? [])].reverse().find((event) => event.phase === 'moving_to_pose')?.pose === 'pad';
  const carried = vla.carry !== null && vla.carry !== undefined && vla.carry.mission_id === mission.mission_id;
  if (!carrying && !carried) return [];
  return (vla.events ?? []).filter((event) => event.phase.startsWith('carry_'));
}

/**
 * MISSION 1's rows: the controller's own rows up to the pick; the designed stop at HOLD_OBSERVE with the block held is
 * the hand-over to the carry, not a failure; the carry, the release and the return come from the runner's carry events.
 */
export function mission1Stages(controller: readonly StageRow[] | null, mission: MissionRecord | null, vla: VlaStatus | null): StageRow[] {
  const rows = controller !== null && controller.length === PRESENTATION_STEPS.length ? controller.map((row) => ({ ...row })) : PRESENTATION_STEPS.map(blankRow);
  if (mission === null) return rows;
  const hold = missionHold(mission);
  const newest = mission.targets.flatMap((target) => target.attempts ?? []).at(-1) ?? null;
  const handover = hold.holding && newest !== null && newest.phase === 'HOLD_OBSERVE';
  if (handover) {
    set(rows, 'pick', 'verified', 'Picked');
    set(rows, 'carry', 'entered', 'Picked; the carry takes over');
  }
  const events = carryEvents(mission, vla);
  for (const event of events) {
    const extra = event as VlaEvent & { direction?: string; pulse?: number; measured_m?: number; jaws?: number };
    if (event.phase === 'carry_start') set(rows, 'carry', 'active', 'Rolling forward with the block');
    else if (event.phase === 'carry_base' && extra.direction === 'forward') set(rows, 'carry', 'active', `Forward pulse ${String(extra.pulse ?? '')}: ${(extra.measured_m ?? 0).toFixed(2)} m`);
    else if (event.phase === 'carry_released') {
      set(rows, 'carry', 'verified', 'Carried to the pad');
      set(rows, 'place', 'verified', 'Released at the pad');
      set(rows, 'return', 'active', 'Rolling back');
    } else if (event.phase === 'carry_base' && extra.direction === 'backward') set(rows, 'return', 'active', `Back: pulse ${String(extra.pulse ?? '')}, ${(extra.measured_m ?? 0).toFixed(2)} m`);
    else if (event.phase === 'carry_done') set(rows, 'return', 'verified', 'Returned to the start');
    else if (event.phase === 'carry_refused') set(rows, activeStep(rows), 'refused', event.message || `Refused here (${event.code ?? 'no code'})`, event.code ?? null);
    else if (event.phase === 'carry_stopped') set(rows, activeStep(rows), 'stopped_on_request', PHASE_STATUS_WORDS.stopped_on_request);
  }
  const carry = vla?.carry ?? null;
  if (events.length === 0 && carry !== null && carry.mission_id === mission.mission_id && carry.result === 'done') {
    set(rows, 'carry', 'verified', 'Carried to the pad');
    set(rows, 'place', 'verified', 'Released at the pad');
    set(rows, 'return', 'verified', 'Returned to the start');
  }
  return rows;
}

/** MISSION 2's rows, from the runner's episode events alone: no runtime run and no mission record exist for it. */
export function mission2Stages(vla: VlaStatus | null): StageRow[] {
  const rows = PRESENTATION_STEPS.map(blankRow);
  if (vla === null) return rows;
  let latched = false;
  for (const event of vla.events ?? []) {
    switch (event.phase) {
      case 'moving_to_start':
      case 'at_start':
        set(rows, 'find', 'active', 'Looking at the scene');
        break;
      case 'thinking':
      case 'moving':
        if (!latched) set(rows, 'find', 'active', `${event.phase === 'thinking' ? 'Thinking' : 'Moving'}, chunk ${String(event.chunk ?? '')}`);
        else if (rows[1]?.status === 'active') set(rows, 'pick', 'active', `Trying again, chunk ${String(event.chunk ?? '')}`);
        else if (rows[2]?.status === 'active') set(rows, 'carry', 'active', `Carrying, chunk ${String(event.chunk ?? '')}`);
        break;
      case 'latched':
        latched = true;
        set(rows, 'find', 'verified', 'Found the block');
        set(rows, 'pick', 'active', 'Jaws closed; checking the grip');
        break;
      case 'grip_empty':
        set(rows, 'pick', 'active', 'Closed on nothing; trying again');
        break;
      case 'object_secured':
        if (event.value === true) {
          set(rows, 'pick', 'verified', `Grip confirmed${event.score === undefined ? '' : ` (${event.score.toFixed(2)})`}`);
          set(rows, 'carry', 'active', 'Carrying to the pad');
        } else set(rows, 'pick', 'failed', 'Grip not confirmed');
        break;
      case 'released':
        set(rows, 'carry', 'verified', 'Carried to the pad');
        set(rows, 'place', 'verified', 'Released at the pad');
        set(rows, 'return', 'active', 'Returning');
        break;
      case 'returning':
        set(rows, 'return', 'active', 'Returning to the start pose');
        break;
      case 'done':
        set(rows, 'return', 'verified', 'Returned');
        break;
      case 'refused':
        set(rows, activeStep(rows), 'refused', event.message || `Refused here (${event.code ?? 'no code'})`, event.code ?? null);
        break;
      case 'stopped':
        set(rows, activeStep(rows), 'stopped_on_request', PHASE_STATUS_WORDS.stopped_on_request);
        break;
      default:
        break;
    }
  }
  return rows;
}

export interface ShowWords {
  title: string;
  status: string;
  tone: ShowTone;
}

const EPISODE_WORDS: Record<string, string> = {
  moving_to_start: 'Moving to the start pose.', at_start: 'At the start pose.', thinking: 'Thinking.', moving: 'Moving.', latched: 'Jaws closed.',
  grip_empty: 'Closed on nothing; trying again.', object_secured: 'Checking the grip.', released: 'Released the block.', returning: 'Returning.', done: 'Done.',
};

/** The mission card's words, from the director's step while mission 1 is directed and from the runner for mission 2. */
export function showWords(input: { phase: ShowPhase; mission: MissionRecord | null; vla: VlaStatus | null; step: DirectorStep | null; runtimeIdle: boolean; vlaRefusal: string | null }): ShowWords {
  const { mission, vla, step } = input;
  if (input.phase === 'mission2') {
    const title = 'Mission 2: VLA pick and place (SmolVLA, on the robot)';
    if (vla === null) return { title, status: 'The VLA runner is not answering.', tone: 'bad' };
    if (vla.state === 'loading') return { title, status: 'Loading the VLA module on the robot.', tone: 'busy' };
    if (vla.state === 'running') {
      const last = (vla.events ?? []).at(-1);
      const words = last === undefined ? 'Starting.' : (EPISODE_WORDS[last.phase] ?? `${last.phase}.`);
      return { title, status: `The VLA policy is driving the arm. ${words}${last?.chunk ? ` Chunk ${String(last.chunk)}.` : ''}`, tone: 'busy' };
    }
    const view = vlaView(vla, true, input.runtimeIdle, input.vlaRefusal);
    if (vla.outcome === null || vla.outcome === undefined) {
      return { title, status: input.vlaRefusal === null ? 'VLA module enabled. Put the block back at its start, then press Start.' : view.detail, tone: input.vlaRefusal === null ? 'good' : 'bad' };
    }
    if (vla.outcome.result === 'done') return { title: 'Mission 2 complete', status: `${view.headline}. ${view.detail}`, tone: 'good' };
    return { title: 'Mission 2 stopped', status: `${view.headline}. ${view.detail}`.trim(), tone: 'bad' };
  }
  const title = 'Mission 1: scripted pick and place';
  if (step === null) {
    if (mission === null) return { title, status: 'Press Start.', tone: 'neutral' };
    if (mission.state === 'RUNNING') return { title, status: 'The mission is running.', tone: 'busy' };
    if (mission.state === 'WAITING_FOR_CAPABILITY') return { title, status: 'The mission is held; it was not started from this display, so it is not directed here.', tone: 'neutral' };
    if (mission.state === 'COMPLETE') return { title: 'Mission 1 complete', status: 'The block is on the pad.', tone: 'good' };
    return { title, status: `The last mission ${mission.state === 'STOPPED' ? 'was stopped' : 'failed'}. Press Start for a new one.`, tone: 'neutral' };
  }
  switch (step.kind) {
    case 'activate':
      return { title, status: 'Activating the mobile approach capability on Cake.', tone: 'busy' };
    case 'stop':
      return { title, status: 'Picked. Handing over to the carry.', tone: 'busy' };
    case 'carry':
      return { title, status: 'Carrying the block to the pad.', tone: 'busy' };
    case 'wait':
      return { title, status: `${step.why.charAt(0).toUpperCase()}${step.why.slice(1)}.`, tone: 'busy' };
    case 'finished':
      return { title: 'Mission 1 complete', status: 'The block is on the pad and the robot is back at its start. Next: Enable VLA.', tone: 'good' };
    case 'failed':
      return { title: 'Mission 1 stopped', status: `${step.why.charAt(0).toUpperCase()}${step.why.slice(1)}.`, tone: 'bad' };
  }
}

export interface EnableVlaModel {
  label: string;
  /** Why the button is off, or null when it may be pressed. */
  off: string | null;
  busy: boolean;
  /** True once the module is enabled: the button stays as the record of it and does nothing. */
  done: boolean;
}

/** The Enable VLA button: one press loads the module on the robot and brings the arm to its start pose. */
export function enableVla(input: { vla: VlaStatus | null; missionRunning: boolean; runActive: boolean; vlaBusy: boolean }): EnableVlaModel {
  const vla = input.vla;
  if (vla === null || vla.state === 'absent') return { label: 'Enable VLA', off: 'this console has no VLA door', busy: false, done: false };
  switch (vla.state) {
    case 'standby':
      if (input.missionRunning || input.runActive) return { label: 'Enable VLA', off: 'the mission is running; it is enabled after mission 1', busy: false, done: false };
      if (input.vlaBusy) return { label: 'Enable VLA', off: 'a VLA command is in flight', busy: true, done: false };
      return { label: 'Enable VLA', off: null, busy: false, done: false };
    case 'loading':
      return { label: 'Enabling VLA', off: 'the module is loading on the robot', busy: true, done: false };
    case 'ready':
      return { label: 'VLA enabled', off: 'the module is enabled', busy: false, done: true };
    case 'running':
      return { label: 'VLA enabled', off: 'the VLA policy is driving the arm', busy: false, done: true };
    case 'moving':
      return { label: 'Enable VLA', off: 'the arm is moving; wait for it', busy: true, done: false };
    case 'error':
      return { label: 'Enable VLA', off: vla.error ?? 'the VLA module could not be loaded', busy: false, done: false };
    default:
      return { label: 'Enable VLA', off: 'the VLA runner on the robot is not answering', busy: false, done: false };
  }
}

export type StartRoute = 'mission' | 'vla';

/** Where Start goes: to the runner once the VLA module is enabled, to the runtime's mission otherwise. */
export function startRoute(vla: VlaStatus | null): StartRoute {
  return vla !== null && (vla.state === 'ready' || vla.state === 'running') ? 'vla' : 'mission';
}

/** Why the VLA Start is off, or null: the runner must be ready and the runtime idle. */
export function vlaStartOff(vla: VlaStatus | null, runtimeIdle: boolean, vlaBusy: boolean): string | null {
  if (vla === null) return 'the VLA runner is not answering';
  if (vla.state === 'running') return 'the VLA policy is driving the arm; Stop ends it';
  if (vla.state === 'loading') return 'the VLA module is loading on the robot';
  if (vla.state === 'moving') return 'the arm is moving to its start pose';
  if (vla.state !== 'ready') return 'the VLA module is not ready';
  if (vlaBusy) return 'a VLA command is in flight';
  if (!runtimeIdle) return 'the mission runtime still holds the arm';
  return null;
}
