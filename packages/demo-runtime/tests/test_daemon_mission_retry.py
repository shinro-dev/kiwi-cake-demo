#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_daemon_mission_retry.py: the daemon's mission_retry op, driven through the daemon's own
# dispatch (every answer passes the sanitizer a socket client gets). The run target is a scripted stand-in with a park, so
# the op's guards and its answers are what is under test; test_mission_retry.py runs the real runner against the fake
# controller. No resident, no robot, no browser, no network.
# Run directly: python3 packages/demo-runtime/tests/test_daemon_mission_retry.py
from __future__ import annotations

import dataclasses
import sys
import threading
import time
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(PACKAGE_DIR.parents[1]))

from demo_runtime import mission, mission_coordinator  # noqa: E402
from tests.test_mission_ops import Core, Deployment, wait_for  # noqa: E402

NOTHING_HELD = {"holding": False, "base_ahead": False, "moved": True}
TARGETS = [{"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": False}]


class ScriptedTarget:
    """A run target that fails its first attempt at ALIGN (retryable, nothing held) and places the next; it parks."""

    def __init__(self, *, park_ok=True, can_park=True):
        self.calls, self.parks, self.park_ok, self._can_park = 0, 0, park_ok, can_park
        self.release = threading.Event()

    def can_park(self):
        return self._can_park

    def park(self, mission_id, target_id):
        self.parks += 1
        return {"ok": self.park_ok, "run_id": f"park-{self.parks}", "reason_code": None if self.park_ok else "park_outcome:failed:5"}

    def __call__(self, execution, capability_available, wait_for_capability_change, target):
        self.calls += 1
        if self.calls == 1:
            return {"target_state": mission.TARGET_FAILED, "run_id": "run-1", "detail": "run_outcome:failed:align_cube_not_detected",
                    "evidence": None, "recovery": {"outcome_class": mission.OUTCOME_RETRYABLE, "reason_code": "align_cube_not_detected",
                                                   "phase": "ALIGN", "measurements": [{"name": "radial_mm", "value": 14.5, "unit": "mm"}],
                                                   "observation_ids": ["obs-20260926T212418Z-wrist-0007"], "stop_state": NOTHING_HELD}}
        return {"target_state": mission.TARGET_PLACED, "run_id": f"run-{self.calls}", "detail": None, "evidence": None,
                "recovery": {"outcome_class": mission.OUTCOME_PLACED, "stop_state": NOTHING_HELD}}


class Connection:
    def __init__(self, hello_ok):
        self.hello_ok = hello_ok


def held(core):
    view = core.ask("mission_state")
    return view if view.get("hold") is not None else None


class TheRetryOp(unittest.TestCase):
    def core(self, target):
        return Core(self, config=Deployment(self).config(), run_target=target)

    def test_a_held_mission_retries_through_the_op_and_completes(self):
        target = ScriptedTarget()
        core = self.core(target)
        started = core.ask("mission_start", {"targets": TARGETS})
        self.assertTrue(started["ok"], started)
        wait_for(self, lambda: held(core), "the hold")
        # The words are read from the coordinator itself: this test's configuration names the ssh user "operator", and the
        # sanitizer every op answer passes redacts that token wherever it appears, inside a word too.
        record = core.core.mission.state()
        self.assertEqual(record["waiting_reason"], "operator_decision")
        self.assertEqual(record["hold"]["next_actions"], ["retry", "stop"])
        answer = core.ask("mission_retry", {"mission_id": started["mission_id"]})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual((answer["mission_id"], answer["attempt"]), (started["mission_id"], 1))
        view = wait_for(self, lambda: (lambda v: v if v.get("state") == mission.MISSION_COMPLETE else None)(core.ask("mission_state")),
                        "the mission's end")
        self.assertEqual(target.parks, 1)
        self.assertEqual(view["targets"][0]["attempts"][0]["run_id"], "run-1")
        self.assertEqual(view["targets"][0]["runs"], ["run-2"], "the stand-in records no run start; the placed outcome names its run")
        self.assertEqual(core.core.mission.state()["targets"][0]["retries"][0]["origin"], "operator_console")

    def test_a_retry_with_no_hold_is_refused_by_name(self):
        core = self.core(ScriptedTarget())
        self.assertEqual(core.ask("mission_retry")["reason_code"], "unknown_mission")

    def test_a_stopped_mission_is_not_retried(self):
        target = ScriptedTarget()
        core = self.core(target)
        core.ask("mission_start", {"targets": TARGETS})
        wait_for(self, lambda: held(core), "the hold")
        self.assertTrue(core.ask("mission_stop")["stopping"])
        answer = core.ask("mission_retry")
        self.assertFalse(answer["ok"])
        self.assertIn(answer["reason_code"], (mission_coordinator.MISSION_STOPPING, mission_coordinator.MISSION_NOT_HELD))
        time.sleep(0.3)
        self.assertEqual((target.calls, target.parks), (1, 0))

    def test_a_run_target_that_cannot_park_offers_no_retry(self):
        target = ScriptedTarget(can_park=False)
        core = self.core(target)
        core.ask("mission_start", {"targets": TARGETS})
        view = wait_for(self, lambda: held(core), "the hold")
        self.assertEqual(view["hold"]["next_actions"], ["stop"])
        self.assertEqual(core.ask("mission_retry")["reason_code"], mission_coordinator.RETRY_NOT_OFFERED)
        core.ask("mission_stop")

    def test_a_failed_park_ends_the_mission_failed_and_starts_nothing_else(self):
        target = ScriptedTarget(park_ok=False)
        core = self.core(target)
        core.ask("mission_start", {"targets": TARGETS})
        wait_for(self, lambda: held(core), "the hold")
        self.assertTrue(core.ask("mission_retry")["ok"])
        view = wait_for(self, lambda: (lambda v: v if v.get("state") == mission.MISSION_FAILED else None)(core.ask("mission_state")),
                        "the mission's end")
        self.assertEqual(view["detail"], "mission_park_failed:park_outcome:failed:5")
        self.assertEqual(target.calls, 1)

    def test_a_press_the_supervisor_would_refuse_is_refused_and_the_hold_remains(self):
        target = ScriptedTarget()
        core = self.core(target)
        # A mission that can move the robot is the one a press is admitted for; the stand-in execution says it can.
        core.core.mission._execution = dataclasses.replace(core.core.mission._execution, performs_motion=True)
        started = core.ask("mission_start", {"targets": TARGETS})
        self.assertTrue(started["ok"], started)
        wait_for(self, lambda: held(core), "the hold")
        supervisor, real_block = core.core.supervisor, core.core.supervisor.block
        supervisor.block = lambda action=None, *, run_slot_gate=True: ("owner_external", None)
        answer = core.ask("mission_retry", {"mission_id": started["mission_id"]})
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, "owner_external"))
        time.sleep(0.3)
        record = core.core.mission.state()
        self.assertEqual((record["state"], record["waiting_reason"]), (mission.MISSION_WAITING, "operator_decision"))
        self.assertEqual(record["hold"]["next_actions"], ["retry", "stop"], "the refused press spent nothing")
        self.assertEqual((target.calls, target.parks), (1, 0))
        supervisor.block = real_block
        self.assertTrue(core.ask("mission_retry", {"mission_id": started["mission_id"]})["ok"])
        wait_for(self, lambda: (lambda v: v if v.get("state") == mission.MISSION_COMPLETE else None)(core.ask("mission_state")),
                 "the mission's end")
        self.assertEqual(target.parks, 1)

    def test_the_body_names_at_most_a_mission_id(self):
        core = self.core(ScriptedTarget())
        self.assertEqual(core.ask("mission_retry", {"candidate_key": "x"})["reason_code"], "bad_request")
        self.assertEqual(core.ask("mission_retry", {"mission_id": 7})["reason_code"], "bad_request")

    def test_a_connection_that_has_not_said_hello_is_refused(self):
        core = self.core(ScriptedTarget())
        answer = core.core.handle("mission_retry", {}, Connection(hello_ok=False))
        self.assertEqual(answer["reason_code"], "hello_required")
        self.assertNotEqual(core.core.handle("mission_retry", {}, Connection(hello_ok=True))["reason_code"], "hello_required")

    def test_a_runtime_with_no_mission_surface_says_so(self):
        core = Core(self, config=None)
        answer = core.ask("mission_retry")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], core.ask("mission_state")["reason_code"])


if __name__ == "__main__":
    unittest.main()
