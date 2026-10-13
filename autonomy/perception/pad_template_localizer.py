#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""pad_template_localizer: recognize and localize the KNOWN collection pad from
its PRINT (letters, logos), also when only a part of the pad is in view.

What it is for: the pad is a textured planar landmark, not an anonymous
rectangle. pad_localizer needs three visible pad edges; useful camera views
usually show fewer. Here local features (SIFT) of the live pad region are
matched against a rectified template of the pad (tools/build_pad_template.py)
and a robust homography is fitted to the matches. No pad edge, corner or hem
and no focal length is needed. The print is not symmetric, so the 180-degree
ambiguity of the outline does not exist here: the text reads one way round.

PAD_FRAME: origin at the pad center, x along the long edge in the READING
direction of the print, y toward the pad's top edge (away from the logos), z up,
meters; corners at (+-0.2075, +-0.1475). Image points: pixels of the image as
passed in (x right, y down). Homographies are 3x3, homogeneous, between image
pixels and PAD_FRAME meters on the pad's top surface (z = 0).

How: the live box (grown, upsampled, CLAHE) is matched with Lowe's ratio test
against the template AND against renderings of it compressed along pad y
(TILTS: the foreshortening of an oblique view, which plain SIFT only partly
survives). The matches of all renderings are pooled ONE-TO-ONE and
cv2.findHomography (USAC_MAGSAC) fits template -> image, so the inlier gate and
reprojection_rms_px are in LIVE image pixels, and many live points that match
one template point can never count as agreement.

Two separate answers. pad_identity: at least MIN_IDENTITY_INLIERS consistent
landmark matches and no mirror: THIS pad is in view. pad_frame_valid: the
homography may be used as metric geometry. It needs at least MIN_FRAME_INLIERS
inliers whose PAD_FRAME convex hull covers at least MIN_HULL_AREA_M2 with a
standard deviation of at least MIN_SPREAD_M along BOTH pad axes (a single
logo row or one word is nearly collinear and does not fix the plane), an
orientation-preserving map with the whole pad in front of the horizon and a
convex projected outline, a local scale within SCALE_RANGE_PX_PER_M with
anisotropy at most MAX_ANISOTROPY, and reprojection rms at most MAX_RMS_PX.
Otherwise identity may still be true, the homographies stay None and reason
says why. These numbers were set on the project's frames of 2026-09-19 (eight
frames of one static scene plus two live scans), not fixed before data.

What it does NOT establish: accuracy against a ruler. The template INHERITS the
uncertainty of the outline fit it was built from (scale along pad y depends on
an assumed focal length: a few mm near the bottom hem, up to about 1 cm deep in
the pad) and that frame's lens distortion; a homography cannot model distortion
of the live frame either. The map is trustworthy near the inlier hull and
degrades when extrapolated to pad regions far from it. It says nothing about
points that are not on the pad's top surface.

Known limit, measured 2026-09-19: the grazing FRONT camera (about 6 cm above
the pad plane, letters about 20 px high) is NOT recognized: 5 to 7 inliers, the
same as on images without any pad. Compression renderings down to 0.18 did not
change that. Only a PERSPECTIVE rendering of the template close to the true
camera pose (within about 7 cm and under 20 degrees of yaw) gave 21 to 27
inliers, still below MIN_FRAME_INLIERS. That view needs a pose prior or
another method; it is reported as not recognized, never guessed.

Needs cv2 (with SIFT) and numpy. Reseeds the global OpenCV RNG.
"""
import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

import cv2
import numpy as np

PAD_SIZE_M = (0.415, 0.295)  # long edge, short edge (recorded project value)
TILTS = (1.0, 0.5, 0.3)  # template renderings: compression along pad y (1.0 = as built)
LIVE_UPSAMPLE = 2.0  # small print in a grazing or dark view only yields features when enlarged
BOX_GROW = 0.10  # the detector box may cut the pad: grown by this fraction per side
CLAHE_CLIP, CLAHE_GRID = 3.0, 8
SIFT_CONTRAST = 0.02  # below the OpenCV default 0.04: the dark live frames are low in contrast
RATIO = 0.75  # Lowe ratio test
RANSAC_GATE_PX = 3.0  # inlier gate, live image pixels
MIN_LIVE_FEATURES = 20
MIN_IDENTITY_INLIERS = 15
MIN_FRAME_INLIERS = 30
MIN_HULL_AREA_M2 = 0.006  # e.g. 0.12 m x 0.05 m; the whole print block spans about 0.03 m2
MIN_SPREAD_M = 0.015  # std of the inliers along pad x and along pad y, each
SCALE_RANGE_PX_PER_M = (200.0, 20000.0)  # sqrt(det) of the local pad -> image map at the inlier centroid
MAX_ANISOTROPY = 15.0  # ratio of its singular values; the outline fit of the grazing front view measured 4.0 to 6.4
MAX_RMS_PX = 2.0
_CACHE = {}


@dataclass
class PadTemplateObservation:
    pad_identity: bool = False  # enough consistent landmark matches: THIS pad is in view
    pad_frame_valid: bool = False  # the homography passed the geometry acceptance
    reason: str = ""  # why valid / why unresolved
    homography_image_to_pad: Optional[list] = None  # homogeneous px -> PAD_FRAME meters (z = 0)
    homography_pad_to_image: Optional[list] = None
    landmark_matches: int = 0  # ratio-test matches, one-to-one
    landmark_inliers: int = 0
    inlier_hull_area_m2: float = 0.0  # convex hull of the inliers in PAD_FRAME
    inlier_spread_m: list = field(default_factory=lambda: [0.0, 0.0])  # std along pad x, pad y
    reprojection_rms_px: float = 0.0  # inliers, live image pixels
    visible_pad_polygon_px: list = field(default_factory=list)  # projected pad outline clipped to the image
    orientation_ok: bool = False  # orientation-preserving: no mirror, the print reads the right way round
    inlier_points_px: list = field(default_factory=list)  # for overlays
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def pad_to_raster(size_m, px_per_m):
    """3x3 matrix, PAD_FRAME meters -> template raster pixels (x right, y down):
    column = (x + width / 2) * px_per_m - 0.5, row = (height / 2 - y) * px_per_m - 0.5."""
    return np.array([
        [px_per_m, 0.0, size_m[0] / 2.0 * px_per_m - 0.5],
        [0.0, -px_per_m, size_m[1] / 2.0 * px_per_m - 0.5],
        [0.0, 0.0, 1.0],
    ])


def _features(gray, mask=None):
    """SIFT on the CLAHE-equalized image: (N, 2) float32 points in pixels, descriptors."""
    equalized = cv2.createCLAHE(CLAHE_CLIP, (CLAHE_GRID, CLAHE_GRID)).apply(gray)
    keypoints, descriptors = cv2.SIFT_create(contrastThreshold=SIFT_CONTRAST).detectAndCompute(equalized, mask)
    return np.float32([k.pt for k in keypoints]).reshape(-1, 2), descriptors


def load_template(template_dir):
    """Template features for every TILTS rendering, keypoints in UNCOMPRESSED
    template raster pixels, plus the pad <-> raster matrices. Cached per directory."""
    key = os.path.realpath(template_dir)
    stamp = os.path.getmtime(os.path.join(key, "template.png"))
    if key in _CACHE and _CACHE[key]["stamp"] == stamp:
        return _CACHE[key]
    gray = cv2.imread(os.path.join(key, "template.png"), cv2.IMREAD_GRAYSCALE)
    valid = cv2.imread(os.path.join(key, "valid.png"), cv2.IMREAD_GRAYSCALE)
    with open(os.path.join(key, "template.json")) as handle:
        meta = json.load(handle)
    if gray is None or valid is None or gray.shape != valid.shape:
        raise FileNotFoundError("template.png / valid.png missing or of different size in %s" % template_dir)
    rows, cols = gray.shape
    renders = []
    for tilt in TILTS:
        image, mask, factor = gray, valid, 1.0
        if tilt < 1.0:  # what a grazing camera does to the print: low-pass along y, then sample
            out_rows = max(8, int(round(rows * tilt)))
            factor = out_rows / rows
            image = cv2.GaussianBlur(gray, (0, 0), sigmaX=0.01, sigmaY=0.8 * math.sqrt(1.0 / factor**2 - 1.0))
            image = cv2.resize(image, (cols, out_rows), interpolation=cv2.INTER_AREA)
            mask = cv2.resize(valid, (cols, out_rows), interpolation=cv2.INTER_NEAREST)
        points, descriptors = _features(image, mask)
        points[:, 1] = (points[:, 1] + 0.5) / factor - 0.5
        renders.append((tilt, points, descriptors))
    size_m = tuple(meta.get("pad_size_m", PAD_SIZE_M))
    to_raster = np.asarray(meta["pad_to_raster"], dtype=float)
    _CACHE[key] = {"stamp": stamp, "renders": renders, "pad_to_raster": to_raster, "size_m": size_m}
    return _CACHE[key]


def _apply(homography, points):
    """(N, 2) points through a 3x3 homography: (N, 2) mapped points and the (N,) homogeneous w."""
    vectors = np.column_stack([points, np.ones(len(points))]) @ np.asarray(homography, dtype=float).T
    w = vectors[:, 2]
    return vectors[:, :2] / np.where(np.abs(w) < 1e-12, 1e-12, w)[:, None], w


def _match(live_points, live_descriptors, renders):
    """Pooled ratio-test matches, ONE-TO-ONE (best ratio per live pixel location
    and per template location): (N, 2) template raster points, (N, 2) live
    points, and the tilt of every match."""
    best = {}
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    for tilt, points, descriptors in renders:
        if descriptors is None or len(descriptors) < 2:
            continue
        for pair in matcher.knnMatch(live_descriptors, descriptors, k=2):
            if len(pair) < 2 or pair[0].distance >= RATIO * pair[1].distance:
                continue
            location = tuple(np.round(live_points[pair[0].queryIdx]).astype(int))  # SIFT repeats a location per orientation
            ratio = pair[0].distance / max(pair[1].distance, 1e-6)
            if location not in best or ratio < best[location][0]:
                best[location] = (ratio, points[pair[0].trainIdx], live_points[pair[0].queryIdx], tilt)
    unique = {}  # and one live point per template location (2 raster px): a hub descriptor must not count many times
    for entry in best.values():
        location = tuple(np.round(entry[1] / 2.0).astype(int))
        if location not in unique or entry[0] < unique[location][0]:
            unique[location] = entry
    found = list(unique.values())
    return (np.float32([f[1] for f in found]).reshape(-1, 2), np.float32([f[2] for f in found]).reshape(-1, 2), [f[3] for f in found])


def localize_pad_by_template(image_bgr, pad_box_xyxy, template_dir) -> PadTemplateObservation:
    """Pad identity and, when the geometry acceptance passes, the image <->
    PAD_FRAME homography (pixels <-> meters, z = 0) of one BGR image, from the
    pad's print alone. pad_box_xyxy: detector box in pixels, or None for the
    whole image. Never raises on a poor image: it returns the reason. Raises
    only when the template directory cannot be read."""
    obs = PadTemplateObservation()
    template = load_template(template_dir)
    image = np.asarray(image_bgr)
    h, w = image.shape[:2]
    x0, y0, x1, y1 = (0, 0, w, h) if pad_box_xyxy is None else [float(v) for v in pad_box_xyxy]
    grow_x, grow_y = BOX_GROW * (x1 - x0), BOX_GROW * (y1 - y0)
    x0, y0 = int(max(0, math.floor(x0 - grow_x))), int(max(0, math.floor(y0 - grow_y)))
    x1, y1 = int(min(w, math.ceil(x1 + grow_x))), int(min(h, math.ceil(y1 + grow_y)))
    if x1 - x0 < 16 or y1 - y0 < 16:
        obs.reason = "box smaller than 16 px: nothing to match"
        return obs
    crop = image[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.resize(gray, None, fx=LIVE_UPSAMPLE, fy=LIVE_UPSAMPLE, interpolation=cv2.INTER_CUBIC)
    live_points, live_descriptors = _features(gray)
    live_points = (live_points + 0.5) / LIVE_UPSAMPLE - 0.5 + (x0, y0)
    if live_descriptors is None or len(live_points) < MIN_LIVE_FEATURES:
        obs.reason = "only %d local features in the box (texture-less or tiny patch): pad not recognized" % len(live_points)
        return obs
    raster_points, image_points, tilts = _match(live_points, live_descriptors, template["renders"])
    obs.landmark_matches = len(raster_points)
    if len(raster_points) < MIN_IDENTITY_INLIERS:
        obs.reason = "only %d ratio-test matches to the pad print: pad not recognized" % len(raster_points)
        return obs
    cv2.setRNGSeed(11)
    raster_to_image, inlier = cv2.findHomography(raster_points, image_points, cv2.USAC_MAGSAC, RANSAC_GATE_PX, maxIters=20000, confidence=0.999)
    inlier = inlier.ravel().astype(bool) if inlier is not None else np.zeros(len(raster_points), bool)
    obs.landmark_inliers = int(inlier.sum())
    if raster_to_image is None or obs.landmark_inliers < MIN_IDENTITY_INLIERS:
        obs.reason = "only %d of %d matches agree on one homography (identity needs %d): pad not recognized" % (
            obs.landmark_inliers, obs.landmark_matches, MIN_IDENTITY_INLIERS)
        return obs
    used = sorted(set(t for t, keep in zip(tilts, inlier) if keep))
    obs.notes.append("inliers by template rendering (compression along pad y): %s" % ", ".join("%.2f: %d" % (t, sum(1 for u, k in zip(tilts, inlier) if k and u == t)) for t in used))

    pad_to_image = raster_to_image @ template["pad_to_raster"]
    inlier_pad, _ = _apply(np.linalg.inv(template["pad_to_raster"]), raster_points[inlier])
    projected, w_inlier = _apply(pad_to_image, inlier_pad)
    half_w, half_h = template["size_m"][0] / 2.0, template["size_m"][1] / 2.0
    corners_pad = np.array([(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)])
    corners_px, w_corner = _apply(pad_to_image, corners_pad)
    sign = np.sign(np.median(w_inlier))
    # PAD_FRAME y is up and image y is down: a true view from above has a NEGATIVE pad -> image Jacobian,
    # whose sign is det(H) / w^3. The raster -> image map of the same view is orientation-preserving.
    obs.orientation_ok = bool(np.all(w_inlier * sign > 0) and np.linalg.det(raster_to_image) * sign > 0)
    obs.reprojection_rms_px = float(np.sqrt(np.mean(np.sum((projected - image_points[inlier]) ** 2, axis=1))))
    obs.inlier_points_px = image_points[inlier].astype(float).round(1).tolist()
    obs.inlier_spread_m = [float(v) for v in inlier_pad.std(axis=0)]
    obs.inlier_hull_area_m2 = float(cv2.contourArea(cv2.convexHull(inlier_pad.astype(np.float32))))
    if not obs.orientation_ok:
        obs.reason = "the matches fit only a MIRRORED or folded map: not this pad's print the right way round"
        return obs
    obs.pad_identity = True

    center = inlier_pad.mean(axis=0)
    step = 0.001  # local pad -> image map at the inlier centroid, px per meter, by 1 mm finite differences
    around, _ = _apply(pad_to_image, np.array([center, center + (step, 0.0), center + (0.0, step)]))
    singular = np.linalg.svd(np.column_stack([around[1] - around[0], around[2] - around[0]]) / step, compute_uv=False)
    scale, anisotropy = float(np.sqrt(singular[0] * singular[1])), float(singular[0] / max(singular[1], 1e-9))
    obs.notes.append("local scale at the inlier centroid %.0f px/m, anisotropy %.1f" % (scale, anisotropy))
    failures = []
    if obs.landmark_inliers < MIN_FRAME_INLIERS:
        failures.append("%d inliers, fewer than %d" % (obs.landmark_inliers, MIN_FRAME_INLIERS))
    if obs.inlier_hull_area_m2 < MIN_HULL_AREA_M2 or min(obs.inlier_spread_m) < MIN_SPREAD_M:
        failures.append("visible print patch too small or nearly collinear (hull %.4f m2, needs %.4f; spread x %.3f m, y %.3f m, needs %.3f each)" % (
            obs.inlier_hull_area_m2, MIN_HULL_AREA_M2, obs.inlier_spread_m[0], obs.inlier_spread_m[1], MIN_SPREAD_M))
    if not np.all(w_corner * sign > 0) or not cv2.isContourConvex(np.clip(corners_px, -1e6, 1e6).astype(np.float32).reshape(-1, 1, 2)):
        failures.append("projected pad outline is not a convex quadrilateral in front of the horizon")
    if not SCALE_RANGE_PX_PER_M[0] <= scale <= SCALE_RANGE_PX_PER_M[1] or anisotropy > MAX_ANISOTROPY:
        failures.append("implausible local scale %.0f px/m or anisotropy %.1f" % (scale, anisotropy))
    if obs.reprojection_rms_px > MAX_RMS_PX:
        failures.append("reprojection rms %.2f px above %.1f" % (obs.reprojection_rms_px, MAX_RMS_PX))
    if failures:
        obs.reason = "pad recognized by its print, PAD_FRAME unresolved: " + "; ".join(failures)
        return obs

    image_to_pad = np.linalg.inv(pad_to_image)
    obs.homography_image_to_pad = (image_to_pad / image_to_pad[2, 2]).tolist()
    obs.homography_pad_to_image = (pad_to_image / pad_to_image[2, 2]).tolist()
    bounds = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    _, clipped = cv2.intersectConvexConvex(np.clip(corners_px, -1e5, 1e5).astype(np.float32), bounds)
    obs.visible_pad_polygon_px = clipped.reshape(-1, 2).astype(float).tolist() if clipped is not None else []
    obs.pad_frame_valid = True
    obs.reason = "PAD_FRAME from %d print landmarks (hull %.4f m2, spread x %.3f m, y %.3f m, rms %.2f px)" % (
        obs.landmark_inliers, obs.inlier_hull_area_m2, obs.inlier_spread_m[0], obs.inlier_spread_m[1], obs.reprojection_rms_px)
    return obs
