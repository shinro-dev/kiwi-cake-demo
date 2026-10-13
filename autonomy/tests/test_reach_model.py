#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_reach_model.py: the kinematic reach model of the placement policies arm_reach and advance_then_reach
# (autonomy/manipulation/reach_model.py). Pure: the model reads the controller's chain, gate and bounds through the
# controller module handed to it, and nothing here moves. What is pinned: the carry target the controller's own
# proven_orientation_pose would accept reaches 152.5 mm beyond the proven grasp radius at every carry and release height, and
# the first refusal past it is the proven gate's lateral-drift bound, not the elbow guard or a joint limit (the numbers of
# the 2026-09-30 reach analysis: gate-legal to +152 on a 1 mm and a 2 mm grid, first refusal at +154 on the 2 mm grid); the
# model's verdict is proven_orientation_pose's own at every sampled target; the joints it reports are the analysis's; the
# command cap keeps the elbow away from the straight-arm guard; the lead compensation never commands past the cap; both
# travel models bound every recorded loaded carry from below; the planning grasp out covers every recorded grasp.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_reach_model.py
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import reach_model as rm  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402


class TheGateReach(unittest.TestCase):
    """R_gate(dz): the largest carry target, in mm beyond the proven grasp radius, that the controller's proven-orientation
    target passes through its own solver, gate (joint limits less the margin, the lateral drift bound, the corrected elbow
    guard), tilt bound and carry bounds."""

    def test_the_reach_is_152_5_mm_at_the_carry_and_the_release_height(self):
        self.assertEqual(152.5, rm.gate_reach_mm(cycle, cycle.CARRY_HEIGHT_MM))
        self.assertEqual(152.5, rm.gate_reach_mm(cycle, cycle.PLACE_HEIGHT_MM))

    def test_it_agrees_with_the_reach_analysis_on_its_coarser_grids(self):
        # why-refused-kinematics (2 mm grid): last legal +152, first refusal +154; design-proto (1 mm grid): legal to +152.
        for dz in (cycle.CARRY_HEIGHT_MM, cycle.PLACE_HEIGHT_MM):
            with self.subTest(dz=dz):
                self.assertTrue(rm.target_legal(cycle, 152.0, dz)[0])
                self.assertFalse(rm.target_legal(cycle, 154.0, dz)[0])
                self.assertGreaterEqual(rm.gate_reach_mm(cycle, dz), 152.0)
                self.assertLess(rm.gate_reach_mm(cycle, dz), 154.0)

    def test_the_first_refusal_past_the_reach_is_the_lateral_drift_bound(self):
        for dz in (cycle.CARRY_HEIGHT_MM, cycle.PLACE_HEIGHT_MM):
            with self.subTest(dz=dz):
                legal, reason = rm.target_legal(cycle, rm.gate_reach_mm(cycle, dz) + rm.REACH_STEP_MM, dz)
                self.assertFalse(legal)
                self.assertIn("drift", reason)
                self.assertNotIn("singularity", reason)

    def test_every_target_up_to_the_reach_is_legal(self):
        for dz in (cycle.CARRY_HEIGHT_MM, cycle.PLACE_HEIGHT_MM):
            reach = rm.gate_reach_mm(cycle, dz)
            for out in [5.0 * step for step in range(int(reach // 5.0) + 1)] + [reach]:
                with self.subTest(dz=dz, out=out):
                    self.assertTrue(rm.target_legal(cycle, out, dz)[0])

    def test_the_verdict_is_proven_orientation_poses_own(self):
        """proven_orientation_pose called FROM the target's own joints (no travel, a trivial path) accepts exactly the
        targets the model calls legal: the model is that function's endpoint checks, not a second opinion."""
        grasp_pan = cycle.KNOWN_GOOD_GRASP_POSE_DEG["shoulder_pan"]
        for dz in (cycle.CARRY_HEIGHT_MM, cycle.PLACE_HEIGHT_MM):
            for out in (0.0, 40.0, 95.0, 128.0, 150.0, 152.5, 153.0, 156.0, 165.0):
                with self.subTest(dz=dz, out=out):
                    joints = rm.joints_at(cycle, out, dz)
                    measured = {"shoulder_pan": grasp_pan, **joints, "wrist_roll": 0.0}
                    try:
                        cycle.proven_orientation_pose(measured, out / 1000.0, dz / 1000.0)
                        accepted = True
                    except cycle.ScanRefused:
                        accepted = False
                    self.assertEqual(accepted, rm.target_legal(cycle, out, dz)[0])

    def test_the_joints_are_the_analysis_ones(self):
        # why-refused-kinematics.out (+152, dz 8) and design-proto.out section 1 (+151 dz 30, +142 dz 8)
        for out, dz, lift, elbow in ((152.0, 8.0, 77.9, -49.5), (151.0, 30.0, 67.3, -39.2), (142.0, 8.0, 69.9, -33.6)):
            with self.subTest(out=out, dz=dz):
                joints = rm.joints_at(cycle, out, dz)
                self.assertAlmostEqual(lift, joints["shoulder_lift"], delta=0.06)
                self.assertAlmostEqual(elbow, joints["elbow_flex"], delta=0.06)

    def test_the_elbow_guard_never_binds_inside_the_reach(self):
        for dz in (cycle.CARRY_HEIGHT_MM, cycle.PLACE_HEIGHT_MM):
            for out in range(0, int(rm.gate_reach_mm(cycle, dz)) + 1, 10):
                with self.subTest(dz=dz, out=out):
                    elbow = rm.joints_at(cycle, float(out), dz)["elbow_flex"]
                    self.assertGreater(abs(elbow - cycle.ELBOW_STRAIGHT_ARM_DEG), cycle.TRUE_SINGULARITY_MARGIN_DEG)

    def test_the_carry_bound_is_part_of_the_model(self):
        with mock.patch.object(cycle, "CARRY_MAX_FORWARD_M", 0.120):
            self.assertEqual(120.0, rm.gate_reach_mm(cycle, cycle.CARRY_HEIGHT_MM))
            self.assertFalse(rm.target_legal(cycle, 120.5, cycle.CARRY_HEIGHT_MM)[0])
        self.assertEqual(152.5, rm.gate_reach_mm(cycle, cycle.CARRY_HEIGHT_MM), "the cache is keyed on the bound")

    def test_a_height_outside_the_carry_bounds_is_refused(self):
        for dz in (-1.0, 61.0):
            with self.subTest(dz=dz), self.assertRaises(ValueError):
                rm.gate_reach_mm(cycle, dz)
        self.assertFalse(rm.target_legal(cycle, 50.0, 61.0)[0])


class TheCapsAndTheLead(unittest.TestCase):
    def test_the_command_cap_stays_the_margin_inside_the_reach_at_both_heights(self):
        reach = min(rm.gate_reach_mm(cycle, cycle.CARRY_HEIGHT_MM), rm.gate_reach_mm(cycle, cycle.PLACE_HEIGHT_MM))
        self.assertEqual(reach - rm.COMMAND_MARGIN_MM, rm.command_cap_mm(cycle))
        self.assertEqual(150.0, rm.command_cap_mm(cycle))

    def test_the_measured_out_cap_is_the_command_cap_less_the_worst_loaded_shortfall(self):
        self.assertEqual(rm.command_cap_mm(cycle) - rm.LOADED_SHORTFALL_MAX_MM, rm.measured_out_cap_mm(cycle))
        self.assertEqual(126.0, rm.measured_out_cap_mm(cycle))

    def test_the_worst_loaded_shortfall_covers_every_recorded_loaded_carry(self):
        for label, commanded, measured in rm.LOADED_SHORTFALL_RECORDS:
            with self.subTest(label):
                self.assertLessEqual(commanded - measured, rm.LOADED_SHORTFALL_MAX_MM)
        firsts = [commanded - measured for label, commanded, measured in rm.LOADED_SHORTFALL_RECORDS if "first send" in label]
        self.assertEqual(rm.LOADED_SHORTFALL_FIRST_MM, math.ceil(max(firsts)))

    def test_the_first_command_leads_by_the_first_send_shortfall(self):
        self.assertEqual(100.0 + rm.LOADED_SHORTFALL_FIRST_MM, rm.lead_command_out_mm(cycle, 100.0))

    def test_a_later_command_leads_by_the_shortfall_the_last_step_measured_bounded(self):
        self.assertAlmostEqual(123.0, rm.lead_command_out_mm(cycle, 100.0, previous=(120.0, 97.0)))
        self.assertAlmostEqual(100.0 + rm.LOADED_SHORTFALL_MAX_MM, rm.lead_command_out_mm(cycle, 100.0, previous=(120.0, 80.0)))
        self.assertAlmostEqual(100.0, rm.lead_command_out_mm(cycle, 100.0, previous=(100.0, 104.0)), msg="an overshoot is never a negative lead")

    def test_a_cap_the_caller_names_bounds_the_lead_and_never_raises_the_command_cap(self):
        """The loaded reach cap of a block the proven policy places (placement_policy THE LOADED REACH CAP)."""
        self.assertEqual(95.0, rm.lead_command_out_mm(cycle, 90.0, None, 95.0))
        self.assertEqual(95.0, rm.lead_command_out_mm(cycle, 80.0, (95.0, 75.0), 95.0))
        self.assertEqual(80.0 + rm.LOADED_SHORTFALL_FIRST_MM, rm.lead_command_out_mm(cycle, 80.0, None, 120.0))
        self.assertEqual(rm.command_cap_mm(cycle), rm.lead_command_out_mm(cycle, 140.0, None, 400.0))

    def test_no_command_passes_the_command_cap(self):
        for target in (100.0, 126.0, 140.0, 200.0):
            for previous in (None, (150.0, 126.0), (150.0, 149.0)):
                with self.subTest(target=target, previous=previous):
                    self.assertLessEqual(rm.lead_command_out_mm(cycle, target, previous=previous), rm.command_cap_mm(cycle))
                    self.assertTrue(rm.target_legal(cycle, rm.lead_command_out_mm(cycle, target, previous=previous), cycle.CARRY_HEIGHT_MM)[0])


class TheTravelModels(unittest.TestCase):
    def test_the_admission_model_is_the_release_admissions_own_segment(self):
        for travel in (-12.0, 0.0, 30.0, 64.0, 100.0, 175.5):
            with self.subTest(travel=travel):
                self.assertEqual((travel, travel / cycle.FK_TRAVEL_OVERSTATEMENT), rm.travel_ends_mm(cycle, travel, rm.ADMISSION))

    def test_the_capped_loss_model_is_the_admission_below_the_crossover_and_a_bounded_loss_above(self):
        crossover = rm.TRAVEL_LOSS_MAX_MM / (1.0 - 1.0 / cycle.FK_TRAVEL_OVERSTATEMENT)
        self.assertAlmostEqual(64.0, crossover)
        for travel in (-12.0, 0.0, 30.0, 63.0):
            self.assertEqual(rm.travel_ends_mm(cycle, travel, rm.ADMISSION), rm.travel_ends_mm(cycle, travel, rm.CAPPED_LOSS))
        for travel in (65.0, 100.0, 133.6):
            self.assertAlmostEqual(travel - rm.TRAVEL_LOSS_MAX_MM, rm.travel_ends_mm(cycle, travel, rm.CAPPED_LOSS)[1])

    def test_both_models_bound_every_recorded_loaded_carry_from_below(self):
        for label, fk_travel, realized in rm.TRAVEL_RECORDS:
            for model in rm.TRAVEL_MODELS:
                with self.subTest(label=label, model=model):
                    self.assertLessEqual(rm.travel_ends_mm(cycle, fk_travel, model)[1], realized + 1e-9)

    def test_an_unknown_model_is_refused(self):
        with self.assertRaises(ValueError):
            rm.travel_ends_mm(cycle, 50.0, "optimistic")


class ThePlanningGraspOut(unittest.TestCase):
    def test_a_coarse_grasp_out_is_charged_the_worst_recorded_under_read(self):
        self.assertEqual(22.0 + 3.0, rm.planning_grasp_out_mm(3.0, measured=False))
        self.assertEqual(3.0, rm.planning_grasp_out_mm(3.0, measured=True))

    def test_the_under_read_covers_every_grasp_of_the_extraction(self):
        for label, coarse, held in rm.GRASP_UNDERREAD_RECORDS:
            with self.subTest(label):
                self.assertLessEqual(held, rm.planning_grasp_out_mm(max(coarse, 0.0), measured=False))
        self.assertEqual(11, len(rm.GRASP_UNDERREAD_RECORDS))

    def test_one_recorded_grasp_held_past_searchs_charge_and_the_held_maximum_covers_every_recorded_grasp(self):
        """THE PLANNING GRASP OUT: the 2026-09-20 09:55 attempt's third close held 30.5 mm past its coarse estimate, beyond
        SEARCH's 22 mm charge (the lead's decision of 2026-10-03 reads a plan under the loaded reach cap at that under-read
        at the binding); GRASP_UNDERREAD_HELD_MAX_MM is the largest difference of every recorded grasp that held."""
        ((label, coarse, held),) = rm.GRASP_UNDERREAD_RETRY_RECORDS
        self.assertEqual(("cycle-run-20260920T095543Z", 3.8, 34.3), (label, coarse, held))
        self.assertGreater(held, rm.planning_grasp_out_mm(max(coarse, 0.0), measured=False), "past SEARCH's charge")
        differences = [held - max(coarse, 0.0) for _label, coarse, held in rm.GRASP_UNDERREAD_RECORDS + rm.GRASP_UNDERREAD_RETRY_RECORDS]
        self.assertAlmostEqual(rm.GRASP_UNDERREAD_HELD_MAX_MM, max(differences), places=9)
        self.assertEqual(30.5, rm.GRASP_UNDERREAD_HELD_MAX_MM)
        self.assertGreater(rm.GRASP_UNDERREAD_HELD_MAX_MM, rm.GRASP_UNDERREAD_MM)


class ThePurity(unittest.TestCase):
    def test_the_model_never_imports_the_controller(self):
        source = (ROOT / "autonomy" / "manipulation" / "reach_model.py").read_text()
        self.assertNotIn("import run_single_cube_cycle", source)
        self.assertNotIn("from run_single_cube_cycle", source)


if __name__ == "__main__":
    unittest.main()
