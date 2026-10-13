# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""scan_scene in the runtime (docs/autonomous-demo/overnight-plan-2026-09-26.md section 2.4, track P0b): a MOTION command
type, mapped to the controller's --do scan with --plan-only when asked, a command capability and therefore a motion
capability (so the robot resident's session gate refuses it exactly like start_cycle while observe stays available),
blocked while a command is unresolved, and a run kind of the command-line client. observe keeps its meaning."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_runtime import cli  # noqa: E402
from demo_runtime import read_models as rm  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402

import demo_contracts  # noqa: E402  (importable once demo_runtime.journal was imported)
from test_supervisor import Harness, command  # noqa: E402


def scan(command_id: str, plan_only: bool = False) -> dict:
    return command(command_id, "scan_scene", {"plan_only": plan_only})


class TheCommandType(unittest.TestCase):
    def test_every_command_type_of_the_contract_is_a_capability_or_stop(self):
        types = demo_contracts.load_schema("common#/$defs/command_type")["enum"]
        self.assertEqual(set(types) - {"stop"}, set(rm.COMMAND_CAPABILITIES))
        self.assertEqual(set(types) - {"stop"}, set(sv.COMMANDS_BLOCKED_WHILE_UNRESOLVED))

    def test_it_is_a_motion_capability_and_observe_is_still_not(self):
        self.assertIn("scan_scene", rm.MOTION_CAPABILITIES)
        self.assertNotIn("observe", rm.MOTION_CAPABILITIES)
        self.assertIs(sv.MOTION_COMMAND_TYPES, rm.MOTION_CAPABILITIES)

    def test_the_session_gate_blocks_it_like_start_and_leaves_observe_as_it_was(self):
        served = {item["id"]: item for item in rm.capabilities(block_code=None, run_active=False, replay_only=False,
                                                              motion_block=("robot_session_not_confirmed", "the robot session is not confirmed"))}
        self.assertEqual(("blocked", "robot_session_not_confirmed", "robot"),
                         (served["scan_scene"]["state"], served["scan_scene"]["reason_code"], served["scan_scene"]["dependency"]))
        self.assertEqual(served["start_cycle"]["state"], served["scan_scene"]["state"])
        self.assertEqual("enabled", served["observe"]["state"])
        for item in served.values():
            self.assertEqual([], demo_contracts.validate("capability", item))

    def test_it_maps_to_do_scan_with_plan_only_when_asked_and_never_a_holding_flag(self):
        self.assertEqual("scan", sv.action_text(scan("scan-0001")))
        for plan_only, expected in ((False, ["--do", "scan"]), (True, ["--do", "scan", "--plan-only"])):
            with self.subTest(plan_only=plan_only):
                argv = sv.controller_arguments(scan("scan-0002", plan_only), "scan-id-0001", address="robot.example", robot_id="robot-example", pi_ssh="")
                self.assertEqual(expected, argv[:len(expected)])
                self.assertNotIn("--holding", argv)
                self.assertEqual(argv[len(expected)], "--remote-ip")

    def test_a_scan_takes_the_lease_like_any_motion_command_unless_plan_only(self):
        self.assertTrue(sv.lease_required(sv.LIVE, True, scan("scan-0003")))
        self.assertFalse(sv.lease_required(sv.LIVE, True, scan("scan-0004", plan_only=True)))
        self.assertFalse(sv.lease_required(sv.SYNTHETIC, True, scan("scan-0005")))

    def test_it_is_not_an_autonomous_run_for_the_owner_label(self):
        self.assertEqual(("start_cycle",), sv.AUTONOMOUS_TYPES)

    def test_the_command_line_client_submits_it_as_the_run_kind_scan(self):
        self.assertEqual(("scan_scene", "plan_only"), cli.RUN_KINDS["scan"])
        self.assertEqual(("observe", "holding"), cli.RUN_KINDS["observe"])


class TheSubmit(unittest.TestCase):
    def test_refused_by_the_session_gate_before_anything_is_started(self):
        calls = []

        def gate():
            calls.append(1)
            return ("robot_session_not_confirmed", "not confirmed"), None

        harness = Harness(self, motion_gate=gate)
        answer = harness.supervisor.submit(scan("scan-gate-0001"))
        self.assertEqual(("rejected", "robot_session_not_confirmed"), (answer["status"], answer["reason_code"]))
        self.assertEqual(1, len(calls))
        self.assertEqual([], harness.launches())

    def test_blocked_while_a_command_is_unresolved(self):
        harness = Harness(self)
        harness.supervisor.journal.append({"rec": "command", "command_id": "lost-0001", "status": "accepted", "run_id": "console-20260101T000000Z-lost-000",
                                           "scan_id": "console-20260101T000000Z-lost-000", "command_type": "scan_scene", "reason_code": None})
        second = harness.new_supervisor()
        self.assertEqual([("lost-0001", "uncertain")], second.reconcile())
        self.assertEqual("owner_unresolved", second.submit(scan("scan-lost-0002"))["reason_code"])
        self.assertEqual([], harness.launches())

    def test_a_malformed_scan_is_rejected_by_the_contract(self):
        harness = Harness(self)
        answer = harness.supervisor.submit(command("scan-bad-0001", "scan_scene", {"plan_only": False, "holding": False}))
        self.assertEqual("rejected", answer["status"])
        self.assertEqual([], harness.launches())


if __name__ == "__main__":
    unittest.main()
