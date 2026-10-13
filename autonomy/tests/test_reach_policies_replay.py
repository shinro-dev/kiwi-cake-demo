#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_reach_policies_replay.py: every recorded start through the placement policies
# (autonomy/tests/reach_policies_replay.py). What is pinned, on the pinned starts, always: no start the installed
# controller (31c546c) admits is refused under arm_reach and advance_then_reach, on any of its axes, and none needs more
# base advance; the pickup band and the pan band refuse what they refused; start 3 of 2026-09-26 stays refused; the
# operator's block of 2026-09-30 is admitted by advance_then_reach under the loaded reach cap, with a 97 mm advance and a
# 36 mm travel on both of its axis signs, as a run naming both placement words plans it (the operator's ruling of
# 2026-10-02, Q8; its longer carry, 53 mm of base and a 91 mm travel, is the plan that ruling replaced); every plan
# admitted after is one the controller's own release admission admits; and the table names every row with no loss.
# With KC_EVIDENCE_ROOT naming the 2026-09-30 evidence root, the pinned starts are what the recorded SEARCH frames say:
# every row with a homography is derived again from the records, and its decisions from the unrounded inputs agree.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_reach_policies_replay.py
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import reach_policies_replay as replay  # noqa: E402
import reach_model  # noqa: E402  (on the path through the replay module)
import run_single_cube_cycle as cycle  # noqa: E402

RESULTS = None


def results() -> list:
    """(start, [one result per axis]) for every replayable pinned start, computed once."""
    global RESULTS
    if RESULTS is None:
        RESULTS = [(start, replay.replay(cycle, start)) for start in replay.RECORDED_STARTS if start.anchor is not None]
    return RESULTS


def placed(verdict: str) -> bool:
    return not verdict.startswith("refused")


class NoRecordedStartIsLost(unittest.TestCase):
    def test_every_start_the_installed_controller_admits_stays_admitted_with_no_more_advance(self):
        for start, answers in results():
            for answer in answers:
                with self.subTest(run=start.run, axis=answer["axis"]):
                    if placed(answer["before"]):
                        self.assertTrue(placed(answer["after"]), answer)
                        self.assertLessEqual(answer["after_placement"]["advance"], answer["before_placement"]["advance"])
                        self.assertNotEqual("LOST", replay.change(answer))

    def test_the_pickup_and_pan_bands_refuse_what_they_refused(self):
        for start, answers in results():
            for answer in answers:
                if answer["before"] in ("refused: TOO_NEAR", "refused: pan band"):
                    with self.subTest(run=start.run, axis=answer["axis"]):
                        self.assertEqual(answer["before"], answer["after"])

    def test_start_3_of_2026_09_26_stays_refused(self):
        for start, answers in results():
            if start.row == "21":
                for answer in answers:
                    with self.subTest(run=start.run):
                        self.assertFalse(placed(answer["after"]))

    def test_the_operators_block_is_advance_then_reachs_under_the_loaded_reach_cap_with_97_mm_on_both_axis_signs(self):
        """The operator's ruling of 2026-10-02 (Q8): a plan under the loaded reach cap fits the 120 mm budget, so AFTER, as a
        run naming both placement words plans it, plans the operator's block under that cap: 97 mm of base and a 36 mm FK
        travel to about 58 mm out. Its longer carry (53 mm of base, a 91 mm travel to about 113 mm out) is the plan of a run
        that names no policy placing it under the cap (verify r11 gates MF1: the replay planned that one, its harness never
        naming the run's words, which the loaded reach cap reads)."""
        (live,) = [(start, answers) for start, answers in results() if start.run == replay.LIVE_RUN]
        start, answers = live
        self.assertEqual(2, len(answers))
        for answer in answers:
            with self.subTest(axis=answer["axis"]):
                self.assertTrue(answer["before"].startswith("refused: along_budget"))
                self.assertEqual("advance_then_reach BASE_ADVANCE a97 t36", answer["after"])
                self.assertLessEqual(answer["after_placement"]["out"], cycle.ARM_LOADED_REACH_OUT_MM - reach_model.LOADED_SHORTFALL_MAX_MM + 0.05, "inside the proven loaded reach")
                self.assertEqual("new", replay.change(answer))

    def test_every_plan_admitted_after_is_one_the_release_admission_admits(self):
        checked = 0
        for start, answers in results():
            for answer in answers:
                plan = answer["after_placement"]
                if plan["admitted"]:
                    checked += 1
                    moved = (start.anchor[0] + answer["axis"][0] * plan["advance"] / 1000.0, start.anchor[1] + answer["axis"][1] * plan["advance"] / 1000.0)
                    with self.subTest(run=start.run, axis=answer["axis"]):
                        self.assertGreaterEqual(cycle.release_margin_worst_mm(moved, answer["axis"], plan["travel"]), cycle.PLACE_REQUIRED_CENTRE_MM)
        self.assertGreater(checked, 30)

    def test_the_table_names_every_row_and_no_loss(self):
        text = replay.table(cycle)
        self.assertEqual(len(replay.RECORDED_STARTS) + 2, len(text.strip().splitlines()))
        self.assertNotIn("LOST", text)
        counted = replay.summary(cycle)["counts"]
        self.assertEqual(0, counted.get("LOST", 0))
        self.assertEqual({"kept", "improved", "new", "still refused", "not replayed"}, set(counted))


class ThePinnedStartsAreTheRecords(unittest.TestCase):
    """LOCAL ONLY: KC_EVIDENCE_ROOT names the 2026-09-30 evidence root (its README says what it holds). Nothing of it is
    copied or printed: a mismatch names the run id only."""

    def setUp(self):
        root = os.environ.get(replay.EVIDENCE_ROOT_ENV)
        if not root or not os.path.isdir(root):
            self.skipTest(f"{replay.EVIDENCE_ROOT_ENV} does not name the evidence root: the pinned starts are not compared with the records")
        self.root = root

    def test_every_row_with_a_homography_is_derived_again_from_the_records(self):
        derived = replay.evidence_starts(cycle, self.root)
        pinned = {start.run: start for start in replay.RECORDED_STARTS if start.axis_source == replay.HOMOGRAPHY}
        self.assertEqual(sorted(pinned), sorted(run for run in derived if run in pinned))
        self.assertEqual(29, len(pinned))
        for run, start in pinned.items():
            with self.subTest(run=run):
                self.assertEqual((start.anchor, start.axes, start.cube_out_mm, start.pan_from_search_deg),
                                 (derived[run].anchor, derived[run].axes, derived[run].cube_out_mm, derived[run].pan_from_search_deg))

    def test_the_decisions_from_the_unrounded_inputs_agree(self):
        exact = replay.evidence_starts(cycle, self.root, digits=None)
        for start, answers in results():
            if start.axis_source != replay.HOMOGRAPHY:
                continue
            (answer,) = answers
            (again,) = replay.replay(cycle, exact[start.run])
            with self.subTest(run=start.run):
                self.assertEqual(placed(answer["before"]), placed(again["before"]))
                self.assertEqual(placed(answer["after"]), placed(again["after"]))
                for key in ("before_placement", "after_placement"):
                    if answer[key]["admitted"]:
                        self.assertEqual(answer[key]["policy"], again[key]["policy"])
                        self.assertLessEqual(abs(answer[key]["advance"] - again[key]["advance"]), 1.0)


if __name__ == "__main__":
    unittest.main()
