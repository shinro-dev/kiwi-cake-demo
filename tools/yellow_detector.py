#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Classical yellow-cube detector baseline (no neural model), against saved frames only.

BGR -> HSV yellow/saturation mask -> morphology -> connected components ->
loose geometry filter. The cubes here are pale unpainted wood, close in hue
to the wood floor -- saturation is the main discriminator, not hue, per
empirical sampling at the manually-clicked cube locations (see
docs/autonomous-demo/decision-log.md-style reasoning in the commit, or just
re-run the sampling: floor saturation tops out around ~78, confident cube
samples run ~96-158). This is a v0 recall-first baseline, not a tuned
production detector.

Usage:
    python3 tools/yellow_detector.py <image.png> [--out result.json]

Importable:
    from yellow_detector import detect_candidates
    candidates = detect_candidates(bgr_image)  # list of dicts
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError as e:
    sys.exit(f"yellow_detector: missing dependency ({e})")

# HSV thresholds (OpenCV H in 0-179). Broad hue window since floor and cubes
# share a similar wood-toned hue; saturation is what actually separates them.
# Empirically: floor saturation tops out ~78, confident cube samples run
# ~96-158. SAT_MIN=100 leaves margin above the floor. KNOWN LIMITATION: at
# any single global threshold tried (80-120), some real cubes still fuse
# with each other or with the background wood door via contiguous
# same-saturation floor-glare pixels -- this is a real low-chroma-contrast
# scene limitation, not a threshold-tuning shortfall; see the evaluation
# report for actual recall on saved frames. Raising SAT_MIN further cleanly
# separates near/bright cubes but loses the smaller/farther/shadowed ones.
HUE_MIN, HUE_MAX = 8, 45
SAT_MIN = 100
VAL_MIN = 45

MIN_AREA_PX = 150  # smaller than any manually-confirmed cube blob (192px, the smallest/farthest sample) -- filters floor-texture speckle
MAX_AREA_PX = 20000  # bigger than the largest manually-confirmed cube blob (~10900px, nearest cube) with margin -- filters room-scale wood objects (e.g. the door frame)
MIN_ASPECT, MAX_ASPECT = 0.35, 3.0  # bbox width/height, loose (cube silhouettes vary with distance/angle)


def detect_candidates(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    mask = ((h >= HUE_MIN) & (h <= HUE_MAX) & (s >= SAT_MIN) & (v >= VAL_MIN)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    candidates = []
    for i in range(1, n):  # 0 is background
        x, y, w, h_box, area = stats[i]
        if area < MIN_AREA_PX or area > MAX_AREA_PX:
            continue
        aspect = w / h_box if h_box else 0
        if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
            continue
        cx, cy = centroids[i]
        bottom_center_u = cx  # centroid x is a reasonable bottom-center-x estimate for a roughly-vertical cube face
        bottom_center_v = y + h_box - 1  # bottom row of the bbox
        # quality: normalized area (bigger/cleaner blob = more confident) times mean saturation inside the blob
        comp_mask = (labels == i)
        mean_sat = float(s[comp_mask].mean())
        quality = min(1.0, area / 400.0) * min(1.0, mean_sat / 150.0)
        candidates.append({
            "bbox": [int(x), int(y), int(w), int(h_box)],
            "centroid": [round(float(cx), 1), round(float(cy), 1)],
            "bottom_center": [round(float(bottom_center_u), 1), round(float(bottom_center_v), 1)],
            "area_px": int(area),
            "mean_saturation": round(mean_sat, 1),
            "quality": round(quality, 3),
        })
    candidates.sort(key=lambda c: -c["quality"])
    return candidates


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", type=Path)
    p.add_argument("--out", type=Path, default=None, help="write candidates as JSON here")
    return p


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    bgr = cv2.imread(str(args.image))
    if bgr is None:
        sys.exit(f"yellow_detector: could not read {args.image}")
    candidates = detect_candidates(bgr)
    result = {"image": args.image.name, "width_px": bgr.shape[1], "height_px": bgr.shape[0], "candidates": candidates}
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"yellow_detector: {len(candidates)} candidates -> {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
