#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_placement_selector.py: the placement policies of a run side by side and the least-motion choice
# (autonomy/manipulation/placement_selector.py). Pure but for the one refusal it raises through a scripted Cycle. What is
# pinned: the proven policy IS the controller's placement_plan, dict for dict and refusal for refusal, swept; with no
# additive word the portfolio plans exactly what placement_plan plans; the least base motion is chosen, and an admitted
# plan within the advance loop's own tolerance of it goes to the proven policy; the block of 2026-09-30 07:14Z is
# advance_then_reach's under both words and under advance_then_reach alone, inside the proven loaded reach with a 97 mm
# advance (the operator's ruling of 2026-10-02, Q8; its longer carry planned 53 mm and a carry to 113 mm out); at the proven
# policy's boundary the plan keeps the cap and the longer carry starts only where no plan under the cap fits the budget
# (verify r9 replay S1); a refusal names the best
# near-miss (the policy the smallest move of the block places), its deficit and a hint checked by the policy it names;
# every text it records is in the record's text shape; a start the proven rule admits is never lost, and never needs more
# advance; the additive policies plan a block the proven policy places with THE LOADED REACH CAP (the decision keeps the
# situation it planned, with that answer), so no additive arm-alone plan of it reaches past the proven arm-alone plan; the
# decision says whether its chosen plan is the long stretch (an additive plan under the reach model's cap) and, for a
# block the proven policy refuses planned under the cap, whether that plan is robust to the record's largest grasp
# under-read, which the bindings over several blocks read; SEARCH asks the proven policy once for a block it places, the
# proven arm alone included (no binding reads that plan at the record's grasps any more: the lead's decision of
# 2026-10-04); and a least-motion plan this build does not execute is refused before anything moves, with its own code.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_placement_selector.py
import dataclasses
import math
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import placement_envelope as pe  # noqa: E402
import placement_policy as pp  # noqa: E402
import placement_selector as ps  # noqa: E402
import policy_advance_then_reach as atr  # noqa: E402
import policy_arm_reach as arm  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import stage_harness as harness  # noqa: E402
from stage_policy import CycleFailed  # noqa: E402

LIVE = (-0.035768012099579305, -0.2129612198129184)
LIVE_AXES = ((0.0616, 0.9981), (-0.0616, 0.9981))
ALLOWANCE = cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM
BOTH = (arm, atr)
WORDS = (arm.WORD, atr.WORD)  # the run's words: the loaded reach cap reads the run's policies (placement_policy.capped_plan_fits)
# (anchor in PAD_FRAME m, reach axis, coarse cube_out mm) of the recorded SEARCH frames the evidence report replays through
# the installed head (the 2026-09-30 reach analysis, evidence-replay.out; rounded as it printed them). The recorded frames
# themselves are replayed by test_reach_policies_replay.py when KC_EVIDENCE_ROOT names them.
RECORDED = (
    ((0.0095, -0.1150), (-0.017, 1.000), 58.5), ((0.0234, -0.1960), (-0.004, 1.000), -24.9), ((0.0244, -0.1967), (0.008, 1.000), -6.7),
    ((0.0233, -0.1407), (0.017, 1.000), 40.0), ((0.0069, -0.1642), (-0.011, 1.000), 18.1), ((0.0067, -0.1637), (-0.023, 1.000), 35.0),
    ((0.0068, -0.1787), (-0.026, 1.000), 3.8), ((0.0113, -0.1681), (-0.040, 0.999), 7.3), ((0.0448, -0.1779), (0.011, 1.000), -3.0),
    ((0.0121, -0.1927), (-0.023, 1.000), 3.6), ((0.0114, -0.0954), (-0.016, 1.000), 104.7), ((-0.0387, -0.1844), (-0.058, 0.998), 13.5),
    ((0.0619, -0.1883), (0.021, 1.000), 9.2), ((0.0689, -0.1869), (0.034, 0.999), 9.2), ((-0.0053, -0.1818), (-0.029, 1.000), 15.1),
    ((-0.0201, -0.1960), (-0.042, 0.999), 7.1), ((-0.0936, -0.1698), (-0.086, 0.996), 34.1), ((0.1604, -0.2110), (0.119, 0.993), -11.2),
    ((0.1604, -0.2107), (0.121, 0.993), -10.4), ((0.0598, -0.2182), (0.041, 0.999), -18.8), ((0.0757, -0.2047), (0.054, 0.999), -3.4),
    ((0.0821, -0.1790), (0.067, 0.998), 28.0),
)


def unit(axis) -> tuple:
    norm = math.hypot(*axis)
    return (axis[0] / norm, axis[1] / norm)


def search(anchor, axis, cube_out) -> pp.Situation:
    return pp.Situation(anchor, unit(axis), max(cube_out, 0.0), False, ALLOWANCE)


def proven_or_none(anchor, axis, grasp):
    try:
        return cycle.placement_plan(anchor, axis, grasp, ALLOWANCE)
    except CycleFailed:
        return None


class TheProvenPolicyIsPlacementPlan(unittest.TestCase):
    def test_its_plan_is_placement_plans_dict_and_its_refusal_placement_plans_refusal(self):
        chance = random.Random(29)
        for _ in range(60):
            anchor, deg, grasp = (chance.uniform(-0.25, 0.25), chance.uniform(-0.30, 0.10)), chance.uniform(60.0, 120.0), chance.uniform(0.0, 60.0)
            axis = (math.cos(math.radians(deg)), math.sin(math.radians(deg)))
            with self.subTest(anchor=anchor, deg=deg, grasp=grasp):
                plan, expected = ps.proven_plan(cycle, cycle.placement_plan, pp.Situation(anchor, axis, grasp, False, ALLOWANCE), explained=True), proven_or_none(anchor, axis, grasp)
                self.assertEqual(expected is not None, plan.admitted)
                if expected is not None:
                    self.assertEqual(expected, plan.raw)
                    self.assertEqual((expected["mode"], expected["base_advance_mm"], expected["fk_travel_mm"]), (plan.mode, plan.base_advance_mm, plan.fk_travel_mm))
                else:
                    self.assertEqual(pe.explain_refusal(cycle, anchor, axis, grasp, ALLOWANCE)["explanation"], plan.values["explanation"])

    def test_with_no_additive_word_the_portfolio_plans_exactly_placement_plan(self):
        portfolio = ps.Portfolio(cycle, cycle.placement_plan, ())
        for anchor, axis, cube_out in RECORDED + tuple((LIVE, axis, -5.2) for axis in LIVE_AXES):
            with self.subTest(anchor=anchor, axis=axis):
                expected = proven_or_none(anchor, unit(axis), max(cube_out, 0.0))
                if expected is None:
                    with self.assertRaises(CycleFailed):
                        portfolio(anchor, unit(axis), max(cube_out, 0.0), ALLOWANCE)
                else:
                    self.assertEqual(expected, portfolio(anchor, unit(axis), max(cube_out, 0.0), ALLOWANCE))


def plan_of(policy, advance, admitted=True) -> pp.Plan:
    return pp.Plan(policy, admitted, ("ARM_ONLY" if advance == 0.0 else "BASE_ADVANCE") if admitted else None, advance if admitted else None, 10.0, 10.0, 40.0, 40.0, "x", {})


class TheLeastMotionChoice(unittest.TestCase):
    def test_the_least_base_motion_wins(self):
        self.assertEqual("arm_reach", ps.select(cycle, (plan_of("arm_reach", 0.0), plan_of("advance_then_reach", 40.0), plan_of("proven", 80.0))).policy)
        self.assertEqual("advance_then_reach", ps.select(cycle, (plan_of("arm_reach", None, False), plan_of("advance_then_reach", 40.0), plan_of("proven", 80.0))).policy)
        self.assertIsNone(ps.select(cycle, (plan_of("arm_reach", None, False), plan_of("proven", None, False))))

    def test_within_the_advance_loops_tolerance_the_proven_policy_wins(self):
        band = cycle.BASE_ADVANCE_TOLERANCE_MM
        self.assertEqual("proven", ps.select(cycle, (plan_of("advance_then_reach", 40.0), plan_of("proven", 40.0 + band))).policy)
        self.assertEqual("advance_then_reach", ps.select(cycle, (plan_of("advance_then_reach", 40.0), plan_of("proven", 40.0 + band + 1.0))).policy)
        self.assertEqual("proven", ps.select(cycle, (plan_of("arm_reach", 0.0), plan_of("proven", 0.0))).policy)

    def test_between_two_additive_plans_of_equal_motion_the_order_decides(self):
        self.assertEqual("arm_reach", ps.select(cycle, (plan_of("arm_reach", 0.0), plan_of("advance_then_reach", 0.0), plan_of("proven", None, False))).policy)


class TheLiveBlock(unittest.TestCase):
    """The block of 2026-09-30 07:14Z (run console-20260930T071429Z-m0c6fb2a), which the proven policy refuses (124.7 mm of
    advance against 120), planned at SEARCH as a run that names the words plans it (the loaded reach cap reads the run's
    policies, ctl.PLACEMENT_ENVELOPE). The operator's ruling of 2026-10-02 (operator question Q8, verify r10 replay MUST-FIX
    1): a plan whose loaded carry stays inside the proven loaded reach fits the 120 mm budget, so it is the plan used:
    advance_then_reach with 97 mm of base and a carry to 58 mm out, commands inside 95. The longer carry it was planned with
    before the ruling (the reach model's cap) took 53 mm of base and a carry to 113 mm out, and on the recorded loaded
    behaviour its runs ended with the cube held on the stretched arm."""

    def test_both_words_choose_advance_then_reach_inside_the_proven_loaded_reach(self):
        capped = cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM
        for axis in LIVE_AXES:
            with self.subTest(axis=axis), mock.patch.object(cycle, "PLACEMENT_ENVELOPE", WORDS):
                decision = ps.decide(cycle, search(LIVE, axis, -5.2), BOTH, cycle.placement_plan)
                self.assertEqual(["arm_reach", "advance_then_reach", "proven"], [plan.policy for plan in decision.plans])
                self.assertEqual([False, True, False], [plan.admitted for plan in decision.plans])
                self.assertIs(False, decision.situation.proven_admits)
                selected = decision.selected
                self.assertEqual(("advance_then_reach", "BASE_ADVANCE", 97.0, 36.0, 58.0), (selected.policy, selected.mode, selected.base_advance_mm, selected.fk_travel_mm, selected.arm_out_mm))
                self.assertEqual(capped, selected.values["reach_cap_mm"])
                portfolio = ps.Portfolio(cycle, cycle.placement_plan, BOTH)
                self.assertEqual({"mode": "BASE_ADVANCE", "fk_travel_mm": 36.0, "base_advance_mm": 97.0, "policy": "advance_then_reach"}, portfolio(LIVE, unit(axis), 0.0, ALLOWANCE))
                self.assertIs(portfolio.last.selected.policy, "advance_then_reach")
                with mock.patch.object(pp, "loaded_reach_cap_mm", lambda ctl, situation, proven_admits: pp.reach_model.measured_out_cap_mm(ctl)):
                    longer = ps.decide(cycle, search(LIVE, axis, -5.2), BOTH, cycle.placement_plan).selected
                self.assertEqual(("advance_then_reach", 53.0, 91.0, 113.0), (longer.policy, longer.base_advance_mm, longer.fk_travel_mm, longer.arm_out_mm), "the longer carry")

    def test_advance_then_reach_alone_admits_it_and_arm_reach_alone_does_not(self):
        for axis in LIVE_AXES:
            with self.subTest(axis=axis):
                with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", (atr.WORD,)):
                    alone = ps.decide(cycle, search(LIVE, axis, -5.2), (atr,), cycle.placement_plan).selected
                self.assertEqual((97.0, 58.0), (alone.base_advance_mm, alone.arm_out_mm))
                with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", (arm.WORD,)):
                    refused = ps.decide(cycle, search(LIVE, axis, -5.2), (arm,), cycle.placement_plan)
                    self.assertIsNone(refused.selected)
                    # the nearest is the proven rule, 5 mm of move toward the pad (its own checked hint); arm_reach needs 45
                    self.assertEqual(("proven", "along_budget", 4.7), (refused.explanation["near_miss_policy"], refused.explanation["constraint"], refused.explanation["deficit_mm"]))
                    self.assertEqual("move the block 5 mm toward the pad", refused.explanation["hint"])
                    self.assertIs(True, refused.explanation["hint_places_block"])
                    with self.assertRaises(CycleFailed) as raised:
                        ps.Portfolio(cycle, cycle.placement_plan, (arm,))(LIVE, unit(axis), 0.0, ALLOWANCE)
                self.assertIsInstance(raised.exception, ps.PlacementRefused)
                self.assertEqual(refused.explanation, raised.exception.explanation)


class TheRefusal(unittest.TestCase):
    def assertInTheRecordShape(self, explanation):
        for name, value in explanation.items():
            self.assertTrue(value is None or isinstance(value, (str, bool, int, float)), name)
            if isinstance(value, str):
                self.assertTrue(pe.TEXT_SHAPE.fullmatch(value), f"{name}: {value!r}")

    def test_it_names_the_policy_the_smallest_move_places_and_the_move_is_checked(self):
        chance = random.Random(3)
        refused = 0
        for _ in range(45):
            anchor, deg = (chance.uniform(-0.30, 0.30), chance.uniform(-0.45, -0.18)), chance.uniform(62.0, 118.0)
            situation = pp.Situation(anchor, (math.cos(math.radians(deg)), math.sin(math.radians(deg))), chance.uniform(0.0, 40.0), False, ALLOWANCE)
            decision = ps.decide(cycle, situation, BOTH, cycle.placement_plan)
            if decision.selected is not None:
                continue
            refused += 1
            explanation = decision.explanation
            with self.subTest(anchor=anchor, deg=deg):
                self.assertInTheRecordShape(explanation)
                self.assertIn(explanation["near_miss_policy"], ("arm_reach", "advance_then_reach", "proven"))
                self.assertEqual("arm_reach+advance_then_reach+proven", explanation["policies_evaluated"])
                if explanation["hint_places_block"] and explanation["near_miss_policy"] != "proven":
                    move = explanation["move_along_axis_mm"]
                    placed = ps.decide(cycle, situation.moved(move), BOTH, cycle.placement_plan)
                    self.assertIsNotNone(placed.selected, "the hint is checked: followed, a policy places the block")
                    for plan in decision.plans:
                        size = ps.move_size(plan)
                        if size is not None:
                            self.assertGreaterEqual(size, abs(move))
        self.assertGreater(refused, 12)

    def test_start_3_is_refused_by_every_policy_and_the_proven_solvers_sideways_move_is_the_near_miss(self):
        anchor, axis = (0.1604, -0.2110), unit((0.119, 0.993))
        decision = ps.decide(cycle, search(anchor, axis, -11.2), BOTH, cycle.placement_plan)
        self.assertIsNone(decision.selected)
        self.assertEqual([None, None], [plan.values["move_mm"] for plan in decision.plans[:2]], "no move along the axis places it for either additive policy")
        self.assertEqual(("side_edge", "right"), (decision.plans[1].values["constraint"], decision.plans[1].values["binding_edge"]))
        proven = pe.explain_refusal(cycle, anchor, axis, 0.0, ALLOWANCE)
        self.assertEqual(("proven", proven["constraint"], proven["hint"]), (decision.explanation["near_miss_policy"], decision.explanation["constraint"], decision.explanation["hint"]))
        self.assertInTheRecordShape(decision.explanation)

    def test_a_reading_that_is_not_finite_has_no_plan_and_the_solvers_explanation(self):
        portfolio = ps.Portfolio(cycle, cycle.placement_plan, BOTH)
        decision = portfolio.decide((float("nan"), -0.2), (0.0, 1.0), 0.0, ALLOWANCE)
        self.assertEqual(((), None), (decision.plans, decision.selected))
        self.assertEqual(pe.NOT_FINITE_EXPLANATION, decision.explanation["explanation"])
        with self.assertRaises(ps.PlacementRefused):
            portfolio((0.0, -0.2), (float("nan"), 1.0), 0.0, ALLOWANCE)


class NoStartIsLost(unittest.TestCase):
    def test_every_recorded_start_the_proven_rule_admits_stays_admitted_with_no_more_advance(self):
        for anchor, axis, cube_out in RECORDED:
            with self.subTest(anchor=anchor, axis=axis, cube_out=cube_out):
                expected = proven_or_none(anchor, unit(axis), max(cube_out, 0.0))
                decision = ps.decide(cycle, search(anchor, axis, cube_out), BOTH, cycle.placement_plan)
                if expected is not None:
                    self.assertIsNotNone(decision.selected)
                    self.assertLessEqual(decision.selected.base_advance_mm, expected["base_advance_mm"])

    def test_the_recorded_refusals_are_named(self):
        refused = [ps.decide(cycle, search(anchor, axis, cube_out), BOTH, cycle.placement_plan) for anchor, axis, cube_out in RECORDED
                   if proven_or_none(anchor, unit(axis), max(cube_out, 0.0)) is None]
        self.assertEqual(3, len(refused), "the installed head refused start 3 twice and the 21:17 block")
        self.assertEqual([None, None, "advance_then_reach"], [None if decision.selected is None else decision.selected.policy for decision in refused])


class TheLoadedReachCap(unittest.TestCase):
    """The operator's ruling of 2026-10-01 ("cap at proven reach", placement_policy THE LOADED REACH CAP)."""

    def test_the_proven_policy_is_planned_first_and_its_answer_caps_the_additive_plans(self):
        for anchor, axis, cube_out in RECORDED:
            with self.subTest(anchor=anchor, axis=axis, cube_out=cube_out):
                decision = ps.decide(cycle, search(anchor, axis, cube_out), BOTH, cycle.placement_plan)
                proven = [plan for plan in decision.plans if plan.policy == pp.PROVEN][0]
                self.assertIs(proven.admitted, decision.situation.proven_admits)
                for plan in decision.plans:
                    if plan.admitted and plan.policy != pp.PROVEN and proven.admitted:
                        self.assertLessEqual(plan.arm_out_mm, cycle.ARM_LOADED_REACH_OUT_MM + 1e-9, plan.record())
                    if plan.policy != pp.PROVEN:
                        self.assertEqual(pp.loaded_reach_cap_mm(cycle, decision.situation, proven.admitted), plan.values["reach_cap_mm"])

    def test_a_situation_that_says_whether_the_proven_policy_places_the_block_is_planned_as_it_says(self):
        anchor, axis, cube_out = RECORDED[7]
        for says in (True, False):
            situation = pp.Situation(anchor, unit(axis), cube_out, True, ALLOWANCE, proven_admits=says)
            decision = ps.decide(cycle, situation, BOTH, cycle.placement_plan)
            self.assertIs(says, decision.situation.proven_admits)
            self.assertEqual(pp.loaded_reach_cap_mm(cycle, situation, says), [plan for plan in decision.plans if plan.policy == "arm_reach"][0].values["reach_cap_mm"])

    def test_no_additive_arm_alone_plan_of_a_block_the_proven_policy_places_passes_the_proven_arm_alone_plan(self):
        """Inside the cap the arm alone's band is the proven arm-alone plan's own (the same admission, the same reach less the
        same measured grasp; at SEARCH less the under-read charge too): wherever an additive policy places the block with the
        arm alone, so does the proven policy, and the tie goes to it. Swept over anchors, axes and grasps."""
        rng, ties = random.Random(20261001), 0
        for _ in range(400):
            anchor = (rng.uniform(-0.15, 0.15), rng.uniform(-0.23, -0.10))
            axis = unit((rng.uniform(-0.12, 0.12), 1.0))
            grasp, measured = rng.uniform(-10.0, 50.0), rng.random() < 0.5
            situation = pp.Situation(anchor, axis, max(grasp, 0.0), measured, ALLOWANCE)
            decision = ps.decide(cycle, situation, BOTH, cycle.placement_plan)
            if not decision.situation.proven_admits:
                continue
            arm_alone = [plan for plan in decision.plans if plan.policy != pp.PROVEN and plan.admitted and plan.mode == pp.MODE_ARM_ONLY]
            proven = [plan for plan in decision.plans if plan.policy == pp.PROVEN][0]
            with self.subTest(anchor=anchor, axis=axis, grasp=grasp, measured=measured):
                if arm_alone:
                    ties += 1
                    self.assertEqual(pp.MODE_ARM_ONLY, proven.mode)
                    self.assertEqual(pp.PROVEN, decision.selected.policy)
        self.assertGreater(ties, 50, "the sweep meets additive arm-alone plans inside the cap")

    def test_at_the_proven_boundary_the_plan_keeps_the_cap_and_the_longer_carry_starts_only_past_the_budget(self):
        """verify r9 replay S1 (the cap jump), under the operator's ruling of 2026-10-02 (Q8). The block slid along its reach
        axis on the operator's line (x -35.8 mm), at SEARCH's coarse grasp 0, as a run with both words plans it. One
        millimetre past the proven policy's last placed position (pad y -208 to -209 mm) the plan used to drop 43 mm of
        advance while its carry jumped from 58 to 113 mm out: now it keeps the cap there and its advance goes on with the
        block, 1 mm for 1 mm. The longer carry starts only where no plan under the cap fits the 120 mm budget: at -236 mm the
        plan under the cap takes all 120 mm, at -237 mm none fits and the longer carry plans 77 mm of base and a carry to
        113 mm out (that block, which the proven policy refuses, keeps the longer try the rulings leave it)."""
        capped, longer = cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM, pp.reach_model.measured_out_cap_mm(cycle)

        def at(y_mm):
            decision = ps.decide(cycle, search((-0.0358, y_mm / 1000.0), (0.0, 1.0), 0.0), BOTH, cycle.placement_plan)
            return decision, next(plan for plan in decision.plans if plan.policy == pp.PROVEN).admitted

        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", WORDS):
            (inside, placed), (past, refused) = at(-208.0), at(-209.0)
            self.assertEqual((True, False), (placed, refused), "the proven policy's last placed position")
            for decision in (inside, past):
                self.assertEqual(("advance_then_reach", capped, 58.0), (decision.selected.policy, decision.selected.values["reach_cap_mm"], decision.selected.arm_out_mm))
            self.assertEqual((92.0, 93.0), (inside.selected.base_advance_mm, past.selected.base_advance_mm))
            (last, _), (beyond, _) = at(-236.0), at(-237.0)
            self.assertEqual((capped, 120.0, 58.0), (last.selected.values["reach_cap_mm"], last.selected.base_advance_mm, last.selected.arm_out_mm))
            self.assertTrue(pp.capped_plan_fits(cycle, last.situation))
            self.assertFalse(pp.capped_plan_fits(cycle, beyond.situation), "no plan under the cap fits the budget")
            self.assertEqual((longer, 77.0, 113.0), (beyond.selected.values["reach_cap_mm"], beyond.selected.base_advance_mm, beyond.selected.arm_out_mm))

    def test_the_decision_says_whether_its_chosen_plan_is_the_long_stretch(self):
        """The bindings over several blocks read it (block_selection THE BINDING TIERS; verify r11 safety MF1,
        policy MF1, replay MUST-FIX 1): the chosen additive plan of a block no plan under the loaded reach cap serves is the
        long stretch, under the reach model's cap, and no other chosen plan is. At the cap's boundary on the operator's line
        (-236 mm planned under the cap, -237 mm the long stretch), the operator's block (under the cap), every recorded start
        and a block by the left edge only the arm alone's long stretch places (0 mm of base, a carry to 126 mm out)."""
        capped, longer = cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM, pp.reach_model.measured_out_cap_mm(cycle)

        def at(anchor, axis=(0.0, 1.0), cube_out=0.0):
            return ps.decide(cycle, search(anchor, axis, cube_out), BOTH, cycle.placement_plan)

        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", WORDS):
            last, beyond = at((-0.0358, -0.236)), at((-0.0358, -0.237))
            self.assertEqual((capped, False), (last.selected.values["reach_cap_mm"], last.long_stretch))
            self.assertEqual((longer, True), (beyond.selected.values["reach_cap_mm"], beyond.long_stretch))
            self.assertEqual((False, True), (pp.long_stretch(cycle, last.situation), pp.long_stretch(cycle, beyond.situation)))
            for axis in LIVE_AXES:
                with self.subTest(live=axis):
                    self.assertIs(False, at(LIVE, axis, -5.2).long_stretch, "the operator's block is planned under the cap")
            edge = at((-0.160, -0.165))
            self.assertEqual(("arm_reach", 0.0, longer, True), (edge.selected.policy, edge.selected.base_advance_mm, edge.selected.arm_out_mm, edge.long_stretch))
            for anchor, axis, cube_out in RECORDED:
                decision = at(anchor, axis, cube_out)
                with self.subTest(anchor=anchor, axis=axis, cube_out=cube_out):
                    wanted = decision.selected is not None and decision.selected.policy != pp.PROVEN and decision.selected.values["reach_cap_mm"] > capped
                    self.assertIs(wanted, decision.long_stretch)
            self.assertIs(False, at((0.10, -0.30)).long_stretch, "no plan: no long stretch")
        self.assertIs(False, ps.Portfolio(cycle, cycle.placement_plan, BOTH).decide((float("nan"), 0.0), (0.0, 1.0), 0.0).long_stretch)

    def test_a_search_decision_of_a_block_the_proven_policy_refuses_says_whether_its_plan_under_the_cap_is_robust(self):
        """block_selection THE BINDING TIERS (the lead's decision of 2026-10-03): Decision.capped_robust is
        placement_policy.capped_plan_robust for a SEARCH decision whose chosen additive plan stays under the loaded reach cap
        for a block the proven policy refuses, and None where it is not asked: a block the proven policy places, a long
        stretch, a measured situation, no plan."""
        def at(anchor, cube_out=0.0, measured=False):
            return ps.decide(cycle, pp.Situation(anchor, (0.0, 1.0), cube_out, measured, ALLOWANCE), BOTH, cycle.placement_plan)

        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", WORDS):
            for anchor, cube_out, robust in (((0.0145, -0.230), 0.0, True), ((0.0145, -0.230), 5.0, False), ((0.0145, -0.234), 0.0, False), (LIVE, 0.0, True)):
                decision = at(anchor, cube_out)
                with self.subTest(anchor=anchor, cube_out=cube_out):
                    self.assertEqual((False, False, "advance_then_reach"), (decision.situation.proven_admits, decision.long_stretch, decision.selected.policy))
                    self.assertIs(robust, decision.capped_robust)
                    self.assertIs(pp.capped_plan_robust(cycle, decision.situation), decision.capped_robust)
            self.assertIsNone(at((0.0145, -0.1925), 22.0).capped_robust, "a block the proven policy places")
            self.assertIsNone(at((-0.0358, -0.237)).capped_robust, "the long stretch")
            self.assertIsNone(at((0.0145, -0.230), 30.5, measured=True).capped_robust, "a measured situation")
            self.assertIsNone(at((0.10, -0.30)).capped_robust, "no plan")

    def test_a_search_decision_asks_the_proven_policy_once_whatever_plan_it_chose(self):
        """The lead's decision of 2026-10-04 (block_selection THE RULE): no binding reads whether a proven arm alone survives
        the record's grasps any more, so SEARCH asks the proven policy once for a block it places, the proven arm alone
        included, under any portfolio (the proven policy alone, as multi_block plans, or beside the additive words), and the
        decision carries no such answer (the robust arm alone of 2026-10-03 asked it a second time, 30.5 mm farther out)."""
        blocks = (((0.0145, -0.1375), 0.0), ((0.0145, -0.1615), 0.6), ((0.0145, -0.1505), 0.0), ((0.0145, -0.1575), 0.0), ((-0.08, -0.1325), 25.0))
        self.assertNotIn("arm_alone_robust", [field.name for field in dataclasses.fields(ps.Decision)])
        self.assertFalse(hasattr(ps, "arm_alone_robust"))
        for policies, words in (((), ()), (BOTH, WORDS), ((atr,), (atr.WORD,))):
            with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words):
                for anchor, cube_out in blocks:
                    asked = []

                    def proven(*args, **kwargs):
                        asked.append(args)
                        return cycle.placement_plan(*args, **kwargs)

                    decision = ps.decide(cycle, pp.Situation(anchor, (0.0, 1.0), cube_out, False, ALLOWANCE), policies, proven)
                    with self.subTest(anchor=anchor, cube_out=cube_out, words=words):
                        self.assertEqual(("proven", "ARM_ONLY"), (decision.selected.policy, decision.selected.mode))
                        self.assertEqual(1, len(asked), asked)

    def test_the_portfolio_keeps_the_situation_its_last_decision_planned(self):
        anchor, axis, cube_out = RECORDED[7]
        portfolio = ps.Portfolio(cycle, cycle.placement_plan, BOTH)
        portfolio(anchor, unit(axis), cube_out, ALLOWANCE)
        self.assertIs(portfolio.last.situation, portfolio.last_situation)
        self.assertIs(True, portfolio.last_situation.proven_admits)


class TheExecutorGuard(unittest.TestCase):
    def _search_with(self, policies, anchor, axis, cube_out):
        portfolio = ps.Portfolio(cycle, cycle.placement_plan, policies)
        try:
            portfolio(anchor, unit(axis), max(cube_out, 0.0), ALLOWANCE)
        except CycleFailed:
            pass
        return portfolio

    def test_a_least_motion_plan_this_build_does_not_execute_is_refused_before_anything_moves(self):
        ctx = harness.HarnessCycle()
        with self.assertRaises(CycleFailed) as raised, mock.patch.object(atr, "EXECUTES", False):  # a build that plans the policy without its executor
            ps.refuse_without_executor(ctx, self._search_with(BOTH, LIVE, LIVE_AXES[0], -5.2), {"observation_id": "obs-1"})
        self.assertIn("advance_then_reach", str(raised.exception))
        self.assertEqual("search_placement_policy_without_executor", ctx.events.calls[-1][1][0])
        self.assertEqual([], ctx.calls, "nothing was commanded")

    def test_the_proven_choice_no_choice_and_no_portfolio_pass(self):
        ctx = harness.HarnessCycle()
        ps.refuse_without_executor(ctx, None, None)
        inside = self._search_with(BOTH, (0.0145, -0.1475 + 0.012), (0.0, 1.0), 3.0)  # the World's arm-only cube: the proven plan and arm_reach both carry it, a tie
        self.assertEqual("proven", inside.last.selected.policy)
        ps.refuse_without_executor(ctx, inside, None)
        ps.refuse_without_executor(ctx, self._search_with((), LIVE, LIVE_AXES[0], -5.2), None)  # refused: no choice at all
        self.assertNotIn("cycle_failed", [call[0] for call in ctx.events.calls])

    def test_once_a_policy_executes_its_choice_passes(self):
        ctx = harness.HarnessCycle()
        ps.refuse_without_executor(ctx, self._search_with(BOTH, LIVE, LIVE_AXES[0], -5.2), None)  # this build executes both additive policies
        self.assertNotIn("cycle_failed", [call[0] for call in ctx.events.calls])


class ThePolicies(unittest.TestCase):
    def test_the_additive_policies_are_placement_words_least_motion_first(self):
        self.assertEqual(("arm_reach", "advance_then_reach"), ps.WORDS)
        self.assertEqual(("arm_reach", "advance_then_reach", "proven"), ps.ORDER)
        for module in ps.POLICIES:
            with self.subTest(module.WORD):
                self.assertEqual(("placement", (module.WORD,)), (module.POLICY.stage, module.POLICY.words))
        self.assertEqual((atr,), ps.enabled(("projected", "advance_then_reach", "scene_scan")))
        self.assertEqual((arm, atr), ps.enabled(("advance_then_reach", "arm_reach")))
        self.assertEqual((), ps.enabled(()))

    def test_every_policy_executes_in_this_build(self):
        self.assertEqual([True, True, True], [ps.executes(name) for name in ("proven", "arm_reach", "advance_then_reach")])
        with mock.patch.object(atr, "EXECUTES", False):
            self.assertEqual([True, True, False], [ps.executes(name) for name in ("proven", "arm_reach", "advance_then_reach")])


if __name__ == "__main__":
    unittest.main()
