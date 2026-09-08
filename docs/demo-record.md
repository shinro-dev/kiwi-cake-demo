<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# The demo record: what was measured on the board

This page is the public derivative of the evidence record behind this
release: two demo segments run by the operator on a LeKiwi's Raspberry Pi 5
on 2026-09-07, captured on the board. The original record reproduces every
capture byte for byte. This page quotes the statements of record verbatim,
summarises each phase and beat, and shows short excerpts of the captures
with the operator's home directory replaced by `/home/<user>/` and device
identifiers replaced by placeholders. Every excerpt is labelled as such, and
every place where an internal cross-reference or a sentence about work
outside this release was removed from a quoted statement is marked `[...]`. No
duration, timestamp or process id in a capture is restated in prose: this
page names what a capture shows, never how long it took.

The physical preconditions the operator held for segment 2 are the ones in
`SAFETY.md`.

## Segment 1: the stub-bundle integrity segment (torque-free)

The child throughout this segment was the harmless line-printing stub that
`demo-plan` writes out. No device was named or opened.

### Phase A: fresh start and three queries

The resident started against the stub bundle with `signature_policy
require` and one trusted key, reported a fresh install with no committed
record to recover, activated the Plan with one slot and one declared
resource, and answered `get-status`, `list-slots` and `read-flight`.

Excerpt, paths redacted:

```
cake-resident: signature policy require, trusted keys loaded 1, trusted key ids 579c9ac8...
cake-resident: journal at start format version 1, records read 0, torn tail none, journal created, next sequence 1
cake-resident: durable start fresh install, no committed record to recover
cake-resident: plan active bindings=0 digest=f46a62a1... epoch=2 resources=1 slots=1
cake-resident: ready pid=<pid>
=== child process (expected: the harmless stub) ===
<pid> /bin/sh /home/<user>/cake-demo/bundle-stub/child/stub-child.sh 3 0 900 cake-demo-stub
=== admin-probe list-slots ===
status ok
...
slot capsule=96790ae1... config=b2b701cf... generation=1 instance=... name=supervisor.lekiwi state=ACTIVE uuid=...
```

The flight ring after the start held the fourteen-record startup sequence
every fresh activation in the record produces: session start, the five
lifecycle events of the supervisor instance, the epoch and binding
publications, the Plan activation, the supervisor's child-started and
health-changed events, then one admin-connection-accepted record per query.
`docs/event-codes.md` decodes them.

### Phase B: the tampered-capsule refusal

One byte of the live store object was flipped (its digest no longer
matched its name). A fresh start was attempted against it. The resident
refused before any Plan became active, naming the content identity, and
the unit ended with exit status 1. Restoring the object cleared the refusal.

Excerpt:

```
=== FULL STDERR (expect the refusal naming content identity) ===
cake-resident: refused: plan store-object-mismatch: the Plan's content-addressed store is unavailable: the object under content identity 96790ae1...febda7ed does not match its own bytes; refused
=== child process (expect none) ===
```

### Phase C: kill -9 recovery of the resident

With the resident running under a transient systemd user unit, the resident
was killed with SIGKILL. The unit relaunched it; the new resident reported
`plan active` with the same digest, a different session identity, and a
fresh stub child with a new pid. The captured `read-flight` after the
relaunch shows the startup sequence again, with the child-started event
carrying restart ordinal 0 (a fresh module, with no memory of the killed
one's restart count).

Excerpt:

```
=== BEFORE: resident pid=<a> ===
=== kill -9 the resident (pid <a>) ===
=== AFTER: resident pid=<b> (was <a>), relaunched=1 ===
=== AFTER: child = <c> /bin/sh /home/<user>/cake-demo/bundle-stub/child/stub-child.sh 3 0 900 cake-demo-stub (was: <d> ...) ===
```

### Phase D: clean stop

`systemctl --user stop` ran the resident's own clean-shutdown path: the
resident printed `cake-resident: stopped`, the admin socket was gone, and no
resident or child process remained.

### Phase E: the stop window

A tight poll of `read-flight` during the stop found the socket already
closed on the first poll (`Connection reset by peer`). Once the stop
sequence has completed the admin socket answers no further request. This
phase adds no capability beyond phase D and carries no statement row.

### Segment 1 statements of record (verbatim, unabridged)

| Capability |
| --- |
| The intact start against the stub bundle reaches plan active and answers the admin socket's three read queries (get-status, list-slots, read-flight). |
| A one-byte tampered store object refuses the start, naming the content identity, and reaches no active Plan; restoring the object clears the refusal. |
| kill -9 of the child is followed by a supervisor restart under the next restart ordinal, and kill -9 of the resident is followed by a relaunch reporting a session identity distinct from the killed process's. |
| The clean stop path (systemctl --user stop) prints the resident's own stopped line and removes the admin socket, leaving no live resident or child. |

## Segment 2: the real-bundle live segment

The child was the operator's own wrapper script, which execs the LeKiwi
host program against the physical robot, under the preconditions in
`SAFETY.md`.

### Beat 1, first attempt: failed at the host's own argument parser

The resident reached `plan active` and spawned the child, but the host
never listened on its ports. The flight ring showed five spawn-and-exit
cycles (child exited, output accounted, safe-stop ran, child started with
restart ordinals one through five), then `EVT_SUPERVISOR_RESTART_BOUND_REACHED`
and `EVT_SUPERVISOR_HEALTH_CHANGED`: the supervisor reached its restart bound
because the child kept exiting immediately. That is the supervisor doing its
job against a child that could not stay up.

The cause was in the wrapper, not in Cake: the supervisor spawns any child
with four positional arguments of its own, and the first wrapper forwarded
them into the LeRobot host's argument parser, which rejected them as
unrecognized before the robot was ever opened. No device was opened during
this attempt. The wrapper template shipped in `bin/templates/` carries the
fix: it ignores those positionals and hardcodes the real flags.

### Beat 1, retry: clean supervised start

After the fix, the resident spawned the host, which listened on both ports.

Excerpt, identifiers and a timing annotation redacted:

```
=== waiting for the child (LeRobot host) to listen on 5555/5556 ===
=== listening=1 ===
LISTEN 0      100          0.0.0.0:5556      0.0.0.0:*
LISTEN 0      100          0.0.0.0:5555      0.0.0.0:*
=== child process ===
<pid> /home/<user>/.../python3 /home/<user>/cake-demo/bin/<lekiwi host entry point> --robot.id=<robot-id> --robot.port=/dev/serial/by-id/<serial-by-id> --robot.max_relative_target=15.0 --robot.disable_torque_on_disconnect=true --robot.cameras={...} --host.connection_time_s=86400
```

### Beat 2: teleoperation through Cake

Operator-confirmed, not measured by the capture: "teleop live, follower
mirrors the leader, through the Cake-supervised host." It carries no
statement row.

### Beat 3: supervised child restart after SIGTERM

SIGTERM was sent to the host process only; the resident was never touched.
Within the polling loop a new host appeared with a new pid and was listening
again. The flight ring decodes to: child exited with a status word whose
signal is 15 (SIGTERM, exactly the signal sent); output accounted with the
live teleoperation session's lines and bytes; the safe-stop command ran;
child started with restart ordinal 1, spawn count 2. The session identity
was unchanged from Beat 1, correctly distinguishing a child-only restart from
a resident restart.

### Beat 4: full crash recovery, the safety beat

The resident was killed with SIGKILL while supervising the live host. The
host was gone within the operator's first check (no orphan; the parent-death
signal held). A relaunched resident was listening on both ports again.

Excerpt, the operator's before-and-after summary:

```
BEFORE/AFTER summary:
  resident pid   <a> -> <b>  (new)
  child pid      <c> -> <d>  (new)
  session_uuid   01a07ae74c0a... -> 01a07ae82275...  (fresh)
  plan_digest, config_identity, build_identity, target_profile_digest: IDENTICAL before/after
  slot instance=..., state=ACTIVE: unchanged
  EVT_SUPERVISOR_CHILD_STARTED restart ordinal: 0 again (fresh module, no
    memory of the killed one's restart count)

VERDICT (operator's words, recorded verbatim):
"Cake-level recovery PASS ([...] byte-identical declared authority),
actuator-safe-recovery NOT DEMONSTRATED ([...] the LeRobot child re-arms
unconditionally on connect)."
```

The two elisions in the verdict remove references to internal ledger rows.

The honest edge, stated plainly: Cake's own recovery is byte-identical
declared authority (the four identities above), but actuator-safe recovery
is not demonstrated, because the actuator is owned by the stock LeRobot
host, whose own connect step unconditionally re-enables torque, recovery
included. Cake's admin protocol carries no torque or actuator concept for
this demo to gate.

A note on the unit: the transient unit the earlier beats ran under had
`Restart=no`, which could not be changed live, so Beat 4 used a persistently
installed unit with `Restart=always`, `RestartSec=2`. The unit template
shipped here carries `Restart=on-abnormal`, `RestartSec=2`; a SIGKILL is an
abnormal exit under either policy, so the relaunch Beat 4 shows is
consistent with both.

### Beat 5: the clean stop, and a runbook finding

SIGINT to the host alone did not stop the demo: the old host exited cleanly
and the supervisor immediately spawned a new one, because it does not know
an operator meant that signal as a final stop. The actual clean stop was
`systemctl --user stop` of the resident: afterwards there was no resident, no
host process, no listener on either port, no admin socket, and the devices
were free.

The resident's own graceful shutdown printed `stopped` before systemd's
cgroup teardown killed one lingering ZeroMQ background thread of the host;
that is cosmetic, and the devices were confirmed free immediately after.
No wheel command was ever issued by anything the operator ran in segment 2.

### Segment 2 statements of record (verbatim where unmarked; `[...]` marks a removed internal cross-reference or a removed sentence about work outside this release)

| Capability |
| --- |
| Beat 1's retry reaches a supervised start of the real child, with the LeRobot host listening on both wire ports under the supervisor's own spawn and accounting. |
| Beat 3's SIGTERM to the child alone is followed by the configured post-exit safe-stop command running once (EVT_SUPERVISOR_SAFE_STOP_RAN) and a fresh child spawn, with the resident's own pid and session identity unchanged, decoded at [...]. |
| Beat 4's kill -9 of the resident is followed by a relaunch reporting a fresh session identity while the Plan digest, config identity, build identity and target-profile digest stay byte identical, and the killed resident's child dies with it, no orphan. |
| The actuator re-arms after Beat 4's crash recovery because the LeRobot child unconditionally re-enables torque on connect; Cake's admin protocol carries no torque concept, so this boundary is not closed by this demo [...]. |
| Beat 5 shows that SIGINT to the child alone does not stop the demo: the supervisor's own restart policy immediately respawns a new child, so the runbook-correct stop is to stop the resident, which quiesces the child as part of its own shutdown. |

## Live replacement

Neither segment performs a live replacement of a running module. The
record's last two rows state that the reclamation proof behind that beat
holds at both build profiles on the development host, and that the ordering
probe which gates the beat on the board has not run on this board. The
public demo therefore does not include it.
