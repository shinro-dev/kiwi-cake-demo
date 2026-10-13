#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_holonomic_placement.py: the pure planner of T6 step 3b (the lateral base pulse of the holonomic
# base, envelope word base_lateral, OFF). Nothing here moves: the module answers (advance, lateral) or None under a
# per-edge rule the executor hands it, and the executor takes the answer under the word only. No robot, no network.
# Run directly: python3 autonomy/tests/test_holonomic_placement.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))

import holonomic_placement as hp  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402

PAD = cycle.PAD_SIZE_M
PROVEN = tuple(cycle.required_edge_margins_mm((0.0, 1.0), 0.0))  # 58.96 on every edge under the default word
UP = (0.0, 1.0)
START_1, AXIS_1 = (-0.0201, -0.196), (-0.042, 0.999)
START_2, AXIS_2 = (-0.0936, -0.1698), (-0.086, 0.996)
START_3 = (0.163, -0.210)


def need(anchor, direction, rule=PROVEN):
    return hp.holonomic_need(anchor, direction, rule, PAD, cycle.BASE_MAX_TOTAL_ADVANCE_MM, cycle.LATERAL_MAX_TOTAL_MM)


class ThePlannerAgreesWithTheExecutor(unittest.TestCase):
    def test_the_edge_margins_are_the_executors_in_the_executors_order(self):
        for point in ((0.163, -0.210), (-0.05, 0.02), (0.0, 0.0)):
            self.assertEqual(tuple(round(v, 9) for v in cycle.edge_margins_mm(point)), tuple(round(v, 9) for v in hp.edge_margins_mm(point, PAD)))
        self.assertEqual(cycle.PAD_EDGE_NORMALS, hp.EDGE_NORMALS)

    def test_the_rule_built_from_the_executors_constants_is_the_proven_one_under_the_default_word(self):
        for value in PROVEN:
            self.assertAlmostEqual(cycle.PLACE_REQUIRED_CENTRE_MM + cycle.BASE_TARGET_BUFFER_MM + cycle.VISUAL_ANCHOR_UNCERTAINTY_MM, value, delta=1e-9)
        self.assertEqual(PROVEN, tuple(cycle.holonomic_rule(UP)))

    def test_the_executors_lateral_constants_are_the_designed_ones(self):
        self.assertEqual((0.05, cycle.BASE_PULSE_S, 50.0, 4, 120.0, 8.0, 8.0, 0.45),
                         (cycle.LATERAL_VY_MPS, cycle.LATERAL_PULSE_S, cycle.LATERAL_RATE_PRIOR_MM_S, cycle.LATERAL_MAX_PULSES, cycle.LATERAL_MAX_TOTAL_MM,
                          cycle.LATERAL_TOLERANCE_MM, cycle.LATERAL_MAX_ALONG_MM, cycle.LATERAL_MAX_AXIS_SLOPE))


class TheRecordedStartsAndTheirNeighbours(unittest.TestCase):
    def test_the_two_placed_runs_need_no_lateral_and_the_same_advance(self):
        self.assertEqual((108.0, 0.0), need(START_1, AXIS_1))
        self.assertEqual((82.0, 0.0), need(START_2, AXIS_2))

    def test_start_3_is_none_and_the_limits_name_the_along_axis_bound(self):
        self.assertIsNone(need(START_3, AXIS_1))
        limits = hp.holonomic_limits(START_3, AXIS_1, PROVEN, PAD, cycle.BASE_MAX_TOTAL_ADVANCE_MM, cycle.LATERAL_MAX_TOTAL_MM)
        self.assertAlmostEqual(121.6, limits["advance_needed_unbounded_mm"], delta=0.1)
        self.assertFalse(limits["advance_within_bound"])
        self.assertTrue(limits["lateral_within_bound"], "a lateral of about 10 mm would fix the side: the along-axis bound is what refuses this block")
        self.assertGreater(limits["lateral_needed_unbounded_mm"], 0.0)  # to the LEFT of the reach axis, toward the pad's centre line

    def test_a_block_beside_the_pad_at_a_reachable_depth_plans_an_advance_and_a_lateral_to_the_left(self):
        self.assertEqual((112.0, 14.5), need((0.163, -0.200), UP))
        self.assertEqual((112.0, -14.5), need((-0.163, -0.200), UP))  # mirrored: to the right
        self.assertEqual((112.0, 0.0), need((0.163, -0.200), UP, tuple(cycle.required_edge_margins_mm(UP, 0.0, projected=True))))  # the projected sides ask 43.96: no lateral

    def test_a_lateral_need_beyond_the_bound_is_none(self):
        self.assertIsNone(need((0.2075 + 0.070, -0.200), UP))  # 70 mm outside the right edge: 128.96 mm of lateral
        self.assertEqual((112.0, 120.0), need((0.2075 + 0.0610, -0.200), UP))  # 61 mm outside: 119.96, rounded 120, inside the bound
        limits = hp.holonomic_limits((0.2075 + 0.070, -0.200), UP, PROVEN, PAD, cycle.BASE_MAX_TOTAL_ADVANCE_MM, cycle.LATERAL_MAX_TOTAL_MM)
        self.assertTrue(limits["advance_within_bound"])
        self.assertFalse(limits["lateral_within_bound"])

    def test_a_point_already_admitted_needs_nothing(self):
        self.assertEqual((0.0, 0.0), need((0.0, 0.0), UP))

    def test_the_lateral_interval_follows_the_left_normal(self):
        low, high = hp.lateral_interval_mm((0.163, 0.0), UP, PROVEN, PAD)
        self.assertAlmostEqual(58.96 - 44.5, low, delta=0.01)  # the right edge asks 14.46 mm to the left
        self.assertAlmostEqual(370.5 - 58.96, high, delta=0.01)  # the left edge allows up to that far
        self.assertIsNone(hp.lateral_interval_mm((0.0, -0.200), UP, PROVEN, PAD), "the near edge is short and no lateral reaches it")
        low, high = hp.lateral_interval_mm((0.0, 0.163), (1.0, 0.0), PROVEN, PAD)  # the axis along x: the far edge is a side edge, n = (0, 1) points at it
        self.assertAlmostEqual(-(58.96 - (147.5 - 163.0)), high, delta=0.01)


if __name__ == "__main__":
    unittest.main()
