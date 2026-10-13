# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/manipulation/policy_search.py, track S: classify_block on the blocks of 2026-09-26, the scene scan behind its
words (the views, the refusals before motion, the partial scene, the events), and SEARCH from the scene under
scene_search (the early exit, ruling 7).

The scan is run on the stage harness (autonomy/tests/stage_harness.py) extended here: the views' perception comes from
synthetic pinhole homographies (test_scene_scan), the candidate listing from the script. The fake machine of the
differential test (test_cycle_behavior_preserved.World) runs the whole controller with --do scan in
TheSceneScanOnTheFakeMachine.
"""
import contextlib
import glob
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))

import stage_harness as harness  # noqa: E402  (puts the controller on the path)
import policy_search  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import test_recorded_pins_20260926 as pins  # noqa: E402
import test_scene_scan as synthetic  # noqa: E402
import test_cycle_mission_mode as mission  # noqa: E402  (the controller's main() on a scripted World)

HOME_PAN = harness.LOW_POSE["shoulder_pan"]


def with_words(*words):
    return harness.controller_view(PLACEMENT_ENVELOPE=tuple(words))


class ScanCycle(harness.HarnessCycle):
    """The harness Cycle with what a scan needs: a session directory, a detector (so none is made here), the targets
    run_pose was asked for, and the candidate listing answered from the script ("candidates:<observation id>": a list of
    entries), else refused as an unreadable frame."""

    def __init__(self, test, script=None, pose=None, home_pan_deg=None):
        super().__init__(script, pose, home_pan_deg)
        directory = tempfile.TemporaryDirectory(prefix="kc-scene-scan-")
        test.addCleanup(directory.cleanup)
        self.session_dir = Path(directory.name)
        self.detector = "harness-detector"
        self.targets = []

    def run_pose(self, target, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
        last = target[-1] if isinstance(target, (list, tuple)) else target
        self.targets.append((label, round(float(last.joints["shoulder_pan"]), 3)))
        return super().run_pose(target, holding, floor_m, pan_allowance, label, manipulation_step)

    def _mission_candidates(self, record):
        entries = self.script.get("candidates:" + str(record.get("observation_id")))  # read whole, never consumed call by call
        return ([], "mission_candidates_frame_unreadable", None) if entries is None else (list(entries), None, None)

    def scene_files(self):
        return sorted(self.session_dir.glob("scene-*.json"))

    def emitted(self, detector=None):
        return [args[1] for kind, args, _kwargs in self.events.calls if kind == "_emit" and args[0] == "perception.result" and (detector is None or args[1]["detector"] == detector)]


def home_file(test, joints=None):
    """The saved low scan pose of the harness (LOW_POSE), for the controller and the scan CLI."""
    directory = tempfile.TemporaryDirectory(prefix="kc-scene-home-")
    test.addCleanup(directory.cleanup)
    path = Path(directory.name) / "scan-home.json"
    path.write_text(json.dumps({"measured_joints_deg": dict(joints or harness.LOW_POSE)}))
    for target in (cycle, cycle.cli):
        patch = mock.patch.object(target, "HOME_FILE", path)
        patch.start()
        test.addCleanup(patch.stop)


def view_record(name: str, offset_deg: float, cubes=(), direct=True, both_ways=False) -> dict:
    """A view's frozen record at `offset_deg` of pan from home: the synthetic pinhole pad frame and the first cube. With
    `both_ways` the pad frame carries the pad-to-image homography as well, so the controller's _pad_frame accepts it and
    SEARCH can go on from the view."""
    homography = synthetic.pinhole_image_to_pad(offset_deg)
    template = {"pad_frame_valid": True, "homography_image_to_pad": homography, "landmark_inliers": 100} if direct else {"pad_frame_valid": False}
    if direct and both_ways:
        template["homography_pad_to_image"] = synthetic.np.linalg.inv(synthetic.np.asarray(homography)).tolist()
    first = cubes[0] if cubes else {"cube": {"detected": False}, "cube_by_template": None}
    return {"observation_id": f"obs-{name}", "frame": "harness-frame", "pad_by_template": template, "cube": first["cube"], "cube_by_template": first["cube_by_template"] if direct else None}


def scan_script(blocks=None, direct=True, views=None, label="SCENE_SCAN", both_ways=False) -> dict:
    """The perception and the listing of every view of a scan (SCAN_VIEWS unless named): `blocks` maps a view name to the
    pad points it sees."""
    script, views = {}, policy_search.SCAN_VIEWS if views is None else views
    for name, offset in list(views) + [("exit", 0.0)]:
        cubes = [synthetic.seen_cube(offset, point, direct=direct) for point in (blocks or {}).get(name, [])]
        script[f"perception:{'EXIT to the low scan pose' if name == 'exit' else f'{label} {name}'}"] = [[view_record(name, offset, cubes, direct, both_ways)]]
        script[f"candidates:obs-{name}"] = cubes
    return script


# ---------------------------------------------------------------------------------------------------------------------
# classify_block: the blocks of 2026-09-26
# ---------------------------------------------------------------------------------------------------------------------
class TheBlocksOf20260926(unittest.TestCase):
    """Overnight plan, track S, offline acceptance: starts 1 and 2 BASE_ADVANCE, the 19:19 block TOO_NEAR with the distance
    to move, start 3 OUTSIDE_PAN_BAND where it projects with both placement limits named (NOT_PLACEABLE on both when set
    on the reference column; its inferred pan in TheRecordedPairOf2037 in test_scene_scan), 21:17 NOT_PLACEABLE on the
    budget."""

    def classify(self, record, pan_deg, home_pan_deg):
        return policy_search.classify_block(harness.HarnessCycle(home_pan_deg=home_pan_deg), harness.controller_view(), record, pan_deg)

    def test_starts_1_and_2_are_base_advance_from_their_recorded_search_frames(self):
        for run_id in pins.replay.PLACED_RUNS:
            script = pins.PLACED_SCRIPTS[run_id]
            search = script["steps"][0]
            with self.subTest(run_id):
                classified = self.classify(search["records"][0], search["joints"]["shoulder_pan"], script["home"]["shoulder_pan"])
                self.assertEqual(("BASE_ADVANCE", None), (classified["class"], classified["reason_code"]))

    def test_the_1919_block_is_too_near_by_57_5_mm(self):
        # Its SEARCH frame's recorded numbers (the perception.result events of run m8c5f9e9: the box, the contact pixel and
        # the cube placed in PAD_FRAME). The run's homographies are not in the local evidence: start 1's SEARCH frame
        # stands in, and TOO_NEAR is decided before the reach direction is read from it.
        stand_in = pins.PLACED_SCRIPTS[pins.replay.START_1]["steps"][0]["records"][0]["pad_by_template"]
        record = {"observation_id": "obs-20260926T191923Z-wrist-0001", "cube": {"detected": True, "bbox_xyxy": [452, 594, 537, 697], "contact_px": [495.0, 696.0]},
                  "pad_by_template": stand_in, "cube_by_template": {"cube_center_pad_m": [-0.020454318267530837, -0.1800498082227241]}}
        classified = self.classify(record, pins.GRASP_MISS_INPUTS["search_pan_deg"], pins.GRASP_MISS_INPUTS["home_pan_deg"])
        self.assertEqual(("TOO_NEAR", "search_cube_inside_pregrasp_reach"), (classified["class"], classified["reason_code"]))
        self.assertEqual(-44.5, classified["values"]["cube_out_mm"])
        self.assertEqual(57.5, classified["values"]["move_block_at_least_mm"])

    def _at(self, anchor, contact_px):
        search = pins.PLACED_SCRIPTS[pins.replay.START_2]["steps"][0]
        record = {**search["records"][0], "cube": {"detected": True, "bbox_xyxy": [0, 0, 1, 1], "contact_px": list(contact_px)}, "cube_by_template": {"cube_center_pad_m": list(anchor)}}
        return self.classify(record, search["joints"]["shoulder_pan"], pins.PLACED_SCRIPTS[pins.replay.START_2]["home"]["shoulder_pan"])

    def _projected(self, anchor):
        """The pixel `anchor` projects to in start 2's SEARCH frame (its recorded pad-to-image homography)."""
        import numpy as np

        pad_to_image = pins.PLACED_SCRIPTS[pins.replay.START_2]["steps"][0]["records"][0]["pad_by_template"]["homography_pad_to_image"]
        q = np.asarray(pad_to_image) @ np.array([anchor[0], anchor[1], 1.0])
        return float(q[0] / q[2]), float(q[1] / q[2])

    def test_start_3_where_it_projects_is_outside_the_pan_band_and_names_both_placement_limits(self):
        # Start 3's own frame is not in the local evidence: its anchor is (the P0c pins), projected here through start 2's
        # SEARCH frame. SEARCH's order refuses it on the return bound first; the class still names both placement limits.
        classified = self._at(pins.START_3, self._projected(pins.START_3))
        self.assertEqual(("OUTSIDE_PAN_BAND", "search_cube_beyond_return_bound"), (classified["class"], classified["reason_code"]))
        values = classified["values"]
        self.assertAlmostEqual(43.4, values["pan_from_home_deg"], delta=0.5)
        self.assertGreater(values["pan_from_home_deg"], values["return_pan_bound_deg"], "the pan band")
        self.assertFalse(values["placeable"])
        self.assertLess(values["side_margin_mm"], values["required_side_margin_mm"], "the side limit")
        self.assertGreater(values["axis_advance_needed_unbounded_mm"], cycle.BASE_MAX_TOTAL_ADVANCE_MM, "the along-axis limit")

    def test_start_3_placed_on_the_reference_column_is_not_placeable_on_both_limits(self):
        # A constructed check of the placement limits alone: the anchor seen on the reference column of start 2's SEARCH
        # frame, where its coarse pregrasp pan lies inside the band. No record shows start 3 there.
        classified = self._at(pins.START_3, (553.0, 600.0))
        self.assertEqual(("NOT_PLACEABLE", "search_placement_infeasible"), (classified["class"], classified["reason_code"]))
        values = classified["values"]
        self.assertLess(values["side_margin_mm"], values["required_side_margin_mm"], "the side limit")
        self.assertGreater(values["axis_advance_needed_unbounded_mm"], cycle.BASE_MAX_TOTAL_ADVANCE_MM, "the along-axis limit")

    def test_the_scene_block_of_start_3_names_the_pan_band_and_both_placement_limits(self):
        classified = self._at(pins.START_3, self._projected(pins.START_3))
        block = {"position_pad_m": list(pins.START_3), "values": classified["values"], "class": classified["class"], "reason_code": classified["reason_code"],
                 "position_source": "direct", "best_observation_id": "obs-start-3", "best_view": "home", "best_pan_deg": 0.0, "views_seen": ["home"], "spread_mm": 0.0,
                 "outside_pan_band_inferred": None, "inferred_pan_from_home_deg": None}
        named = {item["name"]: item["value"] for item in policy_search._block_measurements(harness.controller_view(), block)}
        self.assertEqual(("OUTSIDE_PAN_BAND", False, "side_edge"), (named["class"], named["placeable"], named["binding_constraint"]))
        self.assertGreater(named["pan_from_home_deg"], named["pan_band_deg"])
        self.assertLess(named["side_margin_mm"], named["required_side_margin_mm"])
        self.assertGreater(named["deficit_mm"], 0.0)

    def test_a_block_outside_the_pan_band_that_a_placement_admits_says_so(self):
        # Start 1's anchor, placeable as BASE_ADVANCE, with the return bound narrowed below its pan from home.
        search = pins.PLACED_SCRIPTS[pins.replay.START_1]["steps"][0]
        ctx = harness.HarnessCycle(home_pan_deg=pins.PLACED_SCRIPTS[pins.replay.START_1]["home"]["shoulder_pan"])
        with mock.patch.object(cycle, "RETURN_PAN_BOUND_DEG", 0.0):
            classified = policy_search.classify_block(ctx, harness.controller_view(), search["records"][0], search["joints"]["shoulder_pan"])
        self.assertEqual(("OUTSIDE_PAN_BAND", "search_cube_beyond_return_bound"), (classified["class"], classified["reason_code"]))
        self.assertTrue(classified["values"]["placeable"])
        self.assertEqual("BASE_ADVANCE", classified["values"]["placement_mode"])

    def test_the_2117_block_is_not_placeable_on_the_budget_alone(self):
        classified = self._at(pins.LATE_2117, self._projected(pins.LATE_2117))
        self.assertEqual("NOT_PLACEABLE", classified["class"])
        values = classified["values"]
        self.assertGreater(values["side_margin_mm"], values["required_side_margin_mm"], "its side is clear")
        self.assertAlmostEqual(131.5, values["axis_advance_needed_unbounded_mm"], delta=0.5)

    def test_the_entry_bound_is_taken_from_where_the_arm_will_stand(self):
        record = pins.PLACED_SCRIPTS[pins.replay.START_1]["steps"][0]["records"][0]
        home = pins.PLACED_SCRIPTS[pins.replay.START_1]["home"]["shoulder_pan"]
        ctx = harness.HarnessCycle(home_pan_deg=home)
        coarse = cycle.coarse_pregrasp_from_low_pose(record["cube"]["contact_px"], home - 10.0)
        with mock.patch.object(cycle, "MAX_ENTRY_PAN_SWEEP_DEG", 1.0), mock.patch.object(cycle, "RETURN_PAN_BOUND_DEG", 60.0):
            ctl = harness.controller_view()
            self.assertEqual("search_cube_beyond_pregrasp_entry_bound", policy_search.classify_block(ctx, ctl, record, home - 10.0)["reason_code"])
            standing = policy_search.classify_block(ctx, ctl, record, home - 10.0, stands_at_pan_deg=coarse["pan_deg"])
            self.assertNotEqual("search_cube_beyond_pregrasp_entry_bound", standing["reason_code"], "entered from the pan the arm stands at")


# ---------------------------------------------------------------------------------------------------------------------
# --do scan on the harness
# ---------------------------------------------------------------------------------------------------------------------
class TheSceneScan(unittest.TestCase):
    def setUp(self):
        home_file(self)

    def test_without_its_word_it_refuses_before_any_motion(self):
        for words in ((), ("scene_scan_25",), ("scene_search",)):
            with self.subTest(words=words):
                ctx = ScanCycle(self)
                with self.assertRaises(cycle.ScanRefused) as refused:
                    policy_search.scene_scan(ctx, with_words(*words), False)
                self.assertTrue(str(refused.exception).startswith("SCAN: "))
                self.assertEqual([], ctx.calls)

    def test_the_views_are_single_pose_calls_at_the_low_pose_shape_then_the_exit_and_its_settle(self):
        ctx = ScanCycle(self, scan_script())
        policy_search.scene_scan(ctx, with_words("scene_scan"), False)
        self.assertEqual(["SCENE_SCAN home", "SCENE_SCAN minus_15", "SCENE_SCAN centre", "SCENE_SCAN plus_15", "EXIT to the low scan pose", "SETTLE goals on the measured pose"], ctx.labels("run_pose"))
        pans = [pan for _label, pan in ctx.targets]
        self.assertEqual([round(HOME_PAN + offset, 3) for offset in (0.0, -15.0, 0.0, 15.0, 0.0)], pans[:5])
        self.assertEqual(["SCENE_SCAN"] * 5 + ["SCENE"], ctx.states())

    def test_the_25_deg_views_come_only_under_scene_scan_25(self):
        ctx = ScanCycle(self, scan_script(views=policy_search.SCAN_VIEWS_25))
        policy_search.scene_scan(ctx, with_words("scene_scan", "scene_scan_25"), False)
        self.assertEqual([round(HOME_PAN + offset, 3) for offset in (0.0, -15.0, -25.0, 0.0, 15.0, 25.0, 0.0)], [pan for _label, pan in ctx.targets][:7])
        pans = [HOME_PAN] + [pan for _label, pan in ctx.targets]
        self.assertLessEqual(max(abs(b - a) for a, b in zip(pans, pans[1:])), 25.0, "no step between two views is wider than the return bound")

    def test_it_refuses_before_motion_off_the_low_pose_with_the_gripper_not_open_or_beyond_60_deg(self):
        cases = {"off the low pose": ({**harness.LOW_POSE, "elbow_flex": harness.LOW_POSE["elbow_flex"] + 8.0}, None, ("scene_scan",)),
                 "the gripper not open": ({**harness.LOW_POSE, "gripper": 68.4}, None, ("scene_scan",)),
                 "a 15 deg view beyond 60": ({**harness.LOW_POSE, "shoulder_pan": 50.0}, {**harness.LOW_POSE, "shoulder_pan": 50.0}, ("scene_scan",)),
                 "a 25 deg view beyond 60": ({**harness.LOW_POSE, "shoulder_pan": 40.0}, {**harness.LOW_POSE, "shoulder_pan": 40.0}, ("scene_scan", "scene_scan_25"))}
        for case, (pose, home, words) in cases.items():
            with self.subTest(case):
                if home is not None:
                    home_file(self, home)
                ctx = ScanCycle(self, pose=pose)
                with self.assertRaises(cycle.ScanRefused) as refused:
                    policy_search.scene_scan(ctx, with_words(*words), False)
                self.assertIn("nothing was commanded", str(refused.exception))
                self.assertEqual([], ctx.calls)
                self.assertEqual([], ctx.scene_files())
                home_file(self)

    def test_an_abort_after_motion_ends_with_the_partial_scene_and_propagates(self):
        script = scan_script({"minus_15": [(0.05, -0.20)]})
        script["SCENE_SCAN plus_15"] = RuntimeError("step aborted: operator abort")
        ctx = ScanCycle(self, script)
        with self.assertRaises(RuntimeError) as stopped:
            policy_search.scene_scan(ctx, with_words("scene_scan"), False)
        self.assertEqual("step aborted: operator abort", str(stopped.exception))
        (written,) = ctx.scene_files()
        scene = json.loads(written.read_text())
        self.assertEqual("stopped", scene["partial"])
        self.assertEqual(["home", "minus_15", "centre"], [view["name"] for view in scene["views"]])
        self.assertEqual(1, len(scene["blocks"]))
        self.assertNotIn("EXIT to the low scan pose", ctx.labels("run_pose"), "the arm holds where it stopped")

    def test_a_stop_raised_while_a_view_is_listed_stops_the_scan_there(self):
        ctx = ScanCycle(self, scan_script())
        listed = ctx._mission_candidates

        def stopping(record):
            if record.get("observation_id") == "obs-minus_15":
                ctx.abort["set"] = True
                raise RuntimeError("operator abort: no perception is started once the stop is requested")
            return listed(record)

        ctx._mission_candidates = stopping
        with self.assertRaises(RuntimeError):
            policy_search.scene_scan(ctx, with_words("scene_scan"), False)
        scene = json.loads(ctx.scene_files()[0].read_text())
        self.assertEqual(("stopped", ["home"]), (scene["partial"], [view["name"] for view in scene["views"]]))
        self.assertEqual(["SCENE_SCAN home", "SCENE_SCAN minus_15"], ctx.labels("run_pose"), "nothing is commanded after the stop")

    def test_a_view_refused_after_motion_is_a_cycle_failure_with_the_partial_scene(self):
        script = scan_script()
        script["SCENE_SCAN centre"] = cycle.ScanRefused("the planner refuses this pose")
        ctx = ScanCycle(self, script)
        with self.assertRaises(cycle.CycleFailed) as failed:
            policy_search.scene_scan(ctx, with_words("scene_scan"), False)
        self.assertEqual("scene_scan_step_refused", harness.failure_code(ctx, failed.exception))
        self.assertEqual("refused", json.loads(ctx.scene_files()[0].read_text())["partial"])

    def test_the_first_view_refused_is_still_a_refusal_before_motion(self):
        ctx = ScanCycle(self, {"SCENE_SCAN home": cycle.ScanRefused("the planner refuses this pose")})
        with self.assertRaises(cycle.ScanRefused):
            policy_search.scene_scan(ctx, with_words("scene_scan"), False)
        self.assertEqual([], ctx.scene_files())

    def test_plan_only_plans_every_view_and_the_exit_and_writes_no_scene(self):
        ctx = ScanCycle(self)
        ctx.plan_only = True
        with mock.patch.object(ScanCycle, "_arrive", lambda self, target, holding: {"planned": True, "measured": dict(self.current)}):
            self.assertEqual({"planned": True}, policy_search.scene_scan(ctx, with_words("scene_scan"), True))
        self.assertEqual(["SCENE_SCAN home", "SCENE_SCAN minus_15", "SCENE_SCAN centre", "SCENE_SCAN plus_15", "EXIT to the low scan pose"], ctx.labels("run_pose"))
        self.assertEqual([], ctx.scene_files())

    def test_a_plan_only_argument_the_run_does_not_share_is_refused_before_any_motion(self):
        for argument, run in ((True, False), (False, True)):
            with self.subTest(argument=argument, run=run):
                ctx = ScanCycle(self, scan_script())
                ctx.plan_only = run
                with self.assertRaises(cycle.ScanRefused) as refused:
                    policy_search.scene_scan(ctx, with_words("scene_scan"), argument)
                self.assertIn("nothing was commanded", str(refused.exception))
                self.assertEqual([], ctx.calls)
                self.assertEqual([], ctx.scene_files())

    def test_the_scene_is_one_json_and_the_scene_events_the_map_reads(self):
        ctx = ScanCycle(self, scan_script({"minus_15": [(0.05, -0.20)], "home": [(0.05, -0.20), (-0.08, -0.19)], "centre": [(-0.08, -0.19)]}))
        scene = policy_search.scene_scan(ctx, with_words("scene_scan"), False)
        self.assertEqual(scene, json.loads(ctx.scene_files()[0].read_text()))
        self.assertTrue(scene["fit"]["accepted"], scene["fit"])
        self.assertEqual(2, len(scene["blocks"]))
        self.assertEqual(["scene-view"] * 5 + ["scene-pad"] + ["scene-block"] * 2, [payload["detector"] for payload in ctx.emitted()])
        names = lambda payload: {item["name"] for item in payload["measurements"]}  # noqa: E731
        self.assertLessEqual({"view_index", "view", "pan_deg", "pad_frame_valid", "accepted", "footprint_0_x", "footprint_0_y", "landmark_inliers"}, names(ctx.emitted("scene-view")[0]))
        self.assertLessEqual({"axis_x", "axis_y", "home_bearing_deg", "pan_band_deg", "fit_rotation_deg", "fit_residual_mm", "fit_accepted"}, names(ctx.emitted("scene-pad")[0]))
        self.assertLessEqual({"pad_x", "pad_y", "class", "inferred", "best_observation", "views_seen", "spread_mm"}, names(ctx.emitted("scene-block")[0]))
        self.assertEqual([0, 1, 2, 3, 4], [next(item["value"] for item in payload["measurements"] if item["name"] == "view_index") for payload in ctx.emitted("scene-view")])

    def test_it_runs_under_the_search_stages_identity(self):
        ctx = ScanCycle(self, scan_script())
        policy_search.scene_scan(ctx, with_words("scene_scan"), False)
        entered = [args[0] for kind, args, _kwargs in ctx.events.calls if kind == "enter_policy"]
        self.assertTrue(entered and all(identity["rule_ref"] == "search.scene_scan" for identity in entered))


# ---------------------------------------------------------------------------------------------------------------------
# SEARCH from the scene
# ---------------------------------------------------------------------------------------------------------------------
class SearchFromTheScene(unittest.TestCase):
    def setUp(self):
        home_file(self)

    def test_with_no_word_search_takes_no_scan(self):
        ctx = ScanCycle(self, {"observe": harness.view(harness.LOW_POSE)})
        with self.assertRaises(cycle.CycleFailed):
            policy_search.run_search(ctx, harness.controller_view(), {}, dict(harness.LOW_POSE))
        self.assertNotIn("SEARCH_SCENE", ctx.states())
        self.assertFalse(any(label.startswith("SEARCH_SCENE") for label in ctx.labels("run_pose")))

    def test_the_centre_frame_alone_decides_one_unclipped_block_in_a_mode(self):
        script = pins.PLACED_SCRIPTS[pins.replay.START_1]
        record = {**script["steps"][0]["records"][0], "frame": "harness-frame"}
        ctx = ScanCycle(self, {"observe": harness.view(harness.LOW_POSE, [record]), f"candidates:{record['observation_id']}": [{"cube": record["cube"], "cube_by_template": record["cube_by_template"]}]},
                        home_pan_deg=script["home"]["shoulder_pan"])
        search = ctx.observe(False)
        measured, frame, chosen, stands = policy_search._search_from_scene(ctx, with_words("scene_search"), search, dict(harness.LOW_POSE))
        self.assertEqual((harness.LOW_POSE, search, None, None), (measured, frame, chosen, stands))
        self.assertEqual([], ctx.labels("run_pose"), "no scan")
        self.assertEqual({"early_exit": True, "centre_listing": "complete", "centre_class": "BASE_ADVANCE"}, {k: ctx.transitions[-1][k] for k in ("early_exit", "centre_listing", "centre_class")})

    def test_a_block_seen_only_off_centre_is_chosen_from_its_best_view_and_the_pregrasp_enters_from_home(self):
        script = scan_script({"minus_15": [(0.05, -0.20)]}, label="SEARCH_SCENE")
        script["observe"] = harness.view(harness.LOW_POSE, [view_record("observe", 0.0)])
        ctx = ScanCycle(self, script)
        measured, frame, chosen, stands = policy_search._search_from_scene(ctx, with_words("scene_search"), ctx.observe(False), dict(harness.LOW_POSE))
        self.assertAlmostEqual(HOME_PAN - 15.0, measured["shoulder_pan"], places=3)
        self.assertEqual("obs-minus_15", frame["perception"][-1]["observation_id"])
        self.assertEqual([0.05, -0.2], chosen["cube_by_template"]["cube_center_pad_m"])
        self.assertAlmostEqual(HOME_PAN, stands["shoulder_pan"], places=3)
        self.assertEqual("SEARCH_SCENE_SELECTED", ctx.states()[-1])

    def test_search_hands_on_the_pose_of_the_view_its_frame_came_from_and_enters_the_pregrasp_from_home(self):
        # PLACEMENT reads SearchOutcome.measured's pan as the pan of the SEARCH frame (side_offset's release geometry):
        # after a scene scan that is the chosen block's best view, never the saved low pose the arm stands at.
        for name, offset, point in (("minus_15", -15.0, (0.05, -0.20)), ("plus_15", 15.0, (-0.10, -0.19))):
            with self.subTest(name):
                script = scan_script({name: [point]}, label="SEARCH_SCENE", both_ways=True)
                script["observe"] = harness.view(harness.LOW_POSE, [view_record("observe", 0.0, both_ways=True)])
                ctx = ScanCycle(self, script)
                bounds = []
                real = ctx._refuse_pregrasp_beyond_pan_bounds
                ctx._refuse_pregrasp_beyond_pan_bounds = lambda pan, now, seen: (bounds.append(now), real(pan, now, seen))[1]
                found = policy_search.run_search(ctx, with_words("scene_search"), {}, dict(harness.LOW_POSE))
                self.assertEqual(f"obs-{name}", found.seen["observation_id"])
                self.assertAlmostEqual(HOME_PAN + offset, found.measured["shoulder_pan"], places=3)
                self.assertEqual([round(HOME_PAN, 3)], [round(now, 3) for now in bounds], "the entry bound is taken from where the arm stands")

    def test_two_unplaced_blocks_anywhere_in_the_scene_are_refused_before_anything_is_picked_up_naming_both(self):
        script = scan_script({"minus_15": [(0.05, -0.20)], "plus_15": [(-0.12, -0.19)]}, label="SEARCH_SCENE")
        script["observe"] = harness.view(harness.LOW_POSE, [view_record("observe", 0.0)])
        ctx = ScanCycle(self, script)
        with self.assertRaises(cycle.CycleFailed) as refused:
            policy_search.run_search(ctx, with_words("scene_search"), {}, dict(harness.LOW_POSE))
        self.assertEqual("search_scene_not_one_unplaced_block", harness.failure_code(ctx, refused.exception))
        self.assertIn("block-1 at (+0.050, -0.200) m", str(refused.exception))
        self.assertIn("block-2 at (-0.120, -0.190) m", str(refused.exception))
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("scene_one_unplaced_block")])
        self.assertFalse(any(label.startswith("PREGRASP") for label in ctx.labels("run_pose")))

    def test_a_refusal_from_the_scenes_best_view_names_that_view(self):
        script = scan_script({"minus_15": [(0.05, -0.20)]}, direct=False, label="SEARCH_SCENE")
        script["observe"] = harness.view(harness.LOW_POSE, [view_record("observe", 0.0)])
        ctx = ScanCycle(self, script)
        with self.assertRaises(cycle.CycleFailed) as refused:
            policy_search.run_search(ctx, with_words("scene_search"), {}, dict(harness.LOW_POSE))
        self.assertEqual("search_cube_not_in_pad_frame", harness.failure_code(ctx, refused.exception))
        self.assertIn("obs-minus_15", str(refused.exception))
        self.assertNotIn("low-pose", str(refused.exception))

    def test_an_empty_scene_is_refused(self):
        script = scan_script(label="SEARCH_SCENE")
        script["observe"] = harness.view(harness.LOW_POSE, [view_record("observe", 0.0)])
        ctx = ScanCycle(self, script)
        with self.assertRaises(cycle.CycleFailed) as refused:
            policy_search.run_search(ctx, with_words("scene_search"), {}, dict(harness.LOW_POSE))
        self.assertEqual("search_scene_no_block", harness.failure_code(ctx, refused.exception))


class TheSweepWord(unittest.TestCase):
    """SEARCH SWEEP runs only under the search stage's word search_sweep, declared and not qualified: with no word a low-pose
    frame with no usable cube is refused search_no_cube before any motion, as the 2026-09-20 tag refused it, on every path
    (a LIVE run, a rehearsal, a plan-only run). The sweep itself is unchanged under the word."""

    SWEEP = "search_sweep"

    def setUp(self):
        home_file(self)

    @staticmethod
    def empty_low_pose():
        return harness.view(harness.LOW_POSE, [harness.cube_record(None)])

    def run_search_with(self, words, plan_only=False):
        ctx = harness.HarnessCycle({"observe": self.empty_low_pose()})
        ctx.plan_only = plan_only
        with mock.patch.object(cycle, "PLACEMENT_ENVELOPE", tuple(words)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(cycle.CycleFailed) as refused:
                policy_search.run_search(ctx, with_words(*words), {"placed": False}, ctx.pose())
        return ctx, harness.failure_code(ctx, refused.exception), str(refused.exception)

    def test_the_word_is_declared_by_the_search_stage_and_not_qualified(self):
        self.assertIn(self.SWEEP, policy_search.WORDS)
        self.assertNotIn(self.SWEEP, policy_search.QUALIFIED)
        self.assertIn(self.SWEEP, cycle.envelope_words_declared())
        self.assertNotIn(self.SWEEP, cycle.envelope_words_qualified())

    def test_with_no_word_a_frame_with_no_usable_cube_is_refused_before_any_motion(self):
        for plan_only in (False, True):
            with self.subTest(plan_only=plan_only):
                ctx, code, sentence = self.run_search_with((), plan_only=plan_only)
                self.assertEqual("search_no_cube", code)
                self.assertEqual([], ctx.labels("run_pose"), "no sector is commanded without the word")
                self.assertEqual(["SEARCH"], ctx.states())
                self.assertIn("no cube with a usable contact point in the low-pose wrist frame", sentence)

    def test_other_words_do_not_open_the_sweep(self):
        for words in (("scene_scan",), ("align_v2",), ("projected",)):
            with self.subTest(words=words):
                ctx, code, _ = self.run_search_with(words)
                self.assertEqual("search_no_cube", code)
                self.assertEqual([], ctx.labels("run_pose"))

    def test_the_stage_function_itself_commands_nothing_without_the_word(self):
        ctx = harness.HarnessCycle()
        search, measured = self.empty_low_pose(), dict(harness.LOW_POSE)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual((measured, search), policy_search.search_sweep(ctx, with_words(), search, measured))
        self.assertEqual([], ctx.labels("run_pose"))

    def test_under_the_word_the_sweep_runs_as_before(self):
        ctx, code, _ = self.run_search_with((self.SWEEP,))
        self.assertEqual("search_no_cube_after_sweep", code)
        self.assertEqual(["SEARCH_SWEEP LEFT", "SEARCH_SWEEP RIGHT", "SEARCH_SWEEP return to centre"], ctx.labels("run_pose"))


class TheSweepWordThroughTheController(mission.MissionCase):
    """main() on the scripted World with no cube in any frame: a LIVE run with no word is refused search_no_cube with no
    command sent, and a mission run with no word is refused by its target selection with no command sent; a LIVE run
    naming search_sweep without the attended label is refused before the robot is constructed."""

    def world(self):
        return mission.SweepWorld(floor=[{"pad_m": mission.A_START, "contact_px": mission.CONTACT}], cube_start_pad_m=mission.A_START, cube_final_pad_m=mission.A_FINAL)

    def live(self, **options):
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
            return mission.run_world(self.world(), self.directory, real_move_base=True, runtime_mode=mission._assigned(mission.SUPERVISOR, "LIVE"), **options)

    def test_a_live_run_with_no_word_and_no_usable_cube_sends_nothing(self):
        run = self.live()
        self.assertEnded(run, 5)
        self.assertEqual([], run["sent"])
        self.assertEqual([], run["base_pulses"])
        self.assertEqual(["SEARCH"], run["states"])
        self.assertIn("CYCLE FAILED: SEARCH: no cube with a usable contact point in the low-pose wrist frame.", run["stdout"])

    def test_a_live_run_naming_the_word_without_the_attended_label_is_refused_before_the_robot_exists(self):
        run = self.live(extra_env={"KC_PLACEMENT_ENVELOPE": "search_sweep"})
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))
        self.assertIn("not qualified", run["stdout"])

    def test_a_mission_with_no_word_and_no_usable_cube_is_refused_by_the_selection_before_any_command(self):
        """A mission run (an AUTO target) with no word, in a rehearsal and LIVE: the low-pose frame lists no cube, so the
        target selection refuses it, mission_binding_no_candidate, with no command sent and no sector stated."""
        for live in (False, True):
            with self.subTest(live=live):
                options = {"real_move_base": True, "runtime_mode": mission._assigned(mission.SUPERVISOR, "LIVE")} if live else {}
                with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None} if live else {}):
                    target, run = self.mission(self.world(), mission.TheAutoTargetTakesTheMeasuredMode.auto_target, **options)
                self.assertEnded(run, 5)
                self.assertEqual(([], []), (run["sent"], run["base_pulses"]))
                self.assertNotIn("SEARCH_SWEEP", run["states"])
                result = run["result"]
                self.assertEqual(("finished", "failed", "mission_binding_no_candidate"), (result["stage"], result["outcome"], result["reason_code"]))
                self.assertFalse(mission.tb.result_claims_placement(result, target))

    def test_a_rehearsal_under_the_word_sweeps(self):
        run = mission.run_world(self.world(), self.directory, extra_env={"KC_PLACEMENT_ENVELOPE": "search_sweep"})
        self.assertEnded(run, 5)
        self.assertEqual(["SEARCH", "SEARCH_SWEEP", "SEARCH_SWEEP"], [state for state in run["states"] if state != "PLACEMENT_ENVELOPE"])
        self.assertIn("nor in its sweep sectors LEFT and RIGHT", run["stdout"])
        self.assertGreater(len(run["sent"]), 0)


# ---------------------------------------------------------------------------------------------------------------------
# --do scan on the fake machine: the whole controller, main() included
# ---------------------------------------------------------------------------------------------------------------------
class TheSceneScanOnTheFakeMachine(unittest.TestCase):
    def setUp(self):
        import test_cycle_behavior_preserved as differential
        import test_run_single_cube_cycle as fixtures

        self.differential = differential
        self.directory = fixtures.use_synthetic_runtime(self)

    def _run(self, world, words="scene_scan", events=False):
        path = os.path.join(self.directory, "scan-events.jsonl") if events else None
        run = self.differential.run_scripted_world(cycle, world, self.directory, do="scan", configured=True, events_path=path, extra_env={cycle.PLACEMENT_ENVELOPE_ENV: words})
        run["scenes"] = [json.loads(Path(name).read_text()) for name in sorted(glob.glob(os.path.join(self.directory, "**", "scene-*.json"), recursive=True))]
        if events:
            with open(path) as handle:
                run["events"] = [json.loads(line) for line in handle if line.strip()]
        return run

    def test_a_scan_moves_the_pan_only_and_returns_to_the_saved_pose(self):
        run = self._run(self.differential.World())
        self.assertEqual(("returned", 0), run["ending"], run["stdout"][-3000:])
        self.assertEqual([], run["base_pulses"])
        home = self.differential.fixtures.SYNTHETIC_LOW_POSE
        for action in run["sent"]:
            for joint in ("shoulder_lift", "elbow_flex", "wrist_flex"):
                self.assertAlmostEqual(home[joint], action[f"arm_{joint}.pos"], places=2, msg=joint)
        pans, run_length = [], 0  # where the pan dwelt: a value commanded five times in a row or more, each dwell once
        commanded = [round(action["arm_shoulder_pan.pos"], 1) for action in run["sent"]]
        for index, pan in enumerate(commanded):
            run_length = run_length + 1 if index and commanded[index - 1] == pan else 1
            if run_length == 5 and (not pans or pans[-1] != pan):
                pans.append(pan)
        self.assertEqual([round(home["shoulder_pan"] + offset, 1) for offset in (0.0, -15.0, 0.0, 15.0, 0.0)], pans)
        (scene,) = run["scenes"]
        self.assertIsNone(scene["partial"])
        self.assertEqual(["home", "minus_15", "centre", "plus_15", "exit"], [view["name"] for view in scene["views"]])
        self.assertIn("SCENE block-1:", run["stdout"])

    def test_the_scene_reaches_the_record_as_perception_results(self):
        run = self._run(self.differential.World(), events=True)
        detectors = [line["payload"]["detector"] for line in run["events"] if line["kind"] == "perception.result" and line["payload"]["detector"].startswith("scene-")]
        self.assertEqual(["scene-view"] * 5 + ["scene-pad", "scene-block"], detectors)
        finished = [line for line in run["events"] if line["kind"] == "run.finished"][-1]["payload"]
        self.assertEqual(0, finished["exit_status"])

    def test_a_cycle_under_scene_search_scans_selects_and_places_the_one_block(self):
        run = self.differential.run_scripted_world(cycle, self.differential.World(), self.directory, do="cycle", configured=True,
                                                   extra_env={cycle.PLACEMENT_ENVELOPE_ENV: "scene_search"})
        self.assertEqual(("returned", 0), run["ending"], run["stdout"][-3000:])
        states = [entry["state"] for entry in run["transitions"]]
        self.assertEqual(["PLACEMENT_ENVELOPE", "SEARCH", "SEARCH_SCENE"] + ["SCENE_SCAN"] * 5 + ["SCENE", "SEARCH_SCENE_SELECTED", "PREGRASP"], states[:11])
        self.assertEqual("PLACED", [state for state in states if state == "PLACED"][0])
        self.assertIn("'placed': True", run["stdout"])

    def test_a_base_that_does_not_read_zero_stops_the_scan_with_the_partial_scene(self):
        class MovingBase(self.differential.World):
            def message(inner):
                body = json.loads(super().message())
                if len(inner.sent) > 300:  # after the first views: the base reads a velocity
                    body["x.vel"] = 0.05
                return json.dumps(body)

        run = self._run(MovingBase())
        self.assertEqual(("returned", 4), run["ending"], run["stdout"][-3000:])
        self.assertIn("STOPPED:", run["stdout"])
        (scene,) = run["scenes"]
        self.assertEqual("stopped", scene["partial"])
        self.assertLess(len(scene["views"]), 5)


if __name__ == "__main__":
    unittest.main()
