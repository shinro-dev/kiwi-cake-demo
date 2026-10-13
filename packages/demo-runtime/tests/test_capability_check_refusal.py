#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_capability_check_refusal.py: the capability slot check records a refusal before any
# transaction as exactly that, with its class, and the slot it read before and after as unchanged.
# Driven directly against cake_check_fakes.ScriptedCheck: no daemon, no resident, no socket.
# Run directly: python3 packages/demo-runtime/tests/test_capability_check_refusal.py
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cake_check_fakes import INERT_CAPSULE, ScriptedCheck, slot  # noqa: E402

from demo_runtime import cake_activation, mission  # noqa: E402

TAMPERED_ANSWER = "status mutation_refused\nreplay 0\nsnapshot_present 0\nrefusal_code 100\n"


class TheTamperedTwinIsRefusedBeforeAnyTransaction(unittest.TestCase):
    def setUp(self):
        before = slot(INERT_CAPSULE, 1, 1, active=False)
        self.scripted = ScriptedCheck(TAMPERED_ANSWER, [before, dict(before)])
        self.addCleanup(self.scripted.close)

    def test_the_answer_is_refused_before_transaction_with_its_class(self):
        answer = self.scripted.apply("tampered")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["activation_state"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(answer["outcome"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(answer["refusal"]["kind"], "integrity")
        self.assertEqual(answer["refusal"]["code"], "100")
        self.assertIn("before any transaction", answer["detail"])
        self.assertIs(answer["slot_changed"], False)
        self.assertFalse(answer["unresolved"])

    def test_it_is_never_recorded_as_a_failed_request(self):
        answer = self.scripted.apply("tampered")
        self.assertNotEqual(answer["activation_state"], mission.ACTIVATION_FAILED)
        self.assertNotEqual(answer["activation_state"], mission.ACTIVATION_REJECTED)

    def test_the_record_keeps_the_refusal(self):
        self.scripted.apply("tampered")
        checks, _dropped, _damaged = self.scripted.check.checks()
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["activation_state"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(checks[0]["refusal"]["token"], "plan-unreadable")
        self.assertIs(checks[0]["slot_changed"], False)

    def test_a_refusal_does_not_hold_the_next_check_closed(self):
        self.scripted.apply("tampered")
        self.assertIsNone(self.scripted.check.pending_operation)


if __name__ == "__main__":
    unittest.main()
