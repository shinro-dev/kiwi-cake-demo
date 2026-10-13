// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
/**
 * The VLA beat's view model. The words name what happens and nothing more: the policy is loaded by a process on the
 * robot, not by Cake, so no text here says Cake swapped anything. A refused episode is said as what it was.
 */
import type { MissionRecord } from './missionRecord';
import { missionExecution } from './missionView';

export type VlaEvent = { t: number | null; phase: string; pose?: string; chunk?: number; inference_ms?: number; code?: string; message?: string; value?: boolean; score?: number };
/** Mission 1's scripted carry, as the runner reports it, naming the mission it was run for (vla/carry.py). */
export type VlaCarry = { result: 'done' | 'refused' | 'stopped'; code?: string | null; message?: string; mission_id?: string | null; jaws_after_release?: number | null; seconds?: number };
export type VlaOutcome = { result: 'done' | 'refused' | 'stopped'; code?: string | null; message?: string; object_secured?: boolean | null; released?: boolean; release_by?: 'policy' | 'rule' | null; chunks?: number; seconds?: number };
export type VlaStatus = {
  state: 'standby' | 'loading' | 'ready' | 'running' | 'moving' | 'error' | 'unreachable' | 'absent';
  model_sha256?: string; warm_inference_ms?: number | null; events?: VlaEvent[]; outcome?: VlaOutcome | null;
  preview_jpeg_b64?: string | null; error?: string | null; carry?: VlaCarry | null;
};
export type VlaAction = 'swap' | 'run' | 'stop' | 'unswap' | 'to_mission_pose' | 'carry';
export type VlaView = { visible: boolean; headline: string; detail: string; actions: VlaAction[]; tone: 'neutral' | 'busy' | 'good' | 'bad'; preview: string | null };

const PHASE_TEXT: Record<string, string> = {
  moving_to_start: 'moving the arm to the start pose', at_start: 'at the start pose', thinking: 'thinking',
  moving: 'moving', latched: 'jaws closed', object_secured: 'checking the grip', released: 'released the block',
  returning: 'returning to the start pose', done: 'done', reopened: 'reopened the jaws to try again',
};

const REFUSAL_TEXT: Record<string, string> = {
  retention_not_demonstrated: 'The block was not in the jaws: the policy closed on nothing, so it was stopped before carrying it',
  vla_time_bound: 'The policy ran out of time',
  vla_inference_timeout: 'The robot took too long to think',
  vla_start_pose_not_reached: 'The arm did not reach the start pose',
  vla_grip_not_verified: 'The grip was never confirmed, so the block was not let go',
  vla_runtime_busy: 'The mission runtime took the arm, so the VLA run stopped',
  vla_still_thinking: 'The robot is still finishing the last inference; try again in a moment',
  vla_runner_failed: 'The VLA run could not start',
};

/**
 * WHERE THE SCRIPTED MISSION LEFT THE ARM, in the runtime's own words: the mission holds the block when a physical mission's
 * last attempt at a target recorded `stop_state.holding` (mission 1 stops at HOLD_OBSERVE that way), and it has ended once
 * the operator stopped it. The carry is offered only then; the runner checks the jaws again before it moves anything.
 */
export type MissionHolding = { missionId: string | null; holding: boolean; ended: boolean };
const NO_HOLDING: MissionHolding = { missionId: null, holding: false, ended: false };

export function missionHold(mission: MissionRecord | null): MissionHolding {
  if (mission === null) return NO_HOLDING;
  const physical = missionExecution(mission) === 'physical';
  const holding = physical && mission.targets.some((target) => (target.attempts ?? []).at(-1)?.stop_state?.holding === true);
  return { missionId: mission.mission_id, holding, ended: mission.state === 'STOPPED' || mission.state === 'FAILED' };
}

/** The view, with the robot runner's reason for not taking the last press said in place of the detail when there is one. */
export function vlaView(status: VlaStatus | null, missionDone: boolean, runtimeIdle: boolean, refusal: string | null = null, hold: MissionHolding = NO_HOLDING): VlaView {
  const view = baseView(status, missionDone, runtimeIdle, hold);
  if (refusal === null || status === null || !['standby', 'ready', 'unreachable'].includes(status.state)) return view;
  return { ...view, detail: `Not taken: ${refusal}`, tone: 'bad' };
}

function baseView(status: VlaStatus | null, missionDone: boolean, runtimeIdle: boolean, hold: MissionHolding): VlaView {
  const sha8 = (status?.model_sha256 ?? '').slice(0, 8);
  const model = sha8 ? `SmolVLA, model ${sha8}, running on the robot` : 'SmolVLA, running on the robot';
  const preview = status?.preview_jpeg_b64 ? `data:image/jpeg;base64,${status.preview_jpeg_b64}` : null;
  if (!status) return { visible: false, headline: '', detail: '', actions: [], tone: 'neutral', preview: null };
  const active = status.state === 'loading' || status.state === 'running' || status.state === 'ready' || status.state === 'moving';
  const visible = missionDone || active || status.state === 'error';
  switch (status.state) {
    case 'absent':
      return { visible: false, headline: '', detail: '', actions: [], tone: 'neutral', preview: null };
    case 'unreachable':
      return { visible: missionDone, headline: 'The VLA runner on the robot is not answering', detail: 'Start it with the bring-up script, then reload.', actions: [], tone: 'bad', preview: null };
    case 'error':
      return { visible, headline: 'The VLA policy could not be loaded', detail: status.error ?? '', actions: [], tone: 'bad', preview: null };
    case 'standby': {
      // MISSION 1 IS FINISHED by the scripted carry of THIS mission (the runner names the mission it carried for), or by a
      // mission that completed by itself; then the swap. Before that, the carry when the stopped mission holds the block,
      // and before the mission, the mission's own start pose (the VLA starts from another).
      const carried = status.carry && hold.missionId !== null && status.carry.mission_id === hold.missionId ? status.carry : null;
      if (carried?.result === 'done') {
        return { visible: true, headline: 'Mission 1 finished', detail: `The mission picked the block and the scripted carry set it down at the pad. Next: swap to the VLA policy. Arm only, no wheels. ${model}.`, actions: ['swap'], tone: 'good', preview: null };
      }
      if (missionDone) {
        return { visible, headline: 'Swap to the VLA policy', detail: `Arm only, no wheels. ${model}. The arm moves to the VLA start pose.`, actions: ['swap'], tone: 'neutral', preview: null };
      }
      if (carried) {
        return { visible: true, headline: carried.result === 'stopped' ? 'The carry was stopped' : 'The carry did not finish', detail: carried.message || (carried.code ?? ''), actions: ['to_mission_pose'], tone: 'bad', preview: null };
      }
      if (hold.holding && hold.ended) {
        return { visible: true, headline: 'The mission holds the block', detail: 'Carry it to the pad: the robot rolls 10 cm forward, sets the block down at the pad and rolls back. Stop stops the wheels and holds the arm.', actions: ['carry'], tone: 'neutral', preview: null };
      }
      if (hold.holding) {
        return { visible: true, headline: 'The mission holds the block', detail: 'Stop the mission, then carry the block to the pad from here.', actions: [], tone: 'neutral', preview: null };
      }
      return { visible: true, headline: 'Before the mission: bring the arm to its start pose', detail: 'The scripted mission starts from its own pose; the VLA policy from another.', actions: ['to_mission_pose'], tone: 'neutral', preview: null };
    }
    case 'moving': {
      const pose = [...(status.events ?? [])].reverse().find((event) => event.phase === 'moving_to_pose')?.pose;
      if (pose === 'pad') return { visible: true, headline: 'Carrying the block to the pad', detail: 'Stop stops the wheels and holds the arm.', actions: ['stop'], tone: 'busy', preview: null };
      return { visible: true, headline: `Moving the arm to the ${pose === 'vla' ? 'VLA' : 'mission'} start pose`, detail: 'Stop holds it where it is.', actions: ['stop'], tone: 'busy', preview: null };
    }
    case 'loading':
      return { visible, headline: 'Loading the VLA policy on the robot', detail: model, actions: [], tone: 'busy', preview: null };
    case 'running': {
      const last = (status.events ?? []).at(-1);
      const words = last ? PHASE_TEXT[last.phase] ?? last.phase : 'starting';
      const detail = last?.chunk ? `${words}, chunk ${String(last.chunk)}` : words;
      return { visible: true, headline: 'The VLA policy is driving the arm', detail, actions: ['stop'], tone: 'busy', preview };
    }
    case 'ready': {
      const actions: VlaAction[] = runtimeIdle ? ['run', 'unswap'] : ['unswap'];
      const out = status.outcome;
      if (!out) return { visible, headline: 'VLA policy ready', detail: `Put the block back in front of the robot, then Run. ${model}.`, actions, tone: 'neutral', preview };
      if (out.result === 'done') {
        const grip = out.object_secured === true ? 'grip confirmed' : 'grip not measured';
        const timing = `${String(out.chunks ?? 0)} chunks in ${String(Math.round(out.seconds ?? 0))} s`;
        // The recorded takes contain no release, so the robot opens the jaws by rule once the held block rests at the pad.
        if (out.release_by === 'rule') {
          return { visible, headline: 'The VLA policy carried the block to the pad', detail: `${grip}; jaws opened by rule at the pad; ${timing}.`, actions, tone: 'good', preview };
        }
        return { visible, headline: 'The VLA policy picked and released the block', detail: `${grip}; ${timing}.`, actions, tone: 'good', preview };
      }
      if (out.result === 'stopped') {
        const deadman = (status.events ?? []).some((event) => event.phase === 'deadman');
        return { visible, headline: deadman ? 'Stopped: the console stopped answering the robot' : 'Stopped by the operator', detail: 'The arm holds where it stopped.', actions, tone: deadman ? 'bad' : 'neutral', preview };
      }
      return { visible, headline: REFUSAL_TEXT[out.code ?? ''] ?? 'The VLA run was refused', detail: out.message ?? '', actions, tone: 'bad', preview };
    }
  }
}

/**
 * The mission controls (Start, Try again, Activate) stay off while the VLA policy loads or drives the arm, and while a
 * runner last seen driving does not answer, since it may still be driving: two drivers never command one host.
 */
export function vlaStartBlock(status: VlaStatus | null, maybeDriving: boolean): string | null {
  if (status?.state === 'running') return 'the VLA policy is driving the arm; stop it first';
  if (status?.state === 'loading') return 'the VLA policy is loading on the robot; wait for it';
  if (status?.state === 'moving') {
    const pose = [...(status.events ?? [])].reverse().find((event) => event.phase === 'moving_to_pose')?.pose;
    return pose === 'pad' ? 'the robot is carrying the block to the pad; wait for it' : 'the arm is moving to a start pose; wait for it';
  }
  if (status?.state === 'unreachable' && maybeDriving) return 'the VLA runner stopped answering while it was driving the arm; it may still be driving';
  return null;
}
