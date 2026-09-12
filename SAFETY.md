<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Safety: read this before segment 2

Segment 1 of this demo is torque-free. It supervises a harmless line-printing
stub and never names or opens a device. Nothing in segment 1 can move
anything. You do not need this page for segment 1.

Segment 2 supervises your own LeRobot host program as the child of the Cake
resident. That host owns the serial bus, the motors and the cameras. The
moment the host connects, it enables torque on every motor, and it does so
again on every reconnect, including the reconnects this demo deliberately
causes by killing processes. Cake has no torque or actuator concept and does
not disarm anything. That is the honest edge of this demo, stated in
`LIMITATIONS.md`, and it is why the preconditions below are mandatory.

## Mandatory physical preconditions

Confirm every one of these before starting segment 2, and confirm them again
before the crash-recovery beat:

- The robot is on a stand.
- The wheels are off the ground and cannot touch anything if they turn.
- The arm is parked low and physically supported.
- The leader arm is connected to the laptop, if you intend to teleoperate.
- One hand stays near the power switch for the whole run.
- Your own eyes stay on the arm through every torque transition. Cake's
  telemetry cannot see torque; you are the authoritative signal.

`bin/demo-segment2.sh` prints this list and refuses to continue until you
type, exactly:

```
ROBOT ON STAND, WHEELS OFF GROUND, ARM PARKED, HAND NEAR POWER
```

There is no flag, environment variable or configuration file that skips
this prompt.

## The hard rule

Any unexpected motion, any wheel movement, any re-arm you did not expect, or
anything that makes you uneasy means: stop the resident immediately and treat
it as a safety finding, not as a success.

```
bin/demo-stop.sh
```

If that is not fast enough, cut power. Report what you saw rather than
smoothing it over; the demo record this release is based on does the same.

## Stopping correctly

Stop the resident, never the child. Sending SIGINT or SIGTERM to the LeRobot
host alone does not stop the demo: the supervisor inside Cake treats that as
a child exit and immediately spawns a fresh host, which re-enables torque on
connect. The runbook-correct stop is the resident's own clean shutdown, which
quiesces the child as part of it. `bin/demo-stop.sh` does exactly that.

The clean stop sends your host SIGINT and, if it has not exited after a
short bounded deadline, ends it by force. Whether the motors are unpowered
after a stop is your host's own disconnect behaviour, not Cake's, and it is
not guaranteed if the host is slow to exit. Check the robot.

`docs/stopping-and-cleanup.md` is the ordered exit procedure: the motor
state after each step, the commands that prove nothing is left, the removal
of the user unit, and what to do if a stop hangs.

## What segment 2 will do to your robot

In order, each step waiting for you to press Enter:

1. Start the resident under a user-level systemd unit; the resident admits
   the signed package and spawns your host, which connects to the robot and
   enables torque.
2. Ask you to confirm that teleoperation works through the supervised host.
3. Send SIGTERM to the host only. Your configured safe-stop command runs
   once, then the supervisor spawns a fresh host, which enables torque again.
4. Kill the resident itself with SIGKILL. The host dies with it. systemd
   relaunches the resident, which admits the same package again and spawns a
   fresh host, which enables torque again.
5. Stop the resident cleanly.

Steps 1, 3 and 4 each end with the motors under torque. Keep your hand near
power through all three. After each start the runner waits up to two minutes
for your host to listen on its ports; torque may already be on during that
wait, and if the ports never appear the runner stops the unit.

## What the demo does not protect you from

- It does not disarm motors after a crash. See `LIMITATIONS.md`.
- It does not stop the wheels itself. If the host stops, the wheels stop
  because the host stops them, not because Cake does.
- It does not know what your safe-stop command does. The template shipped
  here only writes a log line; anything that touches the robot is yours to
  write and yours to test on a stand first.
