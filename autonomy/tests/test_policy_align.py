# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/tests/test_policy_align.py: the PREGRASP and ALIGN stage policy (autonomy/manipulation/policy_align.py), track A
of docs/autonomous-demo/overnight-plan-2026-09-26.md.

Always run, numbers only: the recorded boxes, joints and contact pixels come from autonomy/tests/test_recorded_pins_20260926.py
(whose local replay proves they are the record). The replay against the archived frames is autonomy/tests/test_align_replay.py.
"""
import contextlib
import io
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))

import stage_harness as harness  # noqa: E402  (puts the controller on the path)
import geometric_world as gw  # noqa: E402
import math  # noqa: E402
import policy_align  # noqa: E402
import policy_grasp  # noqa: E402
import stage_policy  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import test_recorded_pins_20260926 as pins  # noqa: E402
import test_cycle_mission_mode as mission  # noqa: E402  (the controller's main() on a scripted World)

CTL = cycle._CONTROLLER_VIEW
START_1, START_2 = "console-20260926T201540Z-mb3c1048", "console-20260926T203744Z-m2476137"


def _step(run_id: str, index: int) -> dict:
    return pins.PLACED_SCRIPTS[run_id]["steps"][index]


def _box(step: dict) -> list:
    return step["records"][-1]["cube"]["bbox_xyxy"]


# The recorded views ALIGN decided from, (box, joints): the pregrasp and descent of each recorded run.
MISS_PREGRASP = (pins.GRASP_MISS_INPUTS["align"][0]["bbox_xyxy"], pins.GRASP_MISS_INPUTS["align"][0]["joints"])
MISS_DESCENT = (pins.GRASP_MISS_INPUTS["align"][1]["bbox_xyxy"], pins.GRASP_MISS_INPUTS["align"][1]["joints"])
START_1_PREGRASP, START_1_DESCENT = ((_box(_step(START_1, i)), _step(START_1, i)["joints"]) for i in (1, 2))
START_2_PREGRASP, START_2_TRIMMED, START_2_DESCENT = ((_box(_step(START_2, i)), _step(START_2, i)["joints"]) for i in (1, 2, 4))
DESCENT_PAIRS = {"m8c5f9e9": (MISS_PREGRASP, MISS_DESCENT), "mb3c1048": (START_1_PREGRASP, START_1_DESCENT), "m2476137": (START_2_TRIMMED, START_2_DESCENT)}


def tool(joints: dict) -> dict:
    return cycle.tool_state(joints)


class TheViewModel(unittest.TestCase):
    def test_box_from_alignment_is_alignment_from_bbox_run_backwards(self):
        for lateral, radial, width, pitch in ((0.0, 0.0, 264.0, 64.5), (7.1, 3.2, 264.0, 64.48), (-30.0, 25.0, 351.0, 66.9), (40.0, -20.0, 221.0, 64.7)):
            with self.subTest((lateral, radial, width, pitch)):
                box = policy_align.box_from_alignment(CTL, lateral, radial, width, pitch)
                decision = cycle.alignment_from_bbox(box, pitch)
                self.assertAlmostEqual(lateral, decision["lateral_mm"], places=6)
                self.assertAlmostEqual(radial, decision["radial_mm"], places=6)
                self.assertAlmostEqual(width, box[2] - box[0], places=6)

    def test_the_recorded_boxes_are_whole_and_a_box_at_a_border_is_clipped_there(self):
        for box, _joints in (MISS_PREGRASP, MISS_DESCENT, START_1_PREGRASP, START_1_DESCENT, START_2_PREGRASP, START_2_DESCENT):
            self.assertEqual((), policy_align.clipped_edges(box))
        self.assertEqual(("top",), policy_align.clipped_edges([460, 0, 811, 432]))
        self.assertEqual(("bottom", "left"), policy_align.clipped_edges([1, 392, 672, 720]))
        self.assertEqual(("right",), policy_align.clipped_edges([900, 100, 1279, 300]))

    def test_a_top_clipped_box_is_rebuilt_from_its_bottom_row_and_width(self):
        whole = [460, 47 - 120, 811, 432 - 120]  # mb3c1048's descent view, 120 px higher: its top 73 px above the frame
        cut = [460, 0, 811, 432 - 120]  # what the frame shows of it
        rebuilt = policy_align.unclipped_box(cut)
        self.assertEqual([460.0, 811.0, 312.0], [rebuilt[0], rebuilt[2], rebuilt[3]])
        self.assertLess(abs(rebuilt[1] - whole[1]), 6.0)
        pitch = tool(START_1_DESCENT[1])["pitch_deg"]
        truth, truncated, restored = (cycle.alignment_from_bbox(box, pitch)["radial_mm"] for box in (whole, cut, rebuilt))
        self.assertLess(truncated, truth - 1.0, "the cut box's centre reads the block nearer than it is")
        self.assertLess(abs(restored - truth), 0.5)

    def test_a_side_clipped_or_twice_cut_box_is_not_rebuilt(self):
        self.assertIsNone(policy_align.unclipped_box([0, 100, 300, 400]))
        self.assertIsNone(policy_align.unclipped_box([400, 0, 700, 720]))
        self.assertEqual([400.0, 100.0, 700.0, 400.0], policy_align.unclipped_box([400, 100, 700, 400]))

    def test_the_predictor_reproduces_the_three_recorded_descents_within_its_tolerance(self):
        for run, ((before, joints_before), (after, joints_after)) in DESCENT_PAIRS.items():
            with self.subTest(run):
                predicted = policy_align.predict_box_after(CTL, before, tool(joints_before), tool(joints_after))["bbox_xyxy"]
                worst = max(abs(p - r) for p, r in zip(predicted, after))
                self.assertLessEqual(worst, policy_align.PREDICTOR_TOLERANCE_PX, (run, [round(v) for v in predicted], after))

    def test_the_two_named_descents_of_the_plan(self):
        # [473, 73, 737, 362] -> [460, 47, 811, 432] and [454, 65, 689, 330] -> [446, 108, 728, 427]
        self.assertEqual(([473, 73, 737, 362], [460, 47, 811, 432]), (START_1_PREGRASP[0], START_1_DESCENT[0]))
        self.assertEqual(([454, 65, 689, 330], [446, 108, 728, 427]), (START_2_TRIMMED[0], START_2_DESCENT[0]))

    def test_the_worst_descent_realization_is_the_recorded_one(self):
        realized = []
        for run_id, index in ((START_1, 2), (START_2, 4)):
            step = _step(run_id, index)
            target, reached = tool({**step["joints"], **step["target"]}), tool(step["joints"])
            realized.append({key: reached[key] - target[key] for key in ("out_mm", "up_mm", "pitch_deg")})
        worst = {"out_mm": min(r["out_mm"] for r in realized), "up_mm": min(r["up_mm"] for r in realized), "pitch_deg": max(r["pitch_deg"] for r in realized)}
        for key, value in worst.items():
            self.assertAlmostEqual(value, policy_align.DESCENT_WORST_REALIZATION[key], delta=0.01)

    def test_the_recorded_pregrasp_views_centre_the_block_between_10_and_18_mm_outward_which_brackets_the_bias(self):
        biases = [policy_align.pregrasp_centring_bias_mm(CTL, box, tool(joints)) for box, joints in (MISS_PREGRASP, START_1_PREGRASP, START_2_PREGRASP, START_2_TRIMMED)]
        self.assertTrue(all(10.0 <= bias <= 18.0 for bias in biases), biases)
        self.assertLessEqual(min(biases), cycle.PREGRASP_OUTWARD_BIAS_MM)
        self.assertGreaterEqual(max(biases), cycle.PREGRASP_OUTWARD_BIAS_MM)
        for box, joints in (START_1_PREGRASP, START_2_PREGRASP):
            centred = policy_align.box_from_alignment(CTL, 0.0, -policy_align.pregrasp_centring_bias_mm(CTL, box, tool(joints)), box[2] - box[0], tool(joints)["pitch_deg"])
            self.assertAlmostEqual(policy_align.FRAME_HEIGHT_PX / 2.0, (centred[1] + centred[3]) / 2.0, places=6)


def todays_descent(pregrasp_joints: dict, realization=None) -> dict:
    """The tool after today's ALIGN descent from `pregrasp_joints`: the proven descent at out + 3 mm and the lowest clearance
    (cycle:1737), realized as the worst recorded descent did (DESCENT_WORST_REALIZATION) unless another is given."""
    before = tool(pregrasp_joints)
    target = cycle.cli.pregrasp_descend_poses(pregrasp_joints, (max(before["out_mm"], 0.0) + 3.0) / 1000.0, 0.043)[-1]
    planned = tool({**pregrasp_joints, **target.joints})
    realization = policy_align.DESCENT_WORST_REALIZATION if realization is None else realization
    return {**planned, **{key: planned[key] + realization[key] for key in realization}}


class FailuresSixAndSeven(unittest.TestCase):
    """A block read at radial +12.4 (21:31) or +14.5 (21:24) at the pregrasp leaves the top of the frame after today's
    descent: the predictor puts its top row above the frame. Their own records are on the robot; the pregrasp view is
    mb3c1048's (the same height, the same lateral), with the block read farther out."""

    def test_a_pregrasp_radial_of_12_4_or_14_5_predicts_a_top_row_above_the_frame_after_todays_descent(self):
        box, joints = START_1_PREGRASP
        pregrasp = tool(joints)
        lateral = cycle.alignment_from_bbox(box, pregrasp["pitch_deg"])["lateral_mm"]
        for radial in (12.4, 14.5):
            with self.subTest(radial):
                seen = policy_align.box_from_alignment(CTL, lateral, radial, box[2] - box[0], pregrasp["pitch_deg"], (box[3] - box[1]) / (box[2] - box[0]))
                after = policy_align.predict_box_after(CTL, seen, pregrasp, todays_descent(joints))
                self.assertLess(policy_align.view_margins_px(after["bbox_xyxy"])["top"], 0.0)

    def test_the_recorded_success_stays_inside_the_frame_after_the_same_descent(self):
        box, joints = START_1_PREGRASP
        after = policy_align.predict_box_after(CTL, box, tool(joints), todays_descent(joints))
        self.assertGreater(policy_align.view_margins_px(after["bbox_xyxy"])["top"], 0.0)


# The SEARCH contact pixels of the three recorded runs, and the cube out ALIGN measured at each one's first pregrasp view.
RECORDED_PAIRS = ((pins.GRASP_MISS_INPUTS["search_contact_px"], MISS_PREGRASP), (_step(START_1, 0)["records"][-1]["cube"]["contact_px"], START_1_PREGRASP),
                  (_step(START_2, 0)["records"][-1]["cube"]["contact_px"], START_2_PREGRASP))


def measured_cube_out(view) -> float:
    box, joints = view
    return tool(joints)["out_mm"] + cycle.alignment_from_bbox(box, tool(joints)["pitch_deg"])["radial_mm"]


def words(*names):
    return harness.controller_view(PLACEMENT_ENVELOPE=tuple(names))


class CoarseV2(unittest.TestCase):
    def test_the_recorded_pairs_are_the_plans(self):
        self.assertEqual([-17.9, 21.3, 45.2], [round(measured_cube_out(view), 1) for _contact, view in RECORDED_PAIRS])
        self.assertEqual([696.0, 556.5, 483.8], [contact[1] for contact, _view in RECORDED_PAIRS])

    def test_with_no_word_the_coarse_pregrasp_is_the_controllers(self):
        for contact, _view in RECORDED_PAIRS:
            seen = harness.cube_record([0, 0, 10, 10], contact_px=contact)
            self.assertEqual(cycle.coarse_pregrasp_from_low_pose(contact, -9.6), policy_align.coarse_pregrasp(harness.HarnessCycle(), CTL, seen, {"shoulder_pan": -9.6}))

    def test_under_coarse_v2_each_recorded_contact_reads_within_1_5_mm_of_what_align_measured(self):
        for contact, view in RECORDED_PAIRS:
            with self.subTest(contact):
                seen = harness.cube_record([0, 0, 10, 10], contact_px=contact)
                coarse = policy_align.coarse_pregrasp(harness.HarnessCycle(), words("coarse_v2"), seen, {"shoulder_pan": -9.6})
                self.assertLessEqual(abs(coarse["cube_out_mm"] - measured_cube_out(view)), 1.5)
                legacy = cycle.coarse_pregrasp_from_low_pose(contact, -9.6)
                self.assertEqual((legacy["pan_deg"], legacy["cube_out_mm"]), (coarse["pan_deg"], coarse["legacy_cube_out_mm"]))
                self.assertEqual(round(min(max(coarse["cube_out_mm"] + cycle.PREGRASP_OUTWARD_BIAS_MM, 0.0), 60.0), 1), coarse["forward_mm"])

    def test_the_legacy_map_under_read_every_recorded_run_by_11_to_27_mm(self):
        misses = [measured_cube_out(view) - cycle.coarse_pregrasp_from_low_pose(contact, -9.6)["cube_out_mm"] for contact, view in RECORDED_PAIRS]
        self.assertEqual([26.6, 14.2, 11.1], [round(miss, 1) for miss in misses])


class ThePickupBand(unittest.TestCase):
    """The placement build's near-block refusal (search_cube_inside_pregrasp_reach), kept with its code and sentence, fed
    the refitted coarse value under coarse_v2 and bounded by policy_grasp.min_grasp_forward_mm()."""

    def refusal(self, ctl, cube_out):
        ctx = harness.HarnessCycle()
        seen = harness.cube_record([515, 485, 590, 581], contact_px=[495.0, 696.0])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                policy_align.refuse_pickup_band(ctx, ctl, {"cube_out_mm": cube_out}, seen)
        except stage_policy.CycleFailed as failure:
            fields = [args[2] for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]
            return harness.failure_code(ctx, failure), str(failure), fields, ctx.events.predicates("pregrasp_cube_within_reach")[-1]
        return None, None, None, ctx.events.predicates("pregrasp_cube_within_reach")[-1]

    def test_with_no_word_the_bound_is_minus_13_and_the_sentence_the_placement_builds(self):
        self.assertEqual(-13.0, policy_align.pickup_band_min_cube_out_mm(CTL))
        code, sentence, fields, predicate = self.refusal(CTL, -44.5)
        self.assertEqual("search_cube_inside_pregrasp_reach", code)
        self.assertEqual((-44.5, -13.0, 57.5), (fields["cube_out_mm"], fields["min_cube_out_mm"], fields["move_block_at_least_mm"]))
        self.assertIn("SEARCH: the block stands 31.5 mm nearer than the pregrasp can reach (coarse cube_out -44.5 mm, the pregrasp clamps at -13); move it at least 57.5 mm", sentence)
        self.assertEqual((False, -44.5, -13.0), (predicate["value"], predicate["measured"], predicate["threshold"]))
        self.assertIsNone(self.refusal(CTL, -13.0)[0])

    def test_under_coarse_v2_the_recorded_miss_is_refused_on_its_refitted_value_near_minus_18(self):
        ctl = words("coarse_v2")
        seen = harness.cube_record([515, 485, 590, 581], contact_px=pins.GRASP_MISS_INPUTS["search_contact_px"])
        coarse = policy_align.coarse_pregrasp(harness.HarnessCycle(), ctl, seen, {"shoulder_pan": pins.GRASP_MISS_INPUTS["search_pan_deg"]})
        self.assertLess(abs(coarse["cube_out_mm"] - (-17.9)), 1.0)
        self.assertEqual(-cycle.APPROACH_REACH_LEAD_MM, policy_align.pickup_band_min_cube_out_mm(ctl))
        code, sentence, fields, predicate = self.refusal(ctl, coarse["cube_out_mm"])
        self.assertEqual("search_cube_inside_pregrasp_reach", code)
        self.assertEqual((coarse["cube_out_mm"], -14.0), (fields["cube_out_mm"], fields["min_cube_out_mm"]))
        self.assertGreater(fields["move_block_at_least_mm"], fields["min_cube_out_mm"] - fields["cube_out_mm"])
        self.assertIn("nearer than the pregrasp can reach", sentence)
        self.assertEqual((False, -14.0), (predicate["value"], predicate["threshold"]))

    def test_under_coarse_v2_the_two_placed_runs_pass_the_band(self):
        for contact, _view in RECORDED_PAIRS[1:]:
            with self.subTest(contact):
                ctl = words("coarse_v2")
                seen = harness.cube_record([0, 0, 10, 10], contact_px=contact)
                coarse = policy_align.coarse_pregrasp(harness.HarnessCycle(), ctl, seen, {"shoulder_pan": -9.6})
                self.assertIsNone(self.refusal(ctl, coarse["cube_out_mm"])[0])

    def test_the_bound_follows_the_nearest_grasp_the_grasp_stage_plans(self):
        with mock.patch.object(policy_grasp, "min_grasp_forward_mm", lambda words=(): -25.0):
            self.assertEqual(-38.0, policy_align.pickup_band_min_cube_out_mm(CTL))
            self.assertEqual(-39.0, policy_align.pickup_band_min_cube_out_mm(words("coarse_v2")))
            self.assertIsNone(self.refusal(words("coarse_v2"), -30.0)[0])
            self.assertEqual("search_cube_inside_pregrasp_reach", self.refusal(words("coarse_v2"), -39.5)[0])

    def test_the_words_are_declared_and_none_is_qualified(self):
        self.assertIn("coarse_v2", policy_align.WORDS)
        self.assertEqual(frozenset(), policy_align.QUALIFIED)


# ---------------------------------------------------------------------------------------------------------------------
# align_v2 in the geometric World (autonomy/tests/geometric_world.py)
# ---------------------------------------------------------------------------------------------------------------------
PREGRASP_FORWARD_MM = 30.0
HOME_PAN = harness.LOW_POSE["shoulder_pan"]
REALIZATIONS = {"exact": (0.0, 0.0), "worst recorded": (-13.23, -5.72), "least recorded": (-4.98, -3.23)}


def world_at(radial_mm: float, lateral_mm: float, min_visible: float = 0.4) -> gw.World:
    """A block that reads radial_mm and lateral_mm in the first pregrasp view of a pregrasp at PREGRASP_FORWARD_MM."""
    pregrasp = tool(gw.pregrasp_pose_over(gw.World(0.0, HOME_PAN), PREGRASP_FORWARD_MM))
    cube_out = PREGRASP_FORWARD_MM + radial_mm - policy_align.RADIAL_READ_PER_UP_MM * pregrasp["up_mm"]
    reach = cycle.GRASP_TOOL_RANGE_M * 1000.0 + cube_out
    return gw.World(cube_out, HOME_PAN + math.degrees(math.asin(lateral_mm / reach)), min_visible=min_visible)


def run_align(ctx, *names):
    coarse = {"pan_deg": HOME_PAN, "forward_mm": PREGRASP_FORWARD_MM, "cube_out_mm": PREGRASP_FORWARD_MM - cycle.PREGRASP_OUTWARD_BIAS_MM}
    with contextlib.redirect_stdout(io.StringIO()):
        return policy_align.run_pregrasp_align(ctx, words(*names), harness.LOW_POSE, coarse, harness.cube_record([0, 0, 1, 1]), -30.0)


def align_states(ctx) -> list:
    return [state for state in ctx.states() if state.startswith("ALIGN")]


class AlignV2InTheWorld(unittest.TestCase):
    def test_it_converges_from_radial_minus_20_0_15_25_and_lateral_40_either_side_whatever_the_descent_realizes(self):
        for name, drift in REALIZATIONS.items():
            for radial in (-20.0, 0.0, 15.0, 25.0):
                for lateral in (-40.0, 0.0, 40.0):
                    with self.subTest(realization=name, radial=radial, lateral=lateral):
                        ctx = gw.WorldCycle(world_at(radial, lateral), descent_drift=drift, pan_shortfall_deg=0.5)
                        outcome = run_align(ctx, "align_v2")
                        self.assertTrue(outcome.decision["aligned"])
                        self.assertLessEqual(abs(outcome.decision["radial_mm"]), policy_align.ALIGN_RADIAL_BAND_MM)
                        self.assertTrue(cycle.CAPTURE_WINDOW_MM[0] <= outcome.decision["lateral_mm"] <= cycle.CAPTURE_WINDOW_MM[1])
                        self.assertFalse(outcome.view["perception"][-1]["cube"]["clipped"]["any_border"], "a clipped box was accepted")
                        self.assertGreater(outcome.view["tool"]["up_mm"], policy_align._non_contact_up_mm(CTL), "ALIGN ended below the non-contact floor")
                        self.assertIn("ALIGN non-contact descent", ctx.labels("run_pose"))

    def test_every_view_after_a_descent_shows_the_block_whole(self):
        for name, drift in REALIZATIONS.items():
            for radial in (-20.0, 0.0, 15.0, 25.0):
                with self.subTest(realization=name, radial=radial):
                    ctx = gw.WorldCycle(world_at(radial, 0.0), descent_drift=drift, pan_shortfall_deg=0.5)
                    seen = []
                    original = ctx._view

                    def recording():
                        view = original()
                        seen.append((ctx.calls[-1][1], view["perception"][-1]["cube"]))
                        return view
                    ctx._view = recording
                    run_align(ctx, "align_v2")
                    after = [cube for label, cube in seen if label == "ALIGN non-contact descent"]
                    self.assertTrue(after)
                    self.assertTrue(all(cube["detected"] and not cube["clipped"]["any_border"] for cube in after), after)

    def test_failures_6_and_7_under_align_v2_step_radially_before_any_descent(self):
        for radial in (12.4, 14.5):
            with self.subTest(radial):
                ctx = gw.WorldCycle(world_at(radial, 4.5), descent_drift=REALIZATIONS["worst recorded"])
                run_align(ctx, "align_v2")
                self.assertEqual(["ALIGN", "ALIGN_RADIAL_STEP"], align_states(ctx)[:2])
                labels = ctx.labels("run_pose")
                self.assertLess(labels.index("ALIGN radial step"), labels.index("ALIGN non-contact descent"))

    def test_with_no_word_the_same_block_is_descended_on_at_once(self):
        ctx = gw.WorldCycle(world_at(14.5, 4.5), descent_drift=REALIZATIONS["worst recorded"])
        run_align(ctx)
        self.assertEqual(["PREGRASP (non-contact)", "ALIGN non-contact descent"], ctx.labels("run_pose"))

    def test_a_descent_view_with_no_block_gets_a_recapture_then_a_step_back_up_never_not_detected_at_the_first_loss(self):
        # views: 1 the pregrasp, 2 the descent, 3 the stationary re-capture; the block is hidden in 2 and 3
        ctx = gw.WorldCycle(world_at(0.0, 4.5), descent_drift=REALIZATIONS["least recorded"], hide={2, 3})
        outcome = run_align(ctx, "align_v2")
        self.assertTrue(outcome.decision["aligned"])
        states = align_states(ctx)
        self.assertEqual(["ALIGN", "ALIGN_DESCENT", "ALIGN_RECAPTURE", "ALIGN_REACQUIRE"], states[:4])
        self.assertEqual(("observe", "observe"), ctx.calls[ctx.calls.index(("run_pose", "ALIGN non-contact descent")) + 1])
        self.assertIn("ALIGN re-acquire step back", ctx.labels("run_pose"))
        self.assertNotIn("align_cube_not_detected", [args[0] for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"])

    def test_a_stationary_recapture_that_finds_the_block_goes_on_without_moving(self):
        ctx = gw.WorldCycle(world_at(0.0, 4.5), descent_drift=REALIZATIONS["least recorded"], hide={2})
        run_align(ctx, "align_v2")
        self.assertEqual(["ALIGN", "ALIGN_DESCENT", "ALIGN_RECAPTURE", "ALIGN"], align_states(ctx)[:4])
        self.assertNotIn("ALIGN re-acquire step back", ctx.labels("run_pose"))

    def failure(self, ctx):
        with self.assertRaises(stage_policy.CycleFailed):
            run_align(ctx, "align_v2")
        return [(args[0], args[2]) for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]

    def test_a_block_lost_after_every_descent_ends_align_cube_lost_after_reacquire_clear_of_the_block(self):
        ctx = gw.WorldCycle(world_at(0.0, 4.5), descent_drift=REALIZATIONS["least recorded"], hide=lambda label: label in ("ALIGN non-contact descent", "observe"))
        code, values = self.failure(ctx)
        self.assertEqual(("align_cube_lost_after_reacquire", policy_align.ALIGN_REACQUIRE_MAX), (code, values["reacquires"]))
        self.assertEqual(policy_align.ALIGN_REACQUIRE_MAX, align_states(ctx).count("ALIGN_REACQUIRE"))
        self.assertEqual(policy_align.ALIGN_REACQUIRE_MAX + 1, ctx.labels("run_pose").count("ALIGN non-contact descent"))
        self.assertGreater(cycle.tool_state(ctx.current)["up_mm"], policy_align._non_contact_up_mm(CTL))

    def test_a_block_lost_where_it_was_last_whole_ends_there_after_one_step_back(self):
        ctx = gw.WorldCycle(world_at(0.0, 4.5), descent_drift=REALIZATIONS["least recorded"], hide=set(range(2, 60)))
        code, values = self.failure(ctx)
        self.assertEqual(("align_cube_lost_after_reacquire", 1), (code, values["reacquires"]))
        self.assertGreater(cycle.tool_state(ctx.current)["up_mm"], policy_align._non_contact_up_mm(CTL))

    def test_a_block_never_seen_in_align_is_not_detected_after_one_recapture(self):
        ctx = gw.WorldCycle(world_at(0.0, 4.5), hide=set(range(1, 60)))
        with self.assertRaises(stage_policy.CycleFailed) as failure:
            run_align(ctx, "align_v2")
        self.assertEqual("align_cube_not_detected", harness.failure_code(ctx, failure.exception))
        self.assertEqual(["observe"], ctx.labels("observe"))
        self.assertEqual(["PREGRASP (non-contact)"], ctx.labels("run_pose"))

    def test_a_margin_the_bounded_aim_cannot_keep_is_corrected_by_a_step_first_and_no_band_step_undoes_it(self):
        # a descent that would realize twice as far inward as the worst recorded one: the aim alone cannot keep the top margin
        for worst_out in (-20.0, -25.0):
            with self.subTest(worst_out), mock.patch.object(policy_align, "DESCENT_WORST_REALIZATION", {**policy_align.DESCENT_WORST_REALIZATION, "out_mm": worst_out}):
                ctx = gw.WorldCycle(world_at(4.0, 4.5), descent_drift=REALIZATIONS["worst recorded"])
                outcome = run_align(ctx, "align_v2")
                self.assertTrue(outcome.decision["aligned"])
                corrections = [entry for entry in ctx.transitions if entry["state"] == "ALIGN_RADIAL_STEP" and "predicted_top_px" in entry]
                self.assertEqual(1, len(corrections))
                self.assertGreater(corrections[0]["forward_mm"], 0.0)
                states = align_states(ctx)
                self.assertEqual("ALIGN_DESCENT", states[states.index("ALIGN_RADIAL_STEP") + 2])
                self.assertGreaterEqual(ctx.events.predicates("align_view_margin")[-1]["measured"], policy_align.ALIGN_VIEW_MARGIN_PX)

    def test_the_descent_is_refused_before_it_moves_when_no_correction_keeps_the_view(self):
        def refuse(*_args, **_kwargs):
            raise cycle.ScanRefused("refused by the test")
        ctl = harness.controller_view(PLACEMENT_ENVELOPE=("align_v2",), cartesian_step_pose=refuse)
        ctx = gw.WorldCycle(world_at(0.0, 4.5))
        coarse = {"pan_deg": HOME_PAN, "forward_mm": PREGRASP_FORWARD_MM, "cube_out_mm": 17.0}
        with mock.patch.object(policy_align, "ALIGN_VIEW_MARGIN_PX", 400.0), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(stage_policy.CycleFailed) as failure:
            policy_align.run_pregrasp_align(ctx, ctl, harness.LOW_POSE, coarse, harness.cube_record([0, 0, 1, 1]), -30.0)
        self.assertEqual("align_descent_would_lose_view", harness.failure_code(ctx, failure.exception))
        self.assertNotIn("ALIGN non-contact descent", ctx.labels("run_pose"))
        margin = ctx.events.predicates("align_view_margin")[-1]
        self.assertEqual((False, 400.0), (margin["value"], margin["threshold"]))


class AlignV2InnerBound(unittest.TestCase):
    """Under align_v2 no radial step and no aimed descent commands the tool nearer than the inner bound: the proven grasp
    radius (tool out 0), or near_grasp's nearest grasp only when near_grasp is named too. A move whose commanded tool
    radius lies inside it is refused before it moves, align_inside_grasp_radius, with the arm where it holds."""

    CODE = "align_inside_grasp_radius"

    MOVES = ("ALIGN radial step", "ALIGN non-contact descent")  # the moves the bound holds for; a trim or a step back re-commands a measured radius

    @staticmethod
    def pregrasp_over(forward_mm: float, cube_out_mm: float, drift=(0.0, 0.0)) -> gw.WorldCycle:
        return gw.WorldCycle(gw.World(cube_out_mm, HOME_PAN, min_visible=0.4), descent_drift=drift)

    @staticmethod
    def run_from(ctx, forward_mm: float, *names):
        """ALIGN from a pregrasp at forward_mm, recording the innermost commanded tool radius of every ALIGN move."""
        commanded, real = [], ctx.run_pose

        def recording(target, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
            if label in AlignV2InnerBound.MOVES:
                for pose in (target if isinstance(target, (list, tuple)) else [target]):
                    commanded.append(cycle.tool_state({**ctx.current, **pose.joints})["out_mm"])
            return real(target, holding, floor_m, pan_allowance=pan_allowance, label=label, manipulation_step=manipulation_step)
        ctx.run_pose = recording
        coarse = {"pan_deg": HOME_PAN, "forward_mm": forward_mm, "cube_out_mm": forward_mm - cycle.PREGRASP_OUTWARD_BIAS_MM}
        code = None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                policy_align.run_pregrasp_align(ctx, words(*names), harness.LOW_POSE, coarse, harness.cube_record([0, 0, 1, 1]), -30.0)
            except stage_policy.CycleFailed as failure:
                code = harness.failure_code(ctx, failure)
        return commanded, code

    def test_no_commanded_tool_radius_falls_inside_the_bound_for_blocks_at_minus_30_45_and_60(self):
        """From a pregrasp at the proven grasp radius (forward 0). Without near_grasp every one of the three is refused; under
        near_grasp the block at -30 is aligned inside the -25 bound's reach and the two farther in are refused."""
        near = policy_grasp.NEAR_GRASP_MIN_FORWARD_MM
        expected = {(("align_v2",), -30.0): self.CODE, (("align_v2",), -45.0): self.CODE, (("align_v2",), -60.0): self.CODE,
                    (("align_v2", "near_grasp"), -30.0): None, (("align_v2", "near_grasp"), -45.0): self.CODE, (("align_v2", "near_grasp"), -60.0): self.CODE}
        for (names, cube_out), code_expected in expected.items():
            bound = near if "near_grasp" in names else 0.0
            for realization, drift in REALIZATIONS.items():
                with self.subTest(words=names, cube_out=cube_out, realization=realization):
                    commanded, code = self.run_from(self.pregrasp_over(0.0, cube_out, drift), 0.0, *names)
                    if commanded:
                        self.assertGreaterEqual(round(min(commanded), 1), bound, f"a commanded tool radius {min(commanded):+.1f} mm lies inside {bound:+.1f} mm")
                    self.assertEqual(code_expected, code)

    def test_without_near_grasp_the_bound_is_the_proven_grasp_radius_even_for_a_block_near_grasp_could_reach(self):
        commanded, code = self.run_from(self.pregrasp_over(0.0, -20.0), 0.0, "align_v2")
        self.assertEqual([], commanded, "refused before the first radial step")
        self.assertEqual(self.CODE, code)
        commanded, code = self.run_from(self.pregrasp_over(0.0, -20.0), 0.0, "align_v2", "near_grasp")
        self.assertIsNone(code)
        self.assertGreaterEqual(round(min(commanded), 1), policy_grasp.NEAR_GRASP_MIN_FORWARD_MM)
        self.assertLess(min(commanded), 0.0, "under near_grasp the servo does reach inside the proven radius, to its own bound")

    def test_a_block_outside_the_bound_is_aligned_as_before(self):
        for names in (("align_v2",), ("align_v2", "near_grasp")):
            with self.subTest(words=names):
                commanded, code = self.run_from(gw.WorldCycle(world_at(0.0, 4.5)), PREGRASP_FORWARD_MM, *names)
                self.assertIsNone(code)
                self.assertGreater(min(commanded), 0.0)

    def test_the_refusal_is_decided_before_the_move_at_the_bound_itself(self):
        """A radial step whose commanded tool radius is the bound is taken; one 0.2 mm inside it is refused, nothing moves."""
        pregrasp = gw.pregrasp_pose_over(gw.World(0.0, HOME_PAN), PREGRASP_FORWARD_MM)
        for names, bound in ((("align_v2",), 0.0), (("align_v2", "near_grasp"), policy_grasp.NEAR_GRASP_MIN_FORWARD_MM)):
            ctl = words(*names)
            view = harness.view(pregrasp)
            for target, refused in ((bound, False), (bound - 0.2, True)):
                with self.subTest(words=names, target=target):
                    ctx = harness.HarnessCycle(pose=pregrasp)
                    step = target - view["tool"]["out_mm"]
                    pose, _report = cycle.cartesian_step_pose(pregrasp, step / 1000.0, 0.0, False)
                    out = cycle.tool_state({**pregrasp, **pose.joints})["out_mm"]
                    self.assertAlmostEqual(target, out, delta=0.04, msg="the step's own commanded radius")
                    with contextlib.redirect_stdout(io.StringIO()):
                        try:
                            policy_align.refuse_inside_inner_bound(ctx, ctl, view, [pose], "ALIGN radial step")
                            code = None
                        except stage_policy.CycleFailed as failure:
                            code = harness.failure_code(ctx, failure)
                            self.assertIn("nothing was picked up", str(failure))
                    self.assertEqual(self.CODE if refused else None, code)
                    self.assertEqual([], ctx.labels("run_pose"))

    def test_with_no_word_the_proven_descent_is_unchanged(self):
        commanded, code = self.run_from(self.pregrasp_over(0.0, -45.0), 0.0)
        self.assertNotEqual(self.CODE, code)

    @staticmethod
    def refusal_values(ctx) -> dict:
        """The values the last ctx.failed() stated next to its reason code."""
        return [args[2] for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]

    def test_a_block_whose_aimed_descent_is_the_first_move_inside_the_bound_is_refused_before_the_descent(self):
        """A block at -14 mm, which the coarse_v2 pickup band admits, from a pregrasp at the proven grasp radius: the radial
        reads in the band, no radial step is taken, and the aimed descent is the first move that would command the tool
        inside the bound. It is refused before it moves, under each recorded descent realization."""
        self.assertLessEqual(policy_align.pickup_band_min_cube_out_mm(words("coarse_v2", "align_v2")), -14.0, "SEARCH admits the block")
        for names in (("align_v2",), ("coarse_v2", "align_v2")):
            for realization, drift in REALIZATIONS.items():
                with self.subTest(words=names, realization=realization):
                    ctx = self.pregrasp_over(0.0, -14.0, drift)
                    commanded, code = self.run_from(ctx, 0.0, *names)
                    self.assertEqual(self.CODE, code)
                    values = self.refusal_values(ctx)
                    self.assertEqual("ALIGN non-contact descent", values["move"])
                    self.assertLess(values["commanded_tool_out_mm"], values["inner_bound_mm"])
                    self.assertEqual(0.0, values["inner_bound_mm"])
                    self.assertEqual([], commanded, "no radial step and no descent was commanded")
                    self.assertNotIn("ALIGN non-contact descent", ctx.labels("run_pose"))
                    self.assertEqual(["PREGRASP (non-contact)"], ctx.labels("run_pose"))

    @staticmethod
    def start_5_mm_outside(bound: float) -> dict:
        """A measured pose 5 mm outside `bound` from which one aimed descent can reach it: the pregrasp itself for the proven
        radius; for near_grasp's bound, which no pregrasp reaches, the pregrasp at the proven radius and four 5 mm steps in
        and down, as ALIGN steps."""
        if bound >= 0.0:
            return gw.pregrasp_pose_over(gw.World(bound, HOME_PAN), bound + 5.0)
        start = gw.pregrasp_pose_over(gw.World(bound, HOME_PAN), 0.0)
        while cycle.tool_state(start)["out_mm"] > bound + 5.0 + 0.5:
            pose, _report = cycle.cartesian_step_pose(start, -0.005, -0.005, False)
            start = {**start, **pose.joints}
        return start

    def test_an_aimed_descent_to_the_bound_is_taken_and_one_0_2_mm_inside_is_refused(self):
        """The descent poses themselves (the builder the aimed descent plans with, at its lowest clearance), from 5 mm outside
        the bound: a descent whose commanded tool radius is the bound passes; one 0.2 mm inside is refused, nothing moves."""
        clearance_m = policy_align.ALIGN_DESCENT_CLEARANCES_M[0]
        for names, bound in ((("align_v2",), 0.0), (("align_v2", "near_grasp"), policy_grasp.NEAR_GRASP_MIN_FORWARD_MM)):
            ctl = words(*names)
            start = self.start_5_mm_outside(bound)
            view = harness.view(start)
            self.assertAlmostEqual(bound + 5.0, view["tool"]["out_mm"], delta=0.5)
            for target, refused in ((bound, False), (bound - 0.2, True)):
                with self.subTest(words=names, target=target):
                    ctx = harness.HarnessCycle(pose=start)
                    aim = target
                    for _ in range(3):  # the aim whose commanded radius is the target: the solver lands a few hundredths off it
                        poses = ctl.cli.pregrasp_descend_poses(start, aim / 1000.0, clearance_m)
                        out = min(cycle.tool_state({**start, **pose.joints})["out_mm"] for pose in poses)
                        aim += target - out
                    self.assertAlmostEqual(target, out, delta=0.04, msg="the descent's own commanded radius")
                    self.assertLess(cycle.tool_state({**start, **poses[-1].joints})["up_mm"], view["tool"]["up_mm"] - 4.0, "a descent")
                    with contextlib.redirect_stdout(io.StringIO()):
                        try:
                            policy_align.refuse_inside_inner_bound(ctx, ctl, view, poses, "ALIGN non-contact descent")
                            code = None
                        except stage_policy.CycleFailed as failure:
                            code = harness.failure_code(ctx, failure)
                            self.assertEqual("ALIGN non-contact descent", self.refusal_values(ctx)["move"])
                    self.assertEqual(self.CODE if refused else None, code)
                    self.assertEqual([], ctx.labels("run_pose"))

    def test_the_view_margin_step_is_held_to_the_bound_too(self):
        """The step outward that a short top margin asks for, before the descent. No geometric-World block found reaches it
        with the arm inside the bound, so the descent plan is made to report the top row 2 mm short, through the planner
        ALIGN calls. From a pregrasp at the bound the 2 mm step outward is taken; from one 5 mm inside the bound the same
        step would still command the tool inside it (-3 mm) and is refused before it moves, after the plan was made."""
        real = policy_align.plan_aimed_descent
        for forward_mm, cube_out, refused in ((0.0, -10.0, False), (-5.0, -15.0, True)):
            with self.subTest(pregrasp_forward_mm=forward_mm):
                planned = []

                def top_short_once(ctl, view, box, radial_mm):
                    plan = real(ctl, view, box, radial_mm)
                    planned.append(view["tool"]["out_mm"])
                    return {**plan, "ok": False, "top_short_mm": 2.0} if len(planned) == 1 and plan["poses"] is not None else plan
                ctx = self.pregrasp_over(forward_mm, cube_out)
                with mock.patch.object(policy_align, "plan_aimed_descent", top_short_once):
                    commanded, code = self.run_from(ctx, forward_mm, "align_v2")
                self.assertAlmostEqual(forward_mm, planned[0], delta=0.5, msg="the plan is made from the pregrasp, no step before it")
                labels = ctx.labels("run_pose")
                if refused:
                    self.assertEqual(self.CODE, code)
                    values = self.refusal_values(ctx)
                    self.assertEqual(("ALIGN radial step", -3.0), (values["move"], values["commanded_tool_out_mm"]))
                    self.assertEqual(["PREGRASP (non-contact)"], labels, "the view-margin step was not commanded")
                    self.assertEqual(1, len(planned))
                else:
                    self.assertEqual(["PREGRASP (non-contact)", "ALIGN radial step"], labels[:2], "the view-margin step was taken")
                    self.assertAlmostEqual(forward_mm + 2.0, planned[1], delta=0.5, msg="the next plan is made 2 mm outward")


class AlignV2Decisions(unittest.TestCase):
    def test_the_2d_band_needs_the_lateral_the_radial_and_a_whole_box(self):
        box, joints = START_1_PREGRASP  # lateral 7.1, radial 3.2
        pitch = tool(joints)["pitch_deg"]
        decision = policy_align.alignment_decision(CTL, box, pitch, ("align_v2",))
        self.assertTrue(decision["aligned"] and decision["radial_in_band"] and decision["lateral_in_window"])
        for key in ("lateral_mm", "radial_mm"):
            self.assertEqual(cycle.alignment_from_bbox(box, pitch)[key], decision[key])
        far = policy_align.box_from_alignment(CTL, 4.5, 8.0, 264.0, pitch)
        self.assertFalse(policy_align.alignment_decision(CTL, far, pitch, ("align_v2",))["aligned"])
        self.assertTrue(policy_align.alignment_decision(CTL, far, pitch, ())["aligned"], "with no word the lateral alone decides, as today")
        cut = [box[0], 0, box[2], box[3] - 80]
        clipped = policy_align.alignment_decision(CTL, cut, pitch, ("align_v2",))
        self.assertEqual((False, ["top"], True), (clipped["aligned"], clipped["clipped_edges"], clipped["rebuilt"]))

    def test_the_aimed_descent_of_start_1_aims_at_the_block_not_at_out_plus_3(self):
        box, joints = START_1_PREGRASP
        view = harness.view(joints)
        radial = cycle.alignment_from_bbox(box, view["tool"]["pitch_deg"])["radial_mm"]
        plan = policy_align.plan_aimed_descent(CTL, view, box, radial)
        self.assertTrue(plan["ok"])
        self.assertGreaterEqual(plan["aim_mm"], radial)
        self.assertLessEqual(plan["aim_mm"], policy_align.ALIGN_DESCENT_AIM_MAX_MM)
        self.assertGreaterEqual(plan["predicted"]["margins"]["top"], policy_align.ALIGN_VIEW_MARGIN_PX)
        planned = cycle.tool_state({**joints, **plan["poses"][-1].joints})
        self.assertAlmostEqual(view["tool"]["out_mm"] + plan["aim_mm"], planned["out_mm"], delta=1.0)


class TheAlignWordsThroughTheController(mission.MissionCase):
    """The two words reach the stage through KC_PLACEMENT_ENVELOPE, the one key of every stage's words: declared, so a
    run may name them; not qualified, so a LIVE run naming either is refused before the robot is constructed unless the
    config names the attended label."""

    def world(self):
        return mission.MissionWorld(floor=[{"pad_m": mission.A_START, "contact_px": mission.CONTACT}], cube_start_pad_m=mission.A_START, cube_final_pad_m=mission.A_FINAL)

    def live(self, **options):
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
            return mission.run_world(self.world(), self.directory, real_move_base=True, runtime_mode=mission._assigned(mission.SUPERVISOR, "LIVE"), **options)

    def test_a_live_run_naming_either_word_is_refused_before_the_robot_exists(self):
        for words in ("coarse_v2", "align_v2", "coarse_v2,align_v2"):
            with self.subTest(words):
                run = self.live(extra_env={"KC_PLACEMENT_ENVELOPE": words})
                self.assertEnded(run, 3)
                self.assertEqual(([], 0), (run["sent"], run["disconnects"]))
                self.assertIn("not qualified", run["stdout"])

    def test_a_run_under_both_words_passes_align_through_the_new_servo_and_places(self):
        run = mission.run_world(self.world(), self.directory, extra_env={"KC_PLACEMENT_ENVELOPE": "coarse_v2,align_v2"})
        self.assertEnded(run, 0)
        self.assertIn("PLACED", run["states"])
        self.assertIn("ALIGN_DESCENT", run["states"])
        pregrasp = run["transitions"][run["states"].index("PREGRASP")]
        self.assertEqual("coarse_v2", pregrasp["coarse"]["coarse_map"])
        aligned = [entry for entry in run["transitions"] if entry["state"] == "ALIGN"][-1]
        self.assertTrue(aligned["aligned"] and aligned["radial_in_band"])


if __name__ == "__main__":
    unittest.main()
