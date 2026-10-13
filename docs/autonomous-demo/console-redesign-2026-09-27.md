<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Operator console redesign: the spec from the 2026-09-27 audit

This page is the build contract for the demo screen of `apps/console-web` (the default view, `DemoScreen.tsx`). The
binding brief is `.local-dev/ui-audit-20260927/audit.md` (items C1-C13, the API-only item, T1-T5, section 3, the top
10). The builder implements it test first; the verifier checks the build against every numbered rule here and every
row of section 8.

Since 2026-10-11 the default view at `/` is the show screen (section 13); the demo screen is at `?screen=demo`.

The operator's verdict it answers: "not intended for humans, shows updates too fast, no consistent history,
components in different places and inconsistent, contradicting information in many places; the error was short-lived
and quiet."

## 0. What this spec rests on

- The audit and its nine screenshots (read, not summarized from memory).
- Read-only GETs against the live runtime on port 8771 at about 14:46Z on 2026-09-27: `/api/mission`,
  `/api/mission/candidates`, `/api/robot`, `/api/cake`, `/api/snapshot`, `/api/history`,
  `/api/history/<run>/events` for both mission runs, `/api/capability/check`, `/api/capability/signature`.
- The read routes of `apps/console-api/console_api/app.py` (lines 1166-1195): every GET used below already exists
  and is a pass-through to the runtime (`_forwarded`).
- The code: `DemoScreen.tsx`, `Screen.tsx` (`ActivationReadback`, `CapabilityCard`), `DemoCakeColumn.tsx`,
  `CakePanel.tsx`, `FailureBanner.tsx` with `model/failureBanner.ts`, `model/stageTimeline.ts`, `store/store.ts`,
  `design/receipts.ts`, `design/Drawers.tsx`, `design/timeline.ts`, `model/readyChecklist.ts`,
  `model/missionView.ts`, `model/missionRecord.ts`, `model/capabilityCheck.ts`, `design/cake.ts`,
  `api/synthetic.ts`, `api/syntheticRuntime.ts`, `api/syntheticScenarios.ts`, `design/demoScenarioPlay.ts`.

Facts from the live record that shape this design (all read, none inferred):

| Fact | Where |
|---|---|
| Mission FAILED, `detail` `run_outcome:stopped:base_not_zero_before_segment`, `next_actions` `[open_gripper_by_hand, return_arm, start]` | `/api/mission` |
| The attempt: `targets[0].attempts[0]` has `outcome_class` stopped, `phase` GRASP_CLOSE, `stop_state` `{holding: true, base_ahead: false, moved: true}`, `budget_left` 1, `t_wall_utc` 14:24:40.265Z | `/api/mission` |
| Activation 1: abort-swap `REJECTED_BY_CAKE` at 14:20:21.636Z, slot generation 1 -> 1, capsule 9600566de6c9 unchanged | `/api/mission` `activations[0]` |
| Activation 2: swapped `OBSERVED` at 14:21:45.496Z, generation 1 -> 2, epoch 2 -> 3, capsule 9600566de6c9 -> 2f34a84c1971 | `/api/mission` `activations[1]` |
| The candidates carry key and digest only: no `role`, `label`, `expect` (the runtime config sets no `cake.candidate_roles`) | `/api/mission/candidates` |
| The refusal `action.refused` seq 3904, `reason_code` base_not_zero_before_segment, `action_id` act-0007 | run events |
| Same `action_id`: `predicate.evaluated` seq 3902 `base_zero` value false, measured 0 reads against 3 reads; `measured.state` seq 3903 base 1.171875 deg/s, gripper 68.116; `action.planned` label "CLEARANCE lift" | run events |
| `run.finished` `failure.measurements` is empty and `failure.observation_ids` is empty | run events |
| The px values shown on today's banner come from `straddle_far_edge_row` (seq 3825, `action_id` null, 31 s before the stop) | run events |
| The run's own `finished_wall_utc` 14:24:40.114Z; the mission record carries no end time field | `/api/history`, `/api/mission` |
| Capability checks are dated 2026-09-26 12:40Z, resident session 01a0dda1..., not the current 01a0e337... | `/api/capability/check` |
| Signature check `runtime_session` rt-20260926T184836Z-e34dcd31 at 2026-09-26T20:26:13Z; the snapshot's `runtime_session` is rt-20260927T141418Z-89540562 | `/api/capability/signature`, `/api/snapshot` |
| Live capability slot: generation 2, capsule 2f34a84c1971, active; holder slot capsule 9600566de6c9, health UNKNOWN, pid 0 | `/api/robot` |
| `mission/start` is sent with an empty body: no attestation reaches the runtime | `api/http.ts` line 822 |

## 1. Design direction

The console is a flight recorder that an operator reads standing up, three metres from the screen, while a robot holds
a block. It inherits the existing system and adds no new colour or face:

- Colour: `design/theme.ts` only. `C.ok` #34d399, `C.bad` #f87171, `C.warn` #fbbf24, `C.wait` #a78bfa, `C.accent`
  #00d4ff, `C.mute` #8b8b96 on `SURFACE.page` #0a0a0f. Each verdict word has exactly one of these (section 5.1).
- Type: Geist Sans for words, JetBrains Mono only for ids, digests and times. Numbers use tabular figures.
- The one loud element is the outcome card: the mission verdict word at 28 px, weight 650, in its verdict colour, a
  4 px left rule in the same colour and a 6 percent wash of it as the card background. Everything else is quiet:
  13 px region headings in sentence case, no uppercase labels, no `text-transform` anywhere on the demo screen.
- No motion that is not the answer to a press. New timeline entries appear without animation.
- Every region heading names what it is: "Mission", "Cake on the robot", "Timeline", "Stages", "Scene", "Wrist camera".

## 2. Regions and layout

### 2.1 Desktop (1100 px and wider)

```
+---------------------------------------------------------------------------------------------------+
| TOP BAR  shinro  [Live]  LeKiwi bench: connected  Motion permitted  VERDICT sentence  Start Stop Diag |
+-------------------------------------------------------+-------------------------------------------+
| MISSION (outcome card), 7 of 12 columns, 300 px high  | CAKE ON THE ROBOT, 5 of 12, 300 px high   |
|  Mission  from the mission record  [History] [Ack.]   |  Cake on the robot  oldest first          |
|  verdict word, mission id, started / ended            | +---------------------------------------+ |
| +---------------------------------------------------+ | | activation cards, oldest first        | |
| | run line: run n of m, verdict, stage, state, time | | | [pending: prepared Plan buttons]      | |
| | next steps in the runtime's order [Return arm]    | | | signature check card (dated)          | |
| | what the robot did, what the mission check refused| | | live slot footer                      | |
| | why it stopped + its own measurement              | | +--- bounded, scrolls within the card --+ |
| | where the robot was left (holding, gripper, base) | |  (More below: scroll within this card.)   |
| +------ bounded, scrolls within the card -----------+ |                                           |
|  (More below: scroll within this card.)               |                                           |
+--------------------------------+---------------------------------+--------------------------------+
| STAGES (5 compact rows)        | SCENE (map)                     | WRIST CAMERA (thumbnail 320 px)|
|                                |                                 |  [Scan scene] [Observe]        |
+--------------------------------+---------------------------------+--------------------------------+
| TIMELINE, full width, height 38vh (min 260 px), scrolls, oldest at the top                         |
|  Mission started 14:16:38Z / Run 1 group / Cake lines / Run 2 group / Mission failed / presses     |
+---------------------------------------------------------------------------------------------------+
```

The live status is the prime area (operator layout ruling 2026-09-28): the stages, the scene and the wrist camera sit in
the middle of the screen, directly under the mission card and Cake, and the timeline, the record of what happened, is
below them. The mission card stays at the top beside Cake, under the top bar with Start and Stop. The grid places its
regions in document order (no region carries `order`, `grid-row` or `grid-area`), pinned by
`design/liveInTheMiddle.test.ts`.

Stable positions (operator layout ruling 2026-09-28, amended in round 5 after verify-r4 measured the strip dropping 580 px
at 1440 x 900 when a run ended): the mission card and Cake have one fixed footprint, 300 px high at desktop width
(`design.css`, `.demo-grid__mission` and `.demo-grid__cake`), whether a run is idle, running or ended. Each keeps its
head in view (the mission card: its label, History, Acknowledge, the verdict word, the mission id and its times) and
draws the rest of its account, whole and in the order above, in a bounded region that scrolls within the card
(`design/BoundedAccount.tsx`: a focusable, labelled region, so it scrolls by keyboard too). Nothing of the account is
dropped; while more of it is below the fold, the card says "More below: scroll within this card." The runtime's steps come
right after the run line, so a control the runtime names (Return arm, Try again) is in view at the fixed footprint, and
the long account (the mission check, why it stopped, where the robot was left) follows them. While the mission waits
for Cake, Cake's pending choice (the prepared Plans, one button each) is drawn above Cake's bounded region, outside it,
so the choice the top bar sends the operator to is never below a fold; Cake's region follows the newest activation
(it scrolls itself, never the page, so that card's top is at the region's top) and says "More above" when older cards
are above it (verify-r5 M2). The top bar's readiness
sentence keeps room for two lines (three below 1100 px, four below 720 px), so a state with a shorter sentence does not
lift the screen by a line; its place is drawn on Ready too, empty, the normal state before Start, which has no sentence
and grew the bar 55 px at Start at 1440 (verify-r5 M1). Below 1100 px Start, Stop and Diagnostics take the bar's last
row of their own, so a wider Start label does not rewrap it. A longer sentence still grows the bar, and Stop, its place
and its stickiness are unchanged. So the stages, the
scene and the wrist camera keep their place when a run starts or ends, and at 1440 x 900 their top stays on the first
screen (y 641 in the synthetic stage, where a motion-hold panel sits above the grid). Pinned by
`design/stablePositions.test.ts` (the heights, the head outside the bounded region, every part of the account inside it)
and measured in a real headless Chrome by `tests/test-browser-layout.sh` (the region tops sampled once a second through
idle, running and ended, at 1440 x 900, 1039 x 1030 and 390 x 844, each from Ready and from Not ready, within 4 px).

Grid: `grid-template-columns: repeat(12, minmax(0, 1fr))`, 16 px gaps, 20 px page gutters, `align-items: start`. The
outcome card and the Cake region are cards (12 px radius); the timeline, stages, scene and wrist are plain panels
with a 1 px `--color-border` rule, so the card shape itself says "this is a verdict".

### 2.2 Narrow fallback (below 1100 px; phone width from 360 px)

One column, 16 px side gutters, no horizontal page scroll, in this order: top bar (wraps into three lines: identity
and mode; connection, motion and verdict; Start, Stop, Diagnostics), mission, Cake, stages (five rows stacked), scene,
wrist, timeline (`max-height: 70vh`, scrolls). Ids and digests use `overflow-wrap: anywhere`. The Diagnostics
drawer becomes full width below 720 px. The mission card is 380 px high and Cake 400 px (a candidate's digest wraps under its button here, so the pending
choice is 170 px at 390 px), each with its bounded region
(2.1), so the strip's top (the stages) keeps its place when a run starts or ends. The scene is 600 px high here and
draws its map and legend in its own bounded region: its caption ("Drawn from the records of run ...") and its legend
grow once a run is drawn (464, 525 and 571 px idle, running and ended at 390 x 844), and they moved the wrist camera
under it 61 and 46 px until the scene kept one height (verify-r5 browser must_fix 1). At desktop width the scene sits
beside the wrist camera and keeps its natural height.

### 2.3 Deviations from audit section 3, and why

1. The Scan scene and Observe controls stay beside the wrist thumbnail (the audit's strip does not name them). They are
   the runtime's own `scan_scene` and `observe` commands and have no other home on the main screen. They are never
   primary (rule 4.3).
2. Return arm and Try again live only inside the mission card's step list, and only when the runtime's list names them
   (rule 6.2.4). The audit places "next steps from next_actions" there; the code today also draws them in the banner,
   which this spec deletes.
3. The capability slot check (its Apply buttons and its records) moves to a new Diagnostics tab "Cake", not to the
   main Cake region. Its records are not mission records (audit C1), and a check is refused unless the supervised
   host is held, which a demo never has.

## 3. One source of truth per region

Every route below exists today (section 0). Field paths are JSON paths of the GET answer. The console-web side reads
some of them for the first time; section 3.8 lists those parser changes.

### 3.1 Top bar

| Element | Route | Fields |
|---|---|---|
| Robot name | GET /api/robot | `identity.display_label`, `identity.labelled` |
| Connection | GET /api/robot | `reachable`, `stale`, `connection.forward.state`, `connection.forward.since_wall_utc`; a failed read keeps the last answer (`store.robotError`, `store.robotReadAtMs`) |
| Motion permission | GET /api/robot | `motion_permission.applies`, `.permitted`, `.reason_code`, `.detail` |
| Mode mark | GET /api/snapshot | `data_mode`; plus `store.sourceKind` and `store.replay` |
| Readiness verdict and Start | composed, rule 4.2 | GET /api/mission `state`, `waiting_reason`, `stop_requested`, `next_actions`, `targets[].attempts[-1].stop_state`; GET /api/snapshot `capabilities[id=start_cycle]`, `unresolved_command`, `owner`, `contract_version`; GET /api/robot `motion_permission`; `store.commands` (the guards `startOff` composes today, unchanged) |
| Stop | GET /api/snapshot, GET /api/mission | `capabilities[id=stop].state`, `.reason`; `hold.next_actions` (today's `stopOffReason`, unchanged) |

### 3.2 Mission (the outcome card)

| Element | Route | Fields |
|---|---|---|
| Mission verdict, id, start | GET /api/mission | `state`, `waiting_reason`, `stop_requested`, `mission_id`, `started_wall_utc` |
| Mission end | GET /api/history | `runs[run_id = the mission's last run].finished_wall_utc` (rule 6.7) |
| Run line | GET /api/mission, GET /api/history | `targets[].runs` (order gives "run n of m"), `targets[].label`; `runs[].outcome`, `.started_wall_utc`, `.finished_wall_utc`, `.exit_status`; while running, SSE `/api/events` `run.started`, `state.transition` |
| Stage and state | GET /api/mission | `targets[].attempts[-1].phase` (the state), mapped to a stage by `presentationStepInContext` |
| Why it stopped | GET /api/history/<run>/events | `run.finished` `payload.failure.detail`, `payload.reason_code` |
| Its own measurement | GET /api/history/<run>/events | rule 6.3.4 (the `action_id` join); else `run.finished` `payload.failure.measurements`; placement limit codes keep `placementLimit` |
| Its own frame | GET /api/history/<run>/events, GET /api/observations/<id>/frame | `run.finished` `payload.failure.observation_ids[0]` only |
| Where the robot was left | GET /api/mission; run events | `targets[].attempts[-1].stop_state.holding`, `.base_ahead`, `.moved`; the newest `measured.state` of that run: `payload.joints.gripper`, `t_wall_utc` |
| Next steps | GET /api/mission | `hold.next_actions` while held, else `next_actions`, in the runtime's order |
| While held for you | GET /api/mission | `hold.reason_code`, `hold.phase`, `hold.stop_state`, `hold.retry_requested` |
| Acknowledgement | this display (rule 6.2) | not sent anywhere |
| What the robot did, and what the mission check refused (3.2.1) | GET /api/mission, GET /api/history/<run>/events | the card's run's own attempt: `targets[].attempts[run_id = the run].reason_code`, `.measurements`, `.phase`, `.stop_state.holding`; that run's `run.finished` `payload.predicates.object_secured`, `.base_returned`, a `state.transition` to `RELEASED`, the newest `release_margin` predicate (`measured`, `threshold`) when held |

#### 3.2.1 A mission check that refused (operator ruling 2026-09-28)

A run can do its physical job and still end FAILED: the mission check (`autonomy/manipulation/target_binding.py`, run by
the controller at SEARCH and at PLACED) decides whether this target's block is the one that was placed. The live case was
mission-f9ac04941f32 (2026-09-28): the run grasped the block, released it with a predicted worst-case margin of 40.6 mm
(38.0 mm required), the base was measured back, and the check refused `mission_earlier_target_disturbed` (by the
operator's reading, SEARCH had counted a pad-print logo as an already placed block; the record does not say which
position it was, so the card does not either). The card said "Mission failed" and the controller's sentence only.

When the card's run's attempt ended on one of the eight refusal and attribution codes of `target_binding.py`, on one of
the three retired codes an old record still carries (3.2.4), or on one of the controller's six refusals to look (3.2.2),
the card draws `data-mission-check="<code>"` before "Why it stopped",
with two sentences (`model/missionCheck.ts`):

1. What the robot did, from the run's own records: "The robot grasped the block, released it at a point the release
   admission predicted was over the pad and the base was measured back where it started. The release was admitted with a
   predicted worst-case margin of 40.6 mm to the pad edge (38.0 mm required). That margin is a prediction made before the
   release; where the block came to rest is what the mission check measured after it." Each clause is said only when its
   record says so (`object_secured` true, a transition to RELEASED, `base_returned` true). The base clause is "the base
   was measured back where it started" only when the run's records hold a transition to BASE_ADVANCE or BASE_MEASURED
   (the controller's own `advanced` test) and the `base_returned` predicate does not name the scalar
   `base_never_advanced`; otherwise it is "the base was not driven", because the controller sets `base_returned` true
   for a base that never advanced once PLACED is reached and no return was measured. The release clause rests on
   the release admission: `policy_placement.py` raises `release_not_admitted` with the cube held unless the PREDICTED
   worst-case margin to the pad edge meets the required one, so RELEASED is reached only after an admitted release. PLACED
   is judged afterwards from the exit view, so the card never states "on the pad" as an outcome (for
   `mission_placed_no_cube_at_release_point`, and the retired `mission_placed_no_unclaimed_cube`, the check found none). The margin is the `release_margin` predicate's when the
   page holds it (with the required value), else the attempt's `predicted_worst_case_centre_margin_mm`; the store keeps
   only false predicates of a run (`keptEvent`), so on the screen it is usually the attempt's. With the run's events not read, only the attempt: "The run
   ended at BASE_RETURNED with the gripper empty." For a binding refusal (raised at SEARCH, before PREGRASP, and every one
   of its sentences in `target_binding.py` says so): "Nothing was picked up."
2. What the check refused, one plain sentence per code with the counts the refusal carried: "The mission check did not
   accept the placement: <reason>. The record says placed: false." for the placement codes, "The mission check
   refused this target at SEARCH: <reason>." for the seven binding codes. It never says the block was placed. A binding
   sentence counts what `target_binding.py` counts: a candidate block has a measured position in the pad frame, is not
   already on the pad and is not bound or claimed by an earlier target, and `mission_binding_no_candidate` is raised when
   THIS target has none (a declared target counts only candidates of its mode), so it says "it found no block this
   target can take" with the record's counts (blocks seen, already on the pad, candidate blocks, bound by earlier
   targets, matching this target), never that no block was off the pad. `mission_target_mode_mismatch` says the bound
   block "no longer measures the target's placement mode", which holds also when it measures no bounded placement.

The controller's own sentence, the code and the measurements stay under "Why it stopped" (the account is said beside the
record, never in its place). A code outside the table draws no account. `model/missionCheck.test.ts` holds the table to
the codes `target_binding.py` defines and to the six below (it reads both files), pins every sentence, and pins the live
record.

#### 3.2.2 A mission check that could not look

Before `target_binding.py` decides anything, the controller lists the blocks of the view it checks and reads each one's
position (`run_single_cube_cycle.py`, `_mission_candidates`, `_mission_seen_cube`). It refuses with its own code, and
nothing is counted, when there is no view after the release (`mission_placed_no_exit_view`), the frame cannot be read
(`mission_candidates_frame_unreadable`), the listing raises (`mission_candidates_failed`), is refused
(`mission_candidates_refused`) or stops with proposals left (`mission_candidates_incomplete`), or a listed block has no
readable position (`mission_candidate_unreadable`). The five listing codes come from SEARCH (`_mission_search`, nothing
picked up) and from PLACED (`_mission_placed`, then `policy_return.py` raises after the release and the base return), so
the card reads which from the record: a transition to RELEASED or PLACED among the run's events, a stop phase of
RELEASED, PLACED, BASE_RETURN or BASE_RETURNED, or the release's predicted margin (only `policy_return.py`'s raise hands
it to these codes) is PLACED; a stop phase of the locate phase is SEARCH; with neither it says "The mission check could
not decide: <reason>." and nothing of where.

- At PLACED: sentence 1 as above, then "The mission check could not check the placement: <what it could not do>, so it
  counted nothing. The record says placed: false." The attribution of such a run is
  `Placement(False, None, None, 0, None, None, code)` since the pad policy of 2026-09-29, so the record carries
  `on_pad_count` 0 and no `release_distance_mm` or `release_tolerance_mm`; the card says "The 0 the record carries for
  blocks on the pad is not a count: nothing was counted." and never states a count. An older record carries
  `unclaimed_placed_count` 0 and `earlier_targets_undisturbed` false instead (`Placement(False, None, None, 0, False,
  code)`), and the card says the same of its "unclaimed placed blocks".
- `mission_candidates_incomplete` also names the listing budget: at most 3 blocks in one view
  (`cube_candidates.MAX_PASSES`) and up to 9 more when they are on the pad
  (`target_binding.LISTING_ON_PAD_EXEMPT_PASSES`), a named limit of the single-block version.
- At SEARCH: "Nothing was picked up." and "The mission check could not bind this target at SEARCH: <what it could not
  do>."

#### 3.2.3 Every code the controller ends a run with has an account

`model/controllerCodes.test.ts` reads `run_single_cube_cycle.py` and every `autonomy/manipulation/*.py`, finds every
`self.failed(` and `ctx.failed(` raise site and resolves its code: a literal as written, the desktop perception family
(`perception_remote_` and a `remote_perception.py` code, glossed as a family in `failure.ts`), and each named code from
its own source (`LIVE_FORWARD_EXCLUDED`, `target_binding.py`'s codes for `refusal.reason_code` and
`attribution.reason_code`, `_mission_candidates` for the SEARCH listing site, the base pulse's two refusal codes). A
site it cannot resolve fails it, and so does a code with no account in this table or in the failure glossary. The codes
of `policy_return.py` (every one can end a run at or after PLACED) are checked apart, and each mission-check one must be
said as a placement. Residual, named: the two stop codes the controller writes straight into the mission result
(`operator_abort_perception`, `operator_abort_gripper`, after an operator's Stop) are not raise sites, this test does not
cover them, and the failure glossary has no sentence for them, so the card shows them as the bare code.

#### 3.2.4 The pad policy of 2026-09-29 (ruling (ii))

PLACED credits any block on the pad within the release tolerance of this run's own release point, whatever was there
before (`target_binding.py`, THE RELEASE AGREEMENT; `docs/autonomous-demo/decision-log.md`). Its one placement refusal
is `mission_placed_no_cube_at_release_point`, raised by `policy_return.py` with `on_pad_count` (every block on the pad in
the exit view), `release_distance_mm` (the nearest one's distance to the release segment, null when none is on the pad)
and `release_tolerance_mm` (26.0 mm, 30.0 mm with a sideways release offset). The card says: "after the release it found
no block on the pad within <tolerance> of the point where the robot released its block, so it credited no block to this
run; it saw <n> blocks on the pad, the nearest <d> from the release point, and a block elsewhere on the pad does not
count for this run. Check whether the block left the pad." A PLACED is "a block is on the pad at the release point",
not proof that it is the robot's own block (the accepted residual), so no sentence says whose block is on the pad.

The three codes of the retired claim rules (`mission_earlier_target_disturbed`, `mission_placed_no_unclaimed_cube`,
`mission_placed_more_than_one_unclaimed_cube`) are no longer written by anything. Their sentences stay, marked retired
(`RETIRED_MISSION_CHECK_CODES`), because History still shows missions that ended on them (mission-f9ac04941f32);
`missionCheck.test.ts` and `controllerCodes.test.ts` fail if a controller source writes one again. The code
`mission_placed_only_a_block_listed_at_search` existed only on the pad-policy branch before ruling (ii) and no record
carries it, so it has no account.

Two binding sentences name the pad policy's residuals: `mission_binding_no_candidate` says that when SEARCH sweeps, a
block on the pad in its first view (an earlier block, or the printed logo read as one) ends the sweep, so a block off
the pad outside that view may not have been looked for; `align_more_than_one_cube_verified` says blocks on the pad count
too. `mission_binding_candidate_not_placeable` with several blocks off the pad (an auto target weighs them all) says none
of them has a bounded placement.

### 3.3 Cake on the robot

| Element | Route | Fields |
|---|---|---|
| Activation cards | GET /api/mission | `activations[]` in record order: `operation_uuid`, `t_wall_utc`, `candidate_key`, `activation_state`, `transaction_phase`, `detail`, `slot_before.{read, generation, capsule, epoch}`, `slot_after.{...}`, `stopped_during_activation` |
| Expected outcome | GET /api/mission/candidates | `candidates[key].expect`, `.role`, `.label`; served only when the runtime config sets `cake.candidate_roles` (8771 does not) |
| Pending choice (held for Cake) | GET /api/mission/candidates | `candidates[].key`, `.payload_digest`, `.role`, `.label`; the press is today's `store.activateCandidate` |
| Signature check card | GET /api/capability/signature, GET /api/snapshot | `checks[-1].{t_wall_utc, runtime_session, policy, complete, rows_as_expected, rows_not_as_expected, rows_error, rows_skipped, rows[].{capsule, key, observed, reason_code}}`; `off_reason`, `in_flight`; snapshot `runtime_session` |
| Live slot footer | GET /api/robot | `capability.{name, active, generation, capsule}`, `resident.epoch`, `slots.stale` |
| What set the live slot | GET /api/mission, GET /api/capability/check | the newest `activations[]` whose `slot_after.generation` equals the live generation, else the newest `checks[]` of the current resident session (`current.session_uuid`) with that `slot_after.generation` |

### 3.4 Timeline

| Entry | Route | Fields |
|---|---|---|
| Mission started, stop requested | GET /api/mission | `started_wall_utc`, `stop_requested_wall_utc` |
| Cake lines | GET /api/mission | `activations[].t_wall_utc` and the card fields |
| Retry asked | GET /api/mission | `targets[].retries[].t_wall_utc` (served by the synthetic source; the live record carries `retries: []`) |
| Run groups | GET /api/history, GET /api/history/<run>/events, SSE /api/events | `runs[].started_wall_utc`, `.finished_wall_utc`, `.outcome`; events of the kinds in rule 6.3.2 |
| Mission ended | GET /api/mission, GET /api/history | `state`, rule 6.7 |
| Presses, acknowledgements, link changes | this display | rule 6.5 |

### 3.5 Stages

`stageTimeline` over the mission's run folds, newest run, as today (`timelineRuns`). Only the label, the status word and
the `t_wall_utc` of the first `state.transition` into the stage are drawn. The attribute pair
`data-stage` / `data-stage-status` stays.

### 3.6 Scene

`foldSceneMap` over the mission runs' events, as today. The capability sentence reads GET /api/robot `capability`
(rule 5.7.5), never a fixed text.

### 3.7 Wrist camera

`cameraPanel` as today (snapshot telemetry, `latest_retained_observation.{observation_id, t_wall_utc}`, the frame
route). One state word and one line; no age counter (rule 6.8).

### 3.8 What the API does not give, and what changes where

| Needed | Status | Change |
|---|---|---|
| The attempt record (`targets[].attempts[]`) | served by the runtime and by the synthetic source | console-web: `missionRecord.ts` parses `attempts[]` (today it drops them) |
| Candidate role, label, expect on `/api/mission/candidates` | served only with `cake.candidate_roles` configured | console-web: `MissionCandidate` keeps `role`, `label`, `expect` (today key and digest only); read the list once per mission id, not only while waiting |
| The stop's own measurement | not in `failure.measurements` for this code | console-web: the `action_id` join over the run's own events (rule 6.3.4) |
| Every event of every mission run, unbounded | `/api/history/<run>/events` serves all (284 of 284 for m6ed2dd9) | console-web: the store keeps the timeline kinds of each mission run, never evicted while the page is open (today's fold keeps a 400-event tail and `RUN_FOLDS_MAX` 8) |
| A mission end time | absent | derived, labelled (rule 6.7) |
| An acknowledgement record | absent; no POST allowed | this display only (rule 6.2) |
| Presses from other displays that the runtime did not record | absent | not shown; named as a residual (section 10) |

console-api: no read route change is required. Every field above is already forwarded unaltered.

## 4. States

### 4.1 The states, as read from records

| State | Detected from |
|---|---|
| Idle | no active run (`snapshot.active_run` null) and either no mission (`missionError` `no_mission_recorded`) or an ended mission with no by-hand step listed whose outcome card is acknowledged |
| Running | `mission.state` RUNNING or `snapshot.active_run` non-null |
| Held for Cake | `mission.state` WAITING_FOR_CAPABILITY and `waiting_reason` capability_unavailable |
| Refusal | Held for Cake and the newest activation is a refusal (any refusal word of 5.1) |
| Held for you | `mission.state` WAITING_FOR_CAPABILITY and `waiting_reason` operator_decision |
| Stopped or failed, block held | `mission.state` FAILED or STOPPED and `targets[selected].attempts[-1].stop_state.holding` is true or null |
| Complete | `mission.state` COMPLETE |
| Stale | a region's source has a failed read or its last good read is older than 15 s (`ROBOT_ANSWER_STALE_AFTER_MS`) while the snapshot still answers |
| Disconnected | the snapshot read fails (`store.sourceError`) or the event stream is closed |

### 4.2 The readiness verdict (one, in the top bar)

It is computed from the same guards that decide Start, so the two cannot disagree. The first rule that holds wins:

1. A mission carried or a run active: **Running**, **Waiting for Cake** or **Held for you**. Start is disabled (its
   label says the mission word, as `missionStartLabel` does today).
2. `startOff` non-null (today's composition in `DemoScreen.tsx` lines 305-312): **Not ready**, the first reason as
   the sentence. Start is disabled.
3. An ended mission whose step list does not begin with `start`: **Steps first**, sentence = the first step's words
   (5.7.2). Start is enabled, never primary.
4. Otherwise **Ready**. Start is enabled and primary.

Acknowledging the outcome card (6.2) changes the card only. The verdict keeps reading the record, so a mission that
ended holding the block reads **Steps first** until a new mission is written down.

Items the runtime does not gate Start on (wrist preview, arm acquisition, snapshot component health) are listed in the
verdict's detail popover under "Not checked by the runtime" and never change the verdict (C7).

### 4.3 Primary and enabled

At most one control on the whole screen (dialogs aside) carries `data-primary="true"` (the `default` Button variant). The primary is
the first entry of the runtime's current step list when it is a control on this screen and no by-hand step precedes
it; with no mission it is Start. Enabled is always the runtime's answer (today's guards); this spec changes emphasis,
never admission.

| Control | Idle, ready | Running | Held for Cake / Refusal | Held for you | Stopped or failed, block held | Complete | Stale | Disconnected |
|---|---|---|---|---|---|---|---|---|
| Start | enabled, primary | disabled | disabled | disabled | enabled if `startOff` null, not primary | enabled, primary | as its guards say, not primary | disabled |
| Stop (one, top bar) | runtime says | enabled | enabled | enabled if the hold lists it | runtime says (disabled: no run) | runtime says | as its guards say | as `stopOffReason` says over the last snapshot, never off because a read failed (a lost answer gives an `unknown` press entry) |
| Diagnostics | enabled | enabled | enabled | enabled | enabled | enabled | enabled | enabled |
| Candidate buttons (Cake) | absent | absent | enabled per `activationOffReason`; the `activate` role primary if roles are served, else none | absent | absent | absent | disabled while the mission read is stale | disabled |
| Try again (mission card) | absent | absent | absent | present only if the hold lists `retry` (primary) or `move_block_then_retry` (not primary) | absent | absent | disabled | disabled |
| Return arm (mission card) | absent | absent | absent | absent | present if listed, enabled per `controlOffReason`, primary only if first with no by-hand step before it | absent | disabled | disabled |
| Acknowledge (mission card) | absent | absent | absent | absent | enabled | enabled | enabled | enabled |
| Run signature check (Cake) | runtime says | absent | absent | absent | runtime says | runtime says | disabled | disabled |
| Scan scene, Observe | runtime says, never primary | disabled | disabled | disabled | runtime says, never primary | runtime says, never primary | as guards say | disabled |

Stop has its own fixed danger style (red outline) in every state; it is never styled as primary and never repeated.

### 4.4 What each region shows per state

| State | Top bar verdict | Mission card | Cake | Timeline | Stages, scene, wrist |
|---|---|---|---|---|---|
| Idle | Ready / Not ready | "No mission yet in this runtime session", or the acknowledged card collapsed to one line | "No Cake activation in this mission"; signature card; live slot | everything since the page opened, or the ended mission | "Not reached" rows; last scene; wrist word |
| Running | Running | verdict Running, run n of m, the current controller state with the time it was entered | cards so far | follows, appends | the active stage "In progress" |
| Held for Cake | Waiting for Cake | "Waiting for Cake: run n measured BASE_ADVANCE and needs mobile_approach" (run verdict Needs capability) | cards so far, then the pending choice | the run group ends "Needs capability" | Find "Passed" or as recorded; no Cake text on any stage |
| Refusal | Waiting for Cake | unchanged | the refusal card appended under the earlier ones; the pending choice stays | a Cake line appended | unchanged |
| Held for you | Held for you | the failed attempt, its cause, the hold's steps | unchanged | appended | the ended stage in its run word |
| Stopped or failed, block held | Steps first | red Failed (or amber Stopped on request), cause, measurement, where the robot was left, steps, Acknowledge | unchanged | "Mission failed" line appended after the last run group | the ended stage "Stopped here by the controller" |
| Complete | Ready | green Complete, the placement, the finish time, Acknowledge | unchanged | "Mission complete" line | all stages as recorded |
| Stale | unchanged verdict, plus the stale region's own marker | content kept, marker "Not read since HH:MM:SSZ" if its source is the mission | marker if robot or mission reads are stale | marker if the event stream or history read is stale | wrist marker as the camera panel says |
| Disconnected | Not ready: "The runtime is not answering." (no time; deviation 62) | content kept, dimmed to 60 percent, marker | kept, dimmed, marker | kept; one entry "This display lost the runtime at HH:MM:SSZ" on a display that had reached it; a display that never reached it says "The runtime has not answered this display." and adds no entry | kept, dimmed |

## 5. Copy and vocabulary

### 5.1 One verdict vocabulary

Each word has one colour and one meaning everywhere (main screen, timeline, Diagnostics). `data-verdict` carries the
id. An unknown record token is drawn as itself, in `C.mute`.

| Record | Value | Word (id) | Colour |
|---|---|---|---|
| mission `state` | RUNNING | Running (`running`) | accent |
| | RUNNING with `stop_requested` true | Stopping (`stopping`) | warn |
| | WAITING_FOR_CAPABILITY + capability_unavailable | Waiting for Cake (`waiting_cake`) | wait |
| | WAITING_FOR_CAPABILITY + operator_decision | Held for you (`held_for_you`) | warn |
| | WAITING_FOR_CAPABILITY + other reason | Held: `<reason code>` (`held_other`) | warn |
| | COMPLETE | Complete (`complete`) | ok |
| | STOPPED with `stop_requested` true | Stopped on request (`stopped_on_request`) | warn |
| | STOPPED with `stop_requested` false or absent (deviation 75) | Stopped with no stop request on its record (`stopped_no_request`) | warn |
| | FAILED | Failed (`failed`) | bad |
| run `outcome` | placed_new | Placed (`placed`) | ok |
| | already_placed_noop | Already placed (`already_placed`) | ok |
| | stopped, the run's events hold a `stop.requested` | Stopped on request (`stopped_on_request`) | warn |
| | stopped, no `stop.requested` | Stopped by the controller (`stopped_by_controller`) | bad |
| | refused | Refused (`refused`) | bad |
| | failed, `reason_code` mission_capability_required | Needs capability (`needs_capability`) | wait |
| | failed, any other code | Failed (`failed`) | bad |
| | unknown, placement command | No stated outcome (`no_outcome`) | warn |
| | unknown, a scan, Observe or Return arm that ended well (`nonPlacementCommandSucceeded`) | Done (`done`) | mute |
| activation | OBSERVED, `expect` OBSERVED or absent | Activated (`activated`) | ok |
| | OBSERVED, `expect` something else | Activated, not as expected (`activated_unexpected`) | bad |
| | REJECTED_BY_CAKE, `expect` REJECTED_BY_CAKE, slot unchanged | Refused as expected (`refused_as_expected`) | ok |
| | REJECTED_BY_CAKE, no `expect`, slot unchanged | Cake refused the successor (`refused_by_cake`) | ink (`INK.bright`) |
| | REFUSED_BEFORE_TRANSACTION, `expect` the same | Refused before any transaction, as expected (`refused_before_as_expected`) | ok |
| | REFUSED_BEFORE_TRANSACTION, no `expect` | Cake refused the successor before any transaction (`refused_before`) | ink (`INK.bright`) |
| | any refusal whose `expect` is another state | Refused, not as expected (`refused_unexpected`) | bad |
| | any refusal whose slot read shows a change | Refused, but the slot changed (`refused_slot_changed`) | bad |
| | COMPLETED_NOT_OBSERVED, `expect` the same | Back to inert, as expected (`inert_as_expected`) | ok |
| | COMPLETED_NOT_OBSERVED, otherwise | Completed, capability not active (`completed_inactive`) | warn |
| | REQUEST_FAILED | Did not reach Cake (`not_delivered`) | warn |
| | SUBMITTED | Waiting for Cake's answer (`pending`) | wait |
| signature check | current session, no row unexpected, no error, none skipped | Signatures as expected (`sig_ok`) | ok |
| | current session, none unexpected, n skipped | As expected, n skipped (`sig_partial`) | warn |
| | any row not as expected or in error | Not as expected (`sig_bad`) | bad |
| | `runtime_session` differs from the snapshot's | Earlier session (`sig_earlier`) | mute, whole card |
| | no check served | No signature check in this session (`sig_none`) | mute |
| readiness | rule 4.2 | Ready / Not ready / Steps first / Running / Waiting for Cake / Held for you | ok / bad / warn / accent / wait / warn |

"Slot unchanged" is said only when both reads are `read: ok` and generation, capsule and instance are equal. The
mission and the run are always both stated, each with its own word: "Mission failed" and "Run 2 of 2 stopped by the
controller" (C3). The stage where a run ended carries the run's word with "here": "Stopped here by the controller",
"Failed here", "Refused here" (a new `PhaseStatus` value per word, replacing `failed` on that row, fixing
`stageTimeline.ts` line 330, which writes `failed` for every bad outcome of `BAD_OUTCOMES`, line 102).

### 5.2 One name for the mode

`data_mode` live: **Live**. Synthetic source or `data_mode` synthetic: **Rehearsal**. A replay open: **Recording**.
Absent: **Mode not recorded**. The mark appears once, in the top bar (`data-mode-mark="live|rehearsal|recording"`).
History and Diagnostics rows use the same four words. The strings "Live demonstration", "LIVE RUNTIME", "PHYSICAL" and
"SYNTHETIC" leave the demo screen and the drawer (C12).

### 5.3 Ids

Run ids, mission ids, candidate keys and observation ids are printed exactly as recorded (for example
`console-20260927T142146Z-m6ed2dd9`), in JetBrains Mono, never under `text-transform`, never shortened. Capsule and
digest values are shown as their first 12 hex characters with the full value in the `title` attribute. Runs are
named "run 2 of 2" beside the id, counted from `targets[].runs`.

### 5.4 Date and time

- Absolute UTC always: `HH:MM:SSZ` when the date is the mission's UTC start date (or today's, with no mission),
  `YYYY-MM-DD HH:MM:SSZ` otherwise. Amended 2026-09-28 (section 12): with a runtime session that dates itself the
  reference day is this display's UTC day, so a held mission from yesterday prints its dates; History prints every time
  with its date. The `title` attribute carries the full ISO string with milliseconds. Every
  element that prints a time carries `data-t="<ISO>"`.
- Fractional seconds of any length parse (the runtime writes both `.010Z` and `.572000Z`), truncated to milliseconds.
- Durations appear only between two recorded times ("2 min 54 s", from `started_wall_utc` to `finished_wall_utc`)
  and never against this display's clock.
- No "ago", no "N s old", no local time of day (today's `design/timeline.ts` uses `toLocaleTimeString`; that goes).

### 5.5 An expected refusal

With `expect` REJECTED_BY_CAKE served for the candidate and the slot unchanged, the card reads, headline in `C.ok`:

> Refused as expected, slot unchanged 1 -> 1
> mobile-demo-active-successor-abort-swap, 14:20:21Z, capsule 9600566de6c9 unchanged

With no expectation served (8771 today) the headline is "Cake refused the successor, slot unchanged 1 -> 1" in the
neutral bright ink (`INK.bright`), never in `C.mute`, `C.warn` or `C.bad` (C10), plus one quiet line: "This runtime serves
no expected outcome for this Plan." Every refusal card, expected or not, is the most prominent card of the region: a
16 px bold headline, a 4 px rule and a full outline in its verdict's colour, where the other cards have 14 px and a 3 px
rule. It stays in the list, oldest first, after the activations that follow it. An accepted swap reads "Activated, slot
1 -> 2" with the capsule change on its second line.
The record's `detail` ("the resident answered ABORTED; ... the answer does not say why") stays on the card, attributed
to the runtime that wrote it: "The runtime's detail:". It is the runtime's reading of the resident's answer, or for
REQUEST_FAILED the runtime's own code (`admin_socket_absent`), so it is never labelled as the resident's. The main card,
History, Evidence and the Cake tab print it under that one label.

### 5.6 A signature check from an earlier session

Amended 2026-09-28 (section 12): the main card carries only its verdict word, muted, and one line, "No check in this
runtime session. The last one, 2026-09-26 20:26:13Z (runtime session rt-20260926T184836Z-e34dcd31), is in History."
The lines below (its session line, its counts, the capsule it covered) are History's, in a section of their own.

When `checks[-1].runtime_session` differs from the snapshot's `runtime_session`, the whole card is `C.mute` and its
first line is "Earlier session: 2026-09-26 20:26:13Z (runtime session rt-20260926T184836Z-e34dcd31). It says
nothing about this session." The counts follow in grey. A capsule line is added only when a row's `capsule` equals a
live slot capsule: "It covered capsule 2f34a84c1971, which the live slot carries now: admitted with the deployment
key, refused with the throwaway key." The rows themselves are in Diagnostics.

### 5.7 Other copy rules

1. Holding is read, never asked: `stop_state.holding` true -> "Holding the block"; false -> "Gripper empty"; null ->
   "Not known whether the gripper holds the block". No question mark anywhere on the demo screen. "a cube may still be
   held" is said only when holding is null.
2. Step words (replacing `AFTER_END_WORDS`): `open_gripper_by_hand` -> "Open the gripper by hand and take the block
   out." (tag "by hand"); `return_base` -> "Move the base back to its start by hand." (tag "by hand");
   `return_arm` -> "Return arm: the arm goes to the saved low scan pose." with its button; `start` -> "Start a new
   mission with Start, at the top."; `retry` -> "Try again: the runtime parks the arm, then starts a fresh attempt."
   with its button; `move_block_then_retry` -> "Move the block by hand, then Try again." with its button; `stop` ->
   "Stop, at the top, ends the mission."; any other token verbatim.
3. Numbers as recorded: two decimals, with the record's unit, none when it names none ("gripper 68.12"); base
   velocity in m/s to three decimals and deg/s to two ("x 0.000 m/s, y 0.003 m/s, rotation 1.17 deg/s").
4. The Start dialog tells the truth about attestations: "Your confirmations are kept in this display's press list.
   The runtime does not receive them." While the step list holds by-hand steps, each is added as a checkbox.
5. Capability words follow the live slot: "the base advance uses mobile_approach, active at generation 2" when
   `capability.active` is true; "needs mobile_approach, which is not active" when false; "needs the base capability;
   the robot has not been read" when unread. This replaces `model/sceneMap.ts` line 34 and every Carry-row Cake text.
6. Region sentences name their record when it is not obvious: "from the mission record", "from run 2's records".

## 6. Temporal rules

### 6.1 Activations are append-only

The Cake region renders every `mission.activations[]` entry as its own card, oldest first, for the whole life of the
mission, whatever the mission state. A card is never replaced, merged, hidden or moved to Diagnostics. A new
activation appends one card; a SUBMITTED card updates its own verdict in place when the same `operation_uuid` is
answered (that is the same record completing, not a replacement). Today's "What changed in this mission"
(`ActivationReadback`, newest only) and the capability-wait-only `CapabilityCard` are deleted from the main screen;
the pending choice sits under the cards while the mission waits for Cake (T1, C2).

### 6.2 The outcome card persists until acknowledged

1. When a mission ends (FAILED, STOPPED, COMPLETE), the card shows that ending expanded until the operator presses
   Acknowledge. Nothing but a press collapses it: not a new poll, not a new run of another command, not a reload.
2. Acknowledge records `{mission_id, run_id, attempt, verdict id, acknowledged_at}` (this display's clock, UTC) in the
   store's press list (6.5) and in `localStorage` key `kiwi-console.acknowledged.v1` (array; every read and write in
   try/catch; storage that fails means the card comes back expanded after a reload, the safe side). It is never sent
   to the runtime (no such route; GET-only on 8771).
3. Acknowledged, the card collapses to one line in its own place: "Mission failed at 14:24:40Z. Acknowledged on this
   display at 14:31:05Z." with a Show control. It stays until a new mission id appears. A new attempt or a new mission
   ending shows expanded again.
4. The card's step list is exactly the runtime's list (3.2), in order, and nothing else: no disabled Try again with an
   explanation, no reason-code table (`nextActionsFor`), no budget (C5).
5. While a mission ending is unacknowledged, `document.title` is "<Mission word>: <mission id> | shinro".

### 6.3 The timeline

1. **Persistent and append-only.** The store keeps a timeline list for the page's life. Entries are keyed
   (`run_id:seq` for events, `activation:<operation_uuid>`, `mission:<id>:started|stop|ended`, `press:<id>`,
   `link:<n>`); a key is inserted once and never removed. A re-read that returns fewer records removes nothing.
2. **What enters from run events** (live SSE for the run in progress, `/api/history/<run>/events` for every run in
   `mission.targets[].runs`, read once when the run is finished): `run.started`, `state.transition`, `action.refused`,
   `stop.requested`, `stop.acknowledged`, `stop.outcome`, `run.finished`, `diagnostic.text` with `level` error or
   warning, and the joined cause records of rule 6.3.4. Everything else (frames, perception, timing spans, passing
   predicates, `base_zero` reads, `action.planned`/`started`/`completed`, `run.config`, `evidence.gap`, stdout lines)
   stays in Diagnostics. For run m6ed2dd9 that is 13 entries with the group's header and footer, not 284.
3. **Grouping and order.** Oldest at the top, newest at the bottom, stated in the region heading ("Oldest first.
   Times in UTC."). Ordered by `t_wall_utc` (then `seq`, then record order). Each run is one group headed "Run 2 of 2,
   console-20260927T142146Z-m6ed2dd9, 14:21:46Z to 14:24:40Z (2 min 54 s)" and closed by its run word (5.1). Mission
   lines, Cake lines and presses sit between groups at their own time. A state line reads the controller's own state
   name with its time ("GRASP_CLOSE 14:24:26Z"); a repeated state reads "ALIGN again".
4. **The stop's own cause (the `action_id` join).** For a run whose `run.finished` carries a `reason_code`: take the
   newest `action.refused` of that run with the same `reason_code`; its `action_id` selects the `predicate.evaluated`
   events with `value` false, the newest `measured.state`, and the `action.planned` label, all with the same
   `action_id` and a lower `seq`. The mission card and the timeline both show them: "Refused CLEARANCE lift
   (arm_pose): base velocity not confirmed zero before an arm segment: stopping all motion", "base_zero: 0 reads
   against 3 reads, 14:24:39Z", "Base velocity in that reading: x 0.000 m/s, y 0.003 m/s, rotation 1.17 deg/s". No
   predicate of another action is ever shown as the cause (C4). When no refusal matches, `failure.measurements` are
   shown as recorded; when those are empty: "The record carries no measurement for this stop."

### 6.4 Auto-scroll that pauses

A pure model (`model/follow.ts`) with three events: `appended(n)`, `scrolled(atBottom)` (within 24 px of the end) and
`jump`. Following: new entries scroll into view. The operator scrolls up: following pauses, and a pill "3 new entries
below" (`data-control="timeline-jump"`, `data-unseen="3"`) appears at the bottom edge; pressing it or scrolling back
to the end resumes. The count only changes when entries arrive. The list is `role="log"`, `aria-live="polite"`.

### 6.5 Press receipts are a list

The store gains `presses`: one entry per press on this display (Start, Stop, Try again, Return arm, Scan scene,
Observe, an activation candidate, the slot check, the signature check, the robot confirmation, Acknowledge):
`{id, control, pressed_at (UTC, this display), answer: pending|taken|refused|unknown, text, answered_at, mission_id,
run_id}`. Entries are appended, never overwritten; a pending entry receives its own answer. The text is today's
receipt function's (`receipts.ts`, `activationReceiptWords`, the check receipts). The list renders inside the timeline
("You pressed Stop, 14:30:02Z (this display): Stop refused: there is no run to stop."). A press whose receipt names a
runtime record (a mission id, an `operation_uuid`, a `command_id`) is shown on that record's line instead of twice.
The fixed floating receipt (`DemoScreen.tsx` lines 526-551) is deleted (T2).

### 6.6 Orders and defaults in Diagnostics

History lists runs newest first, and says so in its heading. The Logs run picker lists runs newest first and opens on
the mission's last run, else the newest finished run (T5; today `runChoices` in `panels/LogConsole.tsx` keeps the
runtime's order, which 8771 serves oldest first, and the picker opens on `choices[0]`, the oldest).

### 6.7 The mission's finish time

The card and the timeline show "Ended 14:24:40Z" for an ended mission, taken from `finished_wall_utc` of the last run
in `targets[].runs` (GET /api/history), labelled in its `title`: "the time the mission's last run finished; the
mission record carries no end time". A mission stopped while no run was active uses `stop_requested_wall_utc`,
labelled the same way.

### 6.8 Nothing ticks while nothing happens

No text on the demo screen depends on this display's clock except the stale and disconnected markers, which print a
fixed time and flip once. Today's age counters go: the header `StaleTag` age, the wrist "recorded 7 min 54 s ago",
`ageWords` anywhere on the demo screen, the mission clock. A render 20 s later with no new record is byte-identical
(test rule INV-9).

## 7. Diagnostics: what moves and what goes

### 7.1 Moves (audit section 3, plus what the redesign displaces)

| Moves | To tab |
|---|---|
| pid, spawns, restarts, config digests, resident identities, flight counts | Connections |
| The holder slot `mobile_approach.holder` (capsule, generation, health, pid), with no "by design" sentence (C9) | Cake (new tab) |
| The controller bundle card and all module cards | Cake |
| The capability slot check: its Apply buttons, its records with their session and date, `ActivationEvidence` | Cake |
| The signature table rows (the Run button stays on the main card only) | Cake |
| Plan digests, payload digests, transaction uuids and handles, state paths | Cake (activation details, same verdict words) |
| px predicate values, every predicate, decision frames, perception notes, the retry budget | Evidence |
| Runs outside this mission | History |
| The snapshot's own statements: `components[]` (robot-host, wrist-camera), `link`, `capabilities[id=cake_update]`, the cake ladder, each under a heading naming its source (API-only item) | Connections |
| The last controller measurement joints and the continuous telemetry | Connections |

Evidence and the mission card read the same functions (`stopCause`, the verdict table, the step list), so Evidence
can no longer say "no measured value", "no next step" or "0 recorded" while the card says otherwise (C4, C5).
Evidence counts predicates from the run's own events ("39 predicates recorded, 1 tied to the stop").

### 7.2 Deleted outright

- The full-width failure banner (`FailureBanner.tsx`) and its second Stop; its facts move to the mission card.
- The "Before a new mission, the runtime lists these steps" box (merged into the card's step list).
- The main-screen "Last check, before and after" and the capability check history on the main screen (C1).
- "What changed in this mission" (`ActivationReadback` on the main screen).
- The Carry-row Cake marker (`cakeMarker` text) and every Cake text on a stage row (C8).
- The stage cards' numbers, frames and rule lines on the main screen (they are Evidence's).
- The sentence "carries the same capsule by design" (`cakeModules.ts` line 68).
- The floating receipt toast; the header age tags; "STALE Saved observation, recorded ... ago".
- The titles "Live demonstration", marks "LIVE RUNTIME", "PHYSICAL", "SYNTHETIC" (replaced by 5.2).
- The `KICKER` uppercase style on the demo screen.
- The disabled Try again with its paragraph, and the reason-code next-step table on the main screen and in Evidence.

## 8. Traceability

Test files named here are new unless marked "ext". Model tests are pure; render tests use `renderToStaticMarkup`
as the existing suites do (the package has no DOM environment). "Scenario" means a catalogue entry played by
`demoScenarioPlay.ts` in `demoScenarios.test.ts` (ext).

| Item | Resolved by | Proven by | Scenarios |
|---|---|---|---|
| C1 undated earlier-session check on the main Cake | 7.1, 7.2; 3.3 | `design/cakeStory.test.ts`: the Cake region markup holds no capability-check record; `demoScenarios.test.ts` (ext): `undo-to-inert` main `cake` text has no "Last check" and the live slot footer names the check that set it | undo-to-inert, swap-then-stopped-holding |
| C2 refusal pushed to Evidence | 6.1 | `design/cakeStory.test.ts`: two activations render two cards in record order; ext: `activations` of `end` equals the mission's `operation_uuid` list | one-block, abort-swap-repeated, swap-then-stopped-holding |
| C3 outcome words disagree | 5.1, 4.4 | `model/verdict.test.ts`: every row of 5.1; ext: new scenario `end.outcome.mission` = `failed`, `end.outcome.run` = `stopped_by_controller`, `end.stages.pick` = `stopped_by_controller`, and the text holds no "FAILED" in capitals | swap-then-stopped-holding, hold-live-default, far-block-success |
| C4 unrelated px and frame as the cause | 6.3.4, 3.2 | `model/stopCause.test.ts`: the join picks the same-`action_id` false predicate and measured state and ignores an earlier false predicate with `action_id` null; ext: new scenario outcome text contains "base_zero" and "1.17 deg/s", no "straddle_far_edge_row", no "px", and no `data-failure-frame`; Evidence tab text holds the same line | swap-then-stopped-holding, near-block-grasp-miss, align-no-proposal |
| C5 next steps disagree, Try again vs budget | 6.2.4, 7.1 | `design/outcomeCard.test.ts`: the step list equals `next_actions` in order; no `data-control="retry"` unless the hold lists it; ext: new scenario `offered` has no `retry`; `hold-live-default` hold has no retry control | swap-then-stopped-holding, hold-live-default, hold-then-retry-placed |
| C6 holding asked, not read | 5.7.1, 3.2 | `design/outcomeCard.test.ts`: holding true renders `data-holding="true"` "Holding the block"; null renders the not-known line; the demo markup holds no "?" | swap-then-stopped-holding, align-no-proposal |
| C7 Not ready beside a primary Start; stale wrist on full width | 4.2, 4.3, 3.7 | `model/readiness.test.ts`: verdict Not ready iff `startOff` non-null; wrist and arm never change it; `design/topBar.test.ts`: `data-readiness` and Start `aria-disabled` agree; ext INV-3, INV-4 | preview-not-configured, bring-up-not-confirmed, host-not-supervised, swap-then-stopped-holding |
| C8 capability words against the live slot; Cake on Carry | 5.7.5, 7.2 | `design/sceneMapWords.test.ts`: the three capability sentences by `capability.active`; ext: `timelineCake` is empty in every scenario | scene-scan, one-block, swap-then-stopped-holding |
| C9 holder "same capsule by design" | 7.1, 7.2 | `design/diagnosticsMoves.test.ts`: the demo screen and the Cake tab markup hold no "by design"; the holder card is only in the Cake tab | swap-then-stopped-holding |
| C10 expected refusal reads as a warning | 5.5, 3.8 | `model/activationCard.test.ts`: REJECTED_BY_CAKE with `expect` -> `refused_as_expected` ok, without -> `refused_by_cake` mute, never warn; ext: one-block first card `refused_as_expected`, new scenario first card `refused_by_cake` with "1 -> 1" | one-block, abort-swap-repeated, tampered-refused-before-transaction, swap-then-stopped-holding |
| C11 signature table shown as current | 5.6 | `design/cakeStory.test.ts`: a check whose `runtime_session` differs renders `data-signature-stale="true"`, "Earlier session" and its date; the same session renders the verdict | swap-then-stopped-holding (earlier session served) |
| C12 three names for one mode; run-id casing | 5.2, 5.3 | `design/topBar.test.ts`: one `data-mode-mark`; ext INV-6 (forbidden words), INV-7 (ids verbatim, no `text-transform` in the markup) | all scenarios |
| C13 two Stops; activation in five places | 4.3, 6.1, 7.2 | ext INV-5: exactly one `data-control="stop"`; each `operation_uuid` appears in exactly one `data-activation-card` on the main screen | all scenarios |
| API-only: `cake_update` not built, link unknown, robot-host never contacted | 3.1, 7.1 | `design/diagnosticsMoves.test.ts`: the main markup holds none of `not_built`, "never contacts the robot host", the snapshot `link` state; the Connections tab shows them under "What the runtime's snapshot says about itself" | swap-then-stopped-holding |
| T1 refusal replaced after 84 s | 6.1 | ext INV-1: across `all` renders, each render's activation card ids are a prefix of the next's | one-block, abort-swap-repeated, tampered-refused-before-transaction, swap-then-stopped-holding |
| T2 receipts overwrite each other | 6.5 | `store/presses.test.ts`: three presses give three entries in order; a pending entry gets its answer in place; ext: `receipts.length` presses produce as many `data-timeline-entry="press"` lines | bring-up-not-confirmed (two Start presses), hold-then-retry-placed |
| T3 newest verdict only; no timestamped state sequence | 6.3, 3.5 | `model/missionTimeline.test.ts`: the entries of a run are its state transitions with their `data-t`, grouped, ordered; ext: new scenario run 2 group lists SEARCH ... GRASP_CLOSE with a time each; INV-2 | swap-then-stopped-holding, far-block-success, hold-then-retry-placed |
| T4 ages tick | 5.4, 6.8 | `model/utcTime.test.ts`; ext INV-9: two renders of `end` 20 s apart are identical; INV-8: no "ago" and every visible time ends in Z | all scenarios |
| T5 oldest first, Logs default, no finish time | 6.3.3, 6.6, 6.7 | `design/diagnosticsMoves.test.ts`: History newest first; the Logs picker's selected run is the mission's last run; `design/outcomeCard.test.ts`: "Ended" equals the last run's `finished_wall_utc` | hold-then-retry-placed (three runs), swap-then-stopped-holding |
| Top 1 activations append-only | 6.1 | as T1 and C2 | as T1 |
| Top 2 remove the earlier-session check | 7.2 | as C1 | as C1 |
| Top 3 persistent timestamped timeline | 6.3 | as T3; `design/timelineRegion.test.ts`: `data-follow` and the jump pill from `model/follow.test.ts` states | as T3 |
| Top 4 one vocabulary, mission verdict on main | 5.1 | as C3 | as C3 |
| Top 5 the stop's own measurement | 6.3.4 | as C4 | as C4 |
| Top 6 card until acknowledged, holding from record | 6.2, 5.7.1 | `design/outcomeCard.test.ts`: expanded before Acknowledge, collapsed with the time after, expanded again for a new attempt; a throwing storage keeps it expanded; ext INV-10: once an ended card appears it is in every later render | swap-then-stopped-holding, hold-live-default (Stop at hold), far-block-success |
| Top 7 demote Start while held or not ready; one Stop | 4.2, 4.3 | as C7 and C13; ext: new scenario `end.primary` is empty and Start is on | swap-then-stopped-holding, bring-up-not-confirmed |
| Top 8 expected outcomes make the refusal green | 5.5 | as C10; plus the runtime config `cake.candidate_roles` for 8771 (section 10) | as C10 |
| Top 9 capability text tied to the live slot; Cake out of Carry | 5.7.5, 7.2 | as C8 | as C8 |
| Top 10 signature check dated, flagged stale | 5.6 | as C11 | as C11 |

### 8.1 Cross-scenario invariants (ext, `demoScenarioPlay.ts` + `demoScenarios.test.ts`)

`ScreenRead` gains: `outcome` (`data-outcome-card` text, `data-verdict` of mission and run, `acknowledged`),
`activations` (ordered `data-activation-card` ids with `data-verdict`), `timeline` (ordered `data-timeline-entry`
keys), `primary` (ids with `data-primary="true"`), `readiness` (`data-readiness`), `signature`
(`data-signature-stale`, `data-verdict`), `stopControls` (count of `data-control="stop"`). The banner fields are
re-pointed to `outcome`. Every scenario asserts, over `all` and `extra`:

- INV-1 activation cards are append-only (prefix rule).
- INV-2 timeline keys are append-only (subset rule).
- INV-3 `primary.length <= 1`.
- INV-4 while no mission is carried and no run is active, `readiness === 'not_ready'` if and only if Start is off,
  and `primary` holds `start` if and only if `readiness === 'ready'`.
- INV-5 exactly one `data-control="stop"`.
- INV-6 the text holds none of: "Live demonstration", "LIVE RUNTIME", "PHYSICAL", "SYNTHETIC", "by design".
- INV-7 every run id of the mission appears verbatim and never upper-cased; the markup holds no `text-transform`.
- INV-8 no `/\b\d+ ?(s|min|h) ago\b/`; every `data-t` ends in `Z`.
- INV-9 `end` rendered again after `vi.advanceTimersByTimeAsync(20000)` is byte-identical.
- INV-10 once a render shows an ended mission card, every later render shows it (until an Acknowledge press).

### 8.2 The scenario to add: `swap-then-stopped-holding`

Today's record, played offline (synthetic ids, data mode synthetic, performs_motion false, every non-[STATE] line
prefixed "synthetic script", as the catalogue's rules require):

1. Run 1 `buildMeasuringRun`: SEARCH, TARGET_SELECTED BASE_ADVANCE, the error line "SEARCH: this target's cube
   measures BASE_ADVANCE, which needs the mobile_approach capability, and this run carries none; nothing was picked
   up", `run.finished` failed, exit 5, `mission_capability_required`, phase TARGET_SELECTED. The mission waits.
2. Candidates served WITHOUT role, label or expect (as 8771 serves them): abort-swap then swapped, pressed in that
   order. Abort-swap REJECTED_BY_CAKE, slot 1 -> 1; swapped OBSERVED, slot 1 -> 2, capsule changes.
3. Run 2, a new builder `buildStoppedHoldingRun` with run m6ed2dd9's shapes: [STATE] lines SEARCH, TARGET_SELECTED,
   PREGRASP, ALIGN, ALIGN, APPROACH, STRADDLING, GRASP_CLOSE; `straddling` true; `straddle_far_edge_row` false 333
   against 420 px with `action_id` null and a frame; `closed_on_something` true 67.91 against 67.4; the VERIFICATION
   lift; then under one `action_id`: `action.planned` "CLEARANCE lift", `predicate.evaluated` `base_zero` false 0
   against 3 reads, `measured.state` base theta 1.171875 deg/s and gripper 68.116, `action.refused`
   `base_not_zero_before_segment` "base velocity not confirmed zero before an arm segment: stopping all motion";
   `run.finished` stopped, exit 4, phase GRASP_CLOSE, `failure.measurements` [] and `failure.observation_ids` [].
4. A signature check served from an earlier runtime session (rows as today's shape, invented capsules), so C11 is
   exercised.

Synthetic source changes it needs (console-web only):
- `api/synthetic.ts` `scenarioRunFinished`: a stopped run whose mission stop was NOT requested is written as the
  runtime writes it (`demo_runtime/mission_execution.py` lines 799-801): an attempt of class `stopped` with the run's
  `reason_code` and `phase`, `next_actions` from `nextActions('stopped', stop, false)` (open_gripper_by_hand,
  return_arm, start), and the mission FAILED with `detail` `run_outcome:stopped:<reason>`. A stop the operator asked
  for keeps today's STOPPED path.
- `ScenarioCandidate` roles become optional, so a scenario can serve key and digest only.
- A scenario option `signature?: 'earlier-session' | 'this-session'` served by `signatureCheck()`.
- `ACTIVATIONS['swap-then-stopped-holding']` in `demoScenarioPlay.ts`: the abort-swap key, then the swapped key.

Its claims (ext): `end.outcome` mission `failed`, run `stopped_by_controller`, stage Pick, state GRASP_CLOSE;
"Holding the block", "gripper 68.12"; steps open_gripper_by_hand, return_arm, start in order; `primary` empty; Start
on; readiness `steps_first`; two Cake cards `refused_by_cake` ("1 -> 1") then `activated` ("1 -> 2"); the live slot
footer names the swap's time; the signature card stale; run 2 group with eight state lines and the joined cause;
"Ended" equal to run 2's finish.

### 8.3 Other states outside the catalogue

Stale and disconnected are rendered with a source double whose reads fail after the first, as
`api/reconnect.test.ts` does, in `design/staleRegions.test.ts`: the markers print a fixed UTC time, content is
kept, Start is off, Stop stays on, and a render 20 s later is identical.

## 9. Non-goals

- No change to `packages/demo-runtime`, no new route, no POST from the redesign, no acknowledgement sent anywhere.
- No change to what any control admits; only emphasis, placement and words change.
- The classic screen (`?screen=classic`) is not redesigned; it changes only where a shared model function does.
- No new palette, typeface or icon set; no animation.
- No cross-mission history: the one mission the runtime serves plus the finished-run list, as today.
- No inferred causes: the console joins records by `action_id` and says nothing a record does not.
- No change to the camera pipeline or the scene map's geometry.
- No redesign of Diagnostics beyond the moves, the new Cake tab and the two orderings of 6.6.

## 10. Items the console cannot fully resolve without a runtime change

| Item | What the console does | What would close it |
|---|---|---|
| C10 on 8771: the expected refusal reads green | reads green in every scenario whose candidates carry `expect`; on 8771 reads "Cake refused the successor" as a prominent card in the neutral bright ink, never grey, amber or green | the runtime config key `cake.candidate_roles` naming the abort-swap `refused-demo` and the swap `activate` (configuration, not code: `demo_runtime/config.py` line 577) |
| API-only item | the main screen never reads the snapshot's `components`, `link` or `cake_update`; Diagnostics shows them under their source | the runtime's snapshot statements for a robot-resident deployment are its own text; changing them is a runtime change |
| Mission end time | derived from the last run's finish, labelled | a mission `ended_wall_utc` field |
| The stop's measurement in the failure record | joined by `action_id`, record-backed | the controller filling `failure.measurements` for base_not_zero_before_segment |
| `evidence.capability` null while the capability is active (C8) | not displayed | the runtime recording the capability the run carried |
| Presses from another display that the runtime refused | not shown on this display | a runtime press journal |
| The runtime session a mission, run or picture belongs to (section 12) | derived from the session id's start and the record's own times, said as derived | a `runtime_session` field on the mission record and the finished-run rows |

## 11. Deviations found while building

Where the build follows the code's reality rather than the text above, it is recorded here.

1. Presses (6.5): every press keeps its own timeline line, keyed `press:<id>`, including one whose receipt names a
   record, so the press count equals the line count (the T2 claim) and INV-2 holds. The record's own line does not
   repeat the receipt; it is not moved onto that line.
2. Receipts (6.5): the activation, slot check, signature check and robot confirmation receipts are press-shaped
   rewordings of the existing receipt functions, which spoke of "the last request" and "the record below".
3. Timeline (6.3.2): `run.started` is not an entry of its own; the run's heading stands for it, which gives the 13
   entries the spec counts for m6ed2dd9.
4. Synthetic source: it now stamps each record's `t_wall_utc` as it plays it and fills each finished run's start and
   finish in the history, which every time on the screen needs; a "bench" robot (confirmed, the capability slot
   following the scenario's slot, the holder slot listed) and a `slot_generation` option were added, used by
   `swap-then-stopped-holding` and by `undo-to-inert`, whose live slot footer cannot name the check without a robot.
5. Store: the finished-run list is read again while a finished mission run is missing from it, because a list read as
   the run ended can predate it, and the mission's end time comes only from that list.
6. Readiness (4.2, 4.4): Disconnected is decided before rule 1, as the Disconnected row of 4.4 says Not ready even
   while the last mission read was carried.
7. Holding null (5.7.1) reads "Not known whether the gripper holds the block; a cube may still be held".
8. The wrist line (3.7) prefers the runtime's reason code to a rehearsal cover's title; the state word is written in
   sentence case.
9. No question mark (5.7.1): the Observe control's holding prompt, shared with the classic screen, now states the
   choice ("Off until you state whether the gripper is holding the cube.").
10. Shared components the classic screen also draws changed where the demo screen needed it: the motion hold's label
    is no longer upper-cased, the design Button marks its default variant `data-primary`, and the Actions panel accepts
    `primary={null}`.
11. The signature Run button is drawn only when the runtime serves a signature check; a runtime with none draws the
    "No signature check in this session" card without a disabled button.
12. Stages (3.5): park runs stay among the runs the stages are folded from, as today.
13. Evidence (7.1, 7.2): with the reason-code step table deleted, the Evidence failure card lists the runtime's own
    steps in the mission card's words.
14. The Diagnostics Cake tab is the fifth tab, after Connections.
15. While a mission is carried, the readiness sentence is the existing `missionStartOff` sentence.
16. INV-4 treats a run as active when the snapshot reports one or this tab's fold says one is (readiness Running).
17. Try again beside a mission read that failed is off through the existing `retryRefusal` guard; the demo screen
    test pins it rather than a new rule.
18. Follow on resize (6.4): while the timeline follows, a width or layout change keeps the list at its end (the follow
    model's `resized` event, fed by a ResizeObserver on the list and its entries); a paused list stays where it is and
    its unseen count is unchanged.
19. The kept wrist picture (6.8): through a stream gap the wrist thumbnail draws the last picture this page decoded,
    with a fixed time, "The last picture this page decoded, at HH:MM:SSZ (this display's clock); not live.", never an
    age. While it is drawn, the line under the word says only that the stream is not showing, with the runtime's
    reason. The classic screen keeps its own STALE words with an age.
20. The readiness detail (4.2) says each checklist item's hint beside it (acquire the arm, the host deploy check,
    confirm the robot session, resolve the uncertain command).
21. The live slot footer (3.3) credits only an activation, or a slot check of the current resident session, that
    changed the slot to the live generation: never a refusal, and only when both slot reads answered and differ.
22. Connections (5.2) says the runtime data mode as the mode word (Live, Rehearsal, Mode not recorded); the raw value
    is only in the title.
23. The outcome card's limit line and run line carry `data-failure-limit` and `data-run-stage` for the scenario tests;
    nothing drawn changes.
24. 5.1: until a run's events are read, no run word is given: the card gives none, and a History row names the
    finished-run list's outcome token as the list's.
25. 6.6: "newest first" for a target's runs is the mission's run list reversed, not a sort by finish time.
26. 5.4: the classic screen's event strip prints UTC too (a shared function).
27. Diagnostics: a resident or capability section the runtime marks stale shows its own observed time; the link shows
    this page's last read, as the top bar does; the Cake check's "reading too old" reason is dated by the reading's
    observed time; the engineering reference says "last line reached this tab at", "receive age" and "N record(s)
    before the newest".
28. Evidence names the newest frame a predicate or action consumed, and says it is tied to the stop only when the
    stop's `failure.observation_ids` contain it; its watermark prints the 5.2 mode word.
29. 5.5: the no-expectation note is said only when the candidate list was read and serves the candidate without
    `expect`.
30. 5.7: the `stop_state` words are: `base_ahead` false "The base did not advance, or was measured back", true "The base
    advanced and was not measured back"; `moved` true "The robot moved (an action started)". The no-mission line is
    said only for `no_mission_recorded`; otherwise "not read yet" or "not read: <code>".
31. The lease guard is shared: the classic screen also loses the host's reply age.
32. When motion is held and nothing can be confirmed from this page, Start's off-reason and the readiness sentence are
    "Robot motion is held. Nothing to confirm: <reason>", ahead of the capability's own reason (also when the robot is
    not answering).
33. A stage the run entered with no verifying predicate reads "Entered" in the mute tone; the classic screen's chip
    keeps its colour.
34. 4.4 Complete: the card states the placement from the same function as the Scene (`placementLine`), read from the
    drawn run's events.
35. 5.5 and Top 8: without `cake.candidate_roles` served (as on 8771), a refusal reads the neutral "Refused by Cake,
    slot unchanged 1 -> 1" in the mute tone on the main screen, the timeline, History, Evidence and the Cake tab; no
    expectation is inferred from a candidate's name.
36. 5.5 and Top 8, the classic screen (section 9): its activation readback shares `activationLine`, which now takes a
    refusal's headline and tone from the same card model, so a refusal is never red or amber there either without a
    served expectation that says so.
37. 5.7.1 (round 3): Observe reads holding from the mission's newest attempt, `stop_state.holding`, which the runtime
    computes over the run's own events (`stop_state_from_events`); this tab's fold is read instead only for a run that
    began after that attempt was written. A settled reading is stated beside the control ("Sent as: Holding the block,
    from the mission record, attempt 1 of run <id>, whose stop state records holding true"). The operator is asked only
    when no record settles it (holding null), and the question names the record that left it unknown; it never says
    that nothing in the record says. A gripper opened by hand after the run is in no record, so the command carries the
    runtime's own reading until a new run records otherwise.
38. 3.3 and 5.7.5 (round 3): the live slot footer and the Scene state the time of the slot record they use. A robot
    reading is dated by the first reading on this page that showed the slot as it reads now ("unchanged in every robot
    reading since HH:MM:SSZ"), so nothing ticks. An activation that changed the slot after the robot's reading was
    taken is what the slot carries, "as the activation of <key> read it back at HH:MM:SSZ", with "Set by that
    activation. No robot reading taken after it has reached this page yet." The store reads the robot again after an
    answered activation, as the slot check does.
39. 3.3 (round 3): a failed read is said as not read: "The mission has not been read: <code>." in place of the cards,
    "The mission has not been read, so this page cannot say what set it." in the footer, and "Signature check: not
    read: <code>" (verdict id `sig_unread`, mute), a word the 5.1 table does not list.
40. 5.1 in the Cake tab (round 3): a slot check refused by Cake is headed and coloured by the same verdict as an
    activation card, from its candidate's served `expect`; the other check states keep the check's own headlines.
41. Stages (round 3): until the run's own records are read, every row says "Not seen from here"; a row's time reads
    "entered HH:MM:SSZ", with its meaning in the title.
42. Connections (round 3; replaces the arrival times of 27): an arriving feed is said as a rate and the fixed time its
    run of arrivals began, never the newest sample's time: "answering since HH:MM:SSZ, read every 5 s", "Continuous
    telemetry is arriving at N Hz, as the runtime measures it, since HH:MM:SSZ", "stream lines arriving since
    HH:MM:SSZ", and in the engineering reference "lines reaching this tab: arriving since HH:MM:SSZ". The run restarts
    after a silence or a failed read; a silent feed keeps the fixed time of its last line. The reference's per-sample
    values (the tap's receive age and joints, a camera's measured rate) still change when the runtime's samples do.
43. 5.1 and 5.4 in Diagnostics (round 3; replaces the History row token of 24): History's last-run and previous-run
    lines and Evidence's reason headline and badge give the run's 5.1 word, from its own events or a fold that saw it,
    and none for a stop whose request was not read; the run.finished token is only in a title; run rows say "took
    22 s". A rehearsal's run reads "The rehearsal's run ended: Placed; nothing was physically moved by it".
44. Connections' engineering reference (round 3): its run panels (decisions, run counts, spans, records, diagnostics)
    read the run on the screen as Evidence does, the read-back fold on a page opened after the run; the session panels
    stay on the live view. Its prose is in sentence case.
45. Evidence (round 3): the verification badge reads "N predicates, newest verdict of each"; for a code this console
    has no sentence for, the note names what is listed: the values the stop's record carried, if any, and the run's
    false predicates.
46. The Cake tab's explanatory copy (round 3) states how the resident and an activation work, never this runtime's
    state: the capability card no longer says the mission waits on it, and the policy is the record's.
47. The one-block rehearsal (round 3) keeps its sweep; its runs now also emit the `cube-in-pad-frame` result and a
    `[STATE] PLACED` line from the positions their frames already record, so its Complete card states the placement.
48. Evidence (round 4): the heading "evidence the controller named" lists only the stop's own
    `failure.observation_ids`; with none it says "The stop named no observation". The observations the false predicates
    read are listed apart, under "observations the false predicates read, not named by the stop", and an id the stop
    named is not repeated there.
49. Evidence (round 4): only a retained picture is pinned as the decision frame. A consumed observation whose capture
    record says media `scalar_set`, or a frame the runtime did not retain, is not pinned; the newest picture a decision
    consumed stays pinned. An id pinned before its capture record arrived that turns out to be such a reading is said,
    "This decision read <id>, a base reading of scalar values with no picture", with no picture and no overlay.
50. Connections' engineering reference (round 4): the Cake panel counts "slots the resident reported" and names the
    active ones from the resident's own listing (GET /api/robot, the reading the main screen's live slot comes from),
    or says "not read: <code>"; the runtime snapshot's Cake view and components list are said as the snapshot's ("slots
    in the runtime snapshot's Cake view", "The runtime's snapshot lists no component of this execution path as an
    active Cake module").
51. 5.1, 5.5 and Top 8 (round 4; replaces the mute tone of 35): a Cake refusal with no expectation served reads "Cake
    refused the successor, slot unchanged N -> N" (or "... before any transaction") in the neutral bright ink
    (`INK.bright`, tone `ink`), on the main screen, the timeline, History, Evidence, the Cake tab and the classic
    readback. On the main screen every refusal card, whatever expectation is served, is drawn as the region's most
    prominent card (16 px bold headline, 4 px rule, full outline) in its verdict's colour: green only for "Refused as
    expected", red for a refusal not as expected or with the slot changed.
52. 3.3 and 5.7.1 (round 4; extends 39): with the mission read failed before any mission was read, the timeline says
    "The mission has not been read: <code>." in place of "Nothing recorded on this display yet.", and Observe's holding
    question says "The mission record, which settles whether the gripper is holding the cube, has not been read
    (<code>)" in place of "No record read here says ...". A runtime that answered `no_mission_recorded` keeps the old
    words. The readiness detail (4.2) likewise says "The robot status has not been read: <code>." for the host and
    motion items when the robot read failed, never "The runtime reports no robot resident".
53. 5.2 (round 4): a runtime that has not answered the handshake is neither a contract mismatch nor a rehearsal. The
    mode starts unstated, `data-mode-mark="not_recorded"` and "Mode not recorded", in the top bar, the classic strip,
    the watermarks and the camera reading, until the runtime states one. Only `contract_incompatible`, or a contract
    version other than 1, makes the tab read-only; any other unanswered handshake is asked again before the next
    snapshot read, and the page recovers without a reload once the runtime answers. A runtime that comes back under
    another session reads as replaced. A finished-run list whose read failed is read again once a snapshot answers.
54. 4.3, the Stop row (round 4, safety): Stop is on from the moment this tab sends a command until a snapshot read sent
    after the command's answer has answered, while the record shows a run (the snapshot's active run, the fold's run in
    progress, or a streamed `run.started` with no end of that run or of a later one), and while the mission record
    shows the mission carried (RUNNING or WAITING_FOR_CAPABILITY), whatever the list's `no_active_run` says. The
    runtime publishes a run before it answers the command that started it, so the answer alone does not settle Stop. A
    refused command settles at once; a snapshot read that fails, or that was sent before the answer, does not settle
    it. A mission start or a retry that was not refused settles the same way. Meanwhile Start, Scan scene, Observe and
    Return arm are off, with the reason that a command from this tab is not settled or that the record shows a run in
    progress.
55. 4.3, the Stale and Disconnected columns (round 4): a snapshot older than 15 s counts as the runtime not answering.
    Every command control but Stop is off, drawn and again at the click, while the snapshot read failed ("the runtime
    is not answering", with no time: deviation 62) or the last snapshot is older than 15 s ("the runtime has not answered since its
    last snapshot reached this page at HH:MM:SSZ"). That covers Start, Scan scene, Observe, Return arm, Try again, the
    Cake candidates and Run signature check, and three controls the table does not list: Confirm this robot, the
    uncertain-command statement and the Diagnostics Cake tab's prepared Plans. Beside it: Return arm, Try again and the
    Cake candidates are off while the mission record on this page is not current (its last read failed, or it was read
    more than 15 s ago); Run signature check follows the Cake region's stale marker, off while its own read failed or
    while the Cake region's mission or robot read is not current; and every command that moves the robot (all but
    Observe) is off beside a motion hold. None of these ever turns Stop off. The classic screen's own Start (section 9)
    keeps its old guards; the Actions panel, the uncertain-command panel and the retry guard it shares carry these.
56. The motion-hold banner (round 4) reads "Start, Scan scene, Return arm and Try again stay off until it is lifted.
    Observe does not move the robot and is not held by this."
57. 3.2, 3.5 and 6.3 (round 4): a reload or a second display opened mid-run reads the running run's earlier records
    once (GET /api/history/<run>/events, which the runtime serves for a run in progress from its log) and merges them
    into the live fold by `seq`, so the card, the Stages and the timeline draw that run as the tab that watched it does.
    A run's fold is cached only once its records reach the run's end.
58. 3.2 and 3.5 (round 4): the mission card and the Stages name one run. After the snapshot's active run, the selected
    run is the one the fold's own records say is going on (its `run.started` folded with no end), which is also the
    rule Stop reads (54); the card names the newest run the stages are drawn for whenever the mission lists it.
59. 3.3 and 5.7.5 (round 4; replaces the robot-reading time of 38): the robot's slot reading carries no time of its
    own, so a slot taken from it is said "as the robot's reading says", with no time this page made up; the record
    time beside it is the activation that set the slot. An activation that changed the slot after the robot's reading
    is still said "as the activation of <key> read it back at HH:MM:SSZ", as in 38. A reload draws the same Cake footer
    and Scene.
60. 3.6 (round 4; replaces "over the mission runs' events, as today"): each drawn run contributes only its own records,
    the events whose `run_id` is that run's, which is what the runtime serves for it. The newest Scan scene run the
    runtime lists (or one this tab watched) is drawn first when it precedes the drawn runs with no other cycle run
    between, drawn alone when it is newer than all of them, and otherwise not drawn. The Scene says which runs it drew
    and when each started: "Drawn from the records of the Scan scene run <id>, started HH:MM:SSZ, and run <id>
    (Start), started HH:MM:SSZ." (`data-scene-records`). The store reads that scan's records once the run list names it,
    and reads the list again on the poll while a scan whose end this tab watched is missing from it. A Scan scene run
    after a mission ended is therefore drawn on the Scene, alone and named.
61. 6.2.2 and 6.2.3 (round 4): the acknowledgement key includes the mission's `started_wall_utc`
    (mission, start, attempt, run, state). An acknowledgement older than the mission's start, or whose time does not
    parse, never collapses the card, so a new mission that reuses an id is born expanded. An ending acknowledged under
    the older key shows expanded once more, the safe side. The runtime session is not in the key: missions are folded
    from a durable file across runtime restarts, so a restart does not make an ending new.
62. 4.3 and 4.4, the Disconnected column (refix round 2, verify-r1 must_fix 4; corrects 55): "not answering" carries
    no time. The readiness sentence is "The runtime is not answering." and every control's reason "the runtime is not
    answering": the time of this page's first failed read is this page's, not the runtime's, and printing it made two
    loads over the same answers draw two different screens. A display that did reach the runtime keeps its own
    observation on the timeline in its own voice ("This display lost the runtime at HH:MM:SSZ", "This display reached
    the runtime again at HH:MM:SSZ") and its "Not read since" marker. A display that never had an answer records no
    link change: the timeline header says "The runtime has not answered this display.", and the strip says "Nothing from
    the runtime has reached this display yet." in place of "the last state that did arrive". The stale case keeps its
    time ("the runtime has not answered since its last snapshot reached this page at HH:MM:SSZ"), which is when a read
    did arrive.
63. 4.3, the Stop row (refix round 2, verify-r1 must_fix 1 and 2; extends 54): on a tab whose credential the console
    server no longer accepts, Stop stays in the top bar, enabled and in its danger style, and reads "Reload to stop". Its
    title names the console server's refusal and says that reloading fetches the page with the current credential and
    sends no command; a press reloads the page instead of posting. That is the case when the server refused the
    credential by name (`credential_superseded`), when the runtime was replaced under an API whose handshake says it owns
    its credential per runtime session (`credential_owner: runtime_session`), and when the boot handshake said the API
    minted the credential itself (`api_process`) and a later one says it serves a runtime session's. An API that says
    `api_process` for a runtime it serves keeps one credential for its whole life, and there the plain Stop stays. The
    classic screen's Stop does the same.
64. 3.1 and 5.2 (refix round 2, verify-r1 must_fix 2; extends 53): the boot handshake's credential owner is kept. When it
    was `api_process` and a later answered handshake says `runtime_session`, the tab is marked superseded and replaced
    before any press: commands are off with `credential_superseded`, every control's reason names the reload, the
    session answering is followed and never adopted as the one the tab booted with, and the reconnect banner claims
    readiness only against a session that answered this tab. A refusal code of the console API itself
    (`host_not_allowed`, `origin_not_allowed`, `content_type_not_json`, `console_header_missing`, `credential_missing`,
    `credential_invalid`, `credential_superseded`, `body_not_json`, `runtime_session_changed`) reads "The console server
    refused it: <code>. Nothing reached the runtime." on every press receipt ("The command was not sent on to the
    runtime." for `runtime_session_changed`), and Stop's reads "Stop refused by the console server: <code>. ...", never
    "The runtime refused it".
65. 4.2 and 7.1 (refix round 2, verify-r1 must_fix 3; extends 53): while the runtime has not answered the handshake,
    nothing the page never read is stated as the runtime's. The readiness detail's Contract item is not readable, "Not
    known: the runtime has not answered the handshake (<code>)"; Commands follows the API's own answer (off with its code
    until a handshake is answered; Stop never reads it) and every control's reason is "the runtime is not answering";
    No uncertain command is not readable until a snapshot has reached the page. Diagnostics says "not read" for the
    contract, the link, a camera no line of which reached the page and the controller measurement, draws no synthetic
    Cake badge until a snapshot stated one, and Evidence, the Cake tab, Logs and History say "not read" with the read's
    code when their read failed, never the empty-record sentence (for Evidence's decision frame and verification panel
    and the Cake tab's modules, from refix round 3: 71). In the in-page simulation the contract, commands and uncertain
    items say the simulation answered, with no runtime and no server.
66. 3.2 and 3.5 (refix round 2, verify-r1 must_fix 5; replaces the order of 58): the run in progress is the newer of the
    two records that name one. The fold's own `run.started` wins over the snapshot's active run when it is newer than
    the seq that run started at, and a run whose end the fold holds is no longer in progress by the record. The card
    and the store's reads of finished mission runs use it, and the Stages from refix round 3 (69; until then they read
    the snapshot's active run). The stream's `run.started` and `run.finished`
    re-read the mission record and the snapshot, so after Try again the fresh attempt is named and on the timeline as it
    starts.
67. 3.2 and 4.4, the Running row (refix round 2, verify-r1 must_fix 6): while the mission record says RUNNING (no stop
    asked for), the card's run has ended by its own records and no other run is in progress, the card's headline is
    "Run ended; reading the mission record" in the mute tone and this display's voice, never "Mission running" beside
    the run's end. The mission is read at the run's end and once more a second later while it still says RUNNING.
68. 4.3, the Stop row, and 5.2 (refix round 3, verify-r2 must_fix 1; extends 63 and 64): before it serves the page the
    console API asks the runtime which session it is (waiting at most 2 s) and follows that session first, so a page
    served after a runtime restart holds the credential its own handshake leaves accepted. A runtime that does not
    answer in time changes nothing. The boot block names the session the served credential belongs to
    (`bound_runtime_session`, null when the API minted its own) and its owner (`credential_owner`). When an answered
    handshake, the page's first one included, says the API is bound to another session, the tab is marked superseded and
    replaced before any press: commands are off with `credential_superseded`, and Stop reads "Reload to stop" with a
    title that names the session the page was served for and the one the server serves now, in this display's voice:
    nothing was refused, because nothing was sent.
69. 3.2, 3.5 and 4.2 (refix round 3, verify-r2 must_fix 2; corrects 66): the Stages decide that a stage is in progress
    by the card's rule (`activeRunOnRecord`, and the run must be the newest drawn run), so a run whose `run.finished`
    the fold holds has no stage "In progress", however long the snapshot keeps naming it. While the snapshot's
    capability list still says `run_active` for a run whose end the record holds, and no other run is in progress by the
    record, every control that reads it (Start, the readiness line, the panels' controls) says, in this display's voice,
    "the run has ended by its own record; the runtime has not yet reported itself idle". The control stays off and Stop
    stays on. The store reads the snapshot once more a second after a run's end while it still names that run (each
    second until the next poll, from refix round 4: 73). The in-page source keeps the runtime's order (a run's end is
    published before the run is let go, `run.finished` and then `_release` in `demo_runtime/supervisor.py`), so this is
    the display's rule and not the source's. This covers `run_active` only; its sibling for `mission_running` is 72.
70. 3.6 (refix round 3, verify-r2 must_fix 3; extends 60): at a mission's next run (after the Cake swap, after Try again)
    the Scene keeps the blocks the earlier drawn run saw, drawn as that run's ("seen by run <id> at HH:MM:SSZ", the time
    of the record that saw it), until the new run reports a sighting of its own (a SEARCH or Look detection, or a scan
    block), which replaces them. Meanwhile the Scene says what the new run's own records say about the block, then "the
    block drawn is what run <id> saw." (`data-scene-waiting`; corrected in refix round 4, verify-r3 must_fix 2: the fold
    keeps, for the newest run, the cube-layer answers of the newest frame it reported, `cube-recognizer` and
    `cube-in-pad-frame` with any result, the scan views it reported, and whether its `run.finished` was folded). "Run
    <id> has not reported what it sees yet" only while it has given no cube-layer answer and no scan view and has not
    ended; "Run <id> ended without reporting a sighting" when it ended with none. After an answer the line gives the
    newest frame's answer with its time and the layers' tokens in brackets: "looked at HH:MM:SSZ and did not see a
    block" for the recognizer's not_detected, "could not locate a block in the pad frame at HH:MM:SSZ" for a
    cube-in-pad-frame not_detected, error or ambiguous, "saw a block at HH:MM:SSZ and has not reported where it is yet"
    for a recognizer's detection with no pad-frame answer yet; "and the run has ended" once the run ended, and never
    "yet" for an ended run. The new run's refusal never re-classes a block an earlier run saw. "Nothing seen yet" and
    "Nothing on this map yet" are said only when no drawn run's records hold a sighting.
71. 7.1, Evidence and Cake (refix round 3, verify-r2 must_fix 4; corrects 65): while no snapshot has reached this display
    and the fold holds no run, Evidence's decision-frame card and the verification panel (in Evidence and in
    Connections) say "Not read: no run of this runtime has reached this display (<why>)." in place of "No decision frame
    is pinned", the predicate count and "No predicate has been recorded in this run"; the panel's badge reads "not read".
    While no reading of the robot is held, the Cake tab's modules section says "The resident's modules were not read:
    <code>." ("have not been read yet" before any read); with a reading held and the newest read failed, it says so above
    the held cards. The controller's card is drawn under its own heading, "This runtime's controller (not a Cake
    module)", in every state, never under "Modules the resident holds". The drawer's scope line says "mission: not read
    (<code>)" for a mission read that failed; "no mission" only on the runtime's own `no_mission_recorded`.
72. 3.2, 4.2 and 4.3, the Stop row (refix round 4, verify-r3 must_fix 1; extends 69): the display reads the snapshot
    again when a Stop is answered (after the mission record, when the answer carries a mission part), and whenever a
    mission read finds the end of a mission this display held as carried, together with the capability check and the
    signature check, whose records were read while the mission was carried. While the snapshot read after that end
    still says `mission_running`, it is read again a second later (each second until the next poll: 73). While the
    capability list says `mission_running` and the mission record this display holds is STOPPED, COMPLETE or FAILED,
    every control that reads it (Start, the readiness line, the panels' controls, Return arm on the card) says, in this
    display's voice, "mission <id> has ended by its own record; the runtime has not yet reported itself idle". The
    control stays off and Stop keeps the runtime's own state. The sentence names the mission, so it stays true if
    another display started a new mission the snapshot is about.
73. 3.2 and 4.2 (refix round 4, should_fix honesty S1; extends 69 and 72): after a run's or a mission's end the
    snapshot is read again each second, not once, while it still names the ended run or still says `mission_running`
    beside an ended mission record, at most until the next poll would read it, and never once it no longer says so. The
    classic screen's action card (`?screen=classic`) says the snapshot's `run_active` and `mission_running` as the
    default screen does once the record holds the end ("What stands in the way of a run: the run has ended by its own
    record; the runtime has not yet reported itself idle"), never "a run is already in progress" beside the run's end.
74. 5.1, the activation rows, and 5.5 (refix round 4, should_fix honesty S4; replaces "OBSERVED, `expect` OBSERVED or
    absent: ok" and "REFUSED_BEFORE_TRANSACTION, `expect` the same: ok"): an activation word is green only with a record
    and a served expectation it met. "Activated" is in the ok tone only when `expect` OBSERVED is served; with no
    expectation served it is "Activated" in the neutral bright ink (`INK.bright`), the tone of the refusal with none. A
    refusal before any transaction reads "Refused before any transaction, as expected" in the ok tone only when both slot
    reads show the slot unchanged, as Cake's own refusal already needed; with a read missing it is "Cake refused the
    successor before any transaction" in the bright ink. The classic screen's activation line takes its colour from the
    same verdict.
75. 5.1, the mission `state` rows, and 6.2 (refix round 5, verify-r4 must_fix 1; corrects "STOPPED: Stopped on request"):
    a STOPPED mission is "Stopped on request" (`stopped_on_request`) only when its record carries `stop_requested` true.
    The runtime ends the mission it carries at its own shutdown and writes no stop request for it
    (`demo_runtime/mission_coordinator.py` close: nobody asked for one, the runtime is ending), so after a restart the
    record reads STOPPED with `stop_requested` false and a detail such as "stopped while carrying a target". Any STOPPED
    record without the request reads "Stopped with no stop request on its record" (`stopped_no_request`, warn), which
    claims no cause and is as true of a record older than the field. The card, the timeline's end entry, History and the
    collapsed line read the one word. The card prints a STOPPED mission's own `detail` under its times in the runtime's
    voice, "The runtime's detail: <detail>" (`data-mission-detail`), with or without the request.
76. 3.3 and 6.1 (refix round 5, verify-r4 must_fix 2): after an activation that was not refused, the store reads the
    mission again 250 ms apart, at most 8 times (2 s), while the record read after the answer still says
    WAITING_FOR_CAPABILITY and its newest activation is OBSERVED. The runtime appends the answer before it returns it and
    writes the resume only at the carrying thread's next slot read (`demo_runtime/mission_coordinator.py`,
    `CAPABILITY_POLL_S` 0.5 s), so the one read after the answer could still say WAITING, and until the 5 s poll the card
    said "Mission waiting for Cake" with the candidates on beside the "Activated" card, and a press was refused
    `mission_not_waiting_for_capability`. When a read finds the mission has left the wait, the snapshot is read once too
    (at an end through the mission-end reads of deviation 72). A refusal reads nothing more: the mission still waits,
    and that is what its record says. Not changed: while the record this display last read says WAITING beside the
    OBSERVED answer, the candidates stay on. Until the resume is written the runtime still takes a press; after it, the
    display learns of the resume within one re-read (250 ms), and a press inside that interval is refused.
77. 7.1, Evidence's decision frame (refix round 5, verify-r4 must_fix 3): the store fetches the record and the perception
    of the frame Evidence draws. That is the live or replay fold's pinned frame, as before, and, when that fold pins none,
    the frame the selected run's read-back fold pins (a page opened after the run holds the run's records only there). A
    frame read back for a finished run adds no scene mark. Evidence's layer list words each layer from what this page
    read: a perception read for the shown frame is drawn; a read for it in flight is "the perception of <id> is being read
    by this page"; a read that brought nothing back is "... could not be read by this page"; and with no perception in
    hand for the shown frame it is "... has not been read by this page". "This perception was served for X and the
    picture on screen is Y" is kept only for a perception actually read for another frame X. Before, a page opened after
    the run fetched nothing and every layer said the perception "was served for no observation".
    Round 7 item 1: the console API serves an observation read as the runtime's envelope, `{"ok": true,
    "observation_id", "what", "data": <projected record>}`, and a refusal as `{"ok": false, "reason_code"}` with the
    matching status. The page reads only `data`, and only from an ok envelope for the observation it asked for; before, it
    parsed the envelope itself, every field read as null and the layers said "this frame carries no cube result" beside a
    detected cube. A read that brought nothing back names why after the words above: "; the console server answered
    <reason_code>", "; no answer came", "; the answer carried no data", or "; the answer was for another observation, <id>".
78. The top bar's Stop and the reload offer (refix round 5, verify-r4 must_fix 4; the lead's ruling (c), no console-api
    change): a Stop the console server refuses `credential_superseded` is sent ONCE more with the credential of the page
    the server serves now. The server rotates its credential when any client makes it follow another runtime session (a
    second display's page load, handshake or poll), and a tab learns of that only from its own reads, up to one 5 s poll
    later; in that window it drew the plain Stop and a press was refused before it reached the runtime. On that refusal,
    and only when the page was served with a boot element, the tab reads the page (GET / on its own origin, the document
    a reload fetches), takes the credential from its boot element (`api/boot.ts` bootFromHtml) and sends Stop one more
    time with it. It is not sent a third time, not when the page cannot be read, and not when the page carries the
    credential the tab already holds. The credential is used for that one request and kept nowhere, so every other
    control still goes off with the reload named, and after the press the tab is said to hold a superseded credential, as
    after any such refusal. The receipt says what the display did: "The console server refused this tab's credential
    (credential_superseded). This display read the page the console server serves now, as a reload does, and sent Stop
    once more with the credential in it", then the second Stop's own receipt. Round 7 item 3: a tab that already knows
    its credential is not the one the server accepts now (its own handshake or poll said so, the single display's case
    after a runtime restart) draws the same Stop, enabled, and its press goes through the same takeover, so its first
    press stops the run. Its title states the fact ("This page was served the credential of runtime session A, and the
    console server serves the credential of runtime session B now") and what a press does ("... this display reads the
    page the console server serves now, as a reload does, and sends Stop once more with the credential in it"); it no
    longer says a Stop from this tab would be refused. "Reload to stop", whose press reloads the page and sends no
    command, is drawn only after a press whose takeover could not send its second Stop, whose second Stop was refused for
    its credential too, or that was refused for its credential with no takeover (a page not served with a boot element,
    or a refusal other than credential_superseded); its title names that press's reason. The
    alternative the verdict named as (a), the server accepting a superseded credential on POST /api/stop, is not done:
    it relaxes the guard, and that is the operator's to rule on.
    The read-only banner's sentence for a superseded credential ends "so no handshake gives this tab the new one; a
    reload does, for every control". It said the tab "cannot obtain the new one by asking", which the takeover's
    receipt beside it contradicted.
79. 7.1, Diagnostics > Evidence "why it ended this way" (refix round 7 item 2): a run that ended `stopped` whose own records
    hold a `stop.requested` is explained by its stop records, one line each with the record's wall time and seq: "Stop
    asked for by <origin> at <time>", "Acknowledged by <who> at <time>" (or that no acknowledgement is on the record), and
    "The stop's outcome: <outcome> (<reason_code>) ...; motion ceased: <yes, no or not stated>" with its recorded evidence
    (or that no stop outcome is on the record). Its badge is neutral with no alert icon, and the note says the lines are
    its stop records and that the record carries no reason code beside them. It said, in a red alert badge, that the run
    "ended badly and the record carries no machine-readable reason. That is a gap in the record", beside "The record
    carried no sentence for this failure", about a run whose record states who stopped it. The "gap in the record" note is
    kept for an ending no record explains (no stop request, no reason code). The main screen's failure banner takes the
    first stop line as its sentence for such a run instead of "The controller recorded no reason code."
80. 6.1 and 7.1, a tab open across a runtime restart (refix round 7 item 4): after the runtime is replaced the screen
    draws the selected run from its read-back fold, and that fold was read once, mid-run, and never again, so a run the
    next daemon adopted froze on "In progress" in the Stages (and the Scene on the run as it was) until a reload, beside
    a banner saying the record "keeps arriving and is being shown". Now, while that fold does not hold the run's end, the
    store reads the run's retained events again (the read a fresh tab makes) whenever the records this tab keeps for the
    run go past it, the snapshot names the run's end or no longer names it active, or another runtime session answers;
    at most one read is in flight, a read is never repeated for the same state of the record, a read that reaches less
    far is not taken, and a fold that holds the end is final. When a snapshot names another runtime session than the
    fold's, the event stream is closed and reopened under it from that snapshot's cursor, as a fresh tab subscribes: a
    stream to a daemon that died without closing it can stay open and deliver nothing. The read-only banner says "The
    record below keeps arriving and is being shown" only while the runtime answers this display; with the last exchange
    failed it says "The record below is the last this display read".
81. 2.2 and 6.1, the top bar (refix round 7 item 6): the top bar, which holds the only Stop, is sticky at the top of the
    view over the page's opaque background (`position: sticky; top: 0`), at every width. It scrolled away with the page,
    so right after a press far down the page (an activation after several refusals, Scan scene, Try again, Return arm)
    Stop was hundreds of pixels above the view while the robot moved (measured -391 px at 390 px and -431 px at 1440 px).
    At 390 px the bar wraps to about 250 px of an 844 px view; that height is the cost of keeping Stop in view there.
82. 2.2, no horizontal page scroll (refix round 7 item 7): a design button's label wraps (`.dsbutton` `white-space:
    normal`, `overflow-wrap: anywhere`, `max-width: 100%`; `.dsbutton--sm` has a 32 px minimum height instead of a fixed
    one), the controls row's action button no longer forces one line, and on the demo screen the console's own small
    buttons (the Observe holding choice) wrap and their row wraps. A candidate's key or a tampered twin's name longer
    than a phone is wide pushed the page to 518 px at 390 px and ran a button past its card at 1440 px; a long label now
    grows its button downwards.
83. 2.2 and 6.1, the Diagnostics drawer (refix round 8, verify-r7 must_fix M1): the drawer's overlay starts below the
    top bar, at the bar's measured height (the bar reports its height as it wraps or its detail opens), and is drawn one
    layer under it (z-index 19 against the bar's 20). It covered the whole view above the bar, so with Evidence or
    History open while the robot moved, the only Stop was drawn but a press on it landed on the drawer, at 1440 px and at
    390 px. The bar, its Stop, Start and Diagnostics stay pressable while the drawer is open; the backdrop, Close and
    Escape still close it. The classic screen (`?screen=classic`), whose header is not sticky, passed no height (see 86).
84. 4.2 and 6.1, the Start dialog (refix round 9, verify-r8 must_fix M1): on a runtime that is not the simulation, the
    "Before the mission starts" dialog is not drawn while the record shows a run, and a run that appears while it is open
    closes it for good (a run another display started, or any other). Its "start the mission" is off whenever the top
    bar's Start is off, with Start's own reason above the boxes ("Start is off. ..."), as well as while a box is
    unticked. The dialog stayed open over a run another display had started, its scrim over the top bar, so a press
    where the only Stop is drawn closed the dialog and sent nothing, and "start the mission" was offered although the
    runtime would refuse it.
85. 2.2 and 6.1, every dialog (refix round 9, the class of verify-r8 M1): a dialog's scrim starts below the bar that
    holds the only Stop, at the height the screen measured for it and hands down to every dialog under it (the Start
    dialog, the cycle's checks, "Confirm the connected robot"); with no such bar it starts at the top of the view. Every
    scrim covered the whole view, over that bar, so while any dialog was open a press where Stop is drawn landed on the
    scrim, closed the dialog and sent nothing.
86. The classic screen (`?screen=classic`, refix round 9, verify-r8 should_fix 1): its header, which holds its only Stop,
    reports its height and is drawn above the overlays (z-index 20); the Diagnostics drawer and every dialog under the
    screen start below it. Its Start dialog follows 84: not drawn while the record shows a run, and its "start the
    mission" off, with Start's reason, whenever the header's Start is off. The drawer got no height and covered the whole
    view, header and Stop included, and the Start dialog had the demo screen's M1 defect. The header is sticky (see
    89).
87. 2.2 and 3.1, the top bar's readiness detail (refix round 9, verify-r8 should_fix 2): the lists under "What this
    rests on" scroll inside a box at most 30% of the view's height. They grew the sticky bar without bound: at 844 x 390
    the open bar measured 435 px (569 px in the verifier's case) in a 390 px view, and the only Stop was drawn below the
    bottom of the view. With the box, the open bar measured 223 px at 844 x 390, 213 px at 667 x 375, 420 px at
    390 x 844 and 246 px at 1440 x 900, Stop in view each time. The readiness sentence beside the verdict is not
    bounded; it is one sentence.
88. The classic screen's header at a narrow view (refix round 9, found in this round's audit): the header and its
    controls wrap. On one line, at 390 px, Start, Stop and Diagnostics were laid out past the right edge (Stop at x 618)
    and the page's overflow clipped them, so the classic screen's only Stop could not be reached on a phone. Measured in
    headless Chrome: Stop at x 208 to 263 at 390 x 844 (header 161 px), in view at 844 x 390, and unchanged at 1440 and
    1024 px (header 58 px).
89. The classic screen's header is sticky (refix round 9, found in this round's audit): below the screen's 700 px minimum
    height the page scrolls, and the header, which holds the only Stop, scrolled away with it (at 844 x 390 with the page
    scrolled 310 px, Stop was at y -256). The header is `position: sticky; top: 0`, and the page root clips with
    `overflow: clip` instead of `hidden`, which had made the root a scroll container that never scrolls, so a sticky
    header stuck to it. Measured in headless Chrome at 844 x 390, scrolled 310 px with the drawer open: Stop at y 54 to 86
    and the target of a press at its centre.
90. Section 12 (2026-09-28, operator ruling): a mission, run, Cake activation, signature check or picture of an earlier
    runtime session or an earlier day leaves the main screen for History. This is a new rule, not a deviation; it is
    listed here only so this list stays complete.

## 12. Current and past: the main screen shows only what is current

The operator's ruling of 2026-09-28: "we shouldn't show outdated information at all; historic information should be
moved to a different section", and dates are shown. The case it answers, seen live on 8771: after a runtime restart the
runtime adopts the newest mission on its record without re-entering it (`mission_coordinator.recover`), so the runtime
started at 17:23:10Z served yesterday's FAILED mission `mission-f65c3cf8e99f` as its mission, its journal's last outcome
as the last outcome, and its retained events as the stream, and the main screen drew "Mission failed" in red as if it had
just happened. A Return arm pressed today read as a failure.

### 12.1 The rule, read from the record (`model/currency.ts`)

1. **The session's start.** The runtime writes its session id as `rt-<YYYYMMDDTHHMMSSZ>-<hex>`
   (`demo_runtime/daemon.py`, `journal.utc_compact`); its start is read from that id and from nothing else. No other
   record dates the session. An id in any other shape (the in-page rehearsal's `synthetic-console-session`) dates
   nothing, and then nothing is moved.
2. **A mission that has not ended is always current**, whatever its age: RUNNING, WAITING_FOR_CAPABILITY or any state
   other than COMPLETE, FAILED and STOPPED. An orphaned or held mission needs the operator, and Stop needs it.
3. **A mission this page read before it ended stays current** through its end (the page's own observation,
   `store.missionsSeenOpen`, kept for the page's life and never sent anywhere).
4. **Otherwise an ended mission is placed by its newest record time**: its start, its stop request, its attempts, its
   retries, its activations and its derived end (6.7). At or after the session's start and on this display's UTC day it
   is current; before the start it is "an earlier runtime session"; on an earlier day it is "an earlier day". Today is
   read from this display's clock only when the session dates itself (rule 1), so a day boundary flips it once and
   nothing ticks (6.8).
5. **A run** is current while it is active by the record (`activeRunOnRecord`); a finished run is placed by its own
   recorded times (the finished-run row, its `run.started`, its end in the snapshot). With a dated session and no time
   at all it is not drawn: nothing places it in this session.
6. **A picture**: the wrist's newest saved frame is placed by its `t_wall_utc`; the stream and a picture this page
   decoded are this session's.
7. **A signature check** is placed by its own `runtime_session` against the snapshot's (5.6).

The session id is truncated to the second, so a record written in the same second before a restart counts as this
session's. The mission record does not name its runtime session; "an earlier runtime session" is derived from the
times above and History says it as derived ("recorded before rt-... started").

### 12.2 The main screen with a past mission

| Region | What it shows |
|---|---|
| Mission | `data-outcome-card="past"`, in the muted tone with no verdict colour: "No mission yet in this runtime session." (or "No mission yet today." for an earlier day), then one line "Last mission: failed, ended 2026-09-27 14:24:40Z (in History)" ("last recorded" in place of "ended" when the record gives no end, and the time is then its newest record) with a History button (`data-control="open-history"`, one click to the History drawer). No cause, no "where the robot was left", no Acknowledge, no document title. No step list: an ended mission's step list is the runtime's reading of that mission's last stop state as recorded at its end, not a reading of now, so a by-hand step of yesterday ("Open the gripper by hand") is not on the main screen. Return arm stays beside the line (`data-past-return-arm`), the Actions panel's own control, on or off by the capability list the runtime serves now. Start's guards keep reading the record as before. |
| Cake | none of the mission's activation cards; "No Cake activation in this runtime session. The last mission's activations are in History." The live slot footer stays (it is current); when an activation of the past mission set it, it says so with the full date and "(in History)". |
| Timeline | none of the mission's lines (its start, stop, retries, end, Cake lines and run groups). The store's list stays append-only (6.3.1); the screen filters it (`currentTimelineEntries`), so a line inserted before the session was read leaves too. The filter drops every mission this page read that is History's (`missionsRead`, `pastMissions`), not only the one served now: after Start the runtime serves the new mission and the earlier one's lines stay off. Presses and link changes stay. |
| Stages, scene | drawn from current runs only; the snapshot's last outcome of an earlier session is not the run on the screen. |
| Wrist | a saved frame of an earlier session is not drawn; the line says there is none from this session and dates the kept one. |
| Observe, Return arm (holding) | a past mission does not settle `holding` (`currentHoldingMission` in `panels/commandGuards.ts`): its stop state is what it recorded at its own end, not a reading of now. There is no "Sent as ... from the mission record" line from it; the operator is asked once, told "The mission record the runtime serves, <id>, is History's (its newest record YYYY-MM-DD HH:MM:SSZ, <session words>), so it does not settle whether the gripper is holding the cube now". The click reads the same mission as the screen. A held mission is current (12.1 rule 2) and still settles it (verify-r3 M3). |
| Top bar, Stop | the readiness verdict (4.2) is handed the mission on the main screen, so a past mission is handed as none: its step list never decides rule 3, and the top bar never reads "Steps first: Open the gripper by hand" from yesterday's end (verify-r2 M1). An open or held mission is always current (12.1 rule 2), so rules 1 and 2 of 4.2 are unchanged. Start's guards read the record as before. Stop's visibility and admission are not touched by this rule. |

### 12.3 History

History (the drawer's first tab) is one click from the main screen in every state: the past mission's line carries its
button, and with a current mission the card's head row carries it beside Acknowledge (`data-control="open-history"`).
It carries every item with its full date and time (`YYYY-MM-DD HH:MM:SSZ`) and where it
sits: the mission's summary says "Recorded in an earlier runtime session (recorded before rt-... started). It is not on
the main screen." or "Recorded in this runtime session (rt-...). It is on the main screen."; each run row says its finish
with its date and its session; a mission this page read before the one served now stays under "Earlier missions this page read", with the same summary; activation lines print their dates; a signature check of an earlier session has a section
of its own with its session line, its counts and the live capsule it covered.

### 12.4 Pins

`model/currency.test.ts` (the rule and the timeline filter) and `design/historySeparation.test.ts`: a store over a
runtime restarted today that serves yesterday's FAILED mission draws no failure card, no timeline line, Cake card or stage
word of it, and History carries it dated with its session; an orphaned hold of yesterday's mission stays on the main
screen; a mission of this session stays through its hold and its end on the watching page, and a reload draws the same
card; a past mission's card has no document title or acknowledgement; the whole main screen, top bar included, carries no step of a past mission and keeps Return arm; a saved frame of an earlier session is not drawn.

## 13. The show screen: the default view since 2026-10-11

The operator's ruling of 2026-10-11: the recorded demo is four clicks, Confirm this robot, Start, Enable VLA and Start;
the screen shows the controls, the live wrist camera feed, the stages and Cake's status, with the front camera in the
centre where the scene map was (13.7), and it simplifies the UI, the mission details and the logs, dropping the
information the operator called irrelevant. `design/ShowScreen.tsx` draws it. `model/show.ts` gives its stage rows and
its words. The demo director (`model/director.ts`, run by the store's `startDemo`, `armDemo` and `directDemo`) sends
what mission 1 needs between the first Start and Enable VLA. The commits: a1b8fa8 (the director and the show model),
1cf0fd8 (the screen, made the default), 994b45e (only a run of this runtime session), 0c9bd91 (the wrist camera in the
centre; a driving VLA reads as running).

The screen carries two missions. Mission 1, "scripted pick and place", is the runtime's mission up to the pick, then
the robot runner's scripted carry to the pad. Mission 2, "VLA pick and place (SmolVLA, on the robot)", is one episode
of the robot runner (`vla/runner.py`); no runtime run and no mission record exist for it (`model/show.ts` line 86).

The operator's screen recording of the four clicks, made on 2026-10-12 (its stage times read 16:10Z to 16:12Z), shows
this layout. What it shows beyond the code is said where it bears on the screen (13.4 to 13.8).

### 13.1 Where it opens

`/` opens the show screen. The demo screen this page specifies is at `?screen=demo` and the classic screen at
`?screen=classic`. Any other value of `screen` opens the show screen (`design/screenChoice.ts`, `main.tsx` lines
53-55, pinned by `design/screenChoice.test.ts`).

### 13.2 Regions

```
+---------------------------------------------------------------------------------------------------+
| TOP BAR  shinro [Live]  robot: connected  Motion permitted  VERDICT  sentence                     |
|                                                 Start  Stop  Enable VLA  Diagnostics              |
+---------------------------------------------------------------------------------------------------+
| motion-hold banner with Confirm this robot, drawn only while robot motion is held                 |
+-------------------------+-----------------------------------------------+-------------------------+
| MISSION                 | WRIST CAMERA, large, 16:9                     | FRONT CAMERA, 16:9      |
|  title                  |                                               |  word and one line      |
|  one status line        |                                               |                         |
| STAGES                  |                                               |                         |
|  Find    <word>         |                                               +-------------------------+
|  Pick    <word>         |                                               | CAKE ON THE ROBOT       |
|  Carry   <word>         |                                               |  320 px high            |
|  Place   <word>         |                                               |  activation cards       |
|  Return  <word>         |                                               |  signature check card   |
|                         | state word and one line                       |  live slot footer       |
+-------------------------+-----------------------------------------------+-------------------------+
```

| Region | What it shows |
|---|---|
| Top bar | The demo screen's top bar (`TopBar.tsx`; 3.1, 4.2): the mode mark, the robot and its connection, the motion permission, the readiness verdict with its sentence and "What this rests on", then Start, Stop, Enable VLA and Diagnostics. Start's off-reasons are the demo screen's (4.2; `DemoScreen.tsx` lines 375-389) with three differences (`ShowScreen.tsx` lines 142-153): a mission start, a retry or a demo Start in flight from this tab also counts as a command not settled, the runtime's own `start_cycle` capability reason (`capabilityOffReason`) is not among them, and once the VLA module is enabled `vlaStartOff` (the runner's state, a VLA command in flight, the runtime holding the arm) takes the place of every reason after the unsettled command, the motion hold's included. Enable VLA sits between Stop and Diagnostics (`TopBar.tsx` line 219) and is drawn only when the console has the VLA door (`store.vlaOffered`). Under the bar, when they apply: the strip's alerts, the uncertain-command panel and the motion-hold banner, which carries Confirm this robot. |
| Left: Mission | "Mission", the mission's title (22 px) and one status line (13.6), with a 4 px left rule and a 6 percent wash in the tone of the words: neutral `INK.bright`, busy `C.accent`, good `C.ok`, bad `C.bad`. |
| Left: Stages | The five rows Find, Pick, Carry, Place and Return (`StageTimeline`), each with its word (13.6). The region's `title` attribute names what the rows are read from: "the runtime's mission record and the runner's carry events" for mission 1, "the robot runner's episode events" for mission 2. |
| Centre: Wrist camera | The wrist camera, large. |
| Right: Front camera | The front camera, the same tile, smaller. |
| Right: Cake | "Cake on the robot", 320 px high: the demo screen's Cake region (3.3), with the activation cards of the mission on the screen, the signature check card and the live slot footer. While the director is armed the prepared Plans' buttons are not drawn: the director presses the Plan. |

Each camera tile draws the stream while it shows, the last picture this page decoded through a gap, and otherwise the
newest saved frame of this runtime session. Under it are one state word and one line, as on the demo screen's wrist
panel (3.7), for example "Live" with "The stream is showing on this page.", or "Connecting" with "No picture from this
camera: <reason>." (the front tile in the recording, 13.7).

The grid (`design.css`, `.show-grid`) has three columns, 3 : 6 : 3, at 1100 px and wider, with 16 px gaps and 20 px of
side padding. Below 1100 px it is one column in the same order: mission and stages, wrist camera, front camera, Cake.
Document order is screen order (no `order`, `grid-row` or `grid-area`), and `design/showScreen.test.ts` pins the
mission before the wrist camera and the wrist camera before the front camera.

The screen keeps the rule of section 12. The mission is drawn only when it is current, and the stages draw a run only
when it belongs to this runtime session (994b45e). After a runtime restart the snapshot's last outcome still names an
earlier session's run; before 994b45e the screen drew that run's stages, a carry failed at the hold of a mission that
was History's.

### 13.3 What it leaves out

No scene map; no run account (the outcome card of 3.2, with its cause, its measurement, where the robot was left and
the runtime's steps); no Timeline region (3.4) and no log; no Scan scene, Observe, Return arm or Try again; no VLA
panel (the demo screen's `VlaPanel.tsx`). `design/showScreen.test.ts` pins the scene, the timeline, Scan scene, Return
arm and the VLA panel as absent. The scene map and all four of those controls are on the demo screen (`?screen=demo`);
the classic screen (`?screen=classic`) has Observe and Return arm, and no scene map, Scan scene or Try again.
Diagnostics, opened from the top bar, keeps History, Logs, Evidence, Connections and the Cake tab, as on the demo
screen.

### 13.4 The four clicks

1. **Confirm this robot.** The motion-hold banner's button, drawn only while robot motion is held (`RobotParts.tsx`,
   `MotionHold`). It opens "Confirm the connected robot"; with the dialog's box ticked, its own confirm button confirms
   the resident session and host instance the dialog shows. Before it the verdict is Not ready, "Robot motion is held:
   <why>"; after it, in the recording, the verdict is Ready and Start is the primary control.
2. **Start.** `store.startDemo`. While the runner is in standby it first sends the runner's `to_mission_pose` (the arm
   goes past the mission's start pose, then onto it) and, when the runner takes it, reads the runner up to 40 times,
   500 ms apart, until the runner no longer reports `moving`. It then starts the runtime's mission (POST
   /api/mission/start), whether or not the runner took the move, and when the runtime answers with a mission id it
   arms the director for that mission (13.5). Start reads "Starting" until then.
3. **Enable VLA.** The runner's `swap` (POST /api/vla/swap). The runner loads the checkpoint, whose sha256 must match
   the one in `vla/envelope.json`, warms it up and, when the runtime leaves the arm free, brings the arm to the VLA
   start pose. The runner is a process on the robot that runs beside, never inside, the installed runtime, the Cake
   resident and the supervised host, and changes none of them (`vla/runner.py`). The policy is loaded by that process,
   not by Cake, and no text on the screen says Cake loaded it (`model/vla.ts`). The button is on in standby while no
   mission runs and no run is active. It reads "Enabling VLA" while the module loads and "VLA enabled", off, once it is
   ready (`enableVla`). The block is then put back at its start by hand, as the mission card says.
4. **Start**, again. Once the module is enabled, Start goes to the runner (`startRoute`): its `run` (POST
   /api/vla/run), which the runner refuses unless the runtime is idle and the last inference has finished. One episode
   runs, arm only: the runner's client sends base velocity 0 on every command (`vla/runner.py`). The arm goes to the
   start pose; each chunk is inferred while the arm holds, then played through the gate at 30 Hz; the grip is measured
   the runtime's way at the first chunk boundary after the jaws latch, and once it is confirmed the jaws' reading is
   checked again at every chunk boundary (a block that left the jaws is said, the jaws reopen and the policy may grasp
   again; 06de776). The recorded takes contain no release, so once the arm rests in the end region with the grip
   confirmed, the runner lowers the tool straight down in 8 mm steps, at most five, until the runtime's gate refuses
   one (cd72c96), and opens the jaws by rule; the policy may also open them once the grip is confirmed
   (`vla/episode.py`). While the episode runs the verdict reads Running (0c9bd91), Start reads "Running" and
   is off with "the VLA policy is driving the arm; Stop ends it", and Stop is on. Stop disarms the director, sends the
   runner's `stop` while the runner may be driving (at once, never queued behind another press) and sends the
   runtime's Stop.

### 13.5 What the director sends after the first Start

The director acts only for the one mission this tab armed with its Start: for any other mission it derives nothing
(`directorStep`). A read never arms it, so a page opened on a waiting mission activates nothing and a display that did
not start the mission directs nothing. The armed mission and its runtime session are kept in the tab's
`sessionStorage` (`kiwi-console.demo-armed.v1`), so a reload of the same tab carries on. A snapshot of another runtime
session disarms it, and so does the operator's Stop, also during the first Start's pose move (5e972c9). Activate,
Stop and the carry are then sent with no press of their own. This follows the operator's ruling of 2026-10-11 and is an
exception to the console's rule that a command is only ever sent by somebody pressing a button (`main.tsx` lines 7-9).

A pass runs after every mission read and every runner read, and it sends one step at a time; after it sends a step, one
more pass runs 1.5 s later (`DIRECTOR_SETTLE_MS`). Each pass derives the step from the records, so a step already on
the record (an activation submitted, a stop requested, a carry recorded for this mission) is not sent again.

| The records say | The director |
|---|---|
| The mission waits for Cake (`WAITING_FOR_CAPABILITY`, `capability_unavailable`) and its newest activation is neither `SUBMITTED` nor `OBSERVED` | Activate (POST /api/mission/activate) the Plan served with the role `activate`, else the only Plan. With no Plan, or several and none so marked, it hands back to the operator: "no single Plan to activate (N prepared)". |
| The mission is held for another reason and its newest attempt records the block held (`stop_state.holding` true), as mission 1 is at HOLD_OBSERVE | Stop (POST /api/stop). Held without the block, it hands back: "the mission is held without the block (<reason>)". |
| The mission ended (STOPPED or FAILED) holding the block, and the runner is idle in standby | The runner's carry (POST /api/vla/carry). The runner records the carry with the id of the mission the runtime has on record. |
| The runner's carry names this mission with the result `done`, or the mission is COMPLETE | Finished. |
| The carry names this mission with another result, the mission ended without the block, or the runner is loading, ready, running or in error | It hands back, with the carry's message or its own reason, for example "the VLA module was enabled before the carry (runner ready)". |
| Anything else | It waits, and the mission card says what for. |

The carry (`vla/carry.py`) refuses before any motion unless the jaws read closed on something (the runtime's own
held-or-empty rule) and the arm is at the mission's start pose. It then drives two forward base pulses of 0.05 m, each
measured on the base's own velocity reading, eases the arm to the release pose, opens the jaws, eases the arm back and
drives two backward pulses. A forward pulse that measured less than 70 percent of 0.05 m ends it refused, with the
block still held at the start pose.

In the recording the live slot footer read `mobile_approach` active at generation 2 before Start, and the Cake region
read "No Cake activation in this mission." through mission 1: no activation is on that mission's record.

### 13.6 The words on the screen

The mission card (`showWords`). Mission 2 has the card from the moment the module starts loading until it is dropped
(`showPhase`: the runner loading, ready or running).

| Moment | Title | Status line |
|---|---|---|
| No current mission | Mission 1: scripted pick and place | "Press Start." |
| Mission 1 running | Mission 1: scripted pick and place | "The mission is running." |
| Activate due | Mission 1: scripted pick and place | "Activating the mobile approach capability on Cake." |
| Cake's answer awaited | Mission 1: scripted pick and place | "The activation is in flight.", "Cake is activating the capability.", then "The capability is active; the mission resumes." |
| Stop due at the hold | Mission 1: scripted pick and place | "Picked. Handing over to the carry." |
| The carry | Mission 1: scripted pick and place | "Carrying the block to the pad." |
| The carry of this mission done | Mission 1 complete | "The block is on the pad and the robot is back at its start. Next: Enable VLA." |
| The director handed back | Mission 1 stopped | its reason, as a sentence |
| A held mission this tab did not start | Mission 1: scripted pick and place | "The mission is held; it was not started from this display, so it is not directed here." |
| A mission this tab did not start, ended | Mission 1 complete, when it completed; else the mission 1 title | "The block is on the pad." when it completed; else "The last mission was stopped. Press Start for a new one." or "The last mission failed. Press Start for a new one." |
| The module loading | Mission 2: VLA pick and place (SmolVLA, on the robot) | "Loading the VLA module on the robot." |
| The module enabled | Mission 2: VLA pick and place (SmolVLA, on the robot) | "VLA module enabled. Put the block back at its start, then press Start." |
| The episode running | Mission 2: VLA pick and place (SmolVLA, on the robot) | "The VLA policy is driving the arm." and the newest event's words, with "Chunk N." when it names one |
| The episode done | Mission 2 complete | the runner's headline and detail (`model/vla.ts`), in the recording "The VLA policy carried the block to the pad. grip confirmed; jaws opened by rule at the pad; 20 chunks in 319 s." |
| The episode stopped or refused | Mission 2 stopped | the runner's headline and detail |

Any other wait of the director is its reason as a sentence, for example "The mission is stopping." or "A command is
still settling.". The episode's words are "Starting." before its first event, then "Moving to the start pose.", "At
the start pose.", "Thinking.", "Moving.", "Jaws closed.", "Closed on nothing; trying again.", "Checking the grip.",
"Released the block.", "Returning." and "Done.".

The stage rows (`mission1Stages`, `mission2Stages`):

- Mission 1: the controller's own rows up to the pick (3.5). At the hold with the block held, Pick reads "Picked" and
  Carry "Picked; the carry takes over". The carry's events then give Carry "Rolling forward with the block" and
  "Forward pulse <n>: <metres> m"; at the release Carry "Carried to the pad", Place "Released at the pad" and Return
  "Rolling back"; then Return "Back: pulse <n>, <metres> m" and "Returned to the start".
- Mission 2: Find "Looking at the scene", then "Thinking, chunk <n>" or "Moving, chunk <n>". At the latch Find "Found
  the block" and Pick "Jaws closed; checking the grip"; after an empty close Pick "Closed on nothing; trying again",
  then "Trying again, chunk <n>". After the grip check Pick "Grip confirmed (<score>)" and Carry "Carrying to the pad",
  then "Carrying, chunk <n>", or Pick "Grip not confirmed". At the release Carry "Carried to the pad", Place "Released
  at the pad" and Return "Returning", then "Returning to the start pose" and "Returned".
- In both, a refusal is drawn on the stage it happened in, with the runner's message, and a stop as "Stopped here on
  request".

The top bar at the same moments, as the recording shows it:

| Moment | Verdict | Sentence |
|---|---|---|
| Before Confirm | Not ready | "Robot motion is held: <why>" |
| After Confirm | Ready | none; Start is the primary control |
| The first Start, until the mission runs | Running | "A command sent from this tab may have started a run that the runtime's last snapshot does not show yet, and the runtime accepts only Stop while a run is active."; Start reads "Starting" |
| Mission 1 running | Running | "Mission <id> is being carried. Stop ends it; a second mission is not started beside it." |
| The director's Stop at the hold | Held for you | "Mission <id> is stopping: its stop was asked for and it has not ended yet."; Start reads "Stopping" |
| The carry | Running | "The robot is carrying the block to the pad; wait for it." |
| The module loading | Running | "The VLA policy is loading on the robot; wait for it." |
| The swap's move to the VLA start pose | Running | "The arm is moving to a start pose; wait for it." |
| The episode | Running | "The VLA policy is driving the arm; Stop ends it." |
| After mission 1, and while the module waits ready before and after the episode | Steps first | "Open the gripper by hand and take the block out." (13.8) |

### 13.7 The wrist camera in the centre (0c9bd91)

The directive of 2026-10-11 asked for the scene map to leave the centre "and use this space to showcase the front
camera" (operator), and 1cf0fd8 put the front camera there, with the wrist camera on the right. In the mission started
at 17:18Z the same day, with the camera service running both cameras, SEARCH failed (`search_no_cube`) and the service
had delivered no capture (`captures_ok` 0); its status, read at 17:22Z, reported both camera workers failed with "no
frame in 60 consecutive reads" (`bin/pi/camera_service.py`, `MAX_CONSECUTIVE_EMPTY_READS` 60). No cause is claimed
here. The camera service went back to the wrist camera only, the setup `camera-service.md` keeps until the front camera
is qualified ("The front camera is OFF"), so the centre tile would have shown no picture. The operator asked for the
two tiles to be swapped "so that the central bigger one is the wrist" (2026-10-11, 17:36Z), and 0c9bd91 did it; its
message gives the reason as the wrist view being the view the policies act on. The front tile stays on the right. In
the recording it reads "Connecting" and "No picture from this camera: no_frame_yet." throughout: `Connecting` is the
runtime's word for a preview subscriber that has received no frame (`demo_runtime/sources.py`), and the front camera
is off.

The same commit made a driving VLA read as running. While the policy drove the arm, the bar had read Not ready, "the
VLA module is not ready", beside the button "VLA enabled" (operator, 17:36Z). The verdict now counts the runner
loading, moving or running as a run, and Start's reason names what the runner is doing (`vlaStartOff`).

### 13.8 Residuals

- **"Steps first" after mission 1.** The verdict reads the runtime's mission record (4.2, rule 3). That record ends at
  the director's Stop with the block held, and the carry is the runner's record, not the runtime's. So after mission
  1, and while the module waits ready, the bar reads "Steps first" with the runtime's first step, "Open the gripper by
  hand and take the block out.", beside "Mission 1 complete" or mission 2's lines, although the carry opened the jaws
  at the release pose, and Start is not drawn as the primary control. The recording shows it.
- **The swap's pose move.** After the module loads, the runner moves the arm to the VLA start pose in its `moving`
  state, which `showPhase` does not count as mission 2. For that move the card shows "Mission 1 complete" again and
  the button reads "Enable VLA", off ("the arm is moving; wait for it"), until the runner is ready. The recording
  shows it.
- **"At the pad" is the sequence's word, not a measurement.** Mission 1's "The block is on the pad ..." and its rows
  "Carried to the pad" and "Released at the pad" follow the carry's events and its result `done`, which records the
  measured base pulses and the jaws opened at the release pose. Mission 2's "Released at the pad" follows the runner's
  `released` event, sent when the jaws open. Neither mission takes a view of where the block came to rest, and the
  screen has no counterpart of the demo screen's placement check (3.2.1).
- **Runner events with no words.** The episode also sends `reopened`, `grip_lost` and `set_down` (`vla/episode.py`;
  the last two were added after this screen, in 06de776 and cd72c96). The card says them by their raw names ("The VLA
  policy is driving the arm. grip_lost."; the recording shows "The VLA policy is driving the arm. set_down." before
  "Returning."), and `grip_lost` does not change the rows: after the runner said the block left the jaws, Pick keeps
  "Grip confirmed (<score>)" until the next latch and Carry keeps its "Carrying" words.
- **The runner's 40 events.** The runner's status carries its newest 40 events (`vla/runner.py`), and mission 2's rows
  are folded from those alone. A chunk adds two (thinking, moving), so once an episode has run about 20 chunks past
  its latch, the rows lose the latch and the grip check.
- **A refused activation.** The director waits only while the newest activation is `SUBMITTED` or `OBSERVED`. After
  Cake refuses the Plan (`REJECTED_BY_CAKE`) the mission still waits for Cake (4.1), so the director sends Activate
  again on its next pass, and on every pass after it while Cake refuses; the same holds for a newest activation in
  `REQUEST_FAILED` or `COMPLETED_NOT_OBSERVED` while the mission waits for Cake. Neither `model/director.test.ts` nor
  `store/director.test.ts` has a case for it.
- **A moment of "Failed here" at the hold.** The hand-over words need the mission record to show the block held at
  HOLD_OBSERVE (`mission1Stages`); until a mission read shows it, Carry is the controller's own row. In the recording,
  for less than two seconds at the hold, that row read "Failed here" beside "The mission is running.", before "Picked;
  the carry takes over".
- **A Stop during the first Start's pose move cancels the Start (5e972c9).** Found by the independent review of
  2026-10-12: `startDemo` used to start the mission and arm the director whatever happened to the move, and Stop was off
  until the runner was first read `moving`. Now disarming while the Start is in flight ends the pose wait and starts
  no mission, a mission whose start was already on its way is stopped and never directed, and Stop is on while the
  Start is pending (the show screen's Stop is the store's `stopDemo`). Checked by unit tests; not yet run on the robot.
