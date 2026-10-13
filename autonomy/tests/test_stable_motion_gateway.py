#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_stable_motion_gateway.py: exhaustive deterministic
# tests for autonomy/motion/stable_motion_gateway.py using the fake
# transport and fake clock. No hardware, no real time.sleep(), no network.
# Run directly: python3 autonomy/tests/test_stable_motion_gateway.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

from clock import FakeClock
from mobile_approach import DesiredBaseMotion
from stable_motion_gateway import GatewayLimits, GatewayOutcome, StableMotionGateway
from transport import FakeBaseMotionTransport

TTL_NS = 250_000_000


def cmd(command_id="c1", target_id="cube_a", t_ns=1_000_000_000, vx=0.1, vy=0.0, omega=0.0, ttl_ns=TTL_NS):
    return DesiredBaseMotion(command_id=command_id, target_id=target_id, timestamp_ns=t_ns, vx_mps=vx, vy_mps=vy, omega_rps=omega, expiry_ns=t_ns + ttl_ns)


class TestStartupAndModeGates(unittest.TestCase):
    def setUp(self):
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.gw = StableMotionGateway(self.transport, self.clock)

    def test_startup_is_zero_and_nothing_sent(self):
        self.assertFalse(self.gw.autonomous_enabled)
        self.assertEqual(self.gw.applied_velocity, (0.0, 0.0, 0.0))
        self.assertEqual(self.transport.calls, [], "nothing should be sent merely because the gateway was constructed")

    def test_valid_command_while_autonomous_disabled_not_forwarded(self):
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd())
        self.assertEqual(outcome, GatewayOutcome.ZERO_AUTONOMOUS_DISABLED)
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertTrue(all(c[0] == "zero" for c in self.transport.calls), f"expected only zero calls, got {self.transport.calls}")

    def test_enabling_autonomous_does_not_replay_previous_command(self):
        self.gw.submit_command(cmd(command_id="stale", t_ns=1_000_000_000, vx=0.1))  # rejected, autonomous disabled
        self.gw.enable_autonomous()
        self.transport.calls.clear()
        # no NEW submit_command yet -- tick() must not invent motion from the old rejected one
        vx, vy, omega, outcome, reason = self.gw.tick()
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertFalse(any(c[0] == "velocity" for c in self.transport.calls), "tick() must not send velocity for a command that was never accepted")

    def test_fresh_command_after_enable_is_forwarded(self):
        self.gw.enable_autonomous()
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd(vx=0.1, vy=0.05))
        self.assertEqual(outcome, GatewayOutcome.APPLIED)
        self.assertGreater(vx, 0.0)
        self.assertEqual(self.transport.calls[-1][0], "velocity")


class TestBounds(unittest.TestCase):
    def setUp(self):
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.limits = GatewayLimits(vx_max_mps=0.2, vy_max_mps=0.2, omega_max_rps=0.5,
                                     max_dvx_per_update_mps=10, max_dvy_per_update_mps=10, max_domega_per_update_rps=10)
        self.gw = StableMotionGateway(self.transport, self.clock, self.limits)
        self.gw.enable_autonomous()

    def test_vx_limit_enforced(self):
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd(vx=999.0))
        self.assertLessEqual(abs(vx), self.limits.vx_max_mps + 1e-12)

    def test_vy_limit_enforced(self):
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd(vy=999.0))
        self.assertLessEqual(abs(vy), self.limits.vy_max_mps + 1e-12)

    def test_omega_limit_enforced(self):
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd(omega=999.0))
        self.assertLessEqual(abs(omega), self.limits.omega_max_rps + 1e-12)

    def test_gateway_ignores_mobile_approach_claimed_bounds(self):
        # mobile_approach's own advisory clamp is irrelevant: the gateway must
        # enforce its OWN configured limits regardless of what the producer sent.
        huge = DesiredBaseMotion(command_id="c", target_id="t", timestamp_ns=1_000_000_000, vx_mps=50.0, vy_mps=-50.0, omega_rps=50.0, expiry_ns=1_000_000_000 + TTL_NS)
        vx, vy, omega, outcome, reason = self.gw.submit_command(huge)
        self.assertLessEqual(abs(vx), self.limits.vx_max_mps + 1e-12)
        self.assertLessEqual(abs(vy), self.limits.vy_max_mps + 1e-12)
        self.assertLessEqual(abs(omega), self.limits.omega_max_rps + 1e-12)

    def test_nan_inf_rejected_to_zero(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                transport = FakeBaseMotionTransport()
                gw = StableMotionGateway(transport, FakeClock(1_000_000_000), self.limits)
                gw.enable_autonomous()
                vx, vy, omega, outcome, reason = gw.submit_command(cmd(vx=bad))
                self.assertEqual(outcome, GatewayOutcome.REJECTED_INVALID_NUMBERS)
                self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))

    def test_acceleration_change_bound_enforced(self):
        tight = GatewayLimits(max_dvx_per_update_mps=0.02)
        transport = FakeBaseMotionTransport()
        gw = StableMotionGateway(transport, FakeClock(1_000_000_000), tight)
        gw.enable_autonomous()
        vx1, *_ = gw.submit_command(cmd(command_id="a", t_ns=1_000_000_000, vx=0.15))
        self.assertLessEqual(vx1, tight.max_dvx_per_update_mps + 1e-12)
        vx2, *_ = gw.submit_command(cmd(command_id="b", t_ns=1_000_000_000 + TTL_NS, vx=0.15))
        self.assertLessEqual(vx2 - vx1, tight.max_dvx_per_update_mps + 1e-12)


class TestExpiry(unittest.TestCase):
    def setUp(self):
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.gw = StableMotionGateway(self.transport, self.clock)
        self.gw.enable_autonomous()

    def test_expired_command_rejected_on_arrival(self):
        old = cmd(t_ns=1_000_000_000, ttl_ns=TTL_NS)
        self.clock.advance(TTL_NS + 1)
        vx, vy, omega, outcome, reason = self.gw.submit_command(old)
        self.assertEqual(outcome, GatewayOutcome.REJECTED_EXPIRED_ON_ARRIVAL)
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))

    def test_live_command_expires_to_zero_via_tick(self):
        self.gw.submit_command(cmd(t_ns=self.clock.now_ns(), vx=0.1))
        self.assertNotEqual(self.gw.applied_velocity, (0.0, 0.0, 0.0))
        self.clock.advance(TTL_NS + 1)
        vx, vy, omega, outcome, reason = self.gw.tick()
        self.assertEqual(outcome, GatewayOutcome.ZERO_EXPIRED)
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertEqual(self.transport.calls[-1][0], "zero")


class TestDisconnect(unittest.TestCase):
    def setUp(self):
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.gw = StableMotionGateway(self.transport, self.clock)
        self.gw.enable_autonomous()

    def test_disconnect_forces_zero(self):
        self.gw.submit_command(cmd(vx=0.1))
        self.transport.set_connected(False)
        vx, vy, omega, outcome, reason = self.gw.tick()
        self.assertEqual(outcome, GatewayOutcome.ZERO_DISCONNECTED)
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))

    def test_reconnect_does_not_replay_stale_motion(self):
        self.gw.submit_command(cmd(command_id="a", t_ns=self.clock.now_ns(), vx=0.1))
        self.transport.set_connected(False)
        self.gw.tick()  # zeroes out and invalidates the active command
        self.transport.set_connected(True)
        self.transport.calls.clear()
        vx, vy, omega, outcome, reason = self.gw.tick()
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertFalse(any(c[0] == "velocity" for c in self.transport.calls), "reconnect must not resend the old velocity")


class TestOperatorStop(unittest.TestCase):
    def setUp(self):
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.gw = StableMotionGateway(self.transport, self.clock)
        self.gw.enable_autonomous()

    def test_operator_stop_zeroes_immediately(self):
        self.gw.submit_command(cmd(vx=0.1))
        self.assertNotEqual(self.gw.applied_velocity, (0.0, 0.0, 0.0))
        self.gw.operator_stop()  # no return value by design -- it's a latch, not a query; assert via state
        self.assertEqual(self.gw.applied_velocity, (0.0, 0.0, 0.0))
        self.assertTrue(self.gw.operator_stopped)
        self.assertEqual(self.transport.calls[-1][0], "zero")

    def test_commands_after_operator_stop_remain_suppressed(self):
        self.gw.operator_stop()
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd(vx=0.1))
        self.assertEqual(outcome, GatewayOutcome.ZERO_OPERATOR_STOPPED)
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))

    def test_explicit_reset_required_before_future_motion(self):
        self.gw.operator_stop()
        self.gw.submit_command(cmd(vx=0.1))  # still suppressed
        self.assertTrue(self.gw.operator_stopped)
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd(command_id="after-stop-no-reset", vx=0.1))
        self.assertEqual(outcome, GatewayOutcome.ZERO_OPERATOR_STOPPED, "must stay suppressed without an explicit reset()")
        self.gw.reset()
        self.assertFalse(self.gw.operator_stopped)
        vx, vy, omega, outcome, reason = self.gw.submit_command(cmd(command_id="after-reset", t_ns=self.clock.now_ns(), vx=0.1))
        self.assertEqual(outcome, GatewayOutcome.APPLIED)


class TestReplayAndIdentity(unittest.TestCase):
    def setUp(self):
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.gw = StableMotionGateway(self.transport, self.clock)
        self.gw.enable_autonomous()

    def test_stale_replay_cannot_replace_newer_command(self):
        newer = cmd(command_id="newer", t_ns=2_000_000_000, vx=0.12)
        older = cmd(command_id="older", t_ns=1_500_000_000, vx=0.02)
        self.gw.submit_command(newer)
        applied_after_newer = self.gw.applied_velocity
        vx, vy, omega, outcome, reason = self.gw.submit_command(older)
        self.assertEqual(outcome, GatewayOutcome.REJECTED_STALE_OR_REPLAYED)
        self.assertEqual(self.gw.applied_velocity, applied_after_newer, "a stale replay must not disturb the already-applied newer command")

    def test_duplicate_command_is_idempotent(self):
        c = cmd(command_id="dup", vx=0.1)
        r1 = self.gw.submit_command(c)
        calls_after_first = len(self.transport.calls)
        r2 = self.gw.submit_command(c)
        self.assertEqual(r2[4], "duplicate command_id, already applied")
        self.assertEqual(r1[:3], r2[:3])
        self.assertEqual(len(self.transport.calls), calls_after_first, "a duplicate must not cause a second transport call")

    def test_target_change_does_not_leak_previous_velocity(self):
        self.gw.submit_command(cmd(command_id="a1", target_id="cube_a", t_ns=1_000_000_000, vx=0.15))
        self.gw.submit_command(cmd(command_id="a2", target_id="cube_a", t_ns=1_000_000_000 + TTL_NS, vx=0.15))
        vx_before_switch = self.gw.applied_velocity[0]
        self.assertGreater(vx_before_switch, 0.05, "same target should have ramped up over two updates")

        vx, vy, omega, outcome, reason = self.gw.submit_command(
            cmd(command_id="b1", target_id="cube_b", t_ns=1_000_000_000 + 2 * TTL_NS, vx=0.15)
        )
        default_limits = GatewayLimits()
        self.assertLessEqual(vx, default_limits.max_dvx_per_update_mps + 1e-9, "a new target must ramp from zero, not inherit cube_a's velocity")


class TestTransportFailure(unittest.TestCase):
    def test_transport_exception_degrades_to_zero_not_a_crash(self):
        transport = FakeBaseMotionTransport()
        gw = StableMotionGateway(transport, FakeClock(1_000_000_000), GatewayLimits())
        gw.enable_autonomous()
        transport.raise_on_send = RuntimeError("simulated link fault")
        vx, vy, omega, outcome, reason = gw.submit_command(cmd(vx=0.1))
        self.assertEqual(outcome, GatewayOutcome.ZERO_TRANSPORT_FAILURE)
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertEqual(gw.applied_velocity, (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
