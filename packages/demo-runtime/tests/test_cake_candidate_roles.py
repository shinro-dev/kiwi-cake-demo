#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_cake_candidate_roles.py: every prepared candidate can carry a ROLE from the
# configuration (activate, refused-demo, tampered-demo, undo) and the outcome that role expects, so a screen labels each
# button by what it is for and never preselects one. Without roles the view is exactly what it was: key and digest.
# Run directly: python3 packages/demo-runtime/tests/test_cake_candidate_roles.py
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_activation  # noqa: E402


class CandidateRoles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = {}
        for key in ("mobile-demo-swapped", "mobile-demo-active-successor-abort-swap", "mobile-demo-swapped-tampered",
                    "mobile-demo-inert"):
            path = os.path.join(self.tmp.name, key + ".cakepln")
            with open(path, "wb") as handle:
                handle.write(key.encode("ascii"))
            self.paths[key] = path
        self.roles = {"mobile-demo-swapped": "activate", "mobile-demo-active-successor-abort-swap": "refused-demo",
                      "mobile-demo-swapped-tampered": "tampered-demo", "mobile-demo-inert": {"role": "undo", "label": "Undo"}}

    def test_without_roles_the_view_is_key_and_digest_only(self):
        view = cake_activation.PreparedCandidates(self.paths).view()
        self.assertEqual([sorted(entry) for entry in view], [["key", "payload_digest"]] * 4)

    def test_each_role_carries_its_label_and_expected_outcome(self):
        view = {entry["key"]: entry for entry in cake_activation.PreparedCandidates(self.paths, roles=self.roles).view()}
        self.assertEqual(sorted(view["mobile-demo-swapped"]), ["expect", "key", "label", "payload_digest", "role"])
        self.assertEqual(view["mobile-demo-swapped"]["role"], "activate")
        self.assertEqual(view["mobile-demo-swapped"]["expect"], "OBSERVED")
        self.assertEqual(view["mobile-demo-active-successor-abort-swap"]["expect"], "REJECTED_BY_CAKE")
        self.assertEqual(view["mobile-demo-swapped-tampered"]["expect"], cake_activation.REFUSED_BEFORE_TRANSACTION)
        self.assertEqual(view["mobile-demo-inert"]["expect"], "COMPLETED_NOT_OBSERVED")
        self.assertEqual(view["mobile-demo-inert"]["label"], "Undo")
        for entry in view.values():
            self.assertTrue(entry["label"])

    def test_the_tampered_label_says_integrity_and_never_signature(self):
        label = cake_activation.ROLE_LABELS["tampered-demo"].lower()
        self.assertIn("integrity", label)
        self.assertNotIn("signature", label)

    def test_the_abort_swap_label_says_transactional_refusal(self):
        self.assertIn("abort", cake_activation.ROLE_LABELS["refused-demo"].lower())

    def test_a_key_without_a_role_says_none(self):
        roles = {"mobile-demo-swapped": "activate"}
        view = {entry["key"]: entry for entry in cake_activation.PreparedCandidates(self.paths, roles=roles).view()}
        self.assertIsNone(view["mobile-demo-inert"]["role"])
        self.assertIsNone(view["mobile-demo-inert"]["expect"])
        self.assertEqual(view["mobile-demo-inert"]["label"], "mobile-demo-inert")

    def test_the_primary_candidate_is_the_activate_role_and_nothing_else(self):
        self.assertEqual(cake_activation.PreparedCandidates(self.paths, roles=self.roles).primary(), "mobile-demo-swapped")
        self.assertIsNone(cake_activation.PreparedCandidates(self.paths).primary(), "no role, no primary: nothing is guessed")

    def test_a_malformed_role_table_is_refused_by_name(self):
        cases = {
            "candidate_role_unknown": {"mobile-demo-swapped": "deploy"},
            "candidate_role_key_not_prepared": {"not-prepared": "activate"},
            "candidate_role_repeated": {"mobile-demo-swapped": "activate", "mobile-demo-inert": "activate"},
            "candidate_role_label_not_allowed": {"mobile-demo-swapped": {"role": "activate", "label": "x" * 81}},
            "candidate_roles_not_a_table": ["activate"],
        }
        for reason, roles in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(cake_activation.roles_refusal(roles, sorted(self.paths)), reason)
                with self.assertRaises(ValueError) as raised:
                    cake_activation.PreparedCandidates(self.paths, roles=roles)
                self.assertEqual(str(raised.exception), reason)
        self.assertIsNone(cake_activation.roles_refusal(self.roles, sorted(self.paths)))


if __name__ == "__main__":
    unittest.main()
