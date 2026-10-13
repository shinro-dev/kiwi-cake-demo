// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import runStarted from '@kiwi/demo-contracts/fixtures/event/run.started.json';
import runFinished from '@kiwi/demo-contracts/fixtures/event/run.finished.json';
import type { ContractEvent } from '../contracts/envelope';
import { applyEvent, applyResync, applySnapshot, initialState } from './reducer';
import { explainFailure } from './failure';
import { missionTarget, missionTime } from './mission';
import { activeRunOnRecord, liveFoldDescribes, needsHydration, selectedRun, viewForSelectedRun } from './selectedRun';
import type { PredicateView, ViewState } from './types';

const RUN = 'synthetic-run-0001';

function watching(): ViewState {
  return applyEvent(applySnapshot(initialState(), runningSnapshot as unknown as Snapshot), runStarted as unknown as ContractEvent);
}

function finished(): ViewState {
  return applyEvent(watching(), { ...runFinished, run_id: RUN } as unknown as ContractEvent);
}

function predicate(name: string, value: boolean | null): PredicateView {
  return { predicate: name, value, measured: null, threshold: null, unit: null, seq: 1, state: value === null ? 'not_applicable' : 'evaluated' } as unknown as PredicateView;
}

describe('which run the presentation describes', () => {
  it('is the live run while one is running', () => {
    const selected = selectedRun(watching());
    expect(selected.run_id).toBe(RUN);
    expect(selected.live).toBe(true);
    expect(selected.finished).toBe(false);
    expect(selected.superseded).toBe(false);
    expect(liveFoldDescribes(selected)).toBe(true);
  });

  it('is the run that ended, once it has ended', () => {
    const selected = selectedRun(finished());
    expect(selected.run_id).toBe(RUN);
    expect(selected.finished).toBe(true);
    expect(selected.outcome?.run_id).toBe(RUN);
    // The live fold now describes the SESSION and not this run, so figures taken from it are refused.
    expect(liveFoldDescribes(selected)).toBe(false);
  });

  it('follows the runtime when it starts ANOTHER run, and stops showing the old outcome', () => {
    const view = finished();
    const moved: ViewState = { ...view, active_run: { ...(view.active_run ?? {}), run_id: 'another-run-0002' } as ViewState['active_run'] };
    const selected = selectedRun(moved);
    expect(selected.run_id).toBe('another-run-0002');
    expect(selected.live).toBe(true);
    // The previous run's outcome is not the new run's result, and is not offered as one.
    expect(selected.outcome).toBeNull();
    expect(selected.finished).toBe(false);
  });

  it('says it has been superseded when the RUNTIME was replaced under this tab', () => {
    // A replaced runtime is idle and reports no active run, so currentRunId falls back to the outcome this tab still
    // holds, and the previous runtime's finished run would be drawn as the new session's state.
    const selected = selectedRun(finished(), true);
    expect(selected.run_id).toBe(RUN);
    expect(selected.superseded).toBe(true);
    expect(liveFoldDescribes(selected)).toBe(false);
  });

  it('names no run at all before anything has been seen', () => {
    const selected = selectedRun(initialState());
    expect(selected.run_id).toBeNull();
    expect(selected.finished).toBe(false);
  });
});

describe('the target answers for the selected run', () => {
  it("reads the run's own predicates rather than the live fold", () => {
    const view = finished();
    // The run's retained predicates say the cube was placed. The live fold holds nothing of the sort.
    const target = missionTarget(view, [predicate('placed', true)], false);
    expect(target.state).toBe('placed');
    expect(target.basis).toContain('placed predicate');
  });

  it('refuses to describe a finished run from this session, and says which it is', () => {
    const view = finished();
    // No predicate resolves where the cube finished, and the run has ended: the fallbacks describe the session.
    const target = missionTarget(view, [], false);
    expect(target.state).toBe('not_observed');
    expect(target.label).toBe('not recorded for this run');
    expect(target.basis).toContain('describes the session, not this run');
    // The words it must NOT reach for: they are about the live fold.
    expect(target.label).not.toBe('not located yet');
    expect(target.label).not.toBe('located');
  });

  it('still uses the live fold while the run is running', () => {
    const view = watching();
    const target = missionTarget(view, [], true);
    expect(target.state).toBe('not_located');
    expect(target.label).toBe('not located yet');
  });

  it('keeps its old behaviour for a caller that names no run', () => {
    expect(missionTarget(watching()).label).toBe('not located yet');
  });
});

// HYDRATION. A tab that opened after a run ended folded no run.started, and reading "unavailable" from that is a
// statement about the TAB. The runtime kept every record of that run, so they are read back and folded on their own.
describe('the selected run answers from its own records when this tab holds none', () => {
  const LATE = applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
  const hydratedView = () => applyEvent(watching(), { ...runFinished, run_id: RUN } as unknown as ContractEvent);

  it('asks for the records when the tab folded no start for a finished run', () => {
    const view = applyEvent(LATE, { ...runFinished, run_id: RUN } as unknown as ContractEvent);
    const selected = selectedRun(view);
    expect(selected.finished).toBe(true);
    expect(view.run_started).toBeNull();
    expect(needsHydration(selected, view, null)).toBe(true);
  });

  it('does not ask again once that run has been read', () => {
    const view = applyEvent(LATE, { ...runFinished, run_id: RUN } as unknown as ContractEvent);
    const selected = selectedRun(view);
    expect(needsHydration(selected, view, { run_id: RUN })).toBe(false);
    // A fold of a DIFFERENT run is not an answer about this one.
    expect(needsHydration(selected, view, { run_id: 'another-run-0002' })).toBe(true);
  });

  it('does not ask while the tab is watching the run itself', () => {
    const selected = selectedRun(watching());
    expect(needsHydration(selected, watching(), null)).toBe(false);
  });

  it('reads the figures from the hydrated fold, so the duration stops being unavailable', () => {
    const view = applyEvent(LATE, { ...runFinished, run_id: RUN } as unknown as ContractEvent);
    const selected = selectedRun(view);
    // Before: this tab has no start, so there is no duration to state.
    expect(missionTime(view, Date.parse('2026-01-01T00:02:00Z')).words).toBeNull();
    // After: the run's own records carry both readings and the duration is a subtraction inside the runtime's clock.
    const chosen = viewForSelectedRun(view, selected, { run_id: RUN, view: hydratedView() });
    const time = missionTime(chosen, Date.parse('2026-01-01T09:00:00Z'));
    expect(time.words).not.toBeNull();
    expect(time.running).toBe(false);
    expect(time.meaning).toContain("runtime's own wall clock");
  });

  it('never takes a fold of another run as this run answer', () => {
    const view = applyEvent(LATE, { ...runFinished, run_id: RUN } as unknown as ContractEvent);
    const selected = selectedRun(view);
    const chosen = viewForSelectedRun(view, selected, { run_id: 'another-run-0002', view: hydratedView() });
    expect(chosen).toBe(view);
  });

  it('keeps the live fold while the run is still going', () => {
    const live = watching();
    const selected = selectedRun(live);
    expect(viewForSelectedRun(live, selected, { run_id: RUN, view: hydratedView() })).toBe(live);
  });
});

describe('a newer run whose records this tab missed', () => {
  // The tab watched run A start and end, the runtime then dropped every record of run B for this reader (a declared gap),
  // and the poll names B as the newest finished run, which failed. B is the run on the screen, and its failure is B's.
  function missedRun(): ViewState {
    const watched = applyResync(finished(), { retained_from_seq: 1, missed: [20, 200], reason: null });
    const failedB = {
      outcome: 'failed',
      exit_status: 5,
      predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
      automatic_retries: 0,
      task_control_interventions: 0,
      evidence_complete: true,
      evidence_gaps: 0,
      reason_code: 'grasp_missed',
      phase: 'GRASP_CLOSE',
      failure: { detail: 'grasp_missed detail', measurements: [], observation_ids: [] },
    };
    const snapshot = { ...(runningSnapshot as unknown as Snapshot), seq_cursor: 200, active_run: null, last_outcome: { run_id: 'run-b', finished_seq: 200, result: failedB } };
    return applySnapshot(watched, snapshot as unknown as Snapshot, 0);
  }

  it('is the run on the screen, and its records are read back', () => {
    const view = missedRun();
    const selected = selectedRun(view);
    expect(selected.run_id).toBe('run-b');
    expect(needsHydration(selected, view, null)).toBe(true);
  });

  it('has its failure explained as its own, before its records are read back', () => {
    const explained = explainFailure(missedRun());
    expect(explained?.run_id).toBe('run-b');
    expect(explained?.reason_code).toBe('grasp_missed');
  });
});

describe('the run in progress by the newer record (verify-r1 must_fix 5)', () => {
  it('is the fold\'s own newer run.started while the snapshot still names the run before it', () => {
    // The snapshot names RUN (the park); the stream delivered its end and the fresh attempt's start after it.
    const view = watching();
    const startedSeq = view.active_run?.started_seq ?? 0;
    const ended = applyEvent(view, { ...runFinished, run_id: RUN, seq: startedSeq + 10 } as unknown as ContractEvent);
    const next = applyEvent(ended, { ...runStarted, run_id: 'synthetic-run-0003', seq: startedSeq + 11 } as unknown as ContractEvent);
    expect(next.active_run?.run_id).toBe(RUN);
    expect(activeRunOnRecord(next)).toBe('synthetic-run-0003');
    const selected = selectedRun(next);
    expect(selected.run_id).toBe('synthetic-run-0003');
    expect(selected.finished).toBe(false);
  });

  it('is none once the fold holds the end of the run the snapshot still names, and the screen describes that ended run', () => {
    const view = finished();
    expect(view.active_run?.run_id).toBe(RUN);
    expect(activeRunOnRecord(view)).toBeNull();
    expect(selectedRun(view).run_id).toBe(RUN);
    expect(selectedRun(view).finished).toBe(true);
  });

  it('is the snapshot\'s active run while the fold holds nothing newer', () => {
    expect(activeRunOnRecord(watching())).toBe(RUN);
  });
});
