#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_frozen_primitives.py: the byte-for-byte rule of docs/autonomous-demo/one-block-demo-2026-09-26.md
# (T3 and T6) as a gate. The motion primitives the attended runs of 2026-09-26 physically passed with are pinned by the
# sha256 of their source, read from the controller module with inspect.getsource; a constant is pinned by its repr.
# A pin that moves is a change to a proven primitive, and the operator decides it, never a test update on its own.
#
# WHAT IS FROZEN BY BYTES and what only by binding: base_advance_needed_mm is the placement RULE and is not pinned here
# (T6 step 1b changes its body behind the envelope word). _advance_base_held and _approach_state call it by name, so their
# bytes stay and their outputs follow the word. run_pose's wrist ceiling is an expression inside run_pose (measured plus
# WRIST_FLEX_CEILING_MARGIN_DEG, 0.5 deg since the operator ruling of 2026-10-08), pinned with the method; the gripper band it and base_pulse check through gripper_refusal is pinned by value.
# Run directly: python3 autonomy/tests/test_frozen_primitives.py
import hashlib
import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))

import run_single_cube_cycle as cycle  # noqa: E402


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


FROZEN_METHODS = {  # Cycle methods: the source of each, as inspect.getsource returns it at the pinned commit
    # re-pinned 2026-10-08 by OPERATOR RULING (not a test update): the wrist ceiling margin inside run_pose is the shared
    # run_stage1_scan.WRIST_FLEX_CEILING_MARGIN_DEG (0.5 deg), after 0.2 deg refused the day's pregrasp entry by 0.15 deg.
    "run_pose": "6b22291413745a35238f13bbc5e4f02b579a0f47b945267a5ee5771f0b4522de",
    "base_pulse": "d621bb5de9fa411cc44776997d9c90f300645044d48e481936f6e84fe8abaa42",
    "measured_base_pulse": "fb563340a2455c297e87adce034185599cab5dc0e4e1af01e874121b031a8f96",
    "advance_base": "564a9111a426fdd904894b6b882f1d59c06a3e60a122e9c46b8db96db8d23078",
    "_advance_base_held": "791a46abcfed8c35feed8642272afc77695eeea4d27ff9d972f039bfb70c758f",
    "return_base": "cd35f6b4251d82edce7939b62adbf625f7eace72559b8049ee547e8bb4f75820",
}
FROZEN_FUNCTIONS = {  # module functions
    "base_displacement": "b38e8bfb8cf6fcd70f1e43df86f417895b54b880d58fea724128bd51ba00b3f1",
    "base_posture_refusal": "7de103c2b12c6b6b645ce7c01abafdfc8b6dfe8786b2e661327989bc55e67fc0",
    "gripper_refusal": "1bc1f682f733b48a3a17aadba217d6783b8b144fbb84229bc9f6aa82e93cdf38",
}
# coarse_pregrasp_from_low_pose's clamp line: forward_mm = min(max(out + PREGRASP_OUTWARD_BIAS_MM, 0.0), 60.0). T6 step 2a
# reads its refusal threshold off this line (a cube nearer than -13 mm has its pregrasp clamped), so the line is pinned alone.
PREGRASP_CLAMP_LINE = "62d4445891fbfcc034160ad94d672c0361658f5f2d504ac034703887532a732c"
FROZEN_CONSTANTS = (
    "BASE_ADVANCE_TOLERANCE_MM", "BASE_MAX_ARM_DRIFT_DEG", "BASE_MAX_AXIS_SLOPE", "BASE_MAX_DISTORTION_MM", "BASE_MAX_LATERAL_MM", "BASE_MAX_PULSES",
    "BASE_MAX_TOTAL_ADVANCE_MM", "BASE_PLAUSIBLE_SLACK_MM", "BASE_POSTURE_TOLERANCE_DEG", "BASE_PULSE_S", "BASE_RATE_BOUNDS_MM_S", "BASE_RATE_PRIOR_MM_S",
    "BASE_RETURN_TOLERANCE_MM", "BASE_TARGET_BUFFER_MM", "BASE_VX_MPS", "PLACED_MIN_EDGE_MARGIN_MM", "PLACE_REQUIRED_CENTRE_MM", "VISUAL_ANCHOR_UNCERTAINTY_MM",
    "GRIPPER_OPEN", "GRIPPER_LATCH", "GRIPPER_OPEN_TOLERANCE", "GRIPPER_HOLDING_BAND", "GRIPPER_EMPTY_LATCH_MARGIN")
# GRIPPER_EMPTY_LATCH_MARGIN 0.6 -> 0.87, changed by operator ruling 2026-09-27 on open ruling 2 of
# docs/autonomous-demo/overnight-plan-2026-09-26.md, option A (docs/autonomous-demo/decision-log.md): the empty-latch
# class at the midpoint of the recorded classes. The operator decided this pin; it has NOT been physically passed, and
# its hardware check is the first attended qualification runs.
# GRIPPER_EMPTY_LATCH_MARGIN 0.87 -> 0.73, changed by operator ruling 2026-10-09: a hold read 67.633 (step 980, the block
# squeezed between both jaws on the wrist camera) and 0.87 classed it empty; 0.73 puts the threshold half a step above 978,
# the midpoint of the worst empty (976) and that hold. Only this constant moved (0.87 reproduces the previous digest
# 50dc4f66...). It has NOT been physically passed; its hardware check is the next attended mission 1.
FROZEN_CONSTANTS_DIGEST = "7028a8bab67ffe3cad51fd7747d09b9dcbce3aab01f41f5ddeccc17a670a74e5"

# THE BASE-AT-REST RULE, changed by operator ruling 2026-09-27 (docs/autonomous-demo/decision-log.md) after run
# console-20260927T142146Z-m6ed2dd9 stopped holding the block on one velocity count on two wheels at once. base_pulse and
# grip confirm the base at rest BY NAME (Cycle.base_is_zero and scan_world._base_is_zero), and run_pose through
# run_scan_world, so none of the pins above moved when the rule did: those primitives' bytes are the attended runs', and
# their base-zero verdicts now follow base_rest.at_rest. The rule is pinned here on its own. It has NOT been physically
# passed: these digests record the ruling and the recorded-read replay (test_base_rest*.py), not a robot run.
BASE_REST_SOURCES = {
    "base_rest.wheel_counts": "0e07bdca01cebd4fc2429b05505caca8ca44c98fb7b3267d3a5055df420ee0c4",
    "base_rest.at_rest": "7996ead30c20d59a4d51244b4616cbde7785c77e3df65810764e0475932aecbf",
    "base_rest.reads_at_rest": "4038cd3268791c8e075958edc6bd76983363914c105bf9dd29c1861b9d7a96cf",
    "scan_world._base_is_zero": "c63014b6309cfbd578c72d880bfb1be9fe4b1d09208c2470ea2ff7231e882b05",
    "scan_world._reads_zero": "be50159e27c99cd863b64cb45a8c8b94510e879cc45b9bb34a11327e73c43377",
}
BASE_REST_CONSTANTS = {"base_rest": ("WHEEL_ANGLES_DEG", "WHEEL_RADIUS_M", "BASE_RADIUS_M", "STEPS_PER_REV", "VELOCITY_COUNT_STEPS_PER_S", "REST_MAX_COUNTS"),
                       "scan_world": ("BASE_ZERO_CONSECUTIVE_READS", "BASE_ZERO_MAX_READS", "BASE_ZERO_POLL_S")}
BASE_REST_CONSTANTS_DIGEST = "95cb6a830e55cc19439d8b85048712f952ccb309de438c8e85ee76e3b3492335"


class TheProvenPrimitivesAreFrozen(unittest.TestCase):
    def test_every_cycle_method_that_moves_the_arm_or_the_base_has_the_pinned_source(self):
        for name, digest in FROZEN_METHODS.items():
            with self.subTest(name):
                self.assertEqual(digest, _sha256(inspect.getsource(getattr(cycle.Cycle, name))), f"Cycle.{name} changed: a proven motion primitive is not edited by a test update")

    def test_the_measurement_and_refusal_functions_have_the_pinned_source(self):
        for name, digest in FROZEN_FUNCTIONS.items():
            with self.subTest(name):
                self.assertEqual(digest, _sha256(inspect.getsource(getattr(cycle, name))))

    def test_the_pregrasp_clamp_line_is_the_one_step_2a_reads_its_threshold_from(self):
        lines = [line.strip() for line in inspect.getsource(cycle.coarse_pregrasp_from_low_pose).splitlines() if "forward_mm" in line]
        self.assertEqual(1, len(lines))
        self.assertEqual(PREGRASP_CLAMP_LINE, _sha256(lines[0]))
        self.assertIn("min(max(out + PREGRASP_OUTWARD_BIAS_MM, 0.0), 60.0)", lines[0])

    def test_every_base_constant_the_placement_constants_and_the_gripper_band_have_their_pinned_values(self):
        base = tuple(sorted(name for name in dir(cycle) if name.startswith("BASE_")))
        self.assertEqual(base, tuple(name for name in FROZEN_CONSTANTS if name.startswith("BASE_")), "a BASE_* constant was added or removed: name it here with the operator")
        values = {name: getattr(cycle, name) for name in FROZEN_CONSTANTS}
        self.assertEqual(FROZEN_CONSTANTS_DIGEST, _sha256(repr(values)), f"a frozen constant moved: {values}")

    def test_the_rule_function_is_not_pinned_by_bytes_but_its_callers_are(self):
        """base_advance_needed_mm is the rule T6 step 1b changes behind the envelope word; the loop and the approach state
        call it by name, which is what keeps their bytes while their outputs follow the word."""
        self.assertIn("base_advance_needed_mm(anchor, axis)", inspect.getsource(cycle.Cycle._advance_base_held))
        self.assertIn("base_advance_needed_mm(anchor, axis)", inspect.getsource(cycle.Cycle._approach_state))
        self.assertNotIn("base_advance_needed_mm", FROZEN_FUNCTIONS)


class TheBaseAtRestRuleIsPinnedByTheRuling(unittest.TestCase):
    def test_the_rule_and_the_guard_have_the_pinned_source(self):
        modules = {"base_rest": sys.modules["base_rest"], "scan_world": sys.modules["scan_world"]}
        for name, digest in BASE_REST_SOURCES.items():
            module, function = name.split(".")
            with self.subTest(name):
                self.assertEqual(digest, _sha256(inspect.getsource(getattr(modules[module], function))), f"{name} changed: the base-at-rest rule is the operator's")

    def test_the_rule_constants_have_their_pinned_values(self):
        values = {name: getattr(sys.modules[module], name) for module, names in BASE_REST_CONSTANTS.items() for name in names}
        self.assertEqual(BASE_REST_CONSTANTS_DIGEST, _sha256(repr(values)), f"a base-at-rest constant moved: {values}")
        self.assertEqual(1, values["REST_MAX_COUNTS"])

    def test_the_frozen_primitives_reach_the_rule_by_name(self):
        self.assertIn("self.base_is_zero()", inspect.getsource(cycle.Cycle.base_pulse))
        self.assertIn("_base_is_zero(self._deps(False, 1e9))", inspect.getsource(cycle.Cycle.base_is_zero))
        self.assertIn("_base_is_zero(self._deps(False, 1e9))", inspect.getsource(cycle.Cycle.grip))
        self.assertIs(cycle._base_is_zero, sys.modules["scan_world"]._base_is_zero)
        self.assertIn("reads_at_rest(", inspect.getsource(sys.modules["scan_world"]._reads_zero))


if __name__ == "__main__":
    unittest.main()
