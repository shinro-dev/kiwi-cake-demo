# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""scene_scan: the views of one scene scan fused into ONE scene in PAD_FRAME, without a camera calibration.

The scene scan (policy_search.scene_scan, --do scan) takes wrist views at the low-pose shape at several pans: lift, elbow
and wrist_flex held, only the pan changed. Every view is perceived by the unchanged frozen perception, whose print-landmark
homography (pad_by_template) maps the view's image to PAD_FRAME when the pad print is in view. This module reads those
records and nothing else; it never writes into a frozen record and never calls the frozen perception.

THE PAN AXIS. A pan turns the camera about the shoulder_pan axis, which is vertical to the pad within what the recorded
views show. The same pixel of two views taken at the same lift, elbow and wrist_flex therefore sees two floor points related
by one rotation about that axis, by the pan change: p_ref = R(pan_view - pan_ref) (p_view - A) + A in PAD_FRAME. With two
direct views (each with its own print-landmark homography) the rotation and the axis A are fitted by a 2D Kabsch over a
pixel grid of the frame (fit_pair), with no intrinsics and no hand-eye calibration. The recorded pair of run m2476137
(pans -9.67 and -29.63, 2026-09-26) gives a rotation of 20.07 deg against a measured pan change of 19.96 deg, the axis at
about (-0.013, -0.415) m and a median residual of 1.15 mm over the full frame grid (test_scene_scan pins these numbers).
The fit is ACCEPTED only when every direct pair's rotation stays within FIT_ROTATION_TOLERANCE_DEG of its measured pan
change and its median residual under FIT_RESIDUAL_MEDIAN_MAX_MM, and at least one pair changes the pan by
FIT_MIN_PAN_CHANGE_DEG or more (a pair closer than that says nothing about where the axis is).

THE PAD FRAME CARRIED BY PAN. A view whose print did not resolve PAD_FRAME gets the reference view's homography carried by
its pan change (transfer_homography), only when the fit is accepted and its lift, elbow and wrist_flex agree with the
reference view's within TRANSFER_JOINT_TOLERANCE_DEG. A position read through a carried frame is INFERRED: it may name
and select a block, and it never becomes a placement anchor (its record keeps no cube_by_template, so classify_block
classes it NO_PAD_FRAME).

THE BLOCKS. Every verified cube of every view (the listing of cube_candidates when it is complete, else the record's own
cube) is one sighting. A sighting with a direct placement has its PAD_FRAME centre; one seen through a carried frame has
its contact point mapped through that frame (a contact point, about half a cube nearer than the centre, marked so);
one with neither has no position. Sightings are clustered in PAD_FRAME within CLUSTER_RADIUS_M. Each block states its
position (the median of its direct sightings, else of its inferred ones), its spread (the largest distance of a sighting
from that position), the views it was seen in, its best view (a direct pad frame first, then unclipped, then with a
contact pixel, then the contact nearest the image centre) and the class the caller's classify(record, pan_deg) gives the
best view's record. With an accepted fit it also states its bearing about the axis as a pan from the saved low pose
(inferred_pan_from_home_deg): the pan that brings the reference contact pixel of the home view onto the block.

Numbers here are EMPIRICAL where they say so: set from the replay of 2026-09-26, to be widened only with data.
"""
import math
from typing import Callable, Optional

import numpy as np

SCHEMA = "scene-scan/1"
HELD_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex")
IMAGE_SIZE_PX = (1280, 720)  # the wrist frame (width, height)
FIT_GRID_STEP_PX = 20  # the pixel grid the pair fit compares two views over: the whole frame
FIT_ROTATION_TOLERANCE_DEG = 0.2  # EMPIRICAL: the recorded pair's fitted rotation was 0.11 deg from its measured pan change
FIT_RESIDUAL_MEDIAN_MAX_MM = 1.5  # EMPIRICAL: the recorded pair's median residual over the frame grid was 1.15 mm
FIT_MIN_PAN_CHANGE_DEG = 5.0  # a pair closer in pan than this does not locate the axis; it is still checked for agreement
TRANSFER_JOINT_TOLERANCE_DEG = 0.5  # lift, elbow and wrist_flex of a view and the reference agree within this, or no carry
CLUSTER_RADIUS_M = 0.025  # one cube edge (25.4 mm): two sightings nearer than this are one block; measured cross-view agreement 0.4 to 9.6 mm
FOOTPRINT_RANGE_LIMIT_M = 0.8  # a view's floor footprint is drawn only where its border maps within this of the pad centre
FOOTPRINT_BORDER_SAMPLES = 16  # points per image edge


# ---------------------------------------------------------------------------------------------------------------------
# The geometry
# ---------------------------------------------------------------------------------------------------------------------
def apply_homography(homography, points) -> tuple:
    """(the mapped points (N, 2), their homogeneous scale (N,)). A point with a scale not above zero maps behind the camera."""
    matrix = np.asarray(homography, dtype=float)
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    mapped = np.c_[points, np.ones(len(points))] @ matrix.T
    scale = mapped[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        return mapped[:, :2] / scale[:, None], scale


def frame_grid(step_px: int = FIT_GRID_STEP_PX, size=IMAGE_SIZE_PX) -> np.ndarray:
    xs, ys = np.meshgrid(np.arange(0, size[0] + 1, step_px), np.arange(0, size[1] + 1, step_px))
    return np.c_[xs.ravel(), ys.ravel()].astype(float)


def rotation_matrix(theta_deg: float) -> np.ndarray:
    theta = math.radians(theta_deg)
    return np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])


def rigid_fit(source, target) -> tuple:
    """The 2D rigid map (rotation in degrees, R, t) that best takes `source` onto `target` (Kabsch, no scale)."""
    source, target = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    source_mean, target_mean = source.mean(0), target.mean(0)
    u, _s, vt = np.linalg.svd((source - source_mean).T @ (target - target_mean))
    sign = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    rotation = vt.T @ np.diag([1.0, sign]) @ u.T
    translation = target_mean - rotation @ source_mean
    return math.degrees(math.atan2(rotation[1, 0], rotation[0, 0])), rotation, translation


def fit_pair(h_view, h_ref, grid=None) -> dict:
    """The rigid map from what `h_view` sees to what `h_ref` sees at the same pixels: {rotation_deg (view to reference),
    axis_pad_m (its fixed point, None when the rotation is too small to place it), residual_median_mm, residual_max_mm,
    points, rotation, translation}."""
    grid = frame_grid() if grid is None else np.asarray(grid, dtype=float)
    seen_view, scale_view = apply_homography(h_view, grid)
    seen_ref, scale_ref = apply_homography(h_ref, grid)
    usable = (scale_view > 0) & (scale_ref > 0) & np.all(np.isfinite(seen_view), axis=1) & np.all(np.isfinite(seen_ref), axis=1)
    if usable.sum() < 3:
        return {"rotation_deg": None, "axis_pad_m": None, "residual_median_mm": None, "residual_max_mm": None, "points": int(usable.sum()), "rotation": None, "translation": None}
    theta, rotation, translation = rigid_fit(seen_view[usable], seen_ref[usable])
    residual = np.linalg.norm(seen_view[usable] @ rotation.T + translation - seen_ref[usable], axis=1) * 1000.0
    axis = None
    if abs(theta) >= 0.5:  # (I - R) is near singular for a small rotation: the axis is then not located by this pair
        axis = [float(v) for v in np.linalg.solve(np.eye(2) - rotation, translation)]
    return {"rotation_deg": float(theta), "axis_pad_m": axis, "residual_median_mm": float(np.median(residual)), "residual_max_mm": float(residual.max()),
            "points": int(usable.sum()), "rotation": rotation, "translation": translation}


def transfer_homography(h_ref, axis_pad_m, pan_change_deg: float) -> np.ndarray:
    """The image-to-PAD_FRAME homography of a view taken pan_change_deg (pan_view - pan_ref) from the reference view, at the
    same lift, elbow and wrist_flex: the reference homography followed by the rotation about the axis by -pan_change_deg."""
    axis = np.asarray(axis_pad_m, dtype=float)
    rotation = rotation_matrix(-float(pan_change_deg))
    carry = np.eye(3)
    carry[:2, :2], carry[:2, 2] = rotation, axis - rotation @ axis
    return carry @ np.asarray(h_ref, dtype=float)


def inferred_pan_offset_deg(axis_pad_m, reference_point_pad_m, block_pad_m) -> float:
    """The pan change that brings the floor point `reference_point_pad_m` (seen at the reference contact pixel) onto
    `block_pad_m`, by the rotation about the axis: the negative of the angle from the one to the other about the axis."""
    axis = np.asarray(axis_pad_m, dtype=float)
    u, v = np.asarray(reference_point_pad_m, dtype=float) - axis, np.asarray(block_pad_m, dtype=float) - axis
    return -math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], float(u @ v)))


def footprint(homography, size=IMAGE_SIZE_PX, limit_m: float = FOOTPRINT_RANGE_LIMIT_M) -> list:
    """The image border mapped into PAD_FRAME, in order around the frame, keeping the points in front of the camera and
    within limit_m of the pad centre: the floor this view covered, as a polygon of [x, y] in metres."""
    width, height = size
    samples = np.linspace(0.0, 1.0, FOOTPRINT_BORDER_SAMPLES, endpoint=False)
    border = [(width * s, 0.0) for s in samples] + [(width, height * s) for s in samples] + [(width * (1 - s), height) for s in samples] + [(0.0, height * (1 - s)) for s in samples]
    mapped, scale = apply_homography(homography, border)
    keep = (scale > 0) & np.all(np.isfinite(mapped), axis=1) & (np.linalg.norm(mapped, axis=1) <= limit_m)
    return [[round(float(x), 4), round(float(y), 4)] for x, y in mapped[keep]]


# ---------------------------------------------------------------------------------------------------------------------
# A view as the scan hands it over
# ---------------------------------------------------------------------------------------------------------------------
def direct_homography(record: Optional[dict]):
    """The view's own image-to-PAD_FRAME homography, or None when its print did not resolve PAD_FRAME."""
    template = (record or {}).get("pad_by_template") or {}
    if not template.get("pad_frame_valid") or not template.get("homography_image_to_pad"):
        return None
    return np.asarray(template["homography_image_to_pad"], dtype=float)


def _held(joints: dict) -> dict:
    return {name: float(joints[name]) for name in HELD_JOINTS if name in (joints or {})}


def joints_agree(a: dict, b: dict, tolerance_deg: float = TRANSFER_JOINT_TOLERANCE_DEG) -> bool:
    held_a, held_b = _held(a), _held(b)
    return len(held_a) == len(HELD_JOINTS) == len(held_b) and all(abs(held_a[name] - held_b[name]) <= tolerance_deg for name in HELD_JOINTS)


def fit_pan_axis(views: list) -> dict:
    """The pan-axis fit of one scan (the module docstring). `views` are dicts with name, pan_deg, joints and record.
    Returns {accepted, reasons, reference, axis_pad_m, pairs}: each pair states the view, its measured pan change from the
    reference, the fitted rotation, the rotation error, the residuals and whether it was used to place the axis."""
    direct = [view for view in views if direct_homography(view.get("record")) is not None]
    if len(direct) < 2:
        return {"accepted": False, "reasons": ["fewer_than_two_direct_views"], "reference": direct[0]["name"] if direct else None, "axis_pad_m": None, "pairs": []}
    inliers = [((view["record"].get("pad_by_template") or {}).get("landmark_inliers") or 0) for view in direct]
    reference = direct[int(np.argmax(inliers))]
    h_ref, pairs, rows, targets, reasons = direct_homography(reference["record"]), [], [], [], []
    for view in direct:
        if view is reference:
            continue
        change = float(view["pan_deg"]) - float(reference["pan_deg"])
        pair = {"view": view["name"], "pan_change_deg": round(change, 3)}
        if not joints_agree(view.get("joints") or {}, reference.get("joints") or {}):
            pairs.append({**pair, "used": False, "reason": "joints_disagree"})
            continue
        fitted = fit_pair(direct_homography(view["record"]), h_ref)
        if fitted["rotation_deg"] is None:
            pairs.append({**pair, "used": False, "reason": "no_common_floor"})
            continue
        error = abs(fitted["rotation_deg"] - change)
        passed = error <= FIT_ROTATION_TOLERANCE_DEG and fitted["residual_median_mm"] <= FIT_RESIDUAL_MEDIAN_MAX_MM
        locates = abs(change) >= FIT_MIN_PAN_CHANGE_DEG
        pairs.append({**pair, "used": True, "locates_axis": locates, "rotation_deg": round(fitted["rotation_deg"], 3), "rotation_error_deg": round(error, 3),
                      "residual_median_mm": round(fitted["residual_median_mm"], 2), "residual_max_mm": round(fitted["residual_max_mm"], 2), "passed": passed})
        if not passed:
            reasons.append(f"pair_{view['name']}_outside_thresholds")
        if locates:
            rows.append(np.eye(2) - fitted["rotation"])
            targets.append(fitted["translation"])
    if not rows:
        reasons.append("no_pair_changes_the_pan_enough")
    axis = None
    if rows:
        axis = [round(float(v), 5) for v in np.linalg.lstsq(np.vstack(rows), np.concatenate(targets), rcond=None)[0]]
    return {"accepted": not reasons, "reasons": reasons, "reference": reference["name"], "axis_pad_m": axis, "pairs": pairs}


# ---------------------------------------------------------------------------------------------------------------------
# The fused scene
# ---------------------------------------------------------------------------------------------------------------------
def _clipped(cube: dict) -> bool:
    return any(bool(flag) for flag in ((cube or {}).get("clipped") or {}).values())


def _sightings(view: dict, homography, source: str) -> list:
    """Every verified cube of one view with its position: (direct centre), (inferred contact point), or None."""
    out = []
    entries = view.get("entries")
    if not entries and view.get("record"):
        entries = [{"cube": view["record"].get("cube"), "cube_by_template": view["record"].get("cube_by_template")}]
    for index, entry in enumerate(entries or []):
        cube = entry.get("cube") or {}
        if not cube.get("detected"):
            continue
        centre = (entry.get("cube_by_template") or {}).get("cube_center_pad_m")
        position, kind = None, None
        if centre and source == "direct":
            position, kind = [float(centre[0]), float(centre[1])], "direct"
        elif homography is not None and cube.get("contact_px"):
            mapped, scale = apply_homography(homography, [cube["contact_px"]])
            if scale[0] > 0 and np.all(np.isfinite(mapped[0])):
                position, kind = [float(mapped[0][0]), float(mapped[0][1])], "inferred_contact_point"
        contact = cube.get("contact_px")
        centre_px = (IMAGE_SIZE_PX[0] / 2.0, IMAGE_SIZE_PX[1] / 2.0)
        out.append({"view": view["name"], "observation_id": view.get("observation_id"), "pan_deg": float(view["pan_deg"]), "position": position, "kind": kind,
                    "entry": entry, "entry_index": index, "clipped": _clipped(cube), "has_contact": bool(contact),
                    "centre_distance_px": math.hypot(contact[0] - centre_px[0], contact[1] - centre_px[1]) if contact else math.inf})
    return out


def _best(sightings: list) -> dict:
    return min(sightings, key=lambda s: (s["kind"] != "direct", s["clipped"], not s["has_contact"], s["centre_distance_px"]))


def _median(points) -> list:
    return [float(v) for v in np.median(np.asarray(points, dtype=float), axis=0)]


def cluster(sightings: list, radius_m: float = CLUSTER_RADIUS_M) -> list:
    """Greedy clustering in PAD_FRAME, in the order the sightings came: each joins the nearest block within radius_m of
    that block's position (its direct sightings' median when it has one), or starts a block. Sightings without a position
    are not clustered: each is returned as a block of its own, with no position."""
    blocks = []

    def position_of(block):
        direct = [s["position"] for s in block if s["kind"] == "direct"]
        return _median(direct or [s["position"] for s in block])

    for sighting in sightings:
        if sighting["position"] is None:
            continue
        nearest, distance = None, math.inf
        for block in blocks:
            gap = math.dist(position_of(block), sighting["position"])
            if gap < distance:
                nearest, distance = block, gap
        if nearest is not None and distance <= radius_m:
            nearest.append(sighting)
        else:
            blocks.append([sighting])
    return blocks + [[sighting] for sighting in sightings if sighting["position"] is None]


def fuse(views: list, classify: Callable[[dict, float], dict], home_pan_deg: float, reference_contact_px=(553.0, 545.0), return_pan_bound_deg: float = 25.0) -> dict:
    """ONE scene from the views of one scan (the module docstring). `views`: dicts with name, pan_deg, joints (measured),
    observation_id, record (the view's perception record) and optionally entries (every verified cube of the view, each
    {cube, cube_by_template}) and listing (how that list was made). `classify(record, pan_deg)` is the controller's class of
    one sighting (policy_search.classify_block). Pure: reads the records, writes nothing."""
    fit = fit_pan_axis(views)
    by_name = {view["name"]: view for view in views}
    reference = by_name.get(fit["reference"]) if fit["reference"] else None
    view_rows, sightings, frames = [], [], {}
    for view in views:
        record = view.get("record") or {}
        homography, source = direct_homography(record), "direct"
        if homography is None:
            source = "none"
            if fit["accepted"] and reference is not None and joints_agree(view.get("joints") or {}, reference.get("joints") or {}):
                homography = transfer_homography(direct_homography(reference["record"]), fit["axis_pad_m"], float(view["pan_deg"]) - float(reference["pan_deg"]))
                source = "pan_transfer"
        frames[view["name"]] = homography
        template = record.get("pad_by_template") or {}
        seen = _sightings(view, homography, source)
        sightings.extend(seen)
        view_rows.append({"name": view["name"], "observation_id": view.get("observation_id"), "pan_deg": round(float(view["pan_deg"]), 3),
                          "pan_from_home_deg": round(float(view["pan_deg"]) - float(home_pan_deg), 3), "held_joints_deg": {k: round(v, 3) for k, v in _held(view.get("joints") or {}).items()},
                          "pad_frame_source": source, "landmark_inliers": template.get("landmark_inliers"), "reprojection_rms_px": template.get("reprojection_rms_px"),
                          "blocks_seen": len(seen), "listing": view.get("listing"), "footprint_pad_m": footprint(homography) if homography is not None else []})
    home_view = min((view for view in views if frames.get(view["name"]) is not None), key=lambda view: abs(float(view["pan_deg"]) - float(home_pan_deg)), default=None)
    reference_point = None
    if home_view is not None:
        mapped, scale = apply_homography(frames[home_view["name"]], [reference_contact_px])
        reference_point = [float(mapped[0][0]), float(mapped[0][1])] if scale[0] > 0 else None
    blocks = []
    for index, members in enumerate(cluster(sightings)):
        best = _best(members)
        view = by_name[best["view"]]
        record = {**(view.get("record") or {}), "cube": best["entry"].get("cube"), "cube_by_template": best["entry"].get("cube_by_template") if best["kind"] == "direct" else None}
        classified = classify(record, best["pan_deg"])
        direct = [s["position"] for s in members if s["kind"] == "direct"]
        positioned = [s["position"] for s in members if s["position"] is not None]
        position = _median(direct or positioned) if positioned else None
        block = {"id": f"block-{index + 1}", "position_pad_m": [round(v, 4) for v in position] if position else None,
                 "position_source": "direct" if direct else ("inferred_contact_point" if positioned else "none"),
                 "spread_mm": round(max(math.dist(position, p) for p in positioned) * 1000.0, 1) if position else None,
                 "views_seen": sorted({s["view"] for s in members}, key=[v["name"] for v in views].index), "observation_ids": sorted({s["observation_id"] for s in members if s["observation_id"]}),
                 "best_view": best["view"], "best_entry_index": best["entry_index"], "best_observation_id": best["observation_id"], "best_pan_deg": round(best["pan_deg"], 3),
                 "class": classified["class"], "reason_code": classified["reason_code"], "values": classified["values"],
                 "inferred_pan_from_home_deg": None, "outside_pan_band_inferred": None}
        if fit["accepted"] and position is not None and reference_point is not None:
            offset = inferred_pan_offset_deg(fit["axis_pad_m"], reference_point, position) + (float(home_view["pan_deg"]) - float(home_pan_deg))
            block["inferred_pan_from_home_deg"] = round(offset, 2)
            block["outside_pan_band_inferred"] = abs(offset) > return_pan_bound_deg
            block["range_from_axis_mm"] = round(math.dist(fit["axis_pad_m"], position) * 1000.0, 1)
        blocks.append(block)
    pad = None
    if reference is not None:
        template = reference["record"].get("pad_by_template") or {}
        pad = {"reference_view": reference["name"], "observation_id": reference.get("observation_id"), "landmark_inliers": template.get("landmark_inliers"),
               "reprojection_rms_px": template.get("reprojection_rms_px")}
    return {"schema": SCHEMA, "home_pan_deg": round(float(home_pan_deg), 3), "views": view_rows, "fit": fit, "pad": pad,
            "home_reference_point_pad_m": [round(v, 4) for v in reference_point] if reference_point else None, "blocks": blocks,
            "thresholds": {"fit_rotation_tolerance_deg": FIT_ROTATION_TOLERANCE_DEG, "fit_residual_median_max_mm": FIT_RESIDUAL_MEDIAN_MAX_MM,
                           "fit_min_pan_change_deg": FIT_MIN_PAN_CHANGE_DEG, "transfer_joint_tolerance_deg": TRANSFER_JOINT_TOLERANCE_DEG, "cluster_radius_m": CLUSTER_RADIUS_M}}
