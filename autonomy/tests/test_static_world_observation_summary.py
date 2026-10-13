#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_static_world_observation_summary.py: the text summary of one frozen perception record never raises.
# Seen on hardware 2026-09-20: the outline fit solved the cube at ONE focal length of its sweep, not the nominal one, and the
# summary crashed AFTER the arm had moved, so the step that followed the observation never ran. No robot, no network.
# Run directly: python3 autonomy/tests/test_static_world_observation_summary.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import static_world_observation as swo  # noqa: E402

CUBE = {"detected": True, "confidence": 0.9, "clipped": {}, "contact_px": [710.5, 502.1]}


def _record(solved_focals):
    sweep = [{"focal_px": focal, "pad_detected": True, **({"cube": {"cube_center_pad_m": [0.06, -0.164], "inside_pad_outline": False}} if focal in solved_focals else {})} for focal in (1020.0, 1200.0, 1380.0)]
    return {"camera": "wrist", "pad": None, "pad_note": "no usable outline fit", "cube": CUBE, "focal_sweep": sweep}


class SummaryTest(unittest.TestCase):
    def test_a_sweep_solved_only_off_the_nominal_focal_length_is_reported_not_raised(self):
        lines = swo.summarize(_record({1380.0}))
        self.assertTrue(any("focal sweep" in line for line in lines))
        self.assertTrue(any("not solved at the nominal focal length" in line for line in lines))

    def test_the_nominal_estimate_is_still_reported_when_it_exists(self):
        lines = swo.summarize(_record({1020.0, 1200.0, 1380.0}))
        self.assertTrue(any(line.startswith("  CUBE in PAD_FRAME: x +0.060 m, y -0.164 m") for line in lines))


if __name__ == "__main__":
    unittest.main()
