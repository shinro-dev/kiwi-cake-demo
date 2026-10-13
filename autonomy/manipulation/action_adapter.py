#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""manipulation_action_to_robot_command: the single boundary between a
SmolVLA/teleop manipulation action (six arm/gripper dims) and the physical
LeKiwi robot command (those six dims plus x.vel/y.vel/theta.vel).

Field names verified directly against lerobot's LeKiwi robot class
(robots/lekiwi/lekiwi.py, `_state_ft`/`action_features`): the full action
schema is the six `arm_*.pos` keys below plus `x.vel`/`y.vel`/`theta.vel`.
`LeKiwi.send_action()` indexes `x.vel`/`y.vel`/`theta.vel` unconditionally
(KeyError if absent), so a physical command can never omit them -- but the
manipulation policy/dataset must never see or produce them, on purpose (the
base is mobile_approach's exclusive responsibility, never the VLA's).

This module never imports lerobot -- kept a pure function, the same boundary
autonomy/motion already draws around the hardware-specific package, so this
safety property can never depend on (or be silently changed by) that import.
"""

import math

MANIPULATION_ACTION_KEYS = (
    "arm_shoulder_pan.pos",
    "arm_shoulder_lift.pos",
    "arm_elbow_flex.pos",
    "arm_wrist_flex.pos",
    "arm_wrist_roll.pos",
    "arm_gripper.pos",
)

BASE_ZERO_FIELDS = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}

_ALLOWED_KEYS = set(MANIPULATION_ACTION_KEYS)


class InvalidManipulationAction(ValueError):
    pass


def _is_finite_real_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def manipulation_action_to_robot_command(action: dict) -> dict:
    """Validate a manipulation action (exactly the six arm/gripper keys, each a
    finite real number) and return the physical LeKiwi robot command: those
    six values unchanged, plus x.vel/y.vel/theta.vel hard-zeroed by
    construction -- never derived from `action` in any way, so nothing this
    function is given can ever make the base move.

    Raises InvalidManipulationAction for any missing key, any unexpected key
    (base velocity fields included -- even a supplied 0.0 is rejected, not
    silently accepted, so a caller bug can never slip a base command through
    this boundary), or any non-finite/non-numeric value.
    """
    keys = set(action.keys())

    missing = _ALLOWED_KEYS - keys
    if missing:
        raise InvalidManipulationAction(
            f"missing required manipulation action key(s): {sorted(missing)}"
        )

    unexpected = keys - _ALLOWED_KEYS
    if unexpected:
        raise InvalidManipulationAction(
            f"unexpected key(s) in manipulation action -- base/other fields are not allowed "
            f"here, the base must be zero by construction: {sorted(unexpected)}"
        )

    for key in MANIPULATION_ACTION_KEYS:
        value = action[key]
        if not _is_finite_real_number(value):
            raise InvalidManipulationAction(f"{key} must be a finite real number, got {value!r}")

    return {
        **{key: float(action[key]) for key in MANIPULATION_ACTION_KEYS},
        **BASE_ZERO_FIELDS,
    }


WRIST_ROLL_KEY = "arm_wrist_roll.pos"


def freeze_wrist_roll(action: dict, held_value: float) -> dict:
    """manipulation-v0's wrist_roll seam workaround: return `action` with
    arm_wrist_roll.pos replaced by `held_value`, every other key unchanged
    and the input left untouched.

    wrist_roll is the one SO-101/LeKiwi joint calibrated across its full raw
    encoder range (both leader and follower calibrate it with range_min=0,
    range_max=4095 -- lerobot's own LeKiwi.calibrate()/SOLeader.calibrate()
    hardcode it into "full_turn_motor"). lerobot's MotorsBus._normalize DEGREES
    branch computes `(val - mid) * 360 / max_res` from the raw, unclamped
    value, so raw ticks 4095/0 -- one physical encoder step apart -- read back
    as roughly +180/-180: a ~360 degree computed jump for a sub-degree
    physical motion. A leader (or a policy) reading that crosses the wrap
    produces exactly this jump through no fault of its own; freezing the
    physical/dataset/policy wrist_roll to one taught value sidesteps the seam
    for manipulation v0 rather than attempting to fix the underlying
    representation. Call this before manipulation_action_to_robot_command
    (physical sends) and as the teleop_action_processor in recording (so the
    dataset -- built from that processor's output, not the robot's -- stores
    the held value too, never the discontinuous one)."""
    return {**action, WRIST_ROLL_KEY: float(held_value)}
