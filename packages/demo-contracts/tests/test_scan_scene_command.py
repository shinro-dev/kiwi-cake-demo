# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""scan_scene: the Scan scene command of docs/autonomous-demo/overnight-plan-2026-09-26.md (section 2.4, track P0b). It is
a MOTION command (pan-only views at the low-pose shape, the controller's --do scan), so it takes plan_only and nothing
else: no holding flag, because a scan starts from the saved low pose with the gripper open and nothing held, and a flag
the controller would ignore is not accepted. It is an additive v1 command type: the non-motion observe keeps its meaning.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "python"))

import demo_contracts  # noqa: E402


def command(args) -> dict:
    return {"contract_version": "1", "command_id": "synthetic-command-0009", "type": "scan_scene", "args": args,
            "client": {"session_id": "synthetic-client-0001", "contract_version": "1"}}


class ScanScene(unittest.TestCase):
    def test_it_is_a_command_type_of_the_contract(self):
        self.assertIn("scan_scene", demo_contracts.load_schema("common#/$defs/command_type")["enum"])

    def test_it_takes_plan_only_and_nothing_else(self):
        for plan_only in (True, False):
            self.assertEqual([], demo_contracts.validate("command", command({"plan_only": plan_only})))
        for args, fragment in (({}, "missing required property 'plan_only'"), ({"plan_only": False, "holding": False}, "unexpected property 'holding'"),
                               ({"plan_only": "false"}, "expected boolean"), ({"plan_only": False, "sectors_deg": [15]}, "unexpected property 'sectors_deg'")):
            with self.subTest(args=args):
                errors = demo_contracts.validate("command", command(args))
                self.assertTrue(any(fragment in error for error in errors), errors)

    def test_the_fixture_is_the_example_and_observe_keeps_its_shape(self):
        fixture = json.loads((PACKAGE / "fixtures" / "command" / "scan_scene.json").read_text(encoding="utf-8"))
        self.assertEqual(("scan_scene", {"plan_only": False}), (fixture["type"], fixture["args"]))
        observe = json.loads((PACKAGE / "fixtures" / "command" / "observe.json").read_text(encoding="utf-8"))
        self.assertEqual({"holding": False}, observe["args"])
        self.assertTrue(demo_contracts.validate("command", {**observe, "args": {"plan_only": False}}), "observe must not take a scan's arguments")

    def test_the_generated_bindings_name_it(self):
        generated = (PACKAGE / "ts" / "generated.ts").read_text(encoding="utf-8")
        self.assertIn("'scan_scene'", generated)
        self.assertIn("ArgsScanScene", generated)


if __name__ == "__main__":
    unittest.main()
