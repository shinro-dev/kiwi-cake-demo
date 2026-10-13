#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_mobile_approach.py: offline/synthetic tests for
# autonomy/motion/mobile_approach.py. No hardware, no camera, no network --
# a simple deterministic kinematic simulation stands in for
# observe -> command -> gateway-applies-for-a-bounded-interval -> stop ->
# reacquire. Positions here are representative round numbers chosen to be
# inside the trusted envelope, not literal real-session measurements (see
# .local-dev/localization/ for the real-measurement-derived local replay
# check, which is intentionally not committed).
# Run directly: python3 autonomy/tests/test_mobile_approach.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

from mobile_approach import (
    CONTRACT_V2,
    FRAME_ANCHOR_REACH,
    V2_ADVANCE_TOLERANCE_M,
    V2_MAX_ADVANCE_M,
    V2_QUALITY_NONE,
    V2_VX_MPS,
    ControllerConfig,
    ControllerState,
    HandoffRegion,
    LocalizationEnvelope,
    MobileApproachController,
    TargetObservation,
    TargetObservationV2,
)

STEP_DT_S = 0.25  # matches command_ttl_ns default (250ms) -- gateway applies the command for this long, then stops
MAX_STEPS = 60


def simulate(controller, start_x, start_y, start_t_ns=1_000_000_000, quality=0.9, target_id="cube_sim", max_steps=MAX_STEPS):
    """Advance world-fixed-target kinematics under the controller's own commands.
    A world-fixed target's robot-relative position shrinks by v*dt each step
    when the robot moves toward it (see mobile_approach.py's ex/ey sign
    convention: +vx closes forward error, +vy closes lateral error the same way)."""
    x, y, t_ns = start_x, start_y, start_t_ns
    history = []
    for _ in range(max_steps):
        obs = TargetObservation(target_id=target_id, timestamp_ns=t_ns, x_m=x, y_m=y, quality=quality)
        cmd, state, reason = controller.step(obs, now_ns=t_ns)
        history.append((x, y, cmd, state, reason))
        if state == ControllerState.MANIPULATION_READY:
            break
        x -= cmd.vx_mps * STEP_DT_S
        y -= cmd.vy_mps * STEP_DT_S
        t_ns += int(STEP_DT_S * 1e9)
    return history


class TestSyntheticConvergence(unittest.TestCase):
    POSITIONS = [
        ("centered_far", 0.90, 0.00),
        ("right_offset_far", 0.90, 0.25),
        ("left_offset_far", 0.90, -0.25),
        ("centered_mid", 0.60, 0.20),
        ("centered_mid_2", 0.60, -0.20),
    ]

    def test_converges_to_manipulation_ready(self):
        for name, x0, y0 in self.POSITIONS:
            with self.subTest(name=name, x0=x0, y0=y0):
                controller = MobileApproachController()
                history = simulate(controller, x0, y0)
                final_state = history[-1][3]
                self.assertEqual(
                    final_state, ControllerState.MANIPULATION_READY,
                    f"{name} did not converge within {MAX_STEPS} steps; last state={final_state}, last reason={history[-1][4]}",
                )

    def test_distance_to_handoff_generally_decreases(self):
        config = ControllerConfig()
        for name, x0, y0 in self.POSITIONS:
            with self.subTest(name=name):
                controller = MobileApproachController(config)
                history = simulate(controller, x0, y0)

                def dist(x, y):
                    cx = config.handoff.center_x_m
                    return ((x - cx) ** 2 + y ** 2) ** 0.5

                distances = [dist(x, y) for x, y, _, _, _ in history]
                # Reaching MANIPULATION_READY already guarantees the final point is
                # inside the handoff box, i.e. within its own corner-to-center radius
                # -- a fixed relative-reduction threshold is the wrong bar for a start
                # position that was already close (e.g. the "mid" cases), so check an
                # absolute bound plus a real trend instead of a single ratio.
                import math as _m
                handoff_radius_m = _m.hypot((config.handoff.x_max_m - config.handoff.x_min_m) / 2, config.handoff.y_abs_max_m)
                self.assertLessEqual(distances[-1], handoff_radius_m + 1e-6, f"{name}: final distance {distances[-1]:.3f}m exceeds handoff radius {handoff_radius_m:.3f}m")
                first_half = distances[: len(distances) // 2] or distances
                second_half = distances[len(distances) // 2:] or distances
                self.assertLessEqual(min(second_half), min(first_half), f"{name}: second half never beat first half's best distance")

    def test_no_command_exceeds_configured_bounds(self):
        config = ControllerConfig()
        for name, x0, y0 in self.POSITIONS:
            with self.subTest(name=name):
                controller = MobileApproachController(config)
                history = simulate(controller, x0, y0)
                for x, y, cmd, state, reason in history:
                    self.assertLessEqual(abs(cmd.vx_mps), config.vx_max_mps + 1e-9, f"{name}: vx exceeded max")
                    self.assertLessEqual(abs(cmd.vy_mps), config.vy_max_mps + 1e-9, f"{name}: vy exceeded max")
                    self.assertEqual(cmd.omega_rps, 0.0, f"{name}: omega must be 0 in v0")

    def test_no_bad_centerline_oscillation(self):
        config = ControllerConfig()
        for name, x0, y0 in self.POSITIONS:
            with self.subTest(name=name):
                controller = MobileApproachController(config)
                history = simulate(controller, x0, y0)
                ys = [y for _, y, _, _, _ in history]
                sign_flips = sum(1 for a, b in zip(ys, ys[1:]) if a * b < 0 and abs(a) > config.deadband_m and abs(b) > config.deadband_m)
                self.assertLessEqual(sign_flips, 1, f"{name}: y crossed the centerline {sign_flips} times, suggests oscillation")


class TestFailureModes(unittest.TestCase):
    def setUp(self):
        self.config = ControllerConfig()
        self.controller = MobileApproachController(self.config)
        self.t0 = 1_000_000_000

    def test_missing_target_is_idle_zero(self):
        cmd, state, reason = self.controller.step(None, now_ns=self.t0)
        self.assertEqual(state, ControllerState.IDLE)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps, cmd.omega_rps), (0.0, 0.0, 0.0))

    def test_stale_observation_zero(self):
        obs = TargetObservation(target_id="c1", timestamp_ns=self.t0, x_m=0.6, y_m=0.0, quality=0.9)
        stale_now = self.t0 + self.config.max_observation_age_ns + 1
        cmd, state, reason = self.controller.step(obs, now_ns=stale_now)
        self.assertEqual(state, ControllerState.TARGET_LOST)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps), (0.0, 0.0))
        self.assertIn("stale", reason)

    def test_timeout_is_same_as_stale(self):
        obs = TargetObservation(target_id="c1", timestamp_ns=self.t0, x_m=0.6, y_m=0.0, quality=0.9)
        way_later = self.t0 + 10_000_000_000
        cmd, state, reason = self.controller.step(obs, now_ns=way_later)
        self.assertEqual(state, ControllerState.TARGET_LOST)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps), (0.0, 0.0))

    def test_low_quality_zero(self):
        obs = TargetObservation(target_id="c1", timestamp_ns=self.t0, x_m=0.6, y_m=0.0, quality=0.1)
        cmd, state, reason = self.controller.step(obs, now_ns=self.t0)
        self.assertEqual(state, ControllerState.TARGET_LOST)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps), (0.0, 0.0))
        self.assertIn("quality", reason)

    def test_outside_envelope_refuses_motion(self):
        obs = TargetObservation(target_id="c1", timestamp_ns=self.t0, x_m=1.5, y_m=0.0, quality=0.9)
        cmd, state, reason = self.controller.step(obs, now_ns=self.t0)
        self.assertEqual(state, ControllerState.OUTSIDE_LOCALIZATION_ENVELOPE)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps), (0.0, 0.0))

        obs2 = TargetObservation(target_id="c1", timestamp_ns=self.t0, x_m=0.6, y_m=0.55, quality=0.9)
        cmd2, state2, _ = self.controller.step(obs2, now_ns=self.t0)
        self.assertEqual(state2, ControllerState.OUTSIDE_LOCALIZATION_ENVELOPE)
        self.assertEqual((cmd2.vx_mps, cmd2.vy_mps), (0.0, 0.0))

    def test_already_in_handoff_region_is_manipulation_ready(self):
        h = self.config.handoff
        obs = TargetObservation(target_id="c1", timestamp_ns=self.t0, x_m=h.center_x_m, y_m=0.0, quality=0.9)
        cmd, state, reason = self.controller.step(obs, now_ns=self.t0)
        self.assertEqual(state, ControllerState.MANIPULATION_READY)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps, cmd.omega_rps), (0.0, 0.0, 0.0))

    def test_invalid_numeric_values_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                obs = TargetObservation(target_id="c1", timestamp_ns=self.t0, x_m=bad, y_m=0.0, quality=0.9)
                cmd, state, reason = self.controller.step(obs, now_ns=self.t0)
                self.assertEqual(state, ControllerState.TARGET_LOST)
                self.assertEqual((cmd.vx_mps, cmd.vy_mps), (0.0, 0.0))
                self.assertIn("invalid", reason)

    def test_target_id_change_does_not_inherit_velocity(self):
        obs_a = TargetObservation(target_id="cube_a", timestamp_ns=self.t0, x_m=0.90, y_m=0.0, quality=0.9)
        cmd_a1, state_a1, _ = self.controller.step(obs_a, now_ns=self.t0)
        t1 = self.t0 + self.config.command_ttl_ns
        cmd_a2, state_a2, _ = self.controller.step(
            TargetObservation(target_id="cube_a", timestamp_ns=t1, x_m=0.85, y_m=0.0, quality=0.9), now_ns=t1
        )
        self.assertGreater(cmd_a2.vx_mps, cmd_a1.vx_mps, "same target should be allowed to ramp up")

        t2 = t1 + self.config.command_ttl_ns
        cmd_b, state_b, _ = self.controller.step(
            TargetObservation(target_id="cube_b", timestamp_ns=t2, x_m=0.90, y_m=0.0, quality=0.9), now_ns=t2
        )
        # a brand new target must be slew-limited from a zero baseline, not from cube_a's last velocity
        self.assertLessEqual(cmd_b.vx_mps, self.config.max_slew_mps_per_step + 1e-9)

    def test_construction_rejects_invalid_config(self):
        with self.assertRaises(ValueError):
            ControllerConfig(vx_max_mps=0)
        with self.assertRaises(ValueError):
            ControllerConfig(kx=-1)


def v2(x_m, *, published_ns=1_000_000_000, capture_ns=None, target_id="cube_b"):
    """A v2 observation as the service would hand it over: capture 2 s before publication, a 30 s bound."""
    return TargetObservationV2(target_id=target_id, capture_ns=published_ns - 2_000_000_000 if capture_ns is None else capture_ns,
                               published_ns=published_ns, motion_epoch=3, x_m=x_m, max_age_ns=30_000_000_000,
                               observation_id="20260926T120000Z-0001")


class ContractV2Configuration(unittest.TestCase):
    """ControllerConfig.contract_v2(): the numbers the design brief fixes, copies of the executor's constants (the
    equality with run_single_cube_cycle is pinned in test_contract_v2_pins.py, not here)."""

    def test_the_values_are_the_ones_the_brief_fixes(self):
        config = ControllerConfig.contract_v2()
        self.assertEqual(config.vx_max_mps, 0.05)
        self.assertEqual(config.vy_max_mps, 0.05)
        self.assertEqual(config.omega_max_rps, 0.0)
        self.assertAlmostEqual(config.kx, 12.5)
        self.assertEqual(config.deadband_m, 0.0)
        self.assertEqual(config.max_slew_mps_per_step, 0.10)
        self.assertEqual(config.quality_min, 0.0)
        self.assertEqual(config.command_ttl_ns, 250_000_000)
        self.assertEqual((config.envelope.x_min_m, config.envelope.x_max_m, config.envelope.y_abs_max_m), (0.0, 0.120, 0.0))
        self.assertEqual((config.handoff.x_min_m, config.handoff.x_max_m, config.handoff.y_abs_max_m), (0.0, 0.008, 0.0))

    def test_the_gain_saturates_at_the_pulse_speed_for_every_advance_beyond_the_tolerance(self):
        """kx = vx_max / (tolerance / 2): the smallest advance the executor would still pulse for (just past
        8 mm) already asks for more than the pulse speed, so every commanded vx is exactly 0.05."""
        config = ControllerConfig.contract_v2()
        self.assertEqual(config.kx, V2_VX_MPS / (V2_ADVANCE_TOLERANCE_M / 2))
        self.assertGreater(config.kx * (0.0081 - config.handoff.center_x_m), config.vx_max_mps)
        self.assertGreaterEqual(config.max_slew_mps_per_step, config.vx_max_mps, "the first command must already be full speed")

    def test_the_v1_defaults_are_untouched(self):
        """The deployed robot runs v1 until the next install: its numbers do not move."""
        config = ControllerConfig()
        self.assertEqual((config.kx, config.vx_max_mps, config.deadband_m, config.quality_min, config.max_observation_age_ns),
                         (0.6, 0.15, 0.02, 0.5, 200_000_000))
        self.assertEqual((config.envelope.x_min_m, config.envelope.x_max_m, config.envelope.y_abs_max_m), (0.30, 1.10, 0.40))
        self.assertEqual((config.handoff.x_min_m, config.handoff.x_max_m, config.handoff.y_abs_max_m), (0.30, 0.40, 0.12))

    def test_the_module_words_are_the_contract_words(self):
        self.assertEqual(CONTRACT_V2, "kc.target_observation/2")
        self.assertEqual(FRAME_ANCHOR_REACH, "anchor_reach")
        self.assertEqual((V2_VX_MPS, V2_ADVANCE_TOLERANCE_M, V2_MAX_ADVANCE_M, V2_QUALITY_NONE), (0.05, 0.008, 0.120, 1.0))


class ContractV2Stepping(unittest.TestCase):
    """The controller under contract_v2(), stepped the way the v2 service steps it: on the observation's
    controller_input() with now = published_ns."""

    def setUp(self):
        self.config = ControllerConfig.contract_v2()
        self.controller = MobileApproachController(self.config)

    def step(self, observation):
        return self.controller.step(observation.controller_input(), now_ns=observation.published_ns)

    def test_the_projection_is_stamped_with_the_publication_and_quality_one(self):
        observation = v2(0.050)
        projection = observation.controller_input()
        self.assertEqual(projection.timestamp_ns, observation.published_ns)
        self.assertNotEqual(projection.timestamp_ns, observation.capture_ns)
        self.assertEqual((projection.target_id, projection.x_m, projection.y_m, projection.quality, projection.frame),
                         ("cube_b", 0.050, 0.0, 1.0, "anchor_reach"))

    def test_a_remaining_advance_of_50_mm_commands_exactly_the_pulse_speed(self):
        cmd, state, _ = self.step(v2(0.050))
        self.assertEqual(state, ControllerState.APPROACHING)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps, cmd.omega_rps), (0.05, 0.0, 0.0))
        self.assertEqual(cmd.timestamp_ns, 1_000_000_000)
        self.assertEqual(cmd.expiry_ns, 1_000_000_000 + 250_000_000)
        self.assertEqual(cmd.target_id, "cube_b")

    def test_every_advance_beyond_the_tolerance_commands_the_same_speed(self):
        for x_m in (0.0081, 0.010, 0.030, 0.080, 0.120):
            with self.subTest(x_m=x_m):
                cmd, state, _ = self.step(v2(x_m, published_ns=1_000_000_000 + int(x_m * 1e12)))
                self.assertEqual(state, ControllerState.APPROACHING)
                self.assertEqual(cmd.vx_mps, 0.05)

    def test_inside_the_executor_tolerance_is_manipulation_ready_and_zero(self):
        cmd, state, _ = self.step(v2(0.005))
        self.assertEqual(state, ControllerState.MANIPULATION_READY)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps, cmd.omega_rps), (0.0, 0.0, 0.0))
        self.assertEqual(cmd.timestamp_ns, 1_000_000_000)

    def test_beyond_the_total_advance_bound_is_outside_the_envelope_and_zero(self):
        cmd, state, _ = self.step(v2(0.121))
        self.assertEqual(state, ControllerState.OUTSIDE_LOCALIZATION_ENVELOPE)
        self.assertEqual((cmd.vx_mps, cmd.vy_mps), (0.0, 0.0))

    def test_two_observations_80_ms_apart_give_two_strictly_newer_commands(self):
        """What v1 could not do: the relay republishes every 80 ms with a new published_ns, and the gateway
        rejects a command whose timestamp is not newer than the last accepted one."""
        first, _, _ = self.step(v2(0.050, published_ns=1_000_000_000, capture_ns=999_000_000))
        second, _, _ = self.step(v2(0.050, published_ns=1_080_000_000, capture_ns=999_000_000))
        self.assertEqual((first.vx_mps, second.vx_mps), (0.05, 0.05))
        self.assertLess(first.timestamp_ns, second.timestamp_ns)
        self.assertEqual(second.timestamp_ns - first.timestamp_ns, 80_000_000)
        self.assertEqual(second.expiry_ns - first.expiry_ns, 80_000_000)
        self.assertNotEqual(first.command_id, second.command_id)

    def test_the_controller_age_check_never_zeroes_a_v2_observation_the_service_passed(self):
        """A capture 20 s old, inside its own 30 s bound, is fresh to the service; the controller sees age 0 because
        both the observation stamp and now are the publication stamp."""
        cmd, state, _ = self.step(v2(0.050, published_ns=50_000_000_000, capture_ns=30_000_000_000))
        self.assertEqual(state, ControllerState.APPROACHING)
        self.assertEqual(cmd.vx_mps, 0.05)


if __name__ == "__main__":
    unittest.main()
