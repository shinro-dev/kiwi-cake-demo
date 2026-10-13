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

The template also carries a wheel stop, which stays off until its four
wheel settings are filled in: the LeKiwi deployment's install fills them
in, segment 2's copy does not. When it runs, it writes goal velocity 0 to
the three wheel servos inside the supervisor's half-second limit, if it
can reach the bus; [Safety](SAFETY.md) lists when it cannot. It is best
effort, not a guarantee, and it has not yet been run on the robot.

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
commit f66e512 (issue 4309), and that one line is the first of the patches
`bin/pi/apply-lerobot-patches.sh` writes into a checkout at
b4e2d0b; the runbook runs the script before the first host start.

A third defect, and this one has no upstream fix. At b4e2d0b,
`LeKiwi.send_action` writes `Goal_Position` unconditionally, including when
the command carries base velocities and no arm positions, which is every
base command this demo sends. The motor bus is handed an empty dictionary
and raises while looking up the motor model of its first entry, before the
`Goal_Velocity` write underneath it happens. The host catches that and keeps
listening, so nothing fails loudly: the dispatch clock is never refreshed,
the base watchdog sees a stale command and stops the base, and the laptop is
told nothing. It presents as a base that will not move, with no error
anywhere the operator is looking. The one-line guard that prevents it is
the second patch the script writes, and it is in no lerobot commit at all:
this repository is the only place it is written down.

A third local change, of a different kind, is described in the next section:
it changes no behaviour and exists so that a host can READ what the arm was
configured with. The script owns that one too, and it reports every patch on
its own. `--check` exits 0 only when all of them are in the files, 1 when one
is not there yet, and 3 when a file is neither the text a patch expects nor
the patched text, in which case nothing is written to it. A checkout at or
after f66e512 therefore exits 1 and not 0: it already carries the clamp fix
and it does not carry the guard, so `--check` prints `clamp-fix: already
applied` and `empty-arm-guard: not applied (run with --apply)`, and
`--apply` on it writes the guard alone and leaves the clamp line as it is.

All of these changes live only in the installed checkout, so a `git
checkout`, a pull or a reinstall silently removes them. Before every host it
starts, `bin/pi/start-host.sh` runs `bin/pi/host_boundary.py --verify-robot`
on the robot computer, which reads the installed files themselves and
refuses the start when a change is gone. The supervised host of
`docs/reproduce-end-to-end.md` makes no such check: there, a reinstall of
lerobot means running the patch script again by hand.

## The arm gains are configurable, and that is all that change does

The third patch `bin/pi/apply-lerobot-patches.sh` writes gives
`LeKiwiConfig` three fields, `arm_p_coefficient`, `arm_i_coefficient` and
`arm_d_coefficient`, and makes `LeKiwi.configure` write those fields to the
`P_Coefficient`, `I_Coefficient` and `D_Coefficient` registers of every arm
motor instead of the literals that stood in their place. Their defaults are
`16`, `0` and `32`, which are exactly the literals the stock `configure()`
at b4e2d0b wrote. **No gain is different because of this patch.** A robot
that sets none of the three fields is configured precisely as an unpatched
lerobot configures it, and nothing here has tuned anything. A scalar applies
to every arm motor; a dictionary maps motor name to value, the shape
`max_relative_target` already has in that file.

What it buys is the reading end. The verify-first attach of
`bin/pi/lekiwi_host_safe_attach.py` reads `self.config.arm_p_coefficient`
and its two neighbours to check the live PID registers of every arm motor
before it attaches to an arm that is already holding its pose, and it
refuses rather than write anything. On a stock `LeKiwiConfig` at b4e2d0b
there is no such field and that read raises `AttributeError: 'LeKiwiConfig'
object has no attribute 'arm_p_coefficient'`, at a point where the arm has
been read and no register has been written. Nothing in the attach writes
those registers, before or after this patch; only `configure()` writes them,
on connect, and the autonomous-demo host replaces `configure()` outright.
The host of `docs/reproduce-end-to-end.md` is unaffected either way, because
it replaces `calibrate` only and leaves the stock `configure` in place.

`bin/pi/host_boundary.py --verify-robot` now reads the `config_lekiwi.py`
beside the installed `lekiwi.py` as well, requires those three fields to
resolve to `16`, `0` and `32`, requires `configure()` to take them from the
configuration rather than from literals, and refuses the start otherwise. It
prints the sha256 of both files it read and the three values it resolved, so
the step 0 report of `bin/laptop/qualify-robot.sh` and the log of a host
start record WHICH source was checked rather than only that some source
passed. A field that resolves to a value this repository never recorded is
refused and the value is named: applying over it would conceal a
configuration nobody wrote down, so the patch script refuses that state too,
with exit 3.

### The deployed install declares those three fields twice

Recorded here so that nobody later reads it as a configuration difference.
The `config_lekiwi.py` on the robot computer the demo was run from, read
there read-only on 2026-09-21, declares the three fields **twice**, in two
adjacent comment-and-field blocks. The first block annotates them `int |
dict[str, int]`, the second `int`. Python keeps the last binding of a name
in a class body, so the effective annotation is `int` and the effective
defaults are `16`, `0` and `32`; `dataclasses.fields` on that install
resolves them to exactly those three values. Two backup files beside it
(`.pre-patch3.bak` and `.pre-patch3-dictfix.bak`) show that the repetition is
the residue of two overlapping hand edits, not a deliberate shape.

The identities of the two files read there that day:

| file | sha256 |
| --- | --- |
| `config_lekiwi.py` | `a1ea682fa611bbeaf72a082e288ef04558dbaa52777621695d83dd99a015966e` |
| `lekiwi.py` | `b018415dd4bd67e37cdf66d0c8da785da82b4f54e5e3c0df14cde77aee7073c4` |

`bin/pi/apply-lerobot-patches.sh` writes the block **once**: repeating a
hand-edit artifact into a reader's install would propagate it, and the
resolved values are identical either way. It does not conceal the difference
either. `--check` recognises the duplicated form as already applied and says
so in those words, `--apply` writes nothing to it, and `--verify-robot`
reports it as a NOTE that does not refuse the start, because the robot
computer is in exactly that state and the operator has ruled that install
stays unchanged until a planned deployment session. So a clean install built
from this repository differs from the robot computer in that one respect,
the number of times the fields are written down, and in nothing that
resolves to a different number.

Nothing passes a PID argument at host start: the command line is
`--robot.id`, `--robot.port`, `--robot.max_relative_target=15.0`,
`--robot.disable_torque_on_disconnect=false`, `--robot.cameras={}` and
`--host.connection_time_s=86400`, so the resolved values are the defaults.
All six arm motors are checked against the same scalar, and in scan mode the
gripper is taken out of that list before the check runs, so its gains are
not checked there. There is no per-joint gain difference in the deployed
configuration.

Why the change was made in the first place is not recorded anywhere this
repository can see, and this page does not guess at it. What is recorded is
that it changes no gain, that the attach depends on it, and that a clean
install can now reproduce it.

## Provided for evaluation

The binaries are provided for evaluation only, without source and without
warranty of any kind, under the Business Source License 1.1 in `LICENSE`.
