# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The `cake_status` surface: what it reports, what it retains, and the three things it refuses to say.

This surface is versioned APART from the console contract v1 and is carried by no snapshot. The tests here pin the
three properties the surface exists for:

  - a resident that stops answering leaves its last reading behind, marked stale, instead of becoming an inventory of
    zeroes, and instead of continuing to read as current;
  - a resident that restarts is surfaced as a different session rather than as a reachability flicker;
  - a count is never read as execution status, and nothing derives this robot's task from an admin socket.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_admin, read_models  # noqa: E402

import demo_contracts  # noqa: E402

NOW = 1_000_000_000_000


def inventory(*, reachable=True, session="01a0cdba8c9c7b09b03c1f945f489104", slots=0, mono_ns=NOW,
              wall="2026-09-23T10:27:24.757Z", reason=None) -> dict:
    """A reading shaped exactly like the one cake_admin returns, so these tests cannot pass against a shape it does not produce."""
    if not reachable:
        return {"reachable": False, "reason_code": reason or "admin_socket_absent", "identity": {}, "counts": {}, "flight": None,
                "mission_participation": False, "observed_wall_utc": wall, "observed_mono_ns": mono_ns,
                "clock_domain": cake_admin.CLOCK_DOMAIN}
    return {
        "reachable": True, "reason_code": None,
        "identity": {"session_uuid": session, "build_identity": "d392ef91e3fbffd825c693665f6b49341efb02666a2a7d23731ed4764450ee33",
                     "config_identity": "8ba283b86a3a96739585078756373b069cd94483c322674c2cd6ed2ac4f655c7",
                     "target_profile_digest": "841c3e3f73e01e48ece200ce11385aa2dc4945df51de6bef6d1f8e8bd97d15b3",
                     "plan_digest": "c93aa621cf3d10522a9040ff0ac154f1e9729da6d7247976f1cb3d41aab6c6bc"},
        "counts": {"epoch": 0, "slots": slots, "bindings": 0, "resources": 0, "replay": 0, "snapshot_present": 1},
        "transaction_state": "none", "flight": {"records": 3, "oldest": 0, "next": 2},
        "mission_participation": False, "observed_wall_utc": wall, "observed_mono_ns": mono_ns,
        "clock_domain": cake_admin.CLOCK_DOMAIN,
    }


def view(reading=None, *, retained=None, session_changed=None, configured=True, now_ns=NOW):
    probe = {"artifacts": False, "resident": bool(reading and reading.get("reachable")), "admin_socket": False,
             "cake_configured": configured, "cake_retained": retained, "cake_session_changed": session_changed}
    if reading is not None:
        probe["inventory"] = reading
    return read_models.cake_status_view(probe, now_mono_ns=now_ns)


class ItMatchesItsOwnSchema(unittest.TestCase):
    def test_every_state_this_surface_can_be_in_validates(self):
        states = {
            "not configured": view(None, configured=False),
            "configured and reachable": view(inventory()),
            "configured and never reached": view(inventory(reachable=False)),
            "stale": view(inventory(reachable=False), retained=inventory()),
            "restarted": view(inventory(session="01a0cd7e60ac7a1f8b7b0015e3229606"), session_changed=True),
        }
        for name, state in states.items():
            with self.subTest(state=name):
                self.assertEqual(demo_contracts.validate_cake_status(state), [])

    def test_the_version_is_this_surface_s_own_and_not_the_contract_s(self):
        self.assertEqual(view(inventory())["cake_status_version"], demo_contracts.CAKE_STATUS_VERSION)
        self.assertNotIn("contract_version", view(inventory()))


class LosingContact(unittest.TestCase):
    """The defect this surface exists to prevent: a resident that went away reading as one that holds nothing."""

    def test_a_lost_resident_keeps_its_last_reading_and_is_marked_stale(self):
        answer = view(inventory(reachable=False), retained=inventory(slots=2))
        self.assertFalse(answer["reachable"])
        self.assertTrue(answer["stale"])
        self.assertIsNotNone(answer["observation"], "a lost resident must not empty the inventory")
        self.assertEqual(answer["observation"]["counts"]["slots"], 2, "the retained count is what was last read, not zero")
        self.assertEqual(answer["reason_code"], "admin_socket_absent")

    def test_a_runtime_that_never_reached_one_reports_no_observation_rather_than_zeroes(self):
        answer = view(inventory(reachable=False))
        self.assertIsNone(answer["observation"], "never having read is not the same as having read zeroes")
        self.assertFalse(answer["stale"], "nothing was retained, so nothing is stale")
        self.assertIsNone(answer["activity"]["value"])

    def test_a_current_reading_is_never_marked_stale(self):
        answer = view(inventory(), retained=inventory())
        self.assertFalse(answer["stale"])
        self.assertIsNone(answer["reason_code"])

    def test_the_age_is_measured_from_when_the_read_was_taken(self):
        """A retained observation must READ as old. The age comes from the stamp the reading carries, so a cached or
        retained answer cannot present itself as fresh."""
        answer = view(inventory(reachable=False), retained=inventory(mono_ns=NOW), now_ns=NOW + 30_000_000_000)
        self.assertEqual(answer["observation"]["age_ms"], 30000.0)
        self.assertEqual(answer["observation"]["wall_utc"], "2026-09-23T10:27:24.757Z")

    def test_an_age_is_never_negative(self):
        self.assertEqual(view(inventory(mono_ns=NOW), now_ns=NOW - 5_000_000)["observation"]["age_ms"], 0.0)


class ARestart(unittest.TestCase):
    def test_a_new_session_is_surfaced_rather_than_inferred_from_reachability(self):
        retention = read_models.CakeRetention()
        retention.fold(inventory(session="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
        self.assertIsNone(retention.session_changed(), "one answer is not two, so nothing is established yet")
        retention.fold(inventory(session="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
        self.assertIs(retention.session_changed(), False, "the same session answering twice is not a restart")
        retention.fold(inventory(session="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))
        self.assertIs(retention.session_changed(), True)

    def test_losing_contact_alone_is_not_a_restart(self):
        retention = read_models.CakeRetention()
        retention.fold(inventory())
        retention.fold(inventory(reachable=False))
        retention.fold(inventory())
        self.assertIs(retention.session_changed(), False)

    def test_the_change_stays_reported_after_the_poll_that_spanned_it(self):
        """A browser polls every few seconds and a restart is instantaneous. A flag true for one poll would be missed."""
        retention = read_models.CakeRetention()
        retention.fold(inventory(session="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
        retention.fold(inventory(session="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))
        for _ in range(5):
            retention.fold(inventory(session="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))
        self.assertIs(retention.session_changed(), True)

    def test_a_failed_read_retains_nothing_and_replaces_nothing(self):
        retention = read_models.CakeRetention()
        retention.fold(inventory(slots=4))
        retention.fold(inventory(reachable=False))
        self.assertEqual(retention.last_good()["counts"]["slots"], 4)
        retention.fold(None)
        retention.fold({"reachable": False})
        self.assertEqual(retention.last_good()["counts"]["slots"], 4)


class WhatItWillNotSay(unittest.TestCase):
    def test_a_slot_count_is_never_read_as_execution_status(self):
        answer = view(inventory(slots=3))
        self.assertIsNone(answer["activity"]["value"], "a positive slot count establishes nothing about what is running")
        self.assertIn("not execution status", answer["activity"]["evidence"])

    def test_zero_slots_is_an_answer_and_not_a_failed_read(self):
        answer = view(inventory(slots=0))
        self.assertIs(answer["activity"]["value"], False)
        self.assertIn("not a failed read", answer["activity"]["evidence"])

    def test_participation_is_false_and_is_not_derived_from_the_resident(self):
        for reading in (inventory(), inventory(slots=9), inventory(reachable=False)):
            with self.subTest(slots=reading.get("counts")):
                self.assertIs(view(reading)["mission_participation"]["value"], False)
        self.assertIn("no Cake adapter", view(inventory())["mission_participation"]["evidence"])

    def test_an_unconfigured_runtime_says_so_rather_than_reporting_a_refusal(self):
        answer = view(None, configured=False)
        self.assertFalse(answer["configured"])
        self.assertEqual(answer["reason_code"], "not_configured")
        self.assertIsNone(answer["observation"])

    def test_a_value_the_resident_did_not_print_as_a_digest_is_reported_absent(self):
        """A digest that was reformatted is not the digest the tool reported, and a path is not a digest at all."""
        reading = inventory()
        reading["identity"]["build_identity"] = "not-a-digest"
        answer = view(reading)
        self.assertIsNone(answer["observation"]["resident"]["build_identity"])
        self.assertEqual(demo_contracts.validate_cake_status(answer), [])


class OnePollerOnly(unittest.TestCase):
    def test_the_retention_wrapper_returns_the_probe_result_unchanged(self):
        """The snapshot's ladder reads the same cached value. Folding must not edit it."""
        from demo_runtime import daemon

        original = {"artifacts": False, "resident": True, "admin_socket": True, "inventory": inventory()}
        core = daemon.RuntimeCore.__new__(daemon.RuntimeCore)
        core.cake_retention = read_models.CakeRetention()
        folding = daemon.RuntimeCore._folding_cake_probe(core, lambda: original)
        self.assertIs(folding(), original)
        self.assertEqual(core.cake_retention.last_good()["identity"]["session_uuid"], inventory()["identity"]["session_uuid"])

    def test_a_probe_without_an_inventory_folds_nothing_and_does_not_raise(self):
        from demo_runtime import daemon

        core = daemon.RuntimeCore.__new__(daemon.RuntimeCore)
        core.cake_retention = read_models.CakeRetention()
        folding = daemon.RuntimeCore._folding_cake_probe(core, lambda: {"artifacts": False, "resident": False, "admin_socket": False})
        self.assertEqual(folding(), {"artifacts": False, "resident": False, "admin_socket": False})
        self.assertIsNone(core.cake_retention.last_good())


class TheStampTravelsWithTheReading(unittest.TestCase):
    def test_every_reading_cake_admin_produces_carries_when_it_was_taken(self):
        """Stamped where the read happens, because a stamp taken where an answer is assembled reports the read-back
        time and makes a cached answer look current."""
        for reading in (cake_admin.unavailable(), cake_admin.read_inventory("/nonexistent/admin-probe", "/nonexistent.sock")):
            with self.subTest(reason=reading["reason_code"]):
                self.assertIsInstance(reading["observed_mono_ns"], int)
                self.assertRegex(reading["observed_wall_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
                self.assertEqual(reading["clock_domain"], "laptop_monotonic")


if __name__ == "__main__":
    unittest.main()
