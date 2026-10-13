// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RECORDS THE SCENE DRAWS, NAMED (redesign verdict 2026-09-27 item 8).
//
// The Scene folded the drawn runs' `recent_events`. For the run this tab watched, that is the live fold, which carries
// every earlier run's records across a `run.started`; so after Scan scene then Start, the playing tab drew the scan's
// five views and two blocks while a tab opened afresh, which reads the mission's runs from the runtime, drew one block
// and no views. The Scene changed on reload with no record changing, and neither tab said which run it drew.
//
// NOW EACH RUN CONTRIBUTES ITS OWN RECORDS ONLY, the events whose `run_id` is that run's, which is exactly what the
// runtime serves for it (`journal.py` `read_run`). The scan is one of those runs, named, from the runtime's run list,
// never carried by the live fold:
//
//   the newest Scan scene run the runtime lists (or one this tab watched), when this page holds its records,
//   - drawn alone when it started after every drawn run: it is the newest record of the scene;
//   - drawn first, then the drawn runs, when it started before the first of them with no other cycle run between;
//   - otherwise not drawn.
//
// The Scene then says which runs it drew and when each started, from those runs' own records.

import type { ContractEvent } from '../contracts/envelope';
import type { HistoryEntry } from '../api/types';
import type { FoldedRun } from './stageTimeline';
import type { ViewState } from './types';
import { parseUtcMs, utcStamp } from './utcTime';

/** One run the Scene draws: its id, the command it ran and when it started, and its own records. */
export interface SceneRun {
  run_id: string;
  command_type: string | null;
  started_wall_utc: string | null;
  events: ContractEvent[];
}

export interface SceneRecordsInput {
  /** The runs the stages are drawn for (`timelineRuns`), oldest first. */
  drawnRuns: readonly FoldedRun[];
  /** The runtime's run list (GET /api/history), or empty when it has not been read. */
  history: readonly HistoryEntry[];
  /** The live fold: a scan it watched counts before the run list names it. */
  live: ViewState;
  /** The view this page holds for a run, or null: whichever fold holds it, filtered here to the run's own records. */
  held: (runId: string) => ViewState | null;
}

const SCAN = 'scan_scene';

/** A run's own records: the events whose run id is the run's, which is what the runtime serves for that run. */
export function ownRecords(runId: string, events: readonly ContractEvent[]): ContractEvent[] {
  return events.filter((event) => event.run_id === runId);
}

function sceneRun(runId: string, events: readonly ContractEvent[], history: readonly HistoryEntry[]): SceneRun {
  const own = ownRecords(runId, events);
  const start = own.find((event) => event.kind === 'run.started');
  const listed = history.find((entry) => entry.run_id === runId) ?? null;
  const commandType = start?.kind === 'run.started' ? start.payload.command_type : null;
  return {
    run_id: runId,
    command_type: commandType ?? listed?.command_type ?? null,
    started_wall_utc: start?.t_wall_utc ?? listed?.started_wall_utc ?? null,
    events: own,
  };
}

/** The scans whose start the live fold holds: the ones this tab watched, before the runtime's list names them. */
export function watchedScans(live: ViewState): Array<{ run_id: string; started_wall_utc: string | null; finished: boolean }> {
  const out: Array<{ run_id: string; started_wall_utc: string | null; finished: boolean }> = [];
  for (const event of live.recent_events) {
    if (event.kind !== 'run.started' || event.payload.command_type !== SCAN || event.run_id === null) continue;
    const runId = event.run_id;
    out.push({ run_id: runId, started_wall_utc: event.t_wall_utc, finished: live.recent_events.some((other) => other.kind === 'run.finished' && other.run_id === runId) });
  }
  return out;
}

/**
 * The newest Scan scene run: the runtime's list, and the scans this tab watched (their `run.started` is in the live
 * fold), since the list names a run only once it has ended and a read of it can predate that.
 */
export function newestScanRun(history: readonly HistoryEntry[], live: ViewState): { run_id: string; started_wall_utc: string | null } | null {
  const scans: Array<{ run_id: string; started_wall_utc: string | null }> = history
    .filter((entry) => entry.command_type === SCAN && typeof entry.run_id === 'string')
    .map((entry) => ({ run_id: entry.run_id, started_wall_utc: entry.started_wall_utc ?? null }));
  for (const watched of watchedScans(live)) {
    if (!scans.some((scan) => scan.run_id === watched.run_id)) scans.push({ run_id: watched.run_id, started_wall_utc: watched.started_wall_utc });
  }
  let newest: { run_id: string; started_wall_utc: string | null; at: number } | null = null;
  for (const scan of scans) {
    const at = parseUtcMs(scan.started_wall_utc);
    if (at === null) continue;
    if (newest === null || at > newest.at) newest = { ...scan, at };
  }
  return newest === null ? null : { run_id: newest.run_id, started_wall_utc: newest.started_wall_utc };
}

/** The runs the Scene draws, oldest first, each with its own records only. */
export function sceneRecords(input: SceneRecordsInput): SceneRun[] {
  const drawn = input.drawnRuns.map((run) => sceneRun(run.run_id, run.view.recent_events, input.history)).filter((run) => run.events.length > 0);
  const scan = newestScanRun(input.history, input.live);
  if (scan === null || drawn.some((run) => run.run_id === scan.run_id)) return drawn;
  const view = input.held(scan.run_id);
  if (view === null) return drawn;
  const scanned = sceneRun(scan.run_id, view.recent_events, input.history);
  if (scanned.events.length === 0) return drawn;
  const first = drawn[0];
  const last = drawn[drawn.length - 1];
  if (first === undefined || last === undefined) return [scanned];
  const scanAt = parseUtcMs(scanned.started_wall_utc);
  const firstAt = parseUtcMs(first.started_wall_utc);
  const lastAt = parseUtcMs(last.started_wall_utc);
  if (scanAt === null || firstAt === null || lastAt === null) return drawn;
  if (scanAt > lastAt) return [scanned];
  if (scanAt >= firstAt) return drawn;
  const drawnIds = new Set(drawn.map((run) => run.run_id));
  const cycleBetween = input.history.some((entry) => {
    if (entry.command_type !== 'start_cycle' || drawnIds.has(entry.run_id)) return false;
    const at = parseUtcMs(entry.started_wall_utc);
    return at !== null && at > scanAt && at < firstAt;
  });
  return cycleBetween ? drawn : [scanned, ...drawn];
}

/** The words a run's command is named by on the Scene. */
const COMMAND_WORDS: Record<string, string> = {
  start_cycle: 'Start',
  observe: 'Observe',
  return_arm: 'Return arm',
};

/** "Drawn from the records of ...": which runs the Scene drew, and when each started. Null when it drew none. */
export function sceneRecordsLine(runs: readonly SceneRun[], day: string | null): string | null {
  if (runs.length === 0) return null;
  const parts = runs.map((run) => {
    const when = utcStamp(run.started_wall_utc, day);
    const word = run.command_type === null ? undefined : COMMAND_WORDS[run.command_type];
    const name = run.command_type === SCAN ? `the Scan scene run ${run.run_id}` : `run ${run.run_id}${word === undefined ? '' : ` (${word})`}`;
    return `${name}${when === null ? '' : `, started ${when.text}`}`;
  });
  const joined = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')}, and ${parts[parts.length - 1] ?? ''}`;
  return `Drawn from the records of ${joined ?? ''}.`;
}
