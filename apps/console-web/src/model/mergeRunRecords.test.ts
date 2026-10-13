// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RUN IN PROGRESS, READ BACK INTO A FOLD THAT JOINED IT LATE (redesign verdict item 5). A tab opened mid-run folds
// only what the stream delivers after the snapshot's cursor; the run's earlier records are read back from the runtime
// and merged by seq. The fold rebuilt that way must describe the run exactly as the fold that watched it from its start.

import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { buildMeasuringRunM2fc135a, buildStoppedHoldingRun } from '../api/syntheticScenarios';
import { foldEvents, initialState, mergeRunRecords } from './reducer';
import type { ViewState } from './types';

const T0 = Date.parse('2026-09-27T14:16:00Z');

/** The two runs of swap-then-stopped-holding, numbered as one runtime log numbers them, each with a wall time. */
function records(): { first: ContractEvent[]; second: ContractEvent[] } {
  const stamp = (event: ContractEvent, index: number): ContractEvent =>
    ({ ...event, t_wall_utc: event.t_wall_utc ?? new Date(T0 + index * 250).toISOString() }) as ContractEvent;
  const first = buildMeasuringRunM2fc135a(1, 0, 1_000_000_000).steps.map((step, index) => stamp(step.event, index));
  const last = first[first.length - 1] as ContractEvent;
  const second = buildStoppedHoldingRun(2, last.seq, last.t_mono_ns).steps.map((step, index) => stamp(step.event, first.length + index));
  return { first, second };
}

/** What a fold says about the run it is in: everything a stage, the card, the scene and Evidence read. */
function runDetail(view: ViewState): Record<string, unknown> {
  return {
    machine: view.machine,
    current_action: view.current_action,
    action_history: view.action_history,
    decision_frame: view.decision_frame,
    predicates: view.predicates,
    observations: view.observations,
    perception_by_observation: view.perception_by_observation,
    measured_state: view.measured_state,
    run_config: view.run_config,
    run_started: view.run_started,
    stop: view.stop,
    timings: view.timings,
    diagnostics: view.diagnostics,
    outcome: view.outcome,
    run_events: view.recent_events.filter((event) => event.run_id === view.run_started?.run_id).map((event) => event.seq),
  };
}

/** A tab that opened at `cursor`: it holds the snapshot's last outcome and folds the stream from there on. */
function joinedAt(cursor: number, before: readonly ContractEvent[], stream: readonly ContractEvent[]): ViewState {
  const finished = foldEvents(initialState(), before);
  const opened: ViewState = { ...initialState(), seq_cursor: cursor, outcome: finished.outcome, previous_outcome: finished.outcome };
  return foldEvents(opened, stream.filter((event) => event.seq > cursor));
}

describe('the records of a run in progress, merged into a fold that joined it late', () => {
  const { first, second } = records();
  const all = [...first, ...second];

  for (const at of [3, 10, 20]) {
    it(`describes the run as the fold that watched it, for a tab that joined ${String(at)} records into it`, () => {
      const cursor = (second[at] as ContractEvent).seq;
      const readUpTo = (second[at + 4] as ContractEvent).seq;
      const shown = second.filter((event) => event.seq <= readUpTo + 6);
      const watched = foldEvents(initialState(), [...first, ...shown]);
      const late = joinedAt(cursor, first, shown);
      expect(late.run_started, 'the stream never delivered the start').toBeNull();
      const merged = mergeRunRecords(late, second.filter((event) => event.seq <= readUpTo));
      expect(runDetail(merged)).toEqual(runDetail(watched));
      // Reading back states nothing the stream did not report: no gap, no duplicate, no restart.
      expect(merged.stream_gaps).toEqual(late.stream_gaps);
      expect(merged.counters).toEqual(late.counters);
      expect(merged.last_event_seq).toBe(watched.last_event_seq);
      // And the stream carries on from there exactly as it does for the tab that watched.
      const rest = second.filter((event) => event.seq > readUpTo + 6);
      expect(runDetail(foldEvents(merged, rest))).toEqual(runDetail(foldEvents(watched, rest)));
    });
  }

  it('changes nothing for a fold that watched the run from its start', () => {
    const watched = foldEvents(initialState(), all.slice(0, first.length + 12));
    expect(mergeRunRecords(watched, second.slice(0, 12))).toBe(watched);
  });

  it('changes nothing for a fold that has started a later run since the read was asked', () => {
    const later = foldEvents(initialState(), all);
    expect(mergeRunRecords(later, first)).toBe(later);
  });

  it('changes nothing for records that carry no start', () => {
    const late = joinedAt((second[10] as ContractEvent).seq, first, second);
    expect(mergeRunRecords(late, second.slice(5, 12))).toBe(late);
  });
});
