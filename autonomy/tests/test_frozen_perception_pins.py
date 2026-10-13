#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_frozen_perception_pins.py: the perception the robot benchmark measured is the perception this tree runs.
#
# WHY THIS EXISTS. The robot benchmark (cake-console-handoff-2026-09-23.md section 12.3) proved that the per-frame perception
# gives the same decisions on the robot and on the laptop, AT EXACTLY THE BYTES OF ead60a6: seven source files and the
# perceive() function of the scan CLI. The desktop perception service (bin/laptop/perception/perception_service.py) and
# the robot's client (autonomy/perception/remote_perception.py) rest on that proof: the robot accepts a desktop answer only
# when the code that produced it is that code. A change to any of these bytes voids the equivalence and needs a new proof,
# so it must fail HERE, loudly, before any other suite that uses them.
#
# THE HASH METHOD IS THE HARNESS'S, NOT AN AST EXTRACTION. The benchmark hashed perceive() as the text from its `def` line up
# to "\ndef make_detector" (the harness bench.py, PERCEIVE_HEAD and the slice it hashes). A plain ast.get_source_segment of
# the function gives a different digest (it stops before the blank lines), so it would pin something the benchmark never
# measured. Both the expected values below and remote_perception.code_digests use the harness method.
#
# The expected values are written out here AND in remote_perception.FROZEN_*: two independent statements of the same pin,
# so a change that edits the module's constants to match edited sources still fails this test.
# Run directly: python3 autonomy/tests/test_frozen_perception_pins.py
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))

import remote_perception as rp  # noqa: E402  (standard library at import time)

# The benchmark's expect.json at ead60a6 (section 12.3), copied by hand.
EXPECTED_SOURCES_SHA256 = {
    "autonomy/perception/zero_shot_detector.py": "f7f4a930487c73abd8b16620c2a0d6994d8148bdfdb2b57a6ab01db4f651b839",
    "tools/static_world_observation.py": "827dc664a4d20f89e0777a759edbd11a541b9c8e20766148e4a6fd4e3174ab94",
    "autonomy/perception/cube_recognizer.py": "38542d4027d56710c94e7ad412601269cc4163a1c281e5be939b76c24a5a3cbc",
    "autonomy/perception/pad_localizer.py": "e91f4bd4a8dcbe459fb0036e459f3a27c576f30e1974792fe5a5ea5df507e0ec",
    "autonomy/perception/pad_template_localizer.py": "1bd1c556a6f8ca957a9c3987c24c12cf130e70d86d25fd9cb2903be2282bf300",
    "autonomy/perception/frame_illumination.py": "8b6c051892f13b5b56b1843e1fd5ec966cf586584a284d966fe2338a1143f6f8",
    "autonomy/perception/observation_store.py": "0bb94068f9799625ff349b86ecb5a6e5f4f7998effeba198ed0a2f246620dffc",
}
EXPECTED_PERCEIVE_SHA256 = "19f769e525c3f155cacaf08c48f4bd090cfc6e3cd106bc1141922bf3bee9f179"
PERCEIVE_HEAD = "def perceive(role: str, image, out_dir: Path, name: str, detector) -> list:"  # bench.py PERCEIVE_HEAD, verbatim


def harness_perceive_sha256(text: str) -> str:
    """bench.py's own slice, restated here rather than imported: the module under test must not be its own witness."""
    start = text.index(PERCEIVE_HEAD)
    return hashlib.sha256(text[start:text.index("\ndef make_detector", start)].encode()).hexdigest()


class TheFrozenSourcesAreTheMeasuredOnes(unittest.TestCase):
    def test_every_frozen_source_has_the_bytes_the_benchmark_measured(self):
        for relative, expected in EXPECTED_SOURCES_SHA256.items():
            with self.subTest(source=relative):
                self.assertEqual(expected, hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                                 f"{relative} changed: the robot benchmark's equivalence proof no longer covers it")

    def test_perceive_has_the_text_the_benchmark_measured_by_the_harness_method(self):
        text = (ROOT / "bin" / "laptop" / "perception" / "run_stage1_scan.py").read_text()
        self.assertEqual(EXPECTED_PERCEIVE_SHA256, harness_perceive_sha256(text))

    def test_an_ast_extraction_would_have_pinned_something_else(self):
        """Not vacuous: the method matters, which is why every pin here uses the harness's."""
        import ast

        text = (ROOT / "bin" / "laptop" / "perception" / "run_stage1_scan.py").read_text()
        node = next(item for item in ast.parse(text).body if isinstance(item, ast.FunctionDef) and item.name == "perceive")
        self.assertNotEqual(EXPECTED_PERCEIVE_SHA256, hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest())


class TheRemoteModuleStatesTheSamePins(unittest.TestCase):
    def test_the_module_pins_equal_the_benchmarks(self):
        self.assertEqual(EXPECTED_SOURCES_SHA256, dict(rp.FROZEN_SOURCES_SHA256))
        self.assertEqual(EXPECTED_PERCEIVE_SHA256, rp.FROZEN_PERCEIVE_SHA256)
        self.assertEqual(PERCEIVE_HEAD, rp.PERCEIVE_HEAD)

    def test_code_digests_of_this_tree_are_the_frozen_ones(self):
        digests = rp.code_digests(ROOT)
        self.assertEqual(EXPECTED_SOURCES_SHA256, digests["sources_sha256"])
        self.assertEqual(EXPECTED_PERCEIVE_SHA256, digests["perceive_sha256"])
        self.assertEqual([], rp.frozen_code_problems(digests))

    def test_a_changed_source_is_named_by_the_check(self):
        digests = rp.code_digests(ROOT)
        digests["sources_sha256"] = {**digests["sources_sha256"], "tools/static_world_observation.py": "0" * 64}
        digests["perceive_sha256"] = "1" * 64
        self.assertEqual(["run_stage1_scan.perceive", "tools/static_world_observation.py"], rp.frozen_code_problems(digests))


if __name__ == "__main__":
    unittest.main()
