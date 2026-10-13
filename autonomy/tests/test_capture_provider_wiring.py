# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_capture_provider_wiring.py: which code opens the camera, asked of the production selection path.
#
# `test_mission_capture_client.py` covers the client in isolation. It passed for days while NOTHING called the client:
# `capture_provider` was validated in the configuration, digested, reported on every snapshot, and consumed by no code
# at all, so a runtime configured for the camera service ran the legacy per-capture probe and a green suite said
# nothing about it. These tests ask the other question: does naming the provider actually change which class opens the
# camera, and does the legacy name still produce the command line every recorded run was produced with.

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "perception"))
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))
sys.path.insert(0, str(ROOT / "packages" / "demo-runtime"))

import run_stage1_scan as cli  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402
from demo_runtime.config import CaptureProvider  # noqa: E402


def observe_command():
    return {"type": "observe", "args": {"holding": False}}


class TheFactoryChoosesOneProvider(unittest.TestCase):
    def test_the_default_is_the_legacy_path(self):
        with mock.patch.object(cli, "PiCamera") as legacy, mock.patch.object(cli, "ServiceCamera") as managed:
            cli.make_camera("target", Path("/tmp"))
        legacy.assert_called_once()
        managed.assert_not_called()

    def test_naming_the_service_reaches_the_service_and_never_opens_the_legacy_camera(self):
        with mock.patch.object(cli, "PiCamera") as legacy, mock.patch.object(cli, "ServiceCamera") as managed:
            cli.make_camera("target", Path("/tmp"), cli.CAPTURE_PROVIDER_SERVICE, mission_port=5570)
        managed.assert_called_once()
        self.assertEqual(managed.call_args.kwargs.get("mission_port"), 5570)
        # The one that must NOT happen: the legacy probe opens a device the service holds the lock on.
        legacy.assert_not_called()

    def test_an_unknown_provider_is_refused_rather_than_quietly_legacy(self):
        with self.assertRaises(ValueError) as caught:
            cli.make_camera("target", Path("/tmp"), "something_else")
        self.assertIn("capture provider must be one of", str(caught.exception))


class TheServiceCameraReportsRefusalsWithoutFallingBack(unittest.TestCase):
    """A refusal is a capture that produced no frame, carrying the service's OWN reason. It is never a reason to open
    the camera another way: the service holds the lock, and a fallback would both fail and hide why."""

    def _camera(self, capture):
        camera = cli.ServiceCamera.__new__(cli.ServiceCamera)  # no forward, no ssh, no zmq: this is the mapping under test
        camera._client = mock.Mock(settle_frames=6, capture=capture)
        camera.last_timing = None
        return camera

    def test_a_refused_capture_becomes_a_capture_without_a_frame(self):
        from mission_capture_client import CaptureRefused

        camera = self._camera(mock.Mock(side_effect=CaptureRefused("camera_lock_lost", "the lock is not ours any more")))
        result = camera.capture("wrist")
        self.assertIsNone(result.frame)
        self.assertFalse(result.record.frame_valid)
        self.assertIn("camera_lock_lost", result.record.error)
        # No timing is published for a capture that did not happen: the previous capture's numbers must not be read as
        # this one's.
        self.assertIsNone(camera.last_timing)

    def test_a_refusal_never_reaches_the_legacy_camera(self):
        from mission_capture_client import CaptureRefused

        camera = self._camera(mock.Mock(side_effect=CaptureRefused("service_unreachable", "no answer")))
        with mock.patch.object(cli, "PiCamera") as legacy:
            camera.capture("wrist")
        legacy.assert_not_called()

    def test_a_returned_frame_carries_the_service_lifecycle_record_and_its_two_timings(self):
        frame = mock.Mock(path=Path("/nonexistent/frame.png"), request_ns=11, transfer_ns=22,
                          lifecycle_record={"role": "wrist", "identity_fingerprint": "camera_service",
                                            "settle_frames_requested": 6, "frames_read": 7, "open_ok": True})
        camera = self._camera(mock.Mock(return_value=frame))
        with mock.patch.object(cli.cv2, "imread", return_value=None):
            result = camera.capture("wrist")
        self.assertEqual(camera.last_timing, {"role": "wrist", "capture_ns": 11, "transfer_ns": 22})
        self.assertEqual(result.record.frames_read, 7)
        self.assertTrue(result.record.open_ok)
        # The frame verified and arrived but could not be decoded here. That is a capture without a usable frame and it
        # says so, rather than being reported as a successful capture of nothing.
        self.assertFalse(result.record.frame_valid)
        self.assertIn("could not be decoded", result.record.error)


class TheSupervisorPassesTheProviderItIsConfiguredWith(unittest.TestCase):
    def test_the_legacy_command_line_is_unchanged(self):
        """THE PROVEN ARGV. Every recorded run was produced by this command line and it must not gain an argument."""
        before = sv.controller_arguments(observe_command(), "scan-1", address="192.0.2.1", robot_id="r", pi_ssh="t")
        after = sv.controller_arguments(observe_command(), "scan-1", address="192.0.2.1", robot_id="r", pi_ssh="t",
                                        capture_provider=CaptureProvider.LEGACY_PER_CAPTURE.value, mission_port=5570)
        self.assertEqual(before, after)
        self.assertNotIn("--capture-provider", after)
        self.assertEqual(after[-2:], ["--scan-id", "scan-1"])

    def test_the_service_provider_is_appended_with_its_port(self):
        argv = sv.controller_arguments(observe_command(), "scan-1", address="192.0.2.1", robot_id="r", pi_ssh="t",
                                       capture_provider=CaptureProvider.CAMERA_SERVICE.value, mission_port=5570)
        self.assertIn("--capture-provider", argv)
        self.assertEqual(argv[argv.index("--capture-provider") + 1], "camera_service")
        self.assertEqual(argv[argv.index("--mission-port") + 1], "5570")

    def test_the_controller_accepts_what_the_supervisor_sends(self):
        """THE TWO HALVES, CHECKED AGAINST EACH OTHER.

        The supervisor builds the argv and the controller parses it, in two files that nothing else compares. An
        argument one side sends and the other rejects would surface as a controller that exits 2 the first time the
        camera service is configured, which is on the robot, during a handover. The controller's real parser is asked
        here, in the process it actually runs in.
        """
        argv = sv.controller_arguments(observe_command(), "scan-1", address="192.0.2.1", robot_id="r", pi_ssh="t",
                                       capture_provider=CaptureProvider.CAMERA_SERVICE.value, mission_port=5570)
        import subprocess

        entry = ROOT / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py"
        helped = subprocess.run([sys.executable, str(entry), "--help"], capture_output=True, text=True, timeout=120,
                                env={**os.environ, "KC_PI_SSH": "target"})
        self.assertEqual(helped.returncode, 0, helped.stderr[-800:])
        self.assertIn("--capture-provider", helped.stdout)
        self.assertIn("--mission-port", helped.stdout)
        # The choices the supervisor can emit are the choices the controller accepts.
        self.assertIn(CaptureProvider.CAMERA_SERVICE.value, helped.stdout)
        self.assertIn(CaptureProvider.LEGACY_PER_CAPTURE.value, helped.stdout)
        # And the supervisor really does emit them, so the two assertions above are about the same argv.
        self.assertIn("--capture-provider", argv)
        self.assertIn("--mission-port", argv)


if __name__ == "__main__":
    unittest.main()
