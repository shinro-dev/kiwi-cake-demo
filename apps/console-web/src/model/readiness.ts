// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE READINESS VERDICT (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 4.2; audit C7).
//
// It is computed from the same guards that decide Start, so the two cannot disagree. The first rule that holds wins:
//
//   1. a mission carried or a run active: Running, Waiting for Cake or Held for you; Start is off;
//   2. Start off for a reason of its own (`startOff`): Not ready, that reason as the sentence;
//   3. an ended mission whose step list does not begin with `start`: Steps first, the first step's words; Start is on and
//      never primary;
//   4. otherwise Ready, and Start is the primary control.
//
// Acknowledging the outcome card changes the card only; this keeps reading the record. The mission it is handed is the one
// on the main screen: a mission that is History's (spec section 12) is handed as none, so its step list never decides rule 3. The items the runtime does not
// gate Start on (the wrist preview, the arm acquisition) are listed apart and never change the verdict.

import type { MissionRecord } from './missionRecord';
import type { ReadyItem } from './readyChecklist';
import { missionSteps, nextStep } from './nextSteps';
import { readinessVerdict } from './verdict';
import type { ReadinessId, Verdict } from './verdict';

export interface ReadinessInput {
  mission: MissionRecord | null;
  runActive: boolean;
  /** Start's own off-reason, as the screen composes it; null when Start is on. */
  startOff: string | null;
  /** The ready checklist's items, for the detail: split into what Start's guards check and what they do not. */
  checklist: readonly ReadyItem[];
  /** "The runtime is not answering." while the snapshot read fails (rule 4.4; no time, deviation 62); null or absent otherwise. */
  disconnected?: string | null;
}

export interface Readiness {
  verdict: Verdict;
  /** One sentence: why, in the words of the rule that decided. Null when Ready. */
  sentence: string | null;
  /** Whether Start is the one primary control of the screen. */
  startPrimary: boolean;
  checked: ReadyItem[];
  /** Items the runtime does not gate Start on: listed under "Not checked by the runtime", never deciding anything. */
  unchecked: ReadyItem[];
}

const NOT_CHECKED_BY_THE_RUNTIME: ReadonlySet<ReadyItem['id']> = new Set(['wrist', 'arm']);

function sentence(text: string): string {
  const trimmed = text.trim();
  const first = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(first) ? first : `${first}.`;
}

function carried(mission: MissionRecord | null): ReadinessId | null {
  if (mission === null) return null;
  if (mission.state === 'RUNNING') return 'running';
  if (mission.state !== 'WAITING_FOR_CAPABILITY') return null;
  return mission.waiting_reason === 'capability_unavailable' ? 'waiting_cake' : 'held_for_you';
}

export function readiness(input: ReadinessInput): Readiness {
  const checked = input.checklist.filter((item) => !NOT_CHECKED_BY_THE_RUNTIME.has(item.id));
  const unchecked = input.checklist.filter((item) => NOT_CHECKED_BY_THE_RUNTIME.has(item.id));
  const result = (id: ReadinessId, why: string | null): Readiness => ({ verdict: readinessVerdict(id), sentence: why, startPrimary: id === 'ready', checked, unchecked });
  // DISCONNECTED FIRST (rule 4.4): nothing this display holds is current, whatever the mission it last read was doing.
  if (input.disconnected !== undefined && input.disconnected !== null) return result('not_ready', input.disconnected);
  const mission = carried(input.mission);
  if (mission !== null) return result(mission, input.startOff === null ? null : sentence(input.startOff));
  if (input.runActive) return result('running', input.startOff === null ? null : sentence(input.startOff));
  if (input.startOff !== null) return result('not_ready', sentence(input.startOff));
  const steps = missionSteps(input.mission);
  const first = steps[0];
  if (input.mission !== null && first !== undefined && first !== 'start') return result('steps_first', nextStep(first).words);
  return result('ready', null);
}
