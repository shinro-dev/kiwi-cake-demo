#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cube_candidates.py: every verified cube of one frame, enumerated BESIDE the frozen perception. What
# must hold: the caching detector changes nothing perceive computes; pass 1 of the enumeration is the record's own cube
# (or the enumeration refuses and lists nothing); two cubes in one frame are both listed, each placed through the frame's
# own homography; the same inputs give the same sidecar bytes; and the frozen <obs>.json is never written.
#
# The synthetic tests run everywhere: no model, no camera, no robot, no network. OWLv2 is never loaded; proposals are
# either written by the test or the ones the perception benchmark RECORDED.
#
# RetainedFramesTest is LOCAL ONLY. It reads the perception benchmark next to this checkout (ignored, never committed):
# its frames list (frames-links) and the result files that carry, per frame, the detector's proposals and the record the
# unchanged perception wrote. Frames are only READ; none is copied, and a failure names a frame by its observation id,
# never by where it lives. It is SKIPPED, with the reason, when that data is absent.
# Run directly: python3 autonomy/tests/test_cube_candidates.py
import glob
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("autonomy/perception", "tools", "bin/laptop/perception"):
    sys.path.insert(0, str(ROOT / _sub))

import cv2  # noqa: E402

import cube_candidates as cc  # noqa: E402
import run_stage1_scan  # noqa: E402
import static_world_observation as swo  # noqa: E402
from cube_recognizer import CubeObservation, recognize_cube  # noqa: E402
from zero_shot_detector import CUBE_LABELS, PAD_LABELS  # noqa: E402

BENCH = ROOT / ".local-dev" / "console" / "owlv2-feasibility"
FRAMES = BENCH / "frames-links"
# The benchmark's own pins: the perception sources and perceive() whose outputs it recorded (handoff section 12.3).
PINNED_SOURCES = ("autonomy/perception/zero_shot_detector.py", "tools/static_world_observation.py", "autonomy/perception/cube_recognizer.py",
                  "autonomy/perception/pad_localizer.py", "autonomy/perception/pad_template_localizer.py", "autonomy/perception/frame_illumination.py",
                  "autonomy/perception/observation_store.py")
PERCEIVE_HEAD = "def perceive(role: str, image, out_dir: Path, name: str, detector) -> list:"

WIDTH, HEIGHT = 1280, 720
CUBE_SIDE_PX = 80
BACKGROUND_BGR = (62, 48, 40)  # a dark mat
CUBE_BGR = (120, 215, 232)  # pale yellow
# A plain homography, image pixels -> PAD_FRAME metres: 0.5 mm a pixel, origin at the image centre, pad y up the image.
HOMOGRAPHY = [[0.0005, 0.0, -0.32], [0.0, -0.0005, 0.18], [0.0, 0.0, 1.0]]


def observation_id(number: int) -> str:
    return f"obs-20260101T00000{number}Z-wrist-0001"  # the shape of a store id, built here rather than copied from a run


def scene(corners):
    """A dark frame with one pale square per top-left corner, and faint fixed noise so grabCut's colour models are not
    degenerate. Deterministic: the same corners give the same bytes."""
    image = np.empty((HEIGHT, WIDTH, 3), np.uint8)
    image[:] = BACKGROUND_BGR
    for x, y in corners:
        image[y : y + CUBE_SIDE_PX, x : x + CUBE_SIDE_PX] = CUBE_BGR
    noise = np.random.default_rng(0).integers(-3, 4, image.shape)
    return np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def proposals(corners, scores):
    """One cube-label proposal per square (box 3 px outside it) and one pad proposal the cache must not keep."""
    found = [{"label": CUBE_LABELS[index % 2], "score": score, "box_xyxy": [float(x - 3), float(y - 3), float(x + CUBE_SIDE_PX + 3), float(y + CUBE_SIDE_PX + 3)]}
             for index, ((x, y), score) in enumerate(zip(corners, scores))]
    found.append({"label": PAD_LABELS[0], "score": 0.7, "box_xyxy": [0.0, 0.0, float(WIDTH), float(HEIGHT)]})
    return sorted(found, key=lambda d: -d["score"])


class FixedDetector:
    """Answers every detect() with the same proposals, filtered to the labels asked for, and counts its calls."""

    def __init__(self, detections):
        self.detections, self.calls, self._model = detections, 0, None

    def detect(self, image_bgr, labels, threshold=0.08, try_rotations=False):
        self.calls += 1
        return [dict(d) for d in self.detections if d["label"] in list(labels)]


def record_with_pad(image, cube_detections):
    """The record observe_frame writes when PAD_FRAME is fixed by the print, built from the SAME unchanged functions and
    the same conditions: recognize_cube on the cube-label proposals, cube_by_template through the frame's homography."""
    cube = recognize_cube(image, cube_detections)
    record = {"camera": "wrist", "image_size_px": [WIDTH, HEIGHT], "cube": cube.to_dict(), "pad": None, "focal_sweep": [],
              "pad_by_template": {"pad_identity": True, "pad_frame_valid": True, "reason": "synthetic", "homography_image_to_pad": HOMOGRAPHY}}
    if cube.detected and cube.contact_px is not None and cube.confidence >= swo.CONTACT_CONFIDENCE_FLOOR:
        record["cube_by_template"] = swo.cube_by_template(SimpleNamespace(homography_image_to_pad=HOMOGRAPHY), cube)
    return json.loads(json.dumps(record, default=float))


def write_record(directory: Path, name: str, record: dict) -> bytes:
    """As perceive writes it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(record, indent=2, default=float))
    return (directory / f"{name}.json").read_bytes()


TWO = [(400, 380), (820, 420)]


class CacheTest(unittest.TestCase):
    def test_detect_returns_the_detectors_own_answer_and_keeps_only_cube_proposals(self):
        image = scene(TWO)
        inner = FixedDetector(proposals(TWO, [0.6, 0.45]))
        cache = cc.CubeDetectionCache(inner)
        answer = cache.detect(image, list(CUBE_LABELS) + list(PAD_LABELS))
        self.assertEqual(inner.calls, 1)
        self.assertEqual([d["label"] for d in answer].count(PAD_LABELS[0]), 1)  # the caller still sees the pad
        kept = cache.cube_detections(cc.frame_digest(image))
        self.assertEqual(kept, [d for d in answer if d["label"] in CUBE_LABELS])
        answer[0]["score"] = 0.0  # what the caller does with its answer does not reach the kept copy
        self.assertNotEqual(cache.cube_detections(cc.frame_digest(image))[0]["score"], 0.0)

    def test_a_call_without_the_cube_labels_is_not_read_as_no_cube(self):
        image = scene(TWO)
        cache = cc.CubeDetectionCache(FixedDetector(proposals(TWO, [0.6, 0.45])))
        cache.detect(image, list(PAD_LABELS))
        self.assertIsNone(cache.cube_detections(cc.frame_digest(image)))

    def test_an_evicted_frame_is_not_detected_again(self):
        inner = FixedDetector(proposals(TWO, [0.6, 0.45]))
        cache = cc.CubeDetectionCache(inner, max_frames=2)
        frames = [scene([(100 + 10 * k, 100)]) for k in range(3)]
        for frame in frames:
            cache.detect(frame, list(CUBE_LABELS) + list(PAD_LABELS))
        self.assertIsNone(cache.cube_detections(cc.frame_digest(frames[0])))
        self.assertIsNotNone(cache.cube_detections(cc.frame_digest(frames[2])))
        self.assertEqual(inner.calls, 3)

    def test_other_attributes_are_the_wrapped_detectors(self):
        inner = FixedDetector([])
        cache = cc.CubeDetectionCache(inner)
        self.assertIsNone(getattr(cache, "_model", True))  # the cycle's lazy-load check reads the same answer through the wrapper
        inner._model = "loaded"
        self.assertEqual(cache._model, "loaded")

    def test_the_frame_digest_depends_on_the_pixels_and_the_shape(self):
        image = scene(TWO)
        self.assertEqual(cc.frame_digest(image), cc.frame_digest(image.copy()))
        other = image.copy()
        other[0, 0, 0] ^= 1
        self.assertNotEqual(cc.frame_digest(image), cc.frame_digest(other))
        self.assertNotEqual(cc.frame_digest(image), cc.frame_digest(image.reshape(WIDTH, HEIGHT, 3)))


class FrozenPathTest(unittest.TestCase):
    """The unchanged perceive of the scan CLI, once with the bare detector and once through the cache."""

    def test_perceive_through_the_cache_writes_the_same_bytes_and_the_enumeration_never_touches_them(self):
        image, name = scene(TWO), observation_id(1)
        detections = [d for d in proposals(TWO, [0.6, 0.45]) if d["label"] in CUBE_LABELS]  # no pad proposal: no outline fit, no template
        with tempfile.TemporaryDirectory() as bare_dir, tempfile.TemporaryDirectory() as cached_dir:
            bare, cached = Path(bare_dir), Path(cached_dir)
            run_stage1_scan.perceive("wrist", image, bare, name, FixedDetector(detections))
            inner = FixedDetector(detections)
            cache = cc.CubeDetectionCache(inner)
            run_stage1_scan.perceive("wrist", image, cached, name, cache)
            for suffix in (".json", "-overlay.png"):
                self.assertEqual((bare / f"{name}{suffix}").read_bytes(), (cached / f"{name}{suffix}").read_bytes(), suffix)
            frozen = (cached / f"{name}.json").read_bytes()
            stamp = (cached / f"{name}.json").stat().st_mtime_ns
            sidecar = cc.enumerate_observation(image, cache, cached, name)
            self.assertEqual((cached / f"{name}.json").read_bytes(), frozen)
            self.assertEqual((cached / f"{name}.json").stat().st_mtime_ns, stamp)
            self.assertEqual(inner.calls, 1)  # the enumeration asked the detector nothing
            self.assertEqual(json.loads((cached / f"{name}{cc.SIDECAR_SUFFIX}").read_text()), sidecar)
            self.assertEqual(sidecar["frozen_record_sha256"], "sha256:" + hashlib.sha256(frozen).hexdigest())
            self.assertIsNone(sidecar["refused"], sidecar["detail"])
            self.assertEqual(sidecar["cubes"][0]["cube"], json.loads(frozen)["cube"])
            self.assertEqual(len(sidecar["cubes"]), 2)
            self.assertFalse(sidecar["pad_frame_valid"])  # this record has no PAD_FRAME: pixels only, never a guessed position
            self.assertEqual([c["cube_by_template"] for c in sidecar["cubes"]], [None, None])
            self.assertEqual(sorted(os.listdir(cached)), sorted([f"{name}.json", f"{name}-overlay.png", f"{name}{cc.SIDECAR_SUFFIX}"]))


class EnumerationTest(unittest.TestCase):
    def enumerate(self, corners, scores, record_corners=None, record_scores=None, **options):
        image = scene(corners)
        cube_detections = [d for d in proposals(corners, scores) if d["label"] in CUBE_LABELS]
        record_detections = cube_detections if record_corners is None else [d for d in proposals(record_corners, record_scores) if d["label"] in CUBE_LABELS]
        record = record_with_pad(image, record_detections)
        return cc.enumerate_verified_cubes(image, cube_detections, record, **options), record

    def test_two_cubes_are_both_listed_each_placed_through_the_frames_own_homography(self):
        result, record = self.enumerate(TWO, [0.6, 0.45])
        self.assertIsNone(result.refused, result.detail)
        self.assertTrue(result.pass1_matches_record and result.pad_frame_valid and result.complete)
        self.assertEqual(len(result.cubes), 2)
        first, second = result.cubes
        self.assertEqual((first["cube"], first["cube_by_template"]), (record["cube"], record["cube_by_template"]))
        self.assertEqual(record["cube"]["candidates_verified"], 2)  # the frozen record only COUNTED the second cube
        for entry, (x, y) in zip(result.cubes, TWO):
            centre_px = entry["cube"]["center_px"]
            self.assertLess(abs(centre_px[0] - (x + CUBE_SIDE_PX / 2)), 3.0)
            self.assertLess(abs(centre_px[1] - (y + CUBE_SIDE_PX / 2)), 3.0)
            # contact edge = the square's bottom side; the centre is 12.7 mm behind it, i.e. up the image in this map
            expected = [(x + CUBE_SIDE_PX / 2 - 640) * 0.0005, (360 - (y + CUBE_SIDE_PX)) * 0.0005 + swo.CUBE_EDGE_M / 2]
            np.testing.assert_allclose(entry["cube_by_template"]["cube_center_pad_m"], expected, atol=0.002)
        self.assertGreater(abs(first["cube_by_template"]["cube_center_pad_m"][0] - second["cube_by_template"]["cube_center_pad_m"][0]), 0.15)
        self.assertEqual([p["result"] for p in result.passes], ["cube", "cube"])

    def test_the_best_cube_is_listed_first_whichever_order_the_detector_used(self):
        result, _ = self.enumerate(TWO, [0.45, 0.6])
        self.assertIsNone(result.refused, result.detail)
        self.assertEqual(len(result.cubes), 2)
        self.assertEqual(result.cubes[0]["cube"]["detector_score"], 0.6)

    def test_one_cube_is_one_entry_and_the_list_is_complete(self):
        result, _ = self.enumerate(TWO[:1], [0.6])
        self.assertIsNone(result.refused, result.detail)
        self.assertEqual((len(result.cubes), result.complete), (1, True))

    def test_no_proposal_is_no_cube_and_not_a_refusal(self):
        image = scene([])
        record = record_with_pad(image, [])
        result = cc.enumerate_verified_cubes(image, [], record)
        self.assertEqual((result.refused, result.pass1_matches_record, result.cubes, result.complete), (None, True, [], True))

    def test_at_most_three_passes_and_a_list_cut_by_the_limit_says_so(self):
        four = [(150, 150), (500, 150), (850, 150), (500, 480)]
        result, _ = self.enumerate(four, [0.6, 0.55, 0.5, 0.45])
        self.assertIsNone(result.refused, result.detail)
        self.assertEqual(len(result.passes), cc.MAX_PASSES)
        self.assertEqual(len(result.cubes), 3)
        self.assertFalse(result.complete)
        self.assertIn("pass limit", result.notes[-1])

    def test_proposals_that_are_not_the_records_refuse_and_list_nothing(self):
        result, _ = self.enumerate(TWO, [0.6, 0.45], record_corners=TWO, record_scores=[0.61, 0.45])
        self.assertEqual(result.refused, cc.REFUSED_PASS1_CUBE)
        self.assertEqual(result.cubes, [])
        self.assertFalse(result.complete)

    def test_a_placement_that_the_records_homography_does_not_reproduce_refuses(self):
        image = scene(TWO)
        cube_detections = [d for d in proposals(TWO, [0.6, 0.45]) if d["label"] in CUBE_LABELS]
        record = record_with_pad(image, cube_detections)
        record["cube_by_template"]["cube_center_pad_m"][0] += 0.001
        result = cc.enumerate_verified_cubes(image, cube_detections, record)
        self.assertEqual((result.refused, result.cubes), (cc.REFUSED_PASS1_PLACEMENT, []))
        record = record_with_pad(image, cube_detections)
        record["cube_by_template"]["cube_center_pad_m"][0] += 1e-12  # the last bits another machine may round differently
        result = cc.enumerate_verified_cubes(image, cube_detections, record)
        self.assertIsNone(result.refused, result.detail)
        self.assertEqual(result.cubes[0]["cube_by_template"], record["cube_by_template"])  # the record's own numbers are listed
        record = record_with_pad(image, cube_detections)
        record["cube_by_template"]["inside_pad_outline"] = not record["cube_by_template"]["inside_pad_outline"]
        self.assertEqual(cc.enumerate_verified_cubes(image, cube_detections, record).refused, cc.REFUSED_PASS1_PLACEMENT)
        record = record_with_pad(image, cube_detections)
        del record["cube_by_template"]
        self.assertEqual(cc.enumerate_verified_cubes(image, cube_detections, record).refused, cc.REFUSED_PASS1_PLACEMENT)

    def test_a_record_of_another_frame_size_refuses(self):
        image = scene(TWO)
        cube_detections = [d for d in proposals(TWO, [0.6, 0.45]) if d["label"] in CUBE_LABELS]
        record = record_with_pad(image, cube_detections)
        record["image_size_px"] = [640, 480]
        self.assertEqual(cc.enumerate_verified_cubes(image, cube_detections, record).refused, cc.REFUSED_IMAGE_SIZE)
        self.assertEqual(cc.enumerate_verified_cubes(image, cube_detections, {"cube": None}).refused, cc.REFUSED_RECORD_UNREADABLE)

    def test_a_record_without_pad_frame_lists_pixels_only(self):
        image = scene(TWO)
        cube_detections = [d for d in proposals(TWO, [0.6, 0.45]) if d["label"] in CUBE_LABELS]
        record = record_with_pad(image, cube_detections)
        record["pad_by_template"]["pad_frame_valid"] = False
        del record["cube_by_template"]
        result = cc.enumerate_verified_cubes(image, cube_detections, record)
        self.assertIsNone(result.refused, result.detail)
        self.assertFalse(result.pad_frame_valid)
        self.assertEqual([c["cube_by_template"] for c in result.cubes], [None, None])


class PassRulesTest(unittest.TestCase):
    """The two stopping rules that real silhouettes rarely reach, with recognize_cube replaced by a script."""

    @staticmethod
    def cube(center, mask_box):
        mask = np.zeros((HEIGHT, WIDTH), np.uint8)
        x0, y0, x1, y1 = mask_box
        mask[y0:y1, x0:x1] = 255
        found = CubeObservation(detected=True, confidence=0.9, detector_score=0.6, detector_label=CUBE_LABELS[0], bbox_xyxy=list(mask_box), mask=mask,
                                mask_area_px=int(np.count_nonzero(mask)), center_px=list(center), clipped={"any_border": False, "bottom": False, "left": False, "right": False, "top": False})
        return found

    def run_script(self, answers, detections):
        image = scene([])
        script = iter(answers)
        record = {"image_size_px": [WIDTH, HEIGHT], "cube": json.loads(json.dumps(answers[0].to_dict(), default=float)), "pad_by_template": None}
        with mock.patch.object(cc, "recognize_cube", lambda image, candidates: next(script)):
            return cc.enumerate_verified_cubes(image, detections, record)

    def test_a_winner_that_is_an_earlier_cube_again_is_not_listed_twice(self):
        first = self.cube((140.0, 140.0), (100, 100, 180, 180))
        again = self.cube((175.0, 140.0), (110, 100, 190, 180))  # most of its mask is the first cube's
        boxes = [{"label": CUBE_LABELS[0], "score": 0.6, "box_xyxy": [95.0, 95.0, 185.0, 185.0]}, {"label": CUBE_LABELS[0], "score": 0.5, "box_xyxy": [150.0, 95.0, 195.0, 185.0]}]
        result = self.run_script([first, again], boxes)
        self.assertIsNone(result.refused, result.detail)
        self.assertEqual(len(result.cubes), 1)
        self.assertEqual([p["result"] for p in result.passes], ["cube", "same_cube_again"])
        self.assertTrue(result.complete)

    def test_a_winner_no_box_contains_stops_rather_than_repeating(self):
        first = self.cube((400.0, 400.0), (360, 360, 440, 440))
        boxes = [{"label": CUBE_LABELS[0], "score": 0.6, "box_xyxy": [0.0, 0.0, 50.0, 50.0]}]
        result = self.run_script([first], boxes)
        self.assertEqual((len(result.cubes), len(result.passes), result.complete), (1, 1, False))
        self.assertIn("would repeat it", result.notes[-1])


class SidecarTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.out = Path(self._directory.name)
        self.image, self.name = scene(TWO), observation_id(2)
        self.cache = cc.CubeDetectionCache(FixedDetector(proposals(TWO, [0.6, 0.45])))
        self.cache.detect(self.image, list(CUBE_LABELS) + list(PAD_LABELS))
        self.frozen = write_record(self.out, self.name, record_with_pad(self.image, self.cache.cube_detections(cc.frame_digest(self.image))))

    def tearDown(self):
        self._directory.cleanup()

    def test_the_same_inputs_give_the_same_sidecar_bytes_and_the_frozen_record_stays(self):
        first = cc.enumerate_observation(self.image, self.cache, self.out, self.name)
        first_bytes = cc.sidecar_path(self.out, self.name).read_bytes()
        again = cc.CubeDetectionCache(FixedDetector(proposals(TWO, [0.6, 0.45])))  # a fresh cache and a fresh copy of the frame
        again.detect(self.image.copy(), list(CUBE_LABELS) + list(PAD_LABELS))
        second = cc.enumerate_observation(self.image.copy(), again, self.out, self.name)
        self.assertEqual(first, second)
        self.assertEqual(cc.sidecar_path(self.out, self.name).read_bytes(), first_bytes)
        self.assertEqual((self.out / f"{self.name}.json").read_bytes(), self.frozen)
        self.assertEqual(len(first["cubes"]), 2)
        self.assertEqual([p.name for p in self.out.iterdir() if p.name.endswith(".partial")], [])

    def test_a_frame_the_cache_did_not_see_is_refused_in_writing(self):
        other = scene([(10, 10)])
        sidecar = cc.enumerate_observation(other, self.cache, self.out, self.name)
        self.assertEqual((sidecar["refused"], sidecar["cubes"]), (cc.REFUSED_NO_CACHED_DETECTIONS, []))
        self.assertEqual(json.loads(cc.sidecar_path(self.out, self.name).read_text()), sidecar)
        self.assertEqual((self.out / f"{self.name}.json").read_bytes(), self.frozen)

    def test_an_unreadable_record_is_refused_in_writing(self):
        (self.out / f"{self.name}.json").write_text("{not json")
        sidecar = cc.enumerate_observation(self.image, self.cache, self.out, self.name)
        self.assertEqual(sidecar["refused"], cc.REFUSED_RECORD_UNREADABLE)
        missing = cc.enumerate_observation(self.image, self.cache, self.out, observation_id(3))
        self.assertEqual((missing["refused"], missing["frozen_record_sha256"]), (cc.REFUSED_RECORD_UNREADABLE, None))


def _recorded_runs():
    """[(result file stem, result, [frame entry of its first pass, ...])] for every benchmark result that carries per-frame
    proposals and records. Only stems are returned: nothing about where the data lives."""
    runs = []
    for path in sorted(glob.glob(str(BENCH / "results" / "**" / "*.json"), recursive=True)):
        try:
            with open(path) as handle:
                result = json.load(handle)
        except (OSError, ValueError):
            continue
        passes = result.get("passes") if isinstance(result, dict) else None
        first = passes[0] if isinstance(passes, list) and passes and isinstance(passes[0], list) else []
        frames = [f for f in first if isinstance(f, dict) and "detections" in f and "record" in f and "frame" in f]
        if frames:
            runs.append((Path(path).stem, result, frames))
    return runs


class RetainedFramesTest(unittest.TestCase):
    """LOCAL ONLY: pass 1 over the RECORDED proposals of each retained frame is that frame's recorded cube."""

    @classmethod
    def setUpClass(cls):
        if not FRAMES.is_dir():
            raise unittest.SkipTest("the perception benchmark's frames list is not present in this checkout")
        cls.frames = {path.stem: path for path in sorted(FRAMES.iterdir()) if path.suffix == ".png" and path.exists()}
        if not cls.frames:
            raise unittest.SkipTest("the benchmark's frames list resolves to no readable frame here")
        cls.runs = _recorded_runs()
        if not cls.runs:
            raise unittest.SkipTest("no benchmark result with per-frame proposals and records is present")

    def test_the_sources_are_the_ones_the_benchmark_recorded_with(self):
        text = (ROOT / "bin" / "laptop" / "perception" / "run_stage1_scan.py").read_text()
        start = text.index(PERCEIVE_HEAD)
        perceive = hashlib.sha256(text[start : text.index("\ndef make_detector", start)].encode()).hexdigest()
        for stem, result, _ in self.runs:
            with self.subTest(run=stem):
                pinned = result.get("sources_sha256") or {}
                self.assertEqual(set(pinned), set(PINNED_SOURCES))
                for relative in PINNED_SOURCES:
                    self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), pinned[relative], relative)
                self.assertEqual(perceive, result.get("perceive_source_sha256"))

    def test_pass_one_is_the_recorded_cube_on_every_retained_frame(self):
        checked = 0
        for stem, _, entries in self.runs:
            for entry in entries:
                observation = Path(entry["frame"]).stem
                image = cv2.imread(str(self.frames[observation])) if observation in self.frames else None
                if image is None:
                    continue
                with self.subTest(run=stem, frame=observation), tempfile.TemporaryDirectory() as directory:
                    out = Path(directory)
                    frozen = write_record(out, observation, entry["record"])
                    cache = cc.CubeDetectionCache(FixedDetector(entry["detections"]))
                    cache.detect(image, list(CUBE_LABELS) + list(PAD_LABELS))
                    sidecar = cc.enumerate_observation(image, cache, out, observation)
                    self.assertIsNone(sidecar["refused"], sidecar["detail"])
                    self.assertTrue(sidecar["pass1_matches_record"])
                    self.assertEqual(sidecar["cubes"][0]["cube"], entry["record"]["cube"])
                    self.assertEqual(sidecar["cubes"][0]["cube_by_template"], entry["record"].get("cube_by_template"))
                    self.assertEqual((out / f"{observation}.json").read_bytes(), frozen)
                    checked += 1
        self.assertGreaterEqual(checked, 8, "fewer than the eight retained frames could be checked")


if __name__ == "__main__":
    unittest.main()
