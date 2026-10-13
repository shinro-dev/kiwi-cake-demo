#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_mission_outcome_class.py: what ONE attempt at a mission target came to
# (demo_runtime.mission_execution.classify_outcome), where its run left the robot (stop_state_from_events) and the failure
# facts a target's record keeps (its reason code, phase, measurements and observation ids). The classes are pinned on
# synthetic records; the last cases run the FAKE controller (tests/fake_controller.py) through a real synthetic Supervisor.
# No robot, camera, network or Cake resident.
# Run directly: python3 packages/demo-runtime/tests/test_mission_outcome_class.py
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(PACKAGE_DIR.parents[1]))

from demo_runtime import mission  # noqa: E402
from demo_runtime import mission_execution as mx  # noqa: E402
from tests.test_mission_execution import AUTO, Stack  # noqa: E402

NOTHING_HELD = {"holding": False, "base_ahead": False, "moved": True}
BEFORE_MOTION = {"holding": False, "base_ahead": False, "moved": False}


def completion(outcome="failed", exit_status=5, reason_code=None, phase=None, predicates=None, failure=None):
    result = {"outcome": outcome, "predicates": predicates or {}, "reason_code": reason_code, "phase": phase, "failure": failure}
    return {"outcome": outcome, "exit_status": exit_status, "result": result}


def failing(test, *, cycles: int, **extra) -> dict:
    """The fake controller's environment for `cycles` scripted failures, counted in a file of this test's own."""
    directory = tempfile.mkdtemp(prefix="kc-fake-fail-")
    test.addCleanup(shutil.rmtree, directory, True)
    return {"KC_FAKE_FAIL_CYCLES": str(cycles), "KC_FAKE_FAIL_STATE": os.path.join(directory, "cycles"), **extra}


def event(kind, **payload):
    return {"kind": kind, "payload": payload}


def transition(to_state):
    return event("state.transition", machine="cycle", from_state=None, to_state=to_state)


class TheStopState(unittest.TestCase):
    def test_nothing_moved_and_nothing_held_before_any_action(self):
        self.assertEqual(mx.stop_state_from_events([transition("SEARCH")]), BEFORE_MOTION)

    def test_an_align_failure_leaves_the_arm_moved_with_nothing_held(self):
        events = [transition("SEARCH"), transition("PREGRASP"), event("action.started", action_type="arm_pose"), transition("ALIGN")]
        self.assertEqual(mx.stop_state_from_events(events), NOTHING_HELD)

    def test_an_empty_close_reopened_is_nothing_held(self):
        events = [event("action.started", action_type="arm_pose"), transition("GRASP_CLOSE"),
                  event("predicate.evaluated", predicate="closed_on_something", value=False)] * 3
        self.assertEqual(mx.stop_state_from_events(events)["holding"], False)

    def test_a_close_on_something_is_held_until_released(self):
        events = [event("action.started", action_type="gripper"), transition("GRASP_CLOSE"),
                  event("predicate.evaluated", predicate="closed_on_something", value=True)]
        self.assertIs(mx.stop_state_from_events(events)["holding"], True)
        self.assertIs(mx.stop_state_from_events(events + [transition("RELEASED")])["holding"], False)

    def test_a_close_with_no_verdict_is_unknown_never_empty(self):
        events = [event("action.started", action_type="gripper"), transition("GRASP_CLOSE")]
        self.assertIsNone(mx.stop_state_from_events(events)["holding"])
        self.assertIs(mx.stop_state_from_events(events + [event("predicate.evaluated", predicate="object_secured", value=True)])["holding"], True)

    def test_the_base_is_ahead_after_an_advance_until_a_return_measured_true(self):
        advanced = [event("action.started", action_type="base_pulse"), transition("BASE_ADVANCE")]
        self.assertIs(mx.stop_state_from_events(advanced)["base_ahead"], True)
        returned = advanced + [transition("BASE_RETURN"), event("predicate.evaluated", predicate="base_returned", value=False)]
        self.assertIs(mx.stop_state_from_events(returned)["base_ahead"], True)
        returned[-1] = event("predicate.evaluated", predicate="base_returned", value=True)
        self.assertIs(mx.stop_state_from_events(returned)["base_ahead"], False)

    def test_no_events_file_is_unknown(self):
        self.assertEqual(mx.read_stop_state(None), mx.UNKNOWN_STATE)
        self.assertEqual(mx.read_stop_state("/nonexistent/run-dir"), mx.UNKNOWN_STATE)

    def test_a_damaged_events_file_is_unknown_never_read_as_complete(self):
        with tempfile.TemporaryDirectory() as run_dir:
            lines = [json.dumps(item) for item in (event("action.started", action_type="gripper"), transition("GRASP_CLOSE"),
                                                   event("predicate.evaluated", predicate="closed_on_something", value=False))]
            path = os.path.join(run_dir, mx.CONTROLLER_EVENTS_FILE)
            with open(path, "w") as handle:
                handle.write("\n".join(lines) + "\n")
            self.assertIs(mx.read_stop_state(run_dir)["holding"], False)
            # The same stream with a damaged line in its middle: what that line said is not known, so neither is the state.
            with open(path, "w") as handle:
                handle.write("\n".join([lines[0], '{"kind": "state.trans', lines[1], lines[2]]) + "\n")
            self.assertEqual(mx.read_stop_state(run_dir), mx.UNKNOWN_STATE)


class TheOutcomeClass(unittest.TestCase):
    def classify(self, reason_code, stop_state=NOTHING_HELD, **fields):
        return mx.classify_outcome(completion(reason_code=reason_code, **fields), stop_state=stop_state)

    def test_the_plans_retryable_codes_with_nothing_held(self):
        for code in ("align_cube_not_detected", "align_not_in_window_after_trims", "align_no_non_contact_descent",
                     "align_bounded_corrections_exhausted", "pregrasp_no_non_contact_pose", "straddling_not_reached",
                     "grasp_missed", "search_no_cube", "search_no_cube_after_sweep", "search_no_pad_frame",
                     "search_cube_not_in_pad_frame"):
            with self.subTest(code=code):
                self.assertEqual(self.classify(code), mission.OUTCOME_RETRYABLE)

    def test_a_retryable_code_needs_the_cyclefailed_exit(self):
        self.assertEqual(self.classify("align_cube_not_detected", exit_status=3), mission.OUTCOME_FATAL)

    def test_a_held_cube_needs_the_operator_whatever_the_code(self):
        held = dict(NOTHING_HELD, holding=True)
        for code in ("align_cube_not_detected", "retention_not_demonstrated", "release_not_admitted", None):
            with self.subTest(code=code):
                self.assertEqual(self.classify(code, stop_state=held), mission.OUTCOME_OPERATOR_NEEDED)

    def test_an_unknown_state_is_fatal_never_retryable(self):
        for part in ("holding", "base_ahead", "moved"):
            with self.subTest(part=part):
                self.assertEqual(self.classify("align_cube_not_detected", stop_state=dict(NOTHING_HELD, **{part: None})),
                                 mission.OUTCOME_FATAL)
        self.assertEqual(mx.classify_outcome(completion(reason_code="align_cube_not_detected")), mission.OUTCOME_FATAL)

    def test_placed_with_the_base_not_returned_is_base_only(self):
        ahead = {"holding": False, "base_ahead": True, "moved": True}
        self.assertEqual(self.classify("base_not_returned", stop_state=ahead), mission.OUTCOME_BASE_ONLY)

    def test_a_displaced_base_needs_the_operator(self):
        ahead = {"holding": False, "base_ahead": True, "moved": True}
        self.assertEqual(self.classify("align_cube_not_detected", stop_state=ahead), mission.OUTCOME_OPERATOR_NEEDED)

    def test_an_envelope_refusal_before_motion_needs_the_operator(self):
        for code in sorted(mx.ENVELOPE_REFUSAL_CODES):
            with self.subTest(code=code):
                self.assertEqual(self.classify(code, stop_state=BEFORE_MOTION), mission.OUTCOME_OPERATOR_NEEDED)
                self.assertEqual(self.classify(code), mission.OUTCOME_FATAL, "after motion it is not the refusal it names")

    def test_a_stop_and_a_placement(self):
        self.assertEqual(mx.classify_outcome(completion(outcome="stopped", exit_status=4)), mission.OUTCOME_STOPPED)
        placed = completion(outcome="placed_new", exit_status=0, predicates={"object_secured": True, "placed": True, "base_returned": True})
        self.assertEqual(mx.classify_outcome(placed, stop_state=NOTHING_HELD), mission.OUTCOME_PLACED)

    def test_anything_unclassified_is_fatal(self):
        for code in ("mission_capability_required", "start_pose_not_low_scan", "perception_remote_timeout", "fake_failed", None):
            with self.subTest(code=code):
                self.assertEqual(self.classify(code, stop_state=BEFORE_MOTION), mission.OUTCOME_FATAL)

    def test_the_result_file_stands_in_for_a_code_the_event_did_not_carry(self):
        self.assertEqual(mx.classify_outcome(completion(), result={"reason_code": "grasp_missed", "final_state": "GRASP_CLOSE"},
                                             stop_state=NOTHING_HELD), mission.OUTCOME_RETRYABLE)
        facts = mx.failure_facts(completion(), {"reason_code": "grasp_missed", "final_state": "GRASP_CLOSE"})
        self.assertEqual((facts["reason_code"], facts["phase"]), ("grasp_missed", "GRASP_CLOSE"))


class TheFakeControllersFailureOnTheRecord(unittest.TestCase):
    """The runner's outcome for a run that failed at ALIGN: the class, the facts and the stop state, and the target's
    evidence keeps them. The runner is called directly: the coordinator's hold is not what these cases are about."""

    def run_target(self, stack):
        stack.hardware._attach_mission("mission-0123456789ab", [dict(AUTO)])
        stack.hardware._attach_cancel(threading.Event())
        return stack.runner(None, lambda _name: True, lambda: None, dict(AUTO))

    def test_an_align_failure_is_retryable_with_its_phase_measurements_frames_and_state(self):
        stack = Stack(self, env=failing(self, cycles=1))
        outcome = self.run_target(stack)
        self.assertEqual(outcome["target_state"], mission.TARGET_FAILED)
        self.assertEqual(outcome["detail"], "run_outcome:failed:align_cube_not_detected")
        recovery = outcome["recovery"]
        self.assertEqual(recovery["outcome_class"], mission.OUTCOME_RETRYABLE)
        self.assertEqual((recovery["reason_code"], recovery["phase"]), ("align_cube_not_detected", "ALIGN"))
        self.assertEqual([item["name"] for item in recovery["measurements"]], ["radial_mm", "lateral_mm"])
        self.assertEqual(len(recovery["observation_ids"]), 1)
        self.assertEqual(recovery["stop_state"], NOTHING_HELD)
        evidence = outcome["evidence"]
        self.assertEqual((evidence["reason_code"], evidence["phase"]), ("align_cube_not_detected", "ALIGN"))
        self.assertEqual(evidence["failure_measurements"], recovery["measurements"])
        self.assertEqual(evidence["failure_observation_ids"], recovery["observation_ids"])
        self.assertEqual(evidence["stop_state"], NOTHING_HELD)

    def test_a_failure_with_the_jaws_closed_on_something_needs_the_operator(self):
        stack = Stack(self, env=failing(self, cycles=1, KC_FAKE_FAIL_AT="OBJECT_SECURED", KC_FAKE_FAIL_HOLDING="1",
                                        KC_FAKE_FAIL_CODE="retention_not_demonstrated"))
        recovery = self.run_target(stack)["recovery"]
        self.assertEqual(recovery["outcome_class"], mission.OUTCOME_OPERATOR_NEEDED)
        self.assertIs(recovery["stop_state"]["holding"], True)

    def test_a_placed_run_is_placed_with_nothing_held(self):
        stack = Stack(self)
        outcome = self.run_target(stack)
        self.assertEqual(outcome["target_state"], mission.TARGET_PLACED)
        self.assertEqual(outcome["recovery"]["outcome_class"], mission.OUTCOME_PLACED)
        self.assertEqual(outcome["recovery"]["stop_state"]["holding"], False)

    def test_a_refusal_before_any_run_is_fatal_with_nothing_run(self):
        stack = Stack(self, radii=None)
        recovery = self.run_target(stack)["recovery"]
        self.assertEqual(recovery["outcome_class"], mission.OUTCOME_FATAL)
        self.assertEqual(recovery["stop_state"], mx.NOTHING_RAN)
        self.assertEqual(stack.launches(), [])


if __name__ == "__main__":
    unittest.main()
