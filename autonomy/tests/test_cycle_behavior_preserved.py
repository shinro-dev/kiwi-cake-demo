#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cycle_behavior_preserved.py: a DIFFERENTIAL test. The controller that physically passed is tagged
# single-cube-cycle-pass-2026-09-20. Its source (and the source of the scan CLI it imports) is read from that tag with
# `git show`, loaded next to the current code, and both are driven through the SAME scripted scenarios. What must be
# identical: everything printed to stdout, the state transition record, every payload handed to robot.send_action (order
# and content) and the way the run ends. Then the current code runs the scenarios once more with events ON, a runtime
# configuration, the preflight and the freshness client, and the same four things must STILL equal the baseline.
#
# Two levels. The scripted MACHINE of test_run_single_cube_cycle.py (run_pose / grip / observe replaced) covers the task
# logic and the base path. The scripted WORLD below replaces only the boundary: the robot client, the camera, the detector
# and the base primitive. main(), run_pose, grip, observe, base_pulse, state(), the planner, the executor, the bounded
# interpolation and the observation store all run for real, which is what makes the send_action comparison mean something.
# Time is virtual (it moves only when something sleeps), so a line such as "wall-clock 4.00 s" is a function of the sleeps
# the code asked for: an event hook that slept, or read the robot once more, would change the output and fail this test.
# No robot, no camera, no network, no lerobot, no model. Run directly: python3 autonomy/tests/test_cycle_behavior_preserved.py
import contextlib
import functools
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time as real_time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import numpy as np  # noqa: E402

import run_single_cube_cycle as current  # noqa: E402
import test_run_single_cube_cycle as fixtures  # noqa: E402  (the scripted machine, _pad_view, the synthetic runtime)
from camera_lifecycle import CaptureResult, LifecycleRecord  # noqa: E402
from observation_store import ObservationStore  # noqa: E402
from scan_arm_motion import make_bounded_move_arm  # noqa: E402

BASELINE_TAG = "single-cube-cycle-pass-2026-09-20"
CONTROLLER, SCAN_CLI = "bin/laptop/manipulation/run_single_cube_cycle.py", "bin/laptop/perception/run_stage1_scan.py"
STATE_ORDER = ("arm_shoulder_pan.pos", "arm_shoulder_lift.pos", "arm_elbow_flex.pos", "arm_wrist_flex.pos", "arm_wrist_roll.pos", "arm_gripper.pos", "x.vel", "y.vel", "theta.vel")
SCAN_ID = "synthetic-cycle"
EPOCH = 1789000000  # a fixed instant: every observation id and every record name of a scripted run is the same on every machine


def _git_show(path: str):
    result = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASELINE_TAG}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 and result.stdout else None


@functools.lru_cache(maxsize=None)
def load_baseline():
    """The tagged controller as a module of its own, importing the tagged scan CLI. None when the tag is not in this
    checkout (a shallow clone, a source archive). Everything BELOW those two files is shared with the current code; it is
    either unchanged since the tag or not reached by a scripted run (the perception tool is scripted away)."""
    sources = {path: _git_show(path) for path in (CONTROLLER, SCAN_CLI)}
    if not all(sources.values()):
        return None
    scan_cli = types.ModuleType("run_stage1_scan")
    scan_cli.__file__ = str(ROOT / SCAN_CLI)
    exec(compile(sources[SCAN_CLI], f"{BASELINE_TAG}:{SCAN_CLI}", "exec"), scan_cli.__dict__)
    controller = types.ModuleType("baseline_run_single_cube_cycle")
    controller.__file__ = str(ROOT / CONTROLLER)
    with mock.patch.dict(sys.modules, {"run_stage1_scan": scan_cli}):  # `import run_stage1_scan as cli` inside the tagged source finds the TAGGED one
        exec(compile(sources[CONTROLLER], f"{BASELINE_TAG}:{CONTROLLER}", "exec"), controller.__dict__)
    return controller


class VirtualTime:
    """Stands in for the `time` module inside a controller module. The clock moves ONLY when something sleeps, so durations
    that are printed are exact functions of the sleeps that were asked for, identical from run to run and from code to code."""

    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))

    def monotonic(self):
        return 1000.0 + self.now

    perf_counter = monotonic

    def monotonic_ns(self):
        return int((1000.0 + self.now) * 1e9)

    def gmtime(self, *_):
        return real_time.gmtime(EPOCH + int(self.now))

    def strftime(self, pattern, moment=None):
        return real_time.strftime(pattern, self.gmtime() if moment is None else moment)

    def now_utc(self):
        return self.strftime("%Y-%m-%dT%H:%M:%SZ")


class World:
    """ONE scripted scene. The arm realizes every command exactly; the gripper stalls where the script says a cube is; the
    base moves at the scripted rate; the camera sees what the script says from where the arm is. Everything the controller
    SENT is kept, in order."""

    def __init__(self, **script):
        # open_stalls_at: the jaws do NOT reach the open position on an opening command (something is caught between them),
        # so the next arm step measures a gripper outside its declared phase. cube_scale_per_held_capture: what the camera
        # sees between the jaws changes SIZE on every capture taken once the jaws closed on something, which is a cube that
        # did not rise with them. Both are off by default, so every scenario of the differential comparison is the scene it
        # always was, frame for frame.
        # base_reads (off by default, exact zero as always): the body velocities the host reports, one per message in turn, so a
        # run can be made to read the recorded at-rest dither of the real base on every read (autonomy/tests/test_base_rest_sites.py).
        self.script = {"cube_start_pad_m": [0.0145, -0.1825], "cube_final_pad_m": [0.0145, -0.1475 + 0.058], "latches": [68.4], "base_rate_mm_s": 90.0, "boxes": [[613, 196, 853, 438], [508, 174, 729, 416],
                       [470, 174, 691, 416], [505, 187, 789, 505], [411, 192, 732, 532]], "abort_at_send": None, "abort_in_pulse": False, "host_stamps": True,
                       "open_stalls_at": None, "cube_scale_per_held_capture": 1.0, "base_reads": None, **script}
        self.clock = VirtualTime()
        self.joints = dict(fixtures.SYNTHETIC_LOW_POSE)
        self.sent, self.base_pulses, self.base_mm, self.disconnects, self.host_seq, self.held_captures = [], [], 0.0, 0, 0, 0
        self.latches, self.boxes, self.closing, self.stall, self.released, self.was_held = list(self.script["latches"]), list(self.script["boxes"]), False, None, False, False

    # -- the robot
    def message(self) -> str:
        self.host_seq += 1
        body = {f"arm_{name}.pos": float(value) for name, value in self.joints.items()}
        base = self.script["base_reads"]  # None: exact zero, as always; else the (x, y, theta) reads the host reports, in turn
        body.update(dict(zip(("x.vel", "y.vel", "theta.vel"), base[(self.host_seq - 1) % len(base)] if base else (0.0, 0.0, 0.0))))
        if self.script["host_stamps"]:
            body.update({"kc.session": "synthetic-host-session", "kc.seq": self.host_seq, "kc.t_mono_ns": 5_000_000_000 + self.host_seq * 33_000_000})
        return json.dumps(body)

    def apply(self, action: dict) -> None:
        self.sent.append(dict(action))
        if self.script["abort_at_send"] == len(self.sent):
            signal.raise_signal(signal.SIGINT)  # the operator's Ctrl-C, delivered to the handler main() installed
        for key, value in action.items():
            if key.endswith(".pos") and key != "arm_gripper.pos":
                self.joints[key[len("arm_"):-len(".pos")]] = float(value)
        if "arm_gripper.pos" in action:
            target = float(action["arm_gripper.pos"])
            if target < self.joints["gripper"] - 1e-9 and not self.closing:  # a new closing begins: the script says where this one stalls
                self.closing, self.stall = True, self.latches.pop(0) if self.latches else 66.8
            elif target > self.joints["gripper"] + 1e-9 and self.closing:
                self.closing, self.stall, self.released = False, None, self.was_held
            opened = target if self.script["open_stalls_at"] is None else min(target, self.script["open_stalls_at"])
            self.joints["gripper"] = max(target, self.stall) if self.closing else opened
            self.was_held = self.was_held or (self.closing and self.joints["gripper"] > 67.4)

    # -- the base primitive
    def move_base(self, vx_mps, duration_s) -> bool:
        self.base_pulses.append((vx_mps, round(duration_s, 3)))
        self.clock.sleep(duration_s + 0.3)
        self.base_mm += self.script["base_rate_mm_s"] * duration_s * (1.0 if vx_mps > 0 else -1.0)
        if self.script["abort_in_pulse"]:
            signal.raise_signal(signal.SIGINT)
        return True

    # -- the camera and what it sees
    def frame(self):
        image = np.full((720, 1280, 3), 120, np.uint8)
        self.held_captures += self.was_held  # only what is seen AFTER the jaws closed on something can say whether it rose with them
        scale = self.script["cube_scale_per_held_capture"] ** self.held_captures  # 1.0 by default: rigid with the camera, so every pair of frames scores 1
        width, height = round(420 * scale), round(360 * scale)  # at scale 1 this is exactly [300:660, 380:800], the frame every other scenario sees
        x0, y0 = 590 - width // 2, 480 - height // 2
        image[y0:y0 + height, x0:x0 + width], image[y0:y0 + round(height * 2 / 9), x0:x0 + width] = 200, 90  # a cube with a darker far face between the jaws
        return image

    def perception(self) -> dict:
        cube = {"detected": True, "confidence": 0.9, "clipped": {"bottom": False, "left": False, "right": False, "top": False}}
        if self.joints["shoulder_lift"] < -60.0:  # the low scan pose: the pad print fixes PAD_FRAME, the cube is seen on the floor
            centre = self.script["cube_final_pad_m"] if self.released else self.script["cube_start_pad_m"]
            return {"camera": "wrist", "image_size_px": [1280, 720], "cube": {**cube, "bbox_xyxy": [515, 485, 590, 581], "contact_px": [553.0, 545.0]}, "pad": None, "focal_sweep": [],
                    "pad_by_template": fixtures._pad_view(advance_mm=self.base_mm), "cube_by_template": {"cube_center_pad_m": list(centre), "inside_pad_outline": abs(centre[0]) <= 0.2075 and abs(centre[1]) <= 0.1475}}
        return {"camera": "wrist", "image_size_px": [1280, 720], "cube": {**cube, "bbox_xyxy": self.boxes.pop(0) if self.boxes else [411, 192, 732, 532], "contact_px": None}, "pad": None, "focal_sweep": [], "pad_by_template": None}


class SyntheticCamera:
    def __init__(self, world: World):
        self.world, self.setup_timing, self.last_timing = world, {"ssh_ns": 400_000_000, "copy_ns": 900_000_000}, None

    def capture(self, role: str) -> CaptureResult:
        self.world.clock.sleep(2.0)  # a capture takes time: two observations never share one second, hence never one id
        self.last_timing = {"role": role, "capture_ns": 1_500_000_000, "transfer_ns": 500_000_000}
        return CaptureResult(frame=self.world.frame(), record=LifecycleRecord(role=role, identity_fingerprint="0123456789ab", settle_frames_requested=6, open_ok=True, open_latency_s=0.4, frames_read=7,
                                                                             frame_valid=True, frame_shape=[720, 1280, 3], close_ok=True, total_latency_s=1.2))


def synthetic_lerobot(world: World) -> dict:
    """sys.modules entries that answer `from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig`. The client has the
    read path of the stock one as far as a caller can see it: get_observation() reaches _poll_and_get_latest_message(), adopts
    the message into a NEW last_remote_state built from the state keys only (so the kc.* keys are dropped), and returns it."""
    class LeKiwiClientConfig:
        def __init__(self, remote_ip, id, **options):  # noqa: A002 -- the stock keyword
            self.remote_ip, self.id, self.options = remote_ip, id, options

    class LeKiwiClient:
        def __init__(self, config):
            self.config, self.last_remote_state, self.is_connected = config, {}, False

        def connect(self):
            self.is_connected = True

        def disconnect(self):
            self.is_connected, world.disconnects = False, world.disconnects + 1

        def _poll_and_get_latest_message(self):
            return world.message()

        def get_observation(self):
            message = json.loads(self._poll_and_get_latest_message())
            self.last_remote_state = {key: float(message[key]) for key in STATE_ORDER}
            return self.last_remote_state

        def send_action(self, action):
            world.apply(action)
            return action

    package, robots, lekiwi = types.ModuleType("lerobot"), types.ModuleType("lerobot.robots"), types.ModuleType("lerobot.robots.lekiwi")
    lekiwi.LeKiwiClient, lekiwi.LeKiwiClientConfig = LeKiwiClient, LeKiwiClientConfig
    package.robots, robots.lekiwi = robots, lekiwi
    return {"lerobot": package, "lerobot.robots": robots, "lerobot.robots.lekiwi": lekiwi}


def run_scripted_world(module, world: World, directory: str, *, do: str = "cycle", configured: bool = False, events_path=None, extra_env=None) -> dict:
    """module.main() on a scripted world. configured False is the LEGACY start (no runtime configuration): the store root
    and the saved pose are then put where the test wants them by assigning the two module attributes, which is the only way
    the tagged code has. configured True names the synthetic runtime configuration and nothing is assigned at all."""
    observations = os.path.join(directory, "observations")
    shutil.rmtree(observations, ignore_errors=True)  # every run of a scenario starts from the same empty store, at the same path
    scan_cli = module.cli

    def perceive(role, image, out_dir, name, detector):
        record = world.perception()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.json").write_text(json.dumps(record))
        return [f"{role.upper()}: synthetic perception, cube detected {record['cube']['detected']}, PAD_FRAME {bool((record.get('pad_by_template') or {}).get('pad_frame_valid'))}"]

    def move_base_factory(remote_ip, robot_id, **_options):  # the current code passes client_factory when its reads are stamped; the tagged code passes nothing
        return world.move_base

    # The two lease variables are cleared, not merely left alone: the tagged controller cannot read them, so a shell that
    # happened to export one would make the comparison below meaningless. A scenario that WANTS a lease names it in extra_env.
    environment = {"KC_RUNTIME_CONFIG": os.path.join(directory, "runtime-config.json") if configured else "", "KC_EVENTS_PATH": events_path or "", "KC_RUN_ID": "", "KC_ARTIFACT_DIGEST": "",
                   "KC_HOST_LEASE": "", "KC_HOST_SESSION": "", **(extra_env or {})}
    patches = [mock.patch.object(module, "time", world.clock), mock.patch.object(module, "make_move_base", move_base_factory),
               mock.patch.object(module, "make_bounded_move_arm", lambda read_pose, send: make_bounded_move_arm(read_pose, send, sleep=world.clock.sleep, monotonic=world.clock.perf_counter)),
               mock.patch.object(scan_cli, "now_utc", world.clock.now_utc), mock.patch.object(scan_cli, "PiCamera", lambda ssh_target, local_dir: SyntheticCamera(world)),
               mock.patch.object(scan_cli, "perceive", perceive), mock.patch.object(scan_cli, "make_detector", object), mock.patch.dict(sys.modules, synthetic_lerobot(world)), mock.patch.dict(os.environ, environment)]
    if not configured:
        patches += [mock.patch.object(module, "ObservationStore", functools.partial(ObservationStore, root_dir=observations)),
                    mock.patch.object(module, "HOME_FILE", Path(directory) / "calibration" / "scan-home-current.json")]
    handler, stdout, stderr = signal.getsignal(signal.SIGINT), io.StringIO(), io.StringIO()
    try:
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            try:
                ending = ("returned", module.main(["--do", do, "--remote-ip", "192.0.2.10", "--robot-id", "synthetic-unit", "--pi-ssh", "synthetic-operator@192.0.2.10", "--scan-id", SCAN_ID]))
            except SystemExit as leaving:
                ending = ("SystemExit", leaving.code)
            except Exception as error:  # noqa: BLE001 -- a traceback is an ending too, and must be the SAME ending
                ending = (type(error).__name__, str(error))
    finally:
        signal.signal(signal.SIGINT, handler)
        if configured:
            module.use_runtime_paths(module.cli.runtime_paths({}))  # main() rebound the module to the configuration: back to the legacy layout
    records = sorted(Path(observations, SCAN_ID).glob("cycle-*.json")) if os.path.isdir(os.path.join(observations, SCAN_ID)) else []
    return {"ending": ending, "stdout": stdout.getvalue(), "stderr": stderr.getvalue(), "sent": world.sent, "base_pulses": world.base_pulses, "disconnects": world.disconnects,
            "transitions": json.loads(records[-1].read_text()) if records else None, "record_names": [record.name for record in records]}


# The same boxes, seen 180 px lower in the frame: the cube reads as NEARER (the radial estimate is that much more negative),
# so the grasp happens at about 23 mm out instead of 38, and the bounded retry has room to advance TWICE before the sag
# guard refuses the next target. That is what makes the THIRD empty close, and not a refused step, the end of that cycle.
NEAR_BOXES = [[613, 376, 853, 618], [508, 354, 729, 596], [470, 354, 691, 596], [505, 367, 789, 685], [411, 372, 732, 712]]
WORLD_SCENARIOS = {
    "a full base-advance placement": {},
    "an arm-only placement": {"cube_start_pad_m": [0.0145, -0.1475 + 0.012], "cube_final_pad_m": [0.0145, -0.1475 + 0.045]},
    "an empty close, the bounded retry, then a hold": {"latches": [66.9, 68.4]},
    "three empty closes": {"latches": [66.9, 66.9, 66.9]},
    "a release the evidence does not admit": {"base_rate_mm_s": 12.0},
    "an operator abort while an arm segment runs": {"abort_at_send": 40},
    "an operator abort during a base pulse": {"abort_in_pulse": True},
    "a cube that is already on the pad": {"cube_start_pad_m": [0.0, 0.0]},
    # The failure families below reach raise sites the eight above never reach. They are here, and not only in the events
    # test, because a reason code is worth nothing if stating it changed what the controller did: the tagged controller
    # runs every one of them too, and must print, command and end exactly the same.
    "a cube this cycle could not place, decided before the pickup": {"cube_start_pad_m": [0.20, -0.10]},
    "three empty closes at a reach that admits both advances": {"latches": [66.9, 66.9, 66.9], "boxes": NEAR_BOXES},
    "a cube that does not rise with the jaws": {"cube_scale_per_held_capture": 0.72},
    "a base pulse the two pad frames do not measure": {"base_rate_mm_s": 400.0},
    "jaws that do not reopen after an empty close": {"latches": [66.9, 68.4], "open_stalls_at": 74.0},
}
MACHINE_SCENARIOS = {
    "a full base-advance placement": {"final": {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}},
    "an arm-only placement": {"final": {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.045], "inside_pad_outline": True}, "frame": "f"},
                              "cube_start_pad_m": [0.0145, -0.1475 + 0.012]},
    "three empty closes": {"gripper": 66.9},
    "a release the evidence does not admit": {"base_rate_mm_s": 12.0},
    "an operator abort during a base pulse": {"abort_during_pulse": True},
    "a loaded reach that falls short": {"final": {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"},
                                        "cube_start_pad_m": [0.0145, -0.1475 + 0.012], "carry_stalls": True},
    "a base that is not confirmed stationary": {"base_stops": False},
    "a lowering the gate refuses": {"place_lower_refused": True},
}


def without_clock(transitions):
    return None if transitions is None else [{key: value for key, value in entry.items() if key != "utc"} for entry in transitions]


class BaselineCase(unittest.TestCase):
    def setUp(self):
        self.baseline = load_baseline()
        if self.baseline is None:
            self.skipTest(f"the baseline tag {BASELINE_TAG} is not in this checkout (a shallow clone or a source archive): there is nothing to compare the controller with")
        self.directory = fixtures.use_synthetic_runtime(self)
        current.use_runtime_paths(current.cli.runtime_paths({}))  # the fixture applied the configuration; a differential run starts from the LEGACY layout and says itself when it is configured


class ScriptedMachineDifferential(BaselineCase):
    """The scripted machine of test_run_single_cube_cycle.py, with the REAL state() method, on both codes."""

    def _run(self, module, script: dict) -> dict:
        clock, stdout = VirtualTime(), io.StringIO()
        with mock.patch.object(module, "time", clock), mock.patch.object(module.cli, "now_utc", clock.now_utc), mock.patch.object(module, "HOME_FILE", Path(self.directory) / "calibration" / "scan-home-current.json"), \
                contextlib.redirect_stdout(stdout):
            machine = fixtures.scripted_machine(module, fixtures.machine_script(**script), real_state=True)
            try:
                ending = ("returned", machine.run_cycle())
            except Exception as error:  # noqa: BLE001
                ending = (type(error).__name__, str(error))
        return {"ending": ending, "stdout": stdout.getvalue(), "transitions": machine.transitions, "calls": machine.calls}

    def test_every_scripted_scenario_prints_records_and_ends_as_the_tagged_controller_does(self):
        for name, script in MACHINE_SCENARIOS.items():
            with self.subTest(scenario=name):
                tagged, now = self._run(self.baseline, script), self._run(current, script)
                self.assertEqual(tagged["stdout"], now["stdout"])
                self.assertEqual(tagged["transitions"], now["transitions"])  # the virtual clock is the same in both, so even the utc fields agree
                self.assertEqual(tagged["calls"], now["calls"])
                self.assertEqual(tagged["ending"], now["ending"])
                self.assertIn("[STATE] SEARCH", now["stdout"])  # not vacuous: the real state() ran


class ScriptedWorldDifferential(BaselineCase):
    """main() on the scripted world: the tagged code, the current code as it starts by default, and the current code with a
    runtime configuration, the preflight, the freshness client and events."""

    def _compare(self, tagged: dict, now: dict, what: str) -> None:
        self.assertEqual(tagged["stdout"], now["stdout"], f"stdout differs ({what})")
        self.assertEqual(without_clock(tagged["transitions"]), without_clock(now["transitions"]), f"the transition record differs ({what})")
        self.assertEqual(tagged["transitions"], now["transitions"], f"the transition record differs in its clock field ({what}): something slept that did not sleep before")
        self.assertEqual(tagged["record_names"], now["record_names"])
        self.assertEqual(len(tagged["sent"]), len(now["sent"]), f"the NUMBER of commands differs ({what})")
        self.assertEqual(tagged["sent"], now["sent"], f"a command differs ({what})")
        self.assertEqual(tagged["base_pulses"], now["base_pulses"], f"the base pulses differ ({what})")
        self.assertEqual(tagged["ending"], now["ending"], f"the run ends differently ({what})")
        self.assertEqual(tagged["disconnects"], now["disconnects"])

    def test_every_scenario_is_identical_with_events_off_and_still_identical_with_events_on(self):
        for name, script in WORLD_SCENARIOS.items():
            with self.subTest(scenario=name):
                tagged = run_scripted_world(self.baseline, World(**script), self.directory)
                legacy = run_scripted_world(current, World(**script), self.directory)
                self._compare(tagged, legacy, "events off, no configuration")
                events_path = os.path.join(self.directory, "controller-events.jsonl")
                if os.path.exists(events_path):
                    os.unlink(events_path)
                wired = run_scripted_world(current, World(**script), self.directory, configured=True, events_path=events_path)
                self._compare(tagged, wired, "events on, runtime configuration, preflight, freshness client")
                self.assertEqual("", wired["stderr"], "the emitter or a hook complained")
                self.assertGreater(os.path.getsize(events_path), 0, "events were on and nothing was written: the second comparison proved nothing")
                self.assertEqual(1, tagged["disconnects"])

    def test_the_scenarios_really_reach_what_their_names_say(self):
        """A differential test is only as good as its scenarios: two runs that both die at the first step agree on everything.
        The fourth number is the FEWEST commands a run that really got that far sends, so that a scenario which silently
        starts failing earlier is caught here rather than passing the comparison by agreeing about a short run."""
        expected = {"a full base-advance placement": (0, "BASE_RETURNED", True, 3000), "an arm-only placement": (0, "PLACED", False, 2000),
                    # the second close HOLDS (latches 66.9 then 68.4), so this scenario places the cube and returns the base like the first one
                    "an empty close, the bounded retry, then a hold": (0, "BASE_RETURNED", True, 3000),
                    "three empty closes": (5, "GRASP_RETRY_ADVANCE", False, 1000), "a release the evidence does not admit": (5, "RELEASE_ADMISSION", True, 2000),
                    "an operator abort while an arm segment runs": (4, "PREGRASP", False, 40), "an operator abort during a base pulse": (5, "BASE_MEASURED", True, 1500),
                    # a cube whose footprint is already inside the pad is a NO-OP: SEARCH observes, DONE_ALREADY_PLACED returns, and nothing is ever commanded
                    "a cube that is already on the pad": (0, "DONE_ALREADY_PLACED", False, 0),
                    # a cube this cycle could not place is left EXACTLY where it lies: the feasibility check happens at SEARCH, before anything is picked up
                    "a cube this cycle could not place, decided before the pickup": (5, "SEARCH", False, 0),
                    "three empty closes at a reach that admits both advances": (5, "GRASP_CLOSE", False, 1000), "a cube that does not rise with the jaws": (5, "OBJECT_SECURED", False, 1000),
                    "a base pulse the two pad frames do not measure": (5, "BASE_MEASURED", True, 1500), "jaws that do not reopen after an empty close": (5, "GRASP_RETRY_ADVANCE", False, 1000)}
        for name, script in WORLD_SCENARIOS.items():
            with self.subTest(scenario=name):
                status, last_state, base_moved, fewest_commands = expected[name]
                run = run_scripted_world(current, World(**script), self.directory)
                self.assertEqual(("returned", status), run["ending"], run["stdout"][-2000:])
                self.assertEqual(last_state, run["transitions"][-1]["state"])
                self.assertEqual(base_moved, bool(run["base_pulses"]))
                self.assertGreaterEqual(len(run["sent"]), fewest_commands, "the scenario stopped earlier than its name says")
                if script.get("abort_at_send"):  # the Ctrl-C is raised FROM that command: one more would mean the stop was not honoured
                    self.assertEqual(script["abort_at_send"], len(run["sent"]))
                if fewest_commands == 0:  # the no-op: "at least none" is no claim at all, so say the whole thing
                    self.assertEqual([], run["sent"], "a cube already on the pad must be left exactly where it lies")



class EmptyLowPoseWorld(World):
    """The scripted world with nothing usable in any low-pose frame: the cube is recognized with no contact pixel and
    placed in no PAD_FRAME, from every pan. The tagged controller refused this frame at SEARCH with nothing commanded."""

    def perception(self) -> dict:
        record = super().perception()
        if self.joints["shoulder_lift"] < -60.0:
            record["cube"] = {**record["cube"], "contact_px": None}
            record["cube_by_template"] = None
        return record


class TheLowPoseFrameWithNoUsableCube(BaselineCase):
    """With no word the SEARCH SWEEP does not run (policy_search.WORDS, search_sweep): a low-pose frame with no usable cube
    prints, records, commands and ends exactly as the tagged controller did, which is no command at all. Under the word the
    sweep runs: that difference is the word's, by design, and is stated here so it is never mistaken for the default path."""

    def test_with_no_word_the_run_is_the_tagged_one_and_nothing_is_commanded(self):
        tagged = run_scripted_world(self.baseline, EmptyLowPoseWorld(), self.directory)
        legacy = run_scripted_world(current, EmptyLowPoseWorld(), self.directory)
        ScriptedWorldDifferential._compare(self, tagged, legacy, "no usable cube, events off, no configuration")
        events_path = os.path.join(self.directory, "controller-events.jsonl")
        wired = run_scripted_world(current, EmptyLowPoseWorld(), self.directory, configured=True, events_path=events_path)
        ScriptedWorldDifferential._compare(self, tagged, wired, "no usable cube, events on, runtime configuration")
        self.assertEqual(("returned", 5), legacy["ending"], legacy["stdout"][-2000:])
        self.assertEqual([], legacy["sent"], "no pan command, no command of any kind")
        self.assertEqual([], legacy["base_pulses"])
        self.assertEqual("SEARCH", legacy["transitions"][-1]["state"])

    def test_under_the_word_the_sweep_pans_the_low_pose_and_the_tagged_controller_never_did(self):
        tagged = run_scripted_world(self.baseline, EmptyLowPoseWorld(), self.directory)
        swept = run_scripted_world(current, EmptyLowPoseWorld(), self.directory, extra_env={"KC_PLACEMENT_ENVELOPE": "search_sweep"})
        self.assertEqual([], tagged["sent"])
        self.assertEqual(("returned", 5), swept["ending"], swept["stdout"][-2000:])
        pans = [action["arm_shoulder_pan.pos"] for action in swept["sent"] if "arm_shoulder_pan.pos" in action]
        low = fixtures.SYNTHETIC_LOW_POSE["shoulder_pan"]
        self.assertAlmostEqual(low - current.SEARCH_SWEEP_DEG, min(pans), places=1)
        self.assertAlmostEqual(low + current.SEARCH_SWEEP_DEG, max(pans), places=1)
        self.assertEqual("SEARCH_SWEEP", swept["transitions"][-1]["state"])

if __name__ == "__main__":
    unittest.main()
