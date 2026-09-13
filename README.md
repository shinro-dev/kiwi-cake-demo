<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# kiwi-cake-demo

Status: an evaluation release. The version is the `VERSION` file; the
binaries are on the Releases page:
https://github.com/shinro-dev/kiwi-cake-demo/releases

A precompiled demonstration of Cake, Shinro's control layer for robots,
running underneath the LeRobot host of a LeKiwi. The binaries are published
in this repository's Releases; this repository holds the documentation, the
scripts that run the demo, the tests that check a download, and nothing else.
No source code is distributed.

The demo is released as-is, for evaluation. Read `LIMITATIONS.md` before
you form an opinion, and read `SAFETY.md` before you let it touch a robot.

## Which machine runs what

| On the Raspberry Pi (in the LeKiwi) | On the laptop |
| --- | --- |
| `bin/fetch-release.sh`: download and verify the release | `lerobot` at commit b4e2d0b with the `lekiwi` and `viz` extras |
| `bin/doctor.sh`: classify the board | the SO-101 leader arm on its `/dev/serial/by-id/` port, with its own calibration id |
| `bin/demo-segment1.sh`, or `tests/smoke-segment1.sh`: segment 1, torque-free | `bin/laptop/teleop.py`: the teleoperation client |
| `bin/run-child.sh` and `bin/safe-stop.sh`, written from `bin/templates/` | an ssh session to the Pi: terminal A of segment 2 |
| `bin/demo-segment2.sh`: segment 2, on the live robot | |
| `bin/demo-stop.sh`: the stop, at any time | |

In order across the two machines:

1. Pi: fetch the release, run the doctor, run segment 1 to its PASS line.
2. Pi: install LeRobot with the clamp fix, calibrate the LeKiwi, prove the
   stock host by hand once, write the two files.
3. Laptop: install LeRobot, calibrate the leader arm.
4. Pi, terminal A: segment 2 to Beat 1 (torque on).
5. Laptop, terminal B: the client; then the beats alternate between the two
   terminals as the table below shows.
6. Laptop parks the follower and exits; Pi stops the resident.

`docs/reproduce-end-to-end.md` is every step of that order in full.

## What you need

### On the Raspberry Pi

- a Raspberry Pi 5 inside a LeKiwi: its SO-101 arm, three wheels, two
  cameras, one servo controller;
- the 64-bit Raspberry Pi OS based on Debian 13 (trixie), default kernel;
- `git`, `openssl`, `gpg`, `tar`, and either `curl` or the GitHub CLI `gh`;
- for segment 2 only: a Python environment with `lerobot` at commit
  b4e2d0b and its `lekiwi` extra, plus the one-line clamp fix
  (`bin/pi/apply-lerobot-clamp-fix.sh`); tested with Python 3.13, from the
  system interpreter in a venv and from a conda environment.

### On the laptop

- Ubuntu 24.04 (tested: 24.04.4 LTS);
- Python 3.13 (tested: 3.13.13 in a conda base environment; LeRobot itself
  requires 3.12 or newer);
- `lerobot` at commit `b4e2d0b61017a0db646a12c98a2df3837e37a1b9` with the
  `lekiwi` and `viz` extras (`pip install -e ".[lekiwi,viz]"`);
- membership of the `dialout` group, for the leader arm's serial port;
- TCP 5555 and 5556 on the Pi reachable from the laptop, no firewall
  between them;
- the leader arm on its `/dev/serial/by-id/` port, calibrated once under
  its own id (`<leader-id>`), which is not the robot's id (`<robot-id>`)
  that lives on the Pi;
- an ssh session to the Pi, for terminal A of segment 2;
- a clone of this repository, for `bin/laptop/teleop.py`.

Segment 1 needs only the Pi and none of the LeRobot parts.

## Segment 1, on the Pi

Torque-free: no robot, no LeRobot, nothing that opens a device.

```
git clone https://github.com/shinro-dev/kiwi-cake-demo.git
cd kiwi-cake-demo
bin/fetch-release.sh
bin/doctor.sh
tests/smoke-segment1.sh
bin/demo-stop.sh
```

`fetch-release.sh` downloads the tarball for the release in `VERSION`,
verifies its checksum and signature, and extracts it; `doctor.sh` says in
plain language whether the preflight will accept the board; the smoke test
runs the thirteen steps with assertions and ends with
`KIWI-CAKE SEGMENT 1: PASS`; `demo-stop.sh` stops anything left running
and is safe at any time. `docs/segment-1-stub.md` explains each step;
`bin/demo-segment1.sh --pause` waits for Enter between steps.

## Segment 2, in two terminals

Segment 2 moves the arm. It runs only on the tested Pi 5, from an
interactive terminal, after the acknowledgment in `SAFETY.md`, with
`bin/run-child.sh` and `bin/safe-stop.sh` written from the templates.
Terminal A is an ssh session to the Pi; terminal B is the laptop.

| Order | Terminal A (the Pi) | Terminal B (the laptop) |
| --- | --- | --- |
| 1 | `bin/demo-segment2.sh` to Beat 1: the host listens, torque on | |
| 2 | | `bin/laptop/teleop.py ...`: the follower mirrors the leader |
| 3 | type `CONFIRMED` | |
| 4 | Enter at Beat 3: SIGTERM to the host only | the client loses its connection |
| 5 | a fresh host listens, resident and session unchanged | run the client again |
| 6 | the acknowledgment again, Enter at Beat 4: SIGKILL to the resident | the client loses its connection |
| 7 | the resident relaunched, identities identical, the host re-armed | run the client again |
| 8 | | park the follower low via the leader, then Ctrl-C |
| 9 | Enter at STOP: the host exits cleanly, torque off | |
| 10 | the checks in `docs/stopping-and-cleanup.md` | |

Every line, check and expected output is in `docs/reproduce-end-to-end.md`;
the Pi-side detail of each beat is in `docs/segment-2-live.md`.

## Stopping and cleanup

Park the follower low, close the client, stop the resident, never signal
the host directly. `docs/stopping-and-cleanup.md` is the ordered procedure
with the motor state after each step, the commands that prove nothing is
left, the removal of the user unit, what to do if a stop hangs, and the
emergency stop.

## Presenting the demo

`docs/demo-walkthrough.md` narrates each capability in order: what to say
it proves, tied to its row in `docs/claims.md`, the runner step that shows
it, the exact line to point at, and the honest caveat. Run segment 1 with
`bin/demo-segment1.sh --pause` to talk between steps.

## What Cake is

Cake is a control layer that runs on the robot, beneath the robot's own host
program. It does not replace the host and it does not touch the wire
protocol between the host and the laptop. It launches the host as a
supervised child process and owns its lifecycle. On top of that process
model it adds four things:

- Signed-program admission. A program runs only inside a signed package that
  Cake verifies before launching anything; a tampered package is refused and
  the refusal names the reason.
- Supervision. When the child dies, Cake records the exit and spawns a fresh
  child under a bounded restart policy.
- Crash recovery of declared state. When Cake itself is killed, its relaunch
  restores exactly the deployment it had declared, byte for byte, and reports
  a fresh session identity.
- Live telemetry. While the resident runs, a local socket answers what is
  running, what is bound to what, and a structured event trace.

More on Cake, beyond this demo, is at https://docs.shinro.dev.

## What this demo does

Every sentence in this list is backed by a measured statement in the demo
record this release derives from or by a line of the development-host gate
record; `docs/claims.md` maps each one to its evidence and says which.

1. Builds a signed package on your machine, with a key you generate, and a
   Plan that names the package by its content identity.
2. Refuses to start when one byte of the stored package is changed, naming
   the content identity, and starts again when the byte is restored. Refuses
   an unsigned copy of the package and a copy signed with a key it does not
   trust.
3. Starts the Cake resident with the package admitted, supervising a child
   process. In segment 1 the child is a harmless line-printing stub. In
   segment 2 it is your own LeRobot host, listening on its usual ports.
4. Answers three read queries over a local admin socket, printed as a live
   table with every event decoded by name.
5. When the child is killed, spawns a fresh child under the next restart
   ordinal, with the exit and the restart in the event trace.
6. When the resident itself is killed with SIGKILL, comes back from the same
   configuration with a fresh session identity while the Plan digest, the
   configuration identity, the build identity and the target-profile digest
   stay identical; the killed resident's child does not survive it.
7. Stops cleanly on request, closing the socket and the child with it.

What it does not do is just as important. It does not perform a live
replacement of a running module, and it does not bring the motors back
disarmed after a crash: the LeRobot host re-enables torque on every connect,
and Cake has no torque concept to override that. `LIMITATIONS.md` states
both plainly.

## Supported targets

| Target | Status | Release tarball |
| --- | --- | --- |
| Raspberry Pi 5, Raspberry Pi OS trixie (64-bit), default 16 KiB page kernel, glibc 2.41 | tested and primary | `kiwi-cake-demo-<version>-pi5-aarch64.tar.gz` |
| Raspberry Pi 5 on bookworm, or booted with the 4 KiB kernel | provided as-is; the preflight refuses by construction; segment 1 only, behind `--unsupported-target` | same tarball |
| Raspberry Pi 4 (aarch64) | provided as-is, untested; the preflight refuses by construction; segment 1 only, behind `--unsupported-target` | the `pi5-aarch64` tarball (same file) |
| x86-64 desktop (the SO-101 desktop case) | not in this release | none |

Every binary requires glibc 2.34 or newer and depends on `libc.so.6` alone.
`docs/targets.md` has the reasoning behind each row and what each preflight
refusal means.

## Safety

- Segment 1 is torque-free by construction and needs no robot.
- Segment 2 enables torque every time the host connects, including after
  every recovery the demo performs. Robot on a stand, wheels off the ground,
  arm parked and supported, hand near power. `SAFETY.md` is mandatory.
- Stop the resident, never the child: `bin/demo-stop.sh`.

## The architecture in one picture

![Cake as the control layer under LeKiwi teleoperation](docs/architecture.svg)

The operator side is unchanged: the same LeRobot client, the same protocol
on the same ports. The robot side gains a control plane beneath the host.
The contrast at the bottom is the failover contrast: with the stock stack, a
host that crashes stays dead until someone restarts it by hand; with Cake
underneath, the host is restarted under policy and is back listening on its
ports. The laptop side is unchanged either way.

The drawing's source is `docs/architecture.excalidraw`; its labels were
aligned to the SVG's wording, which is the wording the record supports.

## Repository layout

| Path | What it is |
| --- | --- |
| `bin/` | the scripts a user runs: fetch, verify, doctor, the two segments, stop, telemetry |
| `bin/pi/` | the LeKiwi host wrapper the supervisor spawns in segment 2, and the lerobot clamp fix for the pinned commit |
| `bin/laptop/` | the teleoperation client you run on the laptop (Apache License, Version 2.0) |
| `bin/templates/` | the child wrapper (and a filled example of it), safe-stop command and systemd unit templates for segment 2 |
| `docs/` | the record, the claims map, the target matrix, the runbooks, the stopping procedure, the presenter's walkthrough, release verification, event codes |
| `tests/` | the smoke test and the tests of the tooling itself |
| `tools/` | maintainer side: the strings gate and the release assembler |
| `keys/` | the release signing key |
| `releases/` | the committed checksum list of each release, for cross-checking a download |
| `.github/` | the workflow that runs the tooling tests on every push and pull request |
| `CONTRIBUTING.md` | issues welcome; pull requests not accepted, and why |
| `SECURITY.md` | how to report a vulnerability, privately, and what to include |
| `VERSION` | the release version, one line, read by every script and shipped in every tarball |

A release archive is this tree at the tagged commit with the four binaries
added under `bin/` and a `MANIFEST.txt` at the root; every path above holds
inside an extracted archive except `releases/<version>/` for the archive's
own version (`docs/verifying-a-release.md`).

## Verifying a release

Every release carries `SHA256SUMS`, a detached signature over it, and a
detached signature over each tarball. `bin/verify.sh` checks all of them
against the key in `keys/`; `docs/verifying-a-release.md` shows how to do the
same by hand.

## Reporting a vulnerability

Privately, through GitHub's advisory form for this repository; `SECURITY.md`
has the link and what to include. Never as a public issue.

## Contributing

Issues are welcome; pull requests are not accepted. `CONTRIBUTING.md` says
why and what a useful issue carries.

## License

Business Source License 1.1, Licensor Shinro SAS (France). See `LICENSE`,
`LICENSE-NOTE.md` and `NOTICE`. The binaries are provided for evaluation
only, without source and without warranty. One file is the exception:
`bin/laptop/teleop.py` is derived from a LeRobot example and is under the
Apache License, Version 2.0 (`NOTICE`).
