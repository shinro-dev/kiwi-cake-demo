<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Camera service: one capture owner, qualification and handoff

**Status (2026-09-22): RUN ON HARDWARE. Steps 1, 2 and 3 have passed for the WRIST camera and the camera service is
the capture provider of record for it.** The dated sections near the end of this document carry the measurements,
including two findings that were recorded rather than tuned away (mean brightness outside the legacy spread, accepted
by the operator for one attended run; reported source rate against measured) and what the NORMAL host cannot measure.
**The FRONT camera remains OFF.** Step 4's non-motion half did not pass, but the reason is NOT the one first
recorded: a controlled A/B/A on 2026-09-22 showed the long observation gaps appear with the wrist stream alone,
so they are not attributable to the second camera. The measurement is not stable enough across sessions to
qualify or disqualify the front camera, and the front camera stays off because its prerequisite is unproven.

## What exists

| Piece | File | Runs on |
|---|---|---|
| The capture owner | `bin/pi/camera_service.py` | Pi |
| The shared per-role lock | `autonomy/perception/camera_lock.py` | Pi |
| The legacy path, now taking the same lock | `bin/pi/camera_multiplex_probe.py` | Pi |
| The preview reader | `packages/demo-runtime/demo_runtime/preview_client.py` | laptop |

There are two capture providers and exactly one may own a camera at a time:

- **legacy per-capture** (the proven path): for every frame the laptop runs one ssh command that opens the camera by its
  stable identity, requests MJPG at 1280x720, discards 6 settle frames, keeps the 7th, closes the camera, writes a PNG; the
  laptop then copies it. Seconds per frame, nothing to see in between.
- **camera service**: one process keeps the wrist camera open and serves a mission capture (request/reply) and a preview
  tap (publish) from the same stream. Both sockets bind the Pi's loopback only; the laptop reaches them through an ssh
  forward. No credential protects them: any process on the Pi can ask for a capture or read the preview.

## What the service promises, and on what each promise rests

**Same frame as the legacy path.** The service opens the device with the legacy backend's own code (same identity, V4L2,
MJPG requested, 1280x720 requested, a delivered frame of another size is an error), does not rotate or crop, discards 6
settle frames by default and writes a PNG. The one difference is that the stream stays open between captures, so
auto-exposure runs continuously instead of starting cold at every capture. Whether that changes the picture is a
qualification question (step 1), not an assumption.

**Captured after the request.** A read can return a frame that was buffered before the request, so read order proves
nothing. The service decides by capture timestamp:

- The timestamp is OpenCV's `CAP_PROP_POS_MSEC` on the V4L2 backend: the timestamp of the dequeued driver buffer. For a
  UVC camera the kernel fills it from `CLOCK_MONOTONIC` and flags it start of exposure; with hardware timestamps off (the
  kernel default) it is the host time at which the first packet of the frame arrived. It therefore marks the **start of
  frame reception**, and the exposure began up to one frame period earlier. It never marks dequeue, publication or
  transfer.
- OpenCV exposes neither the clock flag nor the source flag, so the service checks instead of trusting: a stream's
  timestamps are used only while each one is strictly later than the previous one and lies within 5 s before its own
  dequeue time on the Pi's monotonic clock. A timestamp from another clock fails that at once.
- A frame counts when `capture_mono_ns > request_mono_ns + exposure_guard_ns`, the guard being one source frame period
  (because of the start-of-reception meaning above). The first 6 such frames are the settle frames; the next one is
  returned, and it is later than the last settle frame (`settle_boundary_mono_ns`). Request time, guard, boundary and capture
  time are all Pi monotonic and all in the metadata, so the claim can be re-checked from the file.
- **The guard is the LONGER of the two periods the stream shows**, the reported rate's and the measured one's, and
  `exposure_guard_source` says which was used. A driver commonly reports the rate that was asked for: if the stream
  really runs at 10 fps while the API reports 30, one reported period is 33 ms, the exposure of a frame stamped 40 ms
  after the request began about 60 ms BEFORE it, and that frame would have been returned as a post-request capture.
  Adversarial review on 2026-09-20 found the service preferring the reported rate; it now takes the longer period, which
  can only make a capture wait for one more frame. The measured rate alone would be no better, since it is low while a
  stream starts or stalls. A regression test covers the 30-reported, 10-real case.
- **When there is no trustworthy timestamp** the metadata says `timestamp_semantics: "dequeue_time_only"` and the service
  discards the driver buffer depth plus the settle count before it keeps a frame. **This is weaker**: it rests on the buffer
  depth being what the API reports (or the documented default of 4), not on a measurement of when the frame was taken.
  `capture_mono_ns` and `source_stamp.sample_mono_ns` are then null: a dequeue time is not a capture time.
- `request_mono_ns` is taken when the request reaches the service. The laptop sent it earlier, so the rule is conservative
  with respect to the laptop's intent. It says nothing about whether the ARM had stopped: that stays the controller's job.

**The preview cannot delay a mission capture.** The capture thread gives each frame to the preview through a one-slot
holder: a reference swap, an unread frame is overwritten and counted. Scaling, JPEG encoding and sending run on the
preview's own thread, which also stands aside while a mission capture is in flight. Obsolete previews are dropped at three
places (the slot, the publisher's socket at a high-water mark of one, the subscriber's conflating socket) and queued at none.

**The preview rate is not the USB rate.** A preview of 2 to 5 fps is a choice about JPEG work and forwarded bytes. What the
USB bus carries is the SOURCE stream: negotiated pixel format, resolution and source frame rate. `--source-fps` REQUESTS a
source rate; whether a camera honors it is unknown until measured. The service reports the negotiated pixel format,
resolution, the frame rate the driver reports and the frame rate it measures, in `status` and in every frame's metadata.

**The published image is the frame the metadata describes.** A capture API may hand back a view of a buffer it fills
again on the next read, and the mission thread still has to hash and PNG-encode its frame while the capture thread reads
on. The frame that answers a request is therefore copied in the capture thread, before the next read, so what reaches the
store is the frame whose `capture_mono_ns` and `source_stamp.seq` are written beside it. Adversarial review on
2026-09-20 found this open: driven by a device that recycles one buffer, the service published a frame three reads newer
than the one it had selected, with the selected frame's timestamps. A regression test drives exactly that device. The
preview is not copied: it is a picture for a human and never evidence, and at worst it shows a newer frame than its
header says. Whether OpenCV's V4L2 backend really recycles a buffer is a Pi question the copy makes moot.

**Unique names, atomic publication.** A frame is `<store>/<session>/<seq>-<role>-<random>.png` with a `.json` beside it.
Each file is written under a hidden temporary name, flushed, then linked to its final name in a step that refuses to replace
an existing file. The image is published first and the metadata last, and the reply is sent after both: a metadata file
implies a complete image, whose size and sha256 it states. Frames are evidence and the service never deletes one; the store
grows until the operator clears it.

**One owner.** See the handoff rule below.

## The front camera is OFF

The service owns the wrist camera only. Without `--enable-front` the front camera's identity is not read, its lock is not
taken, its device is never opened, and a capture request for it is refused (`camera_not_enabled`).

The front camera shares a USB hub with the servo adapter. It may be enabled, and may stream, **only after** qualification
shows that an open front stream is independent of wrist capture and of the servo bus on that hub (steps 2 and 3 below, run
with the front stream on). **Distinct video nodes are not proof of independence**: two `/dev/video` entries say that the
kernel enumerated two devices, not that they do not share a hub, a bus or its bandwidth. Earlier on this rig two concurrent
camera streams were reported unstable by the operator, which is why the legacy path opens one camera at a time. **That
report is an operator observation and not a demonstrated cause, and the controlled A/B/A recorded below refutes the
attribution**: see "2026-09-22, later". The front camera stays off because its prerequisite is UNPROVEN, not because it
was shown to do harm. Until the front camera is qualified, front frames come from the legacy path, with the service stopped.

## Handoff rule

- Every process that opens a camera first takes that camera's lock: `flock` on `<runtime dir>/kc-camera-lock/<role>.lock`,
  keyed by ROLE, with the holder's label and process id written into the file (never a device identity).
- The service takes the lock before it opens a device and keeps it until it is stopped. A camera that FAILED inside the
  service stays locked: the service does not reopen it and does not hand it over.
- The legacy probe takes the same lock before it opens anything, waiting at most `--lock-wait-s` (2 s). When the service
  holds it the probe **fails closed**: no device is opened, `report.json` carries `refused.reason: "camera_lock_held"` and
  the refusal as the error of every attempt (so the laptop sees an invalid frame with the reason), exit status 3. The same
  happens, with reason `camera_lock_unavailable`, when the lock cannot be used at all, including when `camera_lock.py` was
  not copied to the Pi next to the probe.
- **Switching provider is an explicit stop and start, never implicit, never during a run.** To go from the service to the
  legacy path: finish or abort the run, stop the service (SIGTERM; it closes the devices, then releases the locks), confirm
  with `python3 bin/pi/camera_service.py --lock-status` that no role is held, set `capture_provider` in the runtime config,
  start the next run. To go the other way: no probe running, start the service, confirm the lock holder is `camera_service`.
  Nothing falls back from one provider to the other on its own: a refused capture is reported, not rerouted.
- Both sides must resolve the SAME lock directory: `KC_CAMERA_LOCK_DIR`, else `XDG_RUNTIME_DIR`, else `/run/user/<uid>`,
  else a private directory under the system temporary directory. `--lock-status` and the service status print which source
  was used (never the path); compare them during qualification.
- A runtime directory that belongs to a login session is removed when the user's last session ends. A service that
  outlives its session would then hold a lock nobody else can see. The service checks before every mission capture that
  its lock file is still the file at the lock path and refuses with `camera_lock_lost` when it is not. Run the service as
  a user service, or with lingering enabled for the user, so the directory outlives it.
- The lock is advisory. It excludes the service and the probe from each other. It does not stop a program that opens the
  device without asking (a camera viewer; a motor host configured with cameras).

## Qualification procedure

Run in this order. Stop at the first step that fails; the legacy path remains the provider. Record every number in this
document under a dated heading, with the run directories kept outside the repository.

**The driver for the parts of this that need no motion is `bin/laptop/qualify-robot.sh`**, whose runbook is
`docs/autonomous-demo/hardware-qualification.md`. Its step 1 is the ownership handoff of the "Handoff rule" above (the
legacy probe works, the service takes the wrist lock, the probe is refused with `camera_lock_held`, the service stops,
the probe works again). Its step 4 collects the numbers step 2 below asks for, with the preview subscribed and mission
captures in flight, and compares the motor host's loop rate and longest observation interval against a baseline with the
service stopped. The service itself is started and stopped by `bin/pi/start-camera-service.sh`, which copies and
checksums `camera_service.py`, `camera_lock.py` and `camera_lifecycle.py` onto the robot computer, refuses to start when
any camera lock is already held (naming the holder) or when a video node has an owner, and checks after the start that
the wrist lock really names `camera_service`. Neither script has ever run against the robot.

Two steps of the procedure below the driver does NOT cover, and they stay manual: step 1's picture comparison (the same
scene through both paths, brightness and pad homography within the legacy path's own spread) and step 3 (captures
validated during a full cycle), which is motion. Step 4, the front camera, stays out of scope entirely.

**0. Preconditions.** Motor host running and owning NO camera. Arm at the saved scan pose, torque holding, base at rest.
Fixed lighting. The scene contains the pad and a cube and nothing moves.

**1. No motion: both paths on the same stationary scene.**

- Legacy first, service stopped: 10 wrist captures through the legacy path. Keep the PNGs and `report.json`.
- Stop nothing else, start the service (wrist only, preview ON), confirm the probe is now refused (exit 3) and that
  `--lock-status` names `camera_service`.
- 10 mission captures through the service. Keep the PNGs and their metadata.
- Compare: identical resolution and orientation; no crop (the pad corners land on the same pixels within the pad
  localizer's repeatability); mean brightness and the pad homography within the spread the legacy captures show among
  themselves. A difference larger than the legacy path's own spread is a finding about continuous auto-exposure and fails
  the step until explained.
- From the service metadata record: `timestamp_semantics` (if `dequeue_time_only`, record `fallback_reason`; the service
  may still qualify, as the weaker mode, and this document must then say so), negotiated pixel format and resolution,
  `source_fps_reported`, `source_fps_measured`, `frames_discarded_stale` (non-zero values are the buffered frames the
  timestamp rule exists for), and the request-to-frame latency.
- Check the timestamp rule by hand on three frames: `capture_mono_ns > request_mono_ns + exposure_guard_ns` and
  `capture_mono_ns > settle_boundary_mono_ns`.
- Compare `source_fps_reported` with `source_fps_measured` on a stream that has been running for a minute. If they
  disagree, the reported one is the rate that was asked for and not the rate the bus carries; `exposure_guard_source`
  must then read `source_fps_measured`. A large disagreement is itself a finding about `--source-fps`.

**2. Stream on, nothing moving: what the stream costs.** With the service streaming for 10 minutes:

- host tap rate: the rate of the host tap (the observation samples the motor host publishes, stamped at the source) as its
  subscriber on the laptop measures it, with the sequence gaps that subscriber reports, against the same 10 minutes with
  the service stopped;
- servo read health: the motor host's read errors, retries and the longest gap between two observations, against the same
  baseline. The wrist camera and the servo adapter meet at the Pi's USB controller even when they do not share a hub;
- the preview as the laptop sees it (`preview_client` stats): measured fps, gaps, longest interval;
- `source_fps_measured` and `reads_without_frame` from the service status at the start and at the end;
- if a lower source rate is wanted, restart the service with `--source-fps`, confirm from `negotiated` and
  `source_fps_measured` that the camera honored it, and repeat this step.

Any servo read error or observation gap that the baseline does not show fails the step.

**3. Captures validated after movement, during a full cycle.** Run a full single-cube cycle with the service as the capture
provider and the preview on. For EVERY mission capture of the cycle check, from its metadata, that the frame was captured
after the request (the rule of step 1) and, from the controller's record, that the request was sent after the arm was at
rest. Then check the perception results the cycle produced from those frames against a legacy cycle from a comparable start.
A single frame that fails the timestamp rule, a `stale_suspect`, a capture timeout or a servo fault fails the step.

**4. Front camera (separate, later).** Only after steps 1 to 3 passed for the wrist: repeat steps 2 and 3 with
`--enable-front`, watching wrist capture health and servo read health with BOTH streams open. This is the step that decides
whether the front camera may stream at all.

## 2026-09-22: steps 1 and 2, wrist only, no motion

Run with the NORMAL motor host attached and holding (`--robot.cameras={}`, so the host owns no camera), the arm at the
saved scan pose, and the pad and one cube stationary in front of the robot. The console was stopped for each ownership
transition, so no command could be submitted while the camera owner changed. Raw numbers are under
`.local-dev/qualification/`, outside this checkout.

Service: `cs-20260922T143706Z-cbc64353`, wrist only, front off. Negotiated MJPG 1280x720, buffer depth 4.

**Step 1, both paths on the same stationary scene.** Ten captures each, all valid on both paths.

| Property | Legacy (10) | Service (10) | Against the legacy spread |
|---|---|---|---|
| Resolution | 1280x720 | 1280x720 | identical |
| Orientation ok | 10/10 | 10/10 | identical |
| Pad frame valid | 10/10 | 10/10 | identical |
| Mean brightness | 118.876, sd 0.068 | 118.637, sd 0.071 | **-0.239, which is 3.53 sd: OUTSIDE** |
| Reprojection rms px | 1.084, sd 0.041 | 1.069, sd 0.031 | -0.015, 0.37 sd: within |
| Landmark inliers | 147.7, sd 7.43 | 140.7, sd 6.50 | -7.0, 0.94 sd: within |

No crop: the pad's own corners land within 0.11 to 0.51 px of the legacy mean, which is well inside the localizer's own
reprojection error of about 1.08 px. Geometry is therefore unaffected, and the one figure outside the legacy spread is
brightness.

**THE BRIGHTNESS FINDING, recorded rather than tuned away.** The verdict stands: mean brightness is OUTSIDE the spread
this document accepts, at 3.53 standard deviations, and this comparison is therefore NOT a clean pass. The tolerance is
unchanged and no perception threshold was touched.

The practical magnitude is 0.24 grey levels on a mean of 118.9, which is 0.20 percent of the measured level, and the
difference is systematic rather than noisy: every service capture is darker than every legacy capture, while each
path's own spread is about 0.07. That is the continuous auto-exposure case this document anticipates, because the
legacy path opens the camera cold for each capture and settles six frames while the service holds it open at a
different steady state.

WHAT THE COMPARISON SUPPORTS, stated no wider than the measurements. The tested image geometry and the pad-localization
measurements remain COMPATIBLE between the two paths: identical resolution and orientation, pad valid on 10 of 10 for
both, reprojection error and landmark inliers inside the legacy spread, and the pad's corners within 0.51 px. THE
PIXELS CHANGED. This is a comparison of ten static frames of one stationary scene, so it does not establish retention
performance during motion, and nothing here should be read as covering the grasp, the lift or the placement.

**CUBE DETECTION, on the same retained frames.** Pad localization cannot answer whether the service's pixels cost a
detection, so the controller's own perception entry point (`static_world_observation.observe_frame`, same detector,
same template directory, no threshold changed) was run offline over the twenty retained images. No new capture.

| | Legacy (10) | Service (10) | Against the legacy spread |
|---|---|---|---|
| Cube detected | 10/10 | 10/10 | no loss |
| Contact point resolved | 10/10 | 10/10 | no loss |
| Confidence | 0.9629, sd 0.0069 | 0.9608, sd 0.0044 | -0.0022, 0.31 sd: within |
| Cube centre in PAD_FRAME | x 4.15, y -163.92 mm | x 4.17, y -163.94 mm | shift 0.03 mm |

No detection was lost and no decision changed. The cube centre, which is what the grasp is planned from, moves by
0.03 mm, well inside each path's own spread of about 0.1 to 0.2 mm. This is still ten static frames of one stationary
scene and says nothing about motion.

**OPERATOR ACCEPTANCE, 2026-09-22, SCOPED.** The operator accepted this measured 0.24-grey-level difference for ONE
attended wrist-only qualification run on the tested configuration. The acceptance does not change the bound, does not
generalise to another configuration, and does not close the step for repeat use: a later run re-reads this finding.

Handoff rule demonstrated in both directions: with the service stopped the legacy probe captured ten valid frames;
with the service running the same probe was REFUSED, opened no device and returned no frame, with
`camera_lock_held: the wrist camera is owned by camera_service`; after the service stopped, no role was held.

Metadata the step asks for: `timestamp_semantics` is `driver_buffer_timestamp`, the STRONGER mode, so no
`fallback_reason` applies; `driver_timestamp.trusted` true with 0 violations; `frames_discarded_stale` 2 and
`frames_discarded_settle` 6 per capture; request round trip about 525 ms and copy back about 440 ms on the laptop's
clock. The timestamp rule was checked by hand on three frames and held on all three, with 305 to 329 ms of margin:
`capture_mono_ns > request_mono_ns + exposure_guard_ns` and later than the settle boundary.

**A SECOND FINDING: `source_fps_reported` is 30.0 and `source_fps_measured` is 19.9**, a 34 percent shortfall on a
stream that had been running for minutes. As this document requires, the reported rate is the rate that was asked for
and not the rate the bus carries. `exposure_guard()` already resolves this correctly without a change: it takes the
LONGER of the two periods, so the guard is computed from the measured rate, and the margin above confirms it.

**Step 2, what the stream costs, adapted to the NORMAL backend.** The step as written measures the host tap, and the
tap exists only under the boundary host. The boundary host was NOT started, because the NORMAL host and its arm
acquisition are preserved. What the NORMAL backend does and does not support is in
`docs/autonomous-demo/normal-host-observation.md`. Two 60 s windows on the existing observation path, one consumer,
nothing else changed:

| | Preview off | Preview on | Difference |
|---|---|---|---|
| Received cadence | 29.953 Hz | 29.925 Hz | 0.09 percent, inside the 10 percent bound |
| Inter-arrival p95 | 37.27 ms | 38.07 ms | +0.80 ms |
| Longest single gap | 75.78 ms | 162.8 ms | **+87 ms** |
| Malformed samples | 0 | 0 | none |

Kernel USB and serial error counts on the robot computer were 0 before and 0 after both windows, and the motor host's
own terminal log carried no error, retry, failure or timeout line in 734 lines.

**THE LONGEST-GAP FINDING, checked against the requirements actually in force.** One inter-arrival in about 1795 was
163 ms with the stream open against 76 ms without it. The cadence and the p95 are unaffected, so it is a single outlier
rather than a shift, but it is twice the worst gap seen without the stream and it is recorded as a difference the
stream may have caused.

It violates no applicable requirement, and the two candidates are different things:

- OBSERVATION FRESHNESS. `freshness_refusal_enabled` is false in the configuration under test and `require_fresh` is
  therefore off, so no read behaves differently and NO observation-freshness threshold is in force for this gap to
  exceed. Nothing here invents one.
- THE COMMAND WATCHDOG is `watchdog_timeout_ms = 500` on the installed host, read from its own config class. It counts
  from the last DISPATCHED COMMAND on the command socket, not from an observation on the observation socket. A gap in
  observation DELIVERY to a passive consumer cannot feed it, and no command was in flight during either window. One
  delivery gap does not establish a command-watchdog event and is not reported as one.

So it is retained as an observed limitation to MONITOR during step 3, where commands are in flight and the comparison
becomes meaningful, and not as a failure.

**What remains unverified for step 2, and why.** Servo read errors and retries, and the host's internal loop rate, are
not exported by the stock host and cannot be read from here; the received cadence above is a DELIVERY measurement and
is not the loop rate. The smallest next step that would close it is a counter inside the host, which means the boundary
host and its tap, and that is a separate decision because it replaces the NORMAL host.

## 2026-09-22: step 3, wrist only, attended, one full cycle

Run `console-20260922T180557Z-5xeetlmj`, 816 records. Operator staged the cube outside the pad, cleared the base
travel area and confirmed the physical checks at the Start dialog. NORMAL motor host attached (the robot had been
power cycled and the arm re-acquired first), camera service wrist only with preview on, front OFF throughout. No code,
configuration or threshold changed. Raw evidence under `.local-dev/qualification/step3-20260922T173206Z/`.

**Camera qualification, judged on its own.**

| Criterion | Result |
|---|---|
| Effective provider on the run's own record | `capture_provider = camera_service` |
| Mission captures served / refused | 25 / 0 (see the accounting below) |
| Timestamp rule, capture > request + guard | 25/25 for THIS run |
| Settle rule, capture > settle boundary | 25/25 for THIS run |
| `timestamp_semantics` | `driver_buffer_timestamp` on every record, the stronger mode |
| `exposure_guard_source` | `source_fps_measured` on every record, as the reported-vs-measured finding requires |
| Driver timestamp trust | trusted, 0 violations |
| `reads_without_frame` | 0 |
| Source rate across the run | 19.931 to 19.904 fps, stable |
| Preview yielding | 220 previews stood aside for mission captures |
| Preview recovery | 1508 frames published during the run; still live at the end |
| Kernel USB and serial errors | 0 before, 0 after |
| Motor host terminal log | 0 error, retry, failure or timeout lines |
| Front camera | `off` for the whole run |

**THE CAPTURE ACCOUNTING, corrected.** An earlier draft of this record reported "24 mission captures" beside
"40/40 timestamp and settle checks". The two denominators counted different things and neither was this run:

- 24 was a DIFFERENCE of the service's lifetime `captures_ok` counter across two status calls. It undercounts by one,
  because the "before" call was made after the run had already started and its first capture had landed.
- 40 was the number of metadata files read back, which was simply the 40 most recent on the robot computer. Those span
  FOUR service sessions: 25 from this run's session, 13 from the step 1 comparison, and 2 from earlier Observes.

Scoped to this run's service session `cs-20260922T173941Z-fbfe6699`, the retained evidence holds 25 metadata records,
with 25 distinct sha256 digests and 25 distinct stored paths, every one role `wrist` at 1280x720. The run's own event
log carries 25 `observation.captured` records for the wrist, all retained. Every one of those 25 metadata records
satisfies the timestamp rule and the settle rule, so COVERAGE IS COMPLETE: 25 unique requests, 25 unique captures, 25
retained observations, 25 of 25 checks passed, with nothing left unaccounted for and no second physical run needed.

The one association the retained evidence does NOT support is a digest-level join between the two sides: the
observation record's `content_digest` is computed over the stored observation and the service's `sha256` over its own
PNG, so they are different artifacts of the same capture and do not compare. The association above is by service
session, count, role and resolution.

No capture was refused, no capture failed either rule, and the stream was live before, during and after. Step 3 PASSES
for the wrist camera, under the scoped brightness exception recorded above for this one attended run.

**What the NORMAL host still cannot measure, unchanged by this run.** Servo read errors and retries, and the host's
internal loop rate, are not exported. Observation DELIVERY gaps during the cycle were NOT measured either, and this is
a stronger limit than it was before motion: the host observation socket is ZeroMQ PUSH, which round-robins across
connected PULL peers, so attaching a second consumer during a run would take observations away from the controller.
The preview-off and preview-on windows recorded earlier were safe only because no controller was running. The in-band
substitute is the run's own records, and the run recorded one evidence gap, `source_stamp_missing`: "the host does not
stamp its observations: every consumed stamp of this run is receiver-side". That is the documented NORMAL-backend
residual of `normal-host-observation.md` appearing exactly where it was predicted, and it is not a camera finding.

**Manipulation result, judged separately.** Outcome `placed_new`, exit 0, no reason code, 0 automatic retries, 0
task-control interventions, 0 refused actions.

| End check | Value | Threshold | Verdict |
|---|---|---|---|
| object_secured | 0.818 | 0.5 | true |
| placed | 55.36 mm | 17.96 mm | true |
| clearance_target_met | 55.36 mm | 37.96 mm | true |
| base_returned | 0.391 mm | 10.0 mm | true |

All four end checks measured true, so this run is a full acceptance pass and not the reported-not-confirmed case the
2026-09-22 browser run was. `evidence_complete` is false with 1 gap, and that gap is the `source_stamp_missing`
residual above rather than a missing measurement.

**The console.** A FRESH tab, which folded none of the run live, reads back "Placed and returned",
`1 of 1 cube placed` and the run id, and the run appears in the finished-run list. Reloading submitted no command:
the finished-run count was unchanged and no run became active.

## 2026-09-22: step 4, front camera, non-motion half: FAILED, front stays off

Run with both cameras enabled (`--enable-front`), no controller, no motion, the console stopped so nothing could
command. The question step 4 exists to answer is whether an open front stream is independent of the servo bus on the
hub they share. It is not.

Observation delivery on the existing path, four 60 s windows, one consumer, nothing else changed:

| Condition | Received cadence | Inter-arrival p95 | Longest gap |
|---|---|---|---|
| No preview (baseline) | 29.953 Hz | 37.27 ms | 75.78 ms |
| Wrist stream only | 29.925 Hz | 38.07 ms | 162.8 ms |
| Both streams | 29.927 Hz | 37.95 ms | 250.57 ms |
| Both streams, repeat | 29.916 Hz | 43.64 ms | 257.92 ms |

**The verdict, against this document's own criterion.** "Any servo read error or observation gap that the baseline
does not show fails the step." The baseline's longest gap is 75.78 ms. With both streams open it is about 250 ms, in
BOTH windows, which is 3.4 times the baseline and is plainly reproducible rather than an outlier. The repeat window
also shows p95 rising to 43.64 ms against the baseline's 37.27. THE STEP FAILS and the front camera stays off.

**What is NOT wrong.** Received cadence is 29.92 to 29.95 Hz in every condition, a spread of 0.12 percent, so nothing
here is a throughput collapse. Both cameras stayed healthy throughout: `reads_without_frame` 0 on each, 0 driver
timestamp violations on each, both negotiating MJPG 1280x720, and kernel USB and serial error counts on the robot
computer were 0 before and 0 after. So no camera reported a fault and the kernel logged no USB error.

**WHAT THIS DOES AND DOES NOT ESTABLISH.** The supported finding is narrow: simultaneous streaming COINCIDED with
reproducibly longer observation-delivery gaps, and that fails the documented criterion. Zero logged USB errors does
not establish bus contention and does not exclude other causes. An earlier draft of this record called it contention
on the shared path; that was a cause announced rather than demonstrated, and it is withdrawn. Candidate causes still
to be distinguished include service read timeouts, serialized per-camera processing inside the service, JPEG encoding
work, CPU scheduling on the robot computer, and transport activity on the laptop link. The verdict stands on the
measurement; the cause is open.

**The monotonic progression is the finding.** The longest gap grows with each stream added, 76 ms with none, 163 ms
with one, about 254 ms with two. One stream's cost was already recorded above as an observed limitation to monitor;
two streams roughly double it. Nothing was tuned to obtain two pictures, and no bound was relaxed.

**What this does NOT establish.** These windows carry no controller and no motion, so they say nothing about how a
250 ms delivery gap would interact with a cycle in flight. Concurrent-camera operation during motion remains
unqualified and needs its own procedure and its own fresh physical confirmation; it is not reachable from here,
because the non-motion prerequisite above did not pass.

Reverted immediately: the service runs wrist only, the front role has no preview endpoint, and the console shows the
front panel awaiting qualification.

## 2026-09-22, later: the controlled A/B/A REFUTES the front-camera attribution

The first step 4 comparison ran its conditions minutes apart, in a fixed order, with a service restart between each:
no preview, then wrist, then both, then both again. The longest gap grew 76, 163, 251, 258 ms, and that ordering was
read as the cost of each added stream. It was a confound, and this is what a controlled comparison showed.

Wrist only, then both, then wrist only AGAIN, equal 45 s windows, one session, no controller, console stopped:

| Condition | Received cadence | p95 | Longest gap |
|---|---|---|---|
| Wrist only (A) | 29.931 Hz | 42.74 ms | 246.46 ms |
| Both streams (B) | 29.931 Hz | 45.61 ms | 243.92 ms |
| Wrist only again (A2) | 29.945 Hz | 45.76 ms | 291.79 ms |

**The both-streams window has the SHORTEST longest gap of the three, and the worst gap of all is in a wrist-only
window.** The second stream does not explain the long gaps. What does drift is p95, which rises monotonically with
TIME across the three windows (42.74, 45.61, 45.76) rather than with the number of streams. The earlier monotonic
progression is withdrawn as a confound of measurement order.

**WHAT THIS MEANS FOR THE VERDICT.** The documented criterion, any observation gap the baseline does not show, is
still exceeded, but it is exceeded by the WRIST-ONLY configuration too, which is the configuration that passed step 3
and is deployed. So the criterion as measured here does not separate the front camera from the qualified baseline, and
it cannot carry a front-camera verdict either way. The front camera stays OFF because its prerequisite is unproven,
which is not the same as having been shown to cause harm.

**THE MEASUREMENT IS THE SUSPECT NOW.** These windows are taken on the LAPTOP, over the LAN, at one PULL consumer, and
the stock host neither stamps nor sequences its observations. A gap measured there includes the network and the
laptop's own scheduling, and nothing in the sample lets a receiver separate those from the host. The 76 ms baseline
that the whole comparison rests on was measured in a different session under those same uncontrolled conditions.

**THE SMALLEST NEXT STEP, and it needs no new framework and no boundary host.** Run the same passive consumer ON the
robot computer against the observation port over loopback. That removes the LAN and the laptop's scheduling from the
path while changing nothing about the host, the service or the cameras, and it is the cheapest way to tell a robot-side
gap from a transport-side one. Until that is done, no remedy should be chosen: a lower front-camera capture profile, a
port move or anything else would be tuning against a number whose source is not established.

**NOT ATTEMPTED, and not to be inferred.** No USB rearrangement was requested and none should be until the measurement
is trusted. For the record, the topology read on 2026-09-22 is that the WRIST camera is on `usb3` (`xhci-hcd.1`),
alone on that controller, and the FRONT camera is on `usb1` (`xhci-hcd.0`), which also carries the servo serial
adapter. That makes a shared-controller story physically plausible for the front camera, and the A/B/A above is
evidence AGAINST it being what these windows are measuring. Plausibility is not a finding.

## Degraded mode: mission stills at their true cadence

When the service is not qualified, or is stopped, the console can still show the mission stills: each frame the controller
captured, when it was captured, a few per minute. That is a **supported degraded mode**. It is honest (every picture is a
real mission frame with its real time) and it costs nothing on the USB bus.

It does **not** complete the live-view objective. A still from 40 seconds ago is not a view of the robot now, and the console
must present it as a still with its age, never as live video. The live-view objective is met only by the preview tap of a
qualified service.

## Interfaces

**Mission capture** (REP, `127.0.0.1:5570` on the Pi). Request and reply are one JSON object each.

- `{"op": "capture", "role": "wrist", "session": "<label>", "settle_frames": 6, "timeout_s": 10}`: `session` (the
  contract's session pattern), `settle_frames` and `timeout_s` are optional. Reply `{"ok": true, "frame": {...},
  "lifecycle_record": {...}}`. `frame` is the metadata also written beside the image (`relative_path` is relative to the
  service's store directory); `lifecycle_record` has the fields of the legacy `LifecycleRecord`, with `close_ok: false`
  because this provider does not close the device per capture.
- Refusals: `{"ok": false, "reason": ..., "detail": ...}` with `invalid_request`, `camera_not_enabled`, `camera_failed`,
  `camera_lock_lost`, `capture_timeout`, `resolution_mismatch`, `frame_invalid`, `publication_failed`.
- `{"op": "status"}` and `{"op": "ping"}`.

**Preview** (PUB, `127.0.0.1:5571` wrist, `5572` front when enabled). One message part: the magic `KCPV1`, a 4-byte
big-endian header length, a JSON header (`kc.camera_service.preview/1`: role, service session, `preview_seq`, `source_seq`,
capture and dequeue time with their clock domain, sizes, the source's pixel format and frame rates), then the JPEG.
`preview_client.PreviewSubscriber` reads it: latest frame only, the clock domain of its capture time, receive age and
measured fps in the laptop clock, gaps in `preview_seq`. Pi times and laptop times are never subtracted from each other.

## Not done

- Nothing here has run on the robot: what `CAP_PROP_POS_MSEC` returns on these cameras, whether `--source-fps` is honored,
  USB and servo behavior with the stream open are all unmeasured.
- No laptop-side MISSION client exists yet, and the controller does not use the service: `capture_provider:
  "camera_service"` in the runtime config selects nothing until that client is written and wired in.
- The laptop's copy list for the legacy path (`PI_FILES` in `bin/laptop/perception/run_stage1_scan.py`) must include
  `autonomy/perception/camera_lock.py`; without it every legacy capture is a reported refusal (`camera_lock_unavailable`).
  It does since 2026-09-20 and `autonomy/tests/test_camera_lock.py` is green; the deployed Pi copy must be refreshed
  before the next legacy scan, or the Pi still has the probe that imports a module it has not got.
- The lock is checked before every mission capture and in `status`, and not by the preview worker or by the legacy probe
  between its own cycles. A lock lost mid-run is noticed by the next mission capture, not sooner.
- No unit file starts the service. `bin/pi/start-camera-service.sh` (2026-09-21) deploys it, starts it detached, stops
  it, asks it for its status and passes `--lock-status` through, and `bin/laptop/qualify-robot.sh` opens the ssh forward
  for the mission and preview sockets while it measures. Neither has run against the robot, and neither survives a
  reboot: a service started this way is gone after one, which is what a unit file would fix.
- The service does not reopen a failed camera and does not prune its store.
