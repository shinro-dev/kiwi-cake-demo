// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE GRASP STAGE: the approach, the straddle, the close and its retries, and whether the cube is held.
//
// One of the five stage files (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0b; the shape and the rules
// are in ./stage.ts). The owner of this stage's policy is track G (grasp): it edits this file and no other stage's.
// Every code below is a raise site of bin/laptop/manipulation/run_single_cube_cycle.py (or of the runtime, where the
// comment says so), glossed in the words an operator uses; every state is one the controller emits, with the sentence
// for it, the phase it is grouped in (phase.ts) and the step of the five it is drawn under.

import { argText } from './stage';
import type { StageGlossary } from './stage';

export const GRASP_STAGE: StageGlossary = {
  stage: 'grasp',
  failures: {
    straddling_not_reached: {
      gloss: 'the jaws never reached the grasp height around the cube',
      needs: ['tool_up_mm', 'straddling_max_up_mm'],
      measured: (args) =>
        `the jaws stopped ${argText(args, 'tool_up_mm')} above the grasp height, where straddling needs ${argText(args, 'straddling_max_up_mm')} or less`,
    },
    grasp_missed: {
      gloss: 'the gripper closed on nothing, three times',
      needs: ['closes', 'gripper', 'gripper_latch'],
      measured: (args) =>
        `the gripper closed on nothing ${argText(args, 'closes')} times: the jaw reached ${argText(args, 'gripper')}, against the empty-latch value of ${argText(args, 'gripper_latch')}`,
    },
    // The grasp words (policy_grasp.py, OFF with no word): each code below is raised only under the word that opens it.
    approach_cube_beyond_grasp_range: {
      gloss: 'the block was measured outside the grasp range this run allows, so nothing was closed short of it',
      needs: ['block_estimate_mm', 'wanted_mm', 'nearest_mm', 'farthest_mm'],
      measured: (args) =>
        `the block was measured at ${argText(args, 'block_estimate_mm')}, so the grasp wanted ${argText(args, 'wanted_mm')}, outside this run's grasp range of ${argText(args, 'nearest_mm')} to ${argText(args, 'farthest_mm')}; nothing was closed short of it`,
    },
    grasp_retry_lift_refused: {
      gloss: 'after an empty close the open jaws could not lift high enough to look again',
      needs: ['tool_up_mm', 'observe_up_mm'],
      measured: (args) =>
        `after an empty close the open jaws stopped ${argText(args, 'tool_up_mm')} above the grasp height, short of the ${argText(args, 'observe_up_mm')} a look needs`,
    },
    grasp_retry_cube_lost: {
      gloss: 'after an empty close the block was not seen again, so nothing is held and the jaws are open above the floor',
      needs: ['closes'],
      measured: (args) => `after ${argText(args, 'closes')} empty closes the block was not seen again, on two looks; nothing is held`,
    },
    grasp_retry_not_aligned: {
      gloss: 'after an empty close the block stayed off the jaws\' capture window, so nothing was closed again',
      needs: ['lateral_mm', 'trims'],
      measured: (args) => `after an empty close the block stayed ${argText(args, 'lateral_mm')} off the capture window after ${argText(args, 'trims')} pan trims; nothing is held`,
    },
    grasp_retry_approach_refused: {
      gloss: 'after an empty close the approach to grasp again was refused, so nothing is held and the jaws are open',
      needs: ['to_forward_mm', 'closes'],
      measured: (args) =>
        `after ${argText(args, 'closes')} closes the approach to grasp again at ${argText(args, 'to_forward_mm')} was refused; nothing is held and the jaws are open`,
    },
    grasp_retry_no_untried_radius: {
      gloss: 'every grasp radius near the measured block had already closed empty',
      needs: ['block_estimate_mm', 'closes'],
      measured: (args) => `the block was measured at ${argText(args, 'block_estimate_mm')} and every grasp radius near it had closed empty, after ${argText(args, 'closes')} closes`,
    },
    gripper_did_not_open: {
      gloss: 'the gripper did not open when commanded, so the arm stopped',
      needs: ['gripper', 'gripper_open'],
      measured: (args) => `the gripper reads ${argText(args, 'gripper')} after it was commanded open to ${argText(args, 'gripper_open')}, so the arm stopped`,
    },
    grasp_set_down_refused: {
      gloss: 'holding the cube could not be demonstrated, and it could not be lowered to set it down, so the arm kept it',
      needs: ['tool_up_mm', 'set_down_up_mm'],
      measured: (args) =>
        `holding the cube could not be demonstrated, and it could not be lowered from ${argText(args, 'tool_up_mm')} to ${argText(args, 'set_down_up_mm')} to set it down, so the arm kept it`,
    },
    grasp_set_down_lift_refused: {
      gloss: 'the cube was set down and the jaws opened, but the open lift off it was refused; nothing is held',
      needs: ['tool_up_mm', 'observe_up_mm'],
      measured: (args) =>
        `the cube was set down and the jaws opened, but the open lift off it stopped ${argText(args, 'tool_up_mm')} above the grasp height, short of ${argText(args, 'observe_up_mm')}; nothing is held`,
    },
    retention_not_demonstrated_set_down: {
      gloss: 'holding the cube could not be demonstrated, so the arm set it down and let go; nothing is held',
      needs: ['retention_score', 'retention_min_score'],
      measured: (args) =>
        `holding the cube could not be demonstrated (retention score ${argText(args, 'retention_score')} against ${argText(args, 'retention_min_score')}), so the arm set it down where it was picked and let go; nothing is held`,
    },
    retention_not_demonstrated: {
      gloss: 'holding the cube could not be demonstrated, so the arm kept it and stopped',
      needs: ['retention_score', 'retention_min_score'],
      measured: (args) =>
        `holding the cube could not be demonstrated: the retention score was ${argText(args, 'retention_score')} against the ${argText(args, 'retention_min_score')} required, so the arm kept the cube and stopped`,
    },
  },
  states: {
    // Closing the remaining distance, getting the jaws around the cube and closing them. GRASP_RETRY_ADVANCE (:1047) is
    // the controller retrying its own grasp, which is part of picking and not a phase the mission goes back to.
    APPROACH: { sentence: 'Closing the remaining radial distance to the cube.', phase: 'pick', step: 'pick' },
    STRADDLING: { sentence: 'The jaws are around the cube.', phase: 'pick', step: 'pick' },
    GRASP_CLOSE: { sentence: 'Closing the gripper on the cube.', phase: 'pick', step: 'pick' },
    GRASP_RETRY_ADVANCE: { sentence: 'The grasp did not hold, so the arm advanced and is trying again.', phase: 'pick', step: 'pick' },
    // Under grasp_measured_retry: after an empty close the jaws lift open and look again, then retry at the block measured again.
    GRASP_REOBSERVE: { sentence: 'The grasp did not hold, so the open jaws lifted to look at the block again.', phase: 'pick', step: 'pick' },
    GRASP_RETRY: { sentence: 'Trying the grasp again where the block was measured.', phase: 'pick', step: 'pick' },
    // Under grasp_recover: holding the cube was not demonstrated, so the arm sets it down and lets go.
    GRASP_SET_DOWN: { sentence: 'Setting the cube back down, because holding it could not be demonstrated.', phase: 'verify_grip', step: 'pick' },
    // Whether the cube is actually held, scored between two frames taken above the floor (:1057).
    OBJECT_SECURED: { sentence: 'Checking whether the cube is held.', phase: 'verify_grip', step: 'pick' },
  },
};
