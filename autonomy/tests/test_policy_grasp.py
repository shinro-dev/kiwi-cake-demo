# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/tests/test_policy_grasp.py: the GRASP stage policy (autonomy/manipulation/policy_grasp.py) run alone on the stage
harness, with no word and under each of its words (docs/autonomous-demo/overnight-plan-2026-09-26.md, track G).

The numbers of run console-20260926T191919Z-m8c5f9e9 (the 19:19 grasp miss) are its recorded ones: ALIGN measured the tool
at -7.4 mm and the block 15.6 mm nearer (radial -15.6); the three closes read 67.288, 67.150 and 67.357; the straddle frame
put the block's far edge at row 524 and the reopen frames at 560 to 616.
"""
import contextlib
import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))

import run_single_cube_cycle as cycle  # noqa: E402
import stage_harness as harness  # noqa: E402
import policy_grasp  # noqa: E402

# Run m8c5f9e9's ALIGN, recorded.
MISS_TOOL_OUT_MM, MISS_RADIAL_MM = -7.4, -15.6


class StopAtTheGraspPose(Exception):
    """The grasp pose was asked for: APPROACH's target is known, nothing after it is needed."""


def asked_grasp_forward_mm(tool_out_mm: float, radial_mm: float, **overrides) -> float:
    """The forward shift (mm) APPROACH asks the grasp pose for, from an aligned view whose tool stands at tool_out_mm and a
    decision of radial_mm; `overrides` go in front of the controller's namespace (the run's words, for one). Both grasp
    poses are stopped at: the controller's proven_grasp_pose and the stage's near_grasp_pose."""
    asked = []

    def grasp_pose(measured, forward_m, *args, **kwargs):
        asked.append(forward_m * 1000.0)
        raise StopAtTheGraspPose()

    with mock.patch.object(policy_grasp, "near_grasp_pose", lambda ctl, measured, forward_m, *args, **kwargs: grasp_pose(measured, forward_m)):
        _approach_until_the_grasp_pose(tool_out_mm, radial_mm, grasp_pose, overrides)
    return asked[-1]


def _approach_until_the_grasp_pose(tool_out_mm, radial_mm, grasp_pose, overrides):
    start = harness.view(harness.DESCENDED_POSE)
    start["tool"]["out_mm"] = tool_out_mm
    ctl = harness.controller_view(proven_grasp_pose=grasp_pose, **overrides)
    ctx = harness.HarnessCycle(pose=harness.DESCENDED_POSE)
    with contextlib.redirect_stdout(io.StringIO()), unittest.TestCase().assertRaises(StopAtTheGraspPose):
        policy_grasp.run_grasp(ctx, ctl, {"object_secured": False}, start, {"radial_mm": radial_mm, "lateral_mm": 3.0, "aligned": True})


class TheApproachClampFloor(unittest.TestCase):
    """With no word APPROACH never asks for a grasp nearer than the proven grasp radius: the target is clamped to exactly 0 mm.
    Replay P0c found the floor unguarded (a floor moved to -10 mm left the whole autonomy suite green)."""

    def test_the_recorded_grasp_miss_plans_its_grasp_at_the_proven_radius(self):
        wanted = MISS_TOOL_OUT_MM + MISS_RADIAL_MM + cycle.APPROACH_REACH_LEAD_MM
        self.assertAlmostEqual(-9.0, wanted, places=6)
        self.assertEqual(0.0, asked_grasp_forward_mm(MISS_TOOL_OUT_MM, MISS_RADIAL_MM))

    def test_every_block_nearer_than_the_floor_is_clamped_to_it_and_none_farther_is(self):
        for wanted_mm in (-40.0, -25.0, -10.0, -9.0, -1.0, -0.1):
            with self.subTest(wanted_mm=wanted_mm):
                self.assertEqual(0.0, asked_grasp_forward_mm(0.0, wanted_mm - cycle.APPROACH_REACH_LEAD_MM))
        for wanted_mm in (0.0, 0.5, 12.0, 30.0):
            with self.subTest(wanted_mm=wanted_mm):
                self.assertAlmostEqual(wanted_mm, asked_grasp_forward_mm(0.0, wanted_mm - cycle.APPROACH_REACH_LEAD_MM), places=6)


def detected(bbox_xyxy, observation_id="harness-observation", clipped=None):
    """A frozen perception record of a detected cube with this box."""
    record = harness.cube_record(bbox_xyxy, observation_id=observation_id)
    record["cube"]["clipped"] = dict(clipped or {"top": False, "bottom": True, "left": False, "right": False, "any_border": True})
    return record


def gripper_answer(value, record=None):
    """A grip() result the harness hands back: the reading, the pose where the gripper moved, and its wrist frame."""
    def answer(ctx):
        ctx.current["gripper"] = float(value)
        return {"gripper": float(value), "measured": dict(ctx.current), "tool": cycle.tool_state(ctx.current), "perception": [record] if record else []}
    return answer


class TheGraspEvidence(unittest.TestCase):
    """Events only, with no word: the far-edge row of the straddle frame and of each reopen a retry follows (the last reopen
    before grasp_missed is its evidence, not a row), whether each reopen opened, and the last view of a failure. The recorded straddle rows classify: the successes' 361 and 384 in the band, the miss's 524 and its reopens'
    560 to 616 nearer."""

    def test_the_recorded_far_edge_rows_classify(self):
        for row, side in ((361, "in_band"), (384, "in_band"), (351, "in_band"), (397, "in_band"), (524, "nearer"), (560, "nearer"), (575, "nearer"), (616, "nearer"), (300, "farther")):
            with self.subTest(row=row):
                self.assertEqual((float(row), side), policy_grasp.far_edge_side(detected([414, row, 761, 720]))[:2])
        self.assertEqual((None, None), policy_grasp.far_edge_side(harness.cube_record(None)))
        self.assertEqual((None, None), policy_grasp.far_edge_side(detected([414, 0, 761, 300], clipped={"top": True, "bottom": False, "any_border": True})))

    def test_the_straddle_frame_and_the_reopens_a_retry_follows_are_stated_and_decide_nothing(self):
        script = {"perception:APPROACH proven grasp geometry": [[detected([414, 524, 761, 720], "obs-straddle")]],
                  "grip:latch": [67.288, 67.150, 67.357], "grip:open": [gripper_answer(79.917, detected([414, 560, 761, 720], f"obs-reopen-{n}")) for n in range(3)]}
        ctx = harness.HarnessCycle(script, pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failure:
            policy_grasp.run_grasp(ctx, harness.controller_view(), {"object_secured": False}, harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        self.assertEqual("grasp_missed", harness.failure_code(ctx, failure.exception))
        self.assertEqual(2, ctx.states().count("GRASP_RETRY_ADVANCE"), "with no word the retry is the proven one")
        rows = ctx.events.predicates("straddle_far_edge_row")
        self.assertEqual([524.0, 560.0, 560.0], [entry["measured"] for entry in rows], "the straddle frame, then the two reopens a retry follows")
        self.assertEqual([False, False, False], [entry["value"] for entry in rows])
        self.assertEqual([True, True, True], [entry["measurements"]["block_nearer"] for entry in rows])
        self.assertEqual([True, True, True], [entry["value"] for entry in ctx.events.predicates("gripper_opened")])

    def test_grasp_missed_names_its_last_view(self):
        script = {"grip:latch": [66.9, 66.9, 66.9], "grip:open": [gripper_answer(80.0, detected([414, 560, 761, 720], f"obs-reopen-{n}")) for n in range(3)]}
        ctx = harness.HarnessCycle(script, pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed):
            policy_grasp.run_grasp(ctx, harness.controller_view(), {"object_secured": False}, harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        _code, _message, _values, evidence = [args for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]
        self.assertEqual("obs-reopen-2", evidence[-1]["perception"][-1]["observation_id"])

    def test_straddling_not_reached_names_its_view(self):
        high = harness.view(harness.DESCENDED_POSE, [detected([414, 380, 761, 720], "obs-high")])
        ctx = harness.HarnessCycle({"APPROACH proven grasp geometry": high}, pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed):
            policy_grasp.run_grasp(ctx, harness.controller_view(), {"object_secured": False}, harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        code, _message, _values, evidence = [args for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]
        self.assertEqual("straddling_not_reached", code)
        self.assertEqual("obs-high", evidence["perception"][-1]["observation_id"])
        self.assertEqual([True], [entry["value"] for entry in ctx.events.predicates("straddle_far_edge_row")])

    def test_a_reopen_that_did_not_open_is_stated_false(self):
        script = {"grip:latch": [66.9, 66.9, 66.9], "grip:open": [74.0, 80.0, 80.0]}
        ctx = harness.HarnessCycle(script, pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed):
            policy_grasp.run_grasp(ctx, harness.controller_view(), {"object_secured": False}, harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        self.assertEqual([False, True, True], [entry["value"] for entry in ctx.events.predicates("gripper_opened")])


NEAR = {"PLACEMENT_ENVELOPE": ("near_grasp",)}


def pose_near(forward_mm: float, up_mm: float) -> dict:
    """A measured pose at the proven grasp geometry shifted forward_mm and up_mm (the solver's own answer), pan as recorded."""
    return {**harness.GRASP_POSE, **cycle.cli._proven_grasp_shifted(forward_mm / 1000.0, up_mm / 1000.0)}


class TheNearGrasp(unittest.TestCase):
    """near_grasp: the grasp where the block was measured, down to -25 mm, through near_grasp_pose on the proven gates; a clamp
    of more than 5 mm is refused. Open ruling 3: no guard models the chassis, so the word stays unqualified until its
    no-block check on the robot."""

    def test_the_nearest_target_is_the_words_bound(self):
        self.assertEqual(0.0, policy_grasp.min_grasp_forward_mm())
        self.assertEqual(-25.0, policy_grasp.min_grasp_forward_mm(("near_grasp",)))
        self.assertIsInstance(policy_grasp.min_grasp_forward_mm(("near_grasp",)), float)

    def test_the_recorded_grasp_miss_plans_at_minus_nine_under_the_word_and_at_zero_without_it(self):
        self.assertAlmostEqual(-9.0, asked_grasp_forward_mm(MISS_TOOL_OUT_MM, MISS_RADIAL_MM, **NEAR), places=6)
        self.assertEqual(0.0, asked_grasp_forward_mm(MISS_TOOL_OUT_MM, MISS_RADIAL_MM))
        aim = policy_grasp.grasp_target_mm(MISS_TOOL_OUT_MM + MISS_RADIAL_MM, (), (0.0, 60.0), cycle.APPROACH_REACH_LEAD_MM)
        self.assertEqual((0.0, 9.0), (aim["target_mm"], round(aim["clamped_by_mm"], 6)))

    def test_with_no_empty_radius_the_target_is_approachs_own_expression(self):
        for estimate in (-60.0, -23.0, -14.0, -5.5, 0.0, 7.3, 21.1, 45.2, 46.0, 70.0):
            for bounds in ((0.0, 60.0), (-25.0, 60.0)):
                with self.subTest(estimate=estimate, bounds=bounds):
                    aim = policy_grasp.grasp_target_mm(estimate, (), bounds, 14.0)
                    self.assertEqual(min(max(estimate + 14.0, bounds[0]), bounds[1]), aim["target_mm"])
                    self.assertEqual(0.0, aim["moved_mm"])

    def test_near_grasp_pose_is_accepted_to_minus_25_and_refused_beyond_and_without_the_word(self):
        ctl = harness.controller_view()
        for forward_mm in (-5.0, -10.0, -15.0, -20.0, -25.0):
            with self.subTest(forward_mm=forward_mm):
                pose, report = policy_grasp.near_grasp_pose(ctl, pose_near(forward_mm, 15.0), forward_mm / 1000.0, 0.002, ("near_grasp",))
                reached = cycle.tool_state({**harness.GRASP_POSE, **pose.joints})
                self.assertAlmostEqual(forward_mm, reached["out_mm"], delta=1.0)
                self.assertGreaterEqual(report["lowest_link_m"], cycle.GRASP_TOOL_Z_M - cycle.LOWEST_TARGET_BELOW_GRASP_M)
        with self.assertRaises(cycle.ScanRefused):
            policy_grasp.near_grasp_pose(ctl, pose_near(-26.0, 15.0), -0.026, 0.002, ("near_grasp",))
        with self.assertRaises(cycle.ScanRefused):
            policy_grasp.near_grasp_pose(ctl, pose_near(-5.0, 15.0), -0.005, 0.002)
        with self.assertRaises(cycle.ScanRefused):  # the travel bound of the proven pose: approach in bounded steps first
            policy_grasp.near_grasp_pose(ctl, pose_near(-10.0, 45.0), -0.010, 0.002, ("near_grasp",))
        proven = cycle.proven_grasp_pose(pose_near(5.0, 15.0), 0.005, 0.002)[0]
        self.assertEqual(proven.joints, policy_grasp.near_grasp_pose(ctl, pose_near(5.0, 15.0), 0.005, 0.002, ("near_grasp",))[0].joints, "at or beyond the proven radius it is the proven pose")

    def test_the_recorded_miss_is_reached_and_closed_at_minus_nine_under_the_word(self):
        start = harness.view(harness.DESCENDED_POSE)
        radial = MISS_TOOL_OUT_MM + MISS_RADIAL_MM - start["tool"]["out_mm"]
        ctx = harness.HarnessCycle({"grip:latch": [68.4], "frame": lambda _ctx: _still_frame()}, pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()):
            held = policy_grasp.run_grasp(ctx, harness.controller_view(**NEAR), {"object_secured": False}, start, {"radial_mm": radial, "lateral_mm": 3.0, "aligned": True})
        self.assertAlmostEqual(-9.0, held.view["tool"]["out_mm"], delta=1.0)
        self.assertEqual(["APPROACH", "STRADDLING", "GRASP_CLOSE", "OBJECT_SECURED"], ctx.states())
        self.assertEqual([True], [entry["value"] for entry in ctx.events.predicates("grasp_target_within_range")])

    def test_a_target_clamped_by_more_than_five_mm_is_refused_before_any_move(self):
        for estimate, asked in ((-39.0, -25.0), (-44.0, -25.0), (51.0, 60.0)):  # clamped by 0, 5 and 5 mm: planned at the bound
            with self.subTest(estimate=estimate):
                self.assertAlmostEqual(asked, asked_grasp_forward_mm(0.0, estimate, **NEAR), places=6)
        start = harness.view(harness.DESCENDED_POSE)
        for estimate in (-44.5, -60.0, 51.5):
            with self.subTest(estimate=estimate):
                ctx = harness.HarnessCycle(pose=harness.DESCENDED_POSE)
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failure:
                    policy_grasp.run_grasp(ctx, harness.controller_view(**NEAR), {"object_secured": False}, start,
                                           {"radial_mm": estimate - start["tool"]["out_mm"], "lateral_mm": 3.0, "aligned": True})
                self.assertEqual("approach_cube_beyond_grasp_range", harness.failure_code(ctx, failure.exception))
                self.assertEqual([], ctx.labels("run_pose"))
                self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("grasp_target_within_range")])

    def test_with_no_word_no_target_is_refused_and_the_range_is_not_stated(self):
        start = harness.view(harness.DESCENDED_POSE)
        ctx = harness.HarnessCycle({"APPROACH": harness.view(harness.GRASP_POSE), "grip:latch": [68.4], "frame": lambda _ctx: _still_frame()}, pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()):
            policy_grasp.run_grasp(ctx, harness.controller_view(), {"object_secured": False}, start, {"radial_mm": -60.0 - start["tool"]["out_mm"], "lateral_mm": 3.0, "aligned": True})
        self.assertEqual([], ctx.events.predicates("grasp_target_within_range"))


class Block:
    """A geometric fake of one block on the floor, at `out_mm` from the proven grasp radius and `lateral_mm` in the capture
    window's frame: what the wrist sees of it from any open-jaw view (a box whose alignment_from_bbox decision is the
    block's position relative to the tool), whether a close at the tool's radius holds it (the recorded held window: a close
    lands 3.5..13.1 mm beyond the estimate when it holds, widened here to HELD_WINDOW_MM), and the far-edge row a reopen frame
    shows (in the band when held, below it when the block is nearer than the jaws, above it when farther)."""

    HELD_WINDOW_MM = (2.0, 14.0)
    WIDTH_PX = 300.0

    def __init__(self, out_mm, lateral_mm=4.5):
        self.out_mm, self.lateral_mm, self.closes = float(out_mm), float(lateral_mm), []

    def bbox(self, tool) -> list:
        px_per_mm = cycle.GRASP_REF_WIDTH_PX / cycle.CUBE_EDGE_MM
        scale = cycle.GRASP_REF_WIDTH_PX / self.WIDTH_PX
        centre_x = cycle.NADIR_COLUMN_PX + (cycle.GRASP_REF_CENTER_PX[0] + self.lateral_mm * px_per_mm - cycle.NADIR_COLUMN_PX) / scale
        row = cycle.nadir_row_px(tool["pitch_deg"])
        centre_y = row + (cycle.GRASP_REF_CENTER_PX[1] - (self.out_mm - tool["out_mm"]) * px_per_mm - row) / scale
        half_w, half_h = self.WIDTH_PX / 2.0, self.WIDTH_PX * 1.1 / 2.0
        return [centre_x - half_w, centre_y - half_h, centre_x + half_w, centre_y + half_h]

    def seen(self, ctx) -> list:
        return [detected(self.bbox(cycle.tool_state(ctx.current)), f"obs-look-{len(self.closes)}", clipped={"top": False, "bottom": False, "any_border": False})]

    def side(self, close_out):
        low, high = self.HELD_WINDOW_MM
        return "nearer" if close_out > self.out_mm + high else "farther" if close_out < self.out_mm + low else "in_band"

    def latch(self, ctx):
        close_out = cycle.tool_state(ctx.current)["out_mm"]
        self.closes.append(close_out)
        return gripper_answer(68.4 if self.side(close_out) == "in_band" else 67.2)(ctx)

    def reopen(self, ctx):
        row = {"nearer": 560, "farther": 300, "in_band": 380}[self.side(self.closes[-1])]
        return gripper_answer(79.9, detected([414, row, 761, 720], f"obs-reopen-{len(self.closes)}"))(ctx)

    def script(self, **more) -> dict:
        return {"grip:latch": self.latch, "grip:open": self.reopen, "perception:GRASP_RETRY lift open": self.seen, "perception:GRASP_RETRY pan trim": self.seen,
                "observe": lambda ctx: harness.view(ctx.current, self.seen(ctx)), "frame": lambda _ctx: _still_frame(), **more}


RETRY = {"PLACEMENT_ENVELOPE": ("grasp_measured_retry",)}
NEAR_RETRY = {"PLACEMENT_ENVELOPE": ("near_grasp", "grasp_measured_retry")}


def run_on(block, estimate_mm, words, mission=False, **script):
    """run_grasp from the recorded descended view, ALIGN having measured the block at estimate_mm: (ctx, outcome or the reason code).
    `mission`: the run carries a mission target (ctx.target), as a two-cube mission's pick does."""
    start = harness.view(harness.DESCENDED_POSE)
    ctx = harness.HarnessCycle(block.script(**script), pose=harness.DESCENDED_POSE)
    if mission:
        ctx.target, ctx._mission = types.SimpleNamespace(target_id="harness-target"), {}
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            return ctx, policy_grasp.run_grasp(ctx, harness.controller_view(**words), {"object_secured": False}, start,
                                               {"radial_mm": estimate_mm - start["tool"]["out_mm"], "lateral_mm": 4.5, "aligned": True})
        except cycle.CycleFailed as failure:
            return ctx, harness.failure_code(ctx, failure)


def retry_states(ctx) -> list:
    return [entry for entry in ctx.transitions if entry["state"] == "GRASP_RETRY"]


class TheMeasuredRetry(unittest.TestCase):
    """grasp_measured_retry: after an empty close the arm lifts open, looks, re-aligns and aims at the new estimate, never at a
    radius that closed empty; with no word the proven outward advance is unchanged."""

    def test_the_fake_blocks_box_decides_the_blocks_own_position(self):
        tool = cycle.tool_state(harness.DESCENDED_POSE)
        for out, lateral in ((-23.0, 4.5), (30.0, 1.0), (0.0, 15.0)):
            decision = cycle.alignment_from_bbox(Block(out, lateral).bbox(tool), tool["pitch_deg"])
            self.assertAlmostEqual(out, tool["out_mm"] + decision["radial_mm"], places=6)
            self.assertAlmostEqual(lateral, decision["lateral_mm"], places=6)

    def test_the_recorded_miss_retries_inward_and_holds_under_near_grasp(self):
        block = Block(MISS_TOOL_OUT_MM + MISS_RADIAL_MM)  # the block ALIGN measured: -23.0 mm
        ctx, outcome = run_on(block, block.out_mm, NEAR_RETRY)
        self.assertIsInstance(outcome, policy_grasp.GraspOutcome, outcome)
        self.assertEqual(2, len(block.closes))
        self.assertAlmostEqual(-9.0, block.closes[0], delta=1.0)
        self.assertLess(block.closes[1], block.closes[0] - 2.9, "the retry goes inward, never outward")
        self.assertEqual(["inward"], [entry["direction"] for entry in retry_states(ctx)])
        self.assertEqual("nearer", retry_states(ctx)[0]["far_edge"])
        self.assertIn("GRASP_RETRY lift open", ctx.labels("run_pose"))
        self.assertNotIn("GRASP_RETRY_ADVANCE", ctx.states())
        self.assertTrue(all(entry["value"] for entry in ctx.events.predicates("cube_seen_after_empty_close")))

    def test_a_block_farther_than_measured_retries_outward(self):
        block = Block(30.0)
        ctx, outcome = run_on(block, 15.0, RETRY)  # ALIGN under-read by 15 mm: the first close lands short of it
        self.assertIsInstance(outcome, policy_grasp.GraspOutcome, outcome)
        self.assertEqual(["outward"], [entry["direction"] for entry in retry_states(ctx)])
        self.assertAlmostEqual(block.out_mm + cycle.APPROACH_REACH_LEAD_MM, block.closes[-1], delta=1.0, msg="aimed at the block measured again, not 14 mm beyond the last close")

    def test_without_near_grasp_the_recorded_miss_is_refused_from_the_observation_height(self):
        block = Block(-23.0)
        ctx, outcome = run_on(block, -23.0, RETRY)
        self.assertEqual("approach_cube_beyond_grasp_range", outcome)
        self.assertEqual(1, len(block.closes))
        self.assertEqual("GRASP_RETRY lift open", ctx.labels("run_pose")[-1])
        self.assertGreaterEqual(ctx.transitions[-1]["block_estimate_mm"], -23.5)
        self.assertGreaterEqual(cycle.tool_state(ctx.current)["up_mm"], policy_grasp.RETRY_OBSERVE_MIN_UP_MM)

    def test_three_empty_closes_end_grasp_missed_lifted_open_never_twice_at_one_radius(self):
        block = Block(-10.0)
        block.HELD_WINDOW_MM = (99.0, 100.0)  # nothing holds: the jaws never find the window
        ctx, outcome = run_on(block, -10.0, NEAR_RETRY)
        self.assertEqual("grasp_missed", outcome)
        self.assertEqual(3, len(block.closes))
        for i, first in enumerate(block.closes):
            for second in block.closes[i + 1:]:
                self.assertGreaterEqual(abs(first - second), policy_grasp.EMPTY_RADIUS_EXCLUSION_MM - 0.1, block.closes)
        self.assertEqual("GRASP_RETRY lift open", ctx.labels("run_pose")[-1])
        self.assertEqual(["latch", "open"] * 3, ctx.labels("grip"))
        _code, _message, values, evidence = [args for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]
        self.assertAlmostEqual(-10.0, values["block_estimate_mm"], delta=0.2)
        self.assertGreaterEqual(values["tool_up_mm"], policy_grasp.RETRY_OBSERVE_MIN_UP_MM)
        self.assertIs(evidence[-1]["perception"][-1]["cube"]["detected"], True)

    def test_a_block_not_seen_after_the_lift_is_looked_at_once_more_then_named_lost(self):
        block = Block(-23.0)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY, **{"perception:GRASP_RETRY lift open": lambda _ctx: [harness.cube_record(None)], "observe": harness.view(harness.GRASP_POSE, [harness.cube_record(None)])})
        self.assertEqual("grasp_retry_cube_lost", outcome)
        self.assertEqual(["observe"], ctx.labels("observe"))
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("cube_seen_after_empty_close")])

    def test_a_block_seen_on_the_second_look_is_retried(self):
        block = Block(-23.0)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY, **{"perception:GRASP_RETRY lift open": lambda _ctx: [harness.cube_record(None)]})
        self.assertIsInstance(outcome, policy_grasp.GraspOutcome, outcome)
        self.assertEqual(["observe"], ctx.labels("observe"))

    def test_a_block_off_the_capture_window_is_trimmed_to_before_the_retry(self):
        block = Block(-23.0)
        off = Block(-23.0, lateral_mm=16.0)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY, **{"perception:GRASP_RETRY lift open": lambda ctx: off.seen(ctx)})
        self.assertIsInstance(outcome, policy_grasp.GraspOutcome, outcome)
        self.assertEqual(1, ctx.labels("run_pose").count("GRASP_RETRY pan trim"))

    def test_a_block_that_stays_off_the_window_is_refused_after_two_trims(self):
        block = Block(-23.0, lateral_mm=16.0)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY)
        self.assertEqual("grasp_retry_not_aligned", outcome)
        self.assertEqual(policy_grasp.RETRY_TRIMS_MAX, ctx.labels("run_pose").count("GRASP_RETRY pan trim"))

    def test_jaws_that_do_not_reopen_are_named_before_any_lift(self):
        block = Block(-23.0)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY, **{"grip:open": [74.0]})
        self.assertEqual("gripper_did_not_open", outcome)
        self.assertNotIn("GRASP_RETRY lift open", ctx.labels("run_pose"))

    def test_a_refused_lift_is_named_with_the_jaws_open(self):
        block = Block(-23.0)

        def no_open_lift(measured, forward_m, dz_m, holding):
            if forward_m == 0.0 and dz_m > 0.0 and not holding:
                raise cycle.ScanRefused("harness: the gate refuses this lift")
            return cycle.cartesian_step_pose(measured, forward_m, dz_m, holding)

        ctx, outcome = run_on(block, -23.0, {**NEAR_RETRY, "cartesian_step_pose": no_open_lift})
        self.assertEqual("grasp_retry_lift_refused", outcome)
        self.assertEqual(["latch", "open"], ctx.labels("grip"))
        self.assertNotIn("GRASP_RETRY lift open", ctx.labels("run_pose"))

    def test_a_target_moved_to_a_zones_edge_leaves_the_zone_on_the_side_asked(self):
        # A close radius the solver reaches (FK of the grasp geometry) whose zone edge crosses a power of two: radius - 3.0
        # and radius + 3.0 differ from the radius by 2.9999999999999982 in floating point.
        radius = 14.724353416506455
        inward = policy_grasp.grasp_target_mm(-29.694721341417704, (-radius,), (-25.0, 60.0), 14.0, prefer=-1)
        self.assertAlmostEqual(-radius - 3.0, inward["target_mm"], places=6)
        outward = policy_grasp.grasp_target_mm(0.0, (radius,), (-25.0, 60.0), 14.0, prefer=1)
        self.assertAlmostEqual(radius + 3.0, outward["target_mm"], places=6)
        two = policy_grasp.grasp_target_mm(-28.0, (-9.3, -radius), (-25.0, 60.0), 14.0, prefer=-1)
        self.assertAlmostEqual(-radius - 3.0, two["target_mm"], places=6, msg="inward of both empty closes, never outward of them")

    def test_every_close_radius_the_solver_reaches_is_left_on_the_preferred_side(self):
        for forward_mm in [step / 4.0 for step in range(-100, 229)]:
            radius = cycle.tool_state({**harness.GRASP_POSE, **cycle.cli._proven_grasp_shifted(forward_mm / 1000.0, 0.002)})["out_mm"]
            for prefer, edge in ((-1, radius - 3.0), (1, radius + 3.0)):
                if not -25.0 <= edge <= 60.0:
                    continue
                with self.subTest(radius=radius, prefer=prefer):
                    aim = policy_grasp.grasp_target_mm(radius - 14.0, (radius,), (-25.0, 60.0), 14.0, prefer=prefer)
                    self.assertAlmostEqual(edge, aim["target_mm"], places=6)

    def test_a_block_nearer_than_measured_is_retried_inward_at_a_zone_edge_that_crosses_a_power_of_two(self):
        block = Block(-30.75)
        block.HELD_WINDOW_MM = (2.0, 12.0)
        ctx, outcome = run_on(block, -29.75, NEAR_RETRY)
        self.assertIsInstance(outcome, policy_grasp.GraspOutcome, outcome)
        self.assertTrue(retry_states(ctx))
        self.assertEqual(["inward"] * len(retry_states(ctx)), [entry["direction"] for entry in retry_states(ctx)])
        self.assertEqual(sorted(block.closes, reverse=True), block.closes, "every retry goes inward")

    def test_a_retry_the_geometry_gates_refuse_is_named_with_the_jaws_open(self):
        def refuse_far(measured, forward_m, dz_m):
            if forward_m > 0.035:
                raise cycle.ScanRefused("harness: the gate refuses this grasp geometry")
            return cycle.proven_grasp_pose(measured, forward_m, dz_m)

        block = Block(30.0)
        ctx, outcome = run_on(block, 15.0, {**RETRY, "proven_grasp_pose": refuse_far})
        self.assertEqual("grasp_retry_approach_refused", outcome)
        self.assertEqual(1, len(block.closes))
        self.assertEqual(["latch", "open"], ctx.labels("grip"))
        self.assertEqual(["outward"], [entry["direction"] for entry in retry_states(ctx)])

    def test_with_no_word_the_retry_is_the_proven_outward_advance(self):
        block = Block(-23.0)
        ctx, outcome = run_on(block, -23.0, {})
        self.assertEqual("grasp_missed", outcome)
        self.assertEqual(2, ctx.states().count("GRASP_RETRY_ADVANCE"))
        self.assertEqual([], [label for label in ctx.labels() if label.startswith("GRASP_RETRY")])
        self.assertEqual(sorted(block.closes), block.closes, "the proven retry only advances")


class CountedBlock(Block):
    """A Block whose every view records how many cubes the recognizer verified (candidates_verified; None: not recorded)."""

    def __init__(self, out_mm, verified, lateral_mm=4.5):
        super().__init__(out_mm, lateral_mm)
        self.verified = verified

    def seen(self, ctx) -> list:
        records = super().seen(ctx)
        for record in records:
            if self.verified is not None:
                record["cube"]["candidates_verified"] = self.verified
        return records


class TheMeasuredRetryOfAMissionTarget(unittest.TestCase):
    """A mission target is approached only when its cube is the one cube in view (ALIGN's mission rule): every look a
    retry re-aims from is held to the same rule before the arm moves toward it."""

    def test_a_retry_look_with_one_verified_cube_is_retried(self):
        block = CountedBlock(-23.0, 1)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY, mission=True)
        self.assertIsInstance(outcome, policy_grasp.GraspOutcome, outcome)
        self.assertEqual(2, len(block.closes))

    def test_a_retry_look_with_two_verified_cubes_or_no_count_is_refused_before_the_arm_moves(self):
        for verified, code in ((2, "align_more_than_one_cube_verified"), (None, "align_cube_count_not_recorded")):
            with self.subTest(verified=verified):
                block = CountedBlock(-23.0, verified)
                ctx, outcome = run_on(block, -23.0, NEAR_RETRY, mission=True)
                self.assertEqual(code, outcome)
                self.assertEqual(1, len(block.closes))
                self.assertEqual("GRASP_RETRY lift open", ctx.labels("run_pose")[-1])
                self.assertNotIn("GRASP_RETRY", ctx.states())

    def test_a_second_cube_seen_on_the_stationary_recapture_is_refused(self):
        block = CountedBlock(-23.0, 2)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY, mission=True, **{"perception:GRASP_RETRY lift open": lambda _ctx: [harness.cube_record(None)]})
        self.assertEqual("align_more_than_one_cube_verified", outcome)
        self.assertEqual(["observe"], ctx.labels("observe"))
        self.assertEqual(1, len(block.closes))

    def test_a_second_cube_seen_after_a_pan_trim_is_refused_before_the_next_close(self):
        block = CountedBlock(-23.0, 2)
        off = CountedBlock(-23.0, 1, lateral_mm=16.0)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY, mission=True, **{"perception:GRASP_RETRY lift open": lambda ctx: off.seen(ctx)})
        self.assertEqual("align_more_than_one_cube_verified", outcome)
        self.assertEqual("GRASP_RETRY pan trim", ctx.labels("run_pose")[-1])
        self.assertEqual(1, len(block.closes))

    def test_without_a_mission_target_the_count_is_not_read(self):
        block = CountedBlock(-23.0, 2)
        ctx, outcome = run_on(block, -23.0, NEAR_RETRY)
        self.assertIsInstance(outcome, policy_grasp.GraspOutcome, outcome)


# Gripper readbacks after a close, recorded (the grasp review of 2026-09-26): the ends of the 2026-09-20 empties (66.874..67.081)
# and one between, the 19:19 miss's three, and the holds (67.909..68.392, today's 67.909 and 68.185).
RECORDED_EMPTY = (66.874, 67.012, 67.081, 67.150, 67.288, 67.357)
RECORDED_HELD = (67.909, 68.185, 68.323, 68.392)


class TheLatchMidpoint(unittest.TestCase):
    """latch_midpoint: the empty-latch class at the midpoint of the recorded classes; with no word the controller's own."""

    def test_the_recorded_classes_classify_under_the_word(self):
        ctl = harness.controller_view()
        words = ("latch_midpoint",)
        self.assertAlmostEqual(67.633, policy_grasp.latch_threshold(ctl, words), places=3)
        for reading in RECORDED_EMPTY:
            self.assertFalse(policy_grasp.close_held(ctl, reading, words), reading)
        for reading in RECORDED_HELD:
            self.assertTrue(policy_grasp.close_held(ctl, reading, words), reading)
        for reading in (float("nan"), 72.5):
            self.assertFalse(policy_grasp.close_held(ctl, reading, words), reading)

    def test_one_readback_step_above_the_worst_empty_is_empty_under_the_word_and_the_controller(self):
        ctl = harness.controller_view()
        one_step_up = 67.357 + 0.069
        self.assertFalse(policy_grasp.close_held(ctl, one_step_up), "the controller's class calls it empty")
        self.assertFalse(policy_grasp.close_held(ctl, one_step_up, ("latch_midpoint",)))

    def test_with_no_word_it_is_the_controllers_predicate(self):
        ctl = harness.controller_view()
        for reading in RECORDED_EMPTY + RECORDED_HELD + (66.8, 67.4, 67.401, 72.0, 72.1):
            self.assertEqual(cycle.closed_on_something(reading), policy_grasp.close_held(ctl, reading), reading)
        self.assertEqual(cycle.GRIPPER_LATCH + cycle.GRIPPER_EMPTY_LATCH_MARGIN, policy_grasp.latch_threshold(ctl))

    def test_the_close_is_classed_and_stated_at_the_words_threshold(self):
        # 67.60 lies between the controller's threshold (67.53, operator ruling 2026-10-09) and the word's (67.633, its
        # recorded classes predate that ruling), a value no readback takes (the readback moves in steps of 100/1449: 67.564
        # and 67.633 are the grid values either side). Before the ruling the controller's sat above the word's.
        for words, retried in (((), 0), (("latch_midpoint",), 1)):
            with self.subTest(words=words):
                ctx = harness.HarnessCycle({"grip:latch": [67.60, 68.4], "frame": lambda _ctx: _still_frame()}, pose=harness.DESCENDED_POSE)
                with contextlib.redirect_stdout(io.StringIO()):
                    policy_grasp.run_grasp(ctx, harness.controller_view(PLACEMENT_ENVELOPE=words), {"object_secured": False}, harness.view(harness.DESCENDED_POSE),
                                           {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
                closes = ctx.events.predicates("closed_on_something")
                self.assertEqual(retried, ctx.states().count("GRASP_RETRY_ADVANCE"))
                self.assertEqual(policy_grasp.latch_threshold(harness.controller_view(), words), closes[0]["threshold"])


# Every gripper readback after a latch close on record (2026-09-19 to 2026-09-27), each once, with the class the run itself
# established: an empty close retried or ended grasp_missed with the block seen outside the jaws; a hold went on to
# OBJECT_SECURED on the retention score, or (2026-09-20 09:58 and 2026-09-27 14:24) was stopped by the base-zero guard with
# the block recorded between the jaws. Readbacks are whole steps of 100/1449 of the calibrated range.
READBACK_STEP = 100.0 / 1449.0
LATCH_READBACKS_EMPTY = (67.012, 67.012, 66.874, 67.081, 67.012, 67.012, 67.012, 67.081, 67.288, 67.150, 67.357)
LATCH_READBACKS_HELD = (68.392, 68.392, 68.323, 68.323, 68.323, 67.909, 67.909, 67.909, 67.909, 67.909, 67.909, 67.909, 68.185, 67.909)
# 2026-10-09 (operator ruling): latch closes to 66.8 in free air at the mission start pose, and one at the grasp pose of run
# console-20261009T075942Z-m769b8e5 with the block squeezed between both jaws on the wrist camera. That run's own three
# closes read the same 67.633 and were classed empty by the 0.87 margin; they are not counted here.
LATCH_READBACKS_EMPTY_20261009 = (67.012, 66.874, 67.012)
LATCH_READBACKS_HELD_20261009 = (67.633,)


class TheEmptyLatchClass(unittest.TestCase):
    """The controller's closed_on_something against every recorded latch readback, in readback steps."""

    def test_every_recorded_close_is_classed_as_the_run_established_it(self):
        self.assertEqual((11, 14), (len(LATCH_READBACKS_EMPTY), len(LATCH_READBACKS_HELD)))
        for reading in LATCH_READBACKS_EMPTY + LATCH_READBACKS_EMPTY_20261009:
            self.assertFalse(cycle.closed_on_something(reading), reading)
        for reading in LATCH_READBACKS_HELD + LATCH_READBACKS_HELD_20261009:
            self.assertTrue(cycle.closed_on_something(reading), reading)

    def test_the_recorded_readbacks_are_whole_readback_steps(self):
        for reading in LATCH_READBACKS_EMPTY + LATCH_READBACKS_HELD + LATCH_READBACKS_EMPTY_20261009 + LATCH_READBACKS_HELD_20261009:
            steps = reading / READBACK_STEP
            self.assertAlmostEqual(round(steps), steps, delta=0.02, msg=reading)

    def test_the_threshold_sits_between_readback_steps_with_margin_on_both_sides(self):
        threshold = cycle.GRIPPER_LATCH + cycle.GRIPPER_EMPTY_LATCH_MARGIN
        steps = threshold / READBACK_STEP
        self.assertGreaterEqual(abs(steps - round(steps)), 0.4, "no readback may fall on the threshold")
        empties, holds = LATCH_READBACKS_EMPTY + LATCH_READBACKS_EMPTY_20261009, LATCH_READBACKS_HELD + LATCH_READBACKS_HELD_20261009
        worst_empty, lowest_hold = max(empties) / READBACK_STEP, min(holds) / READBACK_STEP
        # operator ruling 2026-10-09: the 2026-10-09 hold at step 980 leaves four steps between the classes (976..980); the
        # threshold sits at their midpoint, half a step off the grid (it was four above the worst empty and three below the
        # lowest hold while the lowest hold was 984).
        self.assertGreaterEqual(steps - worst_empty, 2.0, "an empty close must drift two steps before it reads as held")
        self.assertGreaterEqual(lowest_hold - steps, 1.0, "a hold must read one step lower before it reads as empty")

    def test_the_grid_values_either_side_of_the_threshold_are_classed_apart(self):
        below = 978 * READBACK_STEP  # 67.495, two steps above the worst empty
        above = 979 * READBACK_STEP  # 67.564, one step below the lowest hold
        self.assertFalse(cycle.closed_on_something(below))
        self.assertTrue(cycle.closed_on_something(above))
        # operator ruling 2026-10-09: the word's threshold (67.633) now sits above the controller's, so under the word
        # both grid values either side of the controller's threshold read empty (they agreed while both sat at 980..981).
        self.assertFalse(policy_grasp.close_held(harness.controller_view(), below, ("latch_midpoint",)))
        self.assertFalse(policy_grasp.close_held(harness.controller_view(), above, ("latch_midpoint",)))


def _changed_frame():
    import numpy as np

    return np.random.default_rng(7).integers(0, 255, (720, 1280), dtype=np.uint8)


class ReadbackAt(harness.HarnessCycle):
    """The harness with a gripper readback at the pose a label reaches (`readbacks`: label -> reading), as the arm's
    readback moves while it carries: a block that slipped out lets the jaws close on to the empty-latch value."""

    readbacks = {}

    def run_pose(self, target, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
        result = super().run_pose(target, holding, floor_m, pan_allowance, label, manipulation_step)
        if label in self.readbacks:
            self.current["gripper"] = float(self.readbacks[label])
            result = {**result, "measured": {**result["measured"], "gripper": float(self.readbacks[label])}}
        return result


class TheSetDown(unittest.TestCase):
    """grasp_recover: after a failed retention with the block still between the jaws, lower it to where it was closed on,
    open, lift open, and end with nothing held; with no word the arm keeps it up with the gripper closed."""

    def script(self, confirm_sees_the_block=True):
        frames = iter([_still_frame(), _changed_frame()])
        confirm = [detected([414, 380, 761, 720], "obs-confirm")] if confirm_sees_the_block else [harness.cube_record(None)]
        return {"grip:latch": [68.4], "frame": lambda _ctx: next(frames), "perception:CONFIRM lift": [confirm]}

    def run_with(self, words, **script):
        ctx = harness.HarnessCycle({**self.script(), **script}, pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failure:
            policy_grasp.run_grasp(ctx, harness.controller_view(PLACEMENT_ENVELOPE=words), {"object_secured": False}, harness.view(harness.DESCENDED_POSE),
                                   {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        return ctx, harness.failure_code(ctx, failure.exception)

    def test_with_no_word_the_arm_keeps_the_block_up_with_the_gripper_closed(self):
        ctx, code = self.run_with(())
        self.assertEqual("retention_not_demonstrated", code)
        self.assertEqual(["latch"], ctx.labels("grip"))
        self.assertEqual("CONFIRM lift", ctx.labels("run_pose")[-1])

    def test_under_the_word_the_block_is_set_down_where_it_was_closed_on_and_nothing_is_held(self):
        ctx, code = self.run_with(("grasp_recover",))
        self.assertEqual("retention_not_demonstrated_set_down", code)
        self.assertEqual(["latch", "open"], ctx.labels("grip"))
        labels = ctx.labels("run_pose")
        lowered = [label for label in labels if label == "GRASP_SET_DOWN lower"]
        self.assertTrue(lowered)
        self.assertLess(labels.index("GRASP_SET_DOWN lower"), len(labels) - 1)
        self.assertEqual("GRASP_SET_DOWN lift open", labels[-1])
        self.assertIn("GRASP_SET_DOWN", ctx.states())
        calls = ctx.calls
        opened_at = calls.index(("grip", "open"))
        self.assertEqual(("run_pose", "GRASP_SET_DOWN lower"), calls[opened_at - 1], "the jaws open only once the block is lowered")
        self.assertGreaterEqual(cycle.tool_state(ctx.current)["up_mm"], policy_grasp.RETRY_OBSERVE_MIN_UP_MM)

    def test_under_the_word_a_block_not_seen_between_the_jaws_is_not_set_down(self):
        ctx, code = self.run_with(("grasp_recover",), **{"perception:CONFIRM lift": [[harness.cube_record(None)]]})
        self.assertEqual("retention_not_demonstrated", code)
        self.assertEqual(["latch"], ctx.labels("grip"))

    def test_under_the_word_a_block_seen_out_of_the_held_band_is_not_set_down(self):
        for row in (100, 560):  # the far edge above the band (farther than the jaws) and below it (nearer)
            with self.subTest(row=row):
                confirm = detected([414, row, 761, 720], "obs-confirm", clipped={"top": False, "bottom": row > 420, "any_border": row > 420})
                ctx, code = self.run_with(("grasp_recover",), **{"perception:CONFIRM lift": [[confirm]]})
                self.assertEqual("retention_not_demonstrated", code)
                self.assertEqual(["latch"], ctx.labels("grip"))
                self.assertEqual("CONFIRM lift", ctx.labels("run_pose")[-1])
                self.assertNotIn("GRASP_SET_DOWN", ctx.states())

    def test_under_the_word_jaws_that_closed_on_to_the_empty_latch_do_not_set_down(self):
        for row in (380, 560):
            with self.subTest(row=row):
                confirm = detected([414, row, 761, 720], "obs-confirm")
                ctx = ReadbackAt({**self.script(), "perception:CONFIRM lift": [[confirm]]}, pose=harness.DESCENDED_POSE)
                ctx.readbacks = {"CONFIRM lift": 66.9}  # the block slipped out: the jaws closed on to the empty-latch value
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failure:
                    policy_grasp.run_grasp(ctx, harness.controller_view(PLACEMENT_ENVELOPE=("grasp_recover",)), {"object_secured": False}, harness.view(harness.DESCENDED_POSE),
                                           {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
                self.assertEqual("retention_not_demonstrated", harness.failure_code(ctx, failure.exception))
                self.assertEqual(["latch"], ctx.labels("grip"))
                self.assertNotIn("GRASP_SET_DOWN", ctx.states())

    def test_under_the_word_a_confirm_readback_still_held_sets_down(self):
        ctx = ReadbackAt(self.script(), pose=harness.DESCENDED_POSE)
        ctx.readbacks = {"CONFIRM lift": 68.3}
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failure:
            policy_grasp.run_grasp(ctx, harness.controller_view(PLACEMENT_ENVELOPE=("grasp_recover",)), {"object_secured": False}, harness.view(harness.DESCENDED_POSE),
                                   {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        self.assertEqual("retention_not_demonstrated_set_down", harness.failure_code(ctx, failure.exception))
        self.assertEqual(["latch", "open"], ctx.labels("grip"))

    def test_a_lowering_the_gate_refuses_is_named_with_the_block_still_held(self):
        def no_lowering(measured, forward_m, dz_m, holding):
            if holding and dz_m < 0.0:
                raise cycle.ScanRefused("harness: the gate refuses this lowering")
            return cycle.cartesian_step_pose(measured, forward_m, dz_m, holding)

        ctx = harness.HarnessCycle(self.script(), pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failure:
            policy_grasp.run_grasp(ctx, harness.controller_view(PLACEMENT_ENVELOPE=("grasp_recover",), cartesian_step_pose=no_lowering), {"object_secured": False},
                                   harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        self.assertEqual("grasp_set_down_refused", harness.failure_code(ctx, failure.exception))
        self.assertEqual(["latch"], ctx.labels("grip"))


class TheSetDownLift(unittest.TestCase):
    """The open lift after a set-down is named as the set-down's own, not as a retry's look."""

    def test_a_refused_lift_after_the_set_down_is_named_with_the_jaws_open(self):
        def no_open_lift(measured, forward_m, dz_m, holding):
            if dz_m > 0.0 and not holding:
                raise cycle.ScanRefused("harness: the gate refuses this lift")
            return cycle.cartesian_step_pose(measured, forward_m, dz_m, holding)

        ctx = harness.HarnessCycle(TheSetDown().script(), pose=harness.DESCENDED_POSE)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(cycle.CycleFailed) as failure:
            policy_grasp.run_grasp(ctx, harness.controller_view(PLACEMENT_ENVELOPE=("grasp_recover",), cartesian_step_pose=no_open_lift), {"object_secured": False},
                                   harness.view(harness.DESCENDED_POSE), {"radial_mm": 0.0, "lateral_mm": 3.0, "aligned": True})
        self.assertEqual("grasp_set_down_lift_refused", harness.failure_code(ctx, failure.exception))
        self.assertEqual(["latch", "open"], ctx.labels("grip"))
        self.assertNotIn("GRASP_SET_DOWN lift open", ctx.labels("run_pose"))

    def test_a_refused_retry_lift_keeps_its_own_code(self):
        def no_open_lift(measured, forward_m, dz_m, holding):
            if forward_m == 0.0 and dz_m > 0.0 and not holding:
                raise cycle.ScanRefused("harness: the gate refuses this lift")
            return cycle.cartesian_step_pose(measured, forward_m, dz_m, holding)

        _ctx, outcome = run_on(Block(-23.0), -23.0, {**NEAR_RETRY, "cartesian_step_pose": no_open_lift})
        self.assertEqual("grasp_retry_lift_refused", outcome)


class TheGraspWords(unittest.TestCase):
    """The grasp stage declares its four words and qualifies none, so the controller admits them off the robot and a LIVE run
    naming one needs the attended label (stage_policy, THE WORDS)."""

    def test_the_controller_admits_the_grasp_words_and_qualifies_none(self):
        words = ("near_grasp", "grasp_measured_retry", "latch_midpoint", "grasp_recover")
        self.assertEqual(words, policy_grasp.WORDS)
        self.assertEqual(frozenset(), policy_grasp.QUALIFIED)
        self.assertEqual(words, cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: ",".join(words)}))
        self.assertFalse(set(words) & cycle.envelope_words_qualified())
        self.assertEqual(("near_grasp",), policy_grasp.POLICY.run_words(harness.controller_view(PLACEMENT_ENVELOPE=("projected", "near_grasp"))))


def _still_frame():
    import numpy as np

    frame = np.full((720, 1280), 120, np.uint8)
    frame[300:660, 380:800] = 200
    frame[300:380, 380:800] = 90
    return frame


if __name__ == "__main__":
    unittest.main()
