#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_rehearsal_observation.py: autonomy/motion/rehearsal_observation.py, the synthetic target observation,
# and its STRUCTURAL exclusion from LIVE execution ("The synthetic observation remains valid only for rehearsal. It must
# be structurally excluded from LIVE execution.").
#   - the source is built only in the runtime's synthetic mode, whose word is demo_runtime.supervisor's;
#   - what it gives: the proven primitive's x 1.0 m, y 0, in the v2 shape (frame "anchor_reach", no quality, stationary,
#     its own bound), freshly stamped on the caller's clock, at the caller's epoch, under an observation id no stored
#     capture has; on the wire its x lies outside the v2 envelope, so a v2 capability publishes no command for it;
#   - no LIVE module imports it (the controller, the relay, the LIVE observation source, the mission execution and the
#     mission service), none of them passes a NUMERIC x_m, and none of them names the synthetic constants.
# Nothing is run on a robot and nothing is imported from the LIVE modules: their sources are PARSED.
# Run directly: python3 autonomy/tests/test_rehearsal_observation.py
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "motion"))

import rehearsal_observation as ro  # noqa: E402
import target_observation_source as tos  # noqa: E402
from clock import FakeClock  # noqa: E402

#: Every module a LIVE run executes on the way to a base pulse or a mission decision.
LIVE_MODULES = (
    "bin/laptop/manipulation/run_single_cube_cycle.py",
    "autonomy/motion/capability_pulse.py",
    "autonomy/motion/target_observation_source.py",
    "packages/demo-runtime/demo_runtime/mission_execution.py",
    "packages/demo-runtime/demo_runtime/mission_service.py",
)


def _tree(relative: str) -> ast.AST:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _number(node) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


class OnlyInASyntheticRuntime(unittest.TestCase):
    def test_every_other_mode_is_refused_and_gets_nothing(self):
        for mode in ("live", "replay", "", None, "SYNTHETIC", "synthetic "):
            with self.subTest(mode=mode), self.assertRaises(ro.SyntheticObservationRefused) as raised:
                ro.synthetic_source(mode, target_id="cube_far", clock=FakeClock(1), motion_epoch=lambda: 0)
            self.assertEqual(ro.REASON_NOT_SYNTHETIC, str(raised.exception))

    def test_the_mode_word_is_the_runtimes(self):
        tree = _tree("packages/demo-runtime/demo_runtime/supervisor.py")
        pairs = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple) and isinstance(node.value, ast.Tuple):
                for name, value in zip(node.targets[0].elts, node.value.elts):
                    if isinstance(name, ast.Name) and isinstance(value, ast.Constant):
                        pairs[name.id] = value.value
        self.assertEqual(pairs.get("SYNTHETIC"), ro.REHEARSAL_MODE)

    def test_what_it_gives(self):
        clock, epoch = FakeClock(5_000), {"value": 2}
        observe = ro.synthetic_source("synthetic", target_id="cube_far", clock=clock, motion_epoch=lambda: epoch["value"])
        first = observe()
        clock.advance(80_000_000)
        epoch["value"] = 3
        second = observe()
        self.assertIsInstance(first, tos.ObservedTarget)
        self.assertEqual((first.x_m, first.y_m, first.frame, first.max_age_ns, first.stationary),
                         (1.0, 0.0, "anchor_reach", ro.SYNTHETIC_MAX_AGE_NS, True))
        self.assertFalse(hasattr(first, "quality"))
        self.assertEqual((first.capture_monotonic_ns, first.motion_epoch), (5_000, 2))
        self.assertEqual((second.capture_monotonic_ns, second.motion_epoch), (80_005_000, 3))
        self.assertEqual({first.observation_id, second.observation_id}, {ro.SYNTHETIC_OBSERVATION_ID})
        self.assertIsNone(first.frame_sha256)
        self.assertEqual(first.evidence()["anchor_pad_m"], None)
        self.assertIsNone(tos.check_for_pulse(second, target_id="cube_far", motion_epoch=3, now_ns=clock.now_ns()))

    def test_on_the_wire_its_x_is_outside_the_v2_envelope(self):
        """The v2 document is well formed (the schema's eleven fields) and its x_m exceeds the executor's bounded total
        advance, so a v2 capability drops it (x_out_of_bounds) and publishes no command: the synthetic input cannot be
        mistaken for a measured remaining advance."""
        clock = FakeClock(5_000)
        observe = ro.synthetic_source("synthetic", target_id="cube_far", clock=clock, motion_epoch=lambda: 0)
        wire = observe().wire_v2(published_ns=clock.now_ns())
        self.assertEqual(tuple(wire), tos.WIRE_FIELDS)
        self.assertEqual((wire["contract"], wire["frame"], wire["stationary"]), (tos.CONTRACT, "anchor_reach", True))
        self.assertGreater(wire["x_m"], tos.MAX_ADVANCE_MM / 1000.0)

    def test_it_needs_the_callers_clock_and_epoch(self):
        with self.assertRaises(ValueError):
            ro.synthetic_source("synthetic", target_id="cube_far", clock=FakeClock(1), motion_epoch=None)
        with self.assertRaises(ValueError):
            ro.synthetic_source("synthetic", target_id="cube_far", clock=object(), motion_epoch=lambda: 0)


class StructurallyOutOfLive(unittest.TestCase):
    def test_no_live_module_imports_it(self):
        for relative in LIVE_MODULES:
            with self.subTest(relative):
                imported = set()
                for node in ast.walk(_tree(relative)):
                    if isinstance(node, ast.Import):
                        imported.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                        imported.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) in ("import_module", "_motion_module", "__import__"):
                        imported.update(arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
                self.assertFalse({name for name in imported if "rehearsal_observation" in name}, imported)

    def test_no_live_module_passes_a_numeric_x_m_or_names_the_synthetic_constants(self):
        for relative in LIVE_MODULES:
            with self.subTest(relative):
                tree = _tree(relative)
                keywords = [node for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg in ("x_m", "y_m") and _number(node.value)]
                entries = [value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                           for key, value in zip(node.keys, node.values) if isinstance(key, ast.Constant) and key.value in ("x_m", "y_m") and _number(value)]
                named = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id.startswith("SYNTHETIC_")]
                self.assertEqual((keywords, entries, named), ([], [], []))

    def test_this_file_really_would_see_one(self):
        """Not vacuous: the rehearsal module itself trips both checks."""
        tree = ast.parse((ROOT / "autonomy" / "motion" / "rehearsal_observation.py").read_text(encoding="utf-8"))
        self.assertTrue([node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id.startswith("SYNTHETIC_")])
        sample = ast.parse("TargetObservation(target_id='t', timestamp_ns=0, x_m=1.0, y_m=0.0, quality=0.9)")
        self.assertTrue([node for node in ast.walk(sample) if isinstance(node, ast.keyword) and node.arg == "x_m" and _number(node.value)])


if __name__ == "__main__":
    unittest.main()
