#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_cake_refusal.py: a `status mutation_refused` answer is Cake JUDGING the candidate
# before any transaction was opened, never a request that failed to land.
#
# The answer shapes are the ones admin-probe prints (`status <name>`, then `refusal_code N` only on a refusal), and the
# refusal classes are the resident's own numbering: 1 to 5 the admin surface's payload-shape refusals, 100 to 106 the
# runtime's (100 is a candidate whose bytes do not read as a Plan artifact whose digests hold: an INTEGRITY refusal),
# 110 plus a class for a refused rollback. No resident and no socket here.
# Run directly: python3 packages/demo-runtime/tests/test_cake_refusal.py
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_activation  # noqa: E402

TAMPERED = """status mutation_refused
replay 0
snapshot_present 0
refusal_code 100
"""

SHAPE = "status mutation_refused\nreplay 0\nsnapshot_present 0\nrefusal_code 2\n"
NO_CODE = "status mutation_refused\nreplay 0\nsnapshot_present 0\n"


class ARefusalBeforeATransactionIsCakeJudging(unittest.TestCase):
    def test_an_integrity_refusal_is_refused_before_transaction(self):
        answer = cake_activation.parse_activation_answer(TAMPERED)
        self.assertEqual(answer["outcome"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(answer["reason_code"], "100")
        self.assertIsNone(answer["transaction_phase"])
        self.assertIsNone(answer["transaction_uuid"])
        self.assertEqual(answer["refusal"]["kind"], "integrity")
        self.assertEqual(answer["refusal"]["token"], "plan-unreadable")

    def test_it_is_never_called_a_failed_request_or_a_rejected_transaction(self):
        answer = cake_activation.parse_activation_answer(TAMPERED)
        self.assertNotEqual(answer["outcome"], cake_activation.REQUEST_FAILED)
        self.assertNotEqual(answer["outcome"], cake_activation.REJECTED_BY_CAKE)

    def test_an_integrity_refusal_is_never_worded_as_a_signature_refusal(self):
        """A flipped byte breaks the artifact's digests. Nothing about it is a signature, and the words say so."""
        for text in (TAMPERED,):
            answer = cake_activation.parse_activation_answer(text)
            words = (answer["detail"] + " " + answer["refusal"]["words"]).lower()
            self.assertNotIn("signature", words)
            self.assertNotIn("signed", words)
            self.assertIn("integrity", words)
            self.assertIn("no transaction", words)

    def test_a_payload_shape_refusal_is_refused_before_transaction_with_its_class(self):
        answer = cake_activation.parse_activation_answer(SHAPE)
        self.assertEqual(answer["outcome"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(answer["refusal"]["token"], "payload_missing")
        self.assertEqual(answer["refusal"]["kind"], "request_shape")

    def test_a_refused_rollback_carries_its_own_class(self):
        answer = cake_activation.parse_activation_answer("status mutation_refused\nrefusal_code 113\n")
        self.assertEqual(answer["outcome"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(answer["refusal"]["token"], "rollback-refused")
        self.assertEqual(answer["refusal"]["rollback_class"], 3)

    def test_an_unnamed_code_is_still_a_refusal_and_says_it_is_unnamed(self):
        answer = cake_activation.parse_activation_answer("status mutation_refused\nrefusal_code 77\n")
        self.assertEqual(answer["outcome"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertIsNone(answer["refusal"]["token"])
        self.assertEqual(answer["refusal"]["kind"], "unclassified")

    def test_a_refusal_that_names_no_code_did_not_say_what_it_judged(self):
        """Without its class the answer is not read as Cake's judgement: it stays a request that did not land."""
        answer = cake_activation.parse_activation_answer(NO_CODE)
        self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)

    def test_other_request_statuses_stay_request_failures(self):
        for status in ("operation_identity_mismatch", "unavailable", "operation_in_flight", "precondition_conflict"):
            with self.subTest(status=status):
                answer = cake_activation.parse_activation_answer(f"status {status}\nrefusal_code 2\n")
                self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)

    def test_a_malformed_code_is_not_a_class(self):
        answer = cake_activation.parse_activation_answer("status mutation_refused\nrefusal_code 1x\n")
        self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)

    def test_the_offline_reader_reads_a_refusal_the_same_way(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        motion = os.path.join(root, "autonomy", "motion")
        if not os.path.isdir(motion):
            raise unittest.SkipTest("autonomy/motion is not in this tree")
        sys.path.insert(0, motion)
        import cake_capability_adapter  # noqa: E402

        self.assertEqual(cake_capability_adapter.REFUSED_BEFORE_TRANSACTION, cake_activation.REFUSED_BEFORE_TRANSACTION)
        for text in (TAMPERED, SHAPE, NO_CODE, "status mutation_refused\nrefusal_code 1x\n"):
            mine = cake_activation.parse_activation_answer(text)
            theirs = cake_capability_adapter.parse_activation_answer(text)
            self.assertEqual(mine["outcome"], theirs["outcome"], text)
            self.assertEqual(mine["reason_code"], theirs["reason_code"], text)


if __name__ == "__main__":
    unittest.main()
