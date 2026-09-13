<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Documentation

kiwi-cake-demo lets you trigger and inspect process failures around the
Pi-side LeRobot host. Start with the [project introduction](../README.md)
or choose the question you want to answer below.

## Try and understand the demo

| I want to... | Start here |
| --- | --- |
| Try the lifecycle experiment without connecting a robot | [Segment 1: stub](segment-1-stub.md) |
| Understand the benefit and extra setup compared with systemd | [Why Cake under LeRobot?](why-cake-under-lerobot.md) |
| See which component owns each process and device | [Architecture](architecture.md) |
| Understand a failure or prepare a reproduction report | [Diagnosing a run](diagnosing-a-run.md) |
| Decide whether my board can run the binaries | [Supported targets](targets.md) |
| Explain the demo to another developer | [Presenter walkthrough](demo-walkthrough.md) |

## Use a robot

Read [Safety](../SAFETY.md) first. Then use the
[complete two-machine runbook](reproduce-end-to-end.md) for installation,
calibration and execution. The [segment 2 guide](segment-2-live.md)
explains the Pi-side runner in detail; [Stopping and cleanup](stopping-and-cleanup.md)
covers normal shutdown, verification and emergencies.

These are controlled bench experiments. A restart can enable torque.
Use the [LeRobot LeKiwi guide](https://huggingface.co/docs/lerobot/en/lekiwi)
for the upstream build, teleoperation, recording and policy workflows.

## Inspect the evidence and boundaries

| Question | Reference |
| --- | --- |
| Which assertions have evidence, and where was it obtained? | [Claims map](claims.md) |
| What did the recorded runs actually show? | [Demo record](demo-record.md) |
| What does the demo not establish? | [Limitations](../LIMITATIONS.md) |
| What do the lifecycle event names and arguments mean? | [Event codes](event-codes.md) |
| What is signed in a downloaded release? | [Verifying a release](verifying-a-release.md) |
| What do the precompiled binaries contain? | [Release binary inspection](release-internals.md) |
| What changed between releases? | [Release notes](../RELEASE_NOTES.md) |

## Give feedback or work on the tooling

[Contributing](../CONTRIBUTING.md) describes useful issue reports and the
current contribution policy. For a suspected vulnerability, use
[private security reporting](../SECURITY.md).

The public source includes the [runner scripts](../bin/),
[Pi adapters](../bin/pi/), [laptop client](../bin/laptop/),
[configuration templates](../bin/templates/) and [tooling tests](../tests/).
[Release assembly](../tools/build-release.sh) and
[binary inspection](../tools/strings-gate.sh) are maintainer tools;
the Cake runtime itself is binary-only.
