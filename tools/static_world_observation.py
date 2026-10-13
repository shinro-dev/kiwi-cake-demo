#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Read-only STATIC WORLD OBSERVATION from saved FRONT and/or WRIST frames:
recognize the collection pad and the cube, establish PAD_FRAME from the known
pad size, and place the cube metrically in PAD_FRAME when both are visible in
the same frame. No camera, robot or network access; nothing moves.

Metric source: the pad. A frame's image -> PAD_FRAME homography comes from
fitting the known 0.415 x 0.295 m pad outline (pad_localizer), so every
position is relative to the pad, in meters, on the support plane. The cube
position is its ground-contact edge mapped through that homography; the cube
center is inferred 12.7 mm behind that edge along the viewing direction
(exact for a face-on cube, up to about 5 mm off for a cube seen corner-on).

The focal length is an INPUT of the pad fit, not a calibration result. Each
frame is therefore solved at the nominal focal length and at -15 % / +15 %,
and the spread is reported as part of the uncertainty. What this tool does
NOT establish: BASE_FRAME coordinates (no camera-to-base transform is used),
lens distortion effects, or accuracy against tape-measured ground truth.

PAD_FRAME: origin at the pad center, x along the long edge in the reading
direction of the print, y toward the top (text) edge, z up, meters.

Usage:
    python3 tools/static_world_observation.py --wrist <png> --front <png> --out-dir <ignored dir>
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))

from cube_recognizer import recognize_cube  # noqa: E402
from observation_store import ensure_local_only  # noqa: E402
from pad_localizer import localize_pad  # noqa: E402
from pad_template_localizer import localize_pad_by_template  # noqa: E402
from zero_shot_detector import CUBE_LABELS, PAD_LABELS, ZeroShotDetector  # noqa: E402

PAD_SIZE_M = (0.415, 0.295)  # recorded project value (day-1 measurement records, 2026-09-15)
CUBE_EDGE_M = 0.0254
# INFERRED from existing evidence only: cube apparent size at tape-measured day-1 positions gives
# about 1130-1160 px, a pinhole fit to the day-1 tape-measured floor pairs gives about 1260 px.
NOMINAL_FOCAL_PX = {"front": 1200.0, "wrist": 1200.0}
FOCAL_SWEEP = (0.85, 1.0, 1.15)
TEMPLATE_DIR = ROOT / ".local-dev" / "calibration" / "pad-template"  # local, ignored: built by tools/build_pad_template.py
CONTACT_CONFIDENCE_FLOOR = 0.5  # a weak cube mask still yields a contact edge; real contact-bearing frames score 0.86-0.97


def to_pad(homography, point_px):
    vector = np.asarray(homography, dtype=float) @ np.array([point_px[0], point_px[1], 1.0])
    return vector[:2] / vector[2]


def camera_ground_point(pad):
    """The point of the pad plane directly below the camera, PAD_FRAME meters."""
    rotation, translation = np.asarray(pad.R_cam_pad, dtype=float), np.asarray(pad.t_cam_pad_m, dtype=float).reshape(3)
    return (-rotation.T @ translation)[:2]


def cube_in_pad_frame(pad, cube):
    contact = to_pad(pad.homography_image_to_pad, cube.contact_px)
    away = contact - camera_ground_point(pad)
    distance = float(np.linalg.norm(away))
    center = contact + (CUBE_EDGE_M / 2) * away / distance
    result = {
        "contact_edge_midpoint_pad_m": [float(v) for v in contact],
        "cube_center_pad_m": [float(v) for v in center],
        "range_from_camera_ground_point_m": distance,
        "inside_pad_outline": bool(abs(center[0]) <= PAD_SIZE_M[0] / 2 and abs(center[1]) <= PAD_SIZE_M[1] / 2),
    }
    if cube.bottom_edge_px is not None:
        ends = [to_pad(pad.homography_image_to_pad, p) for p in cube.bottom_edge_px]
        result["contact_edge_length_m"] = float(np.linalg.norm(ends[0] - ends[1]))  # 0.0254 face-on, up to 0.0359 corner-on
    return result


def cube_from_top_edge(pad, cube, focal_px, pad_thickness_m):
    """Fallback when the ground-contact edge is clipped (the grazing front view): the cube's far TOP
    edge is a point at the known cube height above the floor. Its viewing ray is intersected with the
    plane z = cube edge - pad thickness (PAD_FRAME z = 0 is the pad's top surface; the cube stands on
    the floor). COARSE: at a grazing angle the range scales with the assumed height difference."""
    rows, columns = np.nonzero(cube.mask)
    top = rows.min()
    u, v = float(columns[rows <= top + 2].mean()), float(top)
    height, width = cube.mask.shape[:2]
    rotation, translation = np.asarray(pad.R_cam_pad, dtype=float), np.asarray(pad.t_cam_pad_m, dtype=float).reshape(3)
    camera = -rotation.T @ translation
    ray = rotation.T @ np.array([(u - width / 2) / focal_px, (v - height / 2) / focal_px, 1.0])
    point = camera + (CUBE_EDGE_M - pad_thickness_m - camera[2]) / ray[2] * ray
    toward_camera = (camera[:2] - point[:2]) / np.linalg.norm(camera[:2] - point[:2])
    return [float(value) for value in point[:2] + (CUBE_EDGE_M / 2) * toward_camera]


def cube_by_template(template, cube):
    """Cube position through the PRINT-LANDMARK homography (no focal length, no camera pose). The center is taken
    12.7 mm behind the contact edge along the PAD_FRAME direction of 'up the image' at the contact point."""
    contact = to_pad(template.homography_image_to_pad, cube.contact_px)
    above = to_pad(template.homography_image_to_pad, (cube.contact_px[0], cube.contact_px[1] - 10.0))
    away = (above - contact) / np.linalg.norm(above - contact)
    center = contact + (CUBE_EDGE_M / 2) * away
    return {"contact_edge_midpoint_pad_m": [float(v) for v in contact], "cube_center_pad_m": [float(v) for v in center],
            "inside_pad_outline": bool(abs(center[0]) <= PAD_SIZE_M[0] / 2 and abs(center[1]) <= PAD_SIZE_M[1] / 2)}


def observe_frame(role, image, detector, focal_px, template_dir=None):
    # template_dir: None = TEMPLATE_DIR, the legacy place next to this checkout. A caller that was given an explicit runtime
    # configuration (run_stage1_scan.runtime_paths) passes the configured directory instead; nothing else changes.
    template_dir = TEMPLATE_DIR if template_dir is None else Path(template_dir)
    detections = detector.detect(image, list(CUBE_LABELS) + list(PAD_LABELS))
    pad_candidates = sorted((d for d in detections if d["label"] in PAD_LABELS), key=lambda d: -d["score"])
    cube = recognize_cube(image, [d for d in detections if d["label"] in CUBE_LABELS])
    record = {"camera": role, "image_size_px": [image.shape[1], image.shape[0]], "cube": cube.to_dict(), "pad": None, "focal_sweep": []}
    if not pad_candidates:
        record["pad_note"] = "no pad candidate from the detector"
        return record, None, cube
    box = pad_candidates[0]["box_xyxy"]
    record["pad_detector_score"] = pad_candidates[0]["score"]
    nominal = None
    for factor in FOCAL_SWEEP:
        pad = localize_pad(image, box, focal_px * factor, pad_size_m=PAD_SIZE_M)
        entry = {"focal_px": focal_px * factor, "pad_detected": pad.detected, "confidence": pad.confidence, "boundary_rms_px": pad.boundary_rms_px, "camera_height_m": pad.camera_height_m, "view_elevation_deg": pad.view_elevation_deg}
        if pad.detected and cube.detected and cube.contact_px is not None and cube.confidence >= CONTACT_CONFIDENCE_FLOOR:
            entry["cube"] = cube_in_pad_frame(pad, cube)
        elif pad.detected and cube.detected and cube.confidence >= CONTACT_CONFIDENCE_FLOOR and not cube.clipped["top"]:
            # pad thickness is not recorded: solved for 0 and 4 mm to show what it costs
            entry["cube_center_from_top_edge_pad_m"] = {f"pad_thickness_{int(mm)}mm": cube_from_top_edge(pad, cube, focal_px * factor, mm / 1000.0) for mm in (0.0, 4.0)}
        record["focal_sweep"].append(entry)
        if factor == 1.0:
            nominal = pad
            record["pad"] = pad.to_dict()
    # The pad is a known TEXTURED planar landmark: its print is matched whether or not the outline fit was accepted,
    # so a partial view with too few hem edges still yields PAD_FRAME when enough print is in view.
    if template_dir.is_dir():
        template = localize_pad_by_template(image, box, str(template_dir))
        record["pad_by_template"] = template.to_dict()
        if template.pad_frame_valid and cube.detected and cube.contact_px is not None and cube.confidence >= CONTACT_CONFIDENCE_FLOOR:
            record["cube_by_template"] = cube_by_template(template, cube)
    else:
        record["pad_by_template"] = None
    return record, nominal, cube


def draw_overlay(image, pad, cube, path):
    view = image.copy()
    if pad is not None and pad.detected and pad.image_polygon:
        cv2.polylines(view, [np.array(pad.image_polygon, dtype=np.int32)], True, (0, 255, 0), 2)
        to_image = np.asarray(pad.homography_pad_to_image, dtype=float)
        for end, color in (((0.1, 0.0), (0, 0, 255)), ((0.0, 0.1), (255, 128, 0))):  # PAD_FRAME x red, y blue, 10 cm each
            points = [to_image @ np.array([x, y, 1.0]) for x, y in ((0.0, 0.0), end)]
            points = [tuple(int(v) for v in p[:2] / p[2]) for p in points]
            cv2.arrowedLine(view, points[0], points[1], color, 3)
    if cube.detected:
        x0, y0, x1, y1 = (int(v) for v in cube.bbox_xyxy)
        cv2.rectangle(view, (x0, y0), (x1, y1), (0, 255, 255), 2)
        if cube.contact_px is not None:
            cv2.circle(view, tuple(int(v) for v in cube.contact_px), 6, (255, 0, 255), -1)
    cv2.imwrite(str(path), view)


def summarize(record):
    lines = [f"{record['camera'].upper()}:"]
    pad, cube = record["pad"], record["cube"]
    if pad is None or not pad["detected"]:
        lines.append(f"  pad detected: NO ({record.get('pad_note', 'no usable outline fit')})")
    else:
        lines.append(f"  pad detected: YES  confidence {pad['confidence']:.2f}  visible fraction {pad['visible_fraction']:.2f}  clipped {pad['clipped_sides']}  orientation resolved {pad['orientation_resolved']}")
        lines.append(f"  pad fit: boundary rms {pad['boundary_rms_px']:.1f} px, camera {pad['camera_height_m']:.3f} m above the pad plane, view elevation {pad['view_elevation_deg']:.0f} deg")
    template = record.get("pad_by_template")
    if template is None:
        lines.append("  pad by print landmarks: not run (no local pad template)")
    else:
        edges = next((n for n in (pad or {}).get("notes", []) if n.startswith("supported edges")), "supported edges: n/a").split(";")[0]
        lines.append(f"  pad identity (print landmarks): {'YES' if template['pad_identity'] else 'NO'}  inliers {template['landmark_inliers']}  outer {edges}")
        lines.append(f"  PAD_FRAME by print landmarks: {'YES' if template['pad_frame_valid'] else 'NO'}  ({template['reason']})")
        if "cube_by_template" in record:
            c = record["cube_by_template"]["cube_center_pad_m"]
            lines.append(f"  CUBE in PAD_FRAME by print landmarks: x {c[0]:+.3f} m, y {c[1]:+.3f} m  (inside pad outline: {record['cube_by_template']['inside_pad_outline']})")
    lines.append(f"  cube detected: {'YES' if cube['detected'] else 'NO'}  confidence {cube['confidence']:.2f}  clipped {cube['clipped']}  contact {'available' if cube['contact_px'] else 'unavailable'}")
    solved = [e for e in record["focal_sweep"] if "cube" in e]
    if solved:
        centers = np.array([e["cube"]["cube_center_pad_m"] for e in solved])
        nominal = next((e["cube"] for e in solved if abs(e["focal_px"] - NOMINAL_FOCAL_PX[record["camera"]]) < 1e-6), {})  # the sweep may solve only off the nominal focal length
        if nominal:
            lines.append(f"  CUBE in PAD_FRAME: x {nominal['cube_center_pad_m'][0]:+.3f} m, y {nominal['cube_center_pad_m'][1]:+.3f} m  (inside pad outline: {nominal['inside_pad_outline']})")
        else:
            lines.append(f"  CUBE in PAD_FRAME: not solved at the nominal focal length (only at {[round(e['focal_px']) for e in solved]} px)")
        lines.append(f"  focal sweep -15/0/+15 %: x {centers[:, 0].min():+.3f}..{centers[:, 0].max():+.3f}, y {centers[:, 1].min():+.3f}..{centers[:, 1].max():+.3f} m")
        if "contact_edge_length_m" in nominal:
            lines.append(f"  size cue: contact edge measures {nominal['contact_edge_length_m'] * 1000:.1f} mm through the pad homography (25.4 face-on .. 35.9 corner-on)")
    elif any("cube_center_from_top_edge_pad_m" in e for e in record["focal_sweep"]):
        lines.append("  CUBE in PAD_FRAME, COARSE (contact edge clipped; far top edge at the known 25.4 mm cube height):")
        for e in record["focal_sweep"]:
            if "cube_center_from_top_edge_pad_m" in e:
                lines.append(f"    focal {e['focal_px']:.0f} px: " + "; ".join(f"{k.replace('_', ' ')} -> x {v[0]:+.3f}, y {v[1]:+.3f} m" for k, v in e["cube_center_from_top_edge_pad_m"].items()))
    else:
        lines.append("  CUBE in PAD_FRAME: not available from this frame (needs a pad fit AND a usable cube edge)")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(prog="static_world_observation.py", description="Read-only pad + cube observation from saved frames.")
    parser.add_argument("--front", type=Path)
    parser.add_argument("--wrist", type=Path)
    parser.add_argument("--front-focal-px", type=float, default=NOMINAL_FOCAL_PX["front"])
    parser.add_argument("--wrist-focal-px", type=float, default=NOMINAL_FOCAL_PX["wrist"])
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--template-dir", type=Path, default=None, help="the pad template directory of an explicit runtime configuration (default: the legacy place next to this checkout)")
    args = parser.parse_args(argv)
    if args.front is None and args.wrist is None:
        parser.error("give --front and/or --wrist")
    ensure_local_only(str(args.out_dir.resolve()))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    NOMINAL_FOCAL_PX.update({"front": args.front_focal_px, "wrist": args.wrist_focal_px})

    detector = ZeroShotDetector()
    records, report = {}, ["STATIC WORLD OBSERVATION", f"pad physical dimensions used: {PAD_SIZE_M[0]} x {PAD_SIZE_M[1]} m (recorded day-1 value); cube edge {CUBE_EDGE_M} m", ""]
    for role, path in (("front", args.front), ("wrist", args.wrist)):
        if path is None:
            continue
        image = cv2.imread(str(path))
        if image is None:
            sys.exit(f"static_world_observation: cannot read {path}")
        record, pad, cube = observe_frame(role, image, detector, NOMINAL_FOCAL_PX[role], template_dir=args.template_dir)
        record["source_image"] = path.name
        records[role] = record
        draw_overlay(image, pad, cube, args.out_dir / f"{role}-overlay.png")
        report += summarize(record) + [""]

    centers = {}
    for role, record in records.items():
        for e in record["focal_sweep"]:
            if abs(e["focal_px"] - NOMINAL_FOCAL_PX[role]) < 1e-6:
                if "cube" in e:
                    centers[role] = np.array(e["cube"]["cube_center_pad_m"])
                elif "cube_center_from_top_edge_pad_m" in e:  # coarse fallback, mean of the two thickness assumptions
                    centers[role] = np.mean(list(e["cube_center_from_top_edge_pad_m"].values()), axis=0)
    if len(centers) == 2:
        gap = float(np.linalg.norm(centers["front"] - centers["wrist"]))
        records["cross_view_cube_disagreement_m"] = gap
        report.append(f"CROSS-VIEW: front and wrist place the cube {gap * 1000:.0f} mm apart in PAD_FRAME")
    else:
        report.append(f"CROSS-VIEW: metric cube position available from {sorted(centers) or 'no camera'} only")
    report.append("BASE MOTION: ZERO.  ARM MOTION: ZERO.")

    (args.out_dir / "static-world-observation.json").write_text(json.dumps(records, indent=2, default=float))
    (args.out_dir / "static-world-observation.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
