# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import unittest

import numpy as np

from vla.gates import ActionGate, GateRefusal

ENV = {"joints": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"],
       "joint_min_deg": [-46, -82, -69, -46, -99.75, 63], "joint_max_deg": [20, 97, 90, 78, -93.3, 82.5],
       "floor_tool_z_m": -0.1345, "wrist_roll_deg": -96.6, "start_pose_deg": [-9, -79.5, 34.5, 67.3, -96.6, 79.6]}


def fk_flat(z):
    def fk(joints):
        m = np.eye(4)
        m[2, 3] = z(joints)
        return m
    return fk


def gate(**kw):
    return ActionGate(ENV, fk_flat(lambda j: -0.05), gripper_latch=66.8, gripper_open=80.193, **kw)


START = [-9, -79.5, 34.5, 67.3, -96.6, 79.6]


class GateTest(unittest.TestCase):
    def test_wrist_roll_is_frozen_and_gripper_is_only_latch_or_open(self):
        g = gate()
        g.begin_chunk(START)
        cmd = g.step([-9, -79, 34, 67, -50.0, 74.0])
        self.assertEqual(cmd["arm_wrist_roll.pos"], -96.6)
        self.assertEqual(cmd["arm_gripper.pos"], 80.193)

    def test_latch_needs_debounce_and_holds_through_chatter(self):
        g = gate(debounce_ticks=4, min_latch_ticks=30)
        g.begin_chunk(START)
        seq = [72.0, 74.0, 72.0, 72.0, 72.0, 72.0]  # one blip above resets the count
        grips = [g.step([*START[:5], v])["arm_gripper.pos"] for v in seq]
        self.assertEqual(grips[:5], [80.193] * 5)
        self.assertEqual(grips[5], 66.8)
        chatter = [g.step([*START[:5], v])["arm_gripper.pos"] for v in [78, 72, 78, 72, 78, 78, 78, 78]]
        self.assertTrue(all(x == 66.8 for x in chatter))  # min_latch_ticks keeps it latched

    def test_a_forced_open_does_not_latch_again_until_the_policy_asks_open(self):
        # an empty close (2026-10-09): the episode opens the jaws; a policy that still asks closed must not close them again
        g = gate(debounce_ticks=2, min_latch_ticks=3)
        g.begin_chunk(START)
        for v in [70, 70, 70]:
            g.step([*START[:5], v])
        self.assertTrue(g.latched)
        g.force_open()
        self.assertFalse(g.latched)
        grips = [g.step([*START[:5], 70.0])["arm_gripper.pos"] for _ in range(10)]
        self.assertEqual(grips, [80.193] * 10)
        for v in [79, 79]:  # the policy asks open
            self.assertEqual(g.step([*START[:5], v])["arm_gripper.pos"], 80.193)
        grips = [g.step([*START[:5], 70.0])["arm_gripper.pos"] for _ in range(3)]
        self.assertEqual(grips, [80.193, 66.8, 66.8])  # and only then may it close again (debounce 2)

    def test_release_after_latch_is_reported(self):
        g = gate(debounce_ticks=2, min_latch_ticks=3)
        g.begin_chunk(START)
        for v in [70, 70, 70, 70, 70]:
            g.step([*START[:5], v])
        self.assertTrue(g.latched)
        for v in [79, 79, 79]:
            g.step([*START[:5], v])
        self.assertFalse(g.latched)
        self.assertTrue(g.released_after_latch)

    def test_per_tick_step_is_bounded_and_smoothed(self):
        g = gate(alpha=0.35, max_step_deg=5.0)
        g.begin_chunk(START)
        cmd = g.step([10.0, -79.5, 34.5, 67.3, -96.6, 79.6])  # pan asked 19 deg away in one tick
        self.assertLessEqual(abs(cmd["arm_shoulder_pan.pos"] - START[0]), 5.0 + 1e-9)

    def test_envelope_clamps_each_joint(self):
        g = gate(alpha=1.0, max_step_deg=500.0)
        g.begin_chunk(START)
        cmd = g.step([-200.0, 300.0, 34.5, 67.3, -96.6, 79.6])
        self.assertEqual(cmd["arm_shoulder_pan.pos"], -46)
        self.assertEqual(cmd["arm_shoulder_lift.pos"], 97)

    def test_floor_holds_the_arm_at_the_previous_target_instead_of_going_lower(self):
        # the trials pressed the block 5-35 mm below the deepest demonstration at the pad; the gate holds, never descends
        low = {"lift": None}
        g = ActionGate(ENV, fk_flat(lambda j: -0.20 if j["shoulder_lift"] > 0 else -0.05), gripper_latch=66.8, gripper_open=80.193,
                       alpha=1.0, max_step_deg=500.0)
        g.begin_chunk(START)
        cmd = g.step([-9, 10.0, 34.5, 67.3, -96.6, 79.6])  # this target is below the floor
        self.assertEqual(cmd["arm_shoulder_lift.pos"], START[1])
        self.assertEqual(g.floor_holds, 1)
        cmd = g.step([-9, -60.0, 34.5, 67.3, -96.6, 79.6])  # above the floor again: it moves
        self.assertEqual(cmd["arm_shoulder_lift.pos"], -60.0)

    def test_non_finite_raw_action_is_refused(self):
        g = gate()
        g.begin_chunk(START)
        with self.assertRaises(GateRefusal) as e:
            g.step([float("nan")] * 6)
        self.assertEqual(e.exception.code, "vla_action_not_finite")


if __name__ == "__main__":
    unittest.main()
