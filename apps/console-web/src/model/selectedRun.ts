// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHICH RUN THE PRESENTATION IS DESCRIBING, decided once, so that five figures cannot describe two different runs.
//
// Target, progress, duration, result and the Diagnostics panels were derived separately. Result and progress followed
// the run whose outcome was folded; target and the phase rail followed the LIVE view. While a run is in progress those
// are the same run and nothing shows. After it ends, and especially after the runtime is replaced under an open tab,
// they part: the card said Result over one run while the figures beside it described the session.
//
// SO THERE IS ONE ANSWER HERE, and everything presentational reads it.
//
// WHAT THIS IS NOT. It is NOT a permission. Command eligibility is a statement about the robot NOW and is decided by
// the live snapshot, the capability list and the guards, none of which appear in this file. A tab looking at a run
// from yesterday may still be allowed to return the arm today, and a tab looking at the live run may not be. The two
// questions are kept apart deliberately: `panels/commandGuards.ts` answers the second one and never consults this.

import { currentRunId, outcomeIsOfFoldedRun, resultForCurrentRun } from './steps';
import type { OutcomeView, ViewState } from './types';

export interface SelectedRun {
  /** The run every presentational figure answers for, or null when no run has been seen at all. */
  run_id: string | null;
  /** That run's terminal record, or null while it is still going or when none was folded. */
  outcome: OutcomeView | null;
  /** True while this run is the one the runtime reports as active. */
  live: boolean;
  /** True when the run being described has ended. The screen may then state a duration and a result. */
  finished: boolean;
  /**
   * True when the RUNTIME has been replaced under this tab while the screen still describes a run from before it.
   *
   * This is the case that needed naming. When a runtime starts another run, `currentRunId` follows it and the old
   * outcome stops being shown, which is already right. When a runtime is REPLACED, the new one is idle and reports
   * no active run, so `currentRunId` falls back to the outcome this tab still holds and the previous runtime's
   * finished run is drawn as though it belonged to the new session. The old run's records are still true of the old
   * run; what is false is presenting them as the session's current state.
   */
  superseded: boolean;
}

/**
 * THE RUN THIS FOLD IS INSIDE BY ITS OWN RECORDS: the one whose `run.started` it folded and whose end it has not
 * (redesign verdict item 6). The runtime's snapshot names a new run only at its next poll, up to five seconds after the
 * stream delivered the run's start; until then this is the record that says which run is going on. Null when the fold
 * started no run, or holds the end of that run or of a later one.
 */
export function runInProgressByRecord(view: Pick<ViewState, 'run_started' | 'outcome'>): string | null {
  const started = view.run_started;
  if (started === null || typeof started.run_id !== 'string') return null;
  return outcomeIsOfFoldedRun(view) ? null : started.run_id;
}

/**
 * THE RUN IN PROGRESS, FROM THE NEWER OF THE TWO RECORDS THAT NAME ONE (redesign r4 verify-r1 must_fix 5), or null.
 *
 * The snapshot's `active_run` is a poll behind the stream. After Try again the runtime parks the arm (one run) and starts
 * the fresh attempt (another); the stream delivers the park's `run.finished` and the attempt's `run.started` while the
 * snapshot still names the park. So the fold's own run in progress wins when its `run.started` is newer than the one the
 * snapshot's run started at, and when the fold holds the end of the snapshot's run it is no longer in progress by the
 * record (then the fold's own run in progress, or none). Otherwise the snapshot's active run, then the fold's.
 */
export function activeRunOnRecord(view: Pick<ViewState, 'active_run' | 'run_started' | 'outcome'>): string | null {
  const byRecord = runInProgressByRecord(view);
  const active = view.active_run;
  if (active === null) return byRecord;
  if (byRecord !== null && byRecord !== active.run_id) {
    const startedSeq = view.run_started?.seq;
    if (typeof startedSeq === 'number' && startedSeq > active.started_seq) return byRecord;
  }
  return snapshotRunEndedByRecord(view) ? byRecord : active.run_id;
}

/**
 * True when the snapshot still names a run as active and the fold holds that run's end (its `run.finished`, or the
 * outcome the snapshot itself reported for it). The runtime writes a run's end before it lets go of the run
 * (`demo_runtime/supervisor.py`: the run.finished emit, then `_release`), and the snapshot is a poll behind the stream,
 * so for a moment the two disagree; the record of the end is the newer fact (redesign r4 verify-r2 must_fix 2).
 */
export function snapshotRunEndedByRecord(view: Pick<ViewState, 'active_run' | 'outcome'>): boolean {
  const active = view.active_run;
  if (active === null) return false;
  return view.outcome.run_id === active.run_id && (view.outcome.reported !== null || view.outcome.finished_seq !== null);
}

/**
 * The run the screen describes.
 *
 * The rule is the one `steps.ts` already applies to the result: the run in progress by the newer record
 * (`activeRunOnRecord`: the runtime's active run, or the fold's own newer run.started), and otherwise the run whose
 * outcome this tab holds. `runtimeReplaced` comes from the store, which is the only place that knows the session the
 * handshake named is not the session answering now.
 */
export function selectedRun(view: ViewState, runtimeReplaced = false): SelectedRun {
  const runId = activeRunOnRecord(view) ?? currentRunId(view);
  // The held outcome answers only for the run it belongs to; a run in progress by the record has none yet.
  const outcome = runId === currentRunId(view) ? resultForCurrentRun(view) : null;
  const activeId = view.active_run?.run_id ?? null;
  const live = runId !== null && activeId === runId;
  const superseded = runtimeReplaced && runId !== null;
  return { run_id: runId, outcome, live, finished: outcome !== null, superseded };
}

/**
 * Whether a figure derived from the LIVE fold may be used for the selected run.
 *
 * The live fold describes what this tab is watching now. For a run that has finished, a figure taken from it is a
 * statement about the session and not about that run, so it is refused and the caller says "not recorded" instead of
 * printing a number that belongs to something else. While the run is live the fold IS the run and it is allowed.
 */
export function liveFoldDescribes(selected: SelectedRun): boolean {
  return !selected.finished && !selected.superseded;
}

/**
 * The view the presentation should read for the selected run: the live fold when it watched that run, and otherwise
 * that run's OWN retained records, folded on their own.
 *
 * "Unavailable" is only correct when the records genuinely lack the answer. A tab that opened after a run ended
 * folded no `run.started`, and that is a fact about the TAB, not about the run: the runtime kept every record. So
 * where a hydrated fold of the run's own events exists, it answers, and nothing is inferred from a run id or
 * invented for a state the records do not contain.
 *
 * `hydrated` is used only when it is the SAME run. A fold of a different run is not an answer about this one.
 */
export function viewForSelectedRun(live: ViewState, selected: SelectedRun, hydrated: { run_id: string; view: ViewState } | null): ViewState {
  if (selected.run_id === null) return live;
  if (hydrated !== null && hydrated.run_id === selected.run_id && !liveFoldDescribes(selected)) return hydrated.view;
  // The live fold watched this run from its start, so it holds what a hydration would fetch.
  return live;
}

/** Whether this run's own records still need reading back. False while the live fold already describes it. */
export function needsHydration(selected: SelectedRun, live: ViewState, hydrated: { run_id: string } | null): boolean {
  if (selected.run_id === null || liveFoldDescribes(selected)) return false;
  if (hydrated !== null && hydrated.run_id === selected.run_id) return false;
  // Nothing to gain when the live fold already carries the record that dates the run: its `run.started`, which names
  // the run it started. A fold whose `run.started` names another run watched an earlier run, and the records of this one
  // (dropped for this reader, or written before it opened) are still to be read.
  const started = live.run_started;
  if (started === null) return true;
  return typeof started.run_id === 'string' && started.run_id !== selected.run_id;
}
