# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The wrist-flex ceiling margin (operator ruling 2026-10-08): commands still never raise wrist_flex beyond where the arm
is, plus WRIST_FLEX_CEILING_MARGIN_DEG. 0.2 deg refused the pregrasp entry of the 2026-10-08 scene by 0.15 deg (69.05
wanted from a 68.703 start) and Return arm's settle by 0.06 deg; the margin is one shared constant at every site."""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "perception"))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "perception"))

import run_stage1_scan as cli  # noqa: E402

BASE_ZERO = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}


class WristCeilingMarginTest(unittest.TestCase):
    def test_the_margin_is_half_a_degree(self):
        self.assertEqual(cli.WRIST_FLEX_CEILING_MARGIN_DEG, 0.5)
        self.assertAlmostEqual(cli.wrist_flex_ceiling_for({"wrist_flex": 68.703}, None), 69.203)

    def test_the_2026_10_08_pregrasp_entry_passes_and_a_larger_raise_is_still_refused(self):
        ceiling = cli.wrist_flex_ceiling_for({"wrist_flex": 68.703}, None)
        cmd = {"arm_shoulder_pan.pos": -0.33, "arm_shoulder_lift.pos": -79.0, "arm_elbow_flex.pos": 35.0, "arm_wrist_flex.pos": 69.05, **BASE_ZERO}
        cli.arm_only_command(cmd, ceiling)  # does not raise
        with self.assertRaises(RuntimeError):
            cli.arm_only_command({**cmd, "arm_wrist_flex.pos": 69.5}, ceiling)

    def test_the_cycle_uses_the_shared_margin_and_no_literal_0_2(self):
        text = open(os.path.join(ROOT, "bin", "laptop", "manipulation", "run_single_cube_cycle.py")).read()
        self.assertEqual(len(re.findall(r"\+ cli\.WRIST_FLEX_CEILING_MARGIN_DEG", text)), 1)  # run_pose; Cycle.grip keeps its own 0.2
        self.assertNotRegex(text, r"for item in poses\]\) \+ 0\.2")


if __name__ == "__main__":
    unittest.main()
