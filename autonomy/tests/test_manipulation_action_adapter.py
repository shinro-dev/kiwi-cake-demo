#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_manipulation_action_adapter.py: proves the boundary between
# a SmolVLA/teleop manipulation action (six arm/gripper dims) and the physical
# LeKiwi robot command (those six dims plus x.vel/y.vel/theta.vel hard-zeroed).
# No hardware, no lerobot import -- this module must stay a pure function so it
# can never depend on (or be silently changed by) the hardware-specific package,
# matching autonomy/motion's existing "never imports lerobot" boundary.
# Run directly: python3 autonomy/tests/test_manipulation_action_adapter.py
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))

from action_adapter import (  # noqa: E402
    BASE_ZERO_FIELDS,
    MANIPULATION_ACTION_KEYS,
    InvalidManipulationAction,
    freeze_wrist_roll,
    manipulation_action_to_robot_command,
)

VALID_ACTION = {
    "arm_shoulder_pan.pos": 10.0,
    "arm_shoulder_lift.pos": -20.5,
    "arm_elbow_flex.pos": 30.25,
    "arm_wrist_flex.pos": 0.0,
    "arm_wrist_roll.pos": -5.0,
    "arm_gripper.pos": 50.0,
}


class TestManipulationActionKeys(unittest.TestCase):
    def test_manipulation_action_keys_matches_verified_lekiwi_arm_fields(self):
        # Verified directly against lekiwi.py's _state_ft property (arm_*.pos
        # entries only, base .vel entries excluded on purpose) -- not a live
        # lerobot import, a static guard against this list silently drifting.
        self.assertEqual(
            MANIPULATION_ACTION_KEYS,
            (
                "arm_shoulder_pan.pos",
                "arm_shoulder_lift.pos",
                "arm_elbow_flex.pos",
                "arm_wrist_flex.pos",
                "arm_wrist_roll.pos",
                "arm_gripper.pos",
            ),
        )
        self.assertEqual(len(MANIPULATION_ACTION_KEYS), 6)


class TestValidAction(unittest.TestCase):
    def test_six_valid_arm_fields_produces_nine_field_command(self):
        command = manipulation_action_to_robot_command(VALID_ACTION)
        self.assertEqual(len(command), 9)
        self.assertEqual(set(command.keys()), set(MANIPULATION_ACTION_KEYS) | {"x.vel", "y.vel", "theta.vel"})

    def test_arm_values_pass_through_unchanged(self):
        command = manipulation_action_to_robot_command(VALID_ACTION)
        for key, value in VALID_ACTION.items():
            self.assertEqual(command[key], value)

    def test_base_velocities_are_exactly_zero_in_output(self):
        command = manipulation_action_to_robot_command(VALID_ACTION)
        self.assertEqual(command["x.vel"], 0.0)
        self.assertEqual(command["y.vel"], 0.0)
        self.assertEqual(command["theta.vel"], 0.0)
        # Not just equal-to-zero -- must be the literal constant, unconditionally,
        # never derived from the input in any way.
        self.assertEqual({k: command[k] for k in ("x.vel", "y.vel", "theta.vel")}, BASE_ZERO_FIELDS)

    def test_output_values_are_real_floats(self):
        command = manipulation_action_to_robot_command(VALID_ACTION)
        for value in command.values():
            self.assertIsInstance(value, float)


class TestRejectsUnexpectedBaseFields(unittest.TestCase):
    def test_rejects_input_that_already_contains_x_vel(self):
        # Policy/dataset output must never be able to override the base fields,
        # even by supplying an "innocent" zero -- reject, don't silently accept.
        bad = {**VALID_ACTION, "x.vel": 0.0}
        with self.assertRaises(InvalidManipulationAction) as ctx:
            manipulation_action_to_robot_command(bad)
        self.assertIn("x.vel", str(ctx.exception))

    def test_rejects_input_that_already_contains_y_vel(self):
        bad = {**VALID_ACTION, "y.vel": 0.05}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)

    def test_rejects_input_that_already_contains_theta_vel(self):
        bad = {**VALID_ACTION, "theta.vel": 0.1}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)

    def test_rejects_nonzero_base_velocity_specifically(self):
        bad = {**VALID_ACTION, "x.vel": 0.05}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)


class TestRejectsUnexpectedKeys(unittest.TestCase):
    def test_rejects_arbitrary_unexpected_key(self):
        bad = {**VALID_ACTION, "arm_gripper.force": 1.0}
        with self.assertRaises(InvalidManipulationAction) as ctx:
            manipulation_action_to_robot_command(bad)
        self.assertIn("arm_gripper.force", str(ctx.exception))


class TestRejectsMissingKeys(unittest.TestCase):
    def test_rejects_missing_arm_key(self):
        bad = dict(VALID_ACTION)
        del bad["arm_gripper.pos"]
        with self.assertRaises(InvalidManipulationAction) as ctx:
            manipulation_action_to_robot_command(bad)
        self.assertIn("arm_gripper.pos", str(ctx.exception))

    def test_rejects_empty_action(self):
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command({})


class TestRejectsInvalidNumbers(unittest.TestCase):
    def test_rejects_nan(self):
        bad = {**VALID_ACTION, "arm_elbow_flex.pos": float("nan")}
        with self.assertRaises(InvalidManipulationAction) as ctx:
            manipulation_action_to_robot_command(bad)
        self.assertIn("arm_elbow_flex.pos", str(ctx.exception))

    def test_rejects_positive_infinity(self):
        bad = {**VALID_ACTION, "arm_wrist_roll.pos": math.inf}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)

    def test_rejects_negative_infinity(self):
        bad = {**VALID_ACTION, "arm_wrist_roll.pos": -math.inf}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)

    def test_rejects_non_numeric_string(self):
        bad = {**VALID_ACTION, "arm_gripper.pos": "50.0"}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)

    def test_rejects_none(self):
        bad = {**VALID_ACTION, "arm_gripper.pos": None}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)

    def test_rejects_bool(self):
        # bool is a subclass of int in Python -- must not be silently accepted
        # as 0.0/1.0, since a stray boolean is almost certainly a caller bug.
        bad = {**VALID_ACTION, "arm_gripper.pos": True}
        with self.assertRaises(InvalidManipulationAction):
            manipulation_action_to_robot_command(bad)


class TestFreezeWristRoll(unittest.TestCase):
    """manipulation-v0's wrist_roll seam workaround (this session's investigation:
    wrist_roll is the one SO-101/LeKiwi joint calibrated across its full raw
    encoder range, so lerobot's DEGREES normalization wraps at +-180 there).
    Freezing it to one taught value sidesteps the seam for v0 without touching
    vendored lerobot or recalibrating."""

    def test_input_wrist_roll_value_is_ignored_no_matter_what_it_is(self):
        # Covers both "leader wrist_roll changes do not alter the physical
        # follower command" and "inference policy wrist_roll cannot override
        # the held value" -- freeze_wrist_roll does not know or care which
        # produced the input action, so one property covers both callers.
        for leader_or_policy_value in (-179.9, 0.0, 91.21, 180.0, float("nan"), None, "garbage"):
            action = {**VALID_ACTION, "arm_wrist_roll.pos": leader_or_policy_value}
            frozen = freeze_wrist_roll(action, held_value=-93.58)
            self.assertEqual(frozen["arm_wrist_roll.pos"], -93.58)

    def test_other_five_manipulation_keys_pass_through_unchanged(self):
        frozen = freeze_wrist_roll(VALID_ACTION, held_value=-93.58)
        for key in MANIPULATION_ACTION_KEYS:
            if key == "arm_wrist_roll.pos":
                continue
            self.assertEqual(frozen[key], VALID_ACTION[key])

    def test_does_not_mutate_the_input_action(self):
        original = dict(VALID_ACTION)
        freeze_wrist_roll(VALID_ACTION, held_value=-93.58)
        self.assertEqual(VALID_ACTION, original)

    def test_held_value_is_stored_as_a_plain_float(self):
        frozen = freeze_wrist_roll(VALID_ACTION, held_value=-93.58)
        self.assertIsInstance(frozen["arm_wrist_roll.pos"], float)

    def test_composed_with_the_adapter_base_still_zeroed_and_wrist_roll_held(self):
        # The full boundary a hardware script actually calls: freeze, then adapt.
        action = {**VALID_ACTION, "arm_wrist_roll.pos": 179.9}  # near the seam
        command = manipulation_action_to_robot_command(freeze_wrist_roll(action, held_value=-93.58))
        self.assertEqual(command["arm_wrist_roll.pos"], -93.58)
        self.assertEqual({k: command[k] for k in ("x.vel", "y.vel", "theta.vel")}, BASE_ZERO_FIELDS)


if __name__ == "__main__":
    unittest.main()
