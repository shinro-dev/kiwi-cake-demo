#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_manual_target_harness.py: deterministic tests for the
# HARNESS's runtime wiring (bin/laptop/manual_target_harness.py) -- not just
# StableMotionGateway in isolation. Proves the harness's own polling loop
# actually calls gateway.tick() often enough that an unrefreshed lease
# expires to zero without another operator keystroke, independent of the
# LeKiwi host's separate ~500ms watchdog. No hardware, no real time.sleep()
# except in the two tiny StdinLineSource boundary tests, which use a real
# os.pipe() with a short real timeout because that is the actual select()
# code path -- everything else drives a FakeClock, no real time elapses.
# Run directly: python3 autonomy/tests/test_manual_target_harness.py
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop"))

from clock import FakeClock, MonotonicClock  # noqa: E402
from mobile_approach import DesiredBaseMotion, MobileApproachController  # noqa: E402
from stable_motion_gateway import GatewayLimits, GatewayOutcome, StableMotionGateway  # noqa: E402
from transport import FakeBaseMotionTransport  # noqa: E402

import manual_target_harness as harness  # noqa: E402


def cmd(command_id="c1", target_id="t", t_ns=0, vx=10.0, vy=-10.0, omega=5.0, ttl_ns=1_000_000_000):
    return DesiredBaseMotion(command_id=command_id, target_id=target_id, timestamp_ns=t_ns, vx_mps=vx, vy_mps=vy, omega_rps=omega, expiry_ns=t_ns + ttl_ns)


class FakeLineSource:
    """Test double for harness.StdinLineSource: each poll() call simulates
    tick_interval_s of elapsed time on the paired FakeClock -- what a real
    select() timeout would cost -- then returns the next scripted item: a
    line (str), None (timeout, no input arrived), or harness.EOF."""

    def __init__(self, clock, script):
        self._clock = clock
        self._script = list(script)

    def poll(self, timeout_s):
        self._clock.advance(int(timeout_s * 1_000_000_000))
        if not self._script:
            return harness.EOF
        return self._script.pop(0)


def _silent(*args, **kwargs):
    pass


class TestHarnessTickWiring(unittest.TestCase):
    def setUp(self):
        self.transport = FakeBaseMotionTransport()
        self.clock = FakeClock(1_000_000_000)
        self.gateway = StableMotionGateway(self.transport, self.clock)
        self.controller = MobileApproachController()

    def _run(self, script, command_ttl_ms=150, tick_interval_s=0.03, print_fn=_silent):
        src = FakeLineSource(self.clock, script)
        return harness.run_session(
            self.transport, self.clock, self.gateway, self.controller, src,
            command_ttl_ms=command_ttl_ms, tick_interval_s=tick_interval_s, print_fn=print_fn,
        )

    def test_ticks_periodically_while_waiting_for_input(self):
        tick_count = self._run([None, None, None, None, "QUIT"])
        # one tick() per loop pass: 4 idle timeouts + the pass that reads QUIT
        self.assertEqual(tick_count, 5)

    def test_unrefreshed_lease_expires_to_zero_without_another_keystroke(self):
        messages = []

        def capture(*args, **kwargs):
            if args:
                messages.append(str(args[0]))

        # ENABLE, submit one command producing nonzero vx (x=0.5 is outside
        # the handoff region but inside the trusted envelope -- see
        # mobile_approach.py's LocalizationEnvelope/HandoffRegion), then six
        # idle timeouts (6 * 30ms = 180ms > the 150ms TTL) with no further
        # keystroke, then QUIT.
        self._run(["ENABLE", "0.5 0.0", None, None, None, None, None, None, "QUIT"], print_fn=capture)

        velocity_calls = [c for c in self.transport.calls if c[0] == "velocity"]
        self.assertEqual(len(velocity_calls), 1, "the lease must not be replayed by later ticks")
        self.assertGreater(velocity_calls[0][1], 0.0, "expected a positive vx from the submitted command")

        expired_index = next(i for i, m in enumerate(messages) if "ZERO_EXPIRED" in m)
        shutdown_index = next(i for i, m in enumerate(messages) if "stopped, exiting" in m)
        self.assertLess(
            expired_index, shutdown_index,
            "the lease must expire to zero via the harness's own tick() loop, before QUIT's own shutdown stop",
        )
        self.assertEqual(self.gateway.applied_velocity, (0.0, 0.0, 0.0))

    def test_stop_zeroes_immediately_regardless_of_tick_cadence(self):
        self._run(["ENABLE", "0.5 0.0", "STOP", "QUIT"])
        self.assertEqual(self.gateway.applied_velocity, (0.0, 0.0, 0.0))
        self.assertTrue(self.gateway.operator_stopped)
        kinds = [c[0] for c in self.transport.calls]
        velocity_idx = kinds.index("velocity")
        self.assertEqual(kinds[velocity_idx + 1], "zero", "STOP must zero on the very next transport call, not a later tick")

    def test_quit_shuts_down_to_zero(self):
        self._run(["QUIT"])
        self.assertEqual(self.gateway.applied_velocity, (0.0, 0.0, 0.0))
        self.assertTrue(self.gateway.operator_stopped)
        self.assertEqual(self.transport.calls[-1], ("zero",))

    def test_shutdown_leaves_no_lingering_threads(self):
        before = threading.active_count()
        self._run(["QUIT"])
        self.assertEqual(threading.active_count(), before, "the polling loop is single-threaded: nothing should outlive run_session")

    def test_eof_on_stdin_shuts_down_cleanly(self):
        # no QUIT in the script -- the source simply runs dry and returns EOF
        self._run([])
        self.assertEqual(self.gateway.applied_velocity, (0.0, 0.0, 0.0))
        self.assertEqual(self.transport.calls[-1], ("zero",))


class TestBuildLimits(unittest.TestCase):
    def test_defaults_when_no_flags_given(self):
        args = harness.build_parser().parse_args([])
        limits = harness.build_limits(args)
        defaults = GatewayLimits()
        self.assertEqual(limits.vx_max_mps, defaults.vx_max_mps)
        self.assertEqual(limits.vy_max_mps, defaults.vy_max_mps)
        self.assertEqual(limits.omega_max_rps, defaults.omega_max_rps)

    def test_cli_overrides_become_limit_fields(self):
        args = harness.build_parser().parse_args(["--vx-max", "0.05", "--vy-max", "0.05", "--omega-max", "0.01"])
        limits = harness.build_limits(args)
        self.assertEqual(limits.vx_max_mps, 0.05)
        self.assertEqual(limits.vy_max_mps, 0.05)
        self.assertEqual(limits.omega_max_rps, 0.01)

    def test_partial_override_preserves_other_production_defaults(self):
        args = harness.build_parser().parse_args(["--vx-max", "0.05"])
        limits = harness.build_limits(args)
        defaults = GatewayLimits()
        self.assertEqual(limits.vx_max_mps, 0.05)
        self.assertEqual(limits.vy_max_mps, defaults.vy_max_mps)
        self.assertEqual(limits.omega_max_rps, defaults.omega_max_rps)

    def test_zero_override_is_rejected_not_weakened(self):
        args = harness.build_parser().parse_args(["--omega-max", "0"])
        with self.assertRaises(SystemExit):
            harness.build_limits(args)

    def test_negative_override_is_rejected(self):
        args = harness.build_parser().parse_args(["--vx-max", "-0.1"])
        with self.assertRaises(SystemExit):
            harness.build_limits(args)

    def test_override_actually_becomes_the_gateways_hard_clamp(self):
        transport = FakeBaseMotionTransport()
        clock = FakeClock(0)
        limits = GatewayLimits(vx_max_mps=0.05, vy_max_mps=0.05, omega_max_rps=0.01)
        gw = StableMotionGateway(transport, clock, limits)
        gw.enable_autonomous()
        vx, vy, omega, outcome, _ = gw.submit_command(cmd())
        self.assertEqual(outcome, GatewayOutcome.APPLIED)
        self.assertAlmostEqual(vx, 0.05)
        self.assertAlmostEqual(vy, -0.05)
        self.assertAlmostEqual(omega, 0.01)


class TestStdinLineSourceRealSelect(unittest.TestCase):
    """The one boundary this suite can't fake: does select()-based polling
    on a real fd actually behave as run_session assumes? Real os.pipe(),
    but only ever a very short real timeout (<=50ms) and only when there
    genuinely is nothing to read."""

    def _piped(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        return r, w

    def test_poll_returns_line_when_immediately_available(self):
        r, w = self._piped()
        os.write(w, b"1 2\n")
        os.close(w)
        self.assertEqual(harness.StdinLineSource(fd=r).poll(0.2), "1 2")

    def test_poll_returns_none_on_real_timeout_with_no_input(self):
        r, w = self._piped()
        try:
            self.assertIsNone(harness.StdinLineSource(fd=r).poll(0.05))
        finally:
            os.close(w)

    def test_poll_returns_EOF_at_end_of_stream(self):
        r, w = self._piped()
        os.close(w)
        self.assertIs(harness.StdinLineSource(fd=r).poll(0.2), harness.EOF)

    def test_poll_returns_lines_one_at_a_time_even_when_written_together(self):
        # Regression for the bug this suite exists to catch: a single write()
        # containing two newline-terminated lines must still come back one
        # line per poll() call, not both bundled into the first call (which
        # would silently skip the intervening tick() the caller relies on).
        r, w = self._piped()
        os.write(w, b"ENABLE\n0.5 0.0\n")
        os.close(w)
        src = harness.StdinLineSource(fd=r)
        self.assertEqual(src.poll(0.2), "ENABLE")
        self.assertEqual(src.poll(0.2), "0.5 0.0")
        self.assertIs(src.poll(0.2), harness.EOF)

    def test_poll_returns_none_when_partial_line_buffered_with_no_newline_yet(self):
        r, w = self._piped()
        os.write(w, b"0.5")  # no trailing newline -- not a complete line yet
        src = harness.StdinLineSource(fd=r)
        self.assertIsNone(src.poll(0.05))
        os.write(w, b" 0.0\n")
        os.close(w)
        self.assertEqual(src.poll(0.2), "0.5 0.0")


class TestRunSessionOverRealPipeRegression(unittest.TestCase):
    """End-to-end reproduction, through run_session + the real
    StdinLineSource (not FakeLineSource), of exactly the failure observed in
    a live manual smoke test: ENABLE and one command arriving in a single
    write(), a real pause with nothing further, then QUIT. Before the
    StdinLineSource rewrite this held the command unprocessed (its TTL never
    started) until QUIT arrived, and ZERO_EXPIRED never fired. Uses a short
    but real TTL/tick interval, a real MonotonicClock, and a real pause --
    trading determinism for exercising the actual OS select()/read() path
    end-to-end; kept well under a second."""

    def test_command_expires_to_zero_across_a_real_pause_without_another_keystroke(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        transport = FakeBaseMotionTransport()
        clock = MonotonicClock()  # a real pause needs the real clock, exactly as main() wires it in production
        gateway = StableMotionGateway(transport, clock)
        controller = MobileApproachController()
        messages = []

        def capture(*args, **kwargs):
            if args:
                messages.append(str(args[0]))

        os.write(w, b"ENABLE\n0.5 0.0\n")

        def feed_quit_after_pause():
            import time
            time.sleep(0.25)
            os.write(w, b"QUIT\n")
            os.close(w)

        feeder = threading.Thread(target=feed_quit_after_pause, daemon=True)
        feeder.start()
        try:
            harness.run_session(
                transport, clock, gateway, controller, harness.StdinLineSource(fd=r),
                command_ttl_ms=60, tick_interval_s=0.02, print_fn=capture,
            )
        finally:
            feeder.join(timeout=2)

        velocity_calls = [c for c in transport.calls if c[0] == "velocity"]
        self.assertEqual(len(velocity_calls), 1, "the command must have actually reached the transport")
        self.assertTrue(
            any("ZERO_EXPIRED" in m for m in messages),
            f"expected the harness's own tick() loop to expire the lease during the pause; got: {messages}",
        )
        self.assertEqual(gateway.applied_velocity, (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
