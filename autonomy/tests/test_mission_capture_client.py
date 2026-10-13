#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_mission_capture_client.py: what the controller is allowed to believe about a mission capture.
#
# The defect class these exist for already happened here. On 2026-09-19 a shared directory name made scp nest into an
# existing directory, the OLD frame was read back as the new capture, and five observations stored the same image.
# Nothing failed and nothing said so. So every path where a WRONG frame could reach a caller is a refusal with a reason
# code, and a fresh frame is only ever the one the service says it just took, proven by its digest.
#
# No robot, no camera, no zmq, no ssh: the transport and the transfer are both injected.
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from autonomy.perception.mission_capture_client import (  # noqa: E402
    CaptureRefused, MissionCaptureClient, REQUIRED_HEIGHT, REQUIRED_WIDTH, endpoint_for, scp_fetch)

PNG = b"\x89PNG\r\n\x1a\n" + b"a mission frame" * 8


def metadata_for(data: bytes = PNG, **changes) -> dict:
    record = {"role": "wrist", "relative_path": "cs-session/000001-wrist-abcd1234.png", "encoding": "png",
              "width": REQUIRED_WIDTH, "height": REQUIRED_HEIGHT, "bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest(), "session": "cs-session",
              "source_stamp": {"session": "cs-session", "seq": 4, "clock_domain": "pi_monotonic"},
              "identical_to_previous_mission_frame": False}
    record.update(changes)
    return record


class Service:
    """The camera service's REP side, as this client sees it."""

    def __init__(self, reply=None, data: bytes = PNG):
        self.data = data
        self.reply = reply if reply is not None else {"ok": True, "provider": "camera_service", "service_session": "cs-session",
                                                      "frame": metadata_for(data), "lifecycle_record": {"role": "wrist", "frame_valid": True}}
        self.requests = []

    def request(self, payload, timeout_s):
        self.requests.append(payload)
        return self.reply

    def fetch(self, relative, destination):
        Path(destination).write_bytes(self.data)


class CaptureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-mission-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def client(self, service, **options):
        return MissionCaptureClient(request=service.request, fetch=service.fetch, local_dir=self.tmp, **options)


class AFreshFrame(CaptureCase):
    def test_a_capture_brings_the_frame_back_and_names_the_evidence_it_belongs_to(self):
        service = Service()
        frame = self.client(service).capture("wrist")
        self.assertTrue(frame.path.exists())
        self.assertEqual(frame.path.read_bytes(), PNG)
        self.assertEqual(frame.sha256, hashlib.sha256(PNG).hexdigest())
        self.assertEqual((frame.width, frame.height), (REQUIRED_WIDTH, REQUIRED_HEIGHT))
        self.assertEqual(frame.service_session, "cs-session")
        self.assertEqual(frame.seq, 4, "the frame carries the service's own sequence, so a record can name this capture")
        self.assertEqual(frame.source_stamp["clock_domain"], "pi_monotonic")

    def test_the_request_asks_for_the_properties_the_controller_s_perception_needs(self):
        service = Service()
        self.client(service).capture("wrist")
        asked = service.requests[-1]
        self.assertEqual(asked["op"], "capture")
        self.assertEqual(asked["role"], "wrist")
        self.assertEqual((asked["width"], asked["height"]), (REQUIRED_WIDTH, REQUIRED_HEIGHT))
        self.assertGreater(asked["settle_frames"], 0, "a settle depth is asked for, never left to whatever the service defaults to")

    def test_the_two_laptop_timings_are_separate_and_never_mixed_with_a_robot_stamp(self):
        frame = self.client(Service()).capture("wrist")
        self.assertGreaterEqual(frame.request_ns, 0)
        self.assertGreaterEqual(frame.transfer_ns, 0)
        self.assertEqual(frame.source_stamp["clock_domain"], "pi_monotonic", "the robot's stamp is passed through, not converted")

    def test_the_service_s_own_staleness_suspicion_is_passed_through_rather_than_recomputed(self):
        service = Service()
        service.reply = {**service.reply, "frame": metadata_for(identical_to_previous_mission_frame=True)}
        self.assertTrue(self.client(service).capture("wrist").identical_to_previous)


class NothingIsSubstituted(CaptureCase):
    """Every way a frame that is NOT this request's could reach a caller."""

    def test_a_digest_that_does_not_match_is_refused_and_the_file_is_removed(self):
        # The SAME LENGTH as the frame the service described, so the size check cannot be what catches it. This is the
        # 2026-09-19 shape exactly: a different image of the same size, arriving under the expected name.
        service = Service()
        stale = b"\x89PNG\r\n\x1a\n" + b"an older frame!" * 8
        self.assertEqual(len(stale), len(PNG), "the substitute must be indistinguishable by size")
        service.fetch = lambda relative, destination: Path(destination).write_bytes(stale)
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "frame_digest_mismatch")
        self.assertFalse((self.tmp / metadata_for()["relative_path"]).exists(), "a frame that could not be trusted is not left on disk")

    def test_a_truncated_copy_is_refused_before_the_digest_is_even_considered(self):
        service = Service()
        service.fetch = lambda relative, destination: Path(destination).write_bytes(PNG[:5])
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "frame_size_mismatch")

    def test_a_name_that_is_already_here_is_refused_rather_than_overwritten(self):
        # The 2026-09-19 shape: a second capture landing on the first one's name.
        service = Service()
        client = self.client(service)
        client.capture("wrist")
        with self.assertRaises(CaptureRefused) as caught:
            client.capture("wrist")
        self.assertEqual(caught.exception.reason_code, "frame_path_collision")

    def test_a_reply_that_names_another_camera_is_refused(self):
        service = Service()
        service.reply = {**service.reply, "frame": metadata_for(role="front")}
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "wrong_role_in_reply")

    def test_a_frame_of_the_wrong_size_is_refused_rather_than_fed_to_a_solver(self):
        service = Service()
        service.reply = {**service.reply, "frame": metadata_for(width=640, height=480)}
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "frame_properties_not_as_required")

    def test_a_lossy_encoding_is_refused(self):
        service = Service()
        service.reply = {**service.reply, "frame": metadata_for(encoding="jpeg")}
        with self.assertRaises(CaptureRefused):
            self.client(service).capture("wrist")

    def test_there_is_no_fallback_to_a_preview_frame(self):
        """A preview picture is a low-rate JPEG for a screen. Returning one for a requested observation would put a
        frame nobody asked for behind a measurement, so a refusal stays a refusal."""
        service = Service()
        service.reply = {"ok": False, "reason": "camera_lock_held", "detail": "another owner holds the device"}
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "camera_lock_held", "the service's OWN reason is kept, not rewritten")


class WhenTheServiceCannotAnswer(CaptureCase):
    def test_an_unreachable_service_is_an_error_with_a_reason_and_never_a_frame(self):
        service = Service()
        service.request = lambda payload, timeout_s: (_ for _ in ()).throw(TimeoutError("no answer"))
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "service_unreachable")

    def test_a_transfer_that_fails_is_an_error_and_not_a_half_written_frame(self):
        service = Service()
        service.fetch = lambda relative, destination: (_ for _ in ()).throw(OSError("scp died"))
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "frame_transfer_failed")

    def test_a_reply_without_a_frame_is_refused(self):
        service = Service()
        service.reply = {"ok": True, "provider": "camera_service"}
        with self.assertRaises(CaptureRefused) as caught:
            self.client(service).capture("wrist")
        self.assertEqual(caught.exception.reason_code, "no_frame_in_reply")

    def test_an_unknown_role_never_reaches_the_service(self):
        service = Service()
        with self.assertRaises(CaptureRefused):
            self.client(service).capture("elbow")
        self.assertEqual(service.requests, [])

    def test_counters_tell_the_two_apart(self):
        service = Service()
        client = self.client(service)
        client.capture("wrist")
        with self.assertRaises(CaptureRefused):
            client.capture("wrist")  # the collision
        self.assertEqual((client.captures_ok, client.captures_refused), (1, 1))


class TheTransport(CaptureCase):
    def test_the_mission_socket_is_always_loopback(self):
        self.assertTrue(endpoint_for().startswith("tcp://127.0.0.1:"))
        self.assertTrue(endpoint_for(5570).endswith(":5570"))

    def test_a_frame_path_from_a_reply_can_never_climb_out_of_the_store(self):
        fetch = scp_fetch("user@host", "camera-store")
        for hostile in ("/etc/passwd", "../../../etc/passwd", "cs/../../secret.png"):
            with self.subTest(hostile), self.assertRaises(ValueError):
                fetch(hostile, self.tmp / "out.png")

    def test_the_client_does_not_need_zmq_to_be_imported(self):
        """In a FRESH interpreter: reading sys.modules in this one would only say whether some other test imported zmq.

        It matters because the controller imports this module on the laptop, where zmq is only needed by the code that
        actually opens the socket."""
        import subprocess
        probe = "import sys; import autonomy.perception.mission_capture_client as m; print('zmq' in sys.modules)"
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False", "importing this module must not pull in zmq: it is imported where it is used")


if __name__ == "__main__":
    unittest.main(verbosity=2)
