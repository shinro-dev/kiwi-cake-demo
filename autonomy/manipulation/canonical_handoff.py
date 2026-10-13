#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""canonical_handoff: deterministic, bounded joint-space interpolation from
whatever pose the arm currently measures to the fixed canonical reveal pose,
so the operator never has to manually match joint-angle instructions again
before a policy-evaluation trial.

Compared against replaying a recorded demonstration segment (examples/lekiwi/
replay.py's pattern: send stored actions verbatim at fps) and rejected: replay
assumes the arm already starts near that segment's first frame, which Trial 3
already disproved (its start pose differed from canonical by up to ~60 on
elbow_flex) -- a mismatched replay start would rely entirely on the host's
15-degree max_relative_target clamp to "catch up", an uncontrolled fast snap,
not a chosen velocity. Direct bounded interpolation works from any starting
pose with a per-tick delta fixed in advance.

The interpolation scheme is the same linear scheme lerobot.utils.
action_interpolator.ActionInterpolator already uses for policy-action
smoothing (vendored, torch-based), reimplemented here as plain float-dict
math to match this repo's existing hardware-free pure-module boundary
(action_adapter.py, pose_preflight.py, kinesthetic.py, action_smoothing.py --
none import lerobot or torch).

wrist_roll is excluded from INTERP_JOINTS on purpose: the operator does not
want it forced to any historical value (including -89.80) -- it is read once
by the caller at handoff start and held at that runtime value throughout,
via the existing action_adapter.freeze_wrist_roll, exactly as every other
manipulation script already freezes it. Base velocity is never part of
INTERP_JOINTS either; the caller's own send_tick is expected to route every
waypoint through manipulation_action_to_robot_command, which hard-zeroes the
base structurally.
"""

INTERP_JOINTS = (
    "arm_shoulder_pan.pos",
    "arm_shoulder_lift.pos",
    "arm_elbow_flex.pos",
    "arm_wrist_flex.pos",
    "arm_gripper.pos",
)

CANONICAL_REVEAL_POSE = {
    "arm_shoulder_pan.pos": -16.53,
    "arm_shoulder_lift.pos": -57.89,
    "arm_elbow_flex.pos": 17.71,
    "arm_wrist_flex.pos": 49.80,
    "arm_gripper.pos": 75.16,
}

DEFAULT_N_STEPS = 90  # 3.0s at 30Hz -- fixed duration regardless of starting pose
DEFAULT_MAX_TICK_DELTA_WARN = 5.0  # degrees/gripper-units per tick; comfortably under the 15-degree host clamp

REFUSED_DELTA_TOO_LARGE = "REFUSED_DELTA_TOO_LARGE"
ABORTED = "ABORTED"
COMPLETED = "COMPLETED"


def max_tick_delta(current_pose: dict, target_pose: dict, n_steps: int) -> float:
    """The largest single-tick delta any joint would receive if the move from
    current_pose to target_pose were spread evenly over n_steps ticks."""
    return max(abs(target_pose[key] - current_pose[key]) / n_steps for key in INTERP_JOINTS)


def build_waypoints(current_pose: dict, target_pose: dict, n_steps: int) -> list:
    """n_steps linearly-interpolated waypoints from current_pose to
    target_pose (exclusive of current_pose itself, inclusive of target_pose
    as the final waypoint) -- the same `prev + t * (target - prev)` scheme as
    ActionInterpolator.add(), t = i / n_steps for i = 1..n_steps. Each
    waypoint has exactly the INTERP_JOINTS keys; wrist_roll and base fields
    are never present, by construction."""
    return [
        {key: current_pose[key] + (i / n_steps) * (target_pose[key] - current_pose[key]) for key in INTERP_JOINTS}
        for i in range(1, n_steps + 1)
    ]


def run_canonical_handoff(
    current_pose: dict,
    target_pose: dict,
    send_tick,
    should_abort,
    n_steps: int = DEFAULT_N_STEPS,
    warn_threshold: float = DEFAULT_MAX_TICK_DELTA_WARN,
) -> str:
    """Pure control loop, no device or dataset coupling: send_tick(waypoint)
    performs one tick's physical send (whatever that means to the caller --
    the freeze_wrist_roll/manipulation_action_to_robot_command/send_action
    chain lives entirely in the caller), should_abort() reports whether the
    operator has asked to stop.

    Refuses to send anything and returns REFUSED_DELTA_TOO_LARGE if the
    computed per-tick delta for this current_pose/target_pose/n_steps would
    exceed warn_threshold -- checked once, before the first waypoint is even
    built, so an unexpectedly large mismatch (e.g. a bad pose read) can never
    produce partial motion.

    should_abort() is checked BEFORE each waypoint send, matching
    kinesthetic.run_kinesthetic_episode's should_stop() contract: the tick in
    progress when should_abort() flips is never sent, so ABORTED always means
    "stopped with no further motion", not "one more waypoint slipped
    through". Returns COMPLETED if every waypoint was sent.
    """
    if max_tick_delta(current_pose, target_pose, n_steps) > warn_threshold:
        return REFUSED_DELTA_TOO_LARGE

    for waypoint in build_waypoints(current_pose, target_pose, n_steps):
        if should_abort():
            return ABORTED
        send_tick(waypoint)

    return COMPLETED
