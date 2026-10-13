<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# The ALIGN failure of 2026-09-22, diagnosed from retained evidence

Run `console-20260922T194137Z-gellh5f4`, outcome `failed`, exit 5, reason code
`align_cube_not_detected`. This document replaces the hypotheses in
`console-reliability-handoff-2026-09-22.md` section 2 with a cause established offline. Nothing
here required moving the robot. Everything here is a RECORDED FACT with the file it came from,
or is labelled otherwise.

## 1. The cause

The cube was in the decision frame. It was proposed by the detector at the highest score of the
whole run. It was thrown away by a constant apparent-size ceiling that it exceeded by **0.31
pixels**.

`autonomy/perception/cube_recognizer.py:63`:

```
"box_side_fraction_max": {False: 0.30, True: 0.90},  # of image width; key: rotated host frame
```

The wrist camera runs the unrotated path (`image_orientation: direct-v4l-unrotated` in every
observation), so the ceiling is `0.30` of image width. On a 1280 px image that is **384.0 px**.

In the decision frame `obs-20260922T194245Z-wrist-0001` the detector proposed `'a yellow cube'`
at score **0.664**, box `[427.99, 1.73, 787.12, 386.04]`. Its longest side is **384.31 px**.

`cube_recognizer.verify` returned exactly one reason:

```
box side 0.30 of image width: too large for the cube on this image path
```

Every other criterion passed, with margin:

| criterion | limit | measured |
|---|---|---|
| mask area | >= 150 px | 92732 px |
| solidity | >= 0.90 | 0.958 |
| polygon corners | 4 to 6 | 5 |
| polygon_iou (straight sides) | >= 0.80 | 0.924 |
| box_iou (fills the box) | >= 0.55 | 0.917 |
| aspect ratio | <= 2.0 (unclipped only) | 1.208 |
| **box side fraction** | **<= 0.30** | **0.30024** |

The frame itself shows the mission cube centred, unoccluded, well lit, with both gripper jaws in
view. Illumination was recorded valid (`v_mean` 121.7, `dark_fraction` 0.0,
`saturated_fraction` 0.0), the arm was recorded static (`state_image_association:
STATIC_CONFIRMED`, `joint_stability_max_delta_deg: 0.0`), and the camera was the same physical
device as every other frame (`identity_fingerprint: 2c55e4277303`).

## 2. How this was established

`tools/static_world_observation.py` already exists to read pad and cube from saved frames. The
same perception code was run offline over the four retained frames of the run, with
`cube_recognizer.verify` wrapped to record its inputs and its per-criterion outcome. The
reproduction is byte-identical to the retained sidecars: the same detections, the same scores,
the same note strings, on all four frames.

The environment matches the run manifest: source revision `c0be68e` (the perception sources are
unchanged since), model `google/owlv2-base-patch16-ensemble`, numpy 2.2.6, torch 2.7.1,
transformers 5.5.4, OpenCV 4.12.

## 3. What this refutes

Each of these was a labelled hypothesis in the handoff, and each is now false or irrelevant.

- **"The cube left the camera's view during the descent."** False. The cube is in the frame, and
  the detector scored it 0.664, higher than in any other frame of the run (0.512, 0.522, 0.492).
- **"The pad fit was unavailable, so the approach rested on coarse geometry alone."** True as a
  statement, but not the cause of this failure. The pad fit was already unavailable one frame
  earlier, at `obs-20260922T194213Z-wrist-0002`, and the cube verified there at confidence 0.931.
  The missing pad fit is a real gap in what the run could measure. It is not what produced
  `align_cube_not_detected`.
- **"Lighting, or the tool pitch of 64.7 degrees."** Not supported. Illumination was recorded
  valid and comparable to the proven run (121.7 against 128.4). The pitch difference between the
  two runs at the decision pose is 0.4 degrees.
- **Streaming, the preview, and the camera service.** Not involved. The failure is a decision
  made from a retained frame that was captured, stored and read correctly.
- **`already_placed=false`.** A normal branch condition, as expected. It is not a failure.

## 4. The margin was never there

The ceiling does not sit outside the cycle's operating range. It sits inside it.

In the proven run `console-20260922T180557Z-5xeetlmj`, correlating each retained observation
against that run's own state record (`cycle-20260922T181223Z.json`):

- 20 observations verified a cube.
- From APPROACH through RELEASE_ADMISSION, 11 consecutive verified detections sit between **369
  and 383 px** against the 384.0 px ceiling. The closest clears it by **1.0 px**.
- **Five frames of the proven run already reported `cube detected: NO` for this same reason**,
  at 426, 393, 387, 388 and 465 px (APPROACH, GRASP_CLOSE, BASE_MEASURED, RELEASE_ADMISSION,
  RELEASED). Each of those five frames shows the real cube held in the jaws. The proven run
  survived only because none of those five was a frame a decision depended on.

The proven run is therefore not evidence that this configuration works. It is evidence that it
passed by one pixel.

## 5. The difference between the two runs, exactly

Both runs reached the same ALIGN descent and captured a decision frame at the same descent
height. The observation records name the pose:

| | proven run | failed run |
|---|---|---|
| decision frame | `obs-20260922T180653Z-wrist-0001` | `obs-20260922T194245Z-wrist-0001` |
| `scan.pose_id` | `pregrasp-descend-fwd3-dz43` | `pregrasp-descend-fwd11-dz43` |
| tool vs the proven grasp | -8.8 mm out / +38.6 mm up | -2.0 mm out / +37.6 mm up |
| PREGRASP coarse `forward_mm` | 0.0 | 10.7 |
| cube box longest side | 338 px | 384.31 px |
| verdict | verified, cycle continued to APPROACH | rejected, cycle failed |

Same descent (`dz43`), same height to within 1.0 mm, same camera, same illumination band. The
difference is the forward advance the PREGRASP step chose: **3 mm against 11 mm**. Eight
millimetres closer to the cube grew its longest box side by 46 px and carried it across the
ceiling.

## 6. What the ceiling is worth, measured

The ceiling is not useless, it is mis-placed. Across every retained console run on the unrotated
path (67 cube proposals over five runs), the two populations separate cleanly:

- **True cubes at working distance**: up to **0.3633** (465 px). Confirmed by eye on every frame
  the ceiling rejected inside the proven run: each one is the mission cube in the jaws.
- **Verified false positives**: **0.4352** (557 px), **0.5617** (719 px), **0.8500** (1088 px).
  These are boxes around the pad's printed badge, not the cube. The ceiling is what rejects them,
  and it should keep rejecting them.

So the true-cube population ends at 0.3633 and the false-positive population begins at 0.4352.
The ceiling is at 0.30, which is **inside the true-cube population**, about 21 percent below
where the two separate.

The same module gets this right on the other image path, and records the method in a comment at
`cube_recognizer.py:64`:

```
# Host close-ups: 31 real cubes span 0.54-0.74, the pad's orange badge (a verified false positive) 0.36-0.43.
```

with the window set to `[0.48, 0.90]`: floor below the real range, ceiling above it, false
positives outside. The unrotated window `[0.0, 0.30]` never got that treatment. The module says
so itself at `cube_recognizer.py:53`: the limits "were set after looking at 30 real project
images (2026-09-19)" and "are not validated on any image outside that set." Those 30 images were
SEARCH-range and PREGRASP-range views, where the cube spans 0.08 to 0.21 of image width. They
contained no ALIGN-range or carry-range close-up, which is where the cycle actually makes its
decisions.

## 7. The correction

**STATUS: cause established; correction checked offline; physical validation pending.**

**The exact change.** `autonomy/perception/cube_recognizer.py`, `LIMITS["box_side_fraction_max"]`:

| | old | new |
|---|---|---|
| unrotated path, key `False` | **0.30** | **0.40** |
| rotated host path, key `True` | 0.90 | 0.90, untouched |

**The normalization rule, which is not obvious.** The quantity gated is

```
side_fraction = max(box_width_px, box_height_px) / image_WIDTH_px
```

The LONGER of the two box sides is divided by the image **width** on both axes. On the 1280x720
wrist frame a vertical extent is therefore normalized by 1280 and not by 720, so the bound is not
a fraction of the frame in the direction it is measured. The floor
`box_side_fraction_min[False] = 0.0` is unchanged, so nothing new is excluded at SEARCH range.

**The magnitude, stated plainly.** At 1280 px the bound moves from **384.0 px to 512.0 px**: plus
0.10 of image width, **plus 128 px, a 33 percent relative increase**. The rejection that motivated
this was a miss of **0.31 px**. The change is about four hundred times the miss, and the miss is
NOT its justification. The justification is the population separation in section 6: the old bound
sat inside the true-cube population, and no value that merely admits the failed frame would fix
that. A bound of 0.3003 would have passed that one frame and left the defect in place.

**The affected configuration.** Only frames recognized with `image_is_rotated_host_frame=False`,
which is the direct-V4L unrotated path (`camera.image_orientation: direct-v4l-unrotated` in every
observation record). In the deployed demo that is the wrist camera at 1280x720 through
`capture_provider: camera_service`, with `google/owlv2-base-patch16-ensemble` proposing. Frames
delivered rotated by the robot host take the `True` window and are not affected.

**Where the bound now sits.** True cubes observed reach 0.3633 (465 px); the new bound clears
that by 47 px. Verified false positives begin at 0.4352 (557 px); the new bound sits 45 px below
that. It is near the middle of the measured gap, by construction.

### 7.1 What the offline check is, and what it is not

**The 59 retained frames are the CALIBRATION CORPUS. They are not held-out evidence.** The bound
was chosen from the two populations those same frames define, so re-running them and finding no
regression is an internal consistency check: it confirms the bound was placed where the
measurement said, and it confirms the change is confined to frames near the old bound. It is not
an independent validation and must never be cited as one. **There is no held-out set.** Producing
one means new frames from a run this bound did not come from.

What the check does establish, within that limit: over all 59 retained console frames, no cube
that was detected before is lost, ten that were wrongly reported absent are recovered, each
recovered box was confirmed by eye to be the mission cube, and the three recorded false positives
(0.4352, 0.5617, 0.8500) stay rejected.

Everything else that must be said:

- The separation is **thin**: 0.07 of image width, about 92 px, over five runs on one cube, one
  pad and one lighting setup. It is not a validated margin.
- It changes a gate on the physical cycle, so it **cannot be closed without an attended physical
  run**.
- A ceiling expressed as a constant fraction of image width is a proxy for "too big to be a 25.4
  mm cube", and that question only has an answer relative to the camera's working distance. A
  range-aware limit would be the durable fix. It is a larger change to a module that is
  deliberately metric-free, and it is not proposed here.

### 7.2 What the next Start would actually execute

Read from the running runtime on 2026-09-23, not assumed. The daemon (the one serving 8770) has
its source root at this worktree and builds a fresh content-addressed bundle from the checkout at
each spawn, so the next Start carries the corrected recognizer:

**The detector identity that matters is the recognizer's own file hash**, because the bundle
digest covers all 61 bundled files and changes whenever any of them does:

| | |
|---|---|
| `autonomy/perception/cube_recognizer.py` sha256 | **`38542d4027d56710c94e7ad412601269cc4163a1c281e5be939b76c24a5a3cbc`** |
| effective bound in that file | `box_side_fraction_max[False] = 0.40` |
| capture provider | `camera_service`, wrist 1280x720 |
| model | `google/owlv2-base-patch16-ensemble`, revision null |
| bundle digest the failed run used | `35aa499d7877a30609630dbb9d81e5442aaa0b7cacee854d7f3f86307670ca11` |

That recognizer hash is the identity of the detector configuration awaiting the attended test. It
is what the run manifest will name under the bundle's own file list.

The bundle digest of the NEXT Start is not a constant and must be read rather than quoted. At the
revision that introduced this section it was
`3afa37eeab9494816fd74409ea57cfff59dff122b3eee583eb4a63b6931fac65`, and any later commit touching
a bundled file changes it. To read the current one without starting anything:

```
PYTHONPATH=packages/demo-runtime:packages/demo-contracts/python python3 -c "
from pathlib import Path
from demo_runtime import bundle as B
root = Path('.').resolve()
e = tuple((r, B._sha256_file(root / r)) for r in B.bundle_file_list(root))
print(B.digest_of(e), len(e), B.git_identity(root))"
```

`bundle_file_list` and `digest_of` are pure: this builds nothing and writes nothing.

### 7.3 The regression cases that are preserved

`autonomy/tests/test_cube_size_limits.py` holds, as arithmetic over recorded numbers and with no
image or model:

- the failed decision frame with the complete set of inputs `verify` received, asserting that it
  now verifies AND that no criterion other than the apparent-size window ever rejected it;
- the 0.31 px miss itself, pinned as a number so it cannot later be described loosely;
- the proven run's largest verified cube (0.2992, one pixel under the old bound) with a required
  headroom, so a bound that returns to deciding runs by arithmetic noise fails;
- the recorded false positives, which must stay rejected;
- the SEARCH-range cube of the failed run, which must stay admitted.

## 8. A second defect, independent of the first

The operator was told the wrong thing.

The controller reported:

```
CYCLE FAILED: ALIGN: the cube is not detected in the pregrasp view (coarse geometry too far off, or occluded).
```

Both offered causes are false. The coarse geometry put the cube square in the middle of the
frame, and nothing occluded it. The true reason was already computed, already recorded in the
observation's own `cube.notes`, and never surfaced:

```
proposal 'a yellow cube' 0.664 [428, 2, 787, 386]: rejected, box side 0.30 of image width: too large for the cube on this image path
```

A failure message that guesses at two causes while the recorded reason sits unread in the
evidence is a diagnostics defect in its own right.

**FIXED.** `Cycle._why_no_cube` reads the recognizer's own notes for the view and passes them
through, unexamined, as `recognizer_reason`; the raise site states them instead of naming a
cause, and the withdrawn parenthetical is gone. `model/failure.ts` renders that value after the
measured sentence when the record carries one, and keeps the plain measured sentence for runs
recorded before the change, so no historical record is reinterpreted. Tested on both sides
against the exact note the retained sidecar holds.

## 9. Evidence

- Retained run evidence: `<main>/.local-dev/evidence/align-failure-20260922T194137Z/`
- Proven run observations: `<main>/.local-dev/observations/console-20260922T180557Z-5xeetlmj/`
- The offline reproduction is a wrapper around `cube_recognizer.verify` and
  `tools/static_world_observation.py`. It adds nothing to the perception path and is read-only.
