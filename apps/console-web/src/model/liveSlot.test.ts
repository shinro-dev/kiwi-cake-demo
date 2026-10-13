// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE LIVE SLOT FROM THE NEWEST RECORD BY TIME (browser r2 N1). For a few seconds after an accepted activation the
// robot's reading can be older than the activation that changed the slot; the footer and the Scene then said "not active"
// under a card that says "Activated, slot 1 -> 2". The newer record wins. An activation states its own time; the
// robot's reading states none, because its only times are the page's (redesign verdict item 7).

import { describe, expect, it } from 'vitest';
import type { MissionActivationRecord, MissionRecord, SlotReading } from './missionRecord';
import type { RobotStatus } from './robotStatus';
import { cakeStory } from './cakeStory';
import { liveSlot } from './liveSlot';

const DAY = '2026-09-27';

function robot(active: boolean, generation: number, observed: string | null): RobotStatus {
  return {
    configured: true,
    capability: { configured: true, name: 'mobile_approach', active, reason_code: active ? null : 'slot_not_active', capsule: active ? '2f34a84c1971aaaa' : '1e1e1e1e1e1eaaaa', generation, child_pid: null, stale: false, observed: observed === null ? null : { wall_utc: observed, age_ms: 300 } },
  } as unknown as RobotStatus;
}

function reading(generation: string, active: boolean): SlotReading {
  return { read: 'ok', slot_uuid: 'slot-1', name: 'mobile_approach', state: 'ACTIVE', capsule: active ? '2f34a84c1971aaaa' : '1e1e1e1e1e1eaaaa', generation, instance: '7', session_uuid: 'session-1', plan_digest: null, epoch: null, capability_active: active };
}

function activation(state: string, at: string, before: SlotReading, after: SlotReading): MissionActivationRecord {
  return { operation_uuid: `op-${at}`, target_id: 'cube', candidate_key: state === 'OBSERVED' ? 'mobile-demo-swapped' : 'mobile-demo-active-successor-abort-swap', payload_digest: null, activation_state: state, outcome: state, transaction_phase: null, transaction_uuid: null, detail: null, t_wall_utc: at, slot_before: before, slot_after: after } as MissionActivationRecord;
}

function mission(activations: MissionActivationRecord[]): MissionRecord {
  return { mission_id: 'mission-1', state: 'RUNNING', started_wall_utc: '2026-09-27T14:16:38.847Z', targets: [], activations } as unknown as MissionRecord;
}

const REFUSED = activation('REJECTED_BY_CAKE', '2026-09-27T14:20:21.000Z', reading('1', false), reading('1', false));
const SWAPPED = activation('OBSERVED', '2026-09-27T14:21:45.000Z', reading('1', false), reading('2', true));

describe('the live slot is the newest record by time', () => {
  it("reads an accepted activation's slot, with its time, while the robot's reading is older than it", () => {
    const older = robot(false, 1, '2026-09-27T14:21:40.000Z');
    const slot = liveSlot({ robot: older, robotReadAtMs: Date.parse('2026-09-27T14:21:46.000Z'), mission: mission([REFUSED, SWAPPED]), day: DAY });
    expect(slot?.active).toBe(true);
    expect(slot?.generation).toBe('2');
    expect(slot?.from.kind).toBe('activation');
    const story = cakeStory({ mission: mission([REFUSED, SWAPPED]), candidates: null, signature: null, runtimeSession: null, robot: older, capabilityCheck: null, robotReadAtMs: Date.parse('2026-09-27T14:21:46.000Z') });
    expect(story.slot?.words).toBe('Live slot: mobile_approach, active, generation 2, capsule 2f34a84c1971, as the activation of mobile-demo-swapped read it back at 14:21:45Z');
    expect(story.slot?.words).not.toContain('not active');
  });

  it("reads the robot's reading once one taken after the activation reached this page, with the activation's time as what set it", () => {
    const later = robot(true, 2, '2026-09-27T14:22:10.000Z');
    const story = cakeStory({ mission: mission([REFUSED, SWAPPED]), candidates: null, signature: null, runtimeSession: null, robot: later, capabilityCheck: null, robotReadAtMs: Date.parse('2026-09-27T14:22:11.000Z') });
    expect(story.slot?.words).toBe("Live slot: mobile_approach, active, generation 2, capsule 2f34a84c1971, as the robot's reading says");
    expect(story.slot?.set_by).toBe('Set by the activation of mobile-demo-swapped at 14:21:45Z.');
  });

  it('never takes a refusal newer than the reading as what the slot carries', () => {
    const older = robot(false, 1, '2026-09-27T14:20:00.000Z');
    const slot = liveSlot({ robot: older, robotReadAtMs: Date.parse('2026-09-27T14:20:30.000Z'), mission: mission([REFUSED]), day: DAY });
    expect(slot?.from.kind).toBe('robot');
    expect(slot?.active).toBe(false);
  });

  it("says the robot's reading in the same words whenever and however often this page read it (verdict item 7)", () => {
    const words = (observed: string | null, readAtMs: number): string | undefined =>
      cakeStory({ mission: mission([REFUSED, SWAPPED]), candidates: null, signature: null, runtimeSession: null, robot: robot(true, 2, observed), capabilityCheck: null, robotReadAtMs: readAtMs }).slot?.words;
    const first = words('2026-09-27T14:21:50.000Z', Date.parse('2026-09-27T14:21:51.000Z'));
    // A reload a minute later, a reading with a newer time, and one that carries no time of its own.
    expect(words('2026-09-27T14:22:50.000Z', Date.parse('2026-09-27T14:22:51.000Z'))).toBe(first);
    expect(words(null, Date.parse('2026-09-27T14:40:00.000Z'))).toBe(first);
    expect(first).not.toMatch(/since|\d\d:\d\d:\d\dZ/);
  });
});
