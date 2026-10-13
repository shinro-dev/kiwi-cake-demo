// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A refusal has to be readable without a terminal, and the case that decides whether it is readable is the one where
// this console is BEHIND the controller: a reason code nobody here has a sentence for. These tests pin what happens
// then, and pin that nothing is invented when the record is thin.

import { describe, expect, it } from 'vitest';
import failedWithAReason from '@kiwi/demo-contracts/fixtures/event/run.finished.failed-with-a-reason.json';
import infeasibleBeforeThePickup from '@kiwi/demo-contracts/fixtures/event/predicate.evaluated.infeasible-before-the-pickup.json';
import type { Capability, PayloadRunFinished } from '@kiwi/demo-contracts';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import { describeFailureCode, explainFailure, failureCodeLabel, nextActionsFor } from './failure';
import { applyEvent, foldEvents, initialState } from './reducer';
import { buildStoppedHoldingRun } from '../api/syntheticScenarios';

let seq = 0;

function envelope(extra: Partial<EventEnvelope> = {}): EventEnvelope {
  seq += 1;
  return {
    schema_version: '1',
    seq,
    run_id: 'run-f',
    action_id: null,
    observation_id: null,
    producer: { name: 'test', version: '0', artifact_digest: null, contract_version: '1' },
    source_session: 'session-f',
    t_mono_ns: seq * 1_000_000,
    clock_domain: 'laptop_monotonic',
    t_wall_utc: null,
    reason_code: null,
    provenance: null,
    ...extra,
  };
}

function finished(payload: Partial<PayloadRunFinished>): ContractEvent {
  return {
    ...envelope(),
    kind: 'run.finished',
    payload: {
      outcome: 'failed',
      exit_status: 5,
      predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null },
      automatic_retries: 0,
      task_control_interventions: 0,
      evidence_complete: true,
      evidence_gaps: 0,
      ...payload,
    },
  } as ContractEvent;
}

function stateAfter(event: ContractEvent) {
  return applyEvent(initialState(), event);
}

describe('a reason code this console does not know', () => {
  it('renders as the code itself, never as a blank and never as a guess', () => {
    const state = stateAfter(
      finished({
        reason_code: 'a_code_from_a_newer_controller',
        phase: 'SOME_NEW_STATE',
        failure: { detail: 'the controller said this', measurements: [], observation_ids: [] },
      }),
    );
    const explanation = explainFailure(state);
    expect(explanation).not.toBeNull();
    expect(explanation?.reason_code).toBe('a_code_from_a_newer_controller');
    expect(explanation?.recognised).toBe(false);
    // No sentence is invented for it, and the code is what a headline has to fall back to.
    expect(explanation?.gloss).toBeNull();
    expect(describeFailureCode('a_code_from_a_newer_controller')).toBeNull();
    expect(failureCodeLabel('a_code_from_a_newer_controller')).toBe('a_code_from_a_newer_controller');
  });

  it('keeps the whole payload the record carried under the unknown code', () => {
    const state = stateAfter(
      finished({
        reason_code: 'a_code_from_a_newer_controller',
        phase: 'SOME_NEW_STATE',
        failure: {
          detail: 'a sentence only the controller knows how to write',
          measurements: [
            { name: 'some_new_margin_mm', value: 4.25, unit: 'mm' },
            { name: 'a_direction', value: -1, unit: null },
          ],
          observation_ids: ['obs-new-0001'],
        },
      }),
    );
    const explanation = explainFailure(state);
    expect(explanation?.phase).toBe('SOME_NEW_STATE');
    expect(explanation?.detail).toBe('a sentence only the controller knows how to write');
    expect(explanation?.measurements.map((row) => row.name)).toEqual(['some_new_margin_mm', 'a_direction']);
    expect(explanation?.observation_ids).toEqual(['obs-new-0001']);
  });

  it('says the record does not carry a reason at all, rather than filling one in', () => {
    const explanation = explainFailure(stateAfter(finished({ outcome: 'failed' })));
    expect(explanation?.unexplained).toBe(true);
    expect(explanation?.reason_code).toBeNull();
    expect(explanation?.detail).toBeNull();
    expect(explanation?.measurements).toEqual([]);
  });
});

describe('a reason code this console does know', () => {
  it('carries the gloss AND the code, so the machine-readable word is never hidden', () => {
    const label = failureCodeLabel('search_placement_infeasible');
    expect(label).toContain('search_placement_infeasible');
    expect(label).toContain('no reachable placement');
  });

  it('glosses the near-block refusal without naming a value, so the measurement table carries the numbers', () => {
    const label = failureCodeLabel('search_cube_inside_pregrasp_reach');
    expect(label).toContain('search_cube_inside_pregrasp_reach');
    expect(label).toContain('nearer than the pregrasp can reach');
    expect(label).not.toMatch(/[0-9]/);
    const state = stateAfter(
      finished({
        reason_code: 'search_cube_inside_pregrasp_reach',
        failure: {
          detail: 'SEARCH: the block stands 31.5 mm nearer than the pregrasp can reach; nothing was picked up',
          measurements: [
            { name: 'cube_out_mm', value: -44.5, unit: 'mm' },
            { name: 'min_cube_out_mm', value: -13, unit: 'mm' },
            { name: 'move_block_at_least_mm', value: 57.5, unit: 'mm' },
          ],
          observation_ids: [],
        },
      }),
    );
    const explanation = explainFailure(state);
    expect(explanation?.recognised).toBe(true);
    expect(explanation?.sentence).not.toMatch(/[0-9]/);
    expect(explanation?.measurements.find((row) => row.name === 'move_block_at_least_mm')?.value).toBe(57.5);
  });

  it('reads the real fixture the controller writes for a refusal before the pickup', () => {
    const event = failedWithAReason as unknown as ContractEvent;
    const explanation = explainFailure(stateAfter(event));
    expect(explanation?.outcome).toBe('failed');
    expect(explanation?.phase).toBe('SEARCH');
    expect(explanation?.reason_code).toBe('search_placement_infeasible');
    expect(explanation?.recognised).toBe(true);
    // The controller's own sentence, verbatim. This console writes none of its own in its place.
    expect(explanation?.detail).toContain('nothing was picked up');
    expect(explanation?.measurements.find((row) => row.name === 'anchor_margin_mm')?.value).toBe(7.5);
    expect(explanation?.observation_ids).toEqual(['synthetic-obs-0001']);
  });
});

describe('the observations the stop named, apart from the ones its false predicates read (verdict 2026-09-27 item 9)', () => {
  it("lists under the stop's named evidence only the ids in failure.observation_ids, and the predicates' frames apart", () => {
    // Run m6ed2dd9's shape: the stop (base_not_zero_before_segment) names no observation; the false predicate
    // straddle_far_edge_row read the newest wrist frame.
    const events = buildStoppedHoldingRun(2, 100, 1_000_000_000).steps.map((step) => step.event);
    const state = foldEvents(initialState(), events);
    const read = state.decision_frame?.primary_observation_id ?? '';
    expect(read).not.toBe('');
    const explanation = explainFailure(state);
    expect(explanation?.observation_ids).toEqual([]);
    expect(explanation?.predicate_observation_ids).toContain(read);
  });

  it('never lists a named id twice: one the stop named is not repeated among the predicates\' frames', () => {
    const predicateEvent = infeasibleBeforeThePickup as unknown as ContractEvent;
    const consumed = (predicateEvent.payload as unknown as { evidence: { observation_ids: string[] }[] }).evidence.flatMap((piece) => piece.observation_ids);
    expect(consumed.length).toBeGreaterThan(0);
    let state = applyEvent(initialState(), predicateEvent);
    state = applyEvent(state, {
      ...finished({ reason_code: 'search_placement_infeasible', phase: 'SEARCH', failure: { detail: 'd', measurements: [], observation_ids: [consumed[0] ?? ''] } }),
      seq: predicateEvent.seq + 1,
    });
    const explanation = explainFailure(state);
    expect(explanation?.observation_ids).toEqual([consumed[0]]);
    expect(explanation?.predicate_observation_ids).not.toContain(consumed[0]);
  });
});

describe('measured values against their thresholds', () => {
  it('takes the threshold from a failing predicate and leaves it unavailable when none was recorded', () => {
    const predicateEvent = infeasibleBeforeThePickup as unknown as ContractEvent;
    let state = applyEvent(initialState(), predicateEvent);
    // The run finished AFTER the predicate it reports on. The reducer now ignores a record whose seq it has already
    // passed, so a record folded second has to carry a seq that says it came second; this fixture brings its own.
    state = applyEvent(state, { ...finished({ reason_code: 'search_placement_infeasible', phase: 'SEARCH' }), seq: predicateEvent.seq + 1 });
    const explanation = explainFailure(state);
    const row = explanation?.measurements.find((item) => item.name === 'placement_plan');
    expect(row?.source).toBe('predicate');
    expect(row?.value).toBe(1.5);
    expect(row?.threshold).toBe(37.96);
    expect(row?.unit).toBe('mm');
    // The predicate's own extra measurements are folded too, so the refusal can be read in full.
    expect(state.predicates[0]?.measurements.map((item) => item.name)).toContain('bounded_base_advance_limit_mm');
  });

  it('never turns a predicate with no numbers into a comparison', () => {
    let state = applyEvent(initialState(), {
      ...envelope(),
      kind: 'predicate.evaluated',
      payload: {
        predicate: 'a_predicate_without_numbers',
        value: false,
        threshold: null,
        measured: null,
        unit: null,
        evidence: [],
        consumed_source_stamp: null,
      },
    } as ContractEvent);
    state = applyEvent(state, finished({ reason_code: 'grasp_missed', phase: 'GRASP_CLOSE' }));
    const explanation = explainFailure(state);
    expect(explanation?.measurements).toEqual([]);
    // It is still on the record as a predicate that is false, which is the whole of what was measured about it.
    expect(explanation?.failing_predicates.map((item) => item.predicate)).toEqual(['a_predicate_without_numbers']);
  });
});

describe('the sentence for a code, with the values the raise site carried', () => {
  it('states the numbers when the record carried every one the sentence names', () => {
    const state = stateAfter(
      finished({
        reason_code: 'retention_not_demonstrated',
        phase: 'OBJECT_SECURED',
        failure: {
          detail: 'retention NOT demonstrated: the image between the jaws changed with height',
          measurements: [
            { name: 'retention_score', value: 0.31, unit: null },
            { name: 'retention_min_score', value: 0.55, unit: null },
          ],
          observation_ids: [],
        },
      }),
    );
    const explanation = explainFailure(state);
    expect(explanation?.sentence).toContain('0.31');
    expect(explanation?.sentence).toContain('0.55');
    // The wordy gloss is still what a code is glossed as; the measured sentence is the one with the record in it.
    expect(explanation?.gloss).toBe('holding the cube could not be demonstrated, so the arm kept it and stopped');
  });

  it('falls back to the sentence without numbers when the record carried only one of them', () => {
    const state = stateAfter(
      finished({
        reason_code: 'retention_not_demonstrated',
        phase: 'OBJECT_SECURED',
        failure: { detail: 'retention NOT demonstrated', measurements: [{ name: 'retention_score', value: 0.31, unit: null }], observation_ids: [] },
      }),
    );
    expect(explainFailure(state)?.sentence).toBe('holding the cube could not be demonstrated, so the arm kept it and stopped');
  });

  // THE RECORDED ALIGN FAILURE. The controller named two causes the same record refutes. Both sides changed: the
  // controller now passes the recognizer's notes through, and this reads them.
  it("states the recognizer's own reason for an align failure, in its own words", () => {
    const state = stateAfter(
      finished({
        reason_code: 'align_cube_not_detected',
        phase: 'ALIGN',
        failure: {
          detail: "ALIGN: the cube is not detected in the pregrasp view. The recognizer's own reason: ...",
          measurements: [
            { name: 'attempt', value: 0, unit: null },
            {
              name: 'recognizer_reason',
              value: "proposal 'a yellow cube' 0.664 [428, 2, 787, 386]: rejected, box side 0.30 of image width: too large for the cube on this image path",
              unit: null,
            },
          ],
          observation_ids: ['obs-20260922T194245Z-wrist-0001'],
        },
      }),
    );
    const sentence = explainFailure(state)?.sentence ?? '';
    expect(sentence).toContain('too large for the cube on this image path');
    expect(sentence).toContain('0.664');
    // The withdrawn guess must not come back through the gloss or the sentence.
    expect(sentence).not.toContain('coarse geometry');
    expect(sentence).not.toContain('occluded');
  });

  it('still states the measured sentence for an align failure recorded before the reason was passed through', () => {
    const state = stateAfter(
      finished({
        reason_code: 'align_cube_not_detected',
        phase: 'ALIGN',
        failure: { detail: 'ALIGN: the cube is not detected in the pregrasp view', measurements: [{ name: 'attempt', value: 2, unit: null }], observation_ids: [] },
      }),
    );
    // No recognizer_reason in the record: the sentence keeps its measured form rather than dropping to the gloss.
    expect(explainFailure(state)?.sentence).toBe('the cube was not detected in the pre-grasp view on attempt 2');
  });

  it('substitutes nothing at all into a code this console has no sentence for', () => {
    const state = stateAfter(
      finished({
        reason_code: 'a_code_from_a_newer_controller',
        failure: { detail: 'a sentence only the controller knows', measurements: [{ name: 'retention_score', value: 0.31, unit: null }], observation_ids: [] },
      }),
    );
    expect(explainFailure(state)?.sentence).toBeNull();
    expect(failureCodeLabel('a_code_from_a_newer_controller')).toBe('a_code_from_a_newer_controller');
  });
});

describe('what can be done next after a failure', () => {
  function capability(extra: Partial<Capability>): Capability {
    return { id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false, ...extra };
  }

  it('lists a blocked capability with the server reason and no offer to run it', () => {
    const actions = nextActionsFor('retention_not_demonstrated', [
      capability({ id: 'observe', state: 'blocked', reason_code: 'run_active', reason: 'a run is active: only stop is accepted' }),
      capability({ id: 'return_arm' }),
    ]);
    expect(actions.map((item) => item.capability_id)).toEqual(['observe', 'return_arm']);
    expect(actions[0]?.enabled).toBe(false);
    expect(actions[0]?.reason).toBe('a run is active: only stop is accepted');
    expect(actions[1]?.enabled).toBe(true);
    expect(actions[1]?.reason).toBeNull();
  });

  it('offers nothing for a reason code this console has no next step for, rather than guessing one', () => {
    expect(nextActionsFor('a_code_from_a_newer_controller', [capability({})])).toEqual([]);
  });

  it('offers nothing when the snapshot carried no capability list', () => {
    expect(nextActionsFor('search_no_cube', [])).toEqual([]);
  });

  it('never offers to start a cycle while the record says the cube is still in the gripper', () => {
    const held = nextActionsFor('release_not_admitted', [capability({ id: 'observe' }), capability({ id: 'return_arm' }), capability({ id: 'start_cycle' })]);
    expect(held.map((item) => item.capability_id)).toEqual(['observe', 'return_arm']);
    // With an empty gripper a cycle IS offered, and the arm is brought back first because a cycle has to start from
    // the low scan pose and a run that stopped part way did not leave it there.
    const empty = nextActionsFor('search_no_cube', [capability({ id: 'observe' }), capability({ id: 'return_arm' }), capability({ id: 'start_cycle' })]);
    expect(empty.map((item) => item.capability_id)).toEqual(['observe', 'return_arm', 'start_cycle']);
  });

  it('reaches the explanation from the capability list the snapshot carried', () => {
    const state = {
      ...stateAfter(finished({ reason_code: 'grasp_missed', phase: 'GRASP_CLOSE' })),
      capabilities: [capability({ id: 'observe', state: 'unknown' })],
    };
    const actions = explainFailure(state)?.next_actions ?? [];
    expect(actions).toHaveLength(1);
    expect(actions[0]?.enabled).toBe(false);
  });
});

describe('when there is nothing to explain', () => {
  it('says nothing at all before any run has reported an outcome', () => {
    expect(explainFailure(initialState())).toBeNull();
  });

  it('says nothing about a run that ended as it should', () => {
    const state = stateAfter(
      finished({ outcome: 'placed_new', exit_status: 0, predicates: { object_secured: true, placed: true, clearance_target_met: true, base_returned: true } }),
    );
    expect(explainFailure(state)).toBeNull();
  });

  it('says nothing about an already-placed no-op, which is a success', () => {
    expect(explainFailure(stateAfter(finished({ outcome: 'already_placed_noop', exit_status: 0 })))).toBeNull();
  });

  it('explains a stopped and a refused run as well as a failed one', () => {
    for (const outcome of ['stopped', 'refused', 'unknown'] as const) {
      const explanation = explainFailure(stateAfter(finished({ outcome, reason_code: 'operator_abort', phase: 'CARRY' })));
      expect(explanation?.outcome, outcome).toBe(outcome);
      expect(explanation?.recognised, outcome).toBe(true);
    }
  });
});

// AFTER A FAILURE THE ARM IS WHEREVER IT STOPPED. The controller refuses a cycle that does not start from the low
// scan pose (start_pose_not_low_scan: the lowest link must clear 0.10 m and the gripper must be in its normal-open
// region), so a next-step list that offered only "start a cycle" offered something that would refuse.
describe('returning the arm is offered before starting again', () => {
  function capabilities(): Capability[] {
    return ['observe', 'return_arm', 'start_cycle'].map(
      (id) => ({ id, state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false }) as Capability,
    );
  }

  it('offers return arm after a failure that left the gripper empty', () => {
    const actions = nextActionsFor('align_cube_not_detected', capabilities());
    expect(actions.map((a) => a.capability_id)).toEqual(['observe', 'return_arm', 'start_cycle']);
  });

  it('puts it before starting a cycle, and says why a cycle needs it', () => {
    const actions = nextActionsFor('search_no_cube', capabilities());
    const ids = actions.map((a) => a.capability_id);
    expect(ids.indexOf('return_arm')).toBeLessThan(ids.indexOf('start_cycle'));
    expect(actions.find((a) => a.capability_id === 'return_arm')?.why).toContain('where a cycle has to start from');
    expect(actions.find((a) => a.capability_id === 'start_cycle')?.why).toContain('once the arm is back at its start pose');
  });

  it('does not offer a cycle at all after a failure that left the cube held', () => {
    const actions = nextActionsFor('retention_not_demonstrated', capabilities());
    const ids = actions.map((a) => a.capability_id);
    expect(ids).toContain('return_arm');
    expect(ids).not.toContain('start_cycle');
    // The wording for a held cube is its own: the arm is brought back WITH the cube.
    expect(actions.find((a) => a.capability_id === 'return_arm')?.why).toContain('cube still held');
  });
});

describe('the one-block mission codes read as operator sentences, never as bare codes', () => {
  const MISSION_CODES = [
    'search_no_cube',
    'search_no_cube_after_sweep',
    'mission_binding_no_candidate',
    'mission_binding_candidate_not_unique',
    'mission_capability_required',
    'mission_fallback_capability_required',
    'mission_arm_only_insufficient_while_held',
    'mission_mode_changed_while_held',
    // The controller's LIVE_FORWARD_EXCLUDED constant: the code string it writes, not the constant's name.
    'synthetic_observation_excluded_from_live',
  ];

  it('has a sentence for each of them, with the code kept beside it', () => {
    for (const code of MISSION_CODES) {
      expect(describeFailureCode(code), code).not.toBeNull();
      expect(failureCodeLabel(code), code).toContain(code);
      // Operator words: none of the sentences leans on the controller's mode tokens or its function names.
      for (const jargon of ['BASE_ADVANCE', 'ARM_ONLY', 'PAD_FRAME', 'contact_px', 'fk_', 'placement_plan']) {
        expect(describeFailureCode(code), `${code} says ${jargon}`).not.toContain(jargon);
      }
    }
  });

  it('states the binding numbers the raise site carried, and says nothing was picked up', () => {
    const state = stateAfter(
      finished({
        reason_code: 'mission_binding_no_candidate',
        phase: 'SEARCH',
        failure: {
          detail: 'BINDING: target cube needs exactly one candidate and this SEARCH measured 0; nothing was picked up',
          measurements: [
            { name: 'matching_candidates', value: 0, unit: null },
            { name: 'candidates', value: 0, unit: null },
            { name: 'cubes_seen', value: 1, unit: null },
            { name: 'placed_cubes', value: 1, unit: null },
          ],
          observation_ids: [],
        },
      }),
    );
    const sentence = explainFailure(state)?.sentence ?? '';
    expect(sentence).toContain('1 cube');
    expect(sentence).toContain('already placed');
    expect(sentence).toContain('nothing was picked up');
  });

  it('says the held cube stays held when the arm alone falls short, with the margins the record carried', () => {
    const state = stateAfter(
      finished({
        reason_code: 'mission_arm_only_insufficient_while_held',
        phase: 'ARM_ONLY_INSUFFICIENT',
        failure: {
          detail: 'ARM_ONLY_INSUFFICIENT: the loaded reach falls short and this run carries no capability; the cube stays held',
          measurements: [
            { name: 'fk_travel_from_grasp_mm', value: 40.3, unit: 'mm' },
            { name: 'worst_case_centre_margin_mm', value: 2.1, unit: 'mm' },
            { name: 'required_centre_margin_mm', value: 37.96, unit: 'mm' },
          ],
          observation_ids: [],
        },
      }),
    );
    const sentence = explainFailure(state)?.sentence ?? '';
    expect(sentence).toContain('2.1 mm');
    expect(sentence).toContain('37.96 mm');
    expect(sentence).toContain('stays held');
  });

  it('offers the next step by whether the cube is in the gripper: never a cycle while it is held', () => {
    const capability = (extra: Partial<Capability>): Capability => ({ id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false, ...extra });
    const list = [capability({ id: 'observe' }), capability({ id: 'return_arm' }), capability({ id: 'start_cycle' })];
    for (const code of ['mission_arm_only_insufficient_while_held', 'mission_mode_changed_while_held', 'synthetic_observation_excluded_from_live']) {
      expect(nextActionsFor(code, list).map((item) => item.capability_id), code).toEqual(['observe', 'return_arm']);
    }
    for (const code of ['search_no_cube_after_sweep', 'mission_binding_no_candidate', 'mission_binding_candidate_not_unique']) {
      expect(nextActionsFor(code, list).map((item) => item.capability_id), code).toEqual(['observe', 'return_arm', 'start_cycle']);
    }
    // A capability refusal ends the measuring run; the mission itself resumes the target, so only a look is offered.
    expect(nextActionsFor('mission_capability_required', list).map((item) => item.capability_id)).toEqual(['observe']);
    expect(nextActionsFor('mission_fallback_capability_required', list).map((item) => item.capability_id)).toEqual(['observe']);
  });
});
