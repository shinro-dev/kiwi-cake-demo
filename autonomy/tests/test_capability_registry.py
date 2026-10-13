#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_capability_registry.py: proves the software-only
# capability provider seam (Path A semantics from
# docs/autonomous-demo/cake-capability-assessment.md) -- no Cake, no
# hardware.
# Run directly: python3 autonomy/tests/test_capability_registry.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

from capability_registry import ADMITTED, REJECTED, CapabilityRegistry, Candidate  # noqa: E402


class TestCapabilityRegistry(unittest.TestCase):
    def test_capability_starts_unavailable(self):
        reg = CapabilityRegistry(admitted_identities=frozenset({"real-build"}))
        self.assertFalse(reg.has_capability("mobile_approach"))

    def test_unadmitted_candidate_is_rejected_and_stays_unavailable(self):
        events = []
        reg = CapabilityRegistry(admitted_identities=frozenset({"real-build"}), log=lambda e, **f: events.append((e, f)))
        result = reg.submit_candidate("mobile_approach", Candidate(capsule_identity="unadmitted-build"))
        self.assertEqual(result, REJECTED)
        self.assertFalse(reg.has_capability("mobile_approach"))
        event_names = [e for e, _ in events]
        self.assertEqual(event_names, ["CAKE_CANDIDATE_RECEIVED", "CAKE_CANDIDATE_REJECTED"])
        self.assertIn("unadmitted", events[1][1]["reason"])

    def test_admitted_candidate_activates_the_capability(self):
        events = []
        reg = CapabilityRegistry(admitted_identities=frozenset({"real-build"}), log=lambda e, **f: events.append((e, f)))
        result = reg.submit_candidate("mobile_approach", Candidate(capsule_identity="real-build"))
        self.assertEqual(result, ADMITTED)
        self.assertTrue(reg.has_capability("mobile_approach"))
        event_names = [e for e, _ in events]
        self.assertEqual(event_names, ["CAKE_CANDIDATE_RECEIVED", "CAKE_CANDIDATE_ADMITTED", "CAPABILITY_ACTIVATED"])

    def test_rejection_never_activates_a_different_capability(self):
        reg = CapabilityRegistry(admitted_identities=frozenset({"real-build"}))
        reg.submit_candidate("mobile_approach", Candidate(capsule_identity="unadmitted-build"))
        self.assertFalse(reg.has_capability("mobile_approach"))
        self.assertFalse(reg.has_capability("anything_else"))


if __name__ == "__main__":
    unittest.main()
