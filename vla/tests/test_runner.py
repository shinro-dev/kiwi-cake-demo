# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import contextlib
import time
import unittest

import numpy as np

from vla import runner as r

ENV = {"joints": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"],
       "joint_min_deg": [-46, -82, -69, -46, -99.75, 63], "joint_max_deg": [20, 97, 90, 78, -93.3, 82.5],
       "floor_tool_z_m": -0.1345, "wrist_roll_deg": -96.6, "start_pose_deg": [-9, -79.5, 34.5, 67.3, -96.6, 79.6],
       "model_sha256": "a" * 64}
RT = {"latch": 66.8, "open": 80.193, "retention_min": 0.5, "fk": None}


class FakePolicy:
    sha256 = "a" * 64

    def chunk(self, frame, state):
        return np.array([[*state[:5], 79.0]] * 5)

    def warm_up(self):
        return 7


def wait_state(run, want, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and run.status()["state"] != want:
        time.sleep(0.01)
    return run.status()["state"]


class RunnerTest(unittest.TestCase):
    def test_starts_in_standby_and_swap_loads_to_ready(self):
        run = r.Runner(FakePolicy, None, ENV, RT, runtime_idle=lambda: (True, ""))
        self.assertEqual(run.status()["state"], "standby")
        self.assertEqual(run.run(), {"accepted": False, "reason": "not_ready"})
        self.assertEqual(run.swap(), {"accepted": True})
        self.assertEqual(wait_state(run, "ready"), "ready")
        self.assertEqual(run.status()["warm_inference_ms"], 7)

    def test_a_checkpoint_that_is_not_the_envelopes_is_an_error(self):
        class Other(FakePolicy):
            sha256 = "b" * 64
        run = r.Runner(Other, None, ENV, RT, runtime_idle=lambda: (True, ""))
        run.swap()
        self.assertEqual(wait_state(run, "error"), "error")
        self.assertIn("vla_model_sha_mismatch", run.status()["error"])

    def test_run_is_refused_while_the_runtime_is_busy(self):
        run = r.Runner(FakePolicy, None, ENV, RT, runtime_idle=lambda: (False, "a mission run is active"))
        run.swap()
        wait_state(run, "ready")
        self.assertEqual(run.run(), {"accepted": False, "reason": "vla_runtime_busy", "detail": "a mission run is active"})

    def test_unswap_returns_to_standby_and_is_refused_while_running(self):
        gate_open = {"go": False}

        @contextlib.contextmanager
        def io_factory(policy, emit, stopped, set_preview):
            if isinstance(policy, r._NoPolicy):  # the swap's pose move: no robot in this test
                raise RuntimeError("no robot in this test")
            while not gate_open["go"] and not stopped():
                time.sleep(0.01)
            raise RuntimeError("no robot in this test")
            yield  # pragma: no cover

        run = r.Runner(FakePolicy, io_factory, ENV, RT, runtime_idle=lambda: (True, ""))
        run.swap()
        wait_state(run, "ready")
        self.assertEqual(run.run(), {"accepted": True})
        self.assertEqual(run.unswap(), {"accepted": False, "reason": "running"})
        gate_open["go"] = True
        self.assertEqual(wait_state(run, "ready"), "ready")
        self.assertEqual(run.status()["outcome"]["code"], "vla_runner_failed")
        self.assertEqual(run.unswap(), {"accepted": True})
        self.assertEqual(wait_state(run, "standby"), "standby")


FREE = {"active_run": None, "owner": {"kind": "free", "run_id": None, "lease": None}}


class FakeRuntime:
    def __init__(self, snapshot, mission):
        self._snapshot, self._mission = snapshot, mission

    def snapshot(self):
        if isinstance(self._snapshot, Exception):
            raise self._snapshot
        return self._snapshot

    def mission_state(self):
        return self._mission


class RuntimeIdleTest(unittest.TestCase):
    TERMINAL = ("COMPLETE", "STOPPED", "FAILED")

    def check(self, snapshot, mission):
        return r._runtime_idle_check("unused.sock", client_factory=lambda path: FakeRuntime(snapshot, mission), terminal=self.TERMINAL)()

    def test_an_active_run_is_busy(self):
        self.assertEqual(self.check({"active_run": {"run_id": "x"}}, {"state": "COMPLETE"}), (False, "a mission run is active"))

    def test_a_running_mission_is_busy(self):
        idle, detail = self.check(FREE, {"state": "RUNNING"})
        self.assertFalse(idle)
        self.assertIn("RUNNING", detail)

    def test_a_complete_mission_and_no_run_is_idle(self):
        self.assertEqual(self.check(FREE, {"state": "COMPLETE"}), (True, ""))

    def test_no_mission_recorded_is_idle(self):
        self.assertEqual(self.check(FREE, {"ok": False, "reason_code": "no_mission_recorded"}), (True, ""))

    def test_a_refused_or_empty_answer_is_busy(self):
        for snapshot, mission in (({"ok": False, "reason_code": "internal_error"}, {"state": "COMPLETE"}),
                                  (FREE, {"ok": False, "reason_code": "internal_error"}),
                                  (None, None), (FREE, None), ({"owner": {"kind": "free"}}, {"state": "COMPLETE"})):
            with self.subTest(snapshot=snapshot, mission=mission):
                self.assertFalse(self.check(snapshot, mission)[0])

    def test_an_owner_that_is_not_free_is_busy(self):
        for kind in ("unresolved", "external_detected", "manual", "autonomous"):
            with self.subTest(kind=kind):
                idle, detail = self.check({"active_run": None, "owner": {"kind": kind}}, {"state": "COMPLETE"})
                self.assertFalse(idle)
                self.assertIn(kind, detail)

    def test_an_unreadable_runtime_is_busy(self):
        idle, detail = self.check(ConnectionError("no socket"), {})
        self.assertFalse(idle)
        self.assertIn("could not be read", detail)


class Clock:
    def __init__(self):
        self.t = 0.0


def endless_io_factory(policy_box, still_allowed=None):
    """An Io whose policy never asks to close: the episode runs until it is stopped (or refused)."""
    from vla.episode import Io

    @contextlib.contextmanager
    def factory(policy, emit, stopped, set_preview):
        policy_box["policy"] = policy
        pose = policy_box.setdefault("pose", [-9, -79.5, 34.5, 67.3, -96.6, 79.6])

        def send(cmd):
            pose[:] = [cmd[k] for k in ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos", "arm_wrist_flex.pos",
                                        "arm_wrist_roll.pos", "arm_gripper.pos")]

        def fk(j):
            m = np.eye(4)
            m[2, 3] = 0.0
            return m
        yield Io(read_state=lambda: list(pose), capture=lambda: np.zeros((720, 1280, 3), np.uint8), send=send,
                 infer=policy.chunk, fk=fk, retention=lambda a, b: 0.9, emit=emit, stopped=stopped)
    return factory


RT_FK = dict(RT, fk=lambda j: np.eye(4))


class RunnerSafetyTest(unittest.TestCase):
    def test_the_runtime_is_asked_again_at_every_chunk_and_a_busy_answer_ends_the_run(self):
        answers = iter([(True, ""), (True, ""), (True, ""), (False, "a mission run is active")] + [(False, "x")] * 100)
        box = {}
        run = r.Runner(FakePolicy, endless_io_factory(box), ENV, RT_FK, runtime_idle=lambda: next(answers))
        run.swap()
        wait_state(run, "ready")
        self.assertEqual(run.run(), {"accepted": True})
        for _ in range(100):  # the console polls while the episode runs
            if run.status()["state"] == "ready":
                break
            time.sleep(0.05)
        self.assertEqual(run.status()["outcome"]["code"], "vla_runtime_busy")

    def test_no_status_poll_for_the_dead_man_time_stops_the_run(self):
        box = {}
        run = r.Runner(FakePolicy, endless_io_factory(box), ENV, RT_FK, runtime_idle=lambda: (True, ""), deadman_s=0.4)
        run.swap()
        wait_state(run, "ready")
        self.assertEqual(run.run(), {"accepted": True})
        time.sleep(2.0)  # nobody asks for the status: the forward or the console is gone
        status = run.status()
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["outcome"]["result"], "stopped")
        self.assertIn("deadman", [e["phase"] for e in status["events"]])

    def test_a_run_is_refused_while_the_last_inference_is_still_computing(self):
        import threading
        release = threading.Event()

        class SlowPolicy(FakePolicy):
            def chunk(self, frame, state):
                release.wait(10)
                return super().chunk(frame, state)
        box = {}
        run = r.Runner(SlowPolicy, endless_io_factory(box), ENV, RT_FK, runtime_idle=lambda: (True, ""))
        run.swap()
        wait_state(run, "ready")
        self.assertEqual(run.run(), {"accepted": True})
        time.sleep(1.6)  # past the start move, into the first inference
        run.stop()
        self.assertEqual(wait_state(run, "ready"), "ready")
        self.assertEqual(run.run(), {"accepted": False, "reason": "vla_still_thinking"})
        release.set()
        time.sleep(0.3)
        self.assertEqual(run.run(), {"accepted": True})
        run.stop()
        wait_state(run, "ready")


class RunnerPoseTest(unittest.TestCase):
    def wait_not(self, run, state, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and run.status()["state"] == state:
            time.sleep(0.02)
        return run.status()["state"]

    def test_the_mission_pose_is_reached_from_standby_and_refused_while_the_runtime_is_busy(self):
        box = {}
        run = r.Runner(FakePolicy, endless_io_factory(box), ENV, RT_FK, runtime_idle=lambda: (True, ""))
        self.assertEqual(run.to_mission_pose(), {"accepted": True})
        self.assertEqual(self.wait_not(run, "moving"), "standby")
        self.assertTrue(all(abs(a - b) < 0.01 for a, b in zip(box["pose"][:4], r.MISSION_START_POSE_DEG)))
        busy = r.Runner(FakePolicy, endless_io_factory({}), ENV, RT_FK, runtime_idle=lambda: (False, "a mission run is active"))
        self.assertEqual(busy.to_mission_pose(), {"accepted": False, "reason": "vla_runtime_busy", "detail": "a mission run is active"})

    def test_swap_loads_then_brings_the_arm_to_the_vla_start_pose(self):
        box = {"pose": list(r.MISSION_START_POSE_DEG) + [-96.6, 80.0]}
        run = r.Runner(FakePolicy, endless_io_factory(box), ENV, RT_FK, runtime_idle=lambda: (True, ""))
        run.swap()
        self.assertEqual(wait_state(run, "ready", timeout=5.0), "ready")
        self.assertTrue(all(abs(a - b) < 0.01 for a, b in zip(box["pose"][:4], ENV["start_pose_deg"][:4])))
        self.assertIn("at_pose", [e["phase"] for e in run.status()["events"]])

    def test_swap_back_returns_the_arm_to_the_mission_start(self):
        box = {}
        run = r.Runner(FakePolicy, endless_io_factory(box), ENV, RT_FK, runtime_idle=lambda: (True, ""))
        run.swap()
        wait_state(run, "ready", timeout=5.0)
        self.assertEqual(run.unswap(), {"accepted": True})
        self.assertEqual(wait_state(run, "standby", timeout=5.0), "standby")
        self.assertTrue(all(abs(a - b) < 0.01 for a, b in zip(box["pose"][:4], r.MISSION_START_POSE_DEG)))


if __name__ == "__main__":
    unittest.main()


class RuntimeLiftTest(unittest.TestCase):
    """The grip check's lifts are the runtime's own step (run_single_cube_cycle.cartesian_step_pose): solved from the
    measured joints, pan held, accepted only by the runtime's gate."""

    def test_the_lift_asks_the_runtime_for_one_straight_up_step_from_the_measured_joints(self):
        calls = []

        class Pose:
            joints = {"shoulder_pan": -10.0, "shoulder_lift": 12.0, "elbow_flex": 40.0, "wrist_flex": 25.0}

        class Cycle:
            @staticmethod
            def cartesian_step_pose(measured, forward_m, dz_m, holding):
                calls.append((dict(measured), forward_m, dz_m, holding))
                return Pose(), {"max_deviation_m": 0.0}

        lift = r._runtime_lift_step(Cycle)
        self.assertEqual(lift([-10.0, 15.0, 41.0, 23.0], 0.028), [-10.0, 12.0, 40.0, 25.0])
        self.assertEqual(calls, [({"shoulder_pan": -10.0, "shoulder_lift": 15.0, "elbow_flex": 41.0, "wrist_flex": 23.0}, 0.0, 0.028, True)])

    def test_the_held_or_empty_rule_is_the_runtimes_own_predicate(self):
        calls = []

        class Cycle:
            @staticmethod
            def closed_on_something(gripper_after_latch):
                calls.append(gripper_after_latch)
                return gripper_after_latch > 67.53

        rule = r._runtime_closed_on_something(Cycle)
        self.assertEqual([rule(67.633), rule(67.012)], [True, False])
        self.assertEqual(calls, [67.633, 67.012])

    def test_a_runtime_refusal_reaches_the_episode_with_its_reason(self):
        class Refused(Exception):
            pass

        class Cycle:
            @staticmethod
            def cartesian_step_pose(measured, forward_m, dz_m, holding):
                raise Refused("the gate refuses this step: too close to the singularity")

        with self.assertRaisesRegex(Exception, "singularity"):
            r._runtime_lift_step(Cycle)([0.0, 0.0, 0.0, 0.0], 0.015)


class MissionPoseTest(unittest.TestCase):
    """The mission start pose is the one the camera measures the pad from since 2026-10-09, reached past it then onto it."""

    def test_the_mission_pose_is_approached_from_below_then_settled(self):
        run = r.Runner(FakePolicy, None, ENV, RT, runtime_idle=lambda: (True, ""))
        calls = []
        run._go = lambda target, name: calls.append((tuple(target), name)) or {"result": "done"}
        self.assertEqual(r.MISSION_START_POSE_DEG, (-9.4, -79.341, 33.5, 67.3))
        self.assertEqual(run.to_mission_pose(), {"accepted": True})
        wait_state(run, "standby")
        time.sleep(0.05)
        self.assertEqual(calls, [((-9.4, -79.341, 30.0, 66.3), "mission"), ((-9.4, -79.341, 33.5, 67.3), "mission")])

    def test_a_stopped_approach_is_not_followed_by_the_settle(self):
        run = r.Runner(FakePolicy, None, ENV, RT, runtime_idle=lambda: (True, ""))
        calls = []
        run._go = lambda target, name: calls.append((tuple(target), name)) or {"result": "stopped"}
        run.to_mission_pose()
        wait_state(run, "standby")
        time.sleep(0.05)
        self.assertEqual(len(calls), 1)


class CarryTest(unittest.TestCase):
    """Mission 1's scripted carry is a runner op (operator, 2026-10-11), offered before the swap."""

    RT_HELD = {**RT, "held": lambda g: 67.53 < g <= 72.0}

    def carry_runner(self, idle=(True, ""), link_jaws=67.633, mission_id="mission-31e2813f6d21"):
        from vla.tests.test_carry import FakeLink
        link = FakeLink(arm=(-9.6, -79.4, 37.0, 67.2), jaws=link_jaws)

        @contextlib.contextmanager
        def link_factory():
            yield link

        clock = {"t": 0.0}

        def sleep(s):
            clock["t"] += s

        run = r.Runner(FakePolicy, None, ENV, self.RT_HELD, runtime_idle=lambda: idle, carry_link_factory=link_factory,
                       runtime_mission_id=lambda: mission_id, carry_sleep=sleep, carry_clock=lambda: clock["t"])
        return run, link

    def test_the_carry_runs_and_names_the_mission_it_finished(self):
        run, link = self.carry_runner()
        self.assertIsNone(run.status()["carry"])
        self.assertEqual(run.carry(), {"accepted": True})
        self.assertEqual(wait_state(run, "standby"), "standby")
        out = run.status()["carry"]
        self.assertEqual((out["result"], out["mission_id"]), ("done", "mission-31e2813f6d21"))
        self.assertTrue(link.sent)
        self.assertIn({"phase": "moving_to_pose", "pose": "pad"}, [{k: e.get(k) for k in ("phase", "pose")} for e in run.status()["events"]])

    def test_the_carry_is_refused_once_the_policy_is_swapped_in(self):
        run, link = self.carry_runner()
        run.swap()
        wait_state(run, "ready")
        self.assertEqual(run.carry(), {"accepted": False, "reason": "swapped"})
        self.assertEqual(link.sent, [])

    def test_the_carry_is_refused_while_the_runtime_is_busy(self):
        run, link = self.carry_runner(idle=(False, "the mission is WAITING_FOR_CAPABILITY"))
        self.assertEqual(run.carry(), {"accepted": False, "reason": "vla_runtime_busy", "detail": "the mission is WAITING_FOR_CAPABILITY"})
        self.assertEqual(link.sent, [])

    def test_open_jaws_end_the_carry_before_any_motion_and_say_why(self):
        run, link = self.carry_runner(link_jaws=79.8)
        self.assertEqual(run.carry(), {"accepted": True})
        wait_state(run, "standby")
        self.assertEqual(run.status()["carry"]["code"], "carry_nothing_held")
        self.assertEqual(link.sent, [])

    def test_the_mission_id_is_the_runtimes_own_and_none_when_unread(self):
        class Client:
            def __init__(self, answer):
                self.answer = answer

            def mission_state(self):
                if isinstance(self.answer, Exception):
                    raise self.answer
                return self.answer

        self.assertEqual(r._runtime_mission_id("sock", lambda p: Client({"mission_id": "mission-1", "state": "STOPPED"}))(), "mission-1")
        self.assertIsNone(r._runtime_mission_id("sock", lambda p: Client({"reason_code": "no_mission_recorded"}))())
        self.assertIsNone(r._runtime_mission_id("sock", lambda p: Client(TimeoutError()))())

    def test_the_swap_is_refused_while_the_arm_moves(self):
        run, link = self.carry_runner()
        with run._lock:
            run.state = "moving"  # a carry or a pose move holds the arm
        self.assertEqual(run.swap(), {"accepted": False, "reason": "moving"})


class RuntimeReadRetryTest(unittest.TestCase):
    """The runtime's snapshot takes over a second under a console page-load burst (measured 2026-10-11: 1.14 s, its
    owner() scans /proc), so one read that times out is retried once before the arm is called busy."""
    TERMINAL = ("COMPLETE", "STOPPED", "FAILED")

    class Flaky:
        def __init__(self, failures):
            self.failures, self.calls = failures, []

        def snapshot(self):
            self.calls.append("snapshot")
            if self.failures:
                self.failures -= 1
                raise TimeoutError("timed out")
            return {"active_run": None, "owner": {"kind": "free"}}

        def mission_state(self):
            self.calls.append("mission_state")
            return {"state": "STOPPED"}

    def test_one_timeout_is_retried_and_the_answer_is_the_second_reads(self):
        client = self.Flaky(failures=1)
        check = r._runtime_idle_check("unused.sock", client_factory=lambda path: client, terminal=self.TERMINAL)
        self.assertEqual(check(), (True, ""))
        self.assertEqual(client.calls, ["snapshot", "snapshot", "mission_state"])

    def test_two_timeouts_are_busy_and_say_so(self):
        client = self.Flaky(failures=2)
        check = r._runtime_idle_check("unused.sock", client_factory=lambda path: client, terminal=self.TERMINAL)
        idle, detail = check()
        self.assertFalse(idle)
        self.assertIn("TimeoutError", detail)
        self.assertEqual(client.calls, ["snapshot", "snapshot"])

    def test_the_read_timeout_and_the_console_forward_leave_room_for_the_retry(self):
        self.assertGreaterEqual(r.RUNTIME_READ_TIMEOUT_S, 4.0)
        self.assertEqual(r.RUNTIME_READ_ATTEMPTS, 2)
