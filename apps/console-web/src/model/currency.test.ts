// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT IS CURRENT (docs/autonomous-demo/console-redesign-2026-09-27.md, section 12; operator ruling 2026-09-28): the
// rule that keeps an earlier runtime session's or an earlier day's mission, run and picture off the main screen, read from
// the record and never guessed.

import { describe, expect, it } from 'vitest';
import type { HistoryEntry } from '../api/types';
import { parseMission } from './missionRecord';
import type { MissionRecord } from './missionRecord';
import { currencyContext, currentTimelineEntries, missionCurrency, missionNewestTime, recordCurrency, runCurrency, runtimeSessionStart } from './currency';

const SESSION = 'rt-20260928T172310Z-46d1dc34';
const NOW = Date.parse('2026-09-28T18:00:00.000Z');

function mission(patch: Record<string, unknown> = {}): MissionRecord {
  const parsed = parseMission({
    mission_record_version: '1',
    mission_id: 'mission-f65c3cf8e99f',
    state: 'FAILED',
    data_mode: 'live',
    performs_motion: true,
    execution_label: 'proven-single-cube-controller',
    started_wall_utc: '2026-09-27T14:16:38.010Z',
    selected_target: 'cube',
    waiting_reason: null,
    detail: null,
    stop_requested: false,
    stop_requested_wall_utc: null,
    records_after_end: 0,
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: ['console-20260927T142146Z-m6ed2dd9'], attempts: [], retries: [], park_runs: [] }],
    activations: [],
    hold: null,
    next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
    ...patch,
  });
  if (parsed === null) throw new Error('the fixture did not parse');
  return parsed;
}

const YESTERDAY_RUN: HistoryEntry = {
  run_id: 'console-20260927T142146Z-m6ed2dd9',
  command_id: 'm6ed2dd9',
  command_type: 'start_cycle',
  started_wall_utc: '2026-09-27T14:21:46.000Z',
  finished_wall_utc: '2026-09-27T14:24:40.114Z',
  outcome: 'stopped',
  exit_status: 4,
  evidence_complete: true,
  evidence_gaps: 0,
  artifact_digest: null,
  adopted: false,
  data_mode: 'live',
} as unknown as HistoryEntry;

describe('the runtime session start, read from the id the runtime writes (daemon.py: rt-<UTC compact>-<hex>)', () => {
  it('reads the start of an rt- session id and nothing else', () => {
    expect(runtimeSessionStart(SESSION)).toBe('2026-09-28T17:23:10.000Z');
    expect(runtimeSessionStart('synthetic-console-session')).toBeNull();
    expect(runtimeSessionStart('rt-20261399T000000Z-00000000')).toBeNull();
    expect(runtimeSessionStart(null)).toBeNull();
  });

  it('names today only when the session start is known, so a source with no dated session is never cut by the display clock', () => {
    expect(currencyContext(SESSION, NOW)).toEqual({ session: SESSION, session_start: '2026-09-28T17:23:10.000Z', today: '2026-09-28' });
    expect(currencyContext('synthetic-console-session', NOW)).toEqual({ session: 'synthetic-console-session', session_start: null, today: null });
  });
});

describe('one record time against the session', () => {
  const context = currencyContext(SESSION, NOW);
  it('is current at or after the session start on today, past before it, past on an earlier day', () => {
    expect(recordCurrency('2026-09-28T17:23:10.000Z', context).current).toBe(true);
    expect(recordCurrency('2026-09-28T17:23:09.999Z', context)).toMatchObject({ current: false, reason: 'earlier_session' });
    expect(recordCurrency('2026-09-27T14:24:40.114Z', context)).toMatchObject({ current: false, reason: 'earlier_session', at: '2026-09-27T14:24:40.114Z' });
  });

  it('a session that began yesterday still leaves yesterday on an earlier day', () => {
    const overnight = currencyContext('rt-20260927T230000Z-0badc0de', NOW);
    expect(recordCurrency('2026-09-27T23:30:00.000Z', overnight)).toMatchObject({ current: false, reason: 'earlier_day' });
    expect(recordCurrency('2026-09-28T00:10:00.000Z', overnight).current).toBe(true);
  });

  it('cannot place a record with no time, or any record when the session start is unknown, and keeps it (unplaced)', () => {
    expect(recordCurrency(null, context)).toMatchObject({ current: true, reason: 'unplaced' });
    expect(recordCurrency('2026-09-01T00:00:00.000Z', currencyContext(null, NOW))).toMatchObject({ current: true, reason: 'unplaced' });
  });
});

describe('the mission against the session', () => {
  const context = currencyContext(SESSION, NOW);

  it("THE LIVE CASE: a runtime restarted today still serves yesterday's FAILED mission; it is past, dated by its end", () => {
    const answer = missionCurrency(mission(), [YESTERDAY_RUN], context, []);
    expect(answer).toMatchObject({ current: false, reason: 'earlier_session', at: '2026-09-27T14:24:40.114Z' });
  });

  it('the newest time is the newest of its start, its stop, its attempts, its retries, its activations and its end', () => {
    const record = mission({
      activations: [{ operation_uuid: 'op', t_wall_utc: '2026-09-27T14:21:45.496Z', activation_state: 'OBSERVED' }],
      targets: [{ target_id: 'cube', label: 'the block', state: 'FAILED', runs: ['console-20260927T142146Z-m6ed2dd9'], attempts: [{ attempt: 1, t_wall_utc: '2026-09-27T14:24:40.265Z' }], retries: [] }],
    });
    expect(missionNewestTime(record, [YESTERDAY_RUN])).toBe('2026-09-27T14:24:40.265Z');
  });

  it('a mission not ended is ALWAYS current, whatever its age: an orphaned hold still needs the operator', () => {
    expect(missionCurrency(mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision' }), [], context, [])).toMatchObject({ current: true, reason: 'open' });
    expect(missionCurrency(mission({ state: 'RUNNING' }), [], context, [])).toMatchObject({ current: true, reason: 'open' });
  });

  it('a mission this page read while it was carried stays current through its end, even across a restart', () => {
    expect(missionCurrency(mission(), [YESTERDAY_RUN], context, ['mission-f65c3cf8e99f'])).toMatchObject({ current: true, reason: 'seen_open' });
  });

  it('a mission started and ended in this session is current on a fresh page too (reload equivalence)', () => {
    const today = mission({ mission_id: 'mission-d2f1b2ba17c5', started_wall_utc: '2026-09-28T17:36:18.502Z', targets: [{ target_id: 'cube', label: 'the block', state: 'FAILED', runs: [], attempts: [], retries: [] }] });
    expect(missionCurrency(today, [], context, [])).toMatchObject({ current: true, reason: 'this_session' });
    expect(missionCurrency(today, [], context, ['mission-d2f1b2ba17c5'])).toMatchObject({ current: true });
  });
});

describe('a run against the session', () => {
  const context = currencyContext(SESSION, NOW);
  it('the active run is current; a finished run is placed by its own recorded times', () => {
    expect(runCurrency('console-20260927T142146Z-m6ed2dd9', { active: true, history: [YESTERDAY_RUN], times: [] }, context).current).toBe(true);
    expect(runCurrency('console-20260927T142146Z-m6ed2dd9', { active: false, history: [YESTERDAY_RUN], times: [] }, context)).toMatchObject({ current: false, reason: 'earlier_session' });
    expect(runCurrency('r-today', { active: false, history: [], times: ['2026-09-28T17:40:00.000Z'] }, context).current).toBe(true);
  });

  it('a finished run with no recorded time is not drawn once the session is dated: nothing places it in this session', () => {
    expect(runCurrency('r-unknown', { active: false, history: [], times: [null] }, context)).toMatchObject({ current: false, reason: 'unplaced' });
    expect(runCurrency('r-unknown', { active: false, history: [], times: [] }, currencyContext(null, NOW))).toMatchObject({ current: true, reason: 'unplaced' });
  });
});

describe('the timeline without a past mission', () => {
  it("drops the past mission's own lines, its Cake lines and its runs' groups, and keeps this display's presses and links", () => {
    const past = mission({ activations: [{ operation_uuid: 'op-1', t_wall_utc: '2026-09-27T14:21:45.496Z', activation_state: 'OBSERVED' }] });
    const entries = [
      { key: 'mission:mission-f65c3cf8e99f:started', group: null },
      { key: 'activation:op-1', group: null },
      { key: 'run:console-20260927T142146Z-m6ed2dd9', group: 'console-20260927T142146Z-m6ed2dd9' },
      { key: 'console-20260927T142146Z-m6ed2dd9:3906', group: 'console-20260927T142146Z-m6ed2dd9' },
      { key: 'mission:mission-f65c3cf8e99f:ended', group: null },
      { key: 'press:3', group: null },
      { key: 'link:1', group: null },
    ];
    expect(currentTimelineEntries(entries, [past]).map((entry) => entry.key)).toEqual(['press:3', 'link:1']);
    expect(currentTimelineEntries(entries, [])).toHaveLength(entries.length);
  });
});
