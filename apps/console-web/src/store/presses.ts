// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// PRESS RECEIPTS ARE A LIST (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 6.5; audit T2).
//
// One entry per press on this display: Start, Stop, Try again, Return arm, Scan scene, Observe, an activation
// candidate, the slot check, the signature check, the robot confirmation and Acknowledge. Entries are appended and never
// overwritten; a pending entry receives its own answer, once. The times are this display's clock and the screen says so.
// `record` is the timeline key of the runtime record the receipt names (a mission, an operation, a run), so the timeline
// shows the press on that record's line instead of twice.

export type PressControl =
  | 'start'
  | 'stop'
  | 'retry'
  | 'return_arm'
  | 'scan_scene'
  | 'observe'
  | 'candidate'
  | 'slot_check'
  | 'signature_check'
  | 'robot_confirm'
  | 'acknowledge';

export type PressAnswer = 'pending' | 'taken' | 'refused' | 'unknown';

export interface PressEntry {
  id: string;
  control: PressControl;
  /** When this display registered the press, in UTC, on this display's clock. */
  pressed_at: string;
  answer: PressAnswer;
  /** The receipt's words, or null while pending. */
  text: string | null;
  answered_at: string | null;
  /** The mission and the run this display was showing when the press was made. */
  mission_id: string | null;
  run_id: string | null;
  /** The timeline key of the record the receipt names, or null. */
  record: string | null;
}

/** The words each control is called by in the press list. */
export const PRESS_WORDS: Record<PressControl, string> = {
  start: 'Start',
  stop: 'Stop',
  retry: 'Try again',
  return_arm: 'Return arm',
  scan_scene: 'Scan scene',
  observe: 'Observe',
  candidate: 'an activation candidate',
  slot_check: 'the slot check',
  signature_check: 'the signature check',
  robot_confirm: 'the robot confirmation',
  acknowledge: 'Acknowledge',
};

export function appendPress(list: readonly PressEntry[], entry: PressEntry): PressEntry[] {
  return [...list, entry];
}

/** The pending entry `id` with its answer; an entry already answered keeps its first answer. */
export function answerPress(
  list: readonly PressEntry[],
  id: string,
  answer: { answer: Exclude<PressAnswer, 'pending'>; text: string; answered_at: string; record: string | null },
): PressEntry[] {
  return list.map((item) => (item.id === id && item.answer === 'pending' ? { ...item, ...answer } : item));
}
