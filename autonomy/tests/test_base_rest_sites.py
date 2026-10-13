#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_base_rest_sites.py: EVERY site that judges the base at rest from the host's velocity reads through
# the one rule of autonomy/manipulation/base_rest.py, and none of them stops on the dither this base really shows.
# Operator ruling 2026-09-27 (docs/autonomous-demo/decision-log.md): "Fix the trip. This shouldn't have happened and should
# never fail in the same way in other places."
#   - the executor's base-zero guard and the read taken with each observation (scan_world._reads_zero);
#   - the stop confirmation of the proven forward primitive (run_hybrid_pick_place), of the proven reverse primitive
#     (run_single_cube_cycle.make_move_base) and of the capability relay (capability_pulse), which read up to six times and
#     returned True only at an EXACT zero;
#   - the "stationary" flag of the stamped capture the capability's observation is refused without (Cycle.observe);
#   - the hybrid state machine's stationary check and the stage-1 coexistence report (both exact zero, 1e-6).
# A whole mission run is then made to read the recorded dither on EVERY read, never an exact zero, and must be the same run.
# The supervised host's attach check (bin/pi/lekiwi_host_safe_attach.py) reads the raw registers, not the body velocity:
# since 2026-10-01 it takes a wheel at one count (50 raw, either sign) with goal velocity exactly 0 as at rest, which is
# this rule on every whole-count read (TheHostAttach below). Its body is pinned and awaits a hardware re-proof
# (test_host_safe_attach_static.py); the sweep below names its line and why.
import itertools
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for sub in ("autonomy/manipulation", "autonomy/motion", "autonomy/perception", "bin/laptop/manipulation", "autonomy/tests"):
    path = os.path.join(ROOT, *sub.split("/"))
    if path not in sys.path:
        sys.path.insert(0, path)

import base_rest  # noqa: E402
import hybrid_pick_place as hpp  # noqa: E402
import test_host_boundary as host  # noqa: E402  (the safe attach compiled out of its file, on the fake bus)
import test_base_rest as recorded  # noqa: E402  (the recorded at-rest reads)
import test_capability_pulse as relay_tests  # noqa: E402  (the proven forward primitive and the relay under one fake clock)
import test_cycle_behavior_preserved as differential  # noqa: E402  (the scripted World)
import test_cycle_mission_mode as mission  # noqa: E402  (the mission harness)

# Every NON-ZERO value a read at rest was ever recorded with: a run fed these in turn never reads an exact zero.
DITHER = tuple(body for body, counts, _ in recorded.RECORDED_AT_REST if any(counts))
PULSE = (relay_tests.VX, 0.0, 0.0)
TWO_COUNTS = base_rest.body_velocity((2, 0, 0))


class TheStopConfirmations(unittest.TestCase):
    def test_the_proven_forward_primitive_and_the_relay_confirm_a_stop_that_reads_dither_and_stay_identical(self):
        readings = (PULSE, recorded.TRIP_READ)
        stopped, proven = relay_tests.proven_forward(0.2, readings)
        rig = relay_tests.Rig(relay_tests.ServiceCapability(), readings=readings)
        self.assertTrue(stopped, "the proven forward primitive did not confirm a stop whose last read is one count on two wheels")
        self.assertTrue(rig.pulse.move_base(relay_tests.VX, 0.2))
        self.assertEqual(rig.log, proven)
        self.assertEqual(2, sum(1 for entry in proven if entry[1] == "read"))

    def test_neither_confirms_a_base_still_reading_two_counts_or_the_pulse_speed(self):
        for reading in (TWO_COUNTS, PULSE):
            with self.subTest(reading=reading):
                readings = (reading,) * 6
                stopped, proven = relay_tests.proven_forward(0.2, readings)
                rig = relay_tests.Rig(relay_tests.ServiceCapability(), readings=readings)
                self.assertFalse(stopped)
                self.assertFalse(rig.pulse.move_base(relay_tests.VX, 0.2))
                self.assertEqual(rig.log, proven)
                self.assertEqual(6, sum(1 for entry in proven if entry[1] == "read"))

    def _reverse(self, base_reads) -> bool:
        world = differential.World(base_reads=base_reads)

        class Relay:
            last_refusal, pulses = None, []

            def __call__(self, vx_mps, duration_s):
                return True

        with mock.patch.dict(sys.modules, {**differential.synthetic_lerobot(world), "run_hybrid_pick_place": None}), \
                mock.patch.object(mission.current, "time", world.clock):
            move_base = mission.current.make_move_base(mission.LOCAL, "synthetic-unit", forward=Relay())
            return move_base(-mission.current.BASE_VX_MPS, mission.current.BASE_PULSE_S[0])

    def test_the_proven_reverse_primitive_confirms_a_stop_on_dither_and_never_on_motion(self):
        self.assertTrue(self._reverse(DITHER), "the proven reverse primitive did not confirm a stop that never read an exact zero")
        self.assertFalse(self._reverse((TWO_COUNTS,)))
        self.assertFalse(self._reverse((tuple(-v for v in PULSE),)))


class TheOtherSites(unittest.TestCase):
    def test_the_hybrid_state_machine_reads_through_the_same_rule(self):
        for body in DITHER:
            with self.subTest(body=body):
                self.assertTrue(hpp._base_is_stationary(dict(zip(("x_vel", "y_vel", "theta_vel"), body))))
        for body in (TWO_COUNTS, PULSE):
            with self.subTest(body=body):
                self.assertFalse(hpp._base_is_stationary(dict(zip(("x_vel", "y_vel", "theta_vel"), body))))

    def test_the_guard_and_the_observation_read_are_the_rule(self):
        import scan_world

        for body in DITHER + (TWO_COUNTS, PULSE):
            with self.subTest(body=body):
                read = dict(zip(("x.vel", "y.vel", "theta.vel"), body))
                self.assertEqual(base_rest.reads_at_rest(read), scan_world._reads_zero(read))


class TheHostAttach(unittest.TestCase):
    """The supervised host's attach check judges each wheel by its raw Present_Velocity register, where one count is 50.
    Run on the fake bus of test_host_boundary.py, it is this rule on every whole-count read: what it attaches is at rest
    and what it refuses is not, so an arm is no longer refused on the dither every other site already takes as rest."""

    def attaches(self, counts) -> bool:
        raw = tuple(int(count) * base_rest.VELOCITY_COUNT_STEPS_PER_S for count in counts)
        _, _, problems = host.attach_with_wheels(raw)
        return problems is None

    def test_its_one_count_is_the_rule_s(self):
        self.assertEqual(host.WRAPPER_CONSTANTS["WHEEL_REST_MAX_RAW"], base_rest.REST_MAX_COUNTS * base_rest.VELOCITY_COUNT_STEPS_PER_S)

    def test_it_attaches_on_every_read_recorded_at_rest(self):
        for _, counts, _ in recorded.RECORDED_AT_REST:
            with self.subTest(counts=counts):
                self.assertTrue(self.attaches(counts))
        self.assertTrue(self.attaches(tuple(round(count) for count in base_rest.wheel_counts(*recorded.TRIP_READ))))

    def test_it_agrees_with_the_rule_on_every_whole_count_read(self):
        for counts in itertools.product(range(-3, 4), repeat=3):
            with self.subTest(counts=counts):
                self.assertEqual(self.attaches(counts), base_rest.at_rest(*base_rest.body_velocity(counts)))


# A line that names a base velocity or a base read AND compares something with an exact zero or a 1e- epsilon, in any
# python file of the repository outside the tests. A line-level sweep is best-effort: it knows the forms these sites were
# written in (the stage-1 report's `abs(v) < 1e-6 for v in ... base_before ...` among them), not every form a new one could
# take, and it names each line it allows by its text.
NAMES_A_BASE_READ = re.compile(r"\bx[._]vel\b|\by[._]vel\b|theta[._]vel|\bbase\w*|velocit\w*")
EXACT_ZERO = re.compile(r"(?:==|!=)\s*-?0(?:\.0*)?(?:[eE]0)?\b|<=?\s*(?:1(?:\.0*)?[eE]-\d|0\.0*1\b|0(?:\.0*)?\b)")
# (file, the line's text) -> why it is allowed.
ALLOWED = {
    (os.path.join("bin", "laptop", "manipulation", "evaluate.py"),
     'assert robot_command["x.vel"] == 0.0 and robot_command["y.vel"] == 0.0 and robot_command["theta.vel"] == 0.0'):
        "asserts that a COMMAND built for the robot carries zero base velocity; nothing measured",
    (os.path.join("bin", "laptop", "manipulation", "run_single_cube_cycle.py"), "duration = base_pulse_s(need, rate, first=pulse == 0)"):
        "the pulse counter, not a base read",
    (os.path.join("autonomy", "motion", "capability_pulse.py"),
     "1. refuses anything but a forward pulse: vx <= 0 is the proven reverse primitive's, which restores the base and is"):
        "prose about the COMMANDED pulse direction, not a base read",
    (os.path.join("bin", "pi", "lekiwi_host_safe_attach.py"), "if abs(int(velocity[m])) > WHEEL_REST_MAX_RAW or int(goal_velocity[m]) != 0:"):
        "the supervised host's attach check: the present velocity is judged by one count per wheel (TheHostAttach above); the "
        "exact zero on this line is the GOAL velocity, a command register, which must read 0",
}


def _flagged(line: str) -> bool:
    return bool(NAMES_A_BASE_READ.search(line) and EXACT_ZERO.search(line))


class NoSiteComparesABaseReadWithExactZero(unittest.TestCase):
    def _sweep(self) -> list:
        found = []
        for top in ("autonomy", "bin", "packages"):
            for directory, names, files in os.walk(os.path.join(ROOT, top)):
                names[:] = [name for name in names if name not in ("node_modules", "__pycache__", "tests") and not name.startswith(".")]
                for name in files:
                    if not name.endswith(".py") or name.startswith("test_"):
                        continue
                    path = os.path.join(directory, name)
                    relative = os.path.relpath(path, ROOT)
                    with open(path, encoding="utf-8") as handle:
                        for number, line in enumerate(handle, 1):
                            if _flagged(line):
                                found.append((relative, line.strip(), number))
        return found

    def test_the_sweep(self):
        found = [f"{relative}:{number}: {text[:160]}" for relative, text, number in self._sweep() if (relative, text) not in ALLOWED]
        self.assertEqual([], found, "a base velocity read is compared with an exact zero: judge it with base_rest.reads_at_rest")

    def test_every_allowed_line_is_still_there(self):
        present = {(relative, text) for relative, text, _ in self._sweep()}
        self.assertEqual(set(), set(ALLOWED) - present, "an allowed line is gone: remove its entry")

    def test_the_sweep_is_not_vacuous(self):
        for line in ('if obs["x.vel"] == 0.0 and obs["y.vel"] == 0.0:', 'return abs(base["x_vel"]) < 1e-6',
                     '"stationary": all(float(base[k]) == 0.0 for k in BASE_VELOCITY_KEYS)', "if int(velocity[m]) != 0 or int(goal_velocity[m]) != 0:",
                     "ok = all(abs(v) < 1e-6 for v in list(base_before.values()) + list(base_after.values()))",
                     "ok = all(abs(v) < 1E-6 for v in base_before.values())", "ok = all(abs(v) < 1.0e-6 for v in base_after.values())",
                     "ok = all(abs(v) <= 0 for v in base_before.values())", "if base[\"x.vel\"] == -0.0:"):
            with self.subTest(line=line):
                self.assertTrue(_flagged(line))


class AWholeRunThatNeverReadsAnExactZero(mission.MissionCase):
    """The mission run of test_cycle_mission_mode (A already placed, B carried with a base advance and the return), once as
    it always ran and once with EVERY host read one of the recorded non-zero dither values, in turn."""

    def test_the_dithered_run_is_the_same_run(self):
        _, clean = self.mission(mission.placed_first_world(), mission.second_execution)
        _, dithered = self.mission(mission.placed_first_world(base_reads=DITHER), mission.second_execution)
        self.assertEnded(clean, 0)
        self.assertEnded(dithered, 0)
        for key in ("states", "sent", "base_pulses", "captures"):
            self.assertEqual(clean[key], dithered[key], key)
        self.assertIn("BASE_RETURNED", dithered["states"])

    def test_a_run_whose_base_reads_two_counts_never_moves_the_arm(self):
        _, run = self.mission(mission.placed_first_world(base_reads=(TWO_COUNTS,)), mission.second_execution)
        self.assertNotEqual(("returned", 0), run["ending"])
        self.assertEqual([], [action for action in run["sent"] if any(key.startswith("arm_") for key in action)], "an arm command was sent")


class TheEventsStateTheSameVerdictOnDither(unittest.TestCase):
    """The event stream replays the executor's own guard over the reads it took (run_single_cube_cycle._close_base_group):
    a whole cycle whose every read is recorded dither states every base_zero evaluation as passed, with all of its reads
    counted as at rest."""

    def setUp(self):
        import test_cycle_events as events

        self.events = events
        self.directory = events.fixtures.use_synthetic_runtime(self)
        events.cycle.use_runtime_paths(events.cycle.cli.runtime_paths({}))

    def test_every_base_zero_evaluation_passes_and_counts_every_read_as_at_rest(self):
        scenario = {**differential.WORLD_SCENARIOS["a full base-advance placement"], "base_reads": DITHER}
        with mock.patch.dict(differential.WORLD_SCENARIOS, {"recorded dither on every read": scenario}):
            written = self.events.EventRun(self, scenario="recorded dither on every read")
        evaluations = written.predicates("base_zero")
        self.assertGreater(len(evaluations), 5)
        for record in evaluations:
            reads = [entry["observation_ids"] for entry in record["payload"]["evidence"] if entry["scalar_name"] == "base_velocity_reads"][0]
            self.assertIs(True, record["payload"]["value"], record["payload"])
            self.assertEqual(float(len(reads)), record["payload"]["measured"], "a dither read was not counted as at rest")


class TheApproachViewOnDither(mission.TheApproachView):
    """TheApproachView's capture test, every read dither: the stamped capture is stationary and resolves, as with zero."""

    def setUp(self):
        super().setUp()
        dithered = lambda **script: mission.MissionWorld(**{"floor": [{"pad_m": mission.A_PLACED, "contact_px": [700.0, 330.0]},  # noqa: E731
                                                                     {"pad_m": mission.B_START, "contact_px": mission.CONTACT}],
                                                           "carried": 1, "base_reads": DITHER, **script})
        patcher = mock.patch.object(mission, "placed_first_world", dithered)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_legacy_cycle_and_a_run_without_the_capability_take_no_extra_capture(self):
        self.skipTest("counted on the zero-read scene in test_cycle_mission_mode; only the capture test is re-run on dither")


if __name__ == "__main__":
    unittest.main()
