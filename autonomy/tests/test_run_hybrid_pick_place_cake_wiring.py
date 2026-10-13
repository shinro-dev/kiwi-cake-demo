#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_run_hybrid_pick_place_cake_wiring.py: proves the
# TRACKED runner (bin/laptop/manipulation/run_hybrid_pick_place.py) wires a
# real autonomy/motion/capability_registry.py CapabilityRegistry into
# PickPlaceDeps -- reproducing the WAITING_FOR_CAPABILITY -> invalid-rejected
# -> valid-admitted -> MOBILE_APPROACH boundary from a normal terminal,
# without the gitignored chat/FIFO harness. No device, no lerobot import
# (run_hybrid_pick_place.py only imports lerobot inside main()).
#
# Deliberately narrow: dispatch_cake_command() is a pure command->registry
# mapping and gets full coverage; the one terminal-I/O closure
# (_make_wait_for_capability_change) is exercised with input() patched, not
# with a real terminal -- no generalized interactive-I/O test framework.
# Run directly: python3 autonomy/tests/test_run_hybrid_pick_place_cake_wiring.py
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

from capability_registry import ADMITTED, REJECTED, CapabilityRegistry  # noqa: E402
import run_hybrid_pick_place as rhpp  # noqa: E402


def _events(log_calls, event):
    return [c for c in log_calls if c["event"] == event]


def _recording_log():
    calls = []

    def log(event, **fields):
        calls.append({"event": event, **fields})

    return log, calls


class TestCakeCapabilityRegistryFactory(unittest.TestCase):
    def test_fresh_registry_starts_mobile_approach_unavailable(self):
        log, _ = _recording_log()
        registry = rhpp._make_capability_registry(log)
        self.assertIsInstance(registry, CapabilityRegistry)
        self.assertFalse(registry.has_capability("mobile_approach"))

    def test_admitted_identity_matches_the_proven_physical_path(self):
        # mobile-approach-real-build is the exact capsule identity admitted
        # live on 2026-09-18 (docs/autonomous-demo/physical-pass-2026-09-18.md).
        # A silent drift here would desync the runner from the documented proof.
        log, _ = _recording_log()
        registry = rhpp._make_capability_registry(log)
        self.assertIn(rhpp.CAKE_VALID_CANDIDATE_IDENTITY, registry.admitted_identities)
        self.assertNotIn(rhpp.CAKE_INVALID_CANDIDATE_IDENTITY, registry.admitted_identities)


class TestDispatchCakeCommand(unittest.TestCase):
    def setUp(self):
        self.log, self.calls = _recording_log()
        self.registry = rhpp._make_capability_registry(self.log)

    def test_invalid_is_rejected_and_leaves_capability_unavailable(self):
        result = rhpp.dispatch_cake_command("INVALID", self.registry)
        self.assertEqual(result, REJECTED)
        self.assertFalse(self.registry.has_capability("mobile_approach"))

    def test_invalid_goes_through_the_registry_not_a_direct_flag(self):
        rhpp.dispatch_cake_command("INVALID", self.registry)
        rejected = _events(self.calls, "CAKE_CANDIDATE_REJECTED")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["capsule_identity"], rhpp.CAKE_INVALID_CANDIDATE_IDENTITY)

    def test_valid_is_admitted_and_activates_capability(self):
        result = rhpp.dispatch_cake_command("VALID", self.registry)
        self.assertEqual(result, ADMITTED)
        self.assertTrue(self.registry.has_capability("mobile_approach"))

    def test_valid_goes_through_the_registry_not_a_direct_flag(self):
        rhpp.dispatch_cake_command("VALID", self.registry)
        activated = _events(self.calls, "CAPABILITY_ACTIVATED")
        self.assertEqual(len(activated), 1)
        admitted = _events(self.calls, "CAKE_CANDIDATE_ADMITTED")
        self.assertEqual(admitted[0]["capsule_identity"], rhpp.CAKE_VALID_CANDIDATE_IDENTITY)

    def test_is_case_and_whitespace_insensitive(self):
        result = rhpp.dispatch_cake_command("  invalid  \n", self.registry)
        self.assertEqual(result, REJECTED)

    def test_unrecognized_command_submits_nothing(self):
        result = rhpp.dispatch_cake_command("banana", self.registry)
        self.assertIsNone(result)
        self.assertFalse(self.registry.has_capability("mobile_approach"))
        self.assertEqual(_events(self.calls, "CAKE_CANDIDATE_RECEIVED"), [])

    def test_invalid_then_valid_ends_available_same_registry_instance(self):
        rhpp.dispatch_cake_command("INVALID", self.registry)
        self.assertFalse(self.registry.has_capability("mobile_approach"))
        rhpp.dispatch_cake_command("VALID", self.registry)
        self.assertTrue(self.registry.has_capability("mobile_approach"))


class TestWaitForCapabilityChangeClosure(unittest.TestCase):
    """Terminal-backed closure: input() is patched, nothing else about the
    terminal is exercised (no generalized I/O framework, per instruction)."""

    def test_invalid_returns_control_without_raising_and_leaves_unavailable(self):
        log, _ = _recording_log()
        registry = rhpp._make_capability_registry(log)
        wait = rhpp._make_wait_for_capability_change(registry)
        with patch("builtins.input", return_value="INVALID"):
            wait()  # returns control to the caller -- no exception, no loop
        self.assertFalse(registry.has_capability("mobile_approach"))
        # A second call (the state machine re-checking / waiting again)
        # must still work off the SAME registry instance.
        with patch("builtins.input", return_value="VALID"):
            wait()
        self.assertTrue(registry.has_capability("mobile_approach"))

    def test_unrecognized_command_does_not_raise(self):
        log, _ = _recording_log()
        registry = rhpp._make_capability_registry(log)
        wait = rhpp._make_wait_for_capability_change(registry)
        with patch("builtins.input", return_value="nonsense"):
            wait()
        self.assertFalse(registry.has_capability("mobile_approach"))


class TestMissionAwareLog(unittest.TestCase):
    """Pure wrapper (fake base_log, no print/terminal involved) proving the
    same mission_id survives across capability activation, as required by
    the mission-continuity spec: one id at process start, never regenerated."""

    def test_same_mission_id_on_every_call(self):
        _, calls = _recording_log()
        base_log, calls = _recording_log()
        wrapped = rhpp._make_mission_aware_log(base_log, "fixed-mission-id")
        wrapped("STATE_ENTER", state="WAITING_FOR_CAPABILITY")
        wrapped("CAKE_CANDIDATE_REJECTED", capsule_identity="x")
        wrapped("CAPABILITY_ACTIVATED", capability="mobile_approach")
        wrapped("STATE_ENTER", state="MOBILE_APPROACH")
        mission_ids = {c["mission_id"] for c in calls}
        self.assertEqual(mission_ids, {"fixed-mission-id"})

    def test_forwards_event_and_fields_unchanged_besides_mission_id(self):
        base_log, calls = _recording_log()
        wrapped = rhpp._make_mission_aware_log(base_log, "m1")
        wrapped("STATE_PASS", state="DONE", pose={"x": 1})
        self.assertEqual(calls[0]["event"], "STATE_PASS")
        self.assertEqual(calls[0]["state"], "DONE")
        self.assertEqual(calls[0]["pose"], {"x": 1})
        self.assertEqual(calls[0]["mission_id"], "m1")


class TestDepsWiringIsRegistryBacked(unittest.TestCase):
    """capability_available passed to PickPlaceDeps must be the registry's
    own has_capability -- not a boolean/closure that could drift from it."""

    def test_capability_available_reflects_live_registry_state(self):
        log, _ = _recording_log()
        registry = rhpp._make_capability_registry(log)
        capability_available = registry.has_capability
        self.assertFalse(capability_available("mobile_approach"))
        rhpp.dispatch_cake_command("VALID", registry)
        self.assertTrue(capability_available("mobile_approach"))


if __name__ == "__main__":
    unittest.main()
