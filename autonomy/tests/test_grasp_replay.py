# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/tests/test_grasp_replay.py: the GRASP stage's words against the recorded session of 2026-09-26 (track G of
docs/autonomous-demo/overnight-plan-2026-09-26.md, section 8 item 1).

Always run: the recorded numbers the replay harness pinned (autonomy/tests/test_recorded_pins_20260926.py) and the
far-edge rows of the recorded straddle, close and reopen frames, read once from the session's events and written here as
numbers. LOCAL ONLY (skipped unless KC_EVIDENCE_ROOT names the session's evidence): those rows read back from the evidence,
and both placed runs replayed through the controller with every grasp word on.
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))

import run_single_cube_cycle as cycle  # noqa: E402  (first: it puts the stage modules on the path)
import stage_harness as harness  # noqa: E402
import policy_grasp  # noqa: E402
import replay_20260926 as replay  # noqa: E402
from test_recorded_pins_20260926 import GRASP_MISS_INPUTS, PLACED_SCRIPTS  # noqa: E402

GRASP_WORDS = ("near_grasp", "grasp_measured_retry", "latch_midpoint", "grasp_recover")
# The far-edge row (bbox y0, px) of every recorded frame between the open jaws at grasp height: the straddle frame (the view
# of "APPROACH proven grasp geometry"), then each close and reopen, then the retry poses. Read from the events of each run.
RECORDED_ROWS = {
    replay.GRASP_MISS: {"straddle": [524], "close": [577, 583], "reopen": [560, 575, 587], "retry": [616, 593]},
    replay.START_1: {"straddle": [361], "close": [392]},
    replay.START_2: {"straddle": [384], "close": [395]},
}


class StopAtTheGraspPose(Exception):
    pass


def asked_from_the_recorded_align(words) -> float:
    """The grasp shift (mm) APPROACH asks for from run m8c5f9e9's last recorded ALIGN view, under `words`."""
    view = GRASP_MISS_INPUTS["align"][-1]
    start = harness.view(view["joints"])
    decision = cycle.alignment_from_bbox(view["bbox_xyxy"], start["tool"]["pitch_deg"])
    asked = []

    def stop(*args):
        forward_m = args[2] if len(args) > 2 and not isinstance(args[0], dict) else args[1]
        asked.append(forward_m * 1000.0)
        raise StopAtTheGraspPose()

    ctx = harness.HarnessCycle(pose=view["joints"])
    with mock.patch.object(policy_grasp, "near_grasp_pose", lambda ctl, measured, forward_m, *rest: stop(measured, forward_m)), \
            contextlib.redirect_stdout(io.StringIO()), unittest.TestCase().assertRaises(StopAtTheGraspPose):
        policy_grasp.run_grasp(ctx, harness.controller_view(proven_grasp_pose=stop, PLACEMENT_ENVELOPE=words), {"object_secured": False}, start, decision)
    return asked[-1]


class TheGraspMissUnderTheGraspWords(unittest.TestCase):
    """Run m8c5f9e9 (item 1): ALIGN measured the block at -23.0 mm; the grasp was clamped to 0 and every empty close went
    14 mm outward."""

    def test_its_recorded_align_view_plans_minus_9_under_near_grasp_and_0_without_it(self):
        self.assertEqual(0.0, asked_from_the_recorded_align(()))
        self.assertAlmostEqual(-9.0, asked_from_the_recorded_align(("near_grasp",)), delta=0.06)

    def test_the_near_grasp_pose_at_minus_9_is_reached_from_where_its_approach_stood(self):
        pose, report = policy_grasp.near_grasp_pose(harness.controller_view(), GRASP_MISS_INPUTS["approach_from_joints"], -0.009, 0.002, ("near_grasp",))
        self.assertAlmostEqual(-9.0, cycle.tool_state({**GRASP_MISS_INPUTS["approach_from_joints"], **pose.joints})["out_mm"], delta=1.0)
        self.assertGreaterEqual(report["lowest_link_m"], cycle.GRASP_TOOL_Z_M - cycle.LOWEST_TARGET_BELOW_GRASP_M)

    def test_each_measured_retry_goes_inward_of_every_close_that_came_before_it(self):
        estimate = -23.0
        empties = [cycle.tool_state(joints)["out_mm"] for joints in GRASP_MISS_INPUTS["reopened_joints"]]
        # the radii the jaws reopened at, measured; the closes themselves were stated at -3.1, +4.5 and +8.1 mm
        self.assertEqual([-4.2, 4.5, 8.5], [round(value, 1) for value in empties])
        for closes in range(1, len(empties) + 1):
            with self.subTest(closes=closes):
                aim = policy_grasp.grasp_target_mm(estimate, empties[:closes], (policy_grasp.min_grasp_forward_mm(("near_grasp",)), 60.0), cycle.APPROACH_REACH_LEAD_MM, prefer=-1)
                self.assertLess(aim["target_mm"], min(empties[:closes]))
                self.assertLessEqual(aim["clamped_by_mm"], policy_grasp.APPROACH_CLAMP_REFUSAL_MM)
                without = policy_grasp.grasp_target_mm(estimate, empties[:closes], (0.0, 60.0), cycle.APPROACH_REACH_LEAD_MM, prefer=-1)
                self.assertGreater(without["clamped_by_mm"], policy_grasp.APPROACH_CLAMP_REFUSAL_MM, "without near_grasp the retry is refused, not closed at 0")

    def test_the_recorded_rows_say_the_block_lay_nearer_than_the_jaws(self):
        for kind, rows in RECORDED_ROWS[replay.GRASP_MISS].items():
            for row in rows:
                with self.subTest(kind=kind, row=row):
                    self.assertEqual("nearer", policy_grasp.far_edge_side({"cube": {"detected": True, "bbox_xyxy": [400, row, 740, 720]}})[1])
        for run_id in replay.PLACED_RUNS:
            for kind, rows in RECORDED_ROWS[run_id].items():
                for row in rows:
                    with self.subTest(run=run_id, kind=kind, row=row):
                        self.assertEqual("in_band", policy_grasp.far_edge_side({"cube": {"detected": True, "bbox_xyxy": [400, row, 740, 720]}})[1])

    def test_latch_midpoint_classes_every_recorded_close_as_it_was(self):
        ctl = harness.controller_view()
        holds = [step["readback"] for run_id in replay.PLACED_RUNS for step in PLACED_SCRIPTS[run_id]["steps"] if step["kind"] == "gripper" and step["label"] == "latch"]
        self.assertEqual([False] * 3, [policy_grasp.close_held(ctl, value, ("latch_midpoint",)) for value in GRASP_MISS_INPUTS["closes"]])
        self.assertEqual([True, True], [policy_grasp.close_held(ctl, value, ("latch_midpoint",)) for value in holds])


class TheRecordedSessionUnderTheGraspWords(unittest.TestCase):
    """LOCAL ONLY: the rows above are the evidence's, and the placed runs decide the same under every grasp word."""

    @classmethod
    def setUpClass(cls):
        cls.directory = replay.evidence_dir()
        if cls.directory is None:
            raise unittest.SkipTest(f"{replay.EVIDENCE_ROOT_ENV} does not name the evidence of 2026-09-26 ({replay.EVIDENCE_DIR_NAME})")
        cls.runs = {run_id: replay.RecordedRun.load(cls.directory, run_id) for run_id in replay.CYCLE_RUNS}

    @classmethod
    def tearDownClass(cls):
        for run in getattr(cls, "runs", {}).values():
            run.close()

    @staticmethod
    def rows(run) -> dict:
        found = {"straddle": [], "close": [], "reopen": [], "retry": []}
        for step in run.script()["steps"]:
            kind = {"APPROACH proven grasp geometry": "straddle", "APPROACH advance after an empty close": "retry", "latch": "close", "open": "reopen"}.get(step.get("label"))
            if kind is None or (kind == "reopen" and step["kind"] != "gripper"):
                continue
            boxes = [record["cube"]["bbox_xyxy"] for record in step.get("records") or [] if record["cube"].get("bbox_xyxy")]
            found[kind] += [box[1] for box in boxes[-1:]]
        return {kind: rows for kind, rows in found.items() if rows}

    def test_the_rows_written_here_are_the_evidences(self):
        for run_id, run in self.runs.items():
            with self.subTest(run=run_id):
                rows = self.rows(run)
                if run_id in replay.PLACED_RUNS:
                    rows.pop("reopen", None)  # the release at the pad, not a grasp reopen
                self.assertEqual(RECORDED_ROWS[run_id], rows)

    def test_both_placed_runs_decide_the_same_under_every_grasp_word(self):
        grasp_states = {"APPROACH", "STRADDLING", "GRASP_CLOSE", "OBJECT_SECURED"}
        for run_id in replay.PLACED_RUNS:
            with self.subTest(run=run_id):
                run = self.runs[run_id]
                with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", GRASP_WORDS):
                    result = replay.replay(cycle, run.script(), run.frame_source())
                self.assertIsNone(result.error, f"the replay ended with {result.error!r}")
                self.assertEqual([], result.mismatches, "a commanded pose or pulse differs from the record")
                self.assertEqual([], result.left_over)
                recorded = [(name, fields) for name, fields in run.script()["states"] if name in grasp_states]
                replayed = [(name, fields) for name, fields in result.states if name in grasp_states]
                self.assertEqual([], replay.compare_states(recorded, replayed, compare_retention=True))


if __name__ == "__main__":
    unittest.main()
