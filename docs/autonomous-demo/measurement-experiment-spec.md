<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Measurement and experiment specification

## Storage rule

Tracked:
- numeric calibration/config;
- schemas;
- sanitized aggregate CSVs;
- synthetic fixtures.

Local ignored only:
- raw camera images/video;
- LeRobot datasets/episodes;
- full run logs;
- checkpoints;
- model outputs;
- dev Cake artifacts/signatures unless deliberately prepared for final release.

## Physical constants

Record SI units in `autonomy/config/geometry.yaml`:

- cube edge;
- pad width/height;
- body camera base offset x/y/z;
- approximate body camera roll/pitch/yaw for sanity.

Calibrated extrinsics override manual angle estimates.

## Camera intrinsics

For each camera:
- width/height;
- fx/fy/cx/cy;
- distortion model/coefficients;
- reprojection error;
- calibration target geometry.

Capture ~15–25 diverse ChArUco/checkerboard views locally.
Commit only numeric results.

## Ground-plane validation

Suggested cube positions relative to base:

Forward:
0.25, 0.50, 0.75, 1.00, 1.25 m, optionally 1.50 m.

Lateral:
-0.40, -0.20, 0, +0.20, +0.40 m.

Select ~20–30 useful combinations; no need for full grid.

Aggregate metrics:
- median error;
- p90 error;
- error by range bucket;
- detection/projection failure rate.

## Cube detector evaluation

Local test captures must span:
- daylight;
- dim;
- warm;
- mixed light;
- floor/background changes;
- near/medium/far;
- partial occlusion;
- cube near/away from pad.

Commit aggregate recall/false-positive summaries only.

## Pad localization evaluation

Capture:
- canonical drop pose;
- lateral offsets;
- yaw offsets;
- near/medium;
- partial pad views;
- lighting variation.

Aggregate:
- reacquisition rate;
- valid-homography rate;
- stability/error;
- false positives.

## Manipulation success grid

Start coarse, adjust to real arm geometry:

```text
x = 0.20, 0.25, 0.30, 0.35, 0.40 m
y = -0.15, -0.075, 0, +0.075, +0.15 m
```

For repeated attempts record locally:
- checkpoint;
- x/y;
- grasp success;
- stable lift;
- placement success;
- failure class.

Commit only aggregate success map.

`MANIPULATION_READY` is defined from measured success, perception quality,
zero base motion, and correct handoff state.

## E2E run recorder

Each physical run records locally:
- both camera streams;
- robot state;
- requested/applied actions;
- observations;
- mission transitions;
- Cake lifecycle events;
- continuity counters;
- result/failure class.

No raw run directory is ever committed.
