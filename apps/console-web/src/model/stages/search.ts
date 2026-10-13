// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SEARCH STAGE: finding the block from the low scan pose, the sweep, and binding it as the target.
//
// One of the five stage files (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0b; the shape and the rules
// are in ./stage.ts). The owner of this stage's policy is track S (scene scan and search): it edits this file and no other stage's.
// Every code below is a raise site of bin/laptop/manipulation/run_single_cube_cycle.py (or of the runtime, where the
// comment says so), glossed in the words an operator uses; every state is one the controller emits, with the sentence
// for it, the phase it is grouped in (phase.ts) and the step of the five it is drawn under.

import { argText } from './stage';
import type { StageGlossary } from './stage';

const SWEEP_ENDED =
  "when SEARCH sweeps, a block on the pad in its first view (an earlier block, or the pad's printed logo read as one) ends the sweep, so a block off the pad outside that view may not have been looked for";

export const SEARCH_STAGE: StageGlossary = {
  stage: 'search',
  failures: {
    start_pose_not_low_scan: {
      gloss: 'the run refused to start: the arm was not at the saved low scan pose with the gripper in its normal-open region',
      needs: [],
    },
    search_no_cube: { gloss: 'no cube the robot could pick was seen from the low scan pose, so nothing was picked up', needs: [] },
    // THE SWEEP (docs/autonomous-demo/one-block-demo-2026-09-26.md, T2): when the low-pose frame shows no cube, SEARCH
    // looks through its sweep sectors, and this is the end of a run in which no sector showed one. The arm is back at
    // the centre sector when it is raised.
    search_no_cube_after_sweep: {
      gloss: 'the robot looked through every sweep sector and saw no cube it could pick, so nothing was picked up and the arm is back at the centre sector',
      needs: [],
    },
    // THE MISSION'S OWN REFUSALS. The first two are raised by autonomy/manipulation/target_binding.py through the
    // controller's failed(), before anything is picked up; the values are the counts that decided them. The third is the
    // controller's own raise site in mission mode (the two raised with the cube held are in placement.ts).
    // THE SWEEP RESIDUAL (the pad policy of 2026-09-29, named and pinned in autonomy/): SEARCH sweeps only when its first
    // view shows no block, so a block on the pad in that view ends the search before a block off the pad is looked for.
    mission_binding_no_candidate: {
      gloss: `no cube in view could be taken as this target, so nothing was picked up; ${SWEEP_ENDED}`,
      needs: ['cubes_seen', 'placed_cubes'],
      measured: (args) =>
        `no cube in view could be taken as this target: ${argText(args, 'cubes_seen')} cube(s) were seen and ${argText(args, 'placed_cubes')} of them are already placed on the pad; nothing was picked up; ${SWEEP_ENDED}`,
    },
    // The binding's refusal (target_binding.py): no block off the pad has a bounded placement from this SEARCH (an auto
    // target weighs every one of them since the pad policy of 2026-09-29). The limit of the one nearest the pad is shown
    // beside this sentence when the record carries it (model/failureBanner.ts).
    mission_binding_candidate_not_placeable: {
      gloss: 'no cube off the pad in view has a bounded placement from here: neither the arm alone nor the bounded base advance reaches the pad from it, so nothing was picked up',
      needs: [],
    },
    mission_binding_candidate_not_unique: {
      gloss: 'more than one cube in view could be this target, and the robot picks only when exactly one can, so nothing was picked up',
      needs: ['matching_candidates'],
      measured: (args) =>
        `${argText(args, 'matching_candidates')} cubes in view could be this target, and the robot picks only when exactly one can; nothing was picked up`,
    },
    mission_capability_required: {
      gloss:
        'placing this cube needs the base to move, and this run carried no capability to move it, so nothing was picked up; the mission waits for the capability and comes back to this target once it is there',
      needs: [],
    },
    // Under the additive placement words: the arm alone places the cube; should its release be refused, the same run goes
    // on by moving the base only if the controller's way back brings the loaded arm to the carry pose (otherwise the arm
    // holds the cube where it stands), and it may move the base only with the capability, given before a run starts.
    mission_fallback_capability_required: {
      gloss:
        'the arm alone places this cube; should its release be refused, the same run goes on by moving the base only if the loaded arm comes back to the carry pose, otherwise the arm holds the cube where it stands for the operator; moving the base needs the capability this run did not carry, so nothing was picked up; the mission waits for the capability and comes back to this target once it is there',
      needs: [],
    },
    search_cube_not_in_pad_frame: {
      gloss: 'a cube was seen, but the pad frame was not valid, so the distance to the pad was unknown',
      needs: [],
    },
    search_no_pad_frame: { gloss: 'the pad homography was missing from the perception record of the low pose', needs: [] },
    // THE SCENE SCAN AND SEARCH FROM THE SCENE (track S, under the words scene_scan and scene_search).
    scene_scan_step_refused: {
      gloss: 'a view of the scene scan was refused after the arm had moved; the scene of the views taken was kept, and the arm holds where it stands',
      needs: ['views_taken'],
      measured: (args) =>
        `a view of the scene scan was refused after the arm had moved; the scene of the ${argText(args, 'views_taken')} views taken was kept, and the arm holds where it stands`,
    },
    search_scene_scan_refused: { gloss: 'SEARCH could not start the scene scan, so nothing moved and nothing was picked up', needs: [] },
    search_scene_scan_step_refused: {
      gloss: 'a view of the scene scan was refused after the arm had moved, so nothing was picked up; the arm holds where it stands',
      needs: [],
    },
    search_scene_not_one_unplaced_block: {
      gloss: 'the scene scan saw more than one block that is not on the pad, and the robot picks only when exactly one is, so nothing was picked up',
      needs: ['unplaced_blocks'],
      measured: (args) =>
        `the scene scan saw ${argText(args, 'unplaced_blocks')} blocks that are not on the pad, and the robot picks only when exactly one is, so nothing was picked up`,
    },
    search_scene_no_block: { gloss: 'the scene scan saw no block in any of its views, so nothing was picked up; the arm is back at the saved low pose', needs: [] },
    // SEARCH FROM THE SCENE under the word multi_block (autonomy/manipulation/block_selection.py): several blocks off the
    // pad are a choice (block_selection.py, THE RULE), and the refusal is when none of them can be picked up and placed. The
    // procedure restates the proven one with a code of its own at each raise site, saying what the proven one says.
    search_multi_block_no_placeable_block: {
      gloss: 'the scene scan saw more than one block that is not on the pad, and none of them is one this run can pick up and place, so nothing was picked up',
      needs: ['unplaced_blocks'],
      measured: (args) =>
        `the scene scan saw ${argText(args, 'unplaced_blocks')} blocks that are not on the pad, and none of them is one this run can pick up and place, so nothing was picked up`,
    },
    search_multi_block_scan_refused: { gloss: 'SEARCH could not start the scene scan, so nothing moved and nothing was picked up', needs: [] },
    search_multi_block_scan_step_refused: {
      gloss: 'a view of the scene scan was refused after the arm had moved, so nothing was picked up; the arm holds where it stands',
      needs: [],
    },
    search_multi_block_no_block: { gloss: 'the scene scan saw no block in any of its views, so nothing was picked up; the arm is back at the saved low pose', needs: [] },
    // THE REST OF SEARCH'S RAISE SITES (policy_search.py and the controller's mission gates, all before PREGRASP). Each
    // controller sentence ends "nothing was picked up" or "nothing was commanded", and the gloss says the same; the
    // values are in the measurement table.
    search_sweep_sector_beyond_pan_policy: {
      gloss: "a sweep sector would have turned the arm beyond the scan's pan bound, so nothing was commanded",
      needs: [],
    },
    search_cube_beyond_return_bound: {
      gloss: "the block lies at a pan too far from the saved low pose's pan for a return to that pose to be planned, so nothing was picked up",
      needs: [],
    },
    search_cube_beyond_pregrasp_entry_bound: {
      gloss: 'the block lies at a pan too far from where the arm stands for a pregrasp to be entered from, so nothing was picked up',
      needs: [],
    },
    mission_selected_cube_already_placed: {
      gloss: 'the block selected for this target is already on the pad, and a mission target never finishes on a placed block, so nothing was picked up',
      needs: [],
    },
    mission_capability_observation_unavailable: {
      gloss: 'placing this block needs the base, and there was no actual observation of this target for the capability to move it by, so nothing was picked up',
      needs: [],
    },
    mission_result_not_written: {
      gloss: 'the block selected for this target could not be written back for the runtime, so nothing was picked up',
      needs: [],
    },
  },
  states: {
    // Looking for the cube from the low scan pose (:934).
    SEARCH: { sentence: 'Looking for the cube from the low scan pose.', phase: 'locate', step: 'find' },
    // The two mission-mode states of the search. SEARCH_SWEEP is one wrist sector looked through when the low-pose
    // frame showed no cube (docs/autonomous-demo/one-block-demo-2026-09-26.md, T2); TARGET_SELECTED is the cube in view
    // taken as the mission's target with its measured placement mode (_mission_search). Both are still the search.
    SEARCH_SWEEP: { sentence: 'Scanning for the block: the low-pose frame showed no cube, so the wrist looks through its sweep sectors.', phase: 'locate', step: 'find' },
    TARGET_SELECTED: { sentence: "The block in view was taken as this mission's target, and its placement mode was measured.", phase: 'locate', step: 'find' },
    // The scene scan (--do scan, or SEARCH under scene_search): one state per view, the fused scene, and SEARCH's choice.
    SCENE_SCAN: { sentence: 'Scanning the scene: the wrist looks from one more pan at the low-pose shape.', phase: 'locate', step: 'find' },
    SCENE: { sentence: 'The views of the scan were fused into one scene of the blocks around the pad.', phase: 'locate', step: 'find' },
    SEARCH_SCENE: { sentence: 'Deciding whether the low-pose frame alone decides, or the scene must be scanned.', phase: 'locate', step: 'find' },
    SEARCH_SCENE_SELECTED: { sentence: 'The one block not yet on the pad was chosen from the scene, from its best view.', phase: 'locate', step: 'find' },
    // Under multi_block: of several blocks off the pad, among those the proven policy places the one nearest the pad, as the
    // installed rule binds it; where it places none, the one whose placement moves the base least (block_selection.py).
    SEARCH_SCENE_LEAST_MOTION: {
      sentence: 'Of the blocks not yet on the pad, one was chosen from the scene, from its best view: among those the usual placement places, the one nearest the pad; otherwise the one whose placement moves the base least.',
      phase: 'locate',
      step: 'find',
    },
    // The cube was already placed, so nothing was moved (:952). Terminal, and not a phase.
    DONE_ALREADY_PLACED: { sentence: 'The cube was already inside the pad outline, so nothing was moved.', phase: 'terminal', step: null },
  },
};
