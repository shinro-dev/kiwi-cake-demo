<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Why Cake sits under the LeKiwi teleoperation host

This page explains what the demo shows and why, for people who run LeRobot.
It is held to the same standard as the demo record: every capability
sentence here is one of the rows in `docs/claims.md`, and where a thing was
not measured, this page says so.

## The setup

A LeKiwi robot: a Raspberry Pi 5, one servo controller driving a six-joint
SO-101 arm and three wheels, two cameras. Teleoperated from a laptop with a
leader arm, using the stock LeRobot client over the stock ZMQ protocol on
ports 5555 and 5556. The laptop side is completely unchanged. If you run
LeKiwi teleoperation today, your operator side needs no modification to run
this.

## What the stock stack does not have

In standard LeRobot teleoperation the robot runs a single control program,
the LeKiwi host, which owns the serial bus and the cameras and speaks the
wire protocol. It runs as a single process with no supervision and no
recovery. If the host crashes, hits a parse error, or is killed, it exits
and nothing restarts it. Someone has to notice, ssh in, and bring it back by
hand. There is no record of what happened.

For a bench rig this is fine. For anything that runs unattended or near
people it is a gap, and it is a gap in the process model, not in the
control code.

## What Cake adds, and what it does not change

Cake is a resident control layer that runs on the robot underneath the same
LeKiwi host. It does not replace the host and it does not touch the wire
protocol. It launches the host as a supervised child process and owns its
lifecycle. Everything above the host is untouched; everything below it gains
a control plane.

Running on the LeKiwi, Cake:

- Admits only signed packages. The host runs as the child of a
  signed package that Cake verifies before launching anything. A package
  whose stored bytes have been changed is refused at start, and the refusal
  names the content identity.
- Supervises and restarts. When the host dies, Cake records the exit, runs
  the safe-stop command you configured, and spawns a fresh host under a
  bounded restart policy. What your safe-stop command does is yours; the
  demo record shows only that it ran.
- Comes back from a hard crash. Kill the control layer itself with SIGKILL
  and it is relaunched, restores the deployment it had declared byte for
  byte, reports a fresh session identity, and spawns the host again.
- Reports live. While it runs, you can ask what is running and what is
  bound to what, and read a structured event trace decoded by name.

## What this demo does not show, stated plainly

After a hard crash the ideal is that the actuators come back disarmed,
requiring a deliberate act to move again. Cake recovers its own state
correctly, but in this demo the actuators are owned by the stock LeRobot
host, which re-enables torque on every connect as a matter of course. Cake
has no torque concept to override that. The record states this boundary
from the LeRobot host's own connect code; the crash test on a stand, under
the preconditions in `SAFETY.md`, exercised the recovery but took no
bus-level torque measurement. We report it rather than claim a guarantee
this demo does not have.

The live replacement of a running module is not part of this demo either.

When the host stops, the wheels stop because the host stops them, not
because Cake does.

## The one-line version

Same laptop, same robot, same open protocol. The robot side now has a
supervision and recovery layer the stock stack does not have. Crash the
controller and, instead of a dead process someone revives by hand, the
resident comes back, the host is respawned, and the event trace tells you
what happened.
