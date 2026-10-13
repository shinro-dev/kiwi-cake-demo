# The overnight integration of 2026-09-27: the stage tracks

Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

This records the final merges into `integrate/overnight-2026-09-26` (plan: `overnight-plan-2026-09-26.md`, section 9;
work order status: `one-block-demo-2026-09-26.md`, section 7). The robot was powered off throughout: nothing here was
run on the robot, and no stage word is qualified. Four rounds are recorded: the stage merges up to `9891d14` (sections
1 and 2), the fix round on the adversarial findings against `9891d14` (section 6, head `05fcea0`), the second fix round
on the re-check of `05fcea0` (section 7, head `6994bd8`), then the merge of `fix/ovn4-web` (section 7, merge
`0e80122`), which moved the head to the commit that carries this record.

## 1. Merges and verdicts

The integration branch started this round at `4b63c88` (PM, P0c, P0b, P0a, U2, U1, C, L and R's runtime part already
merged). Merge order: the plan's section 9 order for the stage tracks (A, G, S, then R's controller part), each with
`git merge --no-ff`. Every merge applied without a textual conflict.

| Track | Branch head | Merge | Verdict |
|---|---|---|---|
| A, pregrasp and align | `efc366e` | `45c51ad` | merged; autonomy and web gates green after it |
| G, grasp | `5a6f72b` | `796ba36` | merged; autonomy gate green after it |
| S, scene scan and search | `109816c` | `9604c59` | merged; autonomy and demo-runtime gates green after it |
| R2, return and the mission park | `bb642a4` | `42980b0`, reverted by `f6c1a6e` | reverted: demo-runtime red (1 failed, 1152 passed) |
| P, placement continued | `4e88035` | none in this round; merged in the fix round as part of `fix/ovn4-p` (section 6) | not merged in this round: did not pass verification |

The verdicts that A, G, S and R2 passed verification, and that P did not, were given before this round; this round
re-ran the gates after each merge.

R2's revert. The merge adds `supervisor.start_mission_park`, the interface `mission_execution.run_park` already calls.
The wave-1 test `packages/demo-runtime/tests/test_mission_retry.py::WhatTheHoldOffers::test_a_supervisor_that_cannot_park_offers_only_the_stop`
builds its supervisor that cannot park from the plain `Supervisor`, so with the merge the hold offers
`['retry', 'stop']` where the test expects `['stop']`. This is a wiring overlap between R's runtime part and R2, not a
defect of either: the resolution for the plan's interface is to give that test a supervisor without the park (and to
run the retry tests through the real park), a change to a test file that this round did not make. By section 9 of the
plan the merge was reverted instead. On its own branch R2's tests pass (`test_base_return_replay.py`,
`test_policy_return.py`, `test_supervisor_mission_park.py` with `KC_EVIDENCE_ROOT` set: 41 passed, 38 subtests).
Consequence at `9891d14`: the Supervisor has no `start_mission_park`, so a mission hold offers only Stop; `return_2d`
and `base_return_error_2d` are absent. The fix round restored R2 (section 6).

What P lacked, as read on `track/ovn-p` at `4e88035` (section 6 says which of these the fix round closed):

- `reach_after_advance` and `placed_multiview` are not declared stage words: `policy_placement.WORDS` is unchanged
  from P0a (`projected`, `near_reverse`, `side_offset`, `base_lateral`). `reach_after_advance` exists only as a
  configuration of the solver's model (`placement_envelope.CONFIGURATION_WORDS`), which no controller path takes.
- The Should items `placed_multiview` (PLACED from several stationary views) and the one measured correction pulse at
  the release admission are not built.
- The mission binding path still swallows a placement refusal into mode None: the controller's seen-cube classification
  in `bin/laptop/manipulation/run_single_cube_cycle.py` (not a file P owns) does not call the solver's explanation, so
  the plan's acceptance "a scripted mission with start 3's anchor ends with a refusal whose values and sentence name the
  constraint" is not met on the branch; the wiring needs the controller and `policy_search.py` (owned by S).
- The controller's `_side_edges` keeps a strict comparison, so an exactly diagonal reach axis marks no side edge there
  while the solver's `side_edges` marks every edge; the two rules disagree on that axis.

## 2. Gate tails

Baseline at `4b63c88`, measured at the start of this round (the counts in the task brief, 1279, 967, 238 and 942, are
older than the branch and did not match it):

- `bash tests/test-docs.sh`: `test-docs: 2 failure(s)` (em-dash in `physical-pass-2026-09-18.md` and
  `two-cube-mission-design.md`).
- autonomy: `1298 passed, 23 skipped, 595 subtests passed`.
- demo-runtime: `1142 passed, 2 skipped, 708 subtests passed`.
- console-api: `250 passed, 161 subtests passed`.
- console-web under node 22.22.0: typecheck clean; `Test Files 85 passed (85)`, `Tests 1162 passed (1162)`.

After each merge:

| After | docs | autonomy | demo-runtime | console-api | console-web |
|---|---|---|---|---|---|
| baseline `4b63c88` | 2 failures | 1298 passed, 23 skipped | 1142 passed, 2 skipped | 250 passed | typecheck clean, 1162 passed |
| A `45c51ad` | 2 failures | 1332 passed, 29 skipped | not touched | not touched | typecheck clean, 1165 passed |
| G `796ba36` | 2 failures | 1386 passed, 31 skipped | not touched | not touched | not touched |
| S `9604c59` | 2 failures | 1435 passed, 33 skipped | 1142 passed, 2 skipped | not touched | not touched |
| R2 `42980b0` | 2 failures | 1465 passed, 33 skipped | 1 failed, 1152 passed, 2 skipped | 250 passed | typecheck clean, 1165 passed |
| revert `f6c1a6e` | 2 failures | 1435 passed, 33 skipped | 1142 passed, 2 skipped | 250 passed | typecheck clean, 1165 passed |

At `9891d14`, with `KC_EVIDENCE_ROOT` naming a scratch copy of the evidence directory `one-block-20260926` (the harness
reads the two runtime-data archives in memory):

- the replay files (`test_replay_20260926.py`, `test_align_replay.py`, `test_grasp_replay.py`, `test_scene_replay.py`,
  `test_recorded_pins_20260926.py`): `45 passed, 64 subtests passed`.
- the whole autonomy suite: `1454 passed, 14 skipped, 1468 subtests passed`. The 8 skips of
  `test_recorded_decisions.py` remain: they need the older `manipulation-*/cycle-*.json` session layout, not this one.

The differential tag `single-cube-cycle-pass-2026-09-20` is present (`dc78934`), so `test_cycle_behavior_preserved.py`
ran rather than skipped.

What the proven primitives are byte-identical to. The motion primitives are byte-identical to the 2026-09-26 release
`914c131` (the sha256 pins of `autonomy/tests/test_frozen_primitives.py`, which pin the 2026-09-26 state) and are
untouched by every overnight merge. They are NOT byte-identical to the 2026-09-20 tag: the safety verifier compared tag
and head by bytes and AST and found `Cycle.run_pose`, `base_pulse`, `measured_base_pulse`, `advance_base`,
`return_base`, `grip` and `observe` different, and `Cycle._advance_base_held` absent at the tag. The changes from
2026-09-20 to 2026-09-26 add reason codes, events, the LIVE forward exclusion and the capability-relay refusal; the
bounds stay (`BASE_MAX_PULSES`, `BASE_MAX_TOTAL_ADVANCE_MM`, now checked inside `_advance_base_held`).
`base_displacement`, `base_posture_refusal`, `gripper_refusal`, `return_to_low_pose`, the pose builders, the pregrasp
clamp line and the constants digest are the same at the tag. The 2026-09-20 tag is only the behavioural baseline of the
differential test, and only for its scripted scenarios (13 World and 8 machine scenarios at `9891d14`; the fix round
added the low-pose frame with no contact pixel).



## 3. Words per stage at the head

| Stage | Words declared | Qualified |
|---|---|---|
| SEARCH (S, SAFE) | `scene_scan`, `scene_scan_25`, `scene_search`, `search_sweep` | none |
| ALIGN (A) | `coarse_v2`, `align_v2` | none |
| GRASP (G) | `near_grasp`, `grasp_measured_retry`, `latch_midpoint`, `grasp_recover` | none |
| PLACEMENT (PM, carried by P0a) | `projected`, `near_reverse`, `side_offset`, `base_lateral` | none |
| RETURN (R2) | `return_2d` | none |

Every QUALIFIED set is empty (`QUALIFIED = frozenset()` in each `policy_*.py`). None can be qualified: a word joins its
stage's QUALIFIED set only in the commit that adds its qualification record, and each record needs attended runs on the
robot, which was off. `search_sweep` is new in the fix round: the SEARCH sweep, which ran with no word before it, now
runs only under that word. P's `reach_after_advance` and `placed_multiview` are not declared words.

## 4. Open rulings

The nine rulings of the plan's section 11 stand as written (recovery after a failure; the pinned values; a grasp nearer
than the proven radius; a pan band beyond 25 deg; lateral base motion through v3; the advance budget above 120 mm; the
scan's binding rule; the console layout; the Cake scope). From the two rounds:

1. R2 and the no-park test. The fix round (`fix/ovn4-rt`, `663bc6b`) gave `test_mission_retry.py`'s "cannot park" case
   a supervisor with no park and ran the retry tests through the real `Supervisor.start_mission_park`, then restored
   R2. The first round had left that test edit to the operator; accepting the integration commit accepts it.
2. Track P's missing words: whether to build `reach_after_advance` and `placed_multiview` (and the measured correction
   pulse), or to keep the placement words of PM for the morning.
3. `fix/ovn4-safe` edits two test files outside its list (`test_cycle_mission_mode.py`, 9 lines naming `search_sweep`
   for the sweep test; `test_stage_seams.py`, one expected code, `search_no_cube_after_sweep` to `search_no_cube`). Its
   verifier found no assertion removed; the operator accepts or refuses them with the integration commit.
4. The runtime's class for `align_inside_grasp_radius`: answered by the second fix round (section 7), which makes it
   operator-needed with no Retry, as `search_cube_inside_pregrasp_reach` is. The operator accepts or refuses that
   class with the integration commit.
5. The web fix branch `fix/ovn4-web`: rebuild it on the finding its verifier still holds (section 8), or ship the
   console of `9891d14` with the honesty findings open.
6. The Retry switch `mission.retry` (section 7): it ships off, so a LIVE hold offers only Stop. Whether to set it, with
   the attended label, for the attended Retry check of step 5, and whether a demo runs with it on after that check.

## 5. Morning qualification order (plan section 10)

1. At the desk, before power: read this record and the adversarial findings; rule on the open rulings; accept or refuse
   the integration commit; stage the runtime release and the web bundle from it.
2. Bring-up, the operator at the robot: power on, deploy stop, camera service stop, acquire the arm, deploy check
   (read-only, must PASS), no stand-alone host, install only if the Cake deploy changes are accepted, arm, camera service
   start (wrist only), the runtime release and configuration additions, runtime restart, desktop perception, 8771 on the
   new bundle, the robot confirmed in the browser, the Ready chip green.
3. Evidence first, no motion: copy the records and frames of runs `console-20260926T212418Z-m76fe86c` and
   `console-20260926T213112Z-m66738ff` into the ignored evidence directory and run A's replay on them at the desk; if it
   does not give the re-centre decision, `align_v2` is not qualified that day.
4. Non-motion checks: the Cake beats with the host held (C); the live view and the stall probe (L); Look on three
   placements; a plan-only scan; a plan-only Start whose plan equals the previous release's on the same scene.
5. Single-variable checks, each attended, one word at a time: a +/-15 deg scan; the `search_sweep` qualification
   (a plan-only sweep, one attended sweep with the block off-centre, then three starts); the near grasp pose at -10,
   -20 and -25 mm; ALIGN-only runs at the listed offsets; a provoked failure, the hold and Retry pressed, with
   `mission.retry: true` and the attended label `placement_envelope_attended` in the runtime configuration (without
   both, the LIVE runtime offers only Stop and refuses the Retry and the park); the `return_2d` probes if ruled. Until
   this check has its record, every LIVE hold of steps 2 to 8 offers only Stop, because `mission.retry` ships off.
6. Subtasks: the failure 7 and 6 geometries under `align_v2`; the near block under `near_grasp`, or its refusal; the
   measured retry with the operator's offsets; the placement words of PM in their widened band.
7. Repeated trials: three far-block starts, three arm-only starts at the pad edge, three out-of-frame starts under
   `scene_search`. A word that passes gets its record and joins its stage's QUALIFIED set in one commit; a word that
   fails stays off.
8. The demo, with qualified words only. Without `search_sweep` qualified, a block out of the low-pose frame is refused
   (`search_no_cube`, or `mission_binding_no_candidate` in a mission) and is not searched for.

## 6. The fix round (on the adversarial findings against `9891d14`)

Three adversarial verifiers (safety, honesty on screen, plan acceptance) each gave FAIL on `9891d14`. Four fix branches
were built from `9891d14` and verified; three passed and were merged with `git merge --no-ff`, in this order, without a
textual conflict:

| Branch | Branch head | Merge | Verdict |
|---|---|---|---|
| `fix/ovn4-p`: track P merged, the hint's side named from its checked move, a declared target's no-candidate refusal explained, a non-finite reading refused, the far-edge clause | `3b8cc50` | `57bf874` | merged; autonomy and web gates green after it |
| `fix/ovn4-safe`: the SEARCH sweep only under `search_sweep`; `align_v2` never commands the tool inside the inner bound (0 mm, or -25 mm under `near_grasp`) | `33d148a` | `243a0c2` | merged; autonomy gate green after it |
| the controller-side placement explanation (the rebased integration patch of track P) | none | `d801068` | committed on the integration branch; autonomy gate and replay green after it |
| `fix/ovn4-rt`: R2 restored with the no-park test, the grasp words' nothing-held endings held for a Retry, a Retry asked `block()` before it is written, a refused Retry a 409 | `2ae36ef` | `f05bd83` | merged; every gate green after it |
| `fix/ovn4-web`: the demo screen's honesty findings | `51e2335` | none | not merged: its verifier held two must-fix findings (a completed scan drawn as a failure once the next run starts; the scene-map sentence pairing S's along-axis deficit with the side limit); the next pass is in section 7 |

`d801068`: the controller's seen-cube classification now carries `placement_envelope.explain_refusal` for a cube with no
bounded placement, so a mission's `mission_binding_candidate_not_placeable` (auto target) and
`mission_binding_no_candidate` (a declared target with one cube seen) name the deciding limit (`constraint`), the
shortfall (`deficit_mm`), the sentence and the hint; the single-cube SEARCH refusal `search_placement_infeasible` records
the same values, its sentence unchanged. `_side_edges` takes the solver's rule (`>=`), so an exactly diagonal reach axis
marks every edge as a side edge; it is not a pinned primitive, and the frozen pins and the differential stay green. The
test, `test_cycle_mission_mode.py::TheNotPlaceableRefusalNamesItsLimit`, runs start 3's anchor (+0.163, -0.210) and
21:17's (+0.064, -0.220) on the scripted World, whose reach axis is not the recorded one: start 3 is refused on
`side_edge`, 14.5 mm short, and 21:17 on `along_budget`, 11.5 mm past the budget (the recorded axis gives 9.4 and 11.6
mm, pinned in `test_target_binding.py`). It failed before the change (6 failed subtests) and passes after it.

Gate tails at `f05bd83` (the code tree of `05fcea0`, which added this record only):

| Gate | Result | At `9891d14` |
|---|---|---|
| `bash tests/test-docs.sh` | `test-docs: 2 failure(s)` (the two em-dash baseline files) | the same |
| autonomy | `1530 passed, 33 skipped, 7799 subtests passed` | 1435 passed, 33 skipped |
| demo-runtime | `1171 passed, 2 skipped, 751 subtests passed` | 1142 passed, 2 skipped |
| console-api | `251 passed, 165 subtests passed` | 250 passed |
| demo-contracts | `95 passed, 813 subtests passed` | the same |
| console-web, node 22.22.0 | typecheck clean; `Test Files 85 passed (85)`, `Tests 1170 passed (1170)` | 1165 passed |
| replay, `KC_EVIDENCE_ROOT` on a scratch copy of `one-block-20260926` | `61 passed, 111 subtests passed` (the five replay files, `test_base_return_replay.py`, the frozen pins and the differential) | 45 passed (without the frozen, differential and return replay files) |
| autonomy with `KC_EVIDENCE_ROOT` | `1549 passed, 14 skipped, 7848 subtests passed` (the 8 skips of `test_recorded_decisions.py` remain) | 1454 passed, 14 skipped |

One demo-runtime run made in parallel with the autonomy suite failed one timing test
(`test_supervisor.py::Stop::test_a_stop_before_the_handler_is_installed_is_classified_pre_handler_exit`, a file no
branch touched); the test passed five times alone and the suite passed whole when run alone (the row above).

## 7. The second fix round (on the re-check of `05fcea0`)

Two adversarial re-checks ran on `05fcea0`. Safety gave FAIL on one must-fix: the fix round's restore of R2 made Retry
a live motion-starting path in every LIVE mission, the no-word default path included. A retryable hold offered Retry,
and one press ran the park (`--do exit`) and then a fresh `--do cycle` with no qualification and no attended label,
where the plan refuses every new motion path in a LIVE run without its qualified word or the attended label. Honesty
on screen gave FAIL with two blockers and further must-fix findings, all in `apps/console-web`. Two fix branches were
built from `05fcea0`:

| Branch | Branch head | Merge | Verdict |
|---|---|---|---|
| `fix/ovn5-rt2`: the Retry switch, the endings that leave the arm low at the block, the binding refusals held for the operator | `026a575` | `1ad0cad` | verifier PASS; merged with `git merge --no-ff`, no textual conflict; every gate green after it |
| `fix/ovn4-web`, second and third passes: the demo screen follows the runtime's lists, a finished earlier run never drawn as the current failure, the rehearsals ported from the runtime | `4b4ae5d` | none | not merged at `6994bd8`: its verifier held one must-fix finding; a fourth pass answered it and the branch was merged later (the web merge, below) |

The Retry switch. `fix/ovn5-rt2` adds the runtime configuration key `mission.retry` (`MissionConfig.retry` in
`packages/demo-runtime/demo_runtime/config.py`), a boolean that defaults to `false`. A LIVE runtime offers and runs a
Retry only when `mission.retry` is `true` AND the configuration names the attended label
(`placement_envelope_attended`). `Supervisor.mission_retry_refusal` gives `mission_retry_not_enabled` without the
switch and `mission_retry_not_attended` without the label. The gate is read in three places: the hold's offer (without
it a LIVE hold's `next_actions` are `['stop']`), `request_retry` (it answers with the gate's code and writes nothing),
and `Supervisor.start_mission_park` (it refuses the park before any other gate, and spawns nothing). console-api
answers both codes with 409. A synthetic runtime is not held to the switch, so rehearsals keep Retry. The attended
label is now accepted beside `mission.retry` without an envelope word; without an envelope word the supervisor hands
the label to no controller, so it only admits the Retry. The key enters the configuration digest only when it is true,
so a configuration that leaves it off keeps its digest. The automatic retry (`AUTOMATIC_RETRY`) stays off as before.

The other changes of `fix/ovn5-rt2`:

- `grasp_set_down_lift_refused`, `grasp_retry_lift_refused`, `grasp_retry_approach_refused` and
  `align_inside_grasp_radius` hold as operator-needed with no Retry (`NO_RETRY_CODES` in `mission_execution.py`), even
  with the switch and the label on: a lift or an approach refused part way leaves the jaws open low at the block, a
  pose no recorded exit started from.
- `search_scene_no_block` and `search_scene_not_one_unplaced_block` (both end at the saved low pose) join
  `BLOCK_MOVE_CODES`: with the switch the hold offers `move_block_then_retry` and Stop, without it Stop only. They were
  unclassified and fatal.
- `mission_binding_candidate_not_placeable` and `mission_binding_no_candidate` hold as operator-needed with
  `next_actions` `['stop']` in every configuration; they were fatal. The attempt record and the hold carry an optional
  `detail`, the controller's `run.finished` failure detail bounded to 1024 characters, which names the deciding limit,
  the shortfall and the hint. Nothing decides on it.

Its verifier ran a real LIVE Supervisor with the fake controller over 38 cases: without the switch, with the switch and
no label, and with a word and the label but no switch, each hold serves `['stop']`, the request is refused with the
gate's code, one run is launched and nothing is parked; with the switch and the label the run is `start_cycle`,
`return_arm`, `start_cycle` and ends COMPLETE. Its tests failed before each change (29 failed on `05fcea0` code).

Gate tails at `1ad0cad` (the code tree of the head; the head adds this record only), demo-runtime run alone:

| Gate | Result | At `05fcea0` |
|---|---|---|
| `bash tests/test-docs.sh` | `test-docs: 2 failure(s)` (the two em-dash baseline files) | the same |
| autonomy | `1530 passed, 33 skipped, 7799 subtests passed` | the same |
| demo-runtime, run alone | `1188 passed, 2 skipped, 768 subtests passed` | 1171 passed, 2 skipped, 751 subtests |
| console-api | `251 passed, 167 subtests passed` | 251 passed, 165 subtests |
| demo-contracts | `95 passed, 813 subtests passed` | the same |
| console-web, node 22.22.0 | typecheck clean; `Test Files 85 passed (85)`, `Tests 1170 passed (1170)` | the same |
| replay, `KC_EVIDENCE_ROOT` on a scratch copy of `one-block-20260926` (19 files, sha256 unchanged) | `61 passed, 111 subtests passed` (the five replay files, `test_base_return_replay.py`, the frozen pins and the differential) | the same |

No gate has a new failure against `05fcea0`. No `autonomy/` or `bin/` file changed in this round, so the proven
primitives stay byte-identical to `914c131`.

### The web merge

`fix/ovn4-web` at `bd61c78` was merged with `git merge --no-ff` as `0e80122`, with no textual conflict. The branch had
already merged `6994bd8` (the Retry switch) into itself as `eb4fe79`, and its fourth pass (`d714813` to `bd61c78`)
was built on that. The merge touches 55 files, all under `apps/console-web` (3916 lines in, 452 out); no `autonomy/`,
`bin/` or `packages/` file changed, so the proven primitives stay byte-identical to `914c131`.

What it delivers on the demo screen and in the rehearsals:

- The screen follows the runtime's lists. Try again, Return arm, Start and Stop are on only when the hold's
  `next_actions`, the mission's `next_actions` and the capabilities list them (`cb41b8e`, `1e37444`, `08e3a4a`,
  `f3c5db0`). A hold after a written Retry shows Try again off (`76a6cd0`); a hold a Stop ended is not read as held for
  a decision and says the mission was stopped and that Start begins a new one (`ed6fc19`, `1efc379`).
- The Retry switch reaches the screen. `hold-live-default` rehearses the hold a LIVE runtime serves by default
  (`next_actions` `['stop']`, a Retry refused with `mission_retry_not_enabled`), and its render test pins that only Stop
  is on and nothing offers Try again (`1476391`); a refused Retry's receipt keeps the code and says what it means
  (`bd61c78`).
- A finished earlier run is never drawn as the failure of the run in progress, in one tab, after a reload or on a
  second display (`9ff833e`, `2a99266`, `8ca2cfd`). The fourth pass answers the must-fix finding its verifier held at
  `4b4ae5d`: the banner reads the command from the run on the screen before the history and draws no failure while the
  run's records are still to be read, so a completed Scan scene, Observe or Return arm is not drawn as a red failure in
  a fresh tab, with the history read pending or failed (`d05a7ab`, `reloadAfterEnd.test.ts`).
- The failure banner names the frame the failing stage decided on, or says none was recorded (`36de7e3`, `51e2335`); a
  placement refusal leads with the deciding limit and its shortfall, read from the fields track P records and derived
  from the margins when none is named (`a566c4f`, `a392c36`, `a9d4269`, `placementLimit.ts`); the scene map pairs each
  deficit of track S's record with its own limit (`d90dea1`).
- `BASE_MEASURED` after a return pulse is drawn on Return, not Carry (`dfa799f`); a completed scene scan fails no stage
  and draws no banner (`4ad4616`, `ad69b20`, `785b38e`); the camera chip never says LIVE without a stream and a stall
  shows its age and reason (`1b51dd5`, `59fb7c1`); the Ready chip judges a served robot status and Start is off while
  motion is held (`2f91d26`, `af3d7f9`); the simulated candidates carry the runtime's role codes, so the undo-to-inert
  rehearsal reports no deviation (`9965778`); the screen lists the by-hand steps the runtime names before a new
  mission (`69fb8b8`).
- The rehearsals decide as the head runtime decides. `syntheticRuntime.ts` ports `stop_state_from_events`,
  `classify_outcome` and `mission.next_actions`; the not-placeable rehearsals end at a Stop-only hold and the no-Retry
  endings hold with no Retry (`4c69730`, `fcccf13`); the one-block rehearsal sweeps only under `search_sweep`
  (`6f5efb0`, and `apps/console-web/README.md` now says so); the scene-scan rehearsal is Scan scene, then Start, and its
  cycle records SEARCH_SCENE (`4a7412b`, `4b4ae5d`); Return arm plays as the runtime's `--do exit` (`d714813`).
- Per-scenario render tests of the demo screen: every scenario of the catalogue is played through the store and
  rendered at its start, its hold and its end (`238dc31`, `22f99e1`, `087cd32`, `61f44c1`).

Gate tails at `0e80122`, demo-runtime run alone:

| Gate | Result | At `1ad0cad` |
|---|---|---|
| `bash tests/test-docs.sh` | `test-docs: 2 failure(s)` (the two em-dash baseline files) | the same |
| autonomy | `1530 passed, 33 skipped, 7799 subtests passed` | the same |
| demo-runtime, run alone | `1188 passed, 2 skipped, 768 subtests passed` | the same |
| console-api | `251 passed, 167 subtests passed` | the same |
| demo-contracts | `95 passed, 813 subtests passed` | the same |
| console-web, node 22.22.0 | typecheck clean; `Test Files 91 passed (91)`, `Tests 1304 passed (1304)` | 85 files, 1170 tests |
| replay, `KC_EVIDENCE_ROOT` | not rerun: the merge changed no Python file | `61 passed, 111 subtests passed` |

No gate has a new failure against `1ad0cad`. This record carries no adversarial verifier verdict on the fourth pass
(`d714813` to `bd61c78`) or on `0e80122`; the must-fix finding above is answered in code and in render tests, and
nothing of it was seen in a browser.

### The rehearsal offers (on the re-check of `d7fb1d3`)

A re-check of `d7fb1d3` pressed controls the DEMO screen drew on, one per replay of a scenario to a chosen moment: 49
of 129 presses were refused by the rehearsal's own runtime. Scan scene was on at the start in 17 of the 19 scenarios
and Observe at the end in 10, and both were refused with `synthetic_source`; Start was on at idle in
`observe-landmarks-only` and `undo-to-inert` and was refused with each scenario's own code. The simulation's capability
list served the four commands as enabled whenever no run or mission blocked, while its `submit` played Look and Scan
scene only where the scenario has one and its mission start refused those two scenarios, and the per-scenario render
test pinned the offers. So the first line of the web merge's list above ("The screen follows the runtime's lists") held
for the hold's and the mission's lists, and not for the rehearsals' capability list, until `2bc9a59`.

`2bc9a59` gives the synthetic source one set of standing refusals (no Look, no scene scan, the scenario's own start
refusal) that its snapshot serves and its `submit` refuses with the same code, after the run and mission blocks and
before the robot's motion refusal. `syntheticAdmission.test.ts` checks, for the plain script and every scenario, that
each of the four commands served on is admitted and each served off is refused with the code it was served with. The
scenario harness now presses every control drawn on at the start and at the end of each scenario, as the screen
presses it, and the test asserts none is refused; before the fix it failed in 17 of the 19 scenarios. The recorded
facts now read: Start alone at the start and after a completed mission, Scan scene beside it only in `scene-scan`,
nothing on in the Look and check scenarios until their own control is used, and Start and Return arm after a Stop at
a hold. In the two bring-up scenarios Scan scene and Observe are served off as not played rather than as held for the
robot, since a confirmation would not turn them on.

Limits: the probe presses at the start and at the end only; the controls of a hold (Stop, Try again, the candidates)
are those the re-check confirmed and are not pressed by it. Everything here is read from server-rendered markup; nothing
was seen in a browser.

Gate tails at `2bc9a59`, demo-runtime run alone:

| Gate | Result | At `0e80122` |
|---|---|---|
| `bash tests/test-docs.sh` | `test-docs: 2 failure(s)` (the two em-dash baseline files) | the same |
| autonomy | `1530 passed, 33 skipped, 7799 subtests passed` | the same |
| demo-runtime, run alone | `1188 passed, 2 skipped, 768 subtests passed` | the same |
| console-api | `251 passed, 167 subtests passed` | the same |
| demo-contracts | `95 passed, 813 subtests passed` | the same |
| console-web, node 22.22.0 | typecheck clean; `Test Files 92 passed (92)`, `Tests 1345 passed (1345)` | 91 files, 1304 tests |

What these rounds leave open is listed in section 8, with the rest.

## 8. Not done

Plan section 9, integration:

- Item 2, the combined-words matrix on the fake machine (every stage word on together, scripted Worlds for the ten
  items): not run. No autonomy test names the words of S, A and G together.
- Item 3, the golden events of the fake machine exported as JSON fixtures and read by a web test: not built.
- Item 4, the web bundle built from the integration commit with every U2 scenario screenshotted: not done (no Chrome
  was allowed).
- Item 5, `bin/runtime-release.sh stage` of the integration commit: not done.
- Item 6: this record is written; `feat/operator-console` is not moved, which waits for the operator's acceptance.
- The adversarial verification ran on `9891d14`, and its re-check on `05fcea0`; neither ran on this head. Their
  findings were answered by fix branches rather than by reverting merges.

Plan section 8, offline acceptance, at the head:

- Rows 4 and 5 (start 3, 21:17): the refusal before motion now names the deciding limit, the shortfall and the hint.
  Since the second fix round the runtime holds both binding codes for the operator with Stop only and carries the
  controller's sentence as the hold's `detail`; the head's console does not read that field. Not done: row 4's pan band
  is not named on the default path, and under `scene_scan` start 3 is classed `OUTSIDE_PAN_BAND` where the plan asked
  for NOT_PLACEABLE with the band marked inferred; the single-cube SEARCH sentence does not state the limit (its values
  do); a declared target with two not-placeable cubes keeps the counts-only refusal; `reach_after_advance` is not a
  declared word, so neither row's "admitted in the model" part runs.
- Row 1: the grasp at -9.0 mm needs `coarse_v2` and `near_grasp` together; `near_grasp` alone is still refused at SEARCH.
- Rows 6 and 7: the records of 21:24 and 21:31 are on the robot and not replayed. The committed failure 6/7 test still
  passes with the radial band disabled, and no test drives a clipped descent view through the loop.
- Rows 9 and 10 (console, bring-up): the console findings they waited on are answered in code and in the per-scenario
  render tests since the web merge (section 7); the bundle was not built or seen in a browser (item 4), so neither row
  is accepted.

Track items not built:

- P: `reach_after_advance` and `placed_multiview` are not declared words; `placed_multiview` and the one measured
  correction pulse are not built.
- R2: the controller's call of `policy_return.run_return` passes no `start` (`run_single_cube_cycle.py:1848` against
  `policy_return.py:171`), so a run never computes `base_return_error_2d`.
- U2: the retirement of `PadScenePanel.tsx` (the demo screen no longer mounts it; the file and its tests remain).

Findings left open at the head:

- Honesty on screen, since the web merge (section 7): a re-check of `d7fb1d3` confirmed the Try again and after-Stop
  fixes and found the rehearsal offers that `2bc9a59` answers; no verifier re-checked `2bc9a59`. The console parses the hold without its `detail` (`parseHold` in `apps/console-web/src/model/missionRecord.ts`), so the
  controller's sentence the runtime carries on a binding hold is not drawn from the hold; the banner's limit line reads
  the run's own record instead. The rehearsals' decision is a hand port of `mission_execution.py`: at `0e80122` its
  five code sets and its retryable prefixes equal the runtime's (compared by a script for this record), no test
  compares them, and the header of `syntheticRuntime.ts` still names `05fcea0`. After a Stop at a no-Retry hold the
  console follows the runtime's list, so the Return arm the Retry switch item below describes reaches the screen as
  listed.
- A fresh tab whose read of the run's own records fails (`historyEvents`) draws a completed Scan scene or Observe as a
  red banner, "the run ended without a stated outcome", with "No frame was recorded for this failure" and Return arm
  on, though the runtime recorded no failure (outcome unknown, exit 0). The failed read is not retried: `hydrateRun`
  sets `hydratedError` (`store.ts:1250-1253`) and the key of the DemoScreen effect does not change
  (`DemoScreen.tsx:282-284`), so the banner stays until the next run. The fourth pass answers the read still pending,
  not this failed read.
- Two transient states, each ending by itself. A fresh tab at a hold that offers a retry, before the failed run's
  records are read, says "Try again parks the arm and starts a fresh attempt" with no Try again drawn, and marks Pick
  failed with no banner (first renders only). At each run start, for up to one snapshot poll, the timeline heading names
  the previous run while drawing the new run's rows (`hold-then-retry-placed`, 27.3 to 29.3 s).
- The rehearsals do not state on the DEMO screen the non-default conditions they play under. `one-block` draws
  `SEARCH_SWEEP x2` in Find, and the word `search_sweep` appears only in a Diagnostics line (`syntheticScript.ts:489`).
  `align-no-proposal`, `align-clipped-top`, `near-block-grasp-miss` and `hold-then-retry-placed` offer Try again because
  a synthetic runtime is not held to the Retry switch; the default LIVE runtime offers only Stop there, and the screen
  does not say so. On the LIVE default, Scan scene is on after every end from the runtime's capability list, while the
  head controller refuses `--do scan` without the `scene_scan` word (`policy_search.py:437-442`) and no word is
  qualified; the runtime serves no word list, so the screen cannot know, and the refusal after a press is honest.
- Wording on the DEMO screen: Try again's reason says "park the arm with the proven exit" (`failureBanner.ts:212-214`)
  where the runtime says "the unchanged EXIT" and an exit from the retry poses is unknown; `one-block` shows "placed
  31 mm against 0 mm" and "base_returned 6 mm against 25 mm"; "placement_plan -48.5 mm against 37.96 mm" appears on
  successful Carry rows in 6 scenarios; the capability hold names "a capability whose name the runtime has not
  reported" and then shows a `detail` the runtime never writes at a capability hold (`synthetic.ts:568`; the runtime
  writes `detail` only from mission records, `mission.py:444-445`); the Cake column says "managed by this runtime, as a
  supervised child run" and "this resident has no live admission" under "No runtime, no robot and no camera were
  involved"; Return arm's "Off: a mission is being carried: only stop is accepted" is printed twice in 10 scenarios; the
  catalogue's unrendered `shows` text of both not-placeable scenarios still says "the mission ends".
- Safety notes: the unqualified-word refusal applies only when `KC_RUNTIME_MODE` is exactly `live`, so a controller
  started by hand runs unqualified words with no label; a Stop during `grasp_measured_retry`'s reopen or
  `grasp_recover`'s open in a non-mission run ends `gripper_did_not_open` (exit 5), not STOPPED, with nothing
  commanded after the Stop.
- `align_v2`'s inner bound checks each move's commanded target only: the realized radius can be up to 12.45 mm inside
  it, pan trims and step-backs re-command a measured radius without the check, and SEARCH's pickup band admits a
  -14 mm block under `coarse_v2` with `align_v2` that ALIGN then refuses after PREGRASP has moved (fails closed).
- The runtime: a proven-path `grasp_missed` whose jaws did not reopen is classed retryable, and with the switch and
  the label on the hold offers Retry for it; two guards are unpinned by any test (the exit-status-5 condition on the
  block-move codes, and asking `block()` for the cycle as well as the exit); the 409 mapping for `owner_external` and
  `hardware_commands_not_enabled` applies to every forwarded route; with no word, a mission with no cube in view holds
  on `mission_binding_no_candidate` with Stop only, where `9891d14`'s sweep ending was retryable.
- The Retry switch (section 7): with the switch and the label on, Retry is still offered after
  `grasp_retry_cube_lost`, `grasp_retry_not_aligned`, `grasp_retry_no_untried_radius` and `grasp_missed` under
  `grasp_measured_retry`, which stop lifted at the measured retry's look height, and after the ALIGN endings other than
  `align_inside_grasp_radius`; whether a recorded exit ever started from the look height is unknown. After a Stop at a
  no-Retry hold the runtime lists `return_arm` and `start`, which points to the same unchanged `--do exit` from the
  same low pose at the block that the Retry was withheld for; only the hold's `detail` describes the pose. Two
  fail-closed fallbacks have no test (`ProvenTargetRunner.retry_refusal` when the supervisor has no gate, and
  `MissionCoordinator.retry_refusal` for a hardware target with no gate). No run record names the attended label that
  admits a Retry: without an envelope word the label reaches no controller, and only the configuration digest binds
  it. `search_scene_scan_step_refused` and `scene_scan_step_refused` (the arm holds mid-scan) stay unclassified, so
  the mission ends fatal with no Retry. Commit `64a3883` carries two classification changes, the no-Retry endings and
  the scene endings, under a title that names the first only.
- Docs outside this record still describe the sweep as running with no word in `overnight-plan-2026-09-26.md`
  (sections 1 and 3); `apps/console-web/README.md` was corrected by the web merge.

Robot items, none run (the robot was off): every item of section 5 and every "Robot:" line of the work order's section
7, including the pull of the 21:24 and 21:31 records, the plan-only Start against `914c131`, and every qualification.
