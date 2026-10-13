#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_tap_client.py: the read-only subscriber of the host tap. The PUBLISHER is the real one
# (bin/pi/host_boundary.py, loaded from its file), so the two ends are tested against each other. What is proved: a sample
# the publisher failed to send and a sample lost after it was sent both show up as a subscriber gap, and only the first
# moves the publisher's drop counter, so the two are reported apart; a restarted host is a session change and not a gap;
# every age is laptop clock only and does not move when the host's clock does; the stamp fits the console contract; nothing
# raises into the caller. Fake sockets, and real pyzmq sockets on inproc:// only. No network, no robot, no lerobot.
# Run directly: python3 packages/demo-runtime/tests/test_tap_client.py
import importlib.util
import json
import subprocess
import sys
import threading
import unittest
import uuid
from pathlib import Path

import zmq

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(REPO_ROOT / "packages" / "demo-contracts" / "python"))

from demo_contracts import load_schema, validate  # noqa: E402
from demo_runtime import tap_client  # noqa: E402
from demo_runtime.tap_client import TapClient, parse_tap_message, source_stamp  # noqa: E402

_spec = importlib.util.spec_from_file_location("host_boundary", REPO_ROOT / "bin" / "pi" / "host_boundary.py")
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)

SESSION = 555000111222333


class Clock:
    def __init__(self, start):
        self.value = start

    def __call__(self):
        return self.value

    def seconds(self):
        return self.value / 1e9


class Wire:
    """PUB on one side, SUB on the other. A send can FAIL (the publisher knows), or succeed and be LOST (it does not)."""

    def __init__(self):
        self.in_transit, self.fail_sends, self.lose_sends, self.send_calls = [], set(), set(), 0

    # the publisher's side
    def send(self, payload, flags=0):
        self.send_calls += 1
        if self.send_calls in self.fail_sends:
            raise zmq.Again()
        if self.send_calls not in self.lose_sends:
            self.in_transit.append([payload])

    # the subscriber's side
    def poll(self, timeout=0):
        return bool(self.in_transit)

    def recv_multipart(self):
        return self.in_transit.pop(0)

    def close(self, linger=None):
        pass


class NoControl:
    def recv_multipart(self, flags=0):
        raise zmq.Again()


def observation():
    return {"arm_elbow_flex.pos": 12.5, "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}


class Bench(unittest.TestCase):
    def setUp(self):
        self.wire, self.host_clock, self.laptop_clock = Wire(), Clock(9_000_000_000_000), Clock(40_000_000_000)
        self.boundary = self.new_boundary(SESSION)
        self.client = TapClient("inproc://unused", socket_factory=lambda: self.wire, clock_ns=self.laptop_clock)

    def new_boundary(self, session):
        return hb.HostBoundary(zmq, NoControl(), self.wire, host_session=session, clock=self.host_clock.seconds, clock_ns=self.host_clock, wall_clock=lambda: 1.7e9,
                               log=lambda line: None)

    def publish(self, count, boundary=None):
        for _ in range(count):
            self.host_clock.value += 33_000_000
            (boundary or self.boundary).observe(observation)


class LossesAreToldApart(Bench):
    def test_without_loss_there_is_no_gap(self):
        self.publish(5)
        samples = self.client.poll()
        self.assertEqual([s["seq"] for s in samples], [1, 2, 3, 4, 5])
        self.assertEqual([s["missed_before"] for s in samples], [None, 0, 0, 0, 0], "the first sample has nothing to be compared with")
        self.assertEqual((self.client.subscriber_missed_count, self.client.subscriber_gap_events, self.client.publisher_drop_count), (0, 0, 0))

    def test_a_send_the_publisher_failed_is_a_gap_that_the_publisher_accounts_for(self):
        self.wire.fail_sends = {3, 4}
        self.publish(6)
        samples = self.client.poll()
        self.assertEqual([s["seq"] for s in samples], [1, 2, 5, 6])
        after = samples[2]
        self.assertEqual((after["missed_before"], after["publisher_drops_before"], after["lost_after_send_before"]), (2, 2, 0))
        self.assertEqual((self.client.subscriber_missed_count, self.client.subscriber_gap_events, self.client.publisher_drop_count, self.boundary.tap.drop_count), (2, 1, 2, 2))

    def test_a_sample_lost_after_a_successful_send_is_a_gap_the_publisher_never_sees(self):
        self.wire.lose_sends = {3, 4}  # what libzmq does for a subscriber whose queue is full: the send succeeded
        self.publish(6)
        samples = self.client.poll()
        self.assertEqual([s["seq"] for s in samples], [1, 2, 5, 6])
        after = samples[2]
        self.assertEqual((after["missed_before"], after["publisher_drops_before"], after["lost_after_send_before"]), (2, 0, 2))
        self.assertEqual((self.client.subscriber_missed_count, self.client.publisher_drop_count, self.boundary.tap.drop_count), (2, 0, 0),
                         "the publisher's drop counter is NOT a count of what subscribers missed")

    def test_both_kinds_in_one_gap(self):
        self.wire.fail_sends, self.wire.lose_sends = {2}, {3, 4}
        self.publish(5)
        after = self.client.poll()[-1]
        self.assertEqual((after["seq"], after["missed_before"], after["publisher_drops_before"], after["lost_after_send_before"]), (5, 3, 1, 2))

    def test_a_restarted_host_is_a_session_change_not_a_gap(self):
        self.publish(40)
        self.client.poll()
        restarted = self.new_boundary(SESSION + 1)
        self.publish(2, restarted)
        first, second = self.client.poll()
        self.assertEqual((first["seq"], first["session_changed"], first["missed_before"], first["publisher_drops_before"]), (1, True, None, None))
        self.assertEqual((second["seq"], second["session_changed"], second["missed_before"]), (2, False, 0))
        self.assertEqual((self.client.session_changes, self.client.subscriber_missed_count, self.client.out_of_order_count), (1, 0, 0))

    def test_a_seq_that_repeats_or_goes_back_is_counted_as_such(self):
        self.publish(3)
        replay = list(self.wire.in_transit[:2])
        self.client.poll()
        self.wire.in_transit.extend(replay)
        samples = self.client.poll()
        self.assertEqual(([s["missed_before"] for s in samples], self.client.out_of_order_count, self.client.subscriber_missed_count), ([0, 0], 2, 0))
        self.publish(1)
        self.assertEqual([(s["seq"], s["missed_before"]) for s in self.client.poll()], [(4, 0)], "a replay must not turn the next real sample into a gap")


class ClocksAreNeverMixed(Bench):
    def test_receive_age_is_the_laptops_clock_and_does_not_move_with_the_hosts(self):
        self.publish(1)
        sample = self.client.poll()[0]
        self.assertEqual(sample["rx_mono_ns"], 40_000_000_000)
        self.assertEqual(sample["host_sample_mono_ns"], 9_000_033_000_000, "the host's sample time (publish() moved its clock 33 ms on), as the host wrote it")
        self.laptop_clock.value += 250_000_000
        self.assertEqual(self.client.receive_age_ns(sample), 250_000_000)
        self.assertEqual(self.client.receive_age_ns(), 250_000_000)
        self.host_clock.value -= 8_000_000_000_000  # the host's clock is another clock: whatever it does changes no age here
        self.publish(1)
        later = self.client.poll()[0]
        self.assertEqual(self.client.receive_age_ns(later), 0)
        self.assertEqual(self.client.receive_age_ns(sample), 250_000_000)
        self.assertEqual(sample["host_wall_time_s"], 1.7e9)
        self.assertIsNone(TapClient("inproc://unused", socket_factory=Wire).receive_age_ns())

    def test_the_module_never_subtracts_a_host_time(self):
        source = (PACKAGE_DIR / "demo_runtime" / "tap_client.py").read_text(encoding="utf-8")
        code = source.split('"""', 2)[2]
        for line in code.splitlines():
            if "-" in line.split("#")[0] and ("host_sample_mono_ns" in line or "t_mono_ns" in line or "wall_time" in line):
                self.fail(f"a host clock value next to a subtraction: {line.strip()}")

    def test_the_source_stamp_fits_the_console_contract_and_names_the_hosts_clock(self):
        self.publish(1)
        stamp = source_stamp(self.client.poll()[0])
        self.assertEqual(stamp, {"session": str(SESSION), "seq": 1, "sample_mono_ns": 9_000_033_000_000, "clock_domain": "pi_monotonic"})
        self.assertEqual(validate("common#/$defs/source_stamp", stamp), [])

    def test_the_mirrored_constants_are_the_sources(self):
        self.assertEqual(tap_client.TAP_VERSION, hb.TAP_VERSION)
        self.assertEqual(tap_client.SESSION_PATTERN, load_schema("common#/$defs/source_stamp")["properties"]["session"]["pattern"])
        self.assertIn(tap_client.CLOCK_PI, load_schema("common#/$defs/clock_domain")["enum"])


class WhatASampleCarries(Bench):
    def test_lease_rejections_and_command_timing_come_through_as_the_host_measured_them(self):
        self.boundary.lease.acquire("runtime", 60)
        self.boundary.counters.reject("wrong_generation")
        self.boundary.timing.dispatched()
        self.host_clock.value += 120_000_000
        self.boundary.timing.dispatched()
        self.publish(1)
        sample = self.client.poll()[0]
        self.assertEqual((sample["lease"]["state"], sample["lease"]["generation"], sample["lease"]["host_session"]), ("held", 1, str(SESSION)))
        self.assertEqual(sample["rejections"], {"no_lease": 0, "wrong_generation": 1, "wrong_session": 0, "malformed": 0})
        self.assertEqual((sample["commands"]["dispatched_in_lease"], sample["commands"]["last_gap_ms"], sample["commands"]["max_gap_ms_in_lease"]), (2, 120.0, 120.0))
        self.assertEqual(sample["observation"], observation())
        self.assertEqual(validate("snapshot#/properties/owner/properties/lease", {key: sample["lease"][key] for key in ("host_session", "generation", "expires_in_ms")}), [])

    def test_what_is_not_a_tap_sample_is_counted_and_skipped(self):
        good = {"kc.tap": 1, "session": "s1", "seq": 4}
        for bad in (b"not json", b"\xff", b"[]", json.dumps({**good, "kc.tap": 2}).encode(), json.dumps({**good, "session": "has space"}).encode(), json.dumps({**good, "session": 7}).encode(),
                    json.dumps({**good, "seq": 4.0}).encode(), json.dumps({**good, "seq": -1}).encode(), json.dumps({**good, "seq": True}).encode()):
            self.assertIsNone(parse_tap_message(bad), bad)
            self.wire.in_transit.append([bad])
        self.assertIsNone(parse_tap_message(None))
        self.wire.in_transit.append([b"two", b"frames"])
        self.wire.in_transit.append([json.dumps(good).encode()])
        samples = self.client.poll()
        self.assertEqual(([s["seq"] for s in samples], self.client.malformed_count, self.client.received_count), ([4], 10, 1))
        self.assertEqual((samples[0]["host_sample_mono_ns"], samples[0]["publisher_drops_before"], samples[0]["lease"]), (None, None, None), "a sparse sample is served with what it has")


class NeverRaises(unittest.TestCase):
    def test_a_transport_fault_is_counted_and_the_socket_is_reopened(self):
        opened = []

        class Flaky(Wire):
            def poll(self, timeout=0):
                if len(opened) == 1:
                    raise zmq.ZMQError(zmq.ETERM)
                return super().poll(timeout)

        def factory():
            opened.append(Flaky())
            return opened[-1]

        client = TapClient("inproc://unused", socket_factory=factory)
        self.assertEqual((client.poll(), client.transport_errors), ([], 1))
        self.assertEqual((client.poll(), client.transport_errors, len(opened)), ([], 1, 2))

        def broken_factory():
            raise ImportError("no zmq here")

        client = TapClient("inproc://unused", socket_factory=broken_factory)
        self.assertEqual((client.poll(), client.poll(), client.transport_errors), ([], [], 2))
        client.close()

    def test_samples_yields_until_told_to_stop(self):
        wire = Wire()
        client = TapClient("inproc://unused", socket_factory=lambda: wire)
        for seq in (1, 2, 3):
            wire.in_transit.append([json.dumps({"kc.tap": 1, "session": "s1", "seq": seq}).encode()])
        polls = []
        seen = [sample["seq"] for sample in client.samples(poll_timeout_ms=0, should_stop=lambda: polls.append(1) or len(polls) > 3)]
        self.assertEqual(seen, [1, 2, 3])
        self.assertEqual(client.stats()["received_count"], 3)


class OverRealSockets(unittest.TestCase):
    def test_the_real_publisher_and_the_real_subscriber_understand_each_other(self):
        context = zmq.Context()
        tag = uuid.uuid4().hex
        boundary = hb.open_boundary(zmq, context, f"inproc://control-{tag}", f"inproc://tap-{tag}", log=lambda line: None)
        client = TapClient(f"inproc://tap-{tag}", context=context)
        try:
            for _ in range(5):
                boundary.observe(observation)  # nobody subscribed: a PUB socket neither blocks nor fails
            self.assertEqual((boundary.tap.drop_count, boundary.seq), (0, 5))
            samples = []
            for _ in range(300):  # a subscription takes effect asynchronously
                boundary.observe(observation)
                samples += client.poll(timeout_ms=10)
                if len(samples) >= 3:
                    break
            self.assertGreaterEqual(len(samples), 3, "no tap sample arrived")
            self.assertGreater(samples[0]["seq"], 5, "what was published before the subscription is gone, and that is not a gap")
            self.assertEqual([s["missed_before"] for s in samples[1:]], [0] * (len(samples) - 1))
            self.assertEqual((samples[0]["session"], samples[0]["observation"], samples[0]["lease"]["state"]), (boundary.lease.session_text, observation(), "free"))
            self.assertEqual(validate("common#/$defs/source_stamp", source_stamp(samples[0])), [])
            self.assertEqual(client.stats()["publisher_drop_count"], 0)
        finally:
            client.close()
            boundary.close()
            worker = threading.Thread(target=context.term, daemon=True)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "a socket was left open")


class OnlyALocalEndpoint(unittest.TestCase):
    """The host binds its tap to its own loopback address. An endpoint naming another machine cannot be that socket,
    and a subscriber would take whatever answered there for the robot: it is refused, and not repeated."""

    def test_a_loopback_or_in_process_endpoint_is_kept(self):
        for endpoint in ("tcp://127.0.0.1:5558", "tcp://localhost:5558", "inproc://unused", "inproc://tap-" + uuid.uuid4().hex):
            self.assertEqual(tap_client.check_endpoint(endpoint), endpoint)
            self.assertEqual(TapClient(endpoint, socket_factory=Wire)._endpoint, endpoint)

    def test_any_other_endpoint_is_refused_without_being_echoed(self):
        elsewhere = "tcp://" + ".".join(("192", "0", "2", "41")) + ":5558"
        named = (elsewhere, "tcp://0.0.0.0:5558", "tcp://*:5558", "tcp://127.0.0.1.example:5558", "ipc:///tmp/tap")
        for endpoint in named + ("tcp://127.0.0.1:70000", "tcp://127.0.0.1:", "inproc://", "", None, 5558):
            with self.assertRaises(ValueError) as caught:
                TapClient(endpoint, socket_factory=Wire)
            self.assertIn("ssh forward", str(caught.exception), endpoint)
        for endpoint in named:  # an address or a path a reader could copy is never repeated back
            with self.assertRaises(ValueError) as caught:
                TapClient(endpoint, socket_factory=Wire)
            self.assertNotIn(endpoint, str(caught.exception))


class ImportIsLight(unittest.TestCase):
    def test_importing_the_module_pulls_in_no_zmq_and_no_robot_client(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import demo_runtime.tap_client; print(sorted(m for m in ('zmq', 'lerobot', 'numpy', 'cv2') if m in sys.modules))"
        result = subprocess.run([sys.executable, "-c", code, str(PACKAGE_DIR)], capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "[]"), result.stderr)


if __name__ == "__main__":
    unittest.main()
