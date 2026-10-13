# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import unittest

import numpy as np

from vla import frame


class FrameTest(unittest.TestCase):
    def test_shape_dtype_and_colour_order(self):
        bgr = np.zeros((720, 1280, 3), np.uint8)
        bgr[..., 0] = 200  # blue in BGR
        out = frame.training_frame(bgr)
        self.assertEqual(out.shape, (640, 480, 3))
        self.assertEqual(out.dtype, np.uint8)
        self.assertGreater(out[..., 2].mean(), 150)  # blue is channel 2 in RGB
        self.assertLess(out[..., 0].mean(), 5)

    def test_crop_box_is_the_measured_one(self):
        self.assertEqual(frame.CROP_BOX, (160, 0, 1120, 720))  # measured on the robot 2026-10-08 (tools/vla/measure_wrist_mapping.py)

    def test_rotation_matches_the_recorder(self):
        # a marker in the top-left of the cropped frame lands where cv2.rotate(ROTATE) puts it
        bgr = np.zeros((720, 1280, 3), np.uint8)
        x0, y0, x1, y1 = frame.CROP_BOX
        bgr[y0:y0 + 40, x0:x0 + 40] = 255
        out = frame.training_frame(bgr)
        ref = np.zeros((480, 640, 3), np.uint8)
        ref[0:27, 0:27] = 255
        import cv2
        ref = cv2.rotate(ref, frame.ROTATE)
        self.assertGreater(float((out[..., 0] > 128).astype(float).ravel() @ (ref[..., 0] > 128).astype(float).ravel()), 300)

    def test_refuses_another_size(self):
        with self.assertRaises(ValueError):
            frame.training_frame(np.zeros((480, 640, 3), np.uint8))


if __name__ == "__main__":
    unittest.main()
