#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_activation_refused_before_transaction.py: the activation verdict for an answer Cake gave
# BEFORE any transaction existed (status mutation_refused with its refusal code, outcome REFUSED_BEFORE_TRANSACTION in
# cake_activation's vocabulary). It is Cake judging the candidate, so it is never a failed request, never a rejection of a
# transaction that ran, and never in flight. Pure function and one coordinator case; no resident, no robot.
# Run directly: python3 packages/demo-runtime/tests/test_activation_refused_before_transaction.py
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo_runtime import cake_activation, mission, mission_coordinator  # noqa: E402
from demo_runtime.mission_coordinator import CapabilityAdapter, MissionCoordinator, activation_verdict  # noqa: E402
from tests.test_mission_coordinator import FakeCandidates, recording_execution  # noqa: E402

REFUSAL = {"outcome": "REFUSED_BEFORE_TRANSACTION", "status": "mutation_refused", "transaction_phase": None,
           "transaction_uuid": None, "reason_code": "plan_integrity_invalid", "detail": None}


class TheVerdict(unittest.TestCase):
    def test_the_outcome_word_is_cake_activations_when_it_has_one(self):
        self.assertEqual(mission_coordinator.REFUSED_BEFORE_TRANSACTION_OUTCOME, "REFUSED_BEFORE_TRANSACTION")
        if hasattr(cake_activation, "REFUSED_BEFORE_TRANSACTION"):
            self.assertEqual(cake_activation.REFUSED_BEFORE_TRANSACTION, mission_coordinator.REFUSED_BEFORE_TRANSACTION_OUTCOME)

    def test_a_refusal_before_any_transaction_is_its_own_verdict_naming_the_refusal_code(self):
        asked = []
        state, detail = activation_verdict(dict(REFUSAL), None, lambda: asked.append(1) or True)
        self.assertEqual(state, mission.ACTIVATION_REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(state, "REFUSED_BEFORE_TRANSACTION")
        self.assertIn("plan_integrity_invalid", detail)
        self.assertEqual(asked, [], "a refusal is not decided by reading the slot")
        self.assertNotIn(state, (mission.ACTIVATION_FAILED, mission.ACTIVATION_REJECTED, mission.ACTIVATION_SUBMITTED))

    def test_the_answers_own_detail_is_kept_when_it_has_one(self):
        state, detail = activation_verdict(dict(REFUSAL, detail="the plan's integrity check failed"), None, lambda: False)
        self.assertEqual(detail, "the plan's integrity check failed")

    def test_the_other_outcomes_are_unchanged(self):
        self.assertEqual(activation_verdict({"outcome": cake_activation.REQUEST_FAILED, "detail": "x"}, None, lambda: False)[0],
                         mission.ACTIVATION_FAILED)
        self.assertEqual(activation_verdict({"outcome": cake_activation.REJECTED_BY_CAKE, "detail": "x"}, None, lambda: False)[0],
                         mission.ACTIVATION_REJECTED)


class TheMissionRecordsIt(unittest.TestCase):
    def test_a_refused_activation_ends_the_operation_and_leaves_the_mission_waiting(self):
        tmp = tempfile.mkdtemp(prefix="kc-refused-before-")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        release = threading.Event()
        capability = CapabilityAdapter(available=lambda: release.is_set(), submit=lambda uuid, payload: dict(REFUSAL),
                                       observe=lambda: False, candidates=FakeCandidates())

        def waits(execution, capability_available, wait_for_capability_change, target):
            while not capability_available("mobile_approach"):
                wait_for_capability_change()
            return {"target_state": mission.TARGET_PLACED, "detail": None, "run_id": "run-1"}

        coordinator = MissionCoordinator(f"{tmp}/{mission.MISSIONS_FILE_NAME}", recording_execution(), capability,
                                         data_mode="synthetic", run_target=waits)
        self.addCleanup(coordinator.close)
        started = coordinator.start([{"target_id": "far", "label": "far", "reachable": False, "requires_capability": True}])
        for _ in range(200):
            if (coordinator.state(started["mission_id"]) or {}).get("waiting_reason") == mission.WAITING_ON_CAPABILITY:
                break
            threading.Event().wait(0.02)
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_REFUSED_BEFORE_TRANSACTION)
        view = coordinator.state(started["mission_id"])
        self.assertEqual(view["state"], mission.MISSION_WAITING)
        self.assertEqual(view["activations"][0]["activation_state"], "REFUSED_BEFORE_TRANSACTION")
        second = coordinator.submit_activation("mobile_approach.activate")
        self.assertNotEqual(second.get("reason_code"), mission_coordinator.ACTIVATION_IN_FLIGHT, "a refusal is not in flight")
        release.set()
        self.assertTrue(coordinator.join(10.0))


if __name__ == "__main__":
    unittest.main()
