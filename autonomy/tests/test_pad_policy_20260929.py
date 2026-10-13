#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_pad_policy_20260929.py: the operator's pad policy of 2026-09-29 (docs/autonomous-demo/decision-log.md),
# pinned on the mission layer of the single-cube controller:
#
#   "Human will only place blocks off the pad. Human can subtract blocks from the pad. Robot should not care about the
#    items on the pad after dropping them; the objective is to search outside the pad for all blocks and place them within
#    the pad."
#
# What is pinned:
#   - SEARCH: a candidate is a block whose PAD_FRAME centre is NOT on the pad by the proven PLACED margin; every block on
#     the pad, real or a print the recognizer took for one, is never a candidate and never occupied. Three ways a block
#     on the pad still fails a run are named residuals, pinned here: the listing budget (passes on blocks on the pad are
#     free up to nine), the SEARCH SWEEP (it does not run when the low-pose frame verifies a block on the pad, so SEARCH
#     refuses mission_binding_no_candidate) and ALIGN (align_more_than_one_cube_verified counts blocks on the pad in the
#     pregrasp view). Among several off-pad blocks an auto target takes the NEAREST FEASIBLE one: a bounded placement
#     mode, then the smallest distance outside the pad (the largest signed margin), then the listing order;
#   - PLACED: the EXIT view shows a block on the pad (the proven PLACED margin) within RELEASE_AGREEMENT_MM of THIS run's
#     own release segment (plus the sideways offset's uncertainty when one was taken). Other blocks on the pad are
#     ignored: no uniqueness, no earlier target disturbed, no occupied bookkeeping. No block on the pad at the release is
#     refused as mission_placed_no_cube_at_release_point. Operator ruling (ii) of 2026-09-29: "(ii) credit any block on the
#     pad near this run's release point, whatever was there before". What SEARCH listed on the pad is not consulted and
#     no region of the pad (the printed logo's) is excluded, so removing blocks from the pad never fails a run; THE
#     ACCEPTED RESIDUAL (a false PLACED when this run's block is not on the pad at EXIT for any reason after
#     OBJECT_SECURED, given an admitted release, and another block, or the printed logo, stands within the tolerance of the
#     release) is pinned;
#   - the two-cube mission-only rules are gone from the module (claims, occupied positions, the undisturbed matching);
#   - the live case (run console-20260928T174207Z-m915d3e9) replays as PLACED from its own recorded frames with the printed
#     logo still in the SEARCH listing (skipped without KC_EVIDENCE_ROOT, like test_replay_20260926.py).
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_pad_policy_20260929.py
import inspect
import json
import math
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("autonomy/perception", "autonomy/manipulation", "autonomy/tests", "autonomy/motion", "bin/laptop/manipulation"):
    if str(ROOT / _sub) not in sys.path:
        sys.path.insert(0, str(ROOT / _sub))

import run_single_cube_cycle as cycle  # noqa: E402
import target_binding as tb  # noqa: E402
import test_cycle_mission_mode as mm  # noqa: E402  (the scripted World running main() with a mission target)

NEAR_EDGE_Y = -cycle.PAD_SIZE_M[1] / 2.0
UP = (0.0, 1.0)
GRASP_OUT_MM = cycle.LOW_POSE_REF_GRASP["out_mm"]
RULES = tb.Rules(placed_min_margin_mm=cycle.PLACED_MIN_EDGE_MARGIN_MM, r_claim_mm=tb.CUBE_HALF_EDGE_MM, r_match_mm=tb.CUBE_HALF_EDGE_MM)
AUTO = (tb.DeclaredTarget("cube", tb.AUTO),)

EDGE = (0.020, NEAR_EDGE_Y + 0.006)  # inside the outline by less than the half-diagonal: OFF the pad by the PLACED margin, ARM_ONLY
NEAR_OUT = (-0.060, NEAR_EDGE_Y - 0.030)  # 30 mm outside the near edge: BASE_ADVANCE
FAR_OUT = (0.080, NEAR_EDGE_Y - 0.045)  # 45 mm outside: BASE_ADVANCE, farther from the pad than NEAR_OUT
UNREACHABLE = (0.0, NEAR_EDGE_Y - 0.200)  # no bounded placement
LOGO = (-0.0611, -0.1122)  # the printed logo the live run listed as a placed cube (its TARGET_SELECTED occupied_pad_m)
ON_PAD = (0.050, 0.020)


def seen(point, offset_mm=(0.0, 0.0), with_mode=True):
    """One listed block through the proven functions: its margin and, at SEARCH, its placement_plan mode."""
    centre = (point[0] + offset_mm[0] / 1000.0, point[1] + offset_mm[1] / 1000.0)
    mode = None
    if with_mode:
        try:
            mode = cycle.placement_plan(centre, UP, GRASP_OUT_MM)["mode"]
        except cycle.CycleFailed:
            mode = None
    return tb.SeenCube(centre, cycle.placed_margin_mm(centre), mode)


def release_at(point):
    """A release segment of zero length at `point` (an FK travel of zero)."""
    return cycle.release_segment_pad(point, UP, 0.0)


# =====================================================================================================================
class SearchIgnoresEveryBlockOnThePad(unittest.TestCase):
    def test_a_candidate_is_exactly_a_block_that_is_not_on_the_pad(self):
        for point, candidate in ((EDGE, True), (NEAR_OUT, True), (UNREACHABLE, True), (LOGO, False), (ON_PAD, False)):
            with self.subTest(point=point):
                self.assertEqual(candidate, tb.is_candidate(seen(point), RULES))
                self.assertEqual(not candidate, tb.is_placed(seen(point), RULES))
        self.assertFalse(tb.is_candidate(tb.SeenCube(None), RULES), "a block without a PAD_FRAME centre is nobody's candidate")

    def test_the_phantom_on_the_pad_never_competes_with_the_real_block(self):
        for cubes, index in (([seen(LOGO), seen(NEAR_OUT)], 1), ([seen(NEAR_OUT), seen(LOGO)], 0), ([seen(ON_PAD), seen(LOGO), seen(EDGE)], 2)):
            with self.subTest(order=[cube.center_pad_m for cube in cubes]):
                (binding,) = tb.bind_targets(AUTO, cubes, RULES)
                self.assertEqual(index, binding.index)

    def test_blocks_on_the_pad_alone_are_no_candidate(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(AUTO, [seen(LOGO), seen(ON_PAD)], RULES)
        self.assertEqual(tb.REFUSED_NO_CANDIDATE, refused.exception.reason_code)
        self.assertEqual((0, 2), (refused.exception.values["candidates"], refused.exception.values["placed_cubes"]))

    def test_the_nearest_feasible_off_pad_block_is_taken_whatever_the_listing_order(self):
        for cubes, expected in (([seen(FAR_OUT), seen(NEAR_OUT)], NEAR_OUT), ([seen(NEAR_OUT), seen(FAR_OUT)], NEAR_OUT),
                                ([seen(NEAR_OUT), seen(EDGE)], EDGE), ([seen(UNREACHABLE), seen(FAR_OUT)], FAR_OUT),
                                ([seen(FAR_OUT), seen(LOGO), seen(UNREACHABLE), seen(NEAR_OUT)], NEAR_OUT)):
            with self.subTest(expected=expected):
                (binding,) = tb.bind_targets(AUTO, cubes, RULES)
                self.assertEqual(expected, binding.bound_pad_m)
                self.assertEqual(cubes[binding.index].mode, binding.mode)

    def test_off_pad_blocks_with_no_bounded_placement_are_refused_as_not_placeable(self):
        with self.assertRaises(tb.TargetRefused) as refused:
            tb.bind_targets(AUTO, [seen(UNREACHABLE), seen((0.05, NEAR_EDGE_Y - 0.210)), seen(LOGO)], RULES)
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_PLACEABLE, refused.exception.reason_code)
        self.assertEqual((2, 0), (refused.exception.values["candidates"], refused.exception.values["feasible_candidates"]))

    def test_the_two_cube_mission_only_rules_are_retired(self):
        for name in ("is_claimed", "occupied_positions", "earlier_targets_undisturbed", "PLACED_NO_UNCLAIMED_CUBE", "PLACED_NOT_UNIQUE",
                     "PLACED_EARLIER_TARGET_DISTURBED"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(tb, name), f"{name} belongs to the rule the ruling of 2026-09-29 retired")


class PlacedIsTheBlockAtThisRunsRelease(unittest.TestCase):
    def test_the_own_block_at_the_release_is_placed_beside_a_phantom_and_other_blocks(self):
        own = (0.004, -0.100)
        for cubes in ([seen(own, with_mode=False)], [seen(LOGO, with_mode=False), seen(own, with_mode=False)],
                      [seen(ON_PAD, with_mode=False), seen(own, with_mode=False), seen(LOGO, with_mode=False), seen(NEAR_OUT, with_mode=False)]):
            with self.subTest(blocks=len(cubes)):
                placement = tb.attribute_placement(cubes, release_at(own), RULES)
                self.assertEqual((True, own, None), (placement.placed, placement.placed_pad_m, placement.reason_code))
                self.assertEqual(cubes[placement.placed_index].center_pad_m, own)
                self.assertEqual(sum(tb.is_placed(cube, RULES) for cube in cubes), placement.on_pad_count)
                self.assertAlmostEqual(0.0, placement.release_distance_mm)
                self.assertEqual(tb.RELEASE_AGREEMENT_MM, placement.release_tolerance_mm)

    def test_a_block_that_fell_off_the_pad_is_refused_whatever_else_lies_on_it(self):
        release = (0.004, NEAR_EDGE_Y + 0.030)
        fell = (0.004, NEAR_EDGE_Y - 0.010)  # 10 mm outside the near edge, 40 mm from the release
        for cubes in ([seen(fell, with_mode=False)], [seen(LOGO, with_mode=False), seen(fell, with_mode=False)], [seen(ON_PAD, with_mode=False)], []):
            with self.subTest(blocks=[cube.center_pad_m for cube in cubes]):
                placement = tb.attribute_placement(cubes, release_at(release), RULES)
                self.assertEqual((False, None, tb.PLACED_NO_CUBE_AT_RELEASE), (placement.placed, placement.placed_pad_m, placement.reason_code))
        self.assertEqual("mission_placed_no_cube_at_release_point", tb.PLACED_NO_CUBE_AT_RELEASE)

    def test_a_block_on_the_edge_short_of_the_placed_margin_is_not_placed_even_at_the_release(self):
        edge = (0.004, NEAR_EDGE_Y + 0.010)  # inside the outline, 10 mm from the edge: under the half-diagonal
        placement = tb.attribute_placement([seen(edge, with_mode=False)], release_at(edge), RULES)
        self.assertEqual((False, tb.PLACED_NO_CUBE_AT_RELEASE, 0), (placement.placed, placement.reason_code, placement.on_pad_count))

    def test_a_human_removing_other_blocks_from_the_pad_never_fails_a_run(self):
        own = (-0.009, -0.101)
        before = [seen(LOGO, with_mode=False), seen(ON_PAD, with_mode=False), seen(own, with_mode=False)]
        for kept in ([0, 1, 2], [1, 2], [0, 2], [2]):
            with self.subTest(kept=kept):
                placement = tb.attribute_placement([before[i] for i in kept], release_at(own), RULES)
                self.assertTrue(placement.placed)

    def test_no_uniqueness_the_nearest_on_pad_block_to_the_release_is_credited(self):
        release = (0.0, -0.100)
        near, beside = seen((0.0, -0.110), with_mode=False), seen((0.0, -0.080), with_mode=False)  # 10 and 20 mm from the release, 30 mm apart
        placement = tb.attribute_placement([beside, near], release_at(release), RULES)
        self.assertEqual((True, 1, 2), (placement.placed, placement.placed_index, placement.on_pad_count))
        self.assertAlmostEqual(10.0, placement.release_distance_mm, places=6)

    def test_the_tolerance_bounds_the_agreement_and_the_offset_uncertainty_widens_it(self):
        release = (0.0, -0.090)
        inside = seen((0.0, -0.090 + (tb.RELEASE_AGREEMENT_MM - 0.01) / 1000.0), with_mode=False)
        beyond = seen((0.0, -0.090 + (tb.RELEASE_AGREEMENT_MM + 0.01) / 1000.0), with_mode=False)
        self.assertTrue(tb.is_placed(inside, RULES) and tb.is_placed(beyond, RULES))
        self.assertTrue(tb.attribute_placement([inside], release_at(release), RULES).placed)
        refused = tb.attribute_placement([beyond], release_at(release), RULES)
        self.assertEqual((False, tb.PLACED_NO_CUBE_AT_RELEASE, 1), (refused.placed, refused.reason_code, refused.on_pad_count))
        self.assertGreater(refused.release_distance_mm, tb.RELEASE_AGREEMENT_MM, "the refusal names the nearest on-pad block's distance")
        widened = tb.attribute_placement([beyond], release_at(release), RULES, release_uncertainty_mm=cycle.SIDE_OFFSET_UNCERTAINTY_MM)
        self.assertEqual((True, tb.RELEASE_AGREEMENT_MM + cycle.SIDE_OFFSET_UNCERTAINTY_MM), (widened.placed, widened.release_tolerance_mm))

    def test_the_release_is_a_segment_and_a_malformed_one_is_a_programming_error(self):
        a, b = (0.0, -0.100), (0.0, -0.090)
        mid = seen((0.005, -0.095), with_mode=False)
        self.assertAlmostEqual(5.0, tb.attribute_placement([mid], (a, b), RULES).release_distance_mm, places=6)
        for bad in (None, (a,), (a, (math.nan, 0.0)), ((0.0, 0.0), (0.0, 0.0), (0.0, 0.0))):
            with self.subTest(release=bad), self.assertRaises(ValueError):
                tb.attribute_placement([mid], bad, RULES)

    def test_the_release_segment_is_the_one_the_admission_takes_its_worst_margin_over(self):
        anchor, direction, travel = (0.0027, -0.0876), (-0.034, 0.999), -13.3
        ends = cycle.release_segment_pad(anchor, direction, travel)
        worst = min(cycle.placed_margin_mm(point) for point in ends) - cycle.VISUAL_ANCHOR_UNCERTAINTY_MM
        self.assertAlmostEqual(cycle.release_margin_worst_mm(anchor, direction, travel), worst, places=9)
        self.assertAlmostEqual(travel / cycle.FK_TRAVEL_OVERSTATEMENT, (ends[1][1] - anchor[1]) * 1000.0 / direction[1], places=6)


class RulingIICreditsAnyBlockOnThePadAtTheRelease(unittest.TestCase):
    """The operator's second ruling of 2026-09-29, answering Q1 and Q2: "(ii) credit any block on the pad near this run's
    release point, whatever was there before". PLACED credits the block on the pad nearest this run's release segment
    within the tolerance; what SEARCH listed on the pad is not consulted, and there is no region around the printed logo.
    Removing blocks from the pad can never fail a run. THE ACCEPTED RESIDUAL (a false PLACED when this run's block is not on
    the pad at EXIT for any reason after OBJECT_SECURED, given an admitted release, and another block, or the printed logo,
    stands within the tolerance) is pinned here."""

    FELL = (0.004, NEAR_EDGE_Y - 0.010)  # the own block, 10 mm outside the near edge
    RETIRED_CODE = "mission_placed_only_a_block_" + "listed_at_search"  # spelled in two parts so that the source scan below does not find this file

    def test_attribute_placement_takes_no_search_listing(self):
        self.assertEqual(["cubes", "release", "rules", "release_uncertainty_mm"], list(inspect.signature(tb.attribute_placement).parameters))
        self.assertNotIn("on_pad_at_search", cycle._mission_record())

    def test_the_code_for_a_block_listed_at_search_is_gone_because_nothing_writes_it(self):
        self.assertFalse(any(name == "PLACED_ONLY_A_BLOCK_LISTED_AT_SEARCH" for name in dir(tb)))
        sources = [path for sub in ("autonomy", "bin", "packages", "apps/console-api") for path in (ROOT / sub).rglob("*.py")]
        self.assertGreater(len(sources), 50, "not vacuous: the scan reads the source tree")
        writers = [str(path.relative_to(ROOT)) for path in sources if self.RETIRED_CODE in path.read_text(errors="replace")]
        self.assertEqual([], writers)

    def test_the_own_block_on_the_spot_of_a_block_the_human_took_away_is_credited(self):
        """Q2: SEARCH listed a block on the pad, the human took it away, and this run's block landed within the match
        radius of where it stood. Credited: removing blocks from the pad never fails a run."""
        spot = (-0.061, -0.110)
        own = (spot[0] + 0.006, spot[1])
        placement = tb.attribute_placement([seen(own, with_mode=False)], release_at(spot), RULES)
        self.assertEqual((True, own, None), (placement.placed, placement.placed_pad_m, placement.reason_code))

    def test_the_own_block_on_the_printed_logo_is_credited_there_is_no_logo_region(self):
        own = (LOGO[0] + 0.003, LOGO[1] + 0.002)
        placement = tb.attribute_placement([seen(own, with_mode=False)], release_at(LOGO), RULES)
        self.assertEqual((True, own, None), (placement.placed, placement.placed_pad_m, placement.reason_code))

    def test_removing_blocks_from_the_pad_never_fails_the_run(self):
        own = (-0.009, -0.101)
        for others in ([], [LOGO], [ON_PAD], [LOGO, ON_PAD, (0.030, -0.060)]):
            with self.subTest(still_on_the_pad=others):
                cubes = [seen(point, with_mode=False) for point in others] + [seen(own, with_mode=False)]
                placement = tb.attribute_placement(cubes, release_at(own), RULES)
                self.assertEqual((True, own, None), (placement.placed, placement.placed_pad_m, placement.reason_code))

    def test_the_own_block_is_credited_beside_an_earlier_block(self):
        release = (0.0, -0.100)
        earlier, own = (-0.030, -0.100), (0.004, -0.100)
        placement = tb.attribute_placement([seen(earlier, with_mode=False), seen(own, with_mode=False)], release_at(release), RULES)
        self.assertEqual((True, 1, own, 2), (placement.placed, placement.placed_index, placement.placed_pad_m, placement.on_pad_count))

    def test_accepted_residual_the_logo_at_the_release_is_credited_when_the_own_block_fell_off(self):
        """THE ACCEPTED RESIDUAL (operator ruling (ii), 2026-09-29), pinned so that it is not forgotten: this run's block left
        the pad after an admitted release and the printed logo, read as a block, stands within the tolerance of the release.
        The logo is credited: a false PLACED, whether or not SEARCH listed it."""
        release = (-0.058, -0.100)  # a release whose segment passes 11.9 mm from the logo: inside the 26.0 mm tolerance
        self.assertLess(tb.distance_mm(release, LOGO), tb.RELEASE_AGREEMENT_MM)
        cubes = [seen(LOGO, with_mode=False), seen(self.FELL, with_mode=False)]
        placement = tb.attribute_placement(cubes, release_at(release), RULES)
        self.assertEqual((True, LOGO, None, 1), (placement.placed, placement.placed_pad_m, placement.reason_code, placement.on_pad_count))
        for x_mm in (-83.0, -39.0):  # the release band that puts the logo inside the tolerance, at the recorded depth y -100 mm
            with self.subTest(release_x_mm=x_mm):
                reach = tb.attribute_placement(cubes, release_at((x_mm / 1000.0, -0.100)), RULES)
                self.assertTrue(reach.placed)
                self.assertLess(reach.release_distance_mm, tb.RELEASE_AGREEMENT_MM)

    def test_accepted_residual_an_earlier_block_at_the_release_is_credited_when_the_own_block_fell_off(self):
        earlier = (-0.009, -0.101)  # the live case's placed block, 7.1 mm from the release below
        release = (-0.016, -0.100)
        for cubes in ([seen(earlier, with_mode=False), seen(self.FELL, with_mode=False)], [seen(self.FELL, with_mode=False), seen(earlier, with_mode=False)]):
            with self.subTest(order=[cube.center_pad_m for cube in cubes]):
                placement = tb.attribute_placement(cubes, release_at(release), RULES)
                self.assertEqual((True, earlier, None), (placement.placed, placement.placed_pad_m, placement.reason_code))

    def test_accepted_residual_the_own_block_absent_from_the_exit_view_still_credits_a_block_at_the_release(self):
        """verify pad r1 M1: this run's block lost between OBJECT_SECURED and the release, or kept in the opened jaws, is
        not in the EXIT view at all. The RELEASE_ADMISSION does not observe the held block, so the release is still admitted
        and the block or the printed logo within the tolerance of the release is credited: the same false PLACED."""
        for name, release, other in (("the printed logo", (-0.058, -0.100), LOGO), ("an earlier block", (-0.016, -0.100), (-0.009, -0.101))):
            with self.subTest(credited=name):
                placement = tb.attribute_placement([seen(other, with_mode=False)], release_at(release), RULES)
                self.assertEqual((True, other, None, 1), (placement.placed, placement.placed_pad_m, placement.reason_code, placement.on_pad_count))
                self.assertLess(placement.release_distance_mm, tb.RELEASE_AGREEMENT_MM)

    def test_the_residual_is_bounded_by_the_tolerance_a_fell_block_with_nothing_near_the_release_refuses(self):
        release = (0.004, NEAR_EDGE_Y + 0.030)
        placement = tb.attribute_placement([seen(LOGO, with_mode=False), seen(self.FELL, with_mode=False)], release_at(release), RULES)
        self.assertEqual((False, tb.PLACED_NO_CUBE_AT_RELEASE, 1), (placement.placed, placement.reason_code, placement.on_pad_count))
        self.assertGreater(placement.release_distance_mm, placement.release_tolerance_mm)


class TheToleranceComesFromRecordedPlacements(unittest.TestCase):
    def test_the_value_is_the_worst_recorded_release_error_plus_the_anchor_uncertainty(self):
        errors = [row[1] for row in tb.RELEASE_AGREEMENT_RECORDED_MM]
        self.assertEqual(9, len(errors))
        self.assertEqual(20.0, max(errors))
        self.assertEqual(max(errors) + cycle.VISUAL_ANCHOR_UNCERTAINTY_MM, tb.RELEASE_AGREEMENT_MM)
        self.assertEqual(tb.RELEASE_AGREEMENT_ANCHOR_UNCERTAINTY_MM, cycle.VISUAL_ANCHOR_UNCERTAINTY_MM, "the controller's value, spelled in the module (a test keeps them equal)")
        self.assertLess(max(errors), tb.RELEASE_AGREEMENT_MM)

    def test_the_recorded_rows_reproduce_from_their_release_admissions(self):
        """The eight rows whose PLACED state carries the cube's position, recomputed from each run's RELEASE_ADMISSION anchor
        and FK travel along its PLACEMENT_PLAN reach direction (the recorded numbers, copied from the runs' records)."""
        for (run, recorded), (anchor, direction, travel, cube) in zip(tb.RELEASE_AGREEMENT_RECORDED_MM, RECORDED_RELEASES):
            with self.subTest(run=run):
                a, b = cycle.release_segment_pad(anchor, direction, travel)
                self.assertAlmostEqual(recorded, round(segment_distance_mm(a, b, cube), 1), places=6)


# (RELEASE_ADMISSION anchor_pad_m, PLACEMENT_PLAN reach_direction_pad, fk_travel_from_grasp_mm, PLACED cube_pad_m), in the
# order of RELEASE_AGREEMENT_RECORDED_MM; the ninth (m915d3e9) has no cube_pad_m in its PLACED state and is replayed below.
RECORDED_RELEASES = (
    ((0.0153, -0.0833), (-0.023, 1.0), -8.9, (0.011206422923458784, -0.09512534936647506)),
    ((-0.0355, -0.0873), (-0.058, 0.998), -9.5, (-0.037514201191038675, -0.10049106359108187)),
    ((0.0724, -0.087), (0.034, 0.999), -11.7, (0.052048417623205265, -0.09642016552673315)),
    ((-0.0019, -0.0877), (-0.029, 1.0), -10.7, (-0.00535824949558238, -0.10217061489946083)),
    ((0.0094, -0.0958), (-0.017, 1.0), -7.1, (0.004254437062057652, -0.11192815091317626)),
    ((0.0119, -0.0876), (0.0, 1.0), -6.1, (0.005686481166046997, -0.09214403289002414)),
    ((-0.016, -0.0895), (-0.042, 0.999), -8.3, (-0.015830342509956304, -0.10003678718984568)),
    ((-0.0905, -0.0893), (-0.086, 0.996), -9.2, (-0.0924816175127859, -0.09637467390167725)),
)


def segment_distance_mm(a, b, point) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    length = vx * vx + vy * vy
    u = 0.0 if length == 0.0 else max(0.0, min(1.0, ((point[0] - a[0]) * vx + (point[1] - a[1]) * vy) / length))
    return math.hypot(point[0] - a[0] - u * vx, point[1] - a[1] - u * vy) * 1000.0


class TheResultFileCarriesTheReleaseAgreement(unittest.TestCase):
    def test_version_3_placement_keys(self):
        self.assertEqual(3, tb.MISSION_RESULT_VERSION)
        self.assertEqual(("placed", "placed_pad_m", "on_pad_count", "release_distance_mm", "release_tolerance_mm", "reason_code", "observation_id"), tb._PLACEMENT_KEYS)


# =====================================================================================================================
# The scripted World (test_cycle_mission_mode.MissionWorld) running main() with ONE auto target: the one-block demo path.
PHANTOM_ON_PAD = [-0.061, -0.112]  # where the live run's SEARCH read the printed logo, in the World's PAD_FRAME


class TheOneBlockDemoOnTheScriptedWorld(mm.MissionCase):
    @staticmethod
    def auto_target(directory):
        return mm.write_target(directory, "cube", [mm.declared("cube", tb.AUTO)], radii={"r_claim_mm": tb.CUBE_HALF_EDGE_MM, "r_match_mm": tb.CUBE_HALF_EDGE_MM,
                                                                                         "basis": "test values only"})

    def world(self, after_release=None, floor=None):
        floor = floor or [{"pad_m": PHANTOM_ON_PAD, "contact_px": [700.0, 330.0]}, {"pad_m": mm.A_START, "contact_px": mm.CONTACT}]
        return mm.MissionWorld(floor=floor, carried=len(floor) - 1, cube_start_pad_m=mm.A_START, cube_final_pad_m=mm.A_FINAL, after_release=after_release)

    def assert_placed(self, run, target, credited=None):
        self.assertEnded(run, 0)
        result = run["result"]
        self.assertEqual(("placed_new", None), (result["outcome"], result["reason_code"]))
        self.assertTrue(tb.result_claims_placement(result, target))
        self.assertEqual(mm.A_FINAL if credited is None else credited, result["placement"]["placed_pad_m"])
        placed = run["transitions"][run["states"].index("PLACED")]
        self.assertTrue(placed["value"] and placed["attributed"])
        self.assertLessEqual(placed["release_distance_mm"], placed["release_tolerance_mm"])
        return result

    def test_the_phantom_is_ignored_at_search_and_the_block_is_placed_with_the_phantom_still_in_view(self):
        target, run = self.mission(self.world(), self.auto_target)
        self.assert_placed(run, target)
        selected = run["transitions"][run["states"].index("TARGET_SELECTED")]
        self.assertEqual(([round(v, 4) for v in mm.A_START], 2), (selected["seen_pad_m"], selected["listed_pass"]))
        self.assertNotIn("occupied_pad_m", selected, "nothing on the pad is bookkept")
        self.assertEqual((1, 1), (selected["candidates"], selected["on_pad_ignored"]))

    def test_the_live_failure_the_exit_view_does_not_find_the_phantom_again_and_the_run_is_placed(self):
        target, run = self.mission(self.world(after_release=[{"pad_m": mm.A_FINAL, "contact_px": mm.CONTACT}]), self.auto_target)
        result = self.assert_placed(run, target)
        self.assertEqual(1, result["placement"]["on_pad_count"])

    def test_a_block_that_fell_off_the_pad_is_refused_and_the_base_still_returns(self):
        fell = [mm.A_START[0], NEAR_EDGE_Y - 0.010]
        _, run = self.mission(self.world(after_release=[{"pad_m": PHANTOM_ON_PAD, "contact_px": [700.0, 330.0]}, {"pad_m": fell, "contact_px": mm.CONTACT}]),
                              self.auto_target)
        self.assertEnded(run, 5)
        self.assertIn("RELEASED", run["states"])
        result = run["result"]
        self.assertEqual(("failed", tb.PLACED_NO_CUBE_AT_RELEASE), (result["outcome"], result["reason_code"]))
        self.assertEqual((False, None, 1), (result["placement"]["placed"], result["placement"]["placed_pad_m"], result["placement"]["on_pad_count"]))
        self.assertEqual({"object_secured": True, "placed": False, "base_returned": True}, result["predicates"])
        self.assertIn(tb.PLACED_NO_CUBE_AT_RELEASE, run["stdout"])

    def test_accepted_residual_an_earlier_block_at_the_release_is_credited_when_the_own_block_fell_off(self):
        """Ruling (ii) end to end, THE ACCEPTED RESIDUAL: a block SEARCH listed on the pad lies 8 mm from where this run's
        block is released; this run's block falls off the pad. The earlier block is credited: a false PLACED, accepted."""
        earlier = [mm.A_FINAL[0] + 0.008, mm.A_FINAL[1]]
        fell = [mm.A_START[0], NEAR_EDGE_Y - 0.010]
        floor = [{"pad_m": earlier, "contact_px": [700.0, 330.0]}, {"pad_m": mm.A_START, "contact_px": mm.CONTACT}]
        target, run = self.mission(self.world(floor=floor, after_release=[{"pad_m": earlier, "contact_px": [700.0, 330.0]}, {"pad_m": fell, "contact_px": mm.CONTACT}]),
                                   self.auto_target)
        result = self.assert_placed(run, target, credited=earlier)
        self.assertEqual(1, result["placement"]["on_pad_count"], "this run's own block is off the pad: only the earlier block is on it")

    def test_the_own_block_on_the_spot_of_a_listed_block_the_human_took_away_is_placed(self):
        """Ruling (ii), Q2 end to end: SEARCH lists a block on the pad where this run's block will land; the human takes it
        away; the EXIT view shows only this run's block, within 12.7 mm of where the listed one stood. PLACED."""
        listed = [mm.A_FINAL[0] + 0.005, mm.A_FINAL[1]]
        floor = [{"pad_m": listed, "contact_px": [700.0, 330.0]}, {"pad_m": mm.A_START, "contact_px": mm.CONTACT}]
        target, run = self.mission(self.world(floor=floor, after_release=[{"pad_m": mm.A_FINAL, "contact_px": mm.CONTACT}]), self.auto_target)
        selected = run["transitions"][run["states"].index("TARGET_SELECTED")]
        self.assertEqual(1, selected["on_pad_ignored"], "not vacuous: SEARCH listed the block on the pad")
        result = self.assert_placed(run, target)
        self.assertEqual((mm.A_FINAL, 1), (result["placement"]["placed_pad_m"], result["placement"]["on_pad_count"]))

    def test_an_earlier_block_nearer_the_release_does_not_take_the_credit_from_the_own_block(self):
        earlier = [mm.A_FINAL[0] - 0.020, mm.A_FINAL[1]]  # 20 mm beside where this run's block lands: two blocks, both on the pad
        floor = [{"pad_m": earlier, "contact_px": [700.0, 330.0]}, {"pad_m": mm.A_START, "contact_px": mm.CONTACT}]
        target, run = self.mission(self.world(floor=floor), self.auto_target)
        result = self.assert_placed(run, target)
        self.assertEqual(2, result["placement"]["on_pad_count"])

    def test_two_off_pad_blocks_the_nearest_feasible_one_is_taken_and_placed(self):
        floor = [{"pad_m": mm.B_START, "contact_px": mm.B_CONTACT_BESIDE_A}, {"pad_m": mm.A_START, "contact_px": mm.CONTACT}]
        target, run = self.mission(self.world(floor=floor), self.auto_target)
        self.assert_placed(run, target)
        selected = run["transitions"][run["states"].index("TARGET_SELECTED")]
        self.assertEqual(([round(v, 4) for v in mm.A_START], tb.MODE_ARM_ONLY, 2, 0), (selected["seen_pad_m"], selected["mode"], selected["candidates"], selected["on_pad_ignored"]))


class BlocksOnThePadDoNotSpendTheListingBudget(mm.MissionCase):
    """Verify r1 M2: the listing stops at cube_candidates.MAX_PASSES verified blocks and a list with proposals left is
    refused (mission_candidates_incomplete). Passes spent on blocks already on the pad do not count toward that budget, up
    to target_binding.LISTING_ON_PAD_EXEMPT_PASSES of them; blocks off the pad still do."""

    auto_target = staticmethod(TheOneBlockDemoOnTheScriptedWorld.auto_target)
    assert_placed = TheOneBlockDemoOnTheScriptedWorld.assert_placed
    ON_PAD_BLOCKS = ([-0.061, -0.112], [0.050, 0.020], [-0.050, 0.020], [0.050, -0.050], [-0.050, -0.050], [0.0, 0.060], [0.060, 0.060], [-0.060, 0.060],
                     [0.0, 0.0], [0.030, -0.020])

    def scene(self, on_pad: int, off_pad=None) -> list:
        blocks = [{"pad_m": point, "contact_px": [700.0, 330.0]} for point in self.ON_PAD_BLOCKS[:on_pad]]
        return blocks + (off_pad or []) + [{"pad_m": mm.A_START, "contact_px": mm.CONTACT}]

    def test_the_logo_and_two_earlier_blocks_on_the_pad_do_not_refuse_the_run(self):
        world = mm.MissionWorld(floor=self.scene(3), carried=3, cube_start_pad_m=mm.A_START, cube_final_pad_m=mm.A_FINAL, pass_limit=True)
        target, run = self.mission(world, self.auto_target)
        result = self.assert_placed(run, target)
        self.assertEqual(4, result["placement"]["on_pad_count"])
        self.assertEqual(3, mm.cube_candidates.MAX_PASSES)
        self.assertIn(3 + 3, [entry["max_passes"] for entry in world.enumerated], "not vacuous: the first listing stopped at the limit and was taken again")

    def test_blocks_off_the_pad_still_spend_the_budget(self):
        off = [{"pad_m": mm.B_START, "contact_px": mm.B_CONTACT_BESIDE_A}, {"pad_m": [0.060, -0.200], "contact_px": [600.0, 700.0]},
               {"pad_m": [-0.060, -0.200], "contact_px": [500.0, 700.0]}]
        world = mm.MissionWorld(floor=self.scene(1, off), carried=4, cube_start_pad_m=mm.A_START, cube_final_pad_m=mm.A_FINAL, pass_limit=True)
        _, run = self.mission(world, self.auto_target)
        self.assertEnded(run, 5)
        self.assertEqual([], run["sent"])
        self.assertEqual("mission_candidates_incomplete", run["result"]["reason_code"])

    def test_more_blocks_on_the_pad_than_the_exemption_refuses_under_the_named_code(self):
        # 12 on the pad and the one off it: the three passes of the budget and the nine exempt ones are all spent on the pad
        self.assertEqual(tb.MAX_TARGETS + 1, tb.LISTING_ON_PAD_EXEMPT_PASSES)
        blocks = self.ON_PAD_BLOCKS + ([0.070, -0.020], [-0.070, -0.020], [0.070, 0.000])
        floor = [{"pad_m": point, "contact_px": [700.0, 330.0]} for point in blocks[:tb.LISTING_ON_PAD_EXEMPT_PASSES + 3]] + [{"pad_m": mm.A_START, "contact_px": mm.CONTACT}]
        world = mm.MissionWorld(floor=floor, carried=len(floor) - 1, cube_start_pad_m=mm.A_START, cube_final_pad_m=mm.A_FINAL, pass_limit=True)
        _, run = self.mission(world, self.auto_target)
        self.assertEnded(run, 5)
        self.assertEqual([], run["sent"])
        self.assertEqual("mission_candidates_incomplete", run["result"]["reason_code"])
        self.assertLessEqual(max(entry["max_passes"] for entry in world.enumerated), 3 + tb.LISTING_ON_PAD_EXEMPT_PASSES)


class PadInViewSweepWorld(mm.SweepWorld):
    """The SweepWorld with each block seen only from the sectors its entry names (`seen_from`, a tuple of CENTER, LEFT,
    RIGHT) at the low pose before the release: the printed logo on the pad in the centre frame, the block off the pad in
    one sector only."""

    def floor_now(self) -> list:
        cubes = super().floor_now()
        if self.released or self.joints["shoulder_lift"] >= -60.0:
            return cubes
        seen = [cube for cube in cubes if self.sector() in cube.get("seen_from", ("CENTER", "LEFT", "RIGHT"))]
        return seen or cubes  # nothing seen from here: SweepWorld blanks the record itself (cube_seen_at)


class OnPadBlocksStillFailARunInTwoMorePlaces(mm.MissionCase):
    """Verify r2 M3: the listing budget is not the only way a block on the pad still fails a run. Two more are named
    residuals, both in the motion path this change does not touch, pinned here so that they are not forgotten:
      - SEARCH SWEEP (policy_search.search_sweep, under the word search_sweep): the sweep runs only when the frozen record
        of the centre frame verifies NO cube. A block on the pad in that frame (the printed logo, an earlier block) is such
        a cube, so the sweep does not run, the mission SEARCH lists only blocks on the pad and refuses
        mission_binding_no_candidate where the sweep would have found the block off the pad;
      - ALIGN (Cycle._mission_one_cube_in_view): the pregrasp view must verify exactly one cube, and the recognizer's count
        includes blocks on the pad, so a block on the pad in the pregrasp view refuses align_more_than_one_cube_verified."""

    auto_target = staticmethod(TheOneBlockDemoOnTheScriptedWorld.auto_target)
    assert_placed = TheOneBlockDemoOnTheScriptedWorld.assert_placed

    def mission_with_sweep(self, world, writer):
        """As TheSearchSweepFindsTheBlockOffCentre runs it: under the word search_sweep, the PLACEMENT_ENVELOPE record left out."""
        target, run = self.mission(world, writer, extra_env={"KC_PLACEMENT_ENVELOPE": "search_sweep"})
        run["transitions"] = [entry for entry in run["transitions"] or [] if entry["state"] != "PLACEMENT_ENVELOPE"]
        run["states"] = [state for state in run["states"] if state != "PLACEMENT_ENVELOPE"]
        return target, run

    def sweep_world(self, logo_in_centre: bool) -> PadInViewSweepWorld:
        block = {"pad_m": mm.A_START, "contact_px": mm.CONTACT, "seen_from": ("LEFT",)}
        logo = {"pad_m": PHANTOM_ON_PAD, "contact_px": [700.0, 330.0], "seen_from": ("CENTER",)}
        floor = [logo, block] if logo_in_centre else [block]
        seen_at = {"LEFT": True, "CENTER": True} if logo_in_centre else {"LEFT": True}
        return PadInViewSweepWorld(floor=floor, carried=len(floor) - 1, cube_start_pad_m=mm.A_START, cube_final_pad_m=mm.A_FINAL, cube_seen_at=seen_at)

    def test_without_a_block_on_the_pad_in_the_centre_frame_the_sweep_finds_the_block_and_the_run_is_placed(self):
        target, run = self.mission_with_sweep(self.sweep_world(logo_in_centre=False), self.auto_target)
        self.assert_placed(run, target)
        self.assertEqual(["SEARCH", "SEARCH_SWEEP", "TARGET_SELECTED"], run["states"][:3])

    def test_named_residual_a_block_on_the_pad_in_the_centre_frame_skips_the_sweep_and_the_run_is_refused(self):
        _, run = self.mission_with_sweep(self.sweep_world(logo_in_centre=True), self.auto_target)
        self.assertEnded(run, 5)
        self.assertNotIn("SEARCH_SWEEP", run["states"], "the frozen record verified the logo, so the sweep did not run")
        self.assertEqual(([], tb.REFUSED_NO_CANDIDATE), (run["sent"], run["result"]["reason_code"]))

    def test_named_residual_a_block_on_the_pad_in_the_pregrasp_view_refuses_align(self):
        floor = [{"pad_m": PHANTOM_ON_PAD, "contact_px": [700.0, 330.0]}, {"pad_m": mm.A_START, "contact_px": mm.CONTACT}]
        world = mm.MissionWorld(floor=floor, carried=1, cube_start_pad_m=mm.A_START, cube_final_pad_m=mm.A_FINAL, candidates_verified=2)
        _, run = self.mission(world, self.auto_target)
        self.assertEnded(run, 5)
        self.assertIn("TARGET_SELECTED", run["states"])
        self.assertNotIn("ALIGN", run["states"])
        self.assertEqual("align_more_than_one_cube_verified", run["result"]["reason_code"])


class TheCommittedTextNamesEveryWayOnPadBlocksFailARun(unittest.TestCase):
    """Verify r2 M3: every place that states how blocks on the pad can still fail a run names all three (the listing
    budget, the SEARCH SWEEP, the ALIGN count), and none claims a single one."""

    CLAIMS = ("the one limit", "one limit named below", "the one way blocks on the pad still fail a run", "pass budget excepted", "listing budget excepted")

    @staticmethod
    def texts() -> dict:
        header = (ROOT / "autonomy/tests/test_pad_policy_20260929.py").read_text().split("\nimport ", 1)[0]
        return {"target_binding": tb.__doc__, "run_single_cube_cycle": cycle.__doc__, "test header": header,
                "decision-log": (ROOT / "docs/autonomous-demo/decision-log.md").read_text().split("## 2026-09-29", 1)[-1]}

    def test_each_text_names_the_sweep_and_the_align_residuals_and_claims_no_single_limit(self):
        for name, text in self.texts().items():
            flat = " ".join(text.split())
            with self.subTest(text=name):
                self.assertTrue("SEARCH SWEEP" in flat, f"{name} does not name the SEARCH SWEEP residual")
                self.assertTrue("align_more_than_one_cube_verified" in flat, f"{name} does not name the ALIGN residual")
                for claim in self.CLAIMS:
                    self.assertFalse(claim in flat, f"{name} still claims {claim!r}")

    RESIDUAL_WIDTH = ("this run's block is not on the pad at EXIT for any reason after OBJECT_SECURED, given an admitted release",
                      "it left the pad after the release, it was lost between OBJECT_SECURED and the release, or it stayed in the opened jaws",
                      "The RELEASE_ADMISSION does not observe the held block",
                      "the gripper reading at RELEASED is recorded, not checked")

    def test_the_decision_log_quotes_ruling_ii_and_the_texts_name_the_accepted_residual(self):
        """Ruling (ii) of 2026-09-29 accepts the residual; the log quotes it, and the log and target_binding name what the
        operator accepted: the printed logo, and that no recorded run has lost a block after an admitted release."""
        texts = {name: " ".join(re.sub(r"(?m)^> ?", "", text).split()) for name, text in self.texts().items()}  # a quote's markers dropped
        self.assertIn("(ii) credit any block on the pad near this run's release point, whatever was there before", texts["decision-log"])
        self.assertNotIn("No operator ruling accepts residuals (1) and (2)", texts["decision-log"])
        for name in ("decision-log", "target_binding"):
            with self.subTest(text=name):
                self.assertIn("ACCEPTED RESIDUAL", texts[name])
                self.assertIn("printed logo", texts[name])
                self.assertIn("no recorded run has lost a block after an admitted release", texts[name])
                # verify pad r1 M1: the residual is every way this run's block can be missing at EXIT once it was secured,
                # because nothing observes the held block between OBJECT_SECURED and the EXIT view
                for phrase in self.RESIDUAL_WIDTH:
                    self.assertIn(phrase, texts[name])
                self.assertNotIn("a false PLACED when this run's block left the pad after an admitted release", texts[name])
        self.assertNotIn("PLACED_ONLY_A_BLOCK_LISTED_AT_SEARCH", texts["target_binding"])

# =====================================================================================================================
# THE LIVE CASE on its own evidence: KC_EVIDENCE_ROOT names a directory that is, or holds under its own name, the phantom
# evidence (frames/, proposals/, records/, the run's events). The frames are the console's 1280 px rendition, so numbers
# are compared with the recorded ones within REPRODUCTION_MM, never assumed equal. Nothing is extracted, written or copied,
# and no path of the evidence is printed.
PHANTOM_DIR_NAME = "phantom-20260928"
LIVE_RUN = "console-20260928T174207Z-m915d3e9"
SEARCH_VIEW, EXIT_VIEW = "obs-20260928T174211Z-wrist-0001", "obs-20260928T174654Z-wrist-0001"
REPRODUCTION_MM = 2.0
STATE_LINE = re.compile(r"\[STATE\] (\S+) (\{.*\})")


def phantom_dir():
    root = os.environ.get("KC_EVIDENCE_ROOT")
    if not root:
        return None
    for candidate in (Path(root), Path(root) / PHANTOM_DIR_NAME):
        if (candidate / f"{LIVE_RUN}.events.json").is_file() and (candidate / "records" / f"{EXIT_VIEW}.json").is_file():
            return candidate
    return None


EVIDENCE = phantom_dir()


@unittest.skipIf(EVIDENCE is None, f"set KC_EVIDENCE_ROOT to a directory holding {PHANTOM_DIR_NAME}")
class TheLiveCaseReplaysAsPlaced(unittest.TestCase):
    def states(self, name: str) -> list:
        found = []
        for event in json.loads((EVIDENCE / f"{LIVE_RUN}.events.json").read_text())["events"]:
            for match in STATE_LINE.finditer((event.get("payload") or {}).get("text") or ""):
                if match.group(1) == name:
                    found.append(json.loads(match.group(2)))
        return found

    def listing(self, name: str) -> list:
        import cv2
        import cube_candidates

        image = cv2.imread(str(EVIDENCE / "frames" / f"{name}.jpg"))
        record = json.loads((EVIDENCE / "records" / f"{name}.json").read_text())
        proposals = json.loads((EVIDENCE / "proposals" / f"{name}.json").read_text())
        result = cube_candidates.enumerate_verified_cubes(image, proposals, record)
        self.assertIsNone(result.refused)
        self.assertTrue(result.complete)
        return result.cubes

    def release(self):
        admission, plan = self.states("RELEASE_ADMISSION")[0], self.states("PLACEMENT_PLAN")[0]
        self.assertTrue(admission["admitted"])
        return cycle.release_segment_pad(admission["anchor_pad_m"], plan["reach_direction_pad"], admission["fk_travel_from_grasp_mm"])

    @staticmethod
    def exit_cube(entry) -> tb.SeenCube:
        centre = entry["cube_by_template"]["cube_center_pad_m"]
        return tb.SeenCube(tuple(centre), cycle.placed_margin_mm(centre), None)

    def test_the_recorded_run_failed_on_the_phantom(self):
        placed = self.states("PLACED")[0]
        self.assertEqual((False, "mission_earlier_target_disturbed"), (placed["value"], placed["attribution_reason"]))

    def test_search_lists_the_phantom_on_the_pad_and_binds_the_real_block(self):
        selected = self.states("TARGET_SELECTED")[0]
        listed = self.listing(SEARCH_VIEW)
        self.assertEqual(2, len(listed), "not vacuous: the reproduction lists the printed logo as the run did")
        cubes = []
        for entry in listed:  # the mode an off-pad block measured is the run's own (its TARGET_SELECTED); a block on the pad needs none
            centre = tuple(entry["cube_by_template"]["cube_center_pad_m"])
            margin = cycle.placed_margin_mm(centre)
            cubes.append(tb.SeenCube(centre, margin, None if margin >= cycle.PLACED_MIN_EDGE_MARGIN_MM else selected["mode"]))
        self.assertTrue(tb.is_placed(cubes[1], RULES))
        self.assertLess(tb.distance_mm(cubes[1].center_pad_m, selected["occupied_pad_m"][0]), REPRODUCTION_MM)
        (binding,) = tb.bind_targets(AUTO, cubes, RULES)
        self.assertEqual(0, binding.index)
        self.assertLess(tb.distance_mm(binding.bound_pad_m, selected["seen_pad_m"]), REPRODUCTION_MM)

    def test_the_exit_view_is_placed_by_this_runs_release_with_or_without_the_phantom(self):
        exit_cubes = [self.exit_cube(entry) for entry in self.listing(EXIT_VIEW)]
        self.assertEqual(1, len(exit_cubes), "the EXIT view did not find the phantom again, as the live run did not")
        phantom = self.exit_cube(self.listing(SEARCH_VIEW)[1])
        listed_on_pad = [cube.center_pad_m for cube in (self.exit_cube(entry) for entry in self.listing(SEARCH_VIEW)) if tb.is_placed(cube, RULES)]
        self.assertEqual(1, len(listed_on_pad), "not vacuous: SEARCH listed the printed logo on the pad, and it decides nothing")
        for cubes in (exit_cubes, exit_cubes + [phantom], [phantom] + exit_cubes):
            with self.subTest(blocks=len(cubes)):
                placement = tb.attribute_placement(cubes, self.release(), RULES)
                self.assertEqual((True, None), (placement.placed, placement.reason_code))
                self.assertLess(tb.distance_mm(placement.placed_pad_m, exit_cubes[0].center_pad_m), 1e-9)
                self.assertAlmostEqual(12.3, placement.release_distance_mm, delta=REPRODUCTION_MM)
        self.assertEqual((LIVE_RUN, 12.3), tb.RELEASE_AGREEMENT_RECORDED_MM[-1], "the ninth recorded row is this replay's own reading")

    def test_the_same_block_off_the_pad_would_still_be_refused(self):
        block = self.exit_cube(self.listing(EXIT_VIEW)[0])
        fell = (block.center_pad_m[0], NEAR_EDGE_Y - 0.005)
        phantom = self.exit_cube(self.listing(SEARCH_VIEW)[1])
        placement = tb.attribute_placement([phantom, tb.SeenCube(fell, cycle.placed_margin_mm(fell), None)], self.release(), RULES)
        self.assertEqual((False, tb.PLACED_NO_CUBE_AT_RELEASE, 1), (placement.placed, placement.reason_code, placement.on_pad_count))


if __name__ == "__main__":
    unittest.main()
