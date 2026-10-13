#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test_floor_mapping.py: tools/floor_mapping.py's fit/apply round-trip
# against a synthetic, exactly-known homography -- proves the fitting code
# is mathematically correct, independent of any real measurement noise.
# Opens no camera, no image, no network. Run directly: python3 tests/test_floor_mapping.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    import numpy as np
    from floor_mapping import fit_homography, apply_homography, position_error
except ImportError as e:  # cv2/numpy not installed in this interpreter
    np = None
    IMPORT_ERROR = e
else:
    IMPORT_ERROR = None


@unittest.skipIf(IMPORT_ERROR is not None, f"cv2/numpy not available: {IMPORT_ERROR}")
class TestFloorMapping(unittest.TestCase):
    def setUp(self):
        # A synthetic ground-truth homography: pure known scale + shear + a
        # bit of projective warp, chosen arbitrarily but fixed (not random --
        # this file must not use Math.random-equivalents for reproducibility).
        self.H_true = np.array([
            [0.0008, 0.0001, -0.10],
            [0.0000, 0.0011, -0.55],
            [0.00002, 0.00001, 1.0],
        ], dtype=np.float64)

    def synth_point(self, u, v):
        pt = np.array([u, v, 1.0])
        out = self.H_true @ pt
        return out[0] / out[2], out[1] / out[2]

    def test_fit_recovers_exact_synthetic_points(self):
        uv_points = [(100, 600), (400, 500), (700, 450), (1000, 400), (1200, 380), (250, 420)]
        pairs = [((u, v), self.synth_point(u, v)) for u, v in uv_points]
        H = fit_homography(pairs)
        for (u, v), (x_true, y_true) in pairs:
            x_pred, y_pred = apply_homography(H, u, v)
            self.assertAlmostEqual(x_pred, x_true, places=6)
            self.assertAlmostEqual(y_pred, y_true, places=6)

    def test_held_out_synthetic_point_is_accurate(self):
        fit_uv = [(100, 600), (400, 500), (700, 450), (1000, 400), (1200, 380)]
        pairs = [((u, v), self.synth_point(u, v)) for u, v in fit_uv]
        H = fit_homography(pairs)
        held_out_u, held_out_v = 550, 470
        x_true, y_true = self.synth_point(held_out_u, held_out_v)
        err, (x_pred, y_pred) = position_error(H, held_out_u, held_out_v, x_true, y_true)
        self.assertLess(err, 1e-4, f"held-out synthetic point should fit near-exactly, got err={err}")

    def test_too_few_points_raises(self):
        pairs = [((0, 0), (0, 0)), ((1, 0), (1, 0)), ((0, 1), (0, 1))]
        with self.assertRaises(ValueError):
            fit_homography(pairs)


if __name__ == "__main__":
    unittest.main()
