// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT IS CURRENT, AND WHAT IS HISTORY (docs/autonomous-demo/console-redesign-2026-09-27.md, section 12; operator ruling
// 2026-09-28: "we shouldn't show outdated information at all; historic information should be moved to a different
// section", with dates shown).
//
// After a restart the runtime adopts the newest mission on its record WITHOUT re-entering it
// (`mission_coordinator.recover`), and its journal's last outcome and its event log outlive the process. So a runtime
// restarted today serves yesterday's FAILED mission as `/api/mission`, and the main screen drew it as "Mission failed" as
// if it had just happened. This module decides, from the records alone, whether a record belongs on the main screen.
//
// THE RULE:
//   1. The runtime session's start is read from its id, which the runtime writes as `rt-<UTC YYYYMMDDTHHMMSSZ>-<hex>`
//      (`demo_runtime/daemon.py`, `journal.utc_compact`). No other record dates the session. An id in any other shape
//      (the in-page rehearsal's `synthetic-console-session`) dates nothing, and then nothing is moved: the record cannot be
//      placed, and it stays where it was, with its date printed as rule 5.4 prints it.
//   2. A mission that has not ended (RUNNING, WAITING_FOR_CAPABILITY, any state not COMPLETE, FAILED or STOPPED) is ALWAYS
//      current, whatever its age: an orphaned or held mission is one the operator has to act on, and Stop needs it.
//   3. A mission this page read while it had not ended stays current through its end ("started since the page's session").
//   4. Otherwise an ended mission is current when its NEWEST record time (its start, stop request, attempts, retries,
//      activations and derived end) is at or after the session's start and on today's UTC day; before the start it is
//      "earlier_session", on an earlier day "earlier_day". Today is this display's clock, read only when the session is
//      dated (rule 1), so a day boundary flips it once and nothing ticks.
//   5. A run: the active run is current; a finished run is placed by its own recorded times (the finished-run list, its
//      run.started, its end); with a dated session and no time at all it is not drawn, since nothing places it here.
//
// The session start has one-second precision (the id is truncated to the second): a record written in the same second
// before a restart is counted as this session's. The mission record does not name its runtime session; "earlier runtime
// session" is derived from the times above and is said as derived.

import type { HistoryEntry } from '../api/types';
import type { MissionRecord } from './missionRecord';
import { missionEndTime } from './missionTimeline';
import { parseUtcMs } from './utcTime';
import { missionEnded } from './verdict';

const SESSION_ID = /^rt-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z-[0-9a-f]+$/;

/** When the runtime session began, as an ISO string, read from its id; null for an id that does not date itself. */
export function runtimeSessionStart(session: string | null | undefined): string | null {
  if (typeof session !== 'string') return null;
  const match = SESSION_ID.exec(session);
  if (match === null) return null;
  const [, year, month, day, hour, minute, second] = match;
  const text = `${year ?? ''}-${month ?? ''}-${day ?? ''}T${hour ?? ''}:${minute ?? ''}:${second ?? ''}.000Z`;
  const ms = parseUtcMs(text);
  // A calendar day that does not exist (month 13) is refused, not rolled over into another date.
  if (ms === null || new Date(ms).toISOString() !== text) return null;
  return text;
}

export interface CurrencyContext {
  /** The runtime session the snapshot names now. */
  session: string | null;
  /** Its start, from its id; null when the id does not date it. */
  session_start: string | null;
  /** This display's UTC day, only when the session start is known; null otherwise. */
  today: string | null;
}

export function currencyContext(session: string | null | undefined, nowMs: number): CurrencyContext {
  const start = runtimeSessionStart(session);
  return { session: session ?? null, session_start: start, today: start === null ? null : new Date(nowMs).toISOString().slice(0, 10) };
}

export type CurrencyReason = 'open' | 'seen_open' | 'this_session' | 'unplaced' | 'earlier_session' | 'earlier_day';

export interface Currency {
  current: boolean;
  reason: CurrencyReason;
  /** The record time the answer was read from (ISO), or null when none was. */
  at: string | null;
}

/** One record time against the session and today (rule 4). */
export function recordCurrency(at: string | null | undefined, context: CurrencyContext): Currency {
  const ms = parseUtcMs(at);
  const startMs = parseUtcMs(context.session_start);
  if (ms === null || startMs === null) return { current: true, reason: 'unplaced', at: ms === null ? null : new Date(ms).toISOString() };
  const iso = new Date(ms).toISOString();
  if (ms < startMs) return { current: false, reason: 'earlier_session', at: iso };
  if (context.today !== null && iso.slice(0, 10) < context.today) return { current: false, reason: 'earlier_day', at: iso };
  return { current: true, reason: 'this_session', at: iso };
}

function newest(times: readonly (string | null | undefined)[]): string | null {
  let best: number | null = null;
  for (const text of times) {
    const ms = parseUtcMs(text);
    if (ms !== null && (best === null || ms > best)) best = ms;
  }
  return best === null ? null : new Date(best).toISOString();
}

/** The newest time the mission's record carries, with its derived end (rule 6.7); null when it carries none. */
export function missionNewestTime(mission: MissionRecord, history: readonly HistoryEntry[]): string | null {
  const times: (string | null | undefined)[] = [mission.started_wall_utc, mission.stop_requested_wall_utc, missionEndTime(mission, history)?.iso];
  for (const activation of mission.activations) times.push(activation.t_wall_utc);
  for (const target of mission.targets) {
    for (const attempt of target.attempts ?? []) times.push(attempt.t_wall_utc);
    for (const retry of target.retries ?? []) times.push(retry.t_wall_utc);
  }
  return newest(times);
}

/** Whether the mission on the record belongs on the main screen (rules 2 to 4). */
export function missionCurrency(mission: MissionRecord, history: readonly HistoryEntry[], context: CurrencyContext, seenOpen: readonly string[]): Currency {
  if (!missionEnded(mission.state)) return { current: true, reason: 'open', at: null };
  if (seenOpen.includes(mission.mission_id)) return { current: true, reason: 'seen_open', at: null };
  return recordCurrency(missionNewestTime(mission, history), context);
}

export interface RunTimes {
  /** True while the record says the run is in progress. */
  active: boolean;
  history: readonly HistoryEntry[];
  /** Further recorded times of the run: its run.started, its end as the snapshot or its events carry it. */
  times: readonly (string | null | undefined)[];
}

/** Whether a run belongs on the main screen (rule 5). */
export function runCurrency(runId: string, run: RunTimes, context: CurrencyContext): Currency {
  if (run.active) return { current: true, reason: 'open', at: null };
  const entry = run.history.find((item) => item.run_id === runId);
  const at = newest([entry?.finished_wall_utc, entry?.started_wall_utc, ...run.times]);
  if (at === null && context.session_start !== null) return { current: false, reason: 'unplaced', at: null };
  return recordCurrency(at, context);
}

/** The words for where a record sits, for History: "this runtime session" or the derived earlier one. */
export function sessionWords(currency: Currency, context: CurrencyContext): string {
  const session = context.session ?? 'not named';
  if (currency.reason === 'earlier_session') return `an earlier runtime session (recorded before ${session} started)`;
  if (currency.reason === 'earlier_day') return `an earlier day (runtime session ${session})`;
  if (currency.reason === 'unplaced' && context.session_start === null) return `runtime session ${session}, which does not date itself`;
  if (currency.reason === 'unplaced') return 'a time the record does not carry';
  return `this runtime session (${session})`;
}

/**
 * THE MISSIONS THAT ARE HISTORY'S among those this page read (verify-r1 M1): the one the record serves now when it is not
 * current, and every earlier one this page read that is not current by its own newest record. A mission the page read
 * while it had not ended stays current (rule 3), as it did while it was served.
 */
export function pastMissions(
  read: readonly MissionRecord[],
  served: MissionRecord | null,
  history: readonly HistoryEntry[],
  context: CurrencyContext,
  seenOpen: readonly string[],
): MissionRecord[] {
  const records = served === null ? [...read] : [...read.filter((item) => item.mission_id !== served.mission_id), served];
  return records.filter((mission) => !missionCurrency(mission, history, context, seenOpen).current);
}

/**
 * THE TIMELINE WITHOUT THE LINES OF HISTORY'S MISSIONS: each one's start, stop, retries and end, its Cake lines and its
 * runs' groups. The store's list is append-only for the page's life (rule 6.3.1); a mission that is History's is filtered
 * at the screen, so a line inserted before the session was read, before a day ended, or while that mission was the one
 * served (before the runtime served a newer one) leaves the main screen too. The presses and link changes are this
 * display's own and stay.
 */
export function currentTimelineEntries<T extends { key: string; group: string | null }>(entries: readonly T[], past: readonly MissionRecord[]): T[] {
  if (past.length === 0) return [...entries];
  const runs = new Set(past.flatMap((mission) => mission.targets.flatMap((target) => [...target.runs, ...(target.park_runs ?? [])])));
  const activations = new Set(past.flatMap((mission) => mission.activations.map((activation) => `activation:${activation.operation_uuid}`)));
  const prefixes = past.map((mission) => `mission:${mission.mission_id}:`);
  return entries.filter(
    (entry) => !prefixes.some((prefix) => entry.key.startsWith(prefix)) && !activations.has(entry.key) && !(entry.group !== null && runs.has(entry.group)),
  );
}
