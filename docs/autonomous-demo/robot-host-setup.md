<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Robot host setup: starting the motor host under a holding arm

This page is the operator procedure for bringing up the camera-less LeKiwi
motor host that the single-cube local cycle talks to
(`single-cube-local-cycle.md`). It covers four tracked files:

| File | Runs on | Purpose |
| --- | --- | --- |
| `bin/pi/lekiwi_host_safe_attach.py` | robot computer | the stock LeKiwi host with a verify-first `configure`, a refusing `calibrate` and, in scan mode only, a `send_action` that drops the gripper key |
| `bin/pi/host_boundary.py` | robot computer | copied over on every start and RUN there, read-only, to check that the installed lerobot source still holds what the base path depends on. Its other half, the command-ownership gate, is imported only when the host is started with the word `boundary` (`host-boundary.md`) |
| `bin/pi/start-host.sh` | laptop | preflights the robot computer over ssh, copies both files into a new directory there, verifies each copy, checks the installed lerobot source, starts the host in the foreground |
| `bin/laptop/run-cycle.sh` | laptop | checks that this host runs and is not in scan mode, then runs one bounded action of the cycle |

These are the launchers that were demonstrated locally, with every identity
removed. Their behaviour against real hardware was demonstrated with the local
originals; the tracked copies were checked offline only (static tests,
`shellcheck`, stubbed `ssh`). The wrapper's executable code is identical to
the demonstrated one.

**Emergency stop is the servo power switch.** Cutting servo power drops the
arm: support it by hand if there is time, and keep clear of it if there is
not.

## 1. Private identities: where they live

Nothing in this repository names a robot. The two launchers take every
identity from the environment and refuse to run, without guessing and without
defaults, when one is missing:

| Variable | Meaning | Needed by |
| --- | --- | --- |
| `KC_PI_SSH` | ssh target of the robot computer (key-based login; the scripts use `BatchMode`, so a password prompt is a failure) | both |
| `KC_PI_IP` | address the LeKiwi client connects to | `run-cycle.sh` |
| `KC_ROBOT_ID` | the id the LeKiwi was calibrated with | both |
| `KC_SERVO_PORT` | servo bus device on the robot computer, given as its stable `/dev/serial/by-id/` entry, not as a `ttyUSB`/`ttyACM` number | `start-host.sh` |
| `KC_WRIST_CAMERA_ID` | stable identity of the wrist camera on the robot computer | `run-cycle.sh` |
| `KC_FRONT_CAMERA_ID` | stable identity of the front camera on the robot computer | `run-cycle.sh` |

Optional: `KC_PI_PYTHON` (absolute path of the python of the lerobot install
on the robot computer; the default is the miniconda python in the remote
account's home directory) and `KC_CYCLE_LOG_DIR` (where `run-cycle.sh` writes
its text log).

These launchers read the environment only; they do not parse any
configuration file. Keep the values in a private file that git never sees,
outside the checkout. Inside the checkout the name decides: `.gitignore`
covers `.local-dev/` and files named exactly `.env` or `.env.<something>`,
and it does NOT cover a name such as `robot.env`, which git would offer to
add. The file is plain `NAME=value` lines. The shape, with reserved example
values that name no real machine:

```bash
KC_PI_SSH=<account>@<robot-computer-address>
KC_PI_IP=192.0.2.10
KC_ROBOT_ID=<calibration-id>
KC_SERVO_PORT=/dev/serial/by-id/<servo-adapter-entry>
KC_WRIST_CAMERA_ID=/dev/v4l/by-id/<wrist-camera-entry>
KC_FRONT_CAMERA_ID=/dev/v4l/by-id/<front-camera-entry>
```

Load it into the shell that runs the launchers. With the file kept at
`~/kiwi-cake-demo-private/robot.env` (a suggested location, not one that any
script reads):

```bash
set -a; . ~/kiwi-cake-demo-private/robot.env; set +a
```

Rules that keep identities out of git and out of logs:

- Never commit that file, never paste its values into a tracked file, an
  issue, a pull request or a test. Examples use `192.0.2.x` and placeholders.
- The launchers never print a value: a refusal names the variable only.
- `start-host.sh` places `KC_ROBOT_ID`, `KC_SERVO_PORT` and `KC_PI_PYTHON`
  inside a command line that the remote shell parses. It does not escape
  them; it refuses any value with a character outside `A-Za-z0-9_./:@+=-`.
- The text log of `run-cycle.sh` and `host.txt` on the robot computer hold
  real robot output. `run-cycle.sh` refuses a log directory that is inside
  the checkout and not ignored by git.

## 2. Acquire the holding arm

The host does not pick the arm up. It attaches to an arm that **already holds
its pose**, and it refuses anything else. Before starting the host, the arm
must be in this state, which is exactly what the attach verifies by reads:

- all six arm motors: torque on, position mode, goal within 12 ticks (about
  1 degree) of the present position, and the PID coefficients equal to the
  ones the host configuration expects;
- scan mode only: the five joints as above, and the gripper with torque
  **off** (limp), which the host then never writes;
- all nine motors: Phase bit `0x10` clear;
- the three wheel motors: velocity mode, goal velocity 0, and a present
  velocity of at most one count (50 raw, either sign: a wheel standing
  still reads 0 or one count); their torque may be on or off.

The tool that brings a limp arm into that state is local operator tooling
and is not part of the tracked sources. This page therefore states the
required end state, not the acquisition commands. An arm that was acquired
and then left holding by a previous host (section 5) normally still is in
this state; the attach verifies it either way.

Do not start the stock host (`python -m lerobot.robots.lekiwi.lekiwi_host`)
against a holding arm: its `configure` switches torque off on all nine
motors, rewrites the configuration and switches torque back on, which is a
limp interval followed by a jerk.

## 3. Start the host

On the laptop, from any directory (the script finds the checkout from its own
location), with the identities loaded:

```bash
bin/pi/start-host.sh             # normal mode, the mode the cycle needs
bin/pi/start-host.sh scan        # scan mode: gripper limp and never written
bin/pi/start-host.sh boundary    # normal mode with the command-ownership gate (host-boundary.md)
```

Every start takes the same four steps and copies the same two files,
whichever words are given. The word `scan` changes the mode the host runs
in; the word `boundary` adds one read in step 3 and one variable to the
host's environment, and nothing else. What it does, in order:

1. **Preflight on the robot computer, reads only.** Refuses (exit 3) when a
   robot or motor host process already exists, when the `KC_SERVO_PORT` path
   is missing, when the servo bus device already has an owner, or when any
   video node has an owner. Two hosts on one bus corrupt each other's
   packets, and this host must own no camera.
2. **Copy two files.** Creates a new timestamped directory under
   `m1-host-attach/` in the remote account's home, copies
   `lekiwi_host_safe_attach.py` and `bin/pi/host_boundary.py` there and
   compares SHA-256 sums for each of them (exit 4 on a mismatch, and the
   refusal names which of the two it was). Nothing already on the robot
   computer is overwritten. The second file travels on every start because
   step 3 runs it there; copying it is not enabling anything.
3. **Read the installed lerobot source on the robot computer, reads only.**
   Runs the copy of `host_boundary.py` under the very interpreter the host
   will run with, because the lerobot on this laptop is not the code that
   will execute. Without the word `boundary` it reads ONE file,
   `robots/lekiwi/lekiwi.py`, where `LeKiwi.send_action` lives: that file
   carries two local changes the base path depends on, and a `git checkout`,
   a pull or a reinstall on the robot computer takes them away again in
   silence. With the word it also reads `robots/lekiwi/lekiwi_host.py`, the
   stock loop the gate patches. Every pass that was asked for runs, so the
   operator is told everything that drifted at once, and the host is not
   started unless every pass that ran holds (exit 3 otherwise). A pass that
   could not be run at all refuses the start too, and the refusal says so
   rather than blaming the installed source. `host-boundary.md`,
   "Before it is turned on", gives both passes in full and says what to do
   about a `MISMATCH`; `LIMITATIONS.md` names the two local changes and the
   script that writes them back.
4. **Host in the foreground of this terminal**, over `ssh -t`, with
   `--robot.cameras={}`, `--robot.disable_torque_on_disconnect=false`,
   `--robot.max_relative_target=15.0` and a 24 hour connection time. The
   wrapper refuses to start without the first two flags, and refuses again
   after parsing if the parsed configuration would drop torque on disconnect
   or owns a camera. Output is filtered of warnings and also written to
   `host.txt` in that remote directory.

Expected lines, in this order: `BASE ZERO-SPEED HOLD: ...`, two
`SAFE ATTACH OK ...` lines (the second names the raw present velocity each
wheel read: 0 or one count, 50, of either sign), then the stock host's
`Waiting for commands...`.
With the word `boundary`, `HOST BOUNDARY ON` as well. Keep the terminal open
for as long as the host is needed.

Exit status before the host, and it says the same thing whichever step
produced it:

| Status | What it means |
| --- | --- |
| 5 | a usage error, or a missing or unsafe identity, or `bin/pi/host_boundary.py` is missing from this checkout; nothing was contacted |
| 3 | a preflight refused (step 1); or a pass of step 3 ran and the installed lerobot source does not hold what that pass reads it for; or step 3 could NOT BE RUN on the robot computer. The last two both refuse, and the refusal says which one it was, because a check that never reached a verdict is no verdict about the installed source |
| 4 | a copied file does not match its local original (step 2) |
| 255 | ssh could not reach the robot computer, in any of the three steps before the host. Step 3 turns that status into a refusal of its own rather than reading it as drift, so 255 is never a statement about the installed lerobot |

Any other status before the host belongs to the `ssh` or the `scp` that
failed, a copy that did not go through among them.

**The exit status of `start-host.sh` does not tell whether the attach
passed.** The remote command is a pipeline that ends in `tee`, as it was in
the demonstrated launcher, so a host that printed `SAFE ATTACH REFUSED` and
exited with status 3 still makes `start-host.sh` return 0. Read the output:
`SAFE ATTACH OK` or `SAFE ATTACH REFUSED`.

## 4. What the host writes at startup

The startup is **verify-first, and it is not read-only**.

| Target | Written at attach | Condition |
| --- | --- | --- |
| Any arm register (six motors) | never | the arm is only read |
| Calibration registers (any motor) | never | `calibrate` is replaced by a refusal that exits |
| Wheel `Goal_Velocity` = 0 | yes | only for a wheel whose torque is off, and only after every check passed |
| Wheel `Torque_Enable` = 1 | yes | same wheel, only after its `Goal_Velocity` read back as 0 |
| A wheel that already holds | never | it is only read back |

The base write is the zero-speed wheel hold. After a power cycle the wheel
motors come up with torque off, and the wheels are then free. Holding each
wheel at zero speed does not remove the velocity dither: in 736 reads under the
hold, 127 showed one count on one wheel and 4 on two wheels at once. The
base-zero guard judges one count per wheel as rest (decision log, 2026-09-27),
and so does the attach itself (decision log, 2026-10-01): it refuses a wheel
that reads more than one count or whose goal velocity is not 0.
The order
(zero target first, torque second, then a readback of mode, goal velocity and
torque on all three wheels) means a wheel is never energised with a non-zero
target.

If any check fails, the process prints `SAFE ATTACH REFUSED`, closes the port
with torque untouched and exits with status 3 without having written
anything; it never falls back to the stock `configure`. One inexact message
is kept from the demonstrated wrapper: if a base readback fails *after* the
base writes began, the same `nothing was written` line is printed although
the base writes made up to that point stand. The arm is unaffected in every
case.

After the attach, the running host writes whatever the connected client
commands, as the stock host does (in scan mode, without the gripper key).
Nothing in this wrapper restricts which client may send commands.

## 5. Stop the host without dropping the arm

Press **Ctrl-C once in the host terminal.** The host was started with
`disable_torque_on_disconnect=false`, so its disconnect leaves torque as it
is: the arm keeps holding its last goal, and a later `start-host.sh` can
attach to it again without a new acquisition.

- Do not stop the host while the base is moving. A process that is killed
  (closed terminal, dropped ssh session, `kill`) writes nothing on its way
  out: the arm keeps holding, and nothing sets a wheel's velocity target back
  to zero. Stop motion first; if the base is moving and the host is gone, use
  the servo power switch.
- Do not switch servo power off to stop the host: that drops the arm.
- Releasing the arm is a separate, deliberate step with the arm supported by
  hand, done with the same local operator tooling as the acquisition.
- The preflight of the next start refuses while the old host, or anything
  else, still owns the servo bus.

## 6. Run the cycle against the host

```bash
bin/laptop/run-cycle.sh observe
bin/laptop/run-cycle.sh cycle
bin/laptop/run-cycle.sh pan:+2.2 --plan-only
```

Before commanding anything it asks the robot computer, in one ssh call,
whether a `lekiwi_host_safe_attach.py` process runs and whether its
environment carries `KC_SCAN_MODE=1`. It refuses (exit 3) when the host is
not running, when it runs in scan mode (the gripper is limp and its key is
stripped, so a grasp would close nothing), when the environment of any
process that names the host cannot actually be read (the mode is then
unknown), or when the ssh call itself fails. Actions, preconditions and the floor space
the `cycle` and `base:` actions need are in `single-cube-local-cycle.md`. The
runner keeps its local calibration and observations under the ignored
`.local-dev/` directory of the checkout it runs from.

## 7. Known limits of these preflights

- "Host running" is a match on process command lines that mention the
  wrapper's file name. Another process on the robot computer whose command
  line mentions that name would be taken for the host.
- The camera preflight looks at video nodes at start time only. It does not
  stop a camera from being opened later by another process, which is what
  the one-frame captures of the cycle do on purpose.
- The wrapper was written against lerobot at commit b4e2d0b6. `configure`,
  `calibrate` and `send_action` are replaced by name; check that they still
  have the same shape before using another lerobot version.
