// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE FOUR THINGS A REAL NON-MOTION OBSERVE GOT WRONG, held one by one.
//
// An Observe ran on hardware. It moved nothing, it captured a frame and it recorded the whole pose the controller
// read. The console then marked Run done, advanced to Result, drew an UNKNOWN FAILURE over it, printed "the host is
// not reporting measurements" over a measurement it was holding, and put "7 min 59 s ago" beside "lines are
// arriving". Every case below is one of those, and every one of them is decided from data that already existed.

import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import observeSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/an-observe-recorded-a-pose-while-no-tap-is-configured.json';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import measuredState from '@kiwi/demo-contracts/fixtures/event/measured.state.json';
import runStarted from '@kiwi/demo-contracts/fixtures/event/run.started.json';
import runFinished from '@kiwi/demo-contracts/fixtures/event/run.finished.json';
import capturedFrame from '@kiwi/demo-contracts/fixtures/event/observation.captured.json';
import type { ContractEvent } from '../contracts/envelope';
import { commandScope, UNSCOPED_COMMAND } from './command';
import { explainFailure } from './failure';
import { decideBySeq, sameSession } from './merge';
import { measurementReading } from './measurement';
import { applyEvent, applySnapshot, initialState } from './reducer';
import { currentStep, stepViews } from './steps';
import { stillReading } from './still';
import type { ViewState } from './types';

const RUN = 'run-observe-0001';
const NOW = Date.parse('2026-09-21T21:17:28Z');

/** The `run.started` of a command of a given type, so the fold knows what it is looking at. */
function started(commandType: string, seq: number): ContractEvent {
  const base = runStarted as unknown as ContractEvent;
  return { ...base, run_id: RUN, seq, payload: { ...(base.payload as object), command_type: commandType } } as ContractEvent;
}

/** The `run.finished` of the real Observe: exit 0, outcome unknown, every placement predicate null. */
function observeFinished(seq: number): ContractEvent {
  const base = runFinished as unknown as ContractEvent;
  return {
    ...base,
    run_id: RUN,
    seq,
    payload: {
      ...(base.payload as object),
      outcome: 'unknown',
      exit_status: 0,
      predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
      reason_code: null,
      failure: null,
    },
  } as ContractEvent;
}

function captured(seq: number): ContractEvent {
  return { ...(capturedFrame as unknown as ContractEvent), run_id: RUN, seq } as ContractEvent;
}

function measured(seq: number, pan: number, wallUtc: string): ContractEvent {
  const base = measuredState as unknown as ContractEvent;
  return {
    ...base,
    run_id: RUN,
    seq,
    t_wall_utc: wallUtc,
    payload: { ...(base.payload as object), joints: { ...((base.payload as { joints: object }).joints), shoulder_pan: pan } },
  } as ContractEvent;
}

/** The fold a tab has after watching one Observe go past: started, captured, measured, finished. */
function afterAnObserve(commandType = 'observe'): ViewState {
  let state = initialState();
  for (const event of [started(commandType, 1), captured(4), measured(5, -9.582, '2026-09-21T21:09:29.150910Z'), observeFinished(6)]) {
    state = applyEvent(state, event);
  }
  return state;
}

describe('an observe is not a cycle', () => {
  it('reads the command type off the record rather than inferring it from the outcome word', () => {
    const scope = commandScope(afterAnObserve());
    expect(scope.command_type).toBe('observe');
    expect(scope.kind).toBe('observe');
    expect(scope.source).toBe('run_started');
    expect(scope.completes_run).toBe(false);
    expect(scope.requires_placement).toBe(false);
  });

  it('leaves Run as the step to press and never completes it or advances to Result', () => {
    const state = afterAnObserve();
    const scope = commandScope(state);
    expect(currentStep(state, true, scope)).toBe(3);
    const steps = stepViews(state, true, null, scope);
    expect(steps[2]?.state).toBe('current');
    expect(steps[3]?.state).toBe('waiting');
    expect(steps[3]?.state).not.toBe('done');
  });

  it('draws no failure at all for an observe that did what it was asked to do', () => {
    const state = afterAnObserve();
    expect(explainFailure(state, commandScope(state))).toBeNull();
  });

  it('still explains an observe the controller really did end badly', () => {
    let state = afterAnObserve();
    state = applyEvent(state, {
      ...observeFinished(7),
      payload: { ...(observeFinished(7).payload as object), outcome: 'failed', exit_status: 1, reason_code: 'search_no_cube' },
    } as ContractEvent);
    expect(explainFailure(state, commandScope(state))).not.toBeNull();
  });

  it('keeps a cycle exactly as strict: the same record with a cycle command type completes Run and is a failure', () => {
    // The one case the scoping must never relax. Same outcome word, same absent predicates, same exit status 0: a
    // cycle that cannot say what became of the cube is not a pass, and exit 0 is never read as placement success.
    const state = afterAnObserve('start_cycle');
    const scope = commandScope(state);
    expect(scope.kind).toBe('cycle');
    expect(scope.requires_placement).toBe(true);
    expect(currentStep(state, true, scope)).toBe(4);
    expect(explainFailure(state, scope)).not.toBeNull();
    expect(state.outcome.confirmed).toBeNull();
  });

  it('holds a command it cannot name to the cycle reading, so an unnamed type relaxes nothing', () => {
    let state = initialState();
    state = applyEvent(state, observeFinished(6));
    const scope = commandScope(state);
    expect(scope.source).toBeNull();
    expect(scope.requires_placement).toBe(true);
    expect(currentStep(state, true, scope)).toBe(4);
    expect(explainFailure(state, scope)).not.toBeNull();
    expect(explainFailure(state, UNSCOPED_COMMAND)).not.toBeNull();
  });

  it('reads the type off the history for a reloaded tab, which folded no run.started at all', () => {
    // A reload starts from a snapshot and no events. The finished-run list is the third place the type is written.
    const state = applySnapshot(initialState(), observeSnapshot as unknown as Snapshot, NOW);
    const runId = state.outcome.run_id;
    expect(runId).not.toBeNull();
    const scope = commandScope(state, (id) => (id === runId ? 'observe' : null));
    expect(scope.source).toBe('history');
    expect(scope.completes_run).toBe(false);
    expect(currentStep(state, true, scope)).not.toBe(4);
    expect(explainFailure(state, scope)).toBeNull();
  });
});

describe('the measurement that was already recorded', () => {
  it('shows the pose the observe recorded, with its source and an age from its own recording time', () => {
    const reading = measurementReading(afterAnObserve(), NOW);
    expect(reading.state?.joints['shoulder_pan']).toBe(-9.582);
    expect(reading.from).toBe('folded');
    expect(reading.age_ms).not.toBeNull();
    expect(reading.sentence).toContain('not a live feed');
  });

  it('survives a reload with no events folded, through the snapshot property', () => {
    // The whole point of the contract addition: a tab that folded nothing still has the measurement.
    const state = applySnapshot(initialState(), observeSnapshot as unknown as Snapshot, NOW);
    expect(state.measured_state).toBeNull();
    const reading = measurementReading(state, NOW);
    expect(reading.from).toBe('snapshot');
    expect(reading.state?.joints['shoulder_pan']).toBe(-9.582);
    expect(reading.state?.joints['gripper']).toBe(79.848);
    expect(reading.state?.base_velocity).toEqual({ x_mps: 0, y_mps: 0, theta_dps: 0 });
    expect(reading.state?.seq).toBe(5);
    expect(reading.age_ms).toBeGreaterThan(0);
  });

  it('says so plainly when no controller has read the robot, without naming the tap as the reason', () => {
    const reading = measurementReading(initialState(), NOW);
    expect(reading.state).toBeNull();
    expect(reading.sentence).toContain('No controller has read the robot');
    expect(reading.sentence).not.toContain('tap_not_configured');
  });
});

describe('the merge decides by ordering and identity, in both directions', () => {
  it('lets a snapshot naming a later record beat a record folded earlier', () => {
    expect(decideBySeq({ seq: 5 }, { seq: 300 })).toEqual({ value: { seq: 300 }, from: 'snapshot' });
  });

  it('lets a record folded since beat a snapshot that was delayed in flight', () => {
    expect(decideBySeq({ seq: 340 }, { seq: 300 })).toEqual({ value: { seq: 340 }, from: 'folded' });
  });

  it('takes the fold for the same record described twice, and takes whichever source has anything at all', () => {
    expect(decideBySeq({ seq: 9 }, { seq: 9 }).from).toBe('folded');
    expect(decideBySeq(null, { seq: 9 }).from).toBe('snapshot');
    expect(decideBySeq({ seq: 9 }, null).from).toBe('folded');
    expect(decideBySeq(null, null)).toEqual({ value: null, from: null });
  });

  it('refuses to compare sequence numbers across two runtime sessions', () => {
    expect(sameSession('s-1', 's-1')).toBe(true);
    expect(sameSession('s-1', 's-2')).toBe(false);
    expect(sameSession(null, 's-2')).toBe(false);
  });

  it('applies the ordering to the measurement in both directions', () => {
    const snapshot = { ...(observeSnapshot as unknown as Snapshot) };
    // A snapshot naming record 5 against a fold that has already taken record 40.
    let state = applySnapshot(initialState(), { ...snapshot, seq_cursor: 6 }, NOW);
    state = applyEvent(state, measured(40, -1.111, '2026-09-21T21:16:00Z'));
    expect(measurementReading(state, NOW).from).toBe('folded');
    expect(measurementReading(state, NOW).state?.joints['shoulder_pan']).toBe(-1.111);

    // The other direction: a fold holding record 2 and a snapshot naming record 5.
    let older = applyEvent(initialState(), measured(2, -7.777, '2026-09-21T21:00:00Z'));
    older = applySnapshot(older, { ...snapshot, seq_cursor: 6 }, NOW);
    expect(measurementReading(older, NOW).from).toBe('snapshot');
    expect(measurementReading(older, NOW).state?.joints['shoulder_pan']).toBe(-9.582);
  });

  it('applies the same ordering to the frame, so a stale folded capture cannot hide a newer snapshot one', () => {
    const folded = {
      observation_id: 'obs-old',
      source_role: 'wrist' as const,
      media: 'frame' as const,
      width_px: 1280,
      height_px: 720,
      content_digest: null,
      source_stamp: null,
      retained: true,
      seq: 5,
      t_wall_utc: '2026-09-21T21:00:00Z',
    };
    const newer = { ...folded, observation_id: 'obs-new', seq: 300 };
    expect(stillReading({ observations: { 'obs-old': folded }, snapshot_observation: newer, evidence_gaps: [], image_failed_for: null })).toMatchObject({
      state: 'shown',
      from: 'snapshot',
    });
    expect(
      stillReading({ observations: { 'obs-new': newer }, snapshot_observation: folded, evidence_gaps: [], image_failed_for: null }),
    ).toMatchObject({ state: 'shown', from: 'folded' });
  });

  it('resets rather than merging when a snapshot names a different runtime session', () => {
    // Another log. Its record 5 has no relation to this one's record 40, so nothing is compared: the folded half is
    // cleared and the new session's own answer stands alone.
    let state = applySnapshot(initialState(), runningSnapshot as unknown as Snapshot, NOW);
    state = applyEvent(state, measured(40, -1.111, '2026-09-21T21:16:00Z'));
    expect(state.measured_state?.seq).toBe(40);
    const replaced = applySnapshot(state, { ...(observeSnapshot as unknown as Snapshot), runtime_session: 'another-session-0002' }, NOW);
    expect(replaced.measured_state).toBeNull();
    expect(replaced.counters.runtime_restarts).toBe(1);
    expect(measurementReading(replaced, NOW).from).toBe('snapshot');
    expect(measurementReading(replaced, NOW).state?.seq).toBe(5);
  });
});
