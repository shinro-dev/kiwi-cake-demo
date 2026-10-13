<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Integration handoff, 2026-09-22

Written at the end of the session that produced the browser-driven pick and place. It
is operational: what exists, where it is, what is proven and what is next.

## Preserved

| | |
| --- | --- |
| Evidence commit | `0cd51db` |
| Tag | `browser-pick-place-milestone-2026-09-22` (annotated, local, NOT pushed) |
| Implementation that executed | `16bbc031c0ef9adc35e006851bb8ff8bea22768e`, recorded clean (`dirty: false` in the run's own manifest) |
| Account | `docs/autonomous-demo/browser-pick-place-2026-09-22.md` |

## The run

`console-20260922T083804Z-zxkydauq`, 2026-09-22T08:38:05Z to 08:45:25Z, mode `live`.

Evidence is outside this repository, in the ignored local evidence directory, under
`evidence/browser-pick-place-20260922/`. Verified present: `run/manifest.json`,
`run/controller-events.jsonl`, `run/completion.json`, `run/stdout.log`,
`run/stderr.log`, `journal.jsonl`, and `observations/`. 151 files.

CORRECTED 2026-09-22, after this document first said "72 frames": that 72 is a count of
PNG FILES, not of pictures taken. The run took **24 distinct captures**, each written
into three directories (`frames`, `perception`, `pi-captures`), and the 72 files hold
only 44 distinct images by sha256. Over 424 s that is about 0.057 frames per second,
one still roughly every 18 seconds. Every capture is the WRIST role; the front camera
produced nothing here and has produced nothing in any retained evidence.

Read it with, from that directory:

```
python3 -m json.tool run/manifest.json
python3 -m json.tool run/completion.json
python3 -c "import json;[print(json.loads(l)['kind']) for l in open('run/controller-events.jsonl')]" | sort | uniq -c
python3 -c "import json
for l in open('run/controller-events.jsonl'):
    e=json.loads(l)
    if e['kind']=='predicate.evaluated': print(e['payload'])"
```

## The result

Object secured, placed and base returned. Footprint clearance about **17.6 mm** of
margin above the on-pad bar, against a **20 mm** target, missing it by **2.39 mm**
(observed edge margin 35.57 mm; on-pad bar 17.96 mm; clearance target 37.96 mm).

Zero automatic retries. Zero task-control interventions.

A physical milestone, demonstrated and watched. **Full acceptance remains open**: it
needs `clearance_target_met`, which this run did not meet.

## Defect: the terminal outcome mapping

**FIXED 2026-09-22.** The word is now decided by `_PLACEMENT_PREDICATES`
(`object_secured`, `placed`, `base_returned`) in one pure helper beside them, and
`clearance_target_met` travels as its own predicate. `placed_new` was reused rather than
a sixth value minted, because adding an enum value is a v2 change. The console names the
check that missed and what it measured. No threshold moved and no record was rewritten.
The account below is kept as the description of the defect that was fixed.

`bin/laptop/manipulation/run_single_cube_cycle.py:1468`

```python
outcome = "placed_new" if all(predicates[name] is True for name in _RUN_PREDICATES) else "unknown"
```

The word is all-or-nothing. One false predicate collapses the whole run to `"unknown"`,
which is then indistinguishable from a run whose outcome genuinely is not known. This
run placed the cube and returned, and reported `unknown`.

`:1473` then reads that word to decide whether a failure is attached, and
`packages/demo-runtime/demo_runtime/supervisor.py:1353` reads it again beside the exit
status. A fix has to satisfy all three readers.

The fix belongs in the mapping or in the presentation, NOT in the thresholds. Do not
rewrite the retained records: they are correct and this run's `unknown` is part of the
evidence for the defect.

## Machine state at handoff

Checkout: this worktree, branch `feat/operator-console`, 17 commits, tree clean, nothing
pushed. The sibling main checkout holds about 51 unrelated modified and untracked files
that must stay untouched.

Private configuration, contents never committed or quoted:

- the runtime configuration, in the main checkout's ignored local console directory;
- the operator's host-start wrapper, beside it;
- the gate's private token list, under the main checkout's ignored state directory.

Launch, from this worktree:

```
KC_RUNTIME_CONFIG=<the private runtime configuration> KC_PI_SSH=<the robot ssh target> \
  bash bin/laptop/start-console.sh live --enable-hardware-commands
bash bin/laptop/start-console.sh stop
```

Left running at handoff, deliberately, not restarted:

- a live console API on port 8770 with its runtime, on the real configuration;
- a second console API on port 8791 from user-interface testing, on a scratch data
  directory. It is disposable; stop it with its own data directory.

**Hardware observations below were taken 2026-09-22 between 08:24Z and 08:45Z and are
not valid indefinitely. Re-read before commanding anything.** At that time: the robot
computer had rebooted at 09:24:48 local, the arm was acquired and holding with zero
displacement, the NORMAL safe-attach motor host was attached, and the three base wheels
read zero velocity. A power cycle drops torque and resets every goal, and the attach
refuses in that state until the arm is acquired again.

## What exists, and how far it is proven

Implemented, unit-tested and PROVEN ON HARDWARE:

- the single-cube controller and its motion guards;
- mission stills: the controller ships its capture helpers to the robot computer and
  retains frames per run (24 distinct wrist captures in this run, written as 72 files);
- the runtime, the command journal, single-run ownership, the baseline backend against
  the NORMAL host, and the browser command path end to end.

Implemented and unit-tested, NOT hardware-qualified:

- `bin/pi/camera_service.py`, one owner per camera with a preview tap and mission
  priority, and `autonomy/perception/camera_lock.py`. Never deployed to the robot;
- `packages/demo-runtime/demo_runtime/preview_client.py` and `tap_client.py`, the
  subscribers for the preview and the host tap;
- `bin/pi/host_boundary.py`, the command lease and the observation tap. Deferred.

CORRECTED 2026-09-22: the list above reads as though the preview path were switched on
and merely unqualified. It is not switched on at all. Measured through the real loader
against the live configuration, the tap is null, so the tap endpoint and the preview
endpoint are null for BOTH roles; no forward opens, no subscriber thread starts, the
slot reads `preview_not_configured` and the preview route answers 503. The running
daemon carries no preview port either. **Zero cameras stream today**, and everything
above the preview client is single-role, so the next step is role-aware configuration
and routing rather than an extension of a working stream. See the 2026-09-22 section of
`decision-log.md`.

Contracts, in `packages/demo-contracts/schema/v1`, generated for Python and TypeScript:

- `source.schema.json` already defines the preview stream, preview slot and frame size;
- `snapshot.schema.json` carries the latest retained observation and the latest
  controller measurement;
- `cake.schema.json` defines the ladder, slots, transactions, update preview and
  continuity sources;
- `manifest.schema.json`, `history.schema.json` and `export.schema.json` describe the
  run manifest, the history list and the run-bundle export shape.

Cake today: the read-only status and inventory surface only. There is no admin adapter,
and nothing has been exercised against a Cake resident. The console states once that
execution is the Python baseline and Cake is not connected to the task.

## Next, bounded and in order

1. **Correct the outcome reporting.** Make a run that placed and returned say so, keep
   `clearance_target_met` visible as its own fact, and leave `unknown` meaning unknown.
   Change no threshold and rewrite no record.
2. **Continuous dual-camera video.** Deploy and qualify the camera service, reuse the
   existing ownership and lock, keep mission capture priority, and never let preview
   work block control. Verify both streams DURING a physical cycle; intermittent
   mission stills are not completion.
3. **The minimal frontend data bindings.** Camera sources, task progress, measured
   summary, Cake module state and operations, durable run history. Reuse the contracts
   above rather than inventing a second view model.
4. **Cake integration** through its actual supported interfaces: real module inventory,
   versions, activity, and the allowed enable, disable and update operations, honest
   about whether Cake participates in the running task.

## Open after the outcome work, 2026-09-22

### Result persistence: CLOSED

The clearance figures used to reach the screen only for a tab that folded the
`predicate.evaluated` record live. A tab that opened late, or reloaded, printed "the
record that measured it is not in this fold", while the measurement sat in the retained
run at `history/<run_id>/controller-events.jsonl` the whole time and
`/api/history/{run_id}/events` already served it.

The store now reads those records back when this tab folded none, and folds them through
the SAME reducer the live path uses, so a read-back result cannot drift from a watched
one. It is scoped to the run it was read for, asked once per run, never published if the
run on the screen moved on while the read was in flight, and kept out of REPLAY, which is
a different mode with its own fold. A run that genuinely retained nothing stays
unavailable rather than borrowing a value.

`tests/test-browser-result.sh` holds it, in a real browser: one tab watches a cycle
finish, two more open afterwards, and all three must draw the identical line. PROVEN BY
MUTATION rather than by passing: with the read-back disabled and the app rebuilt, the
suite fails on the late-opening tab; restored, it passes.

### The footprint allowance is not bound yet

The `clearance_target_met` predicate carries the CUBE-CENTRE to pad-edge margin and the
REQUIRED CENTRE margin (`run_single_cube_cycle.py:1142`). The FOOTPRINT clearance and its
target are a different pair, computed at `:1135` and reported on the PLACED state record
as `footprint_margin_any_yaw_mm` and `clearance_target_mm`. Until that pair is bound
data, the screen names the two numbers it actually has and never reconstructs the
footprint figure by subtracting a remembered constant. Once it is bound, the simplified
presentation can show about 17.6 mm of footprint clearance against the 20 mm target.

### Provenance of the 2026-09-22 browser verification

Run `console-20260922T111228Z-99cd440e`, which is the run in the rendered-page evidence
for the outcome wording, was **SYNTHETIC**: data mode synthetic, the fake controller
under `KC_FAKE_SCENARIO=placed_short`, on a scratch data directory and a spare port. A
real runtime process and a real controller process produced it, and NO ROBOT AND NO
CAMERA were involved. It is not a second hardware run. The only physical run remains
`console-20260922T083804Z-zxkydauq`.

## The demo this is all for

Cube A is collected. Cube B needs `mobile_approach`, which the running capability does
not provide. An invalid update is offered and REJECTED without disturbing the running
mission. A valid capability is then activated, and the SAME mission continues on to
Cube B with no restart of the robot and no restart of the client.

That is the claim the whole programme exists to demonstrate: runtime capability
evolution during a live task. Nothing above may weaken it into a reload or a restart.
