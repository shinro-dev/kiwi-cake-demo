<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Perception and localization design

## Scope

Only solve what the task needs:

- detect fixed yellow cubes;
- recognize/localize the known pad;
- estimate cube position relative to base;
- decide whether a cube is inside empirical manipulation readiness;
- support closed-loop approach and pad return.

No general SLAM or scene understanding.

## Frames

Right-handed `base`:
- +x forward
- +y left
- +z up

Calibrate `body_cam` rigidly to `base`.
Use wrist camera extrinsics/forward kinematics only where needed.

Every geometry API states its frame and units.

## Camera roles

| Phase | Body | Wrist |
|---|---|---|
| Search | primary | optional/secondary |
| Coarse cube localization | primary | optional confirmation |
| Mobile approach | primary | optional |
| Manipulation handoff | context | primary |
| Grasp | context | primary |
| Carry | primary | potentially occluded |
| Pad reacquisition | primary | optional |
| Final placement | context | primary |

## Yellow cube detector

### Baseline
Start deterministic:
1. Lab/normalized-HSV candidate mask;
2. morphology;
3. connected components;
4. size/shape/floor-contact plausibility;
5. temporal persistence.

Color is a cue, not a hard assumption.

### Fallback
If acceptance tests fail across representative lighting/backgrounds, train a
small permissively licensed detector such as RT-DETRv2 R18/S.

Do not add YOLO without explicit licensing review.

## Ground-plane localization

For a cube detection:
1. undistort pixel;
2. estimate floor-contact point;
3. construct camera ray;
4. transform to base;
5. intersect with z=0 floor plane;
6. return `(x,y,quality)`.

Fixed cube size is a cross-check.

Long-range coordinates are coarse. Approach always reacquires after short
motions.

## Pad localization

Treat the pad as one known planar textured landmark.

Baseline:
- ORB/SIFT-like features;
- feature matching;
- RANSAC homography;
- known pad corners/dimensions;
- derive visible pad polygon and canonical drop-side approach error.

Do not rely only on color or full-rectangle visibility.

If classical matching fails under tested lighting/perspective, evaluate a
compact learned matcher subject to license review.

## Tracking

Tiny object count: use lightweight association/smoothing.

- selected target intent persists through brief missed detections;
- stale map state does not drive open-loop motion;
- reacquire after major base rotation.

## Manipulation handoff

Perception produces:

```text
TargetObservation
  target_id
  timestamp_ns
  frame=base
  x_m
  y_m
  quality
```

Readiness is empirical:

```text
ManipulationReadiness
  ready
  expected_success
  region_id
  reason
```

Arm reach alone is not the criterion.

## RelateAnything

Not on the first control path.

It consumes image regions and predicts semantic relations; this demo needs
metric facts such as target position, target-in-pad, and target-in-manipulation
region. Geometry is the more direct source of truth.

Revisit later as an optional Cake-loadable semantic capability if useful.
