#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""scan_arm_motion: the bounded arm-motion primitive the SCAN_WORLD executor
is wired to. It composes three existing, physically proven pure functions and
adds nothing of its own to the motion itself:

  canonical_handoff.run_canonical_handoff   linear joint interpolation with a
                                            per-tick bound, refused up front
                                            if the bound would be exceeded
  action_adapter.freeze_wrist_roll          wrist_roll held at the value
                                            measured at the start of the move
  action_adapter.manipulation_action_to_robot_command
                                            the manipulation/base boundary:
                                            base fields are zero by
                                            construction and any other key is
                                            rejected

Why not reuse the hybrid runner's move helper as is: that helper hardcodes
"never abort" and raises on an aborted move, so an operator abort could not
stop a scan segment in progress (segments last up to several seconds). Here
the caller's should_abort is passed straight into the interpolation loop,
which checks it before every tick: the tick in progress when it flips is
never sent, and the arm is left where it is, torqued.

The only hardware-facing callables are injected: read_pose() -> measured
joints (bare names, degrees) and send_robot_command(command) -> None. This
module never imports the robot package and has no base command of its own.

NEVER RUN ON HARDWARE as of 2026-09-19.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manipulation"))

from action_adapter import freeze_wrist_roll, manipulation_action_to_robot_command  # noqa: E402
from canonical_handoff import INTERP_JOINTS, REFUSED_DELTA_TOO_LARGE, run_canonical_handoff  # noqa: E402
from scan_poses import SCAN_CONTROL_FPS, SCAN_JOINTS  # noqa: E402

_BARE_BY_ARM_KEY = {key: key[len("arm_"):-len(".pos")] for key in INTERP_JOINTS}


class ScanMotionRefused(RuntimeError):
    """Raised before anything is sent."""


def make_bounded_move_arm(read_pose, send_robot_command, sleep=time.sleep, fps: int = SCAN_CONTROL_FPS, monotonic=time.perf_counter):
    control_interval = 1.0 / fps

    def move_arm(target: dict, duration_s: float, should_abort) -> dict:
        if set(target) != set(SCAN_JOINTS):
            raise ScanMotionRefused(f"a scan move names exactly {SCAN_JOINTS}, got {sorted(target)}")

        measured = read_pose()
        wrist_roll_hold = float(measured["wrist_roll"])
        current = {key: float(measured[bare]) for key, bare in _BARE_BY_ARM_KEY.items()}
        goal = dict(current)  # the gripper is interpolated from its measured value to itself: held
        for name, value in target.items():
            goal[f"arm_{name}.pos"] = float(value)

        def send_tick(waypoint):
            started = monotonic()
            send_robot_command(manipulation_action_to_robot_command(freeze_wrist_roll(waypoint, wrist_roll_hold)))
            sleep(min(control_interval, max(control_interval - (monotonic() - started), 0.0)))

        status = run_canonical_handoff(current, goal, send_tick, should_abort, n_steps=max(1, round(duration_s * fps)))
        if status == REFUSED_DELTA_TOO_LARGE:
            raise ScanMotionRefused(f"per-tick step too large for a {duration_s:.2f} s move; nothing was sent")
        return read_pose()

    return move_arm
