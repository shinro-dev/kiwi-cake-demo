#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_base_rest.py: the base-at-rest rule of autonomy/manipulation/base_rest.py against the RECORDED
# reads of this base. Operator ruling 2026-09-27 (docs/autonomous-demo/decision-log.md): the base-zero trip of run
# console-20260927T142146Z-m6ed2dd9 must not happen again, here or at any other site that judges the base at rest.
#
# THE RECORDED NUMBERS. Every value-bearing base read found in the records of 2026-09-19 to 2026-09-27 (975 at rest):
# the stdout of the controller, the measured.state events and the observation records. Each is one of the eleven
# exact triples below, and each decomposes, through the host's own kinematics, into whole velocity counts on the three
# wheels (largest residual 4.4e-16 counts). No read at rest ever showed more than ONE count on any wheel; two wheels at
# one count each at the same instant was recorded six times (probably five distinct samples: a printed read can repeat
# the logged read before it), and one of them stopped that run.
# THE MOTION NUMBERS. No body-velocity value read DURING a commanded base pulse is recorded anywhere: the primitive's
# own reads are named by id only. What a moving base reads is therefore derived from what was commanded (0.05 m/s,
# the only base speed any recorded pulse used) and from what the pad measured it did (the pulses' measured rates),
# through the same kinematics. That is stated here and in the decision log, and it is not called a recording.
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))

import base_rest  # noqa: E402

# (x.vel m/s, y.vel m/s, theta.vel deg/s) exactly as the host reported it, the wheel counts (left, back, right) it is,
# and how many at-rest reads had it.
RECORDED_AT_REST = (
    ((0.0, 0.0, 0.0), (0, 0, 0), 830),
    ((0.0022141105520437233, -0.0012783173232380342, -0.5859375), (-1, 0, 0), 56),
    ((-0.0022141105520437233, 0.0012783173232380342, 0.5859375), (1, 0, 0), 50),
    ((-0.0022141105520437224, -0.0012783173232380344, -0.5859375), (0, 0, -1), 16),
    ((0.0022141105520437224, 0.0012783173232380344, 0.5859375), (0, 0, 1), 13),
    ((-3.2775420185926097e-19, -0.002556634646476069, 0.5859374999999999), (0, 1, 0), 3),
    ((-0.004428221104087446, -2.1815971307766465e-19, -6.047127573464964e-19), (1, 0, -1), 2),
    ((-6.984728289775616e-19, 0.0025566346464760688, 1.171875), (1, 0, 1), 2),
    ((3.2775420185926097e-19, 0.002556634646476069, -0.5859374999999999), (0, -1, 0), 1),
    ((6.984728289775616e-19, -0.0025566346464760688, -1.171875), (-1, 0, -1), 1),
    ((0.002214110552043723, 0.003834951969714104, 9.999704655308141e-17), (0, -1, 1), 1),
)
# The read that stopped run console-20260927T142146Z-m6ed2dd9 at GRASP_CLOSE, holding the block, before the CLEARANCE lift:
# the left and the right wheel at one count each (theta 2 x 0.586 deg/s), no base motion commanded.
TRIP_READ = (-6.984728289775616e-19, 0.0025566346464760688, 1.171875)

# Commanded base motion, in the unit of the rule: what the host reports for it (lerobot LeKiwi._wheel_raw_to_body).
PULSE_VX_MPS = 0.05  # the only base speed any recorded pulse used (run_single_cube_cycle.BASE_VX_MPS, the capability's vx_max)
PAD_MEASURED_RATES_MM_S = (47.9, 64.0, 40.0, 200.0)  # the slowest and fastest of the 44 pad-measured pulses, and BASE_RATE_BOUNDS_MM_S


def _read(x, y, theta):
    return base_rest.at_rest(x, y, theta)


class TheDecompositionIsTheHostsOwnKinematics(unittest.TestCase):
    def test_every_recorded_read_is_whole_counts_on_the_wheels_it_names(self):
        for body, counts, _ in RECORDED_AT_REST:
            with self.subTest(body=body):
                decomposed = base_rest.wheel_counts(*body)
                self.assertEqual(3, len(decomposed))
                for got, want in zip(decomposed, counts):
                    self.assertAlmostEqual(want, got, delta=1e-9)

    def test_one_count_is_fifty_encoder_steps_per_second(self):
        # One count on the left wheel alone: 50 steps/s of a 4096-step wheel of radius 0.05 m, through the inverse matrix.
        x, y, theta = base_rest.body_velocity((1, 0, 0))
        self.assertAlmostEqual(-0.0022141105520437233, x, places=15)
        self.assertAlmostEqual(0.0012783173232380342, y, places=15)
        self.assertAlmostEqual(0.5859375, theta, places=12)
        self.assertEqual(50, base_rest.VELOCITY_COUNT_STEPS_PER_S)

    def test_decomposition_and_composition_are_inverse(self):
        for counts in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (3, -2, 7), (-11, 0, 11)):
            got = base_rest.wheel_counts(*base_rest.body_velocity(counts))
            for a, b in zip(counts, got):
                self.assertAlmostEqual(a, b, places=9)


class EveryRecordedReadAtRestIsAtRest(unittest.TestCase):
    def test_every_recorded_value_at_rest_passes(self):
        for body, counts, seen in RECORDED_AT_REST:
            with self.subTest(body=body, counts=counts, seen=seen):
                self.assertTrue(_read(*body))

    def test_the_read_that_stopped_the_run_is_at_rest(self):
        self.assertTrue(_read(*TRIP_READ))
        self.assertEqual((1, 0, 1), tuple(round(c) for c in base_rest.wheel_counts(*TRIP_READ)))

    def test_one_count_on_every_wheel_at_once_in_any_sign_is_at_rest(self):
        # The recorded maximum is two wheels at once; the rule does not depend on how many wheels dither together.
        for left in (-1, 0, 1):
            for back in (-1, 0, 1):
                for right in (-1, 0, 1):
                    with self.subTest(counts=(left, back, right)):
                        self.assertTrue(_read(*base_rest.body_velocity((left, back, right))))


class MotionIsNotAtRest(unittest.TestCase):
    def test_two_counts_on_any_one_wheel_is_not_at_rest(self):
        for wheel in range(3):
            for sign in (-1, 1):
                counts = [0, 0, 0]
                counts[wheel] = 2 * sign
                with self.subTest(counts=counts):
                    self.assertFalse(_read(*base_rest.body_velocity(counts)))

    def test_a_commanded_pulse_is_not_at_rest_in_any_direction(self):
        for body in ((PULSE_VX_MPS, 0.0, 0.0), (-PULSE_VX_MPS, 0.0, 0.0), (0.0, PULSE_VX_MPS, 0.0), (0.0, -PULSE_VX_MPS, 0.0)):
            with self.subTest(body=body):
                self.assertFalse(_read(*body))
                self.assertGreaterEqual(max(abs(c) for c in base_rest.wheel_counts(*body)), 11)

    def test_every_pad_measured_pulse_rate_is_not_at_rest(self):
        for rate in PAD_MEASURED_RATES_MM_S:
            with self.subTest(rate_mm_s=rate):
                self.assertFalse(_read(rate / 1000.0, 0.0, 0.0))
                self.assertFalse(_read(-rate / 1000.0, 0.0, 0.0))

    def test_a_rotation_of_a_few_degrees_per_second_is_not_at_rest(self):
        for theta in (3.0, -3.0, 10.0):
            with self.subTest(theta_dps=theta):
                self.assertFalse(_read(0.0, 0.0, theta))

    def test_the_whole_deceleration_from_a_pulse_is_not_at_rest_until_one_count(self):
        # A pulse decelerating to rest passes through every whole count on its two driven wheels; only the last one is rest.
        driven = round(abs(base_rest.wheel_counts(PULSE_VX_MPS, 0.0, 0.0)[0]))
        for step in range(driven, 1, -1):
            with self.subTest(counts=step):
                self.assertFalse(_read(*base_rest.body_velocity((-step, 0, step))))
        self.assertTrue(_read(*base_rest.body_velocity((-1, 0, 1))))

    def test_a_read_that_is_not_a_number_is_not_at_rest(self):
        for bad in ((math.nan, 0.0, 0.0), (0.0, math.inf, 0.0), (0.0, 0.0, -math.inf)):
            with self.subTest(read=bad):
                self.assertFalse(_read(*bad))


class TheRuleIsOneRuleEverywhere(unittest.TestCase):
    def test_the_key_forms_of_every_site_read_through_the_same_rule(self):
        dotted = {"x.vel": TRIP_READ[0], "y.vel": TRIP_READ[1], "theta.vel": TRIP_READ[2]}
        underscored = {"x_vel": TRIP_READ[0], "y_vel": TRIP_READ[1], "theta_vel": TRIP_READ[2]}
        self.assertTrue(base_rest.reads_at_rest(dotted))
        self.assertTrue(base_rest.reads_at_rest(underscored))
        self.assertFalse(base_rest.reads_at_rest({**dotted, "x.vel": PULSE_VX_MPS}))
        self.assertFalse(base_rest.reads_at_rest({**underscored, "x_vel": PULSE_VX_MPS}))

    def test_a_read_without_the_three_velocities_is_refused_not_guessed(self):
        with self.assertRaises(KeyError):
            base_rest.reads_at_rest({"x.vel": 0.0, "y.vel": 0.0})


if __name__ == "__main__":
    unittest.main()
