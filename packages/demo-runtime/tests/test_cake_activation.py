# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The mutating Cake client: what it will submit, what it refuses to be given, and the three outcomes it keeps apart.

No resident and no socket. The answers below are the real shape `admin-probe` prints, including the one that matters
most: an ABORTED transaction arriving under `status ok`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_activation  # noqa: E402

COMPLETED = """status ok
replay 0
snapshot_present 0
transaction_uuid 22398968749b77310d8643700bfb61d3
transaction_handle 911829390961147908
transaction_phase COMPLETED
"""

ABORTED = """status ok
replay 0
snapshot_present 0
transaction_uuid 57688d24e10913aa9ab0fe08f6b4720b
transaction_handle 911829390961147906
transaction_phase ABORTED
"""

# The resident's answer when the SAME operation uuid arrives carrying different bytes. It is a request-level
# refusal: there is no transaction, and Cake judged no candidate.
IDENTITY_MISMATCH = "status operation_identity_mismatch\n"

RUNNING = """status ok
transaction_uuid 22398968749b77310d8643700bfb61d3
transaction_phase RUNNING
"""


class WhatAnAnswerMeans(unittest.TestCase):
    def test_a_completed_transaction_is_accepted(self):
        answer = cake_activation.parse_activation_answer(COMPLETED)
        self.assertEqual(answer["outcome"], cake_activation.ACCEPTED)
        self.assertEqual(answer["transaction_phase"], "COMPLETED")

    def test_an_aborted_transaction_arrives_under_status_ok_and_is_a_rejection(self):
        """`status ok` DOES NOT MEAN THE UPDATE HAPPENED. A reader that took status for the outcome would call
        this a success."""
        answer = cake_activation.parse_activation_answer(ABORTED)
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(answer["outcome"], cake_activation.REJECTED_BY_CAKE)
        self.assertEqual(answer["detail"], cake_activation.REJECTION_DETAIL)

    def test_the_rejection_detail_states_only_what_the_answer_carries(self):
        """AN ABORT CARRIES NO CAUSE. The detail used to say the candidate was not a capsule this resident
        admitted, and the handoff (section 8) recorded an admitted candidate aborting once the resident had been up
        longer than 30 s, so that cause can be false. The detail says what is known: the resident answered ABORTED,
        the transaction did not complete, and the answer does not say why."""
        detail = cake_activation.parse_activation_answer(ABORTED)["detail"]
        self.assertIn("ABORTED", detail)
        self.assertIn("did not complete", detail)
        self.assertIn("does not say why", detail)
        for cause in ("capsule", "admitted", "not a", "because"):
            self.assertNotIn(cause, detail.lower())

    def test_the_rejection_detail_never_says_upload_signature_or_corruption(self):
        detail = cake_activation.parse_activation_answer(ABORTED)["detail"].lower()
        for wrong in ("upload", "signature", "corrupt"):
            self.assertNotIn(wrong, detail)

    def test_an_identity_mismatch_is_a_request_failure_and_not_cake_rejecting_anything(self):
        answer = cake_activation.parse_activation_answer(IDENTITY_MISMATCH)
        self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)
        self.assertIsNone(answer["transaction_phase"])
        self.assertNotEqual(answer["outcome"], cake_activation.REJECTED_BY_CAKE)

    def test_a_transaction_still_running_is_pending_and_not_a_verdict(self):
        answer = cake_activation.parse_activation_answer(RUNNING)
        self.assertEqual(answer["outcome"], cake_activation.PENDING)
        self.assertEqual(answer["detail"], "RUNNING")

    def test_an_answer_with_no_phase_is_a_request_failure(self):
        answer = cake_activation.parse_activation_answer("status ok\nreplay 0\n")
        self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)

    def test_this_reading_matches_the_offline_readers(self):
        """demo_runtime does not import autonomy, so the two readings are kept equal by a test rather than by an
        import. If one of them changes, this fails and the other is updated deliberately."""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        motion = os.path.join(root, "autonomy", "motion")
        if not os.path.isdir(motion):
            raise unittest.SkipTest("autonomy/motion is not in this tree, so there is no second reader to compare")
        sys.path.insert(0, motion)
        import cake_capability_adapter  # noqa: E402

        for text in (COMPLETED, ABORTED, IDENTITY_MISMATCH, RUNNING, "status ok\n"):
            mine = cake_activation.parse_activation_answer(text)
            theirs = cake_capability_adapter.parse_activation_answer(text)
            self.assertEqual(mine["outcome"], theirs["outcome"], text)
            self.assertEqual(mine["transaction_phase"], theirs["transaction_phase"], text)
            self.assertEqual(mine["status"], theirs["status"], text)


class OnlyAServerPreparedCandidateIsActivatable(unittest.TestCase):
    """A caller names a KEY. It never supplies bytes, a path, a socket or an operation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.good = os.path.join(self.tmp.name, "swapped.cakepln")
        with open(self.good, "wb") as handle:
            handle.write(b"prepared candidate bytes")
        self.empty = os.path.join(self.tmp.name, "empty.cakepln")
        open(self.empty, "wb").close()

    def test_a_prepared_candidate_is_offered_by_key_and_digest_only(self):
        prepared = cake_activation.PreparedCandidates({"mobile_approach.activate": self.good})
        self.assertEqual(prepared.keys(), ["mobile_approach.activate"])
        view = prepared.view()
        self.assertEqual(view[0]["key"], "mobile_approach.activate")
        self.assertTrue(view[0]["payload_digest"].startswith("sha256:"))
        # THE VIEW CARRIES NO PATH AND NO BYTES. A console renders this, and neither belongs on a screen.
        self.assertEqual(set(view[0]), {"key", "payload_digest"})

    def test_an_unprepared_key_has_no_payload(self):
        prepared = cake_activation.PreparedCandidates({"mobile_approach.activate": self.good})
        self.assertIsNone(prepared.payload("anything.else"))
        self.assertIsNone(prepared.digest("anything.else"))

    def test_a_relative_path_is_refused_without_reading_anything(self):
        prepared = cake_activation.PreparedCandidates({"relative": "cake/plans/swapped.cakepln"})
        self.assertEqual(prepared.keys(), [])
        self.assertEqual(prepared.problem("relative"), "candidate_path_not_absolute")

    def test_an_unreadable_candidate_is_reported_without_naming_its_path(self):
        prepared = cake_activation.PreparedCandidates({"gone": os.path.join(self.tmp.name, "absent.cakepln")})
        self.assertEqual(prepared.keys(), [])
        self.assertEqual(prepared.problem("gone"), "candidate_unreadable")

    def test_an_empty_candidate_is_refused(self):
        prepared = cake_activation.PreparedCandidates({"empty": self.empty})
        self.assertEqual(prepared.keys(), [])
        self.assertEqual(prepared.problem("empty"), "candidate_empty")

    def test_the_digest_binds_the_bytes_to_the_operation(self):
        prepared = cake_activation.PreparedCandidates({"a": self.good})
        self.assertEqual(prepared.digest("a"), cake_activation.digest_of(b"prepared candidate bytes"))


class TheRequestItselfCanFail(unittest.TestCase):
    """A request that never reached a resident is REQUEST_FAILED, which is not Cake rejecting a candidate."""

    def test_an_absent_probe_binary_is_a_request_failure(self):
        answer = cake_activation.submit_apply_plan("/nonexistent/admin-probe", "/nonexistent.sock",
                                                   cake_activation.new_operation_uuid(), b"bytes")
        self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)
        self.assertEqual(answer["reason_code"], "probe_binary_unavailable")

    def test_a_relative_probe_path_is_refused(self):
        answer = cake_activation.submit_apply_plan("admin-probe", "/nonexistent.sock",
                                                   cake_activation.new_operation_uuid(), b"bytes")
        self.assertEqual(answer["reason_code"], "probe_binary_unavailable")

    def test_an_empty_payload_is_never_submitted(self):
        answer = cake_activation.submit_apply_plan("/bin/sh", "/nonexistent.sock",
                                                   cake_activation.new_operation_uuid(), b"")
        self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)

    def test_a_fresh_operation_uuid_is_fresh(self):
        self.assertNotEqual(cake_activation.new_operation_uuid(), cake_activation.new_operation_uuid())
        self.assertEqual(len(cake_activation.new_operation_uuid()), 32)


class TheReadClientStaysReadOnly(unittest.TestCase):
    def test_list_slots_and_get_health_are_reads_and_apply_plan_is_not_among_them(self):
        from demo_runtime import cake_admin
        self.assertIn("list-slots", cake_admin.READ_OPERATIONS)
        self.assertIn("get-health", cake_admin.READ_OPERATIONS)
        self.assertNotIn("apply-plan", cake_admin.READ_OPERATIONS)
        self.assertNotIn("replace-slot", cake_admin.READ_OPERATIONS)

    def test_the_read_client_still_refuses_every_mutation(self):
        from demo_runtime import cake_admin
        for operation in ("apply-plan", "replace-slot", "admit-capsule"):
            answer = cake_admin.run_probe("/bin/sh", "/nonexistent.sock", operation)
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["reason_code"], "operation_not_read_only")

    def test_a_failed_slot_read_is_an_empty_string_and_not_an_empty_slot_table(self):
        self.assertEqual(cake_activation.read_slots_text("/nonexistent/admin-probe", "/nonexistent.sock"), "")


if __name__ == "__main__":
    unittest.main()
