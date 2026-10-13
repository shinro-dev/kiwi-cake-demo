#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_offboard_perception_probe.py: the no-motion proof of the desktop perception (bin/pi/
# offboard_perception_probe.py), as a DRY RUN on this machine: the real service in a thread on loopback with a stand-in
# detector that takes a second, frames from files, every case run for real over pyzmq. What must hold:
#   - normal: every frame accepted, the round trip measured on this side's clock and the service's perceive time reported
#     apart from it, the listing's pass 1 reproducing the record, and the rows written in the benchmark harness's format;
#   - replay, missing, timeout, late: each refused with its own code and nothing written as a result;
#   - stop: the SIGINT returns the call within the bound and the next request is accepted;
#   - on the robot it refuses before any case unless the resident reports the host held, and it never runs without the
#     resident's identity pin; a listener beyond loopback refuses too.
# No robot, no camera, no model, no network beyond 127.0.0.1.
# Run directly: python3 autonomy/tests/test_offboard_perception_probe.py
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("autonomy/tests", "bin/pi", "bin/laptop/perception", "autonomy/perception", "tools", "packages/demo-runtime"):
    sys.path.insert(0, str(ROOT / _sub))

import cv2  # noqa: E402

import offboard_perception_probe as probe  # noqa: E402
import remote_perception as rp  # noqa: E402
import run_stage1_scan  # noqa: E402
import static_world_observation as swo  # noqa: E402
import test_cube_candidates as scenes  # noqa: E402
import test_cycle_remote_perception as remote  # noqa: E402  (ServiceThread: the real service in a thread)
from zero_shot_detector import CUBE_LABELS  # noqa: E402

PIN = {"revision": remote.REVISION, "files_sha256": {"config.json": "a" * 64, "model.safetensors": "b" * 64}}


class SlowDetector:
    """The two squares' cube proposals, after `delay_s`: a desktop perceive that takes long enough to be timed out."""

    _model = None

    def __init__(self, delay_s):
        self.delay_s, self.inner = delay_s, scenes.FixedDetector([d for d in scenes.proposals(scenes.TWO, [0.6, 0.45]) if d["label"] in CUBE_LABELS])

    def detect(self, image, labels, *args, **kwargs):
        time.sleep(self.delay_s)
        return self.inner.detect(image, labels)


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-probe-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch.object(run_stage1_scan, "_PAD_TEMPLATE_DIR", None)  # both sides read the print from the scan module's default
        patch.start()
        self.addCleanup(patch.stop)
        self.frames = self.tmp / "frames"
        self.frames.mkdir()
        for number, corners in enumerate(([(400, 380), (820, 420)], [(300, 300)], [(700, 200), (200, 500)]), start=1):
            cv2.imwrite(str(self.frames / f"{scenes.observation_id(number)}.png"), scenes.scene(corners))
        self.pin = self.tmp / "pin.json"
        self.pin.write_text(json.dumps(PIN))

    def argv(self, port, *extra):
        return ["--port", str(port), "--deadline-s", "20", "--short-deadline-s", "0.3", "--late-drain-s", "20", "--stop-after-s", "0.4",
                "--model-pin", str(self.pin), "--template-dir", str(swo.TEMPLATE_DIR), "--evidence-dir", str(self.tmp / "evidence"), *extra]


@unittest.skipUnless(shutil.which("ss"), "the probe reads the listener from the kernel with ss, which this machine lacks")
class TheDryRunOnThisMachine(ProbeCase):
    def test_every_case_gives_its_expected_verdict(self):
        service = remote.ServiceThread(self, SlowDetector(1.0))
        status = probe.main(self.argv(service.port, "--frames-from", str(self.frames)))
        result = json.loads((self.tmp / "evidence" / "probe-result.json").read_text())
        self.assertEqual(0, status, json.dumps(result["cases"], indent=1, default=str)[:4000])
        self.assertTrue(result["dry_run"])
        self.assertEqual(["127.0.0.1"], result["listener"]["addresses"])
        normal = result["cases"]["normal"]
        self.assertEqual(3, len(normal["frames"]))
        self.assertTrue(all(row["accepted"] and row["robot_originated"] is False for row in normal["frames"]))
        self.assertTrue(normal["listing_pass1_all_match"], "the listing on this side reproduces the service's record")
        self.assertGreaterEqual(normal["full_round_trip_robot_clock"]["p50_ms"], 1000.0, "the round trip contains the desktop's second")
        self.assertGreaterEqual(normal["desktop_perceive_desktop_clock"]["p50_ms"], 1000.0)
        self.assertEqual(["request_id_mismatch", "capture_mismatch"], result["cases"]["replay"]["got"])
        for case in ("missing", "timeout"):
            self.assertEqual((rp.DEADLINE, []), (result["cases"][case]["got"], result["cases"][case]["results_written"]), case)
        self.assertEqual((rp.LATE, []), (result["cases"]["late"]["got"], result["cases"]["late"]["results_written"]))
        stop = result["cases"]["stop"]
        self.assertEqual(("cancelled", "accepted", []), (stop["got"], stop["next_request"], stop["results_written"]))
        self.assertLess(stop["signal_to_return_ms"], probe.STOP_BOUND_S * 1000)
        rows = json.loads((self.tmp / "evidence" / "service-rows.json").read_text())
        self.assertEqual(sorted(path.name for path in self.frames.glob("*.png")), sorted(row["frame"] for row in rows["passes"][0]))
        self.assertTrue(all(isinstance(row["record"], dict) and row["detections"] for row in rows["passes"][0]))

    def test_an_evidence_directory_that_is_not_empty_is_refused(self):
        (self.tmp / "evidence").mkdir()
        (self.tmp / "evidence" / "old.json").write_text("{}")
        self.assertEqual(5, probe.main(self.argv(1, "--frames-from", str(self.frames))))

    def test_nothing_listening_is_refused_before_any_case(self):
        status = probe.main(self.argv(probe.free_loopback_port(), "--frames-from", str(self.frames)))
        self.assertEqual(3, status)
        self.assertIn("nothing listens", json.loads((self.tmp / "evidence" / "probe-result.json").read_text())["refused"])


class OnTheRobot(ProbeCase):
    ROBOT = ["--admin-probe", "/opt/example/admin-probe", "--admin-socket", "/run/example/admin.sock", "--expected-build-identity", "cd" * 32]

    def test_it_never_runs_without_the_residents_identity_pin(self):
        with self.assertRaises(SystemExit):
            probe.main(self.argv(1))

    def test_a_host_that_is_not_held_refuses_before_any_case(self):
        from demo_runtime import robot_link

        for binding in ({"session_uuid": "0a" * 16, "host_running": True, "reason_code": None}, {"session_uuid": None, "host_running": None, "reason_code": "robot_unreachable"},
                        {"session_uuid": "0a" * 16, "host_running": None, "reason_code": "health_unavailable"}):
            with self.subTest(binding=binding), mock.patch.object(robot_link, "read_binding", return_value=binding) as read:
                shutil.rmtree(self.tmp / "evidence", ignore_errors=True)
                self.assertEqual(3, probe.main(self.argv(1, *self.ROBOT)))
                self.assertEqual(1, read.call_count, "the resident is read before anything else")
                self.assertIn("held", json.loads((self.tmp / "evidence" / "probe-result.json").read_text())["refused"])

    def test_a_listener_beyond_loopback_refuses(self):
        from demo_runtime import robot_link

        held = {"session_uuid": "0a" * 16, "host_running": False, "reason_code": "supervisor_child_not_running"}
        with mock.patch.object(robot_link, "read_binding", return_value=held), mock.patch.object(probe, "listeners", return_value={"readable": True, "addresses": ["0.0.0.0"], "processes": [None]}):
            self.assertEqual(3, probe.main(self.argv(5590, *self.ROBOT)))
        self.assertIn("beyond loopback", json.loads((self.tmp / "evidence" / "probe-result.json").read_text())["refused"])


class TheListenerTable(unittest.TestCase):
    def table(self, text):
        return lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=text, stderr="")

    def test_addresses_on_the_port_and_only_on_the_port(self):
        text = ("LISTEN 0 128 127.0.0.1:5590 0.0.0.0:* users:((\"sshd\",pid=10,fd=9))\n"
                "LISTEN 0 128 [::1]:5590 [::]:*\n"
                "LISTEN 0 128 0.0.0.0:55900 0.0.0.0:*\n"
                "LISTEN 0 128 *:5591 *:*\n")
        seen = probe.listeners(5590, run=self.table(text))
        self.assertEqual({"readable": True, "addresses": ["127.0.0.1", "[::1]"], "processes": ["sshd", None]}, seen)
        self.assertEqual(["*"], probe.listeners(5591, run=self.table(text))["addresses"])

    def test_an_unreadable_table_is_said_to_be_unreadable(self):
        def fail(*_a, **_k):
            raise OSError("no ss")

        self.assertEqual({"readable": False, "addresses": [], "processes": []}, probe.listeners(5590, run=fail))


class TheProbeCommandsNothing(unittest.TestCase):
    def test_no_robot_client_no_lease_no_motion(self):
        text = (ROOT / "bin" / "pi" / "offboard_perception_probe.py").read_text()
        for word in ("send_action", "LeKiwiClient", "lease_client", "move_base", "x.vel", "torque", "cake_activation"):
            self.assertNotIn(word, text)
        text.encode("ascii")


if __name__ == "__main__":
    unittest.main()
