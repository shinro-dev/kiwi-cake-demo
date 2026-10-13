// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THE ROBOT DID, AND WHAT THE MISSION CHECK REFUSED (docs/autonomous-demo/console-redesign-2026-09-27.md, 3.2.1;
// operator ruling 2026-09-28).
//
// The live case: mission-f9ac04941f32 (2026-09-28, runtime session rt-20260928T172310Z-46d1dc34). Its run grasped the
// block, advanced the base, released with 40.6 mm of predicted margin against 38.0 mm required, and the base was measured
// back; the mission check then refused the placement with mission_earlier_target_disturbed, because a position it had to
// find undisturbed was not matched at the check. The record says placed false. The card has to say both: what the robot
// did and what the check refused, in plain words, and never that the block was placed.
//
// The record below is the runtime's own answer to GET /api/mission for that mission (read 2026-09-28, GET only), trimmed
// to the fields the console parses; the events are that run's own records of the same kinds and values, in its order.

import { describe, expect, it } from 'vitest';
import autonomyBinding from '../../../../autonomy/manipulation/target_binding.py?raw';
import controllerSource from '../../../../bin/laptop/manipulation/run_single_cube_cycle.py?raw';
import cubeCandidates from '../../../../autonomy/perception/cube_candidates.py?raw';
import type { ContractEvent } from '../contracts/envelope';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { OutcomeCard } from '../design/OutcomeCard';
import { LISTING_ON_PAD_EXEMPT_PASSES, LISTING_PASSES, MISSION_CHECK_CODES, RETIRED_MISSION_CHECK_CODES, missionCheck } from './missionCheck';
import { outcomeCard } from './outcomeCard';
import { parseMission } from './missionRecord';
import { keptEvent } from './missionTimeline';
import type { MissionAttemptRecord } from './missionRecord';
import { enter, record, started, verdict } from './stageFixtures';

const RUN = 'console-20260928T174207Z-m915d3e9';

const LIVE_RECORD = {
  mission_record_version: 1, mission_id: 'mission-f9ac04941f32', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: 'proven-single-cube-controller',
  started_wall_utc: '2026-09-28T17:41:50.758Z', selected_target: 'cube', waiting_reason: null, detail: 'run_outcome:failed:mission_earlier_target_disturbed',
  stop_requested: false, stop_requested_wall_utc: null, records_after_end: 0,
  targets: [{
    target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: ['console-20260928T174150Z-m255e4fc', RUN],
    binding: { run_id: RUN, observation_id: 'obs-20260928T174211Z-wrist-0001', bound_pad_m: [0.0023645919811933103, -0.18543160687844704], mode: 'BASE_ADVANCE' },
    attempts: [{
      attempt: 1, run_id: RUN, park_run_id: null, outcome_class: 'fatal', reason_code: 'mission_earlier_target_disturbed', phase: 'BASE_RETURNED',
      measurements: [
        { name: 'unclaimed_placed_count', unit: null, value: 1 },
        { name: 'earlier_targets_undisturbed', unit: null, value: false },
        { name: 'predicted_worst_case_centre_margin_mm', unit: 'mm', value: 40.567476140640466 },
      ],
      observation_ids: ['obs-20260928T174654Z-wrist-0001'], stop_state: { base_ahead: false, holding: false, moved: true }, next_actions: ['return_arm', 'start'], budget_left: 1,
      t_wall_utc: '2026-09-28T17:48:19.480Z',
    }],
    retries: [], park_runs: [],
  }],
  activations: [], hold: null, next_actions: ['return_arm', 'start'],
};

function liveEvents(): ContractEvent[] {
  return [
    started(RUN),
    enter(RUN, 'SEARCH'),
    enter(RUN, 'TARGET_SELECTED', 'SEARCH'),
    enter(RUN, 'GRASP_CLOSE', 'STRADDLING'),
    enter(RUN, 'OBJECT_SECURED', 'GRASP_CLOSE'),
    verdict(RUN, 'object_secured', { value: true, measured: 0.8655589520931244, threshold: 0.5 }),
    enter(RUN, 'BASE_ADVANCE', 'HOLD_OBSERVE'),
    enter(RUN, 'RELEASE_ADMISSION', 'BASE_MEASURED'),
    verdict(RUN, 'release_margin', { value: true, measured: 40.567476140640466, threshold: 37.96051224213831, unit: 'mm' }),
    verdict(RUN, 'release_admitted', { value: true }),
    enter(RUN, 'RELEASED', 'RELEASE_ADMISSION'),
    enter(RUN, 'PLACED', 'RELEASED'),
    verdict(RUN, 'placed', { value: false, threshold: 17.960512242138307, unit: 'mm' }),
    enter(RUN, 'BASE_RETURN', 'PLACED'),
    enter(RUN, 'BASE_RETURNED', 'BASE_MEASURED'),
    verdict(RUN, 'base_returned', { value: true, measured: 3.1421927417784534, threshold: 10, unit: 'mm' }),
    record(
      RUN,
      'run.finished',
      {
        outcome: 'failed', exit_status: 5, predicates: { object_secured: true, placed: false, clearance_target_met: false, base_returned: true }, automatic_retries: 0,
        task_control_interventions: 0, evidence_complete: false, evidence_gaps: 1, reason_code: 'mission_earlier_target_disturbed', phase: 'BASE_RETURNED',
        failure: {
          detail: 'PLACED not demonstrated for this mission target (mission_earlier_target_disturbed): exactly one placed cube that no earlier target claims is needed, with every earlier target still where it was placed',
          measurements: [
            { name: 'unclaimed_placed_count', value: 1, unit: null },
            { name: 'earlier_targets_undisturbed', value: false, unit: null },
            { name: 'predicted_worst_case_centre_margin_mm', value: 40.567476140640466, unit: 'mm' },
          ],
          observation_ids: ['obs-20260928T174654Z-wrist-0001'],
        },
      } as never,
      { reason_code: 'mission_earlier_target_disturbed' },
    ),
  ];
}

function attemptOf(code: string, measurements: MissionAttemptRecord['measurements'] = [], phase = 'SEARCH'): MissionAttemptRecord {
  return {
    attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'operator_needed', reason_code: code, phase, measurements, observation_ids: [],
    stop_state: { holding: false, base_ahead: false, moved: false }, next_actions: ['start'], budget_left: 1, t_wall_utc: '2026-09-28T17:37:34.515Z',
  };
}

describe('the live run whose mission check refused the placement (mission-f9ac04941f32)', () => {
  const mission = parseMission(LIVE_RECORD);
  if (mission === null) throw new Error('the live record does not parse');
  const attempt = mission.targets[0]?.attempts?.[0] ?? null;

  it('says what the robot did and what the check refused, both, in plain words', () => {
    const check = missionCheck(attempt, liveEvents());
    expect(check?.code).toBe('mission_earlier_target_disturbed');
    expect(check?.did).toBe(
      'The robot grasped the block, released it at a point the release admission predicted was over the pad and the base was measured back where it started. The release was admitted with a predicted worst-case margin of 40.6 mm to the pad edge (38.0 mm required). That margin is a prediction made before the release; where the block came to rest is what the mission check measured after it.',
    );
    expect(check?.refused).toBe(
      'The mission check did not accept the placement: a position it had to find undisturbed was not matched by a placed block when it looked (a block placed earlier in this mission, or one this run\'s SEARCH had already counted as placed on the pad, was not found where it was recorded). It counted 1 placed block that nothing else claims. The record says placed: false.',
    );
  });

  it('from the records the store keeps of the run (a false predicate, never a true one), says the same with the attempt\'s margin', () => {
    const check = missionCheck(attempt, liveEvents().filter(keptEvent));
    expect(check?.did).toBe(
      'The robot grasped the block, released it at a point the release admission predicted was over the pad and the base was measured back where it started. The release was admitted with a predicted worst-case margin of 40.6 mm to the pad edge. That margin is a prediction made before the release; where the block came to rest is what the mission check measured after it.',
    );
  });

  it('mission_placed_no_unclaimed_cube: never states as an outcome that the block was released on the pad, which the check did not find', () => {
    const events = liveEvents().map((event) =>
      event.kind === 'run.finished' ? ({ ...event, payload: { ...(event as unknown as { payload: Record<string, unknown> }).payload, reason_code: 'mission_placed_no_unclaimed_cube' } } as ContractEvent) : event,
    );
    const nu = { ...attemptOf('mission_placed_no_unclaimed_cube', [{ name: 'unclaimed_placed_count', value: 0, unit: null }, { name: 'predicted_worst_case_centre_margin_mm', value: 40.567476140640466, unit: 'mm' }], 'BASE_RETURNED') };
    const check = missionCheck(nu, events);
    expect(check?.did).not.toMatch(/released it on the pad/);
    expect(check?.did).toContain('released it at a point the release admission predicted was over the pad');
    expect(check?.did).toContain('That margin is a prediction made before the release');
    expect(check?.refused).toContain('found no placed block on the pad that was not already claimed');
    expect(check?.refused).toContain('The record says placed: false.');
  });

  it('never says the block was placed or the mission succeeded', () => {
    const check = missionCheck(attempt, liveEvents());
    const words = `${check?.did ?? ''} ${check?.refused ?? ''}`;
    expect(words).not.toMatch(/\bplaced the block\b|succeeded|complete|placed: true/i);
  });

  it('with the run\'s events not read yet, says only what the mission record states', () => {
    const check = missionCheck(attempt, []);
    expect(check?.did).toBe('The run ended at BASE_RETURNED with the gripper empty. The release was admitted with a predicted worst-case margin of 40.6 mm to the pad edge. That margin is a prediction made before the release; where the block came to rest is what the mission check measured after it.');
    expect(check?.refused).toContain('The mission check did not accept the placement: ');
    expect(check?.refused).toContain('The record says placed: false.');
  });
});

describe('every mission binding and placement refusal code has a plain sentence', () => {
  it('the table holds exactly target_binding.py\'s refusal and attribution codes and the controller\'s listing refusals', () => {
    const block = autonomyBinding.slice(autonomyBinding.indexOf('# ---- refusal and attribution reason codes'));
    const codes = [...block.matchAll(/^[A-Z_]+ = "(mission_[a-z_]+)"$/gm)].map((match) => match[1]).filter((code) => code !== undefined);
    // The pad policy of 2026-09-29: seven binding refusals and the release agreement's one placement refusal.
    expect(codes.length).toBe(8);
    expect(codes).toContain('mission_placed_no_cube_at_release_point');
    // run_single_cube_cycle.py: the codes _mission_candidates returns and the ones _mission_placed sets (controllerCodes.test.ts
    // holds every raise site of the controller to an account; this holds the table to these two sources).
    const candidates = controllerSource.slice(controllerSource.indexOf('    def _mission_candidates('), controllerSource.indexOf('    def _mission_seen_cube('));
    const placed = controllerSource.slice(controllerSource.indexOf('    def _mission_placed('), controllerSource.indexOf('    def _mission_placed_fields('));
    const listing = [...candidates.matchAll(/return \[\], "([a-z_]+)"/g), ...placed.matchAll(/"(mission_[a-z_]+)"/g)].map((match) => match[1]).filter((code) => code !== undefined);
    expect(listing.length).toBe(6);
    // The retired claim rules' codes stay only for the records History still shows (mission-f9ac04941f32 carries one).
    expect(Object.keys(MISSION_CHECK_CODES).sort()).toEqual([...codes, ...listing, ...RETIRED_MISSION_CHECK_CODES].sort());
  });

  it('keeps the retired claim rules\' codes only for old records: nothing writes them any more, and each is marked retired', () => {
    expect([...RETIRED_MISSION_CHECK_CODES].sort()).toEqual(['mission_earlier_target_disturbed', 'mission_placed_more_than_one_unclaimed_cube', 'mission_placed_no_unclaimed_cube']);
    for (const code of RETIRED_MISSION_CHECK_CODES) {
      expect(MISSION_CHECK_CODES[code]?.retired, code).toBe(true);
      expect(autonomyBinding.includes(`"${code}"`), code).toBe(false);
      expect(controllerSource.includes(`"${code}"`), code).toBe(false);
    }
    for (const [code, entry] of Object.entries(MISSION_CHECK_CODES)) {
      if (!RETIRED_MISSION_CHECK_CODES.includes(code)) expect(entry.retired, code).toBeUndefined();
    }
  });

  it('states the listing budget the controller has (cube_candidates.MAX_PASSES, target_binding.LISTING_ON_PAD_EXEMPT_PASSES)', () => {
    expect(/^MAX_PASSES = (\d+)/m.exec(cubeCandidates)?.[1]).toBe(String(LISTING_PASSES));
    expect(/^LISTING_ON_PAD_EXEMPT_PASSES = MAX_TARGETS \+ 1$/m.test(autonomyBinding)).toBe(true);
    expect(Number(/^MAX_TARGETS = (\d+)/m.exec(autonomyBinding)?.[1]) + 1).toBe(LISTING_ON_PAD_EXEMPT_PASSES);
  });

  it('has no account for the code the pad policy retired before it was released (no record carries it)', () => {
    expect(MISSION_CHECK_CODES['mission_placed_only_a_block_' + 'listed_at_search']).toBeUndefined();
  });

  for (const code of Object.keys(MISSION_CHECK_CODES)) {
    const stage = MISSION_CHECK_CODES[code]?.stage;
    for (const phase of stage === 'placement' ? ['PLACED', 'BASE_RETURNED'] : stage === 'binding' ? ['SEARCH'] : ['SEARCH', 'PLACED', 'BASE_RETURNED']) {
      it(`${code} at ${phase} reads as a sentence of what the robot did and one of what the check refused or could not do`, () => {
        const check = missionCheck(attemptOf(code, [], phase), []);
        expect(check).not.toBeNull();
        expect(check?.did).toMatch(/\.$/);
        expect(check?.refused).toMatch(
          phase === 'SEARCH'
            ? /^The mission check (refused|could not bind) this target at SEARCH: .+\.$/
            : /^The mission check (did not accept|could not check) the placement: .+ The record says placed: false\.$/,
        );
        expect(check?.refused).not.toContain(code);
        expect(check?.refused).not.toContain('undefined');
      });
    }
  }

  it('a binding refusal says nothing was picked up, with the counts the record carries', () => {
    const check = missionCheck(attemptOf('mission_binding_no_candidate', [{ name: 'matching_candidates', value: 0, unit: null }, { name: 'cubes_seen', value: 1, unit: null }, { name: 'placed_cubes', value: 1, unit: null }]), []);
    expect(check?.did).toBe('Nothing was picked up.');
    expect(check?.refused).toBe(
      'The mission check refused this target at SEARCH: it found no block this target can take, and it needs exactly one. A block this target can take has a measured position in the pad frame, is not already on the pad, was not bound by an earlier target of this mission and, when the target declares a placement mode, measures that mode; the record counts blocks seen: 1, already on the pad: 1, matching this target: 0. When SEARCH sweeps, a block on the pad in its first view (an earlier block, or the pad\'s printed logo read as one) ends the sweep, so a block off the pad outside that view may not have been looked for.',
    );
  });

  it('mission_binding_no_candidate with a block off the pad in view never says it saw none (target_binding.py counts only this target\'s candidates)', () => {
    const values = [
      { name: 'matching_candidates', value: 0, unit: null },
      { name: 'candidates', value: 1, unit: null },
      { name: 'bound_earlier', value: 0, unit: null },
      { name: 'cubes_seen', value: 1, unit: null },
      { name: 'placed_cubes', value: 0, unit: null },
    ];
    const check = missionCheck(attemptOf('mission_binding_no_candidate', values), []);
    expect(check?.refused).not.toContain('no block that is not already on the pad');
    expect(check?.refused).toBe(
      'The mission check refused this target at SEARCH: it found no block this target can take, and it needs exactly one. A block this target can take has a measured position in the pad frame, is not already on the pad, was not bound by an earlier target of this mission and, when the target declares a placement mode, measures that mode; the record counts blocks seen: 1, already on the pad: 0, candidate blocks: 1, bound by earlier targets: 0, matching this target: 0. When SEARCH sweeps, a block on the pad in its first view (an earlier block, or the pad\'s printed logo read as one) ends the sweep, so a block off the pad outside that view may not have been looked for.',
    );
  });

  it('mission_target_mode_mismatch never names a placement mode the block may not have (the controller can measure none)', () => {
    const check = missionCheck(attemptOf('mission_target_mode_mismatch', [{ name: 'distance_to_binding_mm', value: 6.2, unit: 'mm' }]), []);
    expect(check?.refused).toBe("The mission check refused this target at SEARCH: the block where this target was bound no longer measures the target's placement mode.");
  });

  it('mission_target_not_found and _not_unique count candidate blocks, never every block', () => {
    const values = [
      { name: 'within_r_match', value: 0, unit: null },
      { name: 'candidates', value: 0, unit: null },
      { name: 'cubes_seen', value: 1, unit: null },
      { name: 'placed_cubes', value: 1, unit: null },
      { name: 'r_match_mm', value: 30, unit: 'mm' },
    ];
    const found = missionCheck(attemptOf('mission_target_not_found', values), []);
    expect(found?.refused).not.toMatch(/: no block lay within/);
    expect(found?.refused).toBe(
      'The mission check refused this target at SEARCH: no candidate block (one with a measured position in the pad frame, not already on the pad and not claimed by an earlier target of this mission) lay within 30.0 mm of where this target was first bound; the record counts blocks seen: 1, already on the pad: 1, candidate blocks: 0.',
    );
    const unique = missionCheck(attemptOf('mission_target_not_unique', [{ name: 'within_r_match', value: 2, unit: null }, { name: 'r_match_mm', value: 30, unit: 'mm' }]), []);
    expect(unique?.refused).toBe(
      'The mission check refused this target at SEARCH: 2 candidate blocks (blocks with a measured position in the pad frame, not already on the pad and not claimed by an earlier target of this mission) lay within 30.0 mm of where this target was first bound, and it needs exactly one.',
    );
  });

  it('mission_binding_candidate_not_placeable speaks of the one candidate block, not the one block seen', () => {
    const check = missionCheck(attemptOf('mission_binding_candidate_not_placeable', [{ name: 'cubes_seen', value: 2, unit: null }, { name: 'placed_cubes', value: 1, unit: null }]), []);
    expect(check?.refused).not.toContain('the one block it saw');
    expect(check?.refused).toBe(
      'The mission check refused this target at SEARCH: the one candidate block (with a measured position in the pad frame, not already on the pad and not bound by an earlier target) has no bounded placement: neither the arm alone nor the bounded base advance reaches the pad from it; the record counts blocks seen: 2, already on the pad: 1.',
    );
  });

  it('mission_binding_candidate_not_placeable with several blocks off the pad says none of them has one (the auto target of the pad policy)', () => {
    const values = [
      { name: 'candidates', value: 3, unit: null },
      { name: 'feasible_candidates', value: 0, unit: null },
      { name: 'cubes_seen', value: 4, unit: null },
      { name: 'placed_cubes', value: 1, unit: null },
    ];
    const check = missionCheck(attemptOf('mission_binding_candidate_not_placeable', values), []);
    expect(check?.refused).not.toContain('the one candidate block');
    expect(check?.refused).toBe(
      'The mission check refused this target at SEARCH: none of the 3 candidate blocks (each with a measured position in the pad frame, not already on the pad and not bound by an earlier target) has a bounded placement: neither the arm alone nor the bounded base advance reaches the pad from any of them; the record counts blocks seen: 4, already on the pad: 1.',
    );
  });

  it('a code this console has no sentence for is not explained at all', () => {
    expect(missionCheck(attemptOf('grasp_missed'), [])).toBeNull();
    expect(missionCheck(null, [])).toBeNull();
  });
});

describe('the outcome card of that mission', () => {
  const mission = parseMission(LIVE_RECORD);
  if (mission === null) throw new Error('the live record does not parse');
  const model = outcomeCard({
    mission, missionError: null, history: [], runEvents: { [RUN]: liveEvents() }, activeRunId: null, runOnScreen: RUN, acknowledgements: [], capability: null, drawnRunEvents: {},
    referenceDay: '2026-09-28',
  });

  it('carries the account of the check for its run', () => {
    expect(model.check?.code).toBe('mission_earlier_target_disturbed');
  });

  it('draws what the robot did and what the check refused before the controller\'s own sentence, which stays', () => {
    const markup = renderToStaticMarkup(createElement(OutcomeCard, { model, returnArm: null, retrying: false, onRetry: () => undefined, onAcknowledge: () => undefined }));
    expect(markup).toContain('data-mission-check="mission_earlier_target_disturbed"');
    const text = markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');
    expect(text).toContain('The robot grasped the block, released it at a point the release admission predicted was over the pad and the base was measured back where it started.');
    expect(text).toContain('The mission check did not accept the placement: ');
    expect(text).toContain('The record says placed: false.');
    expect(markup.indexOf('data-mission-check=')).toBeLessThan(markup.indexOf('data-cause='));
    expect(text).toContain('PLACED not demonstrated for this mission target');
  });
});

describe('an arm-only placement refusal: the base was never driven (run_single_cube_cycle.py _mission_predicates, cycle_returned)', () => {
  // The controller sets base_returned true for a base that never advanced once PLACED is reached, and states it with
  // measured 0.0 and the scalar base_never_advanced. No return was measured; the account must not say one was.
  const ARM = 'console-20260928T174207Z-marm0001';
  const events: ContractEvent[] = [
    started(ARM), enter(ARM, 'SEARCH'), enter(ARM, 'TARGET_SELECTED', 'SEARCH'), enter(ARM, 'GRASP_CLOSE', 'STRADDLING'), enter(ARM, 'OBJECT_SECURED', 'GRASP_CLOSE'),
    verdict(ARM, 'object_secured', { value: true, measured: 0.86, threshold: 0.5 }),
    enter(ARM, 'RELEASE_ADMISSION', 'HOLD_OBSERVE'),
    verdict(ARM, 'release_margin', { value: true, measured: 25.0, threshold: 20.0, unit: 'mm' }),
    enter(ARM, 'RELEASED', 'RELEASE_ADMISSION'), enter(ARM, 'PLACED', 'RELEASED'),
    verdict(ARM, 'placed', { value: false, threshold: 17.96, unit: 'mm' }),
    verdict(ARM, 'base_returned', { value: true, measured: 0.0, threshold: 10, unit: 'mm' }),
    record(ARM, 'run.finished', { outcome: 'failed', exit_status: 5, predicates: { object_secured: true, placed: false, clearance_target_met: false, base_returned: true }, automatic_retries: 0,
      task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0, reason_code: 'mission_placed_no_unclaimed_cube', phase: 'PLACED',
      failure: { detail: 'x', measurements: [{ name: 'unclaimed_placed_count', value: 0, unit: null }], observation_ids: [] } } as never, { reason_code: 'mission_placed_no_unclaimed_cube' }),
  ];
  const codes = ['mission_placed_no_cube_at_release_point', 'mission_placed_no_unclaimed_cube', 'mission_placed_more_than_one_unclaimed_cube', 'mission_earlier_target_disturbed'];

  for (const code of codes) {
    it(`${code}: says the base was not driven, never that it was measured back`, () => {
      const attempt = { ...attemptOf(code, [{ name: 'unclaimed_placed_count', value: 0, unit: null }], 'PLACED'), run_id: ARM };
      for (const read of [events, events.filter(keptEvent)]) {
        const did = missionCheck(attempt, read)?.did ?? '';
        expect(did).not.toContain('measured back');
        expect(did).toContain('the base was not driven');
      }
    });
  }

  it('says the base was not driven from the base_never_advanced scalar alone, even with a base transition read', () => {
    const scalar = { ...(verdict(ARM, 'base_returned', { value: true, measured: 0.0, threshold: 10, unit: 'mm' }) as unknown as { payload: Record<string, unknown> }) };
    const withScalar = { ...scalar, payload: { ...scalar.payload, evidence: [{ type: 'scalar', observation_ids: [], scalar_name: 'base_never_advanced' }] } } as unknown as ContractEvent;
    const read = events.map((event) => ((event as unknown as { payload: Record<string, unknown> }).payload['predicate'] === 'base_returned' ? withScalar : event));
    const attempt = { ...attemptOf('mission_placed_no_unclaimed_cube', [], 'PLACED'), run_id: ARM };
    expect(missionCheck(attempt, [...read.slice(0, 3), enter(ARM, 'BASE_ADVANCE', 'HOLD_OBSERVE'), ...read.slice(3)])?.did).toContain('the base was not driven');
  });
});

describe('a check that could not look (run_single_cube_cycle.py, _mission_candidates and _mission_placed)', () => {
  // THE RECORD AT PLACED: policy_return.py raises with the attribution's code after the release and the base return, and
  // the attribution of a check that could not look is target_binding.Placement(False, None, None, 0, False, code). Its
  // unclaimed_placed_count of 0 and earlier_targets_undisturbed of false were not measured.
  const PLACEHOLDERS = [
    { name: 'unclaimed_placed_count', value: 0, unit: null },
    { name: 'earlier_targets_undisturbed', value: false, unit: null },
    { name: 'predicted_worst_case_centre_margin_mm', value: 40.567476140640466, unit: 'mm' },
  ];
  const LOOK = ['mission_placed_no_exit_view', 'mission_candidates_frame_unreadable', 'mission_candidates_failed', 'mission_candidates_refused', 'mission_candidates_incomplete', 'mission_candidate_unreadable'];

  function eventsWith(code: string): ContractEvent[] {
    return liveEvents().map((event) =>
      event.kind === 'run.finished' ? ({ ...event, payload: { ...(event as unknown as { payload: Record<string, unknown> }).payload, reason_code: code } } as ContractEvent) : event,
    );
  }

  for (const code of LOOK) {
    it(`${code} at PLACED: says what the robot did, that the check counted nothing, and never a count`, () => {
      const check = missionCheck({ ...attemptOf(code, PLACEHOLDERS, 'BASE_RETURNED'), run_id: RUN }, eventsWith(code));
      expect(check?.stage).toBe('placement');
      expect(check?.did).toBe(
        'The robot grasped the block, released it at a point the release admission predicted was over the pad and the base was measured back where it started. The release was admitted with a predicted worst-case margin of 40.6 mm to the pad edge (38.0 mm required). That margin is a prediction made before the release; where the block came to rest is what the mission check measured after it.',
      );
      expect(check?.refused).toMatch(/^The mission check could not check the placement: /);
      expect(check?.refused).toMatch(/counted (nothing|no blocks)/);
      expect(check?.refused).toContain('The 0 the record carries for unclaimed placed blocks is not a count: nothing was counted.');
      expect(check?.refused).toMatch(/The record says placed: false\.$/);
      expect(check?.refused).not.toMatch(/\bfound\b|\bIt counted\b|\bplaced blocks? that\b|\bwas not matched\b/);
      expect(check?.refused).not.toContain('SEARCH');
    });
  }

  // THE RECORD AT PLACED SINCE THE PAD POLICY (mission result v3): the attribution of a check that could not look is
  // target_binding.Placement(False, None, None, 0, None, None, code), so the raise carries on_pad_count 0 and no distance
  // and no tolerance. The 0 was not counted.
  const V3_PLACEHOLDERS = [
    { name: 'on_pad_count', value: 0, unit: null },
    { name: 'release_distance_mm', value: null, unit: null },
    { name: 'release_tolerance_mm', value: null, unit: null },
    { name: 'predicted_worst_case_centre_margin_mm', value: 40.567476140640466, unit: 'mm' },
  ];
  for (const code of LOOK) {
    it(`${code} at PLACED in a v3 record: says the 0 blocks on the pad it carries is not a count`, () => {
      const check = missionCheck({ ...attemptOf(code, V3_PLACEHOLDERS, 'BASE_RETURNED'), run_id: RUN }, eventsWith(code));
      expect(check?.stage).toBe('placement');
      expect(check?.refused).toMatch(/^The mission check could not check the placement: /);
      expect(check?.refused).toContain('The 0 the record carries for blocks on the pad is not a count: nothing was counted.');
      expect(check?.refused).not.toContain('unclaimed');
      expect(check?.refused).not.toMatch(/\bmm from the release point\b|\bwithin\b/);
      expect(check?.refused).toMatch(/The record says placed: false\.$/);
    });
  }

  it('mission_candidates_incomplete at PLACED, with the run\'s events not read: the attempt alone, and the exit view named', () => {
    const check = missionCheck(attemptOf('mission_candidates_incomplete', PLACEHOLDERS, 'BASE_RETURNED'), []);
    expect(check?.did).toBe('The run ended at BASE_RETURNED with the gripper empty. The release was admitted with a predicted worst-case margin of 40.6 mm to the pad edge. That margin is a prediction made before the release; where the block came to rest is what the mission check measured after it.');
    expect(check?.refused).toBe(
      'The mission check could not check the placement: the listing of the blocks of the view taken after the release stopped with blocks it had not checked, and it decides nothing on a partial list, so it counted nothing. It checks at most 3 blocks in one view, and up to 9 more when they are on the pad; with more blocks than that in view, clear the pad or the area and retry. The 0 the record carries for unclaimed placed blocks is not a count: nothing was counted. The record says placed: false.',
    );
  });

  it('mission_placed_no_exit_view: no view, so it did not look where the block rests', () => {
    const check = missionCheck(attemptOf('mission_placed_no_exit_view', [], 'PLACED'), []);
    expect(check?.refused).toBe(
      'The mission check could not check the placement: no view was taken after the release for it to look at, so it did not look at where the block came to rest and counted no blocks. The record says placed: false.',
    );
  });

  it('the same codes at SEARCH: nothing was picked up, and the SEARCH view is named', () => {
    const check = missionCheck(attemptOf('mission_candidates_frame_unreadable', [], 'SEARCH'), []);
    expect(check?.stage).toBe('binding');
    expect(check?.did).toBe('Nothing was picked up.');
    expect(check?.refused).toBe('The mission check could not bind this target at SEARCH: it could not read the frame of the SEARCH view, so it listed no blocks and counted nothing.');
  });

  it('the release\'s predicted margin alone places it at PLACED (only policy_return.py hands it these codes)', () => {
    const check = missionCheck({ ...attemptOf('mission_candidate_unreadable', PLACEHOLDERS, 'SEARCH'), phase: null }, []);
    expect(check?.stage).toBe('placement');
  });

  it('a record that says neither SEARCH nor PLACED: the check could not decide, and nothing is said of where', () => {
    const check = missionCheck({ ...attemptOf('mission_candidates_refused', [], 'SEARCH'), phase: null }, []);
    expect(check?.stage).toBe('unknown');
    expect(check?.did).toBe('The run ended with the gripper empty.');
    expect(check?.refused).toBe('The mission check could not decide: the listing of the blocks of the view it had to check was refused, so it counted nothing.');
  });
});

describe('the outcome card of a run whose check could not read the view after the release', () => {
  const code = 'mission_candidates_incomplete';
  const record = structuredClone(LIVE_RECORD);
  record.detail = `run_outcome:failed:${code}`;
  const first = record.targets[0]?.attempts[0];
  if (first !== undefined) {
    first.reason_code = code;
    first.measurements = [
      { name: 'unclaimed_placed_count', unit: null, value: 0 },
      { name: 'earlier_targets_undisturbed', unit: null, value: false },
      { name: 'predicted_worst_case_centre_margin_mm', unit: 'mm', value: 40.567476140640466 },
    ];
  }
  const mission = parseMission(record);
  if (mission === null) throw new Error('the record does not parse');
  const events = liveEvents().map((event) =>
    event.kind === 'run.finished' ? ({ ...event, payload: { ...(event as unknown as { payload: Record<string, unknown> }).payload, reason_code: code } } as ContractEvent) : event,
  );
  const model = outcomeCard({
    mission, missionError: null, history: [], runEvents: { [RUN]: events }, activeRunId: null, runOnScreen: RUN, acknowledgements: [], capability: null, drawnRunEvents: {},
    referenceDay: '2026-09-28',
  });

  it('draws what the robot did and that the check counted nothing, before the controller\'s own sentence', () => {
    const markup = renderToStaticMarkup(createElement(OutcomeCard, { model, returnArm: null, retrying: false, onRetry: () => undefined, onAcknowledge: () => undefined }));
    expect(markup).toContain(`data-mission-check="${code}"`);
    expect(markup).toContain('data-check-stage="placement"');
    const text = markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');
    expect(text).toContain('The robot grasped the block, released it at a point the release admission predicted was over the pad and the base was measured back where it started.');
    expect(text).toContain('The mission check could not check the placement: the listing of the blocks of the view taken after the release stopped with blocks it had not checked');
    expect(text).toContain('is not a count: nothing was counted. The record says placed: false.');
    expect(markup.indexOf('data-mission-check=')).toBeLessThan(markup.indexOf('data-cause='));
  });
});

describe('the release agreement\'s refusal (the pad policy of 2026-09-29, ruling (ii)): no block on the pad at this run\'s release', () => {
  // policy_return.py raises mission_placed_no_cube_at_release_point with on_pad_count (every block on the pad in the EXIT
  // view), release_distance_mm (the nearest one's distance to the release segment, null when none is on the pad) and
  // release_tolerance_mm (26.0 mm, 30.0 mm with a sideways release offset). The other blocks on the pad are counted and
  // decide nothing.
  const CODE = 'mission_placed_no_cube_at_release_point';
  function refusal(values: { name: string; value: number | null; unit: string | null }[]): MissionAttemptRecord {
    return { ...attemptOf(CODE, [...values, { name: 'predicted_worst_case_centre_margin_mm', value: 40.567476140640466, unit: 'mm' }], 'BASE_RETURNED'), run_id: RUN };
  }
  function eventsWith(): ContractEvent[] {
    return liveEvents().map((event) =>
      event.kind === 'run.finished' ? ({ ...event, payload: { ...(event as unknown as { payload: Record<string, unknown> }).payload, reason_code: CODE } } as ContractEvent) : event,
    );
  }

  it('with blocks elsewhere on the pad: counts them, names the nearest distance and the tolerance, and says they do not count for this run', () => {
    const check = missionCheck(refusal([
      { name: 'on_pad_count', value: 2, unit: null },
      { name: 'release_distance_mm', value: 41.3, unit: 'mm' },
      { name: 'release_tolerance_mm', value: 26.0, unit: 'mm' },
    ]), eventsWith());
    expect(check?.stage).toBe('placement');
    expect(check?.did).toBe(
      'The robot grasped the block, released it at a point the release admission predicted was over the pad and the base was measured back where it started. The release was admitted with a predicted worst-case margin of 40.6 mm to the pad edge (38.0 mm required). That margin is a prediction made before the release; where the block came to rest is what the mission check measured after it.',
    );
    expect(check?.refused).toBe(
      'The mission check did not accept the placement: after the release it found no block on the pad within 26.0 mm of the point where the robot released its block, so it credited no block to this run; it saw 2 blocks on the pad, the nearest 41.3 mm from the release point, and a block elsewhere on the pad does not count for this run. Check whether the block left the pad. The record says placed: false.',
    );
  });

  it('with no block on the pad at all, and the sideways tolerance', () => {
    const check = missionCheck(refusal([
      { name: 'on_pad_count', value: 0, unit: null },
      { name: 'release_distance_mm', value: null, unit: null },
      { name: 'release_tolerance_mm', value: 30.0, unit: 'mm' },
    ]), []);
    expect(check?.refused).toBe(
      'The mission check did not accept the placement: after the release it found no block on the pad within 30.0 mm of the point where the robot released its block, so it credited no block to this run; it saw no block on the pad at all. Check whether the block left the pad. The record says placed: false.',
    );
  });

  it('with one block on the pad, farther than the tolerance', () => {
    const check = missionCheck(refusal([
      { name: 'on_pad_count', value: 1, unit: null },
      { name: 'release_distance_mm', value: 33.0, unit: 'mm' },
      { name: 'release_tolerance_mm', value: 26.0, unit: 'mm' },
    ]), []);
    expect(check?.refused).toContain('it saw 1 block on the pad, 33.0 mm from the release point, and a block elsewhere on the pad does not count for this run.');
  });

  it('with no values read, names the tolerance without a number and states no count', () => {
    const check = missionCheck({ ...attemptOf(CODE, [], 'PLACED') }, []);
    expect(check?.refused).toBe(
      'The mission check did not accept the placement: after the release it found no block on the pad within the release tolerance of the point where the robot released its block, so it credited no block to this run. Check whether the block left the pad. The record says placed: false.',
    );
  });

  it('never says the other blocks on the pad were not looked at (they are counted), and never that the robot\'s block is on the pad', () => {
    const check = missionCheck(refusal([{ name: 'on_pad_count', value: 2, unit: null }, { name: 'release_distance_mm', value: 41.3, unit: 'mm' }, { name: 'release_tolerance_mm', value: 26.0, unit: 'mm' }]), eventsWith());
    const words = `${check?.did ?? ''} ${check?.refused ?? ''}`;
    expect(words).not.toMatch(/not looked at|block is on the pad|placed the block|placed: true/i);
  });
});

describe('the outcome card of a run the release agreement refused (the pad policy of 2026-09-29)', () => {
  const code = 'mission_placed_no_cube_at_release_point';
  const record = structuredClone(LIVE_RECORD);
  record.detail = `run_outcome:failed:${code}`;
  const first = record.targets[0]?.attempts[0];
  if (first !== undefined) {
    first.reason_code = code;
    first.measurements = [
      { name: 'on_pad_count', unit: null, value: 2 },
      { name: 'release_distance_mm', unit: 'mm', value: 41.3 },
      { name: 'release_tolerance_mm', unit: 'mm', value: 26.0 },
      { name: 'predicted_worst_case_centre_margin_mm', unit: 'mm', value: 40.567476140640466 },
    ];
  }
  const mission = parseMission(record);
  if (mission === null) throw new Error('the record does not parse');
  const events = liveEvents().map((event) =>
    event.kind === 'run.finished' ? ({ ...event, payload: { ...(event as unknown as { payload: Record<string, unknown> }).payload, reason_code: code } } as ContractEvent) : event,
  );
  const model = outcomeCard({
    mission, missionError: null, history: [], runEvents: { [RUN]: events }, activeRunId: null, runOnScreen: RUN, acknowledgements: [], capability: null, drawnRunEvents: {},
    referenceDay: '2026-09-28',
  });

  it('draws what the robot did and what the release agreement found, before the controller\'s own sentence', () => {
    const markup = renderToStaticMarkup(createElement(OutcomeCard, { model, returnArm: null, retrying: false, onRetry: () => undefined, onAcknowledge: () => undefined }));
    expect(markup).toContain(`data-mission-check="${code}"`);
    expect(markup).toContain('data-check-stage="placement"');
    const text = markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');
    expect(text).toContain('The mission check did not accept the placement: after the release it found no block on the pad within 26.0 mm of the point where the robot released its block');
    expect(text).toContain('the nearest 41.3 mm from the release point, and a block elsewhere on the pad does not count for this run. Check whether the block left the pad. The record says placed: false.');
    expect(markup.indexOf('data-mission-check=')).toBeLessThan(markup.indexOf('data-cause='));
  });
});
