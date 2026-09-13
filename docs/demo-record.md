<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# The demo record: what was measured on the board

## How to read this evidence

| Record | Scope | Boundary |
| --- | --- | --- |
| 2026-09-07 original demonstration | Stub integrity/lifecycle checks and a real-host run | Teleoperation was operator-confirmed; not all statements came from a board capture |
| [2026-09-13 v0.1.3 run](#the-v013-run-on-the-tested-board-2026-09-13) | The released segment 2 runner, host/resident failures and stop behavior | Idle host, teleoperation skipped; no instrumented torque measurement |

The v0.1.3 record was appended after the release tag. Historical captures
and statements below retain their original wording. In particular,
matching declared identities after relaunch is evidence of fresh Plan
activation from the same configuration, not proof of restored application
state. [Claims and evidence](claims.md) maps each assertion to its source;
[Diagnosing a run](diagnosing-a-run.md) explains how to inspect your own run.

## Source of the records

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

The last section, "The v0.1.3 run on the tested board", is the one part of
this page not derived from that record: one run of this release's own
segment 2 runner on the tested board on 2026-09-13, recorded from the run
directory the runner wrote, from the unit's lines in the board's system
journal, and from two statements by the operator, each marked as such.

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

The four declared identities read back byte-identical, but actuator-safe
recovery is not demonstrated, because the actuator is owned by the stock LeRobot
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

## The v0.1.3 run on the tested board, 2026-09-13

One run of `bin/demo-segment2.sh` from the v0.1.3 tarball (its
`MANIFEST.txt` names version v0.1.3 and source revision 79b52d9), on a
tested Raspberry Pi 5 whose preflight matched the tested-board record,
under the unit template this release ships. The sources of this section
are the run directory the runner wrote (`transcript.txt`, `safe-stop.log`,
and the host's captured output, `supervisor.log` in the module's data
directory under the run's state root), the unit's lines in the board's
system journal, read with the four matches of the command in
`docs/stopping-and-cleanup.md` in journalctl's default output format with
`--utc` (on this board `journalctl --user -u` answers `No journal files
were found`), and two statements by the operator, marked as such where
they appear. The preconditions were the ones in `SAFETY.md`; the operator
typed the acknowledgment before Beat 1 and again before Beat 4. The
excerpts below replace the operator's home directory, the board's
hostname, process ids and systemd's CPU-time figures with placeholders,
shorten digests, key ids and session identities, and mark whole lines
left out of a transcript excerpt with a line reading `...`; nothing else
in them is altered.

The host was `bin/pi/lekiwi_host_noninteractive.py`: the stock host's
main at lerobot b4e2d0b, unmodified, with its calibration prompt replaced
(`LIMITATIONS.md`). The board's lerobot checkout also carried the clamp
fix `LIMITATIONS.md` names and a local patch to the robot class that
makes the arm servos' tracking gains configuration, with the stock values
as defaults; neither touches the host's disconnect or its handling of
signals.

What this run is not: a teleoperation demonstration. At Beat 2 the
operator typed SKIP. Operator-stated, and shown by the captures only for
the last host: no teleoperation client was connected at any point in the
run, no command reached any host, and the follower never mirrored
anything because nothing drove it. The beats after it exercise
supervision and recovery of a host that was connected to the robot and
idle. The run performs no live replacement either.

The runner's own beat counter counts the preflight and the unit as its
beats 1 and 2, so its `KIWI-CAKE beat <n>` lines run two ahead of the
runbook's beat names used below.

### Beat 1: supervised start of the host

The preflight ended in the accepted shape, five checks green and the
observer check refused; the runner installed the unit and printed its
stop settings; on Enter the resident reached `plan active`, the host
listened on both ports with the listeners attributed to its pid, and the
first poll answered all three queries, with one slot ACTIVE and its
health surface served (`docs/claims.md` rows 4 and 5).

Excerpt from the transcript:

```
preflight accepted this board (five-green-observer-refused)
KIWI-CAKE beat 1 preflight: ok
installed /home/<user>/.config/systemd/user/kiwi-cake-demo.service (Restart=on-abnormal, RestartSec=2, KillMode=mixed, KillSignal=SIGTERM, TimeoutStopSec=60)
KIWI-CAKE beat 2 unit: ok
...
resident MainPID <a>: plan active, admin socket answering
host pid <c> listening on 5555 5556
listeners on 5555 5556 attributed to host pid <c>
...
poll queries answered 3 of 3
poll tables printed 1
KIWI-CAKE beat 3 start: ok
```

### Beat 2: teleoperation, skipped

Excerpt from the transcript:

```
> teleoperation: skipped by the operator
KIWI-CAKE beat 4 teleop: SKIPPED (operator)
```

Nothing was measured and nothing was observed at this beat. It carries
no statement row, as it carries none in the 2026-09-07 record either.

### Beat 3: SIGTERM to the host only

The runner sent SIGTERM to the host alone. From the flight ring it
decoded the host's exit carrying signal 15, the safe-stop run, and a
fresh host under restart ordinal 1, with the resident's pid and the
session identity unchanged (`docs/claims.md` rows 6 and 9). The
configured safe-stop command was the shipped template, which appends one
line to `safe-stop.log`; the log gained its first line here (it is quoted
in full under the stop, below).

Excerpt from the transcript:

```
sent SIGTERM to host pid <c> (flight baseline: sequence 17)
listeners on 5555 5556 attributed to host pid <d>
host exited with signal 15 (sequence 18), safe-stop ran (sequence 20), fresh host pid <d> with restart ordinal 1; resident pid <a> and session unchanged
KIWI-CAKE beat 5 child-restart: ok
```

The unit's journal has no line between the resident's `ready` line and
Beat 4: the host's exit and respawn are the supervisor's business inside
the resident, and neither the resident nor the manager logged them.

### Beat 4: SIGKILL to the resident, the crash path

The runner sent SIGKILL to the resident. The host was gone within the
runner's bounded wait, no orphan; systemd relaunched the resident; the
relaunched resident reported a fresh session identity with the Plan
digest, config identity, build identity and target-profile digest
identical; and a fresh host listened on both ports again
(`docs/claims.md` row 7).

Excerpt from the transcript:

```
sent SIGKILL to resident pid <a> (host pid <d>)
host <d> died with the resident (no orphan)
listeners on 5555 5556 attributed to host pid <e>
BEFORE/AFTER:
  resident pid   <a> -> <b> (new)
  host pid       <d> -> <e> (new)
  session_uuid   01a09af76949... -> 01a09af89a70... (fresh)
  plan_digest, config_identity, build_identity, target_profile_digest: IDENTICAL
  host           listening again on 5555 5556, TORQUE ENABLED BY THE HOST
Cake-level recovery held; actuator-safe recovery is not something this demo provides (LIMITATIONS.md).
KIWI-CAKE beat 6 crash-recovery: ok
```

The journal shows how the host ended and how the resident came back:

```
Sep 13 13:32:56 <board> systemd[<m>]: kiwi-cake-demo.service: Main process exited, code=killed, status=9/KILL
Sep 13 13:32:56 <board> systemd[<m>]: kiwi-cake-demo.service: Killing process <d> (python3) with signal SIGKILL.
Sep 13 13:32:56 <board> systemd[<m>]: kiwi-cake-demo.service: Killing process <d1> (python3) with signal SIGKILL.
Sep 13 13:32:56 <board> systemd[<m>]: kiwi-cake-demo.service: Killing process <d2> (python3) with signal SIGKILL.
Sep 13 13:32:56 <board> systemd[<m>]: kiwi-cake-demo.service: Killing process <d3> (python3) with signal SIGKILL.
Sep 13 13:32:56 <board> systemd[<m>]: kiwi-cake-demo.service: Failed with result 'signal'.
Sep 13 13:32:56 <board> systemd[<m>]: kiwi-cake-demo.service: Consumed <figure> CPU time.
Sep 13 13:32:58 <board> systemd[<m>]: kiwi-cake-demo.service: Scheduled restart job, restart counter is at 1.
Sep 13 13:32:58 <board> systemd[<m>]: Started kiwi-cake-demo.service - kiwi-cake-demo: Cake resident supervising the LeKiwi host (user unit).
Sep 13 13:32:58 <board> cake-resident[<b>]: cake-resident: signature policy require, trusted keys loaded 1, trusted key ids 8ec06ce2...
Sep 13 13:32:58 <board> cake-resident[<b>]: cake-resident: journal at start format version 1, records read 0, torn tail none, journal read, next sequence 1
Sep 13 13:32:58 <board> cake-resident[<b>]: cake-resident: durable start fresh install, no committed record to recover
Sep 13 13:32:58 <board> cake-resident[<b>]: cake-resident: plan active bindings=0 digest=b4ca0fda... epoch=2 resources=1 slots=1
Sep 13 13:32:58 <board> cake-resident[<b>]: cake-resident: ready pid=<b>
```

The main process died of the signal, and the manager then sent SIGKILL
to what remained in the unit's control group: four entries named
`python3`, the host's pid among them (whether the other three were
threads of the host or processes of its own, the journal does not say).
This is `KillMode=mixed` doing the second half of its job. Its first
half, on an ordinary stop, is to signal the resident only; its second,
once the main process is gone for any reason, is to end by force whatever
is left. A resident killed with SIGKILL runs nothing and so cannot
quiesce its host. Whether the host was already dying of the parent-death
signal the 2026-09-07 record names for this beat, or ended on the
manager's signal, the journal does not say: it shows the manager's
SIGKILL reaching the host's pid, and the runner found no orphan. Either
way nothing of the host was left behind, which is what the second half
of `KillMode=mixed` is for. `KillMode=process` would leave that second
half out, and `tests/test-templates.sh` refuses it for exactly that
reason. The relaunched resident is a fresh activation: `records read 0`
and `durable start fresh install, no committed record to recover` on its
side, restart counter 1 on the manager's.

No safe-stop command ran at this beat: a killed resident observes no
exit, and the relaunched resident observed none at this beat.
`safe-stop.log` has no line between Beat 3's and the stop's.

Torque after this beat is the fresh host's doing on connect, as at every
connect, not Cake's (`docs/claims.md` row 10). The runner's `TORQUE
ENABLED BY THE HOST` is its own statement of that, printed at every run;
no capture measures torque, and this section carries no separate
observation of the arm at this beat.

### STOP: the clean stop under this release's unit

On Enter the runner ran `systemctl --user stop`. Afterwards there was no
resident, no host, no admin socket and no listener on either port
(`docs/claims.md` row 8).

Excerpt from the transcript:

```
stopped: no resident, no host, no socket, no listener
whether the motors are now unpowered is your host's own disconnect behaviour, not Cake's; check the robot
KIWI-CAKE beat 7 stop: ok
```

The journal's stop sequence, complete:

```
Sep 13 13:33:09 <board> systemd[<m>]: Stopping kiwi-cake-demo.service - kiwi-cake-demo: Cake resident supervising the LeKiwi host (user unit)...
Sep 13 13:33:09 <board> cake-resident[<b>]: cake-resident: shutdown drain barrier_violations=0 deadline_exhausted=0 destroyed_after_drain=0 destroyed_before_drain=0 drained=1 live=1 members=1 quiesce_calls=1 quiesced_or_inactive=1 required=1
Sep 13 13:33:09 <board> cake-resident[<b>]: cake-resident: stopped
Sep 13 13:33:09 <board> systemd[<m>]: Stopped kiwi-cake-demo.service - kiwi-cake-demo: Cake resident supervising the LeKiwi host (user unit).
Sep 13 13:33:09 <board> systemd[<m>]: kiwi-cake-demo.service: Consumed <figure> CPU time.
```

`Stopping`, the resident's own drain line with its deadline not exhausted
and the module drained, the resident's `stopped` line, `Stopped`, and no
`Killing process` line anywhere in the sequence: nothing of the host
outlived the resident's shutdown for the manager to end. Under the unit
of the 2026-09-07 record, segment 2's Beat 5 above records one such kill
after the `stopped` line, of a lingering ZeroMQ background thread; under
this release's unit there is none.

The last host's captured output ends with the two lines the host's main
prints on SIGINT: the line of its `KeyboardInterrupt` handler, then the
first line of its shutdown block, the one printed before its disconnect.
The watchdog line before them is the host's own reaction to receiving no
command from any client: a zero-velocity write to the base motors by the
host itself, which its disconnect repeats. No client issued a wheel
command.

```
WARNING:root:Command not received for more than 500 milliseconds. Stopping the base.
Keyboard interrupt received. Exiting...
Shutting down Lekiwi Host.
```

SIGINT is the stop signal the Plan configured for the child, and under
this unit only the resident sends it: the manager's stop signal is
SIGTERM, sent to the resident alone under `KillMode=mixed`, and the host's
main installs no signal handler, so a SIGTERM reaching it would have
ended it before either line was printed. The host heard from the
resident's shutdown and not from systemd. That is the change to the unit
in this release, observed on the real host; `docs/stopping-and-cleanup.md`,
"What was measured", has the torque-free measurement behind it.

`safe-stop.log` gained its second line during this stop: the resident
observed the host's exit and ran the configured command once, as it had
at Beat 3 and at no other time in the run (`docs/claims.md` row 9). The
log, complete:

```
safe-stop ran at 2026-09-13T13:32:18Z
safe-stop ran at 2026-09-13T13:33:09Z
```

Operator-confirmed, not measured by any capture and outside any claims
row: the arm went limp at the stop. Releasing torque is what the host's
disconnect does under `--robot.disable_torque_on_disconnect=true`, and
what a host ended by force does not do, so the disconnect ran at least
that far. Whether the host then exited on its own within the resident's
child deadline, or was ended by force after that, is not in the captures:
the flight record carrying the host's exit status at the stop was never
read, because the admin socket is gone once the stop has completed.

The limit of this observation, stated plainly: the host was connected
and idle. No client was attached at any point in the run (the operator's
statement, above), so this section shows the stop of an idle host, not
the stop of a host serving a client. The host's handler is the same code
whether or not a client is attached, and nothing in the unit or the
runner changes with one; the resident's own stop path is not readable
from this repository. That is what makes the stop independent of
teleoperation as a statement about the code; this run did not show it
with a client attached.

### v0.1.3 run statements of record

| Capability |
| --- |
| Beat 3's SIGTERM to the host alone was followed by the configured safe-stop command running once and a fresh host under restart ordinal 1, with the resident's pid and session identity unchanged (`docs/claims.md` rows 6 and 9). |
| Beat 4's SIGKILL of the resident was followed by the manager's SIGKILL signalled to what remained of the host in the unit's control group, no orphan, and a relaunch reporting a fresh session identity while the four declared identities stayed identical; the fresh host's torque state is the host's own and was not measured (`docs/claims.md` rows 7 and 10). |
| The stop under this release's unit ran the resident's own shutdown and nothing else: the journal carries the resident's drain and stopped lines and no `Killing process` line; the host acted on SIGINT, the stop signal the Plan configured, and reached the line it prints before its disconnect; the safe-stop command ran once; afterwards no resident, no host, no socket, no listener (`docs/claims.md` rows 8 and 9). Operator-confirmed, outside any row: the arm went limp. |
