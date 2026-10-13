#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_fresh_client.py: the freshness adapter says, per read, whether the observation it served was
# new; it never changes what the base class returns while the refusal is off; and the refusal, when on, waits a bounded time
# and then raises. The base class here is a FAKE with a scripted message queue that mirrors the control flow of the stock
# client's _get_data() (lerobot 0.6.1): no zmq, no lerobot, no numpy, no network. Time is a fake clock; nothing sleeps.
# Run directly: python3 packages/demo-runtime/tests/test_fresh_client.py
import ast
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
CONTRACTS_DIR = REPO_ROOT / "packages" / "demo-contracts"
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(CONTRACTS_DIR / "python"))

import demo_contracts  # noqa: E402
from demo_runtime import fresh_client as fc  # noqa: E402
from demo_runtime.fresh_client import StaleSourceError, make_fresh_client_class, parse_host_metadata, source_stamp_from  # noqa: E402

# The keys the stock client builds its state from (_state_order): six arm positions and the three base velocities.
STATE_ORDER = ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos", "arm_wrist_flex.pos", "arm_wrist_roll.pos", "arm_gripper.pos",
               "x.vel", "y.vel", "theta.vel")
DOCUMENTATION_ADDRESS = "192.0.2.10"  # RFC 5737: an address that routes nowhere
SYNTHETIC_ROBOT = "synthetic-unit"


class FakeBase:
    """The stock client's read path with the transport replaced by a script. One script entry per poll: a raw string is a
    message that arrived, None is 'nothing new within the polling timeout'. An exhausted script keeps answering None.

    Mirrored on purpose, because the adapter's claims are about exactly this flow: no message, a message that is not JSON and
    a message whose processing raises ALL serve the cached last_remote_state; an adopted message REPLACES last_remote_state
    with a new dict built from the STATE_ORDER keys only, so any other key of the message is dropped."""

    def __init__(self, script):
        self.script = list(script)
        self.polls = 0
        self.last_remote_state = {}

    def _poll_and_get_latest_message(self):
        self.polls += 1
        return self.script.pop(0) if self.script else None

    def _get_data(self):
        raw = self._poll_and_get_latest_message()
        if raw is None:
            return self.last_remote_state
        try:
            observation = json.loads(raw)
        except json.JSONDecodeError:
            return self.last_remote_state
        try:
            new_state = {key: float(observation.get(key, 0.0)) for key in STATE_ORDER}
        except Exception:  # the stock client catches Exception here too
            return self.last_remote_state
        self.last_remote_state = new_state
        return new_state

    def get_observation(self):
        return self._get_data()


class MinimalBase:
    """The least a base class can be: no last_remote_state, so adoption cannot be observed and arrival has to do."""

    def __init__(self, script):
        self.script = list(script)
        self.cached = {}

    def _poll_and_get_latest_message(self):
        return self.script.pop(0) if self.script else None

    def get_observation(self):
        raw = self._poll_and_get_latest_message()
        if raw is not None:
            self.cached = {"x.vel": json.loads(raw)["x.vel"]}
        return self.cached


class FakeTime:
    """A monotonic clock that moves only when told to, and a sleep that moves it instead of waiting."""

    def __init__(self, start_ns=1_000_000_000):
        self.now_ns = start_ns
        self.sleeps = []

    def clock_ns(self):
        return self.now_ns

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now_ns += int(seconds * 1e9)
        if len(self.sleeps) > 10_000:  # 20 fake seconds of re-polling: a wait that lost its bound fails here instead of hanging the suite
            raise AssertionError("the wait is not bounded")


def message(x_vel=0.0, **extra):
    body = {key: 0.0 for key in STATE_ORDER}
    body["x.vel"] = x_vel
    body.update(extra)
    return json.dumps(body)


def stamped(seq, x_vel=0.0, session="synthetic-host-session-0001", t_mono_ns=2_000_000_000):
    return message(x_vel, **{"kc.session": session, "kc.seq": seq, "kc.t_mono_ns": t_mono_ns})


def make(script, base=FakeBase):
    fake_time = FakeTime()
    client = make_fresh_client_class(base, clock_ns=fake_time.clock_ns, sleep=fake_time.sleep)(script)
    return client, fake_time


# Every way a poll can turn out, in one script: used to prove that the adapter returns what the base class returns.
NOT_JSON = "{not json"
JSON_LIST = json.dumps([1, 2, 3])
UNPROCESSABLE = message(**{"y.vel": "not-a-number"})
SCRIPTED_CASES = {
    "nothing ever arrives": [None, None, None],
    "one message then silence": [message(0.1), None, None],
    "every read has a message": [message(0.1), message(0.2), message(0.3)],
    "silence first": [None, message(0.1), None, message(0.2)],
    "not json": [message(0.1), NOT_JSON, message(0.2)],
    "not json before any state": [NOT_JSON, None, message(0.2)],
    "a json list": [message(0.1), JSON_LIST, None],
    "processing raises": [message(0.1), UNPROCESSABLE, None, message(0.2)],
    "host stamps": [stamped(1, 0.1), None, stamped(2, 0.2)],
    "host repeats a seq": [stamped(7, 0.1), stamped(7, 0.2), stamped(8, 0.3)],
    "host stamps badly": [message(0.1, **{"kc.session": 5, "kc.seq": "x", "kc.t_mono_ns": [1]}), message(0.2, **{"kc.seq": -1})],
    "an extra key that is not a camera": [message(0.1, note="hello"), None],
}


class FreshVersusCached(unittest.TestCase):
    def test_each_read_reports_whether_it_was_served_a_new_message(self):
        client, _ = make([message(0.1), None, message(0.2), None, None])
        fresh = []
        for _ in range(5):
            client.get_observation()
            fresh.append(client.last_stamp()["fresh"])
        self.assertEqual(fresh, [True, False, True, False, False])

    def test_one_sample_read_three_times_is_one_fresh_read(self):
        # The guard this module exists for: three reads that all show a base at rest, and only one sample behind them.
        client, _ = make([message(0.0)])
        observations, stamps = [], []
        for _ in range(3):
            observations.append(client.get_observation())
            stamps.append(client.last_stamp())
        self.assertTrue(all(o["x.vel"] == 0.0 and o["y.vel"] == 0.0 and o["theta.vel"] == 0.0 for o in observations), "all three reads look like rest")
        self.assertEqual([s["fresh"] for s in stamps], [True, False, False])
        self.assertEqual(sum(s["fresh"] for s in stamps), 1)
        self.assertEqual([s["local_rx_seq"] for s in stamps], [1, 1, 1], "a cached read repeats the identity of the sample it serves")

    def test_before_any_message(self):
        client, _ = make([None, None])
        self.assertEqual(client.last_stamp(), {"fresh": False, "local_rx_seq": 0, "local_rx_mono_ns": None, "age_ns": None, "host": None, "availability": "no_message_yet"})
        self.assertEqual(client.get_observation(), {})
        self.assertEqual(client.last_stamp()["availability"], "no_message_yet")
        self.assertIsNone(client.last_stamp()["age_ns"], "no message, no age: not a made-up number")
        self.assertIsNone(client.to_source_stamp())

    def test_a_message_the_base_class_could_not_use_is_not_a_fresh_read(self):
        for name, unusable in (("not json", NOT_JSON), ("a json list", JSON_LIST), ("processing raises", UNPROCESSABLE)):
            with self.subTest(name):
                client, _ = make([message(0.1), unusable, message(0.3)])
                first = client.get_observation()
                self.assertTrue(client.last_stamp()["fresh"])
                second = client.get_observation()
                self.assertIs(second, first, "the base class served its cached state")
                self.assertFalse(client.last_stamp()["fresh"])
                self.assertEqual(client.last_stamp()["local_rx_seq"], 1, "the sample SERVED is still the first one")
                client.get_observation()
                self.assertTrue(client.last_stamp()["fresh"])
                self.assertEqual(client.last_stamp()["local_rx_seq"], 3, "the gap is the message that arrived and was not used")

    def test_age_is_laptop_monotonic_and_settled_at_the_read(self):
        client, fake_time = make([message(0.1), None])
        client.get_observation()
        received_ns = fake_time.now_ns
        self.assertEqual(client.last_stamp()["local_rx_mono_ns"], received_ns)
        self.assertEqual(client.last_stamp()["age_ns"], 0)
        fake_time.now_ns += 150_000_000  # the spacing of the base-at-rest guard's reads
        client.get_observation()
        stamp = client.last_stamp()
        self.assertEqual((stamp["fresh"], stamp["local_rx_mono_ns"], stamp["age_ns"]), (False, received_ns, 150_000_000))
        fake_time.now_ns += 999_000_000
        self.assertEqual(client.last_stamp()["age_ns"], 150_000_000, "the age at the read, not at the time of asking")

    def test_the_host_clock_is_never_mixed_into_the_age(self):
        for host_time in (5, 9_000_000_000_000_000_000):  # far behind and far ahead of the laptop's clock
            with self.subTest(host_time=host_time):
                client, fake_time = make([stamped(1, t_mono_ns=host_time), None])
                client.get_observation()
                fake_time.now_ns += 40_000_000
                client.get_observation()
                stamp = client.last_stamp()
                self.assertEqual(stamp["age_ns"], 40_000_000)
                self.assertEqual(stamp["host"]["sample_mono_ns"], host_time, "reported as sent, in its own domain")

    def test_last_stamp_is_a_copy(self):
        client, _ = make([stamped(1)])
        client.get_observation()
        taken = client.last_stamp()
        taken["fresh"], taken["host"]["seq"] = False, 999
        self.assertEqual((client.last_stamp()["fresh"], client.last_stamp()["host"]["seq"]), (True, 1))

    def test_a_base_class_without_last_remote_state_falls_back_to_arrival(self):
        client, _ = make([message(0.1), None, message(0.2)], base=MinimalBase)
        fresh = []
        for _ in range(3):
            client.get_observation()
            fresh.append(client.last_stamp()["fresh"])
        self.assertEqual(fresh, [True, False, True])


class HostMetadata(unittest.TestCase):
    def test_captured_when_present(self):
        client, _ = make([stamped(41, session="synthetic-host-session-0001", t_mono_ns=2_000_000_000)])
        client.get_observation()
        stamp = client.last_stamp()
        self.assertEqual(stamp["host"], {"session": "synthetic-host-session-0001", "seq": 41, "sample_mono_ns": 2_000_000_000})
        self.assertEqual(stamp["availability"], "host_stamped")

    def test_absent_means_host_none(self):
        client, _ = make([message(0.1)])
        client.get_observation()
        stamp = client.last_stamp()
        self.assertIsNone(stamp["host"])
        self.assertEqual(stamp["availability"], "host_does_not_stamp")
        self.assertEqual(client.host_stamps_rejected, 0)

    def test_a_cached_read_keeps_the_host_stamp_of_the_sample_it_serves(self):
        client, _ = make([stamped(41), None])
        client.get_observation()
        client.get_observation()
        stamp = client.last_stamp()
        self.assertEqual((stamp["fresh"], stamp["host"]["seq"], stamp["availability"]), (False, 41, "host_stamped"))

    def test_malformed_values_are_tolerated(self):
        good_session = "synthetic-host-session-0001"
        malformed = {
            "seq is a string": {"kc.session": good_session, "kc.seq": "41"},
            "seq is negative": {"kc.session": good_session, "kc.seq": -1},
            "seq is a bool": {"kc.session": good_session, "kc.seq": True},
            "seq is a float": {"kc.session": good_session, "kc.seq": 41.0},
            "seq is null": {"kc.session": good_session, "kc.seq": None},
            "seq is missing": {"kc.session": good_session},
            "session is a number": {"kc.session": 7, "kc.seq": 41},
            "session is empty": {"kc.session": "", "kc.seq": 41},
            "session has a slash": {"kc.session": "a/b", "kc.seq": 41},
            "session has a newline": {"kc.session": good_session + "\n", "kc.seq": 41},
            "session is too long": {"kc.session": "s" * 65, "kc.seq": 41},
            "session is missing": {"kc.seq": 41},
            "everything is a container": {"kc.session": {"a": 1}, "kc.seq": [41], "kc.t_mono_ns": {"b": 2}},
        }
        for name, keys in malformed.items():
            with self.subTest(name):
                client, _ = make([message(0.25, **keys)])
                observation = client.get_observation()  # does not raise
                self.assertEqual(observation["x.vel"], 0.25, "the observation is served all the same")
                stamp = client.last_stamp()
                self.assertTrue(stamp["fresh"], "a bad stamp does not make a new message old")
                self.assertIsNone(stamp["host"], "a stamp that cannot identify the sample is no stamp")
                self.assertEqual(stamp["availability"], "host_does_not_stamp")
                self.assertEqual(client.host_stamps_rejected, 1, "and it is counted, so a host that stamps badly can be told apart")

    def test_a_malformed_sample_time_costs_the_time_and_keeps_the_identity(self):
        for bad_time in ("soon", -5, 1.5, True, None, [1]):
            with self.subTest(bad_time=bad_time):
                client, _ = make([message(0.1, **{"kc.session": "s-1", "kc.seq": 3, "kc.t_mono_ns": bad_time})])
                client.get_observation()
                self.assertEqual(client.last_stamp()["host"], {"session": "s-1", "seq": 3, "sample_mono_ns": None})
                self.assertEqual(client.host_stamps_rejected, 0)

    def test_parse_host_metadata_never_raises(self):
        for raw in (None, 5, b"\xff\xfe", "", "null", "[]", '"text"', "{", "[" * 100_000, '{"kc.seq": 1e999}', '{"kc.seq": NaN}'):
            with self.subTest(raw=str(raw)[:16]):
                is_object, host, _ = parse_host_metadata(raw)
                self.assertIsNone(host)
                self.assertIsInstance(is_object, bool)

    def test_a_host_seq_that_does_not_advance_is_not_fresh(self):
        client, _ = make([stamped(5, 0.1), stamped(5, 0.2), stamped(3, 0.3), stamped(4, 0.4), stamped(6, 0.5)])
        fresh, served = [], []
        for _ in range(5):
            served.append(client.get_observation()["x.vel"])
            fresh.append(client.last_stamp()["fresh"])
        # 4 comes after 3 and is still not news: the session has already shown 5.
        self.assertEqual(fresh, [True, False, False, False, True])
        self.assertEqual(served, [0.1, 0.2, 0.3, 0.4, 0.5], "reporting only: what is served is the base class's business")

    def test_a_new_host_session_starts_over(self):
        client, _ = make([stamped(900, session="session-a"), stamped(0, session="session-b"), stamped(0, session="session-b")])
        fresh = []
        for _ in range(3):
            client.get_observation()
            fresh.append(client.last_stamp()["fresh"])
        self.assertEqual(fresh, [True, True, False])


class RequireFresh(unittest.TestCase):
    def test_off_by_default(self):
        client, fake_time = make([message(0.1)])
        self.assertIs(client.require_fresh, False)
        client.get_observation()
        client.get_observation()  # cached, and served without a word
        self.assertEqual(fake_time.sleeps, [])

    def test_waits_and_succeeds_when_a_message_arrives_within_the_bound(self):
        client, fake_time = make([message(0.1), None, None, None, message(0.2)])
        client.set_require_fresh(True, max_wait_s=0.5)
        self.assertEqual(client.get_observation()["x.vel"], 0.1)
        self.assertEqual(fake_time.sleeps, [], "a fresh read does not wait")
        started_ns = fake_time.now_ns
        observation = client.get_observation()
        self.assertEqual(observation["x.vel"], 0.2, "the NEW sample, not the cached one")
        self.assertTrue(client.last_stamp()["fresh"])
        self.assertEqual(client.last_stamp()["local_rx_seq"], 2)
        self.assertEqual(len(fake_time.sleeps), 3, "three empty polls, then the message")
        self.assertLess(fake_time.now_ns - started_ns, 500_000_000)
        self.assertEqual(client.polls, 5, "the wait goes through the instance's own polling")

    def test_raises_when_nothing_arrives(self):
        client, fake_time = make([message(0.1)])
        client.set_require_fresh(True, max_wait_s=0.05)
        client.get_observation()
        started_ns = fake_time.now_ns
        with self.assertRaises(StaleSourceError) as raised:
            client.get_observation()
        self.assertIsInstance(raised.exception, RuntimeError)
        text = str(raised.exception)
        self.assertIn("went stale", text)
        self.assertIn("Nothing further should be commanded", text)
        self.assertEqual((raised.exception.stamp["fresh"], raised.exception.stamp["local_rx_seq"]), (False, 1))
        waited_ns = fake_time.now_ns - started_ns
        self.assertGreaterEqual(waited_ns, 50_000_000, "it waited the bound out")
        self.assertLess(waited_ns, 60_000_000, "and not much more")
        self.assertFalse(client.last_stamp()["fresh"])

    def test_raises_when_no_message_was_ever_received(self):
        client, _ = make([])
        client.set_require_fresh(True, max_wait_s=0.01)
        with self.assertRaises(StaleSourceError) as raised:
            client.get_observation()
        self.assertIn("no message has been received yet", str(raised.exception))
        self.assertEqual(raised.exception.stamp["availability"], "no_message_yet")

    def test_zero_wait_is_one_poll(self):
        client, fake_time = make([message(0.1), None, message(0.2)])
        client.set_require_fresh(True, max_wait_s=0)
        client.get_observation()
        with self.assertRaises(StaleSourceError):
            client.get_observation()
        self.assertEqual((client.polls, fake_time.sleeps), (2, []))

    def test_a_host_seq_that_does_not_advance_is_stale(self):
        client, _ = make([stamped(7, 0.1), stamped(7, 0.2), stamped(7, 0.3), stamped(8, 0.4)])
        client.set_require_fresh(True, max_wait_s=0.5)
        self.assertEqual(client.get_observation()["x.vel"], 0.1)
        observation = client.get_observation()
        self.assertEqual(observation["x.vel"], 0.4, "two 'new' messages with the old seq were passed over")
        self.assertEqual(client.last_stamp()["host"]["seq"], 8)

        client, _ = make([stamped(7, 0.1)] + [stamped(7, 0.2)] * 100)
        client.set_require_fresh(True, max_wait_s=0.05)
        client.get_observation()
        with self.assertRaises(StaleSourceError):
            client.get_observation()

    def test_a_message_the_base_class_could_not_use_does_not_end_the_wait(self):
        client, _ = make([message(0.1), NOT_JSON, UNPROCESSABLE, message(0.2)])
        client.set_require_fresh(True, max_wait_s=0.5)
        client.get_observation()
        self.assertEqual(client.get_observation()["x.vel"], 0.2)

    def test_it_can_be_switched_off_again(self):
        client, _ = make([message(0.1)])
        client.set_require_fresh(True, max_wait_s=0.01)
        client.get_observation()
        client.set_require_fresh(False)
        self.assertEqual(client.get_observation()["x.vel"], 0.1)
        self.assertFalse(client.last_stamp()["fresh"])

    def test_the_switch_takes_a_bool_and_a_finite_bound(self):
        client, _ = make([])
        for not_a_bool in ("false", 0, 1, None):
            with self.subTest(enabled=not_a_bool), self.assertRaises(TypeError):
                client.set_require_fresh(not_a_bool)
        for bad_bound in (-0.1, float("nan"), float("inf"), "0.5", None, True):
            with self.subTest(max_wait_s=bad_bound), self.assertRaises(ValueError):
                client.set_require_fresh(True, max_wait_s=bad_bound)
        self.assertIs(client.require_fresh, False, "a refused call changes nothing")


class BehaviorPreservation(unittest.TestCase):
    def test_every_scripted_case_returns_what_the_base_class_returns(self):
        for name, script in SCRIPTED_CASES.items():
            with self.subTest(name):
                plain = FakeBase(script)
                client, fake_time = make(script)
                for read in range(len(script) + 2):  # two reads past the end of the script as well
                    expected, returned = plain.get_observation(), client.get_observation()
                    self.assertEqual(returned, expected, f"read {read}")
                    self.assertEqual(list(returned), list(expected), f"read {read}: same keys in the same order")
                    self.assertIs(returned, client.last_remote_state, f"read {read}: the base class's own object, not a copy")
                self.assertEqual(client.polls, plain.polls, "and not one poll more")
                self.assertEqual(fake_time.sleeps, [])

    def test_the_host_keys_never_reach_the_observation(self):
        # The base class drops them (the fake mirrors the stock client in that); what this pins is that the adapter, which DID
        # read them, does not put them back.
        for name, script in SCRIPTED_CASES.items():
            with self.subTest(name):
                client, _ = make(script)
                for _ in range(len(script) + 1):
                    observation = client.get_observation()
                    self.assertEqual([key for key in observation if str(key).startswith("kc.")], [])
        client, _ = make([stamped(1), stamped(1), None, stamped(2)])
        client.set_require_fresh(True)
        for _ in range(2):
            self.assertEqual([key for key in client.get_observation() if key.startswith("kc.")], [])

    def test_the_subclass_is_a_subclass_and_passes_construction_through(self):
        cls = make_fresh_client_class(FakeBase)
        self.assertTrue(issubclass(cls, FakeBase))
        self.assertEqual(cls.__name__, "FreshFakeBase")
        self.assertEqual(cls(script=[None]).script, [None])
        for name in ("last_stamp", "to_source_stamp", "set_require_fresh", "require_fresh", "local_rx_session", "host_stamps_rejected"):
            self.assertFalse(hasattr(FakeBase, name), f"{name} would shadow something of the base class")


class ContractMapping(unittest.TestCase):
    def test_the_mirrored_constants_are_the_contracts(self):
        common = json.loads((CONTRACTS_DIR / "schema" / f"v{demo_contracts.CONTRACT_VERSION}" / "common.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(fc.SESSION_PATTERN, common["$defs"]["source_stamp"]["properties"]["session"]["pattern"])
        self.assertEqual(sorted(common["$defs"]["source_stamp"]["required"]), ["clock_domain", "sample_mono_ns", "seq", "session"])
        for domain in (fc.CLOCK_LAPTOP, fc.CLOCK_PI):
            self.assertIn(domain, common["$defs"]["clock_domain"]["enum"])

    def test_a_host_stamped_read_maps_to_the_hosts_own_stamp(self):
        client, _ = make([stamped(41)])
        client.get_observation()
        mapped = client.to_source_stamp()
        self.assertEqual(mapped, {"session": "synthetic-host-session-0001", "seq": 41, "sample_mono_ns": 2_000_000_000, "clock_domain": "pi_monotonic"})
        self.assertEqual(demo_contracts.validate("common#/$defs/source_stamp", mapped), [])

    def test_an_unstamped_read_maps_to_a_receiver_side_stamp_in_the_laptops_domain(self):
        client, fake_time = make([message(0.1), None])
        client.get_observation()
        mapped = client.to_source_stamp()
        self.assertEqual(mapped, {"session": client.local_rx_session, "seq": 1, "sample_mono_ns": fake_time.now_ns, "clock_domain": "laptop_monotonic"})
        self.assertEqual(demo_contracts.validate("common#/$defs/source_stamp", mapped), [])
        client.get_observation()
        self.assertEqual(client.to_source_stamp(), mapped, "a cached read carries the SAME stamp: that is how a reader of the record sees it")

    def test_a_host_stamp_without_a_sample_time_still_validates(self):
        client, _ = make([message(0.1, **{"kc.session": "s-1", "kc.seq": 3})])
        client.get_observation()
        mapped = client.to_source_stamp()
        self.assertIsNone(mapped["sample_mono_ns"])
        self.assertEqual(demo_contracts.validate("common#/$defs/source_stamp", mapped), [])

    def test_the_mapped_stamp_fits_a_measured_state_event(self):
        event = json.loads((CONTRACTS_DIR / "fixtures" / "event" / "measured.state.json").read_text(encoding="utf-8"))
        self.assertEqual(demo_contracts.validate("event", event), [], "the fixture itself")
        for script in ([stamped(41)], [message(0.1)]):
            client, _ = make(script)
            client.get_observation()
            event["payload"]["consumed_source_stamp"] = client.to_source_stamp()
            self.assertEqual(demo_contracts.validate("event", event), [])

    def test_source_stamp_from_is_a_pure_function_of_a_stored_stamp(self):
        stored = {"fresh": False, "local_rx_seq": 9, "local_rx_mono_ns": 123, "age_ns": 4, "host": None, "availability": "host_does_not_stamp"}
        self.assertEqual(source_stamp_from(stored, "laptop-rx-0123456789ab"), {"session": "laptop-rx-0123456789ab", "seq": 9, "sample_mono_ns": 123, "clock_domain": "laptop_monotonic"})

    def test_the_receive_session_is_minted_and_fits_the_contract(self):
        first, _ = make([])
        second, _ = make([])
        self.assertRegex(first.local_rx_session, r"^laptop-rx-[0-9a-f]{12}$")
        self.assertTrue(re.fullmatch(fc.SESSION_PATTERN, first.local_rx_session))
        self.assertNotEqual(first.local_rx_session, second.local_rx_session)


class DeferredImport(unittest.TestCase):
    def test_the_module_imports_nothing_but_the_standard_library(self):
        tree = ast.parse(Path(fc.__file__).read_text(encoding="utf-8"))
        top_level = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_level.add((node.module or "").split(".")[0])
        self.assertEqual(top_level - set(sys.stdlib_module_names), set(), "lerobot and zmq are not imported at module import")

    def test_fresh_client_builds_the_stock_class_the_way_the_scripts_do(self):
        built = {}

        class FakeConfig:
            def __init__(self, **kwargs):
                built.update(kwargs)

        class FakeLeKiwiClient(FakeBase):
            def __init__(self, config):
                super().__init__([message(0.1)])
                self.config = config

        lekiwi = types.ModuleType("lerobot.robots.lekiwi")
        lekiwi.LeKiwiClient, lekiwi.LeKiwiClientConfig = FakeLeKiwiClient, FakeConfig
        fakes = {"lerobot": types.ModuleType("lerobot"), "lerobot.robots": types.ModuleType("lerobot.robots"), "lerobot.robots.lekiwi": lekiwi}
        with mock.patch.dict(sys.modules, fakes):
            client = fc.fresh_client(DOCUMENTATION_ADDRESS, SYNTHETIC_ROBOT, polling_timeout_ms=30, require_fresh=True, max_wait_s=0.25)
        self.assertIsInstance(client, FakeLeKiwiClient)
        self.assertEqual(built, {"remote_ip": DOCUMENTATION_ADDRESS, "id": SYNTHETIC_ROBOT, "polling_timeout_ms": 30}, "the adapter's own options stay out of the config")
        self.assertIs(client.require_fresh, True)
        self.assertEqual(client.get_observation()["x.vel"], 0.1)
        self.assertTrue(client.last_stamp()["fresh"])
        self.assertNotIn(SYNTHETIC_ROBOT, client.local_rx_session)
        self.assertNotIn(DOCUMENTATION_ADDRESS, client.local_rx_session)

        with mock.patch.dict(sys.modules, fakes):
            self.assertIs(fc.fresh_client(DOCUMENTATION_ADDRESS, SYNTHETIC_ROBOT).require_fresh, False, "the refusal is off unless asked for")


if __name__ == "__main__":
    unittest.main()
