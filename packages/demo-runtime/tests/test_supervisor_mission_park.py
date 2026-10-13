# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""supervisor.start_mission_park: the park before a mission's Retry (demo_runtime.mission_execution.run_park).

The UNCHANGED `--do exit` (contract command return_arm), admitted exactly as a mission's cycle run is admitted, with the
mission's fields on the accept record, the run named on the mission's record before the spawn, and NO mission target file
(the controller refuses one for anything but `--do cycle`). A mission's cycle run keeps its argv and its target file.
"""
import threading
import time
import unittest
from unittest import mock

from .test_supervisor import MISSION_ID, Harness, mission_run, sv  # noqa: F401  (the shared harness and fixtures)

from demo_runtime.journal import read_json_lines


def park_run(cancel=None, *, target_id="cube_near", started=None):
    return sv.MissionRun(mission_id=MISSION_ID, target_id=target_id, cancel=cancel or threading.Event(), write_target=None,
                         on_run_started=(started.append if started is not None else None))


class Spy:
    """Wraps the supervisor's argv builder and child environment, so a test reads what the child was handed."""

    def __init__(self, test, supervisor):
        self.argv, self.env = [], []
        arguments, environment = sv.controller_arguments, supervisor._child_environment

        def spy_arguments(*args, **kwargs):
            built = arguments(*args, **kwargs)
            self.argv.append(list(built))
            return built

        def spy_environment(run, bundle):
            built = environment(run, bundle)
            self.env.append(dict(built))
            return built

        patcher = mock.patch.object(sv, "controller_arguments", spy_arguments)
        patcher.start()
        test.addCleanup(patcher.stop)
        supervisor._child_environment = spy_environment


class AMissionParkIsTheUnchangedExitUnderTheMissionsAdmission(unittest.TestCase):

    def test_the_park_spawns_do_exit_with_no_target_file_and_names_the_run_first(self):
        started = []
        admitted = {"session_uuid": "cd" * 8}
        harness = Harness(self, motion_gate=lambda: (None, admitted))
        spy = Spy(self, harness.supervisor)
        answer = harness.supervisor.start_mission_park(park_run(started=started))
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(set(answer), {"ok", "reason_code", "command_id", "run_id", "status"})
        harness.wait_idle()
        run_id = answer["run_id"]
        self.assertEqual(started, [run_id], "the mission's record names the park run before the spawn")
        self.assertEqual(spy.argv[0][:2], ["--do", "exit"])
        self.assertNotIn(sv.MISSION_TARGET_ENV, spy.env[0], "a park names no mission target to the controller")
        self.assertFalse((harness.data / "history" / run_id / sv.MISSION_TARGET_FILE).exists())
        accepted = [record for record in read_json_lines(harness.data / "journal.jsonl")[0]
                    if record.get("command_id") == answer["command_id"] and record.get("status") == "accepted"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["command"]["type"], "return_arm")
        self.assertEqual(accepted[0]["command"]["args"], {"plan_only": False})
        self.assertEqual(accepted[0]["mission"], {"mission_id": MISSION_ID, "target_id": "cube_near"})
        self.assertEqual(accepted[0]["robot_admission"], admitted)
        completion = harness.completion(answer["command_id"])
        self.assertEqual(completion["exit_status"], 0)
        self.assertEqual(len(harness.launches()), 1)

    def test_the_argv_of_a_park_equals_the_return_arm_commands(self):
        harness = Harness(self)
        spy = Spy(self, harness.supervisor)
        self.assertTrue(harness.supervisor.start_mission_park(park_run())["ok"])
        harness.wait_idle()
        submitted = harness.supervisor.submit({"contract_version": sv.CONTRACT_VERSION, "command_id": "park-argv-0001", "type": "return_arm",
                                               "args": {"plan_only": False}, "client": {"session_id": "s", "contract_version": sv.CONTRACT_VERSION}})
        self.assertNotEqual(submitted.get("status"), "rejected", submitted)
        harness.wait_idle()
        strip = [[value for index, value in enumerate(argv) if index == 0 or argv[index - 1] != "--scan-id"] for argv in spy.argv]
        self.assertEqual(strip[0], strip[1], "the park is the Return arm command's argv, the scan id aside")

    def test_a_cycle_run_of_a_mission_still_carries_its_target_file_and_its_argv(self):
        harness = Harness(self)
        spy = Spy(self, harness.supervisor)
        answer = harness.supervisor.start_mission_run(mission_run())
        self.assertTrue(answer["ok"], answer)
        harness.wait_idle()
        self.assertEqual(spy.argv[0][:2], ["--do", "cycle"])
        self.assertIn(sv.MISSION_TARGET_ENV, spy.env[0])
        self.assertTrue((harness.data / "history" / answer["run_id"] / sv.MISSION_TARGET_FILE).exists())

    def test_a_park_with_a_target_writer_is_refused_before_anything(self):
        harness = Harness(self)
        with self.assertRaises(ValueError):
            harness.supervisor.start_mission_park(mission_run())
        self.assertEqual(harness.launches(), [])

    def test_a_cycle_run_without_a_target_writer_is_never_started(self):
        harness = Harness(self)
        run = sv.MissionRun(mission_id=MISSION_ID, target_id="cube_near", cancel=threading.Event(), write_target=None)
        answer = harness.supervisor.start_mission_run(run)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], sv.MISSION_TARGET_REFUSED)
        self.assertEqual(harness.launches(), [])

    def test_a_park_the_mission_cannot_name_is_never_started_and_says_why(self):
        def refuses(run_id):
            raise RuntimeError("the mission record could not be written")

        harness = Harness(self)
        run = sv.MissionRun(mission_id=MISSION_ID, target_id="cube_near", cancel=threading.Event(), write_target=None, on_run_started=refuses)
        answer = harness.supervisor.start_mission_park(run)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], sv.MISSION_TARGET_REFUSED)
        self.assertEqual(harness.launches(), [])
        texts = [event["payload"]["text"] for event in harness.events()
                 if event["kind"] == "diagnostic.text" and event.get("reason_code") == sv.MISSION_TARGET_REFUSED]
        self.assertEqual(len(texts), 1)
        self.assertNotIn("target file", texts[0], "a park has no target file to blame")
        self.assertIn("park", texts[0])

    def test_the_park_goes_through_the_motion_gate_and_block(self):
        harness = Harness(self, motion_gate=lambda: (("robot_session_not_confirmed", "not confirmed"), None))
        answer = harness.supervisor.start_mission_park(park_run())
        self.assertEqual((answer["ok"], answer["status"], answer["reason_code"]), (False, "rejected", "robot_session_not_confirmed"))
        self.assertEqual(harness.supervisor.table()[answer["command_id"]]["mission"], {"mission_id": MISSION_ID, "target_id": "cube_near"})
        self.assertEqual(harness.supervisor.table()[answer["command_id"]]["command_type"], "return_arm")
        blocked = Harness(self, run_slot_gate=lambda: "mission_running", external_probe=lambda: [4242])
        self.assertEqual(blocked.supervisor.start_mission_park(park_run())["reason_code"], "owner_external")
        self.assertEqual(harness.launches() + blocked.launches(), [])

    def test_only_the_missions_own_run_slot_gate_is_left_out(self):
        harness = Harness(self, run_slot_gate=lambda: "mission_running")
        answer = harness.supervisor.start_mission_park(park_run())
        self.assertTrue(answer["ok"], answer)
        harness.wait_idle()

    def test_a_stopped_mission_starts_no_park(self):
        cancel = threading.Event()
        cancel.set()
        harness = Harness(self)
        answer = harness.supervisor.start_mission_park(park_run(cancel))
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, sv.MISSION_STOPPING))
        self.assertEqual(harness.launches(), [])

    def test_a_stop_before_publication_starts_no_park(self):
        cancel = threading.Event()
        harness = Harness(self)
        calls = []

        def gate():
            calls.append(1)
            if len(calls) == 2:
                cancel.set()
                self.assertIsNone(harness.supervisor.stop())
            return None, {"session_uuid": "ab" * 8}

        harness.supervisor._motion_gate = gate
        answer = harness.supervisor.start_mission_park(park_run(cancel))
        self.assertEqual((answer["status"], answer["reason_code"]), ("rejected", sv.MISSION_STOPPED_AT_PUBLICATION))
        self.assertEqual(harness.launches(), [])

    def test_a_stop_during_the_park_is_a_sigint_and_the_park_ends_stopped(self):
        cancel = threading.Event()
        harness = Harness(self, step_s="0.3")
        answer = harness.supervisor.start_mission_park(park_run(cancel))
        self.assertTrue(answer["ok"], answer)
        deadline = time.monotonic() + 10.0
        while not any(event["kind"] == "action.started" for event in harness.events()) and time.monotonic() < deadline:
            time.sleep(0.02)
        cancel.set()
        stopped = harness.supervisor.stop()
        self.assertEqual(stopped["run_id"], answer["run_id"])
        harness.wait_idle()
        completion = harness.completion(answer["command_id"])
        self.assertEqual((completion["outcome"], completion["exit_status"]), ("stopped", 4))

