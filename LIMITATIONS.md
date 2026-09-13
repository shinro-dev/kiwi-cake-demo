<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Limitations

Use this demo to evaluate host supervision and lifecycle evidence on the
tested Pi 5. The [architecture](docs/architecture.md) describes the
deployment; the [claims map](docs/claims.md) distinguishes recorded
observations from development-host checks.

## Scope of this release

| Area | Available here | Not established by this demo |
| --- | --- | --- |
| Host lifecycle | Controlled child exits, restarts and resident relaunch | Automatic diagnosis of USB, camera, calibration or network faults |
| Deployment identity | Comparison of Plan, configuration, build and target-profile identities | Reproducibility of the external Python environment or restoration of application state |
| Modules | One supervisor slot, one resource and zero bindings | Independent camera or motion modules, or live module replacement |
| LeRobot workflow | Pi host supervision and an arm-only laptop example | Ordinary recording, policy execution or continuity through a failure |
| Inspection | Local admin queries, runner transcripts and captured host output | A durable event archive or an automated support bundle |
| Hardware state | LeRobot connect/disconnect behavior and operator observations | An independent motor stop, a torque readback or recovery into a disarmed state |

The latest [v0.1.3 board run](docs/demo-record.md#the-v013-run-on-the-tested-board-2026-09-13)
used an idle host with teleoperation skipped. Its process and signal
evidence does not establish behavior during an active client session.

## Actuator-safe recovery is not guaranteed

After the resident is killed and relaunched, the Plan digest, configuration
identity, build identity and target-profile digest read back identical,
with a fresh session identity. These are comparisons of declared
identities, not measurements of robot state.

What it does not show is the actuators coming back disarmed. In this demo the
motors are owned by the stock LeRobot host, which enables torque on every
successful connect, recovery included. Cake's admin protocol carries no
torque or actuator concept, so there is nothing for Cake to gate. The
operator's verdict in the demo record reads, with two internal ledger
references removed at the marked elisions:

> Cake-level recovery PASS ([...] byte-identical declared authority),
> actuator-safe-recovery NOT DEMONSTRATED ([...] the LeRobot child re-arms
> unconditionally on connect).

Treat every restart in segment 2 as a torque-on event.
[SAFETY.md](SAFETY.md) is mandatory for that reason.

## No live module replacement

The public demo does not perform a live replacement of a running module.
It does not demonstrate the broader Cake v1.1 module architecture merely
by running the host beneath a supervisor.

## The relaunch is a new process, not a resumed one

A relaunched resident activates the Plan its configuration names.
Module memory does not survive it: the supervisor's restart ordinal begins
again at zero. The v0.1.3 journal explicitly reports a fresh install with
no committed record to recover. Matching identities after relaunch do
not prove recovery of a committed transaction, persistent module state
or an interrupted recording session.

## What is signed, and what is not

The signed object is the supervisor capsule (the module package), not the
external LeRobot program. The shell wrapper, Python dependencies, LeRobot
checkout and calibration files are outside that capsule's signature. The
Plan artifact format carries no signature; the Plan is bound to the capsule by content
identity, which is exactly the binding the tamper beat breaks by changing one
byte of the stored object. Under the `require` signature policy the resident
refuses an unsigned capsule and a capsule signed by a key it does not trust,
naming the policy word; that refusal is exercised by the demo's own verify
step and was measured on the development host, not on the board.

## Configured waits are not performance measurements

The scripts configure restart backoff, query timeouts, termination
deadlines and client rates. Captures also contain timestamps. None of
these establishes an end-to-end restart latency, control-loop rate or
motor-stop deadline. The release has no qualified performance benchmark.

## The safe-stop command is yours

The supervisor runs a configured command once for every child exit it
observes. The original record did not capture the operator's command's
actions; the v0.1.3 run used the shipped template, which only writes one
log line. Anything that touches the robot is yours to write and to test on a
stand first. A hard host death can skip its disconnect path. The host's
watchdog depends on the host continuing to execute; it is not an
independent motor stop. Nothing in the shipped safe-stop template
guarantees that wheels stop or torque is disabled after a process death.

## The preflight cannot reach PASS in the public build

`demo-preflight` runs seven checks in order: page size, glibc, memfd exec
policy, admission, load probe, observer, shipped binaries. The sixth check
verifies a module that the board builds for itself from a source tree that
is not part of this release, so in the public build it always refuses with
the text `--observer-module was not given`, and the seventh check never
runs. The demo runner requires the first five checks to print their green
lines and accepts exactly that one refusal as the end of the roster; any
other refusal stops the run. `bin/doctor.sh` re-implements the seventh check
in shell (one no-op run of each shipped binary) and labels it as such.

## Platform checks do not qualify the whole robot

`demo-preflight` checks platform facts against an embedded board record:
Raspberry Pi 5, Raspberry Pi OS based on Debian 13 (trixie), a 16 KiB page
kernel, glibc 2.41. The page-size check requires equality with 16384, and the
glibc check requires the running version to be at or above 2.41 as well as at
or above the binaries' own floor of 2.34. The doctor's board classification
does not certify an identical OS image, working devices or a calibrated
robot. Consequences of the preflight requirements:

- A Raspberry Pi 4 is refused at the page-size check (its kernels use 4 KiB
  pages).
- A Raspberry Pi 5 running Raspberry Pi OS bookworm (glibc 2.36) is refused
  at the glibc check.
- A Raspberry Pi 5 booted with the 4 KiB kernel (`kernel=kernel8.img`) is
  refused at the page-size check.

[Supported targets](docs/targets.md) says what each refusal means and what the
`--unsupported-target` override does and does not allow. There is no x86-64
build in this release. The shipped binaries are AArch64 and cannot run
natively on an x86-64 desktop.

## Demo keys are for local evaluation

The runner generates and trusts a local demo key. The binary also contains
a publicly derivable fixture key for gate experiments; never trust that
fixture key in a real deployment. See [Release binary inspection](docs/release-internals.md)
for the fixture-key warning and the disclosed binary-string details.

## The stock host prompts for calibration on connect

The stock LeKiwi host calls `LeKiwi.connect()`, which runs
`LeKiwi.calibrate()` whenever the live servo registers disagree with the
loaded calibration file, and `calibrate()` asks on standard input whether to
use that file. Under the Cake supervisor there is no terminal: the question
ends in `EOFError`, the host exits, the supervisor restarts it, and after
five identical exits the supervisor reaches its restart bound and gives up,
which the flight ring shows as `EVT_SUPERVISOR_RESTART_BOUND_REACHED`.
Calibrating beforehand does not prevent it: the disagreement recurs. This is
a property of the stock host, not of Cake, and Cake has no way to answer a
prompt for a child. The demo ships `bin/pi/lekiwi_host_noninteractive.py`,
which replaces that one method with the non-interactive rule (use the loaded
file, or exit without writing to any motor) and otherwise runs the stock
host unchanged; `docs/reproduce-end-to-end.md` uses it.

A second defect of the stock host at the lerobot commit the demo was run
with (b4e2d0b): the `--robot.max_relative_target` clamp raises on every
command, because it indexes the present positions with the goal's key
names. Run by hand, the host logs `Message fetching failed:
'arm_shoulder_pan.pos'` and the follower never moves. lerobot fixed it in
commit f66e512 (issue 4309). `bin/pi/apply-lerobot-clamp-fix.sh` applies
that one line to a checkout at b4e2d0b, reports a checkout at or after
f66e512 as already fixed, and refuses anything else; the runbook runs it
before the first host start.

## Provided for evaluation

The binaries are provided for evaluation only, without source and without
warranty of any kind, under the Business Source License 1.1 in `LICENSE`.
