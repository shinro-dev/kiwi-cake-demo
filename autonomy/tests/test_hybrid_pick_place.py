#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_hybrid_pick_place.py: proves the hybrid pick-place
# state machine against FAKE hardware callbacks -- no device, no lerobot
# import, matching the autonomy/manipulation/*.py pure-module boundary.
# Real coverage of the underlying Cartesian math lives in
# test_cartesian_lift.py; this file proves the STATE MACHINE (sequencing,
# preconditions, abort handling, fresh-state discipline), reusing the real
# cartesian_lift functions (not faked) so the singularity self-refusal test
# below exercises the actual solver against the actual pose that triggered
# it live on 2026-09-18.
# Run directly: python3 autonomy/tests/test_hybrid_pick_place.py
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

from capability_registry import ADMITTED, REJECTED, CapabilityRegistry, Candidate  # noqa: E402
from hybrid_pick_place import (  # noqa: E402
    MOBILE_APPROACH_CAPABILITY,
    STATE_WAITING_FOR_CAPABILITY,
    _Ctx,
    _state_place_approach,
    STATE_ABORTED,
    STATE_BASE_STOP_VERIFY,
    STATE_CLEARANCE_LIFT,
    STATE_CLEARANCE_VERIFY,
    STATE_DONE,
    STATE_GRASP_CLOSE,
    STATE_GRASP_POSITION,
    STATE_GRASP_VERIFY,
    STATE_MOBILE_APPROACH,
    STATE_MOBILE_READY,
    STATE_PLACE_APPROACH,
    STATE_PLACE_READY,
    STATE_PRECHECK,
    STATE_RELEASE,
    STATE_RETREAT,
    GRIPPER_LATCH,
    GRIPPER_OPEN,
    KNOWN_GOOD_GRASP_POSE_DEG,
    PickPlaceDeps,
    run_hybrid_pick_place,
)

JOINT_NAMES = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


class FakeRig:
    """Minimal in-memory stand-in for the robot: tracks one pose dict and
    base velocity, updated only through the same calls the real wiring
    script would make. move_arm applies overrides and freezes everything
    else, exactly like move_to_target.py's actual contract."""

    def __init__(self, start_pose: dict):
        self.pose = dict(start_pose)
        self.base = {"x_vel": 0.0, "y_vel": 0.0, "theta_vel": 0.0}
        self.move_arm_calls = []
        self.move_base_calls = []
        self.base_stop_confirmed = True

    def read_pose(self):
        return dict(self.pose)

    def read_base(self):
        return dict(self.base)

    def move_arm(self, overrides, duration_s, hold_s=0.0):
        self.move_arm_calls.append({"overrides": dict(overrides), "duration_s": duration_s, "hold_s": hold_s})
        self.pose.update(overrides)
        return dict(self.pose)

    def move_base_sustained(self, vx_mps, duration_s):
        self.move_base_calls.append({"vx_mps": vx_mps, "duration_s": duration_s})
        return self.base_stop_confirmed


def _default_pose():
    return {**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": -97.011, "gripper": GRIPPER_OPEN}


def _make_deps(rig, answers, events=None):
    answers = iter(answers)
    events = events if events is not None else []

    def confirm(prompt):
        events.append({"event": "PROMPT", "prompt": prompt})
        try:
            answer = next(answers)
        except StopIteration:
            raise AssertionError(f"test ran out of scripted answers at prompt: {prompt!r}")
        events.append({"event": "ANSWER", "prompt": prompt, "answer": answer})
        return answer

    def log(event, **fields):
        events.append({"event": event, **fields})

    return PickPlaceDeps(
        read_pose=rig.read_pose,
        read_base=rig.read_base,
        move_arm=rig.move_arm,
        move_base_sustained=rig.move_base_sustained,
        confirm=confirm,
        log=log,
    ), events


class TestHappyPath(unittest.TestCase):
    def test_full_chain_reaches_done_with_expected_state_sequence(self):
        rig = FakeRig(_default_pose())
        deps, events = _make_deps(
            rig,
            answers=[
                "CUBE READY",  # GRASP_CLOSE: operator placed the cube
                "YES",  # GRASP_VERIFY
                "YES",  # CLEARANCE_VERIFY (first +20mm increment already enough)
                "EXTEND 40 20",  # PLACE_APPROACH: forward+down in one step
                "YES",  # PLACE_READY / release-ready confirmation
                "SUCCESS",  # post-release confirmation
            ],
        )
        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_DONE)
        self.assertTrue(result.object_secured)
        self.assertTrue(result.end_effector_clearance_confirmed)
        self.assertTrue(result.placement_ready)
        self.assertIsNone(result.abort_reason)

        entered = [e["state"] for e in events if e["event"] == "STATE_ENTER"]
        expected_order = [
            STATE_PRECHECK,
            STATE_GRASP_POSITION,
            STATE_GRASP_CLOSE,
            STATE_GRASP_VERIFY,
            STATE_CLEARANCE_LIFT,
            STATE_CLEARANCE_VERIFY,
            STATE_MOBILE_READY,
            STATE_MOBILE_APPROACH,
            STATE_BASE_STOP_VERIFY,
            STATE_PLACE_APPROACH,
            STATE_PLACE_READY,
            STATE_RELEASE,
            STATE_RETREAT,
        ]
        self.assertEqual(entered, expected_order)
        self.assertIn("DONE", [e["event"] for e in events])

    def test_grasp_close_and_release_use_the_exact_proven_gripper_values(self):
        rig = FakeRig(_default_pose())
        deps, _ = _make_deps(rig, answers=["CUBE READY", "YES", "YES", "EXTEND 40 20", "YES", "SUCCESS"])
        run_hybrid_pick_place(deps)

        gripper_close_call = rig.move_arm_calls[1]  # GRASP_CLOSE is the 2nd move_arm call
        self.assertEqual(gripper_close_call["overrides"], {"gripper": GRIPPER_LATCH})

        release_call = next(c for c in rig.move_arm_calls if c["overrides"].get("gripper") == GRIPPER_OPEN)
        self.assertEqual(release_call["overrides"], {"gripper": GRIPPER_OPEN})

    def test_wrist_roll_is_never_included_in_any_move_arm_overrides(self):
        rig = FakeRig(_default_pose())
        deps, _ = _make_deps(rig, answers=["CUBE READY", "YES", "YES", "EXTEND 40 20", "YES", "SUCCESS"])
        run_hybrid_pick_place(deps)
        for call in rig.move_arm_calls:
            self.assertNotIn("wrist_roll", call["overrides"])

    def test_shoulder_pan_is_never_included_in_cartesian_move_overrides(self):
        # pan is held by cartesian_lift's own design (FIXED_LIFT_JOINTS); the
        # state machine must never re-send it as an override either. Only
        # GRASP_POSITION (the very first move) legitimately sets pan, since
        # that establishes the fixed plane every later Cartesian step relies on.
        rig = FakeRig(_default_pose())
        deps, _ = _make_deps(rig, answers=["CUBE READY", "YES", "YES", "EXTEND 40 20", "YES", "SUCCESS"])
        run_hybrid_pick_place(deps)
        for call in rig.move_arm_calls[1:]:
            self.assertNotIn("shoulder_pan", call["overrides"])


class TestPreconditions(unittest.TestCase):
    def test_grasp_not_confirmed_aborts_before_any_mobility(self):
        rig = FakeRig(_default_pose())
        deps, events = _make_deps(rig, answers=["CUBE READY", "NO"])
        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertFalse(result.object_secured)
        self.assertIn("GRASP_VERIFY", result.abort_reason)
        self.assertEqual(rig.move_base_calls, [])

    def test_mobile_approach_never_runs_without_confirmed_clearance(self):
        rig = FakeRig(_default_pose())
        # NO on clearance every time, forcing the clearance cap to trip.
        deps, events = _make_deps(rig, answers=["CUBE READY", "YES", "NO", "NO", "NO"])
        result = run_hybrid_pick_place(deps, max_clearance_steps=3)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertFalse(result.end_effector_clearance_confirmed)
        self.assertEqual(rig.move_base_calls, [], "MOBILE_APPROACH must never be reached without confirmed clearance")

    def test_release_never_runs_without_placement_ready(self):
        rig = FakeRig(_default_pose())
        deps, events = _make_deps(rig, answers=["CUBE READY", "YES", "YES", "EXTEND 40 20", "NO"])
        result = run_hybrid_pick_place(deps, max_place_steps=1)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertFalse(result.placement_ready)
        release_calls = [c for c in rig.move_arm_calls if c["overrides"].get("gripper") == GRIPPER_OPEN]
        self.assertEqual(release_calls, [], "RELEASE must never fire without a PLACE_READY confirmation")

    def test_operator_abort_at_any_gate_stops_the_chain_immediately(self):
        rig = FakeRig(_default_pose())
        deps, _ = _make_deps(rig, answers=["CUBE READY", "YES", "ABORT"])
        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertEqual(rig.move_base_calls, [])

    def test_base_stop_not_confirmed_blocks_placement(self):
        rig = FakeRig(_default_pose())
        rig.base_stop_confirmed = False
        deps, _ = _make_deps(rig, answers=["CUBE READY", "YES", "YES"])
        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertIn("confirmed full stop", result.abort_reason)
        self.assertFalse(result.base_stationary)
        # No placement-phase or release arm move should have been sent once
        # the sustained move didn't report a confirmed full stop.
        self.assertTrue(all(c["overrides"].get("gripper") != GRIPPER_OPEN for c in rig.move_arm_calls))

    def test_gripper_never_closes_without_operator_confirming_cube_ready(self):
        # Regression for a real packaging gap: GRASP_CLOSE used to fire
        # immediately after GRASP_POSITION with no gate, unlike the proven
        # live procedure (PLACE CUBE BETWEEN JAWS -> wait for CUBE READY).
        rig = FakeRig(_default_pose())
        deps, _ = _make_deps(rig, answers=["ABORT"])
        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertIn(STATE_GRASP_CLOSE, result.abort_reason)
        close_calls = [c for c in rig.move_arm_calls if c["overrides"].get("gripper") == GRIPPER_LATCH]
        self.assertEqual(close_calls, [], "gripper must never close without an explicit CUBE READY confirmation")


class TestClosedLoopFreshState(unittest.TestCase):
    def test_clearance_lift_reads_fresh_state_every_iteration_not_a_stale_delta(self):
        rig = FakeRig(_default_pose())
        # move_arm_calls[0]=GRASP_POSITION, [1]=GRASP_CLOSE, [2]=GRASP_VERIFY's
        # verification lift, [3:6]=the three CLEARANCE_LIFT increments below.
        deps, _ = _make_deps(
            rig, answers=["CUBE READY", "YES", "PARTIAL", "PARTIAL", "YES", "EXTEND 40 20", "YES", "SUCCESS"]
        )
        result = run_hybrid_pick_place(deps, max_clearance_steps=3)

        self.assertNotEqual(result.final_state, STATE_ABORTED, result.abort_reason)
        clearance_moves = rig.move_arm_calls[3:6]
        self.assertEqual(len(clearance_moves), 3)
        # Every clearance move's target must differ (fresh solve from an updated pose), not repeat the exact same delta twice.
        lifts = [c["overrides"]["shoulder_lift"] for c in clearance_moves]
        self.assertEqual(len(set(lifts)), len(lifts), "each increment must be solved from the fresh post-move pose, not reuse a stale delta")

    def test_max_clearance_steps_caps_the_loop_instead_of_looping_forever(self):
        rig = FakeRig(_default_pose())
        deps, _ = _make_deps(rig, answers=["CUBE READY", "YES"] + ["PARTIAL"] * 10)
        result = run_hybrid_pick_place(deps, max_clearance_steps=3)
        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertFalse(result.end_effector_clearance_confirmed)


class TestSingularitySelfRefusal(unittest.TestCase):
    """Reproduces the live 2026-09-18 incident: a 100mm-forward/45mm-down
    placement request from a specific measured pose solves to
    elbow_flex~5.6deg (inside the singularity margin). The state machine
    must refuse to actuate that step -- not silently clamp it, not
    auto-shrink it -- and report why, leaving the operator to choose a
    smaller step next. Uses the REAL cartesian_lift solver (not a fake)."""

    def _pose_from_incident(self):
        return {
            "shoulder_pan": -10.285714285714286,
            "shoulder_lift": 14.54945054945055,
            "elbow_flex": 68.08791208791209,
            "wrist_flex": -5.230769230769231,
            "wrist_roll": -97.01098901098901,
            "gripper": GRIPPER_LATCH,
        }

    def test_unsafe_large_placement_target_is_refused_not_actuated(self):
        # Directly exercises PLACE_APPROACH from the exact incident pose
        # (bypassing GRASP_POSITION's fixed override, which would otherwise
        # erase it) -- the precise regression the live run hit.
        rig = FakeRig(self._pose_from_incident())
        deps, _ = _make_deps(rig, answers=["EXTEND 100 45"])
        ctx = _Ctx(object_secured=True, end_effector_clearance_confirmed=True, arm_frozen=True, base_stationary=True, fresh_observation_available=True)

        next_state = _state_place_approach(deps, ctx, move_duration_s=2.0, step_count=1, max_place_steps=6)

        self.assertEqual(next_state, STATE_ABORTED)
        self.assertIn("singular", ctx.abort_reason.lower())
        self.assertEqual(rig.move_arm_calls, [], "the unsafe target must never be sent to move_arm")

    def test_smaller_placement_target_from_the_same_pose_succeeds(self):
        rig = FakeRig(self._pose_from_incident())
        deps, _ = _make_deps(rig, answers=["EXTEND 60 27"])
        ctx = _Ctx(object_secured=True, end_effector_clearance_confirmed=True, arm_frozen=True, base_stationary=True, fresh_observation_available=True)

        next_state = _state_place_approach(deps, ctx, move_duration_s=2.0, step_count=1, max_place_steps=6)

        self.assertEqual(next_state, STATE_PLACE_READY)
        self.assertEqual(len(rig.move_arm_calls), 1)
        self.assertGreater(abs(rig.move_arm_calls[0]["overrides"]["elbow_flex"]), 10.0)


class TestCakeCapabilityBoundary(unittest.TestCase):
    """Integration tests: real FakeRig + real CapabilityRegistry (not
    faked), proving the MOBILE_READY -> WAITING_FOR_CAPABILITY ->
    MOBILE_APPROACH boundary end-to-end, matching the observable sequence
    in cake-runtime-contract.md and mobile-approach-spec.md section 6."""

    def _deps_with_registry(self, rig, answers, registry):
        deps, events = _make_deps(rig, answers)
        deps.capability_available = registry.has_capability
        deps.wait_for_capability_change = lambda: None  # tests drive submit_candidate() directly, never block
        return deps, events

    def test_mobile_ready_cannot_enter_mobile_approach_when_capability_absent(self):
        rig = FakeRig(_default_pose())
        registry = CapabilityRegistry(admitted_identities=frozenset({"real-mobile-approach"}))
        # No candidate ever submitted -- capability stays unavailable forever,
        # so this must hit the WAITING_FOR_CAPABILITY poll backstop, not MOBILE_APPROACH.
        deps, _ = self._deps_with_registry(rig, ["CUBE READY", "YES", "YES"], registry)
        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertIn(STATE_WAITING_FOR_CAPABILITY, result.abort_reason)
        self.assertEqual(rig.move_base_calls, [], "no base motion may occur while the capability is unavailable")

    def test_rejected_candidate_leaves_capability_unavailable_and_no_motion(self):
        registry = CapabilityRegistry(admitted_identities=frozenset({"real-mobile-approach"}))
        result = registry.submit_candidate(MOBILE_APPROACH_CAPABILITY, Candidate(capsule_identity="unadmitted-build"))
        self.assertEqual(result, REJECTED)
        self.assertFalse(registry.has_capability(MOBILE_APPROACH_CAPABILITY))

    def test_valid_candidate_activates_capability_and_the_same_mission_resumes(self):
        # Registry pre-admitted, but NOT yet activated when the run starts --
        # simulates Cake activating mid-mission, same ctx/run, no restart.
        rig = FakeRig(_default_pose())
        registry = CapabilityRegistry(admitted_identities=frozenset({"real-mobile-approach"}))
        call_count = {"n": 0}

        def capability_available(name):
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Reject an invalid candidate first (section 5's required sequence).
                registry.submit_candidate(name, Candidate(capsule_identity="invalid-candidate"))
            if call_count["n"] == 4:
                registry.submit_candidate(name, Candidate(capsule_identity="real-mobile-approach"))
            return registry.has_capability(name)

        deps, events = _make_deps(rig, ["CUBE READY", "YES", "YES", "EXTEND 40 20", "YES", "SUCCESS"])
        deps.capability_available = capability_available
        deps.wait_for_capability_change = lambda: None

        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_DONE)
        self.assertTrue(result.end_effector_clearance_confirmed)
        self.assertTrue(result.placement_ready)
        # Same run/ctx the whole way -- no second GRASP_POSITION, no restart.
        grasp_position_moves = [c for c in rig.move_arm_calls if c["overrides"] == KNOWN_GOOD_GRASP_POSE_DEG]
        self.assertEqual(len(grasp_position_moves), 1, "capability activation must not restart the mission")
        self.assertEqual(len(rig.move_base_calls), 1, "MOBILE_APPROACH must still use the existing sustained-motion call exactly once")

    def test_capability_available_does_not_skip_mechanical_preconditions(self):
        # Capability available from the start, but clearance was never
        # confirmed (object_secured False) -- must still abort at
        # MOBILE_READY's mechanical precondition check, never reaching the
        # capability check or MOBILE_APPROACH.
        rig = FakeRig(_default_pose())
        registry = CapabilityRegistry(admitted_identities=frozenset({"real-mobile-approach"}))
        registry.submit_candidate(MOBILE_APPROACH_CAPABILITY, Candidate(capsule_identity="real-mobile-approach"))
        deps, _ = self._deps_with_registry(rig, ["CUBE READY", "NO"], registry)
        result = run_hybrid_pick_place(deps)

        self.assertEqual(result.final_state, STATE_ABORTED)
        self.assertFalse(result.object_secured)
        self.assertEqual(rig.move_base_calls, [], "an available capability must never bypass object_secured/clearance checks")


if __name__ == "__main__":
    unittest.main()
