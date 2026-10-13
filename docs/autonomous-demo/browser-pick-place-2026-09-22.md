<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Browser-driven pick and place, 2026-09-22

The first autonomous single-cube cycle started from the console in a browser, on the
robot, by the operator. The operator watched the cube be picked up, carried, released
onto the pad and the robot return.

This is a record of what happened and what the run measured. It is NOT an acceptance
claim: one predicate did not pass, and the outcome metadata is missing. Both are stated
below and neither has been worked around.

## Identity

| | |
| --- | --- |
| Run | `console-20260922T083804Z-zxkydauq` |
| Implementation | `16bbc031c0ef9adc35e006851bb8ff8bea22768e`, working tree clean (`dirty: false` in the run's own manifest) |
| Mode | `live` |
| Started / ended | 2026-09-22T08:38:05Z / 2026-09-22T08:45:25Z (7 min 20 s) |
| Execution path | the runtime's baseline backend against the NORMAL safe-attach motor host; no host boundary, no telemetry tap, no camera service |
| Automatic retries | 0 |
| Task-control interventions | 0 |

The controller, its arguments and the host are the proven ones. The browser submitted
the command; the runtime owned the run; the existing host executed it.

## What the cycle did

```
SEARCH -> PREGRASP -> ALIGN -> ALIGN -> APPROACH -> STRADDLING -> GRASP_CLOSE ->
OBJECT_SECURED -> PLACEMENT_PLAN -> CARRY -> HOLD_OBSERVE -> BASE_ADVANCE ->
BASE_MEASURED -> BASE_ADVANCE -> BASE_MEASURED -> RELEASE_ADMISSION -> RELEASED ->
PLACED -> BASE_RETURN -> BASE_MEASURED -> BASE_RETURN -> BASE_MEASURED -> BASE_RETURNED
```

Every phase of the mission ran once, in order, with no operator step in between.

## The four end predicates

| Predicate | Value | Measured | Threshold |
| --- | --- | --- | --- |
| `object_secured` | true | 0.9157 retention score | 0.5 |
| `placed` | true | 35.57 mm | 17.96 mm |
| `clearance_target_met` | **false** | 35.57 mm | 37.96 mm |
| `base_returned` | true | 0.1796 mm | 10.0 mm |

## What the clearance numbers measure, and what they do not

The two thresholds are derived from the pad and cube geometry and differ by exactly
20 mm. `17.96 mm` is the smallest edge margin at which the cube counts as ON the pad.
`37.96 mm` is that bar plus a 20 mm allowance, and is the whole-footprint clearance
TARGET.

The single measurement `35.57 mm` is the observed edge margin of the cube after
release. It is above the placed bar by 17.6 mm and below the clearance target by
2.39 mm. So:

- the cube is physically on the pad, measured, not asserted: `placed` true at 35.57 mm;
- the placement missed the clearance target by 2.39 mm: `clearance_target_met` false.

Those are two different statements about one measurement, and the run made both.

`release_margin` was true at 38.59 mm against the same 37.96 mm target: the position the
release was ADMITTED at was above target. The cube came to rest 3.02 mm short of that
plan. The gap between an admitted release and an observed placement is the thing this
run measured, and it is the reason those are separate evidence kinds.

`already_placed` was false at the start, measured -8.25 mm against 17.96 mm. A negative
margin means the cube was outside the pad, which is where the supported scene puts it.
That is the expected starting condition and not an execution failure. Had it been true
the run would have been the no-op.

## The outcome word is missing metadata, not a failure

`run.finished` carries:

```
outcome "unknown", exit_status 0, phase "BASE_RETURNED", failure null, reason_code null
evidence_complete false, evidence_gaps 1
```

The controller reached its terminal phase and exited cleanly with no failure and no
reason code. What is absent is the mapping from that terminal state to an outcome word:
a run that placed the cube should read `placed_new`. The screen therefore showed a
generic unknown outcome for a run that physically succeeded at three of its four
predicates. That is a reporting gap in the runtime, and it is open.

The one evidence gap is `source_stamp_missing`: the host does not stamp its
observations because the telemetry tap is deferred, so every consumed stamp is
receiver-side. Known, and the reason `evidence_complete` is false.

## Standing

A browser-driven physical milestone: the cycle was commanded from the console and the
robot completed it. Full acceptance is NOT claimed. It requires `clearance_target_met`,
which this run did not meet, and an outcome the runtime can name. Both remain under
review, and no threshold was changed and no record rewritten to make this read better
than it was.

## Evidence

Held outside this repository, in the ignored local evidence directory, under
`evidence/browser-pick-place-20260922/`: the run directory with its manifest, controller
events, stdout, stderr and completion record; the runtime journal; and the run's
observations with 72 frames. 151 files, about 50 MB. Raw camera frames are never
committed.
