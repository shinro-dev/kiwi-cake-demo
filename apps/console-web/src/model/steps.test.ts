// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ONE THING THIS SCREEN MUST NOT DO: show the result of one run as the result of another.
//
// A live success followed by a failure is the case that costs the most, because both halves look right on their own.
// The success is real, the failure is real, and the only wrong thing is which run the screen attributes the success
// to. These tests hold that attribution at every point it could slip: while the second run is in flight, after the
// second run reports, when a snapshot built BEFORE the failure arrives after it, and after a reload that starts from
// a snapshot with no events behind it at all.

import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import failedWithAReason from '@kiwi/demo-contracts/fixtures/event/run.finished.failed-with-a-reason.json';
import placedNew from '@kiwi/demo-contracts/fixtures/event/run.finished.json';
import placedShort from '@kiwi/demo-contracts/fixtures/event/run.finished.placed-short-of-the-clearance-target.json';
import type { ContractEvent } from '../contracts/envelope';
import { applyEvent, applySnapshot, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { currentAction, currentRunId, currentStep, placementPlanWords, previousRun, readiness, resultForCurrentRun, runIsActive, stateFields, telemetryAgeMs } from './steps';

const NOW = 5_000_000;
const base = runningSnapshot as unknown as Snapshot;

/** The success of the run BEFORE the one that is active, which is what the runtime puts in `last_outcome`. */
const SUCCESS_RUN = 'run-a';
const FAILED_RUN = 'run-b';

function finished(fixture: unknown, runId: string, seq: number): ContractEvent {
  return { ...(fixture as ContractEvent), run_id: runId, seq } as ContractEvent;
}

function snapshotWith(patch: Partial<Snapshot>, seqCursor: number): Snapshot {
  return { ...base, seq_cursor: seqCursor, ...patch } as Snapshot;
}

const successOutcome = (fixture: unknown): Snapshot['last_outcome'] => ({
  run_id: SUCCESS_RUN,
  finished_seq: 10,
  result: (fixture as { payload: unknown }).payload as never,
  finished_wall_utc: '2026-01-01T00:00:00Z',
});

describe('a result is scoped to the run it belongs to', () => {
  it('does not show the previous success as the result of the run in flight', () => {
    // The live half: this tab watched run A succeed.
    let view: ViewState = applySnapshot(initialState(), snapshotWith({ active_run: null, last_outcome: null }, 5), NOW);
    view = applyEvent(view, finished(placedNew, SUCCESS_RUN, 10));
    expect(resultForCurrentRun(view)?.reported).toBe('placed_new');

    // Run B starts. The runtime's snapshot still carries A as the last FINISHED run, because it is.
    view = applySnapshot(
      view,
      snapshotWith({ active_run: { ...base.active_run!, run_id: FAILED_RUN }, last_outcome: successOutcome(placedNew) }, 20),
      NOW,
    );
    expect(currentRunId(view)).toBe(FAILED_RUN);
    expect(runIsActive(view)).toBe(true);
    // The run on the screen has not reported anything, so there is nothing to show as its result.
    expect(resultForCurrentRun(view)).toBeNull();
    // A shown as what it is: the previous run, with the time it finished.
    expect(previousRun(view)?.run_id).toBe(SUCCESS_RUN);
    expect(previousRun(view)?.finished_wall_utc).toBe('2026-01-01T00:00:00Z');
    expect(currentStep(view, true)).toBe(3);
  });

  it('displays the FAILURE once the second run reports, and the success as the previous run', () => {
    let view: ViewState = applySnapshot(initialState(), snapshotWith({ active_run: null, last_outcome: null }, 5), NOW);
    view = applyEvent(view, finished(placedNew, SUCCESS_RUN, 10));
    view = applySnapshot(
      view,
      snapshotWith({ active_run: { ...base.active_run!, run_id: FAILED_RUN }, last_outcome: successOutcome(placedNew) }, 20),
      NOW,
    );
    view = applyEvent(view, finished(failedWithAReason, FAILED_RUN, 47));

    const result = resultForCurrentRun(view);
    expect(result?.run_id).toBe(FAILED_RUN);
    expect(result?.reported).toBe('failed');
    expect(result?.confirmed).toBeNull();
    expect(previousRun(view)?.reported).toBe('placed_new');
    expect(currentStep(view, true)).toBe(4);
    expect(currentAction(view, true).phrase).toBe('Run finished');
  });

  it('keeps the failure when a snapshot built BEFORE it arrives after it', () => {
    // The 5 s poll races the event stream, so a snapshot whose cursor is not behind the fold can still carry the
    // older `last_outcome`. It used to replace the failure with the earlier success, because the run ids differed.
    let view: ViewState = applySnapshot(initialState(), snapshotWith({ active_run: null, last_outcome: null }, 5), NOW);
    view = applyEvent(view, finished(placedNew, SUCCESS_RUN, 10));
    view = applyEvent(view, finished(failedWithAReason, FAILED_RUN, 47));
    view = applySnapshot(view, snapshotWith({ active_run: null, last_outcome: successOutcome(placedNew) }, 47), NOW);

    expect(resultForCurrentRun(view)?.reported).toBe('failed');
    expect(resultForCurrentRun(view)?.run_id).toBe(FAILED_RUN);
  });

  it('shows the failure after a reload that starts from a snapshot and has folded no events at all', () => {
    // A reloaded tab has no event history. Everything it knows comes from one snapshot, whose `last_outcome` is now
    // run B's failure, because B is the most recent run to finish.
    const reloaded = applySnapshot(
      initialState(),
      snapshotWith(
        {
          active_run: null,
          last_outcome: {
            run_id: FAILED_RUN,
            finished_seq: 47,
            result: (failedWithAReason as { payload: unknown }).payload as never,
            finished_wall_utc: '2026-01-01T00:10:00Z',
          },
        },
        50,
      ),
      NOW,
    );
    expect(currentRunId(reloaded)).toBe(FAILED_RUN);
    expect(resultForCurrentRun(reloaded)?.reported).toBe('failed');
    expect(resultForCurrentRun(reloaded)?.reason_code).toBe('search_placement_infeasible');
    // Nothing on that screen may be attributed to the earlier success: it is the same run, so there is no previous
    // run to draw beside it.
    expect(previousRun(reloaded)).toBeNull();
    expect(currentStep(reloaded, true)).toBe(4);
  });

  it('never carries an earlier run’s predicates into a run that reported none', () => {
    let view: ViewState = applySnapshot(initialState(), snapshotWith({ active_run: null, last_outcome: null }, 5), NOW);
    view = applyEvent(view, finished(placedNew, SUCCESS_RUN, 10));
    view = applyEvent(view, finished(failedWithAReason, FAILED_RUN, 47));
    const predicates = resultForCurrentRun(view)?.predicates;
    expect(predicates?.object_secured).toBeNull();
    expect(predicates?.placed).toBeNull();
  });

  it('keeps a placement that missed the clearance target across a reload, as that run’s own result', () => {
    // The 2026-09-22 shape. A reloaded tab folds no events: everything it shows comes from the snapshot, and what it
    // must NOT do is round a placement that missed its quality target back to `unknown`, or attribute it elsewhere.
    const SHORT_RUN = 'run-c';
    const reloaded = applySnapshot(
      initialState(),
      snapshotWith(
        {
          active_run: null,
          last_outcome: {
            run_id: SHORT_RUN,
            finished_seq: 1162,
            result: (placedShort as { payload: unknown }).payload as never,
            finished_wall_utc: '2026-01-01T00:20:00Z',
          },
        },
        1200,
      ),
      NOW,
    );
    const result = resultForCurrentRun(reloaded);
    expect(currentRunId(reloaded)).toBe(SHORT_RUN);
    expect(result?.run_id).toBe(SHORT_RUN);
    expect(result?.reported).toBe('placed_new');
    expect(result?.confirmed).toBeNull();
    expect(result?.predicates?.clearance_target_met).toBe(false);
    expect(result?.predicates?.placed).toBe(true);
    expect(result?.predicates?.base_returned).toBe(true);
    expect(previousRun(reloaded)).toBeNull();
    expect(currentStep(reloaded, true)).toBe(4);
  });

  it('shows a later short placement as the current result and the earlier full success as the previous run', () => {
    const SHORT_RUN = 'run-c';
    let view: ViewState = applySnapshot(initialState(), snapshotWith({ active_run: null, last_outcome: null }, 5), NOW);
    view = applyEvent(view, finished(placedNew, SUCCESS_RUN, 10));
    // The previous run is what the RUNTIME names in last_outcome, not whatever this tab happens to have folded.
    view = applySnapshot(
      view,
      snapshotWith({ active_run: { ...base.active_run!, run_id: SHORT_RUN }, last_outcome: successOutcome(placedNew) }, 20),
      NOW,
    );
    view = applyEvent(view, finished(placedShort, SHORT_RUN, 1162));

    expect(resultForCurrentRun(view)?.run_id).toBe(SHORT_RUN);
    expect(resultForCurrentRun(view)?.confirmed).toBeNull();
    expect(resultForCurrentRun(view)?.predicates?.clearance_target_met).toBe(false);
    // The earlier run met all four and stays confirmed: one run's miss never reaches back over another's record.
    expect(previousRun(view)?.run_id).toBe(SUCCESS_RUN);
    expect(previousRun(view)?.confirmed).toBe('placed_new');
  });
});

describe('the step the console is on is derived from the record', () => {
  it('is Connect while the runtime reports a blocker, and never draws an unknown capability as ready', () => {
    const view = applySnapshot(
      initialState(),
      snapshotWith(
        {
          active_run: null,
          last_outcome: null,
          capabilities: [
            {
              id: 'start_cycle',
              state: 'unknown',
              reason_code: 'robot_readiness_not_read',
              reason: 'no reading about the robot has come back in this runtime session yet',
              dependency: 'robot',
            } as never,
          ],
        },
        5,
      ),
      NOW,
    );
    const answer = readiness(view, NOW, false);
    expect(answer.ready).toBe(false);
    expect(answer.code).toBe('robot_readiness_not_read');
    expect(currentStep(view, answer.ready)).toBe(1);
  });

  it('is Observe once a cycle may be commanded and nothing has been looked at', () => {
    const view = applySnapshot(initialState(), snapshotWith({ active_run: null, last_outcome: null }, 5), NOW);
    expect(currentStep(view, true)).toBe(2);
  });
});

describe('staleness advances when the messages stop', () => {
  it('grows with the clock rather than with arrivals, and is null before any line has arrived', () => {
    expect(telemetryAgeMs({ tap_received_at_ms: null, preview_received_at_ms: null }, NOW)).toBeNull();
    expect(telemetryAgeMs({ tap_received_at_ms: NOW - 9000, preview_received_at_ms: null }, NOW)).toBe(9000);
    expect(telemetryAgeMs({ tap_received_at_ms: NOW - 9000, preview_received_at_ms: null }, NOW + 1000)).toBe(10_000);
  });
});

describe('the sweep and the selection on the current action card', () => {
  function stepEvent(kind: 'state.transition' | 'diagnostic.text', seq: number, payload: Record<string, unknown>): ContractEvent {
    return {
      schema_version: '1', seq, run_id: 'run-b', action_id: null, observation_id: null, kind,
      producer: { name: 'test', version: '0', artifact_digest: null, contract_version: '1' },
      source_session: 'session-s', t_mono_ns: seq * 1_000_000, clock_domain: 'laptop_monotonic', t_wall_utc: null, reason_code: null, provenance: null,
      payload,
    } as unknown as ContractEvent;
  }
  const running = applySnapshot(initialState(), snapshotWith({ active_run: { ...base.active_run!, run_id: 'run-b' }, last_outcome: null }, 20), NOW);

  it('titles the sweep as scanning for the block and shows the sector from the controller state line', () => {
    let view = applyEvent(running, stepEvent('state.transition', 21, { machine: 'cycle', from_state: 'SEARCH', to_state: 'SEARCH_SWEEP' }));
    // The controller's [STATE] line travels as ONE diagnostic.text record with the state's fields (run_single_cube_cycle.py).
    view = applyEvent(view, stepEvent('diagnostic.text', 22, { stream: 'stdout', level: 'info', text: '[STATE] SEARCH_SWEEP {"sector": "LEFT", "pan_deg": -30.0, "outcome": "no_cube"}', redacted: false }));
    const action = currentAction(view, true);
    expect(action.phrase).toBe('Scanning for the block');
    expect(action.detail).toBe('sector LEFT, pan -30 deg, outcome no_cube');
    expect(action.state).toBe('SEARCH_SWEEP');
    expect(stateFields(view, 'SEARCH_SWEEP')).toEqual({ sector: 'LEFT', pan_deg: -30, outcome: 'no_cube' });
  });

  it('reads the newest line for the state, and says the sector is not stated when no line carries it', () => {
    let view = applyEvent(running, stepEvent('state.transition', 21, { machine: 'cycle', from_state: 'SEARCH', to_state: 'SEARCH_SWEEP' }));
    view = applyEvent(view, stepEvent('diagnostic.text', 22, { stream: 'stdout', level: 'info', text: '[STATE] SEARCH_SWEEP {"sector": "LEFT"}', redacted: false }));
    view = applyEvent(view, stepEvent('diagnostic.text', 23, { stream: 'stdout', level: 'info', text: '[STATE] SEARCH_SWEEP {"sector": "RIGHT"}', redacted: false }));
    expect(currentAction(view, true).detail).toBe('sector RIGHT');
    const bare = applyEvent(running, stepEvent('state.transition', 21, { machine: 'cycle', from_state: 'SEARCH', to_state: 'SEARCH_SWEEP' }));
    expect(currentAction(bare, true).phrase).toBe('Scanning for the block');
    expect(currentAction(bare, true).detail).toBe('the sector is not stated in the records this tab holds');
    // A line that is not JSON after the state name is not a field record, and nothing is guessed from it.
    const broken = applyEvent(bare, stepEvent('diagnostic.text', 22, { stream: 'stdout', level: 'info', text: '[STATE] SEARCH_SWEEP not json', redacted: false }));
    expect(stateFields(broken, 'SEARCH_SWEEP')).toBeNull();
  });

  it('says the block was taken as the target and which mode it measured', () => {
    let view = applyEvent(running, stepEvent('state.transition', 21, { machine: 'cycle', from_state: 'SEARCH', to_state: 'TARGET_SELECTED' }));
    view = applyEvent(view, stepEvent('diagnostic.text', 22, { stream: 'stdout', level: 'info', text: '[STATE] TARGET_SELECTED {"target_id": "cube", "mode": "BASE_ADVANCE", "cubes_listed": 1}', redacted: false }));
    const action = currentAction(view, true);
    expect(action.phrase).toBe('Block found, placement measured');
    expect(action.detail).toBe('it measured BASE_ADVANCE: placing it needs the base to move');
    const near = applyEvent(view, stepEvent('diagnostic.text', 23, { stream: 'stdout', level: 'info', text: '[STATE] TARGET_SELECTED {"mode": "ARM_ONLY"}', redacted: false }));
    expect(currentAction(near, true).detail).toBe('it measured ARM_ONLY: the arm alone places it, no wheels');
  });

  // THE ADDITIVE PLACEMENT POLICIES (autonomy/manipulation/placement_executor.py): the chosen policy and its plan in words.
  const PLAN = '{"policy": "advance_then_reach", "admitted": true, "mode": "BASE_ADVANCE", "base_advance_mm": 53.0, "fk_travel_mm": 91.0, "arm_out_mm": 113.0, "worst_margin_mm": 38.4, "capped_loss_margin_mm": 48.5, "sentence": "advance_then_reach advances 53 mm then carries 91.0 mm"}';

  it('names the placement that moves the base least, with its plan, when the selection carries one', () => {
    let view = applyEvent(running, stepEvent('state.transition', 21, { machine: 'cycle', from_state: 'SEARCH', to_state: 'TARGET_SELECTED' }));
    view = applyEvent(view, stepEvent('diagnostic.text', 22, { stream: 'stdout', level: 'info', text: `[STATE] TARGET_SELECTED {"mode": "BASE_ADVANCE", "placement_policy": "advance_then_reach", "placement_plan": ${PLAN}}`, redacted: false }));
    expect(currentAction(view, true).detail).toBe(
      'it measured BASE_ADVANCE: placing it needs the base to move; the placement that moves the base least is advance_then_reach, a measured base advance of 53 mm, then the arm carries the block 91 mm to 113 mm beyond the grasp radius',
    );
  });

  it('says which policy places the held cube, which one the run goes on with, and each measured carry step', () => {
    let view = applyEvent(running, stepEvent('state.transition', 21, { machine: 'cycle', from_state: 'OBJECT_SECURED', to_state: 'PLACEMENT_POLICY' }));
    view = applyEvent(view, stepEvent('diagnostic.text', 22, { stream: 'stdout', level: 'info', text: '[STATE] PLACEMENT_POLICY {"policy": "arm_reach", "plan": {"policy": "arm_reach", "base_advance_mm": 0.0, "fk_travel_mm": 89.8, "arm_out_mm": 126.0}}', redacted: false }));
    let action = currentAction(view, true);
    expect(action.phrase).toBe('Placing the cube');
    expect(action.detail).toBe('placing it with arm_reach, no base motion, then the arm carries the block 89.8 mm to 126 mm beyond the grasp radius');
    view = applyEvent(view, stepEvent('state.transition', 23, { machine: 'cycle', from_state: 'PLACEMENT_POLICY', to_state: 'REACH_CARRY' }));
    view = applyEvent(view, stepEvent('diagnostic.text', 24, { stream: 'stdout', level: 'info', text: '[STATE] REACH_CARRY {"policy": "arm_reach", "step": 1, "commanded_out_mm": 126.1, "measured_out_mm": 110.4}', redacted: false }));
    expect(currentAction(view, true).detail).toBe('carry step 1 commanded 126.1 mm out, measured 110.4 mm out');
    view = applyEvent(view, stepEvent('state.transition', 25, { machine: 'cycle', from_state: 'REACH_RETRACT', to_state: 'PLACEMENT_FALLBACK' }));
    view = applyEvent(view, stepEvent('diagnostic.text', 26, { stream: 'stdout', level: 'info', text: '[STATE] PLACEMENT_FALLBACK {"from_policy": "arm_reach", "to_policy": "advance_then_reach", "why": "the carry stopped (joint_error) short of the release admission", "plan": {"policy": "advance_then_reach", "base_advance_mm": 17.0, "fk_travel_mm": 61.0, "arm_out_mm": 97.2}}', redacted: false }));
    action = currentAction(view, true);
    expect(action.state).toBe('PLACEMENT_FALLBACK');
    expect(action.detail).toBe(
      'arm_reach did not place the block (the carry stopped (joint_error) short of the release admission); going on with advance_then_reach, a measured base advance of 17 mm, then the arm carries the block 61 mm to 97.2 mm beyond the grasp radius',
    );
    const none = applyEvent(view, stepEvent('diagnostic.text', 27, { stream: 'stdout', level: 'info', text: '[STATE] PLACEMENT_FALLBACK {"from_policy": "advance_then_reach", "to_policy": null, "why": "the release admission refused", "plan": null}', redacted: false }));
    expect(currentAction(none, true).detail).toBe('advance_then_reach did not place the block (the release admission refused), and no policy is left that places it from here');
  });

  it('states a proven plan and a missing plan without guessing', () => {
    expect(placementPlanWords({ policy: 'proven', base_advance_mm: 70.0, fk_travel_mm: 0.0, arm_out_mm: 36.2 })).toBe('proven, a measured base advance of 70 mm, then the release at about the grasp radius');
    expect(placementPlanWords(null)).toBeNull();
    expect(placementPlanWords({ admitted: false })).toBeNull();
    expect(placementPlanWords({ policy: 'arm_reach' })).toBe('arm_reach');
  });
});
