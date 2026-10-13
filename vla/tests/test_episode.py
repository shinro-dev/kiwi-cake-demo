# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import time
import unittest

import numpy as np

from vla.episode import Episode, Io
from vla.gates import ActionGate

ENV = {"joints": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"],
       "joint_min_deg": [-46, -82, -69, -46, -99.75, 63], "joint_max_deg": [20, 97, 90, 78, -93.3, 82.5],
       "floor_tool_z_m": -0.1345, "wrist_roll_deg": -96.6,
       "end_pose_min_deg": [-35.5, 64.4, -71.0, -14.8], "end_pose_max_deg": [11.6, 98.9, -15.3, 52.6]}
START = [-9.0, -79.5, 34.5, 67.3, -96.6, 79.6]


class FakeArm:
    """The arm reaches every command at once; z of the tool = lift / 1000 (a stand-in for FK). Closing jaws stop at
    `jaw_floor` when a block is between them (None: nothing between them, they reach the command)."""
    def __init__(self, pose, jaw_floor=None):
        self.pose = list(pose)
        self.sent = []
        self.jaw_floor = jaw_floor

    def send(self, cmd):
        self.sent.append(dict(cmd))
        self.pose = [cmd[k] for k in ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos",
                                      "arm_wrist_flex.pos", "arm_wrist_roll.pos", "arm_gripper.pos")]
        if self.jaw_floor is not None and self.pose[5] < self.jaw_floor:
            self.pose[5] = self.jaw_floor


def fk(j):
    m = np.eye(4)
    m[2, 3] = j["shoulder_lift"] / 1000.0
    return m


def held_rule(gripper):
    """Stand-in for the runtime's closed_on_something: a cube stalls the jaws above 66.8 + 0.73 (the 2026-10-09 margin)."""
    return gripper - 66.8 > 0.73


def runtime_lift(arm, dz_m):
    """Stand-in for the runtime's cartesian_step_pose: the tool dz_m straight up (z = lift / 1000), pan held."""
    return [arm[0], arm[1] + dz_m * 1000.0, arm[2], arm[3]]


def make(chunks, retention=0.8, stop_after=None, arm_start=START, jaw_floor=67.9):
    arm = FakeArm(arm_start, jaw_floor)
    events, clock = [], {"t": 0.0}
    calls = {"n": 0}

    def infer(frame, state):
        assert frame.shape == (640, 480, 3)
        calls["n"] += 1
        return chunks[min(calls["n"] - 1, len(chunks) - 1)]

    def sleep(s):
        clock["t"] += s

    io = Io(read_state=lambda: list(arm.pose), capture=lambda: np.zeros((720, 1280, 3), np.uint8), send=arm.send,
            infer=infer, fk=fk, retention=lambda a, b: retention, emit=events.append,
            stopped=lambda: stop_after is not None and calls["n"] >= stop_after, monotonic=lambda: clock["t"], sleep=sleep,
            lift_step=runtime_lift, closed_on_something=held_rule)
    gate = ActionGate(ENV, fk, gripper_latch=66.8, gripper_open=80.193, alpha=1.0, max_step_deg=50, debounce_ticks=2, min_latch_ticks=3)
    return Episode(io, gate, START, retention_min=0.5, horizon=10, end_region=(ENV["end_pose_min_deg"], ENV["end_pose_max_deg"])), arm, events


def chunk(lift, grip, n=10, elbow=34.5, wflex=67.3):
    return np.array([[-9.0, lift, elbow, wflex, -96.6, grip]] * n)


class EpisodeTest(unittest.TestCase):
    def test_pick_lift_release_return_is_done_with_object_secured(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-30, 70), chunk(-30, 79), chunk(-30, 79)]
        ep, arm, events = make(chunks, retention=0.8)
        out = ep.run()
        self.assertEqual(out["result"], "done", out)
        self.assertTrue(out["object_secured"])
        self.assertTrue(out["released"])
        phases = [e["phase"] for e in events]
        for p in ("moving_to_start", "at_start", "thinking", "moving", "latched", "object_secured", "released", "returning", "done"):
            self.assertIn(p, phases)
        self.assertAlmostEqual(arm.pose[1], START[1], places=3)  # returned to the start pose

    def test_a_grip_not_demonstrated_is_refused_with_the_existing_code_and_the_jaws_stay_closed(self):
        # the runtime's own refusal holds the gripper closed (policy_grasp._secure); a score below the threshold on a block
        # that IS held (2026-10-08, twice) must not drop it wherever the arm is
        chunks = [chunk(-60, 70), chunk(-30, 70), chunk(-30, 70)]
        ep, arm, events = make(chunks, retention=0.1)
        out = ep.run()
        self.assertEqual(out["result"], "refused")
        self.assertEqual(out["code"], "retention_not_demonstrated")
        self.assertFalse(out["object_secured"])
        self.assertEqual(arm.sent[-1]["arm_gripper.pos"], 66.8)

    def test_operator_stop_holds_the_present_pose_and_sends_nothing_after(self):
        ep, arm, events = make([chunk(-60, 79)] * 10, stop_after=2)
        out = ep.run()
        self.assertEqual(out["result"], "stopped")
        self.assertEqual(events[-1]["phase"], "stopped")
        self.assertEqual(arm.sent[-1], arm.sent[-2])  # the last command holds the reading; no policy target follows the stop

    def test_time_bound_refuses(self):
        ep, arm, events = make([chunk(-60, 79)] * 1000)
        ep.max_s = 5.0
        out = ep.run()
        self.assertEqual(out["code"], "vla_time_bound")

    def test_the_stage_time_bound_leaves_a_late_grasp_time_to_place(self):
        # operator ruling 2026-10-09: on the throttled Pi (about 12.5 s a chunk) a grasp latched at 284 s and the 300 s
        # bound ended the run before any place; the bound the runner uses (the default) is 600 s
        ep, arm, events = make([chunk(-60, 79)])
        self.assertEqual(600.0, ep.max_s)

    def test_start_pose_not_reached_refuses_before_any_policy_action(self):
        ep, arm, events = make([chunk(-60, 79)])
        ep.io.read_state = lambda: [30.0, 0.0, 0.0, 0.0, -96.6, 79.6]  # the arm never moves
        out = ep.run()
        self.assertEqual(out["code"], "vla_start_pose_not_reached")
        self.assertNotIn("thinking", [e["phase"] for e in events])

    def test_gate_refusal_ends_the_episode_with_its_code(self):
        ep, arm, events = make([np.array([[float("nan")] * 6] * 10)])
        out = ep.run()
        self.assertEqual(out["code"], "vla_action_not_finite")

    def test_stop_during_inference_ends_within_a_hold_tick(self):
        import threading
        release = threading.Event()
        ep, arm, events = make([chunk(-60, 79)])
        flag = {"stop": False}

        def slow_infer(frame, state):
            flag["stop"] = True  # the operator presses Stop while the policy thinks
            release.wait(5)
            return chunk(-60, 79)
        ep.io.infer = slow_infer
        ep.io.stopped = lambda: flag["stop"]
        ep.io.sleep = lambda s: None
        out = ep.run()
        release.set()
        self.assertEqual(out["result"], "stopped")
        self.assertNotIn("moving", [e["phase"] for e in events])

    def test_inference_beyond_the_timeout_refuses_and_holds(self):
        import threading
        release = threading.Event()
        ep, arm, events = make([chunk(-60, 79)])
        ep.infer_timeout_s = 0.3
        ep.io.monotonic = time.monotonic
        ep.io.sleep = time.sleep

        def stuck(frame, state):
            release.wait(5)
            return chunk(-60, 79)
        ep.io.infer = stuck
        out = ep.run()
        release.set()
        self.assertEqual(out["code"], "vla_inference_timeout")

    def test_held_block_settled_at_the_pad_is_released_by_rule(self):
        # the recordings never release: the policy carries the block to the pad and holds it there
        pad = dict(elbow=-40.0, wflex=20.0)
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(80, 70, **pad), chunk(80, 70, **pad), chunk(80, 70, **pad)]
        ep, arm, events = make(chunks, retention=0.8)
        out = ep.run()
        self.assertEqual(out["result"], "done", out)
        self.assertEqual(out["release_by"], "rule")
        self.assertTrue(out["object_secured"])
        released = [e for e in events if e["phase"] == "released"]
        self.assertEqual(released[0]["by"], "rule")
        opened = [i for i, c in enumerate(arm.sent) if c["arm_gripper.pos"] == 80.193 and i > 0]
        self.assertTrue(opened)
        self.assertAlmostEqual(arm.pose[1], START[1], places=3)

    def test_no_release_by_rule_while_still_moving_at_the_pad(self):
        pad = dict(elbow=-40.0, wflex=20.0)
        chunks = [chunk(-60, 79), chunk(-60, 70)] + [chunk(70 if i % 2 else 90, 70, **pad) for i in range(400)]
        ep, arm, events = make(chunks, retention=0.8)
        ep.max_s = 30.0
        out = ep.run()
        self.assertNotEqual(out.get("release_by"), "rule")
        self.assertEqual(out["code"], "vla_time_bound")


class LaggingArm:
    """A slower arm: each command moves every joint at most `step` deg toward it, the jaws `grip_step` deg; closing jaws
    stop at `jaw_floor` (the block is between them). poses[i] is the reading right before sent[i] arrived."""
    def __init__(self, pose, step=4.0, grip_step=1.0, jaw_floor=72.0):
        self.pose, self.step, self.grip_step, self.jaw_floor = list(pose), step, grip_step, jaw_floor
        self.sent, self.poses = [], []

    def send(self, cmd):
        self.sent.append(dict(cmd))
        self.poses.append(list(self.pose))
        target = [cmd[k] for k in ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos",
                                   "arm_wrist_flex.pos", "arm_wrist_roll.pos", "arm_gripper.pos")]
        for i in range(6):
            lim = self.grip_step if i == 5 else self.step
            self.pose[i] += max(-lim, min(lim, target[i] - self.pose[i]))
        self.pose[5] = max(self.pose[5], self.jaw_floor) if target[5] < self.pose[5] else self.pose[5]


def make_lagging(chunks, retention=0.8, infer_s=0.0, max_step=50.0, horizon=50, **arm_kw):
    arm = LaggingArm(START, **arm_kw)
    events, clock, calls = [], {"t": 0.0}, {"n": 0}

    def infer(frame, state):
        calls["n"] += 1
        if infer_s:
            time.sleep(infer_s)
        return chunks[min(calls["n"] - 1, len(chunks) - 1)]

    def sleep(s):
        clock["t"] += s

    io = Io(read_state=lambda: list(arm.pose), capture=lambda: np.zeros((720, 1280, 3), np.uint8), send=arm.send,
            infer=infer, fk=fk, retention=lambda a, b: retention, emit=events.append, stopped=lambda: False,
            monotonic=lambda: clock["t"], sleep=sleep, lift_step=runtime_lift, closed_on_something=held_rule)
    gate = ActionGate(ENV, fk, gripper_latch=66.8, gripper_open=80.193, alpha=1.0, max_step_deg=max_step, debounce_ticks=2, min_latch_ticks=3)
    ep = Episode(io, gate, START, retention_min=0.5, horizon=horizon, end_region=(ENV["end_pose_min_deg"], ENV["end_pose_max_deg"]))
    return ep, arm, events


PAD = dict(elbow=-40.0, wflex=20.0)


def lagging_pick_chunks():
    return [chunk(-60, 79, n=50), chunk(-60, 70, n=50), chunk(-30, 70, n=50), chunk(80, 70, n=50, **PAD), chunk(80, 70, n=50, **PAD), chunk(80, 70, n=50, **PAD)]


class EpisodeOnALaggingArmTest(unittest.TestCase):
    def test_rule_release_holds_the_jaws_open_until_they_read_open_before_the_return(self):
        ep, arm, events = make_lagging(lagging_pick_chunks())
        out = ep.run()
        self.assertEqual(out["result"], "done", out)
        self.assertEqual(out["release_by"], "rule")
        grips = [c["arm_gripper.pos"] for c in arm.sent]
        first_open_after_latch = next(i for i in range(grips.index(66.8), len(grips)) if grips[i] == 80.193)
        self.assertTrue(all(g == 80.193 for g in grips[first_open_after_latch:]), grips[first_open_after_latch:first_open_after_latch + 20])
        release_lift = arm.sent[first_open_after_latch]["arm_shoulder_lift.pos"]
        leaves = next(i for i in range(first_open_after_latch, len(arm.sent)) if abs(arm.sent[i]["arm_shoulder_lift.pos"] - release_lift) > 1.0)
        self.assertGreaterEqual(arm.poses[leaves][5], 80.193 - 3.0)  # the return starts only once the jaws read open

    def test_the_gripper_is_only_ever_latch_or_open_and_the_roll_only_the_frozen_value(self):
        ep, arm, events = make_lagging(lagging_pick_chunks())
        ep.run()
        self.assertEqual({c["arm_gripper.pos"] for c in arm.sent}, {66.8, 80.193})
        self.assertEqual({c["arm_wrist_roll.pos"] for c in arm.sent}, {ENV["wrist_roll_deg"]})

    def test_a_stop_while_holding_the_block_keeps_the_squeeze(self):
        ep, arm, events = make_lagging(lagging_pick_chunks())
        calls = {"n": 0}
        inner = ep.io.infer

        def infer(frame, state):
            calls["n"] += 1
            return inner(frame, state)
        ep.io.infer = infer
        ep.io.stopped = lambda: calls["n"] >= 3  # after the close, while the block is held
        out = ep.run()
        self.assertEqual(out["result"], "stopped")
        self.assertEqual(arm.sent[-1]["arm_gripper.pos"], 66.8)

    def test_each_chunk_starts_from_the_pose_read_after_the_pause(self):
        # the arm catches up with its last command during the pause; the next chunk must start from where it IS
        chunks = [chunk(-79.5 + 10 * (i + 1) * 5, 79, n=10) for i in range(3)]
        ep, arm, events = make_lagging(chunks, infer_s=1.0, max_step=5.0, horizon=10, step=1.5)
        ep.max_s = 8.0
        firsts, mark = [], {"moving": False}
        emit, send = ep.io.emit, ep.io.send

        def watch_emit(event):
            mark["moving"] = mark["moving"] or event["phase"] == "moving"
            emit(event)

        def watch_send(cmd):
            if mark["moving"]:
                firsts.append(len(arm.sent))
                mark["moving"] = False
            send(cmd)
        ep.io.emit, ep.io.send = watch_emit, watch_send
        ep.run()
        self.assertGreaterEqual(len(firsts), 2)
        for i in firsts:
            self.assertLessEqual(abs(arm.sent[i]["arm_shoulder_lift.pos"] - arm.poses[i][1]), 5.0 + 1e-6, (i, arm.sent[i]["arm_shoulder_lift.pos"], arm.poses[i][1]))

class EpisodeGripTest(unittest.TestCase):
    def test_a_reopen_before_the_grip_is_confirmed_is_a_regrasp_never_a_release(self):
        reopen = np.array([[-9.0, -60, 34.5, 67.3, -96.6, 70.0]] * 5 + [[-9.0, -60, 34.5, 67.3, -96.6, 79.0]] * 5)
        chunks = [chunk(-60, 79), reopen, chunk(-60, 70), chunk(-30, 70), chunk(80, 70, **PAD), chunk(80, 70, **PAD), chunk(80, 70, **PAD)]
        ep, arm, events = make(chunks, retention=0.8)
        out = ep.run()
        phases = [e["phase"] for e in events]
        self.assertIn("reopened", phases)
        self.assertLess(phases.index("reopened"), phases.index("object_secured"))
        self.assertEqual(out["result"], "done", out)
        self.assertTrue(out["object_secured"])

    def test_at_rest_at_the_pad_without_a_measured_grip_is_refused_not_released(self):
        chunks = [chunk(-60, 79), chunk(80, 70, **PAD), chunk(80, 70, **PAD), chunk(80, 70, **PAD)]
        ep, arm, events = make(chunks, retention=0.8)
        ep.io.lift_step = None  # no runtime lift to measure the grip with
        out = ep.run()
        self.assertEqual(out["result"], "refused")
        self.assertEqual(out["code"], "vla_grip_not_verified")
        self.assertFalse(out["released"])
        self.assertEqual(arm.sent[-1]["arm_gripper.pos"], 66.8)  # the block stays held

    def test_a_runtime_that_turns_busy_mid_episode_stops_it_and_holds(self):
        ep, arm, events = make([chunk(-60, 79)] * 50)
        calls = {"n": 0}

        def still_allowed():
            calls["n"] += 1
            return (calls["n"] < 3, "a mission run is active")
        ep.io.still_allowed = still_allowed
        out = ep.run()
        self.assertEqual(out["code"], "vla_runtime_busy")
        self.assertIn("a mission run is active", out["message"])
        self.assertEqual(arm.sent[-1], arm.sent[-2])


class EpisodeGripCheckTest(unittest.TestCase):
    """OBJECT_SECURED the runtime's way (policy_grasp._secure): at the first chunk boundary with the jaws latched, three
    straight-up lifts solved by the runtime (+15, +28, +8 mm), the arm still before each frame, scored between the frames
    after the upper two. On the robot 2026-10-08 the first pair near the floor scored 0.468 and 0.465 on a HELD block."""

    def test_the_grip_is_measured_by_the_runtimes_three_lifts_and_scored_on_the_upper_two(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-30, 70), chunk(-30, 79), chunk(-30, 79)]
        ep, arm, events = make(chunks, retention=0.8)
        lifts, scored, kept = [], [], []

        def lift(a, dz):
            lifts.append(dz)
            return runtime_lift(a, dz)
        ep.io.lift_step = lift
        ep.io.capture = lambda: np.full((720, 1280, 3), int(round(arm.pose[1])) + 128, np.uint8)  # the frame says the lift it was taken at
        ep.io.retention = lambda a, b: scored.append((int(a[0, 0, 0]) - 128, int(b[0, 0, 0]) - 128)) or 0.8
        ep.io.keep = lambda name, frame: kept.append((name, int(frame[0, 0, 0]) - 128)) if name.startswith("grip-") else None  # the release frame is kept too
        out = ep.run()
        self.assertEqual(lifts, [0.015, 0.028, 0.008])
        self.assertEqual(scored, [(-17, -9)])  # latched at lift -60: +15 -> -45, +28 -> -17, +8 -> -9
        self.assertEqual(kept, [("grip-2", -17), ("grip-3", -9)])
        secured = next(e for e in events if e["phase"] == "object_secured")
        self.assertEqual(secured["tool_z_mm"], [-17.0, -9.0])
        self.assertTrue(out["object_secured"])
        self.assertEqual(out["result"], "done", out)
        self.assertTrue(all(c["arm_gripper.pos"] == 66.8 for c in arm.sent[len(arm.sent) - 1:]) or out["released"])

    def test_the_jaws_stay_latched_through_the_grip_check_lifts(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-30, 70), chunk(-30, 79)]
        ep, arm, events = make(chunks, retention=0.8)
        marks = {}
        emit = ep.io.emit

        def watch(event):
            marks.setdefault(event["phase"], len(arm.sent))
            emit(event)
        ep.io.emit = watch
        ep.run()
        between = arm.sent[marks["latched"]:marks["object_secured"]]
        self.assertTrue(between)
        self.assertEqual({c["arm_gripper.pos"] for c in between}, {66.8})

    def test_a_grip_check_lift_the_runtime_refuses_ends_the_episode_holding_the_block(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-30, 70)]
        ep, arm, events = make(chunks, retention=0.8)

        def refuse(a, dz):
            raise RuntimeError("the gate refuses this step: elbow beyond its limit")
        ep.io.lift_step = refuse
        out = ep.run()
        self.assertEqual(out["result"], "refused")
        self.assertEqual(out["code"], "vla_grip_check_lift_refused")
        self.assertIn("elbow beyond its limit", out["message"])
        self.assertEqual(arm.sent[-1]["arm_gripper.pos"], 66.8)

    def test_each_grip_frame_is_taken_with_the_arm_still(self):
        # a slow arm is still travelling when the eased move ends: the frame must wait until two readings agree
        ep, arm, events = make_lagging(lagging_pick_chunks(), step=0.4)
        at_frame = []
        ep.io.keep = lambda name, frame: at_frame.append((list(arm.pose), [arm.sent[-1][k] for k in ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos", "arm_wrist_flex.pos")])) if name.startswith("grip-") else None
        ep.run()
        self.assertEqual(len(at_frame), 2)
        for pose, commanded in at_frame:
            self.assertTrue(all(abs(p - c) <= 0.3 for p, c in zip(pose[:4], commanded)), (pose[:4], commanded))

    def test_a_refused_grip_on_a_slow_arm_leaves_the_jaws_on_the_block(self):
        ep, arm, events = make_lagging(lagging_pick_chunks(), retention=0.1)
        out = ep.run()
        self.assertEqual(out["code"], "retention_not_demonstrated")
        self.assertLessEqual(arm.pose[5], 73.0)  # still closed on the block (jaw_floor 72)


class EpisodeEmptyCloseTest(unittest.TestCase):
    """The runtime measures the grip in two halves (policy_grasp._close, then _secure): the gripper readback after the
    latch must say the jaws stalled on something (closed_on_something) before any lift or frame. On the robot 2026-10-09
    the retention score alone passed an EMPTY close (0.883, the jaws filling the box) and the run reported done."""

    def test_an_empty_close_opens_the_jaws_without_a_lift_and_is_never_a_secured_grip(self):
        chunks = [chunk(-60, 79), chunk(-60, 70)] + [chunk(-60, 70)] * 20
        ep, arm, events = make(chunks, retention=0.95, jaw_floor=None)  # nothing between the jaws; the image would pass
        lifts = []
        ep.io.lift_step = lambda a, dz: lifts.append(dz) or runtime_lift(a, dz)
        ep.max_s = 60.0
        out = ep.run()
        phases = [e["phase"] for e in events]
        self.assertIn("grip_empty", phases)
        self.assertNotIn("object_secured", phases)
        self.assertEqual(lifts, [])
        self.assertIsNone(out["object_secured"])
        self.assertFalse(out["released"])
        empty = next(e for e in events if e["phase"] == "grip_empty")
        self.assertAlmostEqual(empty["gripper"], 66.8, places=3)

    def test_after_an_empty_close_the_jaws_stay_open_while_the_policy_still_asks_closed(self):
        chunks = [chunk(-60, 79), chunk(-60, 70)] + [chunk(-60, 70)] * 20
        ep, arm, events = make(chunks, jaw_floor=None)
        marks = {}
        emit = ep.io.emit

        def watch(event):
            marks.setdefault(event["phase"], len(arm.sent))
            emit(event)
        ep.io.emit = watch
        ep.max_s = 60.0
        out = ep.run()
        after = arm.sent[marks["grip_empty"]:]
        self.assertTrue(after)
        self.assertEqual({c["arm_gripper.pos"] for c in after}, {80.193})
        self.assertEqual([e["phase"] for e in events].count("latched"), 1)
        self.assertEqual(out["code"], "vla_time_bound")

    def test_after_an_empty_close_the_policy_may_grasp_again_and_place(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-60, 79), chunk(-60, 70), chunk(-30, 70),
                  chunk(80, 70, **PAD), chunk(80, 70, **PAD), chunk(80, 70, **PAD)]
        ep, arm, events = make(chunks, retention=0.8, jaw_floor=None)
        emit = ep.io.emit

        def watch(event):
            if event["phase"] == "grip_empty":
                arm.jaw_floor = 67.9  # the block is between the jaws for the next close
            emit(event)
        ep.io.emit = watch
        out = ep.run()
        phases = [e["phase"] for e in events]
        self.assertEqual(phases.count("latched"), 2)
        self.assertLess(phases.index("grip_empty"), phases.index("object_secured"))
        self.assertEqual(out["result"], "done", out)
        self.assertTrue(out["object_secured"])
        self.assertTrue(out["released"])


class GoToTest(unittest.TestCase):
    def test_go_to_moves_the_arm_to_a_named_pose_with_the_jaws_open_and_the_roll_frozen(self):
        ep, arm, events = make_lagging([chunk(-60, 79)])
        mission = [-9.67, -79.253, 38.286, 67.209]
        out = ep.go_to(mission, "mission")
        self.assertEqual(out["result"], "done", out)
        self.assertTrue(all(abs(a - b) <= 3.0 for a, b in zip(arm.pose[:4], mission)))
        self.assertEqual({c["arm_gripper.pos"] for c in arm.sent}, {80.193})
        self.assertEqual({c["arm_wrist_roll.pos"] for c in arm.sent}, {ENV["wrist_roll_deg"]})
        self.assertEqual([e["phase"] for e in events][0], "moving_to_pose")

    def test_go_to_stops_on_the_operator_stop_and_holds(self):
        ep, arm, events = make_lagging([chunk(-60, 79)])
        ep.io.stopped = lambda: len(arm.sent) >= 5
        out = ep.go_to([-9.67, -50.0, 38.286, 67.209], "mission")
        self.assertEqual(out["result"], "stopped")


if __name__ == "__main__":
    unittest.main()


class EpisodeGripLostTest(unittest.TestCase):
    """2026-10-12 on the robot: the grip check passed with the block firmly between the jaws (both lift frames), the block
    was found at its start spot after a reported placement: it slipped out during the carry, and nothing looked at the
    jaws again. The jaw reading drops from the held class to the latch command when the block leaves."""

    def test_a_grip_lost_during_the_carry_is_said_and_the_jaws_reopen_for_another_try(self):
        chunks = [chunk(-60, 79), chunk(-60, 70)] + [chunk(-30, 70)] * 20
        ep, arm, events = make(chunks, jaw_floor=67.9)
        emit = ep.io.emit

        def watch(event):
            if event["phase"] == "object_secured":
                arm.jaw_floor = None  # the block leaves the jaws during the carry: they now reach the latch command
            emit(event)
        ep.io.emit = watch
        ep.max_s = 60.0
        out = ep.run()
        phases = [e["phase"] for e in events]
        self.assertIn("object_secured", phases)
        self.assertIn("grip_lost", phases)
        self.assertLess(phases.index("object_secured"), phases.index("grip_lost"))
        self.assertAlmostEqual(next(e for e in events if e["phase"] == "grip_lost")["gripper"], 66.8, places=3)
        self.assertAlmostEqual(next(e for e in events if e["phase"] == "object_secured")["gripper"], 67.9, places=3)
        self.assertNotIn("released", phases)
        self.assertFalse(out["released"])
        self.assertIsNone(out["object_secured"])

    def test_a_grip_that_holds_through_the_carry_is_never_said_lost(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-30, 70), chunk(80, 70, elbow=-40, wflex=20), chunk(80, 70, elbow=-40, wflex=20)]
        ep, arm, events = make(chunks, jaw_floor=67.9)
        ep.max_s = 60.0
        out = ep.run()
        phases = [e["phase"] for e in events]
        self.assertNotIn("grip_lost", phases)
        self.assertIn("released", phases)
        self.assertTrue(out["released"])


class EpisodeSetDownTest(unittest.TestCase):
    """The rule's release sets the block down with the runtime's own straight-down steps before the jaws open."""

    def test_the_rule_release_lowers_the_tool_before_opening(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-30, 70), chunk(80, 70, elbow=-40, wflex=20), chunk(80, 70, elbow=-40, wflex=20)]
        ep, arm, events = make(chunks, jaw_floor=67.9)
        steps = []
        ep.io.lift_step = lambda a, dz: steps.append(dz) or runtime_lift(a, dz)
        ep.max_s = 60.0
        out = ep.run()
        self.assertTrue(out["released"])
        self.assertEqual(out["release_by"], "rule")
        self.assertEqual([dz for dz in steps if dz < 0], [-0.008] * 5)
        phases = [e["phase"] for e in events]
        self.assertLess(phases.index("set_down"), phases.index("released"))
        self.assertEqual(next(e for e in events if e["phase"] == "set_down")["steps"], 5)

    def test_a_step_the_runtime_refuses_ends_the_set_down_and_the_jaws_still_open(self):
        chunks = [chunk(-60, 79), chunk(-60, 70), chunk(-30, 70), chunk(80, 70, elbow=-40, wflex=20), chunk(80, 70, elbow=-40, wflex=20)]
        ep, arm, events = make(chunks, jaw_floor=67.9)
        count = {"n": 0}

        def lift(a, dz):
            if dz < 0:
                count["n"] += 1
                if count["n"] > 2:
                    raise RuntimeError("the gate refuses this step: below the floor")
            return runtime_lift(a, dz)
        ep.io.lift_step = lift
        ep.max_s = 60.0
        out = ep.run()
        self.assertTrue(out["released"])
        down = next(e for e in events if e["phase"] == "set_down")
        self.assertEqual(down["steps"], 2)
        self.assertIn("below the floor", down["stopped_by"])

