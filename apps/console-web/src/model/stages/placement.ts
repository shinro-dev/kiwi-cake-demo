// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE PLACEMENT STAGE: the placement plan, the carry, the base advance, the release admission and where the cube ended up.
//
// One of the five stage files (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0b; the shape and the rules
// are in ./stage.ts). The owner of this stage's policy is track P (placement): it edits this file and no other stage's.
// Every code below is a raise site of bin/laptop/manipulation/run_single_cube_cycle.py (or of the runtime, where the
// comment says so), glossed in the words an operator uses; every state is one the controller emits, with the sentence
// for it, the phase it is grouped in (phase.ts) and the step of the five it is drawn under.

import { argText } from './stage';
import type { StageGlossary, TemplateArgs } from './stage';

// THE BINDING LIMIT of a placement refusal (autonomy/manipulation/placement_envelope.py): the constraint the controller
// names, in an operator's words. A constraint not listed here is not stated at all.
export const PLACEMENT_CONSTRAINT_WORDS: Readonly<Record<string, string>> = {
  side_edge: 'an edge the reach axis runs along stays short',
  along_budget: 'the base advance along the reach axis is past its budget',
  far_edge: 'advancing only shortens the margin on the pad edge the reach axis runs toward, which is short',
  arm_reach: 'the arm alone falls short',
  pickup_inside_grasp_radius: 'the block stands nearer than the pregrasp can reach',
  lateral_needs_capability: 'only a sideways base move would place it',
};

/**
 * The clause a placement refusal adds when its record names the binding limit: the constraint in words, the deficit the
 * record states and the controller's hint, each only when the record carried it (a deficit of zero, which a refusal only the
 * 1 mm advance grid holds can round to, is not stated); '' when it names no known constraint. A hint the record marks as
 * not checked to place the block (hint_places_block false) is the named limit's own move, and is said to be that only.
 */
export function placementLimitClause(args: TemplateArgs): string {
  const constraint = args['constraint'];
  if (constraint === undefined || typeof constraint.value !== 'string') return '';
  const words = PLACEMENT_CONSTRAINT_WORDS[constraint.value];
  if (words === undefined) return '';
  const deficit = args['deficit_mm'];
  const by = deficit === undefined || deficit.value === null || deficit.value === 0 ? '' : ` by ${argText(args, 'deficit_mm')}`;
  const hint = args['hint'];
  const unchecked = args['hint_places_block']?.value === false ? 'for this limit only: ' : '';
  const advice = hint === undefined || typeof hint.value !== 'string' || hint.value === '' ? '' : `; ${unchecked}${hint.value}`;
  return `; the binding limit: ${words}${by}${advice}`;
}

// WHY AN ADDITIVE PLACEMENT POLICY DID NOT PLACE THE HELD BLOCK (autonomy/manipulation/reach_carry.py, REFUSALS): what a
// refusal record names as refused_by, in an operator's words. A value not listed here is not stated at all.
export const REACH_REFUSAL_WORDS: Readonly<Record<string, string>> = {
  carry_short: 'the carry ended short of every travel the release admission takes',
  hold_not_steady: 'the lowered arm did not hold still before the release',
  release_too_high: 'the lowered cube stood higher than a release is admitted',
  margin_short: 'the release there left the cube centre nearer the pad edge than required',
  replan_short: 'from where the measured advance ended, no travel of the arm alone was admitted',
};

// WHY THE WAY BACK OF A LOADED ARM HELD IT WHERE IT STANDS (autonomy/manipulation/reach_carry.py, THE WAY BACK UNDER LOAD):
// what a reach_way_back_held record names as held_by, in an operator's words. A value not listed here is not stated at all.
export const WAY_BACK_HOLD_WORDS: Readonly<Record<string, string>> = {
  no_step: 'no step back stayed inside what the records show the loaded arm doing',
  joint_error: 'a step measured a lift joint off its target by more than the bound',
  sag: 'a step measured the held cube lower than before it',
  regression: 'the arm did not follow a step of the way back',
};

/** The words a reach_way_back_held record's held_by adds, in parentheses; '' when the record names none this file knows. */
export function wayBackHoldClause(args: TemplateArgs): string {
  const held = args['held_by'];
  if (held === undefined || typeof held.value !== 'string') return '';
  const words = WAY_BACK_HOLD_WORDS[held.value];
  return words === undefined ? '' : ` (${words})`;
}

/** The words a refusal record's refused_by adds, in parentheses; '' when the record names none this file knows. */
export function reachRefusalClause(args: TemplateArgs): string {
  const refusal = args['refused_by'];
  if (refusal === undefined || typeof refusal.value !== 'string') return '';
  const words = REACH_REFUSAL_WORDS[refusal.value];
  return words === undefined ? '' : ` (${words})`;
}

export const PLACEMENT_STAGE: StageGlossary = {
  stage: 'placement',
  failures: {
    search_placement_infeasible: {
      gloss: 'no reachable placement was found before anything was picked up',
      needs: ['anchor_margin_mm', 'required_centre_margin_mm'],
      measured: (args) =>
        `no reachable placement was found before anything was picked up: the best anchor left the cube centre ${argText(args, 'anchor_margin_mm')} from the nearest pad edge, against the ${argText(args, 'required_centre_margin_mm')} required${placementLimitClause(args)}`,
    },
    // THE EXECUTOR GUARD (autonomy/manipulation/placement_selector.py): an additive placement policy (arm_reach,
    // advance_then_reach) is planned by a build before that build executes it. When the plan that moves the base least is
    // such a policy, SEARCH refuses before anything is picked up, so no block is lifted that the placement stage could
    // not then place.
    search_placement_policy_without_executor: {
      gloss: 'the placement that moves the base least is a policy this controller plans but does not execute yet, so nothing was picked up',
      needs: ['placement_policy', 'base_advance_mm', 'fk_travel_mm'],
      measured: (args) =>
        `the placement that moves the base least is ${argText(args, 'placement_policy')}, a base advance of ${argText(args, 'base_advance_mm')} then ${argText(args, 'fk_travel_mm')} of arm travel, and this controller plans that policy but does not execute it yet, so nothing was picked up`,
    },
    // THE MISSION'S OWN REFUSALS WITH THE CUBE HELD: the controller's raise sites in mission mode after the grasp (the
    // binding's refusals, before anything is picked up, are in search.ts).
    mission_arm_only_insufficient_while_held: {
      gloss: 'with the cube held, the arm alone falls short of the pad, and this run carries no capability to move the base, so the cube stays held',
      needs: ['worst_case_centre_margin_mm', 'required_centre_margin_mm'],
      measured: (args) =>
        `with the cube held, the arm alone falls short of the pad: at full reach it would leave the cube centre ${argText(args, 'worst_case_centre_margin_mm')} from the nearest pad edge, against the ${argText(args, 'required_centre_margin_mm')} required, and this run carries no capability to move the base, so the cube stays held`,
    },
    mission_mode_changed_while_held: {
      gloss:
        'with the cube held, the measured grasp put the placement on the other side of the line between the arm alone and the base than this target was bound to, and neither way is taken with a cube held, so the cube stays held',
      needs: [],
    },
    // The controller's LIVE_FORWARD_EXCLUDED, spelled as the code string it writes. A LIVE run started by hand, with no
    // mission target, never drives the base: its base observation would be the synthetic one.
    synthetic_observation_excluded_from_live: {
      gloss: 'placing this cube needs the base, and a live run with no mission target never moves the base (it has no real observation to move it by), so the cube stays held',
      needs: [],
    },
    placement_infeasible_while_held: {
      gloss: 'with the cube held, no reachable placement was left',
      needs: ['anchor_margin_mm', 'required_centre_margin_mm'],
      measured: (args) =>
        `with the cube held, no reachable placement was left: the best anchor left the cube centre ${argText(args, 'anchor_margin_mm')} from the nearest pad edge, against the ${argText(args, 'required_centre_margin_mm')} required${placementLimitClause(args)}`,
    },
    place_approach_no_pose: { gloss: 'no pose above the release point passed the guards, so the cube stayed held', needs: [] },
    // THE ADDITIVE PLACEMENT POLICIES ONCE THE CUBE IS HELD (autonomy/manipulation/placement_executor.py,
    // reach_carry.py and the policies' executors, under the words arm_reach and advance_then_reach). Each raise site has
    // a code of its own, reach_...; every one ends with the cube held. A refused release brings the loaded arm back to the
    // carry pose only where every step of the way back stays inside what the records show the loaded arm doing; where it
    // cannot step, the arm holds the cube where it stands and the next placement policy, which starts only from the carry
    // pose, does not run (reach_way_back_held: the lead's ruling of 2026-10-02 on operator question Q7). So
    // reach_way_back_held, the two retract codes and reach_release_hold_lost leave the arm where it stands, and the others
    // end with the arm near the grasp radius: at the held pose, at the carry pose or at the low scan pose.
    reach_placement_infeasible_while_held: {
      gloss: 'with the cube held, no placement policy of this run places the block from the measured grasp',
      needs: ['anchor_margin_mm', 'required_centre_margin_mm'],
      measured: (args) =>
        `with the cube held, no placement policy of this run places the block from the measured grasp: the block's centre stands ${argText(args, 'anchor_margin_mm')} from the nearest pad edge, against the ${argText(args, 'required_centre_margin_mm')} a release needs${placementLimitClause(args)}`,
    },
    reach_mode_changed_while_held: {
      gloss:
        'with the cube held, every plan that places the block from the measured grasp lies on the other side of the line between the arm alone and the base than this run was bound to, and this run may not cross that line with the cube held, so the cube stays held',
      needs: [],
    },
    reach_approach_no_pose: {
      gloss: 'after the measured base advance, no pose above the release point passed the guards, so the cube stayed held',
      needs: [],
    },
    reach_arm_only_insufficient_while_held: {
      gloss:
        'with the cube held, the arm alone did not place the block and was brought back to the carry pose, and the policies left place it only by moving the base, which this run carries no capability to move, so the cube stays held',
      needs: ['fk_travel_from_grasp_mm'],
      measured: (args) =>
        `with the cube held, the arm alone did not place the block${reachRefusalClause(args)}: it reached ${argText(args, 'fk_travel_from_grasp_mm')} beyond the grasp and was brought back to the carry pose, and the policies left place it only by moving the base, which this run carries no capability to move, so the cube stays held`,
    },
    // The controller names the policies it asked from where the arm stands (the advance member again first, then the
    // proven policy) and the advance budget left; none of them places the block by moving the base within it.
    reach_policies_exhausted_while_held: {
      gloss: 'with the cube held, none of the placement policies asked from where it stands places the block by moving the base within what is left of the advance budget, so the cube stays held at the carry pose',
      needs: ['policies_tried', 'policies_asked', 'advance_budget_left_mm'],
      measured: (args) =>
        `with the cube held, ${argText(args, 'policies_tried')} did not place the block, and none of the placement policies asked from where it stands (${argText(args, 'policies_asked')}) places it by moving the base within the ${argText(args, 'advance_budget_left_mm')} left of the advance budget, so the cube stays held at the carry pose`,
    },
    // The proven policy going on after a measured advance of another policy: the run's own advance passed the bounded
    // total on a pulse, the check the frozen loop makes for its own total alone (placement_executor._cumulative_rule).
    reach_base_advanced_past_bound: {
      gloss: "with the cube held, the base had travelled more than the bounded total over this run's advances, so nothing further was commanded",
      needs: ['advanced_mm', 'bounded_total_advance_mm'],
      measured: (args) =>
        `with the cube held, the base had travelled ${argText(args, 'advanced_mm')} over this run's advances, more than the bounded total of ${argText(args, 'bounded_total_advance_mm')}, so nothing further was commanded`,
    },
    // advance_then_reach asked again after its own advance: a pulse of its further advance carried the run's own advance
    // past the bounded total, the check its rule makes for the whole run (policy_advance_then_reach.remaining_need_rule).
    reach_further_advance_past_bound: {
      gloss: "with the cube held, a further base advance carried the base past the bounded total over this run's advances, so nothing further was commanded",
      needs: ['advanced_mm', 'bounded_total_advance_mm'],
      measured: (args) =>
        `with the cube held, a further base advance carried the base to ${argText(args, 'advanced_mm')} over this run's advances, more than the bounded total of ${argText(args, 'bounded_total_advance_mm')}, so nothing further was commanded`,
    },
    reach_retract_refused: {
      gloss: 'with the cube held, bringing the extended arm back to the carry pose was refused, so the arm holds the cube where it stands',
      needs: ['measured_out_mm', 'grasp_out_mm'],
      measured: (args) =>
        `with the cube held, bringing the extended arm back to the carry pose was refused: the tool stands ${argText(args, 'measured_out_mm')} beyond the proven grasp radius, against the grasp at ${argText(args, 'grasp_out_mm')}, so the arm holds the cube where it stands`,
    },
    reach_retract_incomplete: {
      gloss: 'with the cube held, the extended arm did not come back to the carry pose in its bounded steps, so it holds the cube where it stands',
      needs: ['measured_out_mm', 'grasp_out_mm'],
      measured: (args) =>
        `with the cube held, the extended arm did not come back to the carry pose in its bounded steps: the tool still stands ${argText(args, 'measured_out_mm')} beyond the proven grasp radius, against the grasp at ${argText(args, 'grasp_out_mm')}, so it holds the cube where it stands`,
    },
    reach_way_back_held: {
      gloss: 'with the cube held, the way back of the loaded arm stopped where its next step would have left what the records show the loaded arm doing, or where a step measured the arm not following it, so the arm holds the cube where it stands for the operator to take, and the next placement policy, which starts only from the carry pose, did not run',
      needs: ['measured_out_mm', 'measured_up_mm', 'grasp_out_mm'],
      measured: (args) =>
        `with the cube held, the way back of the loaded arm stopped${wayBackHoldClause(args)}: the tool stands ${argText(args, 'measured_out_mm')} beyond the proven grasp radius and ${argText(args, 'measured_up_mm')} above the grasp height, against the grasp at ${argText(args, 'grasp_out_mm')}, so the arm holds the cube where it stands for the operator to take, and the next placement policy, which starts only from the carry pose, did not run`,
    },
    reach_release_hold_lost: {
      gloss: 'while the lowered cube was held still before the release, the gripper left its holding band, so nothing was released and no arm move can be commanded',
      needs: ['gripper', 'holding_band_min', 'holding_band_max'],
      measured: (args) =>
        `while the lowered cube was held still before the release, the gripper read ${argText(args, 'gripper')}, outside its holding band of ${argText(args, 'holding_band_min')} to ${argText(args, 'holding_band_max')}, so nothing was released and no arm move can be commanded`,
    },
    release_not_admitted: {
      gloss: 'releasing here was not admitted by the pad-relative evidence',
      needs: ['worst_case_centre_margin_mm', 'required_centre_margin_mm'],
      measured: (args) =>
        `releasing here was not admitted: the pad-relative evidence puts the cube centre at worst ${argText(args, 'worst_case_centre_margin_mm')} from the nearest pad edge, against the ${argText(args, 'required_centre_margin_mm')} required`,
    },
    placed_not_demonstrated: {
      gloss: 'the frozen perception from the low pose did not demonstrate the placement',
      needs: ['centre_to_edge_mm', 'placed_min_edge_margin_mm'],
      measured: (args) =>
        `the frozen perception from the low pose measured the cube centre ${argText(args, 'centre_to_edge_mm')} from the nearest pad edge, against the ${argText(args, 'placed_min_edge_margin_mm')} a placement needs`,
    },
    hold_observe_no_pad_frame: { gloss: 'the pad frame was not valid while holding, and the base never moves blind', needs: [] },
    base_advance_exceeds_bound: {
      gloss: 'the placement needed more base travel than the bounded total allows',
      needs: ['needed_mm', 'bounded_total_advance_mm'],
      measured: (args) =>
        `the placement needed ${argText(args, 'needed_mm')} of base travel, more than the bounded total of ${argText(args, 'bounded_total_advance_mm')}`,
    },
    base_advanced_past_bound: {
      gloss: 'the base had already travelled more than the bounded total',
      needs: ['advanced_mm', 'bounded_total_advance_mm'],
      measured: (args) =>
        `the base had already travelled ${argText(args, 'advanced_mm')}, more than the bounded total of ${argText(args, 'bounded_total_advance_mm')}`,
    },
    base_pulse_no_pad_frame_after: { gloss: 'the pad frame was not valid after the pulse, so the displacement is unknown', needs: [] },
    base_measurement_implausible: {
      gloss: 'the two pad frames do not measure a plausible bounded translation',
      needs: ['distortion_mm', 'max_distortion_mm'],
      measured: (args) =>
        `the two pad frames measure ${argText(args, 'distortion_mm')} of distortion, more than the ${argText(args, 'max_distortion_mm')} a plausible bounded translation allows`,
    },
    base_probe_no_pad_frame: { gloss: 'the pad frame was not valid for the base probe, and the base never moves blind', needs: [] },
    operator_abort_base_path: {
      gloss: 'an operator aborted during the base pulse',
      needs: ['advance_mm'],
      measured: (args) => `an operator aborted during the base pulse, after ${argText(args, 'advance_mm')} of measured travel`,
    },
    operator_abort_before_base_pulse: {
      gloss: 'an operator aborted before a further base pulse',
      needs: ['base_ahead_mm'],
      measured: (args) =>
        `an operator aborted before a further base pulse, with the base ${argText(args, 'base_ahead_mm')} ahead of where it started`,
    },
    // THE CAPABILITY'S AND THE RELEASE OFFSET'S RAISE SITES, all with the cube held (the capability relays forward pulses
    // only, and the release offset is planned before the release).
    approach_view_no_pad_frame: {
      gloss: 'the pad frame was not valid in the view the capability is fed, and the base never moves blind, so the cube stays held',
      needs: [],
    },
    operator_abort_before_capability_pulse: {
      gloss: 'an operator stop was seen before the capability commanded anything, so no base pulse was commanded',
      needs: [],
    },
    base_capability_output_refused: {
      gloss: 'the capability refused a base pulse; the base reads stopped and the arm held, and nothing further is commanded',
      needs: [],
    },
    release_offset_beyond_return_bound: {
      gloss: "the release offset would have turned the arm too far from the saved low pose's pan for a return to be planned, so the cube stays held",
      needs: [],
    },
    base_primitive_not_wired: { gloss: 'no base primitive is wired into this run', needs: [] },
    base_pulse_outside_its_bounds: {
      gloss: 'the requested base pulse was outside its validated bounds',
      needs: ['vx_mps', 'duration_s'],
      measured: (args) =>
        `the requested base pulse was outside its validated bounds: it asked for ${argText(args, 'vx_mps')} for ${argText(args, 'duration_s')}`,
    },
    base_pulse_refused_gripper: { gloss: 'the base pulse was refused because of the gripper state', needs: [] },
    base_pulse_refused_posture: { gloss: 'the base pulse was refused because of the arm posture', needs: [] },
    base_not_stationary_before: { gloss: 'the base was not confirmed stationary before the pulse', needs: [] },
    base_not_confirmed_after: { gloss: 'the base was not confirmed stationary after the pulse', needs: [] },
    base_arm_drifted: {
      gloss: 'the arm moved during the base translation, so the pad frames do not measure the base',
      needs: ['arm_drift_deg', 'arm_drift_tolerance_deg'],
      measured: (args) =>
        `the arm moved ${argText(args, 'arm_drift_deg')} during the base translation, past the ${argText(args, 'arm_drift_tolerance_deg')} it is allowed, so the pad frames do not measure the base`,
    },
  },
  states: {
    // Everything from deciding where the cube goes to reading where it ended up.
    //
    //   PLACEMENT_PLAN (:1073) plans the placement once the cube is held.
    //   HOLD_OBSERVE (:829) observes the pad while holding the cube, which is what the placement plan rests on.
    //   CARRY (:912, :1077) and CARRY_REACH_LIMIT (:920) carry the cube outward with the arm.
    //   ARM_ONLY_INSUFFICIENT (:1082) is the finding that the arm alone cannot reach the placement, which is what sends
    //     the mission to the base, so it is part of placing and not a failure group of its own.
    //   BASE_MEASURED (:806) measures the base speed with a timed pulse, and BASE_ADVANCE (:843) drives the base toward
    //     the placement. Both exist only to place the cube.
    //   PLACE_LOWER_REFUSED (:1108) is a refusal inside the placement, after which the cube stays held.
    //   RELEASE_ADMISSION (:1112), RELEASED (:1126) and PLACED (:1137) are the release decision, the release and the
    //     measurement of where the cube ended up.
    PLACEMENT_PLAN: { sentence: 'Planning where the cube is to be put down.', phase: 'place', step: 'carry' },
    HOLD_OBSERVE: { sentence: 'Observing the pad while holding the cube.', phase: 'place', step: 'carry' },
    CARRY: { sentence: 'Carrying the cube outward with the arm.', phase: 'place', step: 'carry' },
    CARRY_REACH_LIMIT: { sentence: 'The last arm step gained almost nothing: this is the reach limit.', phase: 'place', step: 'carry' },
    ARM_ONLY_INSUFFICIENT: { sentence: 'The arm alone cannot reach the planned placement.', phase: 'place', step: 'carry' },
    BASE_MEASURED: { sentence: 'Measuring the base speed with a timed pulse.', phase: 'place', step: 'carry' },
    BASE_ADVANCE: { sentence: 'Driving the base toward the placement.', phase: 'place', step: 'carry' },
    PLACE_LOWER_REFUSED: { sentence: 'Lowering the cube to place it was refused.', phase: 'place', step: 'place' },
    RELEASE_ADMISSION: { sentence: 'Deciding whether releasing the cube here is admitted.', phase: 'place', step: 'place' },
    // THE ADDITIVE PLACEMENT POLICIES (arm_reach, advance_then_reach): the choice once the cube is held, the measured
    // carry step by step, the reach planned again after a measured advance, the hold check before the release, the
    // retract of a loaded arm whose release was refused, and the placement policies the same run asks from the carry pose
    // the retract came back to.
    PLACEMENT_POLICY: { sentence: 'Choosing, from the measured grasp, the placement policy that moves the base least.', phase: 'place', step: 'carry' },
    REACH_CARRY: { sentence: 'Carrying the cube outward one measured step at a time.', phase: 'place', step: 'carry' },
    REACH_CARRY_STOPPED: { sentence: 'The measured carry ended, at the planned reach or short of it.', phase: 'place', step: 'carry' },
    REACH_REPLAN: { sentence: "Planning the arm's reach again from where the measured base advance ended.", phase: 'place', step: 'carry' },
    REACH_RETRACT_STEP: { sentence: 'Bringing the loaded arm back one bounded step, read before the next.', phase: 'place', step: 'carry' },
    REACH_RETRACT: { sentence: 'Bringing the extended arm back to the carry pose, still holding the cube.', phase: 'place', step: 'carry' },
    PLACEMENT_FALLBACK: { sentence: 'A placement policy handed the cube back at the carry pose without placing it, and the run asks the placement policies left from where it stands.', phase: 'place', step: 'carry' },
    RELEASE_HOLD: { sentence: 'Holding the lowered cube still, to check the arm is steady before the release.', phase: 'place', step: 'place' },
    RELEASED: { sentence: 'The gripper opened.', phase: 'place', step: 'place' },
    PLACED: { sentence: 'Checking where the cube ended up on the pad.', phase: 'place', step: 'place' },
  },
};
