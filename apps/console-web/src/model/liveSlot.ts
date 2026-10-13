// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE LIVE CAPABILITY SLOT, FROM THE NEWEST RECORD BY TIME (docs/autonomous-demo/console-redesign-2026-09-27.md, rules
// 3.3 and 5.7.5; browser r2 N1).
//
// Two records say what the capability slot carries: GET /api/robot's reading of it, and a mission activation's slot
// read-back. They are read at different times, and the runtime holds its resident reading for a poll interval, so for a
// few seconds after an accepted activation the robot's reading can be OLDER than the activation that changed the slot.
// Drawn as it was, that reading put "not active" under a card that says "Activated, slot 1 -> 2". The newer record wins:
// an activation that changed the slot AFTER the robot's reading was taken is what the slot carries, with its own time,
// until a robot reading taken after it arrives. A refusal never changes the slot and is never that record.
//
// NOTHING TICKS AND NOTHING IS THE PAGE'S (rule 6.8; redesign verdict item 7): the robot's reading is said without a
// time. Its only times are the newest poll's, which ticks, and the first reading this page happened to make, which a
// reload changes and which dates the page, not the slot. The record-backed time of the slot is the activation or check
// that set it, which the Cake footer states beside it ("Set by the activation of ... at ...").

import type { MissionActivationRecord, MissionRecord } from './missionRecord';
import type { RobotStatus } from './robotStatus';
import { parseUtcMs, utcStamp } from './utcTime';
import type { UtcStamp } from './utcTime';
import { REFUSALS, slotChange } from './verdict';

export interface LiveSlot {
  name: string | null;
  /** Null: the record that says it did not say whether the capability is active. */
  active: boolean | null;
  generation: string | null;
  capsule: string | null;
  /** The record these values come from: the robot's reading, said without a time, or an activation with its own. */
  from: { kind: 'robot' } | { kind: 'activation'; activation: MissionActivationRecord; at: UtcStamp | null };
}

export interface LiveSlotInput {
  robot: RobotStatus | null;
  /** When the robot answer on the screen reached this page: only to order it against an activation, never printed. */
  robotReadAtMs: number | null;
  mission: MissionRecord | null;
  day: string | null;
}

/** The slot as the newest record by time says it, or null when the robot has not been read (or names no slot). */
export function liveSlot(input: LiveSlotInput): LiveSlot | null {
  const robot = input.robot;
  if (robot === null || !robot.configured) return null;
  const capability = robot.capability;
  // THE ROBOT READING'S TIME: the resident's own reading time when it carries one, else when the answer reached this page
  // (the reading cannot be newer than that).
  const readingMs = parseUtcMs(capability.observed?.wall_utc ?? null) ?? input.robotReadAtMs;
  const newer =
    readingMs === null
      ? undefined
      : [...(input.mission?.activations ?? [])].reverse().find((item) => {
          const at = parseUtcMs(item.t_wall_utc);
          const sameSlot = item.slot_after?.name === null || item.slot_after?.name === undefined || capability.name === null || item.slot_after.name === capability.name;
          return !REFUSALS.has(item.activation_state) && slotChange(item.slot_before, item.slot_after) === 'changed' && sameSlot && at !== null && at > readingMs;
        });
  if (newer !== undefined && newer.slot_after !== null && newer.slot_after !== undefined) {
    const after = newer.slot_after;
    return {
      name: after.name ?? capability.name,
      active: after.capability_active,
      generation: after.generation,
      capsule: after.capsule,
      from: { kind: 'activation', activation: newer, at: utcStamp(newer.t_wall_utc, input.day) },
    };
  }
  return {
    name: capability.name,
    active: capability.active,
    generation: capability.generation === null ? null : String(capability.generation),
    capsule: capability.capsule,
    from: { kind: 'robot' },
  };
}

/** Which record the slot's values come from, in words, with the record's own time where it has one. */
export function liveSlotWhen(slot: LiveSlot): string {
  if (slot.from.kind === 'activation') {
    const at = slot.from.at;
    return `as the activation of ${slot.from.activation.candidate_key ?? 'a candidate'} read it back${at === null ? '' : ` at ${at.text}`}`;
  }
  return "as the robot's reading says";
}

/** The slot's state word: active, not active, or not read back. */
export function liveSlotActiveWords(slot: LiveSlot): string {
  return slot.active === true ? 'active' : slot.active === false ? 'not active' : 'whether active was not read back';
}
