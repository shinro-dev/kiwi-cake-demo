#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_host_boundary.py: bin/pi/host_boundary.py, the command ownership gate of the motor host.
# WHAT IS PROVED HERE: a command without the live lease never reaches the host loop and looks like an empty socket (so the
# base watchdog is not fed); a competing sender cannot displace the owner's newest command; kc.* keys never reach
# send_action; the lease lifecycle, and that expiry only rejects; lease traffic never touches the command path; the stamp;
# publisher drops versus subscriber gaps; and that nothing at all is installed unless KC_HOST_BOUNDARY=1.
# AND ONE LAYER FURTHER DOWN, against a fake bus: what an owned velocity-only command, a rejected command and a silent
# watchdog timeout write to the motors, and what it costs when the empty-arm guard the robot computer carries is gone.
# AND FROM THE OTHER END: the third owned patch reproduced on a DISPOSABLE COPY of the pinned lerobot revision in a
# temporary directory, so that the AttributeError a stock LeKiwiConfig raises in the verify-first attach, the three arm
# gains a patched install resolves, and the attach that READS those three registers and writes none of them can all be
# shown without going near the robot computer. That is reproduction of a recorded configuration, not hardware validation.
# HOW: fake sockets, and real pyzmq sockets on inproc:// only. No lerobot import, no network, no hardware. The stock host
# loop is reproduced here as stock_iteration(), statement for statement, from lerobot b4e2d0b6 lekiwi_host.py main(), and
# the deployed LeKiwi.send_action and stop_base the same way, as deployed_send_action() and BusBackedRobot.
# Run directly: python3 autonomy/tests/test_host_boundary.py
import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import uuid

import zmq

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOUNDARY_FILE = os.path.join(ROOT, "bin", "pi", "host_boundary.py")
WRAPPER_FILE = os.path.join(ROOT, "bin", "pi", "lekiwi_host_safe_attach.py")

_spec = importlib.util.spec_from_file_location("host_boundary", BOUNDARY_FILE)
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)

SESSION = 123456789012345
ACTION = {"arm_shoulder_pan.pos": 1.5, "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}
# A base move as autonomy/motion/lerobot_transport.py sends it: the three velocities and not one ".pos" key.
BASE_ONLY = {"x.vel": 0.1, "y.vel": 0.0, "theta.vel": 0.0}
BUS_MOTOR_IDS = {"arm_shoulder_pan": 1, "arm_shoulder_lift": 2, "arm_elbow_flex": 3, "arm_wrist_flex": 4, "arm_wrist_roll": 5, "arm_gripper": 6,
                 "base_left_wheel": 7, "base_back_wheel": 8, "base_right_wheel": 9}  # lerobot b4e2d0b6, LeKiwi.__init__
ARM_MOTORS = [motor for motor in BUS_MOTOR_IDS if motor.startswith("arm")]
BASE_MOTORS = [motor for motor in BUS_MOTOR_IDS if motor.startswith("base")]
ARM_IDS = {BUS_MOTOR_IDS[motor] for motor in ARM_MOTORS}
MAX_RELATIVE_TARGET = 15.0  # --robot.max_relative_target=15.0, the value bin/pi/start-host.sh starts the host with
# Text str.isdigit() or str.isnumeric() accepts and int() rejects, built at runtime so this file stays plain ASCII:
# a superscript two, two superscripts, and a vulgar fraction.
DIGIT_BUT_NOT_A_NUMBER = (chr(0xB2), chr(0xB9) + chr(0xB2), chr(0xBD))
STOCK_OBSERVATION_KEYS = {"arm_shoulder_pan.pos", "x.vel", "y.vel", "theta.vel"}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def ns(self):
        return int(self.t * 1e9)

    def advance(self, seconds):
        self.t += seconds


class FakeSocket:
    """recv_multipart(NOBLOCK) over a list, like an empty or a filled zmq socket; send() can be told to fail."""

    def __init__(self):
        self.pending, self.sent, self.fail_sends, self.closed = [], [], set(), False
        self.send_calls = 0

    def push(self, payload):
        self.pending.append([payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")])

    def recv_multipart(self, flags=0):
        if not self.pending:
            raise zmq.Again()
        return self.pending.pop(0)

    def send(self, payload, flags=0):
        self.send_calls += 1
        if self.send_calls in self.fail_sends:
            raise zmq.Again()
        self.sent.append(payload)

    def close(self, linger=None):
        self.closed = True


class FakeRobot:
    """Records everything the host loop asks of it. The boundary itself must never ask for anything but an observation."""

    def __init__(self):
        self.actions, self.base_stops, self.observations = [], 0, 0

    def send_action(self, action):
        self.actions.append(action)
        return action

    def stop_base(self):
        self.base_stops += 1

    def get_observation(self):
        self.observations += 1
        return {"arm_shoulder_pan.pos": float(self.observations), "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}


class FakeBus:
    """What the deployed dispatch does to the servo bus, in order, as (method, register, values, keyword arguments).
    The values of every call are keyed by MOTOR ID, because an id is what reaches a servo and what a reader can hold
    against the robot: the arm is 1 to 6, the base is 7, 8 and 9. Nothing is opened, sent or moved here.

    sync_write raises StopIteration on an empty values dict, exactly as lerobot's FeetechMotorsBus.sync_write does: it
    takes "model = next(iter(models))" from the ids it was handed before it even looks the register up, so a write of
    no motors raises there, before a byte would go out.
    """

    def __init__(self):
        self.calls = []

    def _ids(self, values):
        return {BUS_MOTOR_IDS[motor]: value for motor, value in values.items()}

    def sync_write(self, register, values, **kwargs):
        ids = self._ids(values)
        if not ids:
            raise StopIteration("next(iter(models)) on a write of no motors")
        self.calls.append(("sync_write", register, ids, kwargs))

    def sync_read(self, register, motors, **kwargs):
        reading = dict.fromkeys(motors, 0.0)  # keyed by motor NAME, which is what send_action indexes it by
        self.calls.append(("sync_read", register, self._ids(reading), kwargs))
        return reading

    def write(self, register, motor, value, **kwargs):
        self.calls.append(("write", register, {BUS_MOTOR_IDS[motor]: value}, kwargs))

    def read(self, register, motor, **kwargs):
        self.calls.append(("read", register, {BUS_MOTOR_IDS[motor]: 0.0}, kwargs))
        return 0.0

    def writes(self):
        """Only the calls that could move a motor; a read of one is not among them."""
        return [call for call in self.calls if call[0] in ("sync_write", "write")]


def wheel_velocities(x_vel, y_vel, theta_vel):
    """Stands where LeKiwi._body_to_wheel_raw stands. The real one mixes the three body velocities into one raw value
    per wheel with numpy; that arithmetic belongs to lerobot and is not reproduced here, because nothing below asserts
    on the NUMBER a wheel is given. What the dispatch depends on is the shape: one entry per base motor, all three of
    them, whatever the command said."""
    return {"base_left_wheel": round(x_vel * 1000), "base_back_wheel": round(y_vel * 1000), "base_right_wheel": round(theta_vel * 1000)}


def capped_goal_positions(goal_present_pos, max_relative_target):
    """lerobot's ensure_safe_goal_position (robots/utils.py at b4e2d0b6), its float branch, the one a command line with
    --robot.max_relative_target=15.0 takes. It is here because the deployed send_action calls it on the way to the bus."""
    return {key: present + max(min(goal - present, max_relative_target), -max_relative_target) for key, (goal, present) in goal_present_pos.items()}


def deployed_send_action(bus, config, action, guard=True):
    """LeKiwi.send_action as the robot computer runs it: lerobot b4e2d0b6 statement for statement, plus the two local
    changes that touch this method, which are not the same kind of change. One is the clamp fix
    (present_pos[key.removesuffix(".pos")]), and that one IS lerobot's own later commit f66e512, written by hand into a
    checkout at b4e2d0b that predates it. The other is the empty-arm guard, which is in no lerobot commit at all and is
    written down only in this repository. Both are modifications of the installed checkout, which a git checkout, a
    pull or a reinstall takes away again in silence. guard=False is the stock line, kept here so that a test can show
    what its absence costs rather than describe it."""
    arm_goal_pos = {k: v for k, v in action.items() if k.endswith(".pos")}
    base_goal_vel = {k: v for k, v in action.items() if k.endswith(".vel")}
    base_wheel_goal_vel = wheel_velocities(base_goal_vel["x.vel"], base_goal_vel["y.vel"], base_goal_vel["theta.vel"])
    if config.max_relative_target is not None:
        present_pos = bus.sync_read("Present_Position", ARM_MOTORS)
        goal_present_pos = {key: (g_pos, present_pos[key.removesuffix(".pos")]) for key, g_pos in arm_goal_pos.items()}
        arm_goal_pos = capped_goal_positions(goal_present_pos, config.max_relative_target)
    arm_goal_pos_raw = {k.replace(".pos", ""): v for k, v in arm_goal_pos.items()}
    if arm_goal_pos_raw or not guard:
        bus.sync_write("Goal_Position", arm_goal_pos_raw)
    bus.sync_write("Goal_Velocity", base_wheel_goal_vel)
    return {**arm_goal_pos, **base_goal_vel}


class BusBackedRobot(FakeRobot):
    """FakeRobot, with send_action and stop_base going through the deployed dispatch onto a FakeBus, so a test can see
    what an accepted, a rejected and an absent command do to the MOTORS and not only to the host loop. The observation
    stays the FakeRobot one and reads no register: everything the bus recorded came from a command or the watchdog."""

    def __init__(self, guard=True, max_relative_target=MAX_RELATIVE_TARGET):
        super().__init__()
        self.bus = FakeBus()
        self.config = type("Config", (), {"max_relative_target": max_relative_target})()
        self.guard = guard

    def send_action(self, action):
        super().send_action(action)
        return deployed_send_action(self.bus, self.config, action, guard=self.guard)

    def stop_base(self):
        super().stop_base()
        self.bus.sync_write("Goal_Velocity", dict.fromkeys(BASE_MOTORS, 0), num_retry=5)


def stock_iteration(host, robot, state, clock, watchdog_timeout_ms=500):
    """One pass of the stock loop body (lerobot b4e2d0b6, lekiwi_host.py main()), with its clock made injectable."""
    try:
        msg = host.zmq_cmd_socket.recv_string(zmq.NOBLOCK)
        data = dict(json.loads(msg))
        robot.send_action(data)
        state["last_cmd_time"] = clock()
        state["watchdog_active"] = False
    except zmq.Again:
        state["no_command"] += 1
    except Exception as error:  # the stock loop logs and carries on
        state["errors"].append(error)
    if (clock() - state["last_cmd_time"] > watchdog_timeout_ms / 1000) and not state["watchdog_active"]:
        state["watchdog_active"] = True
        robot.stop_base()
    last_observation = robot.get_observation()
    try:
        host.zmq_observation_socket.send_string(json.dumps(last_observation), flags=zmq.NOBLOCK)
    except zmq.Again:
        pass
    return last_observation


def new_state(clock):
    return {"last_cmd_time": clock(), "watchdog_active": False, "no_command": 0, "errors": []}


def owned(lease, action=None, **extra):
    return {**(action or ACTION), hb.LEASE_KEY: float(lease.generation), hb.SESSION_KEY: float(lease.host_session), **extra}


class GateFixture(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.events = []
        self.lease = hb.Lease(SESSION, self.clock, lambda name, detail: self.events.append(name))
        self.counters, self.timing, self.socket = hb.Counters(), hb.CommandTiming(self.clock.ns), FakeSocket()
        self.gate = hb.CommandGate(self.socket, self.lease, self.counters, self.timing, zmq)

    def assert_empty(self):
        with self.assertRaises(zmq.Again):
            self.gate.recv_string(zmq.NOBLOCK)


class Ingestion(GateFixture):
    def test_an_unleased_command_is_never_returned_and_the_socket_looks_empty(self):
        self.socket.push(ACTION)
        self.socket.push(ACTION)
        self.assert_empty()
        self.assertEqual(self.counters.rejections, {"no_lease": 2, "wrong_generation": 0, "wrong_session": 0, "malformed": 0})
        self.assertEqual(self.socket.pending, [], "rejected messages are consumed, not left to be read again")
        self.assertEqual(self.timing.dispatched_total, 0)

    def test_metadata_without_a_live_lease_is_no_lease_even_when_it_would_match(self):
        self.socket.push({**ACTION, hb.LEASE_KEY: 1.0, hb.SESSION_KEY: float(SESSION)})
        self.assert_empty()
        self.assertEqual(self.counters.rejections["no_lease"], 1)

    def test_a_command_without_metadata_under_a_live_lease_is_rejected(self):
        self.lease.acquire("owner")
        self.socket.push(ACTION)
        self.assert_empty()
        self.assertEqual(self.counters.rejections["no_lease"], 1)

    def test_wrong_generation_and_wrong_session_are_rejected_and_counted_apart(self):
        self.lease.acquire("first")
        stale = owned(self.lease)
        self.lease.release(SESSION, 1)
        self.lease.acquire("second")
        self.socket.push(stale)
        self.socket.push({**owned(self.lease), hb.SESSION_KEY: float(SESSION + 1)})
        self.assert_empty()
        self.assertEqual((self.counters.rejections["wrong_generation"], self.counters.rejections["wrong_session"]), (1, 1))

    def test_malformed_messages_are_counted_never_raised(self):
        self.lease.acquire("owner")
        good = owned(self.lease)
        for bad in (b"not json", b"\xff\xfe", b"[1, 2]", b'"text"', b"{" * 5000, json.dumps({**good, hb.LEASE_KEY: True}).encode(), json.dumps({**good, hb.LEASE_KEY: "1"}).encode(),
                    json.dumps({**good, hb.LEASE_KEY: 1.5}).encode(), b'{"kc.lease": NaN, "kc.session": 1}', json.dumps({k: v for k, v in good.items() if k != hb.SESSION_KEY}).encode(),
                    json.dumps({**good, "pad": "x" * hb.MAX_COMMAND_BYTES}).encode()):
            self.socket.push(bad)
        self.socket.pending.append([json.dumps(good).encode(), b"second frame"])
        self.assert_empty()
        self.assertEqual(self.counters.rejections["malformed"], 12)
        self.assertEqual(self.counters.internal_errors, 0)

    def test_the_newest_valid_command_wins_and_a_competitor_never_displaces_it(self):
        self.lease.acquire("owner")
        first, second, third = (owned(self.lease, {**ACTION, "arm_shoulder_pan.pos": float(n)}) for n in (1, 2, 3))
        competitor = {**ACTION, "arm_shoulder_pan.pos": 99.0}
        for message in (first, competitor, second, competitor, third, competitor, {**competitor, hb.LEASE_KEY: 7.0, hb.SESSION_KEY: float(SESSION)}):
            self.socket.push(message)  # the competitor speaks LAST: with CONFLATE its message is what the stock host would run
        dispatched = json.loads(self.gate.recv_string(zmq.NOBLOCK))
        self.assertEqual(dispatched, {**ACTION, "arm_shoulder_pan.pos": 3.0})
        self.assertEqual((self.counters.superseded, self.counters.rejections["no_lease"], self.counters.rejections["wrong_generation"]), (2, 3, 1))
        self.assert_empty()

    def test_every_kc_key_is_stripped_before_dispatch_and_nothing_else_is(self):
        self.lease.acquire("owner")
        self.socket.push(owned(self.lease, **{"kc.anything": 5.0, "kcnot.meta": 2.0}))
        dispatched = json.loads(self.gate.recv_string(zmq.NOBLOCK))
        self.assertEqual(dispatched, {**ACTION, "kcnot.meta": 2.0})
        self.assertFalse([key for key in dispatched if key.startswith("kc.")])

    def test_integer_metadata_is_accepted_like_the_floats_the_stock_client_sends(self):
        self.lease.acquire("owner")
        self.socket.push({**ACTION, hb.LEASE_KEY: 1, hb.SESSION_KEY: SESSION})
        self.assertEqual(json.loads(self.gate.recv_string(zmq.NOBLOCK)), ACTION)
        self.assertEqual(float(hb.new_host_session()) % 1, 0.0)
        self.assertTrue(all(0 < hb.new_host_session() < 2 ** 48 for _ in range(200)))
        self.assertEqual(int(float(2 ** 48 - 1)), 2 ** 48 - 1, "a session survives the float the stock client makes of it")

    def test_a_valid_command_extends_the_lease_and_a_rejected_one_does_not(self):
        self.lease.acquire("owner", 10)
        self.clock.advance(9)
        self.socket.push(ACTION)
        self.assert_empty()
        self.assertAlmostEqual(self.lease.status()["expires_in_ms"], 1000.0)
        self.socket.push(owned(self.lease))
        self.gate.recv_string(zmq.NOBLOCK)
        self.assertAlmostEqual(self.lease.status()["expires_in_ms"], 10000.0)

    def test_a_fault_inside_the_gate_looks_like_an_empty_socket(self):
        self.lease.acquire("owner")
        self.socket.push(owned(self.lease))
        self.socket.recv_multipart = lambda flags=0: (_ for _ in ()).throw(zmq.ZMQError(zmq.ETERM))
        self.assert_empty()
        self.assertEqual(self.counters.internal_errors, 1)

    def test_a_flood_is_cut_off_per_call_and_the_owner_is_still_served(self):
        self.lease.acquire("owner")
        for _ in range(hb.MAX_DRAIN_PER_CALL):
            self.socket.push(ACTION)
        self.socket.push(owned(self.lease))
        self.assert_empty()
        self.assertEqual(self.counters.drain_cap_hits, 1)
        self.assertEqual(json.loads(self.gate.recv_string(zmq.NOBLOCK)), ACTION)

    def test_command_timing_is_per_lease_and_measured_between_dispatches(self):
        self.lease = hb.Lease(SESSION, self.clock, lambda name, detail: self.timing.lease_started() if name == "acquired" else None)
        self.gate = hb.CommandGate(self.socket, self.lease, self.counters, self.timing, zmq)
        self.lease.acquire("owner")
        for pause in (0.25, 0.1, 0.4):
            self.clock.advance(pause)
            self.socket.push(owned(self.lease))
            self.gate.recv_string(zmq.NOBLOCK)
        self.clock.advance(2.0)
        report = self.timing.report()
        self.assertEqual((report["dispatched_in_lease"], round(report["first_after_lease_ms"]), round(report["last_gap_ms"]), round(report["max_gap_ms_in_lease"]), round(report["since_last_ms"])),
                         (3, 250, 400, 400, 2000))
        self.lease.release(SESSION, 1)
        self.lease.acquire("next")
        report = self.timing.report()
        self.assertEqual((report["dispatched_total"], report["dispatched_in_lease"], report["max_gap_ms_in_lease"], report["last_gap_ms"]), (3, 0, None, None))


class StockLoopIsNotFed(GateFixture):
    """The same gate, driven by the reproduced stock loop: what the ROBOT and the WATCHDOG see."""

    def setUp(self):
        super().setUp()
        self.robot = FakeRobot()
        self.host = type("Host", (), {})()
        self.host.zmq_cmd_socket, self.host.zmq_observation_socket = self.gate, type("Out", (), {"send_string": lambda self, text, flags=0: None})()
        self.state = new_state(self.clock)

    def test_rejected_commands_cause_no_motor_write_no_watchdog_refresh_no_exception(self):
        started = self.state["last_cmd_time"]
        for _ in range(10):
            self.socket.push(ACTION)
            self.socket.push(b"garbage")
            self.clock.advance(0.1)
            stock_iteration(self.host, self.robot, self.state, self.clock)
        self.assertEqual(self.robot.actions, [], "a rejected command reached send_action")
        self.assertEqual(self.state["last_cmd_time"], started, "a rejected command refreshed the watchdog")
        self.assertEqual(self.state["errors"], [], "rejection must not travel as an exception")
        self.assertEqual(self.state["no_command"], 10)
        self.assertEqual(self.robot.base_stops, 1, "the stock watchdog still stops the base, once, exactly as with an empty socket")
        self.assertEqual(self.robot.observations, 10, "the host keeps running and keeps observing")

    def test_the_owner_is_dispatched_without_metadata_and_feeds_the_watchdog(self):
        self.lease.acquire("owner")
        for _ in range(10):
            self.socket.push(owned(self.lease))
            self.socket.push({**ACTION, "arm_shoulder_pan.pos": -50.0})  # a competitor, after the owner, every iteration
            self.clock.advance(0.1)
            stock_iteration(self.host, self.robot, self.state, self.clock)
        self.assertEqual(self.robot.actions, [ACTION] * 10)
        self.assertEqual(self.state["last_cmd_time"], self.clock())
        self.assertEqual(self.robot.base_stops, 0)

    def test_expiry_rejects_and_does_nothing_else(self):
        self.lease.acquire("owner", 5)
        command = owned(self.lease)
        self.socket.push(command)
        stock_iteration(self.host, self.robot, self.state, self.clock)
        self.clock.advance(5.0)
        for _ in range(3):
            self.socket.push(command)
            self.clock.advance(0.3)
            stock_iteration(self.host, self.robot, self.state, self.clock)
        self.assertEqual(self.robot.actions, [ACTION], "only the command sent before expiry was dispatched")
        self.assertEqual(self.counters.rejections["no_lease"], 3)
        self.assertEqual(self.robot.base_stops, 1, "the STOCK watchdog stopped the base; the boundary wrote nothing")
        status = self.lease.status()
        self.assertEqual((status["state"], status["generation"], status["owner_label"], status["expiries"]), ("expired", 1, None, 1), "expiry hands the robot to nobody")
        self.assertEqual(self.events, ["acquired", "expired"])


class DeployedDispatch(GateFixture):
    """The same gate and the same reproduced host loop, with the ROBOT reproduced too: LeKiwi.send_action and
    LeKiwi.stop_base as the robot computer runs them today, onto a fake bus. The class above stops at robot.send_action;
    these four carry each case one layer further, to the registers and the motor ids a command does or does not reach."""

    def setUp(self):
        super().setUp()
        self.robot = BusBackedRobot()
        self.host = type("Host", (), {})()
        self.host.zmq_cmd_socket, self.host.zmq_observation_socket = self.gate, type("Out", (), {"send_string": lambda self, text, flags=0: None})()
        self.state = new_state(self.clock)
        self.lease.acquire("runtime")

    def iterate(self, robot=None):
        return stock_iteration(self.host, robot or self.robot, self.state, self.clock)

    def test_a_velocity_only_command_writes_the_three_wheels_and_without_the_guard_never_reaches_them(self):
        self.socket.push(owned(self.lease, BASE_ONLY))
        self.iterate()
        self.assertEqual([(method, register) for method, register, _values, _kwargs in self.robot.bus.calls],
                         [("sync_read", "Present_Position"), ("sync_write", "Goal_Velocity")],
                         "the clamp reads the arm because the host runs with --robot.max_relative_target; the only WRITE is the base")
        self.assertEqual(len(self.robot.bus.writes()), 1)
        _method, register, values, kwargs = self.robot.bus.writes()[0]
        self.assertEqual((register, sorted(values), kwargs), ("Goal_Velocity", [7, 8, 9], {}), "every wheel, in one message")
        # The same command with the STOCK unguarded line: the bus raises before Goal_Velocity is written, the stock
        # loop catches it with its bare "except Exception", last_cmd_time is never refreshed, and 500 ms later the
        # watchdog stops the base. On the laptop that is a base that does not move, with no error anywhere.
        unguarded = BusBackedRobot(guard=False)
        with self.assertRaises(StopIteration):
            deployed_send_action(unguarded.bus, unguarded.config, dict(BASE_ONLY), guard=False)
        self.assertEqual(unguarded.bus.writes(), [], "the write of an empty arm raises, so Goal_Velocity is never reached")
        fed = self.state["last_cmd_time"]
        for _ in range(6):
            self.socket.push(owned(self.lease, BASE_ONLY))
            self.clock.advance(0.1)
            self.iterate(unguarded)
        self.assertEqual([type(error) for error in self.state["errors"]], [StopIteration] * 6, "the stock loop swallows it and carries on")
        self.assertEqual(self.state["last_cmd_time"], fed, "a command that raised never refreshes the watchdog")
        self.assertEqual(unguarded.bus.writes(), [("sync_write", "Goal_Velocity", {7: 0, 8: 0, 9: 0}, {"num_retry": 5})],
                         "the only thing that reached the base is the watchdog's stop: six base commands wrote nothing")

    def test_a_velocity_only_command_writes_no_arm_register_at_all(self):
        self.socket.push(owned(self.lease, BASE_ONLY))
        self.iterate()
        writes = self.robot.bus.writes()
        # What the loop below does NOT find is only worth something once this list is known to hold the write it should.
        # With the empty-arm guard mutated away the list is empty, because the arm write raises before the base one is
        # reached, and a test that only iterated it would pass while the command reached no register at all.
        self.assertEqual([(method, register) for method, register, _values, _kwargs in writes], [("sync_write", "Goal_Velocity")],
                         "the base command must have reached the wheels before its not touching the arm means anything")
        for method, register, values, _kwargs in writes:
            self.assertNotEqual(register, "Goal_Position", "a base move wrote an arm goal")
            self.assertFalse(ARM_IDS & set(values), (method, register, values))
        # And the same dispatch DOES write those ids when the action carries arm positions, so the lines above are a
        # fact about this command, not about a path that could never reach the arm.
        self.robot.bus.calls.clear()
        self.socket.push(owned(self.lease, {**ACTION, "arm_gripper.pos": 12.0}))
        self.clock.advance(0.1)
        self.iterate()
        self.assertEqual([(method, register) for method, register, _values, _kwargs in self.robot.bus.calls],
                         [("sync_read", "Present_Position"), ("sync_write", "Goal_Position"), ("sync_write", "Goal_Velocity")])
        self.assertEqual(sorted(self.robot.bus.writes()[0][2]), [1, 6], "the two joints that action carried, by motor id")

    def test_a_rejected_command_reaches_no_register_and_leaves_the_watchdog_where_it_was(self):
        started = self.state["last_cmd_time"]
        for _ in range(4):
            self.socket.push(ACTION)  # no metadata at all
            self.socket.push(owned(self.lease, ACTION, **{hb.SESSION_KEY: float(SESSION + 1)}))  # another host's session
            self.socket.push(b"{not json")
            self.clock.advance(0.1)
            self.iterate()
        self.assertEqual(self.robot.bus.calls, [], "a rejected command reached the servo bus")
        self.assertEqual(self.state["last_cmd_time"], started, "a rejected command refreshed the watchdog")
        self.assertEqual(self.robot.base_stops, 0, "0.4 s is still inside the 500 ms the stock watchdog allows")

    def test_a_silent_watchdog_timeout_stops_the_base_once_and_writes_nothing_else(self):
        for _ in range(8):
            self.clock.advance(0.1)
            self.iterate()
        self.assertEqual((self.state["no_command"], self.robot.base_stops), (8, 1), "the stock watchdog latches: it stops the base once")
        self.assertEqual(self.robot.bus.calls, [("sync_write", "Goal_Velocity", {7: 0, 8: 0, 9: 0}, {"num_retry": 5})],
                         "that stop is the whole of it: every wheel to zero in one message, with retries, and no arm register")


class LeaseLifecycle(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.lease = hb.Lease(SESSION, self.clock)
        self.control = hb.ControlService(FakeSocket(), self.lease, hb.Counters(), zmq)

    def test_acquire_reports_session_generation_and_the_default_ttl(self):
        reply = self.control.handle({"op": "acquire", "owner_label": "runtime", "request_id": "abc123"})
        self.assertEqual((reply["ok"], reply["host_session"], reply["generation"], reply["ttl_s"], reply["state"], reply["request_id"]), (True, str(SESSION), 1, 180.0, "held", "abc123"))
        self.assertEqual(hb.DEFAULT_TTL_S, 180.0)
        self.assertGreater(hb.DEFAULT_TTL_S, 90.0, "the TTL must outlast the longest quiet perception phase")
        self.assertRegex(reply["host_session"], r"^[A-Za-z0-9_.-]{1,64}$", "the session must fit the contract's source_stamp.session")

    def test_a_second_acquire_is_refused_while_the_lease_is_live(self):
        self.lease.acquire("first")
        reply = self.control.handle({"op": "acquire", "owner_label": "second"})
        self.assertEqual((reply["ok"], reply["reason"], reply["generation"], reply["owner_label"]), (False, "lease_held", 1, "first"))
        self.assertEqual(self.control.handle({"op": "acquire", "owner_label": "first"})["reason"], "lease_held", "a label is not an identity: the same label is refused too")

    def test_renew_extends_and_needs_the_session_and_the_generation(self):
        self.lease.acquire("owner", 100)
        self.clock.advance(60)
        self.assertEqual(self.control.handle({"op": "renew", "host_session": str(SESSION), "generation": 2})["reason"], "wrong_generation")
        self.assertEqual(self.control.handle({"op": "renew", "host_session": str(SESSION + 1), "generation": 1})["reason"], "wrong_session")
        self.assertEqual(self.control.handle({"op": "renew", "generation": 1})["reason"], "malformed_request")
        self.assertAlmostEqual(self.lease.status()["expires_in_ms"], 40000.0)
        reply = self.control.handle({"op": "renew", "host_session": str(SESSION), "generation": 1})
        self.assertTrue(reply["ok"])
        self.assertAlmostEqual(reply["expires_in_ms"], 100000.0)

    def test_release_frees_and_every_acquisition_bumps_the_generation(self):
        self.lease.acquire("first")
        self.assertEqual(self.control.handle({"op": "release", "host_session": str(SESSION), "generation": 9})["reason"], "wrong_generation")
        reply = self.control.handle({"op": "release", "host_session": SESSION, "generation": 1.0})
        self.assertEqual((reply["ok"], reply["state"], reply["generation"]), (True, "free", 1))
        self.assertEqual(self.control.handle({"op": "release", "host_session": str(SESSION), "generation": 1})["reason"], "no_lease")
        self.assertEqual(self.lease.acquire("second")["generation"], 2)
        self.assertEqual(self.lease.admit({hb.LEASE_KEY: 1.0, hb.SESSION_KEY: float(SESSION)}), "wrong_generation", "the first owner's commands are dead")
        self.assertIsNone(self.lease.admit({hb.LEASE_KEY: 2.0, hb.SESSION_KEY: float(SESSION)}))

    def test_expiry_transfers_nothing_and_a_new_acquisition_is_explicit(self):
        self.lease.acquire("owner", 30)
        self.clock.advance(29.9)
        self.assertEqual(self.lease.state(), "held")
        self.clock.advance(0.2)
        self.assertEqual(self.lease.state(), "expired")
        self.assertEqual(self.lease.admit({hb.LEASE_KEY: 1.0, hb.SESSION_KEY: float(SESSION)}), "no_lease")
        self.assertEqual(self.control.handle({"op": "renew", "host_session": str(SESSION), "generation": 1})["reason"], "lease_expired", "an expired lease does not come back by renewing")
        status = self.control.handle({"op": "status"})
        self.assertEqual((status["ok"], status["state"], status["generation"], status["owner_label"]), (True, "expired", 1, None))
        self.clock.advance(3600)
        self.assertEqual(self.lease.state(), "expired", "it stays expired until someone acquires, however long that takes")
        self.assertEqual(self.lease.acquire("next")["generation"], 2)

    def test_the_lease_survives_silence_of_everyone_until_release_or_expiry(self):
        self.lease.acquire("runtime", 180)
        self.clock.advance(179)  # no renew, no command, no status: neither the runtime nor the controller is there
        self.assertEqual(self.lease.state(), "held")
        self.assertEqual(self.lease.acquire("intruder")["reason"], "lease_held")

    def test_ttl_is_bounded_and_bad_requests_are_refused_with_the_status_attached(self):
        self.assertEqual(self.lease.acquire("owner", 10 ** 9)["ttl_s"], hb.MAX_TTL_S)
        self.lease.release(SESSION, 1)
        for bad in ({"op": "acquire"}, {"op": "acquire", "owner_label": "has space"}, {"op": "acquire", "owner_label": "x", "ttl_s": -1}, {"op": "acquire", "owner_label": "x", "ttl_s": True},
                    {"op": "acquire", "owner_label": "x", "ttl_s": float("nan")}, {"op": "acquire", "owner_label": "x", "request_id": "no/slash"}, {"op": "fly"}, [], "acquire"):
            reply = self.control.handle(bad)
            self.assertEqual((reply["ok"], reply["state"]), (False, "free"), bad)
        self.assertEqual(self.lease.generation, 1, "a refused request never bumps the generation")

    def test_a_session_that_is_digit_but_not_a_number_is_refused_and_never_raises(self):
        # str.isdigit() is true for a superscript two, and int() rejects it. Reaching int() through a control request
        # used to raise out of the handler and leave the REP socket unanswerable for the life of the host process.
        self.lease.acquire("owner")
        for text in (*DIGIT_BUT_NOT_A_NUMBER, "1.0", "0x1", " 1", "1 ", "", "9" * 17):
            for op in ("renew", "release"):
                reply = self.control.handle({"op": op, "host_session": text, "generation": 1})
                self.assertEqual((reply["ok"], reply["reason"]), (False, "malformed_request"), (op, ascii(text)))
        self.assertEqual(self.lease.state(), "held", "a malformed request changes nothing")
        self.assertIsNone(hb.numeric_field(DIGIT_BUT_NOT_A_NUMBER[0]))
        self.assertEqual((hb.numeric_field(str(SESSION)), hb.numeric_field(SESSION), hb.numeric_field(float(SESSION))), (SESSION, SESSION, SESSION))

    def test_a_handler_fault_still_becomes_a_reply_so_the_control_socket_is_never_wedged(self):
        def explode(request):
            raise ValueError("a fault inside the handler")

        socket = self.control._socket
        self.lease.acquire("owner", 100)
        self.control.handle = explode
        socket.push({"op": "status"})
        socket.push({"op": "status"})
        self.control.serve()
        replies = [json.loads(reply) for reply in socket.sent]
        self.assertEqual([(reply["ok"], reply["reason"]) for reply in replies], [(False, "internal_error")] * 2,
                         "a request that was received must be answered; a REP socket that owes a reply can never read again")
        self.assertEqual([(reply["state"], reply["host_session"], reply["generation"]) for reply in replies], [("held", str(SESSION), 1)] * 2,
                         "the fault reply carries the lease, so a holder does not read it as a restarted host")
        self.assertEqual(self.control._counters.internal_errors, 2)
        self.assertEqual(self.lease.state(), "held")

    def test_the_observation_path_keeps_publishing_the_tap_after_a_bad_control_request(self):
        context = zmq.Context()
        self.addCleanup(context.term)
        endpoint = f"inproc://control-{uuid.uuid4().hex}"
        boundary = hb.open_boundary(zmq, context, endpoint, f"inproc://tap-{uuid.uuid4().hex}", host_session=SESSION, log=lambda line: None)
        self.addCleanup(boundary.close)
        requester = context.socket(zmq.REQ)
        requester.setsockopt(zmq.LINGER, 0)
        requester.setsockopt(zmq.RCVTIMEO, 2000)
        self.addCleanup(requester.close)
        requester.connect(endpoint)
        requester.send_string(json.dumps({"op": "renew", "host_session": DIGIT_BUT_NOT_A_NUMBER[0], "generation": 1}))
        boundary.observe(lambda: {"arm_shoulder_pan.pos": 1.0})
        self.assertEqual(json.loads(requester.recv_string())["reason"], "malformed_request")
        requester.send_string(json.dumps({"op": "acquire", "owner_label": "runtime"}))
        for _ in range(3):
            boundary.observe(lambda: {"arm_shoulder_pan.pos": 1.0})
        self.assertEqual(json.loads(requester.recv_string())["generation"], 1, "the lease must still be acquirable")
        self.assertEqual(boundary.counters.internal_errors, 0)
        self.assertEqual(boundary.tap.sent_count, 4, "every observation still reaches the tap")

    def test_the_rep_socket_always_gets_its_reply_and_retries_one_that_failed(self):
        socket = self.control._socket
        socket.push(b"not json")
        socket.push({"op": "status"})
        socket.fail_sends = {1}
        self.control.serve()
        self.assertEqual(len(socket.sent), 0, "the failed reply is kept, and nothing is read before it went out")
        self.assertEqual(len(socket.pending), 1)
        self.control.serve()
        self.assertEqual([json.loads(reply)["ok"] for reply in socket.sent], [False, True])
        self.assertEqual(json.loads(socket.sent[0])["reason"], "malformed_request")


class FakeHost:
    """What the boundary needs of lerobot's LeKiwiHost: a context and a CONFLATING PULL command socket, bound."""

    def __init__(self, context, endpoint):
        self.zmq_context = context
        self.zmq_cmd_socket = context.socket(zmq.PULL)
        self.zmq_cmd_socket.setsockopt(zmq.CONFLATE, 1)
        self.zmq_cmd_socket.bind(endpoint)
        self.zmq_observation_socket = type("Out", (), {"send_string": lambda self, text, flags=0: None, "close": lambda self: None})()

    def disconnect(self):
        self.zmq_observation_socket.close()
        self.zmq_cmd_socket.close()


class Installed(unittest.TestCase):
    """install() on stand-in classes, with real pyzmq sockets on inproc:// endpoints."""

    def setUp(self):
        self.context = zmq.Context()
        tag = uuid.uuid4().hex
        self.cmd, self.control_endpoint, self.tap_endpoint = (f"inproc://{name}-{tag}" for name in ("cmd", "control", "tap"))
        self.host_cls = type("Host", (FakeHost,), {})
        self.robot_cls = type("Robot", (FakeRobot,), {})
        self.log = []
        self.boundary = hb.install(self.robot_cls, self.host_cls, {hb.ENV_FLAG: "1"}, control_endpoint=self.control_endpoint, tap_endpoint=self.tap_endpoint,
                                   context=self.context, log=self.log.append)
        self.host, self.robot = self.host_cls(self.context, self.cmd), self.robot_cls()
        self.state = new_state(lambda: 0.0)
        self.sockets = []
        self.addCleanup(self.shutdown)

    def shutdown(self):
        for socket in self.sockets:
            socket.close(0)
        self.host.disconnect()
        worker = threading.Thread(target=self.context.term, daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "context.term() hangs: the boundary left a socket open after the host's disconnect()")

    def socket(self, kind, endpoint):
        socket = self.context.socket(kind)
        socket.setsockopt(zmq.LINGER, 0)
        socket.connect(endpoint)
        self.sockets.append(socket)
        return socket

    def iterate(self):
        return stock_iteration(self.host, self.robot, self.state, lambda: 0.0)

    def ask(self, request_socket, payload):
        request_socket.send(json.dumps(payload).encode())
        self.iterate()  # control is served from the observation path of the loop
        self.assertTrue(request_socket.poll(2000))
        return json.loads(request_socket.recv())

    def test_the_stock_socket_is_replaced_on_the_same_endpoint_and_closing_closes_everything(self):
        self.assertIsInstance(self.host.zmq_cmd_socket, hb.CommandGate)
        self.assertIs(self.boundary.gate, self.host.zmq_cmd_socket)
        self.assertTrue(any("ingestion" in line for line in self.log))
        self.assertFalse(any(str(self.boundary.lease.host_session) in line for line in self.log), "the session is not a secret, and still has no business in a log")

    def test_owner_and_competitor_on_real_sockets(self):
        owner, competitor, control = self.socket(zmq.PUSH, self.cmd), self.socket(zmq.PUSH, self.cmd), self.socket(zmq.REQ, self.control_endpoint)
        competitor.send_string(json.dumps(ACTION))
        self.iterate()
        self.assertEqual(self.robot.actions, [], "no lease yet: nothing is dispatched")
        lease = self.ask(control, {"op": "acquire", "owner_label": "runtime"})
        self.assertTrue(lease["ok"])
        metadata = {hb.LEASE_KEY: float(lease["generation"]), hb.SESSION_KEY: float(int(lease["host_session"]))}
        for n in (1, 2, 3):
            # exactly what lekiwi_client.send_action does: every value through float(), then json.dumps
            owner.send_string(json.dumps({key: float(value) for key, value in {**ACTION, "arm_shoulder_pan.pos": n, **metadata}.items()}))
            competitor.send_string(json.dumps({**ACTION, "arm_shoulder_pan.pos": -float(n)}))
        competitor.send_string(json.dumps({**ACTION, "arm_shoulder_pan.pos": -99.0}))
        self.assertTrue(self.boundary.gate._socket.poll(2000))
        self.iterate()
        self.assertEqual(self.robot.actions, [{**ACTION, "arm_shoulder_pan.pos": 3.0}], "without CONFLATE all three owner commands were seen and the newest one won")
        self.assertEqual(self.boundary.counters.superseded, 2)
        self.assertEqual(self.boundary.counters.rejections["no_lease"], 5)
        self.iterate()
        self.assertEqual(len(self.robot.actions), 1)

    def test_lease_traffic_never_reaches_the_command_path(self):
        control = self.socket(zmq.REQ, self.control_endpoint)
        lease = self.ask(control, {"op": "acquire", "owner_label": "runtime"})
        for _ in range(20):
            self.assertTrue(self.ask(control, {"op": "renew", "host_session": lease["host_session"], "generation": lease["generation"]})["ok"])
        self.assertEqual(self.state["no_command"], 21, "every iteration took the stock loop's 'no command' branch")
        self.assertEqual((self.robot.actions, self.state["last_cmd_time"], self.boundary.timing.dispatched_total, self.boundary.counters.received), ([], 0.0, 0, 0))
        self.assertEqual(self.boundary.counters.control_requests, 21)
        self.assertTrue(self.ask(control, {"op": "release", "host_session": lease["host_session"], "generation": lease["generation"]})["ok"])

    def test_the_owners_last_command_is_honoured_before_its_release_in_the_same_iteration(self):
        owner, control = self.socket(zmq.PUSH, self.cmd), self.socket(zmq.REQ, self.control_endpoint)
        lease = self.ask(control, {"op": "acquire", "owner_label": "runtime"})
        owner.send_string(json.dumps({**ACTION, hb.LEASE_KEY: float(lease["generation"]), hb.SESSION_KEY: float(int(lease["host_session"]))}))
        self.assertTrue(self.boundary.gate._socket.poll(2000))
        self.assertTrue(self.ask(control, {"op": "release", "host_session": lease["host_session"], "generation": lease["generation"]})["ok"])
        self.assertEqual(self.robot.actions, [ACTION])

    def test_every_observation_is_stamped_and_seq_strictly_increases(self):
        observations = [self.iterate() for _ in range(50)]
        self.assertEqual([o[hb.SEQ_KEY] for o in observations], list(range(1, 51)))
        self.assertEqual({o[hb.SESSION_KEY] for o in observations}, {str(self.boundary.lease.host_session)})
        self.assertTrue(all(type(o[hb.SAMPLE_KEY]) is int and type(o[hb.SEQ_KEY]) is int for o in observations))
        self.assertEqual(sorted(o[hb.SAMPLE_KEY] for o in observations), [o[hb.SAMPLE_KEY] for o in observations], "the monotonic sample time never goes back")
        self.assertEqual(set(observations[0]) - {hb.SESSION_KEY, hb.SEQ_KEY, hb.SAMPLE_KEY}, STOCK_OBSERVATION_KEYS, "the stock reading is passed on whole")
        json.dumps(observations[0])  # the stock loop serializes it

    def test_the_tap_carries_the_same_sample_and_never_changes_what_the_controller_gets(self):
        without = self.iterate()
        subscriber = self.socket(zmq.SUB, self.tap_endpoint)
        subscriber.subscribe(b"")
        sample = None
        for _ in range(200):  # a subscription takes effect asynchronously
            observation = self.iterate()
            if subscriber.poll(10):
                sample = json.loads(subscriber.recv())
                break
        self.assertIsNotNone(sample, "no tap sample arrived")
        self.assertEqual(set(without), set(observation), "a subscriber changes nothing in the PUSH observation")
        self.assertEqual((sample["kc.tap"], sample["session"], sample["lease"]["state"], sample["rejections"]), (1, observation[hb.SESSION_KEY], "free", dict.fromkeys(hb.REJECTION_REASONS, 0)))
        self.assertLessEqual(sample["seq"], observation[hb.SEQ_KEY])
        self.assertEqual(set(sample["observation"]), STOCK_OBSERVATION_KEYS)
        for field in ("t_mono_ns", "read_ns", "wall_time_s", "commands", "ingestion", "publisher"):
            self.assertIn(field, sample)
        self.assertEqual(set(sample["commands"]), {"dispatched_total", "dispatched_in_lease", "last_gap_ms", "max_gap_ms_in_lease", "first_after_lease_ms", "since_last_ms"})

    def test_an_exception_of_the_stock_observation_passes_through_untouched(self):
        self.robot_cls.get_observation = lambda self: (_ for _ in ()).throw(ConnectionError("bus"))
        boundary = self.boundary
        with self.assertRaises(ConnectionError):
            boundary.observe(lambda: self.robot.get_observation())
        self.assertEqual(boundary.seq, 0)


class TapDropsAreNotSubscriberGaps(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.control, self.tap = FakeSocket(), FakeSocket()
        self.boundary = hb.HostBoundary(zmq, self.control, self.tap, host_session=SESSION, clock=self.clock, clock_ns=self.clock.ns, wall_clock=lambda: 1.7e9, log=lambda line: None)
        self.robot = FakeRobot()

    def test_a_failed_send_is_counted_and_the_controller_still_gets_every_stamped_observation(self):
        self.tap.fail_sends = {3, 4}
        observations = [self.boundary.observe(self.robot.get_observation) for _ in range(6)]
        self.assertEqual([o[hb.SEQ_KEY] for o in observations], [1, 2, 3, 4, 5, 6], "the PUSH path does not depend on the tap")
        published = [json.loads(payload) for payload in self.tap.sent]
        self.assertEqual([sample["seq"] for sample in published], [1, 2, 5, 6])
        self.assertEqual((self.boundary.tap.drop_count, self.boundary.tap.sent_count), (2, 4))
        self.assertEqual([sample["publisher"]["drop_count"] for sample in published], [0, 0, 2, 2], "each sample states the drops BEFORE it")
        self.assertEqual(self.boundary.counters.internal_errors, 0)

    def test_a_sample_that_cannot_be_serialized_is_a_drop_not_a_crash(self):
        self.boundary._wall_clock = lambda: object()
        observation = self.boundary.observe(self.robot.get_observation)
        self.assertEqual((observation[hb.SEQ_KEY], self.boundary.tap.drop_count, self.tap.sent), (1, 1, []))

    def test_only_scalars_of_the_observation_go_on_the_tap(self):
        self.boundary.observe(lambda: {"x.vel": 0.0, "front": bytearray(b"frame"), "nested": {"a": 1}})
        self.assertEqual(json.loads(self.tap.sent[0])["observation"], {"x.vel": 0.0})

    def test_a_reading_that_is_not_a_dict_is_returned_as_it_came(self):
        self.assertEqual(self.boundary.observe(lambda: ["odd"]), ["odd"])
        self.assertEqual((self.boundary.counters.internal_errors, self.boundary.seq), (1, 0))

    def test_rejections_and_lease_status_are_reported_on_the_tap(self):
        gate = hb.CommandGate(FakeSocket(), self.boundary.lease, self.boundary.counters, self.boundary.timing, zmq)
        gate._socket.push(ACTION)
        with self.assertRaises(zmq.Again):
            gate.recv_string(zmq.NOBLOCK)
        self.control.push({"op": "acquire", "owner_label": "runtime", "ttl_s": 60})
        self.boundary.observe(self.robot.get_observation)
        sample = json.loads(self.tap.sent[0])
        self.assertEqual(sample["rejections"]["no_lease"], 1)
        self.assertEqual((sample["lease"]["state"], sample["lease"]["generation"], sample["lease"]["owner_label"], sample["lease"]["expires_in_ms"]), ("held", 1, "runtime", 60000.0))
        self.assertEqual(sample["wall_time_s"], 1.7e9)


class OffUnlessAskedFor(unittest.TestCase):
    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"zmq.{name} was touched although the boundary is off")

    def test_with_the_variable_unset_nothing_is_installed(self):
        for environ in ({}, {hb.ENV_FLAG: "0"}, {hb.ENV_FLAG: ""}, {hb.ENV_FLAG: "true"}, {hb.ENV_FLAG: "11"}):
            host_cls, robot_cls = type("Host", (FakeHost,), {}), type("Robot", (FakeRobot,), {})
            before = (host_cls.__init__, robot_cls.get_observation, dict(vars(host_cls)), dict(vars(robot_cls)))
            self.assertIsNone(hb.install(robot_cls, host_cls, environ, zmq_module=self.Untouchable()))
            self.assertEqual((host_cls.__init__, robot_cls.get_observation, dict(vars(host_cls)), dict(vars(robot_cls))), before)
            self.assertFalse(hb.enabled(environ))

    def test_a_bad_port_refuses_before_anything_is_wrapped(self):
        for name in (hb.CONTROL_PORT_ENV, hb.TAP_PORT_ENV):
            for value in ("80", "70000", "5557;x", "-1", " 5557"):
                host_cls, robot_cls = type("Host", (FakeHost,), {}), type("Robot", (FakeRobot,), {})
                with self.assertRaises(ValueError):
                    hb.install(robot_cls, host_cls, {hb.ENV_FLAG: "1", name: value}, zmq_module=self.Untouchable())
                self.assertIs(host_cls.__init__, FakeHost.__init__)
        with self.assertRaises(ValueError):
            hb.install(FakeRobot, FakeHost, {hb.ENV_FLAG: "1", hb.CONTROL_PORT_ENV: "6000", hb.TAP_PORT_ENV: "6000"}, zmq_module=self.Untouchable())

    def test_a_busy_control_endpoint_refuses_before_anything_is_wrapped(self):
        context = zmq.Context()
        self.addCleanup(context.term)
        endpoint = f"inproc://busy-{uuid.uuid4().hex}"
        squatter = context.socket(zmq.REP)
        squatter.bind(endpoint)
        self.addCleanup(squatter.close, 0)
        host_cls, robot_cls = type("Host", (FakeHost,), {}), type("Robot", (FakeRobot,), {})
        with self.assertRaises(zmq.ZMQError):
            hb.install(robot_cls, host_cls, {hb.ENV_FLAG: "1"}, control_endpoint=endpoint, tap_endpoint=endpoint + "-tap", context=context, log=lambda line: None)
        self.assertIs(host_cls.__init__, FakeHost.__init__)
        self.assertIs(robot_cls.get_observation, FakeRobot.get_observation)

    def test_the_default_endpoints_are_loopback_only(self):
        bound = []

        class Recorder:
            LINGER, SNDHWM, REP, PUB = 1, 2, 3, 4

            class Context:
                @staticmethod
                def instance():
                    return Recorder.Context()

                def socket(self, kind):
                    return type("S", (), {"setsockopt": lambda s, *a: None, "bind": lambda s, endpoint: bound.append(endpoint), "close": lambda s, *a: None})()

        hb.install(type("Robot", (FakeRobot,), {}), type("Host", (FakeHost,), {}), {hb.ENV_FLAG: "1", hb.TAP_PORT_ENV: "6001"}, zmq_module=Recorder, log=lambda line: None)
        self.assertEqual(bound, ["tcp://127.0.0.1:5557", "tcp://127.0.0.1:6001"])


WRAPPER_PROBE = """
import json, os, sys, types
calls = []
lerobot, robots, lekiwi_pkg = (types.ModuleType(n) for n in ("lerobot", "lerobot.robots", "lerobot.robots.lekiwi"))
robot_module, host_module = types.ModuleType("lerobot.robots.lekiwi.lekiwi"), types.ModuleType("lerobot.robots.lekiwi.lekiwi_host")
class LeKiwi:
    def configure(self): pass
    def calibrate(self): pass
    def send_action(self, action): pass
    def get_observation(self): return {}
class LeKiwiHost:
    def __init__(self, config): pass
robot_module.LeKiwi, host_module.LeKiwiHost, host_module.main = LeKiwi, LeKiwiHost, (lambda: calls.append("main"))
stock = (LeKiwiHost.__init__, LeKiwi.get_observation)
for module in (lerobot, robots, lekiwi_pkg, robot_module, host_module):
    sys.modules[module.__name__] = module
if os.environ.get("PROBE_STUB_BOUNDARY") == "1":
    stub = types.ModuleType("host_boundary")
    stub.install = lambda robot_cls, host_cls: calls.append(["install", robot_cls is LeKiwi, host_cls is LeKiwiHost])
    sys.modules["host_boundary"] = stub
sys.path.insert(0, os.environ["PROBE_DIR"])
import lekiwi_host_safe_attach
print(json.dumps({"calls": calls, "boundary_imported": "host_boundary" in sys.modules, "zmq_imported": "zmq" in sys.modules,
                  "host_untouched": (LeKiwiHost.__init__, LeKiwi.get_observation) == stock, "configure_replaced": LeKiwi.configure is lekiwi_host_safe_attach._verify_only_configure}))
"""


class WrapperIntegration(unittest.TestCase):
    """The wrapper is loaded in a CHILD process, against stand-in lerobot modules placed in sys.modules before it is
    imported: it cannot reach a real lerobot, a bus or main(). It is copied ALONE into a temporary directory, the way
    start-host.sh copies it today, so host_boundary.py is NOT next to it."""

    def probe(self, **env):
        with tempfile.TemporaryDirectory() as directory:
            with open(WRAPPER_FILE, encoding="utf-8") as source, open(os.path.join(directory, "lekiwi_host_safe_attach.py"), "w", encoding="utf-8") as copy:
                copy.write(source.read())
            clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_") and k != "PYTHONPATH"}
            return subprocess.run([sys.executable, "-c", WRAPPER_PROBE], env={**clean, "PROBE_DIR": directory, **env}, capture_output=True, text=True, timeout=60, cwd=directory)

    def test_unset_the_wrapper_alone_still_loads_and_touches_nothing_more(self):
        for env in ({}, {"KC_HOST_BOUNDARY": "0"}):
            result = self.probe(**env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"calls": [], "boundary_imported": False, "zmq_imported": False, "host_untouched": True, "configure_replaced": True})

    def test_set_the_wrapper_installs_the_boundary_on_the_two_stock_classes(self):
        result = self.probe(KC_HOST_BOUNDARY="1", PROBE_STUB_BOUNDARY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["calls"], [["install", True, True]], "install(LeKiwi, LeKiwiHost), once, and main() is not called on import")

    def test_set_but_without_the_module_next_to_it_the_wrapper_fails_before_main(self):
        result = self.probe(KC_HOST_BOUNDARY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("host_boundary", result.stderr)
        self.assertEqual(result.stdout, "", "it must not get as far as the stock main() without the enforcement it was asked for")


STOCK_SHAPE = '''
import json, time, zmq
class LeKiwiHost:
    def __init__(self, config):
        self.zmq_context = zmq.Context()
        self.zmq_cmd_socket = self.zmq_context.socket(zmq.PULL)
        self.zmq_cmd_socket.setsockopt(zmq.CONFLATE, 1)
        self.zmq_cmd_socket.bind("tcp://*:1")
        self.zmq_observation_socket = self.zmq_context.socket(zmq.PUSH)
    def disconnect(self):
        self.zmq_observation_socket.close()
        self.zmq_cmd_socket.close()
def main(cfg):
    robot, host = make(cfg)
    last_cmd_time = time.time()
    watchdog_active = False
    try:
        while True:
            try:
                msg = host.zmq_cmd_socket.recv_string(zmq.NOBLOCK)
                data = dict(json.loads(msg))
                robot.send_action(data)
                last_cmd_time = time.time()
                watchdog_active = False
            except zmq.Again:
                pass
            except Exception as e:
                pass
            if time.time() - last_cmd_time > 0.5 and not watchdog_active:
                watchdog_active = True
                robot.stop_base()
            last_observation = robot.get_observation()
            host.zmq_observation_socket.send_string(json.dumps(last_observation), flags=zmq.NOBLOCK)
    finally:
        host.disconnect()
'''


# LeKiwi.send_action, LeKiwi.stop_base and LeKiwi.configure as the robot computer holds them: lerobot b4e2d0b6 plus the
# three local changes that touch these methods. Two of them, the clamp fix and the empty-arm guard, are in send_action.
# The third is the configurable arm gains: configure() writes the three gain registers from the configuration fields
# instead of from the literals 16, 0 and 32 that stood in their place, and the fields themselves are in
# DEPLOYED_CONFIG_SHAPE below. That third one CHANGES NO GAIN: the defaults of those fields are those same literals.
DEPLOYED_ROBOT_SHAPE = '''
class LeKiwi(Robot):
    def configure(self):
        self.bus.disable_torque()
        self.bus.configure_motors()
        for name in self.arm_motors:
            self.bus.write("Operating_Mode", name, OperatingMode.POSITION.value)
            for register, setting in (
                ("P_Coefficient", self.config.arm_p_coefficient),
                ("I_Coefficient", self.config.arm_i_coefficient),
                ("D_Coefficient", self.config.arm_d_coefficient),
            ):
                self.bus.write(register, name, setting[name] if isinstance(setting, dict) else setting)

        for name in self.base_motors:
            self.bus.write("Operating_Mode", name, OperatingMode.VELOCITY.value)

        self.bus.enable_torque()


    @check_if_not_connected
    def send_action(self, action):
        arm_goal_pos = {k: v for k, v in action.items() if k.endswith(".pos")}
        base_goal_vel = {k: v for k, v in action.items() if k.endswith(".vel")}
        base_wheel_goal_vel = self._body_to_wheel_raw(
            base_goal_vel["x.vel"], base_goal_vel["y.vel"], base_goal_vel["theta.vel"]
        )
        if self.config.max_relative_target is not None:
            present_pos = self.bus.sync_read("Present_Position", self.arm_motors)
            goal_present_pos = {key: (g_pos, present_pos[key.removesuffix(".pos")]) for key, g_pos in arm_goal_pos.items()}
            arm_safe_goal_pos = ensure_safe_goal_position(goal_present_pos, self.config.max_relative_target)
            arm_goal_pos = arm_safe_goal_pos
        arm_goal_pos_raw = {k.replace(".pos", ""): v for k, v in arm_goal_pos.items()}
        if arm_goal_pos_raw:
            self.bus.sync_write("Goal_Position", arm_goal_pos_raw)
        self.bus.sync_write("Goal_Velocity", base_wheel_goal_vel)
        return {**arm_goal_pos, **base_goal_vel}

    def stop_base(self):
        self.bus.sync_write("Goal_Velocity", dict.fromkeys(self.base_motors, 0), num_retry=5)
        logger.info("Base motors stopped")
'''

# LeKiwiConfig as bin/pi/apply-lerobot-patches.sh writes it: the stock dataclass of b4e2d0b plus the three arm gain
# fields, declared ONCE, with the defaults 16, 0 and 32 that the stock configure() held as literals. The import lines
# of the real file are left out, because this file is scanned for lerobot imports and the class is all that is read.
DEPLOYED_CONFIG_SHAPE = '''
@RobotConfig.register_subclass("lekiwi")
@dataclass
class LeKiwiConfig(RobotConfig):
    port: str = "/dev/ttyACM0"

    disable_torque_on_disconnect: bool = True

    max_relative_target: float | dict[str, float] | None = None

    cameras: dict[str, CameraConfig] = field(default_factory=lekiwi_cameras_config)

    use_degrees: bool = True

    arm_p_coefficient: int | dict[str, int] = 16
    arm_i_coefficient: int | dict[str, int] = 0
    arm_d_coefficient: int | dict[str, int] = 32
'''

# The SAME configuration as the robot computer carries it: the three fields declared TWICE, in two adjacent blocks
# whose annotations differ and whose values do not. It is the residue of two overlapping hand edits, read there on
# 2026-09-21 and recorded in LIMITATIONS.md. Python keeps the last binding of a name in a class body, so this resolves
# to 16, 0 and 32 exactly as the block above does: it is the same configuration written twice, not another one.
SINGLE_GAIN_BLOCK = ("    arm_p_coefficient: int | dict[str, int] = 16\n"
                     "    arm_i_coefficient: int | dict[str, int] = 0\n"
                     "    arm_d_coefficient: int | dict[str, int] = 32\n")
DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT = DEPLOYED_CONFIG_SHAPE.replace(
    SINGLE_GAIN_BLOCK, SINGLE_GAIN_BLOCK + "\n"
    "    arm_p_coefficient: int = 16\n"
    "    arm_i_coefficient: int = 0\n"
    "    arm_d_coefficient: int = 32\n")


class StockAssumptions(unittest.TestCase):
    def test_the_shape_the_boundary_relies_on_passes(self):
        self.assertEqual(hb.verify_stock_host_source(STOCK_SHAPE), [])

    def test_each_departure_from_it_is_reported(self):
        departures = {
            "watchdog": ("                pass\n            if time", "                pass\n            last_cmd_time = time.time()\n            if time"),
            "order": ("                robot.send_action(data)\n                last_cmd_time = time.time()\n", "                last_cmd_time = time.time()\n                robot.send_action(data)\n"),
            "Again": ("            except zmq.Again:\n                pass\n", ""),
            "reads the command socket": ("                data = dict", "                msg = host.zmq_cmd_socket.recv_string(zmq.NOBLOCK)\n                data = dict"),
            "does not provide": ("            last_observation = robot", "            host.zmq_cmd_socket.poll(1)\n            last_observation = robot"),
            "get_observation": ("            last_observation = robot.get_observation()", "            last_observation = robot.read()"),
            "zmq_context": ("self.zmq_context = zmq.Context()", "self.context = zmq.Context()"),
        }
        for expected, (old, new) in departures.items():
            self.assertEqual(STOCK_SHAPE.count(old), 1, expected)
            problems = hb.verify_stock_host_source(STOCK_SHAPE.replace(old, new))
            self.assertTrue(any(expected in problem for problem in problems), (expected, problems))
        self.assertEqual(hb.verify_stock_host_source("def main(:"), ["the source does not parse"])

    @unittest.skipUnless(os.environ.get("KC_LEROBOT_HOST_SOURCE"), "set KC_LEROBOT_HOST_SOURCE to the path of an installed lerobot lekiwi_host.py to check it (it is read as text, never imported)")
    def test_the_installed_stock_host_has_that_shape(self):
        with open(os.environ["KC_LEROBOT_HOST_SOURCE"], encoding="utf-8") as handle:
            self.assertEqual(hb.verify_stock_host_source(handle.read()), [])

    def test_the_command_line_check_reads_a_file_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            good, bad = os.path.join(directory, "good.py"), os.path.join(directory, "bad.py")
            for path, text in ((good, STOCK_SHAPE), (bad, STOCK_SHAPE.replace("zmq.Again", "zmq.ZMQError"))):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
            clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
            passed = subprocess.run([sys.executable, BOUNDARY_FILE, "--verify-stock-host", good], env=clean, capture_output=True, text=True, timeout=60)
            failed = subprocess.run([sys.executable, BOUNDARY_FILE, "--verify-stock-host", bad], env=clean, capture_output=True, text=True, timeout=60)
        self.assertEqual((passed.returncode, failed.returncode), (0, 1), passed.stderr + failed.stderr)
        self.assertIn("MISMATCH", failed.stdout)
        self.assertNotIn(directory, passed.stdout + failed.stdout)

    # --- the deployed robot source: the same kind of check, against lekiwi.py instead of lekiwi_host.py --------------
    def test_the_deployed_robot_shape_passes(self):
        self.assertEqual(hb.verify_deployed_robot_source(DEPLOYED_ROBOT_SHAPE), [])

    def test_each_departure_from_the_deployed_robot_shape_is_reported(self):
        guarded = '        if arm_goal_pos_raw:\n            self.bus.sync_write("Goal_Position", arm_goal_pos_raw)\n'
        departures = {
            # The guard gone is the stock line back, and with it the defect: an action without a ".pos" key makes the
            # bus raise before Goal_Velocity is written. That is what a fresh lerobot checkout looks like.
            "if arm_goal_pos_raw": (guarded, '        self.bus.sync_write("Goal_Position", arm_goal_pos_raw)\n'),
            # The guard kept, the base write pulled inside it: a velocity-only command would then write no register.
            "at the same level": (guarded + '        self.bus.sync_write("Goal_Velocity", base_wheel_goal_vel)\n',
                                  guarded + '            self.bus.sync_write("Goal_Velocity", base_wheel_goal_vel)\n'),
            "num_retry": ("dict.fromkeys(self.base_motors, 0), num_retry=5", "dict.fromkeys(self.base_motors, 0)"),
            # One of the three keys no longer required is a CONTRACT CHANGE for the laptop, which sends all three on
            # every command precisely because this method has always indexed them.
            "base_goal_vel": ('base_goal_vel["y.vel"], ', ""),
            # The clamp fix gone is the unpatched b4e2d0b line back. The host is always started with
            # --robot.max_relative_target=15.0, so this branch runs on every command and the lookup raises KeyError
            # before any register is written. That one is lerobot's own commit f66e512, applied here by hand.
            "removesuffix": ('present_pos[key.removesuffix(".pos")]', "present_pos[key]"),
            # The guard kept AND the stock unguarded write left beside it, which is what a botched merge leaves behind.
            # The patch script greps for the guard's text and calls it applied, a check that only asks whether SOME
            # guarded write exists is satisfied too, and the stray line still raises on a command without a .pos key.
            "outside": (guarded, guarded + '        self.bus.sync_write("Goal_Position", arm_goal_pos_raw)\n'),
            # Two classes named LeKiwi in one module: Python binds the LAST one, so that is what the robot imports and
            # what this has to read. Resolving the FIRST would call a module sound on the strength of dead code.
            "is missing": ('        logger.info("Base motors stopped")\n',
                           '        logger.info("Base motors stopped")\n\n\nclass LeKiwi(Robot):\n    """The definition this module actually binds."""\n'),
            # configure() back to the stock literals. It changes no gain either way, because those literals ARE the
            # defaults of the three fields; what it changes is that the number this host writes and the number
            # bin/pi/lekiwi_host_safe_attach.py checks the live registers against stop being one fact.
            "self.config.arm_p_coefficient": (
                '            for register, setting in (\n                ("P_Coefficient", self.config.arm_p_coefficient),\n'
                '                ("I_Coefficient", self.config.arm_i_coefficient),\n                ("D_Coefficient", self.config.arm_d_coefficient),\n'
                '            ):\n                self.bus.write(register, name, setting[name] if isinstance(setting, dict) else setting)\n',
                '            self.bus.write("P_Coefficient", name, 16)\n            self.bus.write("I_Coefficient", name, 0)\n'
                '            self.bus.write("D_Coefficient", name, 32)\n'),
            # The fields consulted AND a stock literal write left beside them, which is what a half-finished edit
            # leaves: the register is written with the literal after being written from the field, or instead of it.
            "with a plain number at line": (
                '                self.bus.write(register, name, setting[name] if isinstance(setting, dict) else setting)\n',
                '                self.bus.write(register, name, setting[name] if isinstance(setting, dict) else setting)\n'
                '            self.bus.write("D_Coefficient", name, 32)\n'),
            "LeKiwi.configure is missing": ("    def configure(self):\n", "    def configure_disabled(self):\n"),
        }
        for expected, (old, new) in departures.items():
            self.assertEqual(DEPLOYED_ROBOT_SHAPE.count(old), 1, expected)
            problems = hb.verify_deployed_robot_source(DEPLOYED_ROBOT_SHAPE.replace(old, new))
            self.assertTrue(any(expected in problem for problem in problems), (expected, problems))
        self.assertEqual(hb.verify_deployed_robot_source("class LeKiwi(:"), ["the source does not parse"])
        self.assertEqual(hb.verify_deployed_robot_source("class Other:\n    pass\n"), ["class LeKiwi is missing"])

    @unittest.skipUnless(os.environ.get("KC_LEROBOT_ROBOT_SOURCE"), "set KC_LEROBOT_ROBOT_SOURCE to the path of an installed lerobot lekiwi.py to check it (it is read as text, never imported)")
    def test_the_installed_robot_source_has_the_deployed_shape(self):
        with open(os.environ["KC_LEROBOT_ROBOT_SOURCE"], encoding="utf-8") as handle:
            self.assertEqual(hb.verify_deployed_robot_source(handle.read()), [])

    # --- the configuration beside it: the three arm gain fields, and what they resolve to ----------------------------
    def test_the_configuration_the_patch_writes_holds_and_resolves_to_the_recorded_gains(self):
        self.assertEqual(hb.verify_deployed_configuration(DEPLOYED_CONFIG_SHAPE), ([], []))
        self.assertEqual(hb.resolved_arm_gains(DEPLOYED_CONFIG_SHAPE),
                         [("arm_p_coefficient", "P_Coefficient", 16), ("arm_i_coefficient", "I_Coefficient", 0), ("arm_d_coefficient", "D_Coefficient", 32)])

    def test_the_duplicated_declaration_the_robot_carries_is_a_note_and_not_a_refusal(self):
        # The robot computer declares all three fields twice and must keep starting: the effective values are the
        # recorded ones, so this is reported and not refused. Nothing about it is hidden, which is what the note is for.
        problems, notes = hb.verify_deployed_configuration(DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT)
        self.assertEqual(problems, [])
        self.assertEqual(len(notes), 3, notes)
        for name in ("arm_p_coefficient", "arm_i_coefficient", "arm_d_coefficient"):
            self.assertTrue(any(name in note and "declares" in note for note in notes), (name, notes))
        self.assertEqual(hb.resolved_arm_gains(DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT),
                         [("arm_p_coefficient", "P_Coefficient", 16), ("arm_i_coefficient", "I_Coefficient", 0), ("arm_d_coefficient", "D_Coefficient", 32)])

    def test_a_field_that_is_missing_names_the_exception_the_attach_would_raise(self):
        without = DEPLOYED_CONFIG_SHAPE.replace("    arm_p_coefficient: int | dict[str, int] = 16\n", "")
        problems, notes = hb.verify_deployed_configuration(without)
        self.assertEqual(notes, [])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("AttributeError", problems[0])
        self.assertIn("arm_p_coefficient", problems[0])
        self.assertEqual(hb.resolved_arm_gains(without)[0], ("arm_p_coefficient", "P_Coefficient", hb.ABSENT))

    def test_a_different_value_is_refused_and_the_message_names_what_it_found(self):
        # A gain nobody here recorded is not a tuning question, it is an unknown configuration: the attach would check
        # the live registers against it, and this repository could not say who chose it.
        for text, field, found in ((DEPLOYED_CONFIG_SHAPE.replace("= 16", "= 20"), "arm_p_coefficient", "20"),
                                   (DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT.replace("    arm_d_coefficient: int = 32", "    arm_d_coefficient: int = 8"),
                                    "arm_d_coefficient", "8")):
            problems, notes = hb.verify_deployed_configuration(text)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn(field, problems[0])
            self.assertIn(found, problems[0])
            self.assertIn("LIMITATIONS.md", problems[0])
            # The second text is the duplicated shape the robot carries, so the two fields it did not change are still
            # notes. The changed one is never among them: a value this check does not recognize is not softened.
            self.assertFalse([note for note in notes if field in note], notes)

    def test_the_last_declaration_wins_the_way_python_binds_it(self):
        # Two declarations, the LAST one wrong: reading the first would call a robot configured when it is not.
        text = DEPLOYED_CONFIG_SHAPE.replace(SINGLE_GAIN_BLOCK, SINGLE_GAIN_BLOCK + "\n    arm_p_coefficient: int = 4\n")
        problems, _ = hb.verify_deployed_configuration(text)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("4", problems[0])
        self.assertEqual(hb.resolved_arm_gains(text)[0], ("arm_p_coefficient", "P_Coefficient", 4))

    def test_a_rebinding_without_an_annotation_is_read_the_way_the_dataclass_reads_it(self):
        # The one form that looks like nothing and changes everything. "arm_p_coefficient = 20" after the annotated
        # field is not a field declaration, so dataclasses does not treat it as one; but the decorator takes each
        # default from the class attribute, and this has already replaced it. dataclasses.fields() on a class written
        # this way reports 20. Reading only the annotated lines would report 16, pass, and leave a robot running gains
        # nothing recorded, which is the whole failure this check exists to refuse.
        for rebinding in ("    arm_p_coefficient = 20\n",                       # plain
                          "    arm_p_coefficient, arm_i_coefficient = 20, 5\n"):  # and as a tuple
            with self.subTest(rebinding=rebinding.strip()):
                text = DEPLOYED_CONFIG_SHAPE.replace(SINGLE_GAIN_BLOCK, SINGLE_GAIN_BLOCK + rebinding)
                problems, _ = hb.verify_deployed_configuration(text)
                self.assertTrue(problems, "an unannotated rebinding passed as the recorded configuration")
                self.assertIn("20", problems[0])
                self.assertEqual(dict((name, value) for name, _register, value in hb.resolved_arm_gains(text))["arm_p_coefficient"], 20)

    def test_a_configuration_that_cannot_be_read_is_a_problem_and_never_a_silent_pass(self):
        for text, expected in (("class LeKiwiConfig(:", "the source does not parse"), ("class Other:\n    pass\n", "class LeKiwiConfig is missing")):
            problems, notes = hb.verify_deployed_configuration(text)
            self.assertEqual(notes, [])
            self.assertEqual(len(problems), 1, problems)
            self.assertIn(expected, problems[0])
            self.assertEqual(hb.resolved_arm_gains(text), [])
        # A default that is not a plain whole number is reported as what it is, not treated as an absent field.
        problems, _ = hb.verify_deployed_configuration(DEPLOYED_CONFIG_SHAPE.replace("= 16", "= P_DEFAULT"))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("P_DEFAULT", problems[0])

    def test_the_command_line_check_of_the_robot_source_reads_a_file_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            good, bad = os.path.join(directory, "good.py"), os.path.join(directory, "bad.py")
            unguarded = DEPLOYED_ROBOT_SHAPE.replace("        if arm_goal_pos_raw:\n            self.bus", "        if True:\n            self.bus")
            # The check reads the configuration beside the file it is given, so both live in this directory.
            for path, text in ((good, DEPLOYED_ROBOT_SHAPE), (bad, unguarded), (os.path.join(directory, hb.CONFIG_FILENAME), DEPLOYED_CONFIG_SHAPE)):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
            clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
            passed = subprocess.run([sys.executable, BOUNDARY_FILE, "--verify-robot", good], env=clean, capture_output=True, text=True, timeout=60)
            failed = subprocess.run([sys.executable, BOUNDARY_FILE, "--verify-robot", bad], env=clean, capture_output=True, text=True, timeout=60)
            usage = subprocess.run([sys.executable, BOUNDARY_FILE], env=clean, capture_output=True, text=True, timeout=60)
        self.assertEqual((passed.returncode, failed.returncode, usage.returncode), (0, 1, 1), passed.stderr + failed.stderr)
        self.assertIn("MISMATCH", failed.stdout)
        self.assertNotIn(directory, passed.stdout + failed.stdout)
        for mode in ("--verify-stock-host", "--verify-robot"):
            self.assertIn(mode, usage.stderr, "the usage names both checks, and neither one runs anything")

    def test_the_command_line_check_reports_the_identity_of_every_file_it_read_and_the_gains_it_resolved(self):
        # WHY: a qualification bundle that only says a check passed cannot say WHICH source passed it. These lines are
        # what makes the bundle, and the log of a host start, name the bytes and the three numbers they were read from.
        with tempfile.TemporaryDirectory() as directory:
            robot = os.path.join(directory, "lekiwi.py")
            for path, text in ((robot, DEPLOYED_ROBOT_SHAPE), (os.path.join(directory, hb.CONFIG_FILENAME), DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT)):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
            digests = {name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                       for name, text in (("lekiwi.py", DEPLOYED_ROBOT_SHAPE), (hb.CONFIG_FILENAME, DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT))}
            clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
            result = subprocess.run([sys.executable, BOUNDARY_FILE, "--verify-robot", robot], env=clean, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name, digest in digests.items():
            self.assertIn(f"READ: {name} sha256 {digest}", result.stdout)
        for name, register, value in (("arm_p_coefficient", "P_Coefficient", 16), ("arm_i_coefficient", "I_Coefficient", 0), ("arm_d_coefficient", "D_Coefficient", 32)):
            self.assertIn(f"RESOLVED: {name} = {value}, written to {register} of every arm motor", result.stdout)
        self.assertEqual(result.stdout.count("NOTE: "), 3, "the duplication the robot carries is reported, not swallowed")
        self.assertNotIn("MISMATCH", result.stdout, "and it does not refuse the start: the robot computer is in exactly this state")
        self.assertNotIn(directory, result.stdout)

    def test_a_configuration_that_is_not_beside_the_robot_source_refuses_and_names_no_path(self):
        with tempfile.TemporaryDirectory() as directory:
            robot = os.path.join(directory, "lekiwi.py")
            with open(robot, "w", encoding="utf-8") as handle:
                handle.write(DEPLOYED_ROBOT_SHAPE)
            clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
            result = subprocess.run([sys.executable, BOUNDARY_FILE, "--verify-robot", robot], env=clean, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(hb.CONFIG_FILENAME, result.stdout)
        self.assertNotIn(directory, result.stdout + result.stderr)


# --- the third owned patch, reproduced on a disposable copy of the pinned revision -------------------------------------
# WHY THIS EXISTS. The deployed install carries three local changes, and the third one is a CONFIGURATION dependency: the
# stock LeKiwi.configure() of b4e2d0b writes the literals 16, 0 and 32 to P_Coefficient, I_Coefficient and D_Coefficient
# of every arm motor, and the deployed one writes three LeKiwiConfig fields whose DEFAULTS are those same three literals.
# No gain is different because of it. What it buys is the reading end: bin/pi/lekiwi_host_safe_attach.py reads those three
# fields to check the live registers of every arm motor before it attaches to an arm that is already holding its pose, and
# on a stock LeKiwiConfig that read raises AttributeError. Until now that dependency was only described. What follows
# builds a clean installation from the pinned revision, applies the owned patches to it, and shows the whole of it: the
# exception before, its absence after, the three numbers a patched install resolves, and the fact that the attach path
# READS those three registers and writes none of them. Everything here happens in a temporary directory on a fake bus.
PATCH_SCRIPT = os.path.join(ROOT, "bin", "pi", "apply-lerobot-patches.sh")
LEKIWI_PACKAGE = ("src", "lerobot", "robots", "lekiwi")  # where the installed package puts lekiwi.py and its configuration
PINNED_REVISION = "b4e2d0b6"  # the lerobot commit the deployed checkout sits at, and the one a clean install starts from
GAIN_REGISTERS = tuple(register for _, register, _ in hb.ARM_GAIN_FIELDS)
RECORDED_ARM_GAINS = {register: expected for _, register, expected in hb.ARM_GAIN_FIELDS}  # the deployed reference: 16, 0, 32
HOLDING_TICKS = 2048  # one position, the same for goal and present, which is what "the arm is holding its pose" looks like
# The configuration the host is actually started with, over the defaults declared in the file: bin/pi/start-host.sh passes
# --robot.max_relative_target=15.0, --robot.disable_torque_on_disconnect=false and --robot.cameras={}, and NOT ONE gain
# argument. That is why the three numbers the attach compares the live registers against are the declared defaults.
HOST_COMMAND_LINE = {"max_relative_target": MAX_RELATIVE_TARGET, "disable_torque_on_disconnect": False, "cameras": {}}


def _exactly_once(text, old, new):
    """text with old replaced by new, refusing anything but exactly one occurrence. A fixture built by substitution is
    worth only as much as the substitution being the one that was meant."""
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one occurrence, found {text.count(old)}")
    return text.replace(old, new)


# LeKiwiConfig as lerobot declares it at the pinned revision, byte for byte, with the module's import lines and the other
# two dataclasses left out: this file is scanned for lerobot imports, and LeKiwiConfig is the whole of what is read here.
# It declares NO gain field, which is precisely the state that makes the attach raise. The gated test below holds this
# text against the real file at the pinned revision, so it cannot quietly drift away from what it claims to be.
STOCK_CONFIG_SHAPE = '''
@RobotConfig.register_subclass("lekiwi")
@dataclass
class LeKiwiConfig(RobotConfig):
    port: str = "/dev/ttyACM0"  # port to connect to the bus

    disable_torque_on_disconnect: bool = True

    # `max_relative_target` limits the magnitude of the relative positional target vector for safety purposes.
    # Set this to a positive scalar to have the same value for all motors, or a dictionary that maps motor
    # names to the max_relative_target value for that motor.
    max_relative_target: float | dict[str, float] | None = None

    cameras: dict[str, CameraConfig] = field(default_factory=lekiwi_cameras_config)

    # Set to `True` for backward compatibility with previous policies/dataset
    use_degrees: bool = True
'''

# The two shapes of the gain writes in configure(): the stock one, where the three numbers stand in lekiwi.py as literals,
# and the deployed one, where they come from the configuration. The gated test holds the stock block against the real file
# at the pinned revision as well. Both are indented as they are in the method, because that is how they are replaced.
STOCK_GAIN_WRITES = ('            # Set P_Coefficient to lower value to avoid shakiness (Default is 32)\n'
                     '            self.bus.write("P_Coefficient", name, 16)\n'
                     '            # Set I_Coefficient and D_Coefficient to default value 0 and 32\n'
                     '            self.bus.write("I_Coefficient", name, 0)\n'
                     '            self.bus.write("D_Coefficient", name, 32)\n')
DEPLOYED_GAIN_WRITES = ('            for register, setting in (\n'
                        '                ("P_Coefficient", self.config.arm_p_coefficient),\n'
                        '                ("I_Coefficient", self.config.arm_i_coefficient),\n'
                        '                ("D_Coefficient", self.config.arm_d_coefficient),\n'
                        '            ):\n'
                        '                self.bus.write(register, name, setting[name] if isinstance(setting, dict) else setting)\n')
STOCK_GUARDED_WRITE = '        if arm_goal_pos_raw:\n            self.bus.sync_write("Goal_Position", arm_goal_pos_raw)\n'
# lekiwi.py as a clean installation carries it: the deployed shape above with all three local changes taken back out
# again. Building it by subtraction rather than writing it out keeps the two fixtures the same file with and without the
# patches, so a change to one of them cannot leave the other describing a checkout that never existed.
STOCK_ROBOT_SHAPE = DEPLOYED_ROBOT_SHAPE
for _deployed, _stock in ((DEPLOYED_GAIN_WRITES, STOCK_GAIN_WRITES),
                          ('present_pos[key.removesuffix(".pos")]', "present_pos[key]"),
                          (STOCK_GUARDED_WRITE, '        self.bus.sync_write("Goal_Position", arm_goal_pos_raw)\n')):
    STOCK_ROBOT_SHAPE = _exactly_once(STOCK_ROBOT_SHAPE, _deployed, _stock)


class AttachRefused(Exception):
    """What _refuse does in these tests. The real one prints the problems, disconnects the bus with torque UNTOUCHED and
    then calls os._exit(3), skipping every destructor so that the arm keeps whatever state it had; a test that let it do
    the last of those could report nothing at all. It is raised here instead, and the problems it names are the
    assertion. Nothing else in the attach is replaced."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def _refuse_by_raising(robot, problems):
    raise AttachRefused(problems)


def read_source(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def definition_source(text, path, qualified):
    """The source text of one function or method of a file, dedented so that it can be compiled on its own. The file is
    never imported: bin/pi/lekiwi_host_safe_attach.py imports lerobot and patches LeKiwi at import time, and an installed
    lekiwi.py imports lerobot too. Taking the definition out of the text means the code exercised below is that file's
    own code and not a copy of it that could drift. Where a name is defined twice the LAST definition is taken, which is
    the one Python would bind."""
    tree = ast.parse(text, path)
    scope, name = tree, qualified
    if "." in qualified:
        holder, name = qualified.split(".")
        scope = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}[holder]
    node = {node.name: node for node in ast.walk(scope) if isinstance(node, ast.FunctionDef)}[name]
    return textwrap.dedent(" " * node.col_offset + ast.get_source_segment(text, node))


def module_constants(text, path):
    """Every module-level name in a file that is assigned a plain literal, so that the constants a definition taken out
    of that file reads are the file's own and are not written down a second time here."""
    found = {}
    for statement in ast.parse(text, path).body:
        if not isinstance(statement, ast.Assign):
            continue
        try:
            value = ast.literal_eval(statement.value)
        except (ValueError, TypeError):
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name):
                found[target.id] = value
            elif isinstance(target, ast.Tuple) and isinstance(value, tuple):
                found.update({element.id: item for element, item in zip(target.elts, value) if isinstance(element, ast.Name)})
    return found


WRAPPER_CONSTANTS = module_constants(read_source(WRAPPER_FILE), WRAPPER_FILE)
POSITION_MODE, VELOCITY_MODE = WRAPPER_CONSTANTS["POSITION_MODE"], WRAPPER_CONSTANTS["VELOCITY_MODE"]


class EnumMember:
    """Stands where a member of lerobot's OperatingMode enum stands, so that configure() can be run without importing
    lerobot for two integers. The values are the wrapper's own POSITION_MODE and VELOCITY_MODE, which is what the attach
    compares the live Operating_Mode register against."""

    def __init__(self, value):
        self.value = value


OPERATING_MODE = type("OperatingMode", (), {"POSITION": EnumMember(POSITION_MODE), "VELOCITY": EnumMember(VELOCITY_MODE)})


def attach_configure(printed):
    """LeKiwi.configure as bin/pi/lekiwi_host_safe_attach.py replaces it: the verify-first attach, compiled out of that
    file and given the module names it reads. SCAN_MODE is False because a deployment session starts the host in normal
    mode, where all six arm motors are checked; _refuse raises instead of exiting, and print collects the lines the host
    would have written to the terminal so that a test can hold them against what the bus recorded."""
    namespace = {"SCAN_MODE": False, "GRIPPER": WRAPPER_CONSTANTS["GRIPPER"], "POSITION_MODE": POSITION_MODE, "VELOCITY_MODE": VELOCITY_MODE,
                 "ATTACH_TOLERANCE_TICKS": WRAPPER_CONSTANTS["ATTACH_TOLERANCE_TICKS"],
                 "WHEEL_REST_MAX_RAW": WRAPPER_CONSTANTS["WHEEL_REST_MAX_RAW"], "_refuse": _refuse_by_raising,
                 "print": lambda *args, **kwargs: printed.append(" ".join(str(argument) for argument in args))}
    exec(compile(definition_source(read_source(WRAPPER_FILE), WRAPPER_FILE, "_verify_only_configure"), WRAPPER_FILE, "exec"), namespace)
    return namespace["_verify_only_configure"]


def installed_configure(robot_text):
    """LeKiwi.configure as an installed lekiwi.py holds it: the stock one when the text is stock, the patched one when it
    is patched. It is the one method of the deployed host that writes the three gain registers at all, and the attach
    test below is what holds the rest of the startup to that."""
    namespace = {"OperatingMode": OPERATING_MODE}
    exec(compile(definition_source(robot_text, "lekiwi.py", "LeKiwi.configure"), "lekiwi.py", "exec"), namespace)
    return namespace["configure"]


def parsed_configuration(config_text, **overrides):
    """A stand-in for the LeKiwiConfig instance the host is started with, built from the defaults DECLARED in the text of
    an installed config_lekiwi.py and then given the values the command line sets. The dataclass itself is not
    instantiated, because importing it would import lerobot; what matters for the attach is which attributes exist and
    what they hold, and a field declared twice resolves to the last declaration here exactly as it does in a class body.
    A default this cannot read without lerobot (the camera factory) is left off, and the command line overrides it anyway.
    A field the file does not declare is a field this object does not have, which is the whole of the stock case."""
    config = {node.name: node for node in ast.walk(ast.parse(config_text)) if isinstance(node, ast.ClassDef)}["LeKiwiConfig"]
    fields = {}
    for statement in config.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.value is not None:
            try:
                fields[statement.target.id] = ast.literal_eval(statement.value)
            except (ValueError, TypeError):
                continue
    fields.update(overrides)
    return type("LeKiwiConfig", (), fields)()  # named as lerobot names it, so an absent field raises the message the robot would


class HoldingArmBus(FakeBus):
    """The FakeBus above with a register file behind it, so that a read answers what is in that register and a write
    lands there. It is the same recorder: every call is appended to self.calls in the same shape, and sync_write still
    raises StopIteration on an empty write. What it adds is an arm that is ALREADY HOLDING ITS POSE, which is the only
    state the verify-first attach accepts: torque on, position mode, goal equal to present, the three gains at the
    recorded values, and wheels in velocity mode standing still with their torque off after a power cycle.

    The three bus methods configure() calls and a dispatch never does (disable_torque, configure_motors, enable_torque)
    are recorded rather than silently accepted, so that nothing configure() does to this bus goes unseen."""

    def __init__(self, live_gains=None):
        super().__init__()
        self.registers = {
            "Torque_Enable": {**dict.fromkeys(ARM_MOTORS, 1), **dict.fromkeys(BASE_MOTORS, 0)},
            "Operating_Mode": {**dict.fromkeys(ARM_MOTORS, POSITION_MODE), **dict.fromkeys(BASE_MOTORS, VELOCITY_MODE)},
            "Present_Position": dict.fromkeys(ARM_MOTORS, HOLDING_TICKS),
            "Goal_Position": dict.fromkeys(ARM_MOTORS, HOLDING_TICKS),
            "Present_Velocity": dict.fromkeys(BASE_MOTORS, 0),
            "Goal_Velocity": dict.fromkeys(BASE_MOTORS, 0),
            "Phase": dict.fromkeys(BUS_MOTOR_IDS, 0),
            "Acceleration": dict.fromkeys(ARM_MOTORS, 0),
            "Lock": dict.fromkeys(ARM_MOTORS, 0),
            **{register: dict.fromkeys(ARM_MOTORS, value) for register, value in RECORDED_ARM_GAINS.items()},
        }
        for register, values in (live_gains or {}).items():  # a servo whose live gain is not what the configuration says
            self.registers[register].update(values)

    def read(self, register, motor, **kwargs):
        value = self.registers[register][motor]
        self.calls.append(("read", register, {BUS_MOTOR_IDS[motor]: value}, kwargs))
        return value

    def sync_read(self, register, motors, **kwargs):
        reading = {motor: self.registers[register][motor] for motor in motors}
        self.calls.append(("sync_read", register, self._ids(reading), kwargs))
        return reading

    def write(self, register, motor, value, **kwargs):
        self.registers.setdefault(register, {})[motor] = value
        super().write(register, motor, value, **kwargs)

    def disable_torque(self, *args, **kwargs):
        self.calls.append(("disable_torque", None, {}, {}))

    def enable_torque(self, *args, **kwargs):
        self.calls.append(("enable_torque", None, {}, {}))

    def configure_motors(self, *args, **kwargs):
        self.calls.append(("configure_motors", None, {}, {}))


class AttachedRobot:
    """What LeKiwi.configure reads off the robot object, and nothing else: the bus, the parsed configuration, the two
    motor lists in the order lerobot builds them, and the cameras (none: the host is started with --robot.cameras={})."""

    def __init__(self, bus, config):
        self.bus, self.config = bus, config
        self.arm_motors, self.base_motors, self.cameras = list(ARM_MOTORS), list(BASE_MOTORS), {}


class ReproducingTheDeployedConfigurationOnADisposableCopy(unittest.TestCase):
    """The third owned patch, applied to a CLEAN INSTALLATION built in a temporary directory and exercised against the
    fake bus above.

    WHAT THIS PROVES: that a stock installation raises the AttributeError the record says it raises, at the point it says
    it raises it; that bin/pi/apply-lerobot-patches.sh --apply resolves it; that the patched installation configures the
    arm with the SAME three numbers the stock literals wrote, which is why this patch changes no gain; and that the
    verify-first attach reads the three gain registers of every arm motor and writes none of them.

    WHAT THIS IS NOT. It is the reproduction of a RECORDED configuration on a disposable copy. It is not hardware
    validation, it establishes nothing whatever about how the robot behaves, and no register on any robot was written by
    it: the files come from a git revision or from the text fixtures above, the bus is fake, and the three numbers
    compared against are the ones recorded in bin/pi/host_boundary.py, not numbers read off a servo today. The live robot
    computer is untouched and stays untouched until the planned deployment session."""

    def disposable_install(self, robot_text, config_text):
        """A clean installation: the two files the owned patches touch, at the paths the package puts them, in a
        temporary directory that is removed when the test ends."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        package = os.path.join(directory.name, *LEKIWI_PACKAGE)
        os.makedirs(package)
        for name, text in (("lekiwi.py", robot_text), (hb.CONFIG_FILENAME, config_text)):
            with open(os.path.join(package, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        return directory.name

    def patch(self, mode, tree):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
        return subprocess.run([PATCH_SCRIPT, mode, tree], env=clean, capture_output=True, text=True, timeout=120)

    def installed(self, tree):
        """(the text of lekiwi.py, the text of config_lekiwi.py) as they stand in a disposable install right now."""
        package = os.path.join(tree, *LEKIWI_PACKAGE)
        return read_source(os.path.join(package, "lekiwi.py")), read_source(os.path.join(package, hb.CONFIG_FILENAME))

    def gain_calls(self, bus, method):
        """[(register, {motor id: value})] for every call of that kind naming one of the three gain registers."""
        return [(register, values) for name, register, values, _ in bus.calls if register in GAIN_REGISTERS and name == method]

    def attach(self, config_text, live_gains=None):
        """Run the verify-first attach against a holding arm configured by the text of an installed config_lekiwi.py.
        Returns (the robot it ran on, the lines it printed)."""
        printed = []
        robot = AttachedRobot(HoldingArmBus(live_gains), parsed_configuration(config_text, **HOST_COMMAND_LINE))
        attach_configure(printed)(robot)
        return robot, printed

    def apply_to_a_clean_installation(self):
        """A clean installation from the text fixtures, with the owned patches applied to it. Returns (the tree, the
        patched lekiwi.py, the patched config_lekiwi.py)."""
        tree = self.disposable_install(STOCK_ROBOT_SHAPE, STOCK_CONFIG_SHAPE)
        applied = self.patch("--apply", tree)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        return (tree, *self.installed(tree))

    # --- the defect first: a reproduction that does not show it proves nothing --------------------------------------
    def test_a_stock_installation_raises_the_attribute_error_after_reading_the_arm_and_before_writing_anything(self):
        tree = self.disposable_install(STOCK_ROBOT_SHAPE, STOCK_CONFIG_SHAPE)
        _, config_text = self.installed(tree)
        robot = AttachedRobot(HoldingArmBus(), parsed_configuration(config_text, **HOST_COMMAND_LINE))
        with self.assertRaises(AttributeError) as raised:
            attach_configure([])(robot)
        # The message the robot computer would print, word for word: the field is not declared, so the attribute is not
        # there to read. This is the reason the third patch exists at all.
        self.assertIn("'LeKiwiConfig' object has no attribute 'arm_p_coefficient'", str(raised.exception))
        # And WHERE it is raised matters as much as that it is raised: the arm has already been read, and nothing has
        # been written. An exception on the far side of a write would be a different and much worse defect.
        self.assertEqual(robot.bus.writes(), [], "a stock install raises before any register is written, and that is not an accident")
        self.assertEqual([register for name, register, _, _ in robot.bus.calls if name == "sync_read"],
                         ["Torque_Enable", "Operating_Mode", "Present_Position", "Goal_Position", "Present_Velocity", "Goal_Velocity"])
        self.assertEqual(self.gain_calls(robot.bus, "read"), [], "it does not get as far as reading a gain register: the tuple it would read them for is built first")

    # --- applying the owned patches to it ---------------------------------------------------------------------------
    def test_applying_the_owned_patches_to_a_clean_installation_reproduces_the_recorded_configuration(self):
        tree = self.disposable_install(STOCK_ROBOT_SHAPE, STOCK_CONFIG_SHAPE)
        checked = self.patch("--check", tree)
        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        for patch in ("clamp-fix", "empty-arm-guard", "configurable-arm-gains-fields", "configurable-arm-gains-writes"):
            self.assertIn(f"{patch}: not applied", checked.stdout)
        applied = self.patch("--apply", tree)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        robot_text, config_text = self.installed(tree)
        # The two halves of the owned pair, and the third patch, all agree with the verifier that reads them before a
        # host start: this is the property the applier and bin/pi/host_boundary.py have to keep between them.
        self.assertEqual(hb.verify_deployed_robot_source(robot_text), [])
        self.assertEqual(hb.verify_deployed_configuration(config_text), ([], []))
        # THE COMPARISON, stated: what a clean patched installation resolves, what the deployed install resolves (the
        # shape recorded above, duplicated declaration and all), and the reference table are the same three numbers.
        self.assertEqual(hb.resolved_arm_gains(config_text), hb.resolved_arm_gains(DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT))
        self.assertEqual(hb.resolved_arm_gains(config_text),
                         [("arm_p_coefficient", "P_Coefficient", 16), ("arm_i_coefficient", "I_Coefficient", 0), ("arm_d_coefficient", "D_Coefficient", 32)])
        # A clean installation declares the three fields ONCE. The robot computer declares them twice, which is a note
        # and not a difference of configuration, and that is the one way this copy is not byte for byte the deployed one.
        declared, problem = hb.arm_gain_configuration(config_text)
        self.assertIsNone(problem)
        self.assertEqual(declared, {"arm_p_coefficient": [16], "arm_i_coefficient": [0], "arm_d_coefficient": [32]})
        again = self.patch("--apply", tree)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.installed(tree), (robot_text, config_text), "a second --apply leaves both files byte for byte")

    def test_the_patched_installation_writes_the_same_arm_gains_the_stock_literals_wrote(self):
        # THE WHOLE CLAIM OF THIS PATCH, exercised rather than described: run the stock configure() and the patched one
        # against the same kind of bus and compare what reached the motors. The two are identical, so nothing about how
        # the arm is configured changed; what changed is where the three numbers come from.
        _, patched_robot_text, patched_config_text = self.apply_to_a_clean_installation()
        writes = {}
        for label, robot_text, text in (("stock", STOCK_ROBOT_SHAPE, STOCK_CONFIG_SHAPE), ("patched", patched_robot_text, patched_config_text)):
            robot = AttachedRobot(HoldingArmBus(), parsed_configuration(text, **HOST_COMMAND_LINE))
            installed_configure(robot_text)(robot)
            writes[label] = [(register, values) for _, register, values, _ in robot.bus.writes()]
        self.assertEqual(writes["patched"], writes["stock"])
        expected = [(register, {BUS_MOTOR_IDS[motor]: value}) for motor in ARM_MOTORS for register, value in RECORDED_ARM_GAINS.items()]
        self.assertEqual([call for call in writes["patched"] if call[0] in GAIN_REGISTERS], expected)
        # Said plainly: 16, 0 and 32, to every one of the six arm motors, from a configuration that sets none of them.
        self.assertEqual(sorted({(register, value) for register, values in expected for value in values.values()}),
                         [("D_Coefficient", 32), ("I_Coefficient", 0), ("P_Coefficient", 16)])

    # --- and the reading end, which is what the fields are for ------------------------------------------------------
    def test_the_attach_reads_the_three_gain_registers_of_every_arm_motor_and_writes_none_of_them(self):
        _, _, config_text = self.apply_to_a_clean_installation()
        robot, printed = self.attach(config_text)  # it completes: there is no missing field left to raise on
        read = self.gain_calls(robot.bus, "read")
        self.assertEqual([(register, {BUS_MOTOR_IDS[motor]: RECORDED_ARM_GAINS[register]}) for motor in ARM_MOTORS for register in GAIN_REGISTERS], read)
        # THE REGISTER-WRITE POLICY, on the recorded calls: every call naming one of the three is a read, wherever in
        # this path it happens, and the only writes at all are the base zero-speed hold on the three wheels.
        self.assertEqual({name for name, register, _, _ in robot.bus.calls if register in GAIN_REGISTERS}, {"read"})
        self.assertEqual([(register, values) for _, register, values, _ in robot.bus.writes()],
                         [(register, {BUS_MOTOR_IDS[wheel]: value}) for wheel in BASE_MOTORS for register, value in (("Goal_Velocity", 0), ("Torque_Enable", 1))])
        self.assertTrue(any("NO arm register was written" in line for line in printed), printed)

    def test_a_live_gain_that_differs_from_the_configuration_refuses_the_attach_and_writes_nothing(self):
        # The other half of the policy: the check is a real comparison against the live register, and a servo that does
        # not match is refused rather than written to. Without this, a passing attach would prove only that it ran.
        _, _, config_text = self.apply_to_a_clean_installation()
        robot = AttachedRobot(HoldingArmBus({"P_Coefficient": {"arm_elbow_flex": 20}}), parsed_configuration(config_text, **HOST_COMMAND_LINE))
        with self.assertRaises(AttachRefused) as refused:
            attach_configure([])(robot)
        self.assertEqual(refused.exception.problems, ["arm_elbow_flex: P_Coefficient is 20, expected 16"])
        self.assertEqual(robot.bus.writes(), [], "a refused attach writes nothing at all, gains least of all")

    def test_the_verifier_passes_on_the_patched_installation_and_refuses_the_stock_one(self):
        stock = self.disposable_install(STOCK_ROBOT_SHAPE, STOCK_CONFIG_SHAPE)
        patched, robot_text, config_text = self.apply_to_a_clean_installation()
        clean = {k: v for k, v in os.environ.items() if not k.startswith("KC_")}
        results = {}
        for label, tree in (("stock", stock), ("patched", patched)):
            source = os.path.join(tree, *LEKIWI_PACKAGE, "lekiwi.py")
            results[label] = subprocess.run([sys.executable, BOUNDARY_FILE, "--verify-robot", source], env=clean, capture_output=True, text=True, timeout=60)
        self.assertEqual(results["patched"].returncode, 0, results["patched"].stdout + results["patched"].stderr)
        self.assertEqual(results["stock"].returncode, 1, results["stock"].stdout)
        self.assertIn("AttributeError", results["stock"].stdout)
        for name, register, value in (("arm_p_coefficient", "P_Coefficient", 16), ("arm_i_coefficient", "I_Coefficient", 0), ("arm_d_coefficient", "D_Coefficient", 32)):
            self.assertIn(f"RESOLVED: {name} = {value}, written to {register} of every arm motor", results["patched"].stdout)
        # The identities in the report are of the bytes this reproduction actually read, which is what makes it
        # evidence rather than an assertion that some install somewhere passed.
        for name, text in (("lekiwi.py", robot_text), (hb.CONFIG_FILENAME, config_text)):
            self.assertIn(f"READ: {name} sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}", results["patched"].stdout)
        for label, result in results.items():
            self.assertNotIn(os.path.dirname(BOUNDARY_FILE), result.stdout, label)

    # --- the same round trip from the pinned revision itself, where a checkout of it is at hand ---------------------
    @unittest.skipUnless(os.environ.get("KC_LEROBOT_CHECKOUT"), "set KC_LEROBOT_CHECKOUT to a lerobot git checkout holding "
                         f"{PINNED_REVISION} to run the reproduction from the pinned revision itself (it is read with git show, never modified, never imported)")
    def test_the_same_reproduction_from_the_pinned_lerobot_revision_itself(self):
        # The tests above build the clean installation from text fixtures, so that they run where no lerobot checkout
        # exists. This one starts from the revision the deployed checkout sits at and proves the fixtures are that
        # revision: a fixture that drifted would make every reproduction above describe a file nobody installs.
        checkout = os.environ["KC_LEROBOT_CHECKOUT"]
        sources = {}
        for name in ("lekiwi.py", hb.CONFIG_FILENAME):
            shown = subprocess.run(["git", "-C", checkout, "show", f"{PINNED_REVISION}:{'/'.join(LEKIWI_PACKAGE)}/{name}"],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(shown.returncode, 0, f"{name} at {PINNED_REVISION}: {shown.stderr}")
            sources[name] = shown.stdout
        self.assertIn(STOCK_CONFIG_SHAPE.strip(), sources[hb.CONFIG_FILENAME], "the stock configuration fixture is no longer the pinned one")
        for fixture in (STOCK_GAIN_WRITES, '            self.bus.write("Operating_Mode", name, OperatingMode.POSITION.value)\n',
                        '        self.bus.sync_write("Goal_Position", arm_goal_pos_raw)\n',
                        '        goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in arm_goal_pos.items()}\n'):
            self.assertIn(fixture, sources["lekiwi.py"], "the stock robot fixture is no longer the pinned one")
        tree = self.disposable_install(sources["lekiwi.py"], sources[hb.CONFIG_FILENAME])
        stock_robot_text, stock_config_text = self.installed(tree)
        with self.assertRaises(AttributeError) as raised:
            self.attach(stock_config_text)
        self.assertIn("'LeKiwiConfig' object has no attribute 'arm_p_coefficient'", str(raised.exception))
        applied = self.patch("--apply", tree)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        robot_text, config_text = self.installed(tree)
        self.assertEqual(hb.verify_deployed_robot_source(robot_text), [])
        self.assertEqual(hb.verify_deployed_configuration(config_text), ([], []))
        self.assertEqual(hb.resolved_arm_gains(config_text), hb.resolved_arm_gains(DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT))
        # The gains the real stock configure() writes and the gains the real patched one writes, side by side.
        gains = {}
        for label, text, config in (("stock", stock_robot_text, stock_config_text), ("patched", robot_text, config_text)):
            robot = AttachedRobot(HoldingArmBus(), parsed_configuration(config, **HOST_COMMAND_LINE))
            installed_configure(text)(robot)
            gains[label] = [(register, values) for _, register, values, _ in robot.bus.writes() if register in GAIN_REGISTERS]
        self.assertEqual(gains["patched"], gains["stock"])
        self.assertEqual(gains["patched"], [(register, {BUS_MOTOR_IDS[motor]: value}) for motor in ARM_MOTORS for register, value in RECORDED_ARM_GAINS.items()])
        robot, printed = self.attach(config_text)
        self.assertEqual({name for name, register, _, _ in robot.bus.calls if register in GAIN_REGISTERS}, {"read"})
        self.assertTrue(any("NO arm register was written" in line for line in printed), printed)


def attach_with_wheels(present=(0, 0, 0), goal=(0, 0, 0), registers=None):
    """The verify-first attach against a holding arm whose three wheels (left, back, right) read PRESENT and GOAL as raw
    Present_Velocity and Goal_Velocity, with any other register values in REGISTERS ({register: {motor: value}}).
    Returns (the bus, the lines printed, the problems of a refusal or None when it attached)."""
    bus = HoldingArmBus()
    bus.registers["Present_Velocity"].update(zip(BASE_MOTORS, present))
    bus.registers["Goal_Velocity"].update(zip(BASE_MOTORS, goal))
    for register, values in (registers or {}).items():
        bus.registers[register].update(values)
    printed = []
    robot = AttachedRobot(bus, parsed_configuration(DEPLOYED_CONFIG_SHAPE_AS_THE_ROBOT_CARRIES_IT, **HOST_COMMAND_LINE))
    try:
        attach_configure(printed)(robot)
    except AttachRefused as refused:
        return bus, printed, refused.problems
    return bus, printed, None


class TheAttachTakesOneCountOfWheelDitherAsRest(unittest.TestCase):
    """The safe attach's wheel check, run on the fake bus (operator ruling 2026-09-27: "Fix the trip. This shouldn't have
    happened and should never fail in the same way in other places."). Present_Velocity moves in steps of 50, ONE velocity
    count, and a wheel standing still reads 0 or one count of either sign, on one wheel or on two at once
    (autonomy/manipulation/base_rest.py). The attach refused three arms on 2026-10-01 on exactly that: a wheel at 50 or -50
    with goal velocity 0. A wheel reading at most one count with goal velocity exactly 0 now attaches and gets the
    zero-speed hold; more than one count, or any goal velocity but 0, refuses with nothing written; every other check is
    what it was. A one-count read cannot be produced on the robot at will, so this is where the rule itself is proven."""

    HOLD = [(register, {BUS_MOTOR_IDS[wheel]: value}) for wheel in BASE_MOTORS for register, value in (("Goal_Velocity", 0), ("Torque_Enable", 1))]

    def writes(self, bus):
        return [(register, values) for _, register, values, _ in bus.writes()]

    def test_the_one_count_is_fifty_raw(self):
        self.assertEqual(WRAPPER_CONSTANTS["WHEEL_REST_MAX_RAW"], 50)

    def test_one_count_of_either_sign_on_one_two_or_three_wheels_attaches_and_holds_the_base(self):
        # (50, 0, 0) and (0, -50, 0) are the two readings the refusals of 2026-10-01 named, (50, -50, 0) is both at once, and
        # (-50, -50, 0) is the two-wheel read recorded on 2026-09-22.
        for present in ((50, 0, 0), (0, -50, 0), (50, -50, 0), (-50, 0, 0), (0, 0, 50), (-50, -50, 0), (50, -50, 50), (-50, -50, -50),
                        (0, 0, 0)):
            with self.subTest(present=present):
                bus, printed, problems = attach_with_wheels(present)
                self.assertIsNone(problems, problems)
                self.assertEqual(self.writes(bus), self.HOLD, "the zero target, then torque, on each wheel whose torque was off")
                ok = [line for line in printed if line.startswith("SAFE ATTACH OK: base present velocity")]
                self.assertEqual(len(ok), 1, printed)
                self.assertIn(str(dict(zip(BASE_MOTORS, present))), ok[0], "the OK line states the velocities it read, never a literal 0")
                self.assertIn("NO arm register was written", ok[0])
                self.assertFalse([line for line in printed if "base velocity 0" in line], printed)

    def test_more_than_one_count_on_any_wheel_refuses_with_nothing_written(self):
        for present in ((51, 0, 0), (0, -51, 0), (100, 0, 0), (0, -100, 0), (0, 0, 100), (50, 100, 0), (-150, 0, 0), (0, 0, -32767)):
            with self.subTest(present=present):
                bus, _, problems = attach_with_wheels(present)
                expected = [f"{wheel}: present/goal velocity {value}/0, expected at most one count (|present| <= 50)/0"
                            for wheel, value in zip(BASE_MOTORS, present) if abs(value) > 50]
                self.assertEqual(problems, expected)
                self.assertEqual(bus.writes(), [], "a refused attach writes nothing, to the base least of all")

    def test_a_goal_velocity_other_than_zero_refuses_with_nothing_written_whatever_the_wheel_reads(self):
        for present, goal in (((0, 0, 0), (1, 0, 0)), ((0, 0, 0), (0, -1, 0)), ((0, 0, 0), (0, 0, 50)), ((50, 0, 0), (50, 0, 0)),
                              ((0, -50, 0), (0, -50, 0))):
            with self.subTest(present=present, goal=goal):
                bus, _, problems = attach_with_wheels(present, goal)
                expected = [f"{wheel}: present/goal velocity {p}/{g}, expected at most one count (|present| <= 50)/0"
                            for wheel, p, g in zip(BASE_MOTORS, present, goal) if g != 0]
                self.assertEqual(problems, expected)
                self.assertEqual(bus.writes(), [])

    def test_a_one_count_wheel_changes_no_other_check(self):
        # Every other refusal is what it was, with a wheel at one count beside it: the count neither masks a problem nor adds
        # one. Each case is the problem list the attach gives with all three wheels at exactly 0 as well.
        cases = {
            "an arm joint without torque": {"Torque_Enable": {"arm_elbow_flex": 0}},
            "an arm joint not in position mode": {"Operating_Mode": {"arm_wrist_flex": VELOCITY_MODE}},
            "an arm joint not holding its goal": {"Present_Position": {"arm_shoulder_lift": HOLDING_TICKS + 13}},
            "a gain that differs": {"P_Coefficient": {"arm_shoulder_pan": 20}},
            "a Phase bit": {"Phase": {"base_back_wheel": 0x10}},
            "a wheel not in velocity mode": {"Operating_Mode": {"base_left_wheel": POSITION_MODE}},
        }
        for name, registers in cases.items():
            with self.subTest(case=name):
                _, _, at_zero = attach_with_wheels((0, 0, 0), registers=registers)
                self.assertTrue(at_zero, "the case must refuse on its own")
                for present in ((50, 0, 0), (-50, -50, 0)):
                    bus, _, problems = attach_with_wheels(present, registers=registers)
                    self.assertEqual(problems, at_zero)
                    self.assertEqual(bus.writes(), [])


class ThisTestStaysOffTheRobot(unittest.TestCase):
    def test_neither_this_file_nor_the_module_imports_lerobot_or_opens_a_network_endpoint(self):
        for path in (os.path.abspath(__file__), BOUNDARY_FILE):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIsNone(re.search(r"(?m)^\s*(import|from) lerobot", text.replace(WRAPPER_PROBE, "").replace(STOCK_SHAPE, "")), path)
        with open(os.path.abspath(__file__), encoding="utf-8") as handle:
            endpoints = set(re.findall(r"[a-z]+://[^\s\"'{}]*", handle.read()))
        self.assertTrue(all(endpoint.startswith("inproc://") or endpoint in ("tcp://127.0.0.1:5557", "tcp://127.0.0.1:6001", "tcp://*:1") for endpoint in endpoints), endpoints)


if __name__ == "__main__":
    unittest.main()
