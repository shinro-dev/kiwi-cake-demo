<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Segment 1: the stub-child integrity, telemetry and recovery demo

Torque-free. No robot, no LeRobot install, nothing that opens a device. The
child the resident supervises is a small shell script that prints a few
lines, waits, and exits; `demo-plan` writes it out itself.

```
bin/demo-segment1.sh
```

Options: `--unsupported-target` (only honoured on a board that is not the
tested Pi 5; see `docs/targets.md`), `--keep` (leave the run directory's
bundle and store in place rather than removing them at the end), `--pause`
(wait for Enter between steps, to narrate the run; Ctrl-C at a pause stops
the resident and the stub and exits 130).

Each step prints one line of the form `KIWI-CAKE step N/13 <name>: ok` and
the transcript is kept under `state/runs/<id>/transcript.txt`. The run ends
with `KIWI-CAKE SEGMENT 1: PASS` or stops at the first step that does not
hold, with the reason.

## The thirteen steps and what each one shows

1. Doctor. Architecture, board model, page size, glibc, memfd exec policy,
   and one no-op run of each of the four shipped binaries. Prints the
   board's classification.
2. Key. Generates an Ed25519 key pair with `openssl` into `state/keys/`,
   mode 600, in the two-line format `demo-plan` reads, and a second key for
   the untrusted-key control. Nothing here ever uses the fixture key the
   binary carries.
3. Build. `demo-plan build` writes a content-addressed store, a Plan artifact
   naming the supervisor package by content identity, a signed package and
   its unsigned twin, and the stub child script. The stop signal is SIGINT
   and the safe-stop command is a one-line logger.
4. Admission verdicts. `demo-plan verify`, a separate process reading the
   files back: the signed package is `admitted` under `require`; the unsigned
   twin is `refused` with `reason signature_missing`; the signed package
   against a trusted set holding only the second key is `refused` with
   `reason signature_key_id_not_trusted`.
5. Preflight. `demo-preflight` against the staged package. On the tested
   Pi 5 it prints five green check lines and then refuses at the observer
   check, which is the documented shape (`LIMITATIONS.md`); the runner
   requires exactly that. Any other refusal stops the run here, and
   `doctor.sh` explains it.
6. Configuration. Writes `resident.conf` with `signature_policy = require`
   and `trusted_keys` holding only your key.
7. Tamper. Flips the middle byte of the stored package object and starts
   the resident. Expected: a nonzero exit, no `plan active` line, and a
   refusal on stderr naming `store-object-mismatch` and the package's
   content identity. The byte is restored afterwards.
8. Intact start. The resident prints `ready pid=` and `plan active ...
   resources=1 slots=1`.
9. Telemetry. `bin/telemetry.sh` answers `get-status`, `list-slots` and
   `read-flight` (and `get-health` when served), decodes every event by name,
   and reports `poll queries answered 3 of 3`. The slot `supervisor.lekiwi`
   is `ACTIVE`. The session identity and the four declared identities are
   recorded for step 11. Each identity must have its declared shape (64
   lowercase hexadecimal characters, 32 for the session identity) or the
   step fails naming the field. Every admin-socket query runs under
   `KC_PROBE_TIMEOUT` seconds (default 10, above the probe's own 5-second
   read timeout), so a probe that never answers cannot hold a step past its
   budget.
10. Child kill. SIGKILL to the stub. Expected: a new child with a different
    pid; in the flight ring, `EVT_SUPERVISOR_CHILD_EXITED` whose status word
    says signal 9, then `EVT_SUPERVISOR_CHILD_STARTED` with restart ordinal 1.
11. Resident kill. SIGKILL to the resident. Expected: exit status 137; no
    child carrying this run's tag over five consecutive scans of the process
    table; then the runner relaunches the resident from the same
    configuration file. Expected after relaunch: a different session
    identity, identical Plan digest, configuration identity, build identity
    and target-profile digest, and a fresh child. The before-and-after table
    is printed.
12. Clean stop. SIGTERM to the resident. Expected: `cake-resident: stopped`,
    the socket gone, no child.
13. Summary. The PASS line and the run directory.

## What this segment proves and what it does not

It proves rows 1 to 8 of `docs/claims.md` on your own board with your own
key. It proves nothing about actuators, because nothing in it touches one,
and it makes no timing claim: every wait in the runner is a bounded wait,
not a measurement.

## The relaunch in step 11

On the tested board, the relaunch after SIGKILL was performed by a systemd
user unit. Here it is performed by the runner itself, exactly as the
development-host gate does, so that segment 1 installs nothing on your
machine. Segment 2 uses a systemd user unit.
