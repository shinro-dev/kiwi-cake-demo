// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SPLIT CHANGES NOTHING A READER SEES. The reason glosses, the state sentences and the state-to-phase entries moved
// out of failure.ts, reason.ts and phase.ts into one file per stage (docs/autonomous-demo/overnight-plan-2026-09-26.md,
// track P0b), so each stage track edits its own file. The two tables below are the maps as they were BEFORE the split,
// captured from the code at the integration commit 8b8d5df: every gloss, every list of needed values, the measured
// sentence each template writes when every value is present, and every state's sentence, phase and presentation step.
// The aggregates the three files now build must equal them key for key; a stage track that changes one of these
// entries changes this table in the same commit, on purpose.

import { describe, expect, it } from 'vitest';
import { describeFailureCode, describeFailureCodeWith, FAILURE_REASON_CODES } from '../failure';
import { CONTROLLER_STATES, isTerminalState, phaseOfState, presentationStepOfState } from '../phase';
import { describeState, STATES_WITH_A_SENTENCE } from '../reason';
import type { TemplateArgs } from '../reason';
import { STAGE_FAILURES, STAGE_GLOSSARIES, STAGE_IDS, STAGE_STATES } from './index';

const PRE_SPLIT_FAILURES: Readonly<Record<string, { gloss: string; needs: readonly string[]; measured: string | null }>> = {
  start_pose_not_low_scan: {
    gloss: "the run refused to start: the arm was not at the saved low scan pose with the gripper in its normal-open region",
    needs: [],
    measured: null,
  },
  search_no_cube: {
    gloss: "no cube the robot could pick was seen from the low scan pose, so nothing was picked up",
    needs: [],
    measured: null,
  },
  search_no_cube_after_sweep: {
    gloss: "the robot looked through every sweep sector and saw no cube it could pick, so nothing was picked up and the arm is back at the centre sector",
    needs: [],
    measured: null,
  },
  mission_binding_no_candidate: {
    // Changed by the pad policy of 2026-09-29: the sweep residual is named.
    gloss: "no cube in view could be taken as this target, so nothing was picked up; when SEARCH sweeps, a block on the pad in its first view (an earlier block, or the pad's printed logo read as one) ends the sweep, so a block off the pad outside that view may not have been looked for",
    needs: ["cubes_seen", "placed_cubes"],
    measured: "no cube in view could be taken as this target: <cubes_seen> cube(s) were seen and <placed_cubes> of them are already placed on the pad; nothing was picked up; when SEARCH sweeps, a block on the pad in its first view (an earlier block, or the pad's printed logo read as one) ends the sweep, so a block off the pad outside that view may not have been looked for",
  },
  mission_binding_candidate_not_unique: {
    gloss: "more than one cube in view could be this target, and the robot picks only when exactly one can, so nothing was picked up",
    needs: ["matching_candidates"],
    measured: "<matching_candidates> cubes in view could be this target, and the robot picks only when exactly one can; nothing was picked up",
  },
  mission_capability_required: {
    gloss: "placing this cube needs the base to move, and this run carried no capability to move it, so nothing was picked up; the mission waits for the capability and comes back to this target once it is there",
    needs: [],
    measured: null,
  },
  mission_fallback_capability_required: {
    // Changed by verify r6 policy MF2: the base fallback runs only when the way back brings the loaded arm to the carry pose.
    gloss: "the arm alone places this cube; should its release be refused, the same run goes on by moving the base only if the loaded arm comes back to the carry pose, otherwise the arm holds the cube where it stands for the operator; moving the base needs the capability this run did not carry, so nothing was picked up; the mission waits for the capability and comes back to this target once it is there",
    needs: [],
    measured: null,
  },
  mission_arm_only_insufficient_while_held: {
    gloss: "with the cube held, the arm alone falls short of the pad, and this run carries no capability to move the base, so the cube stays held",
    needs: ["worst_case_centre_margin_mm", "required_centre_margin_mm"],
    measured: "with the cube held, the arm alone falls short of the pad: at full reach it would leave the cube centre <worst_case_centre_margin_mm> from the nearest pad edge, against the <required_centre_margin_mm> required, and this run carries no capability to move the base, so the cube stays held",
  },
  mission_mode_changed_while_held: {
    gloss: "with the cube held, the measured grasp put the placement on the other side of the line between the arm alone and the base than this target was bound to, and neither way is taken with a cube held, so the cube stays held",
    needs: [],
    measured: null,
  },
  synthetic_observation_excluded_from_live: {
    gloss: "placing this cube needs the base, and a live run with no mission target never moves the base (it has no real observation to move it by), so the cube stays held",
    needs: [],
    measured: null,
  },
  search_cube_not_in_pad_frame: {
    gloss: "a cube was seen, but the pad frame was not valid, so the distance to the pad was unknown",
    needs: [],
    measured: null,
  },
  search_no_pad_frame: {
    gloss: "the pad homography was missing from the perception record of the low pose",
    needs: [],
    measured: null,
  },
  search_placement_infeasible: {
    gloss: "no reachable placement was found before anything was picked up",
    needs: ["anchor_margin_mm", "required_centre_margin_mm"],
    measured: "no reachable placement was found before anything was picked up: the best anchor left the cube centre <anchor_margin_mm> from the nearest pad edge, against the <required_centre_margin_mm> required",
  },
  search_placement_policy_without_executor: {
    // Added by the reach policies of 2026-09-30 (placement_selector.refuse_without_executor): not in the pre-split maps.
    gloss: "the placement that moves the base least is a policy this controller plans but does not execute yet, so nothing was picked up",
    needs: ["placement_policy", "base_advance_mm", "fk_travel_mm"],
    measured: "the placement that moves the base least is <placement_policy>, a base advance of <base_advance_mm> then <fk_travel_mm> of arm travel, and this controller plans that policy but does not execute it yet, so nothing was picked up",
  },
  search_cube_inside_pregrasp_reach: {
    gloss: "the block stands nearer than the pregrasp can reach, so nothing was picked up; move it away from the robot",
    needs: [],
    measured: null,
  },
  pregrasp_no_non_contact_pose: {
    gloss: "no pre-grasp pose over the cube passed the guards without contact",
    needs: [],
    measured: null,
  },
  align_cube_not_detected: {
    gloss: "the cube was not detected in the pre-grasp view",
    needs: ["attempt"],
    measured: "the cube was not detected in the pre-grasp view on attempt <attempt>. The recognizer's own reason: <recognizer_reason>",
  },
  align_not_in_window_after_trims: {
    gloss: "the cube stayed outside the capture window after the bounded trims",
    needs: ["lateral_mm", "capture_window_max_mm"],
    measured: "the cube was still <lateral_mm> off the tool axis after the bounded trims, outside the <capture_window_max_mm> capture window",
  },
  align_no_non_contact_descent: {
    gloss: "no descent at this reach passed the guards without contact",
    needs: [],
    measured: null,
  },
  align_bounded_corrections_exhausted: {
    gloss: "alignment ran out of its bounded corrections",
    needs: ["attempts", "trims"],
    measured: "alignment ran out of its bounded corrections after <attempts> attempts and <trims> trims",
  },
  straddling_not_reached: {
    gloss: "the jaws never reached the grasp height around the cube",
    needs: ["tool_up_mm", "straddling_max_up_mm"],
    measured: "the jaws stopped <tool_up_mm> above the grasp height, where straddling needs <straddling_max_up_mm> or less",
  },
  grasp_missed: {
    gloss: "the gripper closed on nothing, three times",
    needs: ["closes", "gripper", "gripper_latch"],
    measured: "the gripper closed on nothing <closes> times: the jaw reached <gripper>, against the empty-latch value of <gripper_latch>",
  },
  retention_not_demonstrated: {
    gloss: "holding the cube could not be demonstrated, so the arm kept it and stopped",
    needs: ["retention_score", "retention_min_score"],
    measured: "holding the cube could not be demonstrated: the retention score was <retention_score> against the <retention_min_score> required, so the arm kept the cube and stopped",
  },
  placement_infeasible_while_held: {
    gloss: "with the cube held, no reachable placement was left",
    needs: ["anchor_margin_mm", "required_centre_margin_mm"],
    measured: "with the cube held, no reachable placement was left: the best anchor left the cube centre <anchor_margin_mm> from the nearest pad edge, against the <required_centre_margin_mm> required",
  },
  place_approach_no_pose: {
    gloss: "no pose above the release point passed the guards, so the cube stayed held",
    needs: [],
    measured: null,
  },
  release_not_admitted: {
    gloss: "releasing here was not admitted by the pad-relative evidence",
    needs: ["worst_case_centre_margin_mm", "required_centre_margin_mm"],
    measured: "releasing here was not admitted: the pad-relative evidence puts the cube centre at worst <worst_case_centre_margin_mm> from the nearest pad edge, against the <required_centre_margin_mm> required",
  },
  placed_not_demonstrated: {
    gloss: "the frozen perception from the low pose did not demonstrate the placement",
    needs: ["centre_to_edge_mm", "placed_min_edge_margin_mm"],
    measured: "the frozen perception from the low pose measured the cube centre <centre_to_edge_mm> from the nearest pad edge, against the <placed_min_edge_margin_mm> a placement needs",
  },
  base_not_returned: {
    gloss: "the cube is placed, but the base did not come back within its tolerance",
    needs: ["remaining_along_axis_mm", "base_return_tolerance_mm"],
    measured: "the cube is placed, but the base stopped <remaining_along_axis_mm> from where it started, outside its <base_return_tolerance_mm> tolerance",
  },
  hold_observe_no_pad_frame: {
    gloss: "the pad frame was not valid while holding, and the base never moves blind",
    needs: [],
    measured: null,
  },
  base_advance_exceeds_bound: {
    gloss: "the placement needed more base travel than the bounded total allows",
    needs: ["needed_mm", "bounded_total_advance_mm"],
    measured: "the placement needed <needed_mm> of base travel, more than the bounded total of <bounded_total_advance_mm>",
  },
  base_advanced_past_bound: {
    gloss: "the base had already travelled more than the bounded total",
    needs: ["advanced_mm", "bounded_total_advance_mm"],
    measured: "the base had already travelled <advanced_mm>, more than the bounded total of <bounded_total_advance_mm>",
  },
  base_return_no_pad_frame: {
    gloss: "the pad frame was not valid for the return, so the base stayed where it was",
    needs: ["advanced_mm"],
    measured: "the pad frame was not valid for the return, so the base stayed <advanced_mm> ahead of where it started",
  },
  base_pulse_no_pad_frame_after: {
    gloss: "the pad frame was not valid after the pulse, so the displacement is unknown",
    needs: [],
    measured: null,
  },
  base_measurement_implausible: {
    gloss: "the two pad frames do not measure a plausible bounded translation",
    needs: ["distortion_mm", "max_distortion_mm"],
    measured: "the two pad frames measure <distortion_mm> of distortion, more than the <max_distortion_mm> a plausible bounded translation allows",
  },
  base_probe_no_pad_frame: {
    gloss: "the pad frame was not valid for the base probe, and the base never moves blind",
    needs: [],
    measured: null,
  },
  operator_abort_base_path: {
    gloss: "an operator aborted during the base pulse",
    needs: ["advance_mm"],
    measured: "an operator aborted during the base pulse, after <advance_mm> of measured travel",
  },
  operator_abort_before_base_pulse: {
    gloss: "an operator aborted before a further base pulse",
    needs: ["base_ahead_mm"],
    measured: "an operator aborted before a further base pulse, with the base <base_ahead_mm> ahead of where it started",
  },
  base_primitive_not_wired: {
    gloss: "no base primitive is wired into this run",
    needs: [],
    measured: null,
  },
  base_pulse_outside_its_bounds: {
    gloss: "the requested base pulse was outside its validated bounds",
    needs: ["vx_mps", "duration_s"],
    measured: "the requested base pulse was outside its validated bounds: it asked for <vx_mps> for <duration_s>",
  },
  base_pulse_refused_gripper: {
    gloss: "the base pulse was refused because of the gripper state",
    needs: [],
    measured: null,
  },
  base_pulse_refused_posture: {
    gloss: "the base pulse was refused because of the arm posture",
    needs: [],
    measured: null,
  },
  base_not_stationary_before: {
    gloss: "the base was not confirmed stationary before the pulse",
    needs: [],
    measured: null,
  },
  base_not_confirmed_after: {
    gloss: "the base was not confirmed stationary after the pulse",
    needs: [],
    measured: null,
  },
  base_arm_drifted: {
    gloss: "the arm moved during the base translation, so the pad frames do not measure the base",
    needs: ["arm_drift_deg", "arm_drift_tolerance_deg"],
    measured: "the arm moved <arm_drift_deg> during the base translation, past the <arm_drift_tolerance_deg> it is allowed, so the pad frames do not measure the base",
  },
  step_refused_mid_cycle: {
    gloss: "a step was refused in the middle of the cycle",
    needs: [],
    measured: null,
  },
  saved_pose_missing: {
    gloss: "the saved low scan pose this run needs was not on the machine",
    needs: [],
    measured: null,
  },
  operator_abort: {
    gloss: "an operator aborted the run",
    needs: [],
    measured: null,
  },
};

const PRE_SPLIT_STATES: Readonly<Record<string, { sentence: string; phase: string; step: string | null }>> = {
  SEARCH: { sentence: "Looking for the cube from the low scan pose.", phase: "locate", step: "find" },
  SEARCH_SWEEP: { sentence: "Scanning for the block: the low-pose frame showed no cube, so the wrist looks through its sweep sectors.", phase: "locate", step: "find" },
  TARGET_SELECTED: { sentence: "The block in view was taken as this mission's target, and its placement mode was measured.", phase: "locate", step: "find" },
  DONE_ALREADY_PLACED: { sentence: "The cube was already inside the pad outline, so nothing was moved.", phase: "terminal", step: null },
  PREGRASP: { sentence: "Moving to the pre-grasp pose computed from the cube contact point.", phase: "align", step: "pick" },
  PREGRASP_BIAS_REDUCED: { sentence: "The outward bias of the pre-grasp was reduced to stay inside the reach.", phase: "align", step: "pick" },
  ALIGN: { sentence: "Turning to bring the cube onto the tool axis.", phase: "align", step: "pick" },
  APPROACH: { sentence: "Closing the remaining radial distance to the cube.", phase: "pick", step: "pick" },
  STRADDLING: { sentence: "The jaws are around the cube.", phase: "pick", step: "pick" },
  GRASP_CLOSE: { sentence: "Closing the gripper on the cube.", phase: "pick", step: "pick" },
  GRASP_RETRY_ADVANCE: { sentence: "The grasp did not hold, so the arm advanced and is trying again.", phase: "pick", step: "pick" },
  OBJECT_SECURED: { sentence: "Checking whether the cube is held.", phase: "verify_grip", step: "pick" },
  PLACEMENT_PLAN: { sentence: "Planning where the cube is to be put down.", phase: "place", step: "carry" },
  BASE_MEASURED: { sentence: "Measuring the base speed with a timed pulse.", phase: "place", step: "carry" },
  BASE_ADVANCE: { sentence: "Driving the base toward the placement.", phase: "place", step: "carry" },
  BASE_RETURN: { sentence: "Driving the base back toward where it started.", phase: "return", step: "return" },
  BASE_RETURNED: { sentence: "Checking how far the base is from where it started.", phase: "return", step: "return" },
  HOLD_OBSERVE: { sentence: "Observing the pad while holding the cube.", phase: "place", step: "carry" },
  CARRY: { sentence: "Carrying the cube outward with the arm.", phase: "place", step: "carry" },
  CARRY_REACH_LIMIT: { sentence: "The last arm step gained almost nothing: this is the reach limit.", phase: "place", step: "carry" },
  ARM_ONLY_INSUFFICIENT: { sentence: "The arm alone cannot reach the planned placement.", phase: "place", step: "carry" },
  RELEASE_ADMISSION: { sentence: "Deciding whether releasing the cube here is admitted.", phase: "place", step: "place" },
  PLACE_LOWER_REFUSED: { sentence: "Lowering the cube to place it was refused.", phase: "place", step: "place" },
  RELEASED: { sentence: "The gripper opened.", phase: "place", step: "place" },
  PLACED: { sentence: "Checking where the cube ended up on the pad.", phase: "place", step: "place" },
};

/**
 * THE ADDITIONS AFTER THE SPLIT, merged at integration from the stage tracks' staged glosses: track A's align_v2 codes
 * and states, track G's grasp words, track S's scene scan. Each is a raise site or a transition of the controller's
 * stage policies (autonomy/manipulation/policy_*.py). Pinned here on purpose, beside the maps before the split.
 */
const ADDED_FAILURES: Readonly<Record<string, { stage: string; gloss: string; needs: readonly string[]; measured: string | null }>> = {
  mission_binding_candidate_not_placeable: {
    stage: "search",
    // Changed by the pad policy of 2026-09-29: an auto target weighs every block off the pad.
    gloss: "no cube off the pad in view has a bounded placement from here: neither the arm alone nor the bounded base advance reaches the pad from it, so nothing was picked up",
    needs: [],
    measured: null,
  },
  align_cube_lost_after_reacquire: {
    stage: "align",
    gloss: "the block left the view and did not come back after the bounded steps back; the arm holds clear of it",
    needs: ["reacquires", "tool_up_mm"],
    measured: "the block left the view and did not come back after <reacquires> steps back; the arm holds <tool_up_mm> above the grasp height, clear of it",
  },
  align_descent_would_lose_view: {
    stage: "align",
    gloss: "the descent would have lost the block from the view, so it was not commanded; nothing was picked up",
    needs: ["predicted_top_px", "view_margin_px"],
    measured: "the descent would have left the block <predicted_top_px> from the top of the view, under the <view_margin_px> margin, so it was not commanded; nothing was picked up",
  },
  align_radial_not_in_band: {
    stage: "align",
    gloss: "the block stayed outside the radial band and no radial step corrected it; nothing was picked up",
    needs: ["radial_mm", "radial_band_mm"],
    measured: "the block read <radial_mm> radially, outside the <radial_band_mm> band, and no radial step corrected it; nothing was picked up",
  },
  approach_cube_beyond_grasp_range: {
    stage: "grasp",
    gloss: "the block was measured outside the grasp range this run allows, so nothing was closed short of it",
    needs: ["block_estimate_mm", "wanted_mm", "nearest_mm", "farthest_mm"],
    measured: "the block was measured at <block_estimate_mm>, so the grasp wanted <wanted_mm>, outside this run's grasp range of <nearest_mm> to <farthest_mm>; nothing was closed short of it",
  },
  grasp_retry_approach_refused: {
    stage: "grasp",
    gloss: "after an empty close the approach to grasp again was refused, so nothing is held and the jaws are open",
    needs: ["to_forward_mm", "closes"],
    measured: "after <closes> closes the approach to grasp again at <to_forward_mm> was refused; nothing is held and the jaws are open",
  },
  grasp_retry_cube_lost: {
    stage: "grasp",
    gloss: "after an empty close the block was not seen again, so nothing is held and the jaws are open above the floor",
    needs: ["closes"],
    measured: "after <closes> empty closes the block was not seen again, on two looks; nothing is held",
  },
  grasp_retry_lift_refused: {
    stage: "grasp",
    gloss: "after an empty close the open jaws could not lift high enough to look again",
    needs: ["tool_up_mm", "observe_up_mm"],
    measured: "after an empty close the open jaws stopped <tool_up_mm> above the grasp height, short of the <observe_up_mm> a look needs",
  },
  grasp_retry_no_untried_radius: {
    stage: "grasp",
    gloss: "every grasp radius near the measured block had already closed empty",
    needs: ["block_estimate_mm", "closes"],
    measured: "the block was measured at <block_estimate_mm> and every grasp radius near it had closed empty, after <closes> closes",
  },
  grasp_retry_not_aligned: {
    stage: "grasp",
    gloss: "after an empty close the block stayed off the jaws' capture window, so nothing was closed again",
    needs: ["lateral_mm", "trims"],
    measured: "after an empty close the block stayed <lateral_mm> off the capture window after <trims> pan trims; nothing is held",
  },
  grasp_set_down_lift_refused: {
    stage: "grasp",
    gloss: "the cube was set down and the jaws opened, but the open lift off it was refused; nothing is held",
    needs: ["tool_up_mm", "observe_up_mm"],
    measured: "the cube was set down and the jaws opened, but the open lift off it stopped <tool_up_mm> above the grasp height, short of <observe_up_mm>; nothing is held",
  },
  grasp_set_down_refused: {
    stage: "grasp",
    gloss: "holding the cube could not be demonstrated, and it could not be lowered to set it down, so the arm kept it",
    needs: ["tool_up_mm", "set_down_up_mm"],
    measured: "holding the cube could not be demonstrated, and it could not be lowered from <tool_up_mm> to <set_down_up_mm> to set it down, so the arm kept it",
  },
  gripper_did_not_open: {
    stage: "grasp",
    gloss: "the gripper did not open when commanded, so the arm stopped",
    needs: ["gripper", "gripper_open"],
    measured: "the gripper reads <gripper> after it was commanded open to <gripper_open>, so the arm stopped",
  },
  retention_not_demonstrated_set_down: {
    stage: "grasp",
    gloss: "holding the cube could not be demonstrated, so the arm set it down and let go; nothing is held",
    needs: ["retention_score", "retention_min_score"],
    measured: "holding the cube could not be demonstrated (retention score <retention_score> against <retention_min_score>), so the arm set it down where it was picked and let go; nothing is held",
  },
  scene_scan_step_refused: {
    stage: "search",
    gloss: "a view of the scene scan was refused after the arm had moved; the scene of the views taken was kept, and the arm holds where it stands",
    needs: ["views_taken"],
    measured: "a view of the scene scan was refused after the arm had moved; the scene of the <views_taken> views taken was kept, and the arm holds where it stands",
  },
  search_scene_no_block: {
    stage: "search",
    gloss: "the scene scan saw no block in any of its views, so nothing was picked up; the arm is back at the saved low pose",
    needs: [],
    measured: null,
  },
  search_scene_not_one_unplaced_block: {
    stage: "search",
    gloss: "the scene scan saw more than one block that is not on the pad, and the robot picks only when exactly one is, so nothing was picked up",
    needs: ["unplaced_blocks"],
    measured: "the scene scan saw <unplaced_blocks> blocks that are not on the pad, and the robot picks only when exactly one is, so nothing was picked up",
  },
  search_multi_block_no_placeable_block: {
    stage: "search",
    gloss: "the scene scan saw more than one block that is not on the pad, and none of them is one this run can pick up and place, so nothing was picked up",
    needs: ["unplaced_blocks"],
    measured: "the scene scan saw <unplaced_blocks> blocks that are not on the pad, and none of them is one this run can pick up and place, so nothing was picked up",
  },
  search_multi_block_scan_refused: {
    stage: "search",
    gloss: "SEARCH could not start the scene scan, so nothing moved and nothing was picked up",
    needs: [],
    measured: null,
  },
  search_multi_block_scan_step_refused: {
    stage: "search",
    gloss: "a view of the scene scan was refused after the arm had moved, so nothing was picked up; the arm holds where it stands",
    needs: [],
    measured: null,
  },
  search_multi_block_no_block: {
    stage: "search",
    gloss: "the scene scan saw no block in any of its views, so nothing was picked up; the arm is back at the saved low pose",
    needs: [],
    measured: null,
  },
  search_scene_scan_refused: {
    stage: "search",
    gloss: "SEARCH could not start the scene scan, so nothing moved and nothing was picked up",
    needs: [],
    measured: null,
  },
  search_scene_scan_step_refused: {
    stage: "search",
    gloss: "a view of the scene scan was refused after the arm had moved, so nothing was picked up; the arm holds where it stands",
    needs: [],
    measured: null,
  },
  search_sweep_sector_beyond_pan_policy: {
    stage: "search",
    gloss: "a sweep sector would have turned the arm beyond the scan's pan bound, so nothing was commanded",
    needs: [],
    measured: null,
  },
  search_cube_beyond_return_bound: {
    stage: "search",
    gloss: "the block lies at a pan too far from the saved low pose's pan for a return to that pose to be planned, so nothing was picked up",
    needs: [],
    measured: null,
  },
  search_cube_beyond_pregrasp_entry_bound: {
    stage: "search",
    gloss: "the block lies at a pan too far from where the arm stands for a pregrasp to be entered from, so nothing was picked up",
    needs: [],
    measured: null,
  },
  mission_selected_cube_already_placed: {
    stage: "search",
    gloss: "the block selected for this target is already on the pad, and a mission target never finishes on a placed block, so nothing was picked up",
    needs: [],
    measured: null,
  },
  mission_capability_observation_unavailable: {
    stage: "search",
    gloss: "placing this block needs the base, and there was no actual observation of this target for the capability to move it by, so nothing was picked up",
    needs: [],
    measured: null,
  },
  mission_result_not_written: {
    stage: "search",
    gloss: "the block selected for this target could not be written back for the runtime, so nothing was picked up",
    needs: [],
    measured: null,
  },
  align_trim_beyond_return_bound: {
    stage: "align",
    gloss: "the pan trim would have turned the arm too far from the saved low pose's pan for a return to be planned, so it was not commanded; nothing was picked up",
    needs: [],
    measured: null,
  },
  align_inside_grasp_radius: {
    stage: "align",
    gloss: "the block stands nearer than ALIGN may reach, so the move was not commanded; the arm holds clear of the block and nothing was picked up",
    needs: [],
    measured: null,
  },
  align_more_than_one_cube_verified: {
    stage: "align",
    // Changed by the pad policy of 2026-09-29: the ALIGN residual is named.
    gloss: "more than one block was verified in the pregrasp view (blocks on the pad count too), and a mission target is approached only when its block is the one block in view, so nothing was picked up",
    needs: [],
    measured: null,
  },
  align_cube_count_not_recorded: {
    stage: "align",
    gloss: "the pregrasp view does not record one verified block, and a mission target is approached only when its block is the one block in view, so nothing was picked up",
    needs: [],
    measured: null,
  },
  approach_view_no_pad_frame: {
    stage: "placement",
    gloss: "the pad frame was not valid in the view the capability is fed, and the base never moves blind, so the cube stays held",
    needs: [],
    measured: null,
  },
  operator_abort_before_capability_pulse: {
    stage: "placement",
    gloss: "an operator stop was seen before the capability commanded anything, so no base pulse was commanded",
    needs: [],
    measured: null,
  },
  base_capability_output_refused: {
    stage: "placement",
    gloss: "the capability refused a base pulse; the base reads stopped and the arm held, and nothing further is commanded",
    needs: [],
    measured: null,
  },
  release_offset_beyond_return_bound: {
    stage: "placement",
    gloss: "the release offset would have turned the arm too far from the saved low pose's pan for a return to be planned, so the cube stays held",
    needs: [],
    measured: null,
  },
  reach_placement_infeasible_while_held: {
    stage: "placement",
    gloss: "with the cube held, no placement policy of this run places the block from the measured grasp",
    needs: ["anchor_margin_mm", "required_centre_margin_mm"],
    measured: "with the cube held, no placement policy of this run places the block from the measured grasp: the block's centre stands <anchor_margin_mm> from the nearest pad edge, against the <required_centre_margin_mm> a release needs",
  },
  reach_mode_changed_while_held: {
    stage: "placement",
    gloss: "with the cube held, every plan that places the block from the measured grasp lies on the other side of the line between the arm alone and the base than this run was bound to, and this run may not cross that line with the cube held, so the cube stays held",
    needs: [],
    measured: null,
  },
  reach_approach_no_pose: {
    stage: "placement",
    gloss: "after the measured base advance, no pose above the release point passed the guards, so the cube stayed held",
    needs: [],
    measured: null,
  },
  reach_arm_only_insufficient_while_held: {
    stage: "placement",
    gloss: "with the cube held, the arm alone did not place the block and was brought back to the carry pose, and the policies left place it only by moving the base, which this run carries no capability to move, so the cube stays held",
    needs: ["fk_travel_from_grasp_mm"],
    measured: "with the cube held, the arm alone did not place the block: it reached <fk_travel_from_grasp_mm> beyond the grasp and was brought back to the carry pose, and the policies left place it only by moving the base, which this run carries no capability to move, so the cube stays held",
  },
  reach_policies_exhausted_while_held: {
    stage: "placement",
    gloss: "with the cube held, none of the placement policies asked from where it stands places the block by moving the base within what is left of the advance budget, so the cube stays held at the carry pose",
    needs: ["policies_tried", "policies_asked", "advance_budget_left_mm"],
    measured: "with the cube held, <policies_tried> did not place the block, and none of the placement policies asked from where it stands (<policies_asked>) places it by moving the base within the <advance_budget_left_mm> left of the advance budget, so the cube stays held at the carry pose",
  },
  reach_base_advanced_past_bound: {
    stage: "placement",
    gloss: "with the cube held, the base had travelled more than the bounded total over this run's advances, so nothing further was commanded",
    needs: ["advanced_mm", "bounded_total_advance_mm"],
    measured: "with the cube held, the base had travelled <advanced_mm> over this run's advances, more than the bounded total of <bounded_total_advance_mm>, so nothing further was commanded",
  },
  reach_further_advance_past_bound: {
    stage: "placement",
    gloss: "with the cube held, a further base advance carried the base past the bounded total over this run's advances, so nothing further was commanded",
    needs: ["advanced_mm", "bounded_total_advance_mm"],
    measured: "with the cube held, a further base advance carried the base to <advanced_mm> over this run's advances, more than the bounded total of <bounded_total_advance_mm>, so nothing further was commanded",
  },
  reach_retract_refused: {
    stage: "placement",
    gloss: "with the cube held, bringing the extended arm back to the carry pose was refused, so the arm holds the cube where it stands",
    needs: ["measured_out_mm", "grasp_out_mm"],
    measured: "with the cube held, bringing the extended arm back to the carry pose was refused: the tool stands <measured_out_mm> beyond the proven grasp radius, against the grasp at <grasp_out_mm>, so the arm holds the cube where it stands",
  },
  reach_retract_incomplete: {
    stage: "placement",
    gloss: "with the cube held, the extended arm did not come back to the carry pose in its bounded steps, so it holds the cube where it stands",
    needs: ["measured_out_mm", "grasp_out_mm"],
    measured: "with the cube held, the extended arm did not come back to the carry pose in its bounded steps: the tool still stands <measured_out_mm> beyond the proven grasp radius, against the grasp at <grasp_out_mm>, so it holds the cube where it stands",
  },
  reach_way_back_held: {
    stage: "placement",
    gloss: "with the cube held, the way back of the loaded arm stopped where its next step would have left what the records show the loaded arm doing, or where a step measured the arm not following it, so the arm holds the cube where it stands for the operator to take, and the next placement policy, which starts only from the carry pose, did not run",
    needs: ["measured_out_mm", "measured_up_mm", "grasp_out_mm"],
    measured: "with the cube held, the way back of the loaded arm stopped: the tool stands <measured_out_mm> beyond the proven grasp radius and <measured_up_mm> above the grasp height, against the grasp at <grasp_out_mm>, so the arm holds the cube where it stands for the operator to take, and the next placement policy, which starts only from the carry pose, did not run",
  },
  reach_release_hold_lost: {
    stage: "placement",
    gloss: "while the lowered cube was held still before the release, the gripper left its holding band, so nothing was released and no arm move can be commanded",
    needs: ["gripper", "holding_band_min", "holding_band_max"],
    measured: "while the lowered cube was held still before the release, the gripper read <gripper>, outside its holding band of <holding_band_min> to <holding_band_max>, so nothing was released and no arm move can be commanded",
  },
  base_return_2d_no_pad_frame: {
    stage: "recovery",
    gloss: "the pad frame was not valid in the view the return measures from, so no return pulse was commanded and the base stayed where it was",
    needs: ["advanced_mm"],
    measured: "the pad frame was not valid in the view the return measures from, so no return pulse was commanded and the base stayed <advanced_mm> ahead of where it started",
  },
};

const ADDED_STATES: Readonly<Record<string, { stage: string; sentence: string; phase: string; step: string | null }>> = {
  ALIGN_DESCENT: { stage: "align", sentence: "Descending over the block, the view checked before the move.", phase: "align", step: "pick" },
  ALIGN_RADIAL_STEP: { stage: "align", sentence: "Stepping toward the block at this height to bring it into the radial band.", phase: "align", step: "pick" },
  ALIGN_REACQUIRE: { stage: "align", sentence: "Stepping back to where the block was last seen whole.", phase: "align", step: "pick" },
  ALIGN_RECAPTURE: { stage: "align", sentence: "The block was not in the view: looking again without moving.", phase: "align", step: "pick" },
  GRASP_REOBSERVE: { stage: "grasp", sentence: "The grasp did not hold, so the open jaws lifted to look at the block again.", phase: "pick", step: "pick" },
  GRASP_RETRY: { stage: "grasp", sentence: "Trying the grasp again where the block was measured.", phase: "pick", step: "pick" },
  GRASP_SET_DOWN: { stage: "grasp", sentence: "Setting the cube back down, because holding it could not be demonstrated.", phase: "verify_grip", step: "pick" },
  SCENE: { stage: "search", sentence: "The views of the scan were fused into one scene of the blocks around the pad.", phase: "locate", step: "find" },
  SCENE_SCAN: { stage: "search", sentence: "Scanning the scene: the wrist looks from one more pan at the low-pose shape.", phase: "locate", step: "find" },
  SEARCH_SCENE: { stage: "search", sentence: "Deciding whether the low-pose frame alone decides, or the scene must be scanned.", phase: "locate", step: "find" },
  SEARCH_SCENE_SELECTED: { stage: "search", sentence: "The one block not yet on the pad was chosen from the scene, from its best view.", phase: "locate", step: "find" },
  SEARCH_SCENE_LEAST_MOTION: { stage: "search", sentence: "Of the blocks not yet on the pad, one was chosen from the scene, from its best view: among those the usual placement places, the one nearest the pad; otherwise the one whose placement moves the base least.", phase: "locate", step: "find" },
  // The additive placement policies of 2026-09-30 (autonomy/manipulation/placement_executor.py, reach_carry.py).
  PLACEMENT_POLICY: { stage: "placement", sentence: "Choosing, from the measured grasp, the placement policy that moves the base least.", phase: "place", step: "carry" },
  REACH_CARRY: { stage: "placement", sentence: "Carrying the cube outward one measured step at a time.", phase: "place", step: "carry" },
  REACH_CARRY_STOPPED: { stage: "placement", sentence: "The measured carry ended, at the planned reach or short of it.", phase: "place", step: "carry" },
  REACH_REPLAN: { stage: "placement", sentence: "Planning the arm's reach again from where the measured base advance ended.", phase: "place", step: "carry" },
  REACH_RETRACT_STEP: { stage: "placement", sentence: "Bringing the loaded arm back one bounded step, read before the next.", phase: "place", step: "carry" },
  REACH_RETRACT: { stage: "placement", sentence: "Bringing the extended arm back to the carry pose, still holding the cube.", phase: "place", step: "carry" },
  PLACEMENT_FALLBACK: { stage: "placement", sentence: "A placement policy handed the cube back at the carry pose without placing it, and the run asks the placement policies left from where it stands.", phase: "place", step: "carry" },
  RELEASE_HOLD: { stage: "placement", sentence: "Holding the lowered cube still, to check the arm is steady before the release.", phase: "place", step: "place" },
};

/** Every name a template asks for is present, and each value is its own name in angle brackets. */
const EVERY_VALUE = new Proxy(
  {},
  { get: (_target, name) => (typeof name === 'string' ? { value: `<${name}>`, unit: null } : undefined) },
) as TemplateArgs;

const ALL_FAILURES = { ...PRE_SPLIT_FAILURES, ...ADDED_FAILURES };
const ALL_STATES = { ...PRE_SPLIT_STATES, ...ADDED_STATES };

describe('the aggregates equal the maps before the split plus the additions, key for key', () => {
  it('keeps the additions apart from the maps before the split', () => {
    for (const code of Object.keys(ADDED_FAILURES)) expect(Object.hasOwn(PRE_SPLIT_FAILURES, code), code).toBe(false);
    for (const state of Object.keys(ADDED_STATES)) expect(Object.hasOwn(PRE_SPLIT_STATES, state), state).toBe(false);
  });

  it('holds the same failure codes, with the same gloss, needs and measured sentence', () => {
    expect([...FAILURE_REASON_CODES].sort()).toEqual(Object.keys(ALL_FAILURES).sort());
    expect(Object.keys(STAGE_FAILURES).sort()).toEqual(Object.keys(ALL_FAILURES).sort());
    for (const [code, before] of Object.entries(ALL_FAILURES)) {
      expect(describeFailureCode(code), code).toBe(before.gloss);
      expect(describeFailureCodeWith(code, EVERY_VALUE), code).toBe(before.measured ?? before.gloss);
      expect(describeFailureCodeWith(code, {}), code).toBe(before.gloss);
      const template = STAGE_FAILURES[code];
      expect(template?.needs, code).toEqual(before.needs);
      expect(template?.measured === undefined, code).toBe(before.measured === null);
    }
  });

  it('holds the same states, with the same sentence, phase and presentation step', () => {
    expect([...CONTROLLER_STATES].sort()).toEqual(Object.keys(ALL_STATES).sort());
    expect([...STATES_WITH_A_SENTENCE].sort()).toEqual(Object.keys(ALL_STATES).sort());
    expect(Object.keys(STAGE_STATES).sort()).toEqual(Object.keys(ALL_STATES).sort());
    for (const [state, before] of Object.entries(ALL_STATES)) {
      expect(describeState(state), state).toBe(before.sentence);
      expect(phaseOfState(state), state).toBe(before.phase);
      expect(isTerminalState(state), state).toBe(before.phase === 'terminal');
      expect(presentationStepOfState(state), state).toBe(before.step ?? 'terminal');
    }
  });

  it('still reports a state or a code nobody glossed as unknown, never as a guess', () => {
    expect(describeState('SCENE_SCAN_UNHEARD_OF')).toBeNull();
    expect(phaseOfState('SCENE_SCAN_UNHEARD_OF')).toBe('unknown');
    expect(presentationStepOfState('SCENE_SCAN_UNHEARD_OF')).toBe('unknown');
    expect(describeFailureCode('scene_unheard_of')).toBeNull();
  });
});

describe('one file per stage', () => {
  it('names the five stages of the plan in order', () => {
    expect(STAGE_IDS).toEqual(['search', 'align', 'grasp', 'placement', 'recovery']);
    expect(STAGE_GLOSSARIES.map((glossary) => glossary.stage)).toEqual([...STAGE_IDS]);
  });

  it('gives every code and every state to exactly one stage', () => {
    const codes = new Map<string, string>();
    const states = new Map<string, string>();
    for (const glossary of STAGE_GLOSSARIES) {
      for (const code of Object.keys(glossary.failures)) {
        expect(codes.get(code), `${code} is in ${codes.get(code) ?? ''} and ${glossary.stage}`).toBeUndefined();
        codes.set(code, glossary.stage);
      }
      for (const state of Object.keys(glossary.states)) {
        expect(states.get(state), `${state} is in ${states.get(state) ?? ''} and ${glossary.stage}`).toBeUndefined();
        states.set(state, glossary.stage);
      }
    }
    expect(codes.size).toBe(Object.keys(ALL_FAILURES).length);
    expect(states.size).toBe(Object.keys(ALL_STATES).length);
    for (const [code, added] of Object.entries(ADDED_FAILURES)) expect(codes.get(code), code).toBe(added.stage);
    for (const [state, added] of Object.entries(ADDED_STATES)) expect(states.get(state), state).toBe(added.stage);
  });

  it('puts each state and code in the stage whose track owns the decision', () => {
    const codeStage = (code: string) => STAGE_GLOSSARIES.find((glossary) => code in glossary.failures)?.stage;
    const stateStage = (state: string) => STAGE_GLOSSARIES.find((glossary) => state in glossary.states)?.stage;
    const all = (items: string[], at: (item: string) => string | undefined, stage: string) => expect(items.map(at)).toEqual(items.map(() => stage));
    all(['SEARCH', 'SEARCH_SWEEP', 'TARGET_SELECTED', 'DONE_ALREADY_PLACED'], stateStage, 'search');
    all(['PREGRASP', 'PREGRASP_BIAS_REDUCED', 'ALIGN'], stateStage, 'align');
    all(['APPROACH', 'STRADDLING', 'GRASP_CLOSE', 'GRASP_RETRY_ADVANCE', 'OBJECT_SECURED'], stateStage, 'grasp');
    all(['PLACEMENT_PLAN', 'HOLD_OBSERVE', 'BASE_ADVANCE', 'RELEASE_ADMISSION', 'PLACED'], stateStage, 'placement');
    all(['BASE_RETURN', 'BASE_RETURNED'], stateStage, 'recovery');
    all(['search_no_cube', 'mission_binding_no_candidate', 'start_pose_not_low_scan'], codeStage, 'search');
    all(['search_cube_inside_pregrasp_reach', 'align_cube_not_detected', 'pregrasp_no_non_contact_pose'], codeStage, 'align');
    all(['grasp_missed', 'straddling_not_reached', 'retention_not_demonstrated'], codeStage, 'grasp');
    all(['search_placement_infeasible', 'release_not_admitted', 'base_advance_exceeds_bound'], codeStage, 'placement');
    all(['base_not_returned', 'base_return_no_pad_frame', 'operator_abort'], codeStage, 'recovery');
  });
});
