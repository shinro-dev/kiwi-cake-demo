#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_mission_retry.py: the hold after a failed attempt and the Retry, end to end against the
# FAKE controller (tests/fake_controller.py) through a synthetic Supervisor. No robot, camera, network or Cake resident.
#
# The track's offline acceptance: an ALIGN failure, the hold, Retry, the park, a fresh cycle, placed (two cycle runs and
# one exit run on the target); two retryable failures exhaust the budget and hold; a held cube never parks; a stop during
# the park or the hold ends STOPPED with no spawn. Beside them: a supervisor that cannot park offers no Retry, an envelope
# refusal before motion asks for the block to be moved and then retries, a stop during a run records where it left the
# robot, and the automatic retry, OFF, parks and retries once when switched on.
#
# THE PARK IS THE REAL ONE: ParkingSupervisor is the Supervisor with its own start_mission_park, only counting the parks
# asked for. NoParkSupervisor sets start_mission_park to None and stands for a supervisor that cannot park.
# Run directly: python3 packages/demo-runtime/tests/test_mission_retry.py
from __future__ import annotations

import os
import shutil
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
import sys

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(REPO_ROOT))

from demo_runtime import mission, mission_coordinator  # noqa: E402
from demo_runtime import mission_execution as mx  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.journal import EventLog, Journal, read_json_lines  # noqa: E402
from demo_runtime.mission_coordinator import MissionCoordinator  # noqa: E402
from tests.test_mission_execution import AUTO, ENDPOINTS, TEST_RADII, Slot  # noqa: E402

FAKE_ENTRY = "packages/demo-runtime/tests/fake_controller.py"
WAIT_S = 30.0


class ParkingSupervisor(sv.Supervisor):
    """The real Supervisor and its real start_mission_park, counting the parks it was asked for."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parks = []

    def start_mission_park(self, mission_run) -> dict:
        answer = super().start_mission_park(mission_run)
        self.parks.append(answer.get("command_id"))
        return answer


class NoParkSupervisor(sv.Supervisor):
    """A Supervisor that cannot park: whatever the Supervisor carries, this one has no start_mission_park."""

    start_mission_park = None


class RetryStack:
    """A synthetic supervisor that can park, the fake controller, a HardwareExecution, its runner and the coordinator."""

    def __init__(self, test, *, fail_cycles=0, env=None, step_s="0.01", parking=True, supervisor_options=None):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-mission-retry-"))
        test.addCleanup(self._remove)
        self.data = self.tmp / "data"
        self.data.mkdir(mode=0o700)
        self.launch_log = self.tmp / "launches.txt"
        environment = {"KC_FAKE_STEP_S": step_s, "KC_FAKE_LAUNCH_LOG": str(self.launch_log),
                       "KC_FAKE_FAIL_CYCLES": str(fail_cycles), "KC_FAKE_FAIL_STATE": str(self.tmp / "fail-state"), **(env or {})}
        kind = ParkingSupervisor if parking else NoParkSupervisor
        self.supervisor = kind(data_dir=self.data, journal=Journal(self.data / "journal.jsonl"), event_log=EventLog(self.data / "events.jsonl"),
                               runtime_session="rt-test-retry", source_root=REPO_ROOT, controller_entry=FAKE_ENTRY,
                               extra_env=environment, **{"mode": sv.SYNTHETIC, **(supervisor_options or {})})
        self.hardware = mx.HardwareExecution(radii=TEST_RADII)
        self.runner = mx.ProvenTargetRunner(hardware=self.hardware, supervisor=self.supervisor, capability_endpoints=ENDPOINTS,
                                            evidence=lambda record: None, observation_available=lambda: (True, None))
        self.coordinator = MissionCoordinator(str(self.data / mission.MISSIONS_FILE_NAME), self.hardware.adapter(),
                                              Slot(available=True).adapter(), data_mode="live", run_target=self.runner)
        self.runner.bind_recorder(self.coordinator)
        self.mission_id = None

    def _remove(self):
        self.coordinator.close()
        if self.supervisor.active_run() is not None and not self.supervisor.detached:
            self.supervisor.stop()
            deadline = time.monotonic() + WAIT_S
            while self.supervisor.active_run() is not None and time.monotonic() < deadline:
                time.sleep(0.02)
        self.supervisor.detach()
        for line in self.launches():
            pid = int(line.split()[0])
            for _ in range(200):
                if not sv.is_alive(pid):
                    break
                try:
                    os.kill(pid, signal.SIGINT)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def launches(self):
        return self.launch_log.read_text().splitlines() if self.launch_log.exists() else []

    def start(self):
        started = self.coordinator.start([dict(AUTO)])
        self.test.assertTrue(started["ok"], started)
        self.mission_id = started["mission_id"]
        return started

    def view(self):
        return self.coordinator.state(self.mission_id)

    def wait_until(self, predicate, what):
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.test.fail(f"{what} did not happen in time")

    def wait_for_hold(self, attempts=1):
        def held():
            view = self.view()
            return view if view and view["hold"] is not None and view["hold"]["attempt"] == attempts else None
        return self.wait_until(held, f"the hold after attempt {attempts}")

    def wait_for_end(self):
        self.test.assertTrue(self.coordinator.join(WAIT_S), "the mission did not end in time")
        return self.view()

    def stop(self):
        """The daemon's order: the mission's event, the supervisor's stop, the mission's own stop."""
        self.coordinator.cancel_now(origin="mission_stop")
        self.supervisor.stop(origin="operator_console")
        return self.coordinator.stop(origin="mission_stop")

    def kinds(self):
        """The command type of every accepted run, in order, from the supervisor's command journal."""
        runs = []
        for record in read_json_lines(self.data / "journal.jsonl")[0]:
            if record.get("status") == "accepted":
                runs.append(record.get("command_type"))
        return runs


class TheHoldAndTheRetry(unittest.TestCase):
    def test_an_align_failure_holds_then_retry_parks_and_a_fresh_cycle_places(self):
        stack = RetryStack(self, fail_cycles=1)
        stack.start()
        view = stack.wait_for_hold()
        self.assertEqual(view["state"], mission.MISSION_WAITING)
        self.assertEqual(view["waiting_reason"], mission.WAITING_ON_OPERATOR)
        hold = view["hold"]
        self.assertEqual((hold["outcome_class"], hold["reason_code"], hold["phase"]), ("retryable", "align_cube_not_detected", "ALIGN"))
        self.assertEqual(hold["next_actions"], ["retry", "stop"])
        self.assertEqual(hold["budget_left"], 1)
        self.assertEqual(len(hold["observation_ids"]), 1)
        self.assertEqual([item["name"] for item in hold["measurements"]], ["radial_mm", "lateral_mm"])
        self.assertEqual(hold["stop_state"], {"holding": False, "base_ahead": False, "moved": True})
        time.sleep(0.5)
        self.assertEqual(len(stack.launches()), 1, "nothing moves in a hold without a press")
        self.assertTrue(stack.coordinator.running, "the hold keeps the mission carried, so it keeps the run slot")

        answer = stack.coordinator.request_retry()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual((answer["target_id"], answer["attempt"]), ("cube", 1))
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        target = view["targets"][0]
        self.assertEqual(target["state"], mission.TARGET_PLACED)
        self.assertEqual(len(target["runs"]), 3)
        self.assertEqual(len(target["park_runs"]), 1)
        self.assertEqual(target["runs"][1], target["park_runs"][0], "the park runs between the two cycles")
        self.assertEqual(stack.kinds(), ["start_cycle", "return_arm", "start_cycle"], "two cycle runs and one exit run")
        self.assertEqual(len(stack.launches()), 3)
        self.assertEqual([entry["attempt"] for entry in target["attempts"]], [1])
        self.assertEqual(target["retries"][0]["origin"], "operator_console")
        self.assertIsNone(view["hold"])
        self.assertEqual(view["next_actions"], ["start"])

    def test_two_retryable_failures_spend_the_budget_and_hold_with_only_the_stop(self):
        stack = RetryStack(self, fail_cycles=2)
        stack.start()
        stack.wait_for_hold(1)
        self.assertTrue(stack.coordinator.request_retry()["ok"])
        view = stack.wait_for_hold(2)
        hold = view["hold"]
        self.assertEqual(hold["next_actions"], ["stop"])
        self.assertEqual(hold["budget_left"], 0)
        self.assertEqual(hold["run_id"], view["targets"][0]["runs"][2])
        self.assertEqual(hold["park_run_id"], view["targets"][0]["park_runs"][0])
        refused = stack.coordinator.request_retry()
        self.assertEqual(refused["reason_code"], mission_coordinator.RETRY_NOT_OFFERED)
        self.assertEqual(len(stack.launches()), 3)
        self.assertTrue(stack.stop()["stopping"])
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 3, "the stop in the hold spawned nothing")
        self.assertEqual(view["next_actions"], ["return_arm", "start"])

    def test_a_held_cube_never_parks(self):
        stack = RetryStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "OBJECT_SECURED", "KC_FAKE_FAIL_HOLDING": "1",
                                                     "KC_FAKE_FAIL_CODE": "retention_not_demonstrated"})
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual(hold["outcome_class"], mission.OUTCOME_OPERATOR_NEEDED)
        self.assertIs(hold["stop_state"]["holding"], True)
        self.assertEqual(hold["next_actions"], ["stop"])
        self.assertEqual(stack.coordinator.request_retry()["reason_code"], mission_coordinator.RETRY_NOT_OFFERED)
        stack.stop()
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(stack.supervisor.parks, [], "no park was ever asked for")
        self.assertEqual(len(stack.launches()), 1)
        self.assertEqual(view["next_actions"], ["open_gripper_by_hand", "return_arm", "start"])

    def test_a_stop_during_the_hold_ends_stopped_with_no_spawn(self):
        stack = RetryStack(self, fail_cycles=1)
        stack.start()
        stack.wait_for_hold()
        begun = time.monotonic()
        self.assertTrue(stack.stop()["stopping"])
        view = stack.wait_for_end()
        self.assertLess(time.monotonic() - begun, 2.0)
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1)
        self.assertEqual(stack.coordinator.request_retry()["reason_code"], mission_coordinator.MISSION_STOPPING)

    def test_a_stop_during_the_park_ends_stopped_and_no_fresh_cycle_is_spawned(self):
        stack = RetryStack(self, fail_cycles=1, step_s="0.4")
        stack.start()
        stack.wait_for_hold()
        self.assertTrue(stack.coordinator.request_retry()["ok"])
        stack.wait_until(lambda: (stack.supervisor.active_run() or {}).get("command_type") == "return_arm" and len(stack.launches()) == 2,
                         "the park running")
        self.assertTrue(stack.stop()["stopping"])
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        time.sleep(0.3)
        self.assertEqual(len(stack.launches()), 2, "the cycle and the park, and no fresh cycle after the stop")
        self.assertEqual(stack.kinds(), ["start_cycle", "return_arm"])
        self.assertEqual(view["next_actions"], ["return_arm", "start"])

    def test_a_retry_and_a_stop_pressed_together_end_stopped(self):
        stack = RetryStack(self, fail_cycles=1)
        stack.start()
        stack.wait_for_hold()
        stack.coordinator.cancel_now(origin="mission_stop")
        self.assertEqual(stack.coordinator.request_retry()["reason_code"], mission_coordinator.MISSION_STOPPING)
        stack.coordinator.stop(origin="mission_stop")
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1)

    def test_a_second_press_for_the_same_hold_is_refused(self):
        stack = RetryStack(self, fail_cycles=1, step_s="0.2")
        stack.start()
        stack.wait_for_hold()
        self.assertTrue(stack.coordinator.request_retry()["ok"])
        second = stack.coordinator.request_retry()
        self.assertIn(second["reason_code"], (mission_coordinator.RETRY_ALREADY_REQUESTED, mission_coordinator.MISSION_NOT_HELD))
        stack.wait_for_end()
        self.assertEqual(stack.kinds(), ["start_cycle", "return_arm", "start_cycle"])

    def test_retry_is_refused_when_nothing_is_held(self):
        stack = RetryStack(self, step_s="0.2")
        stack.start()
        stack.wait_until(lambda: stack.launches(), "the first run")
        self.assertEqual(stack.coordinator.request_retry()["reason_code"], mission_coordinator.MISSION_NOT_HELD)
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_COMPLETE)


class WhatTheHoldOffers(unittest.TestCase):
    def test_a_supervisor_that_cannot_park_offers_only_the_stop(self):
        stack = RetryStack(self, fail_cycles=1, parking=False)
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual(hold["next_actions"], ["stop"])
        self.assertEqual(stack.coordinator.request_retry()["reason_code"], mission_coordinator.RETRY_NOT_OFFERED)
        stack.stop()
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1)

    def test_an_envelope_refusal_before_motion_asks_for_the_block_to_be_moved_then_retries(self):
        stack = RetryStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "SEARCH", "KC_FAKE_FAIL_CODE": "search_placement_infeasible"})
        stack.start()
        hold = stack.wait_for_hold()["hold"]
        self.assertEqual(hold["outcome_class"], mission.OUTCOME_OPERATOR_NEEDED)
        self.assertEqual(hold["stop_state"], {"holding": False, "base_ahead": False, "moved": False})
        self.assertEqual(hold["next_actions"], ["move_block_then_retry", "stop"])
        self.assertTrue(stack.coordinator.request_retry()["ok"])
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_COMPLETE)

    def test_a_fatal_failure_ends_the_mission_as_before_with_its_attempt_on_the_record(self):
        stack = RetryStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_CODE": "perception_remote_timeout"})
        stack.start()
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertEqual(view["detail"], "run_outcome:failed:perception_remote_timeout")
        attempt = view["targets"][0]["attempts"][0]
        self.assertEqual(attempt["outcome_class"], mission.OUTCOME_FATAL)
        self.assertEqual(view["next_actions"], ["return_arm", "start"])
        self.assertEqual(len(stack.launches()), 1)

    def test_a_stop_during_a_run_records_where_the_run_left_the_robot(self):
        stack = RetryStack(self, step_s="0.3")
        stack.start()
        stack.wait_until(lambda: any(event["event"]["kind"] == "action.started"
                                     for event in read_json_lines(stack.data / "events.jsonl")[0]), "the first motion")
        stack.stop()
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        attempt = view["targets"][0]["attempts"][-1]
        self.assertEqual(attempt["outcome_class"], mission.OUTCOME_STOPPED)
        self.assertIs(attempt["stop_state"]["moved"], True)
        self.assertIn("return_arm", view["next_actions"])


class TheAutomaticRetryIsOff(unittest.TestCase):
    def test_it_ships_off(self):
        self.assertFalse(mission_coordinator.AUTOMATIC_RETRY)

    def test_switched_on_it_parks_and_retries_once_with_no_press(self):
        original = mission_coordinator.AUTOMATIC_RETRY
        mission_coordinator.AUTOMATIC_RETRY = True
        self.addCleanup(setattr, mission_coordinator, "AUTOMATIC_RETRY", original)
        stack = RetryStack(self, fail_cycles=1)
        stack.start()
        view = stack.wait_for_end()
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        self.assertEqual(view["targets"][0]["retries"][0]["origin"], "automatic")
        self.assertEqual(stack.kinds(), ["start_cycle", "return_arm", "start_cycle"])

    def test_switched_on_it_retries_once_and_then_holds(self):
        original = mission_coordinator.AUTOMATIC_RETRY
        mission_coordinator.AUTOMATIC_RETRY = True
        self.addCleanup(setattr, mission_coordinator, "AUTOMATIC_RETRY", original)
        stack = RetryStack(self, fail_cycles=2)
        stack.start()
        hold = stack.wait_for_hold(2)["hold"]
        self.assertEqual(hold["next_actions"], ["stop"])
        stack.stop()
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)
        self.assertEqual(stack.kinds(), ["start_cycle", "return_arm", "start_cycle"])

    def test_switched_on_it_never_parks_a_held_cube(self):
        original = mission_coordinator.AUTOMATIC_RETRY
        mission_coordinator.AUTOMATIC_RETRY = True
        self.addCleanup(setattr, mission_coordinator, "AUTOMATIC_RETRY", original)
        stack = RetryStack(self, fail_cycles=1, env={"KC_FAKE_FAIL_AT": "OBJECT_SECURED", "KC_FAKE_FAIL_HOLDING": "1",
                                                     "KC_FAKE_FAIL_CODE": "retention_not_demonstrated"})
        stack.start()
        stack.wait_for_hold()
        time.sleep(0.4)
        self.assertEqual(stack.supervisor.parks, [])
        stack.stop()
        self.assertEqual(stack.wait_for_end()["state"], mission.MISSION_STOPPED)



RETRYABLE_RECOVERY = {"outcome_class": mission.OUTCOME_RETRYABLE, "reason_code": "align_cube_not_detected", "phase": "ALIGN",
                      "measurements": [], "observation_ids": [],
                      "stop_state": {"holding": False, "base_ahead": False, "moved": True}}


class ScriptedRunTarget:
    """A run target that fails every attempt retryably and parks at once. No process is spawned: the hold's ORDER is what
    is under test, and the Event the hold waits on."""

    def __init__(self, evidence=None):
        self.calls, self.parks, self.evidence = 0, 0, evidence

    def can_park(self):
        return True

    def park(self, mission_id, target_id):
        self.parks += 1
        return {"ok": True, "run_id": f"park-{self.parks}", "reason_code": None}

    def __call__(self, execution, capability_available, wait_for_capability_change, target):
        self.calls += 1
        return {"target_state": mission.TARGET_FAILED, "run_id": f"run-{self.calls}", "evidence": self.evidence,
                "detail": "run_outcome:failed:align_cube_not_detected", "recovery": dict(RETRYABLE_RECOVERY)}


class NoResumeWithoutARecordedPress(unittest.TestCase):
    """The hold resumes only on a Retry WRITTEN DOWN for the attempt it holds on. A press accepted in the window before the
    automatic retry's own request, or a wake-up with no record behind it, never moves the robot on its own."""

    def setUp(self):
        from tests.test_mission_coordinator import FakeCandidates, recording_execution
        self.tmp = tempfile.mkdtemp(prefix="kc-retry-order-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.target = ScriptedRunTarget()
        capability = mission_coordinator.CapabilityAdapter(available=lambda: True, submit=lambda url, payload: {},
                                                           observe=lambda: False, candidates=FakeCandidates())
        self.coordinator = MissionCoordinator(str(Path(self.tmp) / mission.MISSIONS_FILE_NAME), recording_execution(),
                                              capability, data_mode="synthetic", run_target=self.target)
        self.addCleanup(self._end)

    def _end(self):
        self.coordinator.stop()
        self.coordinator.join(5)

    def _start(self):
        return self.coordinator.start([{"target_id": "cube", "label": "cube", "reachable": True, "requires_capability": False}])

    def _wait_for_attempts(self, count):
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            view = self.coordinator.state()
            if (view and view["state"] == mission.MISSION_WAITING and view.get("waiting_reason") == mission.WAITING_ON_OPERATOR
                    and len(view["targets"][0]["attempts"]) >= count):
                return view
            time.sleep(0.02)
        self.fail(f"no hold after {count} attempts: {self.coordinator.state()}")

    def test_a_press_before_the_automatic_request_is_the_one_retry_and_the_spent_hold_waits(self):
        original = mission_coordinator.AUTOMATIC_RETRY
        mission_coordinator.AUTOMATIC_RETRY = True
        self.addCleanup(setattr, mission_coordinator, "AUTOMATIC_RETRY", original)
        pressed, append = [], self.coordinator._append

        def append_then_press(record):
            append(record)
            # The operator's press, after the FIRST hold's waiting record and before the automatic request.
            if not pressed and record.get("waiting_reason") == mission.WAITING_ON_OPERATOR:
                pressed.append(self.coordinator.request_retry())

        self.coordinator._append = append_then_press
        self._start()
        self._wait_for_attempts(2)
        time.sleep(4 * mission_coordinator.HOLD_POLL_S)
        view = self.coordinator.state()
        self.assertTrue(pressed and pressed[0]["ok"], pressed)
        self.assertEqual(self.target.calls, 2)
        self.assertEqual(self.target.parks, 1)
        target = view["targets"][0]
        self.assertEqual([(entry["attempt"], entry["origin"]) for entry in target["retries"]], [(1, "operator_console")])
        self.assertEqual([(entry["attempt"], entry["next_actions"], entry["budget_left"]) for entry in target["attempts"]],
                         [(1, ["retry", "stop"], 1), (2, ["stop"], 0)])
        self.assertEqual(view["state"], mission.MISSION_WAITING)

    def test_a_held_attempt_keeps_its_evidence_on_the_record_when_it_is_stopped(self):
        self.target.evidence = {"selection_observation_id": "obs-front-0003", "capability": {"generation": 2}}
        started = self._start()
        self._wait_for_attempts(1)
        self.coordinator.stop()
        self.coordinator.join(5)
        records, _ = read_json_lines(str(Path(self.tmp) / mission.MISSIONS_FILE_NAME))
        attempts = [record for record in records if record.get("rec") == "mission_target_attempt"]
        self.assertEqual([record.get("evidence") for record in attempts], [self.target.evidence])
        view = self.coordinator.state(started["mission_id"])
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(view["targets"][0]["evidence"], self.target.evidence)

    def test_a_wake_up_with_no_retry_on_the_record_does_not_resume(self):
        started = self._start()
        self._wait_for_attempts(1)
        self.coordinator._retry_event_for(started["mission_id"]).set()
        time.sleep(4 * mission_coordinator.HOLD_POLL_S)
        view = self.coordinator.state()
        self.assertEqual((self.target.calls, self.target.parks), (1, 0))
        self.assertEqual(view["targets"][0]["retries"], [])
        self.assertEqual(view["state"], mission.MISSION_WAITING)
        # A real press still retries.
        self.assertTrue(self.coordinator.request_retry()["ok"])
        self._wait_for_attempts(2)
        self.assertEqual((self.target.calls, self.target.parks), (2, 1))



class AHoldNobodyCarries(unittest.TestCase):
    """After a restart the held mission is reported as it was left, and the served view offers only what the runtime will
    accept: a Retry needs a thread to carry it (mission_not_carried), so the view offers the Stop."""

    def test_the_view_after_a_restart_offers_no_retry(self):
        from tests.test_mission_coordinator import FakeCandidates, recording_execution
        tmp = tempfile.mkdtemp(prefix="kc-retry-restart-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = str(Path(tmp) / mission.MISSIONS_FILE_NAME)
        journal = Journal(path)
        t0 = "2026-09-27T00:00:00Z"
        actions = mission.next_actions(mission.OUTCOME_RETRYABLE, RETRYABLE_RECOVERY["stop_state"], retry_left=True)
        self.assertIn(mission.NEXT_RETRY, actions)
        for record in (mission.mission_started("m1", [{"target_id": "cube", "label": "cube", "reachable": True,
                                                        "requires_capability": False}],
                                               t_wall_utc=t0, data_mode="synthetic", performs_motion=False, execution_label="x"),
                       mission.target_selected("m1", "cube", t_wall_utc=t0),
                       mission.target_attempt("m1", "cube", attempt=1, run_id="run-1", park_run_id=None,
                                              outcome_class=mission.OUTCOME_RETRYABLE, reason_code="align_cube_not_detected",
                                              phase="ALIGN", measurements=[], observation_ids=[],
                                              stop_state=RETRYABLE_RECOVERY["stop_state"], next_actions=actions, budget_left=1,
                                              t_wall_utc=t0),
                       mission.target_waiting("m1", "cube", mission.WAITING_ON_OPERATOR, t_wall_utc=t0)):
            journal.append(record)
        capability = mission_coordinator.CapabilityAdapter(available=lambda: True, submit=lambda url, payload: {},
                                                           observe=lambda: False, candidates=FakeCandidates())
        coordinator = MissionCoordinator(path, recording_execution(), capability, data_mode="synthetic",
                                         run_target=ScriptedRunTarget())
        for view in (coordinator.recover(), coordinator.state()):
            self.assertEqual(view["state"], mission.MISSION_WAITING)
            self.assertEqual(view["hold"]["next_actions"], [mission.NEXT_STOP])
            self.assertNotIn(mission.NEXT_RETRY, view["next_actions"])
            # The record itself is unchanged: the attempt still says what was offered when it was written.
            self.assertEqual(view["targets"][0]["attempts"][0]["next_actions"], actions)
        self.assertEqual(coordinator.request_retry()["reason_code"], mission_coordinator.MISSION_NOT_CARRIED)


if __name__ == "__main__":
    unittest.main()
