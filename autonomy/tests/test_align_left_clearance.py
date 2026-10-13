# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/tests/test_align_left_clearance.py: the ALIGN word align_left_clearance (autonomy/manipulation/
policy_align_left_clearance.py), an additive policy beside the proven policy_align.

The operator's ruling of 2026-10-07: the fixed (left) jaw pushed the block on every recorded miss of that day, so under this
word ALIGN accepts the block only 6 to 9 mm right of the fixed jaw (the proven window is 0 to 9) and aims its pan trims at
7.5 mm. The recorded run console-20261007T185345Z-m989a19c read 2.7 mm after its trim and 0.0 mm after its descent; the
proven window accepted both, and the close pushed the block.
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage_harness as harness  # noqa: E402  (puts the controller on the path)
import geometric_world as gw  # noqa: E402
import policy_align  # noqa: E402
import policy_align_left_clearance as clearance  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import test_policy_align as align_tests  # noqa: E402
import test_cycle_mission_mode as mission  # noqa: E402  (the controller's main() on a scripted World)

WORD = clearance.WORD
CTL = cycle._CONTROLLER_VIEW
# The recorded descent of console-20261007T185345Z-m989a19c moved the lateral reading from 2.7 to 0.0 mm.
RECORDED_DESCENT_LATERAL_SHIFT_MM = -2.7


def controller(*names, descent_shift_mm: float = 0.0, ctx=None, **overrides):
    """The controller view with these words; with descent_shift_mm, every lateral read after the first ALIGN descent of
    `ctx` is moved by that much (the recorded change of the reading across a descent)."""
    overrides = {"PLACEMENT_ENVELOPE": tuple(names), **overrides}
    if descent_shift_mm:
        def shifted(bbox_xyxy, pitch_deg):
            decision = dict(cycle.alignment_from_bbox(bbox_xyxy, pitch_deg))
            if "ALIGN non-contact descent" in ctx.labels("run_pose"):
                decision["lateral_mm"] += descent_shift_mm
                decision["aligned"] = cycle.CAPTURE_WINDOW_MM[0] <= decision["lateral_mm"] <= cycle.CAPTURE_WINDOW_MM[1]
            return decision
        overrides["alignment_from_bbox"] = shifted
    return harness.controller_view(**overrides)


def run_align(ctx, ctl, module=clearance):
    coarse = {"pan_deg": align_tests.HOME_PAN, "forward_mm": align_tests.PREGRASP_FORWARD_MM,
              "cube_out_mm": align_tests.PREGRASP_FORWARD_MM - cycle.PREGRASP_OUTWARD_BIAS_MM}
    with contextlib.redirect_stdout(io.StringIO()):
        return module.run_pregrasp_align(ctx, ctl, harness.LOW_POSE, coarse, harness.cube_record([0, 0, 1, 1]), -30.0)


def box_at(lateral_mm: float, pitch: float = 64.7) -> list:
    return policy_align.box_from_alignment(CTL, lateral_mm, 0.0, 264.0, pitch)


class TheWord(unittest.TestCase):
    def test_it_is_an_additive_align_word_not_qualified_and_the_proven_module_declares_nothing_new(self):
        self.assertEqual(("align_left_clearance",), clearance.WORDS)
        self.assertEqual("align", clearance.POLICY.stage)
        self.assertEqual(frozenset(), clearance.QUALIFIED)
        self.assertEqual(("coarse_v2", "align_v2"), policy_align.WORDS)
        self.assertIn(clearance.POLICY, cycle._ADDITIVE_POLICIES)

    def test_the_controller_accepts_it_beside_the_reach_words(self):
        words = cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: f"arm_reach,advance_then_reach,{WORD}"})
        self.assertEqual(("arm_reach", "advance_then_reach", WORD), tuple(words))
        self.assertIn(WORD, cycle.envelope_words_declared())
        self.assertNotIn(WORD, cycle.envelope_words_qualified())

    def test_it_runs_align_only_when_named_and_align_v2_is_not(self):
        self.assertTrue(clearance.engaged(("arm_reach", WORD)))
        self.assertFalse(clearance.engaged(("arm_reach", "advance_then_reach")))
        self.assertFalse(clearance.engaged((WORD, "align_v2")))

    def test_the_window_is_6_to_9_mm_right_of_the_fixed_jaw(self):
        self.assertEqual((6.0, 9.0), clearance.LEFT_CLEARANCE_WINDOW_MM)
        self.assertEqual((0.0, 9.0), tuple(cycle.CAPTURE_WINDOW_MM))


class TheDecision(unittest.TestCase):
    def test_under_the_word_the_lateral_must_lie_inside_6_to_9(self):
        for lateral, aligned in ((0.0, False), (2.7, False), (5.99, False), (6.01, True), (7.5, True), (8.99, True), (9.01, False)):
            with self.subTest(lateral):
                decision = clearance.alignment_decision(CTL, box_at(lateral), 64.7)
                self.assertEqual(aligned, decision["aligned"])
                self.assertAlmostEqual(lateral, decision["lateral_mm"], places=6)

    def test_the_proven_decision_is_unchanged(self):
        for lateral in (0.01, 2.7, 8.99, 9.01, -0.1):
            with self.subTest(lateral):
                self.assertEqual(cycle.alignment_from_bbox(box_at(lateral), 64.7), policy_align.alignment_decision(CTL, box_at(lateral), 64.7, ()))

    def test_the_trim_aims_at_7_5_instead_of_4_5(self):
        for lateral in (-40.0, -10.4, 0.0, 2.7, 12.0):
            with self.subTest(lateral):
                self.assertEqual(cycle.lateral_trim_deg(lateral - 3.0, 0.25), clearance.lateral_trim(CTL, lateral, 0.25))


class InTheWorld(unittest.TestCase):
    def test_it_ends_6_to_9_mm_right_of_the_fixed_jaw_from_any_start_whatever_the_descent(self):
        # The recorded trim shortfall (geometric_world: 0.5 deg). Under a constant 1.0 deg shortfall the 3 mm window is not
        # reached in LEFT_CLEARANCE_TRIMS_MAX trims and ALIGN ends align_not_in_window_after_trims, before any approach.
        for name, drift in align_tests.REALIZATIONS.items():
            for lateral in (-40.0, -10.4, 0.0, 2.7, 4.5, 12.0, 40.0):
                with self.subTest(realization=name, lateral=lateral):
                    ctx = gw.WorldCycle(align_tests.world_at(0.0, lateral), descent_drift=drift, pan_shortfall_deg=0.5)
                    outcome = run_align(ctx, controller(WORD))
                    self.assertTrue(outcome.decision["aligned"])
                    self.assertGreaterEqual(outcome.decision["lateral_mm"], 6.0)
                    self.assertLessEqual(outcome.decision["lateral_mm"], 9.0)
                    self.assertIn("ALIGN non-contact descent", ctx.labels("run_pose"))

    def test_the_proven_stage_runs_first_byte_for_byte_and_the_correction_follows_its_descent(self):
        ctx = gw.WorldCycle(align_tests.world_at(0.0, 2.7), pan_shortfall_deg=0.5)
        run_align(ctx, controller(WORD))
        labels = ctx.labels("run_pose")
        self.assertEqual(["PREGRASP (non-contact)", "ALIGN non-contact descent", "ALIGN pan trim"], labels)

    def test_the_recorded_descent_shift_is_trimmed_out_after_the_descent_before_any_approach(self):
        ctx = gw.WorldCycle(align_tests.world_at(0.0, 7.5), pan_shortfall_deg=0.5)
        outcome = run_align(ctx, controller(WORD, descent_shift_mm=RECORDED_DESCENT_LATERAL_SHIFT_MM, ctx=ctx))
        labels = ctx.labels("run_pose")
        descent = labels.index("ALIGN non-contact descent")
        self.assertIn("ALIGN pan trim", labels[descent + 1:], "the reading below 6 after the descent was not trimmed")
        self.assertGreaterEqual(outcome.decision["lateral_mm"], 6.0)

    def test_the_proven_stage_accepts_the_same_shifted_reading_as_it_did_on_the_robot(self):
        ctx = gw.WorldCycle(align_tests.world_at(0.0, 3.0), pan_shortfall_deg=0.5)
        outcome = run_align(ctx, controller(descent_shift_mm=RECORDED_DESCENT_LATERAL_SHIFT_MM, ctx=ctx), module=policy_align)
        self.assertEqual(["PREGRASP (non-contact)", "ALIGN non-contact descent"], ctx.labels("run_pose"))
        self.assertTrue(0.0 <= outcome.decision["lateral_mm"] < 1.0, outcome.decision["lateral_mm"])
        self.assertTrue(outcome.decision["aligned"])

    def test_a_reading_that_never_reaches_the_window_ends_before_the_approach_never_closes_on_the_fixed_jaw(self):
        ctx = gw.WorldCycle(align_tests.world_at(0.0, 2.7), pan_shortfall_deg=0.5)
        with self.assertRaises(Exception) as raised:
            run_align(ctx, controller(WORD, lateral_trim_deg=lambda lateral_mm, cube_range_m: 0.05))
        self.assertEqual("align_not_in_window_after_trims", harness.failure_code(ctx, raised.exception))
        _code, sentence, values, _evidence = [args for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]
        self.assertEqual((6.0, 9.0), (values["capture_window_min_mm"], values["capture_window_max_mm"]))
        self.assertEqual(clearance.LEFT_CLEARANCE_TRIMS_MAX, values["trims"])
        self.assertIn("after three trims", sentence)
        self.assertEqual(3, clearance.LEFT_CLEARANCE_TRIMS_MAX)
        self.assertEqual(clearance.LEFT_CLEARANCE_TRIMS_MAX, ctx.labels("run_pose").count("ALIGN pan trim"))
        self.assertNotIn("APPROACH", ctx.states())


class ThroughTheController(mission.MissionCase):
    """The word reaches ALIGN through KC_PLACEMENT_ENVELOPE, the one key of every stage's words: declared, so a run may name
    it; not qualified, so a LIVE run naming it is refused before the robot is constructed unless the config names the
    attended label."""

    def world(self):
        return mission.MissionWorld(floor=[{"pad_m": mission.A_START, "contact_px": mission.CONTACT}], cube_start_pad_m=mission.A_START, cube_final_pad_m=mission.A_FINAL)

    def test_a_live_run_naming_it_is_refused_before_the_robot_exists(self):
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
            run = mission.run_world(self.world(), self.directory, real_move_base=True, runtime_mode=mission._assigned(mission.SUPERVISOR, "LIVE"),
                                    extra_env={"KC_PLACEMENT_ENVELOPE": WORD})
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))
        self.assertIn("not qualified", run["stdout"])

    # The scripted World answers every view with the same box, whatever the pan: its block reads +3.0 mm, inside the proven
    # window and left of this word's, and no trim moves it.
    def test_with_no_word_the_scripted_block_at_plus_3_is_accepted_and_placed(self):
        run = mission.run_world(self.world(), self.directory)
        self.assertEnded(run, 0)
        self.assertIn("PLACED", run["states"])
        aligned = [entry for entry in run["transitions"] if entry["state"] == "ALIGN"][-1]
        self.assertTrue(aligned["aligned"])
        self.assertAlmostEqual(3.0, aligned["lateral_mm"], delta=0.5)

    def test_with_the_word_the_controller_runs_this_align_and_never_closes_on_that_block(self):
        run = mission.run_world(self.world(), self.directory, extra_env={"KC_PLACEMENT_ENVELOPE": WORD})
        self.assertNotIn("APPROACH", run["states"])
        self.assertNotIn("GRASP_CLOSE", run["states"])
        self.assertIn("after three trims", run["stdout"])
        attempts = [entry["attempt"] for entry in run["transitions"] if entry["state"] == "ALIGN"]
        self.assertEqual([8, 9, 10], attempts[-3:], "the proven loop's views, then one per correction trim")


if __name__ == "__main__":
    unittest.main()
