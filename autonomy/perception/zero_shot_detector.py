#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""zero_shot_detector: a thin wrapper around the OWLv2 open-vocabulary object
detector, used ONLY to propose where a known mission object (the cube, the
pad) might be in one image.

Model: google/owlv2-base-patch16-ensemble (Apache-2.0). The weights are
downloaded by transformers into the local model cache of the machine that
runs this; they are never committed to this repository.

What this does NOT establish: a box from here is a PROPOSAL, not a detection.
It carries no geometry, no metric size and no guarantee that the object is
real: on the project's own frames the cube labels also score a dark equipment
case at 0.08 to 0.10 and a desk side and a printed logo at 0.04 to 0.08,
while a mostly hidden cube scores only 0.04. Whether a
cube exists is decided by cube_recognizer.recognize_cube, which verifies the
shape inside the proposed box. No detector is trained or tuned here.

Frames and units: every box is [x0, y0, x1, y1] in pixels of the image AS
PASSED IN (x right, y down, origin top-left), clipped to the image. OWLv2
pads its input to a square on the right/bottom, so the network's normalised
boxes are scaled by max(width, height) on both axes, not by width and height.

The robot host delivers the wrist image rotated by 90 degrees. The model was
trained on upright pictures and scores the same cube very differently per
orientation, so detect(..., try_rotations=True) also looks at the image turned
by +90 and -90 degrees, maps those boxes back and keeps the best score of
overlapping boxes. That is three looks instead of one: more recall AND more
chances for a false proposal, which is one more reason a proposal is never a
detection.

torch and transformers are imported on first use, so importing this module
on a machine without them does not fail.
"""

MODEL_NAME = "google/owlv2-base-patch16-ensemble"
MODEL_LICENSE = "Apache-2.0"
DEFAULT_THRESHOLD = 0.08
MERGE_IOU = 0.6  # boxes of one label that overlap this much are one proposal

# Text queries. The second cube label exists because the olive, shaded top
# face seen by the wrist camera at grasp height reads as "wooden block".
CUBE_LABELS = ("a yellow cube", "a small wooden block")
PAD_LABELS = ("a mouse pad", "a dark blue desk mat")


def scale_boxes_to_image(boxes_padded_square, width: int, height: int) -> list:
    """Boxes in pixels of the PADDED SQUARE (side max(width, height)) ->
    boxes clipped to the original width x height image, as lists of float.
    Pure arithmetic so it can be tested without the model."""
    clipped = []
    for x0, y0, x1, y1 in boxes_padded_square:
        clipped.append(
            [
                float(min(max(x0, 0.0), width)),
                float(min(max(y0, 0.0), height)),
                float(min(max(x1, 0.0), width)),
                float(min(max(y1, 0.0), height)),
            ]
        )
    return clipped


def box_from_rotated(box_xyxy, turn: str, width: int, height: int) -> list:
    """A box found in the image turned by 90 degrees ("cw" or "ccw", as
    cv2.rotate does it) -> the same box in pixels of the unturned
    width x height image."""
    x0, y0, x1, y1 = box_xyxy
    if turn == "cw":  # turned (x', y') = (height - y, x)
        return [y0, height - x1, y1, height - x0]
    if turn == "ccw":  # turned (x', y') = (y, width - x)
        return [width - y1, x0, width - y0, x1]
    raise ValueError("turn must be 'cw' or 'ccw'")


def box_iou(a, b) -> float:
    """Intersection over union of two [x0, y0, x1, y1] pixel boxes."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - iw * ih
    return iw * ih / union


def keep_best_of_overlapping(detections, iou_min: float = MERGE_IOU) -> list:
    """Greedy suppression per label: of boxes that overlap by iou_min or more
    only the best-scoring one survives. Sorted by descending score."""
    kept = []
    for detection in sorted(detections, key=lambda d: -d["score"]):
        same = [k for k in kept if k["label"] == detection["label"]]
        if all(box_iou(detection["box_xyxy"], k["box_xyxy"]) < iou_min for k in same):
            kept.append(detection)
    return kept


class ZeroShotDetector:
    """Loads the model once (lazily) and answers detect() calls."""

    def __init__(self, device=None):
        self._device = device  # None: cuda when available, else cpu
        self._model = None
        self._processor = None
        self._torch = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self._torch = torch
        if self._device is None:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._processor = Owlv2Processor.from_pretrained(MODEL_NAME)
        self._model = Owlv2ForObjectDetection.from_pretrained(MODEL_NAME).to(self._device).eval()

    def detect(self, image_bgr, labels, threshold: float = DEFAULT_THRESHOLD, try_rotations: bool = False) -> list:
        """Proposals for the text labels in one BGR image (H, W, 3, uint8).

        Returns a list of {"label": str, "score": float in [0, 1],
        "box_xyxy": [x0, y0, x1, y1]} sorted by descending score. Boxes are in
        pixels of image_bgr, clipped to it; boxes that clip to nothing are
        dropped. Scores are per-label sigmoids: they are not calibrated
        probabilities and labels do not compete with each other.

        try_rotations (for the rotated host wrist image): also detect in the
        image turned by +90 and -90 degrees; see the module docstring."""
        import cv2

        height, width = image_bgr.shape[:2]
        detections = self._detect_once(image_bgr, labels, threshold)
        if not try_rotations:
            return detections
        for turn, code in (("cw", cv2.ROTATE_90_CLOCKWISE), ("ccw", cv2.ROTATE_90_COUNTERCLOCKWISE)):
            for found in self._detect_once(cv2.rotate(image_bgr, code), labels, threshold):
                box = box_from_rotated(found["box_xyxy"], turn, width, height)
                detections.append({"label": found["label"], "score": found["score"], "box_xyxy": box, "turn": turn})
        return keep_best_of_overlapping(detections)

    def _detect_once(self, image_bgr, labels, threshold: float) -> list:
        self._load()
        torch = self._torch
        labels = list(labels)
        height, width = image_bgr.shape[:2]
        image_rgb = image_bgr[:, :, ::-1].copy()
        inputs = self._processor(text=[labels], images=image_rgb, return_tensors="pt").to(self._device)
        with torch.no_grad():
            outputs = self._model(**inputs)
        side = max(width, height)  # the processor pads to a square before resizing
        result = self._processor.post_process_grounded_object_detection(
            outputs=outputs, target_sizes=torch.tensor([[side, side]]), threshold=threshold
        )[0]
        boxes = scale_boxes_to_image(result["boxes"].tolist(), width, height)
        detections = []
        for score, label_index, box in zip(result["scores"].tolist(), result["labels"].tolist(), boxes):
            if box[2] - box[0] < 1.0 or box[3] - box[1] < 1.0:
                continue
            detections.append({"label": labels[label_index], "score": float(score), "box_xyxy": box})
        detections.sort(key=lambda d: -d["score"])
        return detections
