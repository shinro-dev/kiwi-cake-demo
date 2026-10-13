# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import unittest

from vla.envelope import ACTION_KEYS, ENVELOPE


class EnvelopeTest(unittest.TestCase):
    def test_shape_and_provenance(self):
        self.assertEqual(ACTION_KEYS[0], "arm_shoulder_pan.pos")
        self.assertEqual(len(ENVELOPE["joint_min_deg"]), 6)
        self.assertTrue(all(lo < hi for lo, hi in zip(ENVELOPE["joint_min_deg"], ENVELOPE["joint_max_deg"])))
        self.assertEqual(len(ENVELOPE["model_sha256"]), 64)
        self.assertGreaterEqual(ENVELOPE["takes"], 30)

    def test_floor_is_below_every_recorded_grasp_and_above_the_ground_plane_guess(self):
        self.assertLess(ENVELOPE["floor_tool_z_m"], -0.11)
        self.assertGreater(ENVELOPE["floor_tool_z_m"], -0.16)

    def test_end_region_holds_where_every_take_ended_and_excludes_every_grasp(self):
        # the takes end held at the pad (no recorded release); the release-by-rule fires only inside this region
        lo, hi = ENVELOPE["end_pose_min_deg"], ENVELOPE["end_pose_max_deg"]
        self.assertEqual(len(lo), 4)
        self.assertGreater(lo[1], ENVELOPE["grasp_lift_max_deg"])  # no grasp pose lies inside the end region
        for trial_end in ([-10.3, 86.5, None, None], [-13.4, 85.9, None, None], [-10.2, 86.2, None, None]):  # trials 5, 2, 3 at 60 s
            self.assertTrue(lo[0] <= trial_end[0] <= hi[0] and lo[1] <= trial_end[1] <= hi[1])


if __name__ == "__main__":
    unittest.main()
