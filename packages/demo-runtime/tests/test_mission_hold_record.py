#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_mission_hold_record.py: the records of a mission that holds for the operator after a
# failure (demo_runtime.mission): the waiting reason operator_decision, the attempt record, the retry request, the park
# run, the next actions and what the fold and the view make of them. Pure records: no process, no robot, no resident.
# Run directly: python3 packages/demo-runtime/tests/test_mission_hold_record.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo_runtime import mission  # noqa: E402

T0 = "2026-09-27T00:00:00Z"
TARGET = {"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": "auto"}
NOTHING_HELD = {"holding": False, "base_ahead": False, "moved": True}
HELD = {"holding": True, "base_ahead": False, "moved": True}
MEASUREMENTS = [{"name": "radial_mm", "value": 14.5, "unit": "mm"}, {"name": "lateral_mm", "value": -2.6, "unit": "mm"}]


def started(mission_id="m1"):
    return mission.mission_started(mission_id, [TARGET], t_wall_utc=T0, data_mode="live", performs_motion=True,
                                   execution_label="proven-single-cube-controller")


def attempt(n=1, *, outcome_class=mission.OUTCOME_RETRYABLE, stop_state=None, actions=None, budget_left=1, run_id="run-1",
            park_run_id=None, reason_code="align_cube_not_detected"):
    stop_state = NOTHING_HELD if stop_state is None else stop_state
    if actions is None:
        actions = mission.next_actions(outcome_class, stop_state, retry_left=budget_left > 0)
    return mission.target_attempt("m1", "cube", attempt=n, run_id=run_id, park_run_id=park_run_id, outcome_class=outcome_class,
                                  reason_code=reason_code, phase="ALIGN", measurements=MEASUREMENTS,
                                  observation_ids=["obs-20260926T212418Z-wrist-0007"], stop_state=stop_state,
                                  next_actions=actions, budget_left=budget_left, t_wall_utc=T0)


def fold(records):
    return mission.mission_view(mission.fold_missions(records)["m1"])


class TheNextActions(unittest.TestCase):
    """What the operator can do, derived from the class and from the state the run left, never guessed."""

    def test_a_retryable_hold_with_nothing_held_offers_retry_and_stop(self):
        self.assertEqual(mission.next_actions(mission.OUTCOME_RETRYABLE, NOTHING_HELD, retry_left=True),
                         [mission.NEXT_RETRY, mission.NEXT_STOP])

    def test_a_spent_budget_offers_only_the_stop(self):
        self.assertEqual(mission.next_actions(mission.OUTCOME_RETRYABLE, NOTHING_HELD, retry_left=False), [mission.NEXT_STOP])

    def test_an_envelope_refusal_asks_for_the_block_to_be_moved_first(self):
        before_motion = {"holding": False, "base_ahead": False, "moved": False}
        self.assertEqual(mission.next_actions(mission.OUTCOME_OPERATOR_NEEDED, before_motion, retry_left=True),
                         [mission.NEXT_MOVE_BLOCK_THEN_RETRY, mission.NEXT_STOP])

    def test_a_held_cube_never_offers_a_retry(self):
        for outcome_class in (mission.OUTCOME_RETRYABLE, mission.OUTCOME_OPERATOR_NEEDED):
            self.assertEqual(mission.next_actions(outcome_class, HELD, retry_left=True), [mission.NEXT_STOP])

    def test_an_unknown_state_never_offers_a_retry(self):
        unknown = {"holding": None, "base_ahead": None, "moved": None}
        self.assertEqual(mission.next_actions(mission.OUTCOME_RETRYABLE, unknown, retry_left=True), [mission.NEXT_STOP])

    def test_a_displaced_base_never_offers_a_retry(self):
        ahead = {"holding": False, "base_ahead": True, "moved": True}
        self.assertEqual(mission.next_actions(mission.OUTCOME_OPERATOR_NEEDED, ahead, retry_left=True), [mission.NEXT_STOP])

    def test_after_the_end_the_actions_follow_the_state_the_run_left(self):
        self.assertEqual(mission.next_actions(mission.OUTCOME_STOPPED, NOTHING_HELD, retry_left=True),
                         [mission.NEXT_RETURN_ARM, mission.NEXT_START])
        self.assertEqual(mission.next_actions(mission.OUTCOME_STOPPED, HELD, retry_left=True),
                         [mission.NEXT_OPEN_GRIPPER_BY_HAND, mission.NEXT_RETURN_ARM, mission.NEXT_START])
        self.assertEqual(mission.next_actions(mission.OUTCOME_BASE_ONLY, {"holding": False, "base_ahead": True, "moved": True}, retry_left=True),
                         [mission.NEXT_RETURN_BASE, mission.NEXT_RETURN_ARM, mission.NEXT_START])
        self.assertEqual(mission.next_actions(mission.OUTCOME_FATAL, {"holding": False, "base_ahead": False, "moved": False}, retry_left=True),
                         [mission.NEXT_START])
        self.assertEqual(mission.next_actions(mission.OUTCOME_FATAL, None, retry_left=True),
                         [mission.NEXT_OPEN_GRIPPER_BY_HAND, mission.NEXT_RETURN_ARM, mission.NEXT_START],
                         "a state nothing recorded is read as the worst one: a cube may be held and the arm may have moved")

    def test_every_action_is_one_of_the_named_words(self):
        self.assertEqual(set(mission.NEXT_ACTIONS), {"retry", "move_block_then_retry", "return_arm", "open_gripper_by_hand",
                                                     "return_base", "stop", "start"})


class TheHoldOnTheRecord(unittest.TestCase):
    def test_a_hold_waits_for_the_operator_with_its_own_reason_and_the_failure_named(self):
        records = [started(), mission.target_selected("m1", "cube", t_wall_utc=T0),
                   mission.target_run_started("m1", "cube", "run-1", t_wall_utc=T0), attempt(),
                   mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=T0)]
        view = fold(records)
        self.assertEqual(view["state"], mission.MISSION_WAITING)
        self.assertEqual(view["waiting_reason"], "operator_decision")
        self.assertNotEqual(mission.WAITING_ON_OPERATOR, mission.WAITING_ON_CAPABILITY)
        hold = view["hold"]
        self.assertEqual((hold["target_id"], hold["attempt"], hold["outcome_class"], hold["reason_code"], hold["phase"]),
                         ("cube", 1, "retryable", "align_cube_not_detected", "ALIGN"))
        self.assertEqual(hold["measurements"], MEASUREMENTS)
        self.assertEqual(hold["observation_ids"], ["obs-20260926T212418Z-wrist-0007"])
        self.assertEqual(hold["next_actions"], ["retry", "stop"])
        self.assertEqual(hold["budget_left"], 1)
        self.assertFalse(hold["retry_requested"])
        self.assertEqual(view["next_actions"], ["retry", "stop"])
        self.assertEqual(view["targets"][0]["attempts"][0]["run_id"], "run-1")

    def test_a_retry_request_then_a_park_then_a_fresh_attempt_placed(self):
        records = [started(), mission.target_selected("m1", "cube", t_wall_utc=T0),
                   mission.target_run_started("m1", "cube", "run-1", t_wall_utc=T0), attempt(),
                   mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=T0),
                   mission.retry_requested("m1", "cube", attempt=1, origin="operator_console", t_wall_utc=T0)]
        self.assertTrue(fold(records)["hold"]["retry_requested"])
        records += [mission.target_resumed("m1", "cube", t_wall_utc=T0),
                    mission.target_run_started("m1", "cube", "park-1", t_wall_utc=T0, run_kind=mission.RUN_KIND_PARK),
                    mission.target_run_started("m1", "cube", "run-2", t_wall_utc=T0),
                    mission.target_outcome("m1", "cube", mission.TARGET_PLACED, run_id="run-2", t_wall_utc=T0),
                    mission.mission_ended("m1", mission.MISSION_COMPLETE, t_wall_utc=T0)]
        view = fold(records)
        self.assertEqual(view["state"], mission.MISSION_COMPLETE)
        self.assertIsNone(view["hold"])
        self.assertIsNone(view["waiting_reason"])
        target = view["targets"][0]
        self.assertEqual(target["state"], mission.TARGET_PLACED)
        self.assertEqual(target["runs"], ["run-1", "park-1", "run-2"])
        self.assertEqual(target["park_runs"], ["park-1"])
        self.assertEqual(len(target["attempts"]), 1)
        self.assertEqual(target["retries"], [{"attempt": 1, "origin": "operator_console", "t_wall_utc": T0}])
        self.assertEqual(view["next_actions"], ["start"])

    def test_a_stop_during_the_hold_ends_stopped_and_the_actions_follow_the_recorded_state(self):
        records = [started(), mission.target_selected("m1", "cube", t_wall_utc=T0), attempt(stop_state=HELD, outcome_class=mission.OUTCOME_OPERATOR_NEEDED,
                                                                                               reason_code="retention_not_demonstrated"),
                   mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=T0),
                   mission.mission_stop_requested("m1", origin="mission_stop", t_wall_utc=T0),
                   mission.mission_ended("m1", mission.MISSION_STOPPED, detail="stopped", t_wall_utc=T0)]
        view = fold(records)
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertIsNone(view["hold"])
        self.assertEqual(view["next_actions"], ["open_gripper_by_hand", "return_arm", "start"])

    def test_a_stop_after_a_retry_request_assumes_the_arm_may_have_moved(self):
        records = [started(), mission.target_selected("m1", "cube", t_wall_utc=T0),
                   attempt(stop_state={"holding": False, "base_ahead": False, "moved": False}, outcome_class=mission.OUTCOME_OPERATOR_NEEDED,
                           reason_code="search_placement_infeasible"),
                   mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=T0),
                   mission.retry_requested("m1", "cube", attempt=1, origin="operator_console", t_wall_utc=T0),
                   mission.mission_ended("m1", mission.MISSION_STOPPED, detail="stopped", t_wall_utc=T0)]
        self.assertEqual(fold(records)["next_actions"], ["return_arm", "start"])

    def test_nothing_after_the_end_is_folded_from_the_new_records(self):
        records = [started(), mission.mission_ended("m1", mission.MISSION_FAILED, detail="x", t_wall_utc=T0), attempt(),
                   mission.retry_requested("m1", "cube", attempt=1, origin="operator_console", t_wall_utc=T0)]
        view = fold(records)
        self.assertEqual(view["targets"][0]["attempts"], [])
        self.assertEqual(view["targets"][0]["retries"], [])
        self.assertEqual(view["records_after_end"], 2)

    def test_a_waiting_mission_for_the_capability_has_no_hold(self):
        records = [started(), mission.target_selected("m1", "cube", t_wall_utc=T0),
                   mission.target_waiting("m1", "cube", mission.WAITING_ON_CAPABILITY, t_wall_utc=T0)]
        view = fold(records)
        self.assertIsNone(view["hold"])
        self.assertEqual(view["next_actions"], ["stop"])

    def test_the_attempt_record_carries_ids_and_numbers_only(self):
        record = attempt()
        self.assertEqual(record["rec"], "mission_target_attempt")
        self.assertEqual(record["v"], mission.MISSION_RECORD_VERSION)
        self.assertEqual(set(record), {"rec", "v", "mission_id", "target_id", "attempt", "run_id", "park_run_id", "outcome_class",
                                       "reason_code", "phase", "measurements", "observation_ids", "stop_state", "next_actions",
                                       "budget_left", "t_wall_utc"})

    def test_the_controllers_sentence_rides_on_the_attempt_and_the_hold_bounded(self):
        sentence = "BINDING: the right edge is 14.5 mm short; move the block 15 mm to the left; " + "x" * 2000
        record = mission.target_attempt("m1", "cube", attempt=1, run_id="r", park_run_id=None, outcome_class=mission.OUTCOME_OPERATOR_NEEDED,
                                        reason_code="mission_binding_candidate_not_placeable", phase="SEARCH", measurements=[],
                                        observation_ids=[], stop_state=NOTHING_HELD, next_actions=["stop"], budget_left=1, t_wall_utc=T0,
                                        detail=sentence)
        self.assertEqual(record["detail"], sentence[:mission.MAX_ATTEMPT_DETAIL_CHARS])
        view = fold([started(), mission.target_selected("m1", "cube", t_wall_utc=T0), record,
                     mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=T0)])
        self.assertEqual(view["hold"]["detail"], sentence[:mission.MAX_ATTEMPT_DETAIL_CHARS])
        self.assertIsNone(fold([started(), mission.target_selected("m1", "cube", t_wall_utc=T0), attempt(),
                                mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=T0)])["hold"]["detail"])

    def test_the_attempt_record_bounds_what_it_keeps(self):
        many = [{"name": f"m{index}", "value": float(index), "unit": None} for index in range(100)]
        record = mission.target_attempt("m1", "cube", attempt=1, run_id="r", park_run_id=None, outcome_class=mission.OUTCOME_RETRYABLE,
                                        reason_code="align_x", phase="ALIGN", measurements=many + ["not a measurement"],
                                        observation_ids=[f"obs-{index}" for index in range(100)] + [7], stop_state=NOTHING_HELD,
                                        next_actions=["retry", "stop"], budget_left=1, t_wall_utc=T0)
        self.assertEqual(len(record["measurements"]), mission.MAX_ATTEMPT_ITEMS)
        self.assertEqual(len(record["observation_ids"]), mission.MAX_ATTEMPT_ITEMS)
        self.assertTrue(all(isinstance(item, dict) for item in record["measurements"]))
        self.assertTrue(all(isinstance(item, str) for item in record["observation_ids"]))

    def test_an_attempt_keeps_the_evidence_its_run_rested_on(self):
        evidence = {"selection_observation_id": "obs-20260926T212418Z-front-0003", "capability": {"generation": 2, "pid": 41}}
        record = mission.target_attempt("m1", "cube", attempt=1, run_id="run-1", park_run_id=None,
                                        outcome_class=mission.OUTCOME_OPERATOR_NEEDED, reason_code="base_not_returned",
                                        phase="BASE_RETURN", measurements=MEASUREMENTS, observation_ids=[], stop_state=NOTHING_HELD,
                                        next_actions=["stop"], budget_left=1, t_wall_utc=T0, evidence=evidence)
        self.assertEqual(record["evidence"], evidence)
        records = [started(), mission.target_selected("m1", "cube", t_wall_utc=T0), record,
                   mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=T0),
                   mission.mission_stop_requested("m1", origin="mission_stop", t_wall_utc=T0),
                   mission.mission_ended("m1", mission.MISSION_STOPPED, detail="stopped", t_wall_utc=T0)]
        view = fold(records)
        self.assertEqual(view["targets"][0]["evidence"]["selection_observation_id"], "obs-20260926T212418Z-front-0003")
        # An attempt with none reads as before: no key on the record, nothing folded.
        self.assertNotIn("evidence", attempt())
        self.assertIsNone(fold([started(), mission.target_selected("m1", "cube", t_wall_utc=T0), attempt()])["targets"][0]["evidence"])

    def test_a_run_started_record_without_a_kind_reads_as_before(self):
        record = mission.target_run_started("m1", "cube", "run-1", t_wall_utc=T0)
        self.assertNotIn("run_kind", record)
        self.assertEqual(mission.target_run_started("m1", "cube", "p", t_wall_utc=T0, run_kind=mission.RUN_KIND_PARK)["run_kind"], "park")


if __name__ == "__main__":
    unittest.main()
