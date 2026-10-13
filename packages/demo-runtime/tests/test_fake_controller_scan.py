#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_fake_controller_scan.py: the fake controller's `--do scan`, so a synthetic runtime can
# run the scan_scene command end to end. It plays the scene scan the console's scene-scan scenario plays (track U2):
# pan-only views, each a SCENE_SCAN transition with its [STATE] line and a scene-view result, then the fused scene as one
# scene-pad and scene-block results, in the measurement spellings model/sceneMap.ts reads. Every record validates against
# contract v1, and a plan-only scan plans its pans and moves nothing.
# Run directly: python3 packages/demo-runtime/tests/test_fake_controller_scan.py
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_runtime import journal  # noqa: E402,F401  (puts demo_contracts on the path)

import demo_contracts  # noqa: E402  (importable once demo_runtime was imported)
from test_rpc import WAIT_S, Runtime  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_controller.py"


def run_fake(test, *extra) -> tuple:
    tmp = Path(tempfile.mkdtemp(prefix="kc-fake-scan-"))
    events_path = tmp / "events.jsonl"
    env = {**os.environ, "KC_EVENTS_PATH": str(events_path), "KC_RUN_ID": "scan-run-0001", "KC_FAKE_STEP_S": "0.0"}
    done = subprocess.run([sys.executable, str(FAKE), "--do", "scan", *extra, "--remote-ip", "203.0.113.10", "--robot-id", "test_robot"],
                          capture_output=True, text=True, env=env, timeout=60)
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    return done, events


def measurement(result: dict, name: str):
    return next(item["value"] for item in result["payload"]["measurements"] if item["name"] == name)


class TheScanScript(unittest.TestCase):
    def test_it_plays_the_views_and_the_fused_scene_and_every_record_is_contract_v1(self):
        done, events = run_fake(self)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([error for event in events for error in demo_contracts.validate("event", event)], [])
        kinds = [event["kind"] for event in events]
        self.assertEqual((kinds[0], kinds[-1]), ("run.started", "run.finished"))
        scans = [event for event in events if event["kind"] == "state.transition" and event["payload"]["to_state"] == "SCENE_SCAN"]
        lines = [event["payload"]["text"] for event in events if event["kind"] == "diagnostic.text" and event["payload"]["text"].startswith("[STATE] SCENE_SCAN ")]
        self.assertGreaterEqual(len(scans), 3)
        self.assertEqual(len(lines), len(scans))
        fields = json.loads(lines[0][len("[STATE] SCENE_SCAN "):])
        self.assertEqual(set(fields), {"view", "pan_deg", "observation_id"})
        results = [event for event in events if event["kind"] == "perception.result"]
        views = [event for event in results if event["payload"]["detector"] == "scene-view"]
        pads = [event for event in results if event["payload"]["detector"] == "scene-pad"]
        blocks = [event for event in results if event["payload"]["detector"] == "scene-block"]
        self.assertEqual(len(views), len(scans))
        self.assertEqual(len(pads), 1)
        self.assertGreaterEqual(len(blocks), 1)
        for name in ("view_index", "view", "pan_deg", "pad_frame_valid", "landmark_inliers", "footprint_0_x", "footprint_0_y", "accepted"):
            measurement(views[0], name)
        for name in ("axis_x", "axis_y", "home_pan_deg", "home_bearing_deg", "pan_band_deg", "fit_rotation_deg", "measured_pan_change_deg",
                     "fit_residual_mm", "fit_accepted", "views_seen"):
            measurement(pads[0], name)
        for name in ("pad_x", "pad_y", "class", "best_observation", "views_seen", "spread_mm", "inferred"):
            measurement(blocks[0], name)
        finished = events[-1]["payload"]
        self.assertEqual(finished["outcome"], "unknown")

    def test_a_plan_only_scan_plans_its_pans_and_moves_nothing(self):
        done, events = run_fake(self, "--plan-only")
        self.assertEqual(done.returncode, 0, done.stderr)
        kinds = [event["kind"] for event in events]
        self.assertIn("action.planned", kinds)
        self.assertNotIn("action.started", kinds)


class TheSyntheticRuntimeRunsAScan(unittest.TestCase):
    def test_a_scan_scene_command_runs_to_its_end_with_the_scene_on_the_stream(self):
        runtime = Runtime(self, step_s="0.0")
        started = runtime.client.submit(runtime.client.new_command("scan_scene", {"plan_only": False}, command_id="scan-0001"))
        self.assertEqual(started["status"], "running", started)
        deadline = time.monotonic() + WAIT_S
        while runtime.client.result("scan-0001")["status"] != "finished" and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(runtime.client.result("scan-0001")["status"], "finished")
        events = [event for event in runtime.client.events(0, idle_timeout_s=0.5) if event["run_id"] == started["run_id"]]
        self.assertEqual([error for event in events for error in demo_contracts.validate("event", event)], [])
        detectors = {event["payload"]["detector"] for event in events if event["kind"] == "perception.result"}
        self.assertTrue({"scene-view", "scene-pad", "scene-block"} <= detectors)
        finished = [event["payload"]["outcome"] for event in events if event["kind"] == "run.finished"]
        self.assertEqual(finished, ["unknown"])


if __name__ == "__main__":
    unittest.main()
