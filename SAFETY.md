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
a child exit and can spawn a fresh host, which re-enables torque on
connect. The runbook-correct stop is the resident's own clean shutdown, which
quiesces the child as part of it. `bin/demo-stop.sh` does exactly that.
So does `systemctl --user stop kiwi-cake-demo.service`: the unit the runner
installs sets `KillMode=mixed`, so systemd's stop signals the resident only,
and the host hears from the resident's shutdown, not from systemd.

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

- It does not disarm motors after a crash. See [Limitations](LIMITATIONS.md).
- Segment 2 does not stop the wheels itself. Its safe-stop is the template
  copied with its wheel settings left empty, and then it only writes a log
  line. A hard host death can skip LeRobot's disconnect path, and the
  host's watchdog cannot run after its process has died. Neither a child
  exit nor a safe-stop log line proves a motor stop or torque-off.
- It does not know what your safe-stop command does. Anything you make it
  do that touches the robot is yours to write and yours to test on a stand
  first.

## The wheel stop of the LeKiwi deployment

`bin/pi/lekiwi-deploy.sh install` renders the same template with its four
wheel settings filled in. After every host or capability exit the
supervisor observes, the safe-stop then runs `bin/pi/wheels_safe_stop.py`,
which writes goal velocity 0 to the three wheel servos and reads them back.
It addresses one gap: after a host dies without running its disconnect
path (SIGKILL, SIGTERM, which the pinned host does not handle, or a crash
that skips it), nothing wrote the wheels again, and a wheel in velocity
mode keeps its last goal velocity.

Only a release that install rendered this way has the wheel stop. A
release installed before it keeps a safe-stop that only logs, and an
install that refuses, for example on the self-check below, leaves the
previous release current. `bin/pi/lekiwi-deploy.sh arm` refuses to arm a
current release whose safe-stop does not run the wheel stop, whose wheel
stop file is missing, or whose safe-stop and host wrapper do not both
name the wheel stop's lock (below): run install again. `status` says
whether the current release has the wheel stop. Both read the rendered
files; neither runs the wheel stop.

Its limits:

- It writes goal velocity 0 to servos 7, 8 and 9, the wheels as LeRobot's
  LeKiwi numbers them: one sync write whose data names only those three,
  then an acknowledged write to each, repeated once, while its time budget
  allows, for a wheel that does not acknowledge it. It never reads or
  writes a register of an arm servo. It then reads back each wheel's goal
  velocity and operating mode, and a wheel counts as stopped only when
  they read 0 and velocity mode. It writes no torque register, with one
  exception: a wheel that does not count as stopped gets its torque
  switched off, while its time budget allows, so that it is no longer
  driven. That wheel then turns freely until an armed host start holds it
  again; the host refuses to start on a wheel that is not in velocity mode.
- It may leave a wheel's torque on. Whether writing a goal velocity to a
  wheel servo whose torque is off switches that torque on has not been
  measured on this robot's servos, so treat every wheel stop as one that
  can switch the wheels' torque on. Its log reports the torque it reads
  after its writes, never the torque from before. A wheel in velocity
  mode switched on this way would hold at zero speed.
- A wheel that is not in velocity mode is not stopped by a zero goal
  velocity, and a write that switches its torque on could drive it toward
  its stored goal position. The wheel stop writes before it reads the
  mode, so such a wheel can move from the first write until its torque is
  switched off, a few bus exchanges later, and the run ends NOT VERIFIED.
  A wheel whose mode cannot be read is treated the same way. A wheel whose
  mode is not read before the time budget ends is reported NOT VERIFIED,
  with no time left to switch its torque off. This can happen after a
  wheel servo is replaced or reconfigured: a replacement may come in
  another mode, and nothing in this deployment sets the mode. The host
  refuses to start on such a wheel, and the wheel stop runs after that
  refused start and after each held restart.
  `bin/pi/wheels_safe_stop.py --status`, run by hand with the unit
  stopped, reads the torque, mode and velocities of the three wheels and
  writes nothing: run it after any servo change.
- It never writes while a host that `run-host.sh` started holds the servo
  bus or is about to open it. That wrapper takes the wheel stop's lock,
  `~/kc-lekiwi/run/wheels-safe-stop.lock`, before its own owner check,
  and its exec hands the lock to the host, which holds it until it exits.
  The wheel stop writes only while it holds the same lock, so it cannot
  write in the seconds between the wrapper's check and the host opening
  the port. A host start that finds a wheel stop still running waits up
  to 2 s for it, then refuses to start: arm again. While a host lives, a
  capability exit's wheel stop waits out its lock wait, about a quarter
  of a second, and writes nothing: the live host stops the base with its
  own watchdog. The lock does not cover a program that opens the bus
  without it, such as a bench tool or a host started any other way: only
  the owner check below keeps the wheel stop off such a program's bus,
  and only when that program already holds the device when the check
  runs. Nor does it cover a process the host starts, which would inherit
  the lock and keep it after the host exits, so that no wheel stop writes
  until that process ends; the pinned host starts none, as far as its
  source shows.
- It writes nothing when the servo device is missing, when its lock is
  held for its whole wait or cannot be taken, when another process holds
  the device open (a bench tool, for example), when its own owner check
  cannot be trusted, or when the servo library cannot be loaded or the
  device cannot be opened. The owner check reads the process table once,
  before the port is opened, and sees only processes of its own account.
- It is bounded, and therefore best effort. The supervisor ends a
  safe-stop that is still running about half a second after its start
  (measured on the development build of the supervisor, not yet on the
  robot). The wheel stop runs under a hard limit of 0.42 s and writes the
  zero before it reads anything back. `bin/pi/lekiwi-deploy.sh install`
  refuses a robot computer whose python, run as the wheel stop runs, is
  not ready to open the servo bus within 300 ms of its start: the wheel
  stop's `--self-check` times the start, the owner check and the servo
  library's import, and opens nothing. It does not time opening the
  port, and the robot computer can be busier later than at the install,
  so on a slow or overloaded robot computer the wheel stop can still be
  ended before it has written.
- It runs only when the supervisor observes an exit. When the resident
  itself dies (a kill, a crash, the stop timeout), no safe-stop runs for
  that exit and the host dies with the resident. systemd restarts the
  resident (`Restart=on-abnormal`) only when a signal other than SIGHUP,
  SIGINT, SIGTERM or SIGPIPE ended it, such as SIGKILL or the SIGSEGV of
  a crash: its first host start is then held for want of an arm token,
  and that exit runs the wheel stop, some seconds later (not measured).
  A resident that exits with an error status, or dies during a stop, is
  not restarted, and no wheel stop runs after it. A stopped unit
  restarts nothing.
- It cannot reach a servo that no longer answers, a USB adapter that is
  gone, or anything at all when the robot computer has no power.
- Its log is evidence of the register writes and readbacks, not of wheels
  at rest: look at the wheels. A line it cannot write (a full disk) is
  lost, and the writes go on. The logs are
  `~/kc-lekiwi/logs/safe-stop-*.log`, and `bin/pi/lekiwi-deploy.sh status`
  prints the tail of each.

It is tested offline against a simulated bus. It has not yet been run on
the robot: test it on the stand with the wheels in the air before relying
on it, and keep every precondition above.

## The safe attach's wheel check

When `bin/pi/lekiwi-deploy.sh arm` starts the host, its safe attach
(`bin/pi/lekiwi_host_safe_attach.py`) reads the three wheels before it
writes anything. It refuses, writing nothing, a wheel whose present
velocity reads more than one velocity count (50 raw, either sign, about
3.8 mm/s at the rim) or whose goal velocity is not exactly 0. One count is
the dither of a wheel standing still, which the exact-zero check it had
before refused (decision log, 2026-10-01). A wheel turning that slowly
reads like rest at that instant; the attach then ends with every wheel
read back in velocity mode, goal velocity 0, torque on.

Only `bin/pi/lekiwi-deploy.sh install` deploys the host that `arm`
starts. install refuses a host without this check, `arm` refuses a
current release whose host lacks it (run install again), and `status`
says which check the current host carries, with the host file's sha256.
`bin/pi/start-host.sh` can still start a stand-alone host, copied from
the checkout that runs it, with no install. Such a host never refuses
less than this check: from a checkout that predates it, it refuses
more, because every earlier version of the attach refused any wheel
velocity but 0. The check is tested offline against
a simulated bus; it awaits its attended re-proof on the robot.
