#!/usr/bin/env python3
# Copyright 2026 Shinro SAS (modifications). Copyright 2025 The HuggingFace Inc. team.
# Licensed under the Apache License, Version 2.0; see THIRD_PARTY_LICENSES/lerobot/LICENSE.
# Derived from LeRobot's examples/lekiwi/teleoperate.py. This one file is not
# under the Business Source License that covers the rest of this repository.
"""Teleoperate a LeKiwi from an SO-101 leader arm, from the laptop.

Why a script: at the lerobot version this demo was run with (commit
b4e2d0b, version 0.6.1) the lerobot-teleoperate command has no LeKiwi
client among its --robot.type choices, so the client is driven from a
script, as LeRobot's own example does.

What differs from the example:

1. The example drives the base from the keyboard, and its keyboard helper
   always supplies the base keys. This script has no keyboard teleop, so
   it supplies them itself: every action carries x.vel, y.vel and
   theta.vel set to 0.0 beside the arm .pos keys. The host indexes those
   three keys unconditionally (LeKiwi.send_action); an arm-only action
   makes the host log "Message fetching failed: 'x.vel'" on every message
   and the follower never moves.
2. The connects, the viewer start and the control loop run inside one
   try/finally, so Ctrl-C and any failure close the leader arm's serial
   port and the client's sockets, each on its own: a disconnect that
   fails is reported as `teleop: <device> disconnect failed: <reason>`
   and the exit status is nonzero.

What Ctrl-C does not do: the leader arm is unpowered for the whole session
(lerobot disables its torque at connect), and the follower keeps holding
its last commanded pose under the host's torque until the host itself
exits. Park the follower low, by moving the leader low, before you stop
the client and before the demo's stop beat: the arm goes limp when the
host exits cleanly, and that is when it can fall.

The robot id and the leader arm's id are two different calibration ids:
the robot's names the LeKiwi calibration file on the Pi, the leader's
names the leader arm's own file on this laptop.

Arguments, each also readable from an environment variable:
  --remote-ip      KC_REMOTE_IP    the Pi's address on your LAN
  --robot-id       KC_ROBOT_ID     the id the LeKiwi was calibrated with
  --leader-port    KC_LEADER_PORT  the leader arm's /dev/serial/by-id/ path
  --leader-id      KC_LEADER_ID    the leader arm's own calibration id
  --fps            control loop rate, 1 to 1000 (default 30)
  --no-rerun       do not open the rerun viewer
  --check-imports  import what this script needs from lerobot and exit
                   (0 ok, 3 lerobot missing, 4 a name is missing);
                   opens no device
"""

import argparse
import os
import sys
import time

# The base stays idle: zero velocity on every axis, sent with every action.
BASE_IDLE_ACTION = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}

# The two lines the runbooks quote (docs/stopping-and-cleanup.md, docs/reproduce-end-to-end.md).
CONNECTED_LINE = "teleop: connected; the follower mirrors the leader; Ctrl-C stops the client"
CLOSED_LINE = "teleop: client closed; the follower holds its pose under the host's torque until the host exits"


def fps_type(text):
    """--fps must be an integer from 1 to 1000; anything else is a usage error
    (exit 2) raised by argparse before any lerobot import or device open."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer between 1 and 1000, got {text!r}") from None
    if value < 1 or value > 1000:
        raise argparse.ArgumentTypeError(f"must be an integer between 1 and 1000, got {value}")
    return value


def build_parser():
    p = argparse.ArgumentParser(
        prog="teleop.py",
        description="Teleoperate a LeKiwi (running under Cake) from an SO-101 leader arm.",
    )
    p.add_argument("--remote-ip", default=os.environ.get("KC_REMOTE_IP"), help="the Pi's address (env KC_REMOTE_IP)")
    p.add_argument("--robot-id", default=os.environ.get("KC_ROBOT_ID"), help="the LeKiwi's calibration id (env KC_ROBOT_ID)")
    p.add_argument("--leader-port", default=os.environ.get("KC_LEADER_PORT"), help="the leader arm's /dev/serial/by-id/ path (env KC_LEADER_PORT)")
    p.add_argument("--leader-id", default=os.environ.get("KC_LEADER_ID"), help="the leader arm's calibration id (env KC_LEADER_ID)")
    p.add_argument("--fps", type=fps_type, default=30, help="control loop rate, 1 to 1000 (default 30)")
    p.add_argument("--no-rerun", action="store_true", help="do not open the rerun viewer")
    p.add_argument("--check-imports", action="store_true", help="import what this script needs from lerobot and exit; opens no device")
    return p


def check_imports():
    try:
        import lerobot
    except ImportError as e:
        print(f"teleop: lerobot is not importable in this Python: {e}", file=sys.stderr)
        return 3
    try:
        from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig  # noqa: F401
        from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig  # noqa: F401
        from lerobot.utils.robot_utils import precise_sleep  # noqa: F401
        from lerobot.utils.visualization_utils import init_rerun, log_rerun_data  # noqa: F401
    except ImportError as e:
        print(f"teleop: a lerobot name this script needs is missing: {e}", file=sys.stderr)
        return 4
    print(f"teleop: imports ok; lerobot {getattr(lerobot, '__version__', '?')} at {lerobot.__file__}")
    return 0


def close_devices(devices):
    """close_devices(((name, device, connected), ...)) -> the names whose disconnect() raised.

    Every device whose connect() returned gets its own disconnect() call,
    independently of the others: a raise in one never skips the next."""
    failed = []
    for name, device, connected in devices:
        if not connected:
            continue
        try:
            device.disconnect()
        except Exception as e:  # noqa: BLE001 - reported; the next device still gets its turn
            failed.append(name)
            print(f"teleop: {name} disconnect failed: {e}", file=sys.stderr, flush=True)
    return failed


def main(argv=None):
    p = build_parser()
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    if args.check_imports:
        sys.exit(check_imports())
    missing = [n for n, v in (("--remote-ip", args.remote_ip), ("--robot-id", args.robot_id), ("--leader-port", args.leader_port), ("--leader-id", args.leader_id)) if not v]
    if missing:
        p.error("missing " + ", ".join(missing) + " (or the matching KC_* environment variables)")

    try:
        from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig
        from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
        from lerobot.utils.robot_utils import precise_sleep
    except ImportError as e:
        sys.exit(f"teleop: lerobot is not importable in this Python ({e}); install it with the lekiwi extra, see docs/reproduce-end-to-end.md")
    use_rerun = not args.no_rerun
    if use_rerun:
        try:
            import rerun  # noqa: F401
        except ImportError:
            sys.exit("teleop: rerun is not installed; install lerobot's viz extra or pass --no-rerun")
        from lerobot.utils.visualization_utils import init_rerun, log_rerun_data

    robot = LeKiwiClient(LeKiwiClientConfig(remote_ip=args.remote_ip, id=args.robot_id))
    leader_arm = SO101Leader(SO101LeaderConfig(port=args.leader_port, id=args.leader_id))

    # Each flag is set only after that device's own connect() returned, so
    # the finally below never calls disconnect() on a device that never
    # connected, and a device that did connect is closed whatever happened
    # to the other one, to the viewer, or in the loop.
    robot_connected = False
    leader_connected = False
    failed = []
    try:
        robot.connect()
        robot_connected = True
        leader_arm.connect()
        leader_connected = True

        if use_rerun:
            init_rerun(session_name="cake_demo_teleop")

        if not robot.is_connected or not leader_arm.is_connected:
            raise ValueError("Robot or leader arm is not connected!")

        print(CONNECTED_LINE, flush=True)
        while True:
            t0 = time.perf_counter()

            observation = robot.get_observation()

            arm_action = leader_arm.get_action()
            arm_action = {f"arm_{k}": v for k, v in arm_action.items()}

            action = {**arm_action, **BASE_IDLE_ACTION}
            robot.send_action(action)

            if use_rerun:
                log_rerun_data(observation=observation, action=action)

            precise_sleep(max(1.0 / args.fps - (time.perf_counter() - t0), 0.0))
    except KeyboardInterrupt:
        pass
    finally:
        # The leader first (it only releases a serial port), then the client.
        failed = close_devices((("leader", leader_arm, leader_connected), ("robot", robot, robot_connected)))
        if not failed and (robot_connected or leader_connected):
            print(CLOSED_LINE, flush=True)
    if failed:
        sys.exit(f"teleop: {' and '.join(failed)} did not disconnect cleanly; check the devices before the demo's stop beat")


if __name__ == "__main__":
    main()
