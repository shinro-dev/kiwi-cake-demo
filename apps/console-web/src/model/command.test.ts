// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SCAN IS NOT A CYCLE EITHER. scan_scene (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0b) moves the arm
// through pan-only views and puts it back at the low scan pose; it grasps nothing and places nothing, so the placement
// predicates do not decide it and finishing it does not complete the Run step. It is still a motion command, and a scan
// the controller ended badly is still explained.

import { describe, expect, it } from 'vitest';
import runStarted from '@kiwi/demo-contracts/fixtures/event/run.started.json';
import runFinished from '@kiwi/demo-contracts/fixtures/event/run.finished.json';
import type { ContractEvent } from '../contracts/envelope';
import { commandScope } from './command';
import { explainFailure } from './failure';
import { applyEvent, initialState } from './reducer';
import type { ViewState } from './types';

const RUN = 'run-scan-0001';

function started(commandType: string, seq: number): ContractEvent {
  const base = runStarted as unknown as ContractEvent;
  return { ...base, run_id: RUN, seq, payload: { ...(base.payload as object), command_type: commandType } } as ContractEvent;
}

function finished(seq: number, outcome: string, exitStatus: number, reasonCode: string | null): ContractEvent {
  const base = runFinished as unknown as ContractEvent;
  return {
    ...base,
    run_id: RUN,
    seq,
    payload: {
      ...(base.payload as object),
      outcome,
      exit_status: exitStatus,
      predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
      reason_code: reasonCode,
      failure: null,
    },
  } as ContractEvent;
}

function afterAScan(outcome = 'unknown', exitStatus = 0, reasonCode: string | null = null): ViewState {
  let state = initialState();
  for (const event of [started('scan_scene', 1), finished(2, outcome, exitStatus, reasonCode)]) state = applyEvent(state, event);
  return state;
}

describe('a scan of the scene', () => {
  it('is named from the record and asks nothing of the placement predicates', () => {
    const scope = commandScope(afterAScan());
    expect(scope.command_type).toBe('scan_scene');
    expect(scope.kind).toBe('scan');
    expect(scope.source).toBe('run_started');
    expect(scope.completes_run).toBe(false);
    expect(scope.requires_placement).toBe(false);
    expect(scope.label).toBe('scan of the scene');
  });

  it('draws no failure for a scan that ended well, and explains one that did not', () => {
    const good = afterAScan();
    expect(explainFailure(good, commandScope(good))).toBeNull();
    const stopped = afterAScan('stopped', 4, 'operator_abort');
    expect(explainFailure(stopped, commandScope(stopped))).not.toBeNull();
  });

  it('is read off the history for a reloaded tab like every other type', () => {
    let state = initialState();
    state = applyEvent(state, finished(2, 'unknown', 0, null));
    expect(commandScope(state, (id) => (id === RUN ? 'scan_scene' : null)).kind).toBe('scan');
  });
});
