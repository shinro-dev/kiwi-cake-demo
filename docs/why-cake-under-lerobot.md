<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Why try Cake under the LeRobot host?

The useful question is whether a LeKiwi host failure becomes easier to
understand and reproduce. This demo lets you deliberately end the host
or its supervisor, inspect lifecycle events, and compare the declared
identities before and after relaunch.

That is useful when investigating startup loops, separating a child exit
from a resident restart, or discussing a reproducible failure with another
developer. It does not establish the cause of a bad camera frame, a USB
disconnect or a calibration error. Those still require LeRobot output
and checks of the relevant hardware or configuration.

## Who benefits today?

| Audience | Useful experiment | Cost or boundary |
| --- | --- | --- |
| LeKiwi developer investigating host lifecycle | Reproduce an exit and inspect which process restarted, with which identities | Requires the supported Pi environment and extra setup around the host |
| Tinkerer evaluating supervision | Run the same lifecycle sequence with a harmless stub before using hardware | Robot-free does not mean desktop-compatible; release binaries are AArch64 |
| LeRobot contributor reviewing a failure report | Compare a step transcript, host errors and named lifecycle events | Evidence is collected manually; there is no automatic diagnosis or support bundle |
| Owner who wants teleoperation or dataset recording | Evaluate whether these diagnostics solve a problem they actually encounter | No demonstrated improvement to ordinary teleoperation, recording or policy execution |

For the upstream robot workflows, start with the
[LeRobot LeKiwi guide](https://huggingface.co/docs/lerobot/en/lekiwi).

## Compare three ways to run the host

All three can use the same underlying LeRobot host, which owns the serial
bus, cameras and wire protocol. The comparison below describes a direct
launch, a conventional service, and this demo's supplied configuration.
Additional logging, provenance or verification can be built around any
of them.

| Concern | Direct LeRobot host | Host under plain systemd | Host under this Cake demo |
| --- | --- | --- | --- |
| Restart after process exit | Operator or an added wrapper restarts it | `Restart=` and start limits provide restart policy | Cake restarts the child; systemd restarts the resident on abnormal exit |
| Logs | Terminal output, which can be redirected to a file | Journal captures stdout/stderr and service lifecycle | Resident journal, captured host output, runner transcript and structured lifecycle events |
| Identity | Record PID, arguments, revisions and environment yourself | Unit name, configuration, `MainPID` and `NRestarts`; add environment provenance as needed | Session plus Plan, configuration, build and target-profile identities; external Python environment still needs separate provenance |
| Admission check | No capsule admission check in a direct launch | A basic unit starts its configured executable; extra verification requires configuration | Resident admits a signed supervisor capsule named by the Plan's content identity |
| Setup | Upstream host and robot configuration | Host setup plus a service unit | Pinned host, adapter, release binaries, signing keys, Plan, resident configuration and service unit |
| Robot state after restart | LeRobot connects and enables torque | Same LeRobot connect behavior | Same LeRobot connect behavior; no Cake actuator gate |

If restarting the host and retaining stderr solve your problem, a plain
systemd service may be sufficient. The reason to evaluate this demo is
its additional admission and identity checks and its structured lifecycle
evidence. The [architecture](architecture.md) shows the actual boundaries.

## What the evidence establishes

The [claims map](claims.md) links assertions to captured observations or
development-host checks:

- **Admission:** a supervisor admitted only from a signed package under
  the configured trust policy (`docs/claims.md` row 1 and row 3).
  Modifying a byte in that capsule produces a refusal naming its content
  identity (`docs/claims.md` row 2). The signature does not cover the
  external LeRobot checkout, wrapper, dependencies or calibration.
- **Child restart:** the event trace shows a child exit and another
  launch with a restart ordinal (`docs/claims.md` row 6). The ordinary
  stop must target the resident because signaling only the child can
  trigger another launch (`docs/claims.md` row 11).
- **Resident relaunch:** after SIGKILL, a fresh session reports the same
  four declared identities, and the previous host does not survive
  (`docs/claims.md` row 7). This is fresh activation of the configured
  Plan; it does not establish restoration of application memory or a
  recording session.
- **Inspection:** three local read queries expose status, slots and
  lifecycle events (`docs/claims.md` row 5). These report software
  lifecycle facts, not motor state or end-to-end readiness.

The latest [v0.1.3 board record](demo-record.md#the-v013-run-on-the-tested-board-2026-09-13)
exercised host and resident failures with the host idle and teleoperation
skipped. It is evidence for those lifecycle behaviors, not for continuity
during teleoperation or recording.

## Understand the LeRobot baseline

The pinned host already catches errors while handling a message, logs
`Message fetching failed: ...` and continues its loop. Such an error does
not necessarily trigger a Cake restart. The host also exits when
`--host.connection_time_s` expires, printing `Cycle time reached.`;
supervision may restart that intentional exit too.

The demo uses LeRobot's `LeKiwiClient` through its own
[arm-only laptop example](../bin/laptop/teleop.py), because
`lerobot-teleoperate` at the pinned commit has no LeKiwi client type.
Protocol and ports are unchanged, but the supplied client and host
adapter are part of this bench workflow. The
[runbook](reproduce-end-to-end.md) lists the exact pin and clamp fix.

## Boundaries that matter to an adopter

The safe-stop template only writes a log line. If the host dies without
completing its disconnect path, nothing here stops the wheels through an
independent motor control path. Cake has no torque concept, and a
successful LeRobot reconnect enables torque. The demo therefore requires
the stand precautions in [Safety](../SAFETY.md). A successful software
restart is not evidence of actuator-safe recovery.

This is an evaluation of precompiled Cake binaries. The runtime source
and a buildable module SDK are not supplied. The public scripts and
documentation are under the repository's Business Source License terms,
with an Apache 2.0 exception for the laptop client; pull requests are
not accepted. Read [the license note](../LICENSE-NOTE.md),
[contribution policy](../CONTRIBUTING.md) and [limitations](../LIMITATIONS.md)
before deciding how to reuse the work.

## A useful first community evaluation

Run [segment 1](segment-1-stub.md), then use
[Diagnosing a run](diagnosing-a-run.md) to explain one injected failure
from its transcript and events. If you have a supported LeKiwi bench,
repeat with the live host under the documented precautions.

In your feedback, identify which question the evidence answered, which
manual checks remained necessary, and whether a plain service journal
would have been enough. A concrete reproduction and a clear account of
the missing evidence are more useful than a general endorsement of Cake.
