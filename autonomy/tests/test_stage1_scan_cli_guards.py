#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_stage1_scan_cli_guards.py: the outgoing-command guard of the stage-1 scan CLI: the base velocity
# of every command is EXACTLY zero or nothing is sent, the limp scan-mode gripper is never commanded, and wrist_flex
# is never commanded above its ceiling. Pure function: no robot, no network.
# Run directly: python3 autonomy/tests/test_stage1_scan_cli_guards.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "perception"))

sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "perception"))

import cartesian_lift  # noqa: E402
from hybrid_pick_place import GRIPPER_OPEN, KNOWN_GOOD_GRASP_POSE_DEG  # noqa: E402
from run_stage1_scan import (  # noqa: E402
    PREGRASP_MIN_LINK_HEIGHT_M,
    arm_only_command,
    gripper_open_refusal,
    APPROACH_MIN_LINK_HEIGHT_M,
    PREGRASP_CORRECT_MAX_PAN_DEG,
    pregrasp_descend_poses,
    pregrasp_correct_poses,
    pregrasp_poses,
    pregrasp_return_poses,
    wrist_flex_ceiling_for,
)
from scan_poses import plan_scan_segments  # noqa: E402
from scan_world import ARRIVAL_TOLERANCE_DEG, ScanRefused  # noqa: E402

COMMAND = {"arm_shoulder_pan.pos": -9.0, "arm_shoulder_lift.pos": 9.0, "arm_elbow_flex.pos": -76.0, "arm_wrist_flex.pos": 101.0,
           "arm_wrist_roll.pos": -97.0, "arm_gripper.pos": 0.0, "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}


class OutgoingCommandGuard(unittest.TestCase):
    def test_the_gripper_key_is_removed_and_the_rest_is_unchanged(self):
        sent = arm_only_command(COMMAND, wrist_flex_ceiling=101.5)
        self.assertNotIn("arm_gripper.pos", sent)
        self.assertEqual({k: v for k, v in COMMAND.items() if k != "arm_gripper.pos"}, sent)

    def test_any_non_zero_base_velocity_is_refused_even_one_count(self):
        for key, value in (("x.vel", 0.0022), ("y.vel", -0.0013), ("theta.vel", 0.586), ("x.vel", 0.1)):
            with self.assertRaises(RuntimeError):
                arm_only_command({**COMMAND, key: value}, wrist_flex_ceiling=101.5)

    def test_wrist_flex_above_the_ceiling_is_refused(self):
        with self.assertRaises(RuntimeError):
            arm_only_command({**COMMAND, "arm_wrist_flex.pos": 101.6}, wrist_flex_ceiling=101.5)


LOW_SCAN = {"shoulder_pan": -9.3187, "shoulder_lift": -79.2527, "elbow_flex": 34.6813, "wrist_flex": 68.2637}  # last measured low scan pose


class PregraspZeroTargetTest(unittest.TestCase):
    """PREGRASP_0 target: the PROVEN grasp geometry moved forward and UP by the proven Cartesian solver behind the
    proven gate, at a coarse pan. Non-contact by construction: only 50..70 mm above the proven grasp height."""

    def test_pan_first_at_the_start_height_then_the_solved_pregrasp_with_the_grasp_orientation_kept(self):
        pan_pose, target = pregrasp_poses(LOW_SCAN, pan_deg=-15.4, forward_m=0.035, dz_m=0.070)
        self.assertEqual({**LOW_SCAN, "shoulder_pan": -15.4}, pan_pose.joints)
        self.assertEqual(-15.4, target.joints["shoulder_pan"])
        grasp = cartesian_lift.forward_kinematics({**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0})
        reached = cartesian_lift.forward_kinematics({**target.joints, "shoulder_pan": KNOWN_GOOD_GRASP_POSE_DEG["shoulder_pan"], "wrist_roll": 0.0})
        self.assertAlmostEqual(0.070, reached[2, 3] - grasp[2, 3], places=3)
        self.assertAlmostEqual(0.035, float((reached[:2, 3] ** 2).sum() ** 0.5 - (grasp[:2, 3] ** 2).sum() ** 0.5), places=3)
        self.assertLess(abs(sum(target.joints[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex")) - sum(KNOWN_GOOD_GRASP_POSE_DEG[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex"))), 0.5)

    def test_the_target_is_deterministic_in_its_arguments_so_plan_and_execute_agree(self):
        first = [p.joints for p in pregrasp_poses(LOW_SCAN, -15.4, 0.035, 0.070)]
        self.assertEqual(first, [p.joints for p in pregrasp_poses(dict(LOW_SCAN), -15.4, 0.035, 0.070)])

    def test_the_parameters_are_in_the_pose_ids_so_the_approval_digest_binds_them(self):
        ids = [p.pose_id for p in pregrasp_poses(LOW_SCAN, -15.4, 0.035, 0.070)]
        self.assertEqual(["pregrasp-0-pan-15.4", "pregrasp-0-fwd35-dz70"], ids)

    def test_a_height_outside_the_non_contact_window_is_refused(self):
        for dz_m in (0.0, 0.030, 0.049, 0.071, 0.120):
            with self.assertRaises(ScanRefused):
                pregrasp_poses(LOW_SCAN, -15.4, 0.035, dz_m)

    def test_a_clearance_whose_worst_in_tolerance_realization_would_pass_below_the_floor_is_refused(self):
        for dz_m in (0.058, 0.060, 0.066):  # plans cleanly on nominal joints, but +5/+5/+5 deg of accepted arrival error ends below the floor
            with self.assertRaises(ScanRefused):
                pregrasp_poses(LOW_SCAN, -15.4, 0.035, dz_m)
        with self.assertRaises(ScanRefused):
            pregrasp_poses(LOW_SCAN, -15.4, 0.090, 0.070)

    def test_a_target_the_proven_gate_refuses_is_refused(self):
        with self.assertRaises(ScanRefused):
            pregrasp_poses(LOW_SCAN, -15.4, 0.105, 0.070)  # elbow inside the singularity guard band

    def test_the_whole_path_plans_under_the_pregrasp_floor_and_no_realization_inside_the_arrival_tolerance_breaks_it(self):
        poses = pregrasp_poses(LOW_SCAN, -15.4, 0.035, 0.070)
        self.assertEqual(["ENTRY_RISE", "ENTRY_PAN", "SCAN"], [s.kind for s in plan_scan_segments(LOW_SCAN, poses, min_link_height_m=PREGRASP_MIN_LINK_HEIGHT_M)])
        from scan_poses import link_heights_m

        worst = {**poses[1].joints, **{k: poses[1].joints[k] + ARRIVAL_TOLERANCE_DEG for k in ("shoulder_lift", "elbow_flex", "wrist_flex")}}
        self.assertGreater(min(link_heights_m(worst).values()), PREGRASP_MIN_LINK_HEIGHT_M)

    def test_the_floor_keeps_the_tool_well_above_the_proven_grasp_height(self):
        grasp_tool_z = cartesian_lift.forward_kinematics({**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0})[2, 3]
        self.assertGreaterEqual(PREGRASP_MIN_LINK_HEIGHT_M - grasp_tool_z, 0.040)


class PregraspReturnTest(unittest.TestCase):
    PREGRASP = {"shoulder_pan": -15.4, "shoulder_lift": 6.996, "elbow_flex": 49.59, "wrist_flex": 7.107}
    HOME = {"shoulder_pan": -9.0549, "shoulder_lift": -79.5165, "elbow_flex": 34.5055, "wrist_flex": 67.2967}

    def test_the_exit_rises_in_plane_with_the_pan_held_and_only_then_pans_home(self):
        poses = pregrasp_return_poses(self.HOME)
        segments = plan_scan_segments(self.PREGRASP, poses, allow_low_entry=True)  # the DEFAULT scan floor: the exit ends elevated
        self.assertEqual(["ENTRY_RISE", "ENTRY_PAN"], [s.kind for s in segments])
        self.assertEqual({**self.HOME, "shoulder_pan": -15.4}, segments[0].target)
        self.assertEqual(self.HOME, segments[1].target)

    def test_the_exit_is_refused_without_the_low_entry_permission(self):
        with self.assertRaises(ValueError):
            plan_scan_segments(self.PREGRASP, pregrasp_return_poses(self.HOME))

    def test_the_wrist_flex_ceiling_follows_the_measured_home_only_in_the_return_mode(self):
        measured = {**self.PREGRASP, "wrist_roll": -97.4, "gripper": 80.0}
        # the margin is 0.5 deg since the operator ruling of 2026-10-08 (WRIST_FLEX_CEILING_MARGIN_DEG)
        self.assertAlmostEqual(7.107 + 0.5, wrist_flex_ceiling_for(measured, None))
        self.assertAlmostEqual(67.2967 + 0.5, wrist_flex_ceiling_for(measured, pregrasp_return_poses(self.HOME)))
        self.assertAlmostEqual(101.3 + 0.5, wrist_flex_ceiling_for({**measured, "wrist_flex": 101.3}, pregrasp_return_poses(self.HOME)))  # never lower than where the arm already is


class PregraspLocalCorrectionTest(unittest.TestCase):
    """SMALL MOVE -> CAPTURE -> RECOMPUTE at the non-contact pregrasp height: one variable, bounded, height untouched."""

    MEASURED = {"shoulder_pan": -14.769, "shoulder_lift": 7.429, "elbow_flex": 49.451, "wrist_flex": 8.044}

    def test_a_pan_correction_changes_the_pan_only_and_names_itself_in_the_pose_id(self):
        (pose,) = pregrasp_correct_poses(self.MEASURED, pan_delta_deg=4.22)
        self.assertEqual({**self.MEASURED, "shoulder_pan": -10.549}, pose.joints)
        self.assertEqual("pregrasp-correct-pan+4.22", pose.pose_id)

    def test_a_correction_beyond_the_bound_or_of_nothing_is_refused(self):
        for delta in (PREGRASP_CORRECT_MAX_PAN_DEG + 0.1, -PREGRASP_CORRECT_MAX_PAN_DEG - 0.1, 0.0, float("nan")):
            with self.assertRaises(ScanRefused):
                pregrasp_correct_poses(self.MEASURED, pan_delta_deg=delta)

    def test_it_is_deterministic_in_the_approved_start_pose(self):
        self.assertEqual(pregrasp_correct_poses(self.MEASURED, 4.22)[0].joints, pregrasp_correct_poses(dict(self.MEASURED), 4.22)[0].joints)

    def test_it_plans_under_the_pregrasp_floor_with_the_bounded_allowance_only(self):
        poses = pregrasp_correct_poses(self.MEASURED, 4.22)
        kinds = [s.kind for s in plan_scan_segments(self.MEASURED, poses, allow_low_entry=True, min_link_height_m=PREGRASP_MIN_LINK_HEIGHT_M, low_pan_allowance_deg=PREGRASP_CORRECT_MAX_PAN_DEG)]
        self.assertEqual(["ENTRY_RISE", "ENTRY_PAN"], kinds)
        with self.assertRaises(ValueError):
            plan_scan_segments(self.MEASURED, poses, allow_low_entry=True, min_link_height_m=PREGRASP_MIN_LINK_HEIGHT_M)


class BoundedNonContactDescentTest(unittest.TestCase):
    """One bounded descent above the cube: the PROVEN grasp geometry at a lower clearance, pan held at its measured value,
    never below the height at which the jaw tips could reach the cube top plane."""

    MEASURED = {"shoulder_pan": -10.9011, "shoulder_lift": 8.044, "elbow_flex": 49.4505, "wrist_flex": 8.2198}  # after the pan correction: +30 mm out, +66 mm up

    def test_the_target_is_the_proven_grasp_geometry_lower_with_the_measured_pan_held(self):
        (pose,) = pregrasp_descend_poses(self.MEASURED, forward_m=0.035, dz_m=0.043)
        self.assertEqual(self.MEASURED["shoulder_pan"], pose.joints["shoulder_pan"])
        grasp = cartesian_lift.forward_kinematics({**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0})
        reached = cartesian_lift.forward_kinematics({**pose.joints, "shoulder_pan": KNOWN_GOOD_GRASP_POSE_DEG["shoulder_pan"], "wrist_roll": 0.0})
        self.assertAlmostEqual(0.043, reached[2, 3] - grasp[2, 3], places=3)
        self.assertLess(abs(sum(pose.joints[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex")) - sum(KNOWN_GOOD_GRASP_POSE_DEG[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex"))), 0.5)
        self.assertEqual("pregrasp-descend-fwd35-dz43", pose.pose_id)

    def test_one_step_is_a_descent_of_at_most_30_mm_and_never_a_rise_or_a_radial_jump(self):
        for forward_m, dz_m in ((0.035, 0.0355), (0.035, 0.070), (0.035, 0.0658), (0.050, 0.043), (0.010, 0.043)):  # 30.3 mm drop; rise; no real descent; +20 / -20 mm radial
            with self.assertRaises(ScanRefused, msg=(forward_m, dz_m)):
                pregrasp_descend_poses(self.MEASURED, forward_m, dz_m)

    def test_it_plans_under_the_approach_floor_only_and_the_unchanged_sag_rule_sets_the_lowest_step(self):
        poses = pregrasp_descend_poses(self.MEASURED, 0.035, 0.043)
        segments = plan_scan_segments(self.MEASURED, poses, allow_low_entry=True, min_link_height_m=APPROACH_MIN_LINK_HEIGHT_M)
        self.assertEqual(["ENTRY_RISE", "ENTRY_PAN"], [s.kind for s in segments])
        self.assertEqual(poses[0].joints, segments[0].target)  # the in-plane descent with the pan held; the second segment is a zero-move that observes
        with self.assertRaises(ValueError):
            plan_scan_segments(self.MEASURED, poses, allow_low_entry=True, min_link_height_m=PREGRASP_MIN_LINK_HEIGHT_M)
        with self.assertRaises(ValueError):
            plan_scan_segments(self.MEASURED, pregrasp_descend_poses(self.MEASURED, 0.035, 0.040), allow_low_entry=True, min_link_height_m=APPROACH_MIN_LINK_HEIGHT_M)  # no sag margin left

    def test_the_approach_floor_keeps_the_jaw_tips_above_the_cube_top_plane(self):
        grasp_tool_z = cartesian_lift.forward_kinematics({**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0})[2, 3]
        self.assertGreaterEqual(APPROACH_MIN_LINK_HEIGHT_M - grasp_tool_z, 0.0254 + 0.0015)  # even if the tips reach the floor at the proven grasp


class GripperOpenGateTest(unittest.TestCase):
    def test_only_a_gripper_measured_in_the_proven_normal_open_region_may_descend(self):
        for value in (GRIPPER_OPEN, GRIPPER_OPEN - 2.9, GRIPPER_OPEN + 2.9):
            self.assertIsNone(gripper_open_refusal({"gripper": value}))
        for value in (0.0, 100.0, 66.8, GRIPPER_OPEN - 3.5, GRIPPER_OPEN + 3.5):  # limp past the seam reads clamped; 66.8 is the latch
            self.assertIn("gripper", gripper_open_refusal({"gripper": value}))

    def test_an_unreadable_gripper_fails_closed(self):
        self.assertIn("gripper", gripper_open_refusal({"gripper": float("nan")}))


if __name__ == "__main__":
    unittest.main()
