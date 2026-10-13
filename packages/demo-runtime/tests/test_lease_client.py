#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_lease_client.py: the laptop side of the host command lease. The HOST side is the real one
# (bin/pi/host_boundary.py, loaded from its file): its ControlService runs in a thread of this test, on an inproc://
# endpoint, so the two ends are tested against each other and not against a copy. What is proved: the four operations and
# what the client then believes; that nothing ever raises into the caller; that a timed out request CAN be served late and
# that an acquire served late, or whose reply was lost, is adopted while somebody else's lease is not; that the metadata a controller attaches
# is accepted by the host after the float coercion of the stock client. No network, no robot, no lerobot.
# Run directly: python3 packages/demo-runtime/tests/test_lease_client.py
import importlib.util
import json
import subprocess
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

import zmq

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(REPO_ROOT / "packages" / "demo-contracts" / "python"))

from demo_contracts import validate  # noqa: E402
from demo_runtime import lease_client  # noqa: E402
from demo_runtime.lease_client import LeaseClient, attach_lease_metadata, command_metadata  # noqa: E402

_spec = importlib.util.spec_from_file_location("host_boundary", REPO_ROOT / "bin" / "pi" / "host_boundary.py")
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)

SESSION = 987654321012345
LEASE_SCHEMA = "snapshot#/properties/owner/properties/lease"


class HostClock:
    def __init__(self):
        self.t = 50.0

    def __call__(self):
        return self.t


class HostSide:
    """The real Lease and ControlService, served from a thread on an inproc endpoint."""

    def __init__(self, context, endpoint, session=SESSION):
        self.clock = HostClock()
        self.lease = hb.Lease(session, self.clock)
        self.counters = hb.Counters()
        self._context, self._endpoint, self._stop = context, endpoint, threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        assert self._ready.wait(5)

    def _serve(self):
        socket = self._context.socket(zmq.REP)
        socket.setsockopt(zmq.LINGER, 0)
        socket.bind(self._endpoint)
        control = hb.ControlService(socket, self.lease, self.counters, zmq)
        self._ready.set()
        while not self._stop.is_set():
            if socket.poll(10):
                control.serve()
        socket.close(0)

    def stop(self):
        self._stop.set()
        self._thread.join(5)


class WithHost(unittest.TestCase):
    def setUp(self):
        self.context = zmq.Context()
        self.endpoint = f"inproc://control-{uuid.uuid4().hex}"
        self.clients, self.host = [], None
        self.addCleanup(self.shutdown)

    def start_host(self, **kwargs):
        self.host = HostSide(self.context, self.endpoint, **kwargs)
        return self.host

    def client(self, label="runtime", **kwargs):
        client = LeaseClient(self.endpoint, label, context=self.context, **{"timeout_ms": 2000, **kwargs})
        self.clients.append(client)
        return client

    def shutdown(self):
        for client in self.clients:
            client.close()
        if self.host is not None:
            self.host.stop()
        worker = threading.Thread(target=self.context.term, daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "a socket was left open")


class Operations(WithHost):
    def test_acquire_renew_status_release(self):
        host, client = self.start_host(), self.client()
        self.assertFalse(client.held)
        self.assertIsNone(client.command_metadata())
        granted = client.acquire()
        self.assertEqual((granted["ok"], granted["host_session"], granted["generation"], granted["ttl_s"]), (True, str(SESSION), 1, 180.0))
        self.assertTrue(client.held)
        self.assertEqual(client.command_metadata(), {"kc.lease": 1.0, "kc.session": float(SESSION)})
        host.clock.t += 100
        self.assertAlmostEqual(client.status()["expires_in_ms"], 80000.0)
        self.assertAlmostEqual(client.renew()["expires_in_ms"], 180000.0)
        self.assertEqual(client.acquire()["reason"], "already_held_by_this_client")
        released = client.release()
        self.assertEqual((released["ok"], released["state"]), (True, "free"))
        self.assertFalse(client.held)
        self.assertIsNone(client.command_metadata())
        self.assertIsNone(client.lost_reason, "a release is not a loss")
        self.assertEqual((client.renew()["reason"], client.release()["reason"]), ("not_held_by_this_client", "not_held_by_this_client"))
        self.assertEqual(client.acquire(ttl_s=30)["generation"], 2, "every acquisition is a new generation")

    def test_the_metadata_is_accepted_by_the_host_after_the_stock_clients_float_coercion(self):
        host, client = self.start_host(), self.client()
        client.acquire()
        action = {"arm_gripper.pos": 3, "x.vel": 0.0, **client.command_metadata()}
        on_the_wire = json.loads(json.dumps({key: float(value) for key, value in action.items()}))  # lekiwi_client.send_action, then the host's json.loads
        self.assertIsNone(host.lease.admit(on_the_wire))
        self.assertEqual(host.lease.admit({**on_the_wire, "kc.lease": 2.0}), "wrong_generation")

    def test_a_second_client_is_refused_while_the_first_holds_the_lease(self):
        self.start_host()
        first, second = self.client("runtime"), self.client("teleop")
        first.acquire()
        refused = second.acquire()
        self.assertEqual((refused["ok"], refused["reason"], refused["owner_label"]), (False, "lease_held", "runtime"))
        self.assertFalse(second.held)
        self.assertIsNone(second.command_metadata())
        self.assertTrue(first.renew()["ok"])

    def test_close_does_not_release(self):
        host, client = self.start_host(), self.client()
        client.acquire()
        client.close()
        self.assertEqual(host.lease.state(), "held", "a controller may still be commanding under the lease")
        self.assertTrue(client.held)
        self.assertTrue(client.renew()["ok"], "the next call simply opens a new socket")

    def test_the_default_ttl_mirrors_the_host_and_outlasts_the_quiet_phase(self):
        self.assertEqual((lease_client.LEASE_KEY, lease_client.SESSION_KEY, lease_client.DEFAULT_TTL_S), (hb.LEASE_KEY, hb.SESSION_KEY, hb.DEFAULT_TTL_S))
        self.assertGreater(lease_client.DEFAULT_TTL_S, 90.0)
        self.assertEqual(set(lease_client.LEASE_LOST_REASONS), {hb.WRONG_GENERATION, hb.WRONG_SESSION, hb.NO_LEASE, "lease_expired"})


class LosingTheLease(WithHost):
    def test_expiry_on_the_host_is_learned_at_the_next_renew(self):
        host, client = self.start_host(), self.client()
        client.acquire(ttl_s=20)
        host.clock.t += 21
        self.assertTrue(client.held, "the host is the authority; this side has not heard yet")
        refused = client.renew()
        self.assertEqual((refused["ok"], refused["reason"]), (False, "lease_expired"))
        self.assertEqual((client.held, client.lost_reason, client.command_metadata()), (False, "lease_expired", None))
        self.assertEqual(client.acquire()["generation"], 2, "coming back is an explicit new acquisition")
        self.assertIsNone(client.lost_reason)

    def test_an_ownership_change_is_learned_from_a_status(self):
        host, first, second = self.start_host(), self.client("runtime"), self.client("other")
        first.acquire(ttl_s=5)
        host.clock.t += 6
        second.acquire()
        self.assertTrue(first.status()["ok"])
        self.assertEqual((first.held, first.lost_reason), (False, "wrong_generation"))
        self.assertTrue(second.held)

    def test_a_restarted_host_is_not_mistaken_for_the_one_that_granted_the_lease(self):
        host, client = self.start_host(), self.client()
        client.acquire()
        host.stop()
        self.host = HostSide(self.context, self.endpoint + "-restarted", session=SESSION + 7)
        restarted = LeaseClient(self.endpoint + "-restarted", "other", context=self.context, timeout_ms=2000)
        self.clients.append(restarted)
        restarted.acquire()  # generation 1 again, on ANOTHER host session
        client._endpoint = self.endpoint + "-restarted"
        client.close()
        refused = client.renew()
        self.assertEqual(refused["reason"], "wrong_session", "generation 1 of the old host must not renew generation 1 of the new one")
        self.assertEqual((client.held, client.lost_reason), (False, "host_restarted"))
        self.assertEqual(self.host.lease.status()["owner_label"], "other")


class NeverRaises(WithHost):
    def test_no_host_is_a_timeout_and_a_request_served_late_does_not_lock_the_client_out(self):
        client = self.client(timeout_ms=100)
        started = time.monotonic()
        for call in (client.acquire, client.acquire, client.status):
            self.assertEqual(call(), {"ok": False, "reason": "timeout"})
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(client.held)
        host = self.start_host()  # the host appears AFTER every request gave up
        deadline = time.monotonic() + 5
        while host.counters.control_requests < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        # An inproc pipe delivers what was written to it even after the writer closed: the FIRST acquire was served late.
        self.assertEqual((host.lease.state(), host.lease.generation, host.counters.control_requests), ("held", 1, 3))
        recovered = client.acquire()
        self.assertEqual((recovered["ok"], recovered.get("adopted"), recovered["generation"], "reason" in recovered), (True, True, 1, False))
        self.assertEqual((client.held, client.generation), (True, 1))
        self.assertTrue(client.renew()["ok"])

    def test_every_transport_fault_comes_back_as_a_dict(self):
        class Broken:
            def __init__(self, stage, reply=b""):
                self.stage, self.reply = stage, reply

            def send(self, payload):
                if self.stage == "send":
                    raise zmq.Again()

            def poll(self, timeout):
                if self.stage == "poll":
                    raise OSError("poll")
                return True

            def recv(self):
                return self.reply

            def close(self, linger=None):
                if self.stage == "close":
                    raise RuntimeError("close")

        def failing_factory():
            raise ImportError("no zmq here")

        cases = ((lambda: Broken("send"), "timeout"), (lambda: Broken("poll"), "transport_error"), (lambda: Broken("recv", b"not json"), "transport_error"),
                 (lambda: Broken("recv", b"[]"), "bad_reply"), (lambda: Broken("recv", b'{"ok": "yes"}'), "bad_reply"), (lambda: Broken("close", b"\xff"), "transport_error"),
                 (failing_factory, "transport_error"))
        for factory, reason in cases:
            client = LeaseClient("inproc://unused", "runtime", socket_factory=factory, timeout_ms=10)
            for call in (client.acquire, client.status):
                reply = call()
                self.assertEqual((reply["ok"], reply["reason"]), (False, reason))
            self.assertFalse(client.held)
            client.close()


class ScriptedSocket:
    """A REQ stand-in whose replies come from the real ControlService, or never come."""

    def __init__(self, control, lose_reply=False, deliver=True):
        self.control, self.lose_reply, self.deliver, self.reply = control, lose_reply, deliver, None

    def send(self, payload):
        if self.deliver:
            self.reply = json.dumps(self.control.handle(json.loads(payload))).encode()

    def poll(self, timeout):
        return not self.lose_reply and self.reply is not None

    def recv(self):
        return self.reply

    def close(self, linger=None):
        pass


class Adoption(unittest.TestCase):
    def setUp(self):
        self.lease = hb.Lease(SESSION, HostClock())
        self.control = hb.ControlService(None, self.lease, hb.Counters(), zmq)

    def client(self, *sockets):
        queue = list(sockets)
        return LeaseClient("inproc://unused", "runtime", socket_factory=lambda: queue.pop(0), timeout_ms=10)

    def test_an_acquire_whose_reply_was_lost_is_adopted_at_the_next_reply(self):
        client = self.client(ScriptedSocket(self.control, lose_reply=True), ScriptedSocket(self.control))
        self.assertEqual(client.acquire(), {"ok": False, "reason": "timeout"})
        self.assertEqual(self.lease.state(), "held", "the host DID grant it")
        self.assertFalse(client.held)
        status = client.status()
        self.assertEqual((status["state"], client.held, client.generation, client.host_session), ("held", True, 1, str(SESSION)))
        self.assertTrue(self.control.handle({"op": "release", **{"host_session": client.host_session, "generation": client.generation}})["ok"])

    def test_a_retried_acquire_adopts_too_instead_of_locking_itself_out(self):
        client = self.client(ScriptedSocket(self.control, lose_reply=True), ScriptedSocket(self.control))
        client.acquire()
        retried = client.acquire()  # a NEW request id, refused with lease_held: the holder is this client's first request
        self.assertEqual((retried["ok"], retried["adopted"], retried["generation"], client.held, self.lease.generation), (True, True, 1, True, 1))

    def test_somebody_elses_lease_is_never_adopted(self):
        client = self.client(ScriptedSocket(self.control, deliver=False), ScriptedSocket(self.control), ScriptedSocket(self.control))
        self.assertEqual(client.acquire()["reason"], "timeout")  # this one never reached the host
        self.lease.acquire("other", request_id="someone-else")
        self.assertEqual(client.status()["state"], "held")
        self.assertFalse(client.held)
        self.assertEqual(client.acquire()["reason"], "lease_held")
        self.assertFalse(client.held)


class Metadata(unittest.TestCase):
    def test_command_metadata_is_two_floats_and_refuses_what_no_host_issues(self):
        self.assertEqual(command_metadata(3, "42"), {"kc.lease": 3.0, "kc.session": 42.0})
        self.assertTrue(all(type(value) is float for value in command_metadata(3, "42").values()))
        for generation, session in ((0, "42"), (-1, "42"), (1, "0"), (1, "abc"), (1, None), (None, "42"), (True, "42"), (1, str(2 ** 53)), (2 ** 53, "42")):
            with self.assertRaises(ValueError, msg=(generation, session)):
                command_metadata(generation, session)

    def test_attach_adds_the_keys_to_every_action_and_leaves_the_callers_dict_alone(self):
        sent = []

        class Robot:
            def send_action(self, action):
                sent.append(action)
                return "sent"

        robot, action = Robot(), {"x.vel": 0.1, "kc.lease": 99.0}
        current = {"value": command_metadata(2, "42")}
        detach = attach_lease_metadata(robot, lambda: current["value"])
        self.assertEqual(robot.send_action(action), "sent")
        self.assertEqual(sent[-1], {"x.vel": 0.1, "kc.lease": 2.0, "kc.session": 42.0}, "the lease wins over whatever the action carried")
        self.assertEqual(action, {"x.vel": 0.1, "kc.lease": 99.0})
        current["value"] = None  # the lease is gone: the action goes out bare and a host with its boundary on drops it
        robot.send_action({"x.vel": 0.2})
        self.assertEqual(sent[-1], {"x.vel": 0.2})
        detach()
        attach_lease_metadata(robot, command_metadata(5, "42"))
        robot.send_action({"x.vel": 0.3})
        self.assertEqual(sent[-1]["kc.lease"], 5.0)

    def test_the_snapshot_lease_fits_the_console_contract(self):
        lease = hb.Lease(SESSION, HostClock())
        control = hb.ControlService(None, lease, hb.Counters(), zmq)
        client = LeaseClient("inproc://unused", "runtime", socket_factory=lambda: ScriptedSocket(control), timeout_ms=10)
        self.assertIsNone(client.snapshot_lease())
        self.assertEqual(validate(LEASE_SCHEMA, client.snapshot_lease()), [])
        client.status()
        self.assertIsNone(client.snapshot_lease(), "a free host reports no lease")
        client.acquire()
        snapshot = client.snapshot_lease()
        self.assertEqual(snapshot, {"host_session": str(SESSION), "generation": 1, "expires_in_ms": 180000.0})
        self.assertEqual(validate(LEASE_SCHEMA, snapshot), [])
        self.assertIsNotNone(client.last_reply_age_ms())
        self.assertNotEqual(validate(LEASE_SCHEMA, {**snapshot, "host_session": SESSION}), [], "the contract wants the session as text: that is why the host reports text")


class OnlyALocalEndpoint(unittest.TestCase):
    """An acquire sent to a machine that was not meant would take the lease of ANOTHER robot host. The host binds its
    control socket to its own loopback address, so only a local or in-process endpoint can name it."""

    def test_a_loopback_or_in_process_endpoint_is_kept(self):
        for endpoint in ("tcp://127.0.0.1:5557", "tcp://localhost:5557", "inproc://unused", f"inproc://control-{uuid.uuid4().hex}-restarted"):
            self.assertEqual(lease_client.check_endpoint(endpoint), endpoint)
            self.assertEqual(LeaseClient(endpoint, "runtime")._endpoint, endpoint)

    def test_any_other_endpoint_is_refused_without_being_echoed(self):
        elsewhere = "tcp://" + ".".join(("192", "0", "2", "41")) + ":5557"
        named = (elsewhere, "tcp://0.0.0.0:5557", "tcp://*:5557", "tcp://127.0.0.1.example:5557", "ipc:///tmp/control")
        for endpoint in named + ("tcp://127.0.0.1:70000", "inproc://", "", None, 5557):
            with self.assertRaises(ValueError) as caught:
                LeaseClient(endpoint, "runtime")
            self.assertIn("ssh forward", str(caught.exception), endpoint)
        for endpoint in named:
            with self.assertRaises(ValueError) as caught:
                LeaseClient(endpoint, "runtime")
            self.assertNotIn(endpoint, str(caught.exception))


class ImportIsLight(unittest.TestCase):
    def test_importing_the_module_pulls_in_no_zmq_and_no_robot_client(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import demo_runtime.lease_client; print(sorted(m for m in ('zmq', 'lerobot', 'numpy', 'cv2') if m in sys.modules))"
        result = subprocess.run([sys.executable, "-c", code, str(PACKAGE_DIR)], capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "[]"), result.stderr)


if __name__ == "__main__":
    unittest.main()
