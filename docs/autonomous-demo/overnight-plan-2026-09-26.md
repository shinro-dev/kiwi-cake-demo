# The overnight plan of 2026-09-26: stage policies, generalization, a simpler console that shows Cake

Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

The robot is powered off and charging. This plan turns the operator's brief after the live session of 2026-09-26 and
seven read-only stage reviews into tracks that run overnight, offline and in parallel, each with its own files, tests
and acceptance, and into the attended sequence that qualifies them in the morning. It implements nothing and authorizes
no actuation: every robot step below is run only with the operator present and each test authorized by the operator
(`AGENTS.md`). Each fact is marked VERIFIED (read in the code at `ebe8e7d`, recomputed with the controller's own
functions, or read in the recorded evidence), INFERRED (derived, basis named) or UNKNOWN. The controller
`bin/laptop/manipulation/run_single_cube_cycle.py` is cited as `cycle:<line>`, line numbers at `ebe8e7d`.

Plan of record: `autonomous-single-cube-plan.md` (sections 3, 8, 10, 13, 14). Work order: `one-block-demo-2026-09-26.md`.
Handoff: `cake-console-handoff-2026-09-23.md` sections 15 to 17. Evidence (local, ignored):
`.local-dev/console/lekiwi-evidence/one-block-20260926/`.

## 1. The brief and what today showed

The operator's brief (binding): the performance disappointed; the console makes debugging impossible; the Cake layer
should make it easy to show errors and fix them; the policies fail when the block is not perfectly aligned; Observe
does not scan for the block; there is no versatility; each stage of the demo needs a policy review; the console should
be simpler so that it shows Cake's power. The ruling of 14:15 UTC stands: one block from anywhere, the robot decides
arm-only or base by measurement, it scans when the block is not in view, the Cake resident on the robot, signature
verification and a live module update shown, the browser drives the whole demo.

| # | What happened (run) | Cause in the code (VERIFIED unless marked) | Track |
|---|---|---|---|
| 1 | 19:19 `grasp_missed` (m8c5f9e9) | The coarse pregrasp put the block at cube_out -44.5 mm; ALIGN measured it at about -17.9 (tool -2.6 + radial -15.3). APPROACH clamps the grasp target at 0 (cycle:1749) and `proven_grasp_pose` refuses forward < 0 (cycle:340); every empty close retries +14 mm OUTWARD (cycle:1780): closes at -3.1, +4.5, +8.1 mm while the block lay nearer | A, G |
| 2 | 20:15 placed, start 1 (mb3c1048) | Works: 108 mm planned, pulses 26.6, 40.1, 39.7, placed 47.5 mm from the edge | pins |
| 3 | 20:37 placed, start 2 (m2476137) | Works. The 9.1 mm "sideways" return is measured against the last pulse's axis (cycle:1183-1184) and never gated: BASE_RETURNED gates the along-axis value only (cycle:1917). The return review's replay of the recorded homographies puts the physical error near 2.4 mm (INFERRED, pinned by track R) | R |
| 4 | 20:58, 21:08 refused, block (+0.163, -0.210) | Side margin 44.5 mm against 58.96 required, and no advance along the recorded axis admits it (recomputed); the pickup lies about 41 deg from the home pan (INFERRED from one pan-axis fit) beyond the 25 deg return band (cycle:454). The binding swallows the reason into mode None (cycle:1421-1435) and the refusal carries counts only (`target_binding.py:377-379`) | P, S, U1 |
| 5 | 21:17 refused, (+0.064, -0.220) | 131.6 mm of advance needed along start 1's recorded axis (recomputed) against the 120 mm budget | P |
| 6 | 21:24 `align_cube_not_detected` | ALIGN accepts on the lateral value alone (cycle:483, 1722); it descended with radial +14.5 into a view without the block; a missing block ends the run at once (cycle:1711-1716); any non-placed outcome ends the mission (`mission_coordinator.py:382-388`) | A, R |
| 7 | 21:31 `align_cube_not_detected` | The same with radial +12.4; the descent frame shows the block clipped at the top edge, blurred, under the arm's shadow (`frame-obs-fail.png`). The records of 6 and 7 are on the robot only | A, R |
| 8 | Observe: one frame, no scan | `observe` is one capture with no command (cycle:912-939, 3188); SEARCH_SWEEP runs only when the centre frame has no cube with a contact pixel (cycle:1255-1257). The failed outline fit is presentation only: decisions use the print-landmark pad frame (cycle:994-998) | S, U1 |
| 9 | Console | The failure sentence and the decision frame live in drawers (`Drawers.tsx:505, 522`); the pad map is not mounted (`PadScenePanel.tsx` is imported by a test only); candidate keys are sorted (`cake_activation.py:104`) and the first is preselected (`capabilityCheck.ts:376`, `Screen.tsx:800`), which is the abort-swap candidate; 8771 has no preview (the robot runtime's configuration says `preview null`, handoff 15.1); the cause of 8770's stall is UNKNOWN | U1, U2, C, L |
| 10 | Bring-up | The arm was not acquired after a power cycle and a stand-alone host was started by hand; nothing on screen named the missing prerequisite | U1, C, morning protocol |

## 2. Architecture decision: stage policy modules first (P0)

### 2.1 The hypothesis, checked

Hypothesis: every stage's decision logic lives inside the controller, which makes parallel work conflict and makes a
per-stage fix impossible to ship or show on its own. CONFIRMED, with one refinement.

- `Cycle.run_cycle` (cycle:1633-1933) is one method of about 300 lines that sequences SEARCH through BASE_RETURN. Every
  acceptance test, retry budget, retry direction and failure code of those stages is written inline in it (the ALIGN
  loop cycle:1709-1746, the grasp retry cycle:1769-1783, the release admission cycle:1872-1891). The formulas the
  stages share are module functions of the same 3238-line file (`coarse_pregrasp_from_low_pose` cycle:457,
  `alignment_from_bbox` cycle:471, `closed_on_something` cycle:404, `placement_plan` cycle:582,
  `base_advance_needed_mm` cycle:597).
- All seven reviews change that file and `autonomy/tests/test_run_single_cube_cycle.py`; the placement workflow building
  tonight changes the same file (three commits and about 200 uncommitted lines on `track/placement-envelope` at
  22:05 UTC). Parallel tracks on it would conflict line by line.
- A fix cannot be shown on its own: a run's record names the whole controller bundle's digest, never the rule that
  decided a stage. The contract already has the place: `predicate.evaluated` carries `rule_ref` and `rule_digest`
  (`packages/demo-contracts/schema/v1/event.schema.json`), and the console already parses them
  (`apps/console-web/src/model/types.ts:85-86`); the controller never fills them.
- What must not move is already pinned: the proven motion primitives by source hash (`autonomy/tests/test_frozen_primitives.py`,
  committed on `track/placement-envelope`: `run_pose`, `base_pulse`, `measured_base_pulse`, `advance_base`,
  `_advance_base_held`, `return_base`, `base_displacement`, `base_posture_refusal`, `gripper_refusal`, the BASE_*
  constants, the gripper band), the frozen perception by sha256 (`autonomy/perception/remote_perception.py:81-91`,
  which includes `cube_recognizer.py` and `frame_illumination.py`), and the default path by the differential test
  against the tag `single-cube-cycle-pass-2026-09-20` (`autonomy/tests/test_cycle_behavior_preserved.py`; the tag is
  present in this repository).

Refinement: a stage policy is not only a pure decision function. Today's failures are sequencing decisions: what to do
after a clipped view (7), which way to retry after an empty close (1), whether to look again before giving up (6, 7).
The unit extracted is therefore the stage PROCEDURE, its decisions and its bounded retry sequence, written against a
context of primitives. The primitives stay where they are.

### 2.2 The decision

P0 splits the controller into a thin orchestrator and five stage policy modules plus one shared module, without
changing behaviour:

| Module (`autonomy/manipulation/`) | Stages it decides | Owner after P0 |
|---|---|---|
| `policy_search.py` | Scan scene (new action), SEARCH, SEARCH_SWEEP, the selection and binding of the block, the reachability class | S |
| `policy_align.py` | the coarse pregrasp, the pickup band (the near-block refusal), PREGRASP, ALIGN and its non-contact descent | A |
| `policy_grasp.py` | APPROACH, STRADDLING, GRASP_CLOSE and its retries, OBJECT_SECURED, the gripper predicates | G |
| `policy_placement.py` | PLACEMENT_PLAN and its held twin, CARRY, the placement rule the base loop calls by name, PLACE_APPROACH, RELEASE_ADMISSION, PLACED | P |
| `policy_return.py` | BASE_RETURN's policy and the end-of-run predicates | R |
| `stage_policy.py` | the context protocol, `CycleFailed`, the policy identity, the word mechanism | P0a, then integration only |

What stays in the controller: the command line and the action dispatch; the robot, camera and perception wiring;
`run_pose`, `grip`, `observe`; the base pulse family (pinned) and the capability relay; `return_to_low_pose`; the
geometry gates the stages call (`cartesian_step_pose`, `proven_grasp_pose`, `pan_step_pose`,
`proven_orientation_pose`, `tool_state`); the events object; and `run_cycle` as an orchestrator of five calls. A stage
module never imports the controller: the controller runs as a script, so an import would load a second copy with its
own globals. The controller hands each stage a context (the `Cycle` object) and a bundle of its geometry functions.
Every moved name is imported back into the controller, so `effective_constants()` (which reads the controller's
globals, cycle:2058-2077), the source-hash pins and every test that reads `cycle.<name>` see the same objects.

Mission recovery is not a stage module. It is composed in the runtime from unchanged controller actions (park with
`--do exit`, then a fresh `--do cycle` whose SEARCH observes anew), so each attempt is its own run with its own
record, and the controller keeps its contract that the arm holds where a failure leaves it. Stage-local retries (the
ALIGN re-acquire, the measured grasp retry) belong to their stage modules. No in-run loop across stages is built: the
search review's re-SEARCH and the grasp review's pick re-plan are both served by the runtime's hold and retry, with
one budget and one record per attempt.

After P0 a track owns one stage module, its new test files and its web stage file (section 6), and nothing else in the
controller.

### 2.3 What P0 must prove

- `test_cycle_behavior_preserved.py` green, after `git rev-parse single-cube-cycle-pass-2026-09-20` has succeeded in
  the same worktree (the test skips without the tag, and a skipped differential proves nothing).
- `test_frozen_primitives.py` and `test_frozen_perception_pins.py` green with no pin changed.
- `test_cycle_mission_mode.py`, `test_cycle_events.py` and `test_run_single_cube_cycle.py` green unchanged after the pure
  extraction commit (events byte-identical too); the autonomy count equal to the pre-P0 count plus P0's own tests,
  skips unchanged.
- The recorded-decision pins of P0c (today's inputs, today's decisions) green before and after.
- The controller bundle builds and contains every policy module (the static import closure of
  `packages/demo-runtime/demo_runtime/bundle.py`, which indexes modules by flat name across `bin/`, `autonomy/` and
  `tools/`; the new names are unused in the repository today, hence the `policy_` prefix).
- demo-runtime, console-api, contracts and web suites green.

### 2.4 Interfaces fixed by P0

Parallel tracks build against these and never change a signature another track's file calls; they add new functions.

| Producer | Interface | Consumers |
|---|---|---|
| P0a | the stage procedures `run_search`, `run_pregrasp_align`, `run_grasp`, `run_placement`, `run_return`, each `(ctx, ...)` returning a typed outcome; `policy_search.scene_scan(ctx)` behind `--do scan`, refusing before motion until S fills it | S, A, G, P, R |
| P0a | cross-stage calls: `policy_align.alignment_decision(bbox, pitch, words)`, `policy_grasp.min_grasp_forward_mm(words)` (0.0 with no word), `policy_placement.classify_for_binding(ctx, anchor, direction, grasp_out, seen)` returning the mode or None with an explanation, `policy_search.classify_block(ctx, record, pan_deg)` | S, A, G, P |
| P0a | `rule_ref` "<stage>.<selection>" and `rule_digest` "sha256:<hex>" on every stage predicate; `policy.<stage>.selection` and `policy.<stage>.digest` in run.config's summary; one predicate `capability_output_applied` per relayed base pulse (slot generation, producer pid, accepted and applied counts), where `_capability_evidence` only aggregates today (cycle:1590-1605) | U1, C |
| P0b | command type `scan_scene` (args `{plan_only}`) mapped to `--do scan`; the config keys `placement_envelope` and `placement_envelope_attended` (environment `KC_PLACEMENT_ENVELOPE`, `KC_PLACEMENT_ENVELOPE_ATTENDED`) accept any distinct word of the right shape and carry every stage's words | S, A, G, P, R, U1, the operator |
| P0b | `apps/console-web/src/model/stages/<stage>.ts`: the reason glosses, state sentences and state-to-phase entries of one stage | S, A, G, P, R |
| S | `perception.result` detectors `scene-view`, `scene-block`, `scene-pad` with named measurements (pad position, class, best observation, views seen, spread, pan, inliers, fit rotation, fit residual, axis, accepted); a scene JSON per scan in the run's directory | U2 |
| R | mission waiting reason `operator_decision`; record `mission_target_attempt` (attempt, run id, park run id, class, reason code, phase, measurements, observation ids, next actions, budget left); next actions from `retry`, `move_block_then_retry`, `return_arm`, `open_gripper_by_hand`, `return_base`, `stop`, `start`; `POST /api/mission/retry` | U1 |
| C | candidate view `{key, payload_digest, role, label, expect}`; activation outcomes `REQUEST_FAILED`, `REFUSED_BEFORE_TRANSACTION` (refusal code and label), `REJECTED_BY_CAKE` (state path), `OBSERVED` (state path, identity before and after); module card `{name, capsule_digest, config_digest, generation, state, pid, spawns, restarts, managed_by}`; the `CakePanel` component and its props | U1, R |
| L | preview slot fields `skipped_for_mission`, `published`, `dropped_at_socket` | U1 |

### 2.5 The stage policy as Cake's unit of "module version"

Yes, with limits.

- Identity: a stage policy is (stage, selection, digest). The selection is `proven` with no word, else the sorted words;
  the digest is the sha256 of the module's bytes. No semantic version numbers: the decision log's "Cake module
  identity" shows a digest or a generation as a Revision, never as a software version.
- Overnight (P0a, U1, C): every predicate a stage evaluates carries its rule and digest; run.config lists all five; the
  stage timeline and the failure banner say which rule decided; the read model attributes each stage to its component
  (every stage policy: the controller bundle, content-addressed and not signed; the base advance's commands:
  `mobile_approach` at its Revision and pid; motion: the gateway and the host under `supervisor.lekiwi`). A fix to one
  stage changes that stage's digest and no other, so a record shows exactly what changed.
- Later (the private Cake side, a new install and qualification; ruling 9): a Cake slot for the task policy whose signed
  configurations select among qualified stage words. Activating a new generation changes the words the next run reads,
  bound to the Cake-launched process by pid as the relay binds today, and a mission held after a failure retries under
  it: error, signed fix, retry, on one screen. What Cake delivers is a signed selection among policies compiled into
  the controller bundle and qualified on the robot, never code into the controller process and never a number; bounds,
  the gateway and stops are unchanged (plan 8.16, `AGENTS.md`). The screen says that a live update selects among
  pre-admitted signed versions: the resident has no live admission (`AdmitCapsule` is answered unavailable).

### 2.6 One word mechanism

The placement track built `KC_PLACEMENT_ENVELOPE` (the words of a run), `KC_PLACEMENT_ENVELOPE_ATTENDED` (the label of
an attended qualification run) and `PLACEMENT_ENVELOPE_QUALIFIED` (empty), refused in a LIVE run before the robot is
constructed unless qualified or attended (commit `5ae6c83`). P0 generalizes it without renaming it: the keys keep the
placement track's names and carry every stage's words; each stage module declares its words and its QUALIFIED set; the
runtime checks a word's shape (and, for the placement words only, the placement track's rule that `projected` comes
first); the controller refuses a word no stage declares, or a LIVE run naming an unqualified word without the attended
label, before the robot is constructed. Every behaviour change of tonight sits behind a word, OFF by default; with no
word the controller is byte for byte today's, with one exception carried by the merge of section 4: the placement
track's near-block refusal, which is unflagged because it refuses before any motion. A word joins its stage's QUALIFIED
set only in the commit that adds its record (for the placement words the placement track's
`placement-envelope-qualification-<date>.md`, for the others `docs/autonomous-demo/qualification-<word>-<date>.md`).

| Word | Stage | Opens | New motion |
|---|---|---|---|
| `scene_scan` | S | `--do scan` with views at +/-15 deg | pan only, at the low-pose shape |
| `scene_scan_25` | S | the +/-25 deg views | pan only |
| `scene_search` | S | SEARCH from the scene, the waypoint confirmation | the scan |
| `coarse_v2` | A | the refitted coarse map, a smaller outward bias | none of a new kind |
| `align_v2` | A | the 2D acceptance band, the radial step, the aimed descent, the view margins, the re-acquire | bounded steps through the unchanged gate |
| `near_grasp` | G | a grasp target down to -25 mm, inward retries | a new target range, the same gate |
| `grasp_measured_retry` | G | lift, look, re-aim after an empty close | bounded steps |
| `latch_midpoint` | G | the empty-latch class at the recorded midpoint (ruling 2) | none |
| `grasp_recover` | G | a set-down after a failed retention | a lowering with the cube held |
| `projected`, `side_offset`, `reach_after_advance`, `placed_multiview` | P | the placement design of record's steps and the multi-view PLACED | per the design of record |
| `return_2d` | R | the return along the start axis, both axes gated | the unchanged reverse pulse |
| (code constant) | R | the runtime's automatic park and one retry (ruling 1) | the proven EXIT |

## 3. Stage policy review

| Stage | Today (VERIFIED) | Seen today | New policy (word) | Track |
|---|---|---|---|---|
| Look (Observe) | one capture, no motion (cycle:912-939) | 8 | unchanged; the console states the print-landmark pad frame ("Pad located by its print, 97 landmarks") and shows the outline fit as a diagnostic | U1 |
| Scan scene | does not exist | 8 | a motion command: pan-only views at the low-pose shape, one fused scene with a class per block (`scene_scan`, `scene_scan_25`) | S |
| SEARCH | one low-pose frame (cycle:1642-1695) | 4, 5 learned only at Start; 1 accepted | one reachability class used by SEARCH, the binding and the map; SEARCH from the scene, the block confirmed in the PREGRASP waypoint frame that is captured today and ignored (`scene_search`) | S |
| SEARCH_SWEEP | two 25 deg sectors only when no contact pixel (cycle:1241-1286); never ran today | none | unchanged without a word; superseded by the scan under `scene_search` | S |
| PREGRASP | one pixel through a linear map, +13 mm outward bias, forward clamped to [0, 60] (cycle:457-464) | the coarse radial under-read by 26.6, 14.2 and 11.1 mm on the three runs | the refit on recorded pairs and a smaller bias (`coarse_v2`); the pickup band refusal on the corrected value | A |
| ALIGN | accept on lateral alone, one descent to out + 3 mm, a lost block ends the run (cycle:1709-1746) | 6, 7 | accept inside a 2D band; correct the radial before descending; aim the descent; predict the view before each downward move; never accept a clipped box; look again, then step back up, at most twice (`align_v2`) | A |
| APPROACH | target clamped at 0, the proven pose refuses < 0 (cycle:1749, 340) | 1 | grasp where the block was measured, down to -25 mm (`near_grasp`); a clamp of more than 5 mm is a refusal, never silent | G |
| STRADDLING | tool height only, no frame (cycle:1762-1767) | 1 (the straddle frame showed the block near) | the far-edge row of the straddle frame recorded as evidence | G |
| GRASP_CLOSE and retry | +14 mm outward after every empty close (cycle:1769-1783) | 1 | lift open, look, re-align, aim at the measured block, never close twice at a radius that closed empty (`grasp_measured_retry`); the latch class (ruling 2) | G |
| OBJECT_SECURED | retention >= 0.5 or the run ends with the cube held (cycle:1784-1795) | none | a set-down so the failure is nothing-held and retryable (`grasp_recover`) | G |
| PLACEMENT_PLAN | arm-only, or a forward advance to 58.96 mm raw on every edge within 120 mm (cycle:573-604) | 4, 5 | the envelope solver naming the binding constraint, the deficit and a hint; the design of record's words | P |
| CARRY (arm-only) | radial carry, base fallback in a capability run (cycle:1836-1856); never run live | none | unchanged; qualified as it is first (the work order's missing row) | P |
| HOLD_OBSERVE | one capture; an invalid pad frame fails with the cube held (inside the pinned `_advance_base_held`) | none | unchanged tonight (ruling 2) | P |
| BASE_ADVANCE (Cake) | relayed forward pulses, each measured in the pad frame (pinned) | 2, 3 work | unchanged; each pulse attributed to its Cake generation and pid in the events | P0a, C |
| RELEASE_ADMISSION | worst-case centre margin >= 37.96 mm and height <= 15 mm (cycle:1872-1891) | 43.8, 43.1 | per-edge values; one measured correction pulse when short by less than the budget left | P |
| PLACED | the EXIT frame alone (cycle:1900-1913) | 47.5, 51.1 | several stationary views, the one used stated (`placed_multiview`) | P |
| BASE_RETURN | reverse pulses along the last pulse's axis, the along-axis value gated (cycle:1192-1207, 1917) | 3 | the direct 2D error from the SEARCH frame (events); the return along the start axis, both axes gated (`return_2d`) | R |
| After a failure | any non-placed outcome ends the mission; only the capability respawn exists (`mission_execution.py:468-478`) | 1, 6, 7 | the mission holds for the operator with the stage, the measurement, the frame and the next actions; Retry parks and starts a fresh attempt; automatic retry after ruling 1 | R |
| Stop | halts in place, commands nothing after | none | unchanged; the state it left and the next actions stated | R, U1 |
| Cake beats | apply by key; sorted keys, the first preselected; `mutation_refused` reads as a failed request (`cake_activation.py:141-146`) | 9 | roles, ValidatePlan, the transaction's state path, the integrity refusal, undo, truthful participation | C, U1 |

## 4. The placement workflow's branch: merge before P0

Decision: merge `track/placement-envelope` into the integration branch BEFORE P0; P0 then extracts the merged code once.

- It edits the controller functions P0 moves (`base_advance_needed_mm`, the SEARCH block, PLACE_APPROACH) and the runtime
  files P0b changes (`config.py`, `supervisor.py`). Merged first, its changes are carried by the extraction; rebased
  after P0, every patch would have to be re-expressed in the new modules.
- Its step 1a pins (the recorded decisions and the source hashes of the proven primitives) are part of P0's proof.
- Without a word it is behaviour-preserving by its own gate (the differential test), except its near-block refusal,
  which is unflagged because it refuses before any motion.

State at 22:05 UTC: `7246f76` (1a, pins), `5ae6c83` (1b, the projected rule behind `projected`), `9e5c5a9` (2a, the
refusal `search_cube_inside_pregrasp_reach`), step 3 uncommitted. Cutoff 23:45 UTC: what is committed with green
gates is merged; later work is carried by track P onto P0, into `policy_placement.py`. If the merge conflicts on the
work order's end of section 3 (its T6 rewrite against this plan's pointer line), both texts are kept.

Two findings the placement track inherits:

- The near-block refusal keys on the COARSE cube_out, which under-read the measured position by 26.6, 14.2 and 11.1 mm
  on today's three runs (VERIFIED from their [STATE] lines). It refuses some blocks that a measured grasp could reach.
  It stays the conservative default; track A feeds it the refitted value under `coarse_v2` and track G lowers its bound
  under `near_grasp`.
- Start 3 stays refused after step 1b, as the design of record says: its side margin (44.5 mm) fails the plan-time side
  requirement and its along-axis need is 121.6 mm; its pickup lies about 41 deg from the home pan (INFERRED).

## 5. Tracks

| Id | Track | Branch | Depends on | Starts | Size | Priority |
|---|---|---|---|---|---|---|
| PM | the placement build, merged | `track/placement-envelope` | none | running | done at cutoff | 1 |
| P0a | stage seams in the controller | `track/p0-stage-seams` | PM | 00:00 UTC | L | 1 |
| P0b | runtime, contract and web seams | `track/p0-runtime-seams` | PM | 00:00 UTC | M | 1 |
| P0c | recorded-decision replay | `track/p0-replay` | PM | 00:00 UTC | M | 1 |
| A | pregrasp and align | `track/align` | P0a, P0c | after P0 | L | 2 |
| G | grasp | `track/grasp` | P0a, P0c | after P0 | L | 3 |
| R | return and mission recovery | `track/recovery` | runtime part none; controller part P0a; supervisor after P0b | now / after P0 | L | 4 |
| U1 | console screen | `track/console-screen` | seam files after P0b | now | L | 5 |
| C | Cake showcase | `track/cake-showcase` | config, read model and contracts after P0b | now | L | 6 |
| S | scene scan and search | `track/scene` | P0a, P0b, P0c | after P0 | L | 7 |
| U2 | scenarios, replay stage, scene map | `track/console-scenarios` | none | now | M | 8 |
| P | placement, continued | `track/placement-policy` (from the P0 merge; the placement build's later commits carried onto it) | P0a, P0c | after P0 | M | 9 |
| L | live view | `track/live-view` | none | now | S | 10 |
| INT | integration | `integrate/overnight-2026-09-26` | every track | 05:45 UTC | M | - |
| ADV | adversarial verification | none (reads only) | INT | 06:45 UTC | M | - |

Every track works in its own worktree, on its own branch, tests first, commits one concern at a time with the
`Co-Authored-By: Claude <noreply@anthropic.com>` trailer, never pushes, never runs anything that reaches the robot or
ports 8770 and 8771, and follows the committed-text rules (copyright line, no em-dash, no emoji, no forward-looking
wording, repository-relative paths). Every behaviour change is behind a word, OFF by default.

### P0a Stage seams in the controller

- Owns: `bin/laptop/manipulation/run_single_cube_cycle.py`; new `autonomy/manipulation/stage_policy.py` and the five
  `policy_*.py`; new `autonomy/tests/stage_harness.py` and `autonomy/tests/test_stage_seams.py`; the shared cycle tests
  (`test_run_single_cube_cycle.py`, `test_cycle_mission_mode.py`, `test_cycle_events.py`, `test_frozen_primitives.py`,
  `test_cycle_behavior_preserved.py`) only where a moved name's import must follow it, never to loosen an assertion.
- Deliverables:
  1. Commit 1, the pure extraction: every stage procedure out of `run_cycle` into its module, against the context and
     the geometry bundle; `CycleFailed` in `stage_policy.py` imported back, so every catch sees one class; every moved
     name imported back; labels, [STATE] lines, events and commands byte-identical.
  2. Commit 2, the policy identity (events only): `rule_ref` and `rule_digest` on every stage predicate, the five
     identities in run.config, `capability_output_applied` per relayed pulse.
  3. Commit 3, the word mechanism generalized in the controller (section 2.6): words and QUALIFIED sets per stage
     module, membership refused before the robot is constructed; the placement track's words, rules and tests carried
     across unchanged.
  4. The inert `--do scan` seam.
  5. `stage_harness.py`: a fake context with scripted primitive answers and a call log, so each track tests its stage
     alone in its own file; tracks extend it by subclassing in their own files.
- Offline acceptance: section 2.3.
- Robot: none of its own; the morning's first plan-only Start prints the same plan as the release of `914c131` on the
  same scene.

### P0b Runtime, contract and web seams

- Owns: `packages/demo-runtime/demo_runtime/config.py`, `supervisor.py`, `preflight.py`, `cli.py`, `read_models.py`,
  `packages/demo-runtime/tests/test_config.py` and `test_supervisor.py`; `packages/demo-contracts` (the command schema, `fixtures/command/scan_scene.json`, the generated bindings);
  `apps/console-web/src/model/failure.ts`, `reason.ts`, `phase.ts`, `command.ts` and the new
  `apps/console-web/src/model/stages/{search,align,grasp,placement,recovery}.ts` with tests; new runtime test files.
- Deliverables:
  1. The runtime side of the word mechanism: `placement_envelope` accepts any distinct word of the right shape
     (membership becomes the controller's refusal before the robot is constructed), keeps the rule that `projected`
     precedes any other placement word, and stays exported to the child's environment only, the argv unchanged;
     `config.PLACEMENT_ENVELOPE_WORDS` stays equal to the controller's placement words (the existing test).
  2. `scan_scene`: a motion command type, mapped to `--do scan`, in `COMMAND_CAPABILITIES` (so `MOTION_CAPABILITIES`,
     `read_models.py:34-38`, and the robot session gate include it), blocked while a command is unresolved, a
     preflight action kind, a run kind. It is an additive v1 command type: the fixture test requires one example per
     command type and pins the number of EVENT kinds (19), not of command types
     (`packages/demo-contracts/tests/test_fixtures.py:49, 55`); the 2026-09-21 recollection that a Cake command type is
     v2 does not cover a non-Cake command.
  3. The web stage seams: the reason glosses, state sentences and state-to-phase map split per stage; the aggregates
     equal the pre-split maps key for key (a test).
- Offline acceptance: demo-runtime, console-api, `bash tests/test-contracts.sh` and web suites green; the placement
  track's runtime tests pass, except the one that expected the configuration to refuse an unknown word, which now
  expects the controller's refusal (a deliberate move of that check, stated in its commit; ADV verifies the refusal).
- Robot: none.

### P0c Recorded-decision replay

- Owns: new `autonomy/tests/replay_20260926.py` (the harness), `autonomy/tests/test_recorded_pins_20260926.py`
  (always-run, numbers only), `autonomy/tests/test_replay_20260926.py` (skipped without `KC_EVIDENCE_ROOT`).
- Deliverables:
  1. The harness: reads a run's events and output JSON and, with `KC_EVIDENCE_ROOT`, the two `robot-runtime-data`
     archives (observation records, perception records, frames, read from the archive, never extracted into the
     repository) and yields each stage's decision inputs in order: stage, attempt, observation id, measured joints,
     tool state, the cube record, the pad frame.
  2. Pins of today's decisions from today's inputs: the coarse pregrasps (-44.5 and 0.0 at contact (495, 696) and pan
     -8.7; 7.1 and 20.1; 34.1 and 47.1), each ALIGN attempt's lateral, radial and aligned from its recorded box and
     pitch, the APPROACH targets, the close classes of the recorded readbacks (67.288, 67.150, 67.357 empty; 67.909,
     68.185 held), PLACEMENT_PLAN 108.0 and 82.0, the refusals of start 3 and 21:17 (131.6 unbounded), the
     BASE_MEASURED displacements from the recorded homographies.
  3. The local replay of runs mb3c1048 and m2476137, every stage decision reproduced from the archived records.
- Offline acceptance: the pins green at the PM merge and after P0a; the local replay green with `KC_EVIDENCE_ROOT`.
- Robot: none.

### A Pregrasp and align

- Owns: `policy_align.py`; new `autonomy/tests/test_policy_align.py`, `autonomy/tests/geometric_world.py`,
  `autonomy/tests/test_align_replay.py`; `apps/console-web/src/model/stages/align.ts` and its test.
- Must:
  1. A geometric fake World for the harness: a floor cube projected into the wrist frame from the tool FK through the
     inverse of `alignment_from_bbox`, clipped at the 1280 x 720 border, with an optional descent drift, so a policy
     is tested for convergence from a start, not only on scripted boxes.
  2. Pure functions: the box predictor across a height change (fitted on the recorded view pairs), the unclipped centre
     from the bottom row and the width (recorded aspect 1.09 to 1.13), view margins.
  3. `align_v2`: accept with the lateral in the capture window AND the radial within 5 mm (the successes descended at
     +3.2 and +0.7, the failures at +12.4 and +14.5); a radial step at the pregrasp height through the unchanged step
     and gate; the descent aimed at the block (tool out plus the radial clamped to +/-7 mm, inside the planner's 10 mm
     radial limit) instead of out + 3 mm (cycle:1737); before every downward move the predicted top row and side
     margins, corrected first below 40 px, `align_descent_would_lose_view` only when no correction passes the guards;
     a clipped box re-centres and is never accepted; a lost block gets one stationary re-capture through `observe()`,
     then a step back up to the previous clearance and a re-centre, at most twice, then
     `align_cube_lost_after_reacquire` with the arm at a non-contact clearance.
  4. The pickup band (the placement track's refusal, moved here by P0a) fed the refitted value under `coarse_v2`, its
     bound read from `policy_grasp.min_grasp_forward_mm()`.
  5. `coarse_v2`: the radial map refitted on the recorded pairs (contact row against the ALIGN-measured cube out:
     -17.9, 21.3, 45.2 mm; the robot records of 6 and 7 added in the morning) and the outward bias lowered so that the
     block lands at the centre of the pregrasp view.
- Should: the sharpness of every ALIGN frame recorded as evidence, computed in the policy on the stored frame
  (`frame_illumination.py` is frozen); the cube yaw from the recorded mask polygon, and a refusal above 35 deg before
  the descent (the recognizer is frozen; wrist roll stays frozen).
- Offline acceptance: the predictor reproduces the recorded descents ([473, 73, 737, 362] to [460, 47, 811, 432];
  [454, 65, 689, 330] to [446, 108, 728, 427]) within its fitted tolerance; a pregrasp radial of +12.4 or +14.5
  predicts a top row below 0 after today's descent (failures 7 and 6 reproduced); under `align_v2` the same inputs give
  a radial step, not a descent; a descent view with no cube gives the re-capture, then the step back up, never
  `align_cube_not_detected` at the first loss; the geometric World converges from radial -20, 0, +15, +25 mm and
  lateral +/-40 mm; with no word the differential test and P0c's ALIGN pins hold.
- Robot: the morning's evidence pull and replay of 6 and 7 first; read-only captures at the pregrasp and descent
  heights; ALIGN-only runs ending at the non-contact descent height (block at radial -20, 0, +15, +25 mm and lateral
  +/-40 mm, one yawed about 30 deg; pass: never clipped, final radial within 5 mm, lateral in the window); then the
  failure 6 and 7 geometries as full Starts.

### G Grasp

- Owns: `policy_grasp.py`; new `autonomy/tests/test_policy_grasp.py`, `autonomy/tests/test_grasp_replay.py`;
  `apps/console-web/src/model/stages/grasp.ts` and its test.
- Must:
  1. The grasp target from the measured block, `grasp_target_mm(estimate, empty radii, bounds)`, today's value with no
     word. Under `near_grasp` the lower bound is -25 mm through `near_grasp_pose`, built beside `proven_grasp_pose` on
     the same proven gate, travel bound and path floor (`proven_grasp_pose` itself unchanged); `min_grasp_forward_mm()`
     exported for the pickup band; a clamp of more than 5 mm is the refusal `approach_cube_beyond_grasp_range`.
  2. `grasp_measured_retry`: after an empty close, reopen, lift open to the ALIGN observation height (at least +35 mm),
     observe, re-run `alignment_decision`, aim at the new estimate excluding every radius that closed empty (+/-3 mm),
     step toward the side the reopen frame's far-edge row shows; three closes as today; state GRASP_RETRY with the
     direction and the observation it rests on.
  3. Evidence: the straddle far-edge row and `gripper_opened` as predicates (events only); `grasp_missed` and
     `straddling_not_reached` carry their last view (run 191919's `grasp_missed` names no frame today).
- Should: `latch_midpoint` (ruling 2): the empty-latch class at the midpoint of the recorded classes (empties at most
  67.357, holds at least 67.909), the pinned `GRIPPER_EMPTY_LATCH_MARGIN` untouched; `grasp_recover`: after a failed
  retention with the block still between the jaws, lower to the grasp height, open, lift open, end nothing-held.
- Offline acceptance: run m8c5f9e9's numbers (tool -7.4, radial -15.6) plan a grasp at -9.0 mm under `near_grasp` and
  0.0 without it; its empty closes retry inward, never outward; the recorded straddle rows classify (successes 361 and
  384 in band, the miss 524 and its reopens 560 to 616 nearer); `near_grasp_pose` accepted from -5 to -25 mm and
  refused at -26 by the word's bound; with no word the differential test and the pins hold.
- Robot: the near grasp pose at -10, -20, -25 mm with NO block first, slow factor 2, the operator watching the jaw,
  camera and chassis clearance (no guard models the chassis); then a block at ALIGN estimates -5, -10, -15 mm, three
  closes each; the measured retry with the block moved 10 mm outward, then 10 mm inward, by the operator's hand after
  ALIGN; the latch class read on the next five closes.

### P Placement, continued

- Owns: `policy_placement.py`, new `autonomy/manipulation/placement_envelope.py` and any module the placement build
  added; `autonomy/manipulation/target_binding.py`; new `autonomy/tests/test_policy_placement.py`,
  `autonomy/tests/test_placement_envelope.py`, and `autonomy/tests/test_target_binding.py`;
  `apps/console-web/src/model/stages/placement.ts` and its test.
- Must:
  1. What the placement build did not merge at the cutoff, ported into `policy_placement.py`.
  2. The envelope solver (pure): for an anchor, the measured axis and the active words, the release target and base
     vector with the binding constraint (`side_edge`, `along_budget`, `arm_reach`, `pickup_inside_grasp_radius`,
     `lateral_needs_capability`), the deficit in mm and an operator hint ("move the block 12 mm toward the pad"); with
     no word it reproduces `placement_plan` exactly; `projected`, `side_offset`, `reach_after_advance` are its
     configurations.
  3. Every refusal explains itself: `classify_for_binding` states `placement_infeasible` with the constraint, deficit
     and hint, and the not-placeable refusal carries them.
- Should: per-edge admission values (side, along, the binding edge) in the `release_margin` predicate; PLACED from the
  EXIT view and up to two stationary re-captures under `placed_multiview`, the view used stated; one measured
  correction pulse through the unchanged `measured_base_pulse` when the admission falls short by less than the budget
  left.
- Offline acceptance: 108.0, 82.0 and every recorded refusal with no word; start 3 refused with the side (44.5 mm) and
  the along-axis need (121.6 mm) named, and under `reach_after_advance` plus `side_offset` the solver's plan or its
  remaining deficit stated; 21:17 refused as "11.6 mm past the along-axis budget" and admitted in the model under
  `reach_after_advance`; a scripted mission with start 3's anchor ends with a refusal whose values and sentence name
  the constraint.
- Robot: the design of record's schedule (`projected`: an observe with the block in the widened band, one attended
  far-block run, then three; `side_offset`: forced +/-10 deg releases three times each before any side start); the
  arm-only branch as it is, first.

### R Return and mission recovery

- Owns: `policy_return.py`; new `autonomy/tests/test_policy_return.py` and `autonomy/tests/test_base_return_replay.py`;
  `packages/demo-runtime/demo_runtime/mission_execution.py`, `mission_coordinator.py`, `mission.py`, `daemon.py`, and
  `supervisor.py` after P0b; `packages/demo-runtime/tests/fake_controller.py` and new runtime test files;
  `apps/console-api/console_api/app.py` (the retry route) and a new API test file;
  `apps/console-web/src/model/stages/recovery.ts` and its test.
- Must (runtime, starts now):
  1. `classify_outcome(completion)`: retryable with nothing held (`align_*`, `pregrasp_no_non_contact_pose`,
     `straddling_not_reached`, `grasp_missed` with the gripper reopened, `search_no_cube*`, the search pad-frame
     codes), base only (placed, base not returned), operator needed (a cube held; a refusal before motion for the
     envelope), fatal (identity, lease, runtime), stopped.
  2. The hold: a retryable or operator-needed outcome no longer ends the mission; it waits with reason
     `operator_decision` beside `capability_unavailable` (`mission.py:80`), the attempt recorded. Retry (a guarded daemon
     operation and `POST /api/mission/retry`) parks with the unchanged `--do exit` (a mission run whose command is
     `return_arm`, with no mission target file, which `mission_refusal` forbids for anything but `--do cycle`,
     cycle:2908), then spawns a fresh `--do cycle`. Stop outranks every step; the cancel is read before each spawn.
  3. The failure record: `_evidence` keeps the phase, the reason code, the failure's measurements and observation ids
     (dropped today, `mission_execution.py:587-604`); the stop state (holding, at the low pose, base ahead) derived from
     the run's own events, with the next actions.
  4. The verdict for C's `REFUSED_BEFORE_TRANSACTION` in the activation verdict.
- Should (runtime): the automatic park and one retry per target for the retryable class, a code constant OFF until
  ruling 1 and its qualification.
- Must (controller, after P0): `base_return_error_2d` (events only), the start-to-end displacement of the robot-fixed
  point between the SEARCH centre frame and the last return view, along the start axis, lateral and yaw; `return_2d`:
  `return_base_to_start` through the unchanged `measured_base_pulse`, along the start axis, both axes gated, an
  overshoot beyond tolerance refused (no forward pulse); `base_not_returned` carries its views.
- Offline acceptance: fake controller: an ALIGN failure, the hold, Retry, the park, a fresh cycle, placed (two cycle runs
  and one exit run on the target); two retryable failures exhaust the budget and hold; a held cube never parks; a stop
  during the park or the hold ends STOPPED with no spawn; the recorded homographies reproduce the recorded -5.0 and
  -4.1 mm reverse laterals against the tilted axis (VERIFIED in the [STATE] lines) and the direct net displacement the
  return review reported, about 2.4 mm; with no word the differential test holds.
- Robot: a provoked failure (the block covered before ALIGN) ends in the hold with its stage, measurement and frame;
  Retry pressed: the park, then the fresh attempt; Stop during the park and during the hold; then, if ruled, the
  automatic retry once. `return_2d`: two observes with no motion (posture repeatability), supervised base +0.4 and
  -0.4 s probes three times, then three far-block starts.

### C Cake showcase

- Owns: `packages/demo-runtime/demo_runtime/cake_activation.py`, `capability_check.py`, `signature_check.py`,
  `robot_link.py`, `cake_admin.py`, and after P0b `config.py`, `read_models.py` and the Cake schemas and fixtures of
  `packages/demo-contracts`; `autonomy/motion/cake_capability_adapter.py` (its parsers are held equal to the runtime's
  by a test); `bin/pi/lekiwi-deploy.sh`, `bin/lib/common.sh`, `tests/mock-bin/admin-probe`; web:
  `apps/console-web/src/design/CakePanel.tsx`, `cakeModules.ts`, `cakeFixtures.ts` (new), `cake.ts`,
  `CapabilityCheck.tsx`, `SignatureCheck.tsx`, `ActivationEvidence.tsx`, and `apps/console-web/src/model/capabilityCheck.ts`,
  `cakeTask.ts`, `signatureCheck.ts`, with their tests; new runtime test files.
- Must:
  1. Truth: `mission_participation` derived from records (a capability block with applied > 0 bound to a generation and
     pid, or an OBSERVED activation) instead of the constant false (`read_models.py:529, 562, 677`). Both placed
     missions of today carry capability applied 27 and 20 at generation 2, pid 43289, and read "Cake: not connected to
     this task" (`cakeTask.ts:22-37`). Module cards with the Revision (capsule digest, config digest, generation),
     state, pid, spawns and restarts; the controller's card says "content-addressed, not signed, not a Cake module".
  2. Candidate roles from configuration (activate, refused-demo, tampered-demo, undo) with the expected outcome; nothing
     is ever preselected; the primary action is the activate role.
  3. `REFUSED_BEFORE_TRANSACTION`: `status mutation_refused` with its refusal code is Cake judging, not a failed request.
  4. ValidatePlan of the exact bytes before every apply (read-only), and the transaction's state path rebuilt from
     read-flight right after the answer (the flight ring holds 256 records and the runtime's own polls write into it).
  5. The deploy tooling prepares a tampered twin of the swapped Plan (one byte flipped: an INTEGRITY refusal, never
     called a signature refusal), the starting Plan as the undo candidate, and an unsigned capsule twin at install, so
     the signature check's skipped row becomes `signature_missing`.
  6. The Cake panel: module cards, one labelled button per prepared candidate with its expected outcome, a before and
     after card with the state path, the activation history, the signature table labelled by what ran each row, and
     the honesty lines (live admission is unavailable in the resident; abort-swap is a transactional refusal; the
     tampered twin is an integrity refusal).
- Should: the resident-admission row (a scratch resident with its own store and socket refusing a capsule signed by a
  throwaway key, the terminal `prove-signature` step, from the console, only with nothing in flight); per-slot health
  and lifecycle lines and a "host running outside Cake" finding; the stage-to-component attribution in the read model.
- Offline acceptance: fake admin-probe tests for each outcome; an opt-in test against an x86 resident built from the
  private Cake source (`KC_CAKE_PROBE`, `KC_CAKE_SOCKET`, `KC_CAKE_PLANS`, the pattern of
  `autonomy/tests/test_cake_thin_path.py`): swapped COMPLETED, abort-swap ABORTED with the slot unchanged, the tampered
  twin invalid and refused before any transaction, undo back to the inert capsule; today's two mission records give
  participation true with the module named; with keys [abort-swap, swapped] nothing is preselected.
- Robot: non-motion, host held, no mission: the signature check complete; ValidatePlan; the tampered twin refused (slot,
  session and supervisor counters unchanged); abort-swap refused with its path; swapped observed (generation + 1, a new
  pid, no resident restart); undo to inert; the cards against `list-slots` and `get-health`; then one far-block
  mission whose sentence names the capability's generation and pid.

### U1 Console screen

- Owns: every file of `apps/console-web` that P0b, C, U2 and the stage tracks do not own, in particular new
  `design/DemoScreen.tsx`, `design/StageTimeline.tsx`, `design/FailureBanner.tsx`, `design/ReadyChip.tsx`,
  `model/stageTimeline.ts`, `model/readyChecklist.ts`, and `design/Screen.tsx` (kept behind `?screen=classic`),
  `design/Drawers.tsx`, `main.tsx`, `store/`, `model/reducer.ts`, `model/missionRecord.ts`, `model/robotStatus.ts`,
  `model/observation.ts`, `model/streamDecode.ts`, `design/useStreamDecode.ts`, `design/cameras.ts`,
  `panels/Actions.tsx`, `api/http.ts`, `api/types.ts`, with their tests.
- Must:
  1. DemoScreen as the default view: a header (robot, Ready chip, Start, Stop, Diagnostics); the live wrist view that
     keeps the last picture through a gap with its age and the runtime's reason; a full-width failure banner (the stage,
     the measured sentence from the stage gloss, the decision frame, the rule that decided, the next actions Try again,
     Return arm, Stop); the stage timeline (Find, Pick, Carry, Place, Return: verdict, two or three numbers, the
     decision frame's thumbnail, the stage rule and digest, retries inside their stage, the Cake marker where the
     mission held for the capability); U2's scene map and C's Cake panel mounted; everything else behind Diagnostics.
  2. The hold's choices; Scan scene as the primary look, Look (no motion) beside it; the Observe sentence from the
     print-landmark result; the reducer keeps a frame's own perception when a `scene-*` result names that frame (today
     every `perception.result` replaces it, `reducer.ts:802-812`).
  3. One timeline per target across all of its runs (a bounded per-run cache instead of the single hydrated run).
  4. The Ready checklist: the first unmet prerequisite in one sentence; "arm acquired" stated as not readable here, with
     its terminal hint.
- Should: the Start dialog shortened, the attach paragraph behind "why".
- Offline acceptance: vitest under node 22 (render tests of the banner, the timeline and the checklist on every U2
  scenario); `tools/cdp_page.mjs` screenshots of every scenario in its own headless Chrome, kept under `.local-dev`;
  the main screen carries none of the cut labels and all of the safety marks (SYNTHETIC, STALE, the uncertain-command
  block, the motion hold).
- Robot: on 8771 with the new bundle, the timeline's numbers equal the run's [STATE] lines and each thumbnail is the
  observation its predicate names; the operator runs one attended demo from the main screen without opening
  Diagnostics and names the result and, for a failure, its cause.

### U2 Scenarios, replay stage, scene map

- Owns: `apps/console-web/src/api/syntheticScenarios.ts` (new), `api/syntheticScript.ts`, `api/synthetic.ts`,
  `panels/sourceChoice.ts`, `model/sceneMap.ts` (new), `design/SceneMap.tsx` (new), `panels/PadScenePanel.tsx`
  (retired into the scene map), `model/padScene.ts`, with their tests; `apps/console-api/tests/support.py` (a
  fixture-directory hook, no data); `.local-dev/console/replay-stage.sh` (ignored).
- Must:
  1. One scenario per failure mode of today, with the controller's state names and field shapes and today's numbers,
     frames drawn and marked SYNTHETIC: near-block-grasp-miss, far-block-success, far-left-yawed-success,
     not-placeable-side, not-placeable-budget, align-no-proposal, align-clipped-top, observe-landmarks-only,
     preview-stall, preview-not-configured, abort-swap-repeated, bring-up-not-confirmed, host-not-supervised; and one
     per new behaviour: scene-scan, hold-then-retry-placed, tampered-refused-before-transaction, undo-to-inert.
  2. The scene map from the scan's events: the pad, the pan axis and the home bearing, the +/-25 deg wedge only when the
     scene carries an accepted fit, the view footprints, blocks coloured by class with the class sentence, the
     measured pulses, the placed block. The robot's base position is not drawn (no record carries it) and the map says
     so. Its fold is separate from the reducer, which today lets every `perception.result` replace a frame's own
     perception (`reducer.ts:802-812`).
- Should: the local replay stage: today's four runs served through the stub runtime from the ignored evidence, opened in
  the headless Chrome.
- Offline acceptance: per scenario, its transitions, reason code, values and mission states, and nothing plays before
  Start; the screenshots with U1.
- Robot: none.

### S Scene scan and search

- Owns: `policy_search.py`, new `autonomy/perception/scene_scan.py`; new `autonomy/tests/test_policy_search.py`,
  `autonomy/tests/test_scene_scan.py`, `autonomy/tests/test_scene_replay.py`; `apps/console-web/src/model/stages/search.ts`
  and its test.
- Must:
  1. `classify_block`: one class per seen block for SEARCH, the binding and the map: ALREADY_PLACED, ARM_ONLY,
     BASE_ADVANCE (needs the Cake capability), NOT_PLACEABLE (with P's constraint and deficit), TOO_NEAR (A's pickup
     band), OUTSIDE_PAN_BAND, SEEN_NOT_MEASURABLE, NO_PAD_FRAME. With no word SEARCH runs the same checks in the same
     order.
  2. `scene_scan.py`: the pan-axis fit (2D Kabsch between the print-landmark homographies of pad-valid views); the pad
     frame carried by pan only when lift, elbow and wrist flex agree within 0.5 deg and the fit's rotation and residual
     stay inside thresholds set from the replay; a position carried so is marked inferred and never becomes a placement
     anchor; blocks clustered in PAD_FRAME across views with their best view; the view footprints.
  3. `--do scan` (`scene_scan`): from the saved low pose with the gripper open only; single-pose calls at the low-pose
     shape (home, -15, home, +15, home; +/-25 under `scene_scan_25`), each through the unchanged `run_pose`, so no view
     is a transit duplicate; the SETTLE of EXIT at the end (cycle:1351-1360); the scene as one JSON in the run's
     directory and as `perception.result` events (no contract change); an abort ends with exit 4 and the partial scene.
- Should: SEARCH from the scene (`scene_search`): an early exit when the centre frame alone decides; the selection over
  the whole scene (ruling 7); the PREGRASP waypoint frame confirms the block and gives the placement anchor of record,
  or `pregrasp_confirm_lost_block` before contact.
- Offline acceptance: the recorded pair of run m2476137 (pans -9.67 and -29.63) gives a rotation within 0.2 deg of the
  measured pan change and a median residual under 1.5 mm (the search review measured 20.07 against 19.96 deg, axis
  (-0.0125, -0.4132) m, 0.8 mm; the thresholds are set from this pin); fake machine: the scan's call order, base zero
  around every segment, a refusal before motion off the low pose, with the gripper not open or beyond 60 deg absolute,
  exit 4 with a partial scene on an abort; today's blocks classify: starts 1 and 2 BASE_ADVANCE, the 19:19 block
  TOO_NEAR with the distance to move, start 3 NOT_PLACEABLE with both limits and OUTSIDE_PAN_BAND marked inferred,
  21:17 NOT_PLACEABLE on the budget.
- Robot: a plan-only scan; one attended scan at +/-15 with no block (every base-zero check passes, the live fit inside
  the replay thresholds, the next plan-only Start accepted after the SETTLE); the block at five spots (centre, the two
  frame edges, beyond the band on each side), one scan each, the classes checked against the placement rules with the
  pad frame as the metric reference; then +/-25 the same way; then three Starts under `scene_search`, one with the
  block out of the centre frame and one with a second block on the pad.

### L Live view

- Owns: `bin/pi/camera_service.py`, `packages/demo-runtime/demo_runtime/preview_client.py`, `sources.py`, new
  `tools/preview_stall_probe.py`, their new test files.
- Must: a test that a LIVE robot-runtime configuration with a local link and a preview section resolves the wrist
  preview to the camera service's loopback port 5571 and starts no forward (the operator adds the section in the
  morning); the camera service's counters (published, skipped for a mission capture, dropped at the socket) in the
  preview header and `skipped_for_mission` forwarded into the slot (an optional field the contract has), so the page
  can say "paused for a decision capture"; the read-only stall probe (one sample a second, a local jsonl).
- Not tonight: clearing the mission flag earlier in the camera service, unless the probe shows the skips cause the
  stalls.
- Offline acceptance: the header round trip with and without the counters; a slot record without them still accepted.
- Robot: runtime restart with the preview section, 8771 live; the probe during a plan-only Start and one attended run.

## 6. File ownership

- A track edits only the files it owns. Before its merge a judge checks `git diff --name-only <base>...<head>` against
  the owned list and re-runs the track's gates; a track's own report is a claim, not a fact.
- New test files per track. After P0 the shared cycle tests (`test_run_single_cube_cycle.py`, `test_cycle_mission_mode.py`,
  `test_cycle_events.py`, `test_cycle_behavior_preserved.py`, `test_frozen_primitives.py`, `test_recorded_decisions.py`)
  are read-only for the parallel tracks: they import the fixtures and extend them by subclassing in their own files.
- Nobody edits tonight: the pinned primitives and constants (section 2.1), `scan_world.py` (the executor), the frozen
  perception sources and `perceive()`, `bin/laptop/perception/run_stage1_scan.py`, the capability relay and the v2
  contract (`autonomy/motion/`, except C's parser mirror), the work order (integration only).
- Hot files, in order of owners: `run_single_cube_cycle.py` PM, then P0a, then INT; `config.py` PM, P0b, C;
  `supervisor.py` PM, P0b, R; `read_models.py` P0b, C; `failure.ts`, `reason.ts`, `phase.ts` PM, P0b, then INT;
  `target_binding.py` PM, P; `missionRecord.ts` U1.
- A track that finds it needs a file it does not own stops that item and reports it; integration makes the change.

## 7. Sequencing

Times are UTC on the night of 2026-09-26 to 27, targets rather than promises.

1. Now to 23:45: the placement build finishes its scope (PM). In parallel, from this plan's commit: R's runtime part,
   U1, U2, C and L, each avoiding the files P0b takes (C: `config.py`, `read_models.py`, the contracts; R:
   `supervisor.py`; U1: `failure.ts`, `reason.ts`, `phase.ts`, `command.ts`).
2. 23:45: PM merged into `integrate/overnight-2026-09-26`, created from `feat/operator-console` at this plan's commit.
3. 00:00 to 03:00: P0a, P0b and P0c in parallel (disjoint files), from the PM merge. P0c lands first (its pins are P0a's
   proof), then P0b, then P0a, each with the full gates. Hard stop 03:30: if P0a is not green, the stage tracks do not
   start tonight; the night delivers PM, P0b, P0c and the non-controller tracks, and the record says so.
4. 03:00 to 05:45: A, G, R (controller part), S and P from the P0 merge; the early tracks rebase onto it and finish.
5. 05:45 to 06:45: integration (section 9). 06:45 to 07:30: adversarial verification.
6. The morning protocol (section 10), with the operator.

At most six builder agents run at once, queued by the priority column of section 5; a fresh-context judge runs before
each merge. When agents fail on rate or session limits, the work falls back to one agent at a time in priority order,
never to a retried fan-out. Late items are cut from the bottom of each track's Should list first; a track merges what
is committed and gated at the integration cutoff, and the record lists what was not done.

## 8. Offline acceptance: today against the new policies

| Today | Recorded input | Required decision after tonight (word) | Track |
|---|---|---|---|
| 1, grasp miss | SEARCH contact (495, 696) at pan -8.7; ALIGN tool -7.4 radial -15.6; closes 67.288, 67.150, 67.357 | with no word: refused at SEARCH before motion with the distance to move (the merged near-block refusal); under `coarse_v2`: the corrected coarse value near -18 mm feeds that refusal; under `near_grasp`: a grasp planned at -9.0 mm and inward retries | A, G |
| 2, start 1 | the mb3c1048 records | every decision unchanged with no word; under each word only the stated decisions change (for example, the aimed descent targets 21.3 mm instead of 21.1) | P0c, all |
| 3, start 2 | the m2476137 records | unchanged with no word; the direct measurement puts the robot-fixed point about 2.4 mm from where it started, almost all of it across the start axis (INFERRED from the return review's replay, pinned by R), inside both gates of `return_2d` | R |
| 4, start 3 | anchor (+0.163, -0.210), axis (-0.042, 0.999) | refused before motion with both placement limits and the pan band named, and the operator hint; under `reach_after_advance` and `side_offset` the model's plan or its deficit | P, S |
| 5, 21:17 | anchor (+0.064, -0.220) | refused as 11.6 mm past the along-axis budget, with the hint; admitted in the model under `reach_after_advance` | P |
| 6, 21:24 | ALIGN aligned at radial +14.5 (brief; the record is pulled in the morning) | under `align_v2`: a radial step at the pregrasp height instead of the descent; a lost block gives a re-capture, then a step back up; the mission holds for the operator instead of ending | A, R |
| 7, 21:31 | ALIGN accepted at radial +12.4; a descent frame clipped at the top | the predictor places the block above the frame after today's descent; under `align_v2` the policy corrects first; a clipped box re-centres; the mission holds | A, R |
| 8, Observe | run 0i5xpdmx; the m2476137 view pair | "Pad located by its print (97 landmarks)"; the scan's axis fit pinned on the recorded pair | U1, S |
| 9, console | every item | a synthetic scenario whose screen shows the stage, the measured sentence, the frame and the next actions; no candidate preselected; the kept picture through a preview gap | U1, U2, C, L |
| 10, bring-up | the session of 2026-09-26 | the Ready chip names the first missing prerequisite; a host outside Cake is named | U1, C |

The always-run pins carry numbers only; frames and records stay in the ignored evidence directory and are read by the
local replay (`KC_EVIDENCE_ROOT`). The records and frames of 6 and 7 are on the robot: their replay is the first
morning step, before any ALIGN motion.

## 9. Integration and adversarial verification

Integration, on `integrate/overnight-2026-09-26`:

1. Merge order: the stage tracks (A, G, S, P, R's controller part), then the runtime (R, C, L), then the web (U2, U1).
   After each merge: the autonomy, demo-runtime, console-api, contracts and web suites (typecheck and tests under node
   22) and `bash tests/test-docs.sh`; the differential tag checked present; counts compared with the previous merge. A
   merge that turns a gate red is reverted on the integration branch and reported, never fixed by weakening a test.
2. The combined-words matrix on the fake machine: every stage word on together, scripted Worlds for today's ten items;
   with every word off the run equals the differential baseline.
3. Golden events: the fake machine's events for a scan, an ALIGN re-acquire, an inward grasp retry and a hold with a
   retry exported as JSON fixtures and read by a web test through U1's reducer and timeline (the controller's real
   shapes, not only the synthetic ones).
4. The web bundle built from the integration commit and every U2 scenario screenshotted with `tools/cdp_page.mjs`; the
   images stay under `.local-dev`.
5. The runtime release staged with `bin/runtime-release.sh stage <integration commit>` (it builds and verifies a local
   release and deploys nothing).
6. The record: a new `docs/autonomous-demo/overnight-integration-2026-09-27.md` (per track: the merged commit, the gate
   tails, what was not done) and the work order's status updated. `feat/operator-console` moves to the verified
   integration commit only when the operator accepts it.

Adversarial verification, by fresh-context agents that built nothing, one claim family each:

- Behaviour preservation with every word off: the differential test run with its tag present, P0c's pins, the frozen
  pins, the mission-mode suite, and the diff of every pinned method read line by line.
- Motion safety: every new motion path (the scan, the radial step, the re-acquire step, the near grasp, the retry lift,
  the set-down, `return_2d`, the park) refused in a LIVE run without its qualified word or the attended label, before
  the robot is constructed; every new loop polls the abort flag through `run_pose`; no new path bypasses the executor
  or the gateway.
- Claims: every Cake sentence on screen against the honesty rules (integrity is not signature; abort-swap is
  transactional; the controller is not a Cake module; a live update selects among pre-admitted versions); every number
  in the integration record against a gate output.
- Committed text and privacy on the whole diff: the docs gate, no frame, raw record, path, address or private name
  committed.

A finding that holds reverts the offending merge on the integration branch.

## 10. Morning protocol

1. At the desk, before power: read the integration record and the adversarial findings; rule on section 11; accept or
   refuse the integration commit; stage the runtime release and the web bundle from it.
2. Bring-up, the operator at the robot (handoff 15.6 step 2 and section 16; commands from the console worktree root after
   `set -a; . .local-dev/console/lekiwi-env.sh; set +a`): power on; `bash bin/pi/lekiwi-deploy.sh
   stop`; stop the camera service; acquire the arm with the operator's tooling (needed after every power cycle);
   `bash bin/pi/lekiwi-deploy.sh check` (read-only, must PASS); confirm no stand-alone host runs; `install` only if the
   Cake deploy changes are accepted (the tampered twin, the undo candidate, the unsigned twin); `arm` (the supervised
   host on loopback, its listener read back); camera service start (wrist only); install the runtime release and the
   configuration additions (the preview section, `placement_envelope` empty, the candidate roles); runtime restart; desktop
   perception service; 8771 on the new bundle; confirm the robot in the browser; the Ready chip green.
3. Evidence first, no motion: copy the records and frames of runs `console-20260926T212418Z-m76fe86c` and
   `console-20260926T213112Z-m66738ff` from the robot's runtime data into the ignored evidence directory; run A's
   replay on them at the desk. If it does not give the re-centre decision, `align_v2` is not qualified today.
4. Non-motion checks: the Cake beats with the host held (C); the live view and the stall probe (L); Look on three
   placements (the map mark equals the stdout line to the millimetre, the pad source label right); a plan-only scan; a
   plan-only Start whose plan equals the previous release's on the same scene.
5. Single-variable checks, each attended, the block absent unless stated, one word at a time (the configuration names
   that word and an attended label, then a runtime restart): a +/-15 deg scan; the near grasp pose at -10, -20, -25 mm;
   ALIGN-only runs at the listed offsets; the `return_2d` probes if ruled; a provoked failure, the hold, and Retry
   pressed.
6. Subtasks: the failure 7 and 6 geometries under `align_v2`; the near block under `near_grasp`, or its refusal; the
   measured retry with the operator's offsets; the placement words in their widened band.
7. Repeated trials: three far-block starts, three arm-only starts at the pad edge, three out-of-frame starts under
   `scene_search`. Each word that passes gets its record and joins its stage's QUALIFIED set in one commit; a word that
   fails stays off and the demo runs without it.
8. The demo, with qualified words only: one block anywhere in the qualified band, the Cake beats in the panel.

Evidence after every run, into `.local-dev/console/lekiwi-evidence/<date>/` (ignored): the run's events and output JSON,
the runtime-data archive (observations and frames), the mission record, the capability and signature check records, the
scene JSON, console screenshots.

Stop conditions: the Stop button; any executor refusal (the arm holds; the record says why); any motion that does not
match the plan-only print (power by hand); a clipped view or a lost block in an ALIGN qualification run (stop ALIGN
qualification); a pass criterion missed (the word stays off); the resident session changing; a perception answer late
or missing; a host running outside Cake; the arm not acquired; a low battery; anything the operator does not expect.

## 11. Open rulings

Each has a recommended default; nothing that needs a ruling runs LIVE before it.

1. Recovery after a failure with nothing held. Default: the mission holds for the operator (no motion without a press);
   Retry parks and starts a fresh attempt; the automatic park and one retry per target only after their own
   qualification; never after a Stop, never with a cube held or the base displaced.
2. Pinned proven values the reviews want changed (`GRIPPER_EMPTY_LATCH_MARGIN` 0.6, whose threshold sits 0.043 above
   today's worst empty close; `BASE_RATE_PRIOR_MM_S` 100 against 49.1 to 57.3 mm/s measured on the six relayed forward
   pulses of today; the single HOLD_OBSERVE
   capture inside `_advance_base_held`). Default: no pinned code or value changes tonight; the latch midpoint rides as
   `latch_midpoint` in the grasp policy and is qualified on the next five closes; the rate prior and the re-capture wait
   for an explicit pin update.
3. A grasp nearer than the proven radius, down to -25 mm (the gate accepts it offline; no guard models the chassis).
   Default: its no-block check in the morning; live only after that check and the near-block subtask.
4. A pan band beyond 25 deg from home (a staged EXIT through an intermediate pan), the only route to start 3's pickup.
   Default: not tonight; decided after the scan measures where such blocks lie.
5. Lateral base motion through a v3 capability (a new signed capsule; lateral approach counts as Cake mobility).
   Default: not tonight; the side release by pan (`side_offset`) is qualified first.
6. The advance budget above 120 mm (a clearance proxy with no sensing). Default: keep 120; `reach_after_advance` covers
   blocks up to about 86 mm outside the near edge.
7. The scan's binding rule. Default: exactly one unplaced block in the whole scan, else a refusal naming both blocks.
8. The console layout. Default: a repository-first composition from the imported design's existing tokens and
   components, no design import or export tonight, the classic screen behind `?screen=classic` for one release; 8770
   retired from the demo once 8771 shows the live view.
9. Cake scope. Default: no private Cake change and no fault injection tonight; the task-policy slot of section 2.5 is
   designed after the stage words are qualified, and needs a new install.

## 12. Gates and baselines (measured tonight at `ebe8e7d`, this checkout)

- `bash tests/test-docs.sh`: 2 failures, the known em-dash files `physical-pass-2026-09-18.md` and
  `two-cube-mission-design.md`; the private-token scan skipped (no tokens file).
- `python3 -m pytest autonomy/tests -q -p no:cacheprovider`: 1147 passed, 12 skipped.
- `python3 -m pytest packages/demo-runtime/tests -q -p no:cacheprovider`: 942 passed, 1 skipped.
- `python3 -m pytest apps/console-api/tests -q -p no:cacheprovider`: 238 passed; the `test_live.py` failure the work
  order's section 5 names did not occur.
- `pnpm -C apps/console-web typecheck`: clean; `pnpm -C apps/console-web test -- --run` under node 22.22.0: 917 passed in
  59 files.
- The work order's section 5 counts (1104, 897, 223, 881) are older than these (INFERRED: recorded before the track
  merges of `914c131` added tests). The placement workflow states 1145 passed and 14 skipped for its own worktree at
  the same commit (not re-run here; the difference is INFERRED to be checkout-dependent skips).

## 13. Where the evidence disagrees with the brief

1. "Start 3's anchor must now be placeable": not in any qualified configuration tonight. The anchor fails the side
   requirement (44.5 mm) and no advance along the recorded axis admits it; its along-axis need is 121.6 mm; its pickup
   lies about 41 deg from home (INFERRED), beyond the 25 deg band. It becomes placeable in the model only under
   `reach_after_advance` with `side_offset` (unqualified) and pickable only under ruling 4. Tonight's acceptance is a
   refusal before motion that names every limit.
2. Failure 5 needs 131.6 mm along start 1's recorded axis by the controller's own functions, not about 125 mm; that
   run's own axis is not in the local evidence (UNKNOWN).
3. The sideways return of start 2 was never checked against a tolerance: only the along-axis value is gated
   (cycle:1917).
4. The failed outline fit of item 8 did not affect a decision: the SEARCH frames of the three recorded runs and the
   Observe frame each carried a valid print-landmark pad frame (122, 97, 96 and 97 landmarks, reprojection 0.97 to
   1.09 px, VERIFIED from their events); it is a presentation defect.
5. The near-block refusal being merged keys on a coarse value that under-read every recorded run by 11 to 27 mm
   (section 4).

## 14. Not tonight

Rulings 2 to 6 and 9 as stated; the capture bursts inside the executor; any change to the recognizer or the frozen
perception; the wrist-roll close; the pickup reverse (`near_reverse`, which needs a runtime respawn predicate and two
rulings); the yaw of the base; the in-run re-SEARCH and pick re-plan (served by the hold); the same-plan guard on a
retry (it needs a mission target file version shared by P and R, and waits for integration); the restart
fault-injection check; every private Cake change; renaming the word keys, which keep the placement track's names.
