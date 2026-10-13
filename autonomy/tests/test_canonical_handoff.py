#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_canonical_handoff.py: proves the deterministic,
# bounded joint-space interpolation from a measured current pose to the
# canonical reveal pose -- linear waypoint math, the abort/refuse contract,
# and that wrist_roll/base are structurally never part of it. No hardware,
# no lerobot import. Run directly: python3 autonomy/tests/test_canonical_handoff.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))

from canonical_handoff import (  # noqa: E402
    ABORTED,
    CANONICAL_REVEAL_POSE,
    COMPLETED,
    INTERP_JOINTS,
    REFUSED_DELTA_TOO_LARGE,
    build_waypoints,
    max_tick_delta,
    run_canonical_handoff,
)

CURRENT_POSE = {
    "arm_shoulder_pan.pos": -12.13,
    "arm_shoulder_lift.pos": -24.84,
    "arm_elbow_flex.pos": -42.15,
    "arm_wrist_flex.pos": 91.74,
    "arm_gripper.pos": 93.72,
}


class TestCanonicalRevealPose(unittest.TestCase):
    def test_matches_the_operator_specified_target(self):
        self.assertEqual(
            CANONICAL_REVEAL_POSE,
            {
                "arm_shoulder_pan.pos": -16.53,
                "arm_shoulder_lift.pos": -57.89,
                "arm_elbow_flex.pos": 17.71,
                "arm_wrist_flex.pos": 49.80,
                "arm_gripper.pos": 75.16,
            },
        )

    def test_wrist_roll_is_not_in_the_canonical_pose(self):
        # The operator explicitly does not want wrist_roll forced to -89.80
        # or any other historical value -- it must never be a target here.
        self.assertNotIn("arm_wrist_roll.pos", CANONICAL_REVEAL_POSE)


class TestInterpJoints(unittest.TestCase):
    def test_interp_joints_excludes_wrist_roll(self):
        self.assertNotIn("arm_wrist_roll.pos", INTERP_JOINTS)

    def test_interp_joints_excludes_base_fields(self):
        for key in INTERP_JOINTS:
            self.assertNotIn("vel", key)

    def test_interp_joints_includes_gripper(self):
        # Unlike evaluate.py's policy-action EMA (gripper excluded there),
        # the handoff moves the physical pose -- gripper is part of that pose.
        self.assertIn("arm_gripper.pos", INTERP_JOINTS)


class TestMaxTickDelta(unittest.TestCase):
    def test_matches_hand_computed_worst_case(self):
        current = {k: 0.0 for k in INTERP_JOINTS}
        target = {**{k: 0.0 for k in INTERP_JOINTS}, "arm_elbow_flex.pos": 90.0}
        self.assertAlmostEqual(max_tick_delta(current, target, n_steps=90), 1.0)

    def test_zero_delta_when_already_at_target(self):
        self.assertEqual(max_tick_delta(CANONICAL_REVEAL_POSE, CANONICAL_REVEAL_POSE, n_steps=90), 0.0)


class TestBuildWaypoints(unittest.TestCase):
    def test_returns_exactly_n_steps_waypoints(self):
        waypoints = build_waypoints(CURRENT_POSE, CANONICAL_REVEAL_POSE, n_steps=90)
        self.assertEqual(len(waypoints), 90)

    def test_final_waypoint_equals_the_target_exactly(self):
        waypoints = build_waypoints(CURRENT_POSE, CANONICAL_REVEAL_POSE, n_steps=90)
        for key in INTERP_JOINTS:
            self.assertAlmostEqual(waypoints[-1][key], CANONICAL_REVEAL_POSE[key])

    def test_first_waypoint_is_one_step_away_from_current_not_current_itself(self):
        waypoints = build_waypoints(CURRENT_POSE, CANONICAL_REVEAL_POSE, n_steps=90)
        for key in INTERP_JOINTS:
            expected = CURRENT_POSE[key] + (1 / 90) * (CANONICAL_REVEAL_POSE[key] - CURRENT_POSE[key])
            self.assertAlmostEqual(waypoints[0][key], expected)
            self.assertNotAlmostEqual(waypoints[0][key], CURRENT_POSE[key])

    def test_each_waypoint_has_exactly_interp_joints_keys(self):
        waypoints = build_waypoints(CURRENT_POSE, CANONICAL_REVEAL_POSE, n_steps=90)
        for wp in waypoints:
            self.assertEqual(set(wp.keys()), set(INTERP_JOINTS))

    def test_monotonic_progress_toward_target_each_joint(self):
        waypoints = build_waypoints(CURRENT_POSE, CANONICAL_REVEAL_POSE, n_steps=90)
        for key in INTERP_JOINTS:
            distances = [abs(CANONICAL_REVEAL_POSE[key] - wp[key]) for wp in waypoints]
            self.assertEqual(distances, sorted(distances, reverse=True))


class TestRunCanonicalHandoff(unittest.TestCase):
    def test_completes_and_sends_exactly_n_steps_waypoints(self):
        sent = []
        status = run_canonical_handoff(
            CURRENT_POSE, CANONICAL_REVEAL_POSE, send_tick=sent.append, should_abort=lambda: False, n_steps=90
        )
        self.assertEqual(status, COMPLETED)
        self.assertEqual(len(sent), 90)
        for key in INTERP_JOINTS:
            self.assertAlmostEqual(sent[-1][key], CANONICAL_REVEAL_POSE[key])

    def test_abort_stops_immediately_with_no_further_sends(self):
        sent = []

        def should_abort():
            return len(sent) >= 5

        status = run_canonical_handoff(
            CURRENT_POSE, CANONICAL_REVEAL_POSE, send_tick=sent.append, should_abort=should_abort, n_steps=90
        )
        self.assertEqual(status, ABORTED)
        self.assertEqual(len(sent), 5)

    def test_aborted_from_the_start_sends_nothing(self):
        sent = []
        status = run_canonical_handoff(
            CURRENT_POSE, CANONICAL_REVEAL_POSE, send_tick=sent.append, should_abort=lambda: True, n_steps=90
        )
        self.assertEqual(status, ABORTED)
        self.assertEqual(sent, [])

    def test_refuses_and_sends_nothing_when_delta_exceeds_warn_threshold(self):
        sent = []
        huge_mismatch = {**CANONICAL_REVEAL_POSE, "arm_elbow_flex.pos": CANONICAL_REVEAL_POSE["arm_elbow_flex.pos"] + 900.0}
        status = run_canonical_handoff(
            huge_mismatch, CANONICAL_REVEAL_POSE, send_tick=sent.append, should_abort=lambda: False, n_steps=90
        )
        self.assertEqual(status, REFUSED_DELTA_TOO_LARGE)
        self.assertEqual(sent, [])

    def test_realistic_trial3_mismatch_stays_within_warn_threshold(self):
        # Trial 3's actual pose vs canonical (elbow_flex differed by ~60) is
        # exactly the kind of real-world mismatch this must handle cleanly.
        sent = []
        status = run_canonical_handoff(
            CURRENT_POSE, CANONICAL_REVEAL_POSE, send_tick=sent.append, should_abort=lambda: False, n_steps=90
        )
        self.assertEqual(status, COMPLETED)


if __name__ == "__main__":
    unittest.main()
