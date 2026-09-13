#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test_teleop.py: bin/laptop/teleop.py's shutdown and usage paths against
# stub lerobot modules. Opens no device, needs no lerobot; run by tests/test-python.sh.
import contextlib
import importlib.util
import io
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TELEOP = os.path.join(ROOT, "bin", "laptop", "teleop.py")
ARGS = ["--remote-ip", "203.0.113.5", "--robot-id", "kiwi", "--leader-port", "/dev/serial/by-id/test-leader", "--leader-id", "leader", "--no-rerun"]
CONNECTED = "teleop: connected; the follower mirrors the leader; Ctrl-C stops the client"
CLOSED = "teleop: client closed; the follower holds its pose under the host's torque until the host exits"
STUB_MODULES = ("lerobot", "lerobot.robots", "lerobot.robots.lekiwi", "lerobot.teleoperators", "lerobot.teleoperators.so_leader",
                "lerobot.utils", "lerobot.utils.robot_utils", "lerobot.utils.visualization_utils", "rerun")


class Plan:
    """What the fakes do in the current test; reset() before each."""

    @classmethod
    def reset(cls):
        cls.robot_connect_error = None
        cls.robot_disconnect_error = None
        cls.leader_connect_error = None
        cls.leader_disconnect_error = None
        cls.init_rerun_error = None
        cls.actions_before_interrupt = 3
        cls.log = []
        cls.robots = []
        cls.leaders = []


class FakeConfig:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    def __init__(self, config):
        self.config = config
        self.connected = False
        self.actions = []
        Plan.robots.append(self)
        Plan.log.append("robot.init")

    def connect(self):
        Plan.log.append("robot.connect")
        if Plan.robot_connect_error:
            raise Plan.robot_connect_error
        self.connected = True

    @property
    def is_connected(self):
        return self.connected

    def get_observation(self):
        return {"observation.state": [0.0]}

    def send_action(self, action):
        self.actions.append(dict(action))
        return action

    def disconnect(self):
        Plan.log.append("robot.disconnect")
        if Plan.robot_disconnect_error:
            raise Plan.robot_disconnect_error
        self.connected = False


class FakeLeader:
    def __init__(self, config):
        self.config = config
        self.connected = False
        self.reads = 0
        Plan.leaders.append(self)
        Plan.log.append("leader.init")

    def connect(self):
        Plan.log.append("leader.connect")
        if Plan.leader_connect_error:
            raise Plan.leader_connect_error
        self.connected = True

    @property
    def is_connected(self):
        return self.connected

    def get_action(self):
        self.reads += 1
        if self.reads > Plan.actions_before_interrupt:
            raise KeyboardInterrupt  # the operator's Ctrl-C, inside the loop
        return {"shoulder_pan.pos": 1.5, "gripper.pos": 2.5}

    def disconnect(self):
        Plan.log.append("leader.disconnect")
        if Plan.leader_disconnect_error:
            raise Plan.leader_disconnect_error
        self.connected = False


def fake_init_rerun(session_name):
    Plan.log.append("init_rerun")
    if Plan.init_rerun_error:
        raise Plan.init_rerun_error


def install_stubs():
    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m
    module("lerobot", __version__="0.0-stub", __file__="<stub>", __path__=[])
    module("lerobot.robots", __path__=[])
    module("lerobot.robots.lekiwi", LeKiwiClient=FakeClient, LeKiwiClientConfig=FakeConfig)
    module("lerobot.teleoperators", __path__=[])
    module("lerobot.teleoperators.so_leader", SO101Leader=FakeLeader, SO101LeaderConfig=FakeConfig)
    module("lerobot.utils", __path__=[])
    module("lerobot.utils.robot_utils", precise_sleep=lambda seconds: None)
    module("lerobot.utils.visualization_utils", init_rerun=fake_init_rerun,
           log_rerun_data=lambda **kw: Plan.log.append("log_rerun_data"))
    module("rerun")


def remove_stubs():
    for name in STUB_MODULES:
        sys.modules.pop(name, None)


def load_teleop():
    spec = importlib.util.spec_from_file_location("kc_teleop_under_test", TELEOP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TeleopTests(unittest.TestCase):
    def setUp(self):
        Plan.reset()
        install_stubs()
        self.teleop = load_teleop()

    def tearDown(self):
        remove_stubs()

    def run_main(self, argv):
        """-> (outcome, stdout, stderr); outcome is None on a plain return, else the SystemExit or exception raised."""
        out, err = io.StringIO(), io.StringIO()
        outcome = None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.teleop.main(argv)
            except (SystemExit, Exception) as e:  # noqa: BLE001 - the outcome is what is asserted on
                outcome = e
        return outcome, out.getvalue(), err.getvalue()

    def test_leader_connect_failure_closes_the_robot_only(self):
        Plan.leader_connect_error = RuntimeError("no leader on that port")
        outcome, out, _ = self.run_main(ARGS)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual(Plan.log.count("robot.disconnect"), 1)
        self.assertNotIn("leader.disconnect", Plan.log)
        self.assertNotIn(CONNECTED, out)

    def test_robot_connect_failure_closes_nothing(self):
        Plan.robot_connect_error = RuntimeError("no host at that address")
        outcome, out, _ = self.run_main(ARGS)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertNotIn("leader.connect", Plan.log)
        self.assertNotIn("robot.disconnect", Plan.log)
        self.assertNotIn("leader.disconnect", Plan.log)
        self.assertNotIn(CLOSED, out)

    def test_leader_disconnect_failure_still_closes_the_robot_and_exits_nonzero(self):
        Plan.leader_disconnect_error = RuntimeError("serial port busy")
        outcome, out, err = self.run_main(ARGS)
        self.assertIsInstance(outcome, SystemExit)
        self.assertNotIn(outcome.code, (0, None))
        self.assertEqual(Plan.log.count("leader.disconnect"), 1)
        self.assertEqual(Plan.log.count("robot.disconnect"), 1)
        self.assertIn("teleop: leader disconnect failed: serial port busy", err)
        self.assertNotIn(CLOSED, out)

    def test_ctrl_c_closes_both_once_and_every_action_carries_an_idle_base(self):
        Plan.actions_before_interrupt = 3
        outcome, out, _ = self.run_main(ARGS)
        self.assertIsNone(outcome)
        self.assertEqual(Plan.log.count("robot.disconnect"), 1)
        self.assertEqual(Plan.log.count("leader.disconnect"), 1)
        self.assertLess(Plan.log.index("leader.disconnect"), Plan.log.index("robot.disconnect"))
        actions = Plan.robots[0].actions
        self.assertEqual(len(actions), 3)
        for a in actions:
            self.assertEqual((a["x.vel"], a["y.vel"], a["theta.vel"]), (0.0, 0.0, 0.0))
            self.assertIn("arm_shoulder_pan.pos", a)
            self.assertIn("arm_gripper.pos", a)
            self.assertTrue(all(k.startswith("arm_") for k in a if k not in ("x.vel", "y.vel", "theta.vel")))
        self.assertIn(CONNECTED, out)
        self.assertIn(CLOSED, out)

    def test_fps_out_of_range_is_a_usage_error_before_any_connect(self):
        # lerobot is poisoned: reaching its import would end in a different SystemExit than 2.
        remove_stubs()
        sys.modules["lerobot"] = None
        for fps in ("0", "-5", "1001", "abc"):
            with self.subTest(fps=fps):
                Plan.reset()
                outcome, _, err = self.run_main(ARGS + ["--fps", fps])
                self.assertIsInstance(outcome, SystemExit)
                self.assertEqual(outcome.code, 2)
                self.assertIn("--fps", err)
                self.assertEqual(Plan.log, [])
                self.assertEqual(Plan.robots, [])

    def test_fps_in_range_is_accepted(self):
        outcome, _, _ = self.run_main(ARGS + ["--fps", "1000"])
        self.assertIsNone(outcome)
        self.assertEqual(len(Plan.robots[0].actions), 3)

    def test_check_imports_with_the_stubs_exits_zero(self):
        outcome, out, _ = self.run_main(["--check-imports"])
        self.assertIsInstance(outcome, SystemExit)
        self.assertEqual(outcome.code, 0)
        self.assertTrue(out.startswith("teleop: imports ok"))
        self.assertEqual(Plan.log, [])

    def test_viewer_failure_after_both_connects_closes_both(self):
        Plan.init_rerun_error = RuntimeError("no display")
        outcome, _, _ = self.run_main([a for a in ARGS if a != "--no-rerun"])
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual(Plan.log.count("init_rerun"), 1)
        self.assertEqual(Plan.log.count("leader.disconnect"), 1)
        self.assertEqual(Plan.log.count("robot.disconnect"), 1)


if __name__ == "__main__":
    unittest.main()
