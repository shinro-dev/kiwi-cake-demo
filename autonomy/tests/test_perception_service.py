#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_perception_service.py: the desktop half of the offboard exception (bin/laptop/perception/
# perception_service.py). What must hold:
#   - every answer is computed by run_stage1_scan.perceive itself, looked up on that module, with the mission path's own
#     detector wrapping (CubeDetectionCache), and the bytes it answers with are the bytes a direct perceive writes;
#   - the service refuses, with an answer and a reason code, everything that is not this robot's request for a frame it
#     can decode: another protocol, another op, another model, code or template, a template changed since start, a frame
#     it cannot decode, a role the equivalence does not cover, a spent budget, a perception that raised; it never raises;
#   - it binds loopback only, and it carries no motion path at all.
# The detector is a fixed stand-in (no model); the frames are synthetic. No robot, no camera, no network beyond 127.0.0.1.
# Run directly: python3 autonomy/tests/test_perception_service.py
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("autonomy/tests", "autonomy/perception", "tools", "bin/laptop/perception"):
    sys.path.insert(0, str(ROOT / _sub))

import cv2  # noqa: E402

import cube_candidates as cc  # noqa: E402
import perception_service as ps  # noqa: E402
import remote_perception as rp  # noqa: E402
import run_stage1_scan  # noqa: E402
import test_cube_candidates as scenes  # noqa: E402  (the synthetic scene and the fixed stand-in detector)
from zero_shot_detector import CUBE_LABELS  # noqa: E402

REVISION = "0123456789abcdef0123456789abcdef01234567"
FILES_DIGEST = rp.model_files_digest({"config.json": "a" * 64})
ROBOT = {"resident_session": "0a" * 16, "run_id": "run-1", "mission_id": None, "target_id": None, "scan_id": "run-1", "controller_pid": 1}
NAME = scenes.observation_id(3)
CORNERS = scenes.TWO


def image_and_png():
    image = scenes.scene(CORNERS)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return image, encoded.tobytes()


def cube_only_detector():
    """The two squares' cube proposals and no pad proposal: no outline fit, no template, so the test stays fast."""
    return scenes.FixedDetector([d for d in scenes.proposals(CORNERS, [0.6, 0.45]) if d["label"] in CUBE_LABELS])


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-service-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.template = self.tmp / "no-template-here"
        patch = mock.patch.object(run_stage1_scan, "_PAD_TEMPLATE_DIR", self.template)  # what perceive reads the print from
        patch.start()
        self.addCleanup(patch.stop)
        digests = rp.code_digests(ROOT)
        self.identity = rp.PerceptionIdentity(digests["sources_sha256"], digests["perceive_sha256"], rp.template_digest(self.template), REVISION, FILES_DIGEST)
        self.inner = cube_only_detector()
        self.recorder = ps._Recording(self.inner)
        self.service = ps.PerceptionService(identity=self.identity, detector=cc.CubeDetectionCache(self.recorder), recorder=self.recorder,
                                            info={"model_name": "example-org/example-detector", "device": "cpu", "service_session": "s" * 8})
        self.image, self.png = image_and_png()

    def request(self, op=rp.OP_PERCEIVE, *, expect=None, capture=None, budget_ms=5000, **changes) -> dict:
        header = rp.new_request(op, robot=ROBOT, expect=expect or self.identity, budget_ms=budget_ms,
                                capture=None if op == rp.OP_PING else {"observation_id": NAME, "role": "wrist", "camera_service_session": None, "seq": None,
                                                                      "file_sha256": rp.sha256_hex(self.png), "frame_digest": cc.frame_digest(self.image), **(capture or {})})
        header.update(changes)
        return header

    def ask(self, header, payload=None, received_ns=None):
        parts = [header if isinstance(header, bytes) else rp.canonical(header)] + ([] if payload is None else [payload])
        answer = self.service.handle(parts, time.monotonic_ns() if received_ns is None else received_ns)
        return json.loads(answer[0]), answer

    def reason(self, header, payload=None) -> str:
        answered, parts = self.ask(header, payload)
        self.assertEqual(rp.STATUS_REFUSED, answered["status"])
        self.assertEqual(1, len(parts))
        return answered["reason_code"]


class TheComputationIsTheControllers(ServiceCase):
    def test_perceive_is_the_scan_modules_own_function_and_the_bytes_are_a_direct_perceives(self):
        original = run_stage1_scan.perceive
        with mock.patch.object(run_stage1_scan, "perceive", wraps=original) as watched:
            answered, parts = self.ask(self.request(), self.png)
        self.assertEqual(rp.STATUS_OK, answered["status"], answered)
        self.assertEqual(1, watched.call_count, "exactly one perceive per request")
        role, image, _out_dir, name, detector = watched.call_args.args
        self.assertEqual(("wrist", NAME), (role, name))
        self.assertIsInstance(detector, cc.CubeDetectionCache, "the mission path's own wrapping of the detector")
        self.assertEqual(self.image.tobytes(), image.tobytes(), "the decoded frame is the robot's pixels")
        direct = self.tmp / "direct"
        original("wrist", self.image, direct, NAME, cube_only_detector())
        self.assertEqual((direct / f"{NAME}.json").read_bytes(), parts[1])
        self.assertEqual((direct / f"{NAME}-overlay.png").read_bytes(), parts[2])

    def test_the_answer_states_what_it_received_computed_and_on_which_clock(self):
        header = self.request()
        answered, parts = self.ask(header, self.png)
        self.assertEqual((header["request_id"], header["robot"], header["capture"]), (answered["request_id"], answered["robot"], answered["capture"]))
        self.assertEqual({"file_sha256": rp.sha256_hex(self.png), "frame_digest": cc.frame_digest(self.image), "shape": [720, 1280, 3]}, answered["frame"])
        self.assertEqual(self.identity.to_wire(), answered["service"])
        result = answered["result"]
        self.assertEqual((rp.sha256_hex(parts[1]), rp.sha256_hex(parts[2]), 1), (result["sidecar_sha256"], result["overlay_sha256"], result["detect_calls"]))
        kept = [d for d in scenes.proposals(CORNERS, [0.6, 0.45]) if d["label"] in CUBE_LABELS]
        self.assertEqual(kept, result["cube_detections"], "the cube proposals the cache kept for this frame's digest")
        self.assertEqual(kept, result["detections"], "the detector's whole answer, as evidence")
        self.assertEqual("desktop_monotonic", answered["timing"]["clock"])
        self.assertGreaterEqual(answered["timing"]["perceive_ns"], 0)

    def test_the_recorder_passes_the_detectors_own_answer_through(self):
        answer = self.recorder.detect(self.image, list(CUBE_LABELS))
        self.assertIs(answer.__class__, list)
        self.assertEqual(answer, self.recorder.last)
        self.assertIsNot(answer, self.recorder.last)
        self.assertIsNone(self.recorder._model, "any other attribute is the wrapped detector's")

    def test_an_accepted_answer_passes_the_robots_acceptance(self):
        header = self.request()
        now = 5_000
        pending = rp.Outstanding(header=header, sent_ns=now, deadline_ns=now + 10**12, expect=self.identity,
                                 file_sha256=rp.sha256_hex(self.png), frame_digest=cc.frame_digest(self.image))
        _answered, parts = self.ask(header, self.png)
        accepted = rp.accept(parts, pending, now + 1)
        self.assertEqual(json.loads(parts[1]), accepted.record)


class TheServiceRefusesAndNeverRaises(ServiceCase):
    def test_what_is_not_a_request_of_this_protocol(self):
        for parts in ([], [b"\xff"], [b"[]"], [b"{}"]):
            with self.subTest(parts=parts):
                answered = json.loads(self.service.handle(parts, 0)[0])
                self.assertEqual((rp.STATUS_REFUSED, ps.REQUEST_MALFORMED), (answered["status"], answered["reason_code"]))
        self.assertEqual(ps.PROTOCOL_UNSUPPORTED, self.reason(self.request(v=2), self.png))
        self.assertEqual(ps.OP_UNKNOWN, self.reason(self.request(op="detect"), self.png))
        self.assertEqual(ps.REQUEST_MALFORMED, self.reason(self.request(request_id="short"), self.png))
        self.assertEqual(ps.REQUEST_MALFORMED, self.reason(self.request(robot="not an object"), self.png))
        partial = self.request()
        partial["expect"] = {"model_revision": REVISION}
        self.assertEqual(ps.REQUEST_MALFORMED, self.reason(partial, self.png))
        self.assertEqual(ps.REQUEST_MALFORMED, self.reason(self.request(), None))
        self.assertEqual(ps.REQUEST_MALFORMED, self.reason(self.request(budget_ms=0), self.png))
        self.assertEqual(ps.REQUEST_MALFORMED, self.reason(self.request(capture={"observation_id": "../x"}), self.png))

    def test_another_model_code_or_template_is_refused_with_its_own_code(self):
        wire = self.identity.to_wire()
        for change, code in (({"model_revision": "1" * 40}, rp.MODEL_MISMATCH), ({"model_files_digest": "sha256:" + "1" * 64}, rp.MODEL_MISMATCH),
                             ({"perceive_sha256": "1" * 64}, rp.CODE_MISMATCH), ({"template_sha256": "sha256:" + "1" * 64}, rp.TEMPLATE_MISMATCH)):
            with self.subTest(code=code):
                self.assertEqual(code, self.reason(self.request(expect=rp.PerceptionIdentity.from_wire({**wire, **change})), self.png))

    def test_a_template_changed_since_start_is_refused(self):
        self.template.mkdir()
        for name in rp.TEMPLATE_FILES:
            (self.template / name).write_bytes(name.encode())
        self.service.identity = self.identity = rp.PerceptionIdentity(self.identity.sources_sha256, self.identity.perceive_sha256,
                                                                     rp.template_digest(self.template), REVISION, FILES_DIGEST)
        (self.template / "template.json").write_bytes(b"changed on disk")
        self.assertEqual(ps.TEMPLATE_CHANGED, self.reason(self.request(), self.png))

    def test_a_frame_it_cannot_decode_a_front_frame_a_spent_budget_or_a_perception_that_raised(self):
        self.assertEqual(ps.FRAME_UNDECODABLE, self.reason(self.request(), b"not a png"))
        self.assertEqual(ps.FRAME_UNDECODABLE, self.reason(self.request(), b""))
        self.assertEqual(ps.ROLE_UNSUPPORTED, self.reason(self.request(capture={"role": "front"}), self.png))
        self.service._clock_ns = lambda: 5_000_000_000  # received at 0, perceive would start 5 s later: a 1 s budget is spent
        spent = json.loads(self.service.handle([rp.canonical(self.request(budget_ms=1000)), self.png], 0)[0])
        self.assertEqual(ps.BUDGET_SPENT, spent["reason_code"])
        self.service._clock_ns = lambda: 0
        with mock.patch.object(run_stage1_scan, "perceive", side_effect=RuntimeError("inside the frozen perception")):
            self.assertEqual(ps.PERCEPTION_RAISED, self.reason(self.request(), self.png))

    def test_a_ping_answers_the_services_identity_and_nothing_else(self):
        answered, parts = self.ask(self.request(rp.OP_PING))
        self.assertEqual((rp.STATUS_OK, 1, self.identity.to_wire()), (answered["status"], len(parts), answered["service"]))
        self.assertEqual(ps.REQUEST_MALFORMED, self.reason(self.request(rp.OP_PING), self.png), "a ping carries no frame")


class TheStartupChecks(unittest.TestCase):
    PIN = {"revision": REVISION, "files_sha256": {"config.json": "a" * 64}}

    def test_the_frozen_code_and_thresholds_pass_and_a_different_model_does_not(self):
        identity, problems = ps.identify(ROOT, None, self.PIN, self.PIN)
        self.assertEqual([], problems)
        self.assertEqual((REVISION, FILES_DIGEST, rp.TEMPLATE_ABSENT), (identity.model_revision, identity.model_files_digest, identity.template_sha256))
        _, problems = ps.identify(ROOT, None, self.PIN, {**self.PIN, "revision": "1" * 40})
        self.assertIn("the cached model revision is not the pinned one", problems)
        _, problems = ps.identify(ROOT, None, self.PIN, {**self.PIN, "files_sha256": {"config.json": "b" * 64}})
        self.assertIn("the cached model files are not the pinned ones", problems)
        with mock.patch.object(ps, "thresholds_now", lambda: {**ps.PINNED_THRESHOLDS, "DEFAULT_THRESHOLD": 0.1}):
            self.assertIn("thresholds differ from the benchmark's", ps.identify(ROOT, None, self.PIN, self.PIN)[1])

    def test_the_thresholds_the_modules_carry_are_the_benchmarks(self):
        self.assertEqual(ps.PINNED_THRESHOLDS, ps.thresholds_now())

    def test_a_model_pin_is_read_from_its_own_shape_or_from_the_harness_expectation(self):
        with tempfile.TemporaryDirectory() as directory:
            plain, harness, broken = (Path(directory) / name for name in ("pin.json", "expect.json", "broken.json"))
            plain.write_text(json.dumps(self.PIN))
            harness.write_text(json.dumps({"commit": "x", "model": self.PIN}))
            broken.write_text(json.dumps({"revision": REVISION}))
            self.assertEqual(self.PIN, ps.read_model_pin(plain))
            self.assertEqual(self.PIN, ps.read_model_pin(harness))
            with self.assertRaises(ValueError):
                ps.read_model_pin(broken)
            with mock.patch.dict(os.environ, {}), mock.patch.object(run_stage1_scan, "_PAD_TEMPLATE_DIR", None):
                self.assertEqual(3, ps.main(["--port", "0", "--template-dir", directory, "--model-pin", str(broken)]))
                self.assertIsNone(run_stage1_scan._PAD_TEMPLATE_DIR, "a refused start changed nothing the scan module reads")

    def test_the_socket_is_bound_on_loopback_only(self):
        import zmq

        context = zmq.Context()
        self.addCleanup(context.term)
        sock, port = ps.bind_loopback(context, 0)
        self.addCleanup(sock.close, 0)
        self.assertTrue(0 < port < 65536)
        self.assertTrue(sock.getsockopt(zmq.LAST_ENDPOINT).decode().startswith("tcp://127.0.0.1:"))
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        text = (ROOT / "bin" / "laptop" / "perception" / "perception_service.py").read_text()
        self.assertNotIn("tcp://*", text)
        self.assertNotIn("0.0.0.0", text)


class OverRealSockets(ServiceCase):
    def test_the_robots_client_accepts_what_the_service_answers_and_writes_the_same_bytes(self):
        import zmq

        context = zmq.Context()
        self.addCleanup(context.term)
        sock, port = ps.bind_loopback(context, 0)
        stop = threading.Event()
        worker = threading.Thread(target=self.service.serve, args=(sock, stop), daemon=True)
        worker.start()

        def shut():
            stop.set()
            worker.join(5)
            sock.close(0)

        self.addCleanup(shut)
        frame = self.tmp / "frame.png"
        frame.write_bytes(self.png)
        client = rp.RemotePerception(port=port, deadline_s=30.0, expect=self.identity, robot=ROBOT, context=context)
        client.ping()
        self.assertEqual(f"example-org/example-detector@{REVISION}", client.detections.model_identity)
        out = self.tmp / "robot-side"
        client.perceive(frame, out, NAME)
        direct = self.tmp / "direct"
        run_stage1_scan.perceive("wrist", self.image, direct, NAME, cube_only_detector())
        for suffix in (".json", "-overlay.png"):
            self.assertEqual((direct / f"{NAME}{suffix}").read_bytes(), (out / f"{NAME}{suffix}").read_bytes(), suffix)


class TheServiceCommandsNothing(unittest.TestCase):
    def test_no_robot_client_no_lease_no_motion_vocabulary(self):
        text = (ROOT / "bin" / "laptop" / "perception" / "perception_service.py").read_text()
        for word in ("send_action", "LeKiwiClient", "lease_client", "move_base", "x.vel", "torque", "robot_link", "cake_admin"):
            self.assertNotIn(word, text)
        text.encode("ascii")


if __name__ == "__main__":
    unittest.main()
