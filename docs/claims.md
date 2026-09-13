<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Claims and their evidence

Use this map to distinguish observed demo behavior from implementation
details and from capabilities the demo does not establish. Each row cites
a statement from the [board record](demo-record.md), its appended
v0.1.3 run, or the development-host gate record. A successful run checks
the exercised sequence; it does not establish the same outcome for every
possible host or hardware failure.

"Board" means a Raspberry Pi 5 matching the tested-board record: the one
of the 2026-09-07 record and, where a row also cites the v0.1.3 run, the
one of 2026-09-13 (the last section of `docs/demo-record.md`). "Gate"
means the automated gate run on the development host on every build, with
a nonzero control beside every zero it reports.

| # | Claim as written in this repository | Evidence | Where measured |
| --- | --- | --- | --- |
| 1 | Builds a signed package on your machine, with a key you generate, and a Plan that names the package by its content identity. | record, segment 1, phase A: `plan active ... slots=1`; gate line `demo plan and capsule signed under require 1 of 1` | board and gate |
| 2 | Refuses to start when one byte of the stored package is changed, naming the content identity, and starts again when the byte is restored. | record, segment 1 statement: "A one-byte tampered store object refuses the start, naming the content identity, and reaches no active Plan; restoring the object clears the refusal."; gate line `tampered start refusals naming the content identity 1 of 1` | board and gate |
| 3 | Refuses an unsigned copy of the package and a copy signed with a key it does not trust. | gate lines `unsigned artifact refusals under require 1 of 1` and `untrusted key refusals under require 1 of 1` | gate only; the demo's verify step repeats it on your machine |
| 4 | Starts the resident with the package admitted, supervising a child; the stub in segment 1, your LeRobot host in segment 2. | record, segment 1 statement: "The intact start against the stub bundle reaches plan active ..."; segment 2 statement: "Beat 1's retry reaches a supervised start of the real child, with the LeRobot host listening on both wire ports under the supervisor's own spawn and accounting."; gate line `resident starts reaching plan active` | board and gate |
| 5 | Answers three read queries over a local admin socket, printed as a live table with every event decoded by name. | record, segment 1 statement: "... answers the admin socket's three read queries (get-status, list-slots, read-flight)."; gate line `queries answered over the socket 3 of 3` | board and gate |
| 6 | When the child is killed, spawns a fresh child under the next restart ordinal, with the exit and the restart in the event trace. | record, segment 1 statement: "kill -9 of the child is followed by a supervisor restart under the next restart ordinal ...", whose own evidence is the gate lines `child kills followed by a supervisor restart 1 of 1` and `child exit records carrying the kill signal 1 of 1` (the board's segment 1 captures contain no child kill); record, segment 2 statement on Beat 3 (SIGTERM to the host, safe-stop ran once, resident pid and session unchanged) | gate (SIGKILL) and board (SIGTERM, Beat 3) |
| 7 | When the resident is killed with SIGKILL, comes back from the same configuration with a fresh session identity while the Plan digest, configuration identity, build identity and target-profile digest stay identical; the killed resident's child does not survive it. | record, segment 1 statement: "... kill -9 of the resident is followed by a relaunch reporting a session identity distinct from the killed process's."; segment 2 statement on Beat 4: "... while the Plan digest, config identity, build identity and target-profile digest stay byte identical, and the killed resident's child dies with it, no orphan."; gate line `children surviving a resident kill 0` with `orphan control red 1 of 1` | board and gate |
| 8 | Stops cleanly on request, closing the socket and the child with it. | record, segment 1 statement: "The clean stop path (systemctl --user stop) prints the resident's own stopped line and removes the admin socket, leaving no live resident or child."; record, v0.1.3 run statement on the stop (the same stop under this release's unit: the host's own SIGINT lines, no `Killing process` line in the journal) | board |
| 9 | The configured safe-stop command runs once per observed child exit. | record, segment 2 statement on Beat 3: "... the configured post-exit safe-stop command running once (EVT_SUPERVISOR_SAFE_STOP_RAN) ..."; record, v0.1.3 run statements on Beat 3 and on the stop: `safe-stop.log` carries one line for each of the two host exits the resident observed and none for Beat 4, where the killed resident observed nothing | board; what the command did on 2026-09-07 is not recorded; on 2026-09-13 it was the shipped template, which appends one line to its log |
| 10 | Actuator-safe recovery is not demonstrated: the LeRobot host re-enables torque on every connect and Cake has no torque concept. | record, segment 2 statement: "The actuator re-arms after Beat 4's crash recovery because the LeRobot child unconditionally re-enables torque on connect; Cake's admin protocol carries no torque concept, so this boundary is not closed by this demo ..." and the operator's verdict; record, v0.1.3 run statement on Beat 4 (torque after the relaunch is the fresh host's own and was not measured) | board |
| 11 | SIGINT or SIGTERM to the child alone does not stop the demo; the supervisor respawns the child. | record, segment 2 statement on Beat 5 | board |
| 12 | The demo does not perform a live module replacement. | record statement: the ordering probe that gates that beat "has not run on this board" | board |

## Differences between the board record and this demo's runner

- On the board, the relaunch after SIGKILL of the resident was performed by a
  user-level systemd unit. In segment 1 of this demo the relaunch is
  performed by the runner itself, from the same configuration file, exactly
  as the development-host gate does. Segment 2 uses a systemd user unit as
  the board did.
- The board's child kill was Beat 3's SIGTERM to the host; segment 1 here
  uses SIGKILL of the stub as the development-host gate does. Both are
  covered by row 6.
- The operator's own run of the preflight on the board, recorded outside
  the evidence record, ended at the observer refusal with five green checks
  (`LIMITATIONS.md`); the runner requires the same shape.

## What is deliberately not claimed

- Row 1 signs the supervisor capsule. The Plan is unsigned, and external
  LeRobot code, dependencies, wrappers and calibration files are outside
  that capsule's signature.
- Row 5 reports admin query results. It does not establish motor state,
  working cameras or a usable end-to-end client connection.
- Row 7 compares declared identities across fresh activation. The
  v0.1.3 journal says there was no committed record to recover; durable
  application state and recording continuity are not demonstrated.
- Rows 8 and 9 describe observed process cleanup and hook execution.
  Neither establishes a motor stop, torque-off or a physical fail-safe
  property. The supplied hook only appends a log line.
- The v0.1.3 board run used an idle host with teleoperation skipped.
  Earlier operator-confirmed teleoperation is a separate observation.
- Configured waits, frame rates and captured timestamps are not qualified
  performance measurements. Automatic root-cause diagnosis and an
  automated support bundle are not supplied.

See [Architecture](architecture.md), [Limitations](../LIMITATIONS.md) and
[Diagnosing a run](diagnosing-a-run.md) for these boundaries and how to
interpret the available evidence.
