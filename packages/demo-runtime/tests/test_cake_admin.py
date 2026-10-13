# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The read-only Cake admin client: what it parses, what it refuses, and what it will not claim.

The sample below is the REAL output of a real cake-resident, recorded 2026-09-23 from a planless startup of the
x86-64 build. It is kept verbatim so a change to the parser is checked against what the tool actually prints.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_admin  # noqa: E402

REAL_STATUS = """status ok
replay 0
snapshot_present 1
build_identity d392ef91e3fbffd825c693665f6b49341efb02666a2a7d23731ed4764450ee33
session_uuid 01a0cd7e60ac7a1f8b7b0015e3229606
target_profile_digest 841c3e3f73e01e48ece200ce11385aa2dc4945df51de6bef6d1f8e8bd97d15b3
plan_digest c93aa621cf3d10522a9040ff0ac154f1e9729da6d7247976f1cb3d41aab6c6bc
epoch 0
slots 0
bindings 0
resources 0
config_identity 295f749e3eb386cdfddf94197dc7e3fd555678ea31e72296f80f969dedf73135
transaction_state none
flight_sequence_oldest 0
flight_sequence_next 2
"""

REAL_FLIGHT = """status ok
replay 0
snapshot_present 0
flight_records 3
record arg0=0 arg1=0 correlation=0 epoch=0 event=0x01000001 flags=0 sequence=0 severity=0 subject=0
record arg0=1000 arg1=1000 correlation=0 epoch=0 event=0x0b000001 flags=0 sequence=1 severity=0 subject=2052872
"""


class Parsing(unittest.TestCase):
    def test_it_reads_the_real_status_the_resident_printed(self):
        parsed = cake_admin.parse_probe_output(REAL_STATUS)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["fields"]["slots"], 0)
        self.assertEqual(parsed["fields"]["epoch"], 0)
        self.assertEqual(parsed["fields"]["transaction_state"], "none")
        self.assertEqual(parsed["fields"]["flight_sequence_next"], 2)

    def test_a_digest_is_kept_as_the_text_the_tool_printed(self):
        fields = cake_admin.parse_probe_output(REAL_STATUS)["fields"]
        self.assertEqual(fields["build_identity"], "d392ef91e3fbffd825c693665f6b49341efb02666a2a7d23731ed4764450ee33")
        self.assertIsInstance(fields["build_identity"], str, "a digest that was reformatted is not that digest")

    def test_flight_records_are_kept_whole_and_not_interpreted(self):
        parsed = cake_admin.parse_probe_output(REAL_FLIGHT)
        self.assertEqual(len(parsed["records"]), 2)
        self.assertIn("event=0x01000001", parsed["records"][0])

    def test_output_without_status_ok_is_not_ok(self):
        self.assertFalse(cake_admin.parse_probe_output("admin-probe: connect failed: No such file or directory")["ok"])
        self.assertFalse(cake_admin.parse_probe_output("")["ok"])


class Refusals(unittest.TestCase):
    def test_only_the_two_read_only_operations_are_allowed(self):
        for op in ("install", "activate", "swap", "rollback", "commit", ""):
            answer = cake_admin.run_probe("/bin/true", "/dev/null", op)
            self.assertEqual(answer["reason_code"], "operation_not_read_only", op)

    def test_the_probe_binary_must_be_an_absolute_executable_path(self):
        for path in ("admin-probe", "./admin-probe", "", "/nonexistent/admin-probe"):
            answer = cake_admin.run_probe(path, "/dev/null", "get-status")
            self.assertEqual(answer["reason_code"], "probe_binary_unavailable", path)

    def test_an_absent_socket_is_named_as_such_rather_than_run_into(self):
        answer = cake_admin.run_probe("/bin/true", "/nonexistent/admin.sock", "get-status")
        self.assertEqual(answer["reason_code"], "admin_socket_absent")


class WhatItWillNotClaim(unittest.TestCase):
    def test_an_unreachable_resident_yields_no_identity_and_no_participation(self):
        inventory = cake_admin.read_inventory("/nonexistent/admin-probe", "/nonexistent/a.sock")
        self.assertFalse(inventory["reachable"])
        self.assertEqual(inventory["identity"], {})
        self.assertFalse(inventory["mission_participation"])

    def test_participation_stays_false_even_for_a_resident_that_answered(self):
        # Reaching a resident says a resident is running. It says nothing about this robot's task.
        self.assertFalse(cake_admin.unavailable()["mission_participation"])

    def test_nothing_is_configured_unless_both_paths_are_named(self):
        class Config:
            def __init__(self, cake):
                self.cake = cake

        self.assertEqual(cake_admin.configured_paths(None), (None, None))
        self.assertEqual(cake_admin.configured_paths(Config(None)), (None, None))
        self.assertEqual(cake_admin.configured_paths(Config({})), (None, None))
        self.assertEqual(cake_admin.configured_paths(Config({"admin_probe_path": "/x"})), (None, None))
        self.assertEqual(cake_admin.configured_paths(Config({"admin_probe_path": "/x", "admin_socket_path": "/y"})), ("/x", "/y"))


class AgainstARealResident(unittest.TestCase):
    """Skipped unless KC_CAKE_PROBE and KC_CAKE_SOCKET name a running one."""

    def test_a_real_resident_answers_with_an_identity_and_a_slot_count(self):
        probe, socket_path = os.environ.get("KC_CAKE_PROBE"), os.environ.get("KC_CAKE_SOCKET")
        if not probe or not socket_path or not os.path.exists(socket_path):
            raise unittest.SkipTest("KC_CAKE_PROBE and KC_CAKE_SOCKET do not name a running resident")
        inventory = cake_admin.read_inventory(probe, socket_path)
        self.assertTrue(inventory["reachable"])
        self.assertIn("build_identity", inventory["identity"])
        self.assertIsInstance(inventory["counts"].get("slots"), int)
        self.assertFalse(inventory["mission_participation"])


if __name__ == "__main__":
    unittest.main()


class ConfigurationIsOptional(unittest.TestCase):
    """The section is optional and absent means what it meant before: this runtime reads no resident.

    The base document is built here rather than read from a deployment: a test that needs a private file to run is a
    test that does not run, and this file may not name one.
    """

    BASE = {
        "data_root": "/tmp/kc-config-test",
        "host": {"address": "203.0.113.10", "robot_id": "test_robot"},
        "pi_ssh_target": "operator@203.0.113.10",
        "cameras": {"wrist_id": "/dev/v4l/by-id/usb-test-wrist", "front_id": None},
        "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None},
        "observation_root": "observations",
        "pad_template_dir": "calibration/pad-template",
        "saved_pose_file": "calibration/scan-home-current.json",
        "preview": {"enabled": True, "wrist_port": 5571, "front_port": None},
        "tap": None,
        "capture_provider": "camera_service",
        "freshness_refusal_enabled": False,
    }

    def config(self, cake=None):
        from demo_runtime.config import RuntimeConfig

        raw = dict(self.BASE)
        if cake is not None:
            raw["cake"] = cake
        return RuntimeConfig.from_mapping(raw)

    def test_absent_leaves_the_runtime_unconnected(self):
        config = self.config()
        self.assertIsNone(config.cake)
        self.assertEqual(cake_admin.configured_paths(config), (None, None))

    def test_present_names_both_absolute_paths(self):
        config = self.config({"admin_probe_path": "/opt/cake/admin-probe", "admin_socket_path": "/run/kc/a.sock"})
        self.assertEqual(cake_admin.configured_paths(config), ("/opt/cake/admin-probe", "/run/kc/a.sock"))

    def test_a_relative_path_is_refused_rather_than_resolved(self):
        from demo_runtime.config import ConfigError

        for cake in ({"admin_probe_path": "admin-probe", "admin_socket_path": "/run/a.sock"},
                     {"admin_probe_path": "/opt/admin-probe", "admin_socket_path": "a.sock"}):
            with self.assertRaises(ConfigError):
                self.config(cake)

    def test_an_unknown_key_in_the_section_is_refused(self):
        from demo_runtime.config import ConfigError

        with self.assertRaises(ConfigError):
            self.config({"admin_probe_path": "/opt/p", "admin_socket_path": "/run/a.sock", "poll_interval_s": 1})
