<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Robot-side VLA runner

`vla/` is the robot-side half of the console's VLA beat: a SmolVLA policy that drives the arm to pick the block and
bring it to the pad (mission 2 of the recorded demo), and the scripted carry that finishes mission 1. This page
describes the code at this commit; values come from the code or from dated runs on the robot, and operator rulings are
named as such. The checkpoint and the takes are not in this repository; `vla/envelope.json` pins the model's sha256.
Commit ids cited here name commits of the development history, which is not published: each published change is one commit.

## 1. What it is

`vla/runner.py` runs on the robot beside, never inside, the installed runtime, the Cake resident and the supervised
host, and changes none of them (operator, 2026-10-08; `vla/runner.py:3-4`). The policy is loaded by this process, not
by Cake, as the console's own words say (`apps/console-web/src/model/vla.ts:3-4`). A swap restarts none of the three.

- **Control socket.** ZMQ REP on `tcp://127.0.0.1:5593`, one JSON `{"op": "<name>"}` at a time (`vla/runner.py:33`,
  `463-482`); a request that is not JSON gets `{"error": "not_json"}`, an unknown op `{"error": "unknown_op"}`. It
  checks no credential: any local process that reaches the port can send an op; the runner's refusals (section 4) apply.
- **Arm and camera.** Each episode, pose move or carry opens its own LeKiwi client to the supervised host on 127.0.0.1,
  built as the runtime's controller builds it, and closes it after; episodes and pose moves also open the camera
  service's local client, and an episode reads the wrist camera only (`vla/runner.py:351-404`). Every command an
  episode or a pose move sends carries base velocity zero (`vla/runner.py:383-384`); only the carry moves the base.
- **The runtime.** Before a run, a carry or a pose move, and again at every chunk, the runner reads the runtime's own
  snapshot and mission state over its socket (`--runtime-socket`). The arm is free only when no run is active, the
  owner is `free` and the mission is `COMPLETE`, `STOPPED` or `FAILED` or none is recorded; any other answer, a refusal
  or no answer is busy (`vla/runner.py:270-312`). A read waits 4 s; a timed-out read is retried once (commit d28d078).

The console reaches the runner through its API's VLA door, `apps/console-api/console_api/vla.py`, which forwards and
decides nothing:

- The API takes `--vla-endpoint tcp://127.0.0.1:5593`, the desktop end of the ssh forward that `tools/vla/vla-up.sh`
  opens. `VlaForward` accepts only a `tcp://127.0.0.1:` or `ipc://` endpoint and waits 12 s for an answer, room for the
  runner's two runtime reads (`vla.py:16-20`). Without the flag the routes answer 404 `vla_not_configured`.
- `GET /api/vla/status` forwards `status`: the runner's answer, or `{"state": "unreachable", "reason_code":
  "vla_runner_unreachable"}` (`app.py:954-961`). `POST /api/vla/<op>` forwards one of `swap`, `unswap`, `run`, `stop`,
  `to_mission_pose` and `carry`: the runner's answer, or 503 `vla_runner_unreachable` (`app.py:964-975`, `1252-1253`).
- `shutdown` is deliberately not routable: it is in neither `OPS` nor the route pattern (`vla.py:10`, `app.py:1253`),
  so the console cannot end the runner. `vla-up.sh stop` sends it straight through the ssh forward.
- The guard: every POST passes `_command_body()`, the guard of `POST /api/commands` (Host, Origin, JSON content type,
  `X-Console-Request: 1`, the session credential in `X-Console-Credential`, a JSON object body; `app.py:752-763`,
  `security.py:240-260`). A request that fails it is refused, 403 or 400, and nothing is forwarded.
- The browser reads the status every 5 s, and every second while the runner is loading, running or moving; a Stop goes
  out at once, never queued behind another press (`apps/console-web/src/store/store.ts:87`, `1626-1638`, `1644-1649`).

In the recorded demo (operator ruling 2026-10-11: Confirm, Start, Enable VLA, Start) the first Start presses
`to_mission_pose` (runner in standby), runs the runtime's scripted mission 1 and presses `carry` once that mission is
stopped holding the block; Enable VLA presses `swap`, the second Start `run` (under `apps/console-web/src/`:
`store/store.ts:1588`, `1605`, `design/ShowScreen.tsx:185`, `213`). The demo's Cake activation is in mission 1, for
the mobile approach capability (`model/director.ts:6-10`, `model/show.ts:181`); the swap is not a Cake step.

## 2. Files

| Path | What it is |
| --- | --- |
| `vla/runner.py` | The process: states, ops, control socket, runtime reads, the links to the arm and the camera. |
| `vla/episode.py` | One episode: start move, chunks, grip check and watch, release, return; also a pose move. |
| `vla/gates.py` | The action gate every policy action passes before it reaches the arm. |
| `vla/frame.py` | The camera service's 1280x720 wrist frame as the policy was trained to see it: measured crop, 640x480, rotated, RGB. |
| `vla/policy.py` | SmolVLA inference on the robot CPU from the checkpoint alone: sha256 identity, warm-up, one chunk per call. |
| `vla/carry.py` | Mission 1's scripted carry. |
| `vla/envelope.json` | Limits derived from the 30 recorded takes (15412 frames): joint ranges, floor, start pose, end-of-take region, model sha256. |
| `vla/envelope.py`, `vla/__init__.py` | Loads `envelope.json` once; the package marker. |
| `vla/tests/` | The unit tests (section 7). |
| `tools/vla/deploy-pi.sh` | Copies the runner, the checkpoint and the LeRobot source to `~/vla-dev` on the robot. |
| `tools/vla/vla-up.sh` | Starts, stops and reports the runner and its loopback forward. |
| `tools/vla/derive_envelope.py` | Writes `vla/envelope.json` from a LeRobot dataset of the recorded takes. |
| `tools/vla/measure_wrist_mapping.py` | Run on the robot: measures where the 640x480 wrist view sits in the 1280x720 one (`CROP_BOX`). |

## 3. Deploy and start

Both scripts run on the desktop, reach the robot over ssh as `$KC_PI_SSH` and refuse to run without it (source the
console env file). Their files on the robot live under `~/vla-dev`.

`tools/vla/deploy-pi.sh CHECKPOINT_DIR LEROBOT_SRC_DIR` refuses (exit 3) unless `CHECKPOINT_DIR/model.safetensors` has
the sha256 that `vla/envelope.json` names; creates `~/vla-dev/app`, `models/<sha256>`, `lerobot-src` and `captures`;
copies with rsync `vla/` (without `tests` and `__pycache__`) to `~/vla-dev/app/vla`, the checkpoint to
`~/vla-dev/models/<sha256>/` and `LEROBOT_SRC_DIR/lerobot/` to `~/vla-dev/lerobot-src/lerobot/` (the two code copies
with `--delete`); and checks the robot's copy of the model against the same sha256 (exit 3 otherwise). It writes only
under `~/vla-dev` and copies the working tree as it is, recording no commit. It does not create the runner's Python
environment (`~/vla-dev/venv`), nor restart a running runner, which keeps its code until `vla-up.sh stop` and `start`.

`tools/vla/vla-up.sh start|stop|status`:

- `start`: a runner already running on the robot is reported and left alone. Otherwise the script reads the runtime
  release directory from the `WorkingDirectory=` line of the runtime's systemd user unit, `kiwi-cake-runtime.service`
  (exit 3 when that directory does not exist), starts `~/vla-dev/venv/bin/python -m vla.runner --checkpoint
  ~/vla-dev/models/<sha256> --runtime-release <that directory>` detached (with `HF_HUB_OFFLINE=1`,
  `HF_HOME=~/vla-dev/hf` and `PYTHONPATH=~/vla-dev/app:~/vla-dev/lerobot-src`), logs to
  `~/vla-dev/runner-<UTC stamp>.log`, one JSON line per event, and waits up to 10 s for the `listening` line. In both
  cases it then opens the forward from `127.0.0.1:5593` on the desktop to the robot's `127.0.0.1:5593`, unless one is
  open. The runner starts in `standby` with no model loaded.
- `stop`: sends `shutdown` through the forward (20 s timeout), prints the answer, closes the forward and prints
  `stopped`, also when the shutdown did not reach the runner: `status` tells.
- `status`: the forward's process, and the runner's process on the robot or `runner: none`.

That release is the one the runtime unit runs, built by `bin/runtime-release.sh` from one commit. The runner takes the
runtime's own code from it (`vla/runner.py:450-459`), including the latch and open values, the retention score and its
minimum, the held-or-empty rule, the straight-up step and the forward kinematics, and keeps it until restarted.

## 4. States and ops

| State | Meaning |
| --- | --- |
| `standby` | No policy loaded: at start, after a carry, after `unswap`. |
| `loading` | `swap` is loading the checkpoint and running one warm-up inference. |
| `ready` | The policy is loaded and no episode runs. |
| `running` | An episode runs. |
| `moving` | A pose move or the carry holds the arm. |
| `error` | The load failed; `error` says why (for example `vla_model_sha_mismatch: ...`). `swap` may be pressed again. |

The console adds `unreachable` (no usable answer from the runner or from its API) and `absent` (its API was started
without `--vla-endpoint`; `apps/console-web/src/api/http.ts:871-872`). An accepted op answers `{"accepted": true}`, a
refusal `{"accepted": false, "reason": ...}`; `vla_runtime_busy` adds a `detail`, such as `a mission run is active` or
`the runtime could not be read (TimeoutError)`.

| Op | What it does | Refused with |
| --- | --- | --- |
| `status` | Returns `state`, `model_sha256` (the envelope's), `warm_inference_ms`, the last 40 `events`, `outcome`, `preview_jpeg_b64` (a small JPEG of the view the policy is given), `error` and `carry`. Every call resets the dead-man clock. | never |
| `swap` | `loading`: builds the policy, refuses a checkpoint whose sha256 is not the envelope's, warms it up. Then, if the runtime leaves the arm free, `moving` to the VLA start pose (the median first pose of the takes), otherwise a `pose_skipped` event; then `ready`. The swap does not clear the stop flag: after a Stop that no later run, pose move or carry cleared, its pose move ends at once. | `moving`; `loading`; `already_loaded` (ready or running) |
| `run` | Starts one episode (section 5); `ready` again when it ends, with its `outcome`. | `already_running`; `not_ready`; `vla_still_thinking` (the last inference still computes); `vla_runtime_busy` |
| `stop` | Sets the stop flag and answers `{"stopping": true}` only while running. An episode, a pose move or a carry ends at its next tick or hold pass, the arm holding. | never |
| `unswap` | Drops the policy. If the runtime leaves the arm free, `moving` to the mission start pose, then `standby`; otherwise `standby` at once. | the state, while `running`, `moving` or `loading` |
| `to_mission_pose` | `moving` to the mission start pose (pan -9.4, lift -79.341, elbow 33.5, wrist flex 67.3 deg): first 3.5 deg of elbow and 1 deg of wrist flex past it, then onto it, the second move only when the first finished, since the servos stop 1 to 2 deg short of a goal. Then the previous state. | the state, unless `standby` or `ready`; `vla_still_thinking`; `vla_runtime_busy` |
| `carry` | Mission 1's carry (section 6), then `standby`, with its outcome and the runtime's mission id under `carry`. | `swapped` (ready); the state, unless `standby`; `carry_unavailable`; `vla_runtime_busy` |
| `shutdown` | Sets the stop flag, waits up to 15 s for a running episode (not for a carry or a pose move), answers `{"bye": true}` and exits. Not routable from the console. | never |

A pose move that ends over 8 deg off its target is said as `vla_pose_not_reached`, one that raises as `vla_pose_failed`.

## 5. One episode

`Episode.run()` (`vla/episode.py:295-389`) works chunk then pause (operator ruling R-VLA-2: inference on the robot CPU).

1. **Start.** The arm moves to the VLA start pose: lift, elbow and wrist flex first with the pan held, then the pan,
   cosine-eased at 20 deg/s (at least 0.6 s per phase) at 30 Hz, then up to 2 s to settle within 3 deg. A joint still
   more than 8 deg away ends the episode refused `vla_start_pose_not_reached`.
2. **Chunks.** At each boundary the episode checks Stop and the time bound, asks the runtime again whether the arm is
   free (busy: refused `vla_runtime_busy`), captures the wrist frame, reads the arm, runs the grip checks below, then
   asks the policy for a chunk in a worker thread while the arm holds its last command (`thinking`): none within 45 s is
   `vla_inference_timeout`, an exception `vla_inference_failed`, not rows of six `vla_chunk_malformed`. The arm is read
   again after the pause; up to 50 actions go through the gate at 30 Hz from that reading (`moving`, `inference_ms`).
3. **The action gate** (`vla/gates.py`, operator ruling R-VLA-3), per action: six finite numbers, or refused
   `vla_action_not_finite`; the wrist roll frozen at the recorded -96.75 deg; the four arm joints smoothed (EMA, alpha
   0.35) from the pose read at the chunk start, at most 5 deg of change per joint per tick, clamped to the recorded
   ranges widened by 3 deg; and a target whose tool height (forward kinematics) is below the floor, -0.1345 m (5 mm
   under the lowest tool height of every third recorded action, `tools/vla/derive_envelope.py:39`, `46`), keeps the
   previous target, counted in `floor_holds`. The jaws are never the policy's value: open until it asks below 73 for 4
   ticks in a row, then the runtime's latch value, held at least 30 ticks, open again only after 4 ticks above 77.
   Moves and holds send the latch or open value, never a reading.
4. **Time bound.** 600 s from the start, then refused `vla_time_bound` (operator ruling 2026-10-09, commit 2613b5f: a
   300 s bound had ended a run whose grasp latched at 284 s and scored 0.95, before any place).
5. **Dead-man.** While an episode runs, a watcher stops it when no `status` request arrived for `DEADMAN_S`, 90 s
   (event `deadman`); the arm holds and the outcome is `stopped` (`vla/runner.py:43`, `241-246`). It was 6 s until
   commit fe9bdbf: on 2026-10-12 it stopped an episode mid-run, and the session attributed the missing polls to its
   console tab in a background Chrome window, where timers are throttled (the gap was not measured). With 90 s, a run
   whose console stops polling goes on for up to 90 s before the dead-man stops it.
6. **Grip check**, at the first boundary with the jaws latched, in the runtime's two halves. Once the latch was held
   0.75 s and two readings 0.1 s apart agree within 0.3 deg (at most 1.5 s), the jaw reading must pass the runtime's
   held-or-empty rule, `closed_on_something`. An empty close is said (`grip_empty`) and the jaws opened; no new latch
   is taken until the policy itself asks open, so it may grasp again (operator ruling 2026-10-09, commit 5eedc9a). A
   close that passes gets three straight-up lifts, +15, +28 and +8 mm, solved and gated by the runtime's own step, the
   arm still after each; the frames after the upper two are kept (`~/vla-dev/captures/grip-*.jpg`) and scored with the
   runtime's `retention_score`. Below its minimum: refused `retention_not_demonstrated`, the jaws held closed; a refused
   lift: `vla_grip_check_lift_refused`, the block held. `object_secured` carries the score, heights and jaw reading.
7. **Grip watch** (commit 06de776). At every boundary after `object_secured` the jaw reading must still pass
   `closed_on_something`; when it does not, the block has left the jaws: `grip_lost` is said, the grip is unconfirmed
   again, the jaws open and the policy may grasp again. It was added after a 2026-10-12 run that reported a release at
   the pad while the block was found at its start spot. A frame is kept at every release (`grip-<stamp>-release.jpg`).
8. **Release.** A release counts only after `object_secured`; a reopen before it is a regrasp (`reopened`). The policy
   may open the jaws itself after a confirmed grip (`release_by` `policy`). Otherwise the rule releases at a boundary
   where the jaws are latched, the arm is inside the end-of-take region (`end_pose_min_deg` to `end_pose_max_deg` in
   `vla/envelope.json`: where every take ended, widened by 5 deg) and moved less than 3 deg since the last boundary; at
   rest there without a confirmed grip it is refused `vla_grip_not_verified`, the block held. The rule first sets the
   block down (commit cd72c96): up to five straight-down steps of 8 mm through the runtime's own step
   (`cartesian_step_pose`, holding; `vla/runner.py:335-341`), ending early when the runtime refuses one, for example
   at its floor (`set_down` says how many and why); Stop and the time bound end it like any move. Either release keeps
   a frame and holds the open value until the jaws read open (within 3 deg, at most 1.5 s), then says `released` with
   `by` and `jaw_deg`.
9. **Return.** The arm moves back to the VLA start pose (`returning`) and the outcome is `done`. The start and return
   moves are sent directly, not through the action gate, so its floor does not apply to them (`vla/episode.py:123-147`).
10. **Stop and refusals.** Stop is read at every tick and during every pause: the arm holds its present joints with the
    jaws as commanded, and the outcome is `stopped`. Every other end the episode decides is `refused` with a code and
    holds the same way; a refused grip keeps the jaws closed. An exception it does not handle, such as a camera that
    gives no wrist frame, ends the run refused `vla_runner_failed`, without that hold (`vla/runner.py:255-257`).

The outcome carries `result` (`done`, `refused` or `stopped`), `code`, `message`, `object_secured` (true, false or
null), `retention_score`, `released`, `release_by` (`rule`, `policy` or null), `chunks`, `floor_holds` and `seconds`.

## 6. Mission 1's carry

The runtime's scripted mission picks the block and stops at `HOLD_OBSERVE` holding it: the pad is too far to measure
with the block in view. `vla/carry.py` finishes mission 1 (operator ruling 2026-10-08); since 2026-10-11 it is the
runner op `carry`, pressed from the console (commit 04cfde3). It repeats the sequence of the operator's earlier carry
script, which set the block down on the pad on 2026-10-09 and 2026-10-11, at the same rate and with the same move and
hold times (`vla/carry.py:8-9`); since commit abd82ca its pulses are measured (below).

- **Precondition.** The runner is in `standby` (before the swap) and the runtime leaves the arm free: the mission that
  holds the block was stopped. Before any motion it refuses `carry_nothing_held` unless the jaws pass the runtime's
  held-or-empty rule, and `carry_not_at_low_pose` unless every arm joint is within 8 deg of the mission start pose.
- **Sequence, at 30 Hz.** The latch held 0.5 s; two forward pulses; the arm eased in 4 s to the release pose (pan
  -10.73, lift 45.49, elbow 34.54, wrist flex -17.44 deg: "the demonstrations' median release, IK'd 10 cm nearer and
  15 mm higher", `vla/carry.py:6-7`), held 0.8 s; the jaws opened for 1.5 s and read (`jaws_after_release`); the arm
  eased back in 4 s and held 0.8 s; two backward pulses.
- **The pulses.** The base moves only here, through `base_command`: forward or backward at 0.05 m/s or stopped, never
  sideways, never a turn (`vla/carry.py:32-37`). Since commit abd82ca each pulse is commanded until the base's own
  forward velocity reading (`x.vel`) integrates to 0.05 m, at most 2.5 s, then rests 0.8 s; each `carry_base` event
  carries `measured_m` and `commanded_s`, and the outcome `forward_m` and `backward_m`. Two pulses make 0.10 m out and
  0.10 m back: on 2026-10-12 two carries measured 0.101 m out and 0.102 m back, then 0.103 m and 0.101 m, each done in
  about 19.5 s. Commit 2b282e7 passes the runner's clock to the carry beside its sleep, so a test can stub both.
- **Stall refusal.** A forward pulse that measured less than 0.7 of 0.05 m ends the carry refused `carry_base_stalled`
  before anything is released: the wheels stopped, the block still held, the arm at the start pose. On 2026-10-11 at
  15:50 UTC the forward pulses had moved nothing (cause not established) and the block was set down on the floor where
  it was picked. Backward pulses are measured and reported, never refused.
- **Stop, and no dead-man.** Stop ends the carry at the next tick: the wheels stopped, the arm and jaws holding their
  last command; the carry's link also stops the base when it closes (`vla/runner.py:433-437`). An exception ends it as
  `carry_failed`. The dead-man watches episodes only: a started carry runs to its end unless Stop is pressed, its moves
  and holds fixed in time and each pulse capped at 2.5 s.

## 7. Tests

```sh
# from the repository root
python3 -m unittest discover -s vla/tests -t .
```

At this commit it ran 86 tests, all passing (`Ran 86 tests`, `OK`, about 40 s on the desktop). It needs numpy and
OpenCV but no robot, model, runtime or socket: fake arms (one lags its commands, as the servos do), a fake policy,
scripted runtime answers and a fake base link. It covers the envelope and the frame conversion; the gate's rules;
the episode's done path, refusal codes, Stop, 600 s bound, release rule, grip check, empty close, grip watch and
set-down; the runner's ops and refusals, the runtime answers that count as busy, the per-chunk re-check, the dead-man
(with a short test value) and the read retry; and the carry's sequence, refusals, allowed base commands, measured
pulses and stall. The console door's tests are in `apps/console-api/tests/test_vla_route.py`.

## 8. Limits

- **Inference runs on the robot's CPU** (operator ruling R-VLA-2), in fp32 with 3 threads by default
  (`vla/policy.py:52-56`, `vla/runner.py:447`). Read live from the runner's events at every fifth chunk: 11.7 to 13.1 s
  per chunk in six runs on 2026-10-09 (CPU 84.5 to 88.4 C at those chunks, throttle flags 0xe0006 or 0xe0000 at the
  ends of five), 11.6 to 12.7 s on 2026-10-11 (86.2 to 86.7 C, flags 0xe0000), 12.0 to 12.4 s in four runs on
  2026-10-12. A chunk plays for at most 1.7 s (50 actions at 30 Hz), so the arm mostly holds still; of the runs watched
  this way, the two that left the block on the pad took 270.9 s (2026-10-11) and 248.8 s (2026-10-12). Three timed
  swaps (2026-10-08 to 2026-10-11) reached `ready` in 42 to 56 s, about 11 s of it the warm-up.
- **The policy never learned a release.** None of the 30 recorded takes contains one: every take ends with the block
  held at the pad, and the recorder's return opened the jaws unrecorded (`vla/episode.py:22-24`,
  `tools/vla/derive_envelope.py:10-12`). The policy holds the block at the pad and the episode lets go by rule. All five
  releases in the runs watched from 2026-10-09 to 2026-10-12 were by rule.
- **The release point varies with where the policy rests.** The rule opens the jaws wherever the latched arm stops
  inside the end-of-take region, which spans pan -35.51 to 11.59 deg and lift 64.41 to 98.85 deg
  (`vla/envelope.json:36-47`). Nothing measures where the block lands: the console's "carried the block to the pad" is
  read from the rule (`apps/console-web/src/model/vla.ts:118`). On 2026-10-12 a release frame showed the held block over
  the pad's near edge, and after the run the block lay on the floor in front of the pad; the fall itself was not seen
  (commit cd72c96). On the next run the jaws opened over the pad between its logos and the block stayed on the pad.
- **The set-down cannot go below the runtime's proven grasp height.** Its step is the runtime's, which refuses a target
  more than 2 mm below that height (`bin/laptop/manipulation/run_single_cube_cycle.py:338`, `436-437`), so an 8 mm step
  fits only where the arm rests at least 6 mm above it. On that next run it rested lower: the first step was refused
  ("this step targets the tool -16.9 mm relative to the PROVEN grasp height: never below it") and none was taken.
- **The front camera is not used.** The runner captures the wrist camera only, and the policy takes one image, the wrist
  view (`vla/runner.py:369`, `vla/policy.py:20-21`). The front camera is not qualified to stream beside the wrist camera
  (`docs/autonomous-demo/camera-service.md:96-108`); on 2026-10-11, with both enabled, both camera workers failed ("no
  frame in 60 consecutive reads"), and the camera service went back to the wrist camera alone.
