#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_placement_envelope.py: the placement envelope solver (track P of
# docs/autonomous-demo/overnight-plan-2026-09-26.md). Pure: the solver reads the controller's constants and geometry through
# the controller module handed to it, and nothing here moves. What is pinned: with no word the plan IS placement_plan's,
# swept over anchors and axes; every refusal names its binding constraint and the deficit in mm; a hint the solver marks as
# placing (hint_places_block), followed on the same reach axis, places the block by the controller's own placement_plan,
# swept, and start 3's (two constraints) and 21:17's (one) are such hints; a hint not so marked is only its binding
# constraint's move; every text a refusal records passes the controller's named-value sanitizer unchanged; the recorded
# refusals of 2026-09-26 read with their numbers; and no axis, the exactly diagonal one included, makes the explanation
# raise. No robot, no camera, no network.
# Run directly: python3 autonomy/tests/test_placement_envelope.py
import itertools
import math
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import placement_envelope as pe  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402

# The recorded anchors and the axis of start 1's PLACEMENT_PLAN (plan section 13; test_recorded_pins_20260926.py).
START_1, AXIS_1 = (-0.0201, -0.196), (-0.042, 0.999)
START_2, AXIS_2 = (-0.0936, -0.1698), (-0.086, 0.996)
START_3 = (0.163, -0.210)
LATE_2117 = (0.064, -0.220)
DIAGONAL = (math.sqrt(0.5), math.sqrt(0.5))  # |d_x| == |d_y| bit for bit
TEXT_SHAPE = re.compile(r"[A-Za-z0-9_.+ -]{1,96}")  # common#/$defs/named_value.value, the shape the record keeps whole
ALLOWANCE = cycle.PLACE_SIDE_DRIFT_ALLOWANCE_MM


def unit(deg: float) -> tuple:
    return (math.cos(math.radians(deg)), math.sin(math.radians(deg)))


def followed(anchor, direction, move) -> tuple:
    """The block moved as a hint_move says: sideways along the axis's left normal (-d_y, d_x), then along the axis, in mm."""
    sideways, along = move
    return (anchor[0] - direction[1] * sideways / 1000.0 + direction[0] * along / 1000.0, anchor[1] + direction[0] * sideways / 1000.0 + direction[1] * along / 1000.0)


SIDE_WORDS = re.compile(r"(\d+) mm (?:to the )?(left|right)")
ALONG_WORDS = re.compile(r"(\d+) mm (toward the pad|back toward the robot)")


def move_in_words(hint: str) -> tuple:
    """The move an operator reads from a hint's words, as (sideways_mm, along_mm): sideways positive to the left as seen
    from the robot looking along the reach axis, along positive toward the pad. Only the words are read."""
    side, along = SIDE_WORDS.search(hint), ALONG_WORDS.search(hint)
    sideways = 0 if side is None else int(side.group(1)) * (1 if side.group(2) == "left" else -1)
    ahead = 0 if along is None else int(along.group(1)) * (1 if along.group(2) == "toward the pad" else -1)
    return (sideways, ahead)


def robots_left(direction) -> tuple:
    """The robot's left as it looks along `direction`, seen from above in PAD_FRAME: +x runs toward the right edge and +y
    toward the far edge, so the frame is right-handed and the left is the direction turned a quarter counterclockwise."""
    left_edge, far_edge = cycle.PAD_EDGE_NORMALS[1], cycle.PAD_EDGE_NORMALS[2]
    assert (left_edge, far_edge) == ((-1.0, 0.0), (0.0, 1.0))
    return (-direction[1], direction[0])


def controller_plan(anchor, direction, grasp_out, words=()):
    with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", tuple(words)):
        try:
            return cycle.placement_plan(anchor, direction, grasp_out, ALLOWANCE)
        except cycle.CycleFailed:
            return None


ANCHORS = [(x / 1000.0, y / 1000.0) for x in (-190, -120, -40, 0, 60, 130, 175) for y in (-330, -260, -210, -175, -150, -100, 0, 90)]
AXES = [unit(90.0 + tilt) for tilt in (-35.0, -12.0, -2.4, 0.0, 4.9, 20.0, 44.0)] + [AXIS_1, AXIS_2, (1.0, 0.0)]


class WithNoWordThePlanIsPlacementPlan(unittest.TestCase):
    def test_the_plan_equals_placement_plan_over_anchors_axes_and_grasp_radii(self):
        for anchor, axis, grasp_out in itertools.product(ANCHORS, AXES, (0.0, 20.0)):
            with self.subTest(anchor=anchor, axis=axis, grasp_out=grasp_out):
                self.assertEqual(controller_plan(anchor, axis, grasp_out), pe.solve(cycle, anchor, axis, grasp_out)["plan"])

    def test_under_projected_the_plan_equals_placement_plan_under_projected(self):
        for anchor, axis in itertools.product(ANCHORS, AXES):
            with self.subTest(anchor=anchor, axis=axis):
                self.assertEqual(controller_plan(anchor, axis, 0.0, ("projected",)), pe.solve(cycle, anchor, axis, 0.0, ("projected",))["plan"])

    def test_a_feasible_answer_carries_the_plan_as_its_vector_and_no_explanation(self):
        answer = pe.solve(cycle, START_1, AXIS_1, 0.0)
        self.assertTrue(answer["feasible"])
        self.assertEqual(("BASE_ADVANCE", (108.0, 0.0), 0.0), (answer["mode"], answer["base_vector_mm"], answer["fk_travel_mm"]))
        self.assertIsNone(answer["constraint"])
        self.assertEqual({}, answer["values"])
        self.assertEqual(82.0, pe.solve(cycle, START_2, AXIS_2, 0.0)["base_vector_mm"][0])

    def test_the_refusal_values_are_the_controllers_on_every_axis_that_is_not_diagonal(self):
        for anchor, axis, projected in itertools.product(ANCHORS, AXES, (False, True)):
            with self.subTest(anchor=anchor, axis=axis, projected=projected):
                with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("projected",) if projected else ()):
                    expected = cycle.placement_refusal_values(anchor, axis, ALLOWANCE)
                got = pe.refusal_values(cycle, anchor, axis, ALLOWANCE, projected)
                self.assertEqual(set(expected), set(got))
                for key, value in expected.items():
                    if value is None:
                        self.assertIsNone(got[key], key)
                    else:
                        self.assertAlmostEqual(value, got[key], delta=1e-9, msg=key)


class TheExactlyDiagonalAxis(unittest.TestCase):
    """The verifier's note on _side_edges: a strict comparison would leave an exactly diagonal axis with no side edge, and
    a min() over the side edges nothing to read. The side edges are total and nothing the solver answers raises."""

    def test_every_edge_is_a_side_edge_and_the_side_numbers_exist(self):
        self.assertEqual((True, True, True, True), pe.side_edges(cycle, DIAGONAL))
        self.assertEqual(min(cycle.edge_margins_mm((0.0, 0.0))), pe.side_margin_mm(cycle, (0.0, 0.0), DIAGONAL))
        for projected in (False, True):
            values = pe.refusal_values(cycle, (0.0, 0.0), DIAGONAL, ALLOWANCE, projected)
            self.assertIsNone(values["required_axis_margin_mm"])
            self.assertIsNone(values["axis_advance_needed_unbounded_mm"])
            self.assertGreater(values["required_side_margin_mm"], 0.0)

    def test_the_solver_and_the_refusal_explanation_answer_on_it(self):
        for anchor in ((0.0, 0.0), (0.3, 0.3), (-0.25, -0.2)):
            for words in ((), ("projected",)):
                with self.subTest(anchor=anchor, words=words):
                    answer = pe.solve(cycle, anchor, DIAGONAL, 0.0, words)
                    self.assertEqual(controller_plan(anchor, DIAGONAL, 0.0, words), answer["plan"])
                    if not answer["feasible"]:
                        self.assertIn(answer["constraint"], pe.CONSTRAINTS)
                    values = pe.explain_refusal(cycle, anchor, DIAGONAL, 0.0, ALLOWANCE, words)
                    self.assertIn("side_margin_mm", values)
                    edges = pe.release_edge_margins(cycle, anchor, DIAGONAL, 5.0)
                    self.assertIsNone(edges["release_along_margin_mm"])


class EveryRefusalNamesItsLimit(unittest.TestCase):
    def test_start_3_is_refused_on_the_side_edge_with_the_along_budget_named_too(self):
        answer = pe.solve(cycle, START_3, AXIS_1, 0.0)
        self.assertFalse(answer["feasible"])
        self.assertEqual("side_edge", answer["constraint"])
        self.assertEqual(["side_edge", "along_budget"], [entry["constraint"] for entry in answer["constraints"]])
        values = answer["values"]
        self.assertAlmostEqual(44.5, values["side_margin_mm"], delta=0.01)
        self.assertAlmostEqual(58.96, values["required_side_margin_mm"], delta=0.01)
        self.assertAlmostEqual(121.6, values["axis_advance_needed_unbounded_mm"], delta=0.05)
        self.assertAlmostEqual(1.6, values["along_budget_deficit_mm"], delta=0.05)
        self.assertEqual("right", values["binding_edge"])
        # the side deficit is measured after the best bounded advance, which the 2.4 deg tilt of the axis grows a little
        self.assertAlmostEqual(58.96 - (44.5 + 0.042 * 120.0), answer["deficit_mm"], delta=0.05)
        self.assertIn("right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance", answer["sentence"])

    def test_the_recorded_deficits_are_at_0_1_mm(self):
        answer = pe.solve(cycle, START_3, AXIS_1, 0.0)
        self.assertNotEqual(9.4, answer["deficit_mm"])  # the solver's own number is not rounded
        self.assertEqual(9.4, answer["values"]["deficit_mm"])
        self.assertEqual(1.6, answer["values"]["along_budget_deficit_mm"])

    def test_2117_is_11_6_mm_past_the_along_axis_budget_with_the_hint(self):
        answer = pe.solve(cycle, LATE_2117, AXIS_1, 0.0)
        self.assertEqual(("along_budget", "move the block 12 mm toward the pad"), (answer["constraint"], answer["hint"]))
        self.assertAlmostEqual(11.6, answer["deficit_mm"], delta=0.05)
        self.assertIn("11.6 mm past the along-axis budget of 120 mm", answer["sentence"])

    def test_following_the_along_hint_places_the_block(self):
        answer = pe.solve(cycle, LATE_2117, AXIS_1, 0.0)
        self.assertEqual((0, 12), answer["hint_move"])
        self.assertTrue(answer["values"]["hint_places_block"])
        self.assertIsNotNone(controller_plan(followed(LATE_2117, AXIS_1, answer["hint_move"]), AXIS_1, 0.0))

    def test_start_3s_hint_states_both_moves_and_following_it_places_the_block(self):
        # Two constraints hold (the right edge and the along budget): the hint states the sideways move AND the move toward
        # the pad, and neither alone places the block.
        answer = pe.solve(cycle, START_3, AXIS_1, 0.0)
        self.assertEqual("move the block 10 mm to the left as seen from the robot and 3 mm toward the pad", answer["hint"])
        self.assertEqual((10, 3), move_in_words(answer["hint"]))
        self.assertEqual((10, 3), answer["hint_move"])
        self.assertTrue(answer["values"]["hint_places_block"])
        self.assertIsNotNone(controller_plan(followed(START_3, AXIS_1, (10, 3)), AXIS_1, 0.0))
        self.assertIsNone(controller_plan(followed(START_3, AXIS_1, (10, 0)), AXIS_1, 0.0))
        self.assertIsNone(controller_plan(followed(START_3, AXIS_1, (0, 3)), AXIS_1, 0.0))
        self.assertLess(followed(START_3, AXIS_1, (10, 0))[0], START_3[0])  # the robot's left here is toward the pad's middle

    def test_every_placing_hint_over_the_sweep_places_the_block_by_placement_plan(self):
        placing = 0
        for anchor, axis, words in itertools.product(ANCHORS, AXES + [DIAGONAL], ((), ("projected",))):
            answer = pe.solve(cycle, anchor, axis, 0.0, words)
            if answer["feasible"]:
                continue
            with self.subTest(anchor=anchor, axis=axis, words=words):
                self.assertEqual(answer["hint_move"] is not None, answer["values"]["hint_places_block"])
                if answer["hint_move"] is None:
                    continue
                placing += 1
                sideways, along = answer["hint_move"]
                for number in (abs(sideways), abs(along)):
                    if number:
                        self.assertIn(f"{number} mm", answer["hint"])
                target = followed(anchor, axis, answer["hint_move"])
                self.assertTrue(pe.solve(cycle, target, axis, 0.0, words)["feasible"])
                if not (axis == DIAGONAL and words):  # the one place the solver is stricter (module docstring, SIDE EDGES, TOTAL)
                    self.assertIsNotNone(controller_plan(target, axis, 0.0, words))
        self.assertGreater(placing, 100)

    def test_a_refusal_only_the_grid_holds_names_the_short_edge_not_a_zero_budget(self):
        # every limit met in the continuum, no 1 mm grid advance places it: the edge short at the best grid advance is named
        for anchor, tilt in (((-0.144, -0.175), 3.0), ((-0.123, -0.172), 17.0)):
            with self.subTest(anchor=anchor, tilt=tilt):
                answer = pe.solve(cycle, anchor, unit(90.0 + tilt), 0.0)
                self.assertFalse(answer["feasible"])
                self.assertEqual("side_edge", answer["constraint"])
                self.assertGreater(answer["deficit_mm"], 0.0)
                self.assertIn("on the 1 mm advance grid", answer["sentence"])
                self.assertTrue(answer["values"]["hint_places_block"])

    def test_a_block_past_the_far_edge_is_named_as_such_and_sent_back(self):
        answer = pe.solve(cycle, (0.0, 0.3), (0.0, 1.0), 0.0)
        self.assertEqual(("far_edge", "far"), (answer["constraint"], answer["values"]["binding_edge"]))
        self.assertAlmostEqual(300.0 - 147.5 + 58.96, answer["deficit_mm"], delta=0.01)
        self.assertEqual("move the block 212 mm back toward the robot", answer["hint"])
        self.assertIsNotNone(controller_plan(followed((0.0, 0.3), (0.0, 1.0), answer["hint_move"]), (0.0, 1.0), 0.0))

    def test_each_constraint_can_bind(self):
        cases = {
            "pickup_inside_grasp_radius": pe.solve(cycle, START_1, AXIS_1, 0.0, cube_out_mm=cycle.PREGRASP_MIN_CUBE_OUT_MM - 7.0),
            "lateral_needs_capability": pe.solve(cycle, (0.19, 0.0), (0.0, 1.0), 0.0),
            "side_edge": pe.solve(cycle, START_3, AXIS_1, 0.0),
            "along_budget": pe.solve(cycle, LATE_2117, AXIS_1, 0.0),
            "far_edge": pe.solve(cycle, (0.0, 0.3), (0.0, 1.0), 0.0),
            "arm_reach": pe.solve(cycle, START_1, AXIS_1, 0.0, base_allowed=False),
        }
        self.assertEqual(set(pe.CONSTRAINTS), set(cases))
        for constraint, answer in cases.items():
            with self.subTest(constraint):
                self.assertFalse(answer["feasible"])
                self.assertEqual(constraint, answer["constraint"])
                self.assertGreater(answer["deficit_mm"], 0.0)
                self.assertTrue(TEXT_SHAPE.fullmatch(answer["hint"]), answer["hint"])
                self.assertEqual(answer["hint"], answer["values"]["hint"])
        self.assertAlmostEqual(7.0, cases["pickup_inside_grasp_radius"]["deficit_mm"], delta=1e-9)
        self.assertIn(str(math.ceil(cycle.PREGRASP_OUTWARD_BIAS_MM - (cycle.PREGRASP_MIN_CUBE_OUT_MM - 7.0))), cases["pickup_inside_grasp_radius"]["hint"])

    def assert_recorded_whole(self, values: dict):
        """Every text value is in the record's shape and the controller's named-value sanitizer keeps it unchanged."""
        for name, value in values.items():
            self.assertTrue(value is None or isinstance(value, (str, bool, int, float)), name)
            if isinstance(value, str):
                self.assertTrue(TEXT_SHAPE.fullmatch(value), f"{name}: {value!r}")
                self.assertEqual(value, cycle._named(name, value)["value"], name)

    def test_every_text_over_the_sweep_is_recorded_whole(self):
        for anchor, axis, words, grasp_out in itertools.product(ANCHORS, AXES + [DIAGONAL], ((), ("projected",)), (0.0, 20.0)):
            answer = pe.solve(cycle, anchor, axis, grasp_out, words)
            if not answer["feasible"]:
                with self.subTest(anchor=anchor, axis=axis, words=words, grasp_out=grasp_out):
                    self.assertIn(answer["constraint"], pe.CONSTRAINTS)
                    self.assert_recorded_whole(answer["values"])
                    for entry in answer["constraints"]:
                        self.assert_recorded_whole({"hint": entry["hint"], "sentence": entry["sentence"]})

    def test_every_explanation_a_refusal_record_carries_is_recorded_whole(self):
        for anchor, axis in itertools.product(ANCHORS[::3], AXES[::2] + [DIAGONAL]):
            for words, cube_out in (((), None), (("projected",), None), ((), cycle.PREGRASP_MIN_CUBE_OUT_MM - 7.0)):
                with self.subTest(anchor=anchor, axis=axis, words=words, cube_out=cube_out):
                    self.assert_recorded_whole(pe.explain_refusal(cycle, anchor, axis, 0.0, ALLOWANCE, words, cube_out))
        for anchor in (START_3, LATE_2117):  # the recorded ones, the explanation longest before
            values = pe.explain_refusal(cycle, anchor, AXIS_1, 0.0, ALLOWANCE, ())
            self.assert_recorded_whole(values)
            self.assertTrue(values["explanation"])
        for extreme in ((5.0, -9.0), (-9.0, 5.0), (0.0, -40.0)):  # numbers far past any pad: the text still fits
            with self.subTest(extreme=extreme):
                self.assert_recorded_whole(pe.explain_refusal(cycle, extreme, AXIS_1, 0.0, ALLOWANCE, ()))
                self.assert_recorded_whole(pe.solve(cycle, extreme, AXIS_1, 0.0, base_allowed=False)["values"])


class TheModelWords(unittest.TestCase):
    """reach_after_advance and side_offset as configurations of the model; no controller path takes them from here."""

    def test_2117_is_admitted_under_reach_after_advance(self):
        answer = pe.solve(cycle, LATE_2117, AXIS_1, 0.0, ("reach_after_advance",))
        self.assertTrue(answer["feasible"])
        self.assertIsNone(answer["plan"])  # placement_plan still refuses it
        advance, travel = answer["base_vector_mm"][0], answer["fk_travel_mm"]
        self.assertLessEqual(advance, cycle.BASE_MAX_TOTAL_ADVANCE_MM)
        self.assertLessEqual(travel, cycle.ARM_LOADED_REACH_OUT_MM)
        self.assertEqual("BASE_ADVANCE", answer["mode"])
        self.assertEqual("projected+reach_after_advance", pe.explain_refusal(cycle, LATE_2117, AXIS_1, 0.0, ALLOWANCE, ())["model_admits_under"])

    def test_start_3_under_the_model_words_states_its_plan(self):
        answer = pe.solve(cycle, START_3, AXIS_1, 0.0, ("projected", "reach_after_advance", "side_offset"))
        self.assertTrue(answer["feasible"])
        self.assertLessEqual(answer["base_vector_mm"][0], cycle.BASE_MAX_TOTAL_ADVANCE_MM)
        self.assertLessEqual(answer["fk_travel_mm"], cycle.ARM_LOADED_REACH_OUT_MM)
        # and without reach_after_advance the side_offset model alone still states its limit
        alone = pe.solve(cycle, START_3, AXIS_1, 0.0, ("projected", "side_offset"))
        self.assertTrue(alone["feasible"] or alone["constraint"] in pe.CONSTRAINTS)

    def test_a_pan_budget_spent_leaves_side_offset_nothing(self):
        # the grasp 41 deg from home (start 3's pickup, INFERRED in the plan): no delta inside the 15 deg cap brings it back
        # inside the 25 deg return bound, so side_offset adds nothing to projected
        spent = pe.solve(cycle, START_3, AXIS_1, 0.0, ("projected", "side_offset"), pans=(41.0, 41.0, 0.0))
        plain = pe.solve(cycle, START_3, AXIS_1, 0.0, ("projected",))
        self.assertFalse(plain["feasible"])
        self.assertFalse(spent["feasible"])
        self.assertEqual(plain["constraint"], spent["constraint"])


class TheReleaseAdmissionPerEdge(unittest.TestCase):
    def test_the_smallest_edge_is_the_admissions_worst_margin(self):
        for anchor, axis, travel, extra in itertools.product(ANCHORS[::5], AXES[::3], (0.0, 12.0, 40.0), (0.0, 4.0)):
            with self.subTest(anchor=anchor, axis=axis, travel=travel, extra=extra):
                edges = pe.release_edge_margins(cycle, anchor, axis, travel, extra)
                per_edge = [edges[f"release_margin_{name}_mm"] for name in pe.EDGE_NAMES]
                self.assertAlmostEqual(cycle.release_margin_worst_mm(anchor, axis, travel) - extra, min(per_edge), delta=1e-9)
                self.assertEqual(pe.EDGE_NAMES[per_edge.index(min(per_edge))], edges["release_binding_edge"])



# Pad-corner anchors outside the pad with steep reach axes: there the robot's left or right can point away from the pad's
# middle, which is where words that always said "toward the middle" sent the operator the wrong way.
CORNER_ANCHORS = [(sx * x / 1000.0, sy * y / 1000.0) for sx in (-1, 1) for sy in (-1, 1) for x, y in ((285, 205), (260, 190), (240, 175), (300, 150), (215, 230))]


def corner_axes(anchor) -> list:
    """Axes within 30 deg of pointing from the block at the pad's middle, every 6 deg."""
    toward = math.degrees(math.atan2(-anchor[1], -anchor[0]))
    return [unit(toward + tilt) for tilt in range(-30, 31, 6)]


class TheHintsWordsAreTheCheckedMove(unittest.TestCase):
    def test_the_pad_corner_case_says_left_and_the_words_place_the_block(self):
        anchor, axis = (-0.285, -0.205), unit(37.0)
        answer = pe.solve(cycle, anchor, axis, 0.0)
        self.assertFalse(answer["feasible"])
        self.assertTrue(answer["values"]["hint_places_block"])
        move = move_in_words(answer["hint"])
        self.assertEqual(answer["hint_move"], move)
        self.assertGreater(move[0], 0)
        self.assertIn("left", answer["hint"])
        self.assertNotIn("middle", answer["hint"])
        # the robot's left here takes the block further from the pad's middle, and following the words places it
        side_only = followed(anchor, axis, (move[0], 0))
        self.assertGreater(math.hypot(*side_only), math.hypot(*anchor))
        self.assertIsNotNone(controller_plan(followed(anchor, axis, move), axis, 0.0))

    def test_the_left_of_the_words_is_the_robots_left(self):
        left = robots_left(AXIS_1)
        self.assertLess(left[0], 0.0)  # looking up the pad from the near side, the left is toward the left edge
        moved = followed(START_3, AXIS_1, (10, 0))
        self.assertAlmostEqual(START_3[0] + left[0] * 0.010, moved[0], places=9)
        self.assertAlmostEqual(START_3[1] + left[1] * 0.010, moved[1], places=9)

    def test_over_pad_corners_and_steep_axes_following_the_words_places_the_block(self):
        read, left, right = 0, 0, 0
        for anchor in CORNER_ANCHORS:
            for axis, words in itertools.product(corner_axes(anchor), ((), ("projected",))):
                answer = pe.solve(cycle, anchor, axis, 0.0, words)
                if answer["feasible"] or answer["hint_move"] is None:
                    continue
                with self.subTest(anchor=anchor, axis=axis, words=words):
                    move = move_in_words(answer["hint"])
                    self.assertEqual(answer["hint_move"], move)
                    self.assertIsNotNone(controller_plan(followed(anchor, axis, move), axis, 0.0, words))
                    read += 1
                    left += move[0] > 0
                    right += move[0] < 0
        self.assertGreater(read, 50)
        self.assertGreater(left, 5)
        self.assertGreater(right, 5)

    def test_every_hint_the_solver_calls_placing_places_the_block_as_its_words_say(self):
        for anchor, axis in itertools.product(ANCHORS, AXES):
            answer = pe.solve(cycle, anchor, axis, 0.0)
            if answer["feasible"] or answer["hint_move"] is None:
                continue
            with self.subTest(anchor=anchor, axis=axis):
                self.assertEqual(answer["hint_move"], move_in_words(answer["hint"]))

if __name__ == "__main__":
    unittest.main()
