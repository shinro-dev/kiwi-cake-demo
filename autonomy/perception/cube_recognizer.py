#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""cube_recognizer: decides whether ONE known object, the pale-yellow 25.4 mm
mission cube, is really inside a box proposed by zero_shot_detector, and if so
where its mask and its ground-contact edge are in the image.

How a cube is accepted (both are required, colour alone never suffices):
  1. a detector proposal for the cube labels, and
  2. geometric verification of a mask refined from that proposal: cv2.grabCut
     started from the box (the box is the only prior; this module holds no
     HSV or other colour threshold that could decide existence), then the
     silhouette must be one convex blob with 4 to 6 corners and roughly
     straight sides that fills the proposed box, is not elongated unless an
     image border cuts it, and is not larger than the cube can be on that
     image path.

What this does NOT establish: no metric position, distance or size (that
needs the pad homography or a calibrated camera; contact_px and
bottom_edge_px are only the pixel inputs for it), no identity between two
cubes, and no protection against another convex box-shaped object that the
detector also proposes. A cube the detector does not propose is never found
here. Known limits seen on real images: a cube whose neighbour cube lies in
the background ring of its box may get no mask at all; a cube seen corner-on
may be segmented as one face only; the mask runs 1 to 3 px fat where a
contact shadow is present.

Frames and units: every coordinate is in pixels of the image AS PASSED IN
(x right, y down, origin top-left). contact_px and bottom_edge_px are only
produced for unrotated direct-V4L frames, where image-down is toward the
camera on the floor; on the rotated host wrist image they are None.

Needs cv2 and numpy. refine_mask reseeds the global OpenCV RNG.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

MAX_CANDIDATES = 8  # proposals evaluated per image, best score first
DUPLICATE_IOU = 0.7  # proposals overlapping this much are the same object
SAME_CUBE_OVERLAP = 0.3  # a mask sharing this much of itself with a verified mask is that cube again
BOX_GROW = 0.05  # grabCut rectangle: the box grown by this fraction per side
CONTEXT_GROW = 0.45  # crop: the box grown by this fraction per side (sure background ring)
WORK_SIDE_PX = 256  # grabCut runs on the crop scaled down to at most this side
GRABCUT_ITERATIONS = 5
GRABCUT_SEED = 0
OPEN_FRACTION = 0.12  # opening kernel / shorter box side: removes thin leaks (badge stripe, jaw rim)
BORDER_PX = 2  # a mask this close to an image border is clipped by it
JAW_L_MIN = 235  # occluded_hint only: "very bright" (overexposed) on the 0..255 Lab L scale
JAW_CHROMA_MAX = 12  # occluded_hint only: "white" as |a|+|b| distance from grey
JAW_BAND_FRACTION = 0.08  # occluded_hint only: share of the band around the mask that is jaw-like

# Verification limits. They describe the SILHOUETTE of a cube. They were set
# after looking at 30 real project images (2026-09-19) and kept round; they
# are not validated on any image outside that set.
LIMITS = {
    "mask_area_px_min": 150,  # smaller blobs carry no verifiable shape
    "solidity_min": 0.90,  # mask area / convex hull area
    "polygon_iou_min": 0.80,  # mask vs its own 4..6 corner polygon: straight sides
    "vertices": (4, 6),
    "box_iou_min": 0.55,  # mask bounding box vs proposed box
    "aspect_max": 2.0,  # long / short side of the min-area rectangle, unclipped masks only
    "box_side_fraction_max": {False: 0.40, True: 0.90},  # of image width; key: rotated host frame
    # Host close-ups: 31 real cubes span 0.54-0.74, the pad's orange badge (a verified false positive) 0.36-0.43.
    # Direct-V4L close-ups: the same reading, done for this path on 2026-09-22 over 67 cube proposals in five recorded
    # console runs, every box confirmed by eye. Real cubes at working distance reach 0.3633; the pad badge, again a
    # verified false positive, starts at 0.4352. The ceiling is the middle of that gap. It was 0.30, which is INSIDE
    # the real-cube range: run console-20260922T194137Z lost its ALIGN decision to a cube box of 0.30024, and the run
    # that passed cleared 0.30 by one pixel. See docs/autonomous-demo/align-failure-diagnosis-2026-09-22.md. The gap
    # is 0.07 wide and was measured on one cube, one pad and one lighting setup, so this separates the populations
    # that were observed and is not a validated margin.
    "box_side_fraction_min": {False: 0.0, True: 0.48},
}
DETECTOR_SCORE_SATURATION = 0.5  # detector scores at or above this count as full
CLIPPED_FACTOR = 0.85  # confidence factor for a silhouette cut by an image border


@dataclass
class CubeObservation:
    detected: bool = False
    confidence: float = 0.0  # [0, 1]; shape_score * detector factor, see confidence_of
    detector_score: float = 0.0
    detector_label: str = ""
    bbox_xyxy: list = None  # [x0, y0, x1, y1] of the refined mask, pixels
    mask: np.ndarray = None  # uint8 0/255, image size
    mask_area_px: int = 0
    center_px: list = None  # mask centroid [x, y]
    shape: dict = field(default_factory=dict)
    clipped: dict = field(default_factory=dict)
    contact_px: list = None  # midpoint of bottom_edge_px
    bottom_edge_px: list = None  # [[x, y], [x, y]] left then right endpoint
    candidates_evaluated: int = 0
    candidates_verified: int = 0
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        polygon = None
        if self.mask is not None:
            contours, _ = cv2.findContours(self.mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                polygon = max(contours, key=cv2.contourArea).reshape(-1, 2).tolist()
        return {
            "detected": self.detected,
            "confidence": round(self.confidence, 3),
            "detector_score": round(self.detector_score, 3),
            "detector_label": self.detector_label,
            "bbox_xyxy": self.bbox_xyxy,
            "mask_polygon_px": polygon,
            "mask_area_px": self.mask_area_px,
            "center_px": self.center_px,
            "shape": self.shape,
            "clipped": self.clipped,
            "contact_px": self.contact_px,
            "bottom_edge_px": self.bottom_edge_px,
            "candidates_evaluated": self.candidates_evaluated,
            "candidates_verified": self.candidates_verified,
            "notes": list(self.notes),
        }


def _box_iou(a, b) -> float:
    inter = max(min(a[2], b[2]) - max(a[0], b[0]), 0.0) * max(min(a[3], b[3]) - max(a[1], b[1]), 0.0)
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1.0)


def _grown(box, fraction, width, height):
    x0, y0, x1, y1 = box
    gx, gy = max(4.0, fraction * (x1 - x0)), max(4.0, fraction * (y1 - y0))
    low = [int(max(0, np.floor(x0 - gx))), int(max(0, np.floor(y0 - gy)))]
    return low + [int(min(width, np.ceil(x1 + gx))), int(min(height, np.ceil(y1 + gy)))]


def refine_mask(image_bgr, box_xyxy):
    """grabCut started from the proposed box -> uint8 0/255 mask of the image
    size holding the one foreground blob that owns the box centre region, or
    None. Pixels outside the (slightly grown) box are sure background."""
    height, width = image_bgr.shape[:2]
    cx0, cy0, cx1, cy1 = _grown(box_xyxy, CONTEXT_GROW, width, height)
    rx0, ry0, rx1, ry1 = _grown(box_xyxy, BOX_GROW, width, height)
    crop = image_bgr[cy0:cy1, cx0:cx1]
    scale = min(1.0, WORK_SIDE_PX / max(crop.shape[:2]))
    small = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else crop
    labels = np.full(small.shape[:2], cv2.GC_BGD, np.uint8)
    sx0, sy0 = int(round((rx0 - cx0) * scale)), int(round((ry0 - cy0) * scale))
    sx1, sy1 = int(round((rx1 - cx0) * scale)), int(round((ry1 - cy0) * scale))
    labels[sy0:sy1, sx0:sx1] = cv2.GC_PR_FGD
    if not (labels == cv2.GC_BGD).any() or sx1 - sx0 < 4 or sy1 - sy0 < 4:
        return None  # no background ring to learn from, or a degenerate box
    cv2.setRNGSeed(GRABCUT_SEED)  # grabCut seeds its colour models by k-means: same image, same mask
    try:
        cv2.grabCut(small, labels, None, np.zeros((1, 65)), np.zeros((1, 65)), GRABCUT_ITERATIONS, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None
    foreground = ((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)).astype(np.float32)
    foreground = cv2.resize(foreground, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR) > 0.5
    foreground = foreground.astype(np.uint8)
    side = min(box_xyxy[2] - box_xyxy[0], box_xyxy[3] - box_xyxy[1])
    kernel_px = max(3, int(round(OPEN_FRACTION * side)) | 1)
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_px, kernel_px)))
    count, components = cv2.connectedComponents(foreground)
    if count < 2:
        return None
    # The blob that owns most of the central half of the box is the object.
    bx0, by0, bx1, by1 = box_xyxy
    qx, qy = (bx1 - bx0) / 4.0, (by1 - by0) / 4.0
    core = components[int(by0 + qy - cy0) : int(by1 - qy - cy0) + 1, int(bx0 + qx - cx0) : int(bx1 - qx - cx0) + 1]
    votes = np.bincount(core.reshape(-1), minlength=count)
    votes[0] = 0
    if votes.max() == 0:
        return None
    blob = (components == int(votes.argmax())).astype(np.uint8)
    contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(blob, contours, -1, 1, cv2.FILLED)  # fill holes (glare, the drilled dots)
    mask = np.zeros((height, width), np.uint8)
    mask[cy0:cy1, cx0:cx1] = blob * 255
    return mask


def shape_features(mask, box_xyxy) -> dict:
    """The silhouette numbers the verification looks at, for one refined mask.
    polygon_px is the convex hull simplified to its corners (pixels)."""
    contour = max(cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    hull = cv2.convexHull(contour)
    polygon = cv2.approxPolyDP(hull, 0.03 * cv2.arcLength(hull, True), True)
    polygon_mask = np.zeros_like(mask)
    cv2.fillPoly(polygon_mask, [polygon], 255)
    union = np.count_nonzero((polygon_mask > 0) | (mask > 0))
    polygon_iou = np.count_nonzero((polygon_mask > 0) & (mask > 0)) / max(union, 1)
    (_, _), (rect_w, rect_h), _ = cv2.minAreaRect(contour)
    x, y, w, h = cv2.boundingRect(contour)
    box_iou = _box_iou([x, y, x + w, y + h], box_xyxy)
    return {
        "solidity": round(area / max(float(cv2.contourArea(hull)), 1.0), 3),
        "polygon_vertices": int(len(polygon)),
        "polygon_iou": round(float(polygon_iou), 3),
        "aspect_ratio": round(float(max(rect_w, rect_h) / max(min(rect_w, rect_h), 1.0)), 3),
        "box_iou": round(float(box_iou), 3),
        "polygon_px": polygon.reshape(-1, 2).tolist(),
    }


def clipping(image_bgr, mask) -> dict:
    """Which image borders cut the mask, and whether a very bright, colourless
    region (a gripper jaw) touches it. occluded_hint is a hint for the caller
    and never takes part in deciding that a cube exists."""
    height, width = mask.shape
    x, y, w, h = cv2.boundingRect(mask)
    flags = {
        "left": bool(x <= BORDER_PX),
        "right": bool(x + w >= width - BORDER_PX),
        "top": bool(y <= BORDER_PX),
        "bottom": bool(y + h >= height - BORDER_PX),
    }
    band = (cv2.dilate(mask, np.ones((13, 13), np.uint8)) > 0) & ~(cv2.dilate(mask, np.ones((5, 5), np.uint8)) > 0)
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB).astype(np.int32)
    jaw_like = (lab[:, :, 0] >= JAW_L_MIN) & (np.abs(lab[:, :, 1] - 128) + np.abs(lab[:, :, 2] - 128) <= JAW_CHROMA_MAX)
    flags["any_border"] = any(flags.values())
    flags["occluded_hint"] = bool(band.any() and jaw_like[band].mean() >= JAW_BAND_FRACTION)
    return flags


def bottom_edge(mask, polygon_px):
    """The visible ground-contact edge of an unclipped cube silhouette in an
    UNROTATED frame: the longest side of the corner polygon whose outward
    normal points down the image (within 45 degrees). A line is fitted to the
    mask contour along that side and the two corners are projected onto it.
    Returns [[x, y], [x, y]] (left endpoint first, pixels) or None. A cube
    seen corner-on has two such sides; the longer one is reported."""
    polygon = np.asarray(polygon_px, np.float32)
    centre = polygon.mean(axis=0)
    best = None
    for index in range(len(polygon)):
        a, b = polygon[index], polygon[(index + 1) % len(polygon)]
        along = b - a
        length = float(np.linalg.norm(along))
        if length < 4.0:
            continue
        normal = np.array([along[1], -along[0]]) / length
        if np.dot(normal, (a + b) / 2.0 - centre) < 0:
            normal = -normal
        if normal[1] >= 0.707 and (best is None or length > best[0]):
            best = (length, a, b)
    if best is None:
        return None
    length, a, b = best
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contour = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    unit = (b - a) / length
    t = (contour - a) @ unit
    distance = np.abs((contour - a) @ np.array([-unit[1], unit[0]]))
    near = contour[(t > 0.15 * length) & (t < 0.85 * length) & (distance < max(3.0, 0.08 * length))]
    if len(near) >= 5:
        vx, vy, x0, y0 = cv2.fitLine(near, cv2.DIST_L2, 0, 0.01, 0.01).reshape(-1)
        direction, origin = np.array([vx, vy]), np.array([x0, y0])
        a, b = origin + np.dot(a - origin, direction) * direction, origin + np.dot(b - origin, direction) * direction
    return sorted([[round(float(a[0]), 1), round(float(a[1]), 1)], [round(float(b[0]), 1), round(float(b[1]), 1)]])


def verify(features: dict, mask_area_px: int, box_xyxy, image_width: int, clipped: dict, rotated: bool) -> list:
    """Reasons why this mask is NOT a verified cube silhouette; empty = verified."""
    reasons = []
    side_fraction = max(box_xyxy[2] - box_xyxy[0], box_xyxy[3] - box_xyxy[1]) / float(image_width)
    if side_fraction > LIMITS["box_side_fraction_max"][rotated]:
        reasons.append(f"box side {side_fraction:.2f} of image width: too large for the cube on this image path")
    if side_fraction < LIMITS["box_side_fraction_min"][rotated]:
        reasons.append(f"box side {side_fraction:.2f} of image width: too small for the cube on this image path")
    if mask_area_px < LIMITS["mask_area_px_min"]:
        reasons.append(f"mask area {mask_area_px} px: too small to verify a shape")
    if features["solidity"] < LIMITS["solidity_min"]:
        reasons.append(f"solidity {features['solidity']}: not convex")
    low, high = LIMITS["vertices"]
    if not low <= features["polygon_vertices"] <= high:
        reasons.append(f"{features['polygon_vertices']} polygon corners: not a cube silhouette")
    if features["polygon_iou"] < LIMITS["polygon_iou_min"]:
        reasons.append(f"polygon_iou {features['polygon_iou']}: sides not straight")
    if features["box_iou"] < LIMITS["box_iou_min"]:
        reasons.append(f"box_iou {features['box_iou']}: mask does not fill the proposed box")
    if not clipped["any_border"] and features["aspect_ratio"] > LIMITS["aspect_max"]:
        reasons.append(f"aspect ratio {features['aspect_ratio']}: too elongated for an unclipped cube")
    return reasons


def confidence_of(detector_score: float, features: dict, cut_by_border: bool) -> float:
    """[0, 1]. shape_score = 0.5 + 0.5 * mean of three margins, each scaled
    from its verification limit (0) to a clean silhouette (1): solidity
    0.90..0.98, polygon_iou 0.80..0.95, box_iou 0.55..0.95. The detector
    factor is 0.5 + 0.5 * min(1, score / DETECTOR_SCORE_SATURATION), and a
    silhouette cut by an image border is scaled by CLIPPED_FACTOR. The
    product is an ordering aid, NOT a calibrated probability."""
    margins = [
        (features["solidity"] - LIMITS["solidity_min"]) / 0.08,
        (features["polygon_iou"] - LIMITS["polygon_iou_min"]) / 0.15,
        (features["box_iou"] - LIMITS["box_iou_min"]) / 0.40,
    ]
    shape_score = 0.5 + 0.5 * float(np.mean(np.clip(margins, 0.0, 1.0)))
    if cut_by_border:
        shape_score *= CLIPPED_FACTOR  # part of the outline is the image border, not the cube
    return shape_score * (0.5 + 0.5 * min(1.0, detector_score / DETECTOR_SCORE_SATURATION))


def _without_duplicates(candidates) -> list:
    kept = []
    for candidate in sorted(candidates, key=lambda c: -c["score"]):
        if all(_box_iou(candidate["box_xyxy"], other["box_xyxy"]) < DUPLICATE_IOU for other in kept):
            kept.append(candidate)
    return kept[:MAX_CANDIDATES]


def recognize_cube(image_bgr, candidates, image_is_rotated_host_frame: bool = False) -> CubeObservation:
    """The best verified cube of one BGR image, or detected=False.

    candidates: zero_shot_detector output for CUBE_LABELS (dicts with label,
    score, box_xyxy in pixels of image_bgr). The best MAX_CANDIDATES distinct
    proposals are refined and verified; the verified one with the highest
    confidence is returned, and candidates_verified counts how many distinct
    cubes verified (several cubes may legitimately be in view).
    image_is_rotated_host_frame: the image comes from the robot host (rotated
    by 90 degrees), so no ground-contact edge is reported and the cube may
    fill most of the frame."""
    observation = CubeObservation()
    width = image_bgr.shape[1]
    shortlist = _without_duplicates(candidates)
    observation.candidates_evaluated = len(shortlist)
    if not shortlist:
        observation.notes.append("no detector proposal: nothing to verify")
        return observation
    verified_masks = []
    for candidate in shortlist:
        box, score = candidate["box_xyxy"], float(candidate["score"])
        tag = f"proposal {candidate['label']!r} {score:.3f} {[int(round(v)) for v in box]}"
        mask = refine_mask(image_bgr, box)
        if mask is None:
            observation.notes.append(f"{tag}: rejected, no foreground separates from the surroundings of the box")
            continue
        area = int(np.count_nonzero(mask))
        shared = [np.count_nonzero((mask > 0) & (seen > 0)) / min(area, np.count_nonzero(seen)) for seen in verified_masks]
        if shared and max(shared) > SAME_CUBE_OVERLAP:
            observation.notes.append(f"{tag}: skipped, overlaps a better verified cube")
            continue
        features = shape_features(mask, box)
        clipped = clipping(image_bgr, mask)
        reasons = verify(features, area, box, width, clipped, image_is_rotated_host_frame)
        if reasons:
            observation.notes.append(f"{tag}: rejected, " + "; ".join(reasons))
            continue
        verified_masks.append(mask)
        confidence = confidence_of(score, features, clipped["any_border"])
        observation.notes.append(f"{tag}: verified, confidence {confidence:.3f}")
        if confidence <= observation.confidence:
            continue
        moments = cv2.moments(mask, binaryImage=True)
        x, y, w, h = cv2.boundingRect(mask)
        observation.detected, observation.confidence = True, confidence
        observation.detector_score, observation.detector_label = score, candidate["label"]
        observation.bbox_xyxy, observation.mask, observation.mask_area_px = [x, y, x + w, y + h], mask, area
        observation.center_px = [round(moments["m10"] / moments["m00"], 1), round(moments["m01"] / moments["m00"], 1)]
        observation.shape, observation.clipped = features, clipped
    observation.candidates_verified = len(verified_masks)
    if not observation.detected:
        return observation
    if image_is_rotated_host_frame:
        observation.notes.append("rotated host frame: no ground-contact edge reported")
    elif observation.clipped["bottom"]:
        observation.notes.append("bottom clipped by the image border: the ground-contact edge is not visible")
    else:
        edge = bottom_edge(observation.mask, observation.shape["polygon_px"])
        if edge is None:
            observation.notes.append("no polygon side faces down the image: no ground-contact edge")
        else:
            observation.bottom_edge_px = edge
            if observation.clipped["left"] or observation.clipped["right"]:
                observation.notes.append("side clipped by the image border: the contact edge midpoint is not the cube's contact center")
                return observation
            observation.contact_px = [round((edge[0][0] + edge[1][0]) / 2.0, 1), round((edge[0][1] + edge[1][1]) / 2.0, 1)]
    return observation
