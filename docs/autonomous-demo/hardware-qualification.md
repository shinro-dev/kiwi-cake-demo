<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Hardware qualification: one attended session, no motion

**Steps 0 to 4 involve NO MOTION.** No torque is enabled, no motor is commanded, no joint is moved, no wheel is turned.
The session opens the wrist camera, reads the motor host's observations, and sends six messages that the host must
refuse. Nothing else physical happens.

**Browser-triggered motion stays disabled until every step below has passed and its numbers are written down.** Until
then the operator console may show state and evidence; it may not move the robot.

The driver is `bin/laptop/qualify-robot.sh`. It runs the steps in order, stops at the first failure, prints a numbered
transcript, and writes every raw number to a directory outside this checkout (by default
`.local-dev/qualification/<UTC timestamp>`, which git ignores). This file is the runbook: what the operator does, what
each step proves, what it cannot prove, what passes, and what to do when one fails.

```bash
bin/laptop/qualify-robot.sh                 # the whole session
bin/laptop/qualify-robot.sh --skip 0,1      # resume after the first two steps passed
```

Options: `--window-s N` (each measurement window, default 60), `--gap-s N` (how long the tap subscriber stays away in
step 3, default 20), `--rate-tolerance-pct N` (default 10), `--out-dir DIR`, `--port-offset N` (when a forwarded local
port is already in use on the laptop), `--yes` (skip the confirmation of step 2; only for a repeat within one session).

The session needs `KC_PI_SSH`, `KC_PI_IP` and `KC_WRIST_CAMERA_ID` in the shell, and `KC_PI_PYTHON` when the host's
interpreter is not the miniconda python in the remote account's home. No identity is printed, transcribed or written to
any number file.

## What is already satisfied

Preconditions 1 and 2 of `docs/autonomous-demo/host-boundary.md`, "Before it is turned on", were checked on the robot
computer on **2026-09-21**, read-only, in the Python environment the motor host runs in. Recorded here so that the
attended session does not repeat them by hand:

| Precondition | Result on 2026-09-21 |
|---|---|
| 1. the installed host loop matches the boundary's assumptions | `stock host loop: the assumptions of host_boundary.py hold` |
| 2. the pyzmq and libzmq versions of the robot computer | pyzmq 27.1.0, libzmq 4.3.5, the versions the offline tests ran on |

Also recorded from that check: the robot computer is reachable and `aarch64`; its motor host runs under the interpreter
at `$HOME/miniconda3/bin/python3` (the system `python3` there has neither lerobot nor pyzmq); under that interpreter
lerobot is present; and at the time of the check neither the motor host nor a camera probe was running.

Step 0 runs both checks again at the start of the session. That is deliberate: the robot computer can be rebooted,
updated or left with something running between one day and the next, and a precondition recorded yesterday is not a
precondition observed now.

## What the operator does physically

Before starting, and for the whole session:

1. The arm is acquired and HOLDS its pose, as `docs/autonomous-demo/robot-host-setup.md` describes. Nothing in this
   session moves it, and nothing in this session catches it either.
2. The scene is the ordinary one: the pad and a cube in front of the robot, fixed lighting, nothing moving.
3. The base has free floor in front of and behind it. No step commands the base, and the stock watchdog writes a
   zero-speed hold to the wheels whichever way the session goes.
4. A hand near the servo power switch for step 2. The switch is the emergency stop and it is the operator's, not the
   software's.
5. Between step 1 and step 2, the operator starts the motor host WITH the boundary in ANOTHER terminal and leaves it in
   the foreground of that terminal:

   ```bash
   bin/pi/start-host.sh boundary
   ```

   Wait for `SAFE ATTACH OK`, `HOST BOUNDARY ON` and `Waiting for commands...`. The qualification session waits up to
   five minutes for that host to appear and then tells the operator what it sees. Leave that terminal alone for the rest
   of the session: closing it stops the host, and the arm keeps holding when it does.

That is the whole physical part. Everything else the script does over ssh.

## The steps

### Step 0: read-only preconditions

Nothing may be running. The script deploys the files it reads from (`host_boundary.py` and the legacy camera probe, each
copied and checksummed) and then asks the robot computer about itself.

**Proves:** the robot computer is reachable; the interpreter the host runs with exists and has pyzmq, lerobot, numpy and
cv2; the pyzmq and libzmq versions; the INSTALLED `lekiwi_host.py` still matches the shape the boundary rests on; no
motor host and no camera service is running; no `/dev/video` node has an owner; no camera lock is held.

**Cannot prove:** that any of this is still true a minute later. It is a starting condition, not a guarantee.

**Passes when** every one of those answers is the expected one. The transcript prints them all, pass or fail.

### Step 1: camera ownership

No motion, and the motor host does not need to be running. The script runs the legacy per-capture probe, starts the
camera service, runs the probe again, stops the service, and runs the probe once more.

**Proves:** the legacy path works on its own; while the service runs it holds the wrist camera's lock as
`camera_service`; the legacy probe is then REFUSED with `camera_lock_held`, exits 3 and opens no device; after the
service stops no lock is held and the legacy path works again. That is the handoff rule of
`docs/autonomous-demo/camera-service.md` demonstrated in both directions.

**Cannot prove:** that the service's pictures are the same pictures as the legacy path's. That comparison is step 1 of
the camera service's own qualification and is not part of this session. It also cannot prove anything about a program
that opens a camera without taking the lock: the lock is advisory and excludes only the service and the probe.

**Passes when** the first and last probes keep a valid wrist frame and exit 0, the middle one keeps none and exits 3
with `refused.reason: "camera_lock_held"`, the lock names `camera_service` while the service runs, and no lock is held
after it stops.

### Step 2: command rejection

The motor host must be running WITH the boundary. The script asks the operator to confirm, then sends five JSON objects
that carry no lease metadata and one message that is not JSON at all, and reads the host's own counters from the tap.

**No message can reach a motor.** None carries a joint position (`*.pos`) or a wheel velocity (`*.vel`). In lerobot at
commit b4e2d0b6, `LeKiwi.send_action` reads `base_goal_vel["x.vel"]` before it writes any register, so an action without
that key raises before the bus is touched, and the stock loop catches the exception and does not refresh its watchdog
timestamp. On top of that the script sends nothing at all until the boundary's tap has been seen publishing, and the tap
exists only when the boundary is installed.

**Proves:** every message was READ by the host (`ingestion.received` grew by six), every one was counted as rejected
(`no_lease` by five, `malformed` by one), NOTHING was dispatched to the host loop (`commands.dispatched_total`
unchanged), no fault happened inside the boundary (`internal_errors` unchanged), the host kept running at its own rate
with the same session, and the clock the base watchdog reads was not fed: `commands.since_last_ms` counts from the last
DISPATCHED command and did not go backwards.

**Cannot prove** that `stop_base()` ran. Tripping the watchdog requires a command to have been dispatched first, which
requires a live lease and a real action, which is motion and is not part of this session. What this step establishes is
the half that matters for admission: a rejected command does not feed the watchdog clock, so the watchdog behaves for a
rejected command exactly as it does for silence. The other half, that a dispatched command followed by silence still
stops the base, belongs to the first leased cycle, with the operator's authorization for that test.

It also cannot prove anything about a client that forges the metadata. The boundary is cooperative ownership, not
authentication; `host-boundary.md`, "Residual bypasses", lists what it does not stop.

**Passes when** all of the counters above move exactly as described. Any dispatch at all is a failure.

### Step 3: observation freshness

The motor host must be running WITH the boundary. Two bounded windows on the tap, one with the camera stream off and one
with it on, plus a comparison of the host loop rate with a tap subscriber attached and with none.

**Proves:** within each window the source session is one and the same, the sequence advances strictly, the measured rate
(computed from the host's own stamps and sequence numbers, never from laptop time) is what it should be, and no sample
was lost between the host and this laptop. The unsubscribed comparison closes the subscriber for `--gap-s` seconds and
then reads the sequence and stamp again, so the loop rate across the gap is measured entirely on the host's clock: it
shows whether having a tap subscriber changes the host loop at all.

**Cannot prove:** anything about the observation stream the controller reads. The tap is a separate PUB socket; a tap
sample that is lost says nothing about the PUSH/PULL stream, and a subscriber that is absent leaves every observation
stamped. It also cannot see the motor bus: read errors and retries are logged in the host's terminal and are not
exported, so the operator watches that terminal during this step.

**Passes when** each window has one session, a strictly advancing sequence, no lost sample, no malformed sample and no
transport error, and the subscribed and unsubscribed loop rates differ by no more than `--rate-tolerance-pct`.

### Step 4: camera cadence and cost

The motor host must be running WITH the boundary. A baseline window with the camera service stopped, then the service
started and one window in which the preview is subscribed, three mission captures are requested and the tap is watched
at the same time.

**Proves, and records:** the negotiated pixel format and resolution, `source_fps_reported` against
`source_fps_measured`, `timestamp_semantics` (and `fallback_reason` when it is the weaker mode),
`frames_discarded_stale`, `frames_discarded_settle`, the request-to-frame latency on the robot computer's clock and the
round trip on the laptop's, the preview's measured fps, gaps and longest interval as this laptop sees it, and
`reads_without_frame` at the start and at the end. For every capture taken in the stronger mode it checks the service's
own rule from the metadata: `capture_mono_ns > request_mono_ns + exposure_guard_ns` and later than the last settle
frame. And it compares the motor host's loop rate and longest observation interval with the stream open against the
baseline with it closed.

**Cannot prove:** servo read health directly. The motor bus's read errors and retries are not exported by the host, so
what this step measures is the observable proxy, the loop rate and the longest gap between observations, and the
operator reads the host's terminal for bus errors during the window. It also proves nothing about the front camera,
which stays off, and nothing about the pictures themselves: whether the service's frames are equivalent to the legacy
path's is step 1 of `camera-service.md` and is a separate comparison.

**Passes when** every mission capture returns a frame that satisfies the timestamp rule (or records the weaker mode
explicitly), the preview reaches this laptop, the tap window has no lost sample, and the loop rate with the stream open
is within `--rate-tolerance-pct` of the baseline. A capture refusal, a capture that fails the timestamp rule, or a
changed loop rate fails the step.

If `timestamp_semantics` comes back as `dequeue_time_only`, the step can still pass, as the WEAKER mode: the frame is
then chosen by the driver's buffer depth rather than by a measured capture time. Write that in `camera-service.md` under
the dated heading, because it changes what a capture means.

## When a step fails

The session stops at once and nothing after it runs. The transcript names the reason on the `step N: FAIL:` line and the
raw numbers are in the output directory, one JSON file per measurement. Fix the cause and resume with `--skip` listing
the steps that already passed. A resumed session repeats the steps you do not skip; it does not trust yesterday's
result for the ones you do.

Failures with an obvious next move:

| What the transcript says | What it means |
|---|---|
| a precondition of the robot computer does not hold | read the lines above it: something is running, a camera has an owner, or the interpreter has no pyzmq |
| the installed host loop does not hold its assumptions | lerobot was updated on the robot computer. Do not enable the boundary. Read the MISMATCH lines and `host-boundary.md` |
| the legacy probe failed before any service was started | a camera problem that has nothing to do with the service. Stop; the service is not the suspect |
| the legacy probe exited 0, not 3 | the lock did not exclude it. The two sides are not resolving the same lock directory; compare `directory_source` on both |
| the tap published nothing in 10 s | the host is running WITHOUT the boundary. Stop it and start it with `bin/pi/start-host.sh boundary`. Nothing was sent |
| commands were DISPATCHED | the boundary is not doing its job. Stop the host. Do not enable browser motion, and do not continue the session |
| the loop rate differs by more than the tolerance | something costs the host loop time. Which window it appeared in says whether it is the tap or the camera stream |
| the ssh forward exited at once | a local port is already in use on the laptop; rerun with `--port-offset` |

## What this session does not cover

- Anything that moves. The first leased cycle, the base watchdog after a real dispatch, and any browser-triggered motion
  are separate, later, and need the operator's authorization for that specific test.
- Whether the camera service's pictures match the legacy path's (`camera-service.md`, step 1 of its own procedure).
- The front camera, which stays off and is not qualified.
- The lease itself: acquiring, renewing and releasing, and what the console shows, belong to the runtime.
- The demonstrated digest of the boundary block in `test_host_safe_attach_static.py`, which is recorded only after a
  host with the boundary has run a real cycle.
