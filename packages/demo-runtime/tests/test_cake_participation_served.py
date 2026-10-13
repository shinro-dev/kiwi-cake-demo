#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_cake_participation_served.py: mission_participation, in the snapshot's cake ladder
# and in the cake_status surface, is cake_admin.mission_participation of the newest mission view (track C's rule), read
# with the configured capability's name. A runtime with no mission surface keeps the sentence it had.
# Run directly: python3 packages/demo-runtime/tests/test_cake_participation_served.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(PACKAGE_DIR.parents[1]))

from demo_runtime import cake_admin, read_models  # noqa: E402

import demo_contracts  # noqa: E402  (importable once demo_runtime was imported)
from tests.test_mission_ops import Core, Deployment  # noqa: E402

APPLIED_VIEW = {"mission_id": "mission-0123456789ab", "activations": [],
                "targets": [{"target_id": "cube", "evidence": {"capability": {"generation": 3, "pid": 4242, "applied": 3, "accepted": 3}}}]}


def ladder_rung(core) -> dict:
    return core.ask("snapshot")["cake"]["ladder"]["mission_participation"]


def status_finding(core) -> dict:
    return core.ask("cake_status")["mission_participation"]


class TheReadModels(unittest.TestCase):
    def test_a_participation_given_is_the_rung_and_the_status_field(self):
        finding = cake_admin.mission_participation(APPLIED_VIEW, module_name="mobile_approach")
        for probe in ({}, {"scope": "robot_resident", "inventory": {"reachable": False}}):
            with self.subTest(scope=probe.get("scope")):
                cake = read_models.cake_view(probe, participation=finding)
                self.assertEqual(cake["ladder"]["mission_participation"], {"value": True, "evidence": finding["evidence"]})
                self.assertEqual([], demo_contracts.validate("cake", cake))
        status = read_models.cake_status_view({}, now_mono_ns=0, participation=finding)
        self.assertEqual(status["mission_participation"], {"value": True, "evidence": finding["evidence"]})

    def test_without_one_the_rung_is_what_it_was(self):
        self.assertEqual(read_models.cake_view({})["ladder"]["mission_participation"], {"value": False, "evidence": "the controller has no Cake adapter"})
        self.assertIs(read_models.cake_status_view({}, now_mono_ns=0)["mission_participation"]["value"], False)


class TheDaemonServesTheMissionRecord(unittest.TestCase):
    def test_with_no_mission_recorded_it_says_so(self):
        deployment = Deployment(self)
        self.addCleanup(deployment.cleanup)
        core = Core(self, config=deployment.config())
        expected = cake_admin.mission_participation(None, module_name="mobile_approach")["evidence"]
        self.assertEqual(ladder_rung(core), {"value": False, "evidence": expected})
        self.assertEqual(status_finding(core), {"value": False, "evidence": expected})

    def test_a_mission_whose_record_shows_applied_base_commands_takes_part(self):
        deployment = Deployment(self)
        self.addCleanup(deployment.cleanup)
        core = Core(self, config=deployment.config())
        core.core.mission.state = lambda mission_id=None: dict(APPLIED_VIEW)
        expected = cake_admin.mission_participation(APPLIED_VIEW, module_name="mobile_approach")
        self.assertIn("mobile_approach", expected["evidence"])
        self.assertEqual(ladder_rung(core), {"value": True, "evidence": expected["evidence"]})
        self.assertEqual(status_finding(core), {"value": True, "evidence": expected["evidence"]})

    def test_a_runtime_with_no_mission_surface_keeps_its_sentence(self):
        deployment = Deployment(self)
        self.addCleanup(deployment.cleanup)
        core = Core(self, config=deployment.config(cake=False))
        self.assertEqual(ladder_rung(core), {"value": False, "evidence": "the controller has no Cake adapter"})


if __name__ == "__main__":
    unittest.main()
