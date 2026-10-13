# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""vla.frame: the camera service's wrist frame (1280x720 BGR, unrotated) as the policy was trained to see it (the guided
recorder's 640x480 frame, rotated by the robot's ROTATE_90, RGB: H 640 x W 480 x 3). CROP_BOX is MEASURED on this
camera (tools/vla/measure_wrist_mapping.py, 2026-10-08): where the 640x480 mode's field of view sits in the 1280x720
mode. ROTATE is the rotation the kinesthetic host applied (lerobot Cv2Rotation.ROTATE_90 = cv2.ROTATE_90_CLOCKWISE)."""
import cv2
import numpy as np

CROP_BOX = (160, 0, 1120, 720)  # measured 2026-10-08: scale 1.4999, rotation -0.05 deg, 226 inliers, residual 0.87 px
ROTATE = cv2.ROTATE_90_CLOCKWISE


def training_frame(frame_bgr: np.ndarray) -> np.ndarray:
    if frame_bgr is None or frame_bgr.shape[:2] != (720, 1280) or frame_bgr.ndim != 3:
        raise ValueError(f"expected a 1280x720 BGR frame, got {None if frame_bgr is None else frame_bgr.shape}")
    x0, y0, x1, y1 = CROP_BOX
    crop = cv2.resize(frame_bgr[y0:y1, x0:x1], (640, 480), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(cv2.cvtColor(cv2.rotate(crop, ROTATE), cv2.COLOR_BGR2RGB))
