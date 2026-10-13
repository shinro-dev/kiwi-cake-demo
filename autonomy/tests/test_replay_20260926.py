#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_replay_20260926.py: LOCAL ONLY. The recorded-decision replay of the one-block session of 2026-09-26
# (track P0c of docs/autonomous-demo/overnight-plan-2026-09-26.md) against the evidence itself.
#
# SKIPPED unless KC_EVIDENCE_ROOT names the session's evidence directory (one-block-20260926) or a directory that holds it.
# That data is private machine data: it is READ here, the two robot-runtime-data archives member by member in memory, and
# nothing of it is copied, extracted or printed by path. What is proven:
#   1. the numbers test_recorded_pins_20260926.py carries ARE the recorded inputs (so its always-run pins pin the record);
#   2. both placed runs replay through Cycle.run_cycle from the archived records with every [STATE] line, every commanded
#      pose and every base pulse equal to the recorded ones, the retention score computed from the archived frames;
#   3. the decision inputs the harness yields reproduce each recorded ALIGN, close and pulse from the view it names.
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import replay_20260926 as replay  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import test_recorded_pins_20260926 as pins  # noqa: E402


class RecordedReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = replay.evidence_dir()
        if cls.directory is None:
            raise unittest.SkipTest(f"{replay.EVIDENCE_ROOT_ENV} does not name the evidence of 2026-09-26 ({replay.EVIDENCE_DIR_NAME}), so there is no recorded run to replay")
        cls.runs = {run_id: replay.RecordedRun.load(cls.directory, run_id) for run_id in replay.CYCLE_RUNS}

    @classmethod
    def tearDownClass(cls):
        for run in getattr(cls, "runs", {}).values():
            run.close()

    # ------------------------------------------------------------------ 1. the pins are the record
    def test_the_pinned_scripts_of_both_placed_runs_are_the_recorded_runs(self):
        for run_id in replay.PLACED_RUNS:
            with self.subTest(run=run_id):
                self.assertIsNotNone(self.runs[run_id].archive, "the placed runs are replayed from their archive")
                self.assertEqual(pins.PLACED_SCRIPTS[run_id], self.runs[run_id].script())

    def test_the_pinned_grasp_miss_inputs_are_the_recorded_ones(self):
        self.assertEqual(pins.GRASP_MISS_INPUTS, replay.grasp_miss_inputs(self.runs[replay.GRASP_MISS]))

    def test_the_archived_cycle_record_states_what_stdout_states(self):
        for run_id in replay.PLACED_RUNS:
            with self.subTest(run=run_id):
                record = self.runs[run_id].archive.cycle_record(run_id)
                self.assertEqual([[name, fields] for name, fields in self.runs[run_id].states], [[entry["state"], {k: v for k, v in entry.items() if k not in ("state", "utc")}] for entry in record])

    # ------------------------------------------------------------------ 2. the replay from the archive
    def test_both_placed_runs_replay_every_stage_decision_with_the_retention_from_the_archived_frames(self):
        for run_id in replay.PLACED_RUNS:
            with self.subTest(run=run_id):
                run = self.runs[run_id]
                result = replay.replay(cycle, run.script(), run.frame_source())
                self.assertIsNone(result.error, f"the replay ended with {result.error!r}")
                self.assertEqual([], result.mismatches)
                self.assertEqual([], result.left_over)
                self.assertEqual([], replay.compare_states(run.script()["states"], result.states, compare_retention=True))
                finished = next(event["payload"] for event in run.events if event["kind"] == "run.finished")
                self.assertEqual(finished["predicates"], {k: result.summary[k] for k in finished["predicates"]})

    # ------------------------------------------------------------------ 3. the decision inputs
    def test_every_state_of_every_cycle_run_has_its_decision_inputs_in_order(self):
        for run_id, run in self.runs.items():
            with self.subTest(run=run_id):
                inputs = run.decision_inputs()
                self.assertEqual([name for name, _ in run.states], [item.stage for item in inputs])
                self.assertEqual(list(range(len(inputs))), [item.index for item in inputs])
                self.assertTrue(all(item.joints for item in inputs), "every decision names the arm's measured joints")

    def test_each_align_is_reproduced_from_the_box_and_the_pitch_of_the_view_it_names(self):
        checked = 0
        for run_id, run in self.runs.items():
            for item in (item for item in run.decision_inputs() if item.stage == "ALIGN"):
                with self.subTest(run=run_id, attempt=item.attempt):
                    decision = cycle.alignment_from_bbox(item.cube["bbox_xyxy"], item.tool["pitch_deg"])
                    self.assertEqual(item.recorded["tool"], cycle.fmt_tool(item.tool))
                    self.assertEqual((item.recorded["lateral_mm"], item.recorded["radial_mm"], item.recorded["aligned"]),
                                     (round(decision["lateral_mm"], 1), round(decision["radial_mm"], 1), decision["aligned"]))
                    checked += 1
        self.assertEqual(7, checked)  # 2 + 2 + 3 recorded ALIGN views

    def test_each_close_is_classed_from_its_recorded_readback(self):
        checked = 0
        for run_id, run in self.runs.items():
            for item in (item for item in run.decision_inputs() if item.stage == "GRASP_CLOSE"):
                with self.subTest(run=run_id, attempt=item.attempt):
                    self.assertEqual(item.recorded["gripper"], round(item.step["readback"], 3))
                    self.assertEqual(item.recorded["closed_on_something"], cycle.closed_on_something(item.step["readback"]))
                    checked += 1
        self.assertEqual(5, checked)

    def test_each_measured_pulse_of_the_placed_runs_names_a_view_with_a_pad_frame_and_a_frame_on_file(self):
        for run_id in replay.PLACED_RUNS:
            run = self.runs[run_id]
            frames = run.frame_source()
            for item in (item for item in run.decision_inputs() if item.stage == "BASE_MEASURED"):
                with self.subTest(run=run_id, index=item.index):
                    self.assertIsNotNone(item.pad_frame)
                    self.assertIn("homography_image_to_pad", item.pad_frame)
                    self.assertEqual((720, 1280, 3), frames(item.observation_id).shape)

    def test_the_grasp_miss_has_no_archive_so_its_inputs_carry_no_homography(self):
        run = self.runs[replay.GRASP_MISS]
        self.assertIsNone(run.archive)
        self.assertIsNone(run.frame_source())
        search = next(item for item in run.decision_inputs() if item.stage == "PREGRASP")
        self.assertEqual([495.0, 696.0], search.cube["contact_px"])
        self.assertEqual({"pad_frame_valid": True}, search.pad_frame)  # valid by the events; its homographies are in the robot's record only


class EvidenceDirTest(unittest.TestCase):
    """The root guard, exercised on directories this test makes, so that a run without evidence is not a silent one."""

    def test_only_a_directory_holding_the_events_of_start_1_is_evidence(self):
        import tempfile

        self.assertIsNone(replay.evidence_dir({}))
        with tempfile.TemporaryDirectory(prefix="kc-no-evidence-") as empty:
            self.assertIsNone(replay.evidence_dir({replay.EVIDENCE_ROOT_ENV: empty}))
            nested = Path(empty) / replay.EVIDENCE_DIR_NAME
            nested.mkdir()
            (nested / f"{replay.START_1}.events.json").write_text("{}")
            self.assertEqual(nested, replay.evidence_dir({replay.EVIDENCE_ROOT_ENV: empty}))
            self.assertEqual(nested, replay.evidence_dir({replay.EVIDENCE_ROOT_ENV: str(nested)}))
            self.assertIsNone(replay.evidence_dir({replay.EVIDENCE_ROOT_ENV: str(Path(empty) / "there-is-no-such-directory")}))


if __name__ == "__main__":
    unittest.main()
