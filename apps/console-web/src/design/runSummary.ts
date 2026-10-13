// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT ONE FINISHED RUN CAME TO, FOR HISTORY, AND ONLY FOR HISTORY.
//
// The main screen used to state a finished run's outcome in a large headline, again as a recorded-outcome note, again
// as a previous-run line and again beside the clearance box, and the rail and the Cake panel said it a fourth and a
// fifth time. Terminal outcomes and durations now live in History, which is where this is read.
//
// ONE RULE ABOVE THE REST: A FIXTURE RESULT IS NEVER A PHYSICAL PLACEMENT. The run's own data mode decides it. A run
// that is not a live runtime's is worded as what the rehearsal recorded, and "placed" is not said about it.

import type { HistoryEntry } from '../api/types';
import type { ContractEvent } from '../contracts/envelope';
import { acceptanceNote, clearanceMeasurement, establishedResult, resultPredicates } from '../model/acceptance';
import type { CommandScope } from '../model/command';
import { AFTER_RELEASE } from '../model/missionCheck';
import type { MissionRecord, MissionTargetRecord } from '../model/missionRecord';
import { durationWords } from '../model/utcTime';
import { runWordFor } from '../model/verdict';
import type { Verdict } from '../model/verdict';
import { targetOfRun } from '../model/missionView';
import { previousRun, resultForCurrentRun } from '../model/steps';
import type { OutcomeView } from '../model/types';
import type { StoreSnapshot } from '../store/store';

export type RunMode = 'live' | 'rehearsal' | 'replay' | 'unstated';

/** How a run executed, from the data mode its own record carries. Absent is `unstated`, never assumed physical. */
export function runMode(dataMode: string | null | undefined): RunMode {
  if (dataMode === 'live') return 'live';
  if (dataMode === 'synthetic') return 'rehearsal';
  if (dataMode === 'replay') return 'replay';
  return 'unstated';
}

// THE FOUR MODE WORDS OF THE TOP BAR (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 5.2), for every row.
export const RUN_MODE_MARK: Record<RunMode, string> = {
  live: 'Live',
  rehearsal: 'Rehearsal',
  replay: 'Recording',
  unstated: 'Mode not recorded',
};

/** Finished minus started, both written by the runtime. Null when either is missing or unreadable. */
export function runDuration(entry: Pick<HistoryEntry, 'started_wall_utc' | 'finished_wall_utc'>): string | null {
  // THE ONE DURATION FORMAT (spec 5.4; browser r2 N2): "2 min 54 s", as the mission card and the timeline give it.
  return durationWords(entry.started_wall_utc, entry.finished_wall_utc);
}

export interface RunSummary {
  runId: string | null;
  mode: RunMode;
  /** What the run is established to have done. Never "placed" for a run that is not a live runtime's. */
  headline: string;
  /** The word the runtime recorded, when the headline is not simply a restatement of it. */
  recorded: string | null;
  /** The run's spec 5.1 word (`runWordFor`), or null where it is not read yet. */
  word: Verdict | null;
  /** The run.finished outcome token as recorded, for a title attribute only (spec 5.1: raw tokens only in a title). */
  token: string | null;
  reason: string | null;
  /** The clearance measurement, or the acceptance note, when the run carries one. */
  note: string | null;
  target: MissionTargetRecord | null;
  previous: OutcomeView | null;
}

/**
 * THE ONE LINE THE MAIN SCREEN SAYS ABOUT A FINISHED RUN: its mode mark, what it is established to have done and its
 * figures. Nothing in it moves with time or with how the tab arrived (no duration, no age), so a tab that watched the
 * run, one that opened late and a reload draw the same words. A rehearsal's line says it is one and never "placed".
 */
export function lastRunLine(summary: RunSummary): string {
  // A live placement's acceptance note opens with the headline itself; it is said once.
  const lead = `${summary.headline}.`;
  const note = summary.note === null ? '' : summary.note.startsWith(lead) ? summary.note.slice(lead.length).trim() : summary.note;
  return `${RUN_MODE_MARK[summary.mode]}. ${lead}${note === '' ? '' : ` ${note}`}`;
}

/**
 * Whether the run's own records show the block released: a RELEASED transition among its events, a stop phase at or
 * after RELEASED, or the mission's attempt for this run recording the gripper empty where it stopped. Any one is
 * enough; none of them read is not evidence the block is still held, it only leaves the headline to the predicates.
 */
function releasedIn(result: OutcomeView, events: readonly ContractEvent[] | undefined, mission: MissionRecord | null): boolean {
  if (result.phase !== null && AFTER_RELEASE.has(result.phase)) return true;
  if ((events ?? []).some((event) => event.kind === 'state.transition' && event.payload.to_state === 'RELEASED')) return true;
  if (mission === null || result.run_id === null) return false;
  const attempt = mission.targets.flatMap((target) => target.attempts ?? []).find((item) => item.run_id === result.run_id);
  return attempt?.stop_state?.holding === false;
}

/**
 * The run whose result the runtime's fold carries, summarised once. `scope` is the command type on the record, so an
 * Observe reads as an observation and not as a failed placement.
 */
export function runSummary(snapshot: StoreSnapshot, scope: CommandScope, mission: MissionRecord | null): RunSummary | null {
  const view = snapshot.view;
  const result = resultForCurrentRun(view);
  if (result === null) return null;
  const resolved = resultPredicates(view.predicates, result.run_id, snapshot.resultDetail);
  const entry = snapshot.history !== null && snapshot.history.ok ? snapshot.history.entries.find((item) => item.run_id === result.run_id) ?? null : null;
  const mode = runMode(entry?.data_mode ?? view.data_mode);
  // THE RUN'S WORD (spec 5.1), never its token on the screen (browser r2 N2, verifier R-4).
  const word = runWordFor({ events: snapshot.missionRunEvents[result.run_id ?? ''], view, run_id: result.run_id, outcome: result.reported, reason_code: result.reason_code, placement: scope.requires_placement });
  const wordText = word === null ? '; its word is not read yet' : `: ${word.word}`;
  const plain =
    scope.kind === 'observe' ? 'Observation captured' : scope.kind === 'return_arm' ? 'Arm returned' : 'Command finished';
  let headline: string;
  let recorded: string | null = null;
  let note: string | null = null;
  if (!scope.requires_placement) {
    headline = plain;
  } else if (mode !== 'live') {
    // A FIXTURE RESULT. What the rehearsal recorded, in its own word, and never a placement. Its figures are shown,
    // met or missed, as what the rehearsal's own records measured: they are the run's figures, not the robot's.
    headline = `${mode === 'rehearsal' ? "The rehearsal's run ended" : "The record's run ended"}${wordText}; nothing was physically moved by it`;
    const measured = clearanceMeasurement(resolved);
    const source = mode === 'rehearsal' ? "the rehearsal's own records" : 'the record';
    note = measured === null ? null : `What ${source} measured, not a physical measurement: ${measured}`;
  } else {
    const established = establishedResult(result, resolved, releasedIn(result, snapshot.missionRunEvents[result.run_id ?? ''], mission));
    // A BARE OUTCOME WORD IS NOT A SENTENCE. When no measurement establishes more than the word the run recorded, the
    // headline says that the run recorded it, so a failed run reads "The run recorded the outcome failed" and never as
    // a stray "failed." after its mode mark. What the measurements establish ("Placed and returned") is unchanged.
    headline = established.from_predicates ? established.headline : `The run ended${wordText}`;
    recorded = established.recorded;
    note = acceptanceNote(result, resolved) ?? established.qualifier;
  }
  return {
    runId: result.run_id,
    mode,
    headline,
    recorded,
    word,
    token: result.reported,
    reason: result.reason_code,
    note,
    target: targetOfRun(mission, result.run_id),
    previous: previousRun(view),
  };
}
