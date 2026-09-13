<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Presenting the demo: explain one host failure

Start with the question a LeKiwi developer can answer: when the host
exits, what can we see about the exit, the restart and the configuration
that starts again? First show that sequence with a harmless stub, then
with the real host if the bench is prepared. Give the audience the
[diagnostic guide](diagnosing-a-run.md) so they can inspect their own run.

Each block below links an assertion to its row in the [claims map](claims.md),
the runner step, a literal output fragment and the interpretation limit.
The [systemd comparison](why-cake-under-lerobot.md) explains which parts
already exist in a conventional service. Do not present process recovery
as proof of recording continuity or actuator-safe recovery.

How to run it: on the Pi, `bin/demo-segment1.sh --pause` waits for Enter
after each step, so you can talk between steps (Ctrl-C at a pause stops
everything). Segment 2 pauses on its own between beats. Keep
`docs/claims.md` open beside you, and `docs/stopping-and-cleanup.md` within
reach for the end. Output lines below are quoted as the runner prints them;
`<...>` marks a value that varies.

## Segment 1 on the Pi: torque-free, the audience watches the terminal

### 1. Signed-package admission with a key generated here

Proves: a signed package is built on this machine with a key generated on
it, and a Plan names the package by its content identity (`docs/claims.md`
row 1).
Shown by: steps 2 to 4 of `bin/demo-segment1.sh` (keys, build, verify).
Point at: `trusted key (yours): <hex>` and `control key (never trusted): <hex>`
(step 2); `capsule content identity <hex>` (step 3); `signed capsule under
require, your key trusted: <verdict>`, whose verdict reads `admitted`
(step 4).
Caveat: the demo key is generated on this machine on the first run and
reused after that: the runner prints `generated a new Ed25519 demo key at
<path> (mode 600)` the first time and `using the existing demo key at
<path>` on every later run. Explain whether this run generated or reused
the key. The control key is generated fresh on every run, only to be
refused in the next steps.

### 2. Tamper refusal, then recovery

Proves: when one byte of the stored package is changed, the resident
refuses to start and names the content identity; restoring the byte
clears the refusal (`docs/claims.md` row 2).
Shown by: step 7 (tamper), then step 8 (intact start).
Point at: `flipped one byte of the stored object at offset <n>; starting
the resident against it`; then `refused (exit <n>): <the resident's refusal
line>`, which names `store-object-mismatch` and the content identity from
step 3; then `restored the original byte`; then, in step 8, the resident's
own `cake-resident: plan active <...>` line.
Caveat: the changed object is the supervisor capsule, not the external
LeRobot program or Python environment. The refusal happens before any
Plan is active; its identity is the one printed in step 3.

### 3. Signature verification

Proves: an unsigned copy of the package is refused, and a copy signed with
a key the resident does not trust is refused (`docs/claims.md` row 3).
Shown by: step 4, `demo-plan verify` run three times as a separate process
reading the files back.
Point at: `unsigned twin under require: <verdict>`, whose verdict reads
`refused signature_missing`; `signed capsule with only the control key
trusted: <verdict>`, whose verdict reads `refused signature_key_id_not_trusted`.
Caveat: row 3 is measured on the development host's gate; the demo's own
verify step repeats it on this board, which is what the audience sees.

### 4. Live telemetry

Proves: the resident answers three read queries over a local admin socket,
printed as a live table with every event decoded by name (`docs/claims.md`
row 5).
Shown by: step 9, which runs `bin/telemetry.sh` once; you can run it again
yourself at any point while the resident is up.
Point at: from get-status, `build_identity <hex>`, `session_uuid <hex>`,
`plan_digest <hex>`; from list-slots, `slot name=supervisor.lekiwi state=ACTIVE`;
from read-flight, `record event=<EVT_NAME> arg0=<n> arg1=<n> sequence=<n>`,
where the event name is decoded through `docs/event-codes.md` (the startup
sequence reads session start, the supervisor's lifecycle events, the Plan
activation, then `EVT_SUPERVISOR_CHILD_STARTED`); from get-health,
`health_surface served` with its rows, or `health_surface not served`;
and the summary line `poll queries answered 3 of 3`.
Caveat: get-health is printed when the resident serves it and reported as
not served otherwise; it is not one of the three queries the claim counts.
An ACTIVE supervisor slot and a `3 of 3` summary do not establish host
readiness, working cameras or motor state. Inspect the actual rows.
Every poll opens an admin connection and mints one
`EVT_ADMIN_CONNECTION_ACCEPTED` record, and a tight polling loop pushes
older records out of the bounded ring (`docs/event-codes.md`), so poll
slowly when you want the audience to see a particular event.

### 5. Child kill and supervised restart

Proves: when the child is killed, the supervisor spawns a fresh child under
the next restart ordinal, with the exit and the restart in the event trace
(`docs/claims.md` row 6).
Shown by: step 10.
Point at: `sent SIGKILL to the stub child, pid <n>`; then in the flight
ring `flight: EVT_SUPERVISOR_CHILD_EXITED <status> (signal 9)` (the status
word's low bits are the signal) and `flight: EVT_SUPERVISOR_CHILD_STARTED
<ordinal>` with ordinal 1 and spawn count 2; then `new child pid(s): <n>`,
a different pid from the one killed.
Caveat: the resident itself is untouched here; say so, because the next
step is the opposite.

### 6. Resident kill with SIGKILL, and relaunch

Proves: when the resident is killed with SIGKILL it comes back from the
same configuration with a fresh session identity while the Plan digest,
the configuration identity, the build identity and the target-profile
digest stay identical, and the killed resident's child does not survive it
(`docs/claims.md` row 7).
Shown by: step 11.
Point at: `sent SIGKILL to the resident, pid <n>; it ended with status <n>`
(137, which is 128 plus 9); `no child of the killed resident survives (five
consecutive clean scans)`; then the BEFORE/AFTER block:
`session_uuid           <a> -> <b> (fresh)` and
`plan_digest            <hex> (identical)`, with the same `(identical)`
on the configuration identity, the build identity and the target-profile
digest lines.
Caveat: in segment 1 the relaunch is performed by the runner from the same
configuration file, exactly as the development-host gate does; in segment
2 a systemd user unit does it, as on the tested board (`docs/claims.md`,
"Differences"). The relaunched resident is a new process, not a resumed
one: module memory does not survive it ([Limitations](../LIMITATIONS.md)).
The matching identities establish fresh activation of the same declared
configuration, not recovery of a committed transaction or application state.

### 7. The clean stop

Proves: the resident stops cleanly on request, closing the socket and the
child with it (`docs/claims.md` row 8).
Shown by: step 12.
Point at: `the resident ended with status <n> after SIGTERM` (0);
`cake-resident: stopped; socket removed; no child`; and the runner's own
count `safe-stop command ran <n> time(s)`, which is the stub logger the
runner configured, one line per observed child exit.
Caveat: the safe-stop count here is the runner's print for its stub
logger; the measured claim about the safe-stop command belongs to segment
2, Beat 3, below. End segment 1 on `KIWI-CAKE SEGMENT 1: PASS` and say what
it did not touch: no device was named or opened.

## Segment 2 on the live robot: the audience watches the robot

Before this part: `SAFETY.md` preconditions, the acknowledgment typed in
terminal A, terminal B ready on the laptop, and a third terminal (an ssh
session to the Pi) for telemetry. The alternation of the two terminals is
in `docs/reproduce-end-to-end.md`, "The beats, in two terminals".

### 8. Beat 1: the real host started under Cake

Proves: the resident starts with the package admitted and supervises a
child, which in segment 2 is your own LeRobot host (`docs/claims.md` row 4).
Shown by: `bin/demo-segment2.sh`, Enter at Beat 1.
Point at: `resident MainPID <n>: plan active, admin socket answering`; then
`host pid <n> listening on <ports>` (5555 and 5556).
Caveat: treat a successful host connect as a torque-on event. Observe the
supported arm and explain that LeRobot enables torque; the listener line
itself is not a torque measurement.

### 9. Beat 2: teleoperation through the supervised host

Proves: nothing by itself. Beat 2 is an operator observation, carrying no
claims row. The original board record reports operator-confirmed
teleoperation; the v0.1.3 record explicitly skipped it.
Shown by: terminal B running `bin/laptop/teleop.py`, then `CONFIRMED` typed
in terminal A.
Point at: `teleop: connected; the follower mirrors the leader; Ctrl-C stops
the client` on the laptop, and in terminal A, after you type it,
`teleoperation through the supervised host: operator-confirmed`.
Caveat: say plainly that the mirroring is what everyone can see, and that
the runner records your word and measures nothing here. The laptop runs
LeRobot's own LeKiwiClient class through `bin/laptop/teleop.py`, a small
script derived from LeRobot's example, because `lerobot-teleoperate` has no
LeKiwi client type; it speaks the stock protocol on the stock ports, and
nothing on the laptop knows Cake exists. If you type SKIP instead of
CONFIRMED, terminal A prints `KIWI-CAKE beat 4 teleop: SKIPPED
(operator)` and the run's last line reads `KIWI-CAKE SEGMENT 2: DONE
(teleop SKIPPED by the operator)`; the beats that follow run the same
either way.

### 10. Beat 3: the host restarted under the operator's eyes

Proves: when the child is killed, the supervisor spawns a fresh child under
the next restart ordinal with the exit and the restart in the event trace
(`docs/claims.md` row 6), and the configured safe-stop command runs once
per observed child exit (`docs/claims.md` row 9), both measured on the
board at this beat.
Shown by: Enter at Beat 3 in terminal A (SIGTERM to the host only).
Point at: `host exited with signal 15 (sequence <n>), safe-stop ran
(sequence <n>), fresh host pid <n> with restart ordinal <n>; resident pid
<n> and session unchanged`.
Caveat: if a laptop client was running, restart it after the new host is
ready; continuity is not demonstrated. The host can die without disabling
torque, and a successful reconnect enables it again. The safe-stop event
shows hook execution, not a motor action.

### 11. Beat 4: resident relaunch on the live robot

Proves: the resident killed with SIGKILL comes back with a fresh session
identity, the four declared identities byte-identical, and no orphan child
(`docs/claims.md` row 7), measured on the board.
Shown by: the acknowledgment typed again, then Enter at Beat 4 in terminal
A (SIGKILL to the resident itself).
Point at: `host <n> died with the resident (no orphan)`; then the
BEFORE/AFTER block, `session_uuid   <a> -> <b> (fresh)` and
`plan_digest, config_identity, build_identity, target_profile_digest: IDENTICAL`;
then the runner's own verdict line, quoted exactly: `Cake-level recovery
held; actuator-safe recovery is not something this demo provides
(LIMITATIONS.md).` That line closes the beat.
Caveat: this is a new activation from the configured Plan. The v0.1.3
journal reports no committed record to recover. The next block explains
the separate hardware-state boundary.

### 12. The honest edge: the actuators re-arm on reconnect

Proves: actuator-safe recovery is not demonstrated; the LeRobot host
re-enables torque on every connect, and Cake has no torque concept to
override it (`docs/claims.md` row 10).
Shown by: the last line of the Beat 4 block, and the arm itself.
Point at: `host           listening again on <ports>, TORQUE ENABLED BY THE
HOST`.
Caveat: the four declared identities match after relaunch; they do not
describe motor state. LeRobot enables torque on connect. No capture in
the v0.1.3 run measures torque, and the robot must remain on its stand.

### 13. The stop

Proves: the resident stops cleanly on request, closing the socket and the
child with it (`docs/claims.md` row 8).
Shown by: terminal B parks the follower low by moving the leader and exits,
then Enter at STOP in terminal A.
Point at: `stopped: no resident, no host, no socket, no listener`.
Caveat: a completed LeRobot disconnect is expected to release torque;
an error or forced termination can interrupt it. Follow
[Stopping and cleanup](stopping-and-cleanup.md), inspect shutdown errors
and check the physical result. Process absence alone is not torque readback.

## Telemetry live during teleoperation

From a third terminal, an ssh session to the Pi, while the follower
mirrors the leader:

```
bin/telemetry.sh --rounds 30 --interval-seconds 2 --flight-rows 6
```

The socket defaults to segment 2's. Point at `slot name=supervisor.lekiwi
state=ACTIVE` during Beat 2, and after Beat 3 at the newest flight rows:
`EVT_SUPERVISOR_CHILD_EXITED`, `EVT_SUPERVISOR_SAFE_STOP_RAN`,
`EVT_SUPERVISOR_CHILD_STARTED`, decoded by name through
`docs/event-codes.md`. Two seconds between polls is deliberate: each poll
mints an `EVT_ADMIN_CONNECTION_ACCEPTED` record, and a tight loop pushes
the records you want to show out of the ring.

## What the audience watches on the robot at each beat

- Beat 1: observe the supported arm as the host connects and enables torque.
- Beat 2: the follower mirrors the leader. This is the only beat in which
  motion is expected, and it is the operator's motion.
- Beats 3 and 4: watch for any movement during host or resident restart.
  A successful reconnect enables torque; the event trace cannot tell you
  whether the arm held position or the motors became disarmed.
- The stop: check the supported arm's physical state and the software
  cleanup output. Report an observation as an observation, not a torque
  measurement.

Any motion nobody commanded is not part of the show: `SAFETY.md` makes it a
finding, and the presenter stops the resident and says what was seen.
