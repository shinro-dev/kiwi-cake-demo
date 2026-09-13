<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# kiwi-cake-demo

A hands-on demo for LeKiwi developers who want to reproduce Pi-side
LeRobot host exits and inspect what restarts. It runs the host under
Cake, Shinro's robot control layer, and exposes process lifecycle events
and declared deployment identities through a local admin socket.

Use it to answer concrete questions: did the host exit, or did its
supervisor restart? Was another host launched? Did Cake start again
from the same declared configuration? The runners deliberately trigger
failures and save transcripts you can inspect and discuss with others.

## Who this is for

- **LeKiwi developers:** investigate host startup and restart behavior
  with a repeatable sequence of process failures.
- **Tinkerers:** try the same lifecycle experiment with a harmless stub
  before connecting a robot.
- **LeRobot contributors:** evaluate whether the events and identity
  comparisons make a failure report more useful.

If you want to build a LeKiwi, teleoperate it, record a dataset or train a
policy, start with the [LeRobot LeKiwi guide](https://huggingface.co/docs/lerobot/en/lekiwi).
This demo adds a host supervision experiment; it does not demonstrate
better teleoperation, automatic fault diagnosis or recording continuity.

## Try it without a robot

Segment 1 runs on the supported Raspberry Pi and supervises a
line-printing stub. It needs no motors, cameras, LeRobot installation or
connected robot. The release contains AArch64 binaries; there is no
native x86-64 desktop build.

On a Pi 5 running 64-bit Raspberry Pi OS trixie with the default 16 KiB
page kernel, install `git`, `openssl`, `gpg`, `tar`, `curl` and GNU
coreutils, then run:

```sh
git clone https://github.com/shinro-dev/kiwi-cake-demo.git
cd kiwi-cake-demo
bin/fetch-release.sh
bin/doctor.sh
tests/smoke-segment1.sh
```

The fetcher verifies the release checksum and signatures. The doctor
reports board compatibility and binary checks. The smoke test exercises
package tampering, child exit, resident restart and cleanup, ending with:

```text
KIWI-CAKE SEGMENT 1: PASS
```

The transcript and before/after polls are under `state/runs/<id>/`.
Use [Diagnosing a run](docs/diagnosing-a-run.md) to interpret them.
For an interactive walkthrough, run `bin/demo-segment1.sh --pause`.
See the [segment 1 guide](docs/segment-1-stub.md) for each step and the
[target matrix](docs/targets.md) if the doctor refuses your board.

## What to look for

| Experiment | Evidence to inspect | What it establishes |
| --- | --- | --- |
| Change one byte of the stored capsule | Admission refusal naming its content identity | The altered supervisor package is rejected |
| Kill the child process | Child-exit and child-start events, restart ordinal | A new child was launched under the restart policy |
| Kill the Cake resident | New session; matching Plan, configuration, build and target-profile identities | Fresh activation from the same declared configuration |
| Request a clean stop | Shutdown output, child and socket cleanup checks | The observed processes and socket were cleaned up |

The [claims and evidence map](docs/claims.md) distinguishes board
observations from development-host checks. Matching declared identities
does not establish that the external Python environment is unchanged or
that application state was restored.

## Try it with a LeKiwi

Segment 2 puts the Pi-side LeRobot host under the same supervisor. A
systemd user service restarts the Cake resident after an abnormal exit;
Cake supervises the host beneath it. LeRobot continues to own the motors,
cameras and ZMQ protocol. The supplied laptop client is an
arm-only bench example using LeRobot's client API, with base velocities
held at zero.

Follow the [complete two-machine runbook](docs/reproduce-end-to-end.md),
including the pinned LeRobot version, calibration and host wrapper. Read
[SAFETY.md](SAFETY.md) before starting: the robot must be on a stand with
wheels clear and the arm supported. Every successful host reconnect
enables torque, including after an injected failure.

For a normal stop, park and support the follower, close the laptop client,
then run `bin/demo-stop.sh` on the Pi. Follow the
[stopping and cleanup procedure](docs/stopping-and-cleanup.md) to check
the result. Stopping a process is not proof that motors are disarmed.

## What Cake adds, and what this demo leaves open

A plain systemd service already provides host restarts and journal logs.
This demo adds admission of a signed **supervisor capsule**, named
deployment identities, and structured lifecycle events. The capsule's
signature does not cover the external LeRobot code, Python dependencies
or calibration files. See the [comparison with direct LeRobot and systemd](docs/why-cake-under-lerobot.md)
and the [actual deployment architecture](docs/architecture.md).

The demo has one supervisor module. It does not demonstrate independent
camera or motion modules, live module replacement, durable application
state recovery, or actuator-safe recovery. There is no automated support
bundle or diagnosis of camera, USB, calibration or network faults.
[Limitations](LIMITATIONS.md) defines these boundaries. The latest
[v0.1.3 board record](docs/demo-record.md#the-v013-run-on-the-tested-board-2026-09-13)
exercised an idle host with teleoperation skipped.

## Documentation, releases and feedback

- [Documentation index](docs/README.md): choose a guide by task.
- [Diagnosing a run](docs/diagnosing-a-run.md): read the output and prepare
  a useful, redacted reproduction report.
- [Releases](https://github.com/shinro-dev/kiwi-cake-demo/releases) and
  [release verification](docs/verifying-a-release.md): the version is in
  [VERSION](VERSION); archives include the scripts, docs and binaries.
- [Contributing](CONTRIBUTING.md): issues and reproduction reports are
  welcome; pull requests are currently not accepted.
- [Security reports](SECURITY.md): report vulnerabilities privately.

The Cake runtime is precompiled; its source is not published. The runner
scripts, adapters, documentation and tooling tests are available here.
The binaries are provided for evaluation under the Business Source
License 1.1, without warranty. See [LICENSE](LICENSE),
[LICENSE-NOTE.md](LICENSE-NOTE.md) and [NOTICE](NOTICE).
The LeRobot-derived [laptop client](bin/laptop/teleop.py) is licensed under
Apache 2.0. Broader Cake documentation is at [docs.shinro.dev](https://docs.shinro.dev).
