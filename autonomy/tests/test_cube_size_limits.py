#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cube_size_limits.py: the apparent-size window of cube_recognizer, against the sizes the ROBOT
# produced.
#
# Why this test exists. On 2026-09-22 run console-20260922T194137Z-gellh5f4 failed with align_cube_not_detected. The
# cube was in the decision frame, the detector scored it higher than in any other frame of that run, and its silhouette
# passed every shape criterion. One constant rejected it: box_side_fraction_max on the unrotated path, 0.30 of image
# width, which is 384.0 px on a 1280 px frame, against a proposal box whose longest side was 384.31 px. The cycle was
# lost by a third of a pixel. See docs/autonomous-demo/align-failure-diagnosis-2026-09-22.md.
#
# The numbers below are measurements, not choices. They were read off the retained perception records of five recorded
# console runs (67 cube proposals) and every box named here was confirmed by eye on the frame it came from. They are
# stated here rather than in the module because a window is only defensible against the population it was measured on,
# and a test is where that population belongs: moving the window back inside the true-cube range has to fail something.
#
# THIS IS THE CALIBRATION CORPUS, NOT HELD-OUT EVIDENCE. The bound was CHOSEN from these same populations, so this file
# checks internal consistency: that the bound sits where the measurement said, and that it still rejects what was
# recorded as a false positive. It cannot tell anyone the bound generalises. No held-out set exists; producing one
# means frames from a run this bound did not come from. The bound also moves 128 px, from 384.0 to 512.0 on a 1280 px
# frame, which is about four hundred times the 0.31 px miss that exposed the defect: the miss is what was noticed, the
# population separation is what justifies the size of the change.
#
# Everything here is arithmetic over recorded numbers. It loads no image, no model and no private data.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "perception"))

import cube_recognizer  # noqa: E402

UNROTATED = False  # the direct-v4l camera-service path, which is what the wrist camera runs
IMAGE_WIDTH_PX = 1280

# The two populations on the UNROTATED path, as fractions of image width, from the retained records of runs
# console-20260922T083804Z, console-20260922T142619Z, console-20260922T144329Z, console-20260922T180557Z and
# console-20260922T194137Z.
#
# A true cube at working distance. The cycle makes its ALIGN, GRASP_CLOSE, CARRY and RELEASE_ADMISSION decisions in
# this range, so the window has to contain all of it.
TRUE_CUBE_LARGEST = 0.3633  # 465 px, obs-20260922T181034Z-wrist-0001, the cube in the jaws at RELEASED
# A verified false positive: a box around the pad's printed badge, not the cube. The window has to exclude all of it.
FALSE_POSITIVE_SMALLEST = 0.4352  # 557 px, obs-20260922T084309Z-wrist-0001

# The decision frame of the failed run, exactly as cube_recognizer.verify received it. Recorded, not constructed: these
# are the values the run itself computed, recovered by running the same perception offline over the retained frame.
FAILED_DECISION_FRAME = {
    "observation": "obs-20260922T194245Z-wrist-0001",
    "box_xyxy": [427.990478515625, 1.7343521118164062, 787.1182861328125, 386.04364013671875],
    "mask_area_px": 92732,
    "features": {"solidity": 0.958, "polygon_vertices": 5, "polygon_iou": 0.924, "aspect_ratio": 1.208, "box_iou": 0.917},
    "clipped": {"left": False, "right": False, "top": True, "bottom": False, "any_border": True, "occluded_hint": False},
}

# The largest cube box the PROVEN run verified: 383 px against a 384 px ceiling, obs-20260922T181023Z-wrist-0001.
# A gate a passing run clears by one pixel is not a gate.
PROVEN_RUN_LARGEST_VERIFIED = 0.2992
REQUIRED_HEADROOM = 0.03  # of image width, about 38 px: below this the window is deciding runs by arithmetic noise


def side_fraction(box_xyxy, image_width: int = IMAGE_WIDTH_PX) -> float:
    return max(box_xyxy[2] - box_xyxy[0], box_xyxy[3] - box_xyxy[1]) / float(image_width)


def box_of_side(side_px: float) -> list:
    """A box whose longest side is side_px, positioned away from any border."""
    return [10.0, 10.0, 10.0 + side_px, 10.0 + side_px * 0.9]


def a_clean_silhouette() -> dict:
    """Shape features comfortably inside every non-size limit, so that only the size window can reject a box."""
    return {"solidity": 0.97, "polygon_vertices": 4, "polygon_iou": 0.93, "aspect_ratio": 1.2, "box_iou": 0.9}


UNCLIPPED = {"left": False, "right": False, "top": False, "bottom": False, "any_border": False, "occluded_hint": False}


def size_reasons(reasons) -> list:
    return [r for r in reasons if "of image width" in r]


class CubeSizeWindowTest(unittest.TestCase):
    """The window has to sit between the two measured populations, not inside either of them."""

    def ceiling(self) -> float:
        return cube_recognizer.LIMITS["box_side_fraction_max"][UNROTATED]

    def test_the_ceiling_is_above_every_true_cube_the_robot_produced(self):
        self.assertGreater(
            self.ceiling(),
            TRUE_CUBE_LARGEST,
            f"the ceiling {self.ceiling()} is inside the true-cube population, which reaches {TRUE_CUBE_LARGEST} "
            f"({TRUE_CUBE_LARGEST * IMAGE_WIDTH_PX:.0f} px): real cubes at working distance will be reported as absent",
        )

    def test_the_ceiling_is_below_every_verified_false_positive(self):
        self.assertLess(
            self.ceiling(),
            FALSE_POSITIVE_SMALLEST,
            f"the ceiling {self.ceiling()} admits the pad badge, which starts at {FALSE_POSITIVE_SMALLEST} "
            f"({FALSE_POSITIVE_SMALLEST * IMAGE_WIDTH_PX:.0f} px): the window would stop rejecting a known false positive",
        )

    def test_the_ceiling_clears_the_proven_run_by_more_than_arithmetic_noise(self):
        headroom = self.ceiling() - PROVEN_RUN_LARGEST_VERIFIED
        self.assertGreaterEqual(
            headroom,
            REQUIRED_HEADROOM,
            f"the proven run's largest verified cube was {PROVEN_RUN_LARGEST_VERIFIED} "
            f"({PROVEN_RUN_LARGEST_VERIFIED * IMAGE_WIDTH_PX:.0f} px) and the ceiling is {self.ceiling()} "
            f"({self.ceiling() * IMAGE_WIDTH_PX:.0f} px): {headroom * IMAGE_WIDTH_PX:.1f} px of headroom decides a "
            f"cycle by which of several near-identical detector proposals lands under the line",
        )

    def test_the_rotated_host_window_is_not_touched(self):
        """The rotated path was calibrated against its own population and is out of scope here."""
        self.assertEqual(cube_recognizer.LIMITS["box_side_fraction_max"][True], 0.90)
        self.assertEqual(cube_recognizer.LIMITS["box_side_fraction_min"][True], 0.48)

    def test_the_unrotated_floor_stays_open(self):
        """A cube seen from SEARCH range spans about 0.08 of image width; nothing may put a floor above that."""
        self.assertLessEqual(cube_recognizer.LIMITS["box_side_fraction_min"][UNROTATED], 0.08)


class RecordedDecisionFrameTest(unittest.TestCase):
    """The frame that lost the cycle, put back through the verifier that rejected it."""

    def reasons(self) -> list:
        frame = FAILED_DECISION_FRAME
        return cube_recognizer.verify(
            frame["features"], frame["mask_area_px"], frame["box_xyxy"], IMAGE_WIDTH_PX, frame["clipped"], UNROTATED
        )

    def test_the_decision_frame_of_the_failed_run_verifies(self):
        self.assertEqual(
            self.reasons(),
            [],
            f"{FAILED_DECISION_FRAME['observation']} is the mission cube, centred, unoccluded and in the jaws, and it "
            f"is still rejected: {self.reasons()}",
        )

    def test_only_the_size_window_ever_rejected_that_frame(self):
        """Recorded fact: every criterion except the apparent-size ceiling passed, and with margin. If this ever fails,
        the frame is being rejected for a NEW reason and the diagnosis in the document no longer describes the code."""
        frame = FAILED_DECISION_FRAME
        reasons = self.reasons()
        self.assertEqual(
            [r for r in reasons if "of image width" not in r],
            [],
            f"a criterion other than the apparent-size window now rejects {frame['observation']}: {reasons}",
        )

    def test_that_frame_was_rejected_by_a_third_of_a_pixel(self):
        """The margin that lost the run, kept as a number so it cannot be described loosely later."""
        fraction = side_fraction(FAILED_DECISION_FRAME["box_xyxy"])
        self.assertAlmostEqual(fraction, 0.30024, places=5)
        self.assertAlmostEqual(fraction * IMAGE_WIDTH_PX, 384.31, places=2)


class SizeWindowStillRejectsTest(unittest.TestCase):
    """The window still has to do its job: a clean silhouette is not enough on its own."""

    def test_a_badge_sized_box_is_still_rejected_however_clean_its_silhouette(self):
        for fraction in (FALSE_POSITIVE_SMALLEST, 0.5617, 0.8500):
            with self.subTest(fraction=fraction):
                reasons = cube_recognizer.verify(
                    a_clean_silhouette(),
                    90000,
                    box_of_side(fraction * IMAGE_WIDTH_PX),
                    IMAGE_WIDTH_PX,
                    dict(UNCLIPPED),
                    UNROTATED,
                )
                self.assertTrue(
                    size_reasons(reasons),
                    f"a box of {fraction * IMAGE_WIDTH_PX:.0f} px is a recorded false positive and must stay rejected",
                )

    def test_a_working_distance_cube_is_admitted(self):
        for fraction in (0.2641, 0.2992, 0.30024, TRUE_CUBE_LARGEST):
            with self.subTest(fraction=fraction):
                reasons = cube_recognizer.verify(
                    a_clean_silhouette(),
                    90000,
                    box_of_side(fraction * IMAGE_WIDTH_PX),
                    IMAGE_WIDTH_PX,
                    dict(UNCLIPPED),
                    UNROTATED,
                )
                self.assertEqual(
                    size_reasons(reasons),
                    [],
                    f"a box of {fraction * IMAGE_WIDTH_PX:.0f} px is a recorded true cube at working distance",
                )

    def test_a_search_range_cube_is_still_admitted(self):
        reasons = cube_recognizer.verify(
            a_clean_silhouette(), 8409, box_of_side(0.0827 * IMAGE_WIDTH_PX), IMAGE_WIDTH_PX, dict(UNCLIPPED), UNROTATED
        )
        self.assertEqual(size_reasons(reasons), [], "the SEARCH-range cube of the failed run must stay admitted")


if __name__ == "__main__":
    unittest.main()
