<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Release notes

## v0.1.3

The binaries are the same bytes as in v0.1.2, v0.1.1 and v0.1.0 (private
source revision 79b52d9dda83c65bf2fc1172314e4570ab72782c, recorded in
each tarball's `MANIFEST.txt`). What changed is the scripts, the tests
and the documents, after an external audit of v0.1.2.

The organizing finding of that audit: as shipped, after a stop there is
no reliable, tested software guarantee about the physical torque state
of the arm, because the startup port guard, the stop command's success
reporting, and the systemd stop path each have an independent hole, and
the safe-stop template only logs; the stand does the safety work. This
round closes the three software holes; it does not claim actuator-safe
recovery (`LIMITATIONS.md` is unchanged on that point).

The fixes, each with a regression test that fails on v0.1.2:

- `bin/demo-segment2.sh`: the port guard tells any, all and none apart,
  so one leaked listener no longer passes both the startup check and the
  post-stop check; a SKIP at the teleoperation beat is printed as
  `KIWI-CAKE beat 4 teleop: SKIPPED (operator)` and the last line as
  `KIWI-CAKE SEGMENT 2: DONE (teleop SKIPPED by the operator)`.
- `bin/demo-stop.sh`: waits for the stop, reports what it stopped and
  what is left, and exits 1 when a resident of this checkout or its
  supervised child survives; before, it exited 0 in every case and
  looked for the host under a tag the segment 2 host never carries.
- `bin/doctor.sh`: exits 6 when the preflight refuses and 7 when its
  output cannot be parsed; before, both cases exited 0 on the tested
  board. `docs/targets.md` carries the table.
- `bin/lib/common.sh`: the preflight verdict reads the exit status and
  every refusal line, so a signal death or a second refusal is no longer
  accepted as the five-green shape; every admin-probe query is bounded
  by a wall-clock timeout (`timeout` from coreutils is now a required
  tool), and the readiness waits are bounded by wall clock.
- `bin/telemetry.sh` and both runners: a get-status reply counts as
  answered only when it carries the fields the runners compare, so two
  empty identities can no longer compare as identical; get-health
  reports served, not served, failed and timed out as four outcomes.
- `bin/laptop/teleop.py`: `--fps` must be an integer from 1 to 1000,
  checked before LeRobot is imported, and both devices are disconnected
  even when the first disconnect raises.
- `bin/templates/kiwi-cake-demo.service.in`: `KillMode=mixed`,
  `KillSignal=SIGTERM` and `TimeoutStopSec=60`, so systemd's stop
  signals the resident only and the host hears from the resident's own
  shutdown, not from systemd; under the previous unit a SIGTERM from
  systemd would have ended the pinned host, which has no SIGTERM
  handler, before its disconnect could run. `tests/unit-stop-signals.sh`
  is the torque-free board test behind it.
- `tools/build-release.sh` and `tools/publish.sh`: the tarball is the
  repository at the tagged commit (`keys/`, `tests/`, `tools/`,
  `releases/` and the root documents included) with the four binaries
  in `bin/` beside the scripts and no `scripts/` directory, so
  `bin/verify.sh` and `tests/smoke-segment1.sh` work from inside it;
  the one file it cannot contain is `releases/<version>/SHA256SUMS` for
  its own version; `--version` must equal `VERSION`; `--releases-dir`
  keeps a dry assembly out of the checkout; the release body is this
  version's section alone; `docs/verifying-a-release.md` names the
  download directory `bin/fetch-release.sh` writes.
- `docs/why-cake-under-lerobot.md` compares the host by hand, under a
  plain systemd service and under Cake, each given its due, with every
  Cake capability sentence cited to its `docs/claims.md` row; it no
  longer says the stock host has no supervision or recovery of its own,
  or that the laptop needs no script for teleoperation. It states
  plainly that nothing here stops the wheels when the host dies, and
  that a signed package admits the supervisor, not the host's own
  bytes. The README says what is precompiled (the four Cake binaries)
  and what is source (everything else here).
- README and runbook: `bin/laptop/teleop.py` is labelled an arm-only
  bench example whose actions hold the base velocities at zero.

The test report: `tests/run-all.sh` prints one line per suite with its
kind and status (PASS, PASS with skipped checks, SKIPPED for a missing
precondition, FAIL), lists every skipped check, and ends with a count
line, so a skipped check is no longer folded silently into PASS.

v0.1.2 stays valid; v0.1.3 supersedes it. Checksums:
`releases/v0.1.3/SHA256SUMS`.

## v0.1.2

The binaries are the same bytes as in v0.1.1 and v0.1.0 (private source
revision 79b52d9dda83c65bf2fc1172314e4570ab72782c, recorded in each
tarball's `MANIFEST.txt`). What changed: the README is organized by
machine, with a two-terminal walkthrough of segment 2 and a laptop
checklist stating what was tested; `docs/stopping-and-cleanup.md` is the
one ordered exit procedure, and the other documents point at it;
`docs/demo-walkthrough.md` narrates each capability for a presenter, tied
to `docs/claims.md`; `bin/demo-segment1.sh --pause` waits for Enter between
steps so segment 1 can be narrated. One runner fix: Ctrl-C during segment 1
now stops the run and exits 130; before, the trap ran the cleanup and let
the script continue into the next step. v0.1.1 stays valid; v0.1.2
supersedes it. Checksums: `releases/v0.1.2/SHA256SUMS`.

## v0.1.1

Replaces v0.1.0, which is withdrawn. The four binaries are the same bytes as
in v0.1.0: built from the same private source revision,
79b52d9dda83c65bf2fc1172314e4570ab72782c, recorded in each tarball's
`MANIFEST.txt`. What changed: the license (`LICENSE` Change Date is now
2030-09-08, so the Business Source License terms are in force;
`LICENSE-NOTE.md` explains), the documentation (a cross-machine runbook,
`docs/reproduce-end-to-end.md`; the README; `CONTRIBUTING.md`), the scripts
(the LeKiwi host wrapper `bin/pi/lekiwi_host_noninteractive.py`, the laptop
client `bin/laptop/teleop.py`, a filled `run-child.sh` example, a `VERSION`
file, one tarball instead of two), and the git history, rewritten to remove
tooling metadata from commit messages. Checksums: `releases/v0.1.1/SHA256SUMS`.

## v0.1.0

Withdrawn on 2026-09-12 and replaced by v0.1.1: its `LICENSE` carried a
Change Date already in the past. Kept here for the record.

The first public release of the Cake demo for LeKiwi owners. Released as-is,
for evaluation, under the Business Source License 1.1.

What is in it:

- Four precompiled binaries per target: `cake-resident`, `admin-probe`,
  `demo-plan`, `demo-preflight`. Targets: `pi5-aarch64` (tested and primary)
  and `pi4-aarch64` (provided as-is; the same bytes as `pi5-aarch64`).
- The scripts, documentation and license files of this repository at the
  tagged commit, inside every tarball.
- `SHA256SUMS`, its detached signature, and a detached signature per tarball.

What is not in it: any source code; an x86-64 build; live module
replacement; actuator-safe recovery. `LIMITATIONS.md` has the full list.

Checksums: `releases/v0.1.0/SHA256SUMS`. The private source revision the
binaries were built from is recorded in each tarball's `MANIFEST.txt`.
