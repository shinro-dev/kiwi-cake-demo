// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RUNTIME'S STEP LIST, IN WORDS (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 5.7.2).
//
// The outcome card lists exactly the runtime's steps, in its order, and nothing else: the hold's `next_actions` while the
// mission is held for the operator, the mission's `next_actions` otherwise. Each token has one sentence; a step done by
// hand carries the tag "by hand"; a step with a control on this screen names it. A token with no words here is printed
// as itself. Holding is read from the record and never asked: no sentence here is a question.

import type { MissionRecord } from './missionRecord';

export interface NextStep {
  token: string;
  words: string;
  byHand: boolean;
  /** The control on the outcome card that carries this step, or null when it has none there. */
  control: 'return_arm' | 'retry' | null;
}

const STEP_WORDS: Record<string, Omit<NextStep, 'token'>> = {
  open_gripper_by_hand: { words: 'Open the gripper by hand and take the block out.', byHand: true, control: null },
  return_base: { words: 'Move the base back to its start by hand.', byHand: true, control: null },
  return_arm: { words: 'Return arm: the arm goes to the saved low scan pose.', byHand: false, control: 'return_arm' },
  start: { words: 'Start a new mission with Start, at the top.', byHand: false, control: null },
  retry: { words: 'Try again: the runtime parks the arm, then starts a fresh attempt.', byHand: false, control: 'retry' },
  move_block_then_retry: { words: 'Move the block by hand, then Try again.', byHand: false, control: 'retry' },
  stop: { words: 'Stop, at the top, ends the mission.', byHand: false, control: null },
};

export function nextStep(token: string): NextStep {
  const known = STEP_WORDS[token];
  return known === undefined ? { token, words: token, byHand: false, control: null } : { token, ...known };
}

/** The runtime's current step list: the hold's while held, else the mission's; empty when the record carries none. */
export function missionSteps(mission: MissionRecord | null): string[] {
  if (mission === null) return [];
  const hold = mission.hold ?? null;
  if (hold !== null && mission.state === 'WAITING_FOR_CAPABILITY') return [...hold.next_actions];
  return Array.isArray(mission.next_actions) ? [...mission.next_actions] : [];
}
