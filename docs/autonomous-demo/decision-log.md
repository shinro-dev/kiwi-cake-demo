<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Decision log

## Frozen

### Development workflow
- Branch: `dev/autonomous-cube-collection`.
- Branch remains local until E2E + independent review + explicit publication approval.
- Pre-push hook blocks publication by default.
- Branch baseline is stored locally in `.local-dev/base-commit`.

### Props
- fixed yellow cubes;
- fixed Seeed Studio / Hugging Face / LeRobot pad;
- environment and lighting may vary.

### Compute/runtime
- desktop Ubuntu AMD64;
- RTX 4060 Ti 16 GB;
- VLA training/inference on desktop;
- heavy perception on desktop;
- Ubuntu AMD64 Cake is available through the developer's local/private workflow;
- desktop Cake is preferred for the new capability segment;
- Pi stays stable LeRobot hardware host initially.

### Manipulation
- SmolVLA primary;
- ACT fallback;
- arm/gripper only for initial VLA action space.

### Perception
- body camera primary for search/localization/pad return;
- wrist camera primary/supporting for close manipulation;
- classical yellow-cube detector first;
- RT-DETRv2 or comparable permissively licensed detector only if experiments require it;
- do not introduce Ultralytics YOLO without explicit license decision;
- pad as known planar textured landmark;
- no learned depth on critical path;
- RelateAnything not on control critical path.

### Motion
- ground-plane geometry for cube location;
- closed-loop visual servoing;
- `mobile_approach` is preferred Cake-updatable capability;
- stable gateway owns motion bounds/leases.

### Publication/security
- raw captures/datasets/checkpoints never enter Git;
- dev signatures stay local;
- only final deliberately approved release signatures/artifacts may be published;
- publication audit scans branch history, not just final tree.

## P0 to confirm during bootstrap

- exact local AMD64 Cake binary/version/interface;
- lifecycle/descriptor/IPC for an independent `mobile_approach` capability;
- whether current binary suffices or the private Cake source repository needs a small feature;
- exact stable command path from desktop to LeRobot host;
- pinned LeRobot environment on the desktop.

## Bootstrap findings (2026-09-15)

Answers below are sourced from the public docs already in this repo
(`docs/architecture.md`, `docs/release-internals.md`,
`docs/why-cake-under-lerobot.md`) plus non-executing, structural inspection
(`file`, path/name listing only, no private source read, no binary run) of
the developer's local private build. No binary was executed during this
bootstrap; several items below are left **unresolved by design** rather than
probed.

- **Lifecycle/descriptor model (public terms, arch-independent):** a Cake
  **resident** process loads a **capsule** (signed module package) per a
  **Plan** (declared deployment). The public demo's Plan has exactly one
  slot, `supervisor.lekiwi`, one resource, zero bindings. (Corrected on
  2026-09-15 by the capability assessment below: the Plan model itself is a
  multi-slot graph; the one-slot shape is the demo's, not a limit of the
  runtime.) A **local admin socket** exposes read-only
  session/Plan/config/build/target-profile identities (used by "Local admin
  probe" in the architecture diagram); it is an inspection surface, not a
  command channel into LeRobot.
- **Actuation/motion is explicitly outside Cake's model.** Per
  `docs/why-cake-under-lerobot.md` and `LIMITATIONS.md`: "Cake has no torque
  concept," cannot disarm actuators, and a successful LeRobot reconnect
  always re-enables torque regardless of Cake state. The existing
  LeRobot-owned path (LeKiwiClient ↔ host over LeRobot ZMQ, TCP 5555/5556)
  is therefore the only channel that can carry bounded base-motion commands
  today; any "stable motion safety gateway" / bounds-and-lease enforcement
  for `mobile_approach` has to live in that LeRobot-side path (client, host
  adapter, or a new component alongside it), not inside Cake itself.
- **AMD64 runtime placement:** confirmed present. The public release only
  ships AArch64 binaries (`docs/targets.md`); the developer has a separate,
  privately built x86-64 target locally (Rust workspace, both `release` and
  `debug` profiles built). Binary names overlap with ones the public docs
  already describe as part of the normal toolchain (`demo-plan`,
  `demo-preflight`, `cake-resident`), plus a larger set of `*-probe`/
  `*-audit` tools not mentioned in public docs. None were executed; CLI
  behavior for the non-public tools is **unresolved**: none could be
  confirmed side-effect-free without either running them or reading private
  source, and this bootstrap does neither. No daemon/resident was started.
- **Module SDK availability:** `docs/why-cake-under-lerobot.md` states
  plainly that "the runtime source and a buildable module SDK are not
  supplied" publicly. Building a new, independently-updatable
  `mobile_approach` capsule (with the corrupted-update / rollback semantics
  `cake-runtime-contract.md` asks for) is therefore **not achievable from
  the public artifact alone**: it requires the private Cake source
  toolchain the developer already has local access to.
- **Whether the existing binary suffices:** superseded on 2026-09-15 by the
  read-only inspection recorded in `cake-capability-assessment.md`. Short
  form: multi-slot Plans, minimal-closure live replacement, rejection
  before effect, known-good preservation, rollback and introspection all
  exist and are tested; the one missing surface is runtime admission of a
  new capsule, which the baseline design avoids by pre-declaring the slot.
- **Stable command path:** unchanged from the existing architecture:
  LeRobot's ZMQ client/host link, same ports (5555/5556), same host adapter
  (`bin/pi/lekiwi_host_noninteractive.py`). No new Cake-owned command
  channel exists or is proposed.
- **Pinned LeRobot environment on the desktop:** conda `base` currently has
  a working, editable LeRobot **0.6.1** install with PyTorch 2.7.1+cu126 and
  CUDA available against the RTX 4060 Ti. A separate scaffolded environment
  pinned to LeRobot 0.4.4 also exists on this machine but has no packages
  installed. **This is an open reproducibility decision, not resolved by
  this bootstrap.** No environment was created, installed, upgraded, or
  downgraded.

## Cake capability assessment (2026-09-15)

Source: `cake-capability-assessment.md`, a read-only inspection of the
private Cake source. Decisions frozen from it:

- The `mobile_approach` slot is **pre-declared** in the start Plan: the slot
  set cannot change at runtime, and it does not need to.
- "Capability absent" is the slot at an inert generation (an inert build of
  the process-supervisor module that spawns nothing); "capability available"
  is the same slot at the real build's generation with a healthy child.
- Activation is the existing replacement operation naming the slot and the
  real build's content identity; nothing is bound to the slot, so its
  affected closure is itself alone and the LeRobot supervisor slot is never
  quiesced.
- `mobile_approach` is stateless; its child restarts on every replacement.
  No child handover and no resource handover is requested.
- Path A (pre-admit the real build at start through a holder slot; no
  engine change) is the committed baseline. Path B (runtime admission) is
  pursued in parallel and swapped in only when its gate is green; the demo
  is narrated according to whichever path is live.
- The executive reads availability from the slot listing (content identity,
  generation, lifecycle) and health, polled over the admin socket; no push
  surface is requested.
- Build-side private items, both paths: a second build identity of the
  supervising module, the inert variant, and a test that replacing a
  supervising slot restarts only its own child.
- Desktop development uses the private repository's current development tip
  and its development resident driver on x86-64; the local binaries found
  on the machine predate that tip and are to be rebuilt.

## Day-1 physical measurement + mobile_approach v0 (2026-09-15)

Real BODY-camera measurements against the physical LeKiwi (camera-only
access; no torque, no motor commands -- see
`.local-dev/captures/day1-fov-grid/` and `.local-dev/captures/day1-geometry/`
for the raw, ignored evidence). Tooling committed at `2632a0e`.

- **Localization envelope frozen for v0:** trusted region for
  `body_floor_mapping_v0` (an empirical image-to-floor homography fit from
  manual correspondences, NOT a calibrated camera model) is
  `x` approx `0.30`-`1.10` m, `|y| <= 0.40` m. Outside this region, measured
  position error grows from ~1-10cm to 25-37cm (extrapolation past the
  fitted correspondence hull) -- see
  `.local-dev/localization/evaluation-summary.json`.
- **Classical yellow-cube detector (`tools/yellow_detector.py`) result:**
  confirmed via debugging (closing/connectivity/ROI/saturation-sweep
  variations tried, not blind threshold search) that actual scene contrast
  makes global HSV color segmentation unreliable here -- the cubes are pale
  wood, close in hue+saturation to both the floor and a wood door in the
  background, so floor-glare pixels bridge separate cubes (and cubes with
  the door) into single connected components that get rejected by
  size/aspect filtering. Recall was 2/7 and 4/6 matched across two test
  scenes. **Superseding the "classical yellow-cube detector first" bullet
  above**: it remains a useful fallback/debug baseline, but is not suitable
  as this demo's production detector. Next perception implementation should
  be a small learned detector (RT-DETRv2 or RF-DETR class, Apache-2.0,
  fine-tuned on ~30-50 annotated frames) per the existing "RT-DETRv2 or
  comparable permissively licensed detector only if experiments require it"
  bullet -- that condition is now met.
- **`mobile_approach` v0 implemented offline** (`autonomy/motion/mobile_approach.py`,
  tests at `autonomy/tests/test_mobile_approach.py`): bounded P control
  (`vx = clamp(Kx*ex)`, `vy = clamp(Ky*ey)`, `omega = 0`), step-and-observe
  (no continuous trajectory), consumes the committed `TargetObservation`
  schema unchanged, produces the newly-committed `DesiredBaseMotion` schema
  (`autonomy/schemas/desired_base_motion.schema.json`, reconciling
  `mobile-approach-spec.md`'s draft shape into a frozen one). Never imports
  `lerobot` or talks to hardware; the stable motion gateway contract is
  specified separately at `docs/autonomous-demo/stable-motion-gateway-contract.md`
  and not yet implemented.

## First hardware validation session (2026-09-15)

First physically-supervised real-transport session against the LeKiwi base
(`bin/laptop/manual_target_harness.py` + `bin/pi/lekiwi_host_noninteractive.py`,
no perception, manual `TargetObservation`s only). Two operational findings
worth freezing regardless of the transport-timing investigation below:

- **The LeKiwi host must run in the foreground of a real TTY for a hardware
  session, never backgrounded with a bare `&`.** Starting it via
  `command & echo $!` over a non-interactive SSH invocation causes the
  job-control shell to set the child's SIGINT/SIGQUIT disposition to ignore
  *before* exec (standard behavior, so a terminal's Ctrl-C doesn't kill
  background jobs) -- and CPython deliberately preserves an inherited SIGINT
  ignore rather than installing its normal `KeyboardInterrupt`-raising
  handler. Once running, no external signal can undo this for that process:
  confirmed live, SIGINT had zero effect after 15+ seconds and `/proc/<pid>/status`
  showed SIGINT permanently in `SigIgn`. SIGTERM did terminate the process,
  but since this script also registers no SIGTERM handler, that termination
  bypassed `finally:` entirely too -- no `robot.disconnect()`, and per
  `LeKiwi.disconnect()`'s own `self.bus.disconnect(self.config.disable_torque_on_disconnect)`,
  **arm torque was not disabled**. Section 4d's existing "start the stock
  host by hand" / Ctrl-C procedure already does this correctly for a human
  typing at their own terminal; this is only a trap for anything (script,
  agent, remote automation) starting the host over a non-interactive channel.
  Preferred procedure for a real session: an operator-visible `ssh -t`
  (or equivalent real-TTY) session running the host in the foreground, so a
  normal Ctrl-C reaches it.
- **This desktop currently has more than one LeRobot checkout usable as
  `import lerobot`,** and which one is actually active is not obvious from
  section 5a's documented `~/lerobot/.venv` setup alone (that venv does not
  currently exist on this machine; the working install is elsewhere,
  editable-installed into a different conda environment). All relevant
  LeKiwi files (`lekiwi.py`, `lekiwi_client.py`, `lekiwi_host.py`,
  `config_lekiwi.py`) were confirmed byte-identical between the active
  checkout and the separate `~/lerobot` reference checkout at the currently
  pinned commit, so this did not turn out to be a version-skew bug -- but it
  cost real debugging time to rule out, and the private path itself isn't
  named here on purpose (machine-specific, not for a doc that may go
  public). Before any hardware run, verify which one is actually active:
  ```
  python3 -c "import lerobot; print(lerobot.__file__)"
  ```
  This remains the open reproducibility question the "Pinned LeRobot
  environment on the desktop" bootstrap finding above already flagged --
  still not resolved by this session, just confirmed non-hazardous today.

**Real base-motion result: PASS.** After the transport-timing investigation
(real host loop measured stable at ~29.9 Hz / ~33.5ms median servicing gap
over a 60s idle run, zero gaps above 50ms -- ruled out host-loop starvation
as a concern), two physically-supervised single-lease pulses were sent
through the full real stack (`TargetObservation` -> `mobile_approach` ->
`StableMotionGateway` -> real `LeKiwiTransport`/`LeKiwiClient` -> real host):

- `+x.vel` -> robot moved **forward** (~1cm), stopped normally. Gateway hard
  clamp (`vx_max_mps=0.05`) and 150ms TTL -> `ZERO_EXPIRED` both exercised
  and confirmed in software; no further motion after lease expiry.
- `+y.vel` -> robot moved **left**, stopped normally. Same TTL/clamp/zero
  behavior confirmed.
- No unexpected motion at any point (host startup, either pulse, or
  afterward). Coordinate convention frozen: `+x.vel` = forward,
  `+y.vel` = robot-left.

## 2026-09-22 integration decisions

Taken by the operator after the browser-driven pick and place, and recorded here so
that the next session reads them instead of deriving them again.

### The terminal outcome word

A placement is `object_secured`, `placed` and `base_returned`. `clearance_target_met`
is a measured QUALITY, reported as its own predicate beside the word and never folded
into it. Full acceptance is still all four, judged by the reader holding all four.

- `placed_new` is REUSED for a run that placed the cube and returned the base. No new
  outcome value was minted: `event.schema.json#/$defs/run_outcome` is a closed enum and
  `packages/demo-contracts/README.md` makes adding an enum value a v2 change, so a sixth
  word would have forced a whole `schema/v2/` directory.
- The cost of reuse, accepted knowingly: a history row carries the outcome word alone,
  so it cannot qualify itself without opening the run. Closing that additively with
  predicates on `history_entry` is open work, not a residual of this decision.
- No threshold, physical guard, release admission or shared acceptance predicate moved.
- Retained records were NOT rewritten. The `unknown` on the run of 2026-09-22 is the
  evidence the defect existed.

### Streaming: a correction to the 2026-09-22 handoff

The handoff implied the camera work was a matter of extending a working stream to a
second role. That is wrong, and the correction is load-bearing:

- ZERO cameras stream today. Measured through the real loader against the live
  configuration: the tap is null, so the tap endpoint and the preview endpoint are null
  for BOTH roles. No forward opens, no subscriber thread starts, the slot reads
  `preview_not_configured` and the preview route answers 503. The running daemon carries
  no preview port either.
- Everything above the preview client is single-role: one source, one hub topic, one
  telemetry variant, one store slot.
- An explicit preview port is returned verbatim WITHOUT the per-role offset, so one port
  cannot serve two roles. Role-aware configuration is the right shape, not a flag.
- `bin/pi/camera_service.py` has never been deployed to the robot and has never run on
  hardware. It holds the wrist lock for its whole life, so it must not be started before
  a mission-capture client exists, or the controller loses its capture path entirely.

### Evidence counts: unique captures versus copied files

For the preserved run, distinguish files from captures. Verified by counting:

- 72 PNG FILES, which is 24 distinct captures written into three directories
  (`frames`, `perception`, `pi-captures`), and only 44 distinct images by sha256.
- 24 captures over 424 s is about 0.057 frames per second. That is a still every 18
  seconds, and calling it video would be false.
- EVERY capture is the wrist role. The front camera has never produced a frame in any
  retained evidence.

### Cake module identity

Use the fields Cake actually has; do not add a semantic version to satisfy a mockup.

- `capsule_digest` is the content identity of the capsule, `config_digest` the identity
  of its configuration, and `generation` the runtime generation within the resident and
  slot scope Cake defines.
- Display them compactly as a "Revision", for example a shortened digest beside the
  generation. A shortened digest is a DISPLAY LABEL and is never an identifier for a
  command or an equality check; the full structured identities stay underneath.
- `generation` is not a semantic version. A configuration-only update, or a
  reactivation of the same capsule, must never read as new software.
- For an update, keep the previous identity before submission and show the newly
  confirmed identity after activation, so a comparison has two sides.
- Report active modules and permitted operations ONLY from real resident replies. Cake
  being reachable, a module being active, and Cake participating in the robot's task are
  three separate facts and are never collapsed.

### A ruling recovered from session memory, recorded late

PROVENANCE: the following was decided on 2026-09-21 by the agent session working on the
console, recorded only in that session's own memory, and never written here. It is
recorded now as what it is, a recollection with a traceable basis, not a new requirement.
Its SUBSTANCE is independently checkable against the schemas and tests named in it.

- Within contract v1 the Cake story rides the SNAPSHOT. Adding an event kind is a v2
  change and the fixture roster pins the number of kinds, so a Cake event kind and a Cake
  command type are both v2.
- The v1 transaction carries no sequence field, so ordered, cursored Cake transaction
  delivery is a documented v2 residual and not this milestone.
- It follows that a v1-legal live Cake beat is triggered by the operator OUTSIDE the
  console and only observed by it. That is why the first integration is labelled
  Monitoring and may not claim to control the robot.

## P1 experiments

- classical cube detector robustness;
- pad homography robustness;
- ground-plane localization error;
- VLA one-checkpoint pick/place versus separate policies;
- manipulation-ready success threshold;
- safe bounded base command values.

## 2026-09-19: the single-cube reset, recorded late

PROVENANCE: decided on 2026-09-19 by the operator, kept as an uncommitted document at the
operator's request, recorded here on 2026-09-26. `autonomous-single-cube-plan.md` is the plan
of record: one cube, perception first, reachability measured, minimum-motion strategy, the
mobility capability activated only when the selected plan needs it. A two-cube mission before
a general single-cube mission is a non-goal; `two-cube-mission-design.md` is deferred.

## 2026-09-26: one block, the robot decides

Operator ruling at 14:15 UTC, after four binding refusals in the attended session (handoff
section 15.6): the demo is one block from anywhere, picked up and placed on the pad; the
robot decides by measurement whether the arm alone suffices or the base must move; it scans
when the block is not in view; the Cake resident is on the robot and the browser shows
signature verification and a live module update. Withdrawn as defaults: the two-target
default mission (2026-09-23), the declared per-target mode as the target's identity
(2026-09-24) and D5 (2026-09-24). D4, the two-block staging, was a proof step for the
activation path and is not the demo. The work order is `one-block-demo-2026-09-26.md`.

## 2026-09-27: the base is at rest per wheel

Operator ruling, 2026-09-27: "Fix the trip. This shouldn't have happened and
should never fail in the same way in other places." It authorizes changing the
base-zero guard policy and its pinned tests, with evidence, recorded here.

What happened. Run console-20260927T142146Z-m6ed2dd9 (release afeab8f) stopped
with `base_not_zero_before_segment` at GRASP_CLOSE, holding the block, before
the CLEARANCE lift. No base motion was commanded: the capability's log holds
only its ready line for that run. At least two of the guard's six reads were
outside the per-axis band (x 0.0025 m/s, y 0.0030 m/s, theta 0.70 deg/s), which
was sized for one wheel at one count. The last one is the only one whose value
is recorded: one velocity count on the left and on the right wheel at the same
instant (theta 1.172 deg/s).

What was measured. The host computes the body velocity from three wheel
registers that move in steps of 50 steps/s, one velocity count. Every
value-bearing base read recorded from 2026-09-19 to 2026-09-27 decomposes into
whole counts on the wheels. In 975 reads at rest (with the wheel torque off, on
without the hold, unknown on 8 reads, and in the zero-speed hold) no wheel ever
read more than one count; two wheels at one count at the same instant was
recorded six times, probably five distinct samples, since a printed read can
repeat the read logged just before it. No velocity read taken during
a commanded pulse is recorded. Every recorded pulse ran at 0.05 m/s, 11.3 counts
on each driven wheel, and the pad measured 47.9 to 64.0 mm/s, 10.8 to 14.5
counts.

Decided. A read is at rest when no wheel reads more than one count
(`autonomy/manipulation/base_rest.py`). This is the 2026-09-19 decision (one
count is noise, about two counts is not zero) applied per wheel, where the count
is. The rule of three consecutive reads within six is unchanged. The one rule now
judges every site that judged the base at rest from these reads: the executor's
base-zero guard and the read taken with each observation, the stop
confirmation of the forward and the reverse primitive and of the capability
relay, the `stationary` flag of the stamped capture, the hybrid state machine
and the stage-1 coexistence report. The guard and the observation read used the
per-axis band; the others required exact zero on all three axes.

This loosens every one of those rules. On whole-count reads it accepts all they
accepted, and it also accepts one count on two or three wheels at once, which
the band refused, and any single count, which the exact-zero checks refused.
A test added on 2026-09-20 (commit a8c352d) applied the 2026-09-19 decision to
two-wheel reads as not zero; this ruling reverses that reading.

Tests changed under this ruling: `test_scan_world.py` pinned one count on two
wheels at once, and two reads that are not whole counts, as not zero; they are
at rest now. `test_frozen_primitives.py` pins the rule on its own: the proven
primitives reach it by name, so their pins did not move.

Not changed. The supervised host's attach check
(`bin/pi/lekiwi_host_safe_attach.py`) still refuses a wheel whose present
velocity is not exactly 0. Its body is pinned as demonstrated on the robot and
changes only with a hardware re-proof, so the change is staged outside this
branch. A steady motion below one count per wheel (about 4.4 mm/s forward,
3.8 mm/s sideways, 1.8 deg/s turning) reads like rest on every read, and one
below two counts per wheel (about 8.9 mm/s, 7.7 mm/s, 3.5 deg/s) on some reads,
enough to pass a single-read stop confirmation and at times the guard. Nothing
on this base commands such a motion: the primitives command exactly 0.05 m/s,
every base command is issued inside a bounded pulse followed by an explicit
zero, and what the base did is measured against the pad. The fix of the exact
trip rests on its one recorded read; the other reads of that evaluation carry no
stored values. (The attach check changed on 2026-10-01: see that entry.)

Left to the operator: the gripper's empty-close threshold (67.4, run_single_cube_cycle
`closed_on_something`) sits 0.043 above the worst recorded empty close, less than
one readback step of 0.069, the same kind of margin. Changing it is held by the
open ruling on the latch midpoint (`policy_grasp.py`).

## 2026-09-27: the gripper's empty close at the class midpoint

Operator ruling, 2026-09-27, on open ruling 2 of `overnight-plan-2026-09-26.md`:
option A, `GRIPPER_EMPTY_LATCH_MARGIN` 0.6 -> 0.87, so the controller's
empty-close threshold (`closed_on_something` in run_single_cube_cycle) moves
from 66.8 + 0.6 = 67.4 to 66.8 + 0.87 = 67.67. The operator accepted the
recommended option ("agreed on the suggestions for the decisions"). For this
value it replaces the ruling's default (no pinned change, the midpoint riding
only as the `latch_midpoint` word); the rate prior and the HOLD_OBSERVE
re-capture named in the same ruling keep the default.

What was measured. The gripper readback moves in whole steps of 100/1449 of
the calibrated range, 0.069. Every readback after a latch close on record from
2026-09-19 to 2026-09-27, each once: 11 empty closes, 969 to 976 steps, the
worst 67.357; 14 holds, 984 to 991 steps, the lowest 67.909. All of them are
one 25.4 mm cube on one calibration. The former threshold sat 0.043 above the
worst empty close, less than one readback step (0.63 of a step), so one step of
upward drift in an empty close read as held.

Why. The two errors cost different amounts. An empty close read as held skips
the retry, lifts with the jaws closed on nothing and ends
`retention_not_demonstrated`, which is FATAL, with the jaws closed in front of
the audience. A hold read as empty costs a retry, and at worst `grasp_missed`,
which is retryable. Neither error carries an empty gripper to the pad:
OBJECT_SECURED, the vision retention score, is required before any carry.

Decided. 67.67 is half a step above 980 steps, the midpoint of the gap between
the classes, so no readback falls on it. It sits 4.5 steps above the worst
empty close and 3.5 steps below the lowest hold: an empty close now reads as
held only after 5 steps of upward drift (it was 1), and a hold reads as empty
only after 4 steps down (it was 8). Tests changed under this ruling:
`test_frozen_primitives.py` re-pins the constants digest; in
`test_policy_grasp.py` one step above the worst empty is now empty under the
controller as well as under the word, the words threshold test reads 67.65
(between the word's 67.633 and the controller's 67.67), and
`TheEmptyLatchClass` checks all 25 recorded readbacks against the class each
run established.

Not changed. `policy_grasp.py` stays byte-identical, because its bytes are the
grasp stage's Cake rule digest (`stage_policy.py`); its comment on the latch
midpoint still names the former threshold. On the readback grid the word
`latch_midpoint` (67.633) now classes every value as the controller does. The
web fixtures keep 67.4 as the recorded day's threshold.

It has NOT been physically passed. The hardware check is folded into the first
attended qualification runs: five holds, each at 982 steps or more (at least
67.771) with OBJECT_SECURED true; then at least three deliberate empty closes,
with the block moved by the operator's hand after ALIGN by the plan's 10 mm
outward, then 10 mm inward, each at 979 steps or less (at most 67.564) and each
retried rather than lifted closed. The local read-back script
`d2-latch-readbacks.py` reads the attended runs over HTTP GET only; any close
within 1.5 steps of 980.5 stops the demo on this threshold for a new ruling.

## 2026-09-29: the robot does not care about blocks on the pad

Operator ruling, 2026-09-29, verbatim:

> regarding the policy and decision: human will only place blocks off the pad.
> Human can substract blocks from the pad. Robot should not care about the
> items on the pad after dropping them, the objective is to search outside the
> pad all blocks and place them within the pad. We are doing this with a single
> block first, but then we'll generalizing to many blocks, distributed across
> the room in random places. After placing the block on the pad, the robot
> should go to scan mode and search for blocks outside the pad, and plot how to
> place them within the pad.

It supersedes rulings (a) and (b) of 2026-09-28 on the phantom cube and the
lead's release tie-break of 2026-09-29, and it retires the two-cube claim rules
from the mission path.

Why. Run `console-20260928T174207Z-m915d3e9` placed its block about 46 mm
inside the pad and failed `mission_earlier_target_disturbed`: its SEARCH listed
the pad's printed logo at pad (-61, -112) mm as a block already on the pad,
bookkept it as occupied, and the EXIT view did not find it again.

Decided (target_binding, THE PAD POLICY):

- SEARCH. A candidate is a block whose pad-frame centre is not on the pad by
  the proven PLACED margin (centre at least `PLACED_MIN_EDGE_MARGIN_MM`,
  17.96 mm, inside the outline). A block straddling the edge is off the pad and
  is picked up. Every block on the pad, real or the print read as one, is never
  a candidate and never occupied. Three ways a block on the pad still fails a
  run remain, named below as residuals (4) and (5): the listing budget, the
  SEARCH SWEEP and the ALIGN count.
- THE SELECTION RULE. Among several blocks off the pad an auto target takes the
  nearest feasible one: a bounded placement mode (ARM_ONLY or BASE_ADVANCE, as
  measured before), then the largest signed placed margin (nearest the pad),
  then the listing order. None feasible is `mission_binding_candidate_not_placeable`.
- PLACED. The EXIT view must show a block on the pad within
  `RELEASE_AGREEMENT_MM` of this run's own release segment (the RELEASE_ADMISSION
  anchor moved by the FK travel and by the travel over `FK_TRAVEL_OVERSTATEMENT`,
  the segment the admission takes its worst margin over; plus the sideways
  offset's uncertainty when one was taken). Other blocks on the pad are ignored:
  no uniqueness, no earlier target disturbed, no occupied bookkeeping. None there
  is the new code `mission_placed_no_cube_at_release_point`.
- The tolerance: 26.0 mm, the worst recorded distance from the release segment
  to the block PLACED then read, 20.0 mm, plus the anchor uncertainty the
  admission charges, 6.0 mm. The nine recorded runs are 5.2, 4.5, 20.0, 5.3,
  10.5, 6.2, 2.3, 2.6 mm (their own PLACED positions) and 12.3 mm for the live
  case (its EXIT view reproduced offline, since its PLACED carries no position).
  All nine are BASE_ADVANCE releases on one pad; no arm-only release recorded
  both numbers. It is a chosen tolerance, not a calibrated interval.
- Retired: `is_claimed`, `occupied_positions`, `earlier_targets_undisturbed`
  and the codes `mission_placed_no_unclaimed_cube`,
  `mission_placed_more_than_one_unclaimed_cube` and
  `mission_earlier_target_disturbed`, which nothing writes any more. The
  mission result file is version 3: its placement carries `on_pad_count`,
  `release_distance_mm` and `release_tolerance_mm` in place of
  `unclaimed_placed_count` and `earlier_targets_undisturbed`. The claim radius
  is still carried in the target file and enters no predicate.

Correction after verification (verify r1 of this change). The first version of
this entry said only an earlier run's block can stand at the release. That was
false: the pad's printed logo, which the listing reads as a block on the pad
at pad (-61, -112) mm, lies in the band y -112..-92 mm where every recorded
placement lies, and a release within about 22 mm of x -61 mm puts it inside
the 26.0 mm tolerance. Fixer round r1 then restored a protection of the
two-cube rules (a block this run's SEARCH listed on the pad, within 12.7 mm,
was never credited, under the code
`mission_placed_only_a_block_listed_at_search`). Verification r2 showed that
this only moves the error: one block read at EXIT within 12.7 mm of a spot
something occupied before the release is either that thing with this run's
block gone, or this run's block with that thing taken away or covered, so a
rule over the EXIT positions alone must refuse one case or credit the other.
The choice went to the operator as Q1 and Q2 (a false PLACED when the block
fell off beside something SEARCH missed, a false refusal when the block lands
where a removed block stood).

Second operator ruling, 2026-09-29, answering Q1 and Q2, verbatim as relayed:

> (ii) credit any block on the pad near this run's release point, whatever was
> there before

Decided. PLACED credits the block on the pad nearest this run's release
segment within the tolerance (26.0 mm, plus 4.0 mm when a sideways release
offset was taken, from recorded placement error). What this run's SEARCH
listed on the pad is not consulted, and there is no region around the printed
logo: removing blocks from the pad can never fail a run, and this run's block
landing where a block stood before, or on the logo, is credited. The
protection of fixer round r1 is removed, and with it the code
`mission_placed_only_a_block_listed_at_search`, which nothing writes any more
(it existed only on this branch, never on the installed robot). A test pins
that no source file writes it.

ACCEPTED RESIDUAL (accepted by the operator in ruling (ii)). A false PLACED
when this run's block is not on the pad at EXIT for any reason after
OBJECT_SECURED, given an admitted release (the RELEASE_ADMISSION predicted a
worst-case centre margin of at least `PLACE_REQUIRED_CENTRE_MM`, 37.96 mm):
it left the pad after the release, it was lost between OBJECT_SECURED and the
release, or it stayed in the opened jaws. In each case, when another block,
or the pad's printed logo read as a block, stands within the tolerance of the
release point, that block or the printed logo is credited as this run's.
The RELEASE_ADMISSION does not observe the held block: it is computed from the
SEARCH anchor, the measured base displacement and the FK travel, and the
gripper reading at RELEASED is recorded, not checked. Nothing between
OBJECT_SECURED and the EXIT view tells the three cases apart (verify pad r1,
M1: the first wording named only the first case). How this is read against
the ruling, in plain words: ruling (ii) credits any block on the pad near this
run's release point and sets no condition on how this run's block came to be
missing, so this log reads all three cases as inside the ruling. The operator
may rule otherwise; the wording here then changes with the code. The printed
logo is reachable: the offline listing of 2026-09-28 read it at pad
(-61, -112) mm, and a release at the recorded depth within about 22 mm of
x -61 mm puts it inside the tolerance. On the record, no recorded run has lost
a block after an admitted release, and the nine runs of the tolerance did not
lose theirs before it either: all nine read their block on the pad at EXIT.
That is recorded evidence on one pad, not a bound. Tests pin the residual,
including this run's block absent from the EXIT view
(autonomy/tests/test_pad_policy_20260929.py).

Named residuals. (1) and (2), the two sides of Q1 and Q2, are closed by ruling
(ii): (1) is the ACCEPTED RESIDUAL above, and (2), a false refusal, no longer
exists. (3) With several blocks on the pad the nearest one is credited,
whatever was there before. The legacy cycle (no mission target) is
unchanged and still reads the frozen record's single cube. SEARCH from the
scene (`scene_search`) still refuses more than one block off the pad (ruling 7
of 2026-09-26), and the cube listing stops at three verified blocks per frame:
both belong to the next increment. (4) The listing budget (verify r1): the
first version said blocks on the pad are never counted against the run, but
listing passes spent on them counted toward the three-pass limit, and a frame
that stops at the limit refuses the run as `mission_candidates_incomplete` (the
logo, two earlier placements and this run's block reach it). Passes spent on
blocks already on the pad are now free, up to `LISTING_ON_PAD_EXEMPT_PASSES`,
9 (the 8 targets a mission can place plus the logo); a frame with more blocks
on the pad than that still refuses under that code, which a test pins. The
operator's question Q3 (is nine enough for the many-block loop) is not
answered: the cap of nine stays a named limit of the single-block version.
(5) Two more ways (verify r2): an earlier version of this entry said the
listing budget was the only way a block on the pad still fails a run. It is
not. The SEARCH SWEEP (under the word `search_sweep`) runs only when the frozen
record of the low-pose frame verifies no cube, and a block on the pad in that
frame (the printed logo, an earlier block) is such a cube: the sweep does not
run, SEARCH lists only blocks on the pad and refuses
`mission_binding_no_candidate` where the sweep would have found the block off
the pad. ALIGN refuses `align_more_than_one_cube_verified` on the recognizer's
count of verified cubes in the pregrasp view, and that count includes blocks
on the pad. Both decide arm motion (whether to pan, which block the jaws close
on), which this change does not touch; both are pinned on the scripted World.
Whether a recorded scene reaches them is inferred, not observed: offline, the
frozen perception verifies two cubes in three low-pose frames of 2026-09-28
(the off-pad block, which is the record's own cube, and the logo the listing
names), so the logo was a verified cube in those frames but never the record's
pick; and every recorded pregrasp view counts one cube.

Not changed. No motion code; the frozen primitives stay byte-identical. It has
NOT been physically passed; the offline replay of the live case credits it
PLACED at 12.3 mm with the printed logo still in the SEARCH listing.

## 2026-10-01: the safe attach takes one velocity count as rest

What happened. The supervised host's safe attach refused three arms on
2026-10-01, with `base_left_wheel: present/goal velocity 50/0, expected 0/0`
and `base_back_wheel: present/goal velocity -50/0, expected 0/0`. 50 raw is
ONE velocity count, the dither of a wheel at rest (2026-09-27 above). That
entry left the attach unchanged because its body is pinned as demonstrated on
the robot; the operator ruling of that day, "Fix the trip. This shouldn't have
happened and should never fail in the same way in other places.", covers it.

Decided. The attach (`bin/pi/lekiwi_host_safe_attach.py`) refuses a wheel only
when its present velocity reads more than one count (`|v| > 50` raw, either
sign) or its goal velocity is not exactly 0. In the unit the attach reads, that
is the rule of `base_rest.py` on every whole-count read; a test runs both on
every read of -3 to 3 counts per wheel and on every read recorded at rest. The
goal velocity is a command register and stays exact. Every other check is
unchanged (arm torque, mode, goal against present, gains, the Phase bit, wheel
mode), and so is the order: every check before the first write, then the
zero-speed hold, read back as velocity mode, goal velocity 0 and torque on for
all three wheels. The OK line prints the velocities it read instead of the text
`base velocity 0`, which a one-count read would have made false.

What it no longer sees. A wheel turning steadily at one count (about 3.8 mm/s
at its rim) reads like rest at the attach instant, and one between one and two
counts can. The exact-zero check refused such a read. The zero-speed hold that
follows the checks leaves every wheel at goal velocity 0 with torque on.

Deployed only by `bin/pi/lekiwi-deploy.sh install`: the runtime deploy does not
replace the host files. install refuses a staged host without the rule and now
lists the host file in the VERIFIED marker; arm refuses a current release whose
host lacks it; status prints which rule the current host carries, with the host
file's sha256.

Not yet demonstrated. The test pins this body as awaiting its hardware re-proof
(`AWAITING_REPROOF_BODY_SHA256` in `test_host_safe_attach_static.py`), beside
the digest last demonstrated. The commit that records a PASS of the attended
re-proof moves it; a FAIL reverts the change. A one-count read cannot be made
on the robot at will, so the rule itself is proven on the fake bus
(`test_host_boundary.py`), and the robot shows it only when a dither happens to
be read at the attach.
