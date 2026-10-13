#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_preflight.py: the preflight refuses, BEFORE any movement and with a reason code, what today
# fails in the middle of a cycle or degrades in silence: a missing or incomplete saved pose, a missing pad template file, a
# missing wrist camera id, an observation root that would put raw captures inside a git repository. Every fixture is
# synthetic and generated in a temporary directory (the PNGs are built here, byte by byte); no robot, camera or network.
# Run directly: python3 packages/demo-runtime/tests/test_preflight.py
import ast
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import preflight  # noqa: E402
from demo_runtime.config import CakeConfig, CakeRobotConfig, CameraConfig, HostConfig, ModelConfig, RuntimeConfig  # noqa: E402
from demo_runtime.preflight import (REQUIRED_TEMPLATE_FILES, SCAN_JOINTS, Action, ActionKind, ActionRefused, PreflightInputError, input_digests, parse_action,  # noqa: E402
                                    run_preflight)

# Built at runtime: the repository gates refuse these shapes in a tracked file, and the tests need them to prove redaction.
PRIVATE_ADDRESS = ".".join(("192", "168", "7", "41"))
USER_NAME = "operator" + "name"
SSH_TARGET = USER_NAME + "@" + PRIVATE_ADDRESS
ROBOT_ID = "kiwi" + "-unit-" + "0042"
WRIST_ID = "/dev/v4l/by-id/" + "usb-" + "Vendor_Cam_" + "SN" + "7654321" + "-video-index0"
PRIVATE_DIR = "private-" + USER_NAME  # a path component of every fixture path: a leaked path would show it

GIT = shutil.which("git")
GIT_ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}  # a hook's GIT_DIR must not redirect `git init`
WATCH_WAIT_S = 20.0  # the ceiling on waiting for a background reader thread; nothing here is expected to take a second


def png_bytes(width: int, height: int) -> bytes:
    """A complete, valid 8-bit grayscale PNG of zeros: synthetic by construction."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


class Fixture:
    """A complete, valid data root in a temporary directory. Tests break ONE thing and look for ONE reason code."""

    def __init__(self, test: unittest.TestCase):
        holder = tempfile.TemporaryDirectory()
        test.addCleanup(holder.cleanup)
        self.tmp = os.path.realpath(holder.name)
        self.root = Path(self.tmp) / PRIVATE_DIR / "demo-data"
        self.pose = self.root / "calibration" / "scan-home-current.json"
        self.template = self.root / "calibration" / "pad-template"
        self.observations = self.root / "observations"
        self.template.mkdir(parents=True)
        self.observations.mkdir()
        self.pose.write_text(json.dumps({"pose_name": "SYNTHETIC", "measured_joints_deg": {"shoulder_pan": 0.0, "shoulder_lift": 10.0, "elbow_flex": -20.5, "wrist_flex": 30, "wrist_roll": 0.0, "gripper": 5.0}}))
        (self.template / "template.json").write_text(json.dumps({"pad_size_m": [0.4, 0.3], "pad_to_raster": [[100.0, 0.0, 20.0], [0.0, -100.0, 15.0], [0.0, 0.0, 1.0]]}))
        (self.template / "template.png").write_bytes(png_bytes(8, 6))
        (self.template / "valid.png").write_bytes(png_bytes(8, 6))

    def config(self, **overrides) -> RuntimeConfig:
        fields = dict(data_root=self.root, saved_pose_file=self.pose, pad_template_dir=self.template, observation_root=self.observations,
                      host=HostConfig(address=PRIVATE_ADDRESS, robot_id=ROBOT_ID), pi_ssh_target=SSH_TARGET, cameras=CameraConfig(wrist_id=WRIST_ID, front_id=None),
                      model=ModelConfig(identity="example-org/example-detector"))
        fields.update(overrides)
        return RuntimeConfig(**fields)


def check(report, check_id):
    return next(item for item in report.checks if item.id == check_id)


class PreflightCase(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)

    def assert_only_failure(self, report, check_id: str, reason_code: str):
        self.assertFalse(report.ok)
        self.assertEqual([(check_id, reason_code)], [(item.id, item.reason_code) for item in report.failed()], [item.to_dict() for item in report.failed()])


class HappyPathTest(PreflightCase):
    def test_a_complete_configuration_passes_every_check(self):
        self.assertIsNone(preflight.enclosing_git_repo(self.fx.tmp), "the temporary directory is inside a git repository: set TMPDIR outside one")
        report = run_preflight(self.fx.config(), "cycle")
        self.assertTrue(report.ok, [item.to_dict() for item in report.failed()])
        self.assertEqual(["saved_pose", "pad_template", "observation_root_writable", "observation_root_local_only", "wrist_camera_id", "front_camera_id", "host_address", "robot_id",
                          "pi_ssh_target", "model_identity", "action"], [item.id for item in report.checks])
        self.assertEqual({"ok"}, {item.reason_code for item in report.checks})
        self.assertEqual("cycle", report.action_kind)
        self.assertEqual([], os.listdir(self.fx.observations))  # the write probe is gone

    def test_every_check_runs_even_when_several_fail_so_one_report_lists_everything(self):
        self.fx.pose.unlink()
        (self.fx.template / "valid.png").unlink()
        report = run_preflight(self.fx.config(cameras=CameraConfig(wrist_id="")), "dance")
        self.assertEqual(11, len(report.checks))
        self.assertEqual(("saved_pose_missing", "pad_template_file_missing", "wrist_camera_id_missing", "action_not_allowed"), report.reason_codes())


class SavedPoseTest(PreflightCase):
    def test_a_missing_saved_pose_is_refused_at_preflight_not_in_the_middle_of_a_cycle(self):
        self.fx.pose.unlink()
        report = run_preflight(self.fx.config(), "cycle")
        self.assert_only_failure(report, "saved_pose", "saved_pose_missing")

    def test_a_saved_pose_that_does_not_parse_is_refused(self):
        self.fx.pose.write_text("{ not json")
        self.assert_only_failure(run_preflight(self.fx.config(), "cycle"), "saved_pose", "saved_pose_not_json")

    def test_a_saved_pose_without_the_measured_joints_is_refused(self):
        for content in ({}, {"measured_joints_deg": None}, {"measured_joints_deg": [1, 2, 3, 4]}, []):
            with self.subTest(content=content):
                self.fx.pose.write_text(json.dumps(content))
                self.assert_only_failure(run_preflight(self.fx.config(), "cycle"), "saved_pose", "saved_pose_joints_missing")

    def test_every_scan_joint_is_required_and_the_detail_names_the_missing_one(self):
        for joint in SCAN_JOINTS:
            with self.subTest(joint=joint):
                joints = {name: 1.0 for name in SCAN_JOINTS if name != joint}
                self.fx.pose.write_text(json.dumps({"measured_joints_deg": joints}))
                report = run_preflight(self.fx.config(), "cycle")
                self.assert_only_failure(report, "saved_pose", "saved_pose_joints_missing")
                self.assertIn(joint, check(report, "saved_pose").detail)

    def test_a_joint_that_is_not_a_finite_number_is_refused(self):
        for value in ("12.5", None, True, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.fx.pose.write_text(json.dumps({"measured_joints_deg": {**{name: 1.0 for name in SCAN_JOINTS}, "elbow_flex": value}}))
                self.assert_only_failure(run_preflight(self.fx.config(), "cycle"), "saved_pose", "saved_pose_joint_not_numeric")


class PadTemplateTest(PreflightCase):
    def test_each_missing_template_file_is_a_failure_with_its_name_never_a_silent_degradation(self):
        for name in REQUIRED_TEMPLATE_FILES:
            with self.subTest(missing=name):
                fixture = Fixture(self)
                (fixture.template / name).unlink()
                report = run_preflight(fixture.config(), "observe")
                self.assert_only_failure(report, "pad_template", "pad_template_file_missing")
                self.assertIn(name, check(report, "pad_template").detail)

    def test_a_missing_template_directory_is_a_failure(self):
        shutil.rmtree(self.fx.template)
        self.assert_only_failure(run_preflight(self.fx.config(), "observe"), "pad_template", "pad_template_dir_missing")

    def test_a_template_json_that_does_not_parse_or_lacks_the_matrix_the_localizer_reads_is_refused(self):
        for content in ("{ nope", json.dumps([]), json.dumps({"pad_size_m": [0.4, 0.3]}), json.dumps({"pad_to_raster": [[1, 0], [0, 1]]}),
                        json.dumps({"pad_to_raster": [[1, 0, 0], [0, 1, 0], [0, 0, "1"]]}), json.dumps({"pad_to_raster": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "pad_size_m": [0.4, -1]})):
            with self.subTest(content=content):
                (self.fx.template / "template.json").write_text(content)
                self.assert_only_failure(run_preflight(self.fx.config(), "observe"), "pad_template", "pad_template_json_invalid")

    def test_a_template_image_that_is_not_a_png_is_refused(self):
        (self.fx.template / "valid.png").write_bytes(b"synthetic bytes that are not an image at all")
        report = run_preflight(self.fx.config(), "observe")
        self.assert_only_failure(report, "pad_template", "pad_template_image_invalid")
        self.assertIn("valid.png", check(report, "pad_template").detail)

    def test_template_and_mask_of_different_sizes_are_refused_because_the_localizer_refuses_them_at_the_first_capture(self):
        (self.fx.template / "valid.png").write_bytes(png_bytes(8, 5))
        self.assert_only_failure(run_preflight(self.fx.config(), "observe"), "pad_template", "pad_template_size_mismatch")


class IdentityTest(PreflightCase):
    def test_a_missing_wrist_camera_id_is_refused(self):
        report = run_preflight(self.fx.config(cameras=CameraConfig(wrist_id="", front_id=None)), "observe")
        self.assert_only_failure(report, "wrist_camera_id", "wrist_camera_id_missing")

    def test_a_null_front_camera_is_an_explicit_choice_and_an_empty_one_is_a_mistake(self):
        self.assertTrue(check(run_preflight(self.fx.config(), "observe"), "front_camera_id").ok)
        report = run_preflight(self.fx.config(cameras=CameraConfig(wrist_id=WRIST_ID, front_id="")), "observe")
        self.assert_only_failure(report, "front_camera_id", "front_camera_id_empty")

    def test_missing_host_address_robot_id_ssh_target_and_model_identity_are_each_refused_by_name(self):
        cases = {
            ("host_address", "host_address_missing"): dict(host=HostConfig(address="", robot_id=ROBOT_ID)),
            ("robot_id", "robot_id_missing"): dict(host=HostConfig(address=PRIVATE_ADDRESS, robot_id="")),
            ("pi_ssh_target", "pi_ssh_target_missing"): dict(pi_ssh_target=""),
            ("model_identity", "model_identity_missing"): dict(model=ModelConfig(identity="")),
        }
        for (check_id, reason_code), overrides in cases.items():
            with self.subTest(check=check_id):
                self.assert_only_failure(run_preflight(self.fx.config(**overrides), "observe"), check_id, reason_code)

    def test_an_identity_that_could_break_out_of_the_remote_shell_or_pass_as_an_ssh_option_is_refused_without_being_echoed(self):
        marker = "marker" + "value"
        cases = {
            ("wrist_camera_id", "wrist_camera_id_unsafe"): dict(cameras=CameraConfig(wrist_id=marker + "'; touch x; '")),
            ("front_camera_id", "front_camera_id_unsafe"): dict(cameras=CameraConfig(wrist_id=WRIST_ID, front_id=marker + " $(id)")),
            ("pi_ssh_target", "pi_ssh_target_unsafe"): dict(pi_ssh_target="-o" + marker),
            ("robot_id", "robot_id_unsafe"): dict(host=HostConfig(address=PRIVATE_ADDRESS, robot_id=marker + "\n")),
        }
        for (check_id, reason_code), overrides in cases.items():
            with self.subTest(check=check_id):
                report = run_preflight(self.fx.config(**overrides), "observe")
                self.assert_only_failure(report, check_id, reason_code)
                self.assertNotIn(marker, json.dumps(report.to_dict()))


class ActionTest(PreflightCase):
    def test_every_action_the_controller_dispatches_on_is_allowed_and_typed(self):
        expected = {"observe": (), "pan:-4.5": (-4.5,), "move:10,-20": (10.0, -20.0), "grasp:0,0": (0.0, 0.0), "grip:latch": ("latch",), "grip:open": ("open",),
                    "base:-0.3": (-0.3,), "exit": (), "cycle": ()}
        for text, values in expected.items():
            with self.subTest(action=text):
                action = parse_action(text)
                self.assertEqual((ActionKind(text.partition(":")[0]), values), (action.kind, action.values))
                report = run_preflight(self.fx.config(), text)
                self.assertTrue(report.ok)
                self.assertEqual(action.kind.value, report.action_kind)

    def test_an_action_outside_the_allowlist_is_refused_and_its_text_is_never_echoed(self):
        marker = "rm" + "-everything"
        for text in (marker, marker + ":1", "Cycle", " cycle", "cycle;" + marker, "", None, 7):
            with self.subTest(action=text):
                report = run_preflight(self.fx.config(), text)
                self.assertFalse(check(report, "action").ok)
                self.assertIn(check(report, "action").reason_code, ("action_not_allowed", "action_missing"))
                self.assertIsNone(report.action_kind)
                self.assertNotIn(marker, json.dumps(report.to_dict()))

    def test_malformed_arguments_are_refused(self):
        for text in ("pan", "pan:abc", "pan:nan", "pan:inf", "pan:1,2", "move:1", "move:1,2,3", "move:1,", "grasp:x,y", "grip", "grip:squeeze", "base:", "observe:5", "cycle:now", "exit:1"):
            with self.subTest(action=text):
                self.assert_only_failure(run_preflight(self.fx.config(), text), "action", "action_argument_invalid")

    def test_a_number_is_plain_ascii_decimal_because_float_alone_takes_far_more_than_that(self):
        """float() takes surrounding whitespace (a trailing newline included), "1_0" and the digits of any script. Text that
        can come from a browser is held to a grammar instead; the non-ASCII digit is built here so this file stays ASCII."""
        for text in ("pan: 4", "pan:4 ", "pan:4\n", "pan:1_0", "pan:" + chr(0x0661), "pan:0x10", "pan:1e999", "pan:+", "pan:.", "pan:1e", "move:1, 2", "move:1,2\n", "base:--1"):
            with self.subTest(action=text):
                self.assert_only_failure(run_preflight(self.fx.config(), text), "action", "action_argument_invalid")
        for text, values in {"pan:+4": (4.0,), "pan:-0.5": (-0.5,), "pan:.5": (0.5,), "pan:4.": (4.0,), "move:1e1,-2E-1": (10.0, -0.2)}.items():
            with self.subTest(action=text):
                self.assertEqual(values, parse_action(text).values)

    def test_a_colon_with_nothing_after_it_is_refused_for_every_kind(self):
        for kind in ActionKind:
            with self.subTest(kind=kind.value):
                self.assert_only_failure(run_preflight(self.fx.config(), kind.value + ":"), "action", "action_argument_invalid")

    def test_a_hand_built_action_goes_through_the_same_gate(self):
        self.assertTrue(run_preflight(self.fx.config(), Action(ActionKind.PAN, (3.0,))).ok)
        self.assert_only_failure(run_preflight(self.fx.config(), Action(ActionKind.PAN, ())), "action", "action_argument_invalid")

    def test_a_refused_action_carries_no_trace_of_the_text_on_the_exception_either(self):
        marker = "marker" + "action"
        with self.assertRaises(ActionRefused) as caught:
            parse_action(marker + ":1")
        self.assertNotIn(marker, str(caught.exception))
        self.assertIsNone(caught.exception.__context__)


class MotorHostTest(PreflightCase):
    """The one check that leaves this machine. The probe is a fake here: no ssh runs, and no robot computer exists."""

    def report(self, state, **overrides):
        self.asked = []

        def probe(target):
            self.asked.append(target)
            return state

        return run_preflight(self.fx.config(**overrides), "cycle", motor_host=probe)

    def test_without_a_probe_the_report_is_the_local_one_it_always_was_and_nothing_leaves_this_machine(self):
        report = run_preflight(self.fx.config(), "cycle")
        self.assertNotIn("motor_host", [item.id for item in report.checks])
        self.assertEqual(11, len(report.checks))

    def test_a_running_host_that_is_not_in_scan_mode_passes_and_the_check_sits_between_the_ssh_target_and_the_model(self):
        report = self.report("running_normal")
        self.assertTrue(report.ok, [item.to_dict() for item in report.failed()])
        self.assertEqual(["saved_pose", "pad_template", "observation_root_writable", "observation_root_local_only", "wrist_camera_id", "front_camera_id", "host_address",
                          "robot_id", "pi_ssh_target", "motor_host", "model_identity", "action"], [item.id for item in report.checks])
        self.assertEqual([SSH_TARGET], self.asked, "the target comes from the config and from nowhere else")

    def test_every_answer_that_is_not_a_running_normal_host_refuses_the_command_by_name(self):
        expected = {"not_running": "motor_host_not_running", "scan_mode": "motor_host_in_scan_mode", "mode_unknown": "motor_host_mode_unknown",
                    "unreachable": "motor_host_unreachable", "something this module has never heard of": "motor_host_unreachable"}
        for state, reason_code in expected.items():
            with self.subTest(state=state):
                self.assert_only_failure(self.report(state), "motor_host", reason_code)

    def test_a_report_that_refuses_the_host_names_no_target_address_or_path(self):
        report = self.report("scan_mode")
        text = json.dumps(report.to_dict())
        for secret in (self.fx.tmp, PRIVATE_DIR, USER_NAME, PRIVATE_ADDRESS, SSH_TARGET, ROBOT_ID):
            self.assertNotIn(secret, text)

    def test_with_no_usable_ssh_target_nothing_is_asked_and_the_check_says_so_instead_of_inventing_one(self):
        for target in ("", "-oProxyCommand=something"):
            with self.subTest(target=target):
                report = self.report("not_running", pi_ssh_target=target)
                self.assertTrue(check(report, "motor_host").ok, "the missing target is pi_ssh_target's own failure, not a second one")
                self.assertEqual([], self.asked, "an unusable target is never handed to ssh")
                self.assertEqual(["pi_ssh_target"], [item.id for item in report.failed()])

    def test_a_probe_that_raises_is_a_failed_check_that_reports_its_class_only(self):
        def explode(target):
            raise RuntimeError("could not reach " + target)

        report = run_preflight(self.fx.config(), "cycle", motor_host=explode)
        self.assert_only_failure(report, "motor_host", "check_error")
        self.assertNotIn(SSH_TARGET, check(report, "motor_host").detail)


class MotorHostProbeTest(unittest.TestCase):
    """probe_motor_host itself, with a fake runner: what it sends, and what it makes of every answer."""

    def runner(self, returncode=0, raises=None):
        calls = []

        def run(argv, **options):
            calls.append((argv, options))
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(argv, returncode, "", "")

        return run, calls

    def test_it_sends_one_ssh_with_the_script_on_stdin_and_never_a_shell_string(self):
        run, calls = self.runner()
        self.assertEqual("running_normal", preflight.probe_motor_host(SSH_TARGET, runner=run))
        (argv, options), = calls
        self.assertEqual(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", SSH_TARGET, "bash -s"], argv)
        self.assertIs(options["capture_output"], True, "the remote output is never read into a report")
        self.assertEqual(preflight.MOTOR_HOST_TIMEOUT_S, options["timeout"])
        self.assertIn(preflight.MOTOR_HOST_PATTERN, options["input"])
        for forbidden in ("kill", "pkill", "systemctl", "start-host", "python", "rm "):
            self.assertNotIn(forbidden, options["input"], "the probe READS a process list; it starts and stops nothing")

    def test_each_exit_status_of_the_script_becomes_its_own_state_and_anything_else_is_unreachable(self):
        for returncode, state in {0: "running_normal", 3: "not_running", 4: "scan_mode", 5: "mode_unknown", 255: "unreachable", 1: "unreachable", 127: "unreachable"}.items():
            with self.subTest(returncode=returncode):
                run, _calls = self.runner(returncode=returncode)
                self.assertEqual(state, preflight.probe_motor_host(SSH_TARGET, runner=run))

    def test_a_timeout_a_missing_ssh_or_any_other_failure_is_unreachable_and_never_raises(self):
        for error in (subprocess.TimeoutExpired(cmd="ssh", timeout=1.0), FileNotFoundError("ssh"), OSError("broken")):
            with self.subTest(error=type(error).__name__):
                run, _calls = self.runner(raises=error)
                self.assertEqual("unreachable", preflight.probe_motor_host(SSH_TARGET, runner=run))

    def test_every_state_the_probe_can_return_has_a_named_refusal_or_is_the_passing_one(self):
        """A state added later without a reason code would fall through to 'unreachable' and report the wrong thing."""
        self.assertEqual(set(preflight.MOTOR_HOST_STATES), set(preflight._MOTOR_HOST_BY_STATUS.values()) | {"unreachable"})
        self.assertEqual(set(preflight.MOTOR_HOST_STATES) - {"running_normal"}, set(preflight._MOTOR_HOST_REFUSALS))
        for reason_code, detail in preflight._MOTOR_HOST_REFUSALS.values():
            self.assertTrue(reason_code.startswith("motor_host_"), reason_code)
            self.assertTrue(detail)

    def test_the_script_is_the_host_preflight_the_proven_launcher_runs(self):
        """bin/laptop/run-cycle.sh is the launcher every recorded hardware run went through. The process it looks for,
        the scan-mode mark it refuses on and the connect timeout it allows are read out of that file, so a change there
        fails here instead of leaving the runtime asking a different question than the launcher."""
        launcher = (REPO_ROOT / "bin" / "laptop" / "run-cycle.sh").read_text(encoding="utf-8")
        self.assertIn("pgrep -f '" + preflight.MOTOR_HOST_PATTERN + "'", launcher)
        self.assertIn("grep -qx '" + preflight.SCAN_MODE_MARK + "'", launcher)
        self.assertIn("ConnectTimeout=" + str(preflight.SSH_CONNECT_TIMEOUT_S), launcher)
        self.assertIn("/environ", launcher, "the launcher reads the environment of the matched process, and so does this probe")
        for line in ("pgrep -f", "/environ", "grep -qx"):
            self.assertIn(line, preflight._MOTOR_HOST_SCRIPT)


class ReadinessWatchTest(unittest.TestCase):
    """The background reading of the robot computer: what it says before anything has been read, what it makes of each
    answer, and what it does with an answer that has aged. A fake probe and a fake clock; no ssh and no robot computer."""

    def setUp(self):
        self.now = 1000.0
        self.answer = "running_normal"
        self.asked = []

    def clock(self):
        return self.now

    def probe(self, target):
        self.asked.append(target)
        return self.answer

    def watch(self, **overrides):
        options = {"probe": self.probe, "clock": self.clock, **overrides}
        return preflight.ReadinessWatch(SSH_TARGET, **options)

    def read(self, answer):
        self.answer, self.asked = answer, []
        watch = self.watch()
        watch.read_once()
        return watch.reading()

    def test_before_anything_has_been_read_it_is_not_ready_and_says_that_nothing_has_been_read(self):
        reading = self.watch().reading()
        self.assertEqual(("never_read", False, None, "robot_readiness_not_read"), (reading.state, reading.ready, reading.age_ms, reading.reason_code))
        self.assertEqual([], self.asked, "asking what it knows never runs a probe")

    def test_each_answer_becomes_its_own_state_and_only_a_normal_host_is_ready(self):
        expected = {"running_normal": (True, None), "not_running": (False, "motor_host_not_running"), "scan_mode": (False, "motor_host_in_scan_mode"),
                    "mode_unknown": (False, "motor_host_mode_unknown"), "unreachable": (False, "motor_host_unreachable")}
        for answer, (ready, reason_code) in expected.items():
            with self.subTest(answer=answer):
                reading = self.read(answer)
                self.assertEqual((answer, ready, reason_code), (reading.state, reading.ready, reading.reason_code))
                self.assertEqual([SSH_TARGET], self.asked, "the target comes from the configuration and from nowhere else")

    def test_a_computer_that_could_not_be_reached_and_one_with_no_host_running_are_told_apart_at_a_glance(self):
        unreachable, no_host = self.read("unreachable"), self.read("not_running")
        self.assertNotEqual(unreachable.reason_code, no_host.reason_code)
        self.assertIn("could not be asked", unreachable.detail)
        self.assertIn("is not running on the robot computer", no_host.detail)

    def test_an_answer_older_than_the_limit_is_reported_as_stale_and_never_as_the_answer_it_was(self):
        watch = self.watch()
        watch.read_once()
        self.assertTrue(watch.reading().ready)
        self.now += preflight.READINESS_MAX_AGE_S + 0.001
        stale = watch.reading()
        self.assertEqual(("reading_stale", False, "robot_readiness_stale"), (stale.state, stale.ready, stale.reason_code))
        self.assertEqual([SSH_TARGET], self.asked, "ageing out is decided here and never by asking again on the request path")

    def test_an_answer_that_says_the_host_is_down_ages_out_the_same_way_as_one_that_says_it_is_up(self):
        """Staleness is about the reading, not about what it said: an old refusal is no more current than an old pass."""
        self.answer = "not_running"
        watch = self.watch()
        watch.read_once()
        self.now += preflight.READINESS_MAX_AGE_S + 1.0
        self.assertEqual("reading_stale", watch.reading().state)

    def test_the_age_travels_with_the_reading_and_is_stated_in_its_sentence(self):
        watch = self.watch()
        watch.read_once()
        self.now += 7.25
        reading = watch.reading()
        self.assertAlmostEqual(7250.0, reading.age_ms, places=3)
        self.assertIn("a reading taken 7 s ago", reading.detail)
        self.assertNotIn("s ago", self.watch().reading().detail, "there is no age to state when nothing has been read")

    def test_a_probe_that_raises_is_read_as_unreachable_and_the_exception_never_reaches_the_caller(self):
        def explode(target):
            raise RuntimeError("could not reach " + target)

        watch = self.watch(probe=explode)
        self.assertEqual("unreachable", watch.read_once())
        self.assertEqual("motor_host_unreachable", watch.reading().reason_code)

    def test_an_answer_this_module_has_never_heard_of_is_unreachable_rather_than_readiness(self):
        reading = self.read("some state a later build invented")
        self.assertEqual(("unreachable", False), (reading.state, reading.ready))

    def test_a_probe_that_stops_answering_ages_out_instead_of_holding_up_the_runtime(self):
        """The hang this bounds: reading() is on the snapshot path, so it must come back while a probe is stuck inside
        an ssh that will not answer. It does, with the last answer, and that answer then ages out on its own."""
        entered, holding, calls = threading.Event(), threading.Event(), []

        def probe(target):
            calls.append(target)
            if len(calls) > 1:
                entered.set()
                holding.wait(WATCH_WAIT_S)
            return "running_normal"

        watch = preflight.ReadinessWatch(SSH_TARGET, probe=probe, clock=self.clock, interval_s=0.01)
        watch.start()
        try:
            self.assertTrue(entered.wait(WATCH_WAIT_S), "the reader took a first reading and is now stuck in a second")
            self.assertEqual("running_normal", watch.reading().state, "the answer it did get is still the answer it has")
            self.now += preflight.READINESS_MAX_AGE_S + 1.0
            stuck = watch.reading()
            self.assertEqual(("reading_stale", False), (stuck.state, stuck.ready), "a probe that never answers reads as stale, never as ready")
        finally:
            holding.set()
            watch.stop()

    def test_the_reader_takes_readings_on_its_own_and_takes_no_more_once_it_is_stopped(self):
        watch = preflight.ReadinessWatch(SSH_TARGET, probe=self.probe, interval_s=0.01)
        watch.start()
        deadline = time.monotonic() + WATCH_WAIT_S
        while watch.readings < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(watch.readings, 3, "the schedule is the reader's own and nothing on the request path drives it")
        watch.stop()
        taken = watch.readings
        time.sleep(0.1)
        self.assertEqual(taken, watch.readings, "a reader that was stopped takes no further readings")

    def test_every_state_it_can_report_has_a_reason_code_and_a_sentence_except_the_one_that_is_ready(self):
        """A state added without an entry would fall through to unreachable and report a different thing than happened."""
        self.assertEqual(set(preflight.READINESS_STATES) - {preflight.ROBOT_READY}, set(preflight._READINESS_REASONS))
        self.assertEqual(set(preflight.MOTOR_HOST_STATES) - {preflight.ROBOT_READY}, set(preflight._MOTOR_HOST_REFUSALS),
                         "the submit path's own refusals keep exactly the four members its test pins them to")
        for state in preflight.READINESS_STATES:
            with self.subTest(state=state):
                reading = preflight.readiness_of(state, None)
                self.assertEqual(state, reading.state)
                self.assertTrue(reading.detail)
                self.assertEqual(state == preflight.ROBOT_READY, reading.reason_code is None)

    def test_no_sentence_it_can_produce_names_a_target_an_address_or_a_path(self):
        for state in preflight.READINESS_STATES:
            with self.subTest(state=state):
                text = json.dumps(preflight.readiness_of(state, 1234.0).to_dict())
                for secret in (PRIVATE_DIR, USER_NAME, PRIVATE_ADDRESS, SSH_TARGET, ROBOT_ID):
                    self.assertNotIn(secret, text)

    def test_a_pass_never_claims_that_attachment_cannot_refuse(self):
        """The reading says the motor host was running. bin/pi/lekiwi_host_safe_attach.py still refuses to attach when
        torque is off or a joint's goal and present positions are further apart than its tolerance, and a power cycle
        leaves a robot in exactly that state with a perfectly healthy host. The sentence must not say otherwise."""
        detail = preflight.readiness_of(preflight.ROBOT_READY, 0.0).detail
        self.assertIn("can still refuse to attach the arm", detail)
        for overclaim in ("guarantee", "will be accepted", "cannot refuse", "safe to", "ready to command"):
            self.assertNotIn(overclaim, detail)

    def test_a_usable_ssh_target_is_the_same_question_the_identity_check_asks(self):
        for target in ("", None, "-oProxyCommand=something", "two words", "a;b"):
            with self.subTest(target=target):
                self.assertFalse(preflight.usable_ssh_target(target))
        self.assertTrue(preflight.usable_ssh_target(SSH_TARGET))


@unittest.skipUnless(GIT, "git is not installed: the local-only rule cannot be exercised against a real repository")
class ObservationRootLocalOnlyTest(PreflightCase):
    def make_repo(self) -> Path:
        repo = Path(self.fx.tmp) / PRIVATE_DIR / "some-checkout"
        repo.mkdir(parents=True)
        subprocess.run([GIT, "init", "-q", str(repo)], check=True, capture_output=True, env=GIT_ENV, timeout=30)
        (repo / ".gitignore").write_text("ignored-data/\n")
        return repo

    def test_an_observation_root_inside_a_git_repository_and_not_ignored_is_refused(self):
        repo = self.make_repo()
        (repo / "tracked-data").mkdir()
        report = run_preflight(self.fx.config(observation_root=repo / "tracked-data"), "observe")
        self.assert_only_failure(report, "observation_root_local_only", "observation_root_not_local_only")

    def test_it_is_refused_even_when_it_does_not_exist_yet(self):
        repo = self.make_repo()
        report = run_preflight(self.fx.config(observation_root=repo / "not-created" / "deeper"), "observe")
        self.assert_only_failure(report, "observation_root_local_only", "observation_root_not_local_only")
        self.assertFalse((repo / "not-created").exists())  # a preflight leaves no directory behind

    def test_an_observation_root_inside_a_git_repository_that_git_ignores_passes(self):
        repo = self.make_repo()
        (repo / "ignored-data" / "observations").mkdir(parents=True)
        report = run_preflight(self.fx.config(observation_root=repo / "ignored-data" / "observations"), "observe")
        self.assertTrue(report.ok, [item.to_dict() for item in report.failed()])
        self.assertIn("ignored", check(report, "observation_root_local_only").detail)

    def test_a_symlink_outside_the_repository_cannot_stand_in_for_a_directory_inside_it(self):
        repo = self.make_repo()
        (repo / "tracked-data").mkdir()
        link = Path(self.fx.tmp) / "looks-outside"
        os.symlink(repo / "tracked-data", link)
        report = run_preflight(self.fx.config(observation_root=link), "observe")
        self.assert_only_failure(report, "observation_root_local_only", "observation_root_not_local_only")

    def test_when_git_cannot_answer_the_root_is_refused(self):
        repo = self.make_repo()
        (repo / "ignored-data").mkdir()

        def broken(path, repo_root):
            raise OSError("git said " + str(path))

        report = run_preflight(self.fx.config(observation_root=repo / "ignored-data"), "observe", is_ignored=broken)
        self.assert_only_failure(report, "observation_root_local_only", "observation_root_git_unavailable")
        self.assertNotIn(PRIVATE_DIR, check(report, "observation_root_local_only").detail)

    def test_a_dot_git_entry_that_real_git_cannot_open_fails_closed(self):
        broken = Path(self.fx.tmp) / PRIVATE_DIR / "broken-checkout"
        (broken / "data").mkdir(parents=True)
        (broken / ".git").write_text("gitdir: /nonexistent/worktree/metadata\n")  # the FILE form a worktree uses, pointing nowhere
        report = run_preflight(self.fx.config(observation_root=broken / "data"), "observe")
        self.assert_only_failure(report, "observation_root_local_only", "observation_root_git_unavailable")

    def test_the_question_is_put_to_the_innermost_repository_by_real_path(self):
        repo = self.make_repo()
        (repo / "ignored-data").mkdir()
        asked = []
        run_preflight(self.fx.config(observation_root=repo / "ignored-data"), "observe", is_ignored=lambda path, root: asked.append((path, root)) or True)
        self.assertEqual([(str(repo / "ignored-data"), str(repo))], asked)

    def test_the_legacy_observation_root_of_this_checkout_passes_because_git_ignores_it(self):
        legacy = REPO_ROOT / ".local-dev" / "observations"  # never created here: the rule is asked about the path, not the directory
        outcome = preflight._check_observation_local_only(self.fx.config(observation_root=legacy), preflight.git_is_ignored)
        self.assertTrue(outcome.ok, outcome.to_dict())


class ObservationRootWritableTest(PreflightCase):
    def test_a_root_that_does_not_exist_yet_passes_on_its_parent_and_is_not_created(self):
        target = self.fx.root / "later" / "observations"
        report = run_preflight(self.fx.config(observation_root=target), "observe")
        self.assertTrue(report.ok, [item.to_dict() for item in report.failed()])
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())

    def test_a_root_that_is_a_file_is_refused(self):
        target = self.fx.root / "observations-file"
        target.write_text("synthetic")
        self.assert_only_failure(run_preflight(self.fx.config(observation_root=target), "observe"), "observation_root_writable", "observation_root_not_writable")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes through permission bits")
    def test_a_read_only_root_is_refused_by_an_actual_write_probe(self):
        os.chmod(self.fx.observations, 0o500)
        self.addCleanup(os.chmod, self.fx.observations, 0o700)
        self.assert_only_failure(run_preflight(self.fx.config(), "observe"), "observation_root_writable", "observation_root_not_writable")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes through permission bits")
    def test_a_missing_root_under_a_read_only_parent_is_refused(self):
        parent = self.fx.root / "read-only-parent"
        parent.mkdir()
        os.chmod(parent, 0o500)
        self.addCleanup(os.chmod, parent, 0o700)
        self.assert_only_failure(run_preflight(self.fx.config(observation_root=parent / "observations"), "observe"), "observation_root_writable", "observation_root_not_writable")


class ReportTest(PreflightCase):
    def test_a_report_full_of_failures_names_no_path_address_target_or_id(self):
        self.fx.pose.write_text("{")
        (self.fx.template / "template.json").write_text("{")
        target = self.fx.root / "observations-file"
        target.write_text("synthetic")
        report = run_preflight(self.fx.config(observation_root=target, cameras=CameraConfig(wrist_id=WRIST_ID + "'")), PRIVATE_ADDRESS)
        text = json.dumps(report.to_dict())
        self.assertGreaterEqual(len(report.failed()), 5)
        for secret in (self.fx.tmp, PRIVATE_DIR, USER_NAME, PRIVATE_ADDRESS, SSH_TARGET, ROBOT_ID, WRIST_ID, "SN" + "7654321"):
            self.assertNotIn(secret, text)

    def test_a_passing_report_names_none_of_them_either_and_is_json(self):
        report = run_preflight(self.fx.config(), "cycle")
        text = json.dumps(report.to_dict())
        for secret in (self.fx.tmp, PRIVATE_DIR, USER_NAME, PRIVATE_ADDRESS, ROBOT_ID, WRIST_ID):
            self.assertNotIn(secret, text)
        self.assertEqual({"schema", "ok", "action_kind", "config_digest", "checks"}, set(report.to_dict()))
        self.assertEqual({"id", "ok", "reason_code", "detail"}, set(report.to_dict()["checks"][0]))

    def test_a_check_that_raises_is_a_failed_check_that_reports_the_exception_class_only(self):
        def explode(config):
            raise RuntimeError("could not open " + str(config.saved_pose_file))

        with mock.patch.object(preflight, "_check_saved_pose", explode):
            report = run_preflight(self.fx.config(), "cycle")
        self.assert_only_failure(report, "saved_pose", "check_error")
        self.assertIn("RuntimeError", check(report, "saved_pose").detail)
        self.assertNotIn(PRIVATE_DIR, check(report, "saved_pose").detail)
        self.assertEqual(11, len(report.checks))  # the others still ran

    def test_the_report_carries_the_config_digest_so_a_run_bundle_can_tie_the_two_together(self):
        from demo_runtime.config import config_digest
        config = self.fx.config()
        self.assertEqual(config_digest(config), run_preflight(config, "cycle").config_digest)


class InputDigestTest(PreflightCase):
    def test_the_digests_are_those_of_the_saved_pose_and_of_the_required_template_files_in_sorted_order(self):
        digests = input_digests(self.fx.config())
        self.assertEqual({"saved_pose_sha256", "pad_template_sha256"}, set(digests))
        self.assertEqual(hashlib.sha256(self.fx.pose.read_bytes()).hexdigest(), digests["saved_pose_sha256"])
        expected = hashlib.sha256()
        for name in sorted(("valid.png", "template.png", "template.json")):
            content = (self.fx.template / name).read_bytes()
            expected.update(name.encode("ascii") + b"\0" + struct.pack(">Q", len(content)) + content)
        self.assertEqual(expected.hexdigest(), digests["pad_template_sha256"])
        self.assertEqual(digests, input_digests(self.fx.config()))

    def test_a_change_in_any_input_moves_its_digest_and_only_its_digest(self):
        before = input_digests(self.fx.config())
        self.fx.pose.write_text(self.fx.pose.read_text() + "\n")
        after_pose = input_digests(self.fx.config())
        self.assertNotEqual(before["saved_pose_sha256"], after_pose["saved_pose_sha256"])
        self.assertEqual(before["pad_template_sha256"], after_pose["pad_template_sha256"])
        seen = {after_pose["pad_template_sha256"]}
        for name in REQUIRED_TEMPLATE_FILES:
            with open(self.fx.template / name, "ab") as handle:
                handle.write(b"\n")
            seen.add(input_digests(self.fx.config())["pad_template_sha256"])
        self.assertEqual(1 + len(REQUIRED_TEMPLATE_FILES), len(seen))
        (self.fx.template / "unrelated-note.txt").write_text("not a required file")
        self.assertIn(input_digests(self.fx.config())["pad_template_sha256"], seen)  # only the files the localizer reads count

    def test_bytes_cannot_move_between_two_files_without_moving_the_digest(self):
        (self.fx.template / "template.png").write_bytes(b"AAAA")
        (self.fx.template / "valid.png").write_bytes(b"BB")
        first = input_digests(self.fx.config())["pad_template_sha256"]
        (self.fx.template / "template.png").write_bytes(b"AAAAB")
        (self.fx.template / "valid.png").write_bytes(b"B")
        self.assertNotEqual(first, input_digests(self.fx.config())["pad_template_sha256"])

    def test_a_missing_input_raises_instead_of_digesting_nothing_and_names_no_path(self):
        (self.fx.template / "valid.png").unlink()
        with self.assertRaises(PreflightInputError) as caught:
            input_digests(self.fx.config())
        self.assertIn("valid.png", str(caught.exception))
        self.assertNotIn(PRIVATE_DIR, str(caught.exception))
        self.assertIsNone(caught.exception.__context__)


class MirrorPinTest(unittest.TestCase):
    """The preflight copies four facts from sources it must not import. Each is pinned to its source here."""

    def test_scan_joints_match_the_controllers(self):
        tree = ast.parse((REPO_ROOT / "autonomy" / "perception" / "scan_poses.py").read_text())
        values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SCAN_JOINTS" for t in node.targets)]
        self.assertEqual([SCAN_JOINTS], values)

    def test_the_controller_reads_the_saved_pose_through_the_key_the_preflight_checks(self):
        source = (REPO_ROOT / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py").read_text()
        self.assertIn('json.loads(HOME_FILE.read_text())["%s"]' % preflight.SAVED_POSE_KEY, source)

    def test_the_required_template_files_are_the_ones_the_localizer_opens(self):
        source = (REPO_ROOT / "autonomy" / "perception" / "pad_template_localizer.py").read_text()
        loader = source[source.index("def load_template("):source.index("\ndef ", source.index("def load_template(") + 1)]
        self.assertEqual(set(REQUIRED_TEMPLATE_FILES), set(re.findall(r'os\.path\.join\(key, "([^"]+)"\)', loader)))
        self.assertEqual(sorted(REQUIRED_TEMPLATE_FILES), list(REQUIRED_TEMPLATE_FILES))
        self.assertIn('meta["%s"]' % preflight.TEMPLATE_MATRIX_KEY, loader)
        self.assertIn('meta.get("%s"' % preflight.TEMPLATE_SIZE_KEY, loader)

    def test_the_action_allowlist_is_the_set_of_kinds_the_controller_dispatches_on(self):
        source = (REPO_ROOT / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py").read_text()
        main = source[source.index("def main("):]
        self.assertEqual({kind.value for kind in ActionKind}, set(re.findall(r'kind == "([a-z]+)"', main)))
        self.assertEqual(set(preflight.GRIP_CHOICES), set(re.findall(r'"(\w+)": GRIPPER_', main)))


class PiSshTargetOnTheRobot(PreflightCase):
    """A runtime ON THE ROBOT (cake.robot.link local) captures and probes on its own computer: an empty pi_ssh_target
    is not a missing identity there. A desktop runtime (the ssh_forward link, or no cake section) still needs it."""

    def cake(self, link: str) -> CakeConfig:
        robot = CakeRobotConfig(remote_admin_socket_path="" if link == "local" else "/run/example/remote.sock", local_admin_socket_path="/run/example/local.sock",
                                expected_build_identity="0" * 64, link=link)
        return CakeConfig(admin_probe_path="/opt/example/admin-probe", admin_socket_path="/run/example/local.sock", robot=robot)

    def test_an_empty_target_passes_on_the_robot_and_fails_on_the_desktop(self):
        local = run_preflight(self.fx.config(pi_ssh_target="", cake=self.cake("local")), "observe")
        self.assertTrue(check(local, "pi_ssh_target").ok, check(local, "pi_ssh_target").detail)
        for name, cake in (("ssh_forward", self.cake("ssh_forward")), ("no cake section", None)):
            with self.subTest(link=name):
                desktop = run_preflight(self.fx.config(pi_ssh_target="", cake=cake), "observe")
                self.assertEqual("pi_ssh_target_missing", check(desktop, "pi_ssh_target").reason_code)

    def test_a_set_target_is_still_checked_for_safety_on_the_robot(self):
        report = run_preflight(self.fx.config(pi_ssh_target="-oProxyCommand=x", cake=self.cake("local")), "observe")
        self.assertEqual("pi_ssh_target_unsafe", check(report, "pi_ssh_target").reason_code)


if __name__ == "__main__":
    unittest.main()
