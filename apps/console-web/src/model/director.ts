// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
/**
 * THE DEMO DIRECTOR: what the console sends on its own after the operator's one Start (operator ruling 2026-10-11: the
 * recorded demo is four clicks, Confirm, Start, Enable VLA, Start, and nothing in between).
 *
 * Mission 1 is the scripted mission plus the ruled carry. The runtime carries it this far: SEARCH measures the block,
 * refuses for the mobile approach capability and waits for Cake; after the activation it picks the block and stops at
 * HOLD_OBSERVE holding it (the pad is too far to measure with the block in view), held for the operator's decision. The
 * director presses what the operator pressed by hand: Activate (the one prepared Plan), Stop (the hold offers nothing
 * else), then the runner's carry, which rolls forward, sets the block down at the pad and rolls back.
 *
 * It is a pure function of the records, keyed to the ONE mission this tab armed with its Start: it never acts on a
 * mission it did not start, so a reload starts nothing and a page opened on a waiting mission activates nothing (the
 * reload-safety every other read keeps). Every step is derived from the records on every poll, never replayed from
 * memory: a step already on the record (an activation submitted, a stop requested, a carry recorded) is never sent twice.
 */
import type { MissionCandidate, MissionRecord } from './missionRecord';
import type { VlaStatus } from './vla';
import { missionHold } from './vla';

export type DirectorStep =
  | { kind: 'activate'; candidate_key: string }
  | { kind: 'stop' }
  | { kind: 'carry' }
  /** Nothing to send now; `why` is for the screen. */
  | { kind: 'wait'; why: string }
  /** Mission 1 is finished: the block is at the pad and the arm is back. */
  | { kind: 'finished' }
  /** The director stops here; the operator decides. */
  | { kind: 'failed'; why: string };

export interface DirectorInput {
  /** The mission this tab started, or null: nothing is directed. */
  armedMissionId: string | null;
  mission: MissionRecord | null;
  candidates: MissionCandidate[] | null;
  vla: VlaStatus | null;
  busy: { activating: boolean; stopping: boolean; vlaBusy: boolean; commandInFlight: boolean };
  /** A controller run holds the runtime's run slot. */
  runActive: boolean;
}

/** The candidate the director activates: the one served with the role `activate`, else the only one. */
export function chosenCandidate(candidates: readonly MissionCandidate[]): MissionCandidate | null {
  const byRole = candidates.find((item) => item.role === 'activate');
  if (byRole !== undefined) return byRole;
  return candidates.length === 1 ? (candidates[0] ?? null) : null;
}

export function directorStep(input: DirectorInput): DirectorStep | null {
  const { mission, vla } = input;
  if (input.armedMissionId === null || mission === null || mission.mission_id !== input.armedMissionId) return null;
  const carry = vla?.carry ?? null;
  if (carry !== null && carry.mission_id === mission.mission_id) {
    if (carry.result === 'done') return { kind: 'finished' };
    return { kind: 'failed', why: carry.message || (carry.result === 'stopped' ? 'the carry was stopped' : `the carry did not finish (${carry.code ?? 'no code'})`) };
  }
  if (mission.state === 'COMPLETE') return { kind: 'finished' };
  if (mission.state === 'RUNNING') return { kind: 'wait', why: 'the mission is running' };
  const hold = missionHold(mission);
  if (mission.state === 'WAITING_FOR_CAPABILITY') {
    if (mission.stop_requested === true || input.busy.stopping) return { kind: 'wait', why: 'the mission is stopping' };
    if (mission.waiting_reason === 'capability_unavailable') {
      const newest = mission.activations.at(-1) ?? null;
      if (newest !== null && newest.activation_state === 'SUBMITTED') return { kind: 'wait', why: 'Cake is activating the capability' };
      if (newest !== null && newest.activation_state === 'OBSERVED') return { kind: 'wait', why: 'the capability is active; the mission resumes' };
      if (input.busy.activating) return { kind: 'wait', why: 'the activation is in flight' };
      if (input.candidates === null) return { kind: 'wait', why: 'reading the prepared Plans' };
      const candidate = chosenCandidate(input.candidates);
      if (candidate === null) return { kind: 'failed', why: `no single Plan to activate (${String(input.candidates.length)} prepared)` };
      return { kind: 'activate', candidate_key: candidate.key };
    }
    if (hold.holding) return { kind: 'stop' };
    return { kind: 'failed', why: `the mission is held without the block (${mission.hold?.reason_code ?? mission.waiting_reason ?? 'no reason recorded'})` };
  }
  // STOPPED or FAILED
  if (!hold.holding) return { kind: 'failed', why: `the mission ended without the block${mission.detail ? `: ${mission.detail}` : ''}` };
  if (vla === null || vla.state === 'unreachable' || vla.state === 'absent') return { kind: 'wait', why: 'the robot runner is not answering' };
  if (vla.state === 'moving') return { kind: 'wait', why: 'carrying the block to the pad' };
  if (vla.state !== 'standby') return { kind: 'failed', why: `the VLA module was enabled before the carry (runner ${vla.state})` };
  if (input.busy.vlaBusy || input.busy.commandInFlight || input.runActive) return { kind: 'wait', why: 'a command is still settling' };
  return { kind: 'carry' };
}
