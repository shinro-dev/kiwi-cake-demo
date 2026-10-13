# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/tests/test_align_replay.py: LOCAL ONLY. The ALIGN views of the one-block session of 2026-09-26, read from the
evidence itself, against the ALIGN stage policy (track A of docs/autonomous-demo/overnight-plan-2026-09-26.md).

SKIPPED unless KC_EVIDENCE_ROOT names the session's evidence directory (autonomy/tests/replay_20260926.py reads it; the
archives are read member by member in memory and nothing is copied or printed by path). What is proven:
  1. the ALIGN views test_policy_align.py carries are the recorded ones (box and measured joints of each ALIGN decision);
  2. the box predictor reproduces every recorded descent read from the archive within its tolerance;
  3. the frame sharpness of every archived ALIGN frame is computed from the frame itself;
  4. under align_v2 each recorded ALIGN view decides as the policy says: the 2D band, the radial step the gate admits at
     the descent height, the aimed descent over the block of start 1.
The views of failures 6 and 7 (runs 212418 and 213112) are on the robot only; they join this file when they are pulled.
"""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import replay_20260926 as replay  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import policy_align  # noqa: E402
import stage_harness as harness  # noqa: E402
import test_policy_align as always  # noqa: E402

CTL = cycle._CONTROLLER_VIEW


class AlignReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = replay.evidence_dir()
        if cls.directory is None:
            raise unittest.SkipTest(f"{replay.EVIDENCE_ROOT_ENV} does not name the evidence of 2026-09-26 ({replay.EVIDENCE_DIR_NAME}), so there is no recorded ALIGN view to read")
        cls.runs = {run_id: replay.RecordedRun.load(cls.directory, run_id) for run_id in replay.CYCLE_RUNS}
        cls.align = {run_id: [entry for entry in run.decision_inputs() if entry.stage == "ALIGN"] for run_id, run in cls.runs.items()}

    @classmethod
    def tearDownClass(cls):
        for run in getattr(cls, "runs", {}).values():
            run.close()

    def views(self, run_id: str) -> list:
        return [(entry.cube["bbox_xyxy"], entry.joints) for entry in self.align[run_id]]

    def test_the_align_views_of_the_always_run_tests_are_the_recorded_ones(self):
        self.assertEqual([always.MISS_PREGRASP, always.MISS_DESCENT], self.views(replay.GRASP_MISS))
        self.assertEqual([always.START_1_PREGRASP, always.START_1_DESCENT], self.views(always.START_1))
        self.assertEqual([always.START_2_PREGRASP, always.START_2_TRIMMED, always.START_2_DESCENT], self.views(always.START_2))

    def test_the_predictor_reproduces_every_recorded_descent_read_from_the_archive(self):
        pairs = 0
        for run_id in replay.CYCLE_RUNS:
            views = self.views(run_id)
            for (before, joints_before), (after, joints_after) in zip(views, views[1:]):
                tool_before, tool_after = cycle.tool_state(joints_before), cycle.tool_state(joints_after)
                if tool_before["up_mm"] - tool_after["up_mm"] < 15.0:
                    continue  # a pan trim at the pregrasp height, not a descent
                with self.subTest(run=run_id):
                    predicted = policy_align.predict_box_after(CTL, before, tool_before, tool_after)["bbox_xyxy"]
                    self.assertLessEqual(max(abs(p - r) for p, r in zip(predicted, after)), policy_align.PREDICTOR_TOLERANCE_PX)
                    pairs += 1
        self.assertEqual(3, pairs)

    def test_the_sharpness_of_every_archived_align_frame_is_computed_from_the_frame(self):
        measured = {}
        for run_id in replay.PLACED_RUNS:
            frames = self.runs[run_id].frame_source()
            for entry in self.align[run_id]:
                with self.subTest(run=run_id, attempt=entry.attempt):
                    value = policy_align.frame_sharpness(frames(entry.observation_id), entry.cube["bbox_xyxy"])
                    self.assertTrue(value is not None and math.isfinite(value) and value > 0.0)
                    measured[(run_id, entry.attempt)] = value
        # the descent frame of each placed run is the least sharp of its ALIGN frames (4.1 against 5.8; 4.3 against 6.8
        # and 5.8): what the record shows, with no threshold drawn from it
        for run_id in replay.PLACED_RUNS:
            values = [measured[(run_id, entry.attempt)] for entry in self.align[run_id]]
            self.assertEqual(min(values), values[-1])

    def test_under_align_v2_each_recorded_view_decides_by_the_2d_band(self):
        band = {}
        for run_id in replay.CYCLE_RUNS:
            for entry in self.align[run_id]:
                pitch = cycle.tool_state(entry.joints)["pitch_deg"]
                decision = policy_align.alignment_decision(CTL, entry.cube["bbox_xyxy"], pitch, ("align_v2",))
                band[(run_id[-8:], entry.attempt)] = (decision["lateral_in_window"], decision["radial_in_band"], decision["aligned"])
        self.assertEqual({("m8c5f9e9", 0): (True, False, False), ("m8c5f9e9", 1): (True, False, False),
                          ("mb3c1048", 0): (True, True, True), ("mb3c1048", 1): (True, False, False),
                          ("m2476137", 0): (False, True, False), ("m2476137", 1): (True, True, True), ("m2476137", 2): (True, True, True)}, band)

    def test_the_gate_admits_the_radial_step_align_v2_takes_at_each_recorded_descent_height(self):
        for run_id, attempt in ((replay.GRASP_MISS, 1), (always.START_1, 1)):
            entry = self.align[run_id][attempt]
            view = harness.view(entry.joints)
            radial = policy_align.alignment_decision(CTL, entry.cube["bbox_xyxy"], view["tool"]["pitch_deg"], ("align_v2",))["radial_mm"]
            with self.subTest(run=run_id, radial=round(radial, 1)):
                pose, refusal = policy_align._radial_step_pose(CTL, view, radial)
                self.assertIsNone(refusal)
                self.assertAlmostEqual(view["tool"]["out_mm"] + policy_align._clamp(radial, policy_align.ALIGN_RADIAL_STEP_MAX_MM), cycle.tool_state(pose.joints)["out_mm"], delta=1.0)

    def test_the_aimed_descent_over_start_1s_pregrasp_view_keeps_the_block_in_view(self):
        entry = self.align[always.START_1][0]
        view = harness.view(entry.joints)
        radial = cycle.alignment_from_bbox(entry.cube["bbox_xyxy"], view["tool"]["pitch_deg"])["radial_mm"]
        plan = policy_align.plan_aimed_descent(CTL, view, entry.cube["bbox_xyxy"], radial)
        self.assertTrue(plan["ok"])
        self.assertGreaterEqual(plan["predicted"]["margins"]["top"], policy_align.ALIGN_VIEW_MARGIN_PX)


if __name__ == "__main__":
    unittest.main()
