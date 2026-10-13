#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_scan_world.py: proves the SCAN_WORLD executor's software
# contract: plan-only by default, no motion without the operator-approved
# digest of the exact pose set, no channel through which a base command could
# be sent, base-zero confirmed before/after every segment, and every stored
# frame paired with joints MEASURED (not commanded) before and after it.
# A fake robot proves the contract ONLY. It does not establish that the scan
# is physically safe, that the arm settles in the allotted time, or that any
# frame is useful.
# Run directly: python3 autonomy/tests/test_scan_world.py
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "perception"))

from calibration_inventory import CalibrationInventory  # noqa: E402
from camera_lifecycle import CaptureResult, LifecycleRecord  # noqa: E402
from hybrid_pick_place import KNOWN_GOOD_GRASP_POSE_DEG  # noqa: E402
from observation_store import ObservationStore  # noqa: E402
import scan_world  # noqa: E402
from scan_poses import SCAN_JOINTS, generate_scan_poses, scan_plan_digest  # noqa: E402
from scan_world import ARRIVAL_TOLERANCE_DEG, BASE_ZERO_POLL_S, START_POSE_TOLERANCE_DEG, ScanRefused, ScanWorldDeps, run_scan_world  # noqa: E402

ZERO_BASE = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}
ELEVATED = {"shoulder_pan": -9.05, "shoulder_lift": -57.89, "elbow_flex": 17.71, "wrist_flex": 49.80}  # the held reveal pose


class FakeRobot:
    """Holds a measured joint state. move_arm lands on the target plus a
    fixed realization error, like the real arm never lands exactly."""

    def __init__(self, realization_error=None):
        self.joints = {**ELEVATED, "wrist_roll": -97.36, "gripper": 79.8}
        self.base = dict(ZERO_BASE)
        self.realization_error = realization_error or {}
        self.events = []
        self.frame_value = 100
        self.fail_capture_at = None
        self.base_nonzero_after_move = None
        self.base_nonzero_during_capture = None
        self.base_reads_override = []  # scripted base readings, consumed first
        self.raise_in = {}  # method name -> call number that raises
        self.calls = {}
        self.abort_flag = {"set": False}
        self.abort_during_move = None
        self.drift_during_capture = 0.0
        self.captures = 0
        self.moves = 0
        self.clock = 0

    def _maybe_raise(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        if self.raise_in.get(name) == self.calls[name]:
            raise ConnectionError(f"{name}: host unreachable")

    def read_pose(self):
        self._maybe_raise("read_pose")
        self.events.append("read_pose")
        return dict(self.joints)

    def read_base(self):
        self.events.append("read_base")
        if self.base_reads_override:
            return dict(self.base_reads_override.pop(0))
        return dict(self.base)

    def move_arm(self, overrides, duration_s, should_abort):
        self._maybe_raise("move_arm")
        self.events.append(("move_arm", dict(overrides), duration_s))
        self.moves += 1
        if self.abort_during_move == self.moves:
            self.abort_flag["set"] = True  # the operator aborts while this segment is running
        for name, value in overrides.items():
            self.joints[name] = value + self.realization_error.get(name, 0.0)
        if self.base_nonzero_after_move == self.moves:
            self.base = {**ZERO_BASE, "x.vel": 0.01}
        return dict(self.joints)

    def capture(self, role):
        self.events.append(("capture", role))
        self._maybe_raise("capture")
        self.captures += 1
        if self.base_nonzero_during_capture == self.captures:
            self.base = {**ZERO_BASE, "x.vel": 0.05}
        self.joints["wrist_flex"] += self.drift_during_capture
        record = LifecycleRecord(role=role, identity_fingerprint="0123456789ab", settle_frames_requested=6, open_ok=True, close_ok=True)
        if self.fail_capture_at == self.captures:
            record.error = "OSError: device vanished"
            return CaptureResult(frame=None, record=record)
        record.frame_valid, record.frame_shape = True, [12, 16, 3]
        return CaptureResult(frame=np.full((12, 16, 3), self.frame_value, dtype=np.uint8), record=record)

    def now_utc(self):
        self.clock += 1
        return f"2026-09-19T10:{self.clock // 60:02d}:{self.clock % 60:02d}.000Z"


class ScanWorldTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.robot = FakeRobot()
        self.store = ObservationStore(self._tmp.name, session_id="scan-test")
        self.sleeps = []

    def deps(self, should_abort=lambda: False):
        return ScanWorldDeps(
            read_pose=self.robot.read_pose,
            read_base=self.robot.read_base,
            move_arm=self.robot.move_arm,
            capture=self.robot.capture,
            now_utc=self.robot.now_utc,
            sleep=self.sleeps.append,
            should_abort=should_abort,
        )

    def run_scan(self, stage=1, approved=True, approved_start=None, **kwargs):
        """The real flow: plan-only first (what the operator is shown), then
        execute with the digest and start pose of that plan."""
        should_abort = kwargs.pop("should_abort", lambda: self.robot.abort_flag["set"])
        common = dict(stage=stage, store=self.store, calibration=CalibrationInventory.empty(), scan_id="scan-test", **kwargs)
        plan = run_scan_world(self.deps(should_abort), **common)
        self.robot.events.clear()
        return run_scan_world(
            self.deps(should_abort),
            execute=True,
            approved_plan_digest=plan.plan_digest if approved is True else approved,
            approved_start_pose=plan.start_pose if approved_start is None else approved_start,
            **common,
        )

    def moves(self):
        return [e for e in self.robot.events if isinstance(e, tuple) and e[0] == "move_arm"]


class PlanOnlyTest(ScanWorldTestCase):
    def test_default_is_plan_only_with_no_motion_and_no_capture(self):
        result = run_scan_world(self.deps(), stage=1, store=self.store, calibration=CalibrationInventory.empty(), scan_id="scan-test")
        self.assertFalse(result.executed)
        self.assertEqual([], self.moves())
        self.assertEqual(0, self.robot.captures)
        self.assertEqual(["ENTRY_RISE", "ENTRY_PAN", "SCAN", "SCAN"], [s.kind for s in result.segments])
        self.assertEqual([], self.store.load_all())

    def test_plan_reports_the_start_pose_and_the_digest_that_binds_it(self):
        result = run_scan_world(self.deps(), stage=1, store=self.store, calibration=CalibrationInventory.empty(), scan_id="scan-test")
        self.assertEqual(ELEVATED, result.start_pose)
        self.assertEqual(scan_plan_digest(generate_scan_poses(stage=1), ELEVATED, allow_low_entry=False), result.plan_digest)


class ApprovalInterlockTest(ScanWorldTestCase):
    def test_execute_without_an_approved_digest_is_refused_before_any_motion(self):
        with self.assertRaises(ScanRefused):
            self.run_scan(approved=None)
        self.assertEqual([], self.moves())

    def test_execute_with_a_digest_for_a_different_stage_is_refused(self):
        other = scan_plan_digest(generate_scan_poses(stage=1), ELEVATED, allow_low_entry=False)
        with self.assertRaises(ScanRefused):
            self.run_scan(stage=2, approved=other)
        self.assertEqual([], self.moves())

    def test_approval_does_not_carry_over_when_the_arm_moved_since_the_plan(self):
        shown = {**ELEVATED, "shoulder_lift": ELEVATED["shoulder_lift"] + START_POSE_TOLERANCE_DEG + 1.0}
        digest = scan_plan_digest(generate_scan_poses(stage=1), shown, allow_low_entry=False)
        with self.assertRaises(ScanRefused) as caught:
            self.run_scan(approved=digest, approved_start=shown)
        self.assertIn("re-plan", str(caught.exception))
        self.assertEqual([], self.moves())

    def test_start_pose_that_does_not_match_the_digest_is_refused(self):
        with self.assertRaises(ScanRefused):
            self.run_scan(approved_start={**ELEVATED, "shoulder_pan": -12.0})
        self.assertEqual([], self.moves())

    def test_nonzero_base_at_preflight_is_refused_before_any_motion(self):
        # 3 deg/s: a real rotation. The former 0.5 deg/s was below ONE velocity count of this base (0.586 deg/s),
        # which the real host reports while the base stands still with torque off (recorded 2026-09-19).
        self.robot.base = {**ZERO_BASE, "theta.vel": 3.0}
        with self.assertRaises(ScanRefused):
            self.run_scan()
        self.assertEqual([], self.moves())

    def test_unplannable_start_pose_is_refused_before_any_motion(self):
        self.robot.joints["shoulder_pan"] = 120.0
        with self.assertRaises(ScanRefused):
            self.run_scan()
        self.assertEqual([], self.moves())

    def test_low_start_pose_is_refused_unless_low_entry_is_part_of_the_approved_plan(self):
        self.robot.joints.update(KNOWN_GOOD_GRASP_POSE_DEG)
        with self.assertRaises(ScanRefused) as caught:
            self.run_scan()
        self.assertIn("low", str(caught.exception))
        self.assertEqual([], self.moves())

    def test_approved_low_entry_reports_its_sweep_and_runs(self):
        self.robot.joints.update(KNOWN_GOOD_GRASP_POSE_DEG)
        result = self.run_scan(allow_low_entry=True, gripper_state="open", cube_held="no")
        self.assertGreater(result.segments[0].outward_sweep_while_low_m, 0.08)
        self.assertEqual(3, len(result.observation_ids))

    def test_low_entry_is_never_allowed_while_holding_the_cube(self):
        self.robot.joints.update(KNOWN_GOOD_GRASP_POSE_DEG)
        with self.assertRaises(ScanRefused):
            self.run_scan(allow_low_entry=True, gripper_state="holding", cube_held="yes")
        self.assertEqual([], self.moves())

    def test_clock_that_does_not_give_utc_time_is_refused_before_any_motion(self):
        self.robot.now_utc = lambda: "2026-09-19 12:00:01"  # naive local time: observation ids would be wrong or refused
        with self.assertRaises(ScanRefused) as caught:
            self.run_scan()
        self.assertIn("UTC", str(caught.exception))
        self.assertEqual([], self.moves())

    def test_low_entry_permission_is_bound_into_the_digest(self):
        self.robot.joints.update(KNOWN_GOOD_GRASP_POSE_DEG)
        start = {name: self.robot.joints[name] for name in SCAN_JOINTS}
        not_low = scan_plan_digest(generate_scan_poses(stage=1), start, allow_low_entry=False)
        with self.assertRaises(ScanRefused):
            self.run_scan(allow_low_entry=True, approved=not_low, gripper_state="open", cube_held="no")
        self.assertEqual([], self.moves())


class ExecutedScanTest(ScanWorldTestCase):
    def test_every_pose_yields_one_stored_valid_observation(self):
        result = self.run_scan(stage=2)
        self.assertTrue(result.executed)
        self.assertIsNone(result.abort_reason)
        records = self.store.load_all()
        self.assertEqual(9, len(records))
        self.assertEqual([p.pose_id for p in generate_scan_poses(stage=2)], [r["scan"]["pose_id"] for r in records])
        self.assertEqual(9, len({r["observation_id"] for r in records}))

    def test_stored_joints_are_the_measured_ones_not_the_commanded_target(self):
        self.robot.realization_error = {"wrist_flex": 0.7, "shoulder_lift": -0.4}
        self.run_scan()
        first = self.store.load_all()[0]
        target = generate_scan_poses(stage=1)[0].joints
        measured = first["robot_state"]["measured_joints_deg"]
        self.assertAlmostEqual(target["wrist_flex"] + 0.7, measured["wrist_flex"])
        self.assertAlmostEqual(target["shoulder_lift"] - 0.4, measured["shoulder_lift"])
        self.assertEqual(-97.36, measured["wrist_roll"])
        self.assertEqual("MEASURED_PRESENT_POSITION", first["robot_state"]["source"])
        self.assertAlmostEqual(sum(measured[j] for j in ("shoulder_lift", "elbow_flex", "wrist_flex")), first["scan"]["nominal_tool_depression_deg"])

    def test_pose_is_read_fresh_immediately_before_and_after_each_capture(self):
        self.run_scan()
        events = self.robot.events
        for index, event in enumerate(events):
            if event == ("capture", "wrist"):
                self.assertEqual("read_pose", events[index - 1])
                self.assertEqual("read_pose", events[index + 1])

    def test_arm_settles_before_each_capture(self):
        self.run_scan(settle_s=0.8)
        self.assertEqual(3, self.sleeps.count(0.8))

    def test_arm_motion_during_a_capture_is_flagged_in_the_record(self):
        self.robot.drift_during_capture = 1.5
        self.run_scan()
        self.assertEqual("MOVED_DURING_CAPTURE", self.store.load_all()[0]["robot_state"]["state_image_association"])

    def test_move_commands_name_only_the_four_scan_joints(self):
        self.run_scan(stage=3)
        self.assertEqual(16, len(self.moves()))
        for _, overrides, duration_s in self.moves():
            self.assertEqual(set(SCAN_JOINTS), set(overrides))
            self.assertGreaterEqual(duration_s, 1.0)

    def test_coverage_reports_what_was_and_was_not_observed(self):
        result = self.run_scan()
        self.assertEqual(["front"], [s["name"] for s in result.coverage["observed_sectors"]])
        self.assertEqual({"FAR": True, "MID": True, "NEAR": True}, result.coverage["observed_sectors"][0]["bands"])
        self.assertIn("rear-right", [s["name"] for s in result.coverage["unexplored_sectors"]])
        self.assertEqual("NOMINAL_TOOL_AXIS_UNCALIBRATED", result.coverage["coverage_basis"])

    def test_without_calibration_no_record_carries_a_metric_pose(self):
        self.run_scan()
        for record in self.store.load_all():
            self.assertIsNone(record["camera_pose"]["T_base_camera"])
            self.assertEqual("UNAVAILABLE", record["camera_pose"]["status"])

    def test_failed_capture_is_kept_as_evidence_and_the_scan_continues(self):
        self.robot.fail_capture_at = 2
        result = self.run_scan()
        records = self.store.load_all()
        self.assertEqual(3, len(records))
        self.assertIsNone(records[1]["raw_frame_path"])
        self.assertIn("device vanished", records[1]["lifecycle"]["error"])
        self.assertEqual({"FAR": True, "MID": False, "NEAR": True}, result.coverage["observed_sectors"][0]["bands"])

    def test_dark_frame_is_stored_but_does_not_count_as_observed(self):
        self.robot.frame_value = 0
        result = self.run_scan()
        self.assertEqual(3, len(self.store.load_all()))
        self.assertEqual([], result.coverage["observed_sectors"])

    def test_front_camera_view_can_be_appended_with_the_same_record_type(self):
        self.run_scan(include_front=True)
        records = self.store.load_all()
        front = [r for r in records if r["camera"]["role"] == "front"]
        self.assertEqual(1, len(front))
        self.assertIsNone(front[0]["scan"])
        self.assertEqual(("capture", "front"), [e for e in self.robot.events if isinstance(e, tuple) and e[0] == "capture"][-1])

    def test_gripper_and_cube_state_and_annotation_are_recorded(self):
        self.run_scan(gripper_state="holding", cube_held="yes", atlas_annotation={"object": "yellow_cube", "range_m": 0.5, "lighting": "daylight"})
        record = self.store.load_all()[0]
        self.assertEqual(("holding", "yes"), (record["gripper_state"], record["cube_held"]))
        self.assertEqual(0.5, record["atlas_annotation"]["range_m"])


class BaseZeroPredicateTest(unittest.TestCase):
    """The rest-state dither recorded on the real base is zero; anything like a commanded base motion is not."""

    def test_one_velocity_count_on_one_unpowered_wheel_reads_as_zero(self):
        from scan_world import _reads_zero

        side_wheel = {"x.vel": -0.0022141105520437233, "y.vel": 0.0012783173232380342, "theta.vel": 0.5859375}  # recorded 2026-09-19, twice
        mirrored = {key: -value for key, value in side_wheel.items()}
        for rest in ({"x.vel": 0.0, "y.vel": 0.0026, "theta.vel": -0.5859}, {"x.vel": -0.0, "y.vel": -0.0026, "theta.vel": 0.5859}, {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}, side_wheel, mirrored):
            self.assertTrue(_reads_zero(rest), rest)

    def test_two_velocity_counts_on_one_wheel_is_not_zero(self):
        from scan_world import _reads_zero

        for two_counts in ({"x.vel": 0.0, "y.vel": -0.0052, "theta.vel": 1.1718}, {"x.vel": -0.0044, "y.vel": 0.0026, "theta.vel": 1.1718},
                           {"x.vel": 0.0044, "y.vel": 0.0026, "theta.vel": 1.1718}, {"x.vel": 0.0, "y.vel": 0.0052, "theta.vel": -1.1718}):
            self.assertFalse(_reads_zero(two_counts), two_counts)

    def test_one_velocity_count_on_two_wheels_at_once_is_zero(self):
        """Operator ruling 2026-09-27 (docs/autonomous-demo/decision-log.md): run console-20260927T142146Z-m6ed2dd9 stopped
        holding the block because the left and the right wheel read one count each at the same instant. This test
        formerly pinned such reads (and two reads that are not whole counts at all) as NOT zero; one count is judged per
        wheel now, which is the 2026-09-19 decision applied where the count is."""
        from scan_world import _reads_zero

        for rest in ({"x.vel": -6.984728289775616e-19, "y.vel": 0.0025566346464760688, "theta.vel": 1.171875},  # the read that stopped the run
                     {"x.vel": -0.004428221104087446, "y.vel": -2.1815971307766465e-19, "theta.vel": -6.047127573464964e-19},  # recorded twice
                     {"x.vel": 0.002214110552043723, "y.vel": 0.003834951969714104, "theta.vel": 9.999704655308141e-17},  # recorded once
                     {"x.vel": 0.0044, "y.vel": 0.0, "theta.vel": 0.0},  # formerly pinned as not zero: left and right wheel, opposite signs
                     {"x.vel": 0.0, "y.vel": 0.0052, "theta.vel": 0.0}, {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": -1.1718}):  # formerly pinned; below 1.4 counts on every wheel
            self.assertTrue(_reads_zero(rest), rest)

    def test_a_moving_base_is_still_refused_on_every_axis(self):
        from scan_world import _reads_zero

        for moving in ({"x.vel": 0.02, "y.vel": 0.0, "theta.vel": 0.0}, {"x.vel": 0.0, "y.vel": -0.02, "theta.vel": 0.0}, {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 3.0}):
            self.assertFalse(_reads_zero(moving), moving)


class PoseSubsetTest(ScanWorldTestCase):
    """First physical runs: one operator approval covers exactly one band."""

    def test_a_pose_subset_runs_only_that_pose_and_its_digest_does_not_cover_the_stage(self):
        far = generate_scan_poses(stage=1)[0].pose_id
        result = self.run_scan(stage=1, pose_ids=[far])
        self.assertIsNone(result.abort_reason)
        self.assertEqual([far], [r["scan"]["pose_id"] for r in self.store.load_all()])
        whole = run_scan_world(self.deps(), stage=1, store=self.store, calibration=CalibrationInventory.empty(), scan_id="scan-test")
        self.assertNotEqual(whole.plan_digest, result.plan_digest)

    def test_a_custom_pose_is_planned_validated_and_approved_like_a_stage_pose(self):
        import dataclasses

        custom = dataclasses.replace(generate_scan_poses(stage=1)[1], pose_id="elevated-1")  # the band stays a known coverage class
        result = self.run_scan(stage=1, custom_poses=[custom])
        self.assertIsNone(result.abort_reason)
        self.assertEqual(["elevated-1"], [r["scan"]["pose_id"] for r in self.store.load_all()])
        unsafe = dataclasses.replace(custom, joints={**custom.joints, "elbow_flex": 2.0})
        with self.assertRaises(ScanRefused):
            run_scan_world(self.deps(), stage=1, store=self.store, calibration=CalibrationInventory.empty(), scan_id="scan-test", custom_poses=[unsafe])

    def test_an_unknown_pose_selection_is_refused_before_any_motion(self):
        with self.assertRaises(ScanRefused):
            run_scan_world(self.deps(), stage=1, store=self.store, calibration=CalibrationInventory.empty(), scan_id="scan-test", pose_ids=["s1-NOPE-p-010"])
        self.assertEqual([], self.moves())


class AbortTest(ScanWorldTestCase):
    def test_base_motion_detected_mid_scan_stops_all_further_motion(self):
        self.robot.base_nonzero_after_move = 2
        result = self.run_scan(stage=2)
        self.assertEqual(2, len(self.moves()))
        self.assertIn("base", result.abort_reason)
        self.assertEqual(0, self.robot.captures)

    def test_base_motion_that_starts_during_a_capture_stops_before_the_next_segment(self):
        self.robot.base_nonzero_during_capture = 1
        result = self.run_scan(stage=2)
        self.assertEqual(2, len(self.moves()))  # rise + first pan only: nothing after the capture
        self.assertIn("base", result.abort_reason)
        record = self.store.load_all()[0]
        self.assertEqual(0.05, record["robot_state"]["base_velocity"]["x.vel"])  # kept as evidence
        self.assertEqual([], result.coverage["observed_sectors"])  # a moving-base view is not coverage

    def test_base_is_confirmed_zero_immediately_before_every_segment(self):
        self.run_scan()
        events = self.robot.events
        for index, event in enumerate(events):
            if isinstance(event, tuple) and event[0] == "move_arm":
                self.assertEqual("read_base", events[index - 1], msg=f"segment at event {index}")

    def test_base_zero_reads_are_spaced_so_they_are_independent_samples(self):
        self.run_scan()
        self.assertGreaterEqual(self.sleeps.count(BASE_ZERO_POLL_S), 2)

    def test_transient_base_blip_must_settle_before_the_scan_starts(self):
        blip = {**ZERO_BASE, "y.vel": 0.02}
        self.robot.base_reads_override = [dict(ZERO_BASE), blip, dict(ZERO_BASE), dict(ZERO_BASE), dict(ZERO_BASE)]
        result = self.run_scan()
        self.assertIsNone(result.abort_reason)

    def test_the_dither_that_stopped_run_m6ed2dd9_passes_the_guard(self):
        # The recorded evaluation: six reads, the last one (1, 0, 1) counts, at least one more of them out of the former
        # band; this is one of the 24 patterns consistent with it. The former guard never saw three in-band reads in a row.
        trip = {"x.vel": -6.984728289775616e-19, "y.vel": 0.0025566346464760688, "theta.vel": 1.171875}
        self.robot.base_reads_override = [dict(ZERO_BASE), dict(ZERO_BASE), trip, dict(ZERO_BASE), dict(ZERO_BASE), trip]
        result = self.run_scan()
        self.assertIsNone(result.abort_reason)

    def test_a_base_still_moving_at_the_pulse_speed_never_confirms_zero(self):
        pulse = {**ZERO_BASE, "x.vel": 0.05}
        self.robot.base_reads_override = [dict(pulse)] * 6
        with self.assertRaises(ScanRefused):
            self.run_scan()
        self.assertEqual([], self.moves())

    def test_a_decelerating_base_is_waited_for_and_then_confirmed(self):
        import base_rest

        tail = [dict(zip(("x.vel", "y.vel", "theta.vel"), base_rest.body_velocity((-n, 0, n)))) for n in (4, 2)]
        self.robot.base_reads_override = tail + [dict(ZERO_BASE), {"x.vel": -0.0022141105520437233, "y.vel": 0.0012783173232380342, "theta.vel": 0.5859375}, dict(ZERO_BASE)]
        result = self.run_scan()
        self.assertIsNone(result.abort_reason)

    def test_base_that_never_settles_is_refused(self):
        blip = {**ZERO_BASE, "y.vel": 0.02}
        self.robot.base_reads_override = [dict(ZERO_BASE), blip] * 6
        with self.assertRaises(ScanRefused):
            self.run_scan()
        self.assertEqual([], self.moves())

    def test_pose_not_reached_within_tolerance_stops_without_capturing_there(self):
        self.robot.realization_error = {"elbow_flex": ARRIVAL_TOLERANCE_DEG + 1.0}
        result = self.run_scan()
        self.assertEqual(1, len(self.moves()))
        self.assertIn("elbow_flex", result.abort_reason)
        self.assertEqual(0, self.robot.captures)

    def test_measured_pose_below_the_scan_height_stops_the_scan(self):
        with mock.patch.object(scan_world, "SCAN_MIN_LINK_HEIGHT_M", 0.19):  # FAR measures 0.200 m, MID 0.168 m
            result = self.run_scan()
        self.assertIn("height", result.abort_reason)
        self.assertEqual(3, len(self.moves()))
        self.assertEqual(1, self.robot.captures)  # FAR was observed; nothing is captured at the low pose

    def test_operator_abort_is_checked_before_every_segment(self):
        calls = {"n": 0}

        def abort_on_third_check():
            calls["n"] += 1
            return calls["n"] >= 3

        # Polled before and after every segment: checks 1 and 2 bracket the
        # first segment, check 3 is the one before the second segment.
        result = self.run_scan(stage=2, should_abort=abort_on_third_check)
        self.assertEqual(1, len(self.moves()))
        self.assertIn("operator", result.abort_reason)

    def test_operator_abort_during_a_segment_ends_the_scan_without_observing(self):
        self.robot.abort_during_move = 2
        result = self.run_scan()
        self.assertEqual(2, len(self.moves()))
        self.assertIn("operator", result.abort_reason)
        self.assertEqual(0, self.robot.captures)

    def test_segments_receive_the_abort_channel(self):
        seen = []
        original = self.robot.move_arm
        self.robot.move_arm = lambda overrides, duration_s, should_abort: (seen.append(callable(should_abort)), original(overrides, duration_s, should_abort))[1]
        self.run_scan()
        self.assertEqual([True] * 4, seen)

    def test_aborted_scan_keeps_the_observations_already_made(self):
        self.robot.base_nonzero_after_move = 3
        result = self.run_scan()
        self.assertEqual(1, len(self.store.load_all()))
        self.assertEqual(1, len(result.observation_ids))

    def test_exception_after_motion_started_becomes_an_abort_not_a_crash(self):
        for method, call in (("move_arm", 3), ("capture", 2), ("read_pose", 6)):
            with self.subTest(method=method):
                self.setUp()
                self.robot.raise_in = {method: call}
                result = self.run_scan()
                self.assertIn("host unreachable", result.abort_reason)
                self.assertTrue(result.executed)
                self.assertIsNotNone(result.coverage)
                moves_at_failure = len(self.moves())
                self.assertLessEqual(moves_at_failure, 3)
                self.assertGreaterEqual(len(result.observation_ids), 1)


class LowerFloorForPregraspTest(ScanWorldTestCase):
    """PREGRASP_0 reuses this executor with a lower, explicitly passed and digest-bound link-height floor."""

    LOW_SCAN = {"shoulder_pan": -9.32, "shoulder_lift": -79.25, "elbow_flex": 34.68, "wrist_flex": 68.26}
    PREGRASP = {"shoulder_pan": -15.4, "shoulder_lift": 6.996, "elbow_flex": 49.59, "wrist_flex": 7.107}
    FLOOR = -0.060

    def setUp(self):
        super().setUp()
        import dataclasses

        self.robot.joints.update(self.LOW_SCAN)
        template = generate_scan_poses(stage=1)[0]
        self.poses = [dataclasses.replace(template, pose_id="pregrasp-0-pan", joints={**self.LOW_SCAN, "shoulder_pan": -15.4}),
                      dataclasses.replace(template, pose_id="pregrasp-0", joints=dict(self.PREGRASP))]

    def test_the_default_floor_refuses_the_pregrasp_before_any_motion(self):
        with self.assertRaises(ScanRefused):
            self.run_scan(custom_poses=self.poses)
        self.assertEqual([], self.moves())

    def test_with_the_lower_floor_it_pans_high_descends_in_plane_and_observes_at_both_poses(self):
        result = self.run_scan(custom_poses=self.poses, min_link_height_m=self.FLOOR)
        self.assertIsNone(result.abort_reason)
        targets = [m[1] for m in self.moves()]
        self.assertEqual([-9.32, -15.4, -15.4], [t["shoulder_pan"] for t in targets])
        self.assertEqual(self.LOW_SCAN["shoulder_lift"], targets[1]["shoulder_lift"])  # the pan move happens at scan height
        self.assertEqual(self.PREGRASP, targets[2])
        self.assertEqual(["pregrasp-0-pan", "pregrasp-0"], [r["scan"]["pose_id"] for r in self.store.load_all()])

    def test_a_bounded_pan_correction_at_pregrasp_height_runs_only_with_the_allowance_and_observes(self):
        import dataclasses

        measured = {"shoulder_pan": -14.769, "shoulder_lift": 7.429, "elbow_flex": 49.451, "wrist_flex": 8.044}
        self.robot.joints.update(measured)
        pose = dataclasses.replace(self.poses[1], pose_id="pregrasp-correct-pan-10.5", joints={**measured, "shoulder_pan": -10.549})
        common = dict(custom_poses=[pose], min_link_height_m=self.FLOOR, allow_low_entry=True, gripper_state="open", cube_held="no")
        with self.assertRaises(ScanRefused):
            self.run_scan(**common)
        result = self.run_scan(low_pan_allowance_deg=6.0, **common)
        self.assertIsNone(result.abort_reason)
        self.assertEqual([measured, {**measured, "shoulder_pan": -10.549}], [m[1] for m in self.moves()])
        self.assertEqual(["pregrasp-correct-pan-10.5"], [r["scan"]["pose_id"] for r in self.store.load_all()])

    def test_a_low_move_while_holding_is_refused_unless_it_is_declared_a_manipulation_step(self):
        import dataclasses

        held = {"shoulder_pan": -10.9, "shoulder_lift": 40.0, "elbow_flex": 45.0, "wrist_flex": -22.0}  # at the grasp, cube between the jaws
        self.robot.joints.update(held)
        lift = dataclasses.replace(self.poses[1], pose_id="lift", joints={**held, "shoulder_lift": 36.0, "wrist_flex": -19.0})
        common = dict(custom_poses=[lift], min_link_height_m=-0.116, allow_low_entry=True, gripper_state="holding", cube_held="yes")
        with self.assertRaises(ScanRefused):
            self.run_scan(**common)
        result = self.run_scan(manipulation_step=True, **common)
        self.assertIsNone(result.abort_reason)
        self.assertEqual("holding", self.store.load_all()[-1]["robot_state"]["gripper_state"] if "gripper_state" in self.store.load_all()[-1].get("robot_state", {}) else "holding")

    def test_an_approval_given_for_one_floor_does_not_carry_over_to_another(self):
        common = dict(stage=1, store=self.store, calibration=CalibrationInventory.empty(), scan_id="scan-test", custom_poses=self.poses)
        plan = run_scan_world(self.deps(), min_link_height_m=self.FLOOR, **common)
        with self.assertRaises(ScanRefused):
            run_scan_world(self.deps(), execute=True, approved_plan_digest=plan.plan_digest, approved_start_pose=plan.start_pose, min_link_height_m=-0.090, **common)
        self.assertEqual([], self.moves())

    def test_with_a_passed_floor_the_height_is_checked_again_on_the_settled_pose_before_observing(self):
        reads = {"n": 0}
        read_pose = self.robot.read_pose

        def sagging_after_the_move():  # on target right after the last waypoint, then the arm settles lower
            pose = read_pose()
            if self.robot.moves == 3:
                reads["n"] += 1
                if reads["n"] >= 1:
                    pose.update(shoulder_lift=pose["shoulder_lift"] + 4.9, elbow_flex=pose["elbow_flex"] + 4.9, wrist_flex=pose["wrist_flex"] + 4.9)
            return pose

        self.robot.read_pose = sagging_after_the_move
        result = self.run_scan(custom_poses=self.poses, min_link_height_m=-0.047)
        self.assertIn("height", result.abort_reason)
        self.assertEqual(["pregrasp-0-pan"], [r["scan"]["pose_id"] for r in self.store.load_all()])

    def test_a_measured_pose_below_the_passed_floor_stops_all_motion(self):
        self.robot.realization_error = {"shoulder_lift": 4.9, "elbow_flex": 4.9, "wrist_flex": 4.9}  # inside the arrival tolerance, yet the tool ends 27 mm lower
        result = self.run_scan(custom_poses=self.poses, min_link_height_m=-0.047)
        self.assertIn("height", result.abort_reason)
        self.assertEqual(["pregrasp-0-pan"], [r["scan"]["pose_id"] for r in self.store.load_all()])  # nothing is captured below the floor


class NoBaseChannelTest(unittest.TestCase):
    def test_module_has_no_way_to_command_the_base(self):
        with open(os.path.join(ROOT, "autonomy", "perception", "scan_world.py")) as f:
            source = f.read()
        code = "\n".join(line.split("#")[0] for line in re.sub(r'"""(?:.|\n)*?"""', "", source).splitlines())
        for forbidden in ("send_action", "stable_motion_gateway", "lerobot_transport", "mobile_approach", "StableMotionGateway", "lerobot"):
            self.assertNotIn(forbidden, code, msg=forbidden)

    def test_deps_expose_no_base_command(self):
        fields = set(ScanWorldDeps.__dataclass_fields__)
        self.assertEqual({"read_pose", "read_base", "move_arm", "capture", "now_utc", "sleep", "should_abort", "log"}, fields)


if __name__ == "__main__":
    unittest.main()
