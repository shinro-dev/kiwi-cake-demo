#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""measure_wrist_mapping.py: ON THE ROBOT, with the wrist camera free and the arm still. Captures the wrist camera at
640x480 (the recorder's mode) and at 1280x720 (the camera service's mode), back to back, MJPG, and finds where the
640x480 view sits inside the 1280x720 one (ORB features, RANSAC similarity). Prints the crop box in 1280x720 pixels
and the residual, and writes both frames next to --out.
  python3 measure_wrist_mapping.py --device /dev/v4l/by-id/<wrist> --out DIR
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def grab(device: str, w: int, h: int) -> np.ndarray:
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    frame = None
    for _ in range(15):  # let exposure settle
        ok, frame = cap.read()
    cap.release()
    if frame is None or frame.shape[:2] != (h, w):
        sys.exit(f"no {w}x{h} frame from {device}")
    return frame


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    small = grab(a.device, 640, 480)
    time.sleep(0.5)
    big = grab(a.device, 1280, 720)
    cv2.imwrite(str(out / "mode-640x480.png"), small)
    cv2.imwrite(str(out / "mode-1280x720.png"), big)
    orb = cv2.ORB_create(4000)
    ks, ds = orb.detectAndCompute(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), None)
    kb, db = orb.detectAndCompute(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY), None)
    matches = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(ds, db), key=lambda m: m.distance)[:800]
    src = np.float32([ks[m.queryIdx].pt for m in matches])
    dst = np.float32([kb[m.trainIdx].pt for m in matches])
    M, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    x0, y0 = float(M[0, 2]), float(M[1, 2])
    box = [round(x0), round(y0), round(x0 + 640 * scale), round(y0 + 480 * scale)]
    proj = (src @ M[:, :2].T) + M[:, 2]
    resid = float(np.median(np.linalg.norm(proj - dst, axis=1)[inliers.ravel() == 1]))
    result = {"crop_box_1280x720": box, "scale": round(scale, 4), "rotation_deg": round(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), 3),
              "inliers": int(inliers.sum()), "median_residual_px": round(resid, 2)}
    (out / "mapping.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
