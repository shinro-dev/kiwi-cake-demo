#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cartesian_lift.py: proves the SO-101 forward/inverse
# kinematics used to compute CARTESIAN_CLEARANCE_LIFT (a small, approximately
# vertical end-effector motion from the known-good floor grasp pose, computed
# from the arm's actual link geometry instead of another guessed joint
# delta). Pure math, no hardware, no lerobot import -- matches the
# autonomy/manipulation/action_adapter.py boundary this module sits beside.
# Run directly: python3 autonomy/tests/test_cartesian_lift.py
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))

from cartesian_lift import (  # noqa: E402
    ACTIVE_LIFT_JOINTS,
    FIXED_LIFT_JOINTS,
    JOINT_ORDER,
    end_effector_position,
    forward_kinematics,
    solve_cartesian_forward_and_z,
    solve_cartesian_z_lift,
)

# The known-good floor grasp pose from .local-dev/confirmed_grasp_handoff.md
# (pan/lift/elbow/wrist_flex are the session's repeatedly-validated deterministic
# grasp geometry; wrist_roll is a representative runtime-hold value for this
# test fixture only -- production code must always re-read it fresh).
KNOWN_GOOD_GRASP_POSE_DEG = {
    "shoulder_pan": -10.549,
    "shoulder_lift": 43.121,
    "elbow_flex": 54.286,
    "wrist_flex": -33.714,
    "wrist_roll": -97.011,
}


def _is_rigid_transform(t: np.ndarray) -> bool:
    if t.shape != (4, 4):
        return False
    if not np.allclose(t[3, :], [0.0, 0.0, 0.0, 1.0], atol=1e-9):
        return False
    r = t[:3, :3]
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-6):
        return False
    return abs(np.linalg.det(r) - 1.0) < 1e-6


class TestJointOrderConstants(unittest.TestCase):
    def test_active_and_fixed_lift_joints_partition_joint_order(self):
        self.assertEqual(set(ACTIVE_LIFT_JOINTS) | set(FIXED_LIFT_JOINTS), set(JOINT_ORDER))
        self.assertEqual(set(ACTIVE_LIFT_JOINTS) & set(FIXED_LIFT_JOINTS), set())

    def test_shoulder_pan_and_wrist_roll_are_held_fixed(self):
        self.assertIn("shoulder_pan", FIXED_LIFT_JOINTS)
        self.assertIn("wrist_roll", FIXED_LIFT_JOINTS)


class TestForwardKinematics(unittest.TestCase):
    def test_returns_a_valid_rigid_transform_at_the_known_good_grasp_pose(self):
        t = forward_kinematics(KNOWN_GOOD_GRASP_POSE_DEG)
        self.assertTrue(_is_rigid_transform(t))

    def test_returns_a_valid_rigid_transform_at_all_zero_joints(self):
        zero = dict.fromkeys(JOINT_ORDER, 0.0)
        t = forward_kinematics(zero)
        self.assertTrue(_is_rigid_transform(t))

    def test_end_effector_reach_is_within_the_so101_physical_envelope(self):
        # SO-101's own link lengths (URDF-derived) sum to well under 0.5m; a
        # correct FK must place the gripper frame within a generous envelope
        # around base_link's origin. Catches gross unit/scale errors (e.g. a
        # forgotten deg->rad conversion) without asserting an exact value.
        pos = end_effector_position(KNOWN_GOOD_GRASP_POSE_DEG)
        reach = float(np.linalg.norm(pos))
        self.assertGreater(reach, 0.05)
        self.assertLess(reach, 0.5)

    def test_shoulder_pan_rotation_does_not_change_end_effector_height(self):
        # shoulder_pan's joint axis is parallel to base_link's (assumed
        # world-vertical) Z axis -- it is a pure yaw/pan joint. Rotating it
        # must change X/Y but leave Z unchanged. This is an independent
        # geometric fact about the URDF (see cartesian_lift module docstring
        # for the by-hand derivation), not an artifact of the implementation
        # under test.
        base = dict(KNOWN_GOOD_GRASP_POSE_DEG)
        panned = {**base, "shoulder_pan": base["shoulder_pan"] + 12.0}
        z0 = end_effector_position(base)[2]
        z1 = end_effector_position(panned)[2]
        self.assertAlmostEqual(z0, z1, places=6)
        # and it must actually move X or Y, otherwise the test above is vacuous
        xy0 = end_effector_position(base)[:2]
        xy1 = end_effector_position(panned)[:2]
        self.assertGreater(float(np.linalg.norm(xy1 - xy0)), 0.005)


class TestSolveCartesianZLift(unittest.TestCase):
    def test_zero_delta_returns_essentially_the_current_joints(self):
        result = solve_cartesian_z_lift(KNOWN_GOOD_GRASP_POSE_DEG, delta_z_m=0.0)
        for name in ACTIVE_LIFT_JOINTS:
            self.assertAlmostEqual(result["joint_deg"][name], KNOWN_GOOD_GRASP_POSE_DEG[name], places=2)

    def test_never_touches_shoulder_pan_or_wrist_roll(self):
        result = solve_cartesian_z_lift(KNOWN_GOOD_GRASP_POSE_DEG, delta_z_m=0.02)
        for name in FIXED_LIFT_JOINTS:
            self.assertEqual(result["joint_deg"][name], KNOWN_GOOD_GRASP_POSE_DEG[name])

    def test_achieves_the_requested_z_lift_with_small_xy_drift(self):
        delta_z_m = 0.02  # 2cm, illustrative -- not a claimed physical target
        result = solve_cartesian_z_lift(KNOWN_GOOD_GRASP_POSE_DEG, delta_z_m=delta_z_m)
        self.assertTrue(result["converged"])

        before = end_effector_position(KNOWN_GOOD_GRASP_POSE_DEG)
        after = end_effector_position(result["joint_deg"])
        achieved_dz = after[2] - before[2]
        xy_drift = float(np.linalg.norm(after[:2] - before[:2]))

        self.assertAlmostEqual(achieved_dz, delta_z_m, delta=0.0005)  # within 0.5mm
        self.assertLess(xy_drift, 0.005)  # within 5mm

    def test_solution_stays_within_a_bounded_joint_change_for_a_small_lift(self):
        result = solve_cartesian_z_lift(KNOWN_GOOD_GRASP_POSE_DEG, delta_z_m=0.02)
        for name in ACTIVE_LIFT_JOINTS:
            delta = abs(result["joint_deg"][name] - KNOWN_GOOD_GRASP_POSE_DEG[name])
            self.assertLess(delta, 20.0, f"{name} moved {delta:.2f} deg for a 2cm lift -- suspiciously large")

    def test_preserves_extra_keys_like_gripper(self):
        pose = {**KNOWN_GOOD_GRASP_POSE_DEG, "gripper": 66.8}
        result = solve_cartesian_z_lift(pose, delta_z_m=0.015)
        self.assertEqual(result["joint_deg"]["gripper"], 66.8)

    def test_larger_lift_still_converges_with_small_xy_drift(self):
        result = solve_cartesian_z_lift(KNOWN_GOOD_GRASP_POSE_DEG, delta_z_m=0.04)
        self.assertTrue(result["converged"])
        before = end_effector_position(KNOWN_GOOD_GRASP_POSE_DEG)
        after = end_effector_position(result["joint_deg"])
        self.assertAlmostEqual(after[2] - before[2], 0.04, delta=0.001)
        self.assertLess(float(np.linalg.norm(after[:2] - before[:2])), 0.008)


class TestSolveCartesianForwardAndZ(unittest.TestCase):
    def _radial_and_lateral_unit(self, joint_deg):
        pos = end_effector_position(joint_deg)
        radial_xy = pos[:2] / np.linalg.norm(pos[:2])
        lateral_xy = np.array([-radial_xy[1], radial_xy[0]])
        return radial_xy, lateral_xy

    def test_zero_deltas_returns_essentially_the_current_joints(self):
        result = solve_cartesian_forward_and_z(KNOWN_GOOD_GRASP_POSE_DEG, delta_forward_m=0.0, delta_z_m=0.0)
        for name in ACTIVE_LIFT_JOINTS:
            self.assertAlmostEqual(result["joint_deg"][name], KNOWN_GOOD_GRASP_POSE_DEG[name], places=2)

    def test_never_touches_shoulder_pan_or_wrist_roll(self):
        result = solve_cartesian_forward_and_z(KNOWN_GOOD_GRASP_POSE_DEG, delta_forward_m=0.02, delta_z_m=-0.01)
        for name in FIXED_LIFT_JOINTS:
            self.assertEqual(result["joint_deg"][name], KNOWN_GOOD_GRASP_POSE_DEG[name])

    def test_achieves_combined_forward_and_down_motion_with_small_lateral_drift(self):
        delta_forward_m = 0.02
        delta_z_m = -0.015
        result = solve_cartesian_forward_and_z(
            KNOWN_GOOD_GRASP_POSE_DEG, delta_forward_m=delta_forward_m, delta_z_m=delta_z_m
        )
        self.assertTrue(result["converged"])

        radial_unit, lateral_unit = self._radial_and_lateral_unit(KNOWN_GOOD_GRASP_POSE_DEG)
        before = end_effector_position(KNOWN_GOOD_GRASP_POSE_DEG)
        after = end_effector_position(result["joint_deg"])
        delta_xy = after[:2] - before[:2]

        achieved_forward = float(np.dot(delta_xy, radial_unit))
        achieved_lateral = float(np.dot(delta_xy, lateral_unit))
        achieved_dz = after[2] - before[2]

        self.assertAlmostEqual(achieved_forward, delta_forward_m, delta=0.0005)
        self.assertAlmostEqual(achieved_dz, delta_z_m, delta=0.0005)
        self.assertLess(abs(achieved_lateral), 0.002)  # within 2mm -- the true unwanted drift

    def test_pure_forward_motion_leaves_height_essentially_unchanged(self):
        result = solve_cartesian_forward_and_z(KNOWN_GOOD_GRASP_POSE_DEG, delta_forward_m=0.02, delta_z_m=0.0)
        self.assertTrue(result["converged"])
        before = end_effector_position(KNOWN_GOOD_GRASP_POSE_DEG)
        after = end_effector_position(result["joint_deg"])
        self.assertAlmostEqual(after[2], before[2], delta=0.0005)

    def test_preserves_extra_keys_like_gripper(self):
        pose = {**KNOWN_GOOD_GRASP_POSE_DEG, "gripper": 66.8}
        result = solve_cartesian_forward_and_z(pose, delta_forward_m=0.02, delta_z_m=-0.01)
        self.assertEqual(result["joint_deg"]["gripper"], 66.8)


if __name__ == "__main__":
    unittest.main()
