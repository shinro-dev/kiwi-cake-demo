#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Evaluate a trained manipulation policy on the real LeKiwi: policy output is
six-dimensional (arm/gripper only -- it was trained on a 6D action schema and
can structurally never predict base velocity), and the shared manipulation
action adapter pads the required x.vel/y.vel/theta.vel=0.0 fields onto every
physical send_action() call, exactly as in recording. Adapted from LeRobot's
own examples/lekiwi/evaluate.py (NOT modified in place).

Why the adapter is required here specifically: examples/lekiwi/evaluate.py's
own comment says "For production policy deployment, use `lerobot-rollout`
CLI instead" -- but make_robot_action(action_tensor, dataset.features)
builds the action dict strictly from dataset.features' declared names. If
that dataset (the one used to build preprocessor/postprocessor stats) has a
6D action schema, make_robot_action()'s output has ONLY the six arm keys --
and LeKiwi.send_action() on the real host indexes x.vel/y.vel/theta.vel
unconditionally (confirmed by reading lekiwi.py directly), so sending that
6D dict straight to robot.send_action() would raise KeyError on the very
first inference step. The adapter is what makes this combination work at
all, not an optional safety add-on.

Arguments, each also readable from an environment variable:
  --remote-ip      KC_REMOTE_IP     the Pi's address on your LAN
  --robot-id       KC_ROBOT_ID      the id the LeKiwi was calibrated with
  --policy-path    trained policy path or hub repo id
  --dataset-root   local directory for the eval-episode recording
  --repo-id        eval dataset id (local-only unless --push-to-hub)
  --num-episodes   how many eval episodes to run (default 1)
  --episode-time-s seconds per episode (default 60)
  --task           task string given to the policy (default: the agreed one)
  --fps            control loop rate (default 30)
  --push-to-hub    push the eval dataset to the Hub (default: off)
"""

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))

DEFAULT_TASK = "Pick up the yellow cube and place it in the collection area."


def build_parser():
    p = argparse.ArgumentParser(prog="evaluate.py", description="Evaluate a manipulation policy on the real LeKiwi, base hard-zeroed.")
    p.add_argument("--remote-ip", default=os.environ.get("KC_REMOTE_IP"))
    p.add_argument("--robot-id", default=os.environ.get("KC_ROBOT_ID"))
    p.add_argument("--policy-path", required=True, help="trained policy path or hub repo id")
    p.add_argument("--dataset-root", required=True)
    p.add_argument("--repo-id", required=True)
    p.add_argument("--num-episodes", type=int, default=1)
    p.add_argument("--episode-time-s", type=int, default=60)
    p.add_argument("--task", default=DEFAULT_TASK)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--push-to-hub", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    missing = [n for n, v in (("--remote-ip", args.remote_ip), ("--robot-id", args.robot_id)) if not v]
    if missing:
        sys.exit("evaluate: missing " + ", ".join(missing) + " (or the matching KC_* environment variables)")

    import logging

    from lerobot.common.control_utils import predict_action
    from lerobot.datasets import LeRobotDataset
    from lerobot.policies import make_pre_post_processors
    from lerobot.policies.smolvla import SmolVLAPolicy
    from lerobot.policies.utils import make_robot_action
    from lerobot.processor import make_default_processors
    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig
    from lerobot.utils.constants import ACTION, OBS_STR
    from lerobot.utils.feature_utils import build_dataset_frame, hw_to_dataset_features
    from lerobot.utils.keyboard_input import init_keyboard_listener
    from lerobot.utils.robot_utils import precise_sleep
    from lerobot.utils.utils import log_say
    from lerobot.utils.visualization_utils import init_rerun, log_rerun_data

    from action_adapter import (
        BASE_ZERO_FIELDS,
        MANIPULATION_ACTION_KEYS,
        manipulation_action_to_robot_command,
    )

    robot = LeKiwiClient(LeKiwiClientConfig(remote_ip=args.remote_ip, id=args.robot_id))
    policy = SmolVLAPolicy.from_pretrained(args.policy_path)

    # Same 6D action schema as recording -- built from the adapter's own key
    # tuple, not robot.action_features. This is what makes make_robot_action()
    # below produce a 6-key dict instead of raising on a mismatched schema.
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

    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=policy.config,
        pretrained_path=args.policy_path,
        dataset_stats=dataset.meta.stats,
        preprocessor_overrides={"device_processor": {"device": str(policy.config.device)}},
    )

    robot.connect()
    _teleop_action_processor, robot_action_processor, robot_observation_processor = make_default_processors()

    listener, events = init_keyboard_listener()
    init_rerun(session_name="kiwi_manipulation_evaluate")

    try:
        if not robot.is_connected:
            raise ValueError("Robot is not connected!")

        control_interval = 1 / args.fps
        recorded_episodes = 0
        while recorded_episodes < args.num_episodes and not events["stop_recording"]:
            log_say(f"Running inference, recording eval episode {recorded_episodes} of {args.num_episodes}")

            timestamp = 0
            start_episode_t = time.perf_counter()
            while timestamp < args.episode_time_s:
                start_loop_t = time.perf_counter()

                if events["exit_early"]:
                    events["exit_early"] = False
                    break

                obs = robot.get_observation()
                obs_processed = robot_observation_processor(obs)
                observation_frame = build_dataset_frame(dataset.features, obs_processed, prefix=OBS_STR)

                action_tensor = predict_action(
                    observation=observation_frame,
                    policy=policy,
                    device=policy.config.device,
                    preprocessor=preprocessor,
                    postprocessor=postprocessor,
                    use_amp=policy.config.device.type == "cuda",
                    task=args.task,
                    robot_type=robot.name,
                )

                # Six-dimensional by construction: dataset.features' action
                # schema (above) has only the six arm/gripper names, so this
                # can only ever produce those six keys -- never base velocity.
                action_values = make_robot_action(action_tensor, dataset.features)
                assert set(action_values.keys()) == set(MANIPULATION_ACTION_KEYS), (
                    f"policy output must be exactly the six manipulation keys, got {sorted(action_values.keys())} "
                    "-- refusing to guess how to send this to the robot"
                )
                assert not (set(action_values.keys()) & set(BASE_ZERO_FIELDS)), (
                    "policy output must never contain a base velocity key at all"
                )

                # The one and only physical-send boundary: pads the required
                # x.vel/y.vel/theta.vel=0.0 fields on unconditionally. The
                # policy's own output can never reach robot.send_action()
                # without going through this.
                robot_command = manipulation_action_to_robot_command(action_values)
                assert robot_command["x.vel"] == 0.0 and robot_command["y.vel"] == 0.0 and robot_command["theta.vel"] == 0.0

                robot.send_action(robot_command)

                action_frame = build_dataset_frame(dataset.features, action_values, prefix=ACTION)
                frame = {**observation_frame, **action_frame, "task": args.task}
                dataset.add_frame(frame)

                log_rerun_data(observation=obs_processed, action=robot_command)

                dt_s = time.perf_counter() - start_loop_t
                sleep_time_s = control_interval - dt_s
                if sleep_time_s < 0:
                    logging.warning(f"Evaluate loop is running slower ({1 / dt_s:.1f} Hz) than the target FPS ({args.fps} Hz).")
                precise_sleep(max(sleep_time_s, 0.0))
                timestamp = time.perf_counter() - start_episode_t

            if not events["stop_recording"] and ((recorded_episodes < args.num_episodes - 1) or events["rerecord_episode"]):
                log_say("Reset the environment")
                log_say("Waiting for environment reset, press right arrow key when ready...")

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
        listener.stop()

        dataset.finalize()
        if args.push_to_hub:
            dataset.push_to_hub()
        else:
            print(f"evaluate: eval dataset saved locally at {args.dataset_root} -- NOT pushed to the Hub")


if __name__ == "__main__":
    main()
