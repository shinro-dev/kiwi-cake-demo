// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RECOVERY STAGE: the base return, and the ends of a run that no single stage decides (a refused step, an abort, a missing saved pose).
//
// One of the five stage files (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0b; the shape and the rules
// are in ./stage.ts). The owner of this stage's policy is track R (return and mission recovery): it edits this file and no other stage's.
// Every code below is a raise site of bin/laptop/manipulation/run_single_cube_cycle.py (or of the runtime, where the
// comment says so), glossed in the words an operator uses; every state is one the controller emits, with the sentence
// for it, the phase it is grouped in (phase.ts) and the step of the five it is drawn under.

import { argText } from './stage';
import type { StageGlossary } from './stage';

export const RECOVERY_STAGE: StageGlossary = {
  stage: 'recovery',
  failures: {
    base_not_returned: {
      gloss: 'the cube is placed, but the base did not come back within its tolerance',
      needs: ['remaining_along_axis_mm', 'base_return_tolerance_mm'],
      measured: (args) =>
        `the cube is placed, but the base stopped ${argText(args, 'remaining_along_axis_mm')} from where it started, outside its ${argText(args, 'base_return_tolerance_mm')} tolerance`,
    },
    base_return_no_pad_frame: {
      gloss: 'the pad frame was not valid for the return, so the base stayed where it was',
      needs: ['advanced_mm'],
      measured: (args) =>
        `the pad frame was not valid for the return, so the base stayed ${argText(args, 'advanced_mm')} ahead of where it started`,
    },
    // return_2d's BASE_RETURN (policy_return.py): a read-only view first, and no reverse pulse when it has no pad frame.
    base_return_2d_no_pad_frame: {
      gloss: 'the pad frame was not valid in the view the return measures from, so no return pulse was commanded and the base stayed where it was',
      needs: ['advanced_mm'],
      measured: (args) =>
        `the pad frame was not valid in the view the return measures from, so no return pulse was commanded and the base stayed ${argText(args, 'advanced_mm')} ahead of where it started`,
    },
    step_refused_mid_cycle: { gloss: 'a step was refused in the middle of the cycle', needs: [] },
    // The runtime decides these two, not the controller: they arrive on a run that never reached its own raise sites.
    saved_pose_missing: { gloss: 'the saved low scan pose this run needs was not on the machine', needs: [] },
    operator_abort: { gloss: 'an operator aborted the run', needs: [] },
  },
  states: {
    // Driving the base back (:866) and reading how far it ended up from where it started (:1147).
    BASE_RETURN: { sentence: 'Driving the base back toward where it started.', phase: 'return', step: 'return' },
    BASE_RETURNED: { sentence: 'Checking how far the base is from where it started.', phase: 'return', step: 'return' },
  },
};
