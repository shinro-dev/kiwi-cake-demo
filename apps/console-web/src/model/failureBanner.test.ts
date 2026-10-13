// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import type { Capability } from '@kiwi/demo-contracts';
import { commandScope } from './command';
import { failureBanner, noFrameSentence, OPERATOR_DECISION } from './failureBanner';
import type { BannerInput } from './failureBanner';
import type { MissionRecord } from './missionRecord';
import { alignLostRun, enter, finished, placedRun, started, verdict } from './stageFixtures';
import { foldEvents, initialState } from './reducer';
import { stageTimeline } from './stageTimeline';
import type { ViewState } from './types';

function capability(id: string, state: Capability['state']): Capability {
  return { id, state, reason: state === 'enabled' ? null : 'the robot is not answering', reason_code: state === 'enabled' ? null : 'robot_not_answering' } as unknown as Capability;
}

function mission(patch: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-0001', state: 'RUNNING', data_mode: 'synthetic', performs_motion: false,
    execution_label: 'recording-rehearsal', started_wall_utc: null, selected_target: 'block-1', waiting_reason: null, detail: null,
    targets: [], activations: [], ...patch,
  };
}

/** The hold the runtime serves, with the next actions its record gives. */
function hold(nextActions: string[]): NonNullable<MissionRecord['hold']> {
  return {
    target_id: 'block-1', attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'retryable', reason_code: 'align_cube_not_detected', phase: 'ALIGN',
    observation_ids: [], stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: nextActions, budget_left: 1, retry_requested: false,
  };
}

function input(view: ViewState, patch: Partial<BannerInput> = {}): BannerInput {
  return {
    view,
    live: view,
    scope: commandScope(view),
    mission: null,
    timeline: stageTimeline([{ run_id: 'r', view }]),
    retryOffered: true,
    readOnlyReason: null,
    stopBusy: false,
    retryGuard: null,
    returnGuard: null,
    ...patch,
  };
}

describe('the failure banner', () => {
  it('names the stage, the measured sentence, the frame and the rule that decided', () => {
    const view = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled'), capability('observe', 'enabled')] };
    const banner = failureBanner(input(view));
    expect(banner?.headline).toBe('Pick failed');
    expect(banner?.state).toBe('ALIGN');
    expect(banner?.code).toBe('align_cube_not_detected');
    expect(banner?.sentence.startsWith('The cube was not detected in the pre-grasp view')).toBe(true);
    expect(banner?.detail).toContain('descent view');
    expect(banner?.frame_id).toBe('r-obs-descent');
    expect(banner?.rule_words).toBe('align.proven @ a11a00000000');
    expect(banner?.numbers.map((item) => item.name)).toEqual(['attempt', 'aligned', 'radial_mm']);
  });

  it('always shows Try again, Return arm and Stop, each with the reason it is on or off', () => {
    const view = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled')] };
    const banner = failureBanner(input(view));
    expect(banner?.actions.map((item) => item.label)).toEqual(['Try again', 'Return arm', 'Stop']);
    const [retry, back, stop] = banner?.actions ?? [];
    expect(retry?.enabled).toBe(false);
    expect(retry?.reason).toBe('no mission is carried on this runtime; Start begins a new one');
    expect(back?.enabled).toBe(true);
    expect(back?.reason).toContain('low scan pose');
    expect(stop?.enabled).toBe(true);
  });

  it('offers Try again only while the mission is held for the decision, on a runtime that offers it', () => {
    const view = alignLostRun('r');
    const held = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: OPERATOR_DECISION, hold: hold(['retry', 'stop']) });
    expect(failureBanner(input(view, { mission: held }))?.actions[0]?.enabled).toBe(true);
    expect(failureBanner(input(view, { mission: held }))?.held_for_decision).toBe(true);
    expect(failureBanner(input(view, { mission: held, retryOffered: false }))?.actions[0]?.reason).toBe('this runtime does not offer a mission retry');
    expect(failureBanner(input(view, { mission: mission() }))?.actions[0]?.reason).toBe(
      'the mission is not held for a decision, so there is no attempt to retry',
    );
    expect(failureBanner(input(view, { mission: held, readOnlyReason: 'a recorded run is on the screen' }))?.actions[0]?.enabled).toBe(false);
  });

  it('draws Try again off with the motion guard the screen passes in, the reason on the button', () => {
    const view = alignLostRun('r');
    const held = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: OPERATOR_DECISION, hold: hold(['retry', 'stop']) });
    const retry = failureBanner(input(view, { mission: held, retryGuard: 'owner_unresolved' }))?.actions[0];
    expect(retry?.enabled).toBe(false);
    expect(retry?.reason).toBe('owner_unresolved');
    // The banner's own reasons come first: a hold that is not there is said as such, not as a guard.
    expect(failureBanner(input(view, { mission: mission(), retryGuard: 'owner_unresolved' }))?.actions[0]?.reason).toBe(
      'the mission is not held for a decision, so there is no attempt to retry',
    );
  });

  it('offers Try again only when the hold the runtime recorded offers a retry, and says when it offers only Stop', () => {
    const view = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled')] };
    const at = (record: MissionRecord) => failureBanner(input(view, { mission: record }));
    const stopOnly = at(mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: OPERATOR_DECISION, hold: hold(['stop']) }));
    expect(stopOnly?.actions[0]?.enabled).toBe(false);
    expect(stopOnly?.actions[0]?.reason).toContain('the runtime offers only Stop for this hold');
    expect(stopOnly?.actions[0]?.reason).not.toContain('park the arm');
    // Return arm is not in the hold's list either, so the banner does not offer it beside a hold that offers only Stop.
    expect(stopOnly?.actions[1]?.enabled).toBe(false);
    expect(stopOnly?.actions[1]?.reason).toContain('not Return arm');
    expect(stopOnly?.actions[2]?.enabled).toBe(true);
    const unserved = at(mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: OPERATOR_DECISION }));
    expect(unserved?.actions[0]?.enabled).toBe(false);
    expect(unserved?.actions[0]?.reason).toContain('served no record of the held attempt');
    const moveFirst = at(mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: OPERATOR_DECISION, hold: hold(['move_block_then_retry', 'stop']) }));
    expect(moveFirst?.actions[0]?.enabled).toBe(true);
    expect(moveFirst?.actions[0]?.reason).toContain('move the block by hand first');
  });

  it('says nothing is held once a Stop ended the hold, and why Try again is off', () => {
    // The runtime serves STOPPED after a Stop, keeps the waiting reason and serves no hold.
    const view = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled')] };
    const stopped = mission({ state: 'STOPPED', waiting_reason: OPERATOR_DECISION, hold: null, next_actions: ['return_arm', 'start'] });
    const banner = failureBanner(input(view, { mission: stopped }));
    expect(banner?.held_for_decision).toBe(false);
    // Said as stopped: never as held for a decision, nor as still stopping.
    expect(banner?.actions[0]?.reason).toBe(`mission ${stopped.mission_id} was stopped, so there is no attempt to retry; Start begins a new mission`);
    // The hold is over: Return arm is the runtime's list again.
    expect(banner?.actions[1]?.enabled).toBe(true);
  });

  it('draws Try again off once a retry of the held attempt was written', () => {
    const view = alignLostRun('r');
    const asked = { ...hold(['retry', 'stop']), retry_requested: true };
    const retry = failureBanner(input(view, { mission: mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: OPERATOR_DECISION, hold: asked }) }))?.actions[0];
    expect(retry?.enabled).toBe(false);
    expect(retry?.reason).toContain('already asked for');
  });

  it('draws Return arm off with the reason the Actions control gives, so the two never disagree', () => {
    const view = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled')] };
    const back = failureBanner(input(view, { returnGuard: 'owner_unresolved' }))?.actions[1];
    expect(back?.enabled).toBe(false);
    expect(back?.reason).toBe('owner_unresolved');
  });

  it('reads Return arm from the LIVE capability list, not from the fold of a finished run read back after a reload', () => {
    // A run read back from history is folded from the initial state, so its capability list is empty. The runtime's
    // live list is what the embedded control reads; the line beside it must read the same list.
    const hydrated = alignLostRun('r');
    expect(hydrated.capabilities).toEqual([]);
    const live = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled')] };
    const back = failureBanner(input(hydrated, { live }))?.actions[1];
    expect(back?.enabled).toBe(true);
    expect(back?.reason).not.toContain('does not report');
    // The reason code's own why is still the wording: nothing was picked up, and a cycle starts from the low scan pose.
    expect(back?.reason).toBe('put the arm back at the saved low scan pose, which is where a cycle has to start from');
    // And the other way: a finished run whose fold lists the control says nothing about it once the live list does not.
    const stale = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled')] };
    const gone = failureBanner(input(stale, { live: alignLostRun('r') }))?.actions[1];
    expect(gone?.enabled).toBe(false);
    expect(gone?.reason).toBe('this runtime does not report a control that returns the arm');
  });

  it('gates Stop on the live contract, the same as the header Stop, not on the fold of a finished run', () => {
    const hydrated = alignLostRun('r');
    expect(hydrated.contract_compatible).toBe(true);
    const live = { ...alignLostRun('r'), contract_compatible: false };
    expect(failureBanner(input(hydrated, { live }))?.actions[2]?.enabled).toBe(false);
  });

  it('says the runtime does not report Return arm rather than offering it', () => {
    const banner = failureBanner(input(alignLostRun('r')));
    expect(banner?.actions[1]?.enabled).toBe(false);
    expect(banner?.actions[1]?.reason).toBe('this runtime does not report a control that returns the arm');
  });

  it('is absent while the mission holds for the capability the run measured it needs', async () => {
    const { foldEvents, initialState } = await import('./reducer');
    const { enter, finished, started } = await import('./stageFixtures');
    const view = foldEvents(initialState(), [
      started('m'),
      enter('m', 'SEARCH'),
      finished('m', 'refused', { code: 'mission_capability_required', phase: 'TARGET_SELECTED', detail: 'needs the capability', measurements: [], frames: [] }),
    ]);
    const held = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' });
    expect(failureBanner(input(view, { mission: held }))).toBeNull();
    // Without the hold on record, the same run is explained like any other.
    expect(failureBanner(input(view))?.code).toBe('mission_capability_required');
  });

  it('is absent for a run that placed', () => {
    expect(failureBanner(input(placedRun('p')))).toBeNull();
  });

  it('names the frame the failing stage decided on, never a passing already_placed frame from SEARCH', () => {
    // grasp_missed names no frame of its own. SEARCH's already_placed is false for a block not yet placed, and that
    // false is the passing answer: its frame is never the one the Pick stage decided on.
    const run = (closeFrame: string | undefined) =>
      foldEvents(initialState(), [
        started('g'),
        enter('g', 'SEARCH'),
        verdict('g', 'already_placed', { value: false, measured: -40, threshold: 38, unit: 'mm', frame: 'g-obs-search' }),
        verdict('g', 'cube_seen_with_a_contact_point', { value: true, frame: 'g-obs-search' }),
        enter('g', 'ALIGN', 'SEARCH'),
        verdict('g', 'aligned', { value: true, measured: 2.6, threshold: 6, unit: 'mm', frame: 'g-obs-align' }),
        enter('g', 'GRASP_CLOSE', 'ALIGN'),
        verdict('g', 'closed_on_something', { value: false, measured: 67.357, threshold: 67.4, unit: null, ...(closeFrame === undefined ? {} : { frame: closeFrame }) }),
        finished('g', 'failed', { code: 'grasp_missed', phase: 'GRASP_CLOSE', detail: 'three closes found nothing', measurements: [], frames: [] }),
      ]);
    const withClose = run('g-obs-close-3');
    expect(failureBanner(input(withClose))?.frame_id).toBe('g-obs-close-3');
    const withNone = run(undefined);
    const banner = failureBanner(input(withNone));
    expect(banner?.headline).toBe('Pick failed');
    // No failing Pick predicate carries a frame and the failure named none: no frame, never SEARCH's or ALIGN's.
    expect(banner?.frame_id).toBeNull();
    expect(banner?.explanation.failing_predicates.map((item) => item.predicate)).toEqual(['closed_on_something']);
  });
});

describe('the line a failure with no frame is drawn with', () => {
  it('names the stage, else the state, else the run, and never doubles an article', () => {
    expect(noFrameSentence({ stage: 'pick', stage_label: 'Pick', state: 'GRASP_CLOSE' })).toBe('No frame was recorded for this failure: the controller named none for the Pick stage.');
    expect(noFrameSentence({ stage: null, stage_label: 'RETURN_SETTLE', state: 'RETURN_SETTLE' })).toBe('No frame was recorded for this failure: the controller named none for the RETURN_SETTLE state.');
    const run = noFrameSentence({ stage: null, stage_label: 'the run', state: null });
    expect(run).toBe('No frame was recorded for this failure: the controller named none for this run.');
    expect(run).not.toContain('the the');
  });
});

describe('a placement refusal on the banner', () => {
  const refused = (measurements: { name: string; value: number | string; unit: string | null }[]) =>
    foldEvents(initialState(), [
      started('s'),
      enter('s', 'SEARCH'),
      finished('s', 'failed', { code: 'mission_binding_candidate_not_placeable', phase: 'SEARCH', detail: 'BINDING: no bounded placement', measurements, frames: ['s-obs-search'] }),
    ]);
  const counts = [
    { name: 'candidates', value: 1, unit: null },
    { name: 'cubes_seen', value: 1, unit: null },
  ];

  // Track P's record of start 3 (placement_envelope.explain_refusal on fix/ovn4-p), as the binding refusal carries it.
  const start3 = [
    { name: 'side_margin_mm', value: 44.5, unit: 'mm' },
    { name: 'required_side_margin_mm', value: 58.96051224213831, unit: 'mm' },
    { name: 'axis_advance_needed_unbounded_mm', value: 121.58209433647478, unit: 'mm' },
    { name: 'constraint', value: 'side_edge', unit: null },
    { name: 'deficit_mm', value: 9.4, unit: 'mm' },
    { name: 'hint', value: 'move the block 10 mm to the left as seen from the robot and 3 mm toward the pad', unit: null },
    { name: 'binding_edge', value: 'right', unit: null },
    { name: 'along_budget_deficit_mm', value: 1.6, unit: 'mm' },
    { name: 'explanation', value: 'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance', unit: null },
  ];

  it("leads with track P's explanation of the binding limit and its hint, never a shortfall beside a margin it was not computed from", () => {
    const banner = failureBanner(input(refused([...counts, ...start3])));
    expect(banner?.headline).toBe('Find failed');
    expect(banner?.limit).toBe(
      'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance; the along-axis budget is also short, by 1.6 mm; move the block 10 mm to the left as seen from the robot and 3 mm toward the pad',
    );
  });

  it('names the advance budget of 21:17 in words, never its raw code', () => {
    const late = [
      { name: 'axis_advance_needed_unbounded_mm', value: 131.59210434648477, unit: 'mm' },
      { name: 'constraint', value: 'along_budget', unit: null },
      { name: 'deficit_mm', value: 11.6, unit: 'mm' },
      { name: 'hint', value: 'move the block 12 mm toward the pad', unit: null },
      { name: 'explanation', value: 'the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm', unit: null },
    ];
    const limit = failureBanner(input(refused([...counts, ...late])))?.limit;
    expect(limit).toBe('the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm; move the block 12 mm toward the pad');
    expect(limit).not.toContain('along_budget');
  });

  it('derives the limits from the margins a 9891d14 refusal records, which names no constraint', () => {
    const legacy = foldEvents(initialState(), [
      started('l'),
      enter('l', 'SEARCH'),
      finished('l', 'failed', {
        code: 'search_placement_infeasible', phase: 'SEARCH', detail: 'SEARCH: no bounded placement; nothing was picked up', frames: ['l-obs-search'],
        measurements: [
          { name: 'anchor_pad_x_m', value: 0.163, unit: 'm' },
          { name: 'bounded_base_advance_limit_mm', value: 120, unit: 'mm' },
          { name: 'side_margin_mm', value: 44.5, unit: 'mm' },
          { name: 'required_side_margin_mm', value: 58.96051224213831, unit: 'mm' },
          { name: 'axis_advance_needed_unbounded_mm', value: 121.58209433647478, unit: 'mm' },
        ],
      }),
    ]);
    const limit = failureBanner(input(legacy))?.limit;
    expect(limit).toContain('a side margin of 44.5 mm against the 58.96 mm required before any advance, 14.46 mm short');
    expect(limit).toContain('121.58 mm needed against the 120 mm limit, 1.58 mm past it');
    expect(limit).not.toContain('not recorded');
  });

  it("states a declared target's no-candidate refusal's limit when it carries one, and nothing when it does not", () => {
    const noCandidate = (measurements: { name: string; value: number | string; unit: string | null }[]) =>
      foldEvents(initialState(), [
        started('n'),
        enter('n', 'SEARCH'),
        finished('n', 'failed', { code: 'mission_binding_no_candidate', phase: 'SEARCH', detail: 'BINDING: no candidate', measurements, frames: [] }),
      ]);
    expect(failureBanner(input(noCandidate([...counts, ...start3])))?.limit).toContain('9.4 mm short of 58.96 mm');
    expect(failureBanner(input(noCandidate(counts)))?.limit).toBeNull();
  });

  it('says the limit was not recorded when the refusal carries counts only', () => {
    expect(failureBanner(input(refused(counts)))?.limit).toContain('limit not recorded');
    expect(failureBanner(input(alignLostRun('r')))?.limit).toBeNull();
  });
});

describe('the capability hand-over, while the mission is still carried', () => {
  // The run ended with mission_capability_required and the mission has not written its wait yet: the mission, RUNNING,
  // is about to hold for the capability. That end is the mission's hand-over, not a failure.
  function capabilityHandOver(): ViewState {
    return foldEvents(initialState(), [
      started('m'),
      enter('m', 'SEARCH'),
      enter('m', 'TARGET_SELECTED', 'SEARCH'),
      finished('m', 'failed', { code: 'mission_capability_required', phase: 'TARGET_SELECTED', detail: 'needs the capability', measurements: [], frames: [] }),
    ]);
  }

  it('draws no failure while the mission is RUNNING or held for the capability', () => {
    for (const state of ['RUNNING', 'WAITING_FOR_CAPABILITY']) {
      expect(failureBanner(input(capabilityHandOver(), { mission: mission({ state, waiting_reason: state === 'RUNNING' ? null : 'capability_unavailable' }) }))).toBeNull();
    }
  });

  it('is explained as the end of a mission that ended with it', () => {
    expect(failureBanner(input(capabilityHandOver(), { mission: mission({ state: 'FAILED' }) }))?.code).toBe('mission_capability_required');
  });

  it('draws no failure for the arm-alone binding whose fallback asks for the capability, while the mission is carried', () => {
    const handOver = foldEvents(initialState(), [
      started('m'),
      enter('m', 'SEARCH'),
      enter('m', 'TARGET_SELECTED', 'SEARCH'),
      finished('m', 'failed', { code: 'mission_fallback_capability_required', phase: 'TARGET_SELECTED', detail: 'the fallback needs the capability', measurements: [], frames: [] }),
    ]);
    for (const state of ['RUNNING', 'WAITING_FOR_CAPABILITY']) {
      expect(failureBanner(input(handOver, { mission: mission({ state, waiting_reason: state === 'RUNNING' ? null : 'capability_unavailable' }) }))).toBeNull();
    }
  });
});

describe("the banner's Stop and Return arm, from the runtime's lists", () => {
  const stopCapability = (state: Capability['state']): Capability =>
    ({ id: 'stop', state, reason: state === 'enabled' ? null : 'there is no run to stop', reason_code: state === 'enabled' ? null : 'no_active_run' }) as unknown as Capability;

  it("offers Stop only while the runtime's capability list does, with its reason when it does not", () => {
    const view = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled'), stopCapability('disabled')] };
    const off = failureBanner(input(view))?.actions.find((item) => item.id === 'stop');
    expect(off).toMatchObject({ enabled: false, reason: 'there is no run to stop' });
    const on = failureBanner(input({ ...view, capabilities: [stopCapability('enabled')] }))?.actions.find((item) => item.id === 'stop');
    expect(on?.enabled).toBe(true);
  });

  it("offers Return arm for a mission's run only when the mission's list offers it", () => {
    const view = { ...alignLostRun('r'), capabilities: [capability('return_arm', 'enabled'), stopCapability('disabled')] };
    const target = { target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: 'auto' as const, state: 'FAILED', runs: ['r'] };
    const nothingMoved = failureBanner(input(view, { mission: mission({ state: 'FAILED', targets: [target], next_actions: ['start'] }) }))?.actions.find((item) => item.id === 'return_arm');
    expect(nothingMoved).toMatchObject({ enabled: false, reason: "the runtime's mission offers start, not Return arm" });
    const moved = failureBanner(input(view, { mission: mission({ state: 'FAILED', targets: [target], next_actions: ['return_arm', 'start'] }) }))?.actions.find((item) => item.id === 'return_arm');
    expect(moved?.enabled).toBe(true);
    // A run the mission did not carry is answered by the capability list alone.
    const other = failureBanner(input(view, { mission: mission({ state: 'COMPLETE', targets: [{ ...target, runs: ['someone-else'] }], next_actions: ['start'] }) }))?.actions.find((item) => item.id === 'return_arm');
    expect(other?.enabled).toBe(true);
  });
});

describe('a run the runtime reports in progress, beside the end of an earlier run', () => {
  // A reload or a second display folds no `run.started` of the run in progress and holds the snapshot's `last_outcome`,
  // the run before it; one tab can hold the same for a moment after a press.
  const active = (runId: string): ViewState['active_run'] => ({ run_id: runId, command_id: null, command_type: 'start_cycle', status: 'running', state: null, started_seq: 900 }) as ViewState['active_run'];

  it('draws no banner when the tab folded no run.started', () => {
    expect(failureBanner(input({ ...alignLostRun('r'), run_started: null, active_run: active('later') }))).toBeNull();
  });

  it('draws no banner while the fold still holds the earlier run it started', () => {
    expect(failureBanner(input({ ...alignLostRun('r'), active_run: active('later') }))).toBeNull();
  });

  it('draws the banner of the run the runtime reports active, when its end is folded first', () => {
    expect(failureBanner(input({ ...alignLostRun('r'), active_run: active('r') }))?.code).toBe('align_cube_not_detected');
  });
});
