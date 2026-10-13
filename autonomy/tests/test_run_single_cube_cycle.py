#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_run_single_cube_cycle.py: the pure decision and bounding functions of the single-cube local cycle:
# one bounded Cartesian step from a MEASURED pose behind the proven gate, the path (not only the endpoint) checked,
# the pan rule near the cube, the gripper phase gates and the proprioceptive half of OBJECT_SECURED. No robot, no network.
# Run directly: python3 autonomy/tests/test_run_single_cube_cycle.py
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))

import run_single_cube_cycle as cycle  # noqa: E402
from run_single_cube_cycle import ScanRefused, cartesian_step_pose, closed_on_something, gripper_refusal, pan_step_pose, path_report, tool_state  # noqa: E402

ABOVE = {"shoulder_pan": -10.989, "shoulder_lift": 21.4945, "elbow_flex": 49.6264, "wrist_flex": -4.0, "wrist_roll": -97.27, "gripper": 79.917}  # measured after descent 1

# The low scan pose the scripted fakes below already stand in (their HOLD_OBSERVE / EXIT / SETTLE joints and their pan). The
# controller reads its saved pose from a file; in a clean checkout there is none, because the real one is private machine
# data. The tests therefore hand it a SYNTHETIC one, and they do it through the controller's own path resolution
# (KC_RUNTIME_CONFIG -> runtime_paths), not by reaching into the module: what is tested is the way a real run finds the file.
SYNTHETIC_LOW_POSE = {"shoulder_pan": -9.58, "shoulder_lift": -79.253, "elbow_flex": 34.418, "wrist_flex": 67.121, "wrist_roll": -97.3, "gripper": 79.9}


def write_synthetic_runtime(directory: str, **overrides) -> str:
    """A complete private runtime configuration under `directory`, every value synthetic: the saved pose above, a pad template
    that passes the preflight's header checks (three files, two tiny PNGs of one size) and reserved identities. Returns the
    path of the config file. Nothing here is a robot, an address in use or a camera."""
    import cv2
    import numpy as np

    calibration = os.path.join(directory, "calibration")
    template = os.path.join(calibration, "pad-template")
    os.makedirs(template)
    with open(os.path.join(calibration, "scan-home-current.json"), "w") as handle:
        json.dump({"name": "SYNTHETIC_LOW_POSE", "source": "synthetic, written by a test", "measured_joints_deg": SYNTHETIC_LOW_POSE}, handle)
    with open(os.path.join(template, "template.json"), "w") as handle:
        json.dump({"pad_to_raster": [[1000.0, 0.0, 0.0], [0.0, -1000.0, 0.0], [0.0, 0.0, 1.0]], "pad_size_m": [0.415, 0.295]}, handle)
    for name in ("template.png", "valid.png"):
        cv2.imwrite(os.path.join(template, name), np.zeros((4, 6), np.uint8))
    config = {"data_root": directory, "saved_pose_file": "calibration/scan-home-current.json", "pad_template_dir": "calibration/pad-template", "observation_root": "observations",
              "host": {"address": "192.0.2.10", "robot_id": "synthetic-unit"}, "pi_ssh_target": "synthetic-operator@192.0.2.10",
              "cameras": {"wrist_id": "synthetic-wrist-camera", "front_id": None}, "model": {"identity": "synthetic-org/synthetic-detector", "revision": None}, **overrides}
    path = os.path.join(directory, "runtime-config.json")
    with open(path, "w") as handle:
        json.dump(config, handle)
    return path


def use_synthetic_runtime(test: unittest.TestCase, **overrides) -> str:
    """For the length of one test: a synthetic runtime configuration, named by KC_RUNTIME_CONFIG and applied through
    runtime_paths(). Afterwards the module is back on the legacy layout. Returns the temporary directory."""
    directory = tempfile.mkdtemp(prefix="kc-synthetic-runtime-")
    test.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    patch = mock.patch.dict(os.environ, {"KC_RUNTIME_CONFIG": write_synthetic_runtime(directory, **overrides)})
    patch.start()
    test.addCleanup(lambda: cycle.use_runtime_paths(cycle.cli.runtime_paths({})))  # an EMPTY environment: the legacy layout, whatever the process has set
    test.addCleanup(patch.stop)
    cycle.use_runtime_paths(cycle.cli.runtime_paths())
    return directory


class ToolStateTest(unittest.TestCase):
    def test_the_measured_pose_after_descent_1_reads_as_recorded(self):
        state = tool_state(ABOVE)
        self.assertAlmostEqual(21.9, state["out_mm"], delta=0.3)
        self.assertAlmostEqual(36.4, state["up_mm"], delta=0.3)


class CartesianStepTest(unittest.TestCase):
    def test_a_step_is_solved_from_the_measured_pose_with_the_pan_held_and_the_orientation_kept(self):
        pose, report = cartesian_step_pose(ABOVE, forward_m=-0.004, dz_m=-0.020, holding=False)
        after = tool_state(pose.joints)
        self.assertEqual(ABOVE["shoulder_pan"], pose.joints["shoulder_pan"])
        self.assertAlmostEqual(36.4 - 20.0, after["up_mm"], delta=0.5)
        self.assertAlmostEqual(21.9 - 4.0, after["out_mm"], delta=0.5)
        self.assertAlmostEqual(tool_state(ABOVE)["pitch_deg"], after["pitch_deg"], delta=0.5)
        self.assertLess(report["max_deviation_m"], 0.006)
        self.assertEqual("step-fwd-4-dz-20-open", pose.pose_id)

    def test_a_step_is_bounded_and_never_targets_below_the_proven_grasp_height(self):
        for forward_m, dz_m in ((0.0, -0.031), (0.061, 0.0), (0.0, 0.0), (float("nan"), 0.0)):
            with self.assertRaises(ScanRefused):
                cartesian_step_pose(ABOVE, forward_m, dz_m, holding=False)
        low, _ = cartesian_step_pose(ABOVE, 0.0, -0.030, holding=False)
        with self.assertRaises(ScanRefused):
            cartesian_step_pose({**ABOVE, **low.joints}, 0.0, -0.010, holding=False)  # +6.4 mm above the grasp height: -10 would go below it

    def test_the_elbow_guard_sits_at_the_real_straight_arm_singularity_not_at_zero(self):
        self.assertAlmostEqual(-73.8, cycle.ELBOW_STRAIGHT_ARM_DEG, delta=0.3)
        carrying = {"shoulder_pan": -9.407, "shoulder_lift": 25.802, "elbow_flex": 41.099, "wrist_flex": 5.319, "wrist_roll": -97.27, "gripper": 68.6}  # measured while carrying
        pose, report = cartesian_step_pose(carrying, 0.060, 0.006, holding=True)  # the proven gate refuses this one ONLY for elbow 5.4 deg
        self.assertLess(abs(pose.joints["elbow_flex"]), 10.0)
        self.assertGreater(report["min_true_singularity_distance_deg"], 60.0)

    def test_every_other_refusal_of_the_proven_gate_stands_and_the_real_singularity_is_guarded_on_target_and_path(self):
        solved = {"joint_deg": {"shoulder_pan": 0.0, "shoulder_lift": 98.5, "elbow_flex": 5.0, "wrist_flex": 0.0, "wrist_roll": 0.0}, "achieved_delta_z_m": 0.0, "achieved_delta_forward_m": 0.03, "lateral_drift_m": 0.0}
        start = {"shoulder_pan": 0.0, "wrist_roll": 0.0}
        accepted, reason = cycle.gate_step(solved, start, {"delta_forward_m": 0.03, "delta_z_m": 0.0})
        self.assertFalse(accepted)
        self.assertIn("shoulder_lift", reason)  # a joint-limit objection is never waived
        near_straight = {**solved, "joint_deg": {**solved["joint_deg"], "shoulder_lift": 40.0, "elbow_flex": -60.0}}
        self.assertTrue(cycle.gate_step(near_straight, start, {"delta_forward_m": 0.03, "delta_z_m": 0.0})[0])  # the proven gate accepts -60 (it guards 0 and 180 only)
        report = path_report(ABOVE, {**ABOVE, "elbow_flex": -60.0})
        self.assertLess(report["min_true_singularity_distance_deg"], cycle.TRUE_SINGULARITY_MARGIN_DEG)  # ...and the PATH bound is what stops an approach to the real one

    def test_the_path_report_sees_what_lies_between_the_endpoints(self):
        report = path_report(ABOVE, {**ABOVE, "shoulder_lift": 60.0, "elbow_flex": -20.0, "wrist_flex": 24.0})
        self.assertLess(report["elbow_range_deg"][0], 0.0)
        self.assertGreater(report["elbow_range_deg"][1], 0.0)  # the band is crossed ON the path even though both ends are outside it
        self.assertGreater(report["max_deviation_m"], 0.0)


class ProvenGraspPoseTest(unittest.TestCase):
    STRADDLING = {"shoulder_pan": -9.495, "shoulder_lift": 33.099, "elbow_flex": 48.571, "wrist_flex": -9.714, "wrist_roll": -97.27, "gripper": 79.917}  # measured: +2.8 out, +10.1 up, pitch 72

    def test_it_is_the_proven_joint_geometry_shifted_so_the_proven_orientation_is_restored(self):
        from hybrid_pick_place import KNOWN_GOOD_GRASP_POSE_DEG as G

        pose, report = cycle.proven_grasp_pose(self.STRADDLING, 0.010, 0.002)
        after = tool_state(pose.joints)
        self.assertAlmostEqual(sum(G[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex")), after["pitch_deg"], delta=0.3)
        self.assertAlmostEqual(10.0, after["out_mm"], delta=0.5)
        self.assertAlmostEqual(2.0, after["up_mm"], delta=0.5)
        self.assertEqual(self.STRADDLING["shoulder_pan"], pose.joints["shoulder_pan"])
        self.assertGreaterEqual(report["lowest_link_m"], cycle.GRASP_TOOL_Z_M - 0.002)

    def test_it_is_bounded_in_shift_and_in_tool_travel_from_the_measured_pose(self):
        for forward_m, dz_m in ((-0.005, 0.0), (0.0, -0.001), (0.061, 0.0), (0.0, 0.031)):
            with self.assertRaises(ScanRefused):
                cycle.proven_grasp_pose(self.STRADDLING, forward_m, dz_m)
        with self.assertRaises(ScanRefused):
            cycle.proven_grasp_pose(ABOVE, 0.010, 0.002)  # 36 mm above: more than one bounded approach away


class PanStepTest(unittest.TestCase):
    def test_the_open_jaws_may_pan_only_while_their_tips_are_above_the_cube_top(self):
        self.assertAlmostEqual(-8.789, pan_step_pose(ABOVE, 2.2, holding=False).joints["shoulder_pan"], places=3)
        straddling, _ = cartesian_step_pose(ABOVE, 0.0, -0.020, holding=False)
        with self.assertRaises(ScanRefused):
            pan_step_pose({**ABOVE, **straddling.joints}, 2.2, holding=False)
        self.assertIsNotNone(pan_step_pose({**ABOVE, **straddling.joints}, 2.2, holding=True))  # carrying: the cube moves with the jaws

    def test_a_pan_step_is_bounded(self):
        for delta in (0.0, 6.1, -6.1, float("nan")):
            with self.assertRaises(ScanRefused):
                pan_step_pose(ABOVE, delta, holding=False)


class CarryPoseTest(unittest.TestCase):
    HELD = {"shoulder_pan": -6.5, "shoulder_lift": 16.8, "elbow_flex": 66.5, "wrist_flex": -14.0, "wrist_roll": -97.3, "gripper": 68.6}  # after the lifts

    def test_a_carry_target_has_the_proven_orientation_and_may_pass_elbow_zero_under_the_corrected_guard(self):
        from hybrid_pick_place import KNOWN_GOOD_GRASP_POSE_DEG as G

        pose, report = cycle.proven_orientation_pose(self.HELD, 0.030, 0.030)
        self.assertAlmostEqual(sum(G[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex")), tool_state(pose.joints)["pitch_deg"], delta=0.3)
        far, report = cycle.proven_orientation_pose({**self.HELD, **cycle.proven_orientation_pose(self.HELD, 0.030, 0.030)[0].joints, **{"shoulder_lift": 30.0, "elbow_flex": 30.0, "wrist_flex": 3.7}}, 0.105, 0.030)
        self.assertLess(far.joints["elbow_flex"], 10.0)  # inside or beyond the proven gate's 0/180 band, far from the real singularity
        self.assertGreater(report["min_true_singularity_distance_deg"], cycle.TRUE_SINGULARITY_MARGIN_DEG)

    def test_a_carry_step_is_bounded_in_travel_and_in_range(self):
        with self.assertRaises(ScanRefused):
            cycle.proven_orientation_pose(self.HELD, 0.120, 0.030)  # more than 65 mm of tool travel in one step
        with self.assertRaises(ScanRefused):
            cycle.proven_orientation_pose(self.HELD, 0.200, 0.030)


class GripperPredicateTest(unittest.TestCase):
    def test_phase_gates_fail_closed(self):
        self.assertIsNone(gripper_refusal({"gripper": 79.9}, holding=False))
        self.assertIsNone(gripper_refusal({"gripper": 68.0}, holding=True))
        for value, holding in ((68.0, False), (79.9, True), (float("nan"), False), (float("nan"), True), (0.0, True)):
            self.assertIsNotNone(gripper_refusal({"gripper": value}, holding))

    def test_an_empty_close_reaches_the_latch_value_and_a_cube_stalls_the_jaw_above_it(self):
        self.assertFalse(closed_on_something(66.8))
        self.assertFalse(closed_on_something(67.2))
        self.assertTrue(closed_on_something(67.9))
        self.assertTrue(closed_on_something(68.1))
        self.assertFalse(closed_on_something(79.9))  # never closed
        self.assertFalse(closed_on_something(float("nan")))

    def test_the_contact_floor_is_a_plausibility_bound_below_the_proven_grasp_and_the_non_contact_floor_clears_the_cube(self):
        self.assertLess(cycle.CONTACT_FLOOR_M, cycle.GRASP_TOOL_Z_M)
        self.assertGreaterEqual(cycle.NON_CONTACT_FLOOR_M - cycle.GRASP_TOOL_Z_M, 0.0254)


class PerceptionDecisionTest(unittest.TestCase):
    """The decisions of the 2026-09-19 physical cycle, reproduced from its recorded pixels and measured poses."""

    def test_the_coarse_rule_returns_the_grasp_that_physically_worked_for_the_reference_frame(self):
        coarse = cycle.coarse_pregrasp_from_low_pose((553.0, 545.0), -9.582)
        self.assertAlmostEqual(-9.495, coarse["pan_deg"], delta=0.05)
        self.assertAlmostEqual(11.4, coarse["cube_out_mm"], delta=0.1)
        self.assertAlmostEqual(11.4 + cycle.PREGRASP_OUTWARD_BIAS_MM, coarse["forward_mm"], delta=0.1)
        right = cycle.coarse_pregrasp_from_low_pose((553.0 + 59.0, 545.0), -9.582)  # a cube seen 59 px further right needs +5 deg of pan reading
        self.assertAlmostEqual(coarse["pan_deg"] + 5.0, right["pan_deg"], delta=0.05)

    def test_alignment_matches_the_recorded_decisions(self):
        before = cycle.alignment_from_bbox((505, 187, 789, 505), 67.1)  # after descent 1: trimmed by +2.2 deg in the run
        self.assertFalse(before["aligned"])
        self.assertAlmostEqual(10.3, before["lateral_mm"], delta=0.5)
        self.assertAlmostEqual(2.1, cycle.lateral_trim_deg(before["lateral_mm"], 0.205), delta=0.2)
        after = cycle.alignment_from_bbox((411, 142, 732, 482), 68.6)  # after the trim: approached and grasped
        self.assertTrue(after["aligned"])
        self.assertAlmostEqual(3.0, after["lateral_mm"], delta=0.5)

    def test_a_trim_is_bounded_and_signed_like_the_pan_reading(self):
        self.assertGreater(cycle.lateral_trim_deg(+15.0, 0.2), 0.0)  # cube to the right -> camera turns right -> reading increases
        self.assertLess(cycle.lateral_trim_deg(-10.0, 0.2), 0.0)
        self.assertLessEqual(abs(cycle.lateral_trim_deg(+80.0, 0.15)), cycle.STEP_MAX_PAN_DEG)

    def test_retention_is_high_for_a_scene_rigid_with_the_camera_and_low_when_it_moves(self):
        import numpy as np

        rng = np.random.default_rng(7)
        floor = (rng.random((720, 1280)) * 255).astype(np.uint8)
        held = floor.copy(); held[300:660, 380:800] = 200; held[300:380, 380:800] = 90  # a cube with a darker far face between the jaws
        lifted = np.roll(floor, 40, axis=0); lifted[300:660, 380:800] = 200; lifted[300:380, 380:800] = 90  # the floor moved, the cube did not
        dropped = np.roll(floor, 40, axis=0); dropped[420:600, 480:700] = 200; dropped[420:460, 480:700] = 90  # the cube stayed on the floor: smaller, elsewhere
        self.assertGreater(cycle.retention_score(held, lifted), cycle.RETENTION_MIN_SCORE)
        self.assertLess(cycle.retention_score(held, dropped), cycle.RETENTION_MIN_SCORE)

    def test_pad_distances(self):
        self.assertAlmostEqual(35.0, cycle.distance_outside_pad_mm((0.0145, -0.1825)), delta=0.1)
        self.assertEqual(0.0, cycle.distance_outside_pad_mm((0.0, 0.0)))
        self.assertAlmostEqual(32.5, cycle.placed_margin_mm((0.00945, -0.11496)), delta=0.1)
        self.assertLess(cycle.placed_margin_mm((0.0145, -0.1825)), 0.0)
        self.assertAlmostEqual(17.96, cycle.PLACED_MIN_EDGE_MARGIN_MM, delta=0.01)  # the footprint, not the centre, must be inside


def _pad_view(advance_mm=0.0, lateral_mm=0.0, scale=0.0004):
    """Synthetic low-pose PAD_FRAME: pixel (553, 545) sees PAD (0.0145, -0.1825), image-up is pad +y (the reach direction).
    A base that advanced by advance_mm sees every fixed pixel that much deeper in the pad."""
    import numpy as np

    image_to_pad = np.array([[scale, 0.0, 0.0145 - 553.0 * scale + lateral_mm / 1000.0], [0.0, -scale, -0.1825 + 545.0 * scale + advance_mm / 1000.0], [0.0, 0.0, 1.0]])
    return {"pad_identity": True, "pad_frame_valid": True, "homography_image_to_pad": image_to_pad.tolist(), "homography_pad_to_image": np.linalg.inv(image_to_pad).tolist()}


class ReleaseAdmissionTest(unittest.TestCase):
    """Release admission from pad-relative visual evidence: FK only measures the short travel from the grasp radius."""

    def test_the_requirement_is_the_footprint_half_diagonal_plus_the_clearance_target(self):
        self.assertAlmostEqual(17.96 + 20.0, cycle.PLACE_REQUIRED_CENTRE_MM, delta=0.01)

    def test_the_reach_direction_comes_from_the_pad_homography(self):
        direction = cycle.reach_direction_pad(_pad_view()["homography_image_to_pad"], (553.0, 545.0))
        self.assertAlmostEqual(0.0, direction[0], delta=1e-6)
        self.assertAlmostEqual(1.0, direction[1], delta=1e-6)

    def test_fk_travel_counts_only_for_what_it_physically_realized_on_the_worst_recorded_run(self):
        anchor, up = (0.0, -0.1475 + 0.010), (0.0, 1.0)  # the grasp point lies 10 mm inside the near edge
        self.assertAlmostEqual(10.0 - cycle.VISUAL_ANCHOR_UNCERTAINTY_MM, cycle.release_margin_worst_mm(anchor, up, 0.0), delta=0.01)
        self.assertAlmostEqual(10.0 - cycle.VISUAL_ANCHOR_UNCERTAINTY_MM + 64.0 / cycle.FK_TRAVEL_OVERSTATEMENT, cycle.release_margin_worst_mm(anchor, up, 64.0), delta=0.01)
        self.assertAlmostEqual(10.0 - cycle.VISUAL_ANCHOR_UNCERTAINTY_MM - 8.0, cycle.release_margin_worst_mm(anchor, up, -8.0), delta=0.01)  # a lagging arm counts in full
        # the accepted run of 2026-09-20: 20.6 mm outside, FK travel 65.8 mm, perception then measured 21.4 mm inside
        self.assertLess(cycle.release_margin_worst_mm((0.013, -0.1475 - 0.0206), up, 65.8), 21.4)
        self.assertLess(cycle.release_margin_worst_mm((0.013, -0.1475 - 0.0206), up, 65.8), cycle.PLACE_REQUIRED_CENTRE_MM)  # that release would NOT be admitted now

    def test_the_far_and_side_edges_bound_the_release_as_well(self):
        self.assertLess(cycle.release_margin_worst_mm((0.0, 0.1475 - 0.030), (0.0, 1.0), 40.0), 0.0)  # the optimistic end would cross the far edge
        self.assertLess(cycle.release_margin_worst_mm((0.2075 - 0.020, 0.0), (0.0, 1.0), 0.0), cycle.PLACE_REQUIRED_CENTRE_MM)

    def test_arm_only_is_preferred_where_it_achieves_the_margin_and_the_base_advances_otherwise(self):
        inside = cycle.placement_plan((0.0, -0.1475 + 0.012), (0.0, 1.0), grasp_out_mm=30.0)
        self.assertEqual("ARM_ONLY", inside["mode"])
        self.assertGreaterEqual(cycle.release_margin_worst_mm((0.0, -0.1475 + 0.012), (0.0, 1.0), inside["fk_travel_mm"]), cycle.PLACE_REQUIRED_CENTRE_MM)
        self.assertLessEqual(30.0 + inside["fk_travel_mm"], cycle.ARM_LOADED_REACH_OUT_MM)
        outside = cycle.placement_plan((0.013, -0.1475 - 0.0206), (0.0, 1.0), grasp_out_mm=29.0)  # the accepted run's start
        self.assertEqual("BASE_ADVANCE", outside["mode"])
        self.assertAlmostEqual(20.6 + cycle.PLACE_REQUIRED_CENTRE_MM + cycle.VISUAL_ANCHOR_UNCERTAINTY_MM + cycle.BASE_TARGET_BUFFER_MM, outside["base_advance_mm"], delta=1.0)
        with self.assertRaises(cycle.CycleFailed):
            cycle.placement_plan((0.0, -0.1475 - 0.150), (0.0, 1.0), grasp_out_mm=30.0)  # farther than the bounded base advance covers

    def test_a_base_displacement_is_measured_between_two_pad_frames_taken_with_the_arm_held(self):
        anchor = (0.0145, -0.1825)
        moved = cycle.base_displacement(anchor, (0.0, 1.0), _pad_view()["homography_pad_to_image"], _pad_view(advance_mm=42.0, lateral_mm=3.0)["homography_image_to_pad"])
        self.assertAlmostEqual(42.0, moved["advance_mm"], delta=0.01)
        self.assertAlmostEqual(3.0, abs(moved["lateral_mm"]), delta=0.01)
        self.assertLess(moved["distortion_mm"], 0.01)
        self.assertAlmostEqual(-0.1825 + 0.042, moved["anchor_pad_m"][1], delta=1e-6)
        stretched = cycle.base_displacement(anchor, (0.0, 1.0), _pad_view()["homography_pad_to_image"], _pad_view(advance_mm=42.0, scale=0.00044)["homography_image_to_pad"])
        self.assertGreater(stretched["distortion_mm"], cycle.BASE_MAX_DISTORTION_MM)  # two frames that disagree on scale are not a measurement

    def test_a_base_pulse_is_bounded_and_the_first_one_is_a_calibration_pulse(self):
        self.assertAlmostEqual(0.375, cycle.base_pulse_s(75.0, cycle.BASE_RATE_PRIOR_MM_S, first=True), delta=1e-6)  # at most half the need before the rate is measured
        self.assertEqual(cycle.BASE_PULSE_S[1], cycle.base_pulse_s(500.0, 100.0, first=False))
        self.assertEqual(cycle.BASE_PULSE_S[0], cycle.base_pulse_s(5.0, 100.0, first=False))
        self.assertLessEqual(cycle.BASE_PULSE_S[1], 0.8)
        self.assertEqual(0.05, cycle.BASE_VX_MPS)  # the commanded speed of the in-process primitive run on hardware; qualifies nothing about the relay


class RecordedDecisionPins(unittest.TestCase):
    """Plan 14's EVIDENCE step as a gate: the decisions the real functions took on the attended runs of 2026-09-26
    (docs/autonomous-demo/one-block-demo-2026-09-26.md, T5), pinned here in the always-run suite. test_recorded_decisions.py
    reads the private evidence and is skipped without it; these numbers are the PLACEMENT_PLAN states and the SEARCH
    refusal of those runs, copied into the test. A change to the placement rule under the default envelope word moves
    one of them and fails here before any robot sees it."""

    START_1, AXIS_1 = (-0.0201, -0.196), (-0.042, 0.999)  # run mb3c1048: PLACEMENT_PLAN anchor and reach direction
    START_2, AXIS_2 = (-0.0936, -0.1698), (-0.086, 0.996)  # run m2476137
    START_3 = (0.163, -0.210)  # the refused start 3 (two Starts, 20:58 and 21:08 UTC): 163 mm right, 62.5 mm outside
    NEAR_MISS_PX = (553.0, 545.0 + 2.7 * 55.9)  # run m8c5f9e9: the block 55.9 mm nearer than the reference contact row

    def test_the_two_placed_runs_planned_108_and_82_mm_of_base_advance(self):
        self.assertEqual(108.0, cycle.base_advance_needed_mm(self.START_1, self.AXIS_1))
        self.assertEqual(82.0, cycle.base_advance_needed_mm(self.START_2, self.AXIS_2))
        self.assertEqual("BASE_ADVANCE", cycle.placement_plan(self.START_1, self.AXIS_1, 11.4)["mode"])
        self.assertEqual("BASE_ADVANCE", cycle.placement_plan(self.START_2, self.AXIS_2, 11.4)["mode"])

    def test_the_refused_start_3_hit_both_limits_the_side_and_the_along_axis_bound(self):
        self.assertIsNone(cycle.base_advance_needed_mm(self.START_3, self.AXIS_1))
        with self.assertRaises(cycle.CycleFailed):
            cycle.placement_plan(self.START_3, self.AXIS_1, 0.0)
        self.assertAlmostEqual(44.5, cycle.placed_margin_mm((self.START_3[0], 0.0)), delta=0.01)  # the side margin against 58.96 raw
        # Along the axis alone (the y edges only, the x edges ignored) the smallest admitting advance is 121.6 mm, 122 on
        # the 1 mm grid, against BASE_MAX_TOTAL_ADVANCE_MM 120: the block stood 1.6 mm past the along-axis limit as well.
        required = cycle.PLACE_REQUIRED_CENTRE_MM + cycle.BASE_TARGET_BUFFER_MM + cycle.VISUAL_ANCHOR_UNCERTAINTY_MM
        self.assertAlmostEqual(58.96, required, delta=0.01)
        along = next(advance for advance in range(0, 400) if cycle.PAD_SIZE_M[1] * 500.0 + (self.START_3[1] + self.AXIS_1[1] * advance / 1000.0) * 1000.0 >= required)
        self.assertEqual(122, along)
        self.assertAlmostEqual(121.6, (required - (cycle.PAD_SIZE_M[1] * 500.0 + self.START_3[1] * 1000.0)) / self.AXIS_1[1], delta=0.05)
        self.assertGreater(along, cycle.BASE_MAX_TOTAL_ADVANCE_MM)

    def test_the_coarse_pregrasp_of_the_grasp_miss_and_of_the_reference_pixel(self):
        miss = cycle.coarse_pregrasp_from_low_pose(self.NEAR_MISS_PX, -9.582)
        self.assertEqual((-44.5, 0.0), (miss["cube_out_mm"], miss["forward_mm"]))  # clamped at 0: the pregrasp could not come nearer
        reference = cycle.coarse_pregrasp_from_low_pose(cycle.LOW_POSE_REF_CONTACT_PX, -9.582)
        self.assertEqual((11.4, 24.4), (reference["cube_out_mm"], reference["forward_mm"]))


class TheProjectedPlacementRule(unittest.TestCase):
    """T6 step 1b: the edge-projected placement rule behind the envelope word 'projected'. Under the default word (no
    KC_PLACEMENT_ENVELOPE) every decision is the proven one; under the word the 15 mm buffer (the lagging arm, which acts
    along the reach direction only) is projected onto each edge normal, the 6 mm anchor uncertainty stays on every edge,
    and the two plan-time callers charge a 6 mm side drift allowance the loop does not, because there the drift is measured."""

    UP = (0.0, 1.0)

    def projected(self):
        return mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("projected",))

    def test_the_four_raw_edge_margins_in_the_order_right_left_far_near(self):
        self.assertEqual((207.5 - 163.0, 207.5 + 163.0, 147.5 + 210.0, 147.5 - 210.0), tuple(round(v, 6) for v in cycle.edge_margins_mm((0.163, -0.210))))
        self.assertAlmostEqual(min(cycle.edge_margins_mm((0.163, -0.210))), cycle.placed_margin_mm((0.163, -0.210)), delta=1e-9)

    def test_under_the_default_word_every_edge_needs_58_96_raw_and_no_allowance_is_charged(self):
        self.assertEqual((), cycle.PLACEMENT_ENVELOPE)
        required = cycle.required_edge_margins_mm(self.UP, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        self.assertEqual(4, len(required))
        for value in required:
            self.assertAlmostEqual(58.96, value, delta=0.01)
        self.assertEqual(required, cycle.required_edge_margins_mm((0.5, 0.866), cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM))

    def test_under_the_word_the_buffer_is_projected_and_the_allowance_goes_to_the_side_edges_only(self):
        right, left, far, near = cycle.required_edge_margins_mm(self.UP, 0.0, projected=True)
        self.assertAlmostEqual(43.96, right, delta=0.01)  # 37.96 + 6 + 15 x |d . e| with d . e = 0
        self.assertAlmostEqual(43.96, left, delta=0.01)
        self.assertAlmostEqual(58.96, far, delta=0.01)
        self.assertAlmostEqual(58.96, near, delta=0.01)
        right, left, far, near = cycle.required_edge_margins_mm(self.UP, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM, projected=True)
        self.assertAlmostEqual(49.96, right, delta=0.01)
        self.assertAlmostEqual(49.96, left, delta=0.01)
        self.assertAlmostEqual(58.96, far, delta=0.01)  # the edges the axis crosses take no allowance
        self.assertAlmostEqual(58.96, near, delta=0.01)
        self.assertEqual(6.0, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)  # empirical: 3.8 and 5.7 mm of sideways drift over one advance on the two placed runs

    def test_a_30_degree_axis_needs_51_46_on_the_sides_and_56_95_along_the_axis(self):
        right, left, far, near = cycle.required_edge_margins_mm((0.5, 0.866), 0.0, projected=True)
        self.assertAlmostEqual(51.46, right, delta=0.01)
        self.assertAlmostEqual(51.46, left, delta=0.01)
        self.assertAlmostEqual(56.95, far, delta=0.01)
        self.assertAlmostEqual(56.95, near, delta=0.01)

    def test_the_buffer_projection_is_even_in_the_sign_of_the_direction(self):
        self.assertEqual(cycle.required_edge_margins_mm((0.5, 0.866), 0.0, projected=True), cycle.required_edge_margins_mm((-0.5, -0.866), 0.0, projected=True))
        self.assertEqual(cycle.required_edge_margins_mm(self.UP, 3.0, projected=True), cycle.required_edge_margins_mm((0.0, -1.0), 3.0, projected=True))

    def test_the_recorded_starts_plan_the_same_108_and_82_under_the_word(self):
        with self.projected():
            self.assertEqual(108.0, cycle.base_advance_needed_mm(RecordedDecisionPins.START_1, RecordedDecisionPins.AXIS_1))
            self.assertEqual(82.0, cycle.base_advance_needed_mm(RecordedDecisionPins.START_2, RecordedDecisionPins.AXIS_2))
            self.assertEqual(108.0, cycle.placement_plan(RecordedDecisionPins.START_1, RecordedDecisionPins.AXIS_1, 11.4, side_allowance_mm=cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)["base_advance_mm"])
            self.assertEqual(82.0, cycle.placement_plan(RecordedDecisionPins.START_2, RecordedDecisionPins.AXIS_2, 11.4, side_allowance_mm=cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)["base_advance_mm"])

    def test_start_3_stays_refused_under_the_word_and_the_values_name_both_limits(self):
        start, axis = RecordedDecisionPins.START_3, RecordedDecisionPins.AXIS_1
        with self.projected():
            self.assertIsNone(cycle.base_advance_needed_mm(start, axis, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM))
            values = cycle.placement_refusal_values(start, axis, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        self.assertAlmostEqual(44.5, values["side_margin_mm"], delta=0.01)
        self.assertAlmostEqual(50.6, values["required_side_margin_mm"], delta=0.05)  # 37.96 + 6 + 15 x 0.042 + 6 at the recorded tilt
        self.assertAlmostEqual(58.9, values["required_axis_margin_mm"], delta=0.05)
        self.assertAlmostEqual(121.6, values["axis_advance_needed_unbounded_mm"], delta=0.1)
        self.assertGreater(values["axis_advance_needed_unbounded_mm"], cycle.BASE_MAX_TOTAL_ADVANCE_MM)
        plain = cycle.placement_refusal_values(start, axis, 0.0)  # the default word: every requirement 58.96
        self.assertAlmostEqual(58.96, plain["required_side_margin_mm"], delta=0.01)
        self.assertAlmostEqual(58.96, plain["required_axis_margin_mm"], delta=0.01)
        self.assertAlmostEqual(44.5, plain["side_margin_mm"], delta=0.01)

    def test_a_block_50_5_mm_inside_a_side_edge_has_no_base_advance_today_and_plans_62_mm_under_the_word(self):
        anchor = (0.157, -0.150)  # 2.5 mm outside the near edge: the loaded arm alone reaches it, so placement_plan is ARM_ONLY under both words
        self.assertIsNone(cycle.base_advance_needed_mm(anchor, self.UP, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM))
        self.assertEqual("ARM_ONLY", cycle.placement_plan(anchor, self.UP, 11.4, side_allowance_mm=cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)["mode"])
        with self.projected():
            self.assertEqual(62.0, cycle.base_advance_needed_mm(anchor, self.UP, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM))
        farther = (0.157, -0.180)  # 32.5 mm outside: beyond the loaded arm, refused today, a 92 mm advance under the word
        with self.assertRaises(cycle.CycleFailed):
            cycle.placement_plan(farther, self.UP, 11.4, side_allowance_mm=cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        with self.projected():
            plan = cycle.placement_plan(farther, self.UP, 11.4, side_allowance_mm=cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        self.assertEqual(("BASE_ADVANCE", 92.0), (plan["mode"], plan["base_advance_mm"]))

    def test_the_loop_asks_no_allowance_because_there_the_drift_is_measured(self):
        anchor = (0.160, -0.150)  # 47.5 mm inside the side edge: short of the 49.96 the plan asks, past the 43.96 the loop asks
        with self.projected():
            self.assertIsNone(cycle.base_advance_needed_mm(anchor, self.UP, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM))
            self.assertEqual(62.0, cycle.base_advance_needed_mm(anchor, self.UP))

    def test_the_side_margin_is_measured_against_the_edges_the_axis_runs_along(self):
        self.assertAlmostEqual(44.5, cycle.side_margin_mm((0.163, -0.210), self.UP), delta=0.01)
        self.assertAlmostEqual(147.5 - 163.0, cycle.side_margin_mm((-0.210, 0.163), (1.0, 0.0)), delta=0.01)  # the axis along x: the y edges are the sides

    def test_the_envelope_words_come_from_the_environment_and_an_unknown_word_is_refused(self):
        self.assertEqual((), cycle.placement_envelope_words({}))
        self.assertEqual((), cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: ""}))
        self.assertEqual(("projected",), cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: "projected"}))
        self.assertEqual(("projected", "near_reverse"), cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: " projected , near_reverse "}))
        for text in ("proven", "PROJECTED", "projected,nonsense", "near_reverse", "projected,projected"):  # a later word without projected is refused too
            with self.subTest(text), self.assertRaises(ScanRefused):
                cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: text})
        self.assertEqual(frozenset(), cycle.PLACEMENT_ENVELOPE_QUALIFIED)
        self.assertTrue(cycle.PLACEMENT_ENVELOPE_QUALIFIED <= set(cycle.PLACEMENT_ENVELOPE_WORDS))

    def test_a_qualified_word_is_named_by_a_qualification_record_that_carries_the_copyright_line(self):
        import glob
        records = glob.glob(os.path.join(ROOT, "docs", "autonomous-demo", "placement-envelope-qualification-*.md"))
        for word in cycle.PLACEMENT_ENVELOPE_QUALIFIED:
            with self.subTest(word):
                naming = [path for path in records if word in open(path).read() and "Copyright 2026 Shinro SAS" in "".join(open(path).readlines()[:3])]
                self.assertTrue(naming, f"{word} is in PLACEMENT_ENVELOPE_QUALIFIED and no docs/autonomous-demo/placement-envelope-qualification-*.md names it")


class TheSidewaysReleaseOffset(unittest.TestCase):
    """T6 step 3a (envelope word side_offset, OFF): the arm releases to the side of the reach axis AFTER the advance, by a
    pan of the loaded arm about the pan axis. Pure model: pivot P = anchor - r u, release point R(delta) = P + r rot(u, s delta),
    s the sign of lateral_trim_deg's convention (+ pan reading = the tool turns RIGHT). Unqualified: the realization and
    the uncertainty are the starting values the calibration session fixes; no LIVE run takes the word before its record."""

    UP = (0.0, 1.0)
    R = 179.0  # GRASP_TOOL_RANGE_M in mm at the proven grasp pose

    def test_the_constants_are_the_starting_values_of_the_design(self):
        self.assertEqual((15.0, 4.0, 1.0), (cycle.PLACE_SIDE_PAN_MAX_DEG, cycle.SIDE_OFFSET_UNCERTAINTY_MM, cycle.SIDE_OFFSET_REALIZATION))
        self.assertAlmostEqual(self.R, cycle.GRASP_TOOL_RANGE_M * 1000.0, delta=0.5)

    def test_for_delta_zero_the_release_point_is_the_anchor_and_for_15_degrees_the_chord_and_the_shrink_are_the_designed_numbers(self):
        anchor = (0.02, -0.05)
        point = cycle.offset_release_point(anchor, self.UP, self.R, 0.0)
        self.assertAlmostEqual(anchor[0], point["point_pad_m"][0], delta=1e-9)
        self.assertAlmostEqual(anchor[1], point["point_pad_m"][1], delta=1e-9)
        self.assertEqual((0.0, 0.0), (point["chord_mm"], point["shrink_mm"]))
        point = cycle.offset_release_point(anchor, self.UP, self.R, 15.0)
        self.assertAlmostEqual(46.3, point["chord_mm"], delta=0.1)  # r sin 15
        self.assertAlmostEqual(6.1, point["shrink_mm"], delta=0.1)  # r (1 - cos 15)
        self.assertAlmostEqual(3.12, cycle.offset_release_point(anchor, self.UP, self.R, 1.0)["chord_mm"], delta=0.01)
        self.assertAlmostEqual(3.8, cycle.offset_release_point(anchor, self.UP, 218.0, 1.0)["chord_mm"], delta=0.01)

    def test_the_sign_follows_the_proven_lateral_convention_a_positive_pan_moves_the_tool_to_the_right(self):
        self.assertGreater(cycle.lateral_trim_deg(20.0, 0.2), 0.0)  # a cube to the RIGHT is brought back by a POSITIVE pan delta: + turns the tool right
        point = cycle.offset_release_point((0.0, -0.05), self.UP, self.R, 10.0)
        self.assertGreater(point["point_pad_m"][0], 0.0, "with the axis along +y, right is +x")
        self.assertLess(point["point_pad_m"][1], -0.05, "the release point comes back toward the pivot")
        mirrored = cycle.offset_release_point((0.0, -0.05), self.UP, self.R, -10.0)
        self.assertAlmostEqual(-point["point_pad_m"][0], mirrored["point_pad_m"][0], delta=1e-9)
        # a reach direction along +x (the base facing the pad's long side): right is -y
        self.assertLess(cycle.offset_release_point((-0.05, 0.0), (1.0, 0.0), self.R, 10.0)["point_pad_m"][1], 0.0)

    def test_delta_is_zero_whenever_the_projected_rule_already_admits(self):
        for anchor, axis in ((RecordedDecisionPins.START_1[0], -0.196 + 0.108), RecordedDecisionPins.AXIS_1), ((RecordedDecisionPins.START_2[0], -0.1698 + 0.082), RecordedDecisionPins.AXIS_2), ((0.0, 0.0), self.UP):
            plan = cycle.release_offset_plan(anchor, axis, grasp_pan_deg=-9.5, frame_pan_deg=-9.5, home_pan_deg=-9.58, r_release_mm=self.R + 11.4 + 8.0)
            self.assertEqual(0.0, plan["delta_deg"], anchor)
            self.assertEqual([round(v, 9) for v in anchor], [round(v, 9) for v in plan["release_point_pad_m"]])

    def test_an_anchor_32_5_mm_inside_a_side_edge_after_the_advance_plans_a_few_degrees_toward_the_pad(self):
        anchor = (0.2075 - 0.0325, -0.05)  # 32.5 mm inside the RIGHT edge: the offset must go LEFT, a negative pan delta
        plan = cycle.release_offset_plan(anchor, self.UP, grasp_pan_deg=-9.5, frame_pan_deg=-9.5, home_pan_deg=-9.58, r_release_mm=self.R)
        self.assertIsNotNone(plan)
        self.assertLess(plan["delta_deg"], 0.0)
        self.assertTrue(-6.0 <= plan["delta_deg"] <= -3.0, plan)  # 43.96 - 32.5 + 4 = 15.5 mm of chord at r 179 is about 5 deg
        self.assertLess(plan["release_point_pad_m"][0], anchor[0])
        self.assertGreaterEqual(min(cycle.edge_margins_mm(plan["release_point_pad_m"])) - cycle.SIDE_OFFSET_UNCERTAINTY_MM, 43.96 - 0.01)

    def test_the_pan_never_leaves_the_return_bound_nor_its_own_cap(self):
        anchor = (0.2075 - 0.0325, -0.05)
        plan = cycle.release_offset_plan(anchor, self.UP, grasp_pan_deg=-9.58 - 20.5, frame_pan_deg=-9.58, home_pan_deg=-9.58, r_release_mm=self.R)  # run 2: 20.5 deg from home, 4.5 left on that side
        self.assertIsNone(plan, "4.5 deg of pan budget on that side cannot reach the 15.5 mm of chord")
        far = (0.2075 - 0.008, -0.05)  # 8 mm inside: about 40 mm of chord plus the buffer's growing projection, 14.5 deg, inside the 15 cap (the envelope's 'about 8 mm at r 179')
        plan = cycle.release_offset_plan(far, self.UP, grasp_pan_deg=-9.58, frame_pan_deg=-9.58, home_pan_deg=-9.58, r_release_mm=self.R)
        self.assertIsNotNone(plan)
        self.assertLessEqual(abs(plan["delta_deg"]), cycle.PLACE_SIDE_PAN_MAX_DEG)
        self.assertIsNone(cycle.release_offset_plan((0.2075 + 0.010, -0.05), self.UP, grasp_pan_deg=-9.58, frame_pan_deg=-9.58, home_pan_deg=-9.58, r_release_mm=self.R), "beyond the cap")
        with mock.patch.object(cycle, "PLACE_SIDE_PAN_MAX_DEG", 25.0):
            self.assertIsNotNone(cycle.release_offset_plan((0.2075 + 0.010, -0.05), self.UP, grasp_pan_deg=-9.58, frame_pan_deg=-9.58, home_pan_deg=-9.58, r_release_mm=self.R))

    def test_the_force_override_is_read_beside_the_words_and_needs_the_word(self):
        self.assertIsNone(cycle.placement_side_offset_force_deg({}))
        self.assertEqual(10.0, cycle.placement_side_offset_force_deg({cycle.PLACEMENT_SIDE_OFFSET_FORCE_ENV: "+10", cycle.PLACEMENT_ENVELOPE_ENV: "projected,side_offset"}))
        for environ in ({cycle.PLACEMENT_SIDE_OFFSET_FORCE_ENV: "+10", cycle.PLACEMENT_ENVELOPE_ENV: "projected"}, {cycle.PLACEMENT_SIDE_OFFSET_FORCE_ENV: "+10"},
                        {cycle.PLACEMENT_SIDE_OFFSET_FORCE_ENV: "+16", cycle.PLACEMENT_ENVELOPE_ENV: "projected,side_offset"}, {cycle.PLACEMENT_SIDE_OFFSET_FORCE_ENV: "ten", cycle.PLACEMENT_ENVELOPE_ENV: "projected,side_offset"}):
            with self.subTest(environ), self.assertRaises(ScanRefused):
                cycle.placement_side_offset_force_deg(environ)


class TheSidewaysReleaseOnTheScriptedMachine(unittest.TestCase):
    """The admission path of step 3a on the scripted machine: without the word PLACE_APPROACH is at the grasp pan; under
    the word with the forced offset the pan is offset, the RELEASE_OFFSET state names the readback it consumed, and the
    admission is decided from the readback, never from the command."""

    def setUp(self):
        self.directory = use_synthetic_runtime(self)

    def run_machine(self, words=(), force=None, **script):
        pans = []
        real = cycle.cli.pregrasp_poses

        def pregrasp_poses(start, pan_deg, forward_m, dz_m):
            pans.append(round(pan_deg, 3))
            return real(start, pan_deg, forward_m, dz_m)

        machine = scripted_machine(cycle, machine_script(**script))
        failures = []
        machine.events = type("Events", (cycle._NoEvents,), {"cycle_failed": lambda self, code, message, values, evidence: failures.append(code)})()
        cycle.set_placement_envelope(tuple(words), None, force)
        try:
            with mock.patch.object(cycle.cli, "pregrasp_poses", pregrasp_poses), contextlib.redirect_stdout(io.StringIO()):
                try:
                    machine.run_cycle()
                except cycle.CycleFailed:
                    pass
        finally:
            cycle.set_placement_envelope((), None)
        return machine, pans, failures

    def test_without_the_word_place_approach_is_at_the_grasp_pan(self):
        machine, pans, failures = self.run_machine()
        self.assertEqual([], failures)
        self.assertIn("RELEASED", machine.transitions)
        self.assertNotIn("RELEASE_OFFSET", machine.transitions)
        self.assertEqual(-9.495, pans[-1], "PLACE_APPROACH's pan is the grasp pan")

    def test_under_the_word_the_forced_offset_pans_the_approach_and_the_state_names_the_readback(self):
        machine, pans, failures = self.run_machine(("projected", "side_offset"), 10.0)
        self.assertEqual([], failures)
        self.assertAlmostEqual(-9.495 + 10.0, pans[-1], delta=1e-6)
        self.assertEqual(1, len([entry for entry in machine.transitions if entry == "RELEASE_OFFSET"]))
        self.assertIn("RELEASED", machine.transitions)
        self.assertLess(machine.transitions.index("RELEASE_OFFSET"), machine.transitions.index("RELEASE_OFFSET_MEASURED"))
        self.assertLess(machine.transitions.index("RELEASE_OFFSET_MEASURED"), machine.transitions.index("RELEASE_ADMISSION"))
        self.assertEqual((10.0, 10.0, 33.1), machine.offset_realized)  # the readback realized the commanded pan: r 190.4 x sin 10

    def test_the_admission_uses_the_readback_not_the_command(self):
        # the pan does NOT realize (the arm stays at the grasp pan -9.495): the readback delta is 0 and so is the modelled offset
        machine, pans, failures = self.run_machine(("projected", "side_offset"), 10.0, place_pan_realized=False)
        self.assertAlmostEqual(-9.495 + 10.0, pans[-1], delta=1e-6)
        self.assertEqual([], failures)
        offset = machine.offset_realized  # kept by the machine for the test: (delta_commanded_deg, delta_measured_deg, chord_mm)
        self.assertEqual((10.0, 0.0, 0.0), offset)

    def test_a_release_the_offset_point_does_not_admit_never_happens(self):
        edge = [0.2075 - 0.062, -0.1825]  # 62 mm inside the right edge: admitted at 49.96 at plan time; a forced 10 deg to the right takes 33 mm and 4 of uncertainty off it
        machine, pans, failures = self.run_machine(("projected", "side_offset"), 10.0, cube_start_pad_m=edge)
        self.assertEqual(["release_not_admitted"], failures)
        self.assertNotIn("grip:open", machine.calls, "the cube stays held")
        self.assertIn("RELEASE_OFFSET", machine.transitions)
        machine, pans, failures = self.run_machine(("projected", "side_offset"), -10.0, cube_start_pad_m=edge)  # to the left: admitted
        self.assertEqual([], failures)
        self.assertIn("RELEASED", machine.transitions)


class TheLateralMeasurement(unittest.TestCase):
    """T6 step 3b skeleton: the pure measurement of a LATERAL base pulse between two pad frames, under honest names
    (aside_mm along the left normal, along_mm along the reach axis), and its plausibility gates. No primitive, no pulse."""

    def test_the_displacement_is_measured_between_two_pad_frames_with_the_arm_held(self):
        anchor = (0.0145, -0.1825)
        moved = cycle.lateral_displacement(anchor, (0.0, 1.0), _pad_view()["homography_pad_to_image"], _pad_view(lateral_mm=-25.0, advance_mm=2.0)["homography_image_to_pad"])
        self.assertAlmostEqual(25.0, abs(moved["aside_mm"]), delta=0.01)
        self.assertAlmostEqual(2.0, moved["along_mm"], delta=0.01)
        self.assertLess(moved["distortion_mm"], 0.01)
        self.assertNotIn("advance_mm", moved, "a lateral move is never labelled an advance")
        self.assertAlmostEqual(0.0145 - 0.025, moved["anchor_pad_m"][0], delta=1e-6)
        self.assertGreater(moved["aside_mm"], 0.0, "the pad frame moved toward -x under the camera: the base went LEFT (along the left normal of +y)")
        base = cycle.base_displacement(anchor, (0.0, 1.0), _pad_view()["homography_pad_to_image"], _pad_view(lateral_mm=-25.0, advance_mm=2.0)["homography_image_to_pad"])
        self.assertAlmostEqual(base["lateral_mm"], moved["aside_mm"], delta=1e-9, msg="the sign convention is base_displacement's lateral_mm")

    def test_the_gates_are_the_forward_ones_transposed(self):
        ok = {"aside_mm": 20.0, "along_mm": 3.0, "distortion_mm": 1.0}
        self.assertIsNone(cycle.lateral_measurement_refusal(ok, +1.0, 0.4))
        self.assertIsNotNone(cycle.lateral_measurement_refusal({**ok, "distortion_mm": 4.1}, +1.0, 0.4))
        self.assertIsNotNone(cycle.lateral_measurement_refusal({**ok, "along_mm": 8.0 + 0.45 * 20.0 + 0.1}, +1.0, 0.4), "a move along the reach axis during a lateral pulse")
        self.assertIsNone(cycle.lateral_measurement_refusal({**ok, "along_mm": 8.0 + 0.45 * 20.0 - 0.1}, +1.0, 0.4))
        self.assertIsNotNone(cycle.lateral_measurement_refusal({**ok, "aside_mm": -5.1}, +1.0, 0.4), "against the commanded sign beyond 5 mm")
        self.assertIsNone(cycle.lateral_measurement_refusal({**ok, "aside_mm": -20.0}, -1.0, 0.4))
        self.assertIsNotNone(cycle.lateral_measurement_refusal({**ok, "aside_mm": 200.0 * 0.4 + 10.0 + 0.1}, +1.0, 0.4), "faster than 200 mm/s x duration + 10")
        self.assertIsNotNone(cycle.lateral_measurement_refusal(None, +1.0, 0.4), "a missing pad frame ends the run")

    def test_no_lateral_call_exists_on_any_path_without_the_word(self):
        import inspect
        source = cycle_and_stage_source()
        self.assertNotIn("lateral_pulse", source, "no lateral primitive is wired into the Cycle in this round")
        closure = inspect.getsource(cycle.make_move_base)
        self.assertEqual(closure.count("vy_mps="), closure.count("vy_mps=0.0"), "every DesiredBaseMotion of the proven closures has vy 0")
        self.assertNotIn("lateral", closure)


def cycle_and_stage_source() -> str:
    """The Cycle's source and the source of every stage policy module its run_cycle hands the Cycle to (as ctx): a check on
    what the cycle does reads both, wherever a stage's procedure lives (docs/autonomous-demo/overnight-plan-2026-09-26.md,
    track P0a)."""
    import inspect

    manipulation = os.path.join(ROOT, "autonomy", "manipulation")
    texts = [inspect.getsource(cycle.Cycle)]
    for name in sorted(os.listdir(manipulation)):
        if name == "stage_policy.py" or (name.startswith("policy_") and name.endswith(".py")):
            with open(os.path.join(manipulation, name), encoding="utf-8") as handle:
                texts.append(handle.read())
    return "\n".join(texts)


def scripted_machine(module, script, real_state=False):
    """The scripted fake of CycleSequencingTest, for ANY controller module: the differential test builds it from the tagged
    source as well. real_state keeps the controller's own state() (the record and the [STATE] line) instead of the name list."""
    import types

    machine = module.Cycle.__new__(module.Cycle)
    machine.transitions, machine.calls, machine.plan_only, machine.base_settle_s, machine.abort = [], [], False, 0.0, {"set": False}
    pose = {"shoulder_pan": script.get("start_pan", -9.58), "shoulder_lift": -79.4, "elbow_flex": 34.4, "wrist_flex": 68.5, "wrist_roll": -97.3, "gripper": 79.9}
    machine.pose = lambda: dict(pose)
    machine.base = {"advance_mm": 0.0, "rate_mm_s": script.get("base_rate_mm_s", 90.0), "stops": script.get("base_stops", True), "arm_drift_deg": script.get("arm_drift_deg", 0.0)}
    start = script.get("cube_start_pad_m", [0.0145, -0.1825])

    def sector_now() -> str:
        """Which sweep sector of the low pose the arm stands in, by its pan relative to the saved low pose's (the machine's
        start pan): CENTER within a degree, LEFT below it, RIGHT above it."""
        offset = pose["shoulder_pan"] - (-9.58)
        return "CENTER" if abs(offset) < 1.0 else ("LEFT" if offset < 0.0 else "RIGHT")

    def low():
        view = _pad_view(advance_mm=machine.base["advance_mm"] * script.get("registration_gain", 1.0), lateral_mm=machine.base["advance_mm"] * script.get("base_lateral_per_mm", 0.0)) if script.get("pad_frame_while_holding", True) or not machine.holding else {"pad_identity": True, "pad_frame_valid": False, "reason": "scripted"}
        # contact_px: the cube's contact pixel in the centre frame; sector_contact_px: in a sweep sector frame (both default to the
        # reference column, from which the coarse pregrasp pan is the frame's own pan).
        contact = script.get("contact_px" if sector_now() == "CENTER" else "sector_contact_px", [553.0, 545.0])
        record = {"cube": {"detected": True, "bbox_xyxy": [515, 485, 590, 581], "contact_px": list(contact)}, "pad_by_template": view, "cube_by_template": {"cube_center_pad_m": list(start)}, "frame": "f"}
        # cube_seen_at (SEARCH SWEEP): sector -> True (the cube is verified there, in PAD_FRAME) or "pad_frame_invalid" (verified,
        # but the pad print is not fixed from that pan); a sector it does not name verifies nothing. Absent: the cube is in
        # every low-pose frame, which is the scene every scenario above has always had.
        seen_at = script.get("cube_seen_at")
        if seen_at is not None:
            outcome = seen_at.get(sector_now())
            if not outcome:
                record = {"cube": {"detected": False, "bbox_xyxy": None, "contact_px": None, "notes": ["scripted: no proposal verified in this sector"]}, "pad_by_template": view, "cube_by_template": None, "frame": "f"}
            elif outcome == "pad_frame_invalid":
                record = {**record, "pad_by_template": {"pad_identity": True, "pad_frame_valid": False, "reason": "scripted"}, "cube_by_template": None}
        return record

    machine.holding = False
    machine.observe = lambda holding: machine.calls.append("observe") or {"measured": dict(pose), "tool": module.tool_state(pose), "perception": [low()]}

    def move_base(vx_mps, duration_s):
        machine.calls.append(f"base:{vx_mps:+.2f}x{duration_s:.2f}")
        machine.base["advance_mm"] += machine.base["rate_mm_s"] * duration_s * (1.0 if vx_mps > 0 else -1.0)
        pose["shoulder_lift"] += machine.base["arm_drift_deg"]
        machine.abort["set"] = script.get("abort_during_pulse", False)
        return machine.base["stops"]

    machine.move_base, machine.base_is_zero = move_base, lambda: True
    boxes = iter(script["boxes"])

    recorded = {  # REAL measured poses of the 2026-09-19 cycle, so the pure planners run for real on every transition
        "PREGRASP": (7.429, 49.451, 8.044), "ALIGN pan trim": (8.044, 49.451, 8.220), "ALIGN non-contact descent": (21.495, 49.626, -4.0),
        "APPROACH descent": (33.099, 48.571, -9.714), "APPROACH proven": (41.451, 50.330, -28.088), "APPROACH advance": (41.451, 50.330, -28.088), "VERIFICATION": (35.385, 54.286, -23.253), "CLEARANCE": (20.088, 60.176, -11.385), "CONFIRM": (16.8, 61.0, -9.0),
        "HOLD_OBSERVE": (-79.253, 34.418, 67.121), "CARRY": (25.802, 41.099, 5.319), "PLACE": (67.736, -35.736, 53.846), "RETREAT": (25.802, 41.099, 5.319), "EXIT": (-79.253, 34.418, 67.121), "SETTLE": (-79.253, 34.418, 67.121)}

    def run_pose(target, holding, floor, pan_allowance=0.0, label="", manipulation_step=True):
        machine.calls.append(label)
        if label.startswith("SEARCH_SWEEP"):  # a sweep sector or the return to the centre: the arm realizes the pan, lift / elbow / wrist_flex are held
            if script.get("abort_in_sector") and label.endswith(script["abort_in_sector"]):
                raise RuntimeError("step aborted: operator abort during a segment: no further motion commanded")
            last = target[-1] if isinstance(target, (list, tuple)) else target
            pose["shoulder_pan"] = last.joints["shoulder_pan"]
            measured = dict(pose)
            return {"measured": measured, "tool": module.tool_state(measured), "perception": [low()]}
        if label == "PLACE lower" and script.get("place_lower_refused"):
            raise ScanRefused("scripted: the gate refuses the lowering")
        lift, elbow, wrist = next(v for k, v in recorded.items() if label.startswith(k))
        measured = {**pose, "shoulder_pan": -9.495, "shoulder_lift": lift, "elbow_flex": elbow, "wrist_flex": wrist}
        if script.get("pan_realized") and label.startswith(("PREGRASP", "ALIGN pan trim")):  # the arm arrives at the commanded pan (the sweep scenarios: the pan is far from the recorded one)
            measured["shoulder_pan"] = (target[-1] if isinstance(target, (list, tuple)) else target).joints["shoulder_pan"]
        if label.startswith(("CARRY", "PLACE", "APPROACH descent")):  # these arrive at their planned target, so the travel bounds see consistent poses
            measured = {**measured, **(target[-1] if isinstance(target, (list, tuple)) else target).joints}
            if label == "CARRY forward" and script.get("carry_stalls"):
                measured = {**measured, "shoulder_lift": 25.802, "elbow_flex": 41.099, "wrist_flex": 5.319}  # the loaded arm gains nothing
        if script.get("place_pan_realized") is False and label.startswith("PLACE_APPROACH"):  # T6 step 3a: the loaded pan of the offset release does NOT arrive where it was commanded
            measured["shoulder_pan"] = -9.495
        tool = module.tool_state(measured)
        machine.holding = holding
        if label.startswith("EXIT"):
            perception = [{**script["final"], "pad_by_template": _pad_view(advance_mm=machine.base["advance_mm"])}]
        elif label.startswith("HOLD_OBSERVE"):
            perception = [low()]
        else:
            perception = [{"cube": {"detected": True, "bbox_xyxy": next(boxes, [411, 192, 732, 532])}, "frame": "f"}]
        if label.startswith(("HOLD_OBSERVE", "EXIT", "SETTLE")):
            pose.update({"shoulder_lift": lift, "elbow_flex": elbow, "wrist_flex": wrist})  # machine.pose() reads the low pose from here on
        if label.startswith(("HOLD_OBSERVE", "EXIT")):  # both rise to the SAVED low pose: the arm realizes its pan too (after a sweep sector, that is a pan back to the centre)
            pose["shoulder_pan"] = (target[-1] if isinstance(target, (list, tuple)) else target).joints["shoulder_pan"]
        return {"measured": measured, "tool": tool, "perception": perception}

    machine.run_pose = run_pose
    def grip(target, label):
        machine.calls.append("grip:" + label)
        pose["gripper"] = script["gripper"] if label == "latch" else 79.6  # what machine.pose() measures from here on
        return {"gripper": pose["gripper"], "measured": {**pose, "shoulder_lift": 41.0, "elbow_flex": 50.0, "wrist_flex": -28.0, "gripper": script["gripper"]}, "tool": {"out_mm": 11.4, "up_mm": 2.3, "pitch_deg": 63.7, "lowest_link_m": -0.099}, "perception": []}

    machine.grip = grip
    machine._frame = lambda result: script["frames"].pop(0)
    if not real_state:
        machine.state = types.MethodType(lambda self, name, **fields: self.transitions.append(name), machine)
    return machine


def machine_script(**changes):
    import numpy as np

    still = np.full((720, 1280), 120, np.uint8); still[300:660, 380:800] = 200; still[300:380, 380:800] = 90
    script = {"boxes": [[613, 196, 853, 438], [508, 174, 729, 416], [470, 174, 691, 416], [505, 187, 789, 505], [411, 192, 732, 532]],
              # boxes: pregrasp, after trim 1, after trim 2 (aligned, still high), after the descent (off again), after trim 3
              "carry_out_mm": [45.0, 80.0, 100.0], "gripper": 68.4, "frames": [still.copy(), still.copy()],
              "final": {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.009, -0.115], "inside_pad_outline": True}, "frame": "f"}}
    script.update(changes)
    return script


class CycleSequencingTest(unittest.TestCase):
    """The state machine on a scripted fake: states advance only on their predicates; failures hold and say why."""

    def setUp(self):
        use_synthetic_runtime(self)  # the saved low scan pose these fakes stand in, found the way a configured run finds it

    def _cycle(self, script):
        return scripted_machine(cycle, script)

    def _tool(self, out, up, pitch=66.0):
        return {"out_mm": out, "up_mm": up, "pitch_deg": pitch, "lowest_link_m": -0.05}

    def _script(self, **changes):
        return machine_script(**changes)

    def test_a_clean_run_walks_every_state_in_order_and_ends_placed(self):
        machine = self._cycle(self._script())
        summary = machine.run_cycle()
        self.assertEqual({"object_secured": True, "placed": True, "clearance_target_met": False, "footprint_clearance_mm": 14.5, "base_returned": True}, {k: v for k, v in summary.items() if k != "base_advance_mm"})
        self.assertAlmostEqual(94.0, summary["base_advance_mm"], delta=3.0)
        for earlier, later in zip(["SEARCH", "PREGRASP", "ALIGN", "APPROACH", "STRADDLING", "GRASP_CLOSE", "OBJECT_SECURED", "CARRY", "RELEASED", "PLACED"], machine.transitions[1:] + ["-"]):
            self.assertIn(earlier, machine.transitions)
        order = [machine.transitions.index(name) for name in ("SEARCH", "PREGRASP", "APPROACH", "STRADDLING", "GRASP_CLOSE", "OBJECT_SECURED", "RELEASED", "PLACED")]
        self.assertEqual(sorted(order), order)
        self.assertLess(machine.calls.index("grip:latch"), machine.calls.index("VERIFICATION lift"))

    def test_an_empty_close_never_becomes_object_secured_and_recovery_is_bounded_to_two_advances(self):
        machine = self._cycle(self._script(gripper=66.9))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertNotIn("OBJECT_SECURED", machine.transitions)
        self.assertEqual("grip:open", machine.calls[-1])
        self.assertNotIn("VERIFICATION lift", machine.calls)
        self.assertEqual(3, machine.calls.count("grip:latch"))
        self.assertEqual(2, machine.transitions.count("GRASP_RETRY_ADVANCE"))

    def test_a_cube_that_does_not_rise_with_the_jaws_is_not_secured_and_nothing_is_carried(self):
        import numpy as np

        first = np.full((720, 1280), 120, np.uint8); first[300:660, 380:800] = 200; first[300:380, 380:800] = 90
        second = np.full((720, 1280), 120, np.uint8); second[430:600, 500:700] = 200; second[430:470, 500:700] = 90
        machine = self._cycle(self._script(frames=[first, second]))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertNotIn("CARRY", machine.transitions)
        self.assertNotIn("grip:open", machine.calls)  # it holds; releasing is a decision, not a reflex

    def test_a_cube_whose_footprint_could_cross_the_edge_is_not_placed(self):
        near_edge = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.009, -0.1475 + 0.015], "inside_pad_outline": True}, "frame": "f"}
        machine = self._cycle(self._script(final=near_edge))  # centre 15 mm inside: a rotated cube would overhang
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()

    def test_a_placement_the_perception_does_not_confirm_is_a_failure(self):
        outside = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.009, -0.150], "inside_pad_outline": False}, "frame": "f"}
        machine = self._cycle(self._script(final=outside))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertIn("RELEASED", machine.transitions)

    def test_a_cube_outside_the_pad_is_placed_after_a_measured_base_advance_with_the_arm_held_and_the_base_returns(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}
        machine = self._cycle(self._script(final=deep))
        summary = machine.run_cycle()
        self.assertTrue(summary["placed"] and summary["object_secured"] and summary["clearance_target_met"] and summary["base_returned"])
        self.assertAlmostEqual(58.0 - cycle.PLACED_MIN_EDGE_MARGIN_MM, summary["footprint_clearance_mm"], delta=0.1)
        order = [machine.transitions.index(name) for name in ("OBJECT_SECURED", "PLACEMENT_PLAN", "HOLD_OBSERVE", "BASE_ADVANCE", "RELEASE_ADMISSION", "RELEASED", "PLACED", "BASE_RETURN")]
        self.assertEqual(sorted(order), order)
        pulses = [index for index, call in enumerate(machine.calls) if call.startswith("base:")]
        forward = [machine.calls[index] for index in pulses if machine.calls[index].startswith("base:+")]
        self.assertEqual(2, len(forward))  # a calibration pulse for half the need, then the rest at the MEASURED rate
        self.assertEqual("base:+0.05x0.47", forward[0])
        for index in pulses:
            self.assertEqual("observe", machine.calls[index + 1])  # stop, then re-observe the pad: no arm motion in between
        self.assertLess(abs(machine.base["advance_mm"]), cycle.BASE_RETURN_TOLERANCE_MM)
        self.assertNotIn("CARRY forward", machine.calls)  # released at about the grasp radius: FK travel barely matters
        self.assertLess(machine.calls.index("grip:open"), next(i for i in pulses if machine.calls[i].startswith("base:-")))  # the base only reverses after the release

    def test_a_cube_at_the_edge_is_placed_arm_only_without_any_base_motion(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.045], "inside_pad_outline": True}, "frame": "f"}
        machine = self._cycle(self._script(final=deep, cube_start_pad_m=[0.0145, -0.1475 + 0.012]))
        summary = machine.run_cycle()
        self.assertTrue(summary["placed"] and summary["clearance_target_met"])
        self.assertFalse([call for call in machine.calls if call.startswith("base:")])
        self.assertIn("CARRY", machine.transitions)
        self.assertNotIn("HOLD_OBSERVE", machine.transitions)
        self.assertLess(machine.transitions.index("RELEASE_ADMISSION"), machine.transitions.index("RELEASED"))

    def test_a_base_that_is_not_confirmed_stationary_keeps_the_cube_held(self):
        machine = self._cycle(self._script(base_stops=False))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertEqual(1, len([call for call in machine.calls if call.startswith("base:")]))
        self.assertNotIn("grip:open", machine.calls)

    def test_without_a_pad_frame_while_holding_the_base_never_moves(self):
        machine = self._cycle(self._script(pad_frame_while_holding=False))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertFalse([call for call in machine.calls if call.startswith("base:")])
        self.assertNotIn("grip:open", machine.calls)

    def test_an_arm_that_moved_during_the_translation_is_a_failure(self):
        machine = self._cycle(self._script(arm_drift_deg=3.0))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertNotIn("grip:open", machine.calls)

    def test_a_release_the_visual_evidence_does_not_admit_never_happens(self):
        machine = self._cycle(self._script(base_rate_mm_s=12.0))  # the base barely moves: the bounded pulses run out short of the pad
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertEqual(cycle.BASE_MAX_PULSES, len([call for call in machine.calls if call.startswith("base:")]))
        self.assertNotIn("grip:open", machine.calls)
        self.assertNotIn("RELEASED", machine.transitions)

    def test_an_operator_abort_stops_the_base_path_after_the_running_pulse(self):
        machine = self._cycle(self._script(abort_during_pulse=True))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertEqual(1, len([call for call in machine.calls if call.startswith("base:")]))
        self.assertNotIn("grip:open", machine.calls)

    def test_a_cube_that_could_not_be_lowered_is_never_dropped_from_the_carry_height(self):
        machine = self._cycle(self._script(place_lower_refused=True))
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertIn("PLACE_LOWER_REFUSED", machine.transitions)
        self.assertNotIn("grip:open", machine.calls)

    def test_a_pad_frame_that_reports_more_than_the_pulse_can_have_moved_is_not_a_measurement(self):
        machine = self._cycle(self._script(registration_gain=4.0))  # 0.47 s cannot be 170 mm
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertEqual(1, len([call for call in machine.calls if call.startswith("base:")]))
        self.assertNotIn("grip:open", machine.calls)

    def test_a_base_that_translates_along_its_own_axis_a_few_degrees_off_the_camera_axis_is_followed_not_refused(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}
        machine = self._cycle(self._script(final=deep, base_lateral_per_mm=0.2))  # about 11 deg
        self.assertTrue(machine.run_cycle()["base_returned"])
        sideways = self._cycle(self._script(base_lateral_per_mm=1.2))  # 50 deg: not a forward translation
        with self.assertRaises(cycle.CycleFailed):
            sideways.run_cycle()
        self.assertNotIn("grip:open", sideways.calls)

    def test_a_loaded_reach_that_falls_short_falls_back_to_the_base_instead_of_releasing_short(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}
        machine = self._cycle(self._script(final=deep, cube_start_pad_m=[0.0145, -0.1475 + 0.012], carry_stalls=True))
        self.assertTrue(machine.run_cycle()["placed"])
        self.assertLess(machine.transitions.index("ARM_ONLY_INSUFFICIENT"), machine.transitions.index("HOLD_OBSERVE"))
        self.assertTrue([call for call in machine.calls if call.startswith("base:+")])

    def test_a_placement_no_bounded_advance_can_reach_is_refused_before_anything_is_picked_up(self):
        machine = self._cycle(self._script(cube_start_pad_m=[0.30, -0.1825]))  # beside the pad
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertNotIn("grip:latch", machine.calls)
        self.assertFalse([call for call in machine.calls if call and call.startswith(("PREGRASP", "base:"))])


class _FailureRecorder(cycle._NoEvents):
    """Keeps the reason codes and values of Cycle.failed and the predicates the controller stated; every other hook is silent."""

    def __init__(self):
        self.failures, self.predicates = [], []

    def cycle_failed(self, code, message, values, evidence):
        self.failures.append((code, dict(values)))

    def predicate(self, name, value, **fields):
        self.predicates.append((name, bool(value)))


class SearchSweepTest(unittest.TestCase):
    """SEARCH SWEEP on the scripted machine (the module docstring, SEARCH SWEEP): a low-pose frame that verifies no cube with a
    contact pixel sweeps the wrist LEFT, then RIGHT, through run_pose, and the first sector frame with a verified cube in a
    valid PAD_FRAME is the SEARCH frame from then on, its measured pan the one the coarse pregrasp is computed from."""

    LOW_PAN = -9.58  # the machine's start pan (the saved low pose)
    CONTACT = [553.0, 545.0]
    RIGHT_CONTACT = [540.0, 545.0]  # from the RIGHT pan the reference column maps 0.09 deg beyond the return band (the pan offset of the reference grasp); this column maps inside it

    def setUp(self):
        use_synthetic_runtime(self)
        words = mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("search_sweep",))  # the sweep runs only under its word (policy_search.WORDS)
        words.start()
        self.addCleanup(words.stop)

    def test_with_no_word_an_empty_low_pose_frame_is_refused_with_nothing_commanded(self):
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ()):
            machine = self._machine(cube_seen_at={"LEFT": True})
            with self.assertRaises(cycle.CycleFailed) as failure:
                machine.run_cycle()
        self.assertEqual(["observe"], machine.calls)
        self.assertEqual(["SEARCH"], self._states(machine))
        self.assertEqual(self.LOW_PAN, machine.pose()["shoulder_pan"])
        [(code, _values)] = machine.events.failures
        self.assertEqual("search_no_cube", code)
        self.assertNotIn("sweep", str(failure.exception))

    def _machine(self, **changes):
        machine = scripted_machine(cycle, machine_script(**changes), real_state=True)
        machine.events = _FailureRecorder()
        return machine

    @staticmethod
    def _states(machine) -> list:
        """The states of the run, less the PLACEMENT_ENVELOPE record a run that names a word states first."""
        return [entry["state"] for entry in machine.transitions if entry["state"] != "PLACEMENT_ENVELOPE"]

    @staticmethod
    def _sweeps(machine) -> list:
        return [entry for entry in machine.transitions if entry["state"] == "SEARCH_SWEEP"]

    @staticmethod
    def _pregrasp(machine) -> dict:
        return next(entry for entry in machine.transitions if entry["state"] == "PREGRASP")

    def test_the_amplitude_is_the_widest_the_planners_own_entry_bound_admits_after_an_accepted_arrival(self):
        """plan_scan_segments refuses an entry more than MAX_ENTRY_PAN_SWEEP_DEG outside the pans of a call, and the executor
        accepts an arrival up to ARRIVAL_TOLERANCE_DEG past its target: a sector at the full bound whose arrival overshoots
        cannot plan its way back through the centre. The amplitude leaves exactly that tolerance, and no sector lies outside
        plus or minus MAX_ENTRY_PAN_SWEEP_DEG of the centre pan."""
        from scan_poses import MAX_ENTRY_PAN_SWEEP_DEG, plan_scan_segments
        from scan_world import ARRIVAL_TOLERANCE_DEG

        self.assertEqual(25.0, cycle.SEARCH_SWEEP_DEG)
        self.assertEqual(MAX_ENTRY_PAN_SWEEP_DEG - ARRIVAL_TOLERANCE_DEG, cycle.SEARCH_SWEEP_DEG)
        low = dict(SYNTHETIC_LOW_POSE)
        overshoot = ARRIVAL_TOLERANCE_DEG - 0.5  # an arrival the executor accepts
        for amplitude, plans in ((cycle.SEARCH_SWEEP_DEG, True), (MAX_ENTRY_PAN_SWEEP_DEG, False)):
            centre, left, centre_2, right, _ = cycle.cli.sweep_poses(low, amplitude)
            self.assertLessEqual(max(abs(left.joints["shoulder_pan"] - low["shoulder_pan"]), abs(right.joints["shoulder_pan"] - low["shoulder_pan"])), MAX_ENTRY_PAN_SWEEP_DEG)
            overshot = {**low, "shoulder_pan": left.joints["shoulder_pan"] - overshoot}
            if plans:
                plan_scan_segments(overshot, [centre_2, right], allow_low_entry=True, elbow_band=False)
            else:
                with self.assertRaises(ValueError):
                    plan_scan_segments(overshot, [centre_2, right], allow_low_entry=True, elbow_band=False)

    def test_a_cube_in_the_centre_frame_never_sweeps(self):
        machine = self._machine()  # the scene every scenario has: the cube in the first low-pose frame
        self.assertTrue(machine.run_cycle()["placed"])
        self.assertFalse([call for call in machine.calls if call.startswith("SEARCH_SWEEP")])
        self.assertEqual([], self._sweeps(machine))
        self.assertEqual(cycle.coarse_pregrasp_from_low_pose(self.CONTACT, self.LOW_PAN)["pan_deg"], self._pregrasp(machine)["coarse"]["pan_deg"])

    def test_a_cube_in_the_left_sector_is_found_there_and_the_pregrasp_is_computed_from_the_left_pan(self):
        machine = self._machine(cube_seen_at={"LEFT": True})
        self.assertTrue(machine.run_cycle()["placed"])
        self.assertEqual(["observe", "SEARCH_SWEEP LEFT", "PREGRASP (non-contact)"], machine.calls[:3])
        [sweep] = self._sweeps(machine)
        self.assertEqual(("LEFT", "cube", self.LOW_PAN - 25.0), (sweep["sector"], sweep["outcome"], sweep["pan_deg"]))
        expected = cycle.coarse_pregrasp_from_low_pose(self.CONTACT, self.LOW_PAN - 25.0)["pan_deg"]
        self.assertEqual(expected, self._pregrasp(machine)["coarse"]["pan_deg"])
        self.assertNotEqual(cycle.coarse_pregrasp_from_low_pose(self.CONTACT, self.LOW_PAN)["pan_deg"], expected)  # the centre pan would plan elsewhere
        self.assertEqual([("cube_seen_with_a_contact_point", False), ("sweep_sector_cube_seen_with_a_contact_point", True)], machine.events.predicates[:2])

    def test_a_cube_in_the_right_sector_is_found_after_the_left_sector_was_looked_at(self):
        machine = self._machine(cube_seen_at={"RIGHT": True}, sector_contact_px=self.RIGHT_CONTACT)
        self.assertTrue(machine.run_cycle()["placed"])
        self.assertEqual(["observe", "SEARCH_SWEEP LEFT", "SEARCH_SWEEP RIGHT", "PREGRASP (non-contact)"], machine.calls[:4])
        self.assertEqual([("LEFT", "no_cube", self.LOW_PAN - 25.0), ("RIGHT", "cube", self.LOW_PAN + 25.0)], [(s["sector"], s["outcome"], s["pan_deg"]) for s in self._sweeps(machine)])
        self.assertEqual(cycle.coarse_pregrasp_from_low_pose(self.RIGHT_CONTACT, self.LOW_PAN + 25.0)["pan_deg"], self._pregrasp(machine)["coarse"]["pan_deg"])
        self.assertEqual([("cube_seen_with_a_contact_point", False), ("sweep_sector_cube_seen_with_a_contact_point", False), ("sweep_sector_cube_seen_with_a_contact_point", True)],
                         machine.events.predicates[:3])

    def test_no_cube_in_any_sector_returns_the_arm_to_the_centre_and_fails_after_the_sweep(self):
        machine = self._machine(cube_seen_at={})
        with self.assertRaises(cycle.CycleFailed) as failure:
            machine.run_cycle()
        self.assertEqual(["observe", "SEARCH_SWEEP LEFT", "SEARCH_SWEEP RIGHT", "SEARCH_SWEEP return to centre"], machine.calls)
        self.assertEqual(self.LOW_PAN, machine.pose()["shoulder_pan"])
        self.assertEqual(["SEARCH", "SEARCH_SWEEP", "SEARCH_SWEEP"], self._states(machine))
        self.assertEqual([("LEFT", "no_cube"), ("RIGHT", "no_cube")], [(s["sector"], s["outcome"]) for s in self._sweeps(machine)])
        [(code, values)] = machine.events.failures
        self.assertEqual("search_no_cube_after_sweep", code)
        self.assertEqual(("no_cube", "no_cube", 25.0), (values["left_outcome"], values["right_outcome"], values["sweep_deg"]))
        self.assertIn("sweep", str(failure.exception))
        self.assertNotIn("PREGRASP", self._states(machine))

    def test_an_abort_during_a_sector_commands_nothing_further(self):
        machine = self._machine(cube_seen_at={"RIGHT": True}, abort_in_sector="LEFT")
        with self.assertRaises(RuntimeError) as stop:
            machine.run_cycle()
        self.assertNotIsInstance(stop.exception, cycle.CycleFailed)  # the executor's abort, exit 4: not a decision of this controller
        self.assertEqual(["observe", "SEARCH_SWEEP LEFT"], machine.calls)
        self.assertEqual(["SEARCH"], self._states(machine))
        self.assertEqual([], machine.events.failures)

    def test_a_sector_cube_the_pad_frame_does_not_place_is_not_taken(self):
        machine = self._machine(cube_seen_at={"LEFT": "pad_frame_invalid", "RIGHT": True}, sector_contact_px=self.RIGHT_CONTACT)
        self.assertTrue(machine.run_cycle()["placed"])
        self.assertEqual([("LEFT", "pad_frame_invalid"), ("RIGHT", "cube")], [(s["sector"], s["outcome"]) for s in self._sweeps(machine)])
        alone = self._machine(cube_seen_at={"LEFT": "pad_frame_invalid"})
        with self.assertRaises(cycle.CycleFailed):
            alone.run_cycle()
        [(code, values)] = alone.events.failures
        self.assertEqual(("search_no_cube_after_sweep", "pad_frame_invalid", "no_cube"), (code, values["left_outcome"], values["right_outcome"]))
        self.assertEqual("SEARCH_SWEEP return to centre", alone.calls[-1])


    def test_the_return_bound_is_the_planners_entry_bound_less_the_arrival_tolerance(self):
        """EXIT (and the HOLD_OBSERVE rise of a base advance) plans ONE pose at the saved low pose's pan from wherever the arm
        is, and plan_scan_segments refuses that entry from a pan more than MAX_ENTRY_PAN_SWEEP_DEG away; the executor accepts
        an arrival up to ARRIVAL_TOLERANCE_DEG past its target. A commanded pan within the difference can always plan its
        return, whatever accepted arrival it lands at; the reference column of a sector at the sweep amplitude sits on it."""
        from scan_poses import MAX_ENTRY_PAN_SWEEP_DEG, plan_scan_segments
        from scan_world import ARRIVAL_TOLERANCE_DEG

        self.assertEqual(25.0, cycle.RETURN_PAN_BOUND_DEG)
        self.assertEqual(MAX_ENTRY_PAN_SWEEP_DEG - ARRIVAL_TOLERANCE_DEG, cycle.RETURN_PAN_BOUND_DEG)
        low = dict(SYNTHETIC_LOW_POSE)
        home = cycle.cli.pregrasp_return_poses(low)[0]
        overshoot = ARRIVAL_TOLERANCE_DEG - 0.5  # an arrival the executor accepts
        plan_scan_segments({**low, "shoulder_pan": low["shoulder_pan"] - cycle.RETURN_PAN_BOUND_DEG - overshoot}, [home], allow_low_entry=True, elbow_band=False)
        with self.assertRaises(ValueError):
            plan_scan_segments({**low, "shoulder_pan": low["shoulder_pan"] - MAX_ENTRY_PAN_SWEEP_DEG - overshoot}, [home], allow_low_entry=True, elbow_band=False)

    def test_a_sector_cube_beyond_the_return_bound_is_refused_before_anything_is_picked_up(self):
        """A cube the LEFT sector sees at column 300 maps to a coarse pan 46.4 deg from the saved low pose's pan: the pregrasp
        would plan, the cube would be picked, carried and released, and only then would the EXIT be refused. The refusal
        comes first, with nothing commanded after the sector frame: no PREGRASP, the arm at the sector it stopped in."""
        machine = self._machine(cube_seen_at={"LEFT": True}, sector_contact_px=[300.0, 545.0])
        with self.assertRaises(cycle.CycleFailed) as failure:
            machine.run_cycle()
        self.assertEqual(["observe", "SEARCH_SWEEP LEFT"], machine.calls)
        self.assertEqual(self.LOW_PAN - 25.0, machine.pose()["shoulder_pan"])
        self.assertEqual(["SEARCH", "SEARCH_SWEEP"], self._states(machine))
        [(code, values)] = machine.events.failures
        self.assertEqual("search_cube_beyond_return_bound", code)
        coarse = cycle.coarse_pregrasp_from_low_pose([300.0, 545.0], self.LOW_PAN - 25.0)["pan_deg"]
        self.assertEqual((coarse, self.LOW_PAN, 25.0), (values["coarse_pan_deg"], values["home_pan_deg"], values["return_pan_bound_deg"]))
        self.assertAlmostEqual(abs(coarse - self.LOW_PAN), values["pan_from_home_deg"], places=2)
        self.assertGreater(values["pan_from_home_deg"], 25.0)
        self.assertIn("nothing was picked up", str(failure.exception))
        self.assertNotIn("grip:latch", machine.calls)

    def test_a_centre_frame_cube_beyond_the_return_bound_is_refused_the_same_way(self):
        """The bound is the EXIT's, so it holds for the frame taken at the centre too: column 250 maps 25.6 deg from home."""
        machine = self._machine(contact_px=[250.0, 545.0])
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertEqual(["observe"], machine.calls)
        [(code, values)] = machine.events.failures
        self.assertEqual("search_cube_beyond_return_bound", code)
        self.assertEqual(cycle.coarse_pregrasp_from_low_pose([250.0, 545.0], self.LOW_PAN)["pan_deg"], values["coarse_pan_deg"])
        inside = self._machine(contact_px=[300.0, 545.0])  # column 300 maps 21.4 deg from home: inside the band, the cycle runs as before
        self.assertTrue(inside.run_cycle()["placed"])

    def test_a_sector_cube_the_pregrasp_cannot_enter_from_that_sector_is_refused_before_motion(self):
        """From the RIGHT sector a cube at column 100 maps 13.3 deg from home (inside the return band) but 38.3 deg from the
        sector's own pan, more than the planner's entry bound: the pregrasp call would be refused. Stated first, by name."""
        machine = self._machine(cube_seen_at={"RIGHT": True}, sector_contact_px=[100.0, 545.0])
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertEqual(["observe", "SEARCH_SWEEP LEFT", "SEARCH_SWEEP RIGHT"], machine.calls)
        [(code, values)] = machine.events.failures
        self.assertEqual("search_cube_beyond_pregrasp_entry_bound", code)
        coarse = cycle.coarse_pregrasp_from_low_pose([100.0, 545.0], self.LOW_PAN + 25.0)["pan_deg"]
        self.assertEqual((coarse, self.LOW_PAN + 25.0, 30.0), (values["coarse_pan_deg"], values["pan_now_deg"], values["entry_pan_bound_deg"]))
        self.assertLess(abs(coarse - self.LOW_PAN), 25.0)

    def test_an_align_trim_that_would_leave_the_return_band_is_refused_before_the_trim(self):
        """The band holds through ALIGN: the pregrasp lands 22 deg from home (column 516 of the RIGHT frame) and the first trim
        the scripted boxes ask for is +5.34 deg, which would command a pan 27.3 deg from home. The trim is refused before it
        moves: the arm stays at the pregrasp, nothing is gripped, and the pan it holds can still plan the EXIT."""
        machine = self._machine(cube_seen_at={"RIGHT": True}, sector_contact_px=[516.0, 545.0], pan_realized=True)
        with self.assertRaises(cycle.CycleFailed):
            machine.run_cycle()
        self.assertEqual("PREGRASP (non-contact)", machine.calls[-1])
        self.assertNotIn("ALIGN pan trim", machine.calls)
        self.assertNotIn("grip:latch", machine.calls)
        [(code, values)] = machine.events.failures
        self.assertEqual("align_trim_beyond_return_bound", code)
        coarse = cycle.coarse_pregrasp_from_low_pose([516.0, 545.0], self.LOW_PAN + 25.0)["pan_deg"]
        self.assertLess(abs(coarse - self.LOW_PAN), 25.0)
        self.assertAlmostEqual(coarse + 5.34, values["commanded_pan_deg"], places=2)
        self.assertEqual((5.34, self.LOW_PAN, 25.0), (values["trim_deg"], values["home_pan_deg"], values["return_pan_bound_deg"]))
        self.assertGreater(abs(values["commanded_pan_deg"] - self.LOW_PAN), 25.0)
        inside = self._machine(cube_seen_at={"RIGHT": True}, sector_contact_px=[400.0, 545.0], pan_realized=True)  # 12 deg from home: the three trims stay inside
        self.assertTrue(inside.run_cycle()["placed"])
        self.assertEqual(3, inside.calls.count("ALIGN pan trim"))

    def test_a_sector_beyond_the_scan_policy_bound_is_refused_before_the_first_sector_move(self):
        """The sectors are built at the sweep amplitude either side of the FRESH pan. From a start pan of -40 the LEFT sector
        would lie at -65, beyond the scan policy bound of plus or minus 60, and from +40 the RIGHT one at +65: the planner
        would refuse that sector when planning its call, after the other sector had already been run. Both sector pans
        are stated against the bound before the first sector move: nothing is commanded, the arm stands where it started."""
        from scan_poses import SCAN_PAN_ABS_MAX_DEG

        for start_pan, sector in ((-40.0, "LEFT"), (40.0, "RIGHT")):
            with self.subTest(start_pan=start_pan):
                machine = self._machine(cube_seen_at={}, start_pan=start_pan)
                with self.assertRaises(cycle.CycleFailed):
                    machine.run_cycle()
                self.assertEqual(["observe"], machine.calls)
                self.assertEqual(start_pan, machine.pose()["shoulder_pan"])
                self.assertEqual(["SEARCH"], self._states(machine))
                [(code, values)] = machine.events.failures
                self.assertEqual("search_sweep_sector_beyond_pan_policy", code)
                self.assertEqual((start_pan - 25.0, start_pan + 25.0, SCAN_PAN_ABS_MAX_DEG), (values["left_pan_deg"], values["right_pan_deg"], values["scan_pan_abs_max_deg"]))
                self.assertEqual(sector, values["sector"])
        inside = self._machine(cube_seen_at={}, start_pan=-34.0)  # LEFT at -59: inside the bound, the sweep runs
        with self.assertRaises(cycle.CycleFailed):
            inside.run_cycle()
        self.assertEqual("search_no_cube_after_sweep", inside.events.failures[0][0])

class BasePostureTest(unittest.TestCase):
    HOME = {"shoulder_pan": -8.6, "shoulder_lift": -79.25, "elbow_flex": 34.5, "wrist_flex": 67.2}

    def test_the_base_only_moves_with_the_arm_at_the_saved_low_scan_pose(self):
        self.assertIsNone(cycle.base_posture_refusal({**self.HOME, "elbow_flex": 38.3, "gripper": 68.5}, self.HOME))  # the elbow's recorded standing error
        self.assertIn("elbow_flex", cycle.base_posture_refusal({**self.HOME, "elbow_flex": 42.0}, self.HOME))
        self.assertIn("shoulder_pan", cycle.base_posture_refusal({**self.HOME, "shoulder_pan": 60.0}, self.HOME))  # high enough, but swung out
        self.assertIsNotNone(cycle.base_posture_refusal({"shoulder_pan": 0.0, "shoulder_lift": 0.0, "elbow_flex": 0.0, "wrist_flex": 0.0}, self.HOME))  # 173 mm out, 327 mm up
        self.assertIsNotNone(cycle.base_posture_refusal({**self.HOME, "wrist_flex": float("nan")}, self.HOME))


if __name__ == "__main__":
    unittest.main()


class WhyNoCubeTest(unittest.TestCase):
    """The sentence an ALIGN failure carries. It used to name two causes the record refutes.

    Run console-20260922T194137Z ended `align_cube_not_detected` and told the operator "coarse geometry too far off,
    or occluded". The recognizer's own note for that same frame, quoted verbatim below from the retained sidecar,
    said the cube WAS proposed at 0.664 and was rejected on box size alone. Neither named cause was true.
    """

    RECORDED_NOTE = (
        "proposal 'a yellow cube' 0.664 [428, 2, 787, 386]: rejected, box side 0.30 of image width: "
        "too large for the cube on this image path"
    )

    def test_it_quotes_the_recognizer_rather_than_naming_a_cause(self):
        view = {"perception": [{"cube": {"detected": False, "notes": [self.RECORDED_NOTE]}}]}
        why = cycle.Cycle._why_no_cube(view)
        self.assertEqual(why, self.RECORDED_NOTE)
        # The two withdrawn causes must not be reintroduced anywhere in the sentence.
        self.assertNotIn("coarse geometry", why)
        self.assertNotIn("occluded", why)

    def test_it_carries_every_proposal_the_recognizer_evaluated(self):
        view = {"perception": [{"cube": {"detected": False, "notes": ["first rejected, reason one", "second rejected, reason two"]}}]}
        why = cycle.Cycle._why_no_cube(view)
        self.assertIn("reason one", why)
        self.assertIn("reason two", why)

    def test_it_says_the_recognizer_was_silent_rather_than_inventing_a_reason(self):
        self.assertEqual(cycle.Cycle._why_no_cube({"perception": [{"cube": {"detected": False, "notes": []}}]}),
                         "the recognizer recorded no reason")
        self.assertEqual(cycle.Cycle._why_no_cube({"perception": [{"cube": {"detected": False}}]}),
                         "the recognizer recorded no reason")

    def test_it_says_so_when_there_is_no_perception_record_at_all(self):
        self.assertEqual(cycle.Cycle._why_no_cube({}), "no perception record was produced for this view")
        self.assertEqual(cycle.Cycle._why_no_cube({"perception": []}), "no perception record was produced for this view")

    def test_the_raise_site_no_longer_carries_the_withdrawn_explanation(self):
        """Read at the source, because the sentence is what the operator sees on stdout."""
        source = cycle_and_stage_source()
        self.assertNotIn("coarse geometry too far off, or occluded", source)
        self.assertIn("recognizer_reason", source)
