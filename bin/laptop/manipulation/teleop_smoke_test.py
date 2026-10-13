#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Manipulation teleop smoke test: SO-101 leader arm -> LeKiwi arm/gripper,
base velocity hard-zeroed through the shared manipulation action adapter on
every single physical command. NOT the Cake-demo teleop script
(bin/laptop/teleop.py, untouched) -- this is a separate, manipulation-only
entry point so the shared autonomy/manipulation adapter is exercised the same
way here as in recording and evaluation, per the project's "one rule" boundary.

No keyboard teleoperator exists in this script at all: there is no control
surface that could drive the base, even by accident. The only way base
velocity ever reaches the robot is the adapter's own literal 0.0 constants.

Arguments, each also readable from an environment variable:
  --remote-ip      KC_REMOTE_IP     the Pi's address on your LAN
  --robot-id       KC_ROBOT_ID      the id the LeKiwi was calibrated with
  --leader-port    KC_LEADER_PORT   the leader arm's /dev/serial/by-id/ path
  --leader-id      KC_LEADER_ID     the leader arm's own calibration id
  --fps            control loop rate, 1 to 1000 (default 30)
  --no-rerun       do not open the rerun viewer
  --check-imports  import what this script needs from lerobot and exit
                   (0 ok, 3 lerobot missing, 4 a name is missing);
                   opens no device

Purpose: prove leader movement -> follower arm movement and leader gripper ->
follower gripper, with the base stationary, before recording anything. Run it
only after the leader arm's calibration has been freshly verified (do not
reuse a stale calibration file) -- see the leader-calibration procedure this
session's report names separately. Ctrl-C to stop; the leader is unpowered
for the whole session (lerobot disables its torque at connect) and the
follower keeps holding its last commanded pose under the host's torque until
the host itself exits, exactly as bin/laptop/teleop.py's own docstring warns.
"""

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))


def fps_type(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer between 1 and 1000, got {text!r}") from None
    if value < 1 or value > 1000:
        raise argparse.ArgumentTypeError(f"must be an integer between 1 and 1000, got {value}")
    return value


def build_parser():
    p = argparse.ArgumentParser(
        prog="teleop_smoke_test.py",
        description="Manipulation-only teleop smoke test: SO-101 leader -> LeKiwi arm/gripper, base hard-zeroed.",
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
        print(f"teleop_smoke_test: lerobot is not importable in this Python: {e}", file=sys.stderr)
        return 3
    try:
        from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig  # noqa: F401
        from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig  # noqa: F401
        from lerobot.utils.robot_utils import precise_sleep  # noqa: F401
        from lerobot.utils.visualization_utils import init_rerun, log_rerun_data  # noqa: F401
        from action_adapter import manipulation_action_to_robot_command  # noqa: F401
    except ImportError as e:
        print(f"teleop_smoke_test: a required name is missing: {e}", file=sys.stderr)
        return 4
    print(f"teleop_smoke_test: imports ok; lerobot {getattr(lerobot, '__version__', '?')} at {lerobot.__file__}")
    return 0


def close_devices(devices):
    failed = []
    for name, device, connected in devices:
        if not connected:
            continue
        try:
            device.disconnect()
        except Exception as e:  # noqa: BLE001 - reported; the next device still gets its turn
            failed.append(name)
            print(f"teleop_smoke_test: {name} disconnect failed: {e}", file=sys.stderr, flush=True)
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
        sys.exit(f"teleop_smoke_test: lerobot is not importable in this Python ({e})")
    from action_adapter import InvalidManipulationAction, manipulation_action_to_robot_command

    use_rerun = not args.no_rerun
    if use_rerun:
        try:
            import rerun  # noqa: F401
        except ImportError:
            sys.exit("teleop_smoke_test: rerun is not installed; install lerobot's viz extra or pass --no-rerun")
        from lerobot.utils.visualization_utils import init_rerun, log_rerun_data

    robot = LeKiwiClient(LeKiwiClientConfig(remote_ip=args.remote_ip, id=args.robot_id))
    leader_arm = SO101Leader(SO101LeaderConfig(port=args.leader_port, id=args.leader_id))

    robot_connected = False
    leader_connected = False
    failed = []
    try:
        robot.connect()
        robot_connected = True
        leader_arm.connect()
        leader_connected = True

        if use_rerun:
            init_rerun(session_name="kiwi_manipulation_teleop_smoke_test")

        if not robot.is_connected or not leader_arm.is_connected:
            raise ValueError("Robot or leader arm is not connected!")

        print("teleop_smoke_test: connected; the follower arm/gripper mirrors the leader, base is hard-zero; Ctrl-C stops the client", flush=True)
        while True:
            t0 = time.perf_counter()

            observation = robot.get_observation()

            arm_action = leader_arm.get_action()
            manipulation_action = {f"arm_{k}": v for k, v in arm_action.items()}

            try:
                robot_command = manipulation_action_to_robot_command(manipulation_action)
            except InvalidManipulationAction as e:
                sys.exit(f"teleop_smoke_test: leader arm produced an invalid manipulation action, refusing to send anything: {e}")

            robot.send_action(robot_command)

            if use_rerun:
                log_rerun_data(observation=observation, action=robot_command)

            precise_sleep(max(1.0 / args.fps - (time.perf_counter() - t0), 0.0))
    except KeyboardInterrupt:
        pass
    finally:
        failed = close_devices((("leader", leader_arm, leader_connected), ("robot", robot, robot_connected)))
        if not failed and (robot_connected or leader_connected):
            print("teleop_smoke_test: client closed; the follower holds its pose under the host's torque until the host exits", flush=True)
    if failed:
        sys.exit(f"teleop_smoke_test: {' and '.join(failed)} did not disconnect cleanly")


if __name__ == "__main__":
    main()
