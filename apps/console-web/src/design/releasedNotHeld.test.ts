// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A RUN THAT RELEASED THE BLOCK IS NOT HELD.
//
// History's "The last run's outcome" said "Held, not placed" for a run that released the block and whose gripper is
// empty, because the headline was decided from `placed` false and `object_secured` true alone. It showed for the
// m915d3e9 v2 record (mission_earlier_target_disturbed) and for every v3 mission_placed_no_cube_at_release_point
// refusal, while the same drawer said "Gripper empty" and drew RELEASED in the timeline. These pin the headline to the
// release: RELEASED reached, a stop phase at or after it, or a gripper-empty stop state is not Held.

import { describe, expect, it } from 'vitest';
import runFinished from '@kiwi/demo-contracts/fixtures/event/run.finished.failed-with-a-reason.json';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import stateTransition from '@kiwi/demo-contracts/fixtures/event/state.transition.json';
import type { Snapshot } from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import { SyntheticSource } from '../api/synthetic';
import { commandScope } from '../model/command';
import type { MissionRecord } from '../model/missionRecord';
import { applyEvent, applySnapshot, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { lastRunLine, runSummary } from './runSummary';

const RUN = 'synthetic-run-0001';

interface Case {
  code: string;
  measurements: { name: string; value: number | boolean; unit: string | null }[];
}

// The m915d3e9 live v2 record's refusal and a v3 refusal of the pad policy, with the values those records carry.
const M915_V2: Case = {
  code: 'mission_earlier_target_disturbed',
  measurements: [
    { name: 'unclaimed_placed_count', value: 1, unit: null },
    { name: 'earlier_targets_undisturbed', value: false, unit: null },
    { name: 'predicted_worst_case_centre_margin_mm', value: 40.567476140640466, unit: 'mm' },
  ],
};
const V3_REFUSAL: Case = {
  code: 'mission_placed_no_cube_at_release_point',
  measurements: [
    { name: 'on_pad_count', value: 2, unit: null },
    { name: 'release_distance_mm', value: 41.3, unit: 'mm' },
    { name: 'release_tolerance_mm', value: 26.0, unit: 'mm' },
  ],
};

function finishedEvent(item: Case, phase: string): ContractEvent {
  const base = runFinished as unknown as { payload: Record<string, unknown> };
  return {
    ...(runFinished as unknown as ContractEvent),
    run_id: RUN,
    seq: 900,
    reason_code: item.code,
    payload: {
      ...base.payload,
      reason_code: item.code,
      phase,
      predicates: { object_secured: true, placed: false, clearance_target_met: false, base_returned: true },
      failure: { detail: `PLACED not demonstrated for this mission target (${item.code})`, measurements: item.measurements, observation_ids: [] },
    },
  } as unknown as ContractEvent;
}

function finishedView(item: Case, phase: string): ViewState {
  const running = applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
  const view = applyEvent(running, finishedEvent(item, phase));
  return { ...view, data_mode: 'live' } as ViewState;
}

function transition(to: string, seq: number): ContractEvent {
  const base = stateTransition as unknown as { payload: Record<string, unknown> };
  return { ...(stateTransition as unknown as ContractEvent), run_id: RUN, seq, payload: { ...base.payload, to_state: to } } as unknown as ContractEvent;
}

function mission(item: Case, holding: boolean | null): MissionRecord {
  return {
    mission_record_version: '1',
    mission_id: 'mission-f9ac04941f32',
    state: 'FAILED',
    data_mode: 'live',
    performs_motion: true,
    execution_label: 'proven-single-cube-controller',
    started_wall_utc: '2026-09-28T17:37:59Z',
    selected_target: 'cube',
    waiting_reason: null,
    detail: `run_outcome:failed:${item.code}`,
    targets: [
      {
        target_id: 'cube',
        label: 'the block',
        reachable: true,
        requires_capability: 'auto',
        state: 'FAILED',
        runs: [RUN],
        attempts: [
          {
            attempt: 1, run_id: RUN, park_run_id: null, outcome_class: 'fatal', reason_code: item.code, phase: null,
            measurements: item.measurements, observation_ids: [], stop_state: { holding, base_ahead: false, moved: true },
            next_actions: [], budget_left: 1, t_wall_utc: '2026-09-28T17:44:22Z',
          },
        ],
      },
    ],
  } as unknown as MissionRecord;
}

function headline(view: ViewState, events: ContractEvent[], record: MissionRecord | null): string {
  const store = new ConsoleStore(new SyntheticSource());
  const snap: StoreSnapshot = { ...store.getSnapshot(), view, mission: record, missionRunEvents: { [RUN]: events } };
  const summary = runSummary(snap, commandScope(view, () => null), record);
  if (summary === null) throw new Error('no summary');
  return lastRunLine(summary);
}

describe('History does not call a released block held', () => {
  for (const [name, item] of [['the m915d3e9 v2 record', M915_V2], ['a v3 no-block-at-the-release-point refusal', V3_REFUSAL]] as const) {
    describe(name, () => {
      it('says released, not held, when the run reached RELEASED and stopped after it', () => {
        const events = ['SEARCH', 'RELEASE_ADMISSION', 'RELEASED', 'PLACED', 'BASE_RETURN', 'BASE_RETURNED'].map((to, i) => transition(to, 800 + i));
        const line = headline(finishedView(item, 'BASE_RETURNED'), events, mission(item, false));
        expect(line).not.toContain('Held');
        expect(line).toContain('Released, not placed');
      });

      it('says released from the stop phase alone when the run events are not read', () => {
        const line = headline(finishedView(item, 'BASE_RETURNED'), [], null);
        expect(line).not.toContain('Held');
        expect(line).toContain('Released, not placed');
      });

      it('says released from a gripper-empty stop state alone', () => {
        const line = headline(finishedView(item, 'UNSTATED_PHASE'), [], mission(item, false));
        expect(line).not.toContain('Held');
        expect(line).toContain('Released, not placed');
      });
    });
  }

  it('still says held for a run that secured the block and stopped before any release', () => {
    const events = ['SEARCH', 'GRASP', 'HOLD_OBSERVE'].map((to, i) => transition(to, 800 + i));
    const line = headline(finishedView(V3_REFUSAL, 'HOLD_OBSERVE'), events, mission(V3_REFUSAL, true));
    expect(line).toContain('Held, not placed');
  });
});
