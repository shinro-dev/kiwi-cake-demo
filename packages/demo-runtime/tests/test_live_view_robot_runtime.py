#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_live_view_robot_runtime.py: the robot-side runtime (the 8771 console's runtime, which
# runs ON the robot beside the resident, cake.robot.link local) shows the wrist preview when its configuration names a
# preview section. The camera service it reads binds the robot's own loopback, so the runtime subscribes to the service's
# preview port 5571 DIRECTLY and opens no ssh forward: a forward from the robot to itself would try to bind the very
# port the camera service holds. The configuration below has the shape of the robot runtime's configuration of
# 2026-09-26 (pi_ssh_target empty, tap absent, link local) with the preview section the operator adds.
# No camera, no robot, no ssh: the subscriber and the ssh spawn are replaced by recorders, and a spawn fails the test.
# Run directly: python3 packages/demo-runtime/tests/test_live_view_robot_runtime.py
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import daemon, forwards, sources  # noqa: E402
from demo_runtime.config import RuntimeConfig, robot_link_is_local  # noqa: E402
from demo_runtime.preflight import readiness_of  # noqa: E402

ROBOT_BUILD = "b" * 64


def camera_service_wrist_port() -> int:
    """The camera service's own wrist preview port, read from its source as text (no import, no camera library)."""
    text = (REPO_ROOT / "bin" / "pi" / "camera_service.py").read_text(encoding="utf-8")
    declared = re.search(r"DEFAULT_PREVIEW_PORTS = (\{[^}]*\})", text).group(1)
    return json.loads(declared.replace("'", '"'))["wrist"]


def robot_runtime_mapping(tmp: Path, *, preview=True) -> dict:
    raw = {"data_root": str(tmp / "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad", "observation_root": "observations",
           "host": {"address": "127.0.0.1", "robot_id": "example_robot"}, "pi_ssh_target": "",
           "cameras": {"wrist_id": "usb-test-wrist", "front_id": None},
           "cake": {"admin_probe_path": str(tmp / "admin-probe"),
                    "robot": {"link": "local", "local_admin_socket_path": str(tmp / "a.sock"), "expected_build_identity": ROBOT_BUILD}}}
    if preview:
        raw["preview"] = {"enabled": True, "wrist_port": 5571}
    return raw


class FakeLink:
    def start(self):
        return self

    def check(self):
        return forwards.CONNECTED

    def connected(self):
        return True

    def report(self):
        return {"state": forwards.CONNECTED, "reason": None, "since_wall_utc": "2026-09-26T10:00:00.000Z", "since_age_ms": 5.0,
                "starts": 1, "deaths": 0, "restarts": 0, "next_attempt_in_ms": None}

    def stop(self):
        pass


class FakeWatch:
    def start(self):
        return self

    def stop(self):
        pass

    def reading(self):
        return readiness_of("never_read", None)

    def host_pids(self):
        return None


class RecordingSubscriber:
    """Stands in for demo_runtime.preview_client.PreviewSubscriber: it records WHERE it was asked to connect and opens nothing."""

    endpoints: list = []

    def __init__(self, endpoint, role=None, **_options):
        self.endpoint, self.role = endpoint, role
        RecordingSubscriber.endpoints.append((role, endpoint))

    def open(self):
        pass

    def poll_once(self, timeout_ms=None):
        return False

    def latest(self):
        return None

    def stats(self):
        return {}

    def stop(self):
        pass


def refuse_spawn(*argv, **options):
    raise AssertionError("an ssh child was spawned by a runtime that runs on the robot")


class TheRobotRuntimeReadsThePreviewDirectly(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-live-view-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copy(PACKAGE_DIR / "tests" / "fake_robot_probe.py", self.tmp / "admin-probe")
        (self.tmp / "admin-probe").chmod(0o755)
        (self.tmp / "data").mkdir()
        RecordingSubscriber.endpoints = []

    def config(self, **options):
        return RuntimeConfig.from_mapping(robot_runtime_mapping(self.tmp, **options))

    def test_the_configuration_is_the_robot_side_one_and_the_wrist_resolves_to_the_camera_service_port(self):
        config = self.config()
        self.assertTrue(robot_link_is_local(config))
        self.assertEqual(camera_service_wrist_port(), 5571)
        self.assertEqual(sources.preview_roles(config), ("wrist",))
        self.assertEqual(sources.preview_endpoint(config, "wrist"), f"tcp://127.0.0.1:{camera_service_wrist_port()}")
        self.assertIsNone(sources.preview_endpoint(config, "front"), "a role the section does not name has no preview")

    def test_no_forward_is_configured_and_starting_them_spawns_nothing(self):
        config = self.config()
        built = forwards.Forwards.from_config(config)
        with mock.patch.object(forwards.subprocess, "Popen", side_effect=refuse_spawn):
            started = built.start()
        self.assertFalse(built.configured)
        self.assertEqual(set(started.values()), {False})
        for purpose, report in built.report().items():
            with self.subTest(purpose=purpose):
                self.assertEqual((report["configured"], report["child"], report["state"]), (False, False, forwards.NOT_CONFIGURED))
        self.assertIsNone(built.endpoint(forwards.preview_purpose("wrist")), "the subscriber is not pointed at a forward")

    def test_a_live_runtime_core_subscribes_to_5571_and_starts_no_forward(self):
        """The whole daemon path: the REAL SourceSupervisor.from_config and Forwards.from_config, in LIVE mode, on the
        robot-side configuration. Only the subscriber and the ssh spawn are recorders."""
        with mock.patch.object(sources, "PreviewSubscriber", RecordingSubscriber), \
                mock.patch.object(forwards.subprocess, "Popen", side_effect=refuse_spawn):
            core = daemon.RuntimeCore(data_dir=self.tmp / "data", mode=daemon.LIVE, config=self.config(), source_root=REPO_ROOT,
                                      robot_link=FakeLink(), robot_host_watch=FakeWatch(), readiness=FakeWatch(),
                                      controller_entry=daemon.FAKE_CONTROLLER_ENTRY)
            self.addCleanup(core.close)
            self.assertEqual(RecordingSubscriber.endpoints, [("wrist", f"tcp://127.0.0.1:{camera_service_wrist_port()}")])
            self.assertEqual(core.sources.roles(), ("wrist",))
            self.assertTrue(core.sources.preview.configured)
            report = core.forwards_report()
            self.assertTrue(report is None or all(not entry["child"] for entry in report.values()), report)
            self.assertTrue(report is None or all(entry["state"] == forwards.NOT_CONFIGURED for entry in report.values()), report)

    def test_without_the_section_the_robot_runtime_has_no_preview_and_says_so(self):
        """Today's robot configuration (preview null): the slot says not configured, which is what 8771 showed."""
        config = self.config(preview=False)
        self.assertEqual(sources.preview_roles(config), ())
        self.assertIsNone(sources.preview_endpoint(config, "wrist"))
        supervisor = sources.SourceSupervisor.from_config(object.__new__(_Hub), config)
        self.assertFalse(supervisor.preview.configured)
        self.assertEqual(supervisor.latest_preview()["reason"], "preview_not_configured")


class _Hub:
    def publish(self, stream, record):
        pass


if __name__ == "__main__":
    unittest.main()
