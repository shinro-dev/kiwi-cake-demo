// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE MISSION TIMELINE (docs/autonomous-demo/console-redesign-2026-09-27.md, rules 6.3 and 6.7; audit T3, T5).
//
// One list for the page's life, oldest first, times in UTC. Every entry is keyed (`run_id:seq` for an event,
// `run:<id>` for a run's heading, `activation:<operation>`, `mission:<id>:started|stop|ended`, `mission:<id>:retry:<n>`,
// `press:<id>`, `link:<n>`); `mergeTimeline` inserts a key once and never removes one, so a re-read that returns fewer
// records takes nothing away.
//
// WHAT ENTERS FROM A RUN'S EVENTS: its state transitions, a refused action, the stop records, its end, an error or a
// warning line, and the records the stop's cause joins by action id (rule 6.3.4). Frames, perception, timing spans,
// passing predicates, planned and completed actions and stdout stay in Diagnostics. `run.started` is the run's heading.
//
// THE MISSION'S END TIME is not in its record. It is the finish of the mission's last run (GET /api/history), or the
// stop request's time for a mission stopped while no run was active, and it says so in its title (rule 6.7).

import type { ContractEvent } from '../contracts/envelope';
import type { HistoryEntry } from '../api/types';
import type { PressEntry } from '../store/presses';
import { PRESS_WORDS } from '../store/presses';
import type { MissionCandidate, MissionRecord } from './missionRecord';
import { activationCard } from './activationCard';
import { stopCause } from './stopCause';
import { durationWords, parseUtcMs, utcDayOf, utcStamp } from './utcTime';
import { missionEnded, missionVerdict, runVerdictOf } from './verdict';
import type { Verdict } from './verdict';

/** The event kinds a run contributes to the timeline, before the cause join adds its own records. */
export const TIMELINE_EVENT_KINDS: ReadonlySet<string> = new Set([
  'run.started',
  'state.transition',
  'action.refused',
  'stop.requested',
  'stop.acknowledged',
  'stop.outcome',
  'run.finished',
  'diagnostic.text',
]);

/** The kinds the store keeps of every mission run: the timeline's, and the ones the cause join reads. */
export const KEPT_EVENT_KINDS: ReadonlySet<string> = new Set([...TIMELINE_EVENT_KINDS, 'action.planned', 'predicate.evaluated', 'measured.state']);

/** Whether the store keeps this event of a mission run (a passing predicate and an info line are not kept). */
export function keptEvent(event: ContractEvent): boolean {
  if (!KEPT_EVENT_KINDS.has(event.kind)) return false;
  const payload = payloadOf(event);
  if (event.kind === 'predicate.evaluated') return payload['value'] === false;
  if (event.kind === 'diagnostic.text') return payload['level'] === 'error' || payload['level'] === 'warning';
  return true;
}

export const MISSION_END_TITLE = "the time the mission's last run finished; the mission record carries no end time";
export const MISSION_STOP_END_TITLE = 'the time the stop was asked for, with no run active; the mission record carries no end time';

export type TimelineKind = 'mission' | 'cake' | 'retry' | 'run' | 'event' | 'press' | 'link';

export interface TimelineEntry {
  key: string;
  kind: TimelineKind;
  /** The run the entry belongs to (its heading included), or null for a mission, Cake, press or link line. */
  group: string | null;
  /** The recorded time, as an ISO string with milliseconds, or null when the record carries none. */
  t: string | null;
  /** The title the time carries, when it is derived rather than recorded. */
  t_title?: string;
  seq: number | null;
  text: string;
  verdict: Verdict | null;
  /** Further lines under the entry (a Cake card's second line, a run heading's note). */
  more: string[];
}

export interface LinkChange {
  n: number;
  at: string;
  lost: boolean;
}

export interface TimelineInput {
  mission: MissionRecord | null;
  history: readonly HistoryEntry[];
  runEvents: Readonly<Record<string, readonly ContractEvent[]>>;
  presses: readonly PressEntry[];
  links: readonly LinkChange[];
  candidates: readonly MissionCandidate[] | null;
  /** This display's UTC day, the reference day when no mission is on the record (rule 5.4). */
  today?: string | null;
  /**
   * This display's UTC day, given only when the runtime session dates itself (section 12): then it is the reference day,
   * so a time on another day, a held mission's from yesterday included, prints its date.
   */
  datedDay?: string | null;
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function payloadOf(event: ContractEvent): Record<string, unknown> {
  return ((event as { payload?: unknown }).payload ?? {}) as Record<string, unknown>;
}

function runsOf(mission: MissionRecord): string[] {
  return mission.targets.flatMap((target) => target.runs);
}

/** When the mission ended, derived and labelled (rule 6.7); null while it has not ended or nothing records it. */
export function missionEndTime(mission: MissionRecord, history: readonly HistoryEntry[]): { iso: string; title: string } | null {
  if (!missionEnded(mission.state)) return null;
  const runs = runsOf(mission);
  const last = runs[runs.length - 1];
  const finished = last === undefined ? null : (history.find((entry) => entry.run_id === last)?.finished_wall_utc ?? null);
  const finishedMs = parseUtcMs(finished);
  const stopMs = mission.stop_requested === true ? parseUtcMs(mission.stop_requested_wall_utc) : null;
  if (stopMs !== null && (finishedMs === null || finishedMs < stopMs) && mission.state === 'STOPPED') {
    return { iso: new Date(stopMs).toISOString(), title: MISSION_STOP_END_TITLE };
  }
  return finishedMs === null ? null : { iso: new Date(finishedMs).toISOString(), title: MISSION_END_TITLE };
}

/** "run 2 of 2", counted from the mission's runs; null for a run the mission does not list. */
export function runOrdinal(mission: MissionRecord | null, runId: string): { n: number; of: number } | null {
  if (mission === null) return null;
  const runs = runsOf(mission);
  const at = runs.indexOf(runId);
  return at < 0 ? null : { n: at + 1, of: runs.length };
}

function iso(text: string | null | undefined): string | null {
  const ms = parseUtcMs(text);
  return ms === null ? null : new Date(ms).toISOString();
}

function runEntries(mission: MissionRecord, runId: string, events: readonly ContractEvent[], history: HistoryEntry | null, referenceDay: string | null): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  const ordinal = runOrdinal(mission, runId);
  const name = ordinal === null ? `Run ${runId}` : `Run ${String(ordinal.n)} of ${String(ordinal.of)}`;
  const started = events.find((event) => event.kind === 'run.started');
  const finished = [...events].reverse().find((event) => event.kind === 'run.finished');
  const startIso = iso(history?.started_wall_utc) ?? iso(started?.t_wall_utc ?? null);
  const endIso = iso(history?.finished_wall_utc) ?? iso(finished?.t_wall_utc ?? null);
  const from = utcStamp(startIso, referenceDay);
  const to = utcStamp(endIso, referenceDay);
  const span = from === null ? '' : to === null ? `, from ${from.text}` : `, ${from.text} to ${to.text}${durationWords(startIso, endIso) === null ? '' : ` (${durationWords(startIso, endIso) ?? ''})`}`;
  const park = mission.targets.some((target) => (target.park_runs ?? []).includes(runId));
  out.push({ key: `run:${runId}`, kind: 'run', group: runId, t: startIso, seq: -1, text: `${name}, ${runId}${span}`, verdict: null, more: park ? ['The park before a retry: the arm returns to the low scan pose; it is not an attempt at the block.'] : [] });

  const cause = stopCause(events, referenceDay);
  const causeRefusal = cause?.refusal ?? null;
  const tiedKeys = new Map<number, string>();
  if (causeRefusal !== null && cause !== null) {
    // The joined records: the false predicates and the newest measured state of the refused action, before it.
    const joined = events.filter((event) => event.action_id === causeRefusal.action_id && event.seq < causeRefusal.seq);
    const falsePredicates = joined.filter((event) => event.kind === 'predicate.evaluated' && payloadOf(event)['value'] === false);
    const measured = [...joined].reverse().find((event) => event.kind === 'measured.state');
    falsePredicates.forEach((event, index) => tiedKeys.set(event.seq, cause.lines[index + 1] ?? ''));
    if (measured !== undefined) {
      const velocity = cause.lines.find((line) => line.startsWith('Base velocity in that reading'));
      if (velocity !== undefined) tiedKeys.set(measured.seq, velocity);
    }
  }
  const seen = new Set<string>();
  for (const event of events) {
    const key = `${runId}:${String(event.seq)}`;
    const t = iso(event.t_wall_utc);
    const stamp = utcStamp(t, referenceDay);
    const at = stamp === null ? '' : ` ${stamp.text}`;
    const payload = payloadOf(event);
    const tied = tiedKeys.get(event.seq);
    if (tied !== undefined) {
      out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text: tied, verdict: null, more: [] });
      continue;
    }
    switch (event.kind) {
      case 'state.transition': {
        const to = String(payload['to_state'] ?? 'no state named');
        const again = seen.has(to);
        seen.add(to);
        out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text: `${to}${again ? ' again' : ''}${at}`, verdict: null, more: [] });
        break;
      }
      case 'action.refused': {
        const text =
          causeRefusal !== null && causeRefusal.seq === event.seq && cause !== null
            ? (cause.lines[0] ?? '')
            : `Refused ${String(payload['action_type'] ?? 'an action')}${typeof payload['detail'] === 'string' ? `: ${payload['detail']}` : ''}${event.reason_code === null ? '' : ` (${event.reason_code})`}`;
        out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text, verdict: null, more: [] });
        break;
      }
      case 'stop.requested':
        out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text: `Stop asked for (${String(payload['origin'] ?? 'origin not recorded')})${at}`, verdict: null, more: [] });
        break;
      case 'stop.acknowledged':
        out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text: `Stop acknowledged by ${String(payload['acknowledged_by'] ?? 'the controller')}${at}`, verdict: null, more: [] });
        break;
      case 'stop.outcome':
        out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text: `Stop outcome: ${String(payload['outcome'] ?? 'unknown')}${at}`, verdict: null, more: [] });
        break;
      case 'diagnostic.text':
        if (payload['level'] === 'error' || payload['level'] === 'warning') {
          out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text: `${String(payload['level'])}: ${String(payload['text'] ?? '')}`, verdict: null, more: [] });
        }
        break;
      case 'run.finished': {
        const reason = typeof payload['reason_code'] === 'string' ? payload['reason_code'] : event.reason_code;
        // The mission card's and History's word for the run: one function (`runVerdictOf`).
        const verdict = runVerdictOf(events);
        out.push({ key, kind: 'event', group: runId, t, seq: event.seq, text: `${name} ${verdict === null ? 'finished' : lowerFirst(verdict.word)}${reason === null ? '' : `, ${reason}`}`, verdict, more: [] });
        break;
      }
      default:
        break;
    }
  }
  return out;
}

/** The whole timeline from what the store holds now, ordered. `mergeTimeline` keeps what an earlier read held. */
export function composeTimeline(input: TimelineInput): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  const mission = input.mission;
  const referenceDay = input.datedDay ?? utcDayOf(mission?.started_wall_utc ?? null) ?? input.today ?? null;
  if (mission !== null) {
    const id = mission.mission_id;
    const started = iso(mission.started_wall_utc);
    entries.push({ key: `mission:${id}:started`, kind: 'mission', group: null, t: started, seq: null, text: `Mission ${id} started${started === null ? '' : ` ${utcStamp(started, referenceDay)?.text ?? ''}`}`, verdict: null, more: [] });
    if (mission.stop_requested === true) {
      const at = iso(mission.stop_requested_wall_utc);
      entries.push({ key: `mission:${id}:stop`, kind: 'mission', group: null, t: at, seq: null, text: `Stop asked for mission ${id}${at === null ? '' : ` ${utcStamp(at, referenceDay)?.text ?? ''}`}`, verdict: null, more: [] });
    }
    for (const activation of mission.activations) {
      const card = activationCard(activation, input.candidates, referenceDay);
      entries.push({ key: `activation:${activation.operation_uuid}`, kind: 'cake', group: null, t: card.stamp?.iso ?? null, seq: null, text: card.headline, verdict: card.verdict, more: [card.line] });
    }
    for (const target of mission.targets) {
      for (const retry of target.retries ?? []) {
        const at = iso(retry.t_wall_utc);
        entries.push({
          key: `mission:${id}:retry:${String(retry.attempt ?? 'n')}`,
          kind: 'retry',
          group: null,
          t: at,
          seq: null,
          text: `Try again asked for after attempt ${String(retry.attempt ?? '(not recorded)')}${at === null ? '' : ` ${utcStamp(at, referenceDay)?.text ?? ''}`}`,
          verdict: null,
          more: [],
        });
      }
      for (const runId of target.runs) {
        const events = input.runEvents[runId] ?? [];
        const history = input.history.find((entry) => entry.run_id === runId) ?? null;
        if (events.length === 0 && history === null) continue;
        entries.push(...runEntries(mission, runId, events, history, referenceDay));
      }
    }
    const end = missionEndTime(mission, input.history);
    if (missionEnded(mission.state)) {
      const verdict = missionVerdict({ state: mission.state, waiting_reason: mission.waiting_reason, stop_requested: mission.stop_requested ?? false });
      const stamp = end === null ? null : utcStamp(end.iso, referenceDay);
      entries.push({
        key: `mission:${id}:ended`,
        kind: 'mission',
        group: null,
        t: end?.iso ?? null,
        ...(end === null ? {} : { t_title: end.title }),
        seq: null,
        text: `Mission ${lowerFirst(verdict.word)}${stamp === null ? '' : `, ended ${stamp.text}`}`,
        verdict,
        more: [],
      });
    }
  }
  for (const press of input.presses) {
    const stamp = utcStamp(press.pressed_at, referenceDay);
    const answer = press.answer === 'pending' ? "waiting for the runtime's answer" : (press.text ?? press.answer);
    entries.push({
      key: `press:${press.id}`,
      kind: 'press',
      group: null,
      t: stamp?.iso ?? null,
      seq: null,
      text: `You pressed ${PRESS_WORDS[press.control]}${stamp === null ? '' : `, ${stamp.text}`} (this display): ${answer}`,
      verdict: null,
      more: [],
    });
  }
  for (const link of input.links) {
    const stamp = utcStamp(link.at, referenceDay);
    entries.push({
      key: `link:${String(link.n)}`,
      kind: 'link',
      group: null,
      t: stamp?.iso ?? null,
      seq: null,
      text: link.lost ? `This display lost the runtime${stamp === null ? '' : ` at ${stamp.text}`}` : `This display reached the runtime again${stamp === null ? '' : ` at ${stamp.text}`}`,
      verdict: null,
      more: [],
    });
  }
  return ordered(entries);
}

function ordered(entries: readonly TimelineEntry[]): TimelineEntry[] {
  const indexed = entries.map((entry, index) => ({ entry, index, ms: parseUtcMs(entry.t) }));
  indexed.sort((a, b) => {
    // An entry with no recorded time keeps its place after the timed entries of its record order.
    const at = a.ms ?? Number.POSITIVE_INFINITY;
    const bt = b.ms ?? Number.POSITIVE_INFINITY;
    if (at !== bt) return at - bt;
    const as = a.entry.seq ?? Number.MAX_SAFE_INTEGER;
    const bs = b.entry.seq ?? Number.MAX_SAFE_INTEGER;
    if (as !== bs) return as - bs;
    return a.index - b.index;
  });
  return indexed.map((item) => item.entry);
}

/** The timeline after a new read: every earlier key kept, each key's newest content, ordered again. */
export function mergeTimeline(previous: readonly TimelineEntry[], next: readonly TimelineEntry[]): TimelineEntry[] {
  const byKey = new Map<string, TimelineEntry>();
  for (const entry of previous) byKey.set(entry.key, entry);
  for (const entry of next) byKey.set(entry.key, entry);
  return ordered([...byKey.values()]);
}
