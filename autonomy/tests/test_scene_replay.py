# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The scene fusion replayed on the archived records of 2026-09-26 (skipped without KC_EVIDENCE_ROOT, like
test_replay_20260926.py). It proves that the numbers test_scene_scan pins ARE the archived ones, and fuses the archived
low-pose-shape views of run m2476137 (the SEARCH frame and the PREGRASP pan waypoint) into one scene through the
controller's own classify_block. The archive is read in memory by the replay harness; nothing is extracted."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))

import replay_20260926 as replay  # noqa: E402
import stage_harness as harness  # noqa: E402
import policy_search  # noqa: E402
import scene_scan  # noqa: E402
import test_scene_scan as pinned  # noqa: E402

EVIDENCE = replay.evidence_dir()


@unittest.skipIf(EVIDENCE is None, "set KC_EVIDENCE_ROOT to the evidence of 2026-09-26 (see autonomy/tests/replay_20260926.py)")
class TheArchivedPairOf2037(unittest.TestCase):
    def setUp(self):
        self.archive = replay.EvidenceArchive(EVIDENCE / replay.ARCHIVES[replay.START_2])
        self.addCleanup(self.archive.close)

    def view(self, pinned_view: dict, name: str) -> dict:
        observation = self.archive.observation(replay.START_2, pinned_view["observation_id"])
        perception = self.archive.perception(replay.START_2, pinned_view["observation_id"])
        return {"name": name, "pan_deg": observation["robot_state"]["measured_joints_deg"]["shoulder_pan"], "joints": observation["robot_state"]["measured_joints_deg"],
                "observation_id": pinned_view["observation_id"], "record": {**perception, "observation_id": pinned_view["observation_id"]},
                "entries": [{"cube": perception["cube"], "cube_by_template": perception["cube_by_template"]}]}

    def test_the_pinned_numbers_are_the_archived_ones(self):
        for pinned_view, name in ((pinned.SEARCH_2037, "search"), (pinned.WAYPOINT_2037, "waypoint")):
            with self.subTest(name):
                view = self.view(pinned_view, name)
                self.assertEqual(pinned_view["homography_image_to_pad"], view["record"]["pad_by_template"]["homography_image_to_pad"])
                self.assertEqual(pinned_view["landmark_inliers"], view["record"]["pad_by_template"]["landmark_inliers"])
                for joint, value in pinned_view["joints"].items():
                    self.assertEqual(value, view["joints"][joint], joint)
                self.assertEqual("pregrasp-0-pan-30.0" if name == "waypoint" else None, (self.archive.observation(replay.START_2, pinned_view["observation_id"])["scan"] or {}).get("pose_id"))

    def test_the_two_views_fuse_into_one_block_the_controller_classes_base_advance(self):
        views = [self.view(pinned.SEARCH_2037, "search"), self.view(pinned.WAYPOINT_2037, "waypoint")]
        ctx, ctl = harness.HarnessCycle(home_pan_deg=pinned.HOME_2037_PAN_DEG), harness.controller_view()
        scene = scene_scan.fuse(views, lambda record, pan: policy_search.classify_block(ctx, ctl, record, pan, stands_at_pan_deg=pinned.HOME_2037_PAN_DEG),
                                pinned.HOME_2037_PAN_DEG)
        self.assertTrue(scene["fit"]["accepted"], scene["fit"])
        (block,) = scene["blocks"]
        self.assertEqual(["search", "waypoint"], block["views_seen"])
        self.assertEqual("direct", block["position_source"])
        self.assertLess(block["spread_mm"], 10.0, "cross-view agreement at a 20 deg pan change (9.6 mm measured on a yawed block)")
        self.assertEqual("BASE_ADVANCE", block["class"])
        self.assertAlmostEqual(pinned.START_2_PREGRASP_PAN_DEG - pinned.HOME_2037_PAN_DEG, block["inferred_pan_from_home_deg"], delta=1.5)
        self.assertFalse(block["outside_pan_band_inferred"])


if __name__ == "__main__":
    unittest.main()
