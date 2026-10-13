#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_policy_placement.py: the PLACEMENT stage policy on the stage harness (track P of
# docs/autonomous-demo/overnight-plan-2026-09-26.md). What is pinned: classify_for_binding keeps placement_plan's mode and,
# when there is none, names the binding constraint, the deficit and the hint; run_placement's refusal while held carries
# them; an exactly diagonal reach axis (|d_x| == |d_y|, where every edge is a side edge)
# is explained, refused while held with its numbers, and reaches RELEASE_ADMISSION on the default path without an
# exception; the per-edge admission values exist under an envelope word only. No robot, no camera, no network.
# Run directly: python3 autonomy/tests/test_policy_placement.py
import contextlib
import io
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import stage_harness as harness  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import policy_placement  # noqa: E402

AXIS_1 = (-0.042, 0.999)
START_3 = (0.163, -0.210)
LATE_2117 = (0.064, -0.220)
DIAGONAL = (math.sqrt(0.5), math.sqrt(0.5))
EDGE_KEYS = ("release_margin_right_mm", "release_margin_left_mm", "release_margin_far_mm", "release_margin_near_mm", "release_binding_edge")


class ClassifyForBinding(unittest.TestCase):
    def test_a_placeable_block_keeps_placement_plans_mode(self):
        ctl = harness.controller_view()
        self.assertEqual(("BASE_ADVANCE", None), policy_placement.classify_for_binding(harness.HarnessCycle(), ctl, [-0.0201, -0.196], AXIS_1, 0.0))

    def test_2117_names_the_along_budget_its_deficit_and_the_hint(self):
        mode, why = policy_placement.classify_for_binding(harness.HarnessCycle(), harness.controller_view(), list(LATE_2117), AXIS_1, 0.0)
        self.assertIsNone(mode)
        self.assertEqual(("along_budget", "move the block 12 mm toward the pad"), (why["constraint"], why["hint"]))
        self.assertAlmostEqual(11.6, why["deficit_mm"], delta=0.05)
        self.assertIn("11.6 mm past the along-axis budget", why["explanation"])
        self.assertIsInstance(why["sentence"], str)
        self.assertEqual("projected+reach_after_advance", why["model_admits_under"])

    def test_start_3_names_the_side_edge_and_the_along_axis_need(self):
        mode, why = policy_placement.classify_for_binding(harness.HarnessCycle(), harness.controller_view(), list(START_3), AXIS_1, 0.0)
        self.assertIsNone(mode)
        self.assertEqual(("side_edge", "right"), (why["constraint"], why["binding_edge"]))
        self.assertAlmostEqual(44.5, why["side_margin_mm"], delta=0.01)
        self.assertAlmostEqual(121.6, why["axis_advance_needed_unbounded_mm"], delta=0.05)
        self.assertGreater(why["along_budget_deficit_mm"], 0.0)

    def test_a_non_finite_reading_is_refused_with_its_reason_not_raised(self):
        nan, inf = float("nan"), float("inf")
        for anchor, axis, grasp_out in (([nan, -0.2], (0.0, 1.0), 0.0), ([inf, -0.2], (0.0, 1.0), 0.0), ([0.1, -0.2], (nan, 1.0), 0.0), ([0.1, -0.2], (0.0, 1.0), nan)):
            with self.subTest(anchor=anchor, axis=axis, grasp_out=grasp_out):
                mode, why = policy_placement.classify_for_binding(harness.HarnessCycle(), harness.controller_view(), anchor, axis, grasp_out)
                self.assertIsNone(mode)
                self.assertNotIn("hint", why)
                self.assertIn("not finite", why["explanation"])
                self.assertRegex(why["explanation"], r"\A[A-Za-z0-9_.+ -]{1,96}\Z")
                self.assertTrue(all(not isinstance(v, float) or math.isfinite(v) for v in why.values()))

    def test_an_exactly_diagonal_axis_is_explained_not_raised(self):
        mode, why = policy_placement.classify_for_binding(harness.HarnessCycle(), harness.controller_view(), [0.3, 0.3], DIAGONAL, 0.0)
        self.assertIsNone(mode)
        self.assertEqual("side_edge", why["constraint"])
        self.assertIn("side_margin_mm", why)


class RunPlacementCase(unittest.TestCase):
    """run_placement from a held cube, on the harness; it stops at the first refusal."""

    def run_placement(self, anchor, direction, **overrides):
        ctx = harness.HarnessCycle(pose=harness.HELD_POSE)
        ctx.target, ctx.runtime_live = None, False
        ctl = harness.controller_view(reach_direction_pad=lambda homography, pixel: direction, **overrides)
        held = harness.view(harness.HELD_POSE)
        record = harness.cube_record([515, 485, 590, 581], contact_px=[553.0, 545.0], cube_center_pad_m=list(anchor), pad_frame=harness.pad_frame())
        summary = {}
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failed:
            policy_placement.run_placement(ctx, ctl, summary, dict(harness.LOW_POSE), record, {"cube_center_pad_m": list(anchor)}, harness.pad_frame(), held, held)
        return ctx, harness.failure_code(ctx, failed.exception)

    def failure_values(self, ctx) -> dict:
        return [args[2] for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]

    def state(self, ctx, name) -> dict:
        return [entry for entry in ctx.transitions if entry["state"] == name][-1]


class RunPlacementRefusals(RunPlacementCase):
    def test_the_refusal_while_held_names_the_constraint_deficit_and_hint(self):
        ctx, code = self.run_placement(LATE_2117, AXIS_1)
        self.assertEqual("placement_infeasible_while_held", code)
        values = self.failure_values(ctx)
        self.assertEqual("along_budget", values["constraint"])
        self.assertAlmostEqual(11.6, values["deficit_mm"], delta=0.05)
        self.assertEqual("move the block 12 mm toward the pad", values["hint"])
        self.assertAlmostEqual(131.6, values["axis_advance_needed_unbounded_mm"], delta=0.05)

    def test_an_exactly_diagonal_axis_reaches_the_release_admission_on_the_default_path(self):
        # The pad centre is ARM_ONLY; the release height bound set below any height makes the admission refuse, so the run
        # stops right after RELEASE_ADMISSION, which read the side margin of that axis.
        ctx, code = self.run_placement((0.0, 0.0), DIAGONAL, RELEASE_MAX_HEIGHT_MM=-1000.0)
        self.assertEqual("release_not_admitted", code)
        admission = self.state(ctx, "RELEASE_ADMISSION")
        self.assertFalse(admission["admitted"])
        self.assertNotIn("side_margin_after_advance_mm", admission)  # a field of the envelope words only
        (margin,) = [entry for entry in ctx.events.predicates("release_margin")]
        self.assertEqual({"side_margin_after_advance_mm"}, set(margin["measurements"]))
        self.assertAlmostEqual(min(cycle.edge_margins_mm((0.0, 0.0))), margin["measurements"]["side_margin_after_advance_mm"], delta=1e-9)

    def test_an_exactly_diagonal_axis_refused_while_held_is_explained(self):
        ctx, code = self.run_placement((0.3, 0.3), DIAGONAL)
        self.assertEqual("placement_infeasible_while_held", code)
        self.assertEqual("side_edge", self.failure_values(ctx)["constraint"])


class ThePerEdgeAdmission(RunPlacementCase):
    def test_under_a_word_the_admission_states_each_edge_and_the_binding_one(self):
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("projected",)):
            ctx, code = self.run_placement((0.0, -0.05), AXIS_1, RELEASE_MAX_HEIGHT_MM=-1000.0, PLACEMENT_ENVELOPE=("projected",))
        self.assertEqual("release_not_admitted", code)
        admission = self.state(ctx, "RELEASE_ADMISSION")
        for key in EDGE_KEYS:
            self.assertIn(key, admission)
        (margin,) = ctx.events.predicates("release_margin")
        per_edge = [margin["measurements"][key] for key in EDGE_KEYS[:4]]
        self.assertAlmostEqual(margin["measured"], min(per_edge), delta=1e-9)
        self.assertEqual("near", margin["measurements"]["release_binding_edge"])

    def test_with_no_word_nothing_per_edge_is_stated(self):
        ctx, _code = self.run_placement((0.0, -0.05), AXIS_1, RELEASE_MAX_HEIGHT_MM=-1000.0)
        admission = self.state(ctx, "RELEASE_ADMISSION")
        (margin,) = ctx.events.predicates("release_margin")
        for key in EDGE_KEYS:
            self.assertNotIn(key, admission)
            self.assertNotIn(key, margin["measurements"])


if __name__ == "__main__":
    unittest.main()
