#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_policy_advance_then_reach.py: the placement policy advance_then_reach, its planner
# (autonomy/manipulation/policy_advance_then_reach.py). Pure: nothing here moves. What is pinned: the planned advance is
# the smallest on the 1 mm grid, 0 (the arm alone, then an ARM_ONLY plan) or one the frozen advance loop really pulses
# for (more than BASE_ADVANCE_TOLERANCE_MM), never more than the proven 120 mm budget; an admitted plan is one the
# controller's own release admission admits from the moved anchor at the planned travel, with the plan-time side
# allowance off the edges the axis runs along, and one the arm alone still admits when the advance loop stops its
# tolerance short (the reserve); a refused block is refused by that rule at every advance and travel of the grid; the
# block of 2026-09-30 07:14Z, planned with the reach model's cap (a run with no policy that places it under the loaded
# reach cap: the long carry), is admitted with a 53 mm advance on both reach-axis signs, where the proven rule needs
# 124.7 mm; a refusal past the budget names the along-axis need and its deficit, start 3 of 2026-09-26 stays refused on
# its side edge; a run's measured out cap and the budget its measured advance left narrow the plan; a block the proven
# policy places is planned inside the proven loaded reach (THE LOADED REACH CAP), counting only on the out its capped
# commands measure under the record, by the least advance that keeps its carry there, and admitted wherever a lagging arm
# releases it down to the proven rule's buffer (THE LAGGING ARM AT THE RELEASE), and the advance loop's rule of a held run
# reads the same cap, as does the reach after the advance, with what is left of the run's budget (the operator's ruling of
# 2026-10-02, Q8: any block a plan of the run under the cap serves within the budget is planned under it); the policy is
# one word of the placement stage,
# unqualified, and this build executes it (test_reach_policies_execution.py).
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_policy_advance_then_reach.py
import math
import random
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import placement_envelope as pe  # noqa: E402
import placement_policy as pp  # noqa: E402
import policy_advance_then_reach as atr  # noqa: E402
import policy_arm_reach as arm  # noqa: E402
import reach_model as rm  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402

LIVE = (-0.035768012099579305, -0.2129612198129184)
LIVE_AXES = ((0.0616, 0.9981), (-0.0616, 0.9981))
START_3, START_3_AXIS = (0.1604, -0.2110), (0.119, 0.993)
REQUIRED = cycle.PLACE_REQUIRED_CENTRE_MM
TOLERANCE = cycle.BASE_ADVANCE_TOLERANCE_MM
ALLOWANCE = cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM


def unit(axis) -> tuple:
    norm = math.hypot(*axis)
    return (axis[0] / norm, axis[1] / norm)


def situation(anchor, axis, grasp_out=0.0, measured=False) -> pp.Situation:
    return pp.Situation(anchor, unit(axis), grasp_out, measured, ALLOWANCE)


def moved(anchor, axis, mm):
    return (anchor[0] + axis[0] * mm / 1000.0, anchor[1] + axis[1] * mm / 1000.0)


def charged(anchor, axis, advance, travel) -> float:
    """The release admission from the anchor moved by `advance`, less the side allowance on the edges the axis runs along,
    computed with the controller's own edge margins (not the module's affine form)."""
    start = moved(anchor, axis, advance)
    ends = (travel, travel / cycle.FK_TRAVEL_OVERSTATEMENT)
    sides = pe.side_edges(cycle, axis)
    per_edge = [min(cycle.edge_margins_mm(moved(start, axis, end))[index] for end in ends) - (ALLOWANCE if sides[index] else 0.0) for index in range(4)]
    return min(per_edge) - cycle.VISUAL_ANCHOR_UNCERTAINTY_MM


class TheLiveBlock(unittest.TestCase):
    """The operator's block of 2026-09-30 07:14Z planned with the reach model's cap, the long carry: as a run whose words name
    no policy that places it under the loaded reach cap plans it (these tests name none). With advance_then_reach named the
    cap applies (the operator's ruling of 2026-10-02, Q8): 97 mm of base and a carry to 58 mm out, test_placement_selector
    TheLiveBlock."""

    def test_it_is_admitted_with_a_53_mm_advance_where_the_proven_rule_needs_124_7(self):
        for axis in LIVE_AXES:
            with self.subTest(axis=axis):
                plan = atr.plan(cycle, situation(LIVE, axis))
                self.assertTrue(plan.admitted, plan.sentence)
                self.assertEqual(("BASE_ADVANCE", 53.0), (plan.mode, plan.base_advance_mm))
                self.assertGreaterEqual(cycle.release_margin_worst_mm(moved(LIVE, unit(axis), 53.0), unit(axis), plan.fk_travel_mm), REQUIRED)
                self.assertGreaterEqual(charged(LIVE, unit(axis), 53.0, plan.fk_travel_mm), REQUIRED)
                self.assertFalse(atr.plan_at(cycle, situation(LIVE, axis), 52.0), "52 mm does not place it: 53 is the smallest")
                self.assertAlmostEqual(124.66, cycle.axis_advance_needed_unbounded_mm(LIVE, unit(axis)), delta=0.01)
                self.assertIsNone(cycle.base_advance_needed_mm(LIVE, unit(axis)), "the proven rule refuses it inside its 120 mm")

    def test_once_held_the_measured_grasp_needs_no_more_advance(self):
        for axis in LIVE_AXES:
            for grasp in (-5.2, 0.0, 10.0, 16.8):
                with self.subTest(axis=axis, grasp=grasp):
                    held = atr.plan(cycle, situation(LIVE, axis, grasp, measured=True))
                    self.assertTrue(held.admitted)
                    self.assertLessEqual(held.base_advance_mm, 53.0)


class TheInvariants(unittest.TestCase):
    CASES = None

    @classmethod
    def setUpClass(cls):
        chance = random.Random(930)
        cases = [(LIVE, axis, 0.0, False) for axis in LIVE_AXES] + [(START_3, START_3_AXIS, 0.0, False)]
        for _ in range(90):
            anchor = (chance.uniform(-0.25, 0.25), chance.uniform(-0.30, -0.10))
            deg = chance.uniform(65.0, 115.0)
            cases.append((anchor, (math.cos(math.radians(deg)), math.sin(math.radians(deg))), chance.uniform(0.0, 40.0), chance.random() < 0.5))
        cls.CASES = [(anchor, unit(axis), grasp, measured, atr.plan(cycle, situation(anchor, axis, grasp, measured))) for anchor, axis, grasp, measured in cases]

    def test_an_advance_is_zero_or_one_the_loop_pulses_for_and_never_past_the_budget(self):
        for anchor, axis, grasp, measured, plan in self.CASES:
            if plan.admitted:
                with self.subTest(anchor=anchor, axis=axis):
                    self.assertTrue(plan.base_advance_mm == 0.0 or TOLERANCE < plan.base_advance_mm <= cycle.BASE_MAX_TOTAL_ADVANCE_MM, plan.base_advance_mm)
                    self.assertEqual("ARM_ONLY" if plan.base_advance_mm == 0.0 else "BASE_ADVANCE", plan.mode)

    def test_an_admitted_plan_is_the_gates_with_the_allowance_and_keeps_the_reserve(self):
        for anchor, axis, grasp, measured, plan in self.CASES:
            if not plan.admitted:
                continue
            with self.subTest(anchor=anchor, axis=axis, grasp=grasp, measured=measured):
                advance, travel = plan.base_advance_mm, plan.fk_travel_mm
                self.assertGreaterEqual(cycle.release_margin_worst_mm(moved(anchor, axis, advance), axis, travel), REQUIRED)
                self.assertAlmostEqual(cycle.release_margin_worst_mm(moved(anchor, axis, advance), axis, travel), plan.worst_margin_mm, delta=1e-9)
                self.assertLessEqual(plan.arm_out_mm, rm.measured_out_cap_mm(cycle) + 1e-9)
                if advance > 0.0:
                    self.assertGreaterEqual(charged(anchor, axis, advance, travel), REQUIRED)
                    self.assertLessEqual(plan.arm_out_mm, rm.measured_out_cap_mm(cycle) - atr.reserve_mm(cycle) + 1e-9)
                    # THE RESERVE: the loop stops once the remaining need is within its tolerance; from there the arm alone places it
                    self.assertTrue(arm.plan(cycle, pp.Situation(moved(anchor, axis, advance - TOLERANCE), axis, grasp, measured, ALLOWANCE)).admitted)

    def test_the_advance_is_the_smallest_that_places_the_block(self):
        for anchor, axis, grasp, measured, plan in self.CASES:
            if plan.admitted and plan.base_advance_mm > 0.0:
                with self.subTest(anchor=anchor, axis=axis):
                    self.assertFalse(arm.plan(cycle, pp.Situation(anchor, axis, grasp, measured, ALLOWANCE)).admitted, "the arm alone would have placed it")
                    smaller = np.arange(TOLERANCE + 1.0, plan.base_advance_mm - 0.5, 1.0)
                    self.assertFalse(any(atr.plan_at(cycle, pp.Situation(anchor, axis, grasp, measured, ALLOWANCE), float(a)) for a in smaller))

    def test_a_refused_block_is_refused_at_every_advance_and_travel_of_the_grid(self):
        refused = 0
        for anchor, axis, grasp, measured, plan in self.CASES:
            if plan.admitted:
                continue
            refused += 1
            cap = rm.measured_out_cap_mm(cycle) - rm.planning_grasp_out_mm(grasp, measured)
            with self.subTest(anchor=anchor, axis=axis, grasp=grasp, measured=measured):
                self.assertTrue(all(cycle.release_margin_worst_mm(anchor, axis, float(t)) < REQUIRED for t in np.arange(0.0, math.floor(cap) + 0.5, 3.0)))
                for advance in np.arange(TOLERANCE + 1.0, cycle.BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 7.0):
                    self.assertTrue(all(charged(anchor, axis, float(advance), float(t)) < REQUIRED for t in np.arange(0.0, math.floor(cap - atr.reserve_mm(cycle)) + 0.5, 3.0)))
        self.assertGreater(refused, 10, "the sweep must reach refusals")

    def test_the_sweep_reaches_both_kinds_of_admission(self):
        advances = [plan.base_advance_mm for *_rest, plan in self.CASES if plan.admitted]
        self.assertTrue(any(value == 0.0 for value in advances))
        self.assertGreater(sum(value > 0.0 for value in advances), 10)


class TheRunsOwnLimits(unittest.TestCase):
    """What a run measured narrows the plan: the out its loaded arm reached (out_cap_mm) and what is left of the advance
    budget after its measured advance (advance_budget_mm)."""

    def test_a_remaining_budget_below_the_need_refuses_along_the_budget_it_was_given(self):
        for axis in LIVE_AXES:
            with self.subTest(axis=axis):
                given = pp.Situation(LIVE, unit(axis), 0.0, False, ALLOWANCE, advance_budget_mm=40.0)
                plan = atr.plan(cycle, given)
                self.assertFalse(plan.admitted)
                self.assertEqual(("along_budget", 53.0, 40.0), (plan.values["constraint"], plan.values["axis_advance_needed_mm"], plan.values["advance_budget_mm"]))
                self.assertAlmostEqual(13.0, plan.values["deficit_mm"], delta=0.051)
                self.assertTrue(atr.plan(cycle, pp.Situation(LIVE, unit(axis), 0.0, False, ALLOWANCE, advance_budget_mm=53.0)).admitted)

    def test_a_least_advance_rules_out_the_arm_alone_and_keeps_the_smallest_advance_past_it(self):
        """After the arm alone was tried from a stance and its release refused, the next plan must move the base: a least
        advance (min_advance_mm) rules out the plan with no advance, and the smallest advance at or past it is taken."""
        anchor, axis = (0.0145, -0.158), (0.0, 1.0)
        alone = atr.plan(cycle, pp.Situation(anchor, axis, 36.2, True, ALLOWANCE))
        self.assertEqual(("ARM_ONLY", 0.0), (alone.mode, alone.base_advance_mm))
        for least in (atr.first_advance_mm(cycle), 20.0):
            with self.subTest(least=least):
                moved = atr.plan(cycle, pp.Situation(anchor, axis, 36.2, True, ALLOWANCE, min_advance_mm=least))
                self.assertTrue(moved.admitted, moved.sentence)
                self.assertEqual(("BASE_ADVANCE", max(least, atr.first_advance_mm(cycle))), (moved.mode, moved.base_advance_mm))
                self.assertFalse(atr.plan_at(cycle, pp.Situation(anchor, axis, 36.2, True, ALLOWANCE, min_advance_mm=least), 0.0))
        self.assertIsNone(pp.Situation(anchor, axis, 0.0, True).min_advance_mm)
        with self.assertRaises(ValueError):
            pp.Situation(anchor, axis, 0.0, True, min_advance_mm=-1.0)

    def test_a_short_loaded_reach_asks_for_more_advance_never_less(self):
        for axis in LIVE_AXES:
            with self.subTest(axis=axis):
                free = atr.plan(cycle, situation(LIVE, axis, 21.1, measured=True))
                short = atr.plan(cycle, pp.Situation(LIVE, unit(axis), 21.1, True, ALLOWANCE, out_cap_mm=100.0))
                self.assertTrue(free.admitted and short.admitted)
                self.assertGreater(short.base_advance_mm, free.base_advance_mm)
                self.assertLessEqual(short.arm_out_mm, 100.0 - atr.reserve_mm(cycle) + 1e-9)
                self.assertGreaterEqual(cycle.release_margin_worst_mm(moved(LIVE, unit(axis), short.base_advance_mm), unit(axis), short.fk_travel_mm), REQUIRED)


class TheLoadedReachCap(unittest.TestCase):
    """The operator's ruling of 2026-10-01 ("cap at proven reach", placement_policy THE LOADED REACH CAP): a block the proven
    policy places is planned with every carry inside the proven loaded reach, by the least advance that keeps it there, and
    every re-plan of a held run (the advance loop's rule, the reach after the advance) reads the same cap."""

    ARM_STAGE = ((0.0145, -0.158), (0.0, 1.0))

    def test_a_block_the_proven_policy_places_is_planned_by_the_least_advance_whose_carry_stays_inside_the_cap(self):
        anchor, axis = self.ARM_STAGE
        free = atr.plan(cycle, pp.Situation(anchor, axis, 10.0, False, ALLOWANCE, proven_admits=False))
        capped = atr.plan(cycle, pp.Situation(anchor, axis, 10.0, False, ALLOWANCE, proven_admits=True))
        self.assertEqual(("ARM_ONLY", 0.0), (free.mode, free.base_advance_mm), "without the cap the arm alone places it")
        self.assertTrue(capped.admitted, capped.sentence)
        self.assertEqual("BASE_ADVANCE", capped.mode)
        self.assertLessEqual(capped.arm_out_mm, cycle.ARM_LOADED_REACH_OUT_MM + 1e-9)
        self.assertFalse(atr.plan_at(cycle, pp.Situation(anchor, axis, 10.0, False, ALLOWANCE, proven_admits=True), capped.base_advance_mm - 1.0),
                         "no smaller advance keeps the carry inside the cap")
        self.assertEqual(cycle.ARM_LOADED_REACH_OUT_MM - rm.LOADED_SHORTFALL_MAX_MM, capped.values["reach_cap_mm"], "the out capped commands measure under the record")
        self.assertLessEqual(capped.arm_out_mm, cycle.ARM_LOADED_REACH_OUT_MM - rm.LOADED_SHORTFALL_MAX_MM + 1e-9)

    def test_a_capped_plan_is_admitted_wherever_a_lagging_arm_releases_it_down_to_the_proven_rules_buffer(self):
        """THE LAGGING ARM AT THE RELEASE (placement_policy; verify r8 policy MF1): a plan for a block the proven policy places,
        at SEARCH or once held, is admitted by the controller's release admission at every travel from its own down to
        BASE_TARGET_BUFFER_MM less, after its advance and after an advance the loop stopped its tolerance short (the arm alone
        then makes it up); the same block without the cap is planned with the admission's own margin."""
        lags, checked = np.arange(0.0, cycle.BASE_TARGET_BUFFER_MM + 0.25, 0.5), 0
        for anchor in ((0.0145, -0.158), (0.0, -0.165), (0.0, -0.20), (-0.06, -0.19), (0.12, -0.17)):
            for grasp, measured in ((10.0, False), (14.0, True), (29.0, True)):
                capped = atr.plan(cycle, pp.Situation(anchor, (0.0, 1.0), grasp, measured, ALLOWANCE, proven_admits=True))
                if not capped.admitted:
                    continue
                start, checked = moved(anchor, (0.0, 1.0), capped.base_advance_mm), checked + 1
                with self.subTest(anchor=anchor, grasp=grasp):
                    worst = min(cycle.release_margin_worst_mm(start, (0.0, 1.0), capped.fk_travel_mm - lag) for lag in lags)
                    self.assertGreaterEqual(worst, REQUIRED - 1e-9)
                    if capped.base_advance_mm:
                        short = moved(start, (0.0, 1.0), -TOLERANCE)
                        replan = arm.plan(cycle, pp.Situation(short, (0.0, 1.0), grasp + (0.0 if measured else rm.GRASP_UNDERREAD_MM), True, 0.0, proven_admits=True))
                        self.assertTrue(replan.admitted, replan.sentence)
                        self.assertGreaterEqual(min(cycle.release_margin_worst_mm(short, (0.0, 1.0), replan.fk_travel_mm - lag) for lag in lags), REQUIRED - 1e-9)
        self.assertGreaterEqual(checked, 12, "the capped plans checked")
        free = atr.plan(cycle, pp.Situation((0.0, -0.165), (0.0, 1.0), 14.0, True, ALLOWANCE, proven_admits=False))
        self.assertGreaterEqual(cycle.release_margin_worst_mm(moved((0.0, -0.165), (0.0, 1.0), free.base_advance_mm), (0.0, 1.0), free.fk_travel_mm), REQUIRED - 1e-9)
        self.assertLess(free.base_advance_mm, atr.plan(cycle, pp.Situation((0.0, -0.165), (0.0, 1.0), 14.0, True, ALLOWANCE, proven_admits=True)).base_advance_mm,
                        "without the cap and the buffer the advance is smaller")

    def test_the_advance_loops_rule_of_a_held_run_reads_the_runs_cap(self):
        import reach_carry
        anchor, axis = self.ARM_STAGE
        for admits in (True, False):
            held = reach_carry.Held(view={}, grasp_out_mm=21.0, grasp_pan_deg=0.0, anchor=anchor, direction=axis, axis=axis, advanced_mm=0.0,
                                    rate=cycle.BASE_RATE_PRIOR_MM_S, out_cap_mm=None, seen=None, min_advance_mm=atr.first_advance_mm(cycle), proven_admits=admits)
            need = atr.remaining_need_rule(None, cycle, held)(anchor, axis)  # nothing advanced before: the rule never raises, so no Cycle
            expected = atr.plan(cycle, pp.Situation(anchor, axis, 21.0, True, 0.0, None, cycle.BASE_MAX_TOTAL_ADVANCE_MM, atr.first_advance_mm(cycle), admits))
            with self.subTest(proven_admits=admits):
                self.assertEqual(expected.base_advance_mm, need)
        capped = atr.remaining_need_rule(None, cycle, held.but(proven_admits=True))(anchor, axis)
        self.assertGreater(capped, atr.remaining_need_rule(None, cycle, held.but(proven_admits=False))(anchor, axis), "inside the cap the loop drives farther")

    def test_the_reach_after_the_advance_reads_the_cap_with_what_is_left_of_the_runs_budget(self):
        """The operator's ruling of 2026-10-02 (Q8): THE REACH (REACH_REPLAN) plans the arm's travel from the measured anchor
        with what is left of the run's budget, so the loaded reach cap it reads is the run's. The operator's block after an
        advance that moved it 40 mm toward the pad but spent 100 mm of the budget (a base that slipped): a plan under the cap
        would need 43 mm more, past the 20 mm left, so its reach keeps the longer carry; read with the whole budget it would be
        held to the cap and the arm alone refused there."""
        import dataclasses
        from unittest import mock
        import reach_carry
        axis = unit(LIVE_AXES[0])
        held = reach_carry.Held(view={}, grasp_out_mm=0.0, grasp_pan_deg=0.0, anchor=moved(LIVE, axis, 40.0), direction=axis, axis=axis, advanced_mm=100.0,
                                rate=cycle.BASE_RATE_PRIOR_MM_S, out_cap_mm=None, seen=None, min_advance_mm=atr.first_advance_mm(cycle), proven_admits=False)
        capped, longer = cycle.ARM_LOADED_REACH_OUT_MM - rm.LOADED_SHORTFALL_MAX_MM, rm.measured_out_cap_mm(cycle)
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", (arm.WORD, atr.WORD)):
            replanned = atr.replan_situation(cycle, held)
            self.assertEqual((20.0, None, 0.0, None, False), (replanned.advance_budget_mm, replanned.min_advance_mm, replanned.side_allowance_mm, replanned.out_cap_mm,
                                                                 replanned.proven_admits))
            self.assertEqual(held.anchor, replanned.anchor_pad_m)
            self.assertEqual(43.0, atr.plan(cycle, dataclasses.replace(replanned, advance_budget_mm=None, proven_admits=True)).base_advance_mm)
            reach = arm.plan(cycle, replanned)
            self.assertEqual((True, longer), (reach.admitted, reach.values["reach_cap_mm"]))
            whole = arm.plan(cycle, dataclasses.replace(replanned, advance_budget_mm=None))
            self.assertEqual((False, capped), (whole.admitted, whole.values["reach_cap_mm"]))
            self.assertIsNone(reach_carry.carry_cap_mm(cycle, held, replanned), "the carry keeps the reach model's command cap, read for the same situation")


class TheRefusals(unittest.TestCase):
    def test_past_the_budget_it_names_the_along_axis_need_and_the_deficit(self):
        far = moved(LIVE, unit(LIVE_AXES[0]), -90.0)  # the live block 90 mm farther from the pad
        plan = atr.plan(cycle, situation(far, LIVE_AXES[0]))
        self.assertFalse(plan.admitted)
        self.assertEqual("along_budget", plan.values["constraint"])
        need = plan.values["axis_advance_needed_mm"]
        self.assertTrue(atr.plan_at(cycle, situation(far, LIVE_AXES[0]), need))
        self.assertFalse(atr.plan_at(cycle, situation(far, LIVE_AXES[0]), need - 1.0))
        self.assertAlmostEqual(need - cycle.BASE_MAX_TOTAL_ADVANCE_MM, plan.values["deficit_mm"], delta=0.051)
        move = plan.values["move_mm"]
        self.assertTrue(atr.plan(cycle, situation(far, LIVE_AXES[0]).moved(move)).admitted)
        self.assertFalse(atr.plan(cycle, situation(far, LIVE_AXES[0]).moved(move - 1)).admitted)
        self.assertTrue(pe.TEXT_SHAPE.fullmatch(plan.sentence), plan.sentence)

    def test_start_3_of_2026_09_26_stays_refused_on_its_side_edge(self):
        plan = atr.plan(cycle, situation(START_3, START_3_AXIS))
        self.assertFalse(plan.admitted)
        self.assertEqual(("side_edge", "right"), (plan.values["constraint"], plan.values["binding_edge"]))
        self.assertIsNone(plan.values["move_mm"], "no move along the reach axis places it")

    def test_when_the_arm_alone_admits_it_plans_no_advance_and_the_arm_reach_travel(self):
        anchor, axis = (0.0145, -0.1475 + 0.004), (0.0, 1.0)
        plan, alone = atr.plan(cycle, situation(anchor, axis, 0.0, True)), arm.plan(cycle, situation(anchor, axis, 0.0, True))
        self.assertTrue(plan.admitted and alone.admitted)
        self.assertEqual(("ARM_ONLY", 0.0, alone.fk_travel_mm), (plan.mode, plan.base_advance_mm, plan.fk_travel_mm))


class ThePolicy(unittest.TestCase):
    def test_it_is_one_unqualified_word_of_the_placement_stage_and_this_build_executes_it(self):
        self.assertEqual("advance_then_reach", atr.WORD)
        self.assertEqual(("placement", ("advance_then_reach",), frozenset()), (atr.POLICY.stage, atr.POLICY.words, atr.POLICY.qualified))
        self.assertTrue(atr.EXECUTES)
        self.assertTrue(callable(atr.execute))
        self.assertEqual(rm.ADMISSION, atr.PLANNING_TRAVEL_MODEL)
        self.assertAlmostEqual(cycle.FK_TRAVEL_OVERSTATEMENT * cycle.BASE_ADVANCE_TOLERANCE_MM, atr.reserve_mm(cycle))

    def test_it_never_imports_the_controller(self):
        source = (ROOT / "autonomy" / "manipulation" / "policy_advance_then_reach.py").read_text()
        self.assertNotIn("import run_single_cube_cycle", source)
        self.assertNotIn("from run_single_cube_cycle", source)


if __name__ == "__main__":
    unittest.main()
