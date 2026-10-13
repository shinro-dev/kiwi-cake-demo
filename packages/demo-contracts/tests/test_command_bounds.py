# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The command schema carries the controller's numeric bounds, so an out-of-bounds manual step is refused at the API and
never reaches the controller. The numbers below are the controller's own (bin/laptop/manipulation/run_single_cube_cycle.py:
STEP_MAX_PAN_DEG 6.0, STEP_MAX_FORWARD_M 0.060, STEP_MAX_DZ_M 0.030, BASE_PULSE_S 0.2..0.8); the last test reads them
from that file so that a change there cannot leave this contract silently wider than the controller.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
sys.path.insert(0, str(PACKAGE / "python"))

import demo_contracts  # noqa: E402


def command(type_: str, args: dict) -> dict:
    return {"contract_version": "1", "command_id": "synthetic-command-0001", "type": type_, "args": args, "client": {"session_id": "synthetic-client-0001", "contract_version": "1"}}


class Bounds(unittest.TestCase):
    def accepted(self, type_: str, args: dict) -> None:
        self.assertEqual(demo_contracts.validate("command", command(type_, args)), [], f"{type_} {args} should be accepted")

    def refused(self, type_: str, args: dict, fragment: str) -> None:
        errors = demo_contracts.validate("command", command(type_, args))
        self.assertTrue(any(fragment in error for error in errors), f"{type_} {args}: no error mentions {fragment!r}: {errors}")

    def test_pan_is_non_zero_and_at_most_six_degrees(self):
        for deg in (6, -6, 6.0, 0.01, -0.01, 3.5):
            self.accepted("manual_pan", {"deg": deg, "holding": False, "plan_only": False})
        for deg, fragment in ((6.01, "above the maximum 6"), (-6.01, "below the minimum -6"), (0, "must not be zero"), (0.0, "must not be zero"), (-0.0, "must not be zero"),
                              (True, "expected number, got boolean"), ("3", "expected number, got string"), (None, "expected number, got null"), (float("nan"), "not a finite JSON number")):
            self.refused("manual_pan", {"deg": deg, "holding": False, "plan_only": False}, fragment)

    def test_move_is_bounded_on_both_axes_and_not_both_zero(self):
        for forward, dz in ((60, 30), (-60, -30), (60, 0), (0, 30), (0, -0.5), (0.5, 0)):
            self.accepted("manual_move", {"forward_mm": forward, "dz_mm": dz, "holding": True, "plan_only": False})
        for forward, dz, fragment in ((60.5, 0, "$.args.forward_mm: 60.5 is above the maximum 60"), (-61, 0, "$.args.forward_mm: -61 is below the minimum -60"),
                                      (0, 30.5, "$.args.dz_mm: 30.5 is above the maximum 30"), (10, -31, "$.args.dz_mm: -31 is below the minimum -30"),
                                      (0, 0, "both zero"), (0.0, 0.0, "both zero"), (0, -0.0, "both zero")):
            self.refused("manual_move", {"forward_mm": forward, "dz_mm": dz, "holding": True, "plan_only": False}, fragment)
        self.refused("manual_move", {"forward_mm": 10, "holding": True, "plan_only": False}, "missing required property 'dz_mm'")

    def test_grip_is_one_of_the_two_proven_values(self):
        for action in ("latch", "open"):
            self.accepted("manual_grip", {"action": action, "plan_only": False})
        for action in ("close", "LATCH", "", 1, None):
            self.refused("manual_grip", {"action": action, "plan_only": False}, "$.args.action")

    def test_base_pulse_is_signed_seconds_between_a_fifth_and_four_fifths(self):
        for seconds in (0.2, 0.8, -0.2, -0.8, 0.5, -0.5):
            self.accepted("manual_base_pulse", {"seconds": seconds, "holding": False, "plan_only": False})
        for seconds in (0.19, 0.81, -0.19, -0.81, 0, 1.5, -1.5, True, "0.5"):
            self.refused("manual_base_pulse", {"seconds": seconds, "holding": False, "plan_only": False}, "$.args.seconds: matches none of the 2 alternatives")

    def test_holding_and_plan_only_are_explicit_booleans_with_no_default(self):
        self.refused("manual_pan", {"deg": 1, "plan_only": False}, "missing required property 'holding'")
        self.refused("manual_pan", {"deg": 1, "holding": False}, "missing required property 'plan_only'")
        self.refused("manual_pan", {"deg": 1, "holding": 0, "plan_only": False}, "$.args.holding: expected boolean, got integer")
        self.refused("manual_base_pulse", {"seconds": 0.5, "holding": "no", "plan_only": False}, "$.args.holding: expected boolean, got string")
        self.refused("observe", {}, "missing required property 'holding'")
        self.refused("start_cycle", {}, "missing required property 'plan_only'")
        # A flag the controller would ignore is not accepted: the contract does not pretend it has an effect.
        self.refused("start_cycle", {"plan_only": False, "holding": True}, "unexpected property 'holding'")
        self.refused("observe", {"holding": False, "plan_only": True}, "unexpected property 'plan_only'")

    def test_stop_takes_no_argument_and_args_of_one_type_do_not_fit_another(self):
        self.accepted("stop", {})
        self.refused("stop", {"plan_only": True}, "unexpected property 'plan_only'")
        self.refused("manual_grip", {"deg": 3, "holding": False, "plan_only": False}, "missing required property 'action'")
        errors = demo_contracts.validate("command", {key: value for key, value in command("stop", {}).items() if key != "type"})
        self.assertTrue(any("discriminator 'type'" in error for error in errors), errors)

    def test_a_command_never_carries_an_endpoint_a_path_or_a_foreign_contract_version(self):
        for extra in ("remote_ip", "robot_id", "pi_ssh", "scan_id"):
            errors = demo_contracts.validate("command", {**command("stop", {}), extra: "x"})
            self.assertEqual(errors, [f"$: unexpected property {extra!r}"])
        errors = demo_contracts.validate("command", {**command("stop", {}), "contract_version": "2"})
        self.assertEqual(errors, ['$.contract_version: expected "1", got "2"'])

    def test_uncertain_is_a_status_of_its_own_and_always_says_why(self):
        result = {"command_id": "synthetic-command-0001", "status": "uncertain", "reason_code": "runtime_restarted_before_child_recorded", "run_id": None}
        self.assertEqual(demo_contracts.validate("command_result", result), [])
        self.assertEqual(demo_contracts.validate("command_result", {**result, "reason_code": None}), ["$.reason_code: expected string, got null (a reason_code, required when status is rejected or uncertain)"])
        self.assertEqual(demo_contracts.validate("command_result", {**result, "status": "accepted", "reason_code": None}), [])


class ControllerAgreement(unittest.TestCase):
    def test_the_schema_bounds_equal_the_controller_constants(self):
        source = REPO / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py"
        if not source.is_file():
            self.skipTest("the controller source is not in this checkout")
        constants = {}
        for node in ast.parse(source.read_text(encoding="utf-8")).body:  # parsed, never imported: importing the controller pulls robot dependencies in
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("STEP_MAX_PAN_DEG", "STEP_MAX_FORWARD_M", "STEP_MAX_DZ_M", "BASE_PULSE_S"):
                constants[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(set(constants), {"STEP_MAX_PAN_DEG", "STEP_MAX_FORWARD_M", "STEP_MAX_DZ_M", "BASE_PULSE_S"})
        pan = demo_contracts.load_schema("command#/$defs/args_manual_pan")["properties"]["deg"]
        move = demo_contracts.load_schema("command#/$defs/args_manual_move")["properties"]
        forward_range, reverse_range = demo_contracts.load_schema("command#/$defs/args_manual_base_pulse")["properties"]["seconds"]["oneOf"]
        self.assertEqual((pan["minimum"], pan["maximum"]), (-constants["STEP_MAX_PAN_DEG"], constants["STEP_MAX_PAN_DEG"]))
        self.assertEqual((move["forward_mm"]["minimum"], move["forward_mm"]["maximum"]), (-constants["STEP_MAX_FORWARD_M"] * 1000, constants["STEP_MAX_FORWARD_M"] * 1000))
        self.assertEqual((move["dz_mm"]["minimum"], move["dz_mm"]["maximum"]), (-constants["STEP_MAX_DZ_M"] * 1000, constants["STEP_MAX_DZ_M"] * 1000))
        self.assertEqual((forward_range["minimum"], forward_range["maximum"]), tuple(constants["BASE_PULSE_S"]))
        self.assertEqual((reverse_range["minimum"], reverse_range["maximum"]), (-constants["BASE_PULSE_S"][1], -constants["BASE_PULSE_S"][0]))


if __name__ == "__main__":
    unittest.main()
