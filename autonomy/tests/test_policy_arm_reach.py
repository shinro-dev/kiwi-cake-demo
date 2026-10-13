#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_policy_arm_reach.py: the placement policy arm_reach, its planner (autonomy/manipulation/policy_arm_reach.py).
# Pure: nothing here moves. What is pinned: an admitted plan is one the controller's own release admission admits at the
# planned travel, never past the loaded reach cap of the reach model, with no base motion; a refused block is one the gate
# refuses at every travel the cap allows (checked on the whole grid with the controller's own function); the grasp out a
# SEARCH plan counts on is the coarse one plus the worst recorded under-read, and a measured grasp is taken as it is; the
# block of 2026-09-30 07:14Z is refused, with its numbers, on both reach-axis signs; the move a refusal names is the
# smallest along the axis that makes the policy admit; a run's measured out cap is never exceeded; the policy is one word of
# the placement stage, unqualified, and this build executes it (its executor is pinned by test_reach_policies_execution.py).
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_policy_arm_reach.py
import math
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import placement_policy as pp  # noqa: E402
import policy_arm_reach as arm  # noqa: E402
import reach_model as rm  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402

LIVE = (-0.035768012099579305, -0.2129612198129184)
LIVE_AXES = ((0.0616, 0.9981), (-0.0616, 0.9981))  # back-solved from the refusal's numbers; the sign is not in the record (why-refused.md)
REQUIRED = cycle.PLACE_REQUIRED_CENTRE_MM


def situation(anchor, axis, grasp_out=0.0, measured=False) -> pp.Situation:
    norm = math.hypot(*axis)
    return pp.Situation(anchor, (axis[0] / norm, axis[1] / norm), grasp_out, measured, cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM)


def gate(anchor, axis, travel) -> float:
    return cycle.release_margin_worst_mm(anchor, axis, travel)


class TheLiveBlock(unittest.TestCase):
    def test_it_is_refused_with_no_base_motion_on_both_axis_signs(self):
        for axis in LIVE_AXES:
            with self.subTest(axis=axis):
                plan = arm.plan(cycle, situation(LIVE, axis))
                self.assertFalse(plan.admitted)
                self.assertIsNone(plan.mode)
                self.assertEqual("arm_reach", plan.values["constraint"])
                # the coarse cube_out -5.2 reaches SEARCH clamped at 0; plus the worst recorded under-read, 22; cap 126
                self.assertEqual(22.0, plan.values["grasp_out_planned_mm"])
                self.assertEqual(104.0, plan.values["fk_travel_cap_mm"])
                best = max(gate(LIVE, plan_axis(axis), t) for t in np.arange(0.0, 104.5, 1.0))
                self.assertAlmostEqual(best, plan.values["best_with_the_arm_alone_mm"], delta=1e-9)
                self.assertAlmostEqual(REQUIRED - best, plan.values["deficit_mm"], delta=0.051)
                self.assertGreater(plan.values["deficit_mm"], 44.0)
                self.assertTrue(pp.placement_envelope.TEXT_SHAPE.fullmatch(plan.sentence), plan.sentence)

    def test_the_move_it_names_is_the_smallest_that_admits_it(self):
        for axis in LIVE_AXES:
            with self.subTest(axis=axis):
                refused = situation(LIVE, axis)
                move = arm.plan(cycle, refused).values["move_mm"]
                self.assertIsNotNone(move)
                self.assertTrue(arm.plan(cycle, refused.moved(move)).admitted)
                self.assertFalse(arm.plan(cycle, refused.moved(move - 1)).admitted)
                self.assertGreater(move, 40)

    def test_the_gate_the_lead_kept_refuses_every_arm_alone_release_at_any_legal_reach(self):
        """verify r6 policy MF1 (THE BLOCK OF 2026-09-30 07:14Z in the module docstring): not the policy's own cap but the kept
        release admission at the chain's whole reach is the limit, and what would change the answer is stated and pinned."""
        def admits(axis, ratio, travel_cap_mm) -> bool:
            with mock.patch.object(cycle, "FK_TRAVEL_OVERSTATEMENT", ratio):
                return any(gate(LIVE, axis, float(t)) >= REQUIRED for t in np.arange(0.0, math.floor(travel_cap_mm) + 0.5, 1.0))

        reach = rm.gate_reach_mm(cycle, cycle.CARRY_HEIGHT_MM)
        self.assertEqual((152.5, 152.5), (reach, rm.gate_reach_mm(cycle, cycle.PLACE_HEIGHT_MM)))
        legal = reach + 5.2  # FK travel to R_gate from the grasp the coarse estimate put at -5.2 mm
        for raw in LIVE_AXES:
            with self.subTest(axis=raw):
                axis = plan_axis(raw)
                need = next(float(t) for t in np.arange(0.0, 400.0, 0.1) if gate(LIVE, axis, float(t)) >= REQUIRED)
                self.assertAlmostEqual(175.5, need, delta=0.05, msg="the kept gate's FK travel need")
                best = max(gate(LIVE, axis, float(t)) for t in np.arange(0.0, legal + 1e-9, 0.1))
                self.assertAlmostEqual(26.9, best, delta=0.15, msg="the best worst-case margin at the chain's whole reach")
                self.assertLess(best, REQUIRED)
                self.assertFalse(admits(axis, 1.0, 104.0), "SEARCH's 104 mm travel cap is short even if FK travel were real travel")
                self.assertEqual((True, False), (admits(axis, 1.19, 131.2), admits(axis, 1.20, 131.2)), "once held at -5.2: a ratio at most 1.19")
                self.assertEqual((True, False), (admits(axis, 1.43, legal), admits(axis, 1.44, legal)), "the chain's whole reach: a ratio at most 1.43")
        text = arm.__doc__
        for stated in ("175.5", "152.5", "157.7", "26.9", "104", "131.2", "1.19", "1.43", "a smaller SEARCH charge"):
            self.assertIn(stated, text, f"the module docstring states {stated}")


def plan_axis(axis):
    norm = math.hypot(*axis)
    return (axis[0] / norm, axis[1] / norm)


class TheMeasuredReach(unittest.TestCase):
    """After a carry of this run stopped short, the out its loaded arm reached is the cap a plan may count on."""

    def test_a_plan_never_counts_on_more_than_the_measured_out(self):
        anchor, axis = (0.0145, -0.158), (0.0, 1.0)  # 10.5 mm outside the near edge
        free = arm.plan(cycle, pp.Situation(anchor, axis, 21.1, True, 0.0))
        self.assertTrue(free.admitted, free.sentence)
        self.assertLessEqual(free.arm_out_mm, rm.measured_out_cap_mm(cycle))
        short = arm.plan(cycle, pp.Situation(anchor, axis, 21.1, True, 0.0, out_cap_mm=100.0))
        self.assertFalse(short.admitted, "the gate needs 87.1 mm of FK travel, and 100 - 21.1 leaves 78.9")
        self.assertEqual((100.0, 78.9), (short.values["reach_cap_mm"], round(short.values["fk_travel_cap_mm"], 1)))


class TheInvariants(unittest.TestCase):
    CASES = None

    @classmethod
    def setUpClass(cls):
        chance = random.Random(20260930)
        cases = [(LIVE, axis, 0.0, False) for axis in LIVE_AXES]
        for _ in range(160):
            anchor = (chance.uniform(-0.26, 0.26), chance.uniform(-0.26, 0.05))
            deg = chance.uniform(60.0, 120.0)
            cases.append((anchor, (math.cos(math.radians(deg)), math.sin(math.radians(deg))), chance.uniform(0.0, 40.0), chance.random() < 0.5))
        cls.CASES = cases

    def test_an_admitted_plan_is_the_gates_with_no_base_motion_inside_the_cap(self):
        for anchor, axis, grasp, measured in self.CASES:
            plan = arm.plan(cycle, situation(anchor, axis, grasp, measured))
            if not plan.admitted:
                continue
            with self.subTest(anchor=anchor, axis=axis, grasp=grasp, measured=measured):
                self.assertEqual(("ARM_ONLY", 0.0), (plan.mode, plan.base_advance_mm))
                self.assertGreaterEqual(gate(anchor, plan_axis(axis), plan.fk_travel_mm), REQUIRED)
                self.assertAlmostEqual(gate(anchor, plan_axis(axis), plan.fk_travel_mm), plan.worst_margin_mm, delta=1e-9)
                self.assertGreaterEqual(plan.fk_travel_mm, 0.0)
                self.assertLessEqual(plan.arm_out_mm, rm.measured_out_cap_mm(cycle) + 1e-9)
                self.assertAlmostEqual(rm.planning_grasp_out_mm(grasp, measured) + plan.fk_travel_mm, plan.arm_out_mm, delta=1e-9)
                self.assertGreaterEqual(plan.capped_loss_margin_mm, plan.worst_margin_mm - 1e-9, "the capped-loss model never lies below the gate's")

    def test_a_refused_block_is_refused_by_the_gate_at_every_travel_the_cap_allows(self):
        refused = 0
        for anchor, axis, grasp, measured in self.CASES:
            plan = arm.plan(cycle, situation(anchor, axis, grasp, measured))
            if plan.admitted:
                continue
            refused += 1
            cap = rm.measured_out_cap_mm(cycle) - rm.planning_grasp_out_mm(grasp, measured)
            with self.subTest(anchor=anchor, axis=axis, grasp=grasp, measured=measured):
                self.assertTrue(all(gate(anchor, plan_axis(axis), float(t)) < REQUIRED for t in np.arange(0.0, math.floor(cap) + 0.5, 1.0)))
        self.assertGreater(refused, 20, "the sweep must reach refusals")

    def test_the_sweep_reaches_admissions(self):
        self.assertGreater(sum(arm.plan(cycle, situation(*case)).admitted for case in self.CASES), 20)

    def test_a_measured_grasp_admits_everything_the_coarse_one_does(self):
        for anchor, axis, grasp, _measured in self.CASES:
            with self.subTest(anchor=anchor, axis=axis, grasp=grasp):
                if arm.plan(cycle, situation(anchor, axis, grasp, False)).admitted:
                    self.assertTrue(arm.plan(cycle, situation(anchor, axis, grasp, True)).admitted)

    def test_the_planned_travel_aims_past_the_smallest_admitted_one_when_the_gate_still_admits(self):
        anchor, axis = (0.0145, -0.1475 + 0.004), (0.0, 1.0)
        plan = arm.plan(cycle, situation(anchor, axis, 0.0, True))
        self.assertTrue(plan.admitted)
        smallest = next(float(t) for t in np.arange(0.0, 200.0, 1.0) if gate(anchor, axis, float(t)) >= REQUIRED)
        self.assertEqual(smallest + arm.AIM_BEYOND_MM, plan.fk_travel_mm)


class ThePolicy(unittest.TestCase):
    def test_it_is_one_unqualified_word_of_the_placement_stage_and_this_build_executes_it(self):
        self.assertEqual("arm_reach", arm.WORD)
        self.assertEqual(("placement", ("arm_reach",), frozenset()), (arm.POLICY.stage, arm.POLICY.words, arm.POLICY.qualified))
        self.assertTrue(arm.EXECUTES)
        self.assertTrue(callable(arm.execute))
        self.assertEqual({"placement_plan", "reach_carry", "release_margin", "release_admitted", "placed"}, {"placement_plan", "reach_carry", "release_margin", "release_admitted", "placed"} & arm.POLICY.predicates,
                         "the predicates its executor states carry its rule")
        self.assertEqual(rm.ADMISSION, arm.PLANNING_TRAVEL_MODEL, "a plan the release admission could not take is never carried")

    def test_it_never_imports_the_controller(self):
        source = (ROOT / "autonomy" / "manipulation" / "policy_arm_reach.py").read_text()
        self.assertNotIn("import run_single_cube_cycle", source)
        self.assertNotIn("from run_single_cube_cycle", source)


if __name__ == "__main__":
    unittest.main()
