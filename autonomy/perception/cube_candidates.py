#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""cube_candidates: EVERY verified cube of one wrist frame, each placed in
PAD_FRAME, without changing the frozen per-frame perception.

Why it exists: the frozen perception (static_world_observation.observe_frame,
called by run_stage1_scan.perceive) keeps ONE cube, the verified proposal with
the highest confidence; any other verified cube is only counted
(candidates_verified). A two-cube mission has to tell the cube it already
placed from the one it still has to pick up, so it needs every verified cube of
a frame with its PAD_FRAME position. Those sources and perceive are pinned by
hash (the robot benchmark proved their equivalence at exactly those bytes), so
nothing here edits them or calls them differently: this runs AFTER the
unchanged perceive, beside it, and reads what it wrote.

How:
  1. CubeDetectionCache wraps the detector the controller already hands to
     perceive. It forwards every call unchanged and returns the detector's own
     answer; it only keeps a copy of the cube-label proposals, keyed by a digest
     of the frame it was given. The enumeration therefore never runs OWLv2
     again (25 s a frame on the robot, handoff section 12.3).
  2. enumerate_verified_cubes calls the UNCHANGED recognize_cube repeatedly on
     those proposals. Pass 1 gets all of them, exactly as observe_frame did.
     Each later pass drops the proposals whose box contains a previous winner's
     center_px, for at most MAX_PASSES passes. Each cube is placed in PAD_FRAME
     by the UNCHANGED cube_by_template with the homography of that same frame's
     pad_by_template record, under the same conditions observe_frame applies.
     The pad is never fitted again.
  3. Pass 1 must reproduce the record's own cube and cube_by_template. The
     cube is compared whole and exactly, after the same JSON round trip the
     record went through; the placement must have the same keys and inside
     flag and every coordinate within PLACEMENT_REPRODUCTION_TOLERANCE_M (see
     there why that is not exact). If pass 1 does not reproduce the record, the
     proposals are not the ones the record was made from (or the recognizer
     does not reproduce here) and the enumeration REFUSES: it lists no cube at
     all, so no caller can act on a list that disagrees with the frozen record.
     When it does, the listed pass-1 entry IS the record's cube and placement.

Output: a separate sidecar <obs>-candidates.json next to the frozen
<obs>.json. The frozen record is only read here, never written.

What this does NOT establish:
  - identity of a cube across frames or runs (target binding does that, with
    its own radii, in PAD_FRAME);
  - a complete list: the detector proposes what it proposes, recognize_cube
    shortlists at most MAX_CANDIDATES per pass, and a list that stopped at the
    pass limit with proposals left says complete false, never "no more cubes";
  - that two entries are two physical cubes beyond the recognizer's own rule:
    a later winner whose mask shares more than SAME_CUBE_OVERLAP of itself with
    an earlier winner's mask is that cube again (the test recognize_cube itself
    applies within one pass) and is not listed a second time;
  - reproduction on every machine. Measured on the eight retained benchmark
    frames: on the machine that ran perceive, pass 1 reproduces the cube and
    the placement bit for bit (grabCut is seeded); run on the laptop from the
    ROBOT's own proposals, the robot's cube entries reproduced bit for bit and
    its placements within 1e-16 m. The opposite direction was not measured,
    and a stored rounded digit that flips on another machine refuses.

Frames and units as in cube_recognizer (pixels of the image as passed in) and
static_world_observation (PAD_FRAME meters). Needs cv2 and numpy; no model.
recognize_cube reseeds the global OpenCV RNG, as it does inside perceive.
"""
import copy
import hashlib
import json
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np

_HERE = Path(__file__).resolve().parent
for _path in (str(_HERE), str(_HERE.parents[1] / "tools")):
    if _path not in sys.path:  # the controller has both already; only a bare import needs them
        sys.path.insert(0, _path)

from cube_recognizer import SAME_CUBE_OVERLAP, recognize_cube  # noqa: E402
from zero_shot_detector import CUBE_LABELS  # noqa: E402
import static_world_observation as swo  # noqa: E402

MAX_PASSES = 3  # design section 3.1: the mission stages two cubes; a third pass shows whether a third is in view
MAX_CACHED_FRAMES = 64  # one recorded cycle perceives about 25 frames; an evicted frame refuses, it is never re-detected
# The placement is plain float arithmetic on the record's own homography and contact pixel, and a different machine may
# round it differently in the last bit: the robot's records, recomputed on the laptop from the robot's own proposals and
# homographies, came out up to 9.2e-17 m away on three of the four frames with PAD_FRAME. Demanding bit equality there
# would refuse by where perception happens to run, which is not decided (design D7). A nanometre is floating point, not
# geometry: five orders below the 0.16 mm the benchmark accepted between machines, and far below any matching radius.
PLACEMENT_REPRODUCTION_TOLERANCE_M = 1e-9
SIDECAR_SUFFIX = "-candidates.json"
SCHEMA = "cube-candidates/1"

REFUSED_NO_CACHED_DETECTIONS = "no_cached_detections"
REFUSED_RECORD_UNREADABLE = "record_unreadable"
REFUSED_IMAGE_SIZE = "image_size_differs_from_record"
REFUSED_PASS1_CUBE = "pass1_cube_differs_from_record"
REFUSED_PASS1_PLACEMENT = "pass1_placement_differs_from_record"


def frame_digest(image) -> str:
    """sha256 of the DECODED frame the detector was given: shape, dtype and pixel bytes. It is not the digest of the
    stored PNG file that the cycle's events name (content_digest); the wrapper only ever sees the decoded array."""
    array = np.ascontiguousarray(image)
    hasher = hashlib.sha256(f"{array.shape}|{array.dtype.str}|".encode())
    hasher.update(array.tobytes())
    return "sha256:" + hasher.hexdigest()


class CubeDetectionCache:
    """The detector the controller hands to perceive, with a memory of its cube-label proposals.

    detect() passes its arguments to the wrapped detector untouched and returns that detector's own list object, so
    perceive computes exactly what it computes without the wrapper. A call is remembered only when its labels include
    every cube label (observe_frame's call does): a call for other labels says nothing about the cube and must not be
    read as "no cube proposed". The remembered list is a deep copy, filtered exactly as observe_frame filters it before
    recognize_cube, in the detector's order. Any other attribute (the lazy _model the cycle's events look at among them)
    is the wrapped detector's own."""

    def __init__(self, detector, max_frames: int = MAX_CACHED_FRAMES):
        self._detector = detector
        self._max_frames = int(max_frames)
        self._cube_detections = OrderedDict()

    def detect(self, image_bgr, labels, *args, **kwargs):
        detections = self._detector.detect(image_bgr, labels, *args, **kwargs)
        asked = list(labels)
        if all(label in asked for label in CUBE_LABELS):
            digest = frame_digest(image_bgr)
            self._cube_detections[digest] = copy.deepcopy([d for d in detections if d["label"] in CUBE_LABELS])
            self._cube_detections.move_to_end(digest)  # the latest call on a frame is the one its record came from
            while len(self._cube_detections) > self._max_frames:
                self._cube_detections.popitem(last=False)
        return detections

    def cube_detections(self, digest: str):
        """A copy of the cube-label proposals of the frame with this digest, or None when that frame was not seen."""
        found = self._cube_detections.get(digest)
        return None if found is None else copy.deepcopy(found)

    def __getattr__(self, name):
        detector = self.__dict__.get("_detector")
        if detector is None:
            raise AttributeError(name)
        return getattr(detector, name)


@dataclass
class CandidateEnumeration:
    refused: Optional[str] = None  # a REFUSED_* code; None means the cube list below may be used
    detail: str = ""
    pass1_matches_record: bool = False
    pad_frame_valid: bool = False  # the frame's own pad_by_template fixed PAD_FRAME
    complete: bool = False  # True only when the passes ran out of proposals or of verified cubes, never at the pass limit
    cubes: list = field(default_factory=list)  # {"pass", "cube" (CubeObservation.to_dict), "cube_by_template" or None}
    passes: list = field(default_factory=list)  # one entry per recognize_cube call
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"refused": self.refused, "detail": self.detail, "pass1_matches_record": self.pass1_matches_record, "pad_frame_valid": self.pad_frame_valid,
                "complete": self.complete, "cubes": list(self.cubes), "passes": list(self.passes), "notes": list(self.notes)}


def _as_recorded(value):
    """The value as it reads back from a perception record: perceive writes json.dumps(record, default=float)."""
    return json.loads(json.dumps(value, default=float))


def _box_contains(box_xyxy, point) -> bool:
    return point is not None and box_xyxy[0] <= point[0] <= box_xyxy[2] and box_xyxy[1] <= point[1] <= box_xyxy[3]


def _placement(template: Optional[dict], cube):
    """cube_by_template for this cube, under the conditions observe_frame writes it with; None where it would not."""
    if not (template and template.get("pad_frame_valid") and template.get("homography_image_to_pad")):
        return None
    if not (cube.detected and cube.contact_px is not None and cube.confidence >= swo.CONTACT_CONFIDENCE_FLOOR):
        return None
    return swo.cube_by_template(SimpleNamespace(homography_image_to_pad=template["homography_image_to_pad"]), cube)


def _same_placement(recomputed, recorded) -> bool:
    """recomputed (a cube_by_template result or None) reproduces recorded (the record's cube_by_template or None): the same
    keys, the same non-numeric values, and every coordinate within PLACEMENT_REPRODUCTION_TOLERANCE_M."""
    if recomputed is None or recorded is None:
        return recomputed is None and recorded is None
    recomputed = _as_recorded(recomputed)
    if not isinstance(recorded, dict) or set(recomputed) != set(recorded):
        return False
    for key, value in recomputed.items():
        other = recorded[key]
        if isinstance(value, list):
            if not isinstance(other, list) or len(other) != len(value):
                return False
            if not all(isinstance(b, (int, float)) and not isinstance(b, bool) and abs(a - b) <= PLACEMENT_REPRODUCTION_TOLERANCE_M for a, b in zip(value, other)):
                return False
        elif value != other or type(value) is not type(other):
            return False
    return True


def _same_cube_again(mask, earlier: list):
    """The pass number of an earlier listed cube this mask is, by recognize_cube's own rule, or None."""
    area = int(np.count_nonzero(mask))
    for number, seen in earlier:
        shared = np.count_nonzero((mask > 0) & (seen > 0)) / max(min(area, int(np.count_nonzero(seen))), 1)
        if shared > SAME_CUBE_OVERLAP:
            return number
    return None


def _refuse(result: CandidateEnumeration, code: str, detail: str) -> CandidateEnumeration:
    result.refused, result.detail, result.cubes, result.complete = code, detail, [], False
    return result


def enumerate_verified_cubes(image_bgr, cube_detections, record, max_passes: int = MAX_PASSES) -> CandidateEnumeration:
    """Every verified cube of one frame, from the cube-label proposals the frame's perceive used and its frozen record.

    cube_detections: the proposals as CubeDetectionCache kept them (label, score, box_xyxy in pixels of image_bgr).
    record: the frozen perception record of the same frame, as read back from <obs>.json. Only its cube,
    cube_by_template, image_size_px and pad_by_template are used; nothing in it is changed."""
    result = CandidateEnumeration()
    if not isinstance(record, dict) or not isinstance(record.get("cube"), dict):
        return _refuse(result, REFUSED_RECORD_UNREADABLE, "the frozen record has no cube entry to compare pass 1 with")
    size = [int(image_bgr.shape[1]), int(image_bgr.shape[0])]
    if record.get("image_size_px") != size:
        return _refuse(result, REFUSED_IMAGE_SIZE, f"the frame is {size} px, the record says {record.get('image_size_px')}")
    template = record.get("pad_by_template") if isinstance(record.get("pad_by_template"), dict) else None
    result.pad_frame_valid = bool(template and template.get("pad_frame_valid") and template.get("homography_image_to_pad"))
    if not result.pad_frame_valid:
        result.notes.append("this frame's print landmarks did not fix PAD_FRAME: no cube is placed, the list carries pixels only")
    remaining, listed = list(cube_detections), []
    for number in range(1, int(max_passes) + 1):
        cube = recognize_cube(image_bgr, remaining)
        placed = _placement(template, cube)
        entry = {"pass": number, "proposals": len(remaining), "candidates_verified": cube.candidates_verified}
        if number == 1:
            if _as_recorded(cube.to_dict()) != record["cube"]:
                return _refuse(result, REFUSED_PASS1_CUBE, "pass 1 over the kept proposals does not reproduce the record's cube: they are not the proposals the record was made from")
            if not _same_placement(placed, record.get("cube_by_template")):
                return _refuse(result, REFUSED_PASS1_PLACEMENT, "pass 1 does not reproduce the record's cube_by_template through the record's own homography")
            result.pass1_matches_record = True
            placed = record.get("cube_by_template")  # the frozen record is the authority for the cube it already names
        if not cube.detected:
            entry["result"] = "no_verified_cube"
            result.passes.append(entry)
            result.complete = True
            break
        again = _same_cube_again(cube.mask, listed)
        if again is None:
            entry["result"] = "cube"
            listed_cube = record["cube"] if number == 1 else _as_recorded(cube.to_dict())
            result.cubes.append({"pass": number, "cube": listed_cube, "cube_by_template": None if placed is None else _as_recorded(placed)})
            listed.append((number, cube.mask))
        else:
            entry["result"] = "same_cube_again"
            result.notes.append(f"pass {number}: the winner shares more than {SAME_CUBE_OVERLAP} of its mask with the cube of pass {again}; not listed again")
        kept = [d for d in remaining if not _box_contains(d["box_xyxy"], cube.center_px)]
        entry["dropped"] = len(remaining) - len(kept)
        result.passes.append(entry)
        if not kept:
            result.complete = True
            break
        if len(kept) == len(remaining):
            result.notes.append(f"pass {number}: no proposal box contains the winner's centre, so another pass would repeat it; stopped, the list may be incomplete")
            break
        remaining = kept
    else:
        result.notes.append(f"stopped at the pass limit ({max_passes}) with {len(remaining)} proposal(s) left: more cubes may be in view")
    return result


def sidecar_path(out_dir, name: str) -> Path:
    return Path(out_dir) / f"{name}{SIDECAR_SUFFIX}"


def enumerate_observation(image_bgr, detections, out_dir, name: str, max_passes: int = MAX_PASSES) -> dict:
    """Run after the unchanged perceive(role, image, out_dir, name, detections) of the same frame: enumerate, write
    <name>-candidates.json beside the frozen <name>.json and return what was written. detections is the
    CubeDetectionCache perceive was given. A refusal is written too, as evidence; nothing here raises for bad data."""
    out_dir = Path(out_dir)
    digest = frame_digest(image_bgr)
    record_sha256, record = None, None
    try:
        record_bytes = (out_dir / f"{name}.json").read_bytes()
        record_sha256 = "sha256:" + hashlib.sha256(record_bytes).hexdigest()
        record = json.loads(record_bytes)
    except (OSError, ValueError) as error:
        result = _refuse(CandidateEnumeration(), REFUSED_RECORD_UNREADABLE, f"the frozen record cannot be read: {type(error).__name__}")
    else:
        cube_detections = detections.cube_detections(digest)
        if cube_detections is None:
            result = _refuse(CandidateEnumeration(), REFUSED_NO_CACHED_DETECTIONS, "the detector cache did not see this frame; OWLv2 is not run again here")
        else:
            result = enumerate_verified_cubes(image_bgr, cube_detections, record, max_passes)
    sidecar = {"schema": SCHEMA, "observation_id": name, "frame_digest": digest, "frozen_record_sha256": record_sha256, "max_passes": int(max_passes), **result.to_dict()}
    path = sidecar_path(out_dir, name)
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(json.dumps(sidecar, indent=2, default=float))
    os.replace(partial, path)  # a reader sees the whole sidecar or none of it
    return sidecar
