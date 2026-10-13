#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_reach_policies_cycle.py: the additive words (arm_reach, advance_then_reach, multi_block) wired into the
# single-cube controller's SEARCH, on the scripted World of test_cycle_mission_mode.py (main() runs for real; the robot
# client, the camera, the perception and the cube listing are scripted). What is pinned: the operator's block of 2026-09-30
# 07:14Z, staged in the World, is refused with no word exactly as the robot refused it, and under the placement words is
# bound to advance_then_reach inside the proven loaded reach (TARGET_SELECTED names the policy and its plan: the operator's
# ruling of 2026-10-02, Q8, placement_policy THE LOADED REACH CAP), then asks for the capability like every base
# placement, and in a build that withholds the policy's executor is refused by the executor guard before anything moves
# (this build executes it: test_reach_policies_execution.py places it); a block the
# proven policy places is placed exactly as without the words; with multi_block, beside a block the arm alone places, the
# nearer block that needs the base is bound as the proven rule binds it (the lead's decision of 2026-10-04: among blocks the
# proven policy places, the installed rule's block), and the run is the installed one; SEARCH's placement_plan is the
# portfolio only while SEARCH runs and the proven function again however SEARCH ends; run.config names an additive policy
# only when its word is named; the words are declared, unqualified, refused in a LIVE run without the attended label, and
# none of them needs projected. No robot, no camera, no network. Run directly: python3 autonomy/tests/test_reach_policies_cycle.py
import collections
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import run_single_cube_cycle as current  # noqa: E402
import placement_policy as pp  # noqa: E402
import placement_selector as ps  # noqa: E402
import policy_placement  # noqa: E402
import stage_harness as harness  # noqa: E402
import stage_policy  # noqa: E402
import target_binding as tb  # noqa: E402
import test_cycle_mission_mode as mission  # noqa: E402  (the scripted World with several cubes, the target file writers)

PROVEN_PLACEMENT_PLAN = current.placement_plan
LIVE = [-0.035768012099579305, -0.2129612198129184]
LIVE_CONTACT = [454.8, 589.7]  # the recorded contact pixel: coarse cube_out -5.2, clamped at 0 as SEARCH passes it
WORDS = {"KC_PLACEMENT_ENVELOPE": "arm_reach,advance_then_reach"}
AUTO_FIRST = [mission.declared("cube", tb.AUTO)]


def auto_writer(carry: bool):
    """The first execution of the one auto target, as the runtime writes it: without the capability first (carry False), and
    once a SEARCH asked for it, with it (carry True)."""
    return lambda directory: mission.write_target(directory, "cube", AUTO_FIRST, carry=carry)


def states(run) -> list:
    return [name for name in run["states"] if name != "PLACEMENT_ENVELOPE"]


def selected(run) -> dict:
    return run["transitions"][run["states"].index("TARGET_SELECTED")]


class TheOperatorsBlock(mission.MissionCase):
    def world(self):
        return mission.MissionWorld(floor=[{"pad_m": LIVE, "contact_px": LIVE_CONTACT}])

    def expected(self) -> ps.Decision:
        """The portfolio's own decision for the World's SEARCH inputs (its homography: image-up is pad +y), planned as the run
        with the words plans it: the loaded reach cap reads the run's policies (placement_policy.capped_plan_fits)."""
        axis = current.reach_direction_pad(mission.fixtures._pad_view()["homography_image_to_pad"], LIVE_CONTACT)
        coarse = current.coarse_pregrasp_from_low_pose(LIVE_CONTACT, mission.fixtures.SYNTHETIC_LOW_POSE["shoulder_pan"])
        self.assertAlmostEqual(-5.2, coarse["cube_out_mm"], delta=0.05)
        with mock.patch.object(current, "PLACEMENT_ENVELOPE", tuple(WORDS["KC_PLACEMENT_ENVELOPE"].split(","))):
            return ps.decide(current, pp.Situation(LIVE, axis, max(coarse["cube_out_mm"], 0.0), False, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM),
                             ps.POLICIES, current.placement_plan)

    def test_with_no_word_it_is_refused_as_the_robot_refused_it(self):
        _, run = self.mission(self.world(), auto_writer(False))
        self.assertEnded(run, 5)
        self.assertEqual(([], []), (run["sent"], run["base_pulses"]))
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_PLACEABLE, run["result"]["reason_code"])
        self.assertIn("past the along-axis budget of 120 mm", run["stdout"])

    def test_under_the_words_it_is_bound_to_advance_then_reach_and_asks_for_the_capability(self):
        """Inside the proven loaded reach (the operator's ruling of 2026-10-02, Q8): 97 mm of base and a carry to 58 mm out,
        where its longer carry planned 53 mm of base and a carry to 113 mm out."""
        expected = self.expected()
        self.assertEqual(("advance_then_reach", 97.0, 58.0), (expected.selected.policy, expected.selected.base_advance_mm, expected.selected.arm_out_mm))
        _, run = self.mission(self.world(), auto_writer(False), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual(([], []), (run["sent"], run["base_pulses"]), "nothing moved")
        fields = selected(run)
        self.assertEqual(("BASE_ADVANCE", "advance_then_reach", True), (fields["mode"], fields["placement_policy"], fields["placement_executes"]))
        self.assertEqual(expected.selected.record(), fields["placement_plan"])
        self.assertEqual(["arm_reach", "advance_then_reach", "proven"], [plan["policy"] for plan in fields["placement_policies"]])
        self.assertEqual([False, True, False], [plan["admitted"] for plan in fields["placement_policies"]])
        self.assertEqual("mission_capability_required", run["result"]["reason_code"])
        self.assertEqual([{"target_id": "cube", "bound_pad_m": LIVE, "mode": "BASE_ADVANCE", "observation_id": run["result"]["bindings"][0]["observation_id"]}],
                         run["result"]["bindings"])

    def test_with_its_executor_withheld_it_is_refused_by_the_executor_guard_before_anything_moves(self):
        world = self.world()
        with mock.patch.object(ps.policy_advance_then_reach, "EXECUTES", False):  # a build that plans the policy without executing it
            _, run = self.mission(world, auto_writer(carry=True), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual(([], []), (run["sent"], run["base_pulses"]))
        self.assertEqual(ps.EXECUTOR_REFUSAL, run["result"]["reason_code"])
        self.assertEqual("TARGET_SELECTED", run["result"]["final_state"], "the last state is the selection: nothing past SEARCH ran")
        self.assertIn("advance_then_reach", run["stdout"])

    def test_the_guard_opens_with_the_executor(self):
        """Not vacuous: with the policy's executor in the build (this one), the same run goes past the guard to PREGRASP and
        on to the placement (test_reach_policies_execution.py pins how it places the block)."""
        _, run = self.mission(self.world(), auto_writer(carry=True), extra_env=WORDS)
        self.assertIn("PREGRASP", run["states"])
        self.assertNotEqual(ps.EXECUTOR_REFUSAL, (run["result"] or {}).get("reason_code"))

    def test_arm_reach_alone_does_not_place_it_and_the_refusal_names_the_best_near_miss(self):
        _, run = self.mission(self.world(), auto_writer(False), extra_env={"KC_PLACEMENT_ENVELOPE": "arm_reach"})
        self.assertEnded(run, 5)
        self.assertEqual([], run["sent"])
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_PLACEABLE, run["result"]["reason_code"])
        self.assertIn("move the block 5 mm toward the pad", run["stdout"])


class ABlockTheProvenPolicyPlaces(mission.MissionCase):
    def world(self):
        return mission.MissionWorld(floor=[{"pad_m": mission.A_START, "contact_px": mission.CONTACT}], cube_start_pad_m=mission.A_START, cube_final_pad_m=mission.A_FINAL)

    def test_is_placed_exactly_as_without_the_words(self):
        """The same commands and the same states; under the words the record also states the choice once the cube is held
        (PLACEMENT_POLICY: the proven policy, handed to the proven stage)."""
        _, plain = self.mission(self.world(), auto_writer(False))
        _, worded = self.mission(self.world(), auto_writer(False), extra_env=WORDS)
        self.assertEnded(plain, 0)
        self.assertEnded(worded, 0)
        self.assertEqual(states(plain), [name for name in states(worded) if name != "PLACEMENT_POLICY"])
        self.assertEqual(["proven"], [entry["policy"] for entry in worded["transitions"] if entry["state"] == "PLACEMENT_POLICY"])
        self.assertEqual(plain["sent"], worded["sent"])
        self.assertEqual(([], []), (plain["base_pulses"], worded["base_pulses"]))
        fields = selected(worded)
        self.assertEqual(("proven", True, "ARM_ONLY"), (fields["placement_policy"], fields["placement_executes"], fields["mode"]))
        # Under the loaded reach cap the arm alone of a block the proven policy places counts only on the out its capped
        # commands measure under the record and keeps the proven rule's lagging-arm buffer (placement_policy, verify r8
        # policy MF1): it is refused here, and the proven arm-alone plan is the one admitted.
        admitted = [plan["policy"] for plan in fields["placement_policies"] if plan["admitted"]]
        self.assertEqual("proven", admitted[-1])
        self.assertNotIn("arm_reach", admitted)
        plain_fields = selected(plain)
        self.assertEqual(plain_fields, {name: value for name, value in fields.items() if name in plain_fields})
        self.assertEqual({"placement_policy", "placement_plan", "placement_executes", "placement_policies", "binding_rule", "feasible_candidates", "band_refused_candidates",
                          "binding_tier", "tier_set_aside_candidates"}, set(fields) - set(plain_fields))
        self.assertEqual(0, fields["band_refused_candidates"], "one block: nothing set aside (block_selection THE PICKUP BANDS AT THE BINDING)")
        self.assertEqual(("proven_admits", 0, "nearest_the_pad"), (fields["binding_tier"], fields["tier_set_aside_candidates"], fields["binding_rule"]),
                         "one block: nothing set aside (block_selection THE BINDING TIERS)")
        self.assertEqual("placed_new", worded["result"]["outcome"])


class MultiBlock(mission.MissionCase):
    """Two blocks off the pad that the proven policy places: the one nearer the pad is grasped far out (its arm band is short,
    the base carries it), the other lies a little farther out and the arm alone places it (the World's own arm-only cube),
    still with the grasp read 30.5 mm past its coarse estimate. multi_block's least motion took the arm-alone block; the
    lead's decision of 2026-10-04 binds, among blocks the proven policy places, the block the installed rule binds (verify r14:
    the installed arm-only carry of such a block ends held under the recorded loaded models where 31c546c places the other)."""

    NEAR = {"pad_m": [-0.08, -0.1475 + 0.015], "contact_px": [553.0, 545.0 - (60.0 - 11.4) * current.LOW_POSE_PX_PER_RADIAL_MM]}

    def world(self):
        return mission.MissionWorld(floor=[self.NEAR, {"pad_m": mission.A_START, "contact_px": mission.CONTACT}], carried=1,
                                    cube_start_pad_m=mission.A_START, cube_final_pad_m=mission.A_FINAL)

    def test_the_proven_rule_binds_the_nearer_block_which_needs_the_base(self):
        _, run = self.mission(self.world(), auto_writer(False))
        self.assertEnded(run, 5)
        self.assertEqual(([round(v, 4) for v in self.NEAR["pad_m"]], "BASE_ADVANCE"), (selected(run)["seen_pad_m"], selected(run)["mode"]))
        self.assertEqual("mission_capability_required", run["result"]["reason_code"])
        self.assertEqual([], run["sent"])

    def test_multi_block_binds_the_nearer_block_as_the_proven_rule_does_and_names_the_rule(self):
        _, plain = self.mission(self.world(), auto_writer(False))
        for words in ("multi_block", "arm_reach,advance_then_reach,multi_block"):
            _, run = self.mission(self.world(), auto_writer(False), extra_env={"KC_PLACEMENT_ENVELOPE": words})
            with self.subTest(words=words):
                fields = selected(run)
                self.assertEqual(([round(v, 4) for v in self.NEAR["pad_m"]], "BASE_ADVANCE", "nearest_the_pad", 2, "proven_admits"),
                                 (fields["seen_pad_m"], fields["mode"], fields["binding_rule"], fields["feasible_candidates"], fields["binding_tier"]))
                self.assertEqual((plain["ending"], plain["result"]["reason_code"], plain["sent"], plain["base_pulses"]),
                                 (run["ending"], run["result"]["reason_code"], run["sent"], run["base_pulses"]))
        # the World's contact row 545 is a coarse grasp of 11.4 mm (the scenes' rows are 545 + (11.4 - coarse) px per radial mm)
        arm_alone = current.placement_plan(mission.A_START, (0.0, 1.0), 11.4 + pp.reach_model.GRASP_UNDERREAD_HELD_MAX_MM, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        self.assertEqual("ARM_ONLY", arm_alone["mode"], "not vacuous: the farther block is the arm alone even 30.5 mm past its coarse grasp")


class ARunWithoutAMissionTarget(mission.MissionCase):
    """The legacy cycle on the World's own base-advance scene: the words plan it too, and in a build that withholds the
    policy's executor the guard holds it before any motion (this build executes it: test_reach_policies_execution.py)."""

    def test_the_base_advance_scene_is_placed_with_no_word_and_refused_before_any_motion_without_the_executor(self):
        plain = mission.run_world(mission.MissionWorld(), self.directory)
        self.assertEnded(plain, 0)
        self.assertTrue(plain["base_pulses"], "not vacuous: the proven plan drives the base here")
        with mock.patch.object(ps.policy_advance_then_reach, "EXECUTES", False):
            worded = mission.run_world(mission.MissionWorld(), self.directory, extra_env=WORDS)
        self.assertEnded(worded, 5)
        self.assertEqual(([], []), (worded["sent"], worded["base_pulses"]))
        self.assertIn("the least-motion placement is advance_then_reach", worded["stdout"])
        self.assertIs(PROVEN_PLACEMENT_PLAN, current.placement_plan)


class TheRebindingIsSearchOnly(unittest.TestCase):
    def test_placement_plan_is_the_portfolio_inside_search_and_the_proven_function_after_however_it_ends(self):
        ctx = harness.HarnessCycle()
        with mock.patch.object(current, "PLACEMENT_ENVELOPE", ("advance_then_reach",)):
            with current._placement_policies_at_search(ctx):
                self.assertIsInstance(current.placement_plan, ps.Portfolio)
                mode, why = policy_placement.classify_for_binding(ctx, current._CONTROLLER_VIEW, LIVE, (0.0, 1.0), 0.0)
                self.assertEqual(("BASE_ADVANCE", None), (mode, why), "a stage reads the portfolio by name through the view")
            self.assertIs(PROVEN_PLACEMENT_PLAN, current.placement_plan)
            with self.assertRaises(RuntimeError), current._placement_policies_at_search(harness.HarnessCycle()):
                raise RuntimeError("SEARCH ended by an error")
            self.assertIs(PROVEN_PLACEMENT_PLAN, current.placement_plan)
        mode, why = policy_placement.classify_for_binding(ctx, current._CONTROLLER_VIEW, LIVE, (0.0, 1.0), 0.0)
        self.assertIsNone(mode)

    def test_with_no_additive_word_nothing_is_bound_and_no_portfolio_exists(self):
        ctx = harness.HarnessCycle()
        for words in ((), ("projected",), ("scene_scan", "search_sweep")):
            with self.subTest(words=words), mock.patch.object(current, "PLACEMENT_ENVELOPE", words), current._placement_policies_at_search(ctx):
                self.assertIs(PROVEN_PLACEMENT_PLAN, current.placement_plan)
                self.assertIsNone(ctx._search_portfolio)

    def test_multi_block_alone_decides_with_the_proven_policy_and_lends_only_the_scene_search(self):
        import block_selection
        import policy_search
        proven_scene = policy_search._search_from_scene
        ctx = harness.HarnessCycle()
        with mock.patch.object(current, "PLACEMENT_ENVELOPE", ("multi_block",)), current._placement_policies_at_search(ctx):
            self.assertIs(PROVEN_PLACEMENT_PLAN, current.placement_plan)
            self.assertEqual((), ctx._search_portfolio.policies)
            self.assertIs(block_selection.search_from_scene, policy_search._search_from_scene)
        self.assertIs(proven_scene, policy_search._search_from_scene)


class TheWords(unittest.TestCase):
    def test_they_are_declared_unqualified_and_none_needs_projected(self):
        declared = current.envelope_words_declared()
        for word in ("arm_reach", "advance_then_reach", "multi_block"):
            with self.subTest(word):
                self.assertIn(word, declared)
                self.assertNotIn(word, current.envelope_words_qualified())
                self.assertEqual((word,), current.placement_envelope_words({current.PLACEMENT_ENVELOPE_ENV: word}))
        self.assertEqual(len(declared), len(set(declared)))
        self.assertEqual(("projected", "arm_reach", "advance_then_reach", "multi_block"),
                         current.placement_envelope_words({current.PLACEMENT_ENVELOPE_ENV: "projected,arm_reach,advance_then_reach,multi_block"}))
        self.assertEqual({"projected", "near_reverse", "side_offset", "base_lateral"}, set(current.PLACEMENT_ENVELOPE_WORDS), "the placement track's words are unchanged")

    def test_a_live_run_naming_one_is_refused_before_the_robot_exists_without_the_attended_label(self):
        class Reached(Exception):
            pass

        def refuse_to_build(*_args, **_kwargs):
            raise Reached()

        for word in ("arm_reach", "advance_then_reach", "multi_block"):
            env = {"KC_RUNTIME_MODE": "live", "KC_PLACEMENT_ENVELOPE": word, "KC_PLACEMENT_ENVELOPE_ATTENDED": "", "KC_RUNTIME_CONFIG": "", "KC_EVENTS_PATH": ""}
            out = io.StringIO()
            with self.subTest(word), mock.patch.dict(os.environ, env), mock.patch.object(current, "make_robot", refuse_to_build), contextlib.redirect_stdout(out):
                for name in ("KC_MISSION_TARGET", "KC_HOST_LEASE", "KC_HOST_SESSION", "KC_PERCEPTION_REMOTE_PORT", "KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG"):
                    os.environ.pop(name, None)
                status = current.main(["--do", "cycle", "--remote-ip", "192.0.2.10", "--robot-id", "synthetic-unit", "--pi-ssh", "synthetic-operator@192.0.2.10"])
                self.assertEqual(3, status)
                self.assertIn("not qualified", out.getvalue())
                os.environ["KC_PLACEMENT_ENVELOPE_ATTENDED"] = "qual-2026-09-30-a"
                with self.assertRaises(Reached):
                    current.main(["--do", "cycle", "--remote-ip", "192.0.2.10", "--robot-id", "synthetic-unit", "--pi-ssh", "synthetic-operator@192.0.2.10"])

    def test_run_config_names_an_additive_policy_only_when_its_word_is_named(self):
        plain = {item["name"]: item["value"] for item in current.stage_policy_summary({})}
        self.assertEqual(10, len(plain))
        self.assertEqual(collections.Counter(name.split(".")[1] for name in plain), collections.Counter({stage: 2 for stage in stage_policy.STAGES}))
        named = {item["name"]: item["value"] for item in current.stage_policy_summary({current.PLACEMENT_ENVELOPE_ENV: "advance_then_reach,multi_block"})}
        self.assertEqual(plain, {name: value for name, value in named.items() if name in plain})
        self.assertEqual({"policy.placement.advance_then_reach.selection": "advance_then_reach", "policy.search.multi_block.selection": "multi_block",
                          "policy.placement.advance_then_reach.digest": ps.policy_advance_then_reach.POLICY.digest.split(":", 1)[1],
                          "policy.search.multi_block.digest": current.block_selection.POLICY.digest.split(":", 1)[1]},
                         {name: value for name, value in named.items() if name not in plain})


if __name__ == "__main__":
    unittest.main()
