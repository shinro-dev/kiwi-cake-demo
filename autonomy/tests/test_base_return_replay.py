# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The base returns of 2026-09-26, replayed from the recorded homographies (track R of
docs/autonomous-demo/overnight-plan-2026-09-26.md, section 8 item 3).

The numbers are the ones autonomy/tests/test_recorded_pins_20260926.py carries (PLACED_SCRIPTS, generated from the evidence
by replay_20260926.py): the observe views of the two placed runs, start 1 (mb3c1048) and start 2 (m2476137). Pure numbers:
no frame, no record file, no robot.

What it pins:
  - the proven return's reading: the axis the advance ENDED on (start 2: tilted to about 80.1 deg from pad +x by a 14.9 mm
    last pulse), the recorded reverse laterals against it (-5.0 and -4.1 mm on start 2) and the recorded BASE_RETURNED
    fields (start 2: remaining -0.1, sideways -9.1; start 1: 2.9 and -2.2);
  - the same reverse pulses against the START axis, which is what return_2d measures each pulse against;
  - the DIRECT start-to-end error policy_return measures from the SEARCH view: the robot-fixed point about 2.4 mm from
    where it started on start 2, almost all of it across the start axis, inside both gates of return_2d; start 1 inside
    both gates as well; a yaw under 1 deg on both.
"""
import math
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))

import run_single_cube_cycle as cycle  # noqa: E402
import policy_return  # noqa: E402
import stage_policy  # noqa: E402
from test_recorded_pins_20260926 import PLACED_SCRIPTS  # noqa: E402  (the recorded numbers, read only)

CTL = stage_policy.ControllerView(vars(cycle))
START_1, START_2 = "console-20260926T201540Z-mb3c1048", "console-20260926T203744Z-m2476137"
# The observe steps of each script: the SEARCH view, the approach view and one per forward pulse, then the return's first
# view (after EXIT and SETTLE) and one per reverse pulse (replay_20260926 lists them in the order the controller took them).
VIEWS = {START_1: {"forward": (10, 12, 14, 16), "reverse": (24, 26, 28, 30)}, START_2: {"forward": (12, 14, 16, 18), "reverse": (26, 28, 30)}}


def steps(run):
    return PLACED_SCRIPTS[run]["steps"]


def frame(run, index):
    return steps(run)[index]["records"][-1]["pad_by_template"]


def search_outcome(run):
    """What SEARCH hands on (policy_search.SearchOutcome's fields), from the recorded SEARCH view."""
    seen = steps(run)[0]["records"][-1]
    return types.SimpleNamespace(search_frame=frame(run, 0), seen=seen, template=seen["cube_by_template"], measured=steps(run)[0]["joints"])


def proven_advance(run, reference):
    """The proven advance loop's measurement chain (Cycle._advance_base_held): each pulse measured against the axis, the
    axis replaced by the pulse's own direction when it moved more than 10 mm. (anchor, total, axis)."""
    anchor, axis, total = reference["anchor_pad_m"], reference["axis"], 0.0
    forward = VIEWS[run]["forward"]
    for before, after in zip(forward, forward[1:]):
        moved = cycle.base_displacement(anchor, axis, frame(run, before)["homography_pad_to_image"], frame(run, after)["homography_image_to_pad"])
        step = float(math.hypot(*moved["shift_pad_mm"]))
        anchor, total = moved["anchor_pad_m"], total + step
        if step > 10.0:
            axis = (moved["shift_pad_mm"][0] / step, moved["shift_pad_mm"][1] / step)
    return anchor, total, axis


def reverse_pulses(run, anchor, axis):
    """Each recorded reverse pulse measured against `axis`, chained from `anchor` as Cycle.return_base chains it."""
    out, reverse = [], VIEWS[run]["reverse"]
    for before, after in zip(reverse, reverse[1:]):
        moved = cycle.base_displacement(anchor, axis, frame(run, before)["homography_pad_to_image"], frame(run, after)["homography_image_to_pad"])
        anchor = moved["anchor_pad_m"]
        out.append(moved)
    return out


class TheProvenReturnReadsAgainstTheTiltedAxis(unittest.TestCase):
    def test_start_2_axis_after_the_advance_is_tilted_by_the_last_pulse(self):
        reference = policy_return.start_reference(CTL, search_outcome(START_2))
        _anchor, total, axis = proven_advance(START_2, reference)
        self.assertAlmostEqual(total, 80.8, delta=0.1)
        self.assertAlmostEqual(math.degrees(math.atan2(axis[1], axis[0])), 80.1, delta=0.1)
        self.assertAlmostEqual(math.degrees(math.atan2(reference["axis"][1], reference["axis"][0])), 95.0, delta=0.1, msg="the start axis is the planned reach direction")

    def test_the_recorded_reverse_laterals_and_base_returned_fields_are_reproduced(self):
        recorded = {START_1: ([-0.8, -0.1, -1.3], 2.9, -2.2), START_2: ([-5.0, -4.1], -0.1, -9.1)}
        for run, (laterals, remaining_mm, sideways_mm) in recorded.items():
            with self.subTest(run=run):
                anchor, total, axis = proven_advance(run, policy_return.start_reference(CTL, search_outcome(run)))
                pulses = reverse_pulses(run, anchor, axis)
                self.assertEqual([round(pulse["lateral_mm"], 1) for pulse in pulses], laterals)
                self.assertEqual(round(total + sum(pulse["advance_mm"] for pulse in pulses), 1), remaining_mm)
                self.assertEqual(round(sum(pulse["lateral_mm"] for pulse in pulses), 1), sideways_mm)

    def test_start_2_reverse_pulses_against_the_start_axis_and_pad_y(self):
        reference = policy_return.start_reference(CTL, search_outcome(START_2))
        anchor, _total, _axis = proven_advance(START_2, reference)
        pulses = reverse_pulses(START_2, anchor, reference["axis"])
        self.assertEqual([round(pulse["lateral_mm"], 1) for pulse in pulses], [5.5, 6.5], "per reverse pulse, against the start axis")
        self.assertEqual([round(-pulse["shift_pad_mm"][0], 1) for pulse in pulses], [2.0, 3.0], "per reverse pulse, against pad +y (the return review's reading)")


class TheDirectErrorFromTheSearchView(unittest.TestCase):
    def error(self, run, index):
        reference = policy_return.start_reference(CTL, search_outcome(run))
        return policy_return.return_error_2d(CTL, reference, frame(run, index), steps(run)[index]["joints"])

    def test_start_2_came_back_within_about_2_4_mm_almost_all_across_the_start_axis(self):
        error = self.error(START_2, 30)
        self.assertAlmostEqual(math.hypot(*error["shift_pad_mm"]), 2.4, delta=0.1)
        self.assertEqual([round(v, 1) for v in error["shift_pad_mm"]], [2.4, -0.0])
        self.assertAlmostEqual(error["along_start_axis_mm"], -0.25, delta=0.05)
        self.assertAlmostEqual(error["lateral_mm"], -2.43, delta=0.05)
        self.assertAlmostEqual(error["yaw_deg"], -0.68, delta=0.05)
        self.assertTrue(error["comparable"])
        self.assertLessEqual(error["posture_difference_deg"], 0.3)
        self.assertTrue(policy_return.returned_2d(CTL, error), "inside both gates of return_2d")

    def test_start_1_came_back_inside_both_gates(self):
        error = self.error(START_1, 30)
        self.assertEqual([round(v, 1) for v in error["shift_pad_mm"]], [2.9, 2.6])
        self.assertAlmostEqual(error["along_start_axis_mm"], 2.46, delta=0.05)
        self.assertAlmostEqual(error["lateral_mm"], -2.97, delta=0.05)
        self.assertAlmostEqual(error["yaw_deg"], -0.90, delta=0.05)
        self.assertTrue(policy_return.returned_2d(CTL, error))

    def test_the_return_start_views_are_ahead_and_to_the_right_of_the_start(self):
        """The first view of each return, after EXIT and SETTLE: the direct error return_2d starts its pulses from."""
        for run, index, along, lateral in ((START_1, 24, 105.8, -11.7), (START_2, 26, 80.2, -14.4)):
            with self.subTest(run=run):
                error = self.error(run, index)
                self.assertAlmostEqual(error["along_start_axis_mm"], along, delta=0.1)
                self.assertAlmostEqual(error["lateral_mm"], lateral, delta=0.1)
                self.assertTrue(error["comparable"])
                self.assertFalse(policy_return.returned_2d(CTL, error))


if __name__ == "__main__":
    unittest.main()
