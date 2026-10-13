#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_cake_participation.py: whether a Cake module took part in a mission, DERIVED FROM
# THE MISSION'S OWN RECORD, never from an admin socket and never a constant.
#
# The two placed missions of 2026-09-26 are the reference, by their numbers only: each carried one target, `cube`,
# placed in BASE_ADVANCE with the capability evidence {generation 2, pid 43289, accepted = applied = 27} and
# {generation 2, pid 43289, accepted = applied = 20}, and recorded no activation. Both took Cake's mobile_approach
# module into the task: every base command the relay accepted was applied by the motion gateway.
# Run directly: python3 packages/demo-runtime/tests/test_cake_participation.py
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_admin  # noqa: E402


def mission_view(capability, *, state="COMPLETE", target_state="PLACED", activations=()):
    evidence = None if capability is False else {"run_id": "console-20260926T201540Z-mb3c1048", "outcome": "placed_new",
                                                 "mode": "BASE_ADVANCE", "capability": capability}
    return {"mission_id": "mission-88094cda64e1", "state": state, "activations": list(activations),
            "targets": [{"target_id": "cube", "state": target_state, "requires_capability": "auto", "evidence": evidence}]}


START_1 = {"accepted": 27, "applied": 27, "dropped": {}, "generation": 2, "pid": 43289}
START_2 = {"accepted": 20, "applied": 20, "dropped": {}, "generation": 2, "pid": 43289}


class ParticipationIsReadFromTheRecord(unittest.TestCase):
    def test_both_placed_missions_of_the_day_took_part_with_the_module_named(self):
        for capability, applied in ((START_1, 27), (START_2, 20)):
            with self.subTest(applied=applied):
                answer = cake_admin.mission_participation(mission_view(capability), module_name="mobile_approach")
                self.assertIs(answer["value"], True)
                self.assertEqual(answer["basis"], cake_admin.PARTICIPATION_APPLIED)
                self.assertEqual((answer["module"], answer["generation"], answer["pid"], answer["applied"]),
                                 ("mobile_approach", 2, 43289, applied))
                self.assertEqual(answer["target_id"], "cube")
                self.assertIn("mobile_approach", answer["evidence"])
                self.assertIn("generation 2", answer["evidence"])
                self.assertIn("pid 43289", answer["evidence"])
                self.assertIn(f"{applied} ", answer["evidence"])

    def test_nothing_applied_is_not_participation(self):
        answer = cake_admin.mission_participation(mission_view({**START_1, "applied": 0}), module_name="mobile_approach")
        self.assertIs(answer["value"], False)

    def test_a_capability_block_without_its_generation_or_pid_is_not_bound_to_a_module(self):
        for broken in ({**START_1, "pid": None}, {**START_1, "generation": "2"}, {**START_1, "applied": True}):
            with self.subTest(broken=broken):
                self.assertIs(cake_admin.mission_participation(mission_view(broken))["value"], False)

    def test_an_observed_activation_is_participation(self):
        view = mission_view(None, activations=[{"activation_state": "REJECTED_BY_CAKE", "candidate_key": "abort"},
                                               {"activation_state": "OBSERVED", "candidate_key": "mobile-demo-swapped"}])
        answer = cake_admin.mission_participation(view, module_name="mobile_approach")
        self.assertIs(answer["value"], True)
        self.assertEqual(answer["basis"], cake_admin.PARTICIPATION_OBSERVED)
        self.assertIn("mobile-demo-swapped", answer["evidence"])

    def test_an_activation_that_was_not_observed_is_not_participation(self):
        view = mission_view(None, activations=[{"activation_state": "COMPLETED_NOT_OBSERVED", "candidate_key": "x"}])
        self.assertIs(cake_admin.mission_participation(view)["value"], False)

    def test_no_mission_and_a_mission_with_no_such_record_say_false_with_why(self):
        none = cake_admin.mission_participation(None)
        self.assertIs(none["value"], False)
        self.assertIn("no mission", none["evidence"])
        arm_only = cake_admin.mission_participation(mission_view(False))
        self.assertIs(arm_only["value"], False)
        self.assertIsNone(arm_only["basis"])
        self.assertTrue(arm_only["evidence"])

    def test_without_a_module_name_it_says_the_capability_module(self):
        answer = cake_admin.mission_participation(mission_view(START_2))
        self.assertIs(answer["value"], True)
        self.assertIsNone(answer["module"])
        self.assertIn("capability module", answer["evidence"])

    def test_the_inventory_still_says_false_on_its_own(self):
        """An admin socket cannot answer it: the inventory keeps its constant, and the mission surface answers it."""
        self.assertIs(cake_admin.unavailable()["mission_participation"], False)


if __name__ == "__main__":
    unittest.main()
