#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Record LeKiwi manipulation demonstrations: SO-101 leader arm + keyboard
(required by lerobot_record.record_loop for a lekiwi_client robot, but its
base output is discarded -- see below), six-dimensional dataset action
schema, base velocity hard-zeroed through the shared manipulation action
adapter on every physical send. Adapted from LeRobot's own
examples/lekiwi/record.py (NOT modified in place -- this is a separate,
repo-local copy) using this repo's argparse/env-var CLI convention.

Why keyboard is still required: lerobot_record.record_loop() raises unless a
lekiwi_client robot's teleop list is exactly [arm_teleop, keyboard_teleop] --
confirmed by reading lerobot_record.py directly, not assumed. Its own
LeKiwi._from_keyboard_to_base_action() always returns x.vel/y.vel/theta.vel
(0.0 if untouched, never an empty dict -- also confirmed by reading it, not
assumed), so the raw per-step teleop action always carries base keys. Two
separate, independent things then happen to that raw action:

  raw action (6 arm keys + 3 base keys, base keys 0.0 unless a key is pressed)
      |
      +--> dataset write: build_dataset_frame() looks up ONLY the names
      |    declared in dataset.features (built here from the adapter's own
      |    6-key tuple, not robot.action_features) -- the base keys are
      |    present in the raw dict but never declared, so they are never
      |    read or written. Confirmed from feature_utils.py's own source.
      |
      +--> physical send: our robot_action_processor strips the raw action
           down to just the 6 arm keys FIRST (discarding whatever the
           keyboard produced, whether zero or, if a key was pressed by
           accident, nonzero) and runs the result through
           manipulation_action_to_robot_command(), which always returns the
           six arm values plus literal 0.0 base fields. The base cannot move
           from this script regardless of what the keyboard teleop reports.

Arguments, each also readable from an environment variable:
  --remote-ip      KC_REMOTE_IP     the Pi's address on your LAN
  --robot-id       KC_ROBOT_ID      the id the LeKiwi was calibrated with
  --leader-port    KC_LEADER_PORT   the leader arm's /dev/serial/by-id/ path
  --leader-id      KC_LEADER_ID     the leader arm's own calibration id
  --dataset-root   local directory to write the dataset into (required)
  --repo-id        dataset repo id, e.g. <hf_username>/<dataset_name>
                    (used only as a local dataset identifier unless
                    --push-to-hub is also given)
  --num-episodes   how many episodes to record (default 3, the throwaway
                    pass; pass 20 for the first real dataset)
  --episode-time-s seconds per episode (default 30)
  --reset-time-s   seconds given to reset between episodes (default 15)
  --task           single task string for every episode (default: the
                    agreed one-instruction task)
  --fps            control loop rate (default 30)
  --push-to-hub    push the finished dataset to the Hub (default: off --
                    datasets stay local per this project's decision)
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))

DEFAULT_TASK = "Pick up the yellow cube and place it in the collection area."


def build_parser():
    p = argparse.ArgumentParser(prog="record.py", description="Record LeKiwi manipulation demonstrations, base hard-zeroed.")
    p.add_argument("--remote-ip", default=os.environ.get("KC_REMOTE_IP"))
    p.add_argument("--robot-id", default=os.environ.get("KC_ROBOT_ID"))
    p.add_argument("--leader-port", default=os.environ.get("KC_LEADER_PORT"))
    p.add_argument("--leader-id", default=os.environ.get("KC_LEADER_ID"))
    p.add_argument("--dataset-root", required=True, help="local directory to write the dataset into")
    p.add_argument("--repo-id", required=True, help="dataset id, e.g. <hf_username>/<dataset_name> (local-only unless --push-to-hub)")
    p.add_argument("--num-episodes", type=int, default=3)
    p.add_argument("--episode-time-s", type=int, default=30)
    p.add_argument("--reset-time-s", type=int, default=15)
    p.add_argument("--task", default=DEFAULT_TASK)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--push-to-hub", action="store_true", help="push the finished dataset to the Hub (default: off, stays local)")
    return p


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    missing = [n for n, v in (("--remote-ip", args.remote_ip), ("--robot-id", args.robot_id), ("--leader-port", args.leader_port), ("--leader-id", args.leader_id)) if not v]
    if missing:
        sys.exit("record: missing " + ", ".join(missing) + " (or the matching KC_* environment variables)")

    from lerobot.datasets import LeRobotDataset
    from lerobot.processor import make_default_processors
    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig
    from lerobot.scripts.lerobot_record import record_loop
    from lerobot.teleoperators.keyboard import KeyboardTeleop, KeyboardTeleopConfig
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
    from lerobot.utils.constants import ACTION, OBS_STR
    from lerobot.utils.feature_utils import hw_to_dataset_features
    from lerobot.utils.keyboard_input import init_keyboard_listener
    from lerobot.utils.utils import log_say
    from lerobot.utils.visualization_utils import init_rerun

    from action_adapter import MANIPULATION_ACTION_KEYS, manipulation_action_to_robot_command

    robot = LeKiwiClient(LeKiwiClientConfig(remote_ip=args.remote_ip, id=args.robot_id))
    leader_arm = SO101Leader(SO101LeaderConfig(port=args.leader_port, id=args.leader_id))
    keyboard = KeyboardTeleop(KeyboardTeleopConfig())

    # Action schema is genuinely 6D: built from the adapter's own key tuple,
    # NOT robot.action_features (which is 9D, base included). Observation
    # schema is left as robot.observation_features, unchanged -- base
    # state/cameras stay observable, per this session's decision that
    # excluding them from observations is unnecessary for the first proof.
    action_features = hw_to_dataset_features(dict.fromkeys(MANIPULATION_ACTION_KEYS, float), ACTION)
    obs_features = hw_to_dataset_features(robot.observation_features, OBS_STR)
    dataset_features = {**action_features, **obs_features}

    dataset = LeRobotDataset.create(
        repo_id=args.repo_id,
        root=args.dataset_root,
        fps=args.fps,
        features=dataset_features,
        robot_type=robot.name,
        use_videos=True,
        image_writer_threads=4,
    )

    def manipulation_only_robot_action_processor(pair):
        """The single physical-send boundary: strip to just the six arm keys
        (discarding whatever base keys the required keyboard teleop produced,
        zero or not) and run the shared adapter, which always returns the six
        values plus literal 0.0 base fields. This is the only function in this
        script that touches robot.send_action()'s input."""
        action, _obs = pair
        arm_only = {k: v for k, v in action.items() if k in MANIPULATION_ACTION_KEYS}
        return manipulation_action_to_robot_command(arm_only)

    teleop_action_processor, _default_robot_action_processor, robot_observation_processor = make_default_processors()

    robot.connect()
    leader_arm.connect()
    keyboard.connect()

    listener, events = init_keyboard_listener()
    init_rerun(session_name="kiwi_manipulation_record")

    try:
        if not robot.is_connected or not leader_arm.is_connected or not keyboard.is_connected:
            raise ValueError("Robot or teleop is not connected!")

        print(f"record: dataset action features = {list(action_features[ACTION]['names'])}")
        recorded_episodes = 0
        while recorded_episodes < args.num_episodes and not events["stop_recording"]:
            log_say(f"Recording episode {recorded_episodes}")
            record_loop(
                robot=robot,
                events=events,
                fps=args.fps,
                teleop_action_processor=teleop_action_processor,
                robot_action_processor=manipulation_only_robot_action_processor,
                robot_observation_processor=robot_observation_processor,
                dataset=dataset,
                teleop=[leader_arm, keyboard],
                control_time_s=args.episode_time_s,
                single_task=args.task,
                display_data=True,
            )

            if not events["stop_recording"] and ((recorded_episodes < args.num_episodes - 1) or events["rerecord_episode"]):
                log_say("Reset the environment")
                record_loop(
                    robot=robot,
                    events=events,
                    fps=args.fps,
                    teleop_action_processor=teleop_action_processor,
                    robot_action_processor=manipulation_only_robot_action_processor,
                    robot_observation_processor=robot_observation_processor,
                    teleop=[leader_arm, keyboard],
                    control_time_s=args.reset_time_s,
                    single_task=args.task,
                    display_data=True,
                )

            if events["rerecord_episode"]:
                log_say("Re-record episode")
                events["rerecord_episode"] = False
                events["exit_early"] = False
                dataset.clear_episode_buffer()
                continue

            dataset.save_episode()
            recorded_episodes += 1
    finally:
        log_say("Stop recording")
        robot.disconnect()
        leader_arm.disconnect()
        keyboard.disconnect()
        listener.stop()

        dataset.finalize()
        if args.push_to_hub:
            dataset.push_to_hub()
        else:
            print(f"record: dataset saved locally at {args.dataset_root} -- NOT pushed to the Hub (pass --push-to-hub to change that)")


if __name__ == "__main__":
    main()
