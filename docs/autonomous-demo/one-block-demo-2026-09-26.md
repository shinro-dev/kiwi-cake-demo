# One block, robot decides: the work order of 2026-09-26

Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

The code is the documentation. This page records the operator's ruling, the plan it restores, the deliverables cut from
it, the gates each one must pass, and the physical qualification that stands between a green gate and a demo. Nothing
here describes a feature that exists; each deliverable is marked done only by its commit and its gate output.

## 1. The ruling (operator, 2026-09-26, 14:15 UTC)

"Demo one single block, from anywhere, being picked up and dropped in the pad. If it is far away and wheel actuation
is required, then move. If arm length is enough, then don't use wheels. If the robot doesn't see the block in the
screen, it should scan for it. The robot must have the Cake resident, and showcase the power of the Cake resident:
signature module verification, live module update." Driven from the browser console, one Start press.

## 2. What this restores

`autonomous-single-cube-plan.md` (2026-09-19) is the plan of record and is committed beside this page for the first
time. Its section 3 (the robot measures reachability and takes the minimum-motion strategy), 8.16 (mobility is
activated only when the selected plan needs it) and 13 (a two-cube mission before a general single-cube mission is a
non-goal) govern. `two-cube-mission-design.md` stays deferred.

Three build-time defaults on this branch contradicted the plan and are withdrawn by this ruling: the two-target
default mission (`mission_service.DEFAULT_TARGETS`, 2026-09-23), the declared per-target mode as the target's identity
(`target_binding.bind_targets`, 2026-09-24) and D5 of the handoff ("an arm-only target that measures a base advance is
refused", 2026-09-24). D4 (the two-block staging) was a proof step for the activation path, never the demo. The
attended session of 2026-09-26 (handoff section 15.6, four binding refusals, no motion) is the evidence.

## 3. Deliverables

Each track is developed offline, on its own branch `track/<name>` from `feat/operator-console`, tests first, with the
motion primitives and the physical gates of the proven controller unchanged. A track is integrated only with its gates
green (section 5). Robot-side deliverables ship as one runtime release (`bin/runtime-release.sh stage <commit>`)
installed with the operator present; browser-side deliverables ship as a web bundle.

### T1 auto target (robot runtime release)

- `requires_capability` accepts a third value, `"auto"`: the target takes whichever placement mode its own SEARCH
  measures. Binding: the candidates are every cube that is not already placed; exactly one is required, whatever it
  measures. A measured ARM_ONLY runs the proven arm-only cycle. A measured BASE_ADVANCE, in a run that carries no
  capability, ends that run before PREGRASP with `mission_capability_required`; the runtime records the need, waits for
  the capability, and respawns the same target carrying it, exactly as the existing search-source path does.
- The default mission a browser Start launches is one auto target: `{"target_id": "cube", "label": "the block",
  "reachable": true, "requires_capability": "auto"}`. The browser body stays `{}`; no web change is needed for this.
- Validators that must accept the third value: `mission_service.normalize_targets`, the target file reader and writer
  in `target_binding` (bump `MISSION_TARGET_VERSION`; both sides refuse other versions and the child bundle is built
  from the same release), the mission record readers, the web `missionRecord` types.
- A mode that flips while the cube is held, in a run without the capability, still ends the run with the cube held (the
  existing refusal); the operator resets the scene. Carrying the capability on every auto spawn is a separate ruling
  and is not taken here.
- A single block already placed deeper than the placed margin is refused with `mission_binding_no_candidate` and the
  placed count in the refusal's values; the controller never re-places a placed cube.

### T2 search sweep (robot runtime release)

- When the low-pose frame verifies no cube with a contact pixel, SEARCH runs the sweep sectors from the fresh measured
  pan (`cli.sweep_poses`, lift, elbow and wrist held, pan bounded to plus or minus 30 degrees), each through `run_pose`
  with the unchanged abort, base-zero, arrival and wrist-ceiling checks, perceives each frame, stops at the first frame
  with a verified cube and a valid pad frame, and continues the unchanged SEARCH from that frame and that pan. No
  sector with a cube ends the run with `search_no_cube_after_sweep`, arm back at the centre sector.
- One state per sector, `SEARCH_SWEEP`, with the sector, the pan and the outcome, so the console shows the sweep.
- Physical qualification before any demo: plan-only sweep, then one attended sweep with the block off-centre, then
  three starts (plan section 14).
- Since the overnight integration of 2026-09-27 the sweep runs only under the stage word `search_sweep`, which is not
  qualified: with no word, a frame with no contact pixel ends `search_no_cube` (in a mission,
  `mission_binding_no_candidate`) with nothing moved, as the 2026-09-20 controller did.

### T3 signature verification, browser-visible (robot runtime release plus web bundle)

- A read-only runtime operation runs the release's own verifier (`demo-plan verify --policy require`) on the robot for
  every deployed capsule under the deployment key (expected admitted), under a fresh throwaway key (expected refused,
  `signature_key_id_not_trusted`) and for an unsigned twin staged at install (expected refused, `signature_missing`).
  It commands no motion and touches no running slot.
- A guarded `POST /api/capability/signature` beside `/api/capability/check`, and a "Signature policy check (no
  motion)" block in the Cake panel showing the verdict table with the reason codes.
- Honesty rule, on screen: this is the release verifier under the resident's policy. The live resident admits capsules
  when it starts; there is no live admission operation in the resident (`AdmitCapsule` is unavailable), and the
  abort-swap candidate is a transactional activation failure, not a signature refusal.

### T4 the browser manages the whole demo (web bundle)

- Start (one press, the attestation dialog), the mission view through search, sweep, pick, wait, activate, resume,
  place, return and complete, Stop, Return arm, the capability slot check (live module update: abort-swap rejected with
  the slot unchanged, swapped observed with the next generation and a new `--contract v2` child, no resident restart,
  host held) and the signature check of T3.
- Sentences that contradict the demo are corrected: the progress label for one target, the pinned "Cake: not connected
  to this task" line during a mission that waited on and resumed through an activation, the always-disabled "Update a
  module" control, the `auto` state in the mission drawer and records, and bare reason codes (`search_no_cube*`,
  `mission_binding_*`) given operator sentences.

### T5 attended qualification (with the robot)

In order, each with its evidence copied per handoff 15.6 step 7: install the release; one block on the wood beyond arm
reach, Start, the wait, the two activations, the relayed base advance (the first time the Cake relay moves the robot;
at most four pulses, 120 mm, each measured in the pad frame), place, return, complete, from three starts; one block at
the pad edge for the arm-only branch; one block off-centre for the sweep. A demo is shown only after these.

Status 2026-09-26, release 914c1314229a-20260926T184151Z on the robot, driven from the 8771 console:

- Far-block run, start 1 of 3: PASSED. Mission `mission-88094cda64e1`, run `console-20260926T201540Z-mb3c1048`,
  outcome `placed_new`, exit 0, no retries. Block bound 48.5 mm outside the pad; the first execution refused
  `mission_capability_required` and the runtime respawned the target carrying the capability (the slot was already on
  the mobile capsule from the activation of the mission before it). Grasp closed on the block at the first close
  (retention 0.915). Base advance through the Cake-launched capability: three pulses at 0.05 m/s measured 26.6, 40.1
  and 39.7 mm of 108 needed, lateral at most 2.3 mm, distortion at most 0.93 mm, each stop confirmed. Release admitted
  with a worst-case centre margin of 43.8 mm; placed at 47.5 mm from the edge; base returned in three pulses to 2.9 mm
  along the axis and 2.2 mm sideways. The first time the Cake relay moved the robot. Evidence:
  `.local-dev/console/lekiwi-evidence/one-block-20260926/` (local, ignored).
- The activation beat, live: fourteen `mobile-demo-active-successor-abort-swap` requests answered REJECTED_BY_CAKE
  with the slot unchanged at generation 1; `mobile-demo-swapped` answered OBSERVED, slot to generation 2, a new
  `--contract v2` child, the mission resumed on its own (mission `mission-e96d6f4083e2`).
- Signature policy check from the console, after the mission: three deployed capsules, each admitted under the
  deployment key and refused under a throwaway key with `signature_key_id_not_trusted`, six rows as expected, the
  unsigned-twin row skipped (none staged), 49 ms.
- Gap found (mission `mission-e96d6f4083e2`, run `console-20260926T191919Z-m8c5f9e9`): a block 44 mm nearer than the
  proven grasp radius was not refused before motion; the pregrasp clamps forward to 0, the three closes found nothing,
  and the retries advanced forward. `grasp_missed`, the arm held, the base never moved. Next fix: refuse at PREGRASP
  when the coarse `cube_out_mm` lies below the align loop's radial capacity, with an operator sentence; a reverse for
  pickup is a separate primitive to qualify.
- Far-block run, start 2 of 3: PASSED. Mission `mission-c350ab3ee492`, run `console-20260926T203744Z-m2476137`,
  outcome `placed_new`, exit 0, one automatic align retry. Block bound 22 mm outside the pad and 93 mm left of its
  centre line, yawed about 30 degrees; coarse pregrasp pan minus 30 degrees, 20.5 degrees from the home pan, inside the
  25 degree return bound; grasp closed at the first close (retention 0.863). Base advance: three pulses measured 23.5,
  42.1 and 14.9 mm of 82 needed; release admitted at 43.1 mm; placed at 51.1 mm from the edge; base returned to 0.1 mm
  along the axis and 9.1 mm sideways, inside the 10 mm tolerance but close to it (sideways drift of 4 to 5 mm per
  reverse pulse, to be watched). Evidence in the same directory.
- Far-block start 3: two Starts refused at SEARCH before motion (missions `mission-1b280ba59526` and the one before it,
  20:58 and 21:08 UTC), `mission_binding_candidate_not_placeable`: the block stood 163 mm right of the pad's centre
  line and 62.5 mm outside the near edge (pad x +0.163 m, y -0.210 m). Re-derived offline from the real functions at
  ebe8e7d, the block hit BOTH limits of the placement rule, which asks 58.96 mm of raw anchor margin from every edge
  after the advance (37.96 required centre margin + 15 buffer + 6 anchor uncertainty): the side margin was 44.5 mm
  against 58.96, and along the reach axis the smallest admitting advance was 121.6 mm (122 on the 1 mm grid) against
  the 120 mm bound. The y band admits a block at most 61.0 mm outside the near edge; this one stood 62.5. A side rule
  alone does not reach that spot: the along-axis bound stands as well. Physically the pick and the place are easy; the
  executor's envelope is the limit. The operator's ruling (21:15 UTC): acceptable for now, the placement must be
  generalized next. Not yet run: far-block start 3 inside the envelope, the arm-only branch, the sweep. Both decisions
  are pinned in `autonomy/tests/test_run_single_cube_cycle.py` (RecordedDecisionPins).

### T6 generalize the placement (next; robot runtime release, then qualification)

In order of size, each behind its own tests and qualified before a demo, the motion primitives of the proven
controller unchanged unless a step says so:

**Design of record (2026-09-27): edge-projected placement rule now, near-block refusal now, the reverse and the
sideways motions behind qualified words.** Synthesized from three designs and three judges. Every number below was
re-derived from the real functions at `ebe8e7d`: the proven rule asks 58.96 mm of raw anchor margin from every edge
after the advance (37.96 required centre margin + 15 buffer + 6 anchor uncertainty; `base_advance_needed_mm`,
`release_margin_worst_mm` in `bin/laptop/manipulation/run_single_cube_cycle.py`), the release admission asks 43.96 raw
on every edge, the two placed runs plan 108.0 and 82.0 mm under the proven rule and under the projected rule, and the
refused start 3 hit both limits (side 44.5 against 58.96; along-axis need 121.6, 122 on the grid, against
`BASE_MAX_TOTAL_ADVANCE_MM` 120). Step 1 does not rescue that spot; only step 4 or a ruling on the bound reaches it.
Constraint for every step: `autonomy/tests/test_cycle_behavior_preserved.py` compares stdout, the transition record and
every `send_action` payload against the tagged controller, so under the default envelope word no state payload and no
printed line may gain a field; new numbers go into the events predicates' measurements, and into states and the summary
only under a word. The envelope word: `KC_PLACEMENT_ENVELOPE` (runtime config key `placement_envelope`, a list of words,
exported to the child's environment only; the argv is unchanged), read once in `_run` beside `KC_RUNTIME_MODE`; absent
means the proven envelope and every decision is byte-identical; an unknown word, or a list without `projected`, is
refused before the robot is constructed. A LIVE run naming a word `PLACEMENT_ENVELOPE_QUALIFIED` does not name is refused
before the robot is constructed (`placement_envelope_not_qualified`) unless the config also names
`placement_envelope_attended`, the session label of an attended qualification run, which the run stamps into its record
(`PLACEMENT_ENVELOPE` state, `placement_envelope` in the summary). A test allows a word into
`PLACEMENT_ENVELOPE_QUALIFIED` only when a `docs/autonomous-demo/placement-envelope-qualification-*.md` carrying the
copyright line names it. The set is empty on this branch.

1. **1a. Pins first (DONE, `7246f76`).** The recorded decisions of 2026-09-26 in the always-run suite
   (`autonomy/tests/test_run_single_cube_cycle.py`, `RecordedDecisionPins`: 108.0 and 82.0; start 3 None with side 44.5
   and along-axis 122; the grasp miss's coarse `cube_out` -44.5 with forward clamped at 0; the reference pixel 11.4 and
   24.4) and a source-hash pin of the proven primitives (`autonomy/tests/test_frozen_primitives.py`: `run_pose`,
   `base_pulse`, `measured_base_pulse`, `advance_base`, `_advance_base_held`, `return_base`, `base_displacement`,
   `base_posture_refusal`, `gripper_refusal`, the pregrasp clamp line, every `BASE_*` constant, the placement constants
   and the gripper band). `base_advance_needed_mm` is the rule and is frozen by binding only: the loop and the approach
   state call it by name. Qualification: none, offline evidence only.
2. **1b. The edge-projected rule behind `projected` (BUILT, `5ae6c83`; qualified before a demo).** For each pad edge
   with outward normal e the moved anchor's raw margin must satisfy margin - 6 >= 37.96 + 15 |d . e| + allowance: the 6 mm
   anchor uncertainty stays on every edge, the 15 mm buffer (the lagging arm, along the reach direction only) is
   projected onto each edge normal, and the plan-time side drift allowance `PLACE_SIDE_DRIFT_ALLOWANCE_MM` 6.0 (recorded
   sideways totals over one advance: 3.8 and 5.7 mm) is charged on the edges the reach axis runs along by the plan-time
   callers only; the loop asks no allowance because there the drift is measured, and the release admission is unchanged
   at 43.96 raw on every edge. This corrects the T6 wording of 2026-09-26: the 6 mm stays on the sides because the
   release admission already asks it there. Under `projected` the recorded starts still plan 108.0 and 82.0; start 3
   stays refused; (0.157, -0.150) with the axis along +y needs 62 mm (proven None); a 30 degree axis needs 51.46 on the
   sides and 56.95 along the axis. The refusal values and the `placement_infeasible` event carry `side_margin_mm`,
   `required_side_margin_mm`, `required_axis_margin_mm` and `axis_advance_needed_unbounded_mm`, so a start-3-class
   block reads as 1.6 mm past the along-axis limit and never as a side rule that would have fixed it. The record fields
   `side_margin_after_advance_mm` (the `release_margin` predicate) and `base_aside_mm` (every measured pulse's lateral,
   forward and reverse; the `base_returned` predicate) are in the events under every word and in the states only under
   a word. Why a pre-advance arm pan cannot widen anything: `base_posture_refusal` holds every scan joint including the
   pan within 6 degrees of the saved low pose before every pulse, and the base drives along its own x axis whatever the
   pan. Qualification (plan 14): READ-ONLY LIVE (an observe from the low pose with the block about 52 mm inside a side
   edge and 35 mm outside the near edge, no motion; the plan computed offline under both words) -> SUBTASK (one attended
   far-block run from that position with the config naming `projected` and the attended label, the operator present) ->
   REPEATED (three starts across the widened band) -> the qualification record and, in the same commit, `projected`
   added to `PLACEMENT_ENVELOPE_QUALIFIED` and the allowance re-sized from the recorded `side_margin_after_advance_mm`
   rows. Only then may a demo config name the word.
3. **2a. The near-block refusal (DONE, `9e5c5a9`).** `PREGRASP_MIN_CUBE_OUT_MM = -PREGRASP_OUTWARD_BIAS_MM` (-13): the
   pregrasp clamps `forward_mm` to max(out + 13, 0), so a nearer block has its pregrasp clamped and ALIGN can only
   advance, and the jaws close 14 mm beyond the ALIGN estimate by design, so it is closed on short of its centre (run
   `m8c5f9e9`: cube_out -44.5, three empty closes, `grasp_missed`). SEARCH refuses such a block right after the
   pan-bound refusal, after the selection is written back and before anything moves: `search_cube_inside_pregrasp_reach`,
   predicate `pregrasp_cube_within_reach`, values `cube_out_mm`, `min_cube_out_mm`, `move_block_at_least_mm`; the web
   glossary carries the code. The threshold is a geometric argument from the clamp, not a measurement: the recorded
   points are +11.4 and -44.5. Qualification: READ-ONLY LIVE (a Start with the block 40 mm nearer than the proven grasp
   radius: the refusal and the sentence in the console, no motion). The first attended run with a block between -13 and
   0 is a new envelope row, and the threshold moves toward 0 if that run misses.
4. **2b. The bounded pickup reverse before PREGRASP through the proven reverse primitive (word `near_reverse`, OFF; NOT
   BUILT in this round).** Under the word, when 2a would refuse: a run without a base primitive raises
   `mission_pickup_reverse_required` at SEARCH after the selection is written back (final state TARGET_SELECTED, nothing
   moved), and `mission_execution._search_measured_capability`, which today respawns only a BASE_ADVANCE selection for
   `mission_capability_required`, gets a second predicate keyed on the new code so an ARM_ONLY-bound near block is
   respawned carrying the capability (`needs_relay` stays keyed on BASE_ADVANCE: the reverse is never the relay's output,
   `capability_pulse` refuses vx <= 0). A run carrying the base primitive reverses through the UNCHANGED `base_pulse` and
   `measured_base_pulse` (needed = 11.4 - cube_out, at most 2 pulses and 65 mm, each measured against the pad like
   BASE_RETURN, state SEARCH_REVERSE), then SEARCH runs AGAIN from a fresh observe (a new coarse, anchor, pad frame and
   selection that must name the same cube within the match radius, else `pickup_reverse_lost_the_cube`): the cube at the
   grasp radius is established by that second measurement, never by the commanded reverse. The return target is the
   post-reverse reference; `return_base` is fed the measured advance only, so its forward branch is never fed the reversed
   amount; the summary carries `base_start_offset_mm = -reversed`. A forward return to the true start is not taken (a
   LIVE capability run cannot resolve a v2 observation after the release). Qualification: OFFLINE -> READ-ONLY LIVE ->
   SINGLE-VARIABLE (base:-0.4 through `base_probe`, arm at the low pose, block absent, three times) -> SUBTASK ->
   REPEATED. Two operator rulings BEFORE the word is named in a LIVE config: the base ends up to 65 mm behind its start
   for this demo (plan 3.18 and 8.15 unmet), and whether the reverse counts as Cake mobility (this design says yes).
5. **3a. The sideways release without base motion (word `side_offset`, OFF; SKELETON BUILT, `e99bbbb`).** The offset is
   taken AFTER the advance at PLACE_APPROACH, never before it. Pure model beside `placement_plan`:
   `offset_release_point` (pivot P = anchor - r u, release point R(delta) = P + r rot(u, s delta), s the sign of
   `lateral_trim_deg`'s convention: a positive pan reading turns the tool right; chord r sin delta, 3.1 to 3.8 mm per
   degree at r 179 to 218; shrink r (1 - cos delta), 6.1 mm at 15 degrees) and `release_offset_plan` (the smallest
   |delta| on a 0.5 degree grid meeting the projected rule at both ends of the FK travel less
   `SIDE_OFFSET_UNCERTAINTY_MM` 4.0, inside `PLACE_SIDE_PAN_MAX_DEG` 15 and the 25 degree return bound; 0 whenever the
   rule already admits). Under the word, in the BASE_ADVANCE branch only, the one motion change is the pan argument of
   `cli.pregrasp_poses` at PLACE_APPROACH, planned by the unchanged planner and run by the unchanged `run_pose`; the
   RELEASE_OFFSET state names the plan, RELEASE_OFFSET_MEASURED the pan readback the admission is decided from (never the
   command, scaled by `SIDE_OFFSET_REALIZATION` 1.0); a release the offset point does not admit ends with the cube held.
   Not yet in this skeleton: the plan-time side requirement reduced by the offset's capacity, which is what makes a
   block with a plan-time side margin as low as about 8 mm placeable; it follows the calibration, once the realization is
   measured. Qualification: READ-ONLY LIVE (an observe with the block 30 mm inside a side edge; the plan computed
   offline) -> SINGLE-VARIABLE (block on the centre line 40 mm outside the near edge, the qualification-only runtime
   config key `placement_side_offset_force_deg` set to 10 beside `placement_envelope` ["projected", "side_offset"] (the
   runtime hands it to the controller; a runtime-launched run does not read it from the shell), refused in LIVE without
   the attended label; PLACED measured against 0.19 to
   0.22 m x sin 10 degrees = 33 to 38 mm, three times, which fixes the realization and the uncertainty; the tool height
   at PLACE lower against `RELEASE_MAX_HEIGHT_MM` 15 at each pan) -> SUBTASK and REPEATED (three starts with the block 20
   to 40 mm inside a side edge). Then the cap may rise from 15 to 25 degrees with three more starts. No base motion, so
   no Cake-mobility ruling.
6. **3b. The lateral base pulse of the holonomic base (word `base_lateral`, OFF; SKELETON ONLY, `995fe93`).** Built in
   this round: the numpy-only planner `autonomy/manipulation/holonomic_placement.py` (`holonomic_need`: the smallest
   (advance, lateral) pair under the per-edge rule the executor hands it through `holonomic_rule`, minimising
   advance + |lateral| on the 1 mm grid; start 1 (108, 0), start 2 (82, 0), start 3 None with the limits naming the
   along-axis bound, (0.163, -0.200) (112, 14.5) to the left, a lateral beyond 120 None), the executor's constants
   (`LATERAL_VY_MPS` 0.05, the magnitude the gateway clamp proved for +y.vel on 2026-09-15; `LATERAL_RATE_PRIOR_MM_S` 50;
   4 pulses; 120 mm; tolerance 8; along at most 8 + 0.45 x the lateral), `lateral_displacement` (the same 50 mm cross as
   `base_displacement`, under honest names: `aside_mm`, `along_mm`, never `advance_mm`) and
   `lateral_measurement_refusal` (the forward gates transposed). NOT built: the primitive (`lateral_pulse`, mirroring
   `base_pulse`'s refusal order; a gateway-direct twin of `make_move_base`'s reverse closure with vy signed and vx 0,
   exposed by a new signature, never through the vx one-liner, so every existing pulse stays byte for byte; a
   supervised `base-lateral:SIGNED_S` action refused in LIVE like a forward base action), the LATERAL_MEASURED state,
   the sequence (lateral first, then the unchanged advance loop fed the laterally moved measured anchor, the return on
   both axes within 10 mm under this word only), and the PAD_FRAME inlier count against lateral offset. The Cake
   question, not decided here: the v2 relay refuses vy != 0 and the v2 schema fixes y_m at 0; a lateral through the
   executor in a LIVE mission run needs a ruling on whether it counts as Cake mobility, and a lateral through the
   capability needs a contract v3. Qualification: not LIVE before that ruling and a record; then plan 14 from the top
   (landmark inliers at 0, 60 and 120 mm sideways from stored frames and observes, no motion -> base-lateral +0.2 and
   -0.2 with the arm at the low pose and nothing held, then +0.5 and -0.5 for the rate -> the same with the cube held ->
   lateral then advance -> three starts beyond the side band of 1b).
7. **3c. Base yaw then advance: designed, not built until a ruling names its command path.** A yaw about a centre R
   behind the tool shifts the anchor sideways by R sin(theta) and back by R (1 - cos theta); R is not in the repo
   (about 0.27 to 0.30 m, never measured), `base_displacement` reports translation only (a rigid rotation keeps its
   distortion at 0), and every existing path refuses omega: `capability_pulse._judge`, the v2 schema, the proven
   closures' omega clamp 0.01 rad/s. Building it would be `base_yaw_pulse` beside `base_pulse`, a pure `base_pose_change`
   returning translation and rotation, the rotation centre measured from two pulses, the advance along the measured
   rotated axis under the projected rule, a yaw return against a saved start axis (plan 8.15's T_pad_base_start, which
   does not exist today), and either an in-process yaw (which the 2026-09-24 ruling does not cover) or a contract v3
   with theta. It buys about 1 mm per degree over 3a at the price of a contract change and an unqualified pad-frame
   validity from a rotated low pose. Nothing of it is written; `autonomy/tests/test_capability_pulse.py` pins that the v2
   relay refuses every yaw and every lateral (`TheRelayRefusesEveryYaw`). Not before 1b, 2b and 3a are qualified.
8. **4. Advance to the bound, then reach the rest with the arm (word `reach_after_advance`, OFF; NOT BUILT).** The only
   along-axis widening and the only route to start 3's class. Under the word `base_advance_needed_mm` may return the
   bound (120) when the residual per-edge shortfall s satisfies s x 1.6 <= `GRASP_POSE_MAX_FORWARD_M` x 1000 - grasp_out -
   `PLACE_LEAD_MM`, and the PLACE_APPROACH target becomes grasp_out + 8 + s x 1.6, inside the existing 60 mm clamp; the
   release admission is unchanged and measures the realized travel at both ends, so a lagging arm ends with the cube held.
   Numbers: s at most 25.4 mm at the reference grasp radius 11.4 (32.5 at grasp_out 0, 13.75 at 30); start 3 needs s =
   1.6 (2.6 mm of wanted travel). Both recorded releases after an advance realized about -8 mm of travel, so this rests
   on a release radius no recorded run reached: its own calibration (PLACE_APPROACH at 20, 40 and 60 mm beyond the
   proven grasp radius with the cube held, the tool height and the realized travel measured) -> SUBTASK (about 75 mm
   outside the near edge) -> REPEATED -> the record names the word.

Envelope after each step. Frame: PAD_FRAME, anchor = the cube centre measured at SEARCH, pad 415 x 295 mm, the reach
axis about +y. BEFORE (`ebe8e7d`): 58.96 mm of raw margin from every edge after the advance; side band |x| <= 148.5 mm;
along the axis at most 61.0 mm outside the near edge by at most 120 mm of forward-only advance in at most 4 pulses at
0.05 m/s (measured 49 to 57 mm/s, 14.9 to 42.1 mm per pulse); ARM_ONLY from 15 mm outside the near edge at grasp_out 0;
a cube already 17.96 mm inside is done; rotation 0, lateral 0; pickup: coarse cube_out clamps at 0 with no lower refusal,
upper 60, pan within 25 degrees of the home pan. AFTER 1a: nothing moves; the record says start 3 hit both limits. AFTER
1b (qualified): plan-time side band |x| <= 157.5 mm (49.96 raw with the 6 mm allowance), admitted after a measured
advance down to 43.96 raw on the sides (|x| <= 163.5); along the axis unchanged; starts 1 and 2 plan the same 108 and 82;
start 3 stays refused. AFTER 2a: pickup band stated and enforced, cube_out in [-13, +60] mm about the proven grasp
radius; a nearer block is refused before any motion. AFTER 2b (qualified, a run carrying the capability): cube_out down
to about -53 mm by at most 65 mm of measured reverse in at most 2 pulses; the base ends that far behind its start (a
ruling). AFTER 3a (qualified, cap 15 degrees, with the plan-time capacity): the release offset sideways by r sin(15) less
4 mm = 42 to 52 mm toward the side with pan budget, a block placeable with a plan-time side margin as low as about 8 mm
at r 179; at the 25 degree cap 72 to 88 mm. AFTER 3b (if ruled, built and qualified): plan-time side band |x| <= 277.5 mm
with 120 mm of lateral in at most 4 pulses; return on both axes within 10 mm. AFTER 3c: 0 degrees of base rotation in
every step of this record. AFTER 4 (qualified): along the axis up to 86 mm outside the near edge at grasp_out 11.4;
start 3's class (62.5 mm outside) is reached by this step only. Speeds unchanged at 0.05 m/s on every axis;
`BASE_MAX_TOTAL_ADVANCE_MM` 120 and the v2 x_m ceiling 0.12 untouched throughout.

Risks kept on record: the 6 mm plan-time allowance halves the widening at plan time (9 mm per side instead of 15) and
rests on two runs' sideways totals; without it the first widened run could fail closed by 0.5 mm after 2.3 mm of drift
per pulse; it is re-sized in the qualification commit. 2b's runtime seam must be written and tested first, or an
ARM_ONLY-bound near block is never respawned with a base primitive; the re-run of the selection after the reverse is
inferred and must be checked in `target_binding` before building. 2a's -13 is the clamp, not a measurement: blocks between
-13 and 0 are unknown territory. The plan-time projection uses the camera axis, the loop the measured one; both recorded
runs were within 5 degrees of +y and the 30 degree test pins the projection's growth. 3a admits from a model whose
realization and uncertainty are unmeasured; the forced-offset calibration comes first. 3b's realized lateral rate, sign
and pad-frame validity at 120 mm are unmeasured. Step 4 credits release travel through `FK_TRAVEL_OVERSTATEMENT`,
measured on loaded carries, not on the PLACE descent.

The overnight plan after the live session (stage policy review, tracks, morning order): `overnight-plan-2026-09-26.md`.

## 4. Envelope, stated honestly

"From anywhere" today means: inside the low-pose wrist frame (its sweep sectors only under the unqualified word
`search_sweep`), not cut by the frame border,
with a valid pad frame, and placeable by the rule of the proven controller: the cube centre measured at SEARCH must come
to 58.96 mm of raw margin from every pad edge after a forward-only base advance of at most 120 mm in at most four
pulses. The 120 mm is the pulse budget, not the block position: with the reach axis about +y, a block may stand at most
61.0 mm outside the near edge, and the side band is |x| <= 148.5 mm (pad half-width 207.5 less 58.96). No rotation, no
lateral base motion, no navigation. Plan section 10 gets its first measured rows from T5.

## 5. Gates and baselines (2026-09-26, this checkout)

- `tests/test-docs.sh`: two pre-existing em-dash failures in `physical-pass-2026-09-18.md` and
  `two-cube-mission-design.md`; nothing new may fail.
- `python3 -m pytest autonomy/tests`: 1104 passed, 12 skipped.
- `python3 -m pytest packages/demo-runtime/tests`: 897 passed, 1 skipped.
- `python3 -m pytest apps/console-api/tests`: 223 passed, one pre-existing failure in `test_live.py`
  (`test_telemetry_reports_what_the_runtime_actually_has`), to be explained, not hidden.
- `pnpm -C apps/console-web typecheck`: clean. `pnpm -C apps/console-web test` under node 22 (`.nvmrc`; `nvm use
  22`): 881 passed in 56 files. Under node 18 the same suite reports 79 failures, all environmental.
- Every commit: `Co-Authored-By: Claude <noreply@anthropic.com>`, one concern per commit, never on `main`.
- Baselines of `track/placement-envelope` after T6 steps 1a, 1b, 2a and the step 3 skeletons (2026-09-27, from
  `ebe8e7d` at 1145 / 942 / 238 / 917): `tests/test-docs.sh` the same two em-dash failures; autonomy 1203 passed, 14
  skipped; demo-runtime 948 passed, 1 skipped; console-api 238 passed (one chained run showed
  `test_live.py::TestReplayOnlyStack::test_reading_works_and_every_command_is_refused` failing once; it passed alone and
  in a full rerun, and is recorded here rather than hidden); console-web typecheck clean, 918 passed under node 22.

## 6. The demo, as the operator presses it

1. With the robot: `lekiwi-deploy.sh stop`, camera service stop, `check`, `arm` (torque on, the operator present),
   camera service start, robot runtime restart, perception service on the desktop. These stay at the terminal on
   purpose: they put torque on the arm.
2. Browser, 8771: reload, Confirm this robot, Return arm.
3. Cake panel: Signature policy check (T3), then the slot check with the abort-swap candidate (rejected, unchanged)
   and the swapped candidate (observed, next generation).
4. One block anywhere in the band of section 4. Start.
5. If the block is within arm reach: pick, place, return, complete, no wheels. If not: the mission holds for the
   capability; Activate this candidate; the robot picks the block, advances the base through the Cake-launched
   capability, places, returns, complete. If the block is out of view: the run is refused with nothing moved, unless
   `search_sweep` is qualified, in which case the sweep finds it first.
6. Stop at any moment ends the program in software; the servo power switch is the stop of last resort.

## 7. Overnight status (2026-09-27, `integrate/overnight-2026-09-26`)

Per track of `overnight-plan-2026-09-26.md`: what is on the integration branch, what is not, and what only the robot
can settle. No stage word is qualified: every QUALIFIED set is empty, because the robot was powered off all night. The
record of the final merges, the two fix rounds on the adversarial findings, their gate tails and every item not done
is `overnight-integration-2026-09-27.md` (the second fix round is its section 7, the items not done its section 8).
The proven motion primitives are byte-identical to the release `914c131`, not to the 2026-09-20 tag (that record,
section 2).

PM (placement build, merge `8b8d5df`). Delivered: the pins of 2026-09-26 and of the proven primitives, the `projected`
rule gated in LIVE, the near-block refusal before motion, and the step 3a and 3b skeletons (`side_offset`,
`base_lateral`). Not done: step 3 beyond its skeletons. Robot: the design of record's schedule for `projected` and
`side_offset`.

P0a (stage seams, merge `7048491`). Delivered: `run_cycle` split into five stage modules plus `stage_policy.py`, the
policy identity on the record, one word mechanism refused before the robot is constructed, the inert `--do scan`
seam and the stage harness. Robot: the first plan-only Start must print the same plan as the release of `914c131` on
the same scene.

P0b (runtime, contract and web seams, merge `60412d2`). Delivered: `placement_envelope` accepting any distinct word of
the right shape, `scan_scene` as a motion command mapped to `--do scan`, one web stage file per stage. Robot: none.

P0c (recorded-decision replay, merge `193f3a3`). Delivered: the replay harness, the always-run pins and the local
replay of runs mb3c1048 and m2476137 (green with `KC_EVIDENCE_ROOT` set). Not done: the records of 21:24 and 21:31
are on the robot and are not in the replay. Robot: pull those two records first.

A (pregrasp and align, merge `45c51ad`). Delivered: the ALIGN view model, `coarse_v2` and `align_v2`, the geometric
test world, the replay of the ALIGN views of 2026-09-26, the web align states; since the fix round (`fix/ovn4-safe`,
merge `243a0c2`) `align_v2` never commands the tool inside the inner bound (0 mm, or -25 mm under `near_grasp`),
checked on commanded targets only. Robot: the replay of failures 6 and 7 from their pulled records (if it does not
give the re-centre decision, `align_v2` is not qualified that day), then the read-only captures, the ALIGN-only runs
at the listed offsets and the failure 6 and 7 geometries.

G (grasp, merge `796ba36`). Delivered: `near_grasp`, `grasp_measured_retry`, `latch_midpoint`, `grasp_recover`, the
straddle evidence and the last view on grasp failures, the replay against the 19:19 session. Robot: the near grasp
pose at -10, -20 and -25 mm with no block first, then the block at -5, -10 and -15 mm, the measured retry with the
block moved by hand, and the latch class on the next five closes.

S (scene scan and search, merge `9604c59`). Delivered: `scene_scan.py` (the pan-axis fit and the fused scene),
`classify_block`, `--do scan` under `scene_scan` and `scene_scan_25`, SEARCH from the scene under `scene_search`, the
fit replayed on the archived pair of run m2476137; since the fix round the old SEARCH sweep runs only under the word
`search_sweep` (merge `243a0c2`). Robot: the `search_sweep` qualification of T2 before any demo relies on it; a
plan-only scan, an attended scan at +/-15 deg with no block, the block at five spots, then +/-25 deg, then three
Starts under `scene_search`.

P (placement, continued, merged with the fix round as `fix/ovn4-p`, merge `57bf874`, and the controller wiring
`d801068`). Delivered: the envelope solver, the refusal explanations (the deciding limit, the shortfall, the hint, the
hint's side named left or right as seen from the robot) on the mission binding refusals and in the SEARCH refusal's
values, the per-edge admission values under a word, the web placement clause; since the second fix round
(`fix/ovn5-rt2`, merge `1ad0cad`) the runtime holds the explained binding refusals for the operator with Stop only and
carries the controller's sentence as the hold's `detail`. Not done: `reach_after_advance` and `placed_multiview` are
not declared words; `placed_multiview` and the measured correction pulse are not built; the head's console does not
read the hold's `detail`. Robot: the design of record's schedule for the placement words.

R, runtime part (merge `2a8d9e2`, fixes in `fix/ovn4-rt`, merge `f05bd83`, and `fix/ovn5-rt2`, merge `1ad0cad`).
Delivered: the attempt classes, the hold for the operator with its evidence, the `mission_retry` operation and route,
the activation verdict for a refusal before any transaction, the automatic park and retry shipped off; in the fix
round, the grasp words' nothing-held endings held for a Retry (a held cube never), a Retry asked `block()` before it is
written, a refused Retry a 409; in the second fix round, the Retry switch `mission.retry`, which ships off: a LIVE
hold offers only Stop, and the runtime refuses the Retry and the park (`mission_retry_not_enabled`,
`mission_retry_not_attended`, both 409), unless the configuration sets `mission.retry: true` and names the attended
label; the refused-lift and refused-approach grasp endings and `align_inside_grasp_radius` hold with no Retry; the two
scene endings hold for a block move. Not done: a proven-path `grasp_missed` whose jaws did not reopen is still classed
retryable (offered only with the switch and the label on); no run record names the label that admits a Retry. Robot:
with `mission.retry: true` and the attended label, a provoked failure ends in the hold with its stage, measurement and
frame; Retry pressed: the park, then the fresh attempt; Stop during the park and during the hold. Without the switch,
a provoked failure holds with Stop only.

R2, controller part and the park (merged at `42980b0`, reverted at `f6c1a6e`, restored by `fix/ovn4-rt` with the
no-park test given its own supervisor, merge `f05bd83`). Delivered: `Supervisor.start_mission_park`, `return_2d`
(unqualified) and the return replay. Not done: the controller passes no SEARCH view to the return policy, so
`base_return_error_2d` is never computed in a run. Robot: the `return_2d` probes if ruled.

C (Cake showcase, merge `9d13fc2`). Delivered: `REFUSED_BEFORE_TRANSACTION` read as Cake's judgement, ValidatePlan of
the exact bytes and the state path, candidate roles with nothing preselected, participation derived from the mission
record, the Cake panel, the install's prepared candidates, the opt-in resident test, the host outside Cake named.
Robot: the Cake beats with the host held, then one far-block mission whose sentence names the generation and pid.

U1 (console screen, merge `64d7a1c`). Delivered: the demo screen as the default view (classic behind
`?screen=classic`), the failure banner, the stage timeline across a target's runs, the Ready checklist, the kept
picture through a stream gap, Try again posting the mission retry. Robot: on 8771 the timeline's numbers against the
run's [STATE] lines, and one attended demo from the main screen without Diagnostics. Not merged: the fix
branch `fix/ovn4-web` on the honesty findings did not pass verification in either round (at `4b4ae5d` one must-fix
remains: a tab opened after a completed Scan scene draws it as a failure), so those findings stay open at the head, and
the head's console shows Try again on beside a LIVE hold that offers only Stop (the integration record, section 8).

U2 (scenarios and scene map, merge `bf2bab0`). Delivered: the scenario catalogue of 2026-09-26, the scene map folded
apart from the reducer, the console-api fixture-directory hook. Robot: none.

L (live view, merge `b6b1c37`). Delivered: the wrist preview at 5571 with no forward, the camera service's gap
counters in the preview, a decision capture no longer read as a stall, the read-only stall probe. Robot: runtime
restart with the preview section, 8771 live, the probe during a plan-only Start and one attended run.
