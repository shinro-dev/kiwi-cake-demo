<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Segment 2: supervising your own LeKiwi host

Opt-in. Moves the arm. Read `SAFETY.md` first; the runner will make you
type the acknowledgment it states.

## What you need

- The tested Pi 5 configuration (`docs/targets.md`), with `bin/doctor.sh`
  reporting the preflight shape `five checks green, observer refused`. The
  runner refuses any other board and has no override.
- A working LeKiwi host from your own LeRobot install. This demo ships no
  part of LeRobot except a small wrapper, `bin/pi/lekiwi_host_noninteractive.py`,
  which starts the stock host without its calibration prompt; the stock host
  cannot run as a supervised child because that prompt has no terminal to
  read from (`LIMITATIONS.md`). Calibrate the robot once by hand first; the
  wrapper then uses the calibration file without asking.
- `systemd --user` available, and `loginctl enable-linger $USER` run once if
  you want the resident to outlive your ssh session.
- Segment 1 passing on this board. Do not skip it.

## The two files you write

Copy the two templates and fill in the placeholders:

```
cp bin/templates/run-child.sh.in   bin/run-child.sh
cp bin/templates/safe-stop.sh.in   bin/safe-stop.sh
chmod 755 bin/run-child.sh bin/safe-stop.sh
```

`run-child.sh` is the child command the supervisor spawns. It must `exec`
your host directly, so the supervisor's pid tracking points at the Python
process and not at a lingering shell. The supervisor spawns any child with
four positional arguments of its own that mean nothing to a LeRobot host;
the template ignores them on purpose and hardcodes your host's real flags
instead. Forwarding them was the first thing the demo's original run got
wrong, and the host's argument parser rejected them before the robot was
ever opened. The six placeholders are your Python interpreter, your host
entry point, the robot id, the serial port, the camera list and the
connection time. Everything else stays as it is.

`safe-stop.sh` runs once for every child exit the supervisor observes, with
the log path the runner configured as its first argument. The template only
appends a line to that log and has nothing to fill in. If you make it touch
the robot, test it on a stand first, keep it short, and remember that the
supervisor ends it after a bounded deadline.

`bin/templates/run-child.sh.example` is the same template with every
placeholder filled with example values; copy its shape, not its values. Two
of them must match your own calibration: the robot id and the serial by-id
path.

The runner refuses to start while either file still carries a placeholder
outside its comment lines.

Two things about how your wrapper is started: the supervisor gives it a
minimal environment (a PATH of `/usr/bin:/bin` and nothing else), so use
absolute paths and export inside `run-child.sh` any variable your host
needs; and the runner expects the host to listen on ports 5555 and 5556
(set `KC_HOST_PORTS="a b"` for other ports). It refuses to start while
something already listens there, and it waits up to two minutes for the
ports to appear after each start, a window during which torque may already
be on; if they never appear it stops the unit and fails.

## The run

```
bin/demo-segment2.sh
```

1. The runner checks the board, the two files, and that a terminal is
   attached, prints the preconditions from `SAFETY.md`, and waits for you to
   type the acknowledgment exactly.
2. It builds the real package with `--child-command` naming your
   `run-child.sh`, stop signal SIGINT, and `--safe-stop-command` naming your
   `safe-stop.sh`, then runs the preflight against it, requiring the five
   green checks.
3. It writes `resident.conf`, installs
   `~/.config/systemd/user/kiwi-cake-demo.service` from the template with
   absolute paths, and reloads the user manager.
4. Beat 1, on Enter: `systemctl --user start`. Expected: `plan active`, and
   your host listening on ports 5555 and 5556. Torque is now on.
5. Beat 2: you type CONFIRMED once teleoperation works through the
   supervised host from the laptop with `bin/laptop/teleop.py`
   (`docs/reproduce-end-to-end.md`, the laptop side), or SKIP. This beat is your observation;
   the runner records what you typed and measures nothing.
6. Beat 3, on Enter: SIGTERM to the host only. Expected: the safe-stop event,
   then a fresh host with a new pid listening again, with the resident's pid
   and session identity unchanged. Torque is on again.
7. Beat 4, on Enter, after you re-confirm the preconditions: SIGKILL to the
   resident. Expected: the host dies with it (no orphan), systemd relaunches
   the resident, a fresh session identity is reported, and the four declared
   identities read back identical. A fresh host connects and torque is on
   again. This is the honest edge: Cake recovered its declared state; the
   actuators were re-armed by the host, not disarmed.
8. Stop: the runner stops the unit, which is what `bin/demo-stop.sh` does
   too. Expected: no resident, no host process, no socket, no listener on
   the ports. Whether the motors are unpowered afterwards is your host's
   own disconnect behaviour; the record's operator additionally checked by
   hand that the serial and camera devices were free.

Every beat is logged under `state/runs/<id>/`.

## Stopping, and the one thing not to do

Never stop the demo by signalling the host. SIGINT or SIGTERM to the host
alone is a child exit to the supervisor, which respawns the host at once,
torque and all. `bin/demo-stop.sh` stops the resident, whose own shutdown
quiesces the host.

`docs/stopping-and-cleanup.md` is the ordered exit procedure: the motor
state after each step, the commands that prove nothing is left, the removal
of the user unit, what to do if a stop hangs, and the one cosmetic journal
message to expect.
