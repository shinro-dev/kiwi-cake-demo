#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""pad_localizer: metric pose of the KNOWN collection pad in one image.

What it is for: the pad (a planar dark-teal desk mat of known size) is the
mission's metric reference. Given a detector box around the pad and a focal
length, localize_pad fits the 6-DoF pose of the rounded-rectangle pad model
to the segmented pad boundary and returns the image <-> PAD_FRAME planar
homography.

PAD_FRAME: origin at the pad center, x along the long edge in the READING
direction of the printed text, y toward the pad's TOP edge (the edge nearer
the two text lines), z up, right-handed, meters. Pad corners are at
(+-0.2075, +-0.1475); the logos sit at negative y. Camera frame: OpenCV
convention (x right, y down, z forward), meters. Image points: pixels.

How: (1) the fabric color model is LEARNED from the detector box (k-means on
Lab chroma, Mahalanobis gate on the dominant cluster); no fixed color
threshold decides anything; (2) a coarse pose grid is scored by mask IoU;
(3) the best starts are refined by a robust signed boundary distance in
which boundary points INSIDE the model (occluders such as gripper jaws or a
cube) saturate quickly and mask points on the image border only have to lie
inside the model; (4) with three visible edges the pad turned by 90 degrees
at another distance explains the same image lines, so that twin is always
fitted too, and the branding decides: the text block is the largest bright
hole of the fabric mask and runs along x, the smaller logo holes lie on its
-y side. (On the real pad the text block sits near the pad center, so its
own y position is NOT used as evidence.)

What it does NOT establish: the focal length is an INPUT. Three visible
edges of a rectangle of known size fix the pose only for a known focal
length, and the boundary residual does not change with it, so an error in
focal_px goes straight into camera_height_m and the scale along the viewing
direction. Lens distortion is ignored. The homography maps image points to the pad's
TOP surface (z = 0): a point on the floor lies one pad thickness lower and
is displaced by thickness * horizontal_distance / camera_height (about 1 cm
in the grazing front view, under 1 mm in the elevated wrist view; the pad
thickness is not recorded). With fewer than three supported pad
edges no homography is returned. confidence is a fit-quality score, not a
probability. The acceptance numbers below were set while looking at eight
frames of one static scene, not fixed before data. Known weak spot: a long
straight occluder edge parallel to a HIDDEN pad edge (a text line cut by the
image border) can attract the model edge during the coarse starts.

Needs cv2, numpy and scipy.
"""
import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import cv2
import numpy as np
from scipy.optimize import least_squares

PAD_SIZE_M = (0.415, 0.295)  # long edge, short edge (recorded project value)
CORNER_RADIUS_M = 0.015  # fixed: weakly observed, and a free radius drifted to its bound and dragged the pose
MAHALANOBIS_GATE = 3.5  # fabric gate, in sigmas of the learned Lab cluster
INSIDE_SATURATION_PX = 6.0  # boundary points inside the model: occluders, saturate early
OUTSIDE_SATURATION_PX = 25.0  # boundary points outside the model: unexplained mask
INLIER_GATE_PX = 5.0
MIN_EDGE_POINTS = 40  # inlier boundary points for a pad edge to count as supported
MIN_EDGE_SPAN_PX = 25.0
MIN_EDGE_SPAN_FRACTION = 0.25  # of the in-image extent of that projected model edge
# Pad identity: the merged text block of THIS pad, measured in PAD_FRAME on the real frames
# (std along x 0.079-0.081 m, area 0.0167-0.0171 m2; dark non-pad rectangles gave 0.032-0.040 m, 0.0025-0.0045 m2).
TEXT_STD_X_RANGE_M = (0.060, 0.100)
TEXT_AREA_RANGE_M2 = (0.006, 0.022)  # lower bound leaves room for a text block cut by the image border
MIN_FREE_POINTS = 40  # boundary points that are not on the image border
ACCEPT_IOU = 0.80
ACCEPT_INLIER_FRACTION = 0.50
ELEVATIONS_DEG = (-6, 0, 6, 12, 20, 32, 46, 62, 80)  # optical axis against the pad plane; <= 0: a grazing camera looking level
RECTIFY_RES_M = 0.0025
TEXT_MERGE_M = 0.022  # closing that merges letters and the two text lines into one block
MIN_TEXT_AREA_M2 = 0.002
MIN_LOGO_AREA_M2 = 0.00015  # bright holes beside the text block, enough to call them logos
MIN_TEXT_LOGO_GAP_M = 0.04  # logo holes lie at least this far from the text block center in y
EDGE_NAMES = ("bottom", "right", "top", "left")  # label k of pad_outline


@dataclass
class PadObservation:
    detected: bool = False  # a pose fit was accepted (three supported edges, agreement gates)
    confidence: float = 0.0  # iou * sqrt(inlier_fraction) / (1 + (boundary_rms_px / 3)^2)
    image_polygon: list = field(default_factory=list)  # fitted outline clipped to the image, px
    mask_contour: list = field(default_factory=list)  # segmented visible pad region, px
    homography_image_to_pad: Optional[list] = None  # homogeneous px -> PAD_FRAME meters (z = 0)
    homography_pad_to_image: Optional[list] = None
    center_px: Optional[list] = None  # projected pad center, may lie outside the image
    visible_fraction: float = 0.0  # share of the pad AREA projecting inside the image bounds
    clipped_sides: list = field(default_factory=list)  # pad edges not fully inside the image
    orientation_resolved: bool = False  # branding fixed the edge assignment and the flip
    R_cam_pad: Optional[list] = None  # rotation PAD_FRAME -> camera frame
    t_cam_pad_m: Optional[list] = None  # pad center in the camera frame, meters
    camera_height_m: Optional[float] = None  # camera center above the pad plane
    view_elevation_deg: Optional[float] = None  # angle between the pad plane and the ray pad center -> camera
    boundary_rms_px: Optional[float] = None  # RMS distance of the inlier boundary points
    focal_px_used: float = 0.0
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def pad_outline(size_m, radius_m, step_m=0.004):
    """Closed rounded-rectangle outline in PAD_FRAME meters, counter-clockwise
    from the bottom-left: (M, 2) points and (M,) labels, 0 bottom, 1 right,
    2 top, 3 left (EDGE_NAMES), -1 on a corner arc."""
    hw, hh = size_m[0] / 2.0, size_m[1] / 2.0
    corners = np.array([(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)])
    points, labels = [], []
    for k in range(4):
        p0, p1 = corners[k], corners[(k + 1) % 4]
        along = (p1 - p0) / np.linalg.norm(p1 - p0)
        a, b = p0 + along * radius_m, p1 - along * radius_m
        n = max(2, int(np.linalg.norm(b - a) / step_m))
        points.append(a + np.outer(np.linspace(0.0, 1.0, n, endpoint=False), b - a))
        labels += [k] * n
        center = b + radius_m * np.array([-along[1], along[0]])
        start = math.atan2(b[1] - center[1], b[0] - center[0])
        angles = start + np.linspace(0.0, math.pi / 2.0, 6, endpoint=False)
        points.append(center + radius_m * np.column_stack([np.cos(angles), np.sin(angles)]))
        labels += [-1] * 6
    return np.vstack(points), np.array(labels)


def project_pad_points(K, R, t, points_xy, z_min=0.02):
    """PAD_FRAME points (N, 2) on the pad plane -> pixels (N', 2) plus the keep
    mask of the points in front of the camera (camera z > z_min meters)."""
    cam = np.asarray(points_xy, dtype=float) @ R[:, :2].T + t
    keep = cam[:, 2] > z_min
    cam = cam[keep]
    uv = cam[:, :2] / cam[:, 2:3] * (K[0, 0], K[1, 1]) + (K[0, 2], K[1, 2])
    return np.clip(uv, -1e6, 1e6), keep


def segment_pad(image_bgr, box_xyxy):
    """Fabric mask with holes filled, and the bright holes (branding), both
    uint8 0/255 at image size, or None. The fabric color is learned from the
    box interior; MAHALANOBIS_GATE is relative to that learned cluster."""
    h, w = image_bgr.shape[:2]
    x0, y0 = max(0, int(box_xyxy[0])), max(0, int(box_xyxy[1]))
    x1, y1 = min(w, int(box_xyxy[2])), min(h, int(box_xyxy[3]))
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None
    # No blur before the gate: blurring mixes background into the edge pixels,
    # they then fail the gate, and the mask boundary moves about 2 px inward.
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    core = lab[y0:y1:4, x0:x1:4].reshape(-1, 3)
    cv2.setRNGSeed(7)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    # Cluster on chroma (a, b) only: the fabric keeps its chroma across the strong
    # brightness gradient of a lamp-lit pad; its L spread is then learned, not assumed.
    _, assigned, _ = cv2.kmeans(np.ascontiguousarray(core[:, 1:]), 3, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    fabric = core[assigned.ravel() == np.argmax(np.bincount(assigned.ravel(), minlength=3))]
    # Near-neutral occluders (white jaws, white print) can share the fabric's chroma cluster and
    # would inflate its learned L spread until white passes the gate: trim L by median and MAD first.
    lightness = fabric[:, 0]
    sigma = 1.4826 * np.median(np.abs(lightness - np.median(lightness))) + 1.0
    fabric = fabric[np.abs(lightness - np.median(lightness)) <= MAHALANOBIS_GATE * sigma]
    mean = fabric.mean(axis=0)
    inverse = np.linalg.inv(np.cov(fabric.T) + np.eye(3))
    delta = lab.reshape(-1, 3) - mean
    distance2 = np.einsum("ij,jk,ik->i", delta, inverse, delta).reshape(h, w)
    raw = (distance2 < MAHALANOBIS_GATE**2).astype(np.uint8)
    raw = cv2.morphologyEx(cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(raw)
    if count < 2:
        return None
    inside = np.bincount(labels[y0:y1, x0:x1].ravel(), minlength=count)
    inside[0] = 0
    component = (labels == int(np.argmax(inside))).astype(np.uint8)
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros((h, w), np.uint8)
    cv2.drawContours(filled, contours, -1, 255, -1)
    holes = ((filled > 0) & (component == 0) & (lab[:, :, 0] > mean[0])).astype(np.uint8) * 255
    return filled, holes


def _rotation(axis, angle):
    c, s = math.cos(angle), math.sin(angle)
    if axis == "x":
        return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _coarse_search(mask, K, size_m):
    """Pose starts from a grid scored by mask IoU at quarter resolution: two
    starts per (edge assignment, camera roll). Params are rvec + t."""
    small = cv2.resize(mask, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA) > 127
    ys, xs = np.nonzero(mask)
    (_, (rect_w, rect_h), angle) = cv2.minAreaRect(np.column_stack([xs, ys])[::37].astype(np.float32))
    ray = np.linalg.solve(K, np.array([xs.mean(), ys.mean(), 1.0]))
    outline, _ = pad_outline(size_m, CORNER_RADIUS_M, step_m=0.01)
    facing = np.diag([1.0, -1.0, -1.0])  # pad seen from above, pad y toward image up
    starts = []
    for family in (0, 1):  # which pad edge runs along the mask's long direction
        for roll in (0, 90, 180, 270):
            group = []
            for yaw_offset in (-15, 0, 15):
                yaw = -math.radians(angle) + family * math.pi / 2.0 + math.radians(yaw_offset)
                for elevation in ELEVATIONS_DEG:
                    tilt = _rotation("x", -math.radians(90.0 - elevation))
                    R = _rotation("z", math.radians(roll)) @ tilt @ facing @ _rotation("z", yaw)
                    for scale in (0.6, 0.85, 1.2, 1.7):
                        depth = K[0, 0] * size_m[0] / max(rect_w, rect_h, 1.0) * scale
                        for ax in (-0.25, 0.0, 0.25):
                            for ay in (-0.25, 0.0, 0.25):
                                t = depth * ray - R @ np.array([ax * size_m[0], ay * size_m[1], 0.0])
                                uv, _ = project_pad_points(K, R, t, outline)
                                if len(uv) < 3:
                                    continue
                                canvas = np.zeros(small.shape, np.uint8)
                                cv2.fillPoly(canvas, [np.clip(uv * 0.25, -2e4, 2e4).astype(np.int32)], 1)
                                model = canvas > 0
                                iou = (model & small).sum() / max(1, (model | small).sum())
                                group.append((iou, elevation, np.concatenate([cv2.Rodrigues(R)[0].ravel(), t])))
            group.sort(key=lambda g: -g[0])
            starts += group[:1] + [g for g in group if g[1] != group[0][1]][:1]  # best, and best at another elevation
    return [params for _, _, params in sorted(starts, key=lambda s: -s[0])]


def _signed_distances(params, K, size_m, points):
    """Pixel distance of image points to the projected model outline, positive
    OUTSIDE the model. None when the model is not in front of the camera."""
    outline, _ = pad_outline(size_m, CORNER_RADIUS_M, step_m=0.006)
    uv, _ = project_pad_points(K, cv2.Rodrigues(params[:3])[0], params[3:6], outline)
    if len(uv) < 8:
        return None
    polygon = uv.astype(np.float32).reshape(-1, 1, 2)
    return np.array([-cv2.pointPolygonTest(polygon, (float(x), float(y)), True) for x, y in points])


def _residuals(params, K, size_m, free, border, soften):
    distances = _signed_distances(params, K, size_m, np.vstack([free, border]))
    if distances is None:
        return np.full(len(free) + len(border), OUTSIDE_SATURATION_PX * soften)
    distances[len(free) :] = np.maximum(distances[len(free) :], 0.0)  # border points: inside is free
    saturation = np.where(distances > 0.0, OUTSIDE_SATURATION_PX, INSIDE_SATURATION_PX) * soften
    return distances / np.sqrt(1.0 + (distances / saturation) ** 2)


def _refine(start, K, size_m, free, border, stages=((5.0, 40), (1.0, 60))):
    """Robust pose refinement, graduated: a soft pass with wide saturation so a
    rough grid start still feels the edges, then the final saturation. A start
    that is already close must skip the soft pass: with wide saturation a long
    straight occlusion notch can pull a model edge onto itself. Returns (cost
    at the final saturation, params = rvec, t)."""
    params = np.asarray(start, dtype=float)
    for soften, evaluations in stages:
        solution = least_squares(
            _residuals, params, args=(K, size_m, free, border, soften),
            method="trf", diff_step=1e-4, x_scale=[0.1] * 3 + [0.05] * 3, max_nfev=evaluations,
        )
        params = solution.x
    return 2.0 * solution.cost, params


def _pose(params):
    """(R, t) of params with the camera on the +z side of the pad: the outline
    is mirror symmetric, so a fit may come out as the same outline seen from below."""
    R, t = cv2.Rodrigues(params[:3])[0], np.array(params[3:6], dtype=float)
    return (R @ np.diag([1.0, -1.0, -1.0]) if (-R.T @ t)[2] < 0 else R), t


def _is_supported(points, model_extent_px=0.0):
    if len(points) < MIN_EDGE_POINTS:
        return False
    span = np.linalg.norm(points.max(axis=0) - points.min(axis=0))
    return span >= max(MIN_EDGE_SPAN_PX, MIN_EDGE_SPAN_FRACTION * model_extent_px)


def _edge_support(params, K, size_m, free_all):
    """Signed distances of all free boundary points, and the inlier points
    attributed to each straight pad edge (by the nearest projected model sample)."""
    distances = _signed_distances(params, K, size_m, free_all)
    R, t = _pose(params)
    outline, labels = pad_outline(size_m, CORNER_RADIUS_M)
    uv, keep = project_pad_points(K, R, t, outline)
    if distances is None or len(uv) < 8:
        return None
    inlier = np.abs(distances) <= INLIER_GATE_PX
    points = free_all[inlier]
    nearest = np.argmin(((points[:, None, :] - uv[None, :, :]) ** 2).sum(axis=2), axis=1) if len(points) else np.zeros(0, int)
    edges = {name: points[labels[keep][nearest] == k] for k, name in enumerate(EDGE_NAMES)}
    width, height = 2.0 * K[0, 2], 2.0 * K[1, 2]  # extent of each model edge inside the image (principal point at the center)
    extents = {}
    for k, name in enumerate(EDGE_NAMES):
        e = uv[labels[keep] == k]
        e = e[(e[:, 0] >= 0) & (e[:, 0] < width) & (e[:, 1] >= 0) & (e[:, 1] < height)]
        extents[name] = float(np.linalg.norm(e.max(axis=0) - e.min(axis=0))) if len(e) else 0.0
    supported = [n for n, pts in edges.items() if _is_supported(pts, extents[n])]
    return distances, inlier, edges, supported


def _swapped_twin(params, size_m, supported):
    """With exactly three supported edges the fourth is out of view, and the
    same three image lines are explained equally well by the pad turned 90
    degrees at another distance (long and short edge exchanged). Returns that
    twin's start params, or None. Only the branding can tell the two apart."""
    if len(supported) != 3:
        return None
    R, t = _pose(params)
    inward = {"bottom": (0.0, 1.0), "right": (-1.0, 0.0), "top": (0.0, -1.0), "left": (1.0, 0.0)}
    n_old = -np.array(inward[[n for n in EDGE_NAMES if n not in supported][0]])  # inward normal of the middle edge
    n_new = np.array([n_old[1], -n_old[0]])  # the same direction in the frame turned by +90 degrees
    # An edge with a y normal is a long edge: size_m[0] long, size_m[1] / 2 from the center.
    (len_old, half_old), (len_new, half_new) = [
        (size_m[0], size_m[1] / 2.0) if abs(n[1]) > 0.5 else (size_m[1], size_m[0] / 2.0) for n in (n_old, n_new)
    ]
    middle_cam = R @ np.append(-n_old * half_old, 0.0) + t
    R_new = R @ _rotation("z", math.pi / 2.0)
    t_new = (len_new / len_old) * middle_cam - R_new @ np.append(-n_new * half_new, 0.0)
    return np.concatenate([cv2.Rodrigues(R_new)[0].ravel(), t_new])


def _homographies(K, R, t):
    pad_to_image = K @ np.column_stack([R[:, 0], R[:, 1], t])
    return np.linalg.inv(pad_to_image), pad_to_image


def _branding_evidence(holes, image_to_pad, size_m):
    """The text block in PAD_FRAME: the largest bright hole of the fabric mask
    after rectification and a closing that merges letters and both lines.
    Returns center and spread in meters, or None when there is no such block."""
    res = RECTIFY_RES_M
    cols, rows = int(round(size_m[0] / res)), int(round(size_m[1] / res))
    to_raster = np.array([[1.0 / res, 0.0, cols / 2.0], [0.0, -1.0 / res, rows / 2.0], [0.0, 0.0, 1.0]])
    rectified = cv2.warpPerspective(holes, to_raster @ image_to_pad, (cols, rows), flags=cv2.INTER_NEAREST)
    k = int(round(TEXT_MERGE_M / res)) | 1
    rectified = cv2.morphologyEx(rectified, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(rectified)
    if count < 2:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area_m2 = float(stats[biggest, cv2.CC_STAT_AREA]) * res * res
    if area_m2 < MIN_TEXT_AREA_M2:
        return None
    ys, xs = np.nonzero(labels == biggest)
    x_m, y_m = (xs + 0.5) * res - size_m[0] / 2.0, size_m[1] / 2.0 - (ys + 0.5) * res
    # Every other bright hole is a logo candidate: area on either side of the text block.
    rest_y_m = size_m[1] / 2.0 - (np.nonzero((labels > 0) & (labels != biggest))[0] + 0.5) * res
    below = float((rest_y_m < y_m.mean() - MIN_TEXT_LOGO_GAP_M).sum()) * res * res
    above = float((rest_y_m > y_m.mean() + MIN_TEXT_LOGO_GAP_M).sum()) * res * res
    return {
        "x_m": float(x_m.mean()), "y_m": float(y_m.mean()), "std_x_m": float(x_m.std()), "std_y_m": float(y_m.std()),
        "area_m2": area_m2, "logos_below_m2": below, "logos_above_m2": above,
    }


def _fit_pad(image_bgr, pad_box_xyxy, focal_px, principal_point, pad_size_m):
    """The whole fit. Returns (PadObservation, internals or None); internals
    carry the per-edge inlier points and the holes (for inspection only)."""
    obs = PadObservation(focal_px_used=float(focal_px))
    h, w = image_bgr.shape[:2]
    segmented = segment_pad(image_bgr, pad_box_xyxy)
    if segmented is None:
        obs.notes.append("no fabric region learned from the box: no fit")
        return obs, None
    mask, holes = segmented
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contour = max(contours, key=cv2.contourArea)
    obs.mask_contour = cv2.approxPolyDP(contour, 1.5, True).reshape(-1, 2).astype(float).tolist()
    contour = contour.reshape(-1, 2).astype(float)
    on_border = (contour[:, 0] <= 1) | (contour[:, 1] <= 1) | (contour[:, 0] >= w - 2) | (contour[:, 1] >= h - 2)
    free_all, border_all = contour[~on_border], contour[on_border]
    if len(free_all) < MIN_FREE_POINTS:
        obs.notes.append("pad fabric segmented but its boundary lies on the image border: no pad edge, no homography")
        return obs, None
    free = free_all[:: max(1, len(free_all) // 300)]
    border = border_all[:: max(1, len(border_all) // 60)]
    cx, cy = principal_point if principal_point is not None else ((w - 1) / 2.0, (h - 1) / 2.0)
    K = np.array([[focal_px, 0.0, cx], [0.0, focal_px, cy], [0.0, 0.0, 1.0]], dtype=float)

    candidates = sorted((_refine(start, K, pad_size_m, free, border) for start in _coarse_search(mask, K, pad_size_m)), key=lambda c: c[0])
    support = _edge_support(candidates[0][1], K, pad_size_m, free_all)
    twin = _swapped_twin(candidates[0][1], pad_size_m, support[3]) if support else None
    if twin is not None:
        candidates = sorted(candidates + [_refine(twin, K, pad_size_m, free, border, stages=((1.0, 60),))], key=lambda c: c[0])

    chosen, evidence, text_center = candidates[0][1], None, None
    for cost, params in candidates:  # branding decides among fits of similar boundary cost
        if cost > 1.5 * candidates[0][0] + len(free):
            break
        found = _branding_evidence(holes, _homographies(K, *_pose(params))[0], pad_size_m)
        if found and found["std_x_m"] >= 1.5 * found["std_y_m"]:  # the text runs along the long edge
            chosen, evidence = params, found
            break
    R, t = _pose(chosen)
    if evidence is None:
        obs.notes.append("no text block along the long edge in the fabric holes: edge assignment and 180-degree flip NOT resolved")
    else:
        below, above = evidence["logos_below_m2"], evidence["logos_above_m2"]
        sign = -1.0 if above > below else 1.0
        text_center = (sign * evidence["x_m"], sign * evidence["y_m"])
        if max(below, above) >= MIN_LOGO_AREA_M2 and max(below, above) >= 4.0 * min(below, above):
            if sign < 0:  # logos must lie at -y of the text: turn the frame by 180 degrees
                R = R @ np.diag([-1.0, -1.0, 1.0])
            obs.orientation_resolved = True
            obs.notes.append(
                "orientation from branding: text block (largest bright fabric hole, %.4f m2, spread x %.3f m, y %.3f m) "
                "centered at PAD_FRAME (%+.4f, %+.4f) m; logo holes %.5f m2 on its -y side against %.5f m2 on its +y side"
                % (evidence["area_m2"], evidence["std_x_m"], evidence["std_y_m"], *text_center, max(below, above), min(below, above))
            )
        else:
            obs.notes.append("text block found (edge assignment resolved) but no one-sided logo holes: 180-degree flip NOT resolved")
    params = np.concatenate([cv2.Rodrigues(R)[0].ravel(), t])

    support = _edge_support(params, K, pad_size_m, free_all)
    if support is None:
        obs.notes.append("fitted model is not in front of the camera: no homography")
        return obs, None
    distances, inlier, edge_points, supported = support
    inlier_fraction = float(inlier.mean())
    outline, labels = pad_outline(pad_size_m, CORNER_RADIUS_M)
    uv, keep = project_pad_points(K, R, t, outline)
    model = np.zeros((h, w), np.uint8)
    cv2.fillPoly(model, [np.clip(uv, -2e4, 2e4).astype(np.int32)], 1)
    iou = float(((model > 0) & (mask > 0)).sum() / max(1, ((model > 0) | (mask > 0)).sum()))
    obs.notes.append(
        "supported edges: %s; inlier fraction %.2f (gate %.0f px); mask/model IoU %.3f"
        % (",".join(supported) or "none", inlier_fraction, INLIER_GATE_PX, iou)
    )
    if len(supported) < 3:
        obs.notes.append("fewer than three supported pad edges: pose not determined, no homography")
        return obs, None

    image_to_pad, pad_to_image = _homographies(K, R, t)
    camera_in_pad = -R.T @ t
    _, clipped = cv2.intersectConvexConvex(uv.astype(np.float32), np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32))
    grid = np.stack(np.meshgrid(np.linspace(-0.5, 0.5, 84)[1::2], np.linspace(-0.5, 0.5, 60)[1::2]), -1).reshape(-1, 2) * pad_size_m
    grid_uv, _ = project_pad_points(K, R, t, grid)
    seen = (grid_uv[:, 0] >= 0) & (grid_uv[:, 0] < w) & (grid_uv[:, 1] >= 0) & (grid_uv[:, 1] < h)
    for k, name in enumerate(EDGE_NAMES):
        edge_uv = uv[labels[keep] == k]
        whole = (labels == k).sum() == len(edge_uv)
        if not whole or edge_uv.min() < 0 or edge_uv[:, 0].max() >= w or edge_uv[:, 1].max() >= h:
            obs.clipped_sides.append(name)
    rms = float(np.sqrt(np.mean(distances[inlier] ** 2)))
    obs.image_polygon = clipped.reshape(-1, 2).astype(float).tolist() if clipped is not None else []
    obs.homography_image_to_pad = (image_to_pad / image_to_pad[2, 2]).tolist()
    obs.homography_pad_to_image = (pad_to_image / pad_to_image[2, 2]).tolist()
    obs.center_px = (pad_to_image[:2, 2] / pad_to_image[2, 2]).tolist()
    obs.visible_fraction = float(seen.sum() / len(grid))
    obs.R_cam_pad, obs.t_cam_pad_m = R.tolist(), t.tolist()
    obs.camera_height_m = float(camera_in_pad[2])
    obs.view_elevation_deg = float(math.degrees(math.asin(camera_in_pad[2] / np.linalg.norm(camera_in_pad))))
    obs.boundary_rms_px = rms
    obs.confidence = float(iou * math.sqrt(inlier_fraction) / (1.0 + (rms / 3.0) ** 2))
    if evidence is None:
        obs.confidence *= 0.5  # the long/short edge assignment is a coin toss without the text block
    is_this_pad = bool(
        evidence is not None
        and TEXT_STD_X_RANGE_M[0] <= evidence["std_x_m"] <= TEXT_STD_X_RANGE_M[1]
        and TEXT_AREA_RANGE_M2[0] <= evidence["area_m2"] <= TEXT_AREA_RANGE_M2[1]
    )
    obs.detected = bool(iou >= ACCEPT_IOU and inlier_fraction >= ACCEPT_INLIER_FRACTION and is_this_pad)
    if not obs.detected:
        reason = "IoU or inlier fraction under the acceptance gate" if is_this_pad else "the printed text block of this pad was not found at its known size (identity check)"
        obs.notes.append("fit rejected: %s; rejected fit had confidence %.2f, camera height %.3f m" % (reason, obs.confidence, obs.camera_height_m))
        obs.homography_image_to_pad = obs.homography_pad_to_image = obs.R_cam_pad = obs.t_cam_pad_m = None
        obs.confidence = 0.0
    return obs, {"edge_points": edge_points, "holes": holes, "image_to_pad": image_to_pad, "text_center_m": text_center}


def localize_pad(image_bgr, pad_box_xyxy, focal_px, principal_point=None, pad_size_m=PAD_SIZE_M) -> PadObservation:
    """Pose of the pad in one BGR image. pad_box_xyxy: detector box in pixels.
    focal_px: focal length in pixels (square pixels); principal_point: (cx, cy)
    pixels, image center when None. Never raises on a poor image: it returns
    detected=False with the reason in notes."""
    return _fit_pad(np.asarray(image_bgr), pad_box_xyxy, float(focal_px), principal_point, tuple(pad_size_m))[0]
