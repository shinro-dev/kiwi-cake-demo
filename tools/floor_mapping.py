#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""body_floor_mapping_v0 -- an empirical, task-specific BODY-image-pixel to
floor-plane mapping, fit directly from manually-clicked (u,v) <-> (x,y)
correspondences. This is NOT a calibrated camera model (no intrinsics, no
lens distortion correction, no extrinsic pose) -- it is a single planar
homography valid only near the fitted operating region, good enough for a
closed-loop controller that observes/moves-a-bounded-step/re-observes rather
than needing a one-shot precise estimate. See
docs/autonomous-demo/measurement-experiment-spec.md and
docs/autonomous-demo/perception-localization.md for where real intrinsic
calibration fits in later.

Library:
    fit_homography(pairs) -> 3x3 np.ndarray
        pairs: iterable of ((u, v), (x, y))
    apply_homography(H, u, v) -> (x, y)
    residuals(H, pairs) -> list of (cube_id_or_None, error_m)

CLI (for ad-hoc fitting/inspection against LOCAL, ignored correspondence
files -- never pass real measurements as literal arguments that could end up
committed):
    python3 tools/floor_mapping.py fit --pairs pairs.json
        pairs.json: {"cube_1": {"u":.., "v":.., "x":.., "y":..}, ...}
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as e:
    sys.exit(f"floor_mapping: missing dependency ({e})")


def fit_homography(pairs):
    """pairs: list of ((u, v), (x, y)). Returns the 3x3 homography image->floor."""
    pairs = list(pairs)
    if len(pairs) < 4:
        raise ValueError(f"need at least 4 correspondences, got {len(pairs)}")
    src = np.array([[u, v] for (u, v), _ in pairs], dtype=np.float64)
    dst = np.array([[x, y] for _, (x, y) in pairs], dtype=np.float64)
    method = 0 if len(pairs) <= 6 else cv2.RANSAC  # tiny hand-verified sets: plain least squares is more legible than RANSAC
    H, mask = cv2.findHomography(src, dst, method=method)
    if H is None:
        raise ValueError("cv2.findHomography failed to fit a homography")
    return H


def apply_homography(H, u, v):
    pt = np.array([[[float(u), float(v)]]], dtype=np.float64)
    out = cv2.perspectiveTransform(pt, H)
    x, y = out[0, 0]
    return float(x), float(y)


def position_error(H, u, v, x_true, y_true):
    x_pred, y_pred = apply_homography(H, u, v)
    return ((x_pred - x_true) ** 2 + (y_pred - y_true) ** 2) ** 0.5, (x_pred, y_pred)


def load_pairs_file(path):
    data = json.loads(Path(path).read_text())
    pairs = []
    ids = []
    for cid, rec in data.items():
        pairs.append(((rec["u"], rec["v"]), (rec["x"], rec["y"])))
        ids.append(cid)
    return ids, pairs


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    fit_p = sub.add_parser("fit", help="fit a homography from a pairs JSON file and print it + residuals")
    fit_p.add_argument("--pairs", type=Path, required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "fit":
        ids, pairs = load_pairs_file(args.pairs)
        H = fit_homography(pairs)
        print("body_floor_mapping_v0 homography (image (u,v) -> floor (x,y)):")
        print(H)
        print()
        errs = []
        for cid, ((u, v), (x, y)) in zip(ids, pairs):
            err, (xp, yp) = position_error(H, u, v, x, y)
            errs.append(err)
            print(f"{cid}: true=({x:.3f},{y:.3f}) pred=({xp:.3f},{yp:.3f}) err={err*100:.1f}cm")
        errs.sort()
        if errs:
            median = errs[len(errs) // 2]
            print(f"\nmedian fit-set error: {median*100:.1f}cm, worst: {errs[-1]*100:.1f}cm")


if __name__ == "__main__":
    main()
