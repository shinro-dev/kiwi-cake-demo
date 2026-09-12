<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Reproducing the demo end to end

This is the one runbook that covers both machines, in the order the steps
must happen: the Pi from an empty board to the first beat, the laptop from
an empty environment to a moving follower, then the beats and the stop.
`docs/segment-1-stub.md` and `docs/segment-2-live.md` are the Pi-side detail
of each segment; this page tells you when to open them.

## The tested configuration

| Item | What the demo was run with |
| --- | --- |
| Robot side | Raspberry Pi 5 Model B inside a LeKiwi (SO-101 arm, three wheels, two cameras, one servo controller) |
| Pi operating system | Raspberry Pi OS based on Debian 13 (trixie), 64-bit, default kernel (16 KiB pages), glibc 2.41 |
| Operator side | an SO-101 leader arm on an Ubuntu 24.04 laptop |
| LeRobot | commit `b4e2d0b61017a0db646a12c98a2df3837e37a1b9` (version 0.6.1) on both machines, with the one-line clamp fix of section 4b on the Pi |
| Network | one LAN, the laptop and the Pi on it, no firewall between them |
| Cake | the release named in the `VERSION` file of this checkout |

## What you need

Hardware: the four items above. Software on the Pi: the operating system
above with `git`, `openssl`, `gpg`, `tar` and either `curl` or the GitHub
CLI `gh`; a Python 3.12 or newer environment with `lerobot` and its `lekiwi`
extra. Software on the laptop: a Python 3.12 or newer environment with
`lerobot`, its `lekiwi` extra and, for the viewer, its `viz` extra; a clone
of this repository (only `bin/laptop/teleop.py` is used there).

## Network facts

- The Pi's address on the LAN is what `hostname -I` prints on the Pi. It is
  written `<pi-ip>` below.
- The LeKiwi host listens on TCP 5555 (commands in) and 5556 (observations
  out). The laptop connects to both. The demo runner checks that both are
  listening on the Pi after every start.
- Nothing may sit between the two machines that blocks those ports.

## Pi side

### 4a. Cake, segment 1

From the README's quick start:

```
git clone https://github.com/shinro-dev/kiwi-cake-demo.git
cd kiwi-cake-demo
bin/fetch-release.sh
bin/doctor.sh
tests/smoke-segment1.sh
```

`bin/doctor.sh` must classify the board `pi5-tested`, and the smoke test
must end with `KIWI-CAKE SEGMENT 1: PASS`. Do not go on until it does;
`docs/segment-1-stub.md` explains every step.

### 4b. LeRobot on the Pi

```
git clone https://github.com/huggingface/lerobot ~/lerobot
cd ~/lerobot
git checkout b4e2d0b61017a0db646a12c98a2df3837e37a1b9
python3 -m venv .venv
.venv/bin/pip install -e ".[lekiwi]"
```

The demo was run from a conda environment (Python 3.13) with LeRobot
installed from that checkout in editable mode. The venv route above was
repeated on the tested Pi on 2026-09-12 from the system Python 3.13: the
install completed and the host's modules imported; the host itself was not
run from that environment. One note from that run: a corrupt cached
download made `pip` fail twice with `IncompleteRead`, and
`pip install --no-cache-dir` got past it.

Then apply the clamp fix. At this LeRobot commit, the host's
`--robot.max_relative_target` clamp, which the child wrapper keeps for
safety, raises on every command and the follower never moves
(`LIMITATIONS.md`, "The stock host prompts for calibration on connect",
second paragraph). LeRobot fixed it in commit f66e512; the script applies
that one line to a checkout at b4e2d0b and touches nothing else:

```
~/kiwi-cake-demo/bin/pi/apply-lerobot-clamp-fix.sh --apply ~/lerobot
```

Expected: `clamp-fix: applied to ...lekiwi.py`. A LeRobot checkout at or
after f66e512 already carries the fix and the script reports `already
applied`; such a checkout was not exercised by this demo.

The interpreter the child wrapper needs is `~/lerobot/.venv/bin/python3`,
as an absolute path (the supervisor gives the child no `HOME`).

### 4c. Device names

```
ls -l /dev/serial/by-id/
ls -l /dev/v4l/by-id/
```

The first lists the servo bus adapter, the second the cameras; use the
`-video-index0` entry of each camera. These `by-id` names survive
re-enumeration, which the plain `/dev/ttyACM0` and `/dev/video0` names do
not.

### 4d. Calibrate the LeKiwi once, then prove the stock host by hand

```
~/lerobot/.venv/bin/lerobot-calibrate --robot.type=lekiwi --robot.id=<robot-id> \
  --robot.port=/dev/serial/by-id/<adapter> --robot.cameras='{}'
```

`<robot-id>` is a name you choose; the calibration file lands at
`~/.cache/huggingface/lerobot/calibration/robots/lekiwi/<robot-id>.json`.
The empty cameras object keeps the calibrate command from opening LeRobot's
default `/dev/video0` and `/dev/video2`, which may not be your cameras.

Then start the stock host by hand once, with exactly the flags your
`run-child.sh` is going to carry (see 4e for what each one is):

```
~/lerobot/.venv/bin/python -m lerobot.robots.lekiwi.lekiwi_host \
  --robot.id=<robot-id> --robot.port=/dev/serial/by-id/<adapter> \
  --robot.max_relative_target=15.0 --robot.disable_torque_on_disconnect=true \
  --robot.cameras='<your cameras JSON>' --host.connection_time_s=86400
```

If it asks `Press ENTER to use provided calibration file`, press Enter.
What you see next: a short burst of `WARNING:root:No command available`,
then `WARNING:root:Command not received for more than 500 milliseconds.
Stopping the base.`, then nothing: the host's informational lines are not
shown by default. Torque is on. Stop it with Ctrl-C: `Keyboard interrupt
received. Exiting...`, `Shutting down Lekiwi Host.`, and torque is off
again. This is the last time the prompt is answered by hand, and it is the
place to debug your flags: under Cake the host's output is counted by the
supervisor, not shown.

### 4e. The two files

```
cp bin/templates/run-child.sh.example bin/run-child.sh
cp bin/templates/safe-stop.sh.in bin/safe-stop.sh
chmod 755 bin/run-child.sh bin/safe-stop.sh
```

Edit `bin/run-child.sh` and replace every example value: the interpreter,
the host entry (`bin/pi/lekiwi_host_noninteractive.py` in this checkout,
as an absolute path), `--robot.id` (the `<robot-id>` you calibrated with),
`--robot.port` (the adapter's `by-id` path), and each camera's `by-id`
path, width, height, fps and rotation. Keep
`--robot.max_relative_target=15.0`, `--robot.disable_torque_on_disconnect=true`
and `--host.connection_time_s=86400`; the example's comments say why. The
wrapper skips the calibration prompt that would otherwise crash-loop the
host under the supervisor (`LIMITATIONS.md`). Check the file with
`bash -n bin/run-child.sh`. `safe-stop.sh` needs nothing filled in.

### 4f. Segment 2 up to Beat 1

```
bin/demo-segment2.sh
```

Type the acknowledgment from `SAFETY.md` exactly, then Enter at Beat 1.
Expected: `host pid <n> listening on 5555 5556`. Torque is on. Leave this
terminal at the Beat 2 prompt and go to the laptop.

## Laptop side

### 5a. LeRobot on the laptop

The same commit as 4b, with the viewer:

```
git clone https://github.com/huggingface/lerobot ~/lerobot
cd ~/lerobot
git checkout b4e2d0b61017a0db646a12c98a2df3837e37a1b9
python3 -m venv .venv
.venv/bin/pip install -e ".[lekiwi,viz]"
```

The clamp fix is host-side only; the laptop does not need it. Check the
client's imports without opening any device:

```
~/lerobot/.venv/bin/python ~/kiwi-cake-demo/bin/laptop/teleop.py --check-imports
```

Expected: `teleop: imports ok; lerobot 0.6.1 at ...`.

### 5b. The leader arm's port

```
ls -l /dev/serial/by-id/
```

If opening it is refused, add yourself to the `dialout` group and log in
again.

### 5c. Calibrate the leader arm once

```
~/lerobot/.venv/bin/lerobot-calibrate --teleop.type=so101_leader \
  --teleop.port=/dev/serial/by-id/<leader> --teleop.id=<leader-id>
```

The file lands at
`~/.cache/huggingface/lerobot/calibration/teleoperators/so_leader/<leader-id>.json`.

The trap, stated plainly: `<leader-id>` is the leader arm's own
calibration id and lives on the laptop; `<robot-id>` is the LeKiwi's and
lives on the Pi. They are two different files, and the client takes both.
LeRobot's documentation uses `my_awesome_leader_arm` and `my_awesome_kiwi`
as its examples; nothing requires those names, but the two must not be
confused with each other.

### 5d. Run the client

```
~/lerobot/.venv/bin/python ~/kiwi-cake-demo/bin/laptop/teleop.py \
  --remote-ip <pi-ip> --robot-id <robot-id> \
  --leader-port /dev/serial/by-id/<leader> --leader-id <leader-id>
```

The same four values can come from the environment as `KC_REMOTE_IP`,
`KC_ROBOT_ID`, `KC_LEADER_PORT` and `KC_LEADER_ID`. If the leader asks
`Press ENTER to use provided calibration file`, press Enter. Add
`--no-rerun` if you do not want the viewer.

Why a script and not `lerobot-teleoperate`: at this LeRobot commit that
command has no LeKiwi client among its `--robot.type` choices. Why the
script sends `x.vel`, `y.vel` and `theta.vel` as zero with every action:
the host indexes those three keys unconditionally, and an arm-only action
fails every message on the host (the base stays still either way).

### 5e. What you should see

On the laptop: `teleop: connected; the follower mirrors the leader; Ctrl-C
stops the client`, and, with the viewer, a window with the Pi's two camera
streams and the action plots. On the robot: the follower arm moves with the
leader within a second of the line above. Nothing else moves; the base
velocities are sent as zero. The leader arm is unpowered throughout: it
moves only when you move it.

### 5f. Safe first movements

A few degrees of wrist roll, then the gripper, watching the follower, then
the larger joints. Stay inside the range you calibrated. Any motion you did
not command means `SAFETY.md`: stop the resident and treat it as a finding.

### 5g. Before you stop the client

Move the leader so the follower rests low and supported. Then Ctrl-C.
Ctrl-C closes the client only: the follower keeps holding that pose under
the host's torque, and the host's watchdog stops the base within half a
second. The follower goes limp when the host exits cleanly, which is the
demo's stop beat; that is when an unparked arm falls. Expected line:
`teleop: client closed; the follower holds its pose under the host's torque
until the host exits`.

## The beats, back on the Pi

Beat 2: type `CONFIRMED`. Beat 3 sends SIGTERM to the host only: the
client on the laptop loses its connection, so start it again once the
runner reports the fresh host listening; the signalled host leaves torque as
it was and the new host enables it again on connect. Beat 4 kills the
resident: the same note, and re-confirm the preconditions when asked. What
each beat prints and checks is in `docs/segment-2-live.md`.

## The stop

Enter at STOP. Expected: `stopped: no resident, no host, no socket, no
listener`. The host's clean exit releases torque
(`--robot.disable_torque_on_disconnect=true`), so the arm must be low before
this beat. At any other time, `bin/demo-stop.sh` stops the resident the
same way. Removing the unit afterwards is in `docs/segment-2-live.md`.

## When it does not work

| What you see | Cause | What to do |
| --- | --- | --- |
| `EVT_SUPERVISOR_RESTART_BOUND_REACHED` right after Beat 1 and the host never listens (`bin/telemetry.sh` shows five child exits) | the stock host's calibration prompt; or a wrapper that forwards `"$@"`; or a camera entry without `"type"`; or a wrong `by-id` path | use `bin/pi/lekiwi_host_noninteractive.py` as the host entry; keep the example's shape; run the host by hand (4d) to see its own error |
| the host listens, then exits after about 30 seconds, and the restart bound is reached | `--host.connection_time_s` left at its default | set it to 86400 as the example does |
| the follower never moves; by hand the host prints `ERROR:root:Message fetching failed: 'x.vel'` | the client sent arm keys only | the shipped client sends the base keys; use it |
| the follower never moves; by hand the host prints `ERROR:root:Message fetching failed: 'arm_shoulder_pan.pos'` | the clamp defect of the pinned LeRobot commit | `bin/pi/apply-lerobot-clamp-fix.sh --apply ~/lerobot` (4b) |
| the client exits with `Timeout waiting for LeKiwi Host to connect expired.` | the host is not at Beat 1 yet, the address is wrong, or 5556 is blocked | check `ss -tln` on the Pi and `<pi-ip>` |
| the client asks to calibrate the leader, or the follower jumps at connect | the leader's id and the robot's id were swapped, or one is uncalibrated | 5c and 4d: two ids, two files |
| `something already listens on 5555 5556` | your own host is still running | stop it first; segment 2 must be the only host |

## The operator's live test

Everything above the beats is verified by the tests in this repository and
by the segment 1 smoke test on the board. The live teleoperation and beats
2 to 4 on the physical rig are the maintainer's own test, performed with
the robot on a stand under the preconditions in `SAFETY.md`; this page was
written from that run, and no automated test covers it.
