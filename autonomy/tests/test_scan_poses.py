#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_scan_poses.py: proves the SCAN_WORLD observation pose
# set is deterministic, stays inside the existing joint/singularity margins,
# keeps every moving link elevated, never names wrist_roll / gripper / base,
# and that transits between poses are FK-checked. Offline kinematics only:
# this does NOT establish that the poses are physically safe on the real rig
# (cable strain, chassis/mast clearance, realized-vs-commanded error) or what
# the wrist camera actually sees from them.
# Run directly: python3 autonomy/tests/test_scan_poses.py
import math
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "perception"))

from canonical_handoff import CANONICAL_REVEAL_POSE, DEFAULT_MAX_TICK_DELTA_WARN  # noqa: E402
from cartesian_lift import end_effector_position, forward_kinematics  # noqa: E402
from hybrid_pick_place import KNOWN_GOOD_GRASP_POSE_DEG  # noqa: E402
from scan_poses import (  # noqa: E402
    DEFAULT_BANDS,
    MAX_ENTRY_PAN_SWEEP_DEG,
    SCAN_CONTROL_FPS,
    SCAN_JOINTS,
    SCAN_MAX_JOINT_SPEED_DEG_S,
    SCAN_MIN_LINK_HEIGHT_M,
    SCAN_PAN_ABS_MAX_DEG,
    SCAN_SAG_ALLOWANCE_DEG,
    STAGE_PAN_TARGETS_DEG,
    generate_scan_poses,
    link_heights_m,
    plan_scan_segments,
    pose_set_digest,
    scan_plan_digest,
    segment_duration_s,
    validate_scan_pose,
    validate_transit,
)

FLOOR_GRASP = dict(KNOWN_GOOD_GRASP_POSE_DEG)
ELEVATED_START = {"shoulder_pan": -9.05, "shoulder_lift": -57.89, "elbow_flex": 17.71, "wrist_flex": 49.80}  # the held reveal pose


def _with_roll(joints, roll=-97.0):
    return {**joints, "wrist_roll": roll}


class BandGeometryTest(unittest.TestCase):
    def test_nominal_depression_is_the_fk_tool_axis_depression(self):
        for band in DEFAULT_BANDS:
            joints = _with_roll({"shoulder_pan": 0.0, **band.joints()})
            tool_z_axis = forward_kinematics(joints)[:3, 2]
            fk_depression = math.degrees(math.asin(-tool_z_axis[2]))
            self.assertAlmostEqual(band.nominal_tool_depression_deg, fk_depression, places=3, msg=band.name)

    def test_bands_look_progressively_steeper(self):
        depressions = [band.nominal_tool_depression_deg for band in DEFAULT_BANDS]
        self.assertEqual(["FAR", "MID", "NEAR"], [band.name for band in DEFAULT_BANDS])
        self.assertEqual(sorted(depressions), depressions)
        self.assertGreater(depressions[0], 0.0)

    def test_far_band_is_the_physically_held_reveal_pose_plus_wrist_flex_only(self):
        far = DEFAULT_BANDS[0].joints()
        self.assertLess(abs(far["shoulder_lift"] - CANONICAL_REVEAL_POSE["arm_shoulder_lift.pos"]), 0.5)
        self.assertLess(abs(far["elbow_flex"] - CANONICAL_REVEAL_POSE["arm_elbow_flex.pos"]), 0.5)
        self.assertLess(abs(far["wrist_flex"] - CANONICAL_REVEAL_POSE["arm_wrist_flex.pos"]), 11.0)

    def test_link_chain_agrees_with_cartesian_lift_forward_kinematics(self):
        joints = _with_roll({"shoulder_pan": -30.0, **DEFAULT_BANDS[1].joints()})
        np.testing.assert_allclose(end_effector_position(joints)[2], link_heights_m(joints)["tool"], atol=1e-9)


class GeneratedPoseSetTest(unittest.TestCase):
    def test_stage_sizes(self):
        self.assertEqual(3, len(generate_scan_poses(stage=1)))
        self.assertEqual(9, len(generate_scan_poses(stage=2)))
        self.assertEqual(15, len(generate_scan_poses(stage=3)))

    def test_stage_one_stays_at_the_physically_exercised_pan(self):
        self.assertEqual((-10.0,), STAGE_PAN_TARGETS_DEG[1])
        self.assertEqual({-10.0}, {pose.joints["shoulder_pan"] for pose in generate_scan_poses(stage=1)})

    def test_unknown_stage_is_refused(self):
        with self.assertRaises(ValueError):
            generate_scan_poses(stage=4)

    def test_every_pose_in_every_stage_passes_validation(self):
        for stage in STAGE_PAN_TARGETS_DEG:
            for pose in generate_scan_poses(stage=stage):
                self.assertEqual([], validate_scan_pose(pose.joints), msg=pose.pose_id)

    def test_every_moving_link_stays_elevated(self):
        for pose in generate_scan_poses(stage=3):
            self.assertGreaterEqual(min(link_heights_m(_with_roll(pose.joints)).values()), SCAN_MIN_LINK_HEIGHT_M, msg=pose.pose_id)

    def test_poses_name_only_the_four_scan_joints(self):
        self.assertEqual(("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex"), SCAN_JOINTS)
        for pose in generate_scan_poses(stage=3):
            self.assertEqual(set(SCAN_JOINTS), set(pose.joints), msg=pose.pose_id)

    def test_pose_ids_are_unique_and_generation_is_deterministic(self):
        first, second = generate_scan_poses(stage=3), generate_scan_poses(stage=3)
        self.assertEqual([p.pose_id for p in first], [p.pose_id for p in second])
        self.assertEqual(len(first), len({p.pose_id for p in first}))

    def test_serpentine_order_changes_pan_or_band_never_both(self):
        poses = generate_scan_poses(stage=3)
        for previous, current in zip(poses, poses[1:]):
            pan_changed = previous.joints["shoulder_pan"] != current.joints["shoulder_pan"]
            band_changed = previous.band != current.band
            self.assertTrue(pan_changed != band_changed, msg=f"{previous.pose_id} -> {current.pose_id}")
            self.assertLessEqual(abs(previous.joints["shoulder_pan"] - current.joints["shoulder_pan"]), 30.0)

    def test_tool_azimuth_is_the_fk_heading_of_the_tool_axis(self):
        by_pan = {pose.joints["shoulder_pan"]: pose for pose in generate_scan_poses(stage=3) if pose.band == "FAR"}
        axis = forward_kinematics(_with_roll(by_pan[-30.0].joints, 0.0))[:3, 2]
        self.assertAlmostEqual(math.degrees(math.atan2(axis[1], axis[0])), by_pan[-30.0].tool_azimuth_deg, places=6)
        self.assertGreater(by_pan[-30.0].tool_azimuth_deg, 0.0)  # negative pan points the tool toward +y of the arm frame
        self.assertLess(by_pan[30.0].tool_azimuth_deg, 0.0)

    def test_tool_azimuth_does_not_depend_on_the_runtime_wrist_roll(self):
        pose = [p for p in generate_scan_poses(stage=3) if p.pose_id == "s3-MID-p-060"][0]
        axis = forward_kinematics(_with_roll(pose.joints, -97.0))[:3, 2]
        self.assertAlmostEqual(math.degrees(math.atan2(axis[1], axis[0])), pose.tool_azimuth_deg, places=2)
        self.assertAlmostEqual(60.0, pose.tool_azimuth_deg, places=2)

    def test_every_pose_keeps_the_height_bound_under_shoulder_sag(self):
        self.assertEqual(5.0, SCAN_SAG_ALLOWANCE_DEG)
        for pose in generate_scan_poses(stage=3):
            sagged = {**pose.joints, "shoulder_lift": pose.joints["shoulder_lift"] + SCAN_SAG_ALLOWANCE_DEG}
            self.assertGreaterEqual(min(link_heights_m(_with_roll(sagged)).values()), SCAN_MIN_LINK_HEIGHT_M, msg=pose.pose_id)

    def test_pose_declares_its_geometry_as_nominal_and_in_the_arm_frame(self):
        pose = generate_scan_poses(stage=1)[0]
        self.assertEqual("ARM_BASE_LINK_FK", pose.frame)
        self.assertEqual("NOMINAL_TOOL_AXIS_UNCALIBRATED", pose.coverage_basis)


class ValidationTest(unittest.TestCase):
    def _valid(self):
        return dict(generate_scan_poses(stage=1)[0].joints)

    def test_wrist_flex_inside_the_limit_margin_is_refused(self):
        self.assertTrue(validate_scan_pose({**self._valid(), "wrist_flex": 93.0}))

    def test_elbow_near_the_singularity_is_refused(self):
        self.assertTrue(validate_scan_pose({**self._valid(), "elbow_flex": 12.0}))

    def test_pan_beyond_the_scan_policy_bound_is_refused(self):
        self.assertTrue(validate_scan_pose({**self._valid(), "shoulder_pan": SCAN_PAN_ABS_MAX_DEG + 1.0}))

    def test_pose_that_only_clears_the_height_bound_without_sag_margin_is_refused(self):
        thin = {"shoulder_pan": -10.0, "shoulder_lift": -40.0, "elbow_flex": 30.0, "wrist_flex": 70.0}  # 0.1075 m nominal
        self.assertTrue(any("sag" in reason for reason in validate_scan_pose(thin)))

    def test_low_pose_is_refused(self):
        reasons = validate_scan_pose({"shoulder_pan": -10.0, **{k: FLOOR_GRASP[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex")}})
        self.assertTrue(any("height" in reason for reason in reasons))

    def test_non_finite_or_extra_or_missing_joint_is_refused(self):
        self.assertTrue(validate_scan_pose({**self._valid(), "wrist_flex": float("nan")}))
        self.assertTrue(validate_scan_pose({**self._valid(), "wrist_roll": 0.0}))
        self.assertTrue(validate_scan_pose({**self._valid(), "x.vel": 0.0}))
        missing = self._valid()
        del missing["elbow_flex"]
        self.assertTrue(validate_scan_pose(missing))


class TransitTest(unittest.TestCase):
    def test_rise_from_the_floor_grasp_pose_with_pan_held_is_accepted(self):
        start = dict(FLOOR_GRASP)
        end = {"shoulder_pan": start["shoulder_pan"], **DEFAULT_BANDS[0].joints()}
        self.assertEqual([], validate_transit(start, end))

    def test_changing_pan_while_low_is_refused(self):
        start = dict(FLOOR_GRASP)
        end = {"shoulder_pan": 20.0, **DEFAULT_BANDS[0].joints()}
        self.assertTrue(any("pan" in reason for reason in validate_transit(start, end)))

    def test_every_consecutive_scan_transit_is_accepted(self):
        poses = generate_scan_poses(stage=3)
        for previous, current in zip(poses, poses[1:]):
            self.assertEqual([], validate_transit(previous.joints, current.joints), msg=f"{previous.pose_id} -> {current.pose_id}")

    def test_transit_ending_below_the_scan_height_is_refused(self):
        high = {"shoulder_pan": -10.0, **DEFAULT_BANDS[0].joints()}
        low = {"shoulder_pan": -10.0, "shoulder_lift": 60.0, "elbow_flex": 60.0, "wrist_flex": 0.0}
        self.assertTrue(any("ends" in reason for reason in validate_transit(high, low)))

    def test_transit_that_dips_between_two_elevated_poses_is_refused(self):
        # Both endpoints keep every link >= 0.125 m, but the straight joint-space
        # path between them sags to about 0.116 m (found by FK search).
        a = {"shoulder_pan": 0.0, "shoulder_lift": -30.0, "elbow_flex": 20.0, "wrist_flex": 60.0}
        b = {"shoulder_pan": 0.0, "shoulder_lift": -15.0, "elbow_flex": 70.0, "wrist_flex": -60.0}
        self.assertEqual([], validate_transit(a, b, min_link_height_m=0.10))
        self.assertTrue(any("dips" in reason for reason in validate_transit(a, b, min_link_height_m=0.12)))


class SegmentPlanTest(unittest.TestCase):
    def test_entry_rises_with_the_measured_pan_held_before_any_pan_motion(self):
        segments = plan_scan_segments(ELEVATED_START, generate_scan_poses(stage=2))
        self.assertEqual("ENTRY_RISE", segments[0].kind)
        self.assertEqual(-9.05, segments[0].target["shoulder_pan"])
        first_pose = generate_scan_poses(stage=2)[0]
        self.assertEqual("ENTRY_PAN", segments[1].kind)
        self.assertEqual(first_pose.joints, segments[1].target)
        self.assertEqual(first_pose.pose_id, segments[1].pose_id)

    def test_low_start_pose_is_refused_by_default(self):
        with self.assertRaises(ValueError) as caught:
            plan_scan_segments({**FLOOR_GRASP}, generate_scan_poses(stage=1))
        self.assertIn("low", str(caught.exception))

    def test_explicit_low_entry_reports_the_floor_level_outward_sweep(self):
        segments = plan_scan_segments({**FLOOR_GRASP}, generate_scan_poses(stage=1), allow_low_entry=True)
        self.assertEqual("ENTRY_RISE", segments[0].kind)
        self.assertGreater(segments[0].outward_sweep_while_low_m, 0.08)  # about 10 cm: the tool swings outward before it is up
        self.assertLess(segments[0].outward_sweep_while_low_m, 0.12)
        self.assertEqual(0.0, segments[2].outward_sweep_while_low_m)

    def test_elevated_entry_has_no_low_sweep(self):
        self.assertEqual(0.0, plan_scan_segments(ELEVATED_START, generate_scan_poses(stage=1))[0].outward_sweep_while_low_m)

    def test_start_pan_far_outside_the_stage_pan_range_is_refused(self):
        self.assertEqual(30.0, MAX_ENTRY_PAN_SWEEP_DEG)
        with self.assertRaises(ValueError) as caught:
            plan_scan_segments({**ELEVATED_START, "shoulder_pan": 55.0}, generate_scan_poses(stage=1))
        self.assertIn("pan", str(caught.exception))

    def test_start_pan_inside_the_stage_pan_range_is_accepted(self):
        self.assertEqual("s3-FAR-p-060", plan_scan_segments(ELEVATED_START, generate_scan_poses(stage=3))[1].pose_id)

    def test_non_finite_measured_start_is_refused(self):
        for joint in ("shoulder_lift", "elbow_flex", "wrist_flex", "shoulder_pan"):
            with self.assertRaises(ValueError, msg=joint):
                plan_scan_segments({**ELEVATED_START, joint: float("nan")}, generate_scan_poses(stage=1))

    def test_every_pose_is_the_target_of_exactly_one_observing_segment(self):
        poses = generate_scan_poses(stage=3)
        segments = plan_scan_segments(ELEVATED_START, poses)
        self.assertEqual([p.pose_id for p in poses], [s.pose_id for s in segments if s.pose_id])

    def test_plan_is_refused_when_the_start_pan_is_outside_the_scan_policy(self):
        with self.assertRaises(ValueError):
            plan_scan_segments({**ELEVATED_START, "shoulder_pan": 120.0}, generate_scan_poses(stage=1))

    def test_segment_targets_never_name_wrist_roll_gripper_or_base(self):
        for segment in plan_scan_segments(ELEVATED_START, generate_scan_poses(stage=3)):
            self.assertEqual(set(SCAN_JOINTS), set(segment.target))

    def test_duration_keeps_every_joint_under_the_speed_cap_and_the_tick_bound(self):
        start = dict(FLOOR_GRASP)
        end = {"shoulder_pan": start["shoulder_pan"], **DEFAULT_BANDS[0].joints()}
        duration = segment_duration_s(start, end)
        largest = max(abs(end[j] - start[j]) for j in SCAN_JOINTS)
        self.assertLessEqual(largest / duration, SCAN_MAX_JOINT_SPEED_DEG_S + 1e-9)
        self.assertLess(largest / (duration * SCAN_CONTROL_FPS), DEFAULT_MAX_TICK_DELTA_WARN)

    def test_tiny_move_still_gets_a_minimum_duration(self):
        pose = generate_scan_poses(stage=1)[0].joints
        self.assertGreaterEqual(segment_duration_s(pose, pose), 1.0)


class PoseSetDigestTest(unittest.TestCase):
    def test_digest_is_deterministic(self):
        self.assertEqual(pose_set_digest(generate_scan_poses(stage=2)), pose_set_digest(generate_scan_poses(stage=2)))

    def test_digest_differs_between_stages(self):
        self.assertNotEqual(pose_set_digest(generate_scan_poses(stage=1)), pose_set_digest(generate_scan_poses(stage=2)))

    def test_digest_changes_when_any_joint_target_changes(self):
        import dataclasses

        poses = generate_scan_poses(stage=1)
        nudged = dataclasses.replace(poses[0], joints={**poses[0].joints, "wrist_flex": poses[0].joints["wrist_flex"] + 0.1})
        self.assertNotEqual(pose_set_digest(poses), pose_set_digest([nudged] + poses[1:]))


class ScanPlanDigestTest(unittest.TestCase):
    def test_plan_digest_binds_the_start_pose_and_the_low_entry_permission(self):
        poses = generate_scan_poses(stage=1)
        base = scan_plan_digest(poses, ELEVATED_START, allow_low_entry=False)
        self.assertEqual(base, scan_plan_digest(poses, dict(ELEVATED_START), allow_low_entry=False))
        self.assertNotEqual(base, scan_plan_digest(poses, {**ELEVATED_START, "shoulder_pan": -9.25}, allow_low_entry=False))
        self.assertNotEqual(base, scan_plan_digest(poses, ELEVATED_START, allow_low_entry=True))
        self.assertNotEqual(base, scan_plan_digest(generate_scan_poses(stage=2), ELEVATED_START, allow_low_entry=False))

    def test_plan_digest_ignores_joints_the_scan_never_commands(self):
        poses = generate_scan_poses(stage=1)
        self.assertEqual(
            scan_plan_digest(poses, ELEVATED_START, allow_low_entry=False),
            scan_plan_digest(poses, {**ELEVATED_START, "wrist_roll": -97.0, "gripper": 80.0}, allow_low_entry=False),
        )


class HeldJointExemptionTest(unittest.TestCase):
    """A hand-taught named scan pose may HOLD a joint near its limit; it may never command it there."""

    UPRIGHT = {"shoulder_pan": -8.879, "shoulder_lift": 9.275, "elbow_flex": -76.264, "wrist_flex": 101.319}

    def test_the_taught_upright_pose_is_refused_without_the_exemption(self):
        self.assertTrue(any("wrist_flex" in reason for reason in validate_scan_pose(self.UPRIGHT)))

    def test_a_pan_only_pose_is_accepted_when_wrist_flex_is_held_at_its_measured_value(self):
        held = {k: self.UPRIGHT[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex")}
        for pan in (-38.879, -8.879, 21.121):
            self.assertEqual([], validate_scan_pose({**self.UPRIGHT, "shoulder_pan": pan}, held))

    def test_the_exemption_never_covers_a_wrist_flex_that_is_commanded_to_move(self):
        held = {"wrist_flex": 101.319}
        for commanded in (101.6, 100.9):
            self.assertTrue(any("wrist_flex" in r for r in validate_scan_pose({**self.UPRIGHT, "wrist_flex": commanded}, held)))


class LowerFloorForPregraspTest(unittest.TestCase):
    """PREGRASP_0: a caller may pass a LOWER link-height floor for a non-contact pregrasp above the cube. The default
    (scan) floor is unchanged for every existing caller, and the pan-only-while-elevated rule never follows the
    lowered floor."""

    LOW_SCAN = {"shoulder_pan": -9.32, "shoulder_lift": -79.25, "elbow_flex": 34.68, "wrist_flex": 68.26}  # measured low scan pose
    PAN_ONLY = {**LOW_SCAN, "shoulder_pan": -15.4}
    PREGRASP = {"shoulder_pan": -15.4, "shoulder_lift": 6.996, "elbow_flex": 49.59, "wrist_flex": 7.107}  # +35 mm forward, +70 mm up from the proven grasp
    FLOOR = -0.060

    def _pose(self, pose_id, joints):
        import dataclasses

        return dataclasses.replace(generate_scan_poses(stage=1)[0], pose_id=pose_id, joints=dict(joints))

    def test_the_pregrasp_pose_is_refused_at_the_scan_floor_and_accepted_at_the_passed_floor(self):
        self.assertTrue(any("lowest link" in r for r in validate_scan_pose(self.PREGRASP)))
        self.assertEqual([], validate_scan_pose(self.PREGRASP, min_link_height_m=self.FLOOR))

    def test_the_sag_margin_is_checked_against_the_passed_floor(self):
        lowest = min(link_heights_m(self.PREGRASP).values())
        self.assertTrue(any("sag" in r for r in validate_scan_pose(self.PREGRASP, min_link_height_m=lowest - 0.005)))

    def test_pan_at_scan_height_then_an_in_plane_descent_is_planned_only_with_the_lower_floor(self):
        poses = [self._pose("pregrasp-0-pan", self.PAN_ONLY), self._pose("pregrasp-0", self.PREGRASP)]
        with self.assertRaises(ValueError):
            plan_scan_segments(self.LOW_SCAN, poses)
        segments = plan_scan_segments(self.LOW_SCAN, poses, min_link_height_m=self.FLOOR)
        self.assertEqual(["ENTRY_RISE", "ENTRY_PAN", "SCAN"], [s.kind for s in segments])
        self.assertEqual(self.LOW_SCAN["shoulder_pan"], segments[0].target["shoulder_pan"])  # the first segment holds the measured pan
        self.assertEqual(self.PREGRASP, segments[2].target)

    def test_a_pan_change_below_the_scan_height_stays_refused_with_the_lower_floor(self):
        swung = {**self.PREGRASP, "shoulder_pan": -5.0}
        self.assertTrue(any("shoulder_pan may only change" in r for r in validate_transit(self.PREGRASP, swung, min_link_height_m=self.FLOOR)))
        with self.assertRaises(ValueError):
            plan_scan_segments(self.PREGRASP, [self._pose("swung", swung)], allow_low_entry=True, min_link_height_m=self.FLOOR)

    def test_a_small_bounded_pan_correction_at_pregrasp_height_needs_an_explicit_allowance(self):
        measured = {"shoulder_pan": -14.769, "shoulder_lift": 7.429, "elbow_flex": 49.451, "wrist_flex": 8.044}  # PREGRASP_0 as realized
        corrected = {**measured, "shoulder_pan": -10.549}
        self.assertTrue(any("shoulder_pan may only change" in r for r in validate_transit(measured, corrected, min_link_height_m=self.FLOOR)))
        self.assertEqual([], validate_transit(measured, corrected, min_link_height_m=self.FLOOR, low_pan_allowance_deg=6.0))
        self.assertTrue(validate_transit(measured, {**measured, "shoulder_pan": -5.0}, min_link_height_m=self.FLOOR, low_pan_allowance_deg=6.0))  # 9.8 deg > allowance
        self.assertTrue(validate_transit(measured, {**corrected, "shoulder_lift": 30.0}, min_link_height_m=self.FLOOR, low_pan_allowance_deg=6.0))  # never combined with a descent below the floor
        segments = plan_scan_segments(measured, [self._pose("pregrasp-correct", corrected)], allow_low_entry=True, min_link_height_m=self.FLOOR, low_pan_allowance_deg=6.0)
        self.assertEqual([measured, corrected], [s.target for s in segments])  # a zero-move entry, then the pan alone
        with self.assertRaises(ValueError):
            plan_scan_segments(measured, [self._pose("pregrasp-correct", corrected)], allow_low_entry=True, min_link_height_m=self.FLOOR)

    def test_the_low_pan_allowance_is_bound_into_the_approval_digest(self):
        poses = [self._pose("pregrasp-correct", {**self.PREGRASP, "shoulder_pan": -10.549})]
        without = scan_plan_digest(poses, self.PREGRASP, True, min_link_height_m=self.FLOOR)
        self.assertEqual(without, scan_plan_digest(poses, self.PREGRASP, True, min_link_height_m=self.FLOOR, low_pan_allowance_deg=0.0))
        self.assertNotEqual(without, scan_plan_digest(poses, self.PREGRASP, True, min_link_height_m=self.FLOOR, low_pan_allowance_deg=6.0))

    def test_a_descent_that_would_pass_below_the_passed_floor_is_refused(self):
        grasp = {**KNOWN_GOOD_GRASP_POSE_DEG}
        self.assertTrue(validate_transit(self.PAN_ONLY, {**grasp, "shoulder_pan": -15.4}, min_link_height_m=self.FLOOR))

    def test_the_floor_is_bound_into_the_approval_digest_and_the_default_digest_is_unchanged(self):
        poses = [self._pose("pregrasp-0", self.PREGRASP)]
        default = scan_plan_digest(poses, self.LOW_SCAN, False)
        self.assertEqual(default, scan_plan_digest(poses, self.LOW_SCAN, False, min_link_height_m=None))
        lowered = scan_plan_digest(poses, self.LOW_SCAN, False, min_link_height_m=self.FLOOR)
        self.assertNotEqual(default, lowered)
        self.assertNotEqual(lowered, scan_plan_digest(poses, self.LOW_SCAN, False, min_link_height_m=-0.080))


if __name__ == "__main__":
    unittest.main()
