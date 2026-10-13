// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ALIGN STAGE: the pickup band, the pre-grasp pose and the alignment onto the tool axis.
//
// One of the five stage files (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0b; the shape and the rules
// are in ./stage.ts). The owner of this stage's policy is track A (pregrasp and align): it edits this file and no other stage's.
// Every code below is a raise site of bin/laptop/manipulation/run_single_cube_cycle.py (or of the runtime, where the
// comment says so), glossed in the words an operator uses; every state is one the controller emits, with the sentence
// for it, the phase it is grouped in (phase.ts) and the step of the five it is drawn under.

import { argText } from './stage';
import type { StageGlossary } from './stage';

// THE WORDS OF THIS STAGE (autonomy/manipulation/policy_align.py): coarse_v2 changes numbers and raises nothing new;
// align_v2 adds the states and codes below. They are named apart so a reader sees what the word adds, and ALIGN_STAGE
// holds them (merged at integration; stages.test.ts pins them as additions after the split). Each code is a raise site
// of policy_align.py and each state a transition it emits under align_v2.
export const ALIGN_V2_ADDITIONS: Pick<StageGlossary, 'failures' | 'states'> = {
  failures: {
    // The descent is not commanded: the block's box, predicted where the worst recorded descent would leave it, stands
    // closer than the margin to the top or a side of the view, and neither a raised aim nor a step corrects it.
    align_descent_would_lose_view: {
      gloss: 'the descent would have lost the block from the view, so it was not commanded; nothing was picked up',
      needs: ['predicted_top_px', 'view_margin_px'],
      measured: (args) =>
        `the descent would have left the block ${argText(args, 'predicted_top_px')} from the top of the view, under the ${argText(args, 'view_margin_px')} margin, so it was not commanded; nothing was picked up`,
    },
    // A lost block was re-captured in place, then looked for again where it was last whole, and did not come back.
    align_cube_lost_after_reacquire: {
      gloss: 'the block left the view and did not come back after the bounded steps back; the arm holds clear of it',
      needs: ['reacquires', 'tool_up_mm'],
      measured: (args) =>
        `the block left the view and did not come back after ${argText(args, 'reacquires')} steps back; the arm holds ${argText(args, 'tool_up_mm')} above the grasp height, clear of it`,
    },
    align_radial_not_in_band: {
      gloss: 'the block stayed outside the radial band and no radial step corrected it; nothing was picked up',
      needs: ['radial_mm', 'radial_band_mm'],
      measured: (args) =>
        `the block read ${argText(args, 'radial_mm')} radially, outside the ${argText(args, 'radial_band_mm')} band, and no radial step corrected it; nothing was picked up`,
    },
  },
  states: {
    ALIGN_RADIAL_STEP: { sentence: 'Stepping toward the block at this height to bring it into the radial band.', phase: 'align', step: 'pick' },
    ALIGN_DESCENT: { sentence: 'Descending over the block, the view checked before the move.', phase: 'align', step: 'pick' },
    ALIGN_RECAPTURE: { sentence: 'The block was not in the view: looking again without moving.', phase: 'align', step: 'pick' },
    ALIGN_REACQUIRE: { sentence: 'Stepping back to where the block was last seen whole.', phase: 'align', step: 'pick' },
  },
};

export const ALIGN_STAGE: StageGlossary = {
  stage: 'align',
  failures: {
    // THE NEAR BLOCK (docs/autonomous-demo/one-block-demo-2026-09-26.md, T6 step 2a): the pregrasp clamps its forward
    // offset at zero, so a block nearer than that clamp is closed on short of its centre. Refused at SEARCH before any
    // motion; the values (cube_out_mm, min_cube_out_mm, move_block_at_least_mm) are in the measurement table, not here.
    search_cube_inside_pregrasp_reach: {
      gloss: 'the block stands nearer than the pregrasp can reach, so nothing was picked up; move it away from the robot',
      needs: [],
    },
    pregrasp_no_non_contact_pose: { gloss: 'no pre-grasp pose over the cube passed the guards without contact', needs: [] },
    // THE RECOGNIZER'S OWN REASON, NOT A GUESS AT ONE. The controller's sentence for this code used to name two causes,
    // "coarse geometry too far off, or occluded", while the recognizer's note for the same frame said the cube WAS
    // proposed at 0.664 and was rejected on box size alone. Both named causes were false. The controller now passes its
    // notes through as `recognizer_reason` and this reads them. `recognizer_reason` is NOT in `needs`: a run recorded
    // before that change carries no such value, and the sentence must stay the measured one for those runs rather than
    // fall back to the gloss.
    align_cube_not_detected: {
      gloss: 'the cube was not detected in the pre-grasp view',
      needs: ['attempt'],
      measured: (args) => {
        const attempt = `the cube was not detected in the pre-grasp view on attempt ${argText(args, 'attempt')}`;
        const why = args['recognizer_reason'];
        if (why === undefined || why.value === null || String(why.value).length === 0) return attempt;
        return `${attempt}. The recognizer's own reason: ${String(why.value)}`;
      },
    },
    align_not_in_window_after_trims: {
      gloss: 'the cube stayed outside the capture window after the bounded trims',
      needs: ['lateral_mm', 'capture_window_max_mm'],
      measured: (args) =>
        `the cube was still ${argText(args, 'lateral_mm')} off the tool axis after the bounded trims, outside the ${argText(args, 'capture_window_max_mm')} capture window`,
    },
    align_no_non_contact_descent: { gloss: 'no descent at this reach passed the guards without contact', needs: [] },
    align_bounded_corrections_exhausted: {
      gloss: 'alignment ran out of its bounded corrections',
      needs: ['attempts', 'trims'],
      measured: (args) =>
        `alignment ran out of its bounded corrections after ${argText(args, 'attempts')} attempts and ${argText(args, 'trims')} trims`,
    },
    // THE REST OF ALIGN'S RAISE SITES (policy_align.py, and the controller's one-cube check of a mission target). All are
    // before APPROACH, so nothing was picked up; the values are in the measurement table.
    align_trim_beyond_return_bound: {
      gloss: "the pan trim would have turned the arm too far from the saved low pose's pan for a return to be planned, so it was not commanded; nothing was picked up",
      needs: [],
    },
    align_inside_grasp_radius: {
      gloss: 'the block stands nearer than ALIGN may reach, so the move was not commanded; the arm holds clear of the block and nothing was picked up',
      needs: [],
    },
    align_more_than_one_cube_verified: {
      gloss: 'more than one block was verified in the pregrasp view (blocks on the pad count too), and a mission target is approached only when its block is the one block in view, so nothing was picked up',
      needs: [],
    },
    align_cube_count_not_recorded: {
      gloss: 'the pregrasp view does not record one verified block, and a mission target is approached only when its block is the one block in view, so nothing was picked up',
      needs: [],
    },
    ...ALIGN_V2_ADDITIONS.failures,
  },
  states: {
    // The pre-grasp pose and the turn onto the tool axis. PREGRASP_BIAS_REDUCED (:978) is the same pre-grasp with its
    // outward bias cut back to stay inside the reach, so it belongs with the pre-grasp and not in a group of its own.
    PREGRASP: { sentence: 'Moving to the pre-grasp pose computed from the cube contact point.', phase: 'align', step: 'pick' },
    PREGRASP_BIAS_REDUCED: { sentence: 'The outward bias of the pre-grasp was reduced to stay inside the reach.', phase: 'align', step: 'pick' },
    ALIGN: { sentence: 'Turning to bring the cube onto the tool axis.', phase: 'align', step: 'pick' },
    ...ALIGN_V2_ADDITIONS.states,
  },
};

