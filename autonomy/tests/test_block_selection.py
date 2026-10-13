#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_block_selection.py: the SEARCH stage's word multi_block (autonomy/manipulation/block_selection.py):
# which of several blocks off the pad a mission target binds. Pure. What is pinned: among the blocks the proven policy
# places (THE BINDING TIERS' first tier), an auto target binds exactly the block the installed controller's rule binds, the
# one nearest the pad (the largest signed placed margin, then the listing order), whatever base or arm travel the blocks'
# plans name, on the verifiers' floors (a proven arm alone beside a nearer block the base carries, robust or not to the
# record's grasps), in either listing order, under every word set (the lead's decision of 2026-10-04); among blocks the
# proven policy refuses, the auto target binds the admissible block with the least base motion (then the least arm travel,
# the block nearest the pad, the listing order), where the installed rule binds the one nearest the pad; the binding names
# the rule that chose (binding_rule); a mission with a target that declares its mode, alone or beside an auto target, is
# bound exactly as the proven binding binds it, its bindings, selection and refusals word for word (the lead's decision of
# 2026-10-01: the words apply to auto targets only); every refusal the proven binding makes with one block or with none (no
# candidate, no bounded placement, the targets sharing a mode) is made the same, with the same values; a later execution
# selects by position exactly as before; with one candidate nothing changes; a candidate SEARCH's pickup bands refuse is set
# aside while another with a placement passes them (THE PICKUP BANDS AT THE BINDING, verify r8 replay MUST-FIX 3); beside a
# nearer block the proven policy places, the operator's block of 2026-09-30 is no longer bound by its longer carry (the
# operator's ruling of 2026-10-02, Q8); a block only the long stretch places (an additive plan under the reach model's cap)
# is never bound, by either binding rule, while another listed block has the proven plan or one under the loaded reach cap,
# however much less base it names or however much nearer the pad it lies, and the least motion ranks it after every such
# plan (verify r11 safety MF1, policy MF1 and replay MUST-FIX 1), on the faces the verifiers found too; a block the proven
# policy refuses is never bound while another listed block is one the proven policy places, however much less base its plan
# under the loaded reach cap names, the operator's block included, on verify r12's floors too (THE BINDING TIERS, the lead's
# decision of 2026-10-03; verify r12 safety MF1 and replay MUST-FIX 1); every block of a later tier is set aside, in every
# listing order; one block keeps its own plan whatever its tier; the word is the search stage's, unqualified. No robot, no
# camera, no network. Run directly: python3 autonomy/tests/test_block_selection.py
import itertools
import math
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))  # policy_search, which block_selection imports, imports scene_scan from there
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import block_selection as bs  # noqa: E402
import placement_policy as pp  # noqa: E402
import placement_selector as ps  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import target_binding as tb  # noqa: E402

RULES = tb.Rules(cycle.PLACED_MIN_EDGE_MARGIN_MM, 12.7, 12.7)
LIVE = (-0.035768012099579305, -0.2129612198129184)  # the operator's block of 2026-09-30 07:14Z, PAD_FRAME metres
ALLOWANCE = cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM
TIE = cycle.BASE_ADVANCE_TOLERANCE_MM  # the advance loop's own stopping tolerance: plans within it of each other are the same motion to the robot


def plan(policy, advance, travel=10.0) -> pp.Plan:
    return pp.Plan(policy, True, "ARM_ONLY" if advance == 0.0 else "BASE_ADVANCE", advance, travel, travel, 40.0, 40.0, "x", {})


def decision(*plans) -> ps.Decision:
    chosen = ps.select(cycle, plans)
    return ps.Decision(tuple(plans), chosen, None if chosen else {"explanation": "none"})


def cube(centre, margin, chosen: ps.Decision) -> tb.SeenCube:
    return tb.SeenCube(centre, margin, None if chosen.selected is None else chosen.selected.mode, None if chosen.selected else {"explanation": "none", "hint": "none"})


def auto_target(targets=None, index=0, first=True, **extra):
    """A MissionTarget stand-in with what select_for_execution reads."""
    targets = targets or (tb.DeclaredTarget("cube", tb.AUTO),)
    fields = {"targets": tuple(targets), "index": index, "first_execution": first, "bound_pad_m": None, "mode": targets[index].mode, "target_id": targets[index].target_id}
    fields.update(extra)
    return type("Target", (), fields)()


def geometric(anchor, cube_out) -> tuple:
    """(SeenCube, Decision) for a block seen at SEARCH straight ahead, planned by the proven policy alone."""
    situation = pp.Situation(anchor, (0.0, 1.0), max(cube_out, 0.0), False, ALLOWANCE)
    chosen = ps.decide(cycle, situation, (), cycle.placement_plan)
    return tb.SeenCube(anchor, cycle.placed_margin_mm(anchor), None if chosen.selected is None else chosen.selected.mode,
                       None if chosen.selected is not None else {"explanation": chosen.explanation["explanation"], "hint": chosen.explanation["hint"]}), chosen


class AnAutoTargetTakesTheLeastMotion(unittest.TestCase):
    """Among blocks the proven policy refuses (a later binding tier), the least motion; among blocks it places, the installed
    rule (TheInstalledRuleInsideTheFirstTier below)."""

    def test_the_order_after_the_motion(self):
        """The blocks below are all of the tier the proven policy refuses but for the first case's second block (its plans hold
        an admitted proven one: the first tier, before any plan of a later tier)."""
        tied = (plan("advance_then_reach", 40.0), plan("proven", 40.0))
        cases = (
            ("the first tier", [decision(plan("advance_then_reach", 10.0)), decision(*tied)], 1, "nearest_the_pad"),
            ("the least base", [decision(plan("advance_then_reach", 41.0)), decision(plan("advance_then_reach", 40.0))], 1, "least_motion"),
            ("the least arm travel", [decision(plan("arm_reach", 0.0, 90.0)), decision(plan("arm_reach", 0.0, 60.0))], 1, "least_motion"),
            ("the block nearest the pad", [decision(plan("arm_reach", 0.0)), decision(plan("arm_reach", 0.0))], 1, "least_motion"),
            ("the listing order", [decision(plan("arm_reach", 0.0)), decision(plan("arm_reach", 0.0))], 0, "least_motion"),
        )
        for name, decisions, wanted, rule in cases:
            with self.subTest(name):
                margins = (-30.0, -20.0) if name == "the block nearest the pad" else (-25.0, -25.0)
                cubes = [cube((0.05 * index, -0.17), margins[index], decisions[index]) for index in range(2)]  # two blocks are never within one edge of each other
                self.assertEqual(wanted, bs.select_for_execution(auto_target(), cubes, decisions, RULES)[1].index)
                self.assertEqual(((wanted, cubes[wanted]), rule), bs.the_rule(list(enumerate(cubes)), decisions))
                self.assertEqual(rule, bs.binding_rule(decisions[wanted]))

    def test_the_operators_block_beside_a_nearer_block_the_proven_policy_places_leaves_the_nearer_one_bound(self):
        """verify r10 replay MUST-FIX 1 (its two-block scene, the operator's block in verify r9 replay's cap_jump_x shape), under
        the operator's ruling of 2026-10-02 (Q8). The operator's block of 2026-09-30 beside a block 12 mm nearer the pad and 70 mm aside, which the
        proven policy places (113 mm of base), as a run with the words and multi_block plans them at SEARCH. multi_block's rule
        used to bind the operator's block for its longer carry (53 mm of base, a carry to 113 mm out), which ended held on the
        stretched arm under the recorded loaded lag where 31c546c places the nearer block. Now both are planned under the
        proven loaded reach cap, the nearer one with 12 mm less base, and the rule binds it in either listing order, as the
        proven binding and the words without multi_block do."""
        near, capped = (0.0333631879004207, -0.20099271169536256), cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("arm_reach", "advance_then_reach", bs.WORD)):
            policies = ps.enabled(cycle.PLACEMENT_ENVELOPE)
            for dx in (0.0, 0.0724, -0.0724):
                axis = (dx, math.sqrt(1.0 - dx * dx))
                for coarse in (0.0, 20.0):
                    decisions = {anchor: ps.decide(cycle, pp.Situation(anchor, axis, coarse, False, ALLOWANCE), policies, cycle.placement_plan) for anchor in (LIVE, near)}
                    with self.subTest(dx=dx, coarse=coarse):
                        self.assertEqual((False, True), tuple(decisions[anchor].situation.proven_admits for anchor in (LIVE, near)))
                        for decision in decisions.values():
                            self.assertEqual(("advance_then_reach", capped, 58.0), (decision.selected.policy, decision.selected.values["reach_cap_mm"], decision.selected.arm_out_mm))
                        self.assertEqual(12.0, decisions[LIVE].selected.base_advance_mm - decisions[near].selected.base_advance_mm)
                        for listed in ((LIVE, near), (near, LIVE)):
                            cubes = [tb.SeenCube(anchor, cycle.placed_margin_mm(anchor), decisions[anchor].selected.mode, None) for anchor in listed]
                            _bindings, selection = bs.select_for_execution(auto_target(), cubes, [decisions[anchor] for anchor in listed], RULES)
                            self.assertEqual(near, tuple(selection.seen_pad_m), listed)

    def test_the_cap_jump_pair_binds_the_block_the_proven_policy_places(self):
        """verify r9 replay V3 (cap_jump_x), under the operator's ruling of 2026-10-02 (Q8): a block the proven policy refuses
        (+40 mm aside, 214 mm behind the pad's centre) beside one it places (-40 mm aside, 188 mm behind). With multi_block the
        least-motion rule used to bind the refused one for its longer carry (54 mm of base, a carry past the proven loaded
        reach), which ended held under the record's median loaded lag, where 31c546c places the other. Now both are planned
        under the proven loaded reach cap, the block the proven policy places with 26 mm less base, and the rule binds it in
        either listing order."""
        refused, placed = (0.040, -0.214), (-0.040, -0.188)
        capped = cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("arm_reach", "advance_then_reach", bs.WORD)):
            policies = ps.enabled(cycle.PLACEMENT_ENVELOPE)
            for dx in (0.0, 0.03, -0.03):
                axis = (dx, math.sqrt(1.0 - dx * dx))
                for coarse in (0.0, 15.0):
                    decisions = {anchor: ps.decide(cycle, pp.Situation(anchor, axis, coarse, False, ALLOWANCE), policies, cycle.placement_plan) for anchor in (refused, placed)}
                    with self.subTest(dx=dx, coarse=coarse):
                        self.assertEqual((False, True), tuple(decisions[anchor].situation.proven_admits for anchor in (refused, placed)))
                        for decision in decisions.values():
                            self.assertEqual(("advance_then_reach", capped), (decision.selected.policy, decision.selected.values["reach_cap_mm"]))
                            self.assertLessEqual(decision.selected.arm_out_mm, capped)
                        self.assertEqual(26.0, decisions[refused].selected.base_advance_mm - decisions[placed].selected.base_advance_mm)
                        for listed in ((refused, placed), (placed, refused)):
                            cubes = [tb.SeenCube(anchor, cycle.placed_margin_mm(anchor), decisions[anchor].selected.mode, None) for anchor in listed]
                            _bindings, selection = bs.select_for_execution(auto_target(), cubes, [decisions[anchor] for anchor in listed], RULES)
                            self.assertEqual(placed, tuple(selection.seen_pad_m), listed)

    def test_a_block_on_the_pad_or_without_a_plan_is_never_taken(self):
        placed = decision(plan("proven", 0.0))
        refused = ps.Decision((), None, {"explanation": "none"})
        far = decision(plan("advance_then_reach", 100.0))
        cubes = [cube((0.0, 0.0), 147.5, placed), cube((0.1, -0.30), -150.0, refused), cube((0.0, -0.2), -52.5, far)]
        self.assertEqual(2, bs.select_for_execution(auto_target(), cubes, [placed, refused, far], RULES)[1].index)


class ThePickupBandsAtTheBinding(unittest.TestCase):
    """verify r8 replay MUST-FIX 3 (block_selection THE PICKUP BANDS AT THE BINDING): under the words a candidate SEARCH's
    pickup bands refuse is never bound while another candidate with a placement passes them, by either binding rule; when
    every candidate with a placement is refused by a band, the cubes are read as they are."""

    A = ((0.150, -0.1675), -20.0)  # near the right side edge, nearest the pad: the bands refuse it (31 deg of pan from home)
    B = ((0.0, -0.1875), -40.0)    # on the centreline, farther out: the bands pass it
    BAND = "search_cube_beyond_return_bound"

    def scene(self, a_first: bool):
        a = decision(plan("advance_then_reach", 31.0, 92.0))
        b = decision(plan("proven", 90.0, 0.0))
        cubes = [cube(*self.A, a), cube(*self.B, b)]
        decisions, bands = [a, b], [self.BAND, None]
        if not a_first:
            cubes, decisions, bands = cubes[::-1], decisions[::-1], bands[::-1]
        return cubes, decisions, bands

    def test_the_block_the_bands_refuse_is_set_aside_and_either_rule_binds_the_other(self):
        for a_first in (True, False):
            cubes, decisions, bands = self.scene(a_first)
            b_index = 1 if a_first else 0
            with self.subTest(a_first=a_first):
                self.assertEqual(1 - b_index, tb.select_for_execution(auto_target(), cubes, RULES)[1].index, "nearest the pad, blind to the bands")
                read, set_aside = bs.past_the_pickup_bands(cubes, bands, RULES)
                self.assertEqual(1, set_aside)
                self.assertIsNone(read[1 - b_index].mode)
                self.assertEqual(cubes[b_index], read[b_index])
                self.assertEqual(b_index, tb.select_for_execution(auto_target(), read, RULES)[1].index)
                self.assertEqual(b_index, bs.select_for_execution(auto_target(), read, decisions, RULES)[1].index)

    def test_when_every_placeable_candidate_is_refused_by_a_band_the_cubes_are_read_as_they_are(self):
        cubes, _decisions, _bands = self.scene(True)
        for bands in ([self.BAND, self.BAND], [self.BAND, "search_cube_inside_pregrasp_reach"]):
            with self.subTest(bands=bands):
                self.assertEqual((tuple(cubes), 0), bs.past_the_pickup_bands(cubes, bands, RULES))
        self.assertEqual((tuple(cubes[:1]), 0), bs.past_the_pickup_bands(cubes[:1], [self.BAND], RULES), "one placeable block: as before")

    def test_a_band_answer_for_a_block_on_the_pad_or_without_a_placement_sets_nothing_aside(self):
        refused = ps.Decision((), None, {"explanation": "none"})
        placed = cube((0.0, 0.0), 147.5, decision(plan("proven", 0.0)))
        nowhere = cube((0.1, -0.30), -150.0, refused)
        b = cube(*self.B, decision(plan("proven", 90.0, 0.0)))
        self.assertEqual(((placed, nowhere, b), 0), bs.past_the_pickup_bands([placed, nowhere, b], [self.BAND, self.BAND, None], RULES))
        with self.assertRaises(ValueError):
            bs.past_the_pickup_bands([b], [], RULES)


def stretched(*plans) -> ps.Decision:
    """A decision whose chosen plan is the long stretch (placement_selector.Decision.long_stretch)."""
    chosen = ps.select(cycle, plans)
    return ps.Decision(tuple(plans), chosen, None, None, True)


def robust(*plans) -> ps.Decision:
    """A decision of a block the proven policy refuses whose chosen plan under the loaded reach cap still fits the budget at
    the record's largest grasp under-read (placement_selector.Decision.capped_robust)."""
    chosen = ps.select(cycle, plans)
    return ps.Decision(tuple(plans), chosen, None, None, False, True)


def axis_deg(degrees: float) -> tuple:
    return (math.sin(math.radians(degrees)), math.cos(math.radians(degrees)))


class TheLongStretchAtTheBinding(unittest.TestCase):
    """verify r11 safety MF1, policy MF1 and replay MUST-FIX 1 (block_selection THE BINDING TIERS, its last tier; the binding
    loss accounting of 2026-10-02): under the words a block only the long stretch places (an additive plan under the reach
    model's cap, which the operator's ruling of 2026-10-02 keeps for a block no plan under the loaded reach cap serves) is
    never bound, by either binding rule, while another listed block has the proven plan or one under the loaded reach cap,
    however much less base its plan names or however much nearer the pad it lies; and THE RULE ranks every such plan after
    the others. When every block with a placement is a long stretch, the cubes are read as they are."""

    LONG = ((0.040, -0.240), -92.5)  # beyond the capped plans' budget boundary: its long plan names less base
    NEAR = ((-0.040, -0.200), -52.5)

    def scene(self, long_first: bool, near_plan=None):
        long_decision = stretched(plan("advance_then_reach", 80.0, 91.0))
        near_decision = decision(near_plan or plan("advance_then_reach", 84.0, 26.0))
        cubes = [cube(*self.LONG, long_decision), cube(*self.NEAR, near_decision)]
        decisions = [long_decision, near_decision]
        return (cubes, decisions) if long_first else (cubes[::-1], decisions[::-1])

    def test_a_long_stretch_beside_a_plan_under_the_cap_is_set_aside_and_either_rule_binds_the_other(self):
        for near_plan in (plan("advance_then_reach", 84.0, 26.0), plan("proven", 112.0, 0.0)):
            for long_first in (True, False):
                cubes, decisions = self.scene(long_first, near_plan)
                near_index, long_index = (1, 0) if long_first else (0, 1)
                with self.subTest(near=near_plan.policy, long_first=long_first):
                    read, set_aside = bs.past_the_binding_tiers(cubes, decisions, RULES)
                    self.assertEqual(1, set_aside)
                    self.assertIsNone(read[long_index].mode)
                    self.assertEqual(cubes[near_index], read[near_index])
                    self.assertEqual(near_index, tb.select_for_execution(auto_target(), read, RULES)[1].index)
                    self.assertEqual(near_index, bs.select_for_execution(auto_target(), read, decisions, RULES)[1].index)

    def test_the_rule_ranks_a_long_stretch_after_every_other_plan_whatever_base_it_names(self):
        for near_plan in (plan("advance_then_reach", 84.0, 26.0), plan("proven", 112.0, 0.0)):
            for long_plan in (plan("advance_then_reach", 80.0, 91.0), plan("arm_reach", 0.0, 93.0)):
                long_decision, near_decision = stretched(long_plan), decision(near_plan)
                with self.subTest(near=near_plan.policy, long=long_plan.policy):
                    self.assertLess(long_plan.base_advance_mm, near_plan.base_advance_mm, "not vacuous: the long stretch names less base")
                    self.assertLess(bs.motion_key(1, cube(*self.NEAR, near_decision), near_decision), bs.motion_key(0, cube(*self.LONG, long_decision), long_decision))
                    for long_first in (True, False):
                        cubes = [cube(*self.LONG, long_decision), cube(*self.NEAR, near_decision)]
                        decisions = [long_decision, near_decision]
                        if not long_first:
                            cubes, decisions = cubes[::-1], decisions[::-1]
                        selection = bs.select_for_execution(auto_target(), cubes, decisions, RULES)[1]
                        self.assertEqual(self.NEAR[0], selection.seen_pad_m, "THE RULE alone, with nothing set aside")

    def test_when_every_placeable_candidate_is_a_long_stretch_the_cubes_are_read_as_they_are(self):
        first, second = stretched(plan("advance_then_reach", 80.0, 91.0)), stretched(plan("advance_then_reach", 73.0, 56.0))
        cubes = [cube(*self.LONG, first), cube(*self.NEAR, second)]
        self.assertEqual((tuple(cubes), 0), bs.past_the_binding_tiers(cubes, [first, second], RULES))
        self.assertEqual(1, bs.select_for_execution(auto_target(), cubes, [first, second], RULES)[1].index, "among long stretches, the least motion as before")
        self.assertEqual((tuple(cubes[:1]), 0), bs.past_the_binding_tiers(cubes[:1], [first], RULES), "one placeable block: as before")

    def test_a_block_on_the_pad_or_without_a_placement_sets_nothing_aside(self):
        refused = ps.Decision((), None, {"explanation": "none"})
        placed = cube((0.0, 0.0), 147.5, decision(plan("proven", 0.0)))
        nowhere = cube((0.1, -0.30), -150.0, refused)
        long_decision = stretched(plan("advance_then_reach", 80.0, 91.0))
        long_cube = cube(*self.LONG, long_decision)
        self.assertEqual(((placed, nowhere, long_cube), 0), bs.past_the_binding_tiers([placed, nowhere, long_cube], [decision(plan("proven", 0.0)), refused, long_decision], RULES))
        self.assertEqual(((nowhere, long_cube), 0), bs.past_the_binding_tiers([nowhere, long_cube], [None, long_decision], RULES))
        with self.assertRaises(ValueError):
            bs.past_the_binding_tiers([long_cube], [], RULES)

    def test_on_the_faces_the_verifiers_found_both_rules_bind_the_block_a_plan_under_the_cap_or_the_proven_policy_places(self):
        """The faces of verify r11 policy MF1 and safety MF1, on the controller's own planning (pure): a far block beyond the
        capped plans' budget boundary beside one the proven policy places (multi_block took the far one: 80 against 84 mm,
        78 against 80); a block by the left side edge on a reach axis turned 20 deg (the nearest-the-pad rule took it:
        margin -7.5 against -12.5, its long advance_then_reach 112 mm and a carry to 113 mm out); a block by the left edge
        that only the arm alone's long stretch places (0 mm of base, a carry to 126 mm out; both rules took it); and the
        safety lens's A4 beside FAR (78 against 90 mm here, the operator's line 25 mm beyond her block)."""
        faces = (("far, straight axis", (0.040, -0.240), (-0.040, -0.200), axis_deg(0.0), 0.0, 0.0),
                 ("far, straight axis, nearer", (0.040, -0.238), (-0.040, -0.196), axis_deg(0.0), 0.0, 0.0),
                 ("side edge, axis turned 20 deg", (-0.215, -0.150), (0.0, -0.160), axis_deg(20.0), 0.0, 0.0),
                 ("the arm alone's long stretch", (-0.160, -0.165), (-0.080, -0.200), axis_deg(0.0), 0.0, 0.0),
                 ("A4 beside FAR", (LIVE[0], -0.238), (0.0145, -0.1925), axis_deg(0.0), 0.0, 22.0))
        word_sets = (("arm_reach", "advance_then_reach"), ("arm_reach", "advance_then_reach", bs.WORD), ("advance_then_reach",))
        capped = cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM
        for words in word_sets:
            with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words):
                policies = ps.enabled(words)
                for name, a, b, axis, a_coarse, b_coarse in faces:
                    decisions = {a: ps.decide(cycle, pp.Situation(a, axis, a_coarse, False, ALLOWANCE), policies, cycle.placement_plan),
                                 b: ps.decide(cycle, pp.Situation(b, axis, b_coarse, False, ALLOWANCE), policies, cycle.placement_plan)}
                    with self.subTest(words=words, face=name):
                        long_plan, near_plan = decisions[a].selected, decisions[b].selected
                        self.assertTrue(decisions[a].long_stretch, "only the long stretch places A")
                        self.assertEqual((pp.reach_model.measured_out_cap_mm(cycle), False), (long_plan.values["reach_cap_mm"], decisions[a].situation.proven_admits))
                        self.assertFalse(decisions[b].long_stretch)
                        self.assertTrue(near_plan.policy == "proven" or near_plan.values["reach_cap_mm"] == capped, near_plan)
                        self.assertTrue(long_plan.base_advance_mm < near_plan.base_advance_mm or cycle.placed_margin_mm(a) > cycle.placed_margin_mm(b),
                                        "not vacuous: one binding rule took A before the fix")
                        for listed in ((a, b), (b, a)):
                            cubes = [tb.SeenCube(anchor, cycle.placed_margin_mm(anchor), decisions[anchor].selected.mode, None) for anchor in listed]
                            ordered = [decisions[anchor] for anchor in listed]
                            read, set_aside = bs.past_the_binding_tiers(cubes, ordered, RULES)
                            self.assertEqual(1, set_aside)
                            self.assertEqual(b, tuple(tb.select_for_execution(auto_target(), read, RULES)[1].seen_pad_m), ("nearest the pad", listed))
                            self.assertEqual(b, tuple(bs.select_for_execution(auto_target(), read, ordered, RULES)[1].seen_pad_m), ("least motion", listed))
                            self.assertEqual(b, tuple(bs.select_for_execution(auto_target(), cubes, ordered, RULES)[1].seen_pad_m), ("THE RULE alone", listed))


def seen(anchor, chosen: ps.Decision) -> tb.SeenCube:
    """A listed block at `anchor` with the placement mode of its decision, as the controller lists it at SEARCH."""
    return tb.SeenCube(tuple(anchor), cycle.placed_margin_mm(anchor), None if chosen.selected is None else chosen.selected.mode, None)


def all_bindings(case, cubes, decisions) -> tuple:
    """(what the nearest-the-pad rule binds, what THE RULE binds, what THE RULE binds with nothing set aside), each a seen
    position, after the binding tiers as the controller reads them (set aside: the count)."""
    read, set_aside = bs.past_the_binding_tiers(cubes, decisions, RULES)
    nearest = tuple(tb.select_for_execution(auto_target(), read, RULES)[1].seen_pad_m)
    least = tuple(bs.select_for_execution(auto_target(), read, decisions, RULES)[1].seen_pad_m)
    alone = tuple(bs.select_for_execution(auto_target(), cubes, decisions, RULES)[1].seen_pad_m)
    return nearest, least, alone, set_aside


class TheBindingTiers(unittest.TestCase):
    """The lead's decision of 2026-10-03 (block_selection THE BINDING TIERS; verify r12 safety MF1 and replay MUST-FIX 1; the
    binding loss accounting of 2026-10-02): among several listed blocks a block the proven policy refuses is bound only where
    no listed block is one the proven policy places, by either binding rule and by THE RULE alone, however much less base its
    plan under the loaded reach cap names and however much nearer the pad it lies; among the blocks the proven policy places
    the binding is the installed rule's, the block nearest the pad, whatever base the words' plans name (the lead's decision
    of 2026-10-04, TheInstalledRuleInsideTheFirstTier below); a long stretch stays the last resort; one block keeps its own
    plan, whatever its tier (the operator's ruling of 2026-10-02, Q8)."""

    PLACED, REFUSED, LONG = (-0.080, -0.2075), (0.0145, -0.160), (0.040, -0.150)

    def test_the_tiers_are_read_from_the_search_decision(self):
        self.assertEqual(("proven_admits", "capped_robust", "capped", "long_stretch"), bs.TIERS)
        cases = ((decision(plan("proven", 119.0, 0.0)), "proven_admits"), (decision(plan("advance_then_reach", 48.0, 26.0), plan("proven", 70.0, 0.0)), "proven_admits"),
                 (robust(plan("advance_then_reach", 100.0, 36.0)), "capped_robust"), (robust(plan("arm_reach", 0.0, 40.0)), "capped_robust"),
                 (decision(plan("advance_then_reach", 100.0, 36.0)), "capped"), (decision(plan("arm_reach", 0.0, 40.0)), "capped"),
                 (stretched(plan("advance_then_reach", 80.0, 91.0)), "long_stretch"))
        for chosen, wanted in cases:
            with self.subTest(wanted=wanted, policy=chosen.selected.policy):
                self.assertEqual(wanted, bs.tier_name(chosen))
                self.assertEqual(bs.TIERS.index(wanted), bs.tier(chosen))
                self.assertEqual(bs.tier(chosen), bs.motion_key(0, cube((0.0, -0.2), -50.0, chosen), chosen)[0], "THE RULE ranks by the tier first")

    def test_a_block_the_proven_policy_refuses_is_set_aside_beside_one_it_places_however_little_base_it_names(self):
        placed = decision(plan("proven", 119.0, 0.0))
        for refused in (decision(plan("advance_then_reach", 100.0, 36.0)), decision(plan("arm_reach", 0.0, 40.0)), stretched(plan("advance_then_reach", 80.0, 91.0))):
            for refused_first in (True, False):
                listed = [(self.REFUSED, refused), (self.PLACED, placed)]
                if not refused_first:
                    listed = listed[::-1]
                cubes, decisions = [seen(anchor, chosen) for anchor, chosen in listed], [chosen for _anchor, chosen in listed]
                with self.subTest(refused=bs.tier_name(refused), policy=refused.selected.policy, refused_first=refused_first):
                    self.assertGreater(cycle.placed_margin_mm(self.REFUSED), cycle.placed_margin_mm(self.PLACED), "not vacuous: the refused block lies nearer the pad")
                    self.assertLess(refused.selected.base_advance_mm, placed.selected.base_advance_mm, "not vacuous: its plan names less base")
                    self.assertEqual((self.PLACED, self.PLACED, self.PLACED, 1), all_bindings(self, cubes, decisions))
                    read, _set_aside = bs.past_the_binding_tiers(cubes, decisions, RULES)
                    self.assertIsNone(read[decisions.index(refused)].mode, "read without a placement")

    def test_on_the_r12_floors_both_rules_bind_the_block_the_proven_policy_places(self):
        """verify r12 safety MF1's floors V1 and V2 and verify r12 replay MUST-FIX 1's V3Y scenes, on the controller's own
        planning: A, a block the proven policy refuses that the words plan under the loaded reach cap (not a long stretch, so
        the long-stretch set-aside of 68e50d9 kept it), beside B, a block the proven policy places near its budget with a
        far-out coarse grasp. THE RULE took A by 1 to 4 mm of planned base, and once held past SEARCH's grasp charge A's plan
        went long and the run ended with the cube held on the stretched arm, where 31c546c places B."""
        floors = (("safety V1", (0.0145, -0.234), 0.0, (-0.080, -0.2075), 28.0, 0.0), ("safety V2", (0.0145, -0.2305), 5.0, (-0.080, -0.2065), 28.0, 0.0))
        floors += tuple((f"replay V3Y dx {dx} A {a_y} B {b_y} at {b_coarse}", (0.0145, a_y), 0.0, (-0.080, b_y), b_coarse, dx)
                        for dx in (0.0, 0.0616) for a_y in (-0.232, -0.234) for b_y in (-0.205, -0.2075) for b_coarse in (28.0, 30.0))
        word_sets = (("arm_reach", "advance_then_reach", bs.WORD), ("advance_then_reach", bs.WORD), ("arm_reach", "advance_then_reach"), ("advance_then_reach",))
        took_a = set()
        for words in word_sets:
            with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words):
                policies = ps.enabled(words)
                for name, a, a_coarse, b, b_coarse, dx in floors:
                    axis = (dx, math.sqrt(1.0 - dx * dx))
                    a_decision = ps.decide(cycle, pp.Situation(a, axis, a_coarse, False, ALLOWANCE), policies, cycle.placement_plan)
                    b_decision = ps.decide(cycle, pp.Situation(b, axis, b_coarse, False, ALLOWANCE), policies, cycle.placement_plan)
                    with self.subTest(words=words, floor=name):
                        self.assertEqual((False, True), (a_decision.situation.proven_admits, b_decision.situation.proven_admits))
                        self.assertEqual(("capped", "proven_admits"), (bs.tier_name(a_decision), bs.tier_name(b_decision)), "A is planned under the cap: no long stretch")
                        self.assertLessEqual(a_decision.selected.arm_out_mm, cycle.ARM_LOADED_REACH_OUT_MM - pp.reach_model.LOADED_SHORTFALL_MAX_MM)
                        if a_decision.selected.base_advance_mm < b_decision.selected.base_advance_mm:
                            took_a.add(name)
                        for listed in ((a, b), (b, a)):
                            chosen = {a: a_decision, b: b_decision}
                            cubes, decisions = [seen(anchor, chosen[anchor]) for anchor in listed], [chosen[anchor] for anchor in listed]
                            self.assertEqual((b, b, b, 1), all_bindings(self, cubes, decisions), listed)
        self.assertTrue({"safety V1", "safety V2"} <= took_a, "not vacuous: on both safety floors A's plan names less base than B's")

    def test_the_operators_block_beside_a_block_the_proven_policy_places_with_more_base_leaves_that_block_bound(self):
        """The tier binds here, not the tie band: under the words the operator's block of 2026-09-30 (the proven policy refuses
        it) is planned under the loaded reach cap with 97 mm of base, 21 to 23 mm less than the proven plan of verify r12
        safety V1's B (118 to 120 mm), so THE RULE took it; the installed controller binds B and places it, and so does every
        binding rule now."""
        b = (-0.080, -0.2075)
        for words in (("arm_reach", "advance_then_reach", bs.WORD), ("advance_then_reach", bs.WORD), ("arm_reach", "advance_then_reach")):
            with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words):
                policies = ps.enabled(words)
                for dx in (0.0, 0.0616, -0.0616):
                    axis = (dx, math.sqrt(1.0 - dx * dx))
                    live = ps.decide(cycle, pp.Situation(LIVE, axis, 0.0, False, ALLOWANCE), policies, cycle.placement_plan)
                    placed = ps.decide(cycle, pp.Situation(b, axis, 28.0, False, ALLOWANCE), policies, cycle.placement_plan)
                    with self.subTest(words=words, dx=dx):
                        self.assertEqual(("advance_then_reach", 97.0, "capped_robust"), (live.selected.policy, live.selected.base_advance_mm, bs.tier_name(live)))
                        self.assertEqual(("proven", "proven_admits"), (placed.selected.policy, bs.tier_name(placed)))
                        self.assertGreater(placed.selected.base_advance_mm - live.selected.base_advance_mm, TIE, "not vacuous: outside the tie band")
                        for listed in ((LIVE, b), (b, LIVE)):
                            chosen = {LIVE: live, b: placed}
                            cubes, decisions = [seen(anchor, chosen[anchor]) for anchor in listed], [chosen[anchor] for anchor in listed]
                            self.assertEqual((b, b, b, 1), all_bindings(self, cubes, decisions), listed)

    def test_among_blocks_the_proven_policy_places_the_nearest_the_pad_is_bound_whatever_base_their_plans_name(self):
        """The lead's decision of 2026-10-04: a plan of the first tier that names less base (an additive plan under the cap,
        48 mm) does not take the binding from the block nearer the pad whose proven plan names more (55 to 100 mm), inside the
        advance loop's tolerance of it or outside; both rules bind the nearer block, as the installed rule does."""
        quick = decision(plan("advance_then_reach", 48.0, 26.0), plan("proven", 70.0, 0.0))
        self.assertEqual(("advance_then_reach", "proven_admits"), (quick.selected.policy, bs.tier_name(quick)))
        for proven_advance in (55.0, 48.0 + TIE, 57.0, 100.0):
            slow = decision(plan("proven", proven_advance, 0.0))
            for quick_first in (True, False):
                listed = [((0.0145, -0.158), quick), ((-0.080, -0.1435), slow)]
                if not quick_first:
                    listed = listed[::-1]
                cubes, decisions = [seen(anchor, chosen) for anchor, chosen in listed], [chosen for _anchor, chosen in listed]
                with self.subTest(proven_advance=proven_advance, quick_first=quick_first):
                    index = bs.select_for_execution(auto_target(), cubes, decisions, RULES)[1].index
                    self.assertIs(slow, decisions[index])
                    self.assertEqual(tb.select_for_execution(auto_target(), cubes, RULES)[1].index, index, "the installed rule's block")
                    self.assertEqual(((index, cubes[index]), "nearest_the_pad"), bs.the_rule(list(enumerate(cubes)), decisions))

    def test_on_the_controllers_planning_the_nearer_block_is_bound_beside_arm_stage(self):
        """ARM_STAGE (advance_then_reach under the cap, 48 mm of base) beside a block the proven policy places 4 or 2 mm inside
        the near edge, grasped far out (55 and 57 mm of base): both rules bind the nearer block, as 31c546c does, in either
        listing order (the lead's decision of 2026-10-04; its tie band of 2026-10-03 bound ARM_STAGE at 57 mm)."""
        arm_stage = (0.0145, -0.158)
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("arm_reach", "advance_then_reach", bs.WORD)):
            policies = ps.enabled(cycle.PLACEMENT_ENVELOPE)
            quick = ps.decide(cycle, pp.Situation(arm_stage, (0.0, 1.0), 10.0, False, ALLOWANCE), policies, cycle.placement_plan)
            self.assertEqual(("advance_then_reach", 48.0, "proven_admits"), (quick.selected.policy, quick.selected.base_advance_mm, bs.tier_name(quick)))
            for near, advance in (((-0.080, -0.1435), 55.0), ((-0.080, -0.1455), 57.0)):
                slow = ps.decide(cycle, pp.Situation(near, (0.0, 1.0), 60.0, False, ALLOWANCE), policies, cycle.placement_plan)
                with self.subTest(near=near):
                    self.assertEqual(("proven", advance, "proven_admits"), (slow.selected.policy, slow.selected.base_advance_mm, bs.tier_name(slow)))
                    for listed in ((arm_stage, near), (near, arm_stage)):
                        chosen = {arm_stage: quick, near: slow}
                        cubes, decisions = [seen(anchor, chosen[anchor]) for anchor in listed], [chosen[anchor] for anchor in listed]
                        self.assertEqual(near, tuple(tb.select_for_execution(auto_target(), cubes, RULES)[1].seen_pad_m), "31c546c's rule takes the nearer block")
                        self.assertEqual((0, near), (bs.past_the_binding_tiers(cubes, decisions, RULES)[1], tuple(bs.select_for_execution(auto_target(), cubes, decisions, RULES)[1].seen_pad_m)))

    def test_several_blocks_of_later_tiers_are_all_set_aside_in_every_listing_order(self):
        """verify r12 gates should-fix 1 (its three-block scene): every block of a later tier is set aside, not the first one only."""
        placed = decision(plan("proven", 77.0, 0.0))
        capped = decision(plan("advance_then_reach", 60.0, 30.0))
        first_long, second_long = stretched(plan("advance_then_reach", 80.0, 91.0)), stretched(plan("advance_then_reach", 70.0, 90.0))
        scenes = (("two long stretches beside the proven plan", ((0.040, -0.240), first_long), ((-0.215, -0.150), second_long), ((0.0, -0.160), placed), (0.0, -0.160)),
                  ("two long stretches beside a plan under the cap", ((0.040, -0.240), first_long), ((-0.215, -0.150), second_long), ((0.0, -0.160), capped), (0.0, -0.160)),
                  ("a long stretch and a capped plan beside the proven plan", ((0.040, -0.240), first_long), ((0.0145, -0.150), capped), ((-0.080, -0.2075), placed), (-0.080, -0.2075)))
        for name, *blocks, wanted in scenes:
            for listed in itertools.permutations(blocks):
                cubes, decisions = [seen(anchor, chosen) for anchor, chosen in listed], [chosen for _anchor, chosen in listed]
                with self.subTest(scene=name, order=[anchor for anchor, _chosen in listed]):
                    self.assertEqual((wanted, wanted, wanted, 2), all_bindings(self, cubes, decisions))

    def test_among_blocks_the_proven_policy_refuses_a_robust_plan_under_the_cap_comes_first_and_the_long_stretch_last(self):
        """The lead's decision of 2026-10-03, its second clause: where no listed block is one the proven policy places, a block
        whose plan under the loaded reach cap still fits the budget at the record's largest grasp under-read (coarse + 30.5
        mm) is bound before one whose plan fits only inside SEARCH's 22 mm charge, however nearer the pad that one lies or
        however less base it names; a long stretch stays last."""
        firm, fragile, far = robust(plan("advance_then_reach", 111.0, 36.0)), decision(plan("advance_then_reach", 100.0, 11.0)), stretched(plan("advance_then_reach", 80.0, 91.0))
        firm_at, fragile_at, far_at = (-0.060, -0.2275), (0.0145, -0.160), (0.040, -0.150)
        scenes = (("robust beside capped", ((firm_at, firm), (fragile_at, fragile)), firm_at, 1), ("capped beside the long stretch", ((fragile_at, fragile), (far_at, far)), fragile_at, 1),
                  ("all three", ((firm_at, firm), (fragile_at, fragile), (far_at, far)), firm_at, 2))
        for name, blocks, wanted, set_aside in scenes:
            for listed in itertools.permutations(blocks):
                cubes, decisions = [seen(anchor, chosen) for anchor, chosen in listed], [chosen for _anchor, chosen in listed]
                with self.subTest(scene=name, order=[anchor for anchor, _chosen in listed]):
                    self.assertEqual((wanted, wanted, wanted, set_aside), all_bindings(self, cubes, decisions))
        self.assertGreater(cycle.placed_margin_mm(fragile_at), cycle.placed_margin_mm(firm_at), "not vacuous: the fragile plan's block lies nearer the pad")
        self.assertLess(fragile.selected.base_advance_mm, firm.selected.base_advance_mm, "and its plan names less base")

    def test_on_the_controllers_planning_the_robust_block_is_bound_where_the_nearest_the_pad_took_the_other(self):
        """Two blocks the proven policy refuses, on the straight axis: N 67.5 mm outside the near edge seen at coarse +25 mm,
        planned under the cap with 116 mm of base, whose plan under the cap no longer fits once held past SEARCH's charge (at
        the record's 30.5 mm, and at the World's 26.2); R 80 mm outside seen at coarse 0, 111 mm, still under the cap at the
        record's under-read. The nearest-the-pad rule took N; both rules now take R."""
        n, r = (0.0145, -0.215), (-0.060, -0.2275)
        for words in (("arm_reach", "advance_then_reach"), ("advance_then_reach",), ("arm_reach", "advance_then_reach", bs.WORD)):
            with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words):
                policies = ps.enabled(words)
                chosen = {n: ps.decide(cycle, pp.Situation(n, (0.0, 1.0), 25.0, False, ALLOWANCE), policies, cycle.placement_plan),
                          r: ps.decide(cycle, pp.Situation(r, (0.0, 1.0), 0.0, False, ALLOWANCE), policies, cycle.placement_plan)}
                with self.subTest(words=words):
                    self.assertEqual(((False, "capped", 116.0), (True, "capped_robust", 111.0)),
                                     tuple((chosen[block].capped_robust, bs.tier_name(chosen[block]), chosen[block].selected.base_advance_mm) for block in (n, r)))
                    for listed in ((n, r), (r, n)):
                        cubes, decisions = [seen(anchor, chosen[anchor]) for anchor in listed], [chosen[anchor] for anchor in listed]
                        self.assertEqual(n, tuple(tb.select_for_execution(auto_target(), cubes, RULES)[1].seen_pad_m), "not vacuous: nearest the pad, blind to the tiers")
                        self.assertEqual((r, r, r, 1), all_bindings(self, cubes, decisions), listed)

    def test_one_block_keeps_its_own_plan_whatever_its_tier(self):
        """The operator's ruling of 2026-10-02 (Q8) exactly: one block with a placement is bound with its own plan, beside a
        block on the pad or one without a placement."""
        on_pad = cube((0.0, 0.0), 147.5, decision(plan("proven", 0.0)))
        nowhere = cube((0.1, -0.30), -150.0, ps.Decision((), None, {"explanation": "none"}))
        for chosen in (decision(plan("proven", 119.0, 0.0)), robust(plan("advance_then_reach", 111.0, 36.0)), decision(plan("advance_then_reach", 116.0, 36.0)),
                       stretched(plan("advance_then_reach", 80.0, 91.0))):
            lone = seen(self.REFUSED, chosen)
            with self.subTest(tier=bs.tier_name(chosen)):
                for cubes, decisions in (([lone], [chosen]), ([on_pad, nowhere, lone], [decision(plan("proven", 0.0)), None, chosen])):
                    self.assertEqual((tuple(cubes), 0), bs.past_the_binding_tiers(cubes, decisions, RULES))
                    self.assertEqual(self.REFUSED, tuple(bs.select_for_execution(auto_target(), cubes, decisions, RULES)[1].seen_pad_m))
                    self.assertEqual(self.REFUSED, tuple(tb.select_for_execution(auto_target(), cubes, RULES)[1].seen_pad_m))


class TheTiersBeforeTheBands(unittest.TestCase):
    """The lead's decision of 2026-10-03 on every binding path (verify r13 gates MUST-FIX 1): the binding tiers read every
    listed candidate with a placement, the ones SEARCH's pickup bands refuse included, and the bands then read the first tier
    (past_the_tiers_and_the_bands, which the controller's mission binding calls). A block the bands refuse holds its tier:
    beside it every block of a later tier is set aside, and where the bands refuse every block of the first tier the cubes are
    read as they are, so the binding takes one of them and SEARCH refuses it with its band's own code before anything moves,
    as the installed controller binds and refuses a block it places that the bands refuse. Inside the first tier the bands set
    aside every block they refuse while another passes them (verify r8 replay MUST-FIX 3). Read the other way round (the
    bands first, e3756e3's order), the binding took a block of a later tier beside one the bands refuse."""

    BAND = "search_cube_beyond_return_bound"
    X, C, L = (0.150, -0.155), (0.0145, -0.160), (-0.0358, -0.238)  # X nearest the pad: the bands refuse it (its pan from home)
    P, R = (0.0, -0.1875), (0.0145, -0.230)

    def read(self, listed) -> tuple:
        """(cubes, decisions, bands) of `listed`, (anchor, decision, band answer) triples, in that order."""
        return [seen(anchor, chosen) for anchor, chosen, _band in listed], [chosen for _anchor, chosen, _band in listed], [band for _anchor, _chosen, band in listed]

    def bound(self, cubes, decisions, bands) -> tuple:
        """(what the nearest-the-pad rule binds, what THE RULE binds, the tiers' set-aside, the bands' set-aside) after the
        tiers and the bands, as the controller reads them."""
        read, tiered, set_aside = bs.past_the_tiers_and_the_bands(cubes, decisions, bands, RULES)
        return (tuple(tb.select_for_execution(auto_target(), read, RULES)[1].seen_pad_m), tuple(bs.select_for_execution(auto_target(), read, decisions, RULES)[1].seen_pad_m),
                tiered, set_aside)

    def bands_first(self, cubes, decisions, bands) -> tuple:
        read, _set_aside = bs.past_the_pickup_bands(cubes, bands, RULES)
        read, _tiered = bs.past_the_binding_tiers(read, decisions, RULES)
        return tuple(tb.select_for_execution(auto_target(), read, RULES)[1].seen_pad_m)

    def test_a_block_the_proven_policy_places_that_the_bands_refuse_sets_aside_every_later_tier(self):
        """Clause 1: a block the proven policy refuses is bound only where no listed block is one it places, the one the bands
        refuse included. The binding takes X, and SEARCH then refuses it before anything moves."""
        x, c, l = decision(plan("proven", 0.0, 85.0)), decision(plan("advance_then_reach", 60.0, 30.0)), stretched(plan("advance_then_reach", 78.0, 91.0))
        for listed in itertools.permutations(((self.X, x, self.BAND), (self.C, c, None), (self.L, l, None))):
            cubes, decisions, bands = self.read(listed)
            with self.subTest(order=[anchor for anchor, _chosen, _band in listed]):
                self.assertEqual(self.C, self.bands_first(cubes, decisions, bands), "not vacuous: read the other way round, the capped block is bound")
                self.assertEqual((self.X, self.X, 2, 0), self.bound(cubes, decisions, bands))

    def test_a_robust_block_under_the_cap_that_the_bands_refuse_sets_aside_a_long_stretch(self):
        """Clause 2: a robust plan under the cap comes before one only the long stretch serves, the one the bands refuse
        included (e3756e3's scene: the binding took the long stretch)."""
        a, l = robust(plan("advance_then_reach", 58.0, 36.0)), stretched(plan("advance_then_reach", 78.0, 91.0))
        for listed in itertools.permutations(((self.X, a, self.BAND), (self.L, l, None))):
            cubes, decisions, bands = self.read(listed)
            with self.subTest(order=[anchor for anchor, _chosen, _band in listed]):
                self.assertEqual(self.L, self.bands_first(cubes, decisions, bands), "not vacuous: read the other way round, the long stretch is bound")
                self.assertEqual((self.X, self.X, 1, 0), self.bound(cubes, decisions, bands))

    def test_inside_the_first_tier_the_bands_set_aside_the_block_they_refuse(self):
        """verify r8 replay MUST-FIX 3 inside the first tier: two blocks the proven policy places, the one nearer the pad refused
        by a band; both rules bind the other. A refused block of a later tier is set aside by its tier."""
        x, p = decision(plan("proven", 0.0, 85.0)), decision(plan("proven", 90.0, 0.0))
        for listed in itertools.permutations(((self.X, x, self.BAND), (self.P, p, None))):
            cubes, decisions, bands = self.read(listed)
            with self.subTest(order=[anchor for anchor, _chosen, _band in listed]):
                self.assertEqual((self.P, self.P, 0, 1), self.bound(cubes, decisions, bands))
        a = robust(plan("advance_then_reach", 58.0, 36.0))
        for listed in itertools.permutations(((self.X, a, self.BAND), (self.P, p, None))):
            cubes, decisions, bands = self.read(listed)
            with self.subTest(later=[anchor for anchor, _chosen, _band in listed]):
                self.assertEqual((self.P, self.P, 1, 0), self.bound(cubes, decisions, bands))

    def test_when_the_bands_refuse_every_block_of_the_first_tier_the_cubes_are_read_as_they_are(self):
        x, r = decision(plan("proven", 0.0, 85.0)), decision(plan("proven", 100.0, 0.0))
        l = stretched(plan("advance_then_reach", 78.0, 91.0))
        for listed in itertools.permutations(((self.X, x, self.BAND), (self.R, r, "search_cube_inside_pregrasp_reach"), (self.L, l, None))):
            cubes, decisions, bands = self.read(listed)
            with self.subTest(order=[anchor for anchor, _chosen, _band in listed]):
                read, tiered, set_aside = bs.past_the_tiers_and_the_bands(cubes, decisions, bands, RULES)
                self.assertEqual((1, 0), (tiered, set_aside))
                self.assertIsNone(read[decisions.index(l)].mode, "the long stretch set aside by its tier")
                self.assertEqual(self.X, tuple(tb.select_for_execution(auto_target(), read, RULES)[1].seen_pad_m), "the nearest the pad of the first tier")
        lone = [(self.X, x, self.BAND)]
        self.assertEqual((tuple(self.read(lone)[0]), 0, 0), bs.past_the_tiers_and_the_bands(*self.read(lone), RULES), "one block: as before")


def straight(anchor, cube_out, policies) -> tuple:
    """(SeenCube, Decision) for a block seen at SEARCH straight ahead, planned by the run's portfolio: the proven policy and
    `policies` (the additive ones the run's words name)."""
    chosen = ps.decide(cycle, pp.Situation(anchor, (0.0, 1.0), max(cube_out, 0.0), False, ALLOWANCE), policies, cycle.placement_plan)
    return seen(anchor, chosen), chosen


class TheInstalledRuleInsideTheFirstTier(unittest.TestCase):
    """The lead's decision of 2026-10-04 (block_selection THE RULE; verify r14 safety, policy and gates MF1, under the binding
    loss accounting of 2026-10-02): among two or more listed blocks the proven policy places, every binding binds exactly the
    block the installed controller's rule binds (target_binding's THE SELECTION RULE: the largest signed placed margin, then
    the listing order), whatever multi_block says, and names the rule (nearest_the_pad). Each floor the verifiers found holds
    A, a block the proven policy places with the arm alone at SEARCH's coarse grasp (0 mm of base), beside B, a block nearer
    the pad that it places with the base, the block 31c546c binds and places. The least motion took A, robust to the record's
    grasps or not, and the installed arm-only carry of A ended with the cube held under the recorded loaded models, where
    31c546c places B: F1, F4, F6 and P (verify r13 safety and policy: A's plan needs the base a few mm past its coarse grasp),
    AB_1 to AB_5 (verify r14 safety: A still the arm alone 30.5 mm past it) and PAIR (verify r14 policy's World floor). RF
    holds two arm-alone blocks, the one nearer the pad fragile, the other robust (the least motion took the robust one)."""

    # (A, its coarse grasp), (B, the block nearer the pad, its coarse grasp), each seen straight ahead
    FLOORS = {"F1": (((0.0145, -0.1575), 0.0), ((-0.08, -0.1525), 25.0)), "F4": (((0.0145, -0.1505), -10.0), ((-0.08, -0.1485), 25.0)),
              "F6": (((0.0145, -0.1535), -5.0), ((-0.08, -0.1495), 25.0)), "P": (((0.0145, -0.1615), 0.6), ((-0.06, -0.1525), 28.0)),
              "AB_1": (((0.0145, -0.1393), -5.5), ((-0.08, -0.1353), 45.0)), "AB_2": (((0.0145, -0.1379), 5.2), ((-0.08, -0.1359), 45.0)),
              "AB_3": (((0.0145, -0.1375), 0.0), ((-0.08, -0.1355), 44.0)), "AB_4": (((0.0145, -0.1435), 0.0), ((-0.08, -0.1405), 37.0)),
              "AB_5": (((0.0145, -0.1435), 0.0), ((-0.08, -0.1425), 34.0)), "PAIR": (((0.0, -0.1425), 0.0), ((0.075, -0.141), 36.0)),
              "RF": (((0.0145, -0.1375), 0.0), ((-0.08, -0.1325), 25.0))}
    WORD_SETS = ((bs.WORD,), ("arm_reach", "advance_then_reach", bs.WORD), ("advance_then_reach", bs.WORD), ("arm_reach", bs.WORD), ("arm_reach", "advance_then_reach"))

    def decided(self, words, *blocks) -> list:
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words):
            return [straight(anchor, cube_out, ps.enabled(words)) for anchor, cube_out in blocks]

    def test_each_floor_holds_an_arm_alone_beside_a_nearer_block_both_of_the_first_tier(self):
        for name, ((arm, arm_out), (base, base_out)) in self.FLOORS.items():
            for words in self.WORD_SETS:
                (_a, a_decision), (_b, b_decision) = self.decided(words, (arm, arm_out), (base, base_out))
                with self.subTest(floor=name, words=words):
                    self.assertEqual(("proven", "ARM_ONLY", 0.0), (a_decision.selected.policy, a_decision.selected.mode, a_decision.selected.base_advance_mm))
                    self.assertEqual(("proven_admits", "proven_admits"), (bs.tier_name(a_decision), bs.tier_name(b_decision)))
                    self.assertGreater(cycle.placed_margin_mm(base), cycle.placed_margin_mm(arm), "B lies nearer the pad")
                    if name != "RF":
                        self.assertEqual("BASE_ADVANCE", b_decision.selected.mode)
                        self.assertGreater(b_decision.selected.base_advance_mm, TIE, "not vacuous: B's plan moves the base past the loop's tolerance, A's does not")
                    else:
                        self.assertLess(b_decision.selected.fk_travel_mm, a_decision.selected.fk_travel_mm, "not vacuous: the least motion ranks A after B only by its arm travel")

    def test_every_binding_takes_the_block_the_installed_rule_takes_in_either_order_and_names_the_rule(self):
        for name, ((arm, arm_out), (base, base_out)) in self.FLOORS.items():
            for words in self.WORD_SETS:
                chosen = dict(zip((arm, base), (decision for _cube, decision in self.decided(words, (arm, arm_out), (base, base_out)))))
                for listed in ((arm, base), (base, arm)):
                    cubes, decisions = [seen(anchor, chosen[anchor]) for anchor in listed], [chosen[anchor] for anchor in listed]
                    with self.subTest(floor=name, words=words, listed=listed):
                        installed = tb.select_for_execution(auto_target(), cubes, RULES)
                        self.assertEqual(base, tuple(installed[1].seen_pad_m), "31c546c's rule takes the block nearer the pad")
                        self.assertEqual(installed, bs.select_for_execution(auto_target(), cubes, decisions, RULES))
                        self.assertEqual(tb.bind_targets(auto_target().targets, cubes, RULES), bs.bind_targets(auto_target().targets, cubes, decisions, RULES))
                        self.assertEqual((base, base, base, 0), all_bindings(self, cubes, decisions))
                        self.assertEqual(((installed[1].index, cubes[installed[1].index]), "nearest_the_pad"), bs.the_rule(list(enumerate(cubes)), decisions))
                        self.assertEqual("nearest_the_pad", bs.binding_rule(chosen[base]))

    def test_beside_a_block_of_a_later_tier_the_first_tier_is_still_the_installed_rules(self):
        """Three blocks: A and B of the first tier and a block the proven policy refuses, robust under the cap (a later tier):
        the later tier is set aside and the installed rule takes B, in every listing order."""
        later = (0.0145, -0.2125)
        for name in ("AB_2", "AB_5", "PAIR"):
            (arm, arm_out), (base, base_out) = self.FLOORS[name]
            words = ("arm_reach", "advance_then_reach", bs.WORD)
            chosen = dict(zip((arm, base, later), (decision for _cube, decision in self.decided(words, (arm, arm_out), (base, base_out), (later, 0.0)))))
            self.assertEqual("capped_robust", bs.tier_name(chosen[later]))
            for listed in itertools.permutations((arm, base, later)):
                cubes, decisions = [seen(anchor, chosen[anchor]) for anchor in listed], [chosen[anchor] for anchor in listed]
                with self.subTest(floor=name, listed=listed):
                    self.assertEqual((base, base, base, 1), all_bindings(self, cubes, decisions))

    def test_one_arm_alone_keeps_its_own_plan(self):
        """Q8 exactly: a single listed block is bound with its own plan, the proven arm alone included (the run then ends as the
        installed controller's does)."""
        for arm, arm_out in (self.FLOORS["P"][0], self.FLOORS["AB_5"][0]):
            for words in self.WORD_SETS:
                ((lone, chosen),) = self.decided(words, (arm, arm_out))
                with self.subTest(arm=arm, words=words):
                    selection = bs.select_for_execution(auto_target(), [lone], [chosen], RULES)[1]
                    self.assertEqual((arm, "ARM_ONLY"), (tuple(selection.seen_pad_m), selection.mode))
                    self.assertEqual(tb.select_for_execution(auto_target(), [lone], RULES), bs.select_for_execution(auto_target(), [lone], [chosen], RULES))


class TheLeastMotionAmongBlocksTheProvenPolicyRefuses(unittest.TestCase):
    """multi_block's least motion, in the tiers after the first (the lead's decision of 2026-10-04): where no listed block is one
    the proven policy places, among blocks of one tier the one whose plan moves the base least is bound, where the installed
    rule takes the one nearest the pad, and the binding says the least motion chose. On the controller's own planning: FAR, 65
    mm outside the near edge seen at the grasp radius (96 mm of base under the cap), and NEAR, 62.5 mm outside seen at coarse
    +20 (106 mm), both robust under the cap."""

    FAR, NEAR = ((0.0145, -0.2125), 0.0), ((-0.08, -0.21), 20.0)

    def test_the_least_motion_block_is_bound_in_either_order(self):
        for words in (("arm_reach", "advance_then_reach", bs.WORD), ("advance_then_reach", bs.WORD)):
            with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words):
                chosen = {anchor: straight(anchor, cube_out, ps.enabled(words))[1] for anchor, cube_out in (self.FAR, self.NEAR)}
            far, near = self.FAR[0], self.NEAR[0]
            with self.subTest(words=words):
                self.assertEqual(((96.0, "capped_robust"), (106.0, "capped_robust")), tuple((chosen[a].selected.base_advance_mm, bs.tier_name(chosen[a])) for a in (far, near)))
                self.assertGreater(cycle.placed_margin_mm(near), cycle.placed_margin_mm(far), "not vacuous: NEAR lies nearer the pad")
                for listed in ((far, near), (near, far)):
                    cubes, decisions = [seen(anchor, chosen[anchor]) for anchor in listed], [chosen[anchor] for anchor in listed]
                    self.assertEqual(near, tuple(tb.select_for_execution(auto_target(), cubes, RULES)[1].seen_pad_m), "the installed rule takes NEAR")
                    selection = bs.select_for_execution(auto_target(), cubes, decisions, RULES)[1]
                    self.assertEqual(far, tuple(selection.seen_pad_m), listed)
                    self.assertEqual(((selection.index, cubes[selection.index]), "least_motion"), bs.the_rule(list(enumerate(cubes)), decisions))
                    self.assertEqual("least_motion", bs.binding_rule(chosen[far]))


class TheProvenRefusalsStay(unittest.TestCase):
    def test_no_candidate_and_no_bounded_placement_are_refused_as_the_proven_binding_refuses(self):
        refused = ps.Decision((), None, {"explanation": "none"})
        scenes = ([cube((0.0, 0.0), 147.5, decision(plan("proven", 0.0)))], [cube((0.1, -0.30), -150.0, refused)],
                  [cube((0.1, -0.30), -150.0, refused), cube((-0.1, -0.31), -160.0, refused)], [])
        for cubes in scenes:
            decisions = [None] * len(cubes)
            with self.subTest(cubes=len(cubes)):
                with self.assertRaises(tb.TargetRefused) as proven:
                    tb.select_for_execution(auto_target(), cubes, RULES)
                with self.assertRaises(tb.TargetRefused) as ours:
                    bs.select_for_execution(auto_target(), cubes, decisions, RULES)
                self.assertEqual((proven.exception.reason_code, str(proven.exception), proven.exception.values), (ours.exception.reason_code, str(ours.exception), ours.exception.values))

    def test_declared_targets_sharing_a_mode_are_refused_as_before(self):
        targets = (tb.DeclaredTarget("a", False), tb.DeclaredTarget("b", False))
        cubes = [cube((0.0, -0.16), -12.5, decision(plan("proven", 0.0)))]
        with self.assertRaises(tb.TargetRefused) as ours:
            bs.select_for_execution(auto_target(targets), cubes, [None], RULES)
        self.assertEqual(tb.REFUSED_TARGETS_SHARE_A_MODE, ours.exception.reason_code)

    def test_with_one_candidate_the_binding_is_the_proven_one(self):
        one = decision(plan("proven", 76.0))
        cubes = [cube((0.0067, -0.1637), -16.2, one), cube((0.0, 0.0), 147.5, decision(plan("proven", 0.0)))]
        self.assertEqual(tb.select_for_execution(auto_target(), cubes, RULES), bs.select_for_execution(auto_target(), cubes, [one, None], RULES))


class AMissionWithADeclaredTargetIsTheProvenBindings(unittest.TestCase):
    """The lead's decision of 2026-10-01: the words apply to auto targets only. Whatever the blocks' decisions say, a mission
    with a declared target binds, selects and refuses exactly as target_binding does."""

    SLOW, QUICK, BASE = decision(plan("arm_reach", 0.0, 80.0)), decision(plan("arm_reach", 0.0, 40.0)), decision(plan("proven", 76.0))
    CUBES = [cube((0.0, -0.16), -12.5, SLOW), cube((0.05, -0.155), -7.5, QUICK), cube((-0.05, -0.19), -42.5, BASE)]

    def assertProven(self, target, cubes, decisions):
        try:
            proven = tb.select_for_execution(target, cubes, RULES)
        except tb.TargetRefused as refusal:
            with self.assertRaises(tb.TargetRefused) as ours:
                bs.select_for_execution(target, cubes, decisions, RULES)
            self.assertEqual((refusal.reason_code, str(refusal), refusal.values), (ours.exception.reason_code, str(ours.exception), ours.exception.values))
            return refusal.reason_code
        self.assertEqual(proven, bs.select_for_execution(target, cubes, decisions, RULES))
        if target.first_execution:
            self.assertEqual(tb.bind_targets(target.targets, cubes, RULES), bs.bind_targets(target.targets, cubes, decisions, RULES))
        return None

    def test_two_blocks_of_a_declared_mode_are_refused_as_not_unique_as_the_proven_binding_refuses_them(self):
        targets = (tb.DeclaredTarget("a", False), tb.DeclaredTarget("b", True))
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_UNIQUE, self.assertProven(auto_target(targets), self.CUBES, [self.SLOW, self.QUICK, self.BASE]))

    def test_a_mixed_mission_binds_its_auto_target_by_the_proven_selection_rule_not_by_the_least_motion(self):
        """The least motion would bind the auto target to the arm-alone block the declared target needs (verify r5 policy MF1):
        the proven rule binds the nearest feasible block, the one the base carries, and leaves the declared target its own."""
        near, near_plan = geometric((0.0, -0.155), 60.0)
        arm_only, arm_plan = geometric((0.05, -0.160), 0.0)
        cubes, decisions = [near, arm_only], [near_plan, arm_plan]
        for targets in ((tb.DeclaredTarget("a", tb.AUTO), tb.DeclaredTarget("b", False)), (tb.DeclaredTarget("a", False), tb.DeclaredTarget("b", tb.AUTO))):
            with self.subTest(targets=[target.requires_capability for target in targets]):
                self.assertIsNone(self.assertProven(auto_target(targets), cubes, decisions))
                bindings, _selection = bs.select_for_execution(auto_target(targets), cubes, decisions, RULES)
                self.assertEqual({0: "BASE_ADVANCE", 1: "ARM_ONLY"}, {binding.index: binding.mode for binding in bindings})

    def test_none_of_its_mode_is_refused_with_the_lone_blocks_limit_as_before(self):
        targets = (tb.DeclaredTarget("a", False),)
        refused = ps.Decision((), None, {"explanation": "none"})
        lone = tb.SeenCube((0.1, -0.30), -150.0, None, {"explanation": "it is far", "hint": "move it"})
        self.assertEqual(tb.REFUSED_NO_CANDIDATE, self.assertProven(auto_target(targets), [lone], [refused]))

    def test_a_later_execution_of_a_declared_target_selects_as_the_proven_binding_selects(self):
        targets = (tb.DeclaredTarget("a", False, (0.05, -0.155)), tb.DeclaredTarget("b", True, (-0.05, -0.19)))
        self.assertIsNone(self.assertProven(auto_target(targets, index=1, first=False, bound_pad_m=(-0.05, -0.19)), self.CUBES, [self.SLOW, self.QUICK, self.BASE]))


class ALaterExecution(unittest.TestCase):
    def test_selects_by_position_as_before(self):
        chosen = decision(plan("proven", 0.0))
        cubes = [cube((0.0, -0.16), -12.5, chosen), cube((0.05, -0.155), -7.5, chosen)]
        later = auto_target(first=False, bound_pad_m=(0.05, -0.155), mode="ARM_ONLY")
        self.assertEqual(tb.select_for_execution(later, cubes, RULES), bs.select_for_execution(later, cubes, [chosen, chosen], RULES))


class ThePolicy(unittest.TestCase):
    def test_it_is_one_unqualified_word_of_the_search_stage(self):
        self.assertEqual(("search", ("multi_block",), frozenset()), (bs.POLICY.stage, bs.POLICY.words, bs.POLICY.qualified))
        self.assertEqual("multi_block", bs.WORD)

    def test_it_never_imports_the_controller(self):
        source = (ROOT / "autonomy" / "manipulation" / "block_selection.py").read_text()
        self.assertNotIn("import run_single_cube_cycle", source)
        self.assertNotIn("from run_single_cube_cycle", source)


if __name__ == "__main__":
    unittest.main()
