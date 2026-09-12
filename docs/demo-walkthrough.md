<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Presenting the demo: one capability at a time

This page is for a presenter narrating the demo to an audience. The
runners print everything on their own; this page says, for each
capability in order, what to say it proves, which command or runner step
shows it, the exact output line to point at, and the honest caveat where
one exists. Every "proves" sentence is one of the rows of `docs/claims.md`,
cited by number; where something is the audience's observation rather than
a measured claim, this page says so.

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
Caveat: none. Say that the key never existed before this run and that the
control key is generated only to be refused in the next steps.

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
Caveat: none. Point out that the refusal happens before any Plan is active
and that the identity in the refusal is the one printed in step 3.

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
one: module memory does not survive it (`LIMITATIONS.md`).

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
Caveat: torque is on from this line. Have the audience watch the arm
stiffen, and say that it is the stock host enabling torque on connect, not
Cake.

### 9. Beat 2: teleoperation through the supervised host

Proves: nothing by itself. Beat 2 is the audience's observation, recorded
in `docs/demo-record.md` as operator-confirmed and carrying no claims row.
Shown by: terminal B running `bin/laptop/teleop.py`, then `CONFIRMED` typed
in terminal A.
Point at: `teleop: connected; the follower mirrors the leader; Ctrl-C stops
the client` on the laptop, and in terminal A, after you type it,
`teleoperation through the supervised host: operator-confirmed`.
Caveat: say plainly that the mirroring is what everyone can see, that the
laptop side is the stock LeRobot client, and that the runner records your
word and measures nothing here.

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
Caveat: the laptop client loses its connection when the host's sockets
close, and is simply run again once this line appears. The signalled host
left torque as it was, and the fresh host enables it again on connect.

### 11. Beat 4: crash recovery on the live robot

Proves: the resident killed with SIGKILL comes back with a fresh session
identity, the four declared identities byte-identical, and no orphan child
(`docs/claims.md` row 7), measured on the board.
Shown by: the acknowledgment typed again, then Enter at Beat 4 in terminal
A (SIGKILL to the resident itself).
Point at: `host <n> died with the resident (no orphan)`; then the
BEFORE/AFTER block, `session_uuid   <a> -> <b> (fresh)` and
`plan_digest, config_identity, build_identity, target_profile_digest: IDENTICAL`;
then the runner's own verdict line `Cake-level recovery held; actuator-safe
recovery is not something this demo provides (LIMITATIONS.md).`.
Caveat: the next block.

### 12. The honest edge: the actuators re-arm on reconnect

Proves: actuator-safe recovery is not demonstrated; the LeRobot host
re-enables torque on every connect, and Cake has no torque concept to
override it (`docs/claims.md` row 10).
Shown by: the last line of the Beat 4 block, and the arm itself.
Point at: `host           listening again on <ports>, TORQUE ENABLED BY THE
HOST`.
Caveat: this is the edge to state, not to soften. Cake restored its
declared state byte for byte; the servos were re-armed by the host. Say
that the demo reports it rather than claiming a guarantee it does not
have, and that this is why the robot is on a stand.

### 13. The stop

Proves: the resident stops cleanly on request, closing the socket and the
child with it (`docs/claims.md` row 8).
Shown by: terminal B parks the follower low by moving the leader and exits,
then Enter at STOP in terminal A.
Point at: `stopped: no resident, no host, no socket, no listener`.
Caveat: the host's clean exit releases torque, so the arm goes limp on its
support; `docs/stopping-and-cleanup.md` is the ordered procedure and the
checks that prove nothing is left.

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

- Beat 1: the arm stiffens, torque on. Nothing moves.
- Beat 2: the follower mirrors the leader. This is the only beat in which
  motion is expected, and it is the operator's motion.
- Beat 3: the arm holds its pose while the host is replaced, then
  re-stiffens as the fresh host enables torque again. Nothing is expected
  to move; the record measured torque being re-enabled, not motion.
- Beat 4: the same as Beat 3, after the resident itself has been killed and
  relaunched; the laptop client is run again afterwards.
- The stop: the arm goes limp on its support, which is why it was parked
  first.

Any motion nobody commanded is not part of the show: `SAFETY.md` makes it a
finding, and the presenter stops the resident and says what was seen.
