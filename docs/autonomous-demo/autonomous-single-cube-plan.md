<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Autonomous single-cube mission: architecture and plan

**Version:** 1.0
**Date:** 2026-09-19
**Status:** active architecture and plan for the flagship demo. Design only:
this document implements nothing and authorizes no actuation.

This document describes what is being built. It is deliberately independent
of any particular robot pose, shell session, in-flight calibration experiment
or transient failure. Session-level findings belong in the dated records
(`physical-pass-2026-09-18.md`, `decision-log.md`, local session notes); this
document changes only when the architecture or the plan changes.

It is grounded in what was physically learned through 2026-09-18 and
2026-09-19 and in the physical primitives this repository has already proven.
The explicit non-goals in section 13 are part of the architecture, not an
appendix: read them before proposing work.

## 1. Purpose, claim and scope

The flagship claim is unchanged (`two-cube-mission-design.md`):

> Cake can safely change the capabilities available to a running physical
> system while its mission remains alive.

Per `AGENTS.md`, the demo claim is runtime capability evolution, not general
autonomous robotics. What changes with this plan is the robotics underneath
that claim.
The 2026-09-18 physical pass proved one hand-staged canonical geometry with
operator-confirmed physical judgments. The target is now a mission that
perceives, measures, plans and verifies for itself across a bounded, measured
envelope of starting configurations, so that the capability transition
happens inside a mission that is autonomous in a defensible sense.

"Random starting position" always means "inside the supported envelope" of
section 10. No statement about this system may be stronger than the recorded
evidence for it. A two-cube mission comes only after the single-cube mission
is general and reliable.

## 2. Relationship to earlier documents

- `delivery-plan.md` (v0.3): its two-cube first filmable sequence, its
  SmolVLA-critical manipulation track, its day-by-day schedule and its
  estimate are superseded by this document as the active plan. Its
  development/publication workflow, compute topology and scope-control rule
  stand.
- `two-cube-mission-design.md`: deferred, not discarded. One mission identity
  spanning several target cycles remains the intended design for a later
  two-cube production.
- `manipulation-vla.md`: SmolVLA is parked as the critical-path manipulation
  controller (section 12). Its dataset, privacy and inference-safety rules
  stand for any later learned-policy work.
- `perception-localization.md`: frames, ground-plane localization, the pad as
  a known planar landmark and the licensing rule stand. Its camera-role table
  is replaced by section 6, and its detector baseline is replaced by learned
  recognition, consistent with the decision-log entry of 2026-09-15 that found
  fixed color segmentation unsuitable as the production detector.
- `autonomy-motion.md`: the mission executive is extended by section 4 and
  `mobile_approach` generalizes to signed SE(2) servoing. Its stable motion
  safety gateway section stands unchanged.
- `stable-motion-gateway-contract.md`, `cake-runtime-contract.md`,
  `mobile-approach-spec.md`, `measurement-experiment-spec.md`,
  `public-repo-security.md` and `publication-gate.md` stand unchanged.
- `physical-pass-2026-09-18.md` remains the record of the proven canonical
  chain and its reproduction checklist. That chain is the reference
  implementation this plan generalizes.

## 3. Mission objective

From a supported but non-canonical starting configuration, the robot must:

1. autonomously inspect the environment;
2. locate both the collection pad and the yellow 25.4 mm cube;
3. estimate their metric positions and orientations relative to the robot;
4. understand which regions have and have not been observed;
5. establish the pad as a persistent mission/world reference;
6. save the robot's initial pose relative to the pad;
7. determine whether the cube and the pad are reachable without moving the
   base;
8. choose the minimum-motion feasible strategy;
9. if necessary, reposition toward the cube;
10. hand off from global/environmental perception to near-field wrist
    perception;
11. align to and grasp the cube;
12. verify physically that the object is actually secured;
13. move to a high, clear carry configuration;
14. decide whether placement is possible from the current base pose;
15. if necessary, reposition relative to the pad;
16. place the cube at a safe point INSIDE the actual pad polygon;
17. verify the cube is actually inside the pad;
18. return the robot to its saved initial pose relative to the pad;
19. finish the mission.

The planner avoids locomotion whenever arm-only manipulation is feasible.

The easiest valid case:

```text
cube just outside pad
+ both reachable from the current base pose
= no base motion at all
```

The harder valid case may require:

```text
base -> pickup stance
     -> grasp
base -> placement stance
     -> place
base -> original start pose
```

## 4. Canonical architecture

```text
MISSION START
    |
    v
ACTIVE WORLD SCAN
    elevated/concentric wrist scan
    + front-camera supplementation
    |
    v
METRIC WORLD MODEL
    cube: XY / range / bearing
    pad:  XY / range / yaw / polygon
    coverage + uncertainty
    |
    v
ESTABLISH PAD_FRAME
SAVE INITIAL BASE POSE IN PAD_FRAME
    |
    v
PLAN MINIMUM-MOTION TASK
    |
    +---- arm-only
    +---- rotate-only
    +---- one relocation
    +---- two relocations
    |
    v
APPROACH CUBE IF NEEDED
    |
    v
FRONT -> WRIST HANDOFF
    |
    v
FINE PICKUP ALIGN
    |
    v
GRASP -> VERIFY -> CARRY HIGH
    |
    v
REACQUIRE / LOCALIZE PAD
    |
    v
PLAN DROP TARGET / STANCE
    |
    +-------------------+-------------------+
    |                                       |
reachable from here                   move required
    |                                       |
    |                                       v
    |                           CLOSED-LOOP BASE SERVO
    |                                       |
    +-------------------+-------------------+
                        |
                        v
METRIC ARM PLACEMENT
    |
    v
VERIFY CUBE IN PAD
    |
    v
RETURN TO SAVED START POSE
    |
    v
MISSION DONE
```

Every transition in this diagram is gated by a physical predicate
(section 14). Software progress alone never advances the mission.

## 5. Frames and conventions

- `BASE_FRAME`: right-handed, +x forward, +y left, +z up
  (`perception-localization.md`). The base velocity convention is physically
  confirmed: `+x.vel` moves the robot forward, `+y.vel` moves it left
  (decision log, 2026-09-15). The day-1 measurements placed the origin at the
  floor point below the front camera optical center; M1 freezes the origin
  together with the camera extrinsics.
- Support plane: the plane the robot drives on and on which the cube and the
  pad rest, expressed in `BASE_FRAME` by calibration, never assumed.
- `PAD_FRAME`: origin at the pad center, axes aligned with the pad edges
  (proposed: x along the long edge), z up. A bare rectangle leaves a
  180-degree yaw ambiguity; the pad's printed branding resolves it where
  visible, otherwise the ambiguity is carried explicitly and resolved
  consistently between mission start and return. It never affects
  containment tests.
- SE(2) pose: `(x_m, y_m, theta_rad)`, theta positive counter-clockwise seen
  from above. All three components are signed.
- `T_pad_base`: the pose of `BASE_FRAME` expressed in `PAD_FRAME`.
  `T_pad_base_start` is saved once, after the first confident pad
  localization of the mission.
- Units: SI internally; degrees only in operator-facing output. At the
  LeRobot boundary `theta.vel` is degrees per second; that conversion already
  lives in `autonomy/motion/lerobot_transport.py`.
- Every geometry API states its frame and units (existing rule).

## 6. Camera architecture

The real camera limitations are part of the design. Provenance labels used
in this section: "recorded" means present in tracked repository documents or
code; "local record" means present in dated local (ignored) session or
measurement records; "operator-reported" means stated by the operator and
still to be measured.

### Wrist / gripper camera

- eye-in-hand, rigidly mounted to the gripper;
- useful for elevated local scanning and for near-field manipulation;
- partially obstructed by the gripper when empty;
- more substantially obstructed while carrying the cube;
- must NOT be assumed to be a global environmental camera in all arm states;
- its pose is derived from measured arm state through FK plus a fixed
  hand-eye extrinsic, never from commanded joints;
- elevated scan poses may provide useful top-down object views;
- recorded: the Pi host scripts identify it by its stable
  `/dev/v4l/by-id/` path, because plain `/dev/videoN` names do not survive
  re-enumeration (`docs/reproduce-end-to-end.md`);
- local record (day-1 measurement session, 2026-09-15): LeRobot's stock
  LeKiwi configuration rotates this stream by 90 degrees in software, while
  a direct V4L capture is unrotated. Every calibration artifact states which
  image orientation and resolution it applies to.

### Front / body camera

- fixed relative to the base (called the body camera in earlier documents;
  LeRobot configuration key `front`);
- useful for medium and far environmental perception;
- not wide-angle;
- has a near-field blind region immediately in front of the robot, and
  therefore cannot own the last centimeters of pickup or placement;
- local record (section 11): mounted low, optical center at 0.0508 m above
  the floor, so it views the floor at a grazing angle;
- recorded: the day-1 empirical floor mapping was trusted only for
  x approx 0.30 to 1.10 m and abs(y) <= 0.40 m, with position error growing
  to 25-37 cm outside that region (decision log, 2026-09-15). That mapping
  was an empirical homography, not a calibrated camera model;
- recorded: this camera has been physically disconnected for every recorded
  physical pass (`physical-pass-2026-09-18.md`, checklist item 5), and the
  evaluation host `bin/pi/lekiwi_host_eval.py` opens only the wrist camera.
  Restoring it is milestone M1 work.

### Complementary use and time-multiplexing

The two cameras are COMPLEMENTARY: the wrist camera owns elevated scanning
and the near field; the front camera owns the medium and far field and most
of the carry phase.

Operator-reported: concurrent streaming from both cameras previously caused
USB/V4L instability on this rig. The initial design therefore evaluates
TIME-MULTIPLEXING rather than requiring concurrent streaming:

```text
WRIST SCAN
-> close wrist
-> FRONT observation
-> close front
-> WRIST manipulation
```

No architecture may assume both streams must remain open simultaneously.

Constraints on the multiplexing mechanism, to be evaluated in M1:

- switching cameras must never interrupt the motor host. Arm torque
  continuity while carrying the cube is a hard requirement, and torque is
  off between host sessions (`physical-pass-2026-09-18.md`, checklist
  item 4);
- candidate mechanisms: on-demand open/close inside the hardware host; a
  camera service separate from the motor host; direct V4L capture, which
  `tools/capture_measurement.py` already performs for camera-only work;
- M1 retires the standing invariant "the front camera stays disconnected"
  and replaces it with "at most one camera stream open at a time" until
  concurrent streaming has been validated separately;
- the world model treats observations as timestamped and asynchronous, so it
  behaves identically whether streams are multiplexed or, later, concurrent.

## 7. Active scan architecture

The first mission action is generally perception, not locomotion.

The upright/elevated wrist scan is an ACTIVE PERCEPTION primitive. It covers
the ground around the robot deliberately, from several known camera poses,
rather than with one arbitrary sweep:

- a safe elevated observation posture;
- several shoulder-pan sectors;
- multiple radial/elevation/pitch bands;
- measured arm state captured at every observation;
- camera pose obtained from FK at every observation;
- repeated object sightings fused;
- scan coverage represented explicitly.

The scan exploits the fact that top-down object recognition can be easier
than near-horizontal observation: the front camera sees the floor at a
grazing angle, the elevated wrist camera does not.

Design rules:

- every scan pose is reachable with joint and singularity margins, collision
  safe, and recorded with the gripper state (open, closed, holding);
- each observation record holds: timestamp, camera, image, measured joints,
  FK-derived camera pose, detections and their image geometry;
- coverage is kept on the support plane in `BASE_FRAME` (cells or polar
  sectors): observed or not, best view quality, time of last observation.
  The footprint of a view is its image frustum projected on the support
  plane minus the region occluded by the gripper;
- because the camera is rigid to the gripper, the gripper's own structure
  occupies an approximately fixed image region for a given gripper state.
  The observability atlas (section 11) measures that occlusion mask for the
  open, closed and holding states;
- scan poses are chosen to create useful parallax between views of the same
  ground region.

Stopping and escalation:

- if both cube and pad are found with adequate uncertainty without moving
  the base, do NOT move the base merely to "search better";
- if an object remains unobserved, use the coverage information to decide
  which sector remains unseen, and only then consider, in this order:
  1. an additional arm scan pose aimed at the unseen sector;
  2. a front-camera observation;
  3. a minimal base rotation that exposes the unseen sector;
- any base motion, including a search rotation, is mobility: it passes
  through the same capability gate and the same motion gateway as every
  other base motion;
- avoid blind fixed 360-degree base scanning.

## 8. Algorithm and responsibility map

Component names below are logical responsibilities, not frozen module names.

| Area | Logical component | Core methods | Builds on | First milestone |
|---|---|---|---|---|
| Active perception | world scan | elevated concentric wrist scan; front-camera supplementation; coverage accounting; active view selection | FK in `cartesian_lift.py`; bounded interpolation in `canonical_handoff.py`; wrist-only host | M1, M2 |
| Recognition | cube and pad recognizers | learned `yellow_cube` and `collection_pad` perception; fixed color rules diagnostic only; tolerance to normal lighting changes | decision log 2026-09-15; licensing rule | M2 |
| Camera geometry | calibration inventory | intrinsics; camera-to-base and camera-to-arm extrinsics; FK-derived wrist camera pose; fixed front-camera pose; support-plane calibration | `tools/gen_charuco_target.py`; `measurement-experiment-spec.md` | M1 |
| Cube metric localization | cube localizer | recognition; selected image geometry; pixel ray + support-plane intersection; multi-view fusion; 25.4 mm size as consistency cue; uncertainty | ground-plane method in `perception-localization.md`; `tools/floor_mapping.py` | M2 |
| Pad metric localization | pad localizer | recognition; planar geometry; branded features; known dimensions; homography / planar pose; center, yaw, polygon; uncertainty | pad as known planar landmark (decision log) | M2 |
| World model | world model | observations in `BASE_FRAME`; conversion to `PAD_FRAME`; history; confidence; coverage; saved `T_pad_base_start` | `autonomy/schemas/target_observation.schema.json` | M2 |
| Planning | task planner | enumerate feasible strategies; choose minimum-cost feasible one | mission executive in `autonomy-motion.md` | M3 |
| Reachability | reachability analysis | FK/IK; joint and singularity margins; carry clearance; camera geometry; known manipulation envelope | `cartesian_lift.py`; margins in `hybrid_pick_place.py` | M3 |
| Base pose generation | stance generator | pickup stance relative to cube; placement stance relative to pad/drop point; canonical local manipulation geometry | known-good grasp geometry | M3, M4 |
| Base control | base servo (`mobile_approach` generalized) | closed-loop signed SE(2) visual/metric servo; gateway unchanged | `mobile_approach.py`; `stable_motion_gateway.py`; `lerobot_transport.py` | M4 |
| Pickup | pickup | coarse geometry; wrist handoff; robust cube localization; fine alignment; deterministic grasp; verification lift; retention verification | grasp states of `hybrid_pick_place.py` | M5 |
| Object-secured contract | physical predicate | command complete AND proprioceptive evidence AND visual retention | recorded failure of 2026-09-18 | M5 |
| Carry | carry | high/clear carry state; mobility only when secured and clear; front camera primary | `MOBILE_READY` preconditions | M5 |
| Placement | placement | pad polygon; erosion; safe drop target; arm-versus-base optimization; forward/down Cartesian IK; release; containment check | `solve_cartesian_forward_and_z`; proven release sequence | M6 |
| Return | return to start | reacquire pad; compare with saved start pose; servo back | base servo | M6 |
| Cake | capability boundary | availability, not geometry; activate mobility only when the plan needs it; preserve rejection, activation and continuity | `capability_registry.py`; `cake-runtime-contract.md` | preserved throughout; M7 |

### 8.1 Active perception

See section 7. Active view selection means the next view is chosen from the
coverage map and the current uncertainty, not from a fixed script.

### 8.2 Recognition

- learned `yellow_cube` perception and learned `collection_pad` perception
  are the perception authority;
- fixed HSV/color rules are diagnostic/secondary only. Recorded reasons: the
  day-1 front-camera color detector reached recall 2/7 and 4/6 because the
  pale wood cubes are close in hue and saturation to the floor and the
  background (decision log, 2026-09-15); and a wrist-camera fixed color mask
  that worked under one lamp merged cube and background into a single
  frame-sized component under a different, equally normal lamp (local
  session evidence, 2026-09-18);
- perception must tolerate normal daylight, artificial and mixed light and
  reasonable shadows. Complete darkness is outside the envelope and is caught
  by the gross illumination gate, which is a sanity check and not a
  robustness mechanism;
- recognition outputs image geometry suitable for metric localization, not
  only a box: for the cube a floor-contact point or top-face geometry, for
  the pad corners, edges, keypoints or a mask that supports a planar fit;
- licensing: permissively licensed models only (the decision log names the
  RT-DETRv2 / RF-DETR class under Apache-2.0); no Ultralytics YOLO without
  an explicit license decision; training images, datasets and checkpoints
  stay local and ignored.

### 8.3 Camera geometry

- camera intrinsics for each camera, stated for the image orientation and
  resolution actually consumed;
- camera-to-base extrinsic for the fixed front camera; camera-to-arm
  (hand-eye) extrinsic for the wrist camera;
- FK-derived wrist camera pose: FK of the measured joints composed with the
  hand-eye extrinsic;
- fixed front-camera pose;
- support-plane calibration, which also tests the modeling assumption in
  `cartesian_lift.py` that the arm base Z axis is world-vertical;
- numeric results are tracked and raw calibration images stay local
  (`measurement-experiment-spec.md`);
- this replaces `body_floor_mapping_v0`, the day-1 empirical homography.

### 8.4 Cube metric localization

- object recognition;
- selected image geometry (contact point or top face, not the box center);
- pixel ray + support-plane intersection in `BASE_FRAME`;
- repeated and multi-view fusion across scan poses;
- the known 25.4 mm cube size is a consistency cue, not the sole ranging
  mechanism;
- an uncertainty estimate that combines pixel noise, FK and extrinsic error
  and plane calibration error, validated against tape-measured ground truth.

### 8.5 Pad metric localization

- recognition;
- planar geometry;
- branded visual features where useful;
- known physical dimensions (section 11);
- homography / planar pose;
- pad center, yaw and polygon in `BASE_FRAME`;
- an uncertainty estimate;
- partial views are expected: do not rely on full-rectangle visibility
  (`perception-localization.md`).

### 8.6 World model

- cube and pad observations;
- coordinates in `BASE_FRAME`;
- conversion to `PAD_FRAME` once the pad is localized;
- observation history with timestamp and source view;
- confidence and uncertainty;
- covered and uncovered sectors;
- the saved `T_pad_base_start`;
- fused cube estimates remain expressible as `TargetObservation` records so
  the base servo keeps its committed input schema: extend the schemas, do
  not fork them;
- stale map state never drives open-loop motion
  (`perception-localization.md`).

### 8.7 Planning

Enumerate feasible strategies:

- arm-only;
- rotate-only;
- one relocation, including one shared stance from which both pickup and
  placement are reachable, when feasible;
- two relocations.

Choose the minimum-cost feasible strategy. Cost is ordered: feasibility with
margins first, then the number of base motions, then base displacement and
rotation, then manipulation risk (margins to joint limits, singularities,
envelope edges and localization uncertainty). Every strategy that moves the
base includes the return to the saved start pose in its cost.

When uncertainty makes reachability ambiguous, the cheapest
information-gathering action (another scan view) is preferred over motion.
Plans are re-evaluated when observations change the world model.

### 8.8 Reachability

- FK/IK through `cartesian_lift.py`: shoulder_pan selects the vertical
  plane; shoulder_lift, elbow_flex and wrist_flex solve radius and height in
  that plane; wrist_roll is never forced;
- joint margins (`JOINT_LIMIT_MARGIN_DEG`) and singularity margins
  (`SINGULARITY_MARGIN_DEG`); a target that would cross the singularity
  margin is refused, not silently shrunk;
- carry clearance;
- useful camera geometry: a pose only counts if the camera that must observe
  the target can actually do so from it;
- known manipulation envelope: arm reach alone is not the criterion.
  Readiness is empirical and is measured with the manipulation success grid
  of `measurement-experiment-spec.md`. The known-good grasp geometry is the
  first validated point of that envelope.

### 8.9 Base pose generation

- desired pickup stance relative to the cube;
- desired placement stance relative to the pad and the selected drop point;
- prefer canonical local manipulation geometry where practical: generate the
  pickup stance that places the cube where the known-good grasp geometry
  expects it, which converts a general manipulation problem into navigation
  plus a small local alignment;
- stances respect keep-out regions: never drive onto the pad, never through
  the cube.

### 8.10 Base control

- CLOSED-LOOP signed SE(2) visual/metric servo;
- x, y and theta are all signed;
- reverse and lateral motion are ordinary corrections. The base is
  holonomic and lateral motion is physically confirmed (decision log,
  2026-09-15);
- the existing `StableMotionGateway` remains the low-level actuator and
  safety layer, unchanged: leases, hard velocity clamps, slew limits, zero
  on expiry or disconnect, autonomous-mode permission and operator stop;
- each bounded motion segment is realized with the proven refreshed-lease
  pattern, ends with an explicit zero, and is confirmed by repeated base-zero
  reads (`physical-pass-2026-09-18.md`, checklist items 13 and 14);
- the loop is stop, reobserve, correct. Termination is decided on observed
  pose error, never on elapsed time;
- timed "drive forward N seconds" motion is calibration/debug behavior, NOT
  navigation.

Today's `mobile_approach` v0 is bounded P control on x and y toward a point
handoff region with omega fixed at zero. The generalization adds signed theta
and stance (pose) goals relative to the cube or the pad.

### 8.11 Pickup

- front/environmental coarse geometry;
- wrist handoff before the cube enters the front camera's near-field blind
  region. The handoff band is an output of the supported-envelope
  measurements (section 10);
- robust cube localization in the wrist view;
- local fine alignment;
- deterministic grasp where useful (known-good geometry, deterministic
  latch);
- verification lift;
- actual object retention verification.

### 8.12 Object-secured contract

`OBJECT_SECURED` may become true only from real evidence such as:

- the grasp command completed, AND
- appropriate proprioceptive evidence, AND
- visual object retention or equivalent physical evidence (the object moves
  with the gripper through the verification lift and remains between the
  jaws).

NEVER: gripper angle alone == object secured.

Recorded reason: on 2026-09-18 an unattended canonical run reported DONE
while the cube was never picked up. Its only grasp check was that the
gripper reached its commanded position, which is true whether or not
anything is between the jaws (local session notes). Which proprioceptive
signal is informative on this gripper (closure position against an empty
close, servo load if exposed) is to be evaluated, not assumed.

Until sensor predicates are validated, the operator confirmation gates of
`hybrid_pick_place.py` remain the fallback. They are replaced predicate by
predicate, never bypassed.

### 8.13 Carry

- move to a physically valid high/clear carry state;
- base mobility only when the object is secured and end-effector clearance
  is established (the existing `MOBILE_READY` preconditions: object secured,
  clearance confirmed, arm frozen, base stationary);
- the front camera likely becomes the primary environmental sensor while the
  cube is held, because the wrist camera may be substantially occluded.

### 8.14 Placement

- localize the actual pad polygon;
- erode the polygon by the cube half-size plus a safety margin. The half-edge
  is 12.7 mm; when the cube's yaw relative to the pad is not controlled, use
  the half-diagonal (approx 18 mm). The safety margin covers pad
  localization and release uncertainty;
- select a safe interior drop target;
- determine whether the target is already arm-reachable;
- optimize arm extension versus base displacement;
- use the existing forward/down Cartesian IK where appropriate;
- release;
- visually verify the cube footprint is actually inside the pad.

### 8.15 Return

- reacquire and refine the pad pose;
- compare the current base pose with the saved `T_pad_base_start`;
- navigate back with the visual/metric servo;
- do not merely replay inverse timed movements.

### 8.16 Cake

- Cake controls capability availability, not robotics geometry;
- activate mobility ONLY when the selected task plan genuinely requires
  mobility;
- if an arm-only task is feasible, mobility need not be activated;
- preserve: invalid/unadmitted candidate rejection, valid activation, same
  live mission continuity;
- the stable safety layer stays outside the replaceable capability: Cake
  admission and lifecycle never weaken leases, bounds or stops (`AGENTS.md`).

## 9. Distance estimation is part of recognition

Recognition must not stop at "cube detected at pixel x,y".

The initial observation produces, as early as possible:

```text
cube:
  distance
  bearing
  x/y in base frame
  uncertainty
  supporting observations

pad:
  distance
  bearing
  center x/y
  yaw
  polygon
  uncertainty
  supporting observations
```

Distance and bearing estimates inform:

- reachability;
- scan continuation;
- camera handoff;
- base movement necessity;
- planner cost.

The multi-view elevated wrist scan deliberately creates useful parallax where
possible.

## 10. Supported envelope

"Random starting position" is a BOUNDED SUPPORTED ENVELOPE, not an undefined
claim. These ranges are to be MEASURED, not guessed.

| Quantity | Status | Existing data point |
|---|---|---|
| supported base-start area and yaw | to be measured | none |
| supported cube search area | to be measured | none |
| supported pad placement/search area | to be measured | none |
| normal lighting envelope: daylight, artificial, mixed, reasonable shadows | to be measured | fixed color rules failed across two normal lamps (section 8.2) |
| not complete darkness | gate exists | lights-off frames are rejected by the gross illumination gate |
| front-camera useful range | to be measured with a calibrated model | v0 floor mapping trusted for x approx 0.30 to 1.10 m, abs(y) <= 0.40 m (decision log, 2026-09-15) |
| wrist-camera useful range per scan pose | to be measured | none |
| front near-field blind zone | to be measured | nothing nearer than approx 0.30 m was trusted in the v0 mapping |
| wrist occlusion envelope (open, closed, holding) | to be measured | known qualitatively only |
| camera handoff bands | derived from the rows above | none |

## 11. Observability atlas

Before assuming what either camera can recognize, build an empirical
dual-camera observability atlas. Measure cube and pad appearance versus:

- camera;
- range;
- bearing;
- pad yaw;
- arm observation pose;
- gripper open/closed;
- cube held/not held;
- lighting;
- clipping/occlusion.

Do not assume physical dimensions alone imply visual detectability.

The atlas is produced with a structured capture tool that records, per
capture, the camera, the measured joints, the lighting label and the
tape-measured ground truth. Raw captures stay in ignored local directories;
only numeric aggregates are tracked (`measurement-experiment-spec.md`,
`AGENTS.md`).

Known physical constants:

| Constant | Recorded value | Provenance |
|---|---|---|
| cube edge | 0.0254 m (25.4 mm) | day-1 measurement session records, 2026-09-15 |
| pad width x height | 0.415 m x 0.295 m | day-1 measurement session records, 2026-09-15 |
| front camera optical-center height | 0.0508 m | day-1 measurement session records, 2026-09-15 |

These values were recorded by hand in the local day-1 measurement session
records. They have not yet been promoted to the tracked
`autonomy/config/geometry.yaml` that `measurement-experiment-spec.md`
specifies; M1 re-measures and promotes them.

## 12. Reuse map

### KEEP / REUSE

| Item | Where it lives | Why it stays |
|---|---|---|
| FK / Cartesian IK, `solve_cartesian_z_lift`, `solve_cartesian_forward_and_z` | `autonomy/manipulation/cartesian_lift.py` | physically validated; steps are always solved from a fresh measured pose and re-measured |
| bounded joint-space interpolation | `autonomy/manipulation/canonical_handoff.py` | smooth, bounded per-tick motion from any start pose |
| manipulation/base boundary, wrist-roll freeze handling | `autonomy/manipulation/action_adapter.py` | arm commands can never move the base; wrist_roll is held, never forced |
| `StableMotionGateway`, refreshed-lease semantics, explicit base-zero confirmation | `autonomy/motion/stable_motion_gateway.py`; `stable-motion-gateway-contract.md` | the safety layer under every base motion |
| `CapabilityRegistry` / Cake boundary | `autonomy/motion/capability_registry.py`; `cake-runtime-contract.md` | rejection, activation and continuity proven on 2026-09-18 |
| Pi eval host | `bin/pi/lekiwi_host_eval.py` | noninteractive, calibrate-from-file-or-refuse host |
| stable camera by-id approach | Pi host scripts | survives device renumbering |
| pickup diagnostics / persistent evidence infrastructure | `autonomy/manipulation/pickup_diagnostics.py`; `tools/capture_measurement.py` | every physical observation can be reconstructed afterwards |
| gross illumination sanity gate | first implemented in a calibration harness; to be promoted into the shared perception preflight | refuses motion on truly unusable/dark frames; threshold derived from recorded lit and lights-off frames |
| known-good physical grasp geometry | `KNOWN_GOOD_GRASP_POSE_DEG` in `autonomy/manipulation/hybrid_pick_place.py` | the canonical local manipulation target |
| gripper latch/release knowledge | `GRIPPER_LATCH`, `GRIPPER_OPEN` in `hybrid_pick_place.py` | deterministic, physically validated |
| carry/clearance lessons | `physical-pass-2026-09-18.md`, checklist items 8 and 9 | fresh-solved increments; mobility preconditions |
| deterministic placement mechanics | `physical-pass-2026-09-18.md`, checklist items 15 and 16 | fallback and reference for metric placement |

### REFACTOR / GENERALIZE

| From | To |
|---|---|
| `mobile_approach` (x/y P control, omega fixed at zero, timed sustained move) | target-relative signed SE(2) navigation |
| hybrid pick-place orchestration (fixed poses, operator gates) | world-model-driven execution with sensor predicates |
| fixed canonical placement | metric pad-target placement |
| fixed pickup | coarse navigation + wrist local alignment |

### PARK

| Item | Reason |
|---|---|
| SmolVLA as critical-path manipulation controller | in physical trials the checkpoint did not reliably descend into grasp geometry, close the gripper or reach the handoff pose (local session notes) |
| the old short homogeneous 16-episode dataset as a generalization solution | short and homogeneous: too little variation to support generalization (operator assessment) |
| fixed HSV cube segmentation as authoritative perception | recorded failures listed in section 8.2 |

## 13. Explicit non-goals and anti-patterns

NOT:

- hardcoded timed-forward motion as navigation;
- fixed-HSV perception as the perception authority;
- fixed-room-light calibration;
- arbitrary fixed cube pixel targets as global geometry;
- gripper position == object secured;
- software state progression without physical predicates;
- VLA as the flagship critical path;
- a two-cube mission before a generalized, reliable single-cube mission;
- forcing base motion when arm-only is feasible;
- assuming the front camera handles the near field;
- assuming the wrist camera remains useful while carrying the cube;
- assuming simultaneous dual-camera streaming is mandatory;
- tuning one physical sample until it passes and immediately promoting it to
  a general primitive.

Also unchanged from the earlier documents: no SLAM, no generic VLM
navigation and no learned depth on the critical path.

## 14. Engineering workflow

Every capability follows a strict progression:

```text
EVIDENCE/DATASET
-> OFFLINE VALIDATION
-> READ-ONLY LIVE VALIDATION
-> SINGLE-VARIABLE PHYSICAL CALIBRATION
-> SUBTASK VALIDATION
-> REPEATED VARIED TRIALS
-> INTEGRATION
-> FREEZE
```

Rule:

> A semantic state can only become true if an actual sensor observation or
> deterministic physical invariant establishes it.

Examples:

```text
GRIPPER_AT_LATCH_POSITION  !=  OBJECT_SECURED
MOTION_COMMAND_COMPLETE    !=  ARRIVED_AT_TARGET
GRIPPER_OPEN               !=  CUBE_INSIDE_PAD
```

Standing practice:

- no hardware start, torque enable or motor command without the operator
  explicitly authorizing the specific test (`AGENTS.md`);
- physical calibration changes one variable at a time and judges success on
  measured motion, not commanded motion;
- a motion is refused when its precondition observation is invalid;
- every physical step persists before/after evidence in ignored local
  directories.

## 15. Milestones

### M1: camera and observability foundation

- restore the front camera;
- stable device identity for both cameras;
- front/wrist time-multiplex validation that never interrupts the motor
  host;
- camera calibration inventory;
- elevated wrist scan primitive;
- structured capture tool;
- observability atlas.

Exit evidence: each camera is stable on its own across repeated open/close
cycles; numeric calibration results are tracked; the scan primitive runs a
defined pose set safely and records measured joints per view; atlas
aggregates exist.

### M2: robust perception and metric world scan

- cube recognition robust to normal lighting variation;
- pad recognition;
- cube metric localization;
- pad metric localization;
- observation fusion;
- coverage;
- uncertainty;
- validate distances against tape-measured ground truth.

BASE DOES NOT MOVE.

Exit evidence: median and p90 position error by range bucket, detection
failure rate and pad pose error, across the lighting envelope.

### M3: planning

- reachability analysis;
- candidate strategy enumeration;
- minimum-motion plan selection;
- no physical movement initially.

Exit evidence: for scanned scenes the planner's strategy, stances and
reachability verdicts match hand-verified cases.

### M4: closed-loop base navigation

- compute the pickup stance;
- navigate to it using signed SE(2);
- stop, reobserve, correct;
- test cube approach and pad docking without manipulation.

Exit evidence: final stance error measured against ground truth; gateway
invariants intact in every run.

### M5: general pickup

- front-to-wrist handoff;
- near-field alignment;
- grasp;
- visual/proprioceptive retention;
- carry high;
- varied pickup positions.

Exit evidence: `OBJECT_SECURED` is never true without a held cube, across
varied pickup positions.

### M6: general pad placement and return

- safe drop region;
- arm-versus-base optimization;
- placement;
- actual containment verification;
- return to the saved pad-relative start pose.

Exit evidence: containment verified visually per run; return error measured
in `PAD_FRAME`.

### M7: Cake integration and reliability/speed

- capability activation only when the planner requires mobility;
- repeated varied trials;
- failure handling;
- cycle-time optimization.

Exit evidence: invalid/unadmitted candidate rejection, valid activation and
same live mission continuity hold inside planner-driven missions.

## 16. Acceptance philosophy

Success is not one canonical pass. A varied scene matrix comes before any
reliability claim. A representative matrix eventually includes:

- no-base-motion cases;
- cube-approach cases;
- cube-approach + pad-relocation cases;
- different robot initial yaw;
- different normal lighting conditions;
- different cube/pad relative geometry.

Target reliability is initially approximately >= 90% over a meaningful
sample before any strong robustness claim, with exact criteria refined after
the supported envelope is measured. Failures are classified, not discarded.

## 16a. Next increment: the mission loop over every block off the pad (not built)

The operator's pad policy of 2026-09-29 (`decision-log.md`) builds the single
block first; this is the increment after it, not built in that change:

1. PLACE one block (the mission run as it stands: nearest feasible block off
   the pad, PLACED by the release agreement).
2. SCAN MODE: back at the low scan pose, the scene scan looks for blocks off
   the pad only; blocks on the pad are not looked at.
3. NEXT BLOCK: the nearest feasible block off the pad is the next target;
   one not placeable from here is named with its limit and left.
4. Repeat until the scan finds no block off the pad, then end the mission
   PLACED ALL, or name every block left and why.
5. Generalize to many blocks distributed across the room at random places: the
   scan covers the room, not only the pad's surroundings, and the plan says how
   the base reaches each block before the pickup.

What has to change first, each measured before it is claimed: the cube listing
stops at three verified blocks off the pad per frame (`cube_candidates.MAX_PASSES`;
passes on blocks already on the pad are free up to nine) and refuses a frame it
did not run out of; SEARCH from the scene refuses more than
one block off the pad (ruling 7 of 2026-09-26); ALIGN approaches only when the
pregrasp view verifies one block; and the base has no approach beyond the
bounded advance toward the pad. In the loop the robot's own earlier blocks sit
where it releases (every recorded release lies near pad y -100 mm), so a block
on the pad at the release is the common case, not the edge case: under ruling
(ii) of 2026-09-29 PLACED credits any block on the pad near the release, so a
run whose own block is not on the pad at EXIT for any reason after
OBJECT_SECURED, given an admitted release (it left the pad after the release,
it was lost between OBJECT_SECURED and the release, or it stayed in the opened
jaws), beside an earlier block is a false PLACED (decision-log.md, 2026-09-29,
the ACCEPTED RESIDUAL). The RELEASE_ADMISSION does not observe the held block.
The loop needs its own check of the held and the released block before it
relies on PLACED.

## 17. Delivery estimate

This is a planning estimate, not a commitment. It replaces the estimate in
`delivery-plan.md`.

- initial useful SCAN_WORLD / metric perception milestone: roughly 2-4
  focused working days, if camera/USB behavior is cooperative;
- first genuinely intelligent generalized single-cube run: roughly 5-8
  focused working days;
- filmable/repeatable demo: roughly 8-12 focused working days;
- higher-confidence robustness: roughly 2-3 weeks.

Front-camera USB stability and perception/metric calibration are the largest
schedule uncertainties.

## 18. Immediate next milestone

> From a stationary robot, perform an active elevated scan and produce a
> metric world observation of the cube and pad, including distance, bearing,
> pose/polygon, uncertainty and coverage, without moving the base.

Illustrative output:

```text
WORLD SCAN

cube:
  found: true
  range_m: ...
  bearing_deg: ...
  position_base_m: [...]
  uncertainty_m: ...
  observations: ...

pad:
  found: true
  range_m: ...
  bearing_deg: ...
  center_base_m: [...]
  yaw_deg: ...
  polygon_base_m: [...]
  uncertainty: ...
  observations: ...

coverage:
  observed_sectors: [...]
  unexplored_sectors: [...]

planning preview:
  cube_arm_reachable: ...
  pad_arm_reachable: ...
  base_motion_currently_justified: ...

BASE MOTION: ZERO
```
