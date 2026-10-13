#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_integration_offline.py: the pre-hardware proof --
# synthetic TargetObservation -> mobile_approach -> DesiredBaseMotion ->
# stable_motion_gateway -> fake transport, wired together exactly as
# docs/autonomous-demo/stable-motion-gateway-contract.md specifies. No
# hardware, no real time, no Cake.
# Run directly: python3 autonomy/tests/test_integration_offline.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

from clock import FakeClock
from mobile_approach import ControllerState, MobileApproachController, TargetObservation
from stable_motion_gateway import GatewayOutcome, StableMotionGateway
from transport import FakeBaseMotionTransport

STEP_DT_NS = 250_000_000  # matches ControllerConfig.command_ttl_ns default


class TestOfflineIntegration(unittest.TestCase):
    def setUp(self):
        self.controller = MobileApproachController()
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.gateway = StableMotionGateway(self.transport, self.clock)
        self.gateway.enable_autonomous()

    def run_cycle(self, x, y, target_id="cube_sim", quality=0.9):
        """One observe -> mobile_approach -> gateway -> transport cycle,
        returning (applied_vx, applied_vy, applied_omega, mobile_approach_state)."""
        now_ns = self.clock.now_ns()
        obs = TargetObservation(target_id=target_id, timestamp_ns=now_ns, x_m=x, y_m=y, quality=quality)
        command, mobile_state, _ = self.controller.step(obs, now_ns=now_ns)
        vx, vy, omega, outcome, _ = self.gateway.submit_command(command)
        return vx, vy, omega, mobile_state, outcome

    def test_converges_toward_handoff_region_through_the_gateway(self):
        x, y = 0.90, 0.20
        handoff = self.controller.config.handoff
        cx = handoff.center_x_m

        def dist():
            return ((x - cx) ** 2 + y ** 2) ** 0.5

        start_dist = dist()
        reached_ready = False
        for _ in range(40):
            vx, vy, omega, mobile_state, outcome = self.run_cycle(x, y)
            self.assertLessEqual(abs(vx), self.gateway.limits.vx_max_mps + 1e-9, "gateway must never exceed its own vx bound")
            self.assertLessEqual(abs(vy), self.gateway.limits.vy_max_mps + 1e-9, "gateway must never exceed its own vy bound")
            self.assertLessEqual(abs(omega), self.gateway.limits.omega_max_rps + 1e-9, "gateway must never exceed its own omega bound")
            if mobile_state == ControllerState.MANIPULATION_READY:
                reached_ready = True
                break
            x -= vx * (STEP_DT_NS / 1e9)
            y -= vy * (STEP_DT_NS / 1e9)
            self.clock.advance(STEP_DT_NS)

        self.assertTrue(reached_ready, "mobile_approach did not reach MANIPULATION_READY through the gateway within 40 cycles")
        self.assertLess(dist(), start_dist * 0.5)

    def test_stopping_observations_eventually_zeros_via_ttl(self):
        self.run_cycle(0.90, 0.0)
        self.assertNotEqual(self.gateway.applied_velocity, (0.0, 0.0, 0.0))
        # no more observations arrive; only time passes and tick() is polled, as
        # the real gateway would be (independent of mobile_approach's own cadence)
        self.clock.advance(STEP_DT_NS + 1)
        vx, vy, omega, outcome, reason = self.gateway.tick()
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertEqual(outcome, GatewayOutcome.ZERO_EXPIRED)

    def test_operator_stop_interrupts_the_convergence_loop(self):
        x, y = 0.90, 0.0
        for _ in range(3):
            vx, vy, omega, mobile_state, outcome = self.run_cycle(x, y)
            x -= vx * (STEP_DT_NS / 1e9)
            y -= vy * (STEP_DT_NS / 1e9)
            self.clock.advance(STEP_DT_NS)
        self.assertNotEqual(self.gateway.applied_velocity, (0.0, 0.0, 0.0), "should be mid-approach before the stop")

        self.gateway.operator_stop()
        self.assertEqual(self.gateway.applied_velocity, (0.0, 0.0, 0.0))

        # mobile_approach keeps producing commands (it does not know about operator stop --
        # that is the gateway's job, per the contract), but none of them may move the base.
        vx, vy, omega, mobile_state, outcome = self.run_cycle(x, y)
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertEqual(outcome, GatewayOutcome.ZERO_OPERATOR_STOPPED)

    def test_target_change_does_not_leak_velocity_end_to_end(self):
        x, y = 0.90, 0.0
        for _ in range(3):
            vx, vy, omega, mobile_state, outcome = self.run_cycle(x, y, target_id="cube_a")
            x -= vx * (STEP_DT_NS / 1e9)
            self.clock.advance(STEP_DT_NS)
        vx_before_switch = self.gateway.applied_velocity[0]
        self.assertGreater(vx_before_switch, 0.05, "should have ramped up approaching cube_a")

        self.clock.advance(STEP_DT_NS)
        vx, vy, omega, mobile_state, outcome = self.run_cycle(0.90, 0.0, target_id="cube_b")
        self.assertLessEqual(vx, self.gateway.limits.max_dvx_per_update_mps + 1e-9,
                              "switching to cube_b must not inherit cube_a's velocity through mobile_approach+gateway")


if __name__ == "__main__":
    unittest.main()
