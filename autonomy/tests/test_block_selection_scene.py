#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_block_selection_scene.py: SEARCH from the scene (the word scene_search) under the SEARCH word
# multi_block (autonomy/manipulation/block_selection.py, SEARCH FROM THE SCENE). The lead's decision 4 of 2026-09-30:
# several off-pad blocks bind an admissible block instead of a refusal. What is pinned, on the stage harness of
# test_policy_search.py (synthetic pinhole views, the listing from the script), with SEARCH run inside the controller's own
# window (run_single_cube_cycle._placement_policies_at_search), as run_cycle runs it:
#   - a scene with several blocks off the pad that the proven policy places binds the one the installed controller's rule
#     takes, the one nearest the pad, then the first listed, never the least base where the two differ (the lead's decision
#     of 2026-10-04), whichever view saw it and in whatever order, states the choice and the rule that chose with every
#     candidate's plan, and SEARCH goes on from that block's best view;
#     under the placement words a block the proven policy refuses is taken only where no block of the scene is one the
#     proven policy places, though its plan under the loaded reach cap moves the base less, and a block only the long
#     stretch places is the last resort (block_selection THE BINDING TIERS, the lead's decision of 2026-10-03); a block the
#     pickup bands refuse holds its tier, so a block of a later tier is not taken beside it (the bands refuse the one taken,
#     before anything moves), while inside the first tier the bands set it aside (verify r13 gates MUST-FIX 1); the record
#     states every candidate's tier and whether the bands refuse it;
#   - several blocks off the pad of which none can be picked up and placed is refused before anything moves, naming them;
#   - with one block off the pad, or none, the scene search is the proven one, state for state (its refusals under codes of
#     its own: a code names its raise site), and record for record apart from the wall-clock stamp every record carries (the
#     two SEARCH runs are made under a clock that moves on between them, so the comparison never rests on the clock);
#   - without the word, the proven refusal of a scene with more than one block off the pad stands, and the controller's
#     window lends the scene search to multi_block for the length of SEARCH only, however SEARCH ends.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_block_selection_scene.py
import itertools
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))

import stage_harness as harness  # noqa: E402  (puts the controller on the path)
import block_selection as bs  # noqa: E402
import policy_search  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import run_stage1_scan as wall_clock  # noqa: E402  (the controller's cli: Cycle.state stamps every record with its now_utc)
import test_policy_search as scenes  # noqa: E402

PROVEN_SCENE_SEARCH = policy_search._search_from_scene
NEAR, FAR = (0.03, -0.16), (-0.03, -0.19)  # both off the pad, both placed with the base from either view; NEAR needs the smaller advance
BEYOND = ((0.05, -0.30), (-0.12, -0.31))  # both far off the pad: no bounded placement for either
# Under the placement words: the proven policy places PLACED_FAR (113 to 114 mm of base); the proven policy refuses STRETCH
# and no plan under the loaded reach cap fits it in the budget, so only the long stretch places it, with less base (89 to
# 100 mm and a carry to about 113 mm out, from either view).
PLACED_FAR, STRETCH = (-0.03, -0.20), (0.03, -0.248)
# Under the placement words, seen from minus_15: the proven policy refuses CAPPED and the words plan it under the loaded reach
# cap with 110 mm of base, less than PLACED_FAR's 113 from plus_15 (the lead's decision of 2026-10-03, THE BINDING TIERS).
CAPPED = (0.05, -0.215)
# Under the placement words, seen from home: the proven policy places BANDED (88 mm of base) and its coarse pregrasp pan lies
# 28 deg from home, past SEARCH's 25 deg return bound; the proven policy places PLACED (102 mm), whose pan the bands pass
# (6 deg); only the long stretch places SIDE_STRETCH (advance_then_reach, 58 mm of base; 21 deg).
BANDED, PLACED, SIDE_STRETCH = (0.08, -0.175), (-0.02, -0.19), (-0.14, -0.165)
PLACEMENT_WORDS = ("arm_reach", "advance_then_reach")


def scene(test, blocks: dict):
    script = scenes.scan_script(blocks, label="SEARCH_SCENE", both_ways=True)
    script["observe"] = harness.view(harness.LOW_POSE, [scenes.view_record("observe", 0.0, both_ways=True)])
    return scenes.ScanCycle(test, script)


def search(ctx, words):
    """SEARCH as run_cycle runs it: inside the controller's window for the run's words."""
    with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", tuple(words)), cycle._placement_policies_at_search(ctx):
        return policy_search.run_search(ctx, scenes.with_words(*words), {}, dict(harness.LOW_POSE))


def fields(ctx, state):
    return [entry for entry in ctx.transitions if entry["state"] == state]


def without_utc(entries):
    """The records without their wall-clock stamp: Cycle.state stamps every record with cli.now_utc(), to the second, so two
    runs share it only when no second ends between them."""
    return [{key: value for key, value in entry.items() if key != "utc"} for entry in entries]


def ticking():
    """A wall clock that moves on one second at every read, so two SEARCH runs made one after the other never share a stamp."""
    ticks = itertools.count()
    return lambda: "2026-09-30T00:{:02d}:{:02d}Z".format(*divmod(next(ticks) % 3600, 60))


class SeveralBlocksOffThePad(unittest.TestCase):
    WORDS = ("scene_search", "multi_block")

    def setUp(self):
        scenes.home_file(self)

    def test_the_block_nearest_the_pad_is_bound_whichever_view_saw_it(self):
        for layout in ({"minus_15": [NEAR], "plus_15": [FAR]}, {"minus_15": [FAR], "plus_15": [NEAR]}):
            with self.subTest(layout=layout):
                ctx = scene(self, layout)
                found = search(ctx, self.WORDS)
                self.assertEqual(list(NEAR), found.seen["cube_by_template"]["cube_center_pad_m"])
                (chosen,) = fields(ctx, "SEARCH_SCENE_LEAST_MOTION")
                self.assertEqual((list(NEAR), "proven_admits", "nearest_the_pad"), (chosen["position_pad_m"], chosen["binding_tier"], chosen["binding_rule"]))
                plans = {tuple(candidate["position_pad_m"]): candidate for candidate in chosen["candidates"]}
                self.assertEqual({NEAR, FAR}, set(plans))
                self.assertGreater(cycle.placed_margin_mm(NEAR), cycle.placed_margin_mm(FAR))
                self.assertLess(plans[NEAR]["base_advance_mm"], plans[FAR]["base_advance_mm"])
                self.assertEqual(2, chosen["unplaced_blocks"])
                self.assertEqual([True], [entry["value"] for entry in ctx.events.predicates("scene_least_motion_block")])
                self.assertEqual([], ctx.events.predicates("scene_one_unplaced_block"), "the proven count is not the rule here")
                self.assertNotIn("SEARCH_SCENE_SELECTED", ctx.states())
                self.assertFalse(any(label.startswith("PREGRASP") for label in ctx.labels("run_pose")), "SEARCH decides before anything is picked up")

    def test_among_blocks_the_proven_policy_places_the_installed_rules_order_decides_not_the_least_base(self):
        """The lead's decision of 2026-10-04 in the scene search: EVEN_FIRST and EVEN_SECOND lie as near the pad as each other
        (one margin), and the proven policy places both, EVEN_SECOND with 1 mm less base. The installed rule takes the first
        listed of equal margins, and so does the scene search, in either listing order; the least base took EVEN_SECOND."""
        even_first, even_second = (-0.10, -0.16), (0.03, -0.16)
        self.assertEqual(cycle.placed_margin_mm(even_first), cycle.placed_margin_mm(even_second))
        for listed in ((even_first, even_second), (even_second, even_first)):
            with self.subTest(listed=listed):
                ctx = scene(self, {"home": list(listed)})
                found = search(ctx, self.WORDS)
                (chosen,) = fields(ctx, "SEARCH_SCENE_LEAST_MOTION")
                plans = [(tuple(candidate["position_pad_m"]), candidate["base_advance_mm"], candidate["binding_tier"]) for candidate in chosen["candidates"]]
                self.assertEqual([(even_first, 73.0, "proven_admits"), (even_second, 72.0, "proven_admits")], sorted(plans, key=lambda plan: -plan[1]), "not vacuous")
                self.assertEqual(listed, tuple(plan[0] for plan in plans), "the scene lists them in the view's order")
                self.assertEqual((list(listed[0]), "nearest_the_pad"), (chosen["position_pad_m"], chosen["binding_rule"]))
                self.assertEqual(list(listed[0]), found.seen["cube_by_template"]["cube_center_pad_m"])

    def test_a_block_only_the_long_stretch_places_is_taken_after_one_the_proven_policy_places_though_it_moves_the_base_less(self):
        """block_selection THE BINDING TIERS (verify r11 replay MUST-FIX 1, which asked it of the scene search as of the
        binding): under the placement words the long stretch is the last resort among the blocks of a scene, as the binding
        reads it, so the block the proven policy places is taken, whichever view saw which."""
        for layout in ({"minus_15": [PLACED_FAR], "plus_15": [STRETCH]}, {"minus_15": [STRETCH], "plus_15": [PLACED_FAR]}):
            with self.subTest(layout=layout):
                ctx = scene(self, layout)
                found = search(ctx, self.WORDS + PLACEMENT_WORDS)
                (chosen,) = fields(ctx, "SEARCH_SCENE_LEAST_MOTION")
                plans = {tuple(candidate["position_pad_m"]): candidate for candidate in chosen["candidates"]}
                self.assertEqual({PLACED_FAR, STRETCH}, set(plans))
                self.assertEqual(("proven", "advance_then_reach"), (plans[PLACED_FAR]["policy"], plans[STRETCH]["policy"]))
                self.assertLess(plans[STRETCH]["base_advance_mm"], plans[PLACED_FAR]["base_advance_mm"], "not vacuous: the long stretch moves the base less")
                self.assertGreater(plans[STRETCH]["arm_out_mm"], cycle.ARM_LOADED_REACH_OUT_MM, "its carry passes the proven loaded reach")
                self.assertEqual(list(PLACED_FAR), chosen["position_pad_m"])
                self.assertEqual(list(PLACED_FAR), found.seen["cube_by_template"]["cube_center_pad_m"])
                self.assertEqual("proven", chosen["policy"])
                self.assertEqual((False, True), (plans[PLACED_FAR].get("long_stretch"), plans[STRETCH].get("long_stretch")), "the record says which is the long stretch")
                self.assertEqual(("proven_admits", "long_stretch"), (plans[PLACED_FAR].get("binding_tier"), plans[STRETCH].get("binding_tier")))
                self.assertEqual(("proven_admits", "nearest_the_pad"), (chosen.get("binding_tier"), chosen.get("binding_rule")))

    def test_a_block_the_proven_policy_refuses_is_taken_after_one_it_places_though_its_plan_under_the_cap_moves_the_base_less(self):
        """block_selection THE BINDING TIERS (the lead's decision of 2026-10-03; verify r12 safety MF1 and replay MUST-FIX 1,
        which the scene search reads by the same rule): a block the proven policy places is taken wherever the scene has one."""
        ctx = scene(self, {"minus_15": [CAPPED], "plus_15": [PLACED_FAR]})
        found = search(ctx, self.WORDS + PLACEMENT_WORDS)
        (chosen,) = fields(ctx, "SEARCH_SCENE_LEAST_MOTION")
        plans = {tuple(candidate["position_pad_m"]): candidate for candidate in chosen["candidates"]}
        self.assertEqual({PLACED_FAR, CAPPED}, set(plans))
        self.assertEqual((("proven", "proven_admits"), ("advance_then_reach", "capped_robust")),
                         tuple((plans[block]["policy"], plans[block]["binding_tier"]) for block in (PLACED_FAR, CAPPED)))
        self.assertLess(plans[CAPPED]["base_advance_mm"], plans[PLACED_FAR]["base_advance_mm"], "not vacuous: the refused block's plan moves the base less")
        self.assertLessEqual(plans[CAPPED]["arm_out_mm"], cycle.ARM_LOADED_REACH_OUT_MM, "under the loaded reach cap: not a long stretch")
        self.assertEqual(list(PLACED_FAR), chosen["position_pad_m"])
        self.assertEqual(list(PLACED_FAR), found.seen["cube_by_template"]["cube_center_pad_m"])
        self.assertEqual("proven_admits", chosen["binding_tier"])

    def test_a_block_the_bands_refuse_holds_its_tier_so_a_block_of_a_later_tier_is_not_taken(self):
        """verify r13 gates MUST-FIX 1 (the lead's decision of 2026-10-03 on every binding path): the scene search reads the
        tiers of every block with a placement, the ones SEARCH's pickup bands refuse included, before the bands, as the mission
        binding does (block_selection THE BINDING TIERS). BANDED, a block the proven policy places that the return bound
        refuses, holds the first tier, so SIDE_STRETCH, which only the long stretch places, is not taken: BANDED is, and SEARCH
        refuses it with the band's own code before anything is picked up. Reading the bands first took SIDE_STRETCH."""
        ctx = scene(self, {"home": [BANDED, SIDE_STRETCH]})
        with self.assertRaises(cycle.CycleFailed) as refused:
            search(ctx, self.WORDS + PLACEMENT_WORDS)
        self.assertEqual("search_cube_beyond_return_bound", harness.failure_code(ctx, refused.exception))
        (chosen,) = fields(ctx, "SEARCH_SCENE_LEAST_MOTION")
        plans = {tuple(candidate["position_pad_m"]): candidate for candidate in chosen["candidates"]}
        self.assertEqual({BANDED, SIDE_STRETCH}, set(plans))
        self.assertEqual((("OUTSIDE_PAN_BAND", True, "proven_admits"), ("BASE_ADVANCE", False, "long_stretch")),
                         tuple((plans[block]["class"], plans[block]["band_refused"], plans[block]["binding_tier"]) for block in (BANDED, SIDE_STRETCH)))
        self.assertLess(plans[SIDE_STRETCH]["base_advance_mm"], plans[BANDED]["base_advance_mm"], "not vacuous: the long stretch moves the base less")
        self.assertEqual((list(BANDED), "OUTSIDE_PAN_BAND", "proven_admits", 1), (chosen["position_pad_m"], chosen["block_class"], chosen["binding_tier"], chosen["admissible_blocks"]))
        self.assertFalse(any(label.startswith("PREGRASP") for label in ctx.labels("run_pose")), "nothing is picked up")

    def test_inside_the_first_tier_a_block_the_bands_refuse_is_never_taken_while_another_passes_them(self):
        """verify r8 replay MUST-FIX 3 in the scene search: BANDED and PLACED share the first tier (the proven policy places
        both); the bands refuse BANDED and pass PLACED, which is taken, and SEARCH goes on from its best view."""
        ctx = scene(self, {"home": [BANDED, PLACED]})
        found = search(ctx, self.WORDS + PLACEMENT_WORDS)
        (chosen,) = fields(ctx, "SEARCH_SCENE_LEAST_MOTION")
        plans = {tuple(candidate["position_pad_m"]): candidate for candidate in chosen["candidates"]}
        self.assertEqual(((True, "proven_admits"), (False, "proven_admits")), tuple((plans[block]["band_refused"], plans[block]["binding_tier"]) for block in (BANDED, PLACED)))
        self.assertLess(plans[BANDED]["base_advance_mm"], plans[PLACED]["base_advance_mm"], "not vacuous: the block the bands refuse moves the base less")
        self.assertEqual(list(PLACED), chosen["position_pad_m"])
        self.assertEqual(list(PLACED), found.seen["cube_by_template"]["cube_center_pad_m"])

    def test_the_choice_runs_under_the_multi_block_rule(self):
        ctx = scene(self, {"minus_15": [NEAR], "plus_15": [FAR]})
        search(ctx, self.WORDS)
        entered = [args[0]["rule_ref"] for kind, args, _kwargs in ctx.events.calls if kind == "enter_policy" and args[0]]
        self.assertIn("search.multi_block", entered)

    def test_several_blocks_none_of_which_can_be_placed_are_refused_before_anything_moves_naming_them(self):
        ctx = scene(self, {"minus_15": [BEYOND[0]], "plus_15": [BEYOND[1]]})
        with self.assertRaises(cycle.CycleFailed) as refused:
            search(ctx, self.WORDS)
        self.assertEqual("search_multi_block_no_placeable_block", harness.failure_code(ctx, refused.exception))
        self.assertIn("block-1 at (+0.050, -0.300) m", str(refused.exception))
        self.assertIn("block-2 at (-0.120, -0.310) m", str(refused.exception))
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("scene_least_motion_block")])
        self.assertFalse(any(label.startswith("PREGRASP") for label in ctx.labels("run_pose")))

    def test_one_block_off_the_pad_is_the_proven_scene_search(self):
        proven, worded = scene(self, {"minus_15": [NEAR]}), scene(self, {"minus_15": [NEAR]})
        with mock.patch.object(wall_clock, "now_utc", ticking()):
            search(proven, ("scene_search",))
            search(worded, self.WORDS)
        self.assertEqual(proven.states(), worded.states())
        self.assertEqual(without_utc(fields(proven, "SEARCH_SCENE_SELECTED")), without_utc(fields(worded, "SEARCH_SCENE_SELECTED")))
        self.assertEqual([True], [entry["value"] for entry in worded.events.predicates("scene_one_unplaced_block")])

    def test_an_empty_scene_is_refused_as_the_proven_scene_search_refuses_it_under_a_code_of_its_own(self):
        proven, worded = scene(self, {}), scene(self, {})
        for ctx, words, code in ((proven, ("scene_search",), "search_scene_no_block"), (worded, self.WORDS, "search_multi_block_no_block")):
            with self.assertRaises(cycle.CycleFailed) as refused:
                search(ctx, words)
            self.assertEqual(code, harness.failure_code(ctx, refused.exception))
        self.assertEqual(proven.states(), worded.states())


class WithoutTheWord(unittest.TestCase):
    def setUp(self):
        scenes.home_file(self)

    def test_the_proven_refusal_of_several_blocks_off_the_pad_stands(self):
        ctx = scene(self, {"minus_15": [NEAR], "plus_15": [FAR]})
        with self.assertRaises(cycle.CycleFailed) as refused:
            search(ctx, ("scene_search",))
        self.assertEqual("search_scene_not_one_unplaced_block", harness.failure_code(ctx, refused.exception))

    def test_the_window_lends_the_scene_search_to_multi_block_for_the_length_of_search_only(self):
        ctx = harness.HarnessCycle()
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", ("scene_search", "multi_block")):
            with cycle._placement_policies_at_search(ctx):
                self.assertIs(bs.search_from_scene, policy_search._search_from_scene)
            self.assertIs(PROVEN_SCENE_SEARCH, policy_search._search_from_scene)
            with self.assertRaises(RuntimeError), cycle._placement_policies_at_search(harness.HarnessCycle()):
                raise RuntimeError("SEARCH ended by an error")
            self.assertIs(PROVEN_SCENE_SEARCH, policy_search._search_from_scene)
        for words in ((), ("scene_search",), ("arm_reach",)):
            with self.subTest(words=words), mock.patch.object(cycle, "PLACEMENT_ENVELOPE", words), cycle._placement_policies_at_search(harness.HarnessCycle()):
                self.assertIs(PROVEN_SCENE_SEARCH, policy_search._search_from_scene)


if __name__ == "__main__":
    unittest.main()
