# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import unittest

from vla import carry as c

LATCH, OPEN = 66.8, 80.193
LOW_POSE = (-9.4, -79.341, 33.5, 67.3)
HELD_READ, OPEN_READ = 67.633, 79.8


def held(gripper):
    return 66.8 + 0.73 < gripper <= 72.0


class FakeLink:
    """The arm follows every command at once; the jaws read HELD_READ while latched and OPEN_READ once opened. The base
    reads the commanded velocity back one tick later (`base_gain` 1.0), or stays still (`base_gain` 0.0: a host that
    ignores the pulses, as on 2026-10-11 15:50Z)."""

    def __init__(self, arm=(-9.6, -79.4, 37.0, 67.2), jaws=HELD_READ, stop_after=None, base_gain=1.0):
        self.arm, self.roll, self.jaws = list(arm), -96.7, jaws
        self.sent, self.stop_after, self.base_gain = [], stop_after, base_gain
        self.measured_vx = 0.0

    def read_state(self):
        return [*self.arm, self.roll, self.jaws]

    def read_base(self):
        return self.measured_vx

    def send(self, arm, roll, grip, vx):
        self.sent.append((list(arm), roll, grip, vx))
        self.arm = list(arm)
        self.measured_vx = vx * self.base_gain
        if grip >= OPEN:
            self.jaws = OPEN_READ

    def stopped(self):
        return self.stop_after is not None and len(self.sent) >= self.stop_after


def run(link, events=None, **kw):
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s

    return c.carry(link, latch=LATCH, open_=OPEN, held=held, start_ref=LOW_POSE, tolerance_deg=8.0, stopped=link.stopped,
                   emit=(events.append if events is not None else lambda event: None), sleep=sleep, monotonic=lambda: clock["t"], **kw)


class CarryTest(unittest.TestCase):
    def test_the_carry_sends_the_ruled_sequence(self):
        link = FakeLink()
        out = run(link)
        self.assertEqual(out["result"], "done")
        self.assertEqual(out["jaws_after_release"], OPEN_READ)
        vx = [s[3] for s in link.sent]
        self.assertEqual(set(vx), {0.0, c.BASE_V_MPS, -c.BASE_V_MPS})
        # each pulse ends once the base measured PULSE_M, about PULSE_S of commands when the base follows at once
        ticks = round(c.PULSE_S * c.HZ)
        self.assertAlmostEqual(vx.count(c.BASE_V_MPS), c.PULSES * ticks, delta=2 * c.PULSES)
        self.assertAlmostEqual(vx.count(-c.BASE_V_MPS), c.PULSES * ticks, delta=2 * c.PULSES)
        first_open = next(i for i, s in enumerate(link.sent) if s[2] >= OPEN)
        # the wheels roll forward only while the jaws hold, at the start pose; backward only after the jaws opened
        self.assertTrue(all(s[2] == LATCH and s[0] == link.sent[0][0] for s in link.sent if s[3] > 0))
        self.assertTrue(all(i > first_open and s[2] == OPEN for i, s in enumerate(link.sent) if s[3] < 0))
        # the jaws open at the release pose, and the arm ends where it started, the wheels stopped
        self.assertEqual(link.sent[first_open][0], list(c.RELEASE_POSE_DEG))
        self.assertEqual(link.sent[-1][0], [-9.6, -79.4, 37.0, 67.2])
        self.assertEqual(link.sent[-1][3], 0.0)
        self.assertEqual({s[1] for s in link.sent}, {-96.7})

    def test_nothing_held_is_refused_before_any_motion(self):
        link = FakeLink(jaws=OPEN_READ)
        out = run(link)
        self.assertEqual((out["result"], out["code"]), ("refused", "carry_nothing_held"))
        self.assertEqual(link.sent, [])

    def test_an_arm_away_from_the_low_pose_is_refused_before_any_motion(self):
        link = FakeLink(arm=(-9.6, -79.4, 50.0, 67.2))
        out = run(link)
        self.assertEqual((out["result"], out["code"]), ("refused", "carry_not_at_low_pose"))
        self.assertEqual(link.sent, [])

    def test_a_stop_mid_carry_stops_the_wheels_and_holds_the_arm(self):
        link = FakeLink(stop_after=40)  # inside the first forward pulse
        out = run(link)
        self.assertEqual(out["result"], "stopped")
        last, before = link.sent[-1], link.sent[-2]
        self.assertEqual(last[3], 0.0)
        self.assertEqual((last[0], last[2]), (before[0], before[2]))
        self.assertEqual(len(link.sent), 41)

    def test_the_base_command_takes_only_the_ruled_pulses(self):
        self.assertEqual(c.base_command(c.BASE_V_MPS), {"x.vel": c.BASE_V_MPS, "y.vel": 0.0, "theta.vel": 0.0})
        self.assertEqual(c.base_command(0.0), {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
        for bad in (0.1, -0.2, 0.049, float("nan")):
            with self.assertRaises(ValueError):
                c.base_command(bad)

    def test_every_base_pulse_reports_the_distance_the_base_measured(self):
        events = []
        link = FakeLink()
        run(link, events=events)
        pulses = [e for e in events if e.get("phase") == "carry_base"]
        self.assertEqual([(e["direction"], e["pulse"]) for e in pulses], [("forward", 1), ("forward", 2), ("backward", 1), ("backward", 2)])
        for e in pulses:
            self.assertAlmostEqual(e["measured_m"], c.PULSE_M, delta=0.004)
        done = [e for e in events if e.get("phase") == "carry_done"][0]
        self.assertAlmostEqual(done["forward_m"], c.PULSES * c.PULSE_M, delta=0.008)
        self.assertAlmostEqual(done["backward_m"], c.PULSES * c.PULSE_M, delta=0.008)

    def test_a_base_that_does_not_move_ends_the_carry_before_the_release_with_the_block_still_held(self):
        events = []
        link = FakeLink(base_gain=0.0)
        out = run(link, events=events)
        self.assertEqual((out["result"], out["code"]), ("refused", "carry_base_stalled"))
        self.assertIn("measured 0.000 m", out["message"])
        self.assertTrue(all(s[2] == LATCH for s in link.sent), "the jaws never opened")
        self.assertEqual(link.sent[-1][3], 0.0, "the wheels are stopped")
        self.assertEqual(link.sent[-1][0], [-9.6, -79.4, 37.0, 67.2], "the arm never left the start pose")
        # a stalled pulse is commanded for at most PULSE_CAP_S before the carry gives up on it
        self.assertLessEqual(sum(1 for s in link.sent if s[3] > 0), round(c.PULSE_CAP_S * c.HZ) + 1)

    def test_a_slow_base_gets_commanded_longer_until_the_distance_is_measured(self):
        events = []
        link = FakeLink(base_gain=0.5)
        out = run(link, events=events)
        self.assertEqual(out["result"], "done")
        forward = sum(1 for s in link.sent if s[3] > 0)
        self.assertGreater(forward, round(1.8 * c.PULSES * c.PULSE_S * c.HZ))
        self.assertLessEqual(forward, c.PULSES * (round(c.PULSE_CAP_S * c.HZ) + 1))


if __name__ == "__main__":
    unittest.main()
