#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_mission_grasp_endings.py: how the mission classes the endings the grasp stage's words can
# produce (autonomy/manipulation/policy_grasp.py: grasp_measured_retry, near_grasp, grasp_recover). An ending that leaves
# nothing held becomes a hold the operator can Retry from; an ending with a cube held never offers a Retry; the
# block-out-of-range refusal asks for the block to be moved first. Pinned on synthetic records, then on the FAKE controller
# (tests/fake_controller.py, KC_FAKE_FAIL_JAWS) through a real synthetic Supervisor. No robot, camera, network or resident.
# Run directly: python3 packages/demo-runtime/tests/test_mission_grasp_endings.py
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(PACKAGE_DIR.parents[1]))

from demo_runtime import mission, mission_coordinator  # noqa: E402
from demo_runtime import mission_execution as mx  # noqa: E402
from tests.test_mission_execution import AUTO, Stack  # noqa: E402
from tests.test_mission_outcome_class import BEFORE_MOTION, NOTHING_HELD, completion, event, failing, transition  # noqa: E402
from tests.test_mission_retry import RetryStack  # noqa: E402

HELD = dict(NOTHING_HELD, holding=True)

# policy_grasp's endings after the jaws closed empty and reopened, or after the block was set down and the jaws opened.
NOTHING_HELD_ENDINGS = ("grasp_retry_cube_lost", "grasp_retry_not_aligned", "grasp_retry_no_untried_radius",
                        "retention_not_demonstrated_set_down")
# Nothing held, and the arm stands low at the block where a lift or an approach was refused part way: the operator decides,
# and no Retry follows.
NO_RETRY_ENDINGS = ("grasp_retry_approach_refused", "grasp_retry_lift_refused", "grasp_set_down_lift_refused")
# The near_grasp refusal: the block lies outside this run's grasp range, so it has to be moved before anything else.
BLOCK_OUT_OF_RANGE = "approach_cube_beyond_grasp_range"
# Endings that leave the block between closed jaws.
HELD_ENDINGS = ("grasp_set_down_refused", "retention_not_demonstrated")

SET_DOWN_EVENTS = [event("action.started", action_type="gripper"), transition("GRASP_CLOSE"),
                   event("predicate.evaluated", predicate="closed_on_something", value=True),
                   event("predicate.evaluated", predicate="object_secured", value=False), transition("GRASP_SET_DOWN")]


def hold_actions(outcome_class, stop_state):
    return mission.next_actions(outcome_class, stop_state, retry_left=True)


class TheStopStateAfterASetDown(unittest.TestCase):
    def test_a_set_down_whose_jaws_measured_open_holds_nothing(self):
        events = SET_DOWN_EVENTS + [event("predicate.evaluated", predicate="gripper_opened", value=True)]
        self.assertEqual(mx.stop_state_from_events(events), NOTHING_HELD)

    def test_a_set_down_that_never_opened_still_holds_the_block(self):
        self.assertIs(mx.stop_state_from_events(SET_DOWN_EVENTS)["holding"], True)

    def test_a_set_down_whose_jaws_did_not_open_still_holds_the_block(self):
        events = SET_DOWN_EVENTS + [event("predicate.evaluated", predicate="gripper_opened", value=False)]
        self.assertIs(mx.stop_state_from_events(events)["holding"], True)

    def test_jaws_opened_after_a_held_close_without_a_set_down_is_not_read_as_empty(self):
        events = SET_DOWN_EVENTS[:3] + [event("predicate.evaluated", predicate="gripper_opened", value=True)]
        self.assertIs(mx.stop_state_from_events(events)["holding"], True)


class TheGraspEndingsClass(unittest.TestCase):
    def classify(self, reason_code, stop_state=NOTHING_HELD, **fields):
        return mx.classify_outcome(completion(reason_code=reason_code, **fields), stop_state=stop_state)

    def test_a_nothing_held_ending_is_retryable_and_its_hold_offers_the_retry(self):
        for code in NOTHING_HELD_ENDINGS:
            with self.subTest(code=code):
                self.assertEqual(self.classify(code), mission.OUTCOME_RETRYABLE)
                self.assertEqual(hold_actions(self.classify(code), NOTHING_HELD), [mission.NEXT_RETRY, mission.NEXT_STOP])

    def test_a_refused_lift_or_approach_needs_the_operator_and_offers_no_retry(self):
        for code in NO_RETRY_ENDINGS:
            with self.subTest(code=code):
                self.assertEqual(self.classify(code), mission.OUTCOME_OPERATOR_NEEDED)
                self.assertIs(mx.retry_allowed(code), False)
                self.assertIs(mx.retry_allowed(NOTHING_HELD_ENDINGS[0]), True)

    def test_a_nothing_held_ending_needs_the_cyclefailed_exit(self):
        for code in NOTHING_HELD_ENDINGS + NO_RETRY_ENDINGS:
            with self.subTest(code=code):
                self.assertEqual(self.classify(code, exit_status=3), mission.OUTCOME_FATAL)

    def test_the_block_out_of_range_asks_for_the_block_to_be_moved(self):
        for state in (NOTHING_HELD, BEFORE_MOTION):
            with self.subTest(moved=state["moved"]):
                outcome = self.classify(BLOCK_OUT_OF_RANGE, stop_state=state)
                self.assertEqual(outcome, mission.OUTCOME_OPERATOR_NEEDED)
                self.assertEqual(hold_actions(outcome, state), [mission.NEXT_MOVE_BLOCK_THEN_RETRY, mission.NEXT_STOP])

    def test_a_held_cube_never_offers_a_retry_whatever_the_grasp_code(self):
        for code in NOTHING_HELD_ENDINGS + NO_RETRY_ENDINGS + HELD_ENDINGS + (BLOCK_OUT_OF_RANGE, "grasp_missed", "gripper_did_not_open"):
            with self.subTest(code=code):
                outcome = self.classify(code, stop_state=HELD)
                self.assertEqual(outcome, mission.OUTCOME_OPERATOR_NEEDED)
                self.assertEqual(hold_actions(outcome, HELD), [mission.NEXT_STOP])

    def test_an_unknown_jaw_state_is_never_retryable(self):
        for code in NOTHING_HELD_ENDINGS + NO_RETRY_ENDINGS + (BLOCK_OUT_OF_RANGE,):
            with self.subTest(code=code):
                self.assertEqual(self.classify(code, stop_state=dict(NOTHING_HELD, holding=None)), mission.OUTCOME_FATAL)

    def test_jaws_that_did_not_open_end_the_mission(self):
        self.assertEqual(self.classify("gripper_did_not_open"), mission.OUTCOME_FATAL)


class TheGraspEndingsOnTheFakeController(unittest.TestCase):
    """The runner's outcome for a grasp ending scripted on the fake controller: the class and where the run left the robot."""

    def recovery(self, **env):
        stack = Stack(self, env=failing(self, cycles=1, **env))
        stack.hardware._attach_mission("mission-0123456789ab", [dict(AUTO)])
        stack.hardware._attach_cancel(threading.Event())
        return stack.runner(None, lambda _name: True, lambda: None, dict(AUTO))["recovery"]

    def test_an_empty_close_ending_is_retryable_with_nothing_held(self):
        for code in ("grasp_retry_cube_lost", "grasp_retry_not_aligned", "grasp_retry_no_untried_radius"):
            with self.subTest(code=code):
                recovery = self.recovery(KC_FAKE_FAIL_AT="GRASP_CLOSE", KC_FAKE_FAIL_JAWS="empty", KC_FAKE_FAIL_CODE=code)
                self.assertEqual((recovery["outcome_class"], recovery["reason_code"]), (mission.OUTCOME_RETRYABLE, code))
                self.assertEqual(recovery["stop_state"], NOTHING_HELD)

    def test_an_empty_close_ending_left_low_at_the_block_needs_the_operator_with_nothing_held(self):
        for code in ("grasp_retry_approach_refused", "grasp_retry_lift_refused"):
            with self.subTest(code=code):
                recovery = self.recovery(KC_FAKE_FAIL_AT="GRASP_CLOSE", KC_FAKE_FAIL_JAWS="empty", KC_FAKE_FAIL_CODE=code)
                self.assertEqual((recovery["outcome_class"], recovery["reason_code"]), (mission.OUTCOME_OPERATOR_NEEDED, code))
                self.assertEqual(recovery["stop_state"], NOTHING_HELD)
                self.assertIs(recovery["retry"], False)

    def test_a_set_down_ending_is_retryable_with_nothing_held(self):
        recovery = self.recovery(KC_FAKE_FAIL_AT="OBJECT_SECURED", KC_FAKE_FAIL_JAWS="set_down", KC_FAKE_FAIL_CODE="retention_not_demonstrated_set_down")
        self.assertEqual((recovery["outcome_class"], recovery["reason_code"]), (mission.OUTCOME_RETRYABLE, "retention_not_demonstrated_set_down"))
        self.assertEqual(recovery["stop_state"], NOTHING_HELD)

    def test_a_set_down_whose_lift_off_was_refused_needs_the_operator_with_nothing_held(self):
        recovery = self.recovery(KC_FAKE_FAIL_AT="OBJECT_SECURED", KC_FAKE_FAIL_JAWS="set_down", KC_FAKE_FAIL_CODE="grasp_set_down_lift_refused")
        self.assertEqual((recovery["outcome_class"], recovery["reason_code"]), (mission.OUTCOME_OPERATOR_NEEDED, "grasp_set_down_lift_refused"))
        self.assertEqual(recovery["stop_state"], NOTHING_HELD)
        self.assertIs(recovery["retry"], False)

    def test_a_set_down_that_never_opened_needs_the_operator_with_the_block_held(self):
        recovery = self.recovery(KC_FAKE_FAIL_AT="OBJECT_SECURED", KC_FAKE_FAIL_JAWS="set_down_refused",
                                 KC_FAKE_FAIL_CODE="grasp_set_down_refused")
        self.assertEqual(recovery["outcome_class"], mission.OUTCOME_OPERATOR_NEEDED)
        self.assertIs(recovery["stop_state"]["holding"], True)

    def test_the_block_out_of_range_needs_the_operator_with_nothing_held(self):
        recovery = self.recovery(KC_FAKE_FAIL_AT="APPROACH", KC_FAKE_FAIL_CODE=BLOCK_OUT_OF_RANGE)
        self.assertEqual(recovery["outcome_class"], mission.OUTCOME_OPERATOR_NEEDED)
        self.assertEqual(recovery["stop_state"], NOTHING_HELD)


class TheHoldAfterAGraspEnding(unittest.TestCase):
    def test_a_set_down_ending_holds_then_retry_parks_and_a_fresh_cycle_places(self):
        stack = RetryStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "OBJECT_SECURED", "KC_FAKE_FAIL_JAWS": "set_down",
                                                     "KC_FAKE_FAIL_CODE": "retention_not_demonstrated_set_down"})
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual((hold["outcome_class"], hold["reason_code"]), (mission.OUTCOME_RETRYABLE, "retention_not_demonstrated_set_down"))
        self.assertEqual(hold["next_actions"], [mission.NEXT_RETRY, mission.NEXT_STOP])
        self.assertTrue(stack.coordinator.request_retry()["ok"])
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        self.assertEqual(stack.kinds(), ["start_cycle", "return_arm", "start_cycle"])

    def test_a_set_down_that_never_opened_holds_with_only_the_stop_and_never_parks(self):
        stack = RetryStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "OBJECT_SECURED", "KC_FAKE_FAIL_JAWS": "set_down_refused",
                                                     "KC_FAKE_FAIL_CODE": "grasp_set_down_refused"})
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual(hold["next_actions"], [mission.NEXT_STOP])
        self.assertEqual(stack.coordinator.request_retry()["reason_code"], mission_coordinator.RETRY_NOT_OFFERED)
        stack.stop()
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)
        self.assertEqual(stack.supervisor.parks, [])

    def test_the_block_out_of_range_holds_asking_for_the_block_to_be_moved_then_retries(self):
        stack = RetryStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "APPROACH", "KC_FAKE_FAIL_CODE": BLOCK_OUT_OF_RANGE})
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual(hold["next_actions"], [mission.NEXT_MOVE_BLOCK_THEN_RETRY, mission.NEXT_STOP])
        self.assertTrue(stack.coordinator.request_retry()["ok"])
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_COMPLETE)


if __name__ == "__main__":
    unittest.main()
