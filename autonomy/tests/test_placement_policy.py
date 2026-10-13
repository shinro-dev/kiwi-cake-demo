#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_placement_policy.py: the interface every placement policy plans through
# (autonomy/manipulation/placement_policy.py). Pure: nothing here moves. What is pinned: the release admission's worst-case
# margin in its affine form is the controller's own release_margin_worst_mm, to 1e-9 mm, on grids of anchors, axes,
# advances and travels (so a policy that plans on a grid decides exactly as the gate will); the side allowance comes off
# the edges the reach axis runs along and no other; a situation that is not a reading is refused; THE LOADED REACH CAP is
# the proven loaded reach for a block the proven policy places, and for any other whenever a plan of the run's additive
# policies under it fits the advance budget (the operator's ruling of 2026-10-02, Q8: the operator's block of 2026-09-30
# included), the reach model's measured-out cap otherwise, and every plan's budget and its release buffer read it; at the
# binding a plan under the cap is read at the record's largest grasp under-read too (capped_plan_robust); a plan's
# record is rounded, flat and in the record's text shape; the interface module never imports the controller.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_placement_policy.py
import itertools
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import dataclasses  # noqa: E402

import placement_envelope as pe  # noqa: E402
import placement_policy as pp  # noqa: E402
import policy_advance_then_reach as atr  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402

LIVE = (-0.035768012099579305, -0.2129612198129184)  # the block of run console-20260930T071429Z-m0c6fb2a, PAD_FRAME metres


def unit(deg: float) -> tuple:
    return (math.cos(math.radians(deg)), math.sin(math.radians(deg)))


def moved(anchor, direction, mm: float) -> tuple:
    return (anchor[0] + direction[0] * mm / 1000.0, anchor[1] + direction[1] * mm / 1000.0)


class TheAffineMarginIsTheGatesOwn(unittest.TestCase):
    ANCHORS = (LIVE, (0.0, -0.1825), (0.1604, -0.211), (-0.0936, -0.1698), (0.0, 0.0), (0.19, -0.14), (-0.30, -0.25), (0.02, 0.16))
    AXES = tuple(unit(deg) for deg in (90.0, 86.5, 93.5, 75.0, 110.0, 45.0, 135.0, 0.0, 180.0, -90.0))

    def test_without_an_allowance_it_is_release_margin_worst_mm_at_every_advance_and_travel(self):
        advances, travels = np.arange(0.0, 130.0, 7.0), np.arange(-12.0, 160.0, 5.5)
        for anchor, axis in itertools.product(self.ANCHORS, self.AXES):
            with self.subTest(anchor=anchor, axis=axis):
                grid_a, grid_t = np.meshgrid(advances, travels, indexing="ij")
                low, high = grid_a + grid_t / cycle.FK_TRAVEL_OVERSTATEMENT, grid_a + grid_t
                ours = pp.worst_margins_mm(cycle, anchor, axis, low, high)
                gates = np.array([[cycle.release_margin_worst_mm(moved(anchor, axis, a), axis, t) for t in travels] for a in advances])
                self.assertLess(float(np.max(np.abs(ours - gates))), 1e-9)

    def test_a_scalar_pair_gives_a_scalar(self):
        value = pp.worst_margins_mm(cycle, LIVE, unit(86.5), 40.0, 64.0)
        self.assertAlmostEqual(cycle.release_margin_worst_mm(LIVE, unit(86.5), 64.0), float(value), delta=1e-9)
        self.assertEqual((), np.shape(value))

    def test_the_side_allowance_comes_off_the_edges_the_axis_runs_along_only(self):
        for anchor, axis in itertools.product(self.ANCHORS, self.AXES):
            with self.subTest(anchor=anchor, axis=axis):
                sides = pe.side_edges(cycle, axis)
                for s in (0.0, 30.0, 90.0):
                    per_edge = [m + s * r for m, r in zip(cycle.edge_margins_mm(anchor), pp.edge_rates(cycle, axis))]
                    wanted = min(value - (5.0 if side else 0.0) for value, side in zip(per_edge, sides)) - cycle.VISUAL_ANCHOR_UNCERTAINTY_MM
                    self.assertAlmostEqual(wanted, float(pp.worst_margins_mm(cycle, anchor, axis, s, s, side_allowance_mm=5.0)), delta=1e-9)

    def test_the_rate_of_an_edge_is_the_solvers(self):
        for axis in self.AXES:
            self.assertEqual(tuple(-pe._dot(axis, e) for e in cycle.PAD_EDGE_NORMALS), tuple(pp.edge_rates(cycle, axis)))


class TheSituation(unittest.TestCase):
    def test_a_reading_that_is_not_finite_is_refused(self):
        for bad in ((float("nan"), 0.0), (0.0, float("inf"))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pp.Situation(bad, (0.0, 1.0), 0.0, False)
        with self.assertRaises(ValueError):
            pp.Situation(LIVE, (0.0, 1.0), float("nan"), False)
        with self.assertRaises(ValueError):
            pp.Situation(LIVE, (0.0, 0.0), 0.0, False)

    def test_it_keeps_floats_and_the_moved_anchor_moves_along_the_axis(self):
        situation = pp.Situation([-0.0357, -0.213], [0.0616, 0.9981], 3, True, 6)
        self.assertEqual((-0.0357, -0.213), situation.anchor_pad_m)
        self.assertEqual(3.0, situation.grasp_out_mm)
        self.assertEqual(situation.anchor_pad_m, situation.moved(0.0).anchor_pad_m)
        along = situation.moved(10.0).anchor_pad_m
        self.assertAlmostEqual(10.0, math.hypot(along[0] - situation.anchor_pad_m[0], along[1] - situation.anchor_pad_m[1]) * 1000.0 / math.hypot(*situation.direction), places=9)


class TheRunsOwnLimits(unittest.TestCase):
    """A run that has MEASURED a limit plans with it: out_cap_mm is the out the loaded arm reached when a carry of this run
    stopped short, advance_budget_mm what is left of the proven advance budget after this run's measured advance."""

    def test_both_are_absent_by_default_and_the_budget_is_the_models_own(self):
        situation = pp.Situation(LIVE, (0.0, 1.0), 10.0, True)
        self.assertEqual((None, None), (situation.out_cap_mm, situation.advance_budget_mm))
        model = pp.reach_model.measured_out_cap_mm(cycle)
        self.assertEqual((10.0, model, model - 10.0), pp.reach_budget(cycle, situation))
        self.assertEqual(cycle.BASE_MAX_TOTAL_ADVANCE_MM, pp.advance_budget_mm(cycle, situation))

    def test_a_measured_out_cap_lowers_the_cap_and_never_raises_it(self):
        model = pp.reach_model.measured_out_cap_mm(cycle)
        self.assertEqual((10.0, 98.5, 88.5), pp.reach_budget(cycle, pp.Situation(LIVE, (0.0, 1.0), 10.0, True, 0.0, out_cap_mm=98.5)))
        self.assertEqual(model, pp.reach_budget(cycle, pp.Situation(LIVE, (0.0, 1.0), 10.0, True, 0.0, out_cap_mm=model + 40.0))[1])

    def test_a_remaining_budget_lowers_the_budget_and_never_raises_it(self):
        self.assertEqual(40.0, pp.advance_budget_mm(cycle, pp.Situation(LIVE, (0.0, 1.0), 0.0, True, advance_budget_mm=40.0)))
        self.assertEqual(cycle.BASE_MAX_TOTAL_ADVANCE_MM, pp.advance_budget_mm(cycle, pp.Situation(LIVE, (0.0, 1.0), 0.0, True, advance_budget_mm=500.0)))
        self.assertEqual(0.0, pp.advance_budget_mm(cycle, pp.Situation(LIVE, (0.0, 1.0), 0.0, True, advance_budget_mm=0.0)))

    def test_they_are_readings_and_travel_with_the_moved_situation(self):
        for bad in ({"out_cap_mm": float("nan")}, {"advance_budget_mm": float("inf")}, {"advance_budget_mm": -1.0}, {"out_cap_mm": "far"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pp.Situation(LIVE, (0.0, 1.0), 0.0, True, **bad)
        situation = pp.Situation(LIVE, (0.0, 1.0), 0.0, True, 6.0, out_cap_mm=100, advance_budget_mm=70)
        self.assertEqual((100.0, 70.0), (situation.out_cap_mm, situation.advance_budget_mm))
        self.assertEqual((100.0, 70.0, 6.0), (situation.moved(12.0).out_cap_mm, situation.moved(12.0).advance_budget_mm, situation.moved(12.0).side_allowance_mm))


class TheLoadedReachCap(unittest.TestCase):
    """The operator's ruling of 2026-10-01 ("cap at proven reach"): one function answers the largest measured out a plan may
    carry the loaded block to, and every plan's budget reads it. For a block the proven policy places no loaded command
    passes the proven loaded reach, and the plan counts only on the out such a command measures under the record: the
    worst recorded loaded shortfall is kept between the two, as the reach model keeps it for every other block (verify r8
    policy MF1: the loaded carry steps of 2026-09-19/20 commanded inside +95 mm out measured 14.0 to 19.9 mm short)."""

    def test_it_is_the_proven_loaded_reach_less_the_recorded_shortfall_for_a_block_the_proven_policy_places_and_the_reach_models_cap_otherwise(self):
        situation = pp.Situation(LIVE, (0.0, 1.0), 10.0, True)
        self.assertEqual(95.0, cycle.ARM_LOADED_REACH_OUT_MM)
        self.assertEqual(24.0, pp.reach_model.LOADED_SHORTFALL_MAX_MM)
        self.assertEqual(cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM, pp.loaded_reach_cap_mm(cycle, situation, True))
        for refused in (False, None):
            self.assertEqual(pp.reach_model.measured_out_cap_mm(cycle), pp.loaded_reach_cap_mm(cycle, situation, refused))
        self.assertLess(pp.loaded_reach_cap_mm(cycle, situation, True), pp.reach_model.measured_out_cap_mm(cycle))

    def test_a_block_the_proven_policy_refuses_is_capped_whenever_a_plan_of_the_run_under_the_cap_fits_the_budget(self):
        """The operator's ruling of 2026-10-02 (operator question Q8, verify r10 replay MUST-FIX 1): whenever a plan whose
        loaded carry stays inside the proven loaded reach (commands inside 95 mm out, counting on 71 mm measured, the 15 mm
        lagging-arm buffer) fits the 120 mm driving budget, it is used for every block, the blocks the proven policy refuses
        included; the longer carry stays only for a block no such plan of the run serves. The operator's block of 2026-09-30
        07:14Z, at SEARCH's planning grasp: advance_then_reach places it under the cap within the budget, so its cap is the
        proven loaded reach's and its release keeps the buffer; with a budget one millimetre short of that plan's advance, or
        in a run whose words name no policy that places it under the cap, it keeps the longer carry."""
        capped, longer = cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM, pp.reach_model.measured_out_cap_mm(cycle)
        live = pp.Situation(LIVE, (0.0, 1.0), 0.0, False, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM, proven_admits=False)
        with self.assertRaises(cycle.CycleFailed):  # the proven policy refuses it, as the installed controller did
            cycle.placement_plan(LIVE, (0.0, 1.0), 22.0, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("arm_reach", "advance_then_reach")):
            plan = atr.plan(cycle, dataclasses.replace(live, proven_admits=True))
            self.assertTrue(plan.admitted and 0.0 < plan.base_advance_mm <= cycle.BASE_MAX_TOTAL_ADVANCE_MM, plan.record())
            self.assertLessEqual(plan.arm_out_mm, capped + 1e-9)
            self.assertTrue(pp.capped_plan_fits(cycle, live))
            for refused in (False, None):
                self.assertEqual(capped, pp.loaded_reach_cap_mm(cycle, live, refused))
                self.assertEqual(cycle.ARM_LOADED_REACH_OUT_MM, pp.loaded_command_cap_mm(cycle, live, refused))
            self.assertEqual(cycle.BASE_TARGET_BUFFER_MM, pp.release_lag_mm(cycle, live))
            self.assertEqual(plan.record(), atr.plan(cycle, live).record(), "planned exactly as a block the proven policy places is planned")
            short = dataclasses.replace(live, advance_budget_mm=plan.base_advance_mm - 1.0)
            self.assertFalse(pp.capped_plan_fits(cycle, short))
            self.assertEqual(longer, pp.loaded_reach_cap_mm(cycle, short, False))
            self.assertEqual(0.0, pp.release_lag_mm(cycle, short))
            self.assertEqual(capped, pp.loaded_reach_cap_mm(cycle, short, True), "a block the proven policy places keeps the cap whatever the budget")
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("arm_reach",)):
            self.assertFalse(pp.capped_plan_fits(cycle, live), "the arm alone under the cap does not place it, and the run names no other policy")
            self.assertEqual(longer, pp.loaded_reach_cap_mm(cycle, live, False))
        self.assertEqual((), tuple(cycle.PLACEMENT_ENVELOPE))
        self.assertFalse(pp.capped_plan_fits(cycle, live), "a run that names no additive policy has no plan under the cap")
        self.assertEqual(longer, pp.loaded_reach_cap_mm(cycle, live, False))
        self.assertFalse(pp.capped_plan_fits(cycle, None))

    def test_at_the_binding_a_plan_under_the_cap_is_read_at_the_records_largest_grasp_under_read_too(self):
        """The lead's decision of 2026-10-03 (block_selection THE BINDING TIERS): capped_plan_robust is capped_plan_fits with
        the grasp the record allows past SEARCH's charge, the coarse grasp plus reach_model.GRASP_UNDERREAD_HELD_MAX_MM (30.5
        mm, where SEARCH charges 22), as if held there; a measured grasp is read as it is. On the straight axis, blocks the
        proven policy refuses, each planned under the cap at SEARCH: 114 mm of base at (14.5, -230) mm seen at coarse 0
        still fits there, 117 mm at coarse 5 does not, nor 116 to 118 mm farther out (verify r12 safety V1's A among them);
        the operator's block fits at both."""
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("arm_reach", "advance_then_reach")):
            for anchor, coarse, robust in (((0.0145, -0.230), 0.0, True), ((0.0145, -0.230), 5.0, False), ((0.0145, -0.2325), 0.0, False), ((0.0145, -0.234), 0.0, False),
                                           (LIVE, 0.0, True)):
                situation = pp.Situation(anchor, (0.0, 1.0), coarse, False, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM, proven_admits=False)
                with self.subTest(anchor=anchor, coarse=coarse):
                    self.assertTrue(pp.capped_plan_fits(cycle, situation), "planned under the cap at SEARCH")
                    self.assertIs(robust, pp.capped_plan_robust(cycle, situation))
                    held = dataclasses.replace(situation, grasp_out_mm=coarse + pp.reach_model.GRASP_UNDERREAD_HELD_MAX_MM, measured=True)
                    self.assertIs(robust, pp.capped_plan_fits(cycle, held), "as once held at that grasp")
                    self.assertIs(robust, pp.capped_plan_robust(cycle, held), "a measured grasp is read as it is")
        self.assertFalse(pp.capped_plan_robust(cycle, None))

    def test_its_commands_stop_at_the_proven_loaded_reach_and_at_the_reach_models_command_cap_otherwise(self):
        situation = pp.Situation(LIVE, (0.0, 1.0), 10.0, True)
        self.assertEqual(cycle.ARM_LOADED_REACH_OUT_MM, pp.loaded_command_cap_mm(cycle, situation, True))
        for refused in (False, None):
            self.assertEqual(pp.reach_model.command_cap_mm(cycle), pp.loaded_command_cap_mm(cycle, situation, refused))
        with mock.patch.object(pp, "loaded_reach_cap_mm", lambda ctl, situation, proven_admits: 61.5):
            self.assertEqual(61.5 + pp.reach_model.LOADED_SHORTFALL_MAX_MM, pp.loaded_command_cap_mm(cycle, situation, True), "it reads the cap function")
        kept = pp.loaded_command_cap_mm(cycle, situation, True) - pp.loaded_reach_cap_mm(cycle, situation, True)
        self.assertGreaterEqual(kept, 19.9, "every loaded carry step on record inside the proven loaded reach measured less short than this")

    def test_every_plans_budget_reads_it_and_a_measured_out_cap_still_lowers_it(self):
        capped = pp.Situation(LIVE, (0.0, 1.0), 10.0, True, proven_admits=True)
        cap = cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM
        self.assertEqual((10.0, cap, cap - 10.0), pp.reach_budget(cycle, capped))
        self.assertEqual(60.0, pp.reach_budget(cycle, pp.Situation(LIVE, (0.0, 1.0), 10.0, True, out_cap_mm=60.0, proven_admits=True))[1])
        self.assertEqual(pp.reach_model.measured_out_cap_mm(cycle), pp.reach_budget(cycle, pp.Situation(LIVE, (0.0, 1.0), 10.0, True, proven_admits=False))[1])
        with mock.patch.object(pp, "loaded_reach_cap_mm", lambda ctl, situation, proven_admits: 61.5):
            self.assertEqual(61.5, pp.reach_budget(cycle, capped)[1], "the budget reads the cap function, not a copy of it")

    def test_a_plan_for_a_block_the_proven_policy_places_is_admitted_at_every_release_down_to_the_lagging_arms_buffer(self):
        """THE LAGGING ARM AT THE RELEASE (verify r8 policy MF1): the proven base rule aims its advance BASE_TARGET_BUFFER_MM past
        the admission so that a lagging arm (the four base placements of 2026-09-20 released 8.9 to 11.7 mm behind the grasp)
        is still admitted; a plan for a block the proven policy places keeps that buffer on its travel. Its worst margin on the
        grid is the admission's own at the worst release from the planned travel down to the buffer less, to 1e-9 mm, and a
        situation without the cap keeps the admission's margin exactly."""
        self.assertEqual(15.0, cycle.BASE_TARGET_BUFFER_MM)
        travels, displacements = np.arange(0.0, 100.0, 3.5), np.array([0.0, 21.0, 64.0])
        lags = np.arange(0.0, cycle.BASE_TARGET_BUFFER_MM + 0.25, 0.5)
        for anchor, axis in itertools.product(TheAffineMarginIsTheGatesOwn.ANCHORS[:5], TheAffineMarginIsTheGatesOwn.AXES[:6]):
            capped, free = (pp.Situation(anchor, axis, 10.0, True, proven_admits=admits) for admits in (True, False))
            self.assertEqual((cycle.BASE_TARGET_BUFFER_MM, 0.0), (pp.release_lag_mm(cycle, capped), pp.release_lag_mm(cycle, free)))
            ours, plain = pp.worst_on_grid(cycle, capped, travels, displacements), pp.worst_on_grid(cycle, free, travels, displacements)
            for i, d in enumerate(displacements):
                start = moved(anchor, axis, d)
                gates = np.array([cycle.release_margin_worst_mm(start, axis, t) for t in travels])
                lagging = np.array([min(cycle.release_margin_worst_mm(start, axis, t - lag) for lag in lags) for t in travels])
                with self.subTest(anchor=anchor, axis=axis, displacement=d):
                    self.assertLess(float(np.max(np.abs(plain[i] - gates))), 1e-9)
                    self.assertLess(float(np.max(np.abs(ours[i] - lagging))), 1e-9)
        self.assertIsNone(pp.Situation(LIVE, (0.0, 1.0), 0.0, True).proven_admits)
        self.assertEqual(0.0, pp.release_lag_mm(cycle, pp.Situation(LIVE, (0.0, 1.0), 0.0, True)),
                         "a situation that does not say yet, in a run with no plan under the cap, keeps the admission's margin")

    def test_whether_the_proven_policy_places_the_block_is_a_reading_and_travels_with_the_moved_situation(self):
        self.assertIsNone(pp.Situation(LIVE, (0.0, 1.0), 0.0, True).proven_admits)
        for value in (True, False, np.bool_(True)):
            situation = pp.Situation(LIVE, (0.0, 1.0), 0.0, True, proven_admits=value)
            self.assertIs(bool(value), situation.proven_admits)
            self.assertIs(bool(value), situation.moved(12.0).proven_admits)
        for bad in ("yes", 1, 0.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pp.Situation(LIVE, (0.0, 1.0), 0.0, True, proven_admits=bad)


class ThePlanRecord(unittest.TestCase):
    def test_a_record_is_flat_rounded_and_in_the_text_shape(self):
        plan = pp.Plan(policy="arm_reach", admitted=True, mode=pp.MODE_ARM_ONLY, base_advance_mm=0.0, fk_travel_mm=63.123, arm_out_mm=85.1234,
                       worst_margin_mm=38.456, capped_loss_margin_mm=41.2, sentence="arm_reach carries it 63.1 mm", values={"constraint": None})
        record = plan.record()
        self.assertEqual({"policy": "arm_reach", "admitted": True, "mode": "ARM_ONLY", "base_advance_mm": 0.0, "fk_travel_mm": 63.1, "arm_out_mm": 85.1,
                          "worst_margin_mm": 38.5, "capped_loss_margin_mm": 41.2, "sentence": "arm_reach carries it 63.1 mm"}, record)
        refused = pp.Plan(policy="arm_reach", admitted=False, mode=None, base_advance_mm=None, fk_travel_mm=None, arm_out_mm=None, worst_margin_mm=None,
                          capped_loss_margin_mm=None, sentence="short", values={})
        self.assertIsNone(refused.record()["mode"])

    def test_the_shaper_keeps_a_text_in_the_record_shape_and_cuts_at_a_space(self):
        self.assertEqual("a fine sentence", pp.shaped("a fine sentence: with a colon", "a fine sentence"))
        long = pp.shaped("word " * 40)
        self.assertLessEqual(len(long), pe.TEXT_MAX_CHARS)
        self.assertTrue(pe.TEXT_SHAPE.fullmatch(long))


class ThePurity(unittest.TestCase):
    def test_the_interface_never_imports_the_controller(self):
        source = (ROOT / "autonomy" / "manipulation" / "placement_policy.py").read_text()
        self.assertNotIn("import run_single_cube_cycle", source)
        self.assertNotIn("from run_single_cube_cycle", source)


if __name__ == "__main__":
    unittest.main()
