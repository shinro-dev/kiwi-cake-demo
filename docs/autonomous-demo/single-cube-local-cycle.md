<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Single-cube local cycle: run instructions

**Status (2026-09-20): placement with edge clearance demonstrated from three
different starts; a small demonstration of variation, not a reliability claim.**
At commit 5dd28be the `cycle` action completed three uninterrupted physical
runs (6 min 35 s to 7 min 3 s, no operator action and no agent action during a
run, no grasp retry) from cube starts 45 mm outside the pad near its centre
line, 37 mm outside and 39 mm to the left, and 39 mm outside and 60 to 70 mm to
the right. Each run: OBJECT_SECURED from the gripper stall plus a retention
score of 0.93, a base advance of 97 to 109 mm in three measured pulses with the
arm folded and held, a release admitted from the pad-relative evidence, PLACED
from the frozen perception with the whole footprint inside the pad for any yaw
with 34.4, 29.0 and 33.1 mm to spare (target 20 mm), and the base back within
2 mm of its start along its travel axis and within 3 mm sideways. The worst-case bound of the admission held in every run (by
3.1, 2.3 and 8.3 mm); the measured cube centre differed from the central
estimate by -2.9, -3.7 and +2.3 mm.

The same session also recorded two attempts that did not complete. One, on an
earlier commit, failed in the pickup: the first gripper close was short, clipped
the cube's near corner and spun it, and two bounded advances then missed; the
approach reach lead was corrected from 4 to 14 mm and every later first close
held. One was stopped at its first arm move by the unchanged base-zero guard (a
chance reading of the zero-speed-held wheels during a large pan swing); the arm
held, nothing was touched, and the same staging passed on the next launch. The
arm-only placement branch, the operator abort of the base path and the refusal
branches of the base path are covered by offline tests only.

For comparison, the first acceptance run (commit 925eec7, the same day)
admitted its release from a forward-kinematics depth of 45.1 mm where
perception then measured 21.4 mm, and left 3.4 mm of footprint clearance.

Entry point: `bin/laptop/manipulation/run_single_cube_cycle.py`, run on the
laptop against a running camera-less motor host that owns no camera and does
not drop torque on disconnect. The arm path never commands the base: every arm
command is built by the manipulation action adapter and re-checked for exact
zero. The base moves only in the placement states described below, through
the proven gateway primitive, with the arm folded and held.

## Preconditions

- The arm holds the low scan pose and the gripper measures the proven
  normal-open value (80.193, tolerance 3.0).
- `KC_PI_SSH` and `KC_WRIST_CAMERA_ID` are set (the wrist camera is opened on
  the robot computer for one frame at a time, by stable identity).
- The cube and the pad print are visible from the low scan pose.
- An operator is at the robot with the servo power switch in reach.
- At least 15 cm of free floor between the front of the base and the pad's
  near edge, and the same behind the base: a placement may advance the base
  by up to 12 cm and reverses over the same path.

## Commands

```bash
python3 bin/laptop/manipulation/run_single_cube_cycle.py --do cycle --remote-ip "$KC_PI_IP" --robot-id "$KC_ROBOT_ID"
python3 bin/laptop/manipulation/run_single_cube_cycle.py --do observe --remote-ip "$KC_PI_IP" --robot-id "$KC_ROBOT_ID"
python3 bin/laptop/manipulation/run_single_cube_cycle.py --do exit --remote-ip "$KC_PI_IP" --robot-id "$KC_ROBOT_ID"
```

Single bounded actions for supervised work: `pan:DEG`, `move:FORWARD_MM,DZ_MM`,
`grasp:FORWARD_MM,DZ_MM`, `grip:latch`, `grip:open`, `base:SIGNED_S` (one base
pulse of 0.2 to 0.8 s at 0.05 m/s from the low scan pose, + forward, - reverse,
measured against the pad), with `--holding` while a cube is held and
`--plan-only` to print a plan without commanding anything. Ctrl-C stops the
waypoints and the arm holds; in the base states it takes effect after the
running pulse (at most 0.8 s), once the base is confirmed stationary.

## States and physical predicates

`SEARCH -> PREGRASP -> ALIGN -> APPROACH -> STRADDLING -> GRASP_CLOSE ->
OBJECT_SECURED -> PLACEMENT_PLAN -> CARRY -> RELEASE_ADMISSION -> RELEASED ->
PLACED`, each printed as `[STATE]` and saved as `cycle-<UTC>.json` in the
observation session directory. When the loaded arm cannot reach the required
margin, `CARRY` is followed by `HOLD_OBSERVE -> BASE_ADVANCE / BASE_MEASURED
(repeated)` and `PLACED` by `BASE_RETURN / BASE_MEASURED (repeated) ->
BASE_RETURNED`. A state advances only on evidence:

- ALIGNED: the detected cube, propagated to grasp depth about the image nadir,
  lies inside the jaw capture window.
- OBJECT_SECURED: the gripper stalled above its empty-latch value AND the image
  between the jaws stays the same across two lifts. Never the gripper angle alone.
- RELEASE ADMITTED: pad-relative evidence, not forward kinematics. The anchor
  is the cube centre the frozen perception measured in the pad frame at
  SEARCH, i.e. the pad point under the tool at the grasp configuration. A base
  advance moves it, and that move is measured between two pad frames taken
  with the arm held. Forward kinematics contributes only the travel from the
  grasp radius to the release radius, counted for what it physically realized
  on the worst recorded run (travel / 1.6). The release is admitted when the
  worst case leaves the cube centre at least its half-diagonal (18 mm) plus
  20 mm from every pad edge, and the measured tool height is at most 15 mm.
  Otherwise the cube stays held.
- PLACED: from the low scan pose, the frozen perception finds the cube inside
  the pad with the centre at least one cube half-diagonal from every edge, so
  the whole footprint is inside for any cube yaw.

Every arm move is planned from the fresh measured pose, bound to that pose by a
digest, executed by the bounded interpolation at half speed, surrounded by
base-zero checks and followed by arrival and measured-height checks. The
singularity guard of the proven gate is kept; where its only objection is the
elbow band around zero, the same bound is applied at the arm's real
straight-arm angle, on the target and on every sample of the interpolated path.

## Placement: arm-only first, the base when reach is short

`PLACEMENT_PLAN` prefers an arm-only carry where the loaded reach (about 95 mm
beyond the proven grasp range) achieves the required margin; in practice that
is a cube that starts on the pad edge or inside it. Otherwise:

1. `HOLD_OBSERVE`: the arm folds to the low scan pose with the cube held. The
   pad frame cannot be obtained at carry height (the wrist frame shows one
   logo row, which does not fix a plane), and it stays valid from the low pose
   with the cube in the jaws.
2. `BASE_ADVANCE`: bounded forward pulses of the proven primitive
   (`MobileApproachController -> StableMotionGateway -> LeKiwiTransport`,
   0.05 m/s, 0.2 to 0.8 s, at most 4 pulses and 120 mm in total). The first
   pulse covers at most half the need and measures the rate. A pulse is
   refused unless the arm measures the saved low scan pose, the gripper
   measures its phase and the base is confirmed stationary.
3. `BASE_MEASURED`: after every pulse the primitive's stop confirmation, the
   executor's own base-zero reads and an arm-drift check (0.6 deg) must hold;
   then the pad is observed again and the displacement is measured. Two frames
   that disagree on the shape of a 50 mm cross around the anchor, a
   displacement off the expected axis, against the commanded direction, or
   larger than the pulse can have produced, are not a measurement: the cycle
   stops with the cube held.
4. The cube is lowered at about the grasp radius (so the travel that forward
   kinematics has to vouch for is a few millimetres), released after
   `RELEASE_ADMISSION`, verified by the unchanged PLACED predicate, and the
   base reverses to its start by the same measured pulses. The next pickup
   needs the base where the cycle started: the pickup window is 0 to 60 mm
   beyond the proven grasp range.

Placement feasibility is decided at SEARCH, before anything is picked up.

## Known limits

- The image constants (nadir column, capture window, low-pose scales) come
  from one scene and one camera mount.
- The zero-shot detector does not fire when the cube fills the frame (below
  about 15 mm above the grasp height).
- Depth inside the pad computed from forward kinematics overstates the real
  depth at long reach; it no longer admits a release, and only the perception
  check decides PLACED. The 1 / 1.6 discount rests on two runs.
- The base axis is measured by the first pulse, not calibrated; yaw during a
  pulse is only seen as a sideways component. The base return corrects along
  the travel axis only; a sideways residual is reported, not corrected.
- The holding band of the gripper includes its empty-latch value, so a cube
  lost while the base moves would only be noticed by the final PLACED check.
- The admission's worst-case bound held with 2.3 to 8.3 mm to spare on three
  runs; the 6 mm anchor uncertainty is doing real work and rests on thin
  evidence: the same placed cube read 51.1 mm inside the edge from the advanced
  base and 45.9 mm from the returned base (another run's two readings agreed
  within 0.3 mm), so the estimate varies by about 5 mm with the viewpoint and
  the clearances above carry that much uncertainty. Sideways, one cube landed
  20 mm from the anchor's x (the low-pose
  x estimate of an obliquely seen cube varied by 10 mm between observations);
  the pad's side edges are far away in the supported scene, so this never bound.
- Until 2026-09-27 the base-zero guard could stop a run by chance when a large
  arm swing shook the zero-speed-held wheels: two wheels reading one velocity
  count each at the same instant fell outside its per-axis band. It stopped run
  console-20260927T142146Z-m6ed2dd9 holding the block. Rest is now judged per
  wheel (no wheel above one count, `autonomy/manipulation/base_rest.py`), at
  every site that judges the base at rest; see the decision log of that date.
- Nothing senses the clearance between the base and the pad: it rests on the
  120 mm total bound and the operator-confirmed free floor.
- The lift joint cannot raise the fully extended arm: the retreat goes back
  first, then uses the exit path.
