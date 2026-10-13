// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import type { MissionRecord, MissionTargetRecord } from './missionRecord';
import type { ViewState } from './types';
import { ALIGN_DIGEST, alignLostRun, enter, finished, graspMissedRun, placedRun, record, started, verdict } from './stageFixtures';
import { foldEvents, initialState } from './reducer';
import { presentationSteps } from '../design/steps5';
import { recordedNumber, stageRuleWords, stageTimeline, stepOfPredicate } from './stageTimeline';
import { explainFailure } from './failure';
import { applyEvent } from './reducer';

function target(patch: Partial<MissionTargetRecord> = {}): MissionTargetRecord {
  return { target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: 'auto', state: 'ACTIVE', runs: [], ...patch };
}

function mission(patch: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1',
    mission_id: 'mission-0001',
    state: 'RUNNING',
    data_mode: 'synthetic',
    performs_motion: false,
    execution_label: 'recording-rehearsal',
    started_wall_utc: null,
    selected_target: 'block-1',
    waiting_reason: null,
    detail: null,
    targets: [target()],
    activations: [],
    ...patch,
  };
}

describe('the stage of a predicate', () => {
  it('follows the controller call sites, and a rule reference for a name it does not know', () => {
    expect(stepOfPredicate({ predicate: 'aligned', rule_ref: null })).toBe('pick');
    expect(stepOfPredicate({ predicate: 'release_margin', rule_ref: null })).toBe('place');
    expect(stepOfPredicate({ predicate: 'scene_block_class', rule_ref: 'search.scene_scan' })).toBe('find');
    expect(stepOfPredicate({ predicate: 'something_new', rule_ref: null })).toBeNull();
  });

  it('prints a recorded number as recorded, rounded to two decimals, never a zero for a missing one', () => {
    expect(recordedNumber(12.4, 'mm')).toBe('12.4 mm');
    expect(recordedNumber(67.3571, 'deg')).toBe('67.36 deg');
    expect(recordedNumber(null, 'mm')).toBeNull();
    expect(recordedNumber(Number.NaN, 'mm')).toBeNull();
  });
});

describe('the stage timeline of one run', () => {
  it('puts a lost block on Pick, with the failure values, its frame, the rule and the ALIGN attempts', () => {
    const view = alignLostRun();
    const timeline = stageTimeline([{ run_id: 'synthetic-align-lost', view }]);
    const [find, pick, carry, place, back] = timeline.rows;
    expect(timeline.rows.map((row) => row.label)).toEqual(['Find', 'Pick', 'Carry', 'Place', 'Return']);
    expect(find?.status).toBe('entered');
    expect(pick?.status).toBe('failed');
    expect(pick?.failure_code).toBe('align_cube_not_detected');
    expect(pick?.frame_id).toBe('synthetic-align-lost-obs-descent');
    expect(pick?.numbers.map((item) => `${item.name} ${item.text}`)).toEqual(['attempt 3', 'aligned 2.61 mm against 6 mm', 'radial_mm 12.4 mm']);
    expect(pick?.rule).toEqual({ ref: 'align.proven', digest: ALIGN_DIGEST });
    expect(pick === undefined ? '' : stageRuleWords(pick)).toBe('align.proven @ a11a00000000');
    expect(pick?.retries).toEqual([{ state: 'ALIGN', count: 3 }]);
    expect(carry?.status).toBe('not_reached');
    expect(place?.status).toBe('not_reached');
    expect(back?.status).toBe('not_reached');
  });

  it('says a rule was not recorded rather than naming one', () => {
    const timeline = stageTimeline([{ run_id: 'g', view: graspMissedRun('g') }]);
    const pick = timeline.rows[1];
    expect(pick?.status).toBe('failed');
    expect(pick?.decided_by).toBe('closed_on_something');
    expect(pick?.rule).toBeNull();
    expect(pick === undefined ? '' : stageRuleWords(pick)).toBe('rule not recorded');
    // The close is counted inside Pick, never as a stage of its own.
    expect(pick?.retries).toEqual([
      { state: 'GRASP_CLOSE', count: 3 },
      { state: 'GRASP_RETRY_ADVANCE', count: 2 },
    ]);
  });

  it('verifies every stage of a placed run and marks the relayed base pulses on Carry', () => {
    const timeline = stageTimeline([{ run_id: 'p', view: placedRun('p') }]);
    expect(timeline.rows.map((row) => row.status)).toEqual(['entered', 'verified', 'entered', 'verified', 'verified']);
    const carry = timeline.rows[2];
    expect(carry?.numbers[0]).toEqual({ name: 'placement_plan', text: '108 mm' });
    const place = timeline.rows[3];
    expect(place?.decided_by).toBe('placed');
    expect(place?.frame_id).toBe('p-obs-exit');
    expect(place?.numbers.length).toBeLessThanOrEqual(3);
  });
});

describe('a run that ended by the mission holding for the capability', () => {
  it('is not a failed stage: the stage is entered, the code is kept, and Carry carries the hold', async () => {
    const { foldEvents, initialState } = await import('./reducer');
    const { enter, finished, started } = await import('./stageFixtures');
    const view = foldEvents(initialState(), [
      started('m'),
      enter('m', 'SEARCH'),
      enter('m', 'TARGET_SELECTED', 'SEARCH'),
      finished('m', 'refused', { code: 'mission_capability_required', phase: 'TARGET_SELECTED', detail: 'needs the capability', measurements: [], frames: [] }),
    ]);
    const timeline = stageTimeline([{ run_id: 'm', view }], mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }), target({ state: 'WAITING_FOR_CAPABILITY' }));
    expect(timeline.rows[0]?.status).toBe('entered');
    expect(timeline.rows[0]?.failure_code).toBe('mission_capability_required');
  });
});

describe('one timeline per target across its runs', () => {
  it('lets the newest run decide and lists the earlier failed run on the stage where it ended', () => {
    const timeline = stageTimeline(
      [
        { run_id: 'first', view: alignLostRun('first') },
        { run_id: 'second', view: placedRun('second') },
      ],
      mission(),
      target({ runs: ['first', 'second'] }),
    );
    expect(timeline.run_ids).toEqual(['first', 'second']);
    const pick = timeline.rows[1];
    expect(pick?.status).toBe('verified');
    expect(pick?.earlier).toEqual([{ run_id: 'first', outcome: 'failed', reason_code: 'align_cube_not_detected' }]);
    expect(timeline.basis).toContain('newest of 2 runs of Block 1');
  });

  it('draws every stage not reached for a target with no run folded, held or not', () => {
    const held = stageTimeline([], mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }), target({ state: 'WAITING_FOR_CAPABILITY' }));
    expect(held.rows.every((row) => row.status === 'not_reached')).toBe(true);
  });
});

describe('the pulse measurement belongs to the pulse it follows', () => {
  // The controller enters BASE_MEASURED and records base_measured after EVERY pulse, the return's included.
  const events = (ended: boolean) => [
    started('p'),
    enter('p', 'SEARCH'),
    enter('p', 'PLACEMENT_PLAN', 'SEARCH'),
    enter('p', 'BASE_ADVANCE', 'PLACEMENT_PLAN'),
    enter('p', 'BASE_MEASURED', 'BASE_ADVANCE'),
    verdict('p', 'base_measured', { value: true, measured: 0.93, threshold: 4, unit: 'mm' }),
    enter('p', 'RELEASE_ADMISSION', 'BASE_MEASURED'),
    enter('p', 'PLACED', 'RELEASE_ADMISSION'),
    enter('p', 'BASE_RETURN', 'PLACED'),
    enter('p', 'BASE_MEASURED', 'BASE_RETURN'),
    verdict('p', 'base_measured', { value: true, measured: 0.1, threshold: 4, unit: 'mm' }),
    ...(ended ? [finished('p', 'placed_new', null)] : []),
  ];

  it('draws the Return in progress, not the Carry, while a return pulse is measured', () => {
    const view = foldEvents(initialState(), events(false));
    const rows = presentationSteps({ ...view, active_run: { run_id: 'p' } } as unknown as typeof view).rows;
    expect(rows.find((row) => row.step === 'return')?.status).toBe('active');
    expect(rows.find((row) => row.step === 'carry')?.status).not.toBe('active');
  });

  it("puts the return pulse's measurement on the Return row, never on Carry", () => {
    const view = foldEvents(initialState(), events(true));
    const timeline = stageTimeline([{ run_id: 'p', view }]);
    const carry = timeline.rows.find((row) => row.step === 'carry');
    const back = timeline.rows.find((row) => row.step === 'return');
    // The fold keeps the newest reading of a predicate, here the return pulse's: it is the Return's, and Carry does not show it.
    expect(carry?.numbers.find((item) => item.name === 'base_measured')).toBeUndefined();
    expect(back?.numbers.find((item) => item.name === 'base_measured')?.text).toBe('0.1 mm against 4 mm');
  });

  it('banners a measurement failure during the return as a Return failure', () => {
    const view = foldEvents(initialState(), [
      ...events(false),
      finished('p', 'failed', { code: 'base_measurement_implausible', phase: 'BASE_MEASURED', detail: 'implausible', measurements: [], frames: [] }),
    ]);
    expect(stageTimeline([{ run_id: 'p', view }]).rows.find((row) => row.step === 'return')?.status).toBe('failed');
    expect(stageTimeline([{ run_id: 'p', view }]).rows.find((row) => row.step === 'carry')?.status).not.toBe('failed');
  });
});

describe('a completed scene scan', () => {
  it('fails no stage: its outcome names no placement, and its SCENE_SCAN states are the search', () => {
    const view = foldEvents(initialState(), [
      record('scan', 'run.started', { command_id: 'cmd-scan', command_type: 'scan_scene', data_mode: 'synthetic', plan_only: false, holding: false }),
      enter('scan', 'SCENE_SCAN'),
      enter('scan', 'SCENE_SCAN', 'SCENE_SCAN'),
      enter('scan', 'SCENE', 'SCENE_SCAN'),
      record('scan', 'run.finished', {
        outcome: 'unknown', exit_status: 0, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
        automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0, reason_code: null, phase: null, failure: null,
      }),
    ]);
    const rows = stageTimeline([{ run_id: 'scan', view }]).rows;
    expect(rows.map((row) => row.status)).not.toContain('failed');
  });
});

describe('a run in progress after a run that ended badly', () => {
  // The fold of run A (lost at ALIGN), then run B started and in progress at CARRY. Whatever put run A's outcome beside
  // run B's records (a snapshot's last_outcome, a hydrated history), it is not run B's and it explains nothing of run B.
  function laterRunCarrying(): ReturnType<typeof alignLostRun> {
    const earlier = alignLostRun('run-a');
    let later = applyEvent(earlier, started('run-b'));
    for (const [to, from] of [['SEARCH', null], ['PREGRASP', 'SEARCH'], ['ALIGN', 'PREGRASP'], ['APPROACH', 'ALIGN'], ['CARRY', 'APPROACH']] as const) later = applyEvent(later, enter('run-b', to, from));
    return { ...later, outcome: earlier.outcome };
  }

  it('draws no failure for the earlier run', () => {
    expect(explainFailure(laterRunCarrying())).toBeNull();
  });

  it('marks no stage failed while the later run is in progress', () => {
    const rows = stageTimeline([{ run_id: 'run-b', view: laterRunCarrying() }]).rows;
    expect(rows.map((row) => row.status)).not.toContain('failed');
    expect(rows.find((row) => row.step === 'pick')?.status).not.toBe('failed');
    expect(['entered', 'active']).toContain(rows.find((row) => row.step === 'carry')?.status);
  });
});

describe('the numbers of a placement refusal that names its binding limit', () => {
  // The head's binding refusal of start 3 (target_binding with the SeenCube explanation): the explanation's values sorted
  // by name, then the counts. Its numbers row leads with the limit that decided, never with a limit that did not bind.
  const values = [
    ['along_budget_deficit_mm', 1.6, 'mm'],
    ['axis_advance_needed_unbounded_mm', 121.58209433647478, 'mm'],
    ['binding_edge', 'right', null],
    ['constraint', 'side_edge', null],
    ['deficit_mm', 9.4, 'mm'],
    ['explanation', 'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance', null],
    ['hint', 'move the block 10 mm to the left as seen from the robot and 3 mm toward the pad', null],
    ['hint_places_block', true, null],
    ['model_admits_under', 'projected+reach_after_advance', null],
    ['side_margin_mm', 44.5, 'mm'],
    ['candidates', 1, null],
  ].map(([name, value, unit]) => ({ name, value, unit })) as never;

  it('shows the constraint, its deficit and the edge, and no sentence or hint beside them', () => {
    const view = foldEvents(initialState(), [
      started('np'),
      enter('np', 'SEARCH'),
      finished('np', 'failed', { code: 'mission_binding_candidate_not_placeable', phase: 'SEARCH', detail: 'BINDING: ...', measurements: values, frames: [] }),
    ]);
    const find = stageTimeline([{ run_id: 'np', view }]).rows.find((row) => row.step === 'find');
    expect(find?.status).toBe('failed');
    expect(find?.numbers.map((item) => `${item.name} ${item.text}`)).toEqual(['constraint side_edge', 'deficit_mm 9.4 mm', 'binding_edge right']);
  });
});

describe('a run the runtime reports in progress, beside the end of an earlier run', () => {
  // What a tab holds when it did not fold the later run's `run.started`: a reload, or a second display opened during the
  // run, subscribes from the snapshot's cursor, and the snapshot's `last_outcome` is the finished earlier run. The same
  // holds on one tab for the moment a snapshot naming the new run is folded before that run's `run.started`.
  const active = (runId: string): ViewState['active_run'] => ({ run_id: runId, command_id: null, command_type: 'start_cycle', status: 'running', state: null, started_seq: 900 }) as ViewState['active_run'];

  it('fails no stage when the tab folded no run.started', () => {
    const reloaded: ViewState = { ...alignLostRun('run-a'), run_started: null, active_run: active('run-b') };
    expect(stageTimeline([{ run_id: 'run-b', view: reloaded }]).rows.map((row) => row.status)).not.toContain('failed');
  });

  it('fails no stage while the fold still holds the earlier run it started', () => {
    const flash: ViewState = { ...alignLostRun('run-a'), active_run: active('run-b') };
    expect(stageTimeline([{ run_id: 'run-b', view: flash }]).rows.map((row) => row.status)).not.toContain('failed');
  });

  it('still fails the stage of the run the runtime reports active, when its end is folded before the snapshot moves on', () => {
    const stale: ViewState = { ...alignLostRun('run-a'), active_run: active('run-a') };
    expect(stageTimeline([{ run_id: 'run-a', view: stale }]).rows.map((row) => row.status)).toContain('failed');
  });
});

describe('no stage row carries Cake text (spec 7.2, audit C8)', () => {
  it('has no Cake field on any row, held for the capability or not', () => {
    const held = stageTimeline([], mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }), target({ state: 'WAITING_FOR_CAPABILITY' }));
    const placed = stageTimeline([{ run_id: 'p', view: placedRun('p') }]);
    for (const row of [...held.rows, ...placed.rows]) expect(Object.keys(row)).not.toContain('cake');
  });
});

describe("the stage where a run ended carries the run's word with here (spec 5.1, audit C3)", () => {
  function endedAt(outcome: 'failed' | 'stopped' | 'refused', stopAsked: boolean): ViewState {
    return foldEvents(initialState(), [
      started('e'),
      enter('e', 'SEARCH'),
      enter('e', 'PREGRASP', 'SEARCH'),
      enter('e', 'ALIGN', 'PREGRASP'),
      enter('e', 'GRASP_CLOSE', 'ALIGN'),
      ...(stopAsked ? [record('e', 'stop.requested', { origin: 'operator_console', command_id: null })] : []),
      finished('e', outcome, { code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE', detail: 'base velocity not confirmed zero', measurements: [], frames: [] }),
    ]);
  }

  it('reads a stop the controller made as "Stopped here by the controller", never as failed', () => {
    const pick = stageTimeline([{ run_id: 'e', view: endedAt('stopped', false) }]).rows.find((row) => row.step === 'pick');
    expect(pick?.status).toBe('stopped_by_controller');
    expect(pick?.words).toBe('Stopped here by the controller');
  });

  // VERIFIER R2 DEFECT 6: the card gives no run word until the run's own events are read (V13), and neither does a row.
  // A page opened after the run whose events did not read holds only the snapshot's outcome: no stop.requested can be
  // in it, so a word from it would be a guess between "by the controller" and "on request".
  it("gives no run word on any row while the run's own records are not read", () => {
    const outcomeOnly: ViewState = { ...initialState(), outcome: endedAt('stopped', false).outcome };
    const rows = stageTimeline([{ run_id: 'e', view: outcomeOnly }]).rows;
    for (const row of rows) {
      expect(row.status).toBe('not_observed');
      expect(row.words).toBe('Not seen from here');
    }
    // Once the run's events are read, the row carries the run's word.
    const read = stageTimeline([{ run_id: 'e', view: outcomeOnly, events: endedAt('stopped', false).recent_events }]).rows.find((row) => row.step === 'pick');
    expect(read?.status).toBe('stopped_by_controller');
  });

  it('reads a stop that was asked for as "Stopped here on request"', () => {
    const pick = stageTimeline([{ run_id: 'e', view: endedAt('stopped', true) }]).rows.find((row) => row.step === 'pick');
    expect(pick?.status).toBe('stopped_on_request');
    expect(pick?.words).toBe('Stopped here on request');
  });

  it('reads a refusal as "Refused here" and a failure as "Failed here"', () => {
    expect(stageTimeline([{ run_id: 'e', view: endedAt('refused', false) }]).rows.find((row) => row.step === 'pick')?.words).toBe('Refused here');
    const failed = stageTimeline([{ run_id: 'e', view: endedAt('failed', false) }]).rows.find((row) => row.step === 'pick');
    expect(failed?.status).toBe('failed');
    expect(failed?.words).toBe('Failed here');
  });
});

describe('the time each stage was entered', () => {
  it('is the recorded time of the first state transition into the stage, and null for a stage not entered', () => {
    const events = [
      started('t'),
      record('t', 'state.transition', { machine: 'cycle', from_state: null, to_state: 'SEARCH' }, { t_wall_utc: '2026-09-27T14:21:50.097644Z' }),
      record('t', 'state.transition', { machine: 'cycle', from_state: 'SEARCH', to_state: 'PREGRASP' }, { t_wall_utc: '2026-09-27T14:22:20.763357Z' }),
      record('t', 'state.transition', { machine: 'cycle', from_state: 'PREGRASP', to_state: 'ALIGN' }, { t_wall_utc: '2026-09-27T14:23:07.787197Z' }),
    ];
    const view = foldEvents(initialState(), events);
    const rows = stageTimeline([{ run_id: 't', view, events }]).rows;
    expect(rows.find((row) => row.step === 'find')?.entered_at).toBe('2026-09-27T14:21:50.097Z');
    expect(rows.find((row) => row.step === 'pick')?.entered_at).toBe('2026-09-27T14:22:20.763Z');
    expect(rows.find((row) => row.step === 'carry')?.entered_at).toBeNull();
  });
});
