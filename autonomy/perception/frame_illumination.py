#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""frame_illumination: the gross illumination sanity diagnostic, shared by
every camera and every observation.

This is a sanity check, not a robustness mechanism
(autonomous-single-cube-plan.md section 8.2): it only answers "is this frame
effectively dark", so an observation made with the lights off is labelled
unusable instead of being mistaken for an empty scene. Normal daylight,
artificial and mixed light all sit far above the threshold and must never
need it retuned.

Threshold provenance: first derived in the one-axis pan calibration harness
from recorded frames. Lit wrist frames measured a whole-frame HSV value mean
of 86.7 to 120 under two different lamps; lights-off frames measured 0.30 to
0.33. 15.0 is more than 40x the worst dark reading and under 1/5 of the
weakest lit reading. That harness keeps its own copy; this module is the
shared one for new perception code and adds clipping fractions as atlas
diagnostics.

numpy only. HSV value is the per-pixel channel maximum, which is what
OpenCV's BGR2HSV produces for 8-bit frames, so no OpenCV import is needed.
"""
import numpy as np

ILLUMINATION_MIN_V = 15.0
DARK_PIXEL_MAX_V = 5
SATURATED_PIXEL_MIN_V = 250

STATUS_VALID = "CAMERA_ILLUMINATION_VALID"
STATUS_TOO_DARK = "CAMERA_ILLUMINATION_TOO_DARK"
STATUS_NO_FRAME = "CAMERA_ILLUMINATION_NO_FRAME"


def illumination_diagnostics(frame_bgr) -> dict:
    """Whole-frame brightness statistics for one 8-bit color frame (channel
    order is irrelevant: only the per-pixel maximum is used). Never raises on
    a missing frame; a failed capture is reported as unusable."""
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return {
            "usable": False,
            "status": STATUS_NO_FRAME,
            "v_mean": None,
            "v_median": None,
            "dark_fraction": None,
            "saturated_fraction": None,
            "min_v_threshold": ILLUMINATION_MIN_V,
        }

    value = np.asarray(frame_bgr).max(axis=-1) if np.asarray(frame_bgr).ndim == 3 else np.asarray(frame_bgr)
    v_mean = float(value.mean())
    usable = v_mean >= ILLUMINATION_MIN_V
    return {
        "usable": bool(usable),
        "status": STATUS_VALID if usable else STATUS_TOO_DARK,
        "v_mean": v_mean,
        "v_median": float(np.median(value)),
        "dark_fraction": float((value <= DARK_PIXEL_MAX_V).mean()),
        "saturated_fraction": float((value >= SATURATED_PIXEL_MIN_V).mean()),
        "min_v_threshold": ILLUMINATION_MIN_V,
    }
