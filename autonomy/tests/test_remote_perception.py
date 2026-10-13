#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_remote_perception.py: the robot's side of the desktop perception (autonomy/perception/
# remote_perception.py). What must hold, against the operator's brief ("a missing, stale, mismatched or late response must
# not authorize movement"):
#   - accept() refuses every way an answer can be wrong, in the documented order, and accepts only the exact answer;
#   - with REAL pyzmq against a stand-in service on this machine's loopback: a missing answer ends at the robot's own
#     deadline, a late one is refused as late, a stop abandons the request within one poll slice, a cancel-exempt wait is
#     not abandoned, an answer to an abandoned request never reaches the next one, and an accepted answer is written as
#     exactly the bytes the service sent, where perceive writes them, and nothing at all is written for a refusal;
#   - the robot's expected identity refuses a tree that is not the measured perception.
# No robot, no camera, no model, no network beyond 127.0.0.1.
# Run directly: python3 autonomy/tests/test_remote_perception.py
import copy
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("autonomy/perception", "tools"):
    sys.path.insert(0, str(ROOT / _sub))

import cv2  # noqa: E402

import remote_perception as rp  # noqa: E402
from cube_candidates import frame_digest  # noqa: E402

REVISION = "0123456789abcdef0123456789abcdef01234567"
FILES_DIGEST = rp.model_files_digest({"config.json": "a" * 64})
DIGESTS = rp.code_digests(ROOT)
IDENTITY = rp.PerceptionIdentity(DIGESTS["sources_sha256"], DIGESTS["perceive_sha256"], rp.TEMPLATE_ABSENT, REVISION, FILES_DIGEST)
ROBOT = {"resident_session": "0a" * 16, "run_id": "console-20260925T120000Z-abcd", "mission_id": None, "target_id": None,
         "scan_id": "console-20260925T120000Z-abcd", "controller_pid": 4242}
NAME = "obs-20260925T120000Z-wrist-0001"
SIDECAR = json.dumps({"camera": "wrist", "image_size_px": [64, 48], "cube": {"detected": False}}, indent=2).encode()
OVERLAY = b"\x89PNG overlay bytes as the service read them"
CUBES = [{"label": "a yellow cube", "score": 0.61, "box_xyxy": [1.0, 2.0, 30.0, 40.0]}]


def png(seed: int = 0) -> bytes:
    image = np.random.default_rng(seed).integers(0, 255, (48, 64, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def outstanding(payload: bytes, *, now=10_000_000_000, deadline_s=5.0, op=rp.OP_PERCEIVE) -> rp.Outstanding:
    capture = None if op == rp.OP_PING else {"observation_id": NAME, "role": "wrist", "camera_service_session": None, "seq": None,
                                              "file_sha256": rp.sha256_hex(payload),
                                              "frame_digest": frame_digest(cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR))}
    header = rp.new_request(op, robot=ROBOT, expect=IDENTITY, budget_ms=int(deadline_s * 1000), capture=capture)
    return rp.Outstanding(header=header, sent_ns=now, deadline_ns=now + int(deadline_s * 1e9), expect=IDENTITY,
                          file_sha256=None if capture is None else capture["file_sha256"], frame_digest=None if capture is None else capture["frame_digest"])


def answer_for(request: dict, payload: bytes, *, identity=IDENTITY, sidecar=SIDECAR, overlay=OVERLAY, cubes=CUBES, status=rp.STATUS_OK, **header_changes) -> list:
    """What a correct service answers to `request` (the parsed header) for `payload`; `header_changes` then break it."""
    header = {"v": rp.PROTOCOL, "op": request["op"], "request_id": request["request_id"], "robot": copy.deepcopy(request["robot"]),
              "capture": copy.deepcopy(request.get("capture")), "status": status, "reason_code": None if status == rp.STATUS_OK else "stand_in_refusal",
              "service": identity.to_wire(), "service_info": {"model_name": "example-org/example-detector", "device": "cpu"}}
    if request["op"] == rp.OP_PING:
        header.update(header_changes)
        return [rp.canonical(header)]
    image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    header.update(frame={"file_sha256": rp.sha256_hex(payload), "frame_digest": frame_digest(image), "shape": list(image.shape)},
                  result={"summary": ["WRIST: stand-in perception"], "sidecar_sha256": rp.sha256_hex(sidecar), "overlay_sha256": rp.sha256_hex(overlay),
                          "cube_detections": cubes, "detections": cubes, "detect_calls": 1},
                  timing={"clock": "desktop_monotonic", "queue_ns": 1000, "perceive_ns": 12_000_000_000})
    header.update(header_changes)
    return [rp.canonical(header), sidecar, overlay]


class AcceptRefusesEveryWrongAnswer(unittest.TestCase):
    def setUp(self):
        self.payload = png()
        self.pending = outstanding(self.payload)
        self.request = self.pending.header
        self.now = self.pending.sent_ns + 1_000_000_000

    def refused(self, parts, now=None) -> rp.RemotePerceptionRefused:
        with self.assertRaises(rp.RemotePerceptionRefused) as caught:
            rp.accept(parts, self.pending, self.now if now is None else now)
        return caught.exception

    def test_the_exact_answer_is_accepted_with_its_record_summary_and_proposals(self):
        accepted = rp.accept(answer_for(self.request, self.payload), self.pending, self.now)
        self.assertEqual((json.loads(SIDECAR), SIDECAR, OVERLAY, ["WRIST: stand-in perception"]), (accepted.record, accepted.sidecar, accepted.overlay, accepted.summary))
        self.assertEqual(CUBES, accepted.cube_detections)
        self.assertIsNot(CUBES, accepted.cube_detections)

    def test_an_answer_after_the_deadline_is_late_whatever_it_says_and_lateness_is_checked_first(self):
        self.assertEqual(rp.LATE, self.refused(answer_for(self.request, self.payload), now=self.pending.deadline_ns + 1).reason_code)
        self.assertEqual(rp.LATE, self.refused(answer_for(self.request, self.payload, request_id="f" * 32), now=self.pending.deadline_ns + 1).reason_code)
        rp.accept(answer_for(self.request, self.payload), self.pending, self.pending.deadline_ns)  # AT the deadline is still in time

    def test_malformed_answers(self):
        good = answer_for(self.request, self.payload)
        for parts in ("not parts", [], [b"\xff\xfe"], [b"[1, 2]"], [rp.canonical({"v": 2})], good[:2], good + [b"extra"], [good[0], "text", good[2]]):
            with self.subTest(parts=str(parts)[:40]):
                self.assertEqual(rp.MALFORMED, self.refused(parts).reason_code)
        self.assertEqual(rp.MALFORMED, self.refused(answer_for(self.request, self.payload, op=rp.OP_PING)).reason_code)
        self.assertEqual(rp.MALFORMED, self.refused(answer_for(self.request, self.payload, service="not an identity")).reason_code)

    def test_another_request_another_robot_or_another_capture(self):
        self.assertEqual(rp.REQUEST_ID_MISMATCH, self.refused(answer_for(self.request, self.payload, request_id="0" * 32)).reason_code)
        for key, value in (("resident_session", "0b" * 16), ("controller_pid", 4243), ("run_id", "console-other"), ("target_id", "cube-b")):
            with self.subTest(robot=key):
                self.assertEqual(rp.ROBOT_MISMATCH, self.refused(answer_for(self.request, self.payload, robot={**ROBOT, key: value})).reason_code)
        for key, value in (("observation_id", "obs-other"), ("seq", 7), ("file_sha256", "0" * 64), ("frame_digest", "sha256:" + "0" * 64)):
            with self.subTest(capture=key):
                self.assertEqual(rp.CAPTURE_MISMATCH, self.refused(answer_for(self.request, self.payload, capture={**self.request["capture"], key: value})).reason_code)
        self.assertEqual(rp.CAPTURE_MISMATCH, self.refused(answer_for(self.request, self.payload, capture=None)).reason_code)

    def test_a_refusal_of_the_service_keeps_the_services_own_code(self):
        refusal = self.refused(answer_for(self.request, self.payload, status=rp.STATUS_REFUSED))
        self.assertEqual((rp.SERVICE_REFUSED, "stand_in_refusal"), (refusal.reason_code, refusal.service_reason))

    def test_another_model_another_code_or_another_template(self):
        for identity, code in ((rp.PerceptionIdentity(IDENTITY.sources_sha256, IDENTITY.perceive_sha256, IDENTITY.template_sha256, "1" * 40, FILES_DIGEST), rp.MODEL_MISMATCH),
                               (rp.PerceptionIdentity(IDENTITY.sources_sha256, IDENTITY.perceive_sha256, IDENTITY.template_sha256, REVISION, "sha256:" + "1" * 64), rp.MODEL_MISMATCH),
                               (rp.PerceptionIdentity({**IDENTITY.sources_sha256, "tools/static_world_observation.py": "1" * 64}, IDENTITY.perceive_sha256,
                                                      IDENTITY.template_sha256, REVISION, FILES_DIGEST), rp.CODE_MISMATCH),
                               (rp.PerceptionIdentity(IDENTITY.sources_sha256, "1" * 64, IDENTITY.template_sha256, REVISION, FILES_DIGEST), rp.CODE_MISMATCH),
                               (rp.PerceptionIdentity(IDENTITY.sources_sha256, IDENTITY.perceive_sha256, "sha256:" + "1" * 64, REVISION, FILES_DIGEST), rp.TEMPLATE_MISMATCH)):
            with self.subTest(code=code):
                self.assertEqual(code, self.refused(answer_for(self.request, self.payload, identity=identity)).reason_code)

    def test_bytes_or_pixels_the_robot_did_not_send(self):
        other = png(seed=1)
        self.assertEqual(rp.FRAME_FILE_MISMATCH, self.refused(answer_for(self.request, other)).reason_code)
        good = answer_for(self.request, self.payload)
        header = json.loads(good[0])
        header["frame"]["frame_digest"] = "sha256:" + "0" * 64
        self.assertEqual(rp.FRAME_DIGEST_MISMATCH, self.refused([rp.canonical(header)] + good[1:]).reason_code)

    def test_a_record_or_overlay_that_is_not_the_named_bytes_or_not_a_record(self):
        good = answer_for(self.request, self.payload)
        self.assertEqual(rp.PAYLOAD_MISMATCH, self.refused([good[0], SIDECAR + b" ", good[2]]).reason_code)
        self.assertEqual(rp.PAYLOAD_MISMATCH, self.refused([good[0], good[1], OVERLAY[:-1]]).reason_code)
        for sidecar in (b"not json", b"[1, 2]", b"\xff"):
            with self.subTest(sidecar=sidecar):
                self.assertEqual(rp.SIDECAR_UNREADABLE, self.refused(answer_for(self.request, self.payload, sidecar=sidecar)).reason_code)

    def test_a_summary_or_proposals_of_the_wrong_shape(self):
        good = answer_for(self.request, self.payload)
        header = json.loads(good[0])
        for result in ({**header["result"], "summary": "one line"}, {**header["result"], "cube_detections": [{"label": "a cube"}]},
                       {**header["result"], "cube_detections": "none"}):
            with self.subTest(result=str(result)[:60]):
                self.assertEqual(rp.MALFORMED, self.refused([rp.canonical({**header, "result": result})] + good[1:]).reason_code)
        accepted = rp.accept([rp.canonical({**header, "result": {**header["result"], "cube_detections": None}})] + good[1:], self.pending, self.now)
        self.assertIsNone(accepted.cube_detections, "no proposals kept is a frame the listing refuses, not an empty list")

    def test_a_ping_is_accepted_only_from_this_robots_service(self):
        ping = outstanding(b"", op=rp.OP_PING)
        rp.accept(answer_for(ping.header, b""), ping, ping.sent_ns + 1)
        other = rp.PerceptionIdentity(IDENTITY.sources_sha256, IDENTITY.perceive_sha256, IDENTITY.template_sha256, "1" * 40, FILES_DIGEST)
        with self.assertRaises(rp.RemotePerceptionRefused) as caught:
            rp.accept(answer_for(ping.header, b"", identity=other), ping, ping.sent_ns + 1)
        self.assertEqual(rp.MODEL_MISMATCH, caught.exception.reason_code)


class TheRobotsExpectation(unittest.TestCase):
    def test_a_tree_that_is_not_the_measured_perception_is_refused(self):
        with tempfile.TemporaryDirectory() as tree:
            for relative in list(rp.FROZEN_SOURCES_SHA256) + [rp.PERCEIVE_SOURCE]:
                (Path(tree) / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, Path(tree) / relative)
            rp.robot_identity(tree, None, model_revision=REVISION, model_files_digest_value=FILES_DIGEST)  # the copy is the measured code
            source = Path(tree) / "autonomy/perception/cube_recognizer.py"
            source.write_bytes(source.read_bytes() + b"\n")
            with self.assertRaises(rp.RemotePerceptionRefused) as caught:
                rp.robot_identity(tree, None, model_revision=REVISION, model_files_digest_value=FILES_DIGEST)
            self.assertEqual(rp.NOT_FROZEN, caught.exception.reason_code)
            self.assertIn("cube_recognizer", caught.exception.detail)

    def test_a_model_pin_that_is_not_a_revision_and_a_digest_is_refused(self):
        for revision, files in (("cfd3195", FILES_DIGEST), (REVISION, "a" * 64), ("", "")):
            with self.subTest(revision=revision), self.assertRaises(rp.RemotePerceptionRefused) as caught:
                rp.robot_identity(ROOT, None, model_revision=revision, model_files_digest_value=files)
            self.assertEqual(rp.CONFIG_INCOMPLETE, caught.exception.reason_code)

    def test_the_template_digest_names_the_three_files_the_localizer_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "pad-template"
            self.assertEqual(rp.TEMPLATE_ABSENT, rp.template_digest(template))
            self.assertEqual(rp.TEMPLATE_ABSENT, rp.template_digest(None))
            template.mkdir()
            for name in rp.TEMPLATE_FILES:
                (template / name).write_bytes(name.encode())
            first = rp.template_digest(template)
            (template / "unrelated.txt").write_text("not read by the localizer")
            self.assertEqual(first, rp.template_digest(template))
            (template / "valid.png").write_bytes(b"changed")
            self.assertNotEqual(first, rp.template_digest(template))
            (template / "valid.png").unlink()
            self.assertNotIn(rp.template_digest(template), (first, rp.TEMPLATE_ABSENT))

    def test_the_model_files_digest_does_not_depend_on_the_order_of_the_map(self):
        self.assertEqual(rp.model_files_digest({"b": "2", "a": "1"}), rp.model_files_digest({"a": "1", "b": "2"}))


class TheProposalsTheListingReads(unittest.TestCase):
    def test_no_model_runs_on_the_robot(self):
        with self.assertRaises(rp.RemotePerceptionRefused) as caught:
            rp.RemoteCubeDetections().detect(np.zeros((4, 4, 3), np.uint8), ["a yellow cube"])
        self.assertEqual(rp.MODEL_NOT_HERE, caught.exception.reason_code)

    def test_kept_by_digest_as_copies_bounded_and_none_means_not_seen(self):
        store = rp.RemoteCubeDetections(max_frames=2)
        store.remember("d1", CUBES)
        kept = store.cube_detections("d1")
        kept[0]["score"] = 0.0
        self.assertEqual(CUBES, store.cube_detections("d1"))
        store.remember("d2", [])
        store.remember("d3", CUBES)
        self.assertIsNone(store.cube_detections("d1"), "evicted, never detected again")
        self.assertEqual([], store.cube_detections("d2"))
        store.remember("d3", None)
        self.assertIsNone(store.cube_detections("d3"))


class StandInService:
    """A REP socket on this machine's loopback that answers with `respond(parts)` after `delay_s`, one request at a time,
    in its own thread. It is the transport's counterpart, not the service: bin/laptop/perception/perception_service.py has
    its own tests."""

    def __init__(self, test, respond, delay_s=0.0):
        import zmq

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.bind("tcp://127.0.0.1:0")
        self.port = int(self.socket.getsockopt(zmq.LAST_ENDPOINT).decode().rsplit(":", 1)[1])
        self.respond, self.delay_s, self.received = respond, delay_s, []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        test.addCleanup(self.close)

    def loop(self):
        import zmq

        poller = zmq.Poller()
        poller.register(self.socket, zmq.POLLIN)
        while not self.stop.is_set():
            if not dict(poller.poll(50)).get(self.socket):
                continue
            parts = self.socket.recv_multipart()
            self.received.append(json.loads(parts[0]))
            if self.stop.wait(self.delay_s):
                return
            self.socket.send_multipart(self.respond(parts))

    def close(self):
        self.stop.set()
        self.thread.join(5)
        self.socket.close(0)
        self.context.term()


def correct(parts):
    request = json.loads(parts[0])
    return answer_for(request, parts[1] if len(parts) > 1 else b"")


class TheClientOverRealSockets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-remote-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.frame = self.tmp / "frame.png"
        self.frame.write_bytes(png())
        self.out = self.tmp / "perception"

    def client(self, service, deadline_s=5.0, should_cancel=lambda: False):
        import zmq

        context = zmq.Context()
        self.addCleanup(context.term)
        return rp.RemotePerception(port=service.port, deadline_s=deadline_s, expect=IDENTITY, robot=ROBOT, should_cancel=should_cancel, context=context)

    def written(self):
        return sorted(path.name for path in self.out.iterdir() if not path.name.endswith(rp.EXCHANGE_SUFFIX)) if self.out.exists() else []

    def test_an_accepted_answer_is_written_as_the_services_bytes_where_perceive_writes_them(self):
        client = self.client(StandInService(self, correct))
        summary = client.perceive(self.frame, self.out, NAME, capture={"camera_service_session": "cam-1", "seq": 3})
        self.assertEqual(["WRIST: stand-in perception"], summary)
        self.assertEqual(SIDECAR, (self.out / f"{NAME}.json").read_bytes())
        self.assertEqual(OVERLAY, (self.out / f"{NAME}-overlay.png").read_bytes())
        self.assertEqual(CUBES, client.detections.cube_detections(frame_digest(cv2.imread(str(self.frame)))))
        exchange = json.loads((self.out / f"{NAME}{rp.EXCHANGE_SUFFIX}").read_text())
        self.assertTrue(exchange["accepted"])
        self.assertEqual(("cam-1", 3), (exchange["request"]["capture"]["camera_service_session"], exchange["request"]["capture"]["seq"]))
        self.assertEqual(ROBOT, exchange["request"]["robot"])
        self.assertEqual("robot_monotonic", exchange["timing"]["clock"])
        self.assertEqual("desktop_monotonic", exchange["reply"]["timing"]["clock"])

    def test_a_missing_answer_ends_at_the_robots_deadline_and_nothing_is_written(self):
        client = self.client(StandInService(self, correct, delay_s=2.0), deadline_s=0.3)
        started = time.monotonic()
        with self.assertRaises(rp.RemotePerceptionRefused) as caught:
            client.perceive(self.frame, self.out, NAME)
        self.assertEqual(rp.DEADLINE, caught.exception.reason_code)
        self.assertLess(time.monotonic() - started, 0.3 + 0.25)
        self.assertEqual([], self.written())
        self.assertFalse(json.loads((self.out / f"{NAME}{rp.EXCHANGE_SUFFIX}").read_text())["accepted"])

    def test_a_refused_answer_writes_no_result(self):
        client = self.client(StandInService(self, lambda parts: answer_for(json.loads(parts[0]), parts[1], request_id="0" * 32)))
        with self.assertRaises(rp.RemotePerceptionRefused) as caught:
            client.perceive(self.frame, self.out, NAME)
        self.assertEqual(rp.REQUEST_ID_MISMATCH, caught.exception.reason_code)
        self.assertEqual([], self.written())

    def test_a_stop_abandons_the_request_within_one_slice_and_nothing_is_written(self):
        flag = {"set": False, "at": None}

        def stop():
            flag["at"], flag["set"] = time.monotonic(), True

        client = self.client(StandInService(self, correct, delay_s=3.0), deadline_s=10.0, should_cancel=lambda: flag["set"])
        timer = threading.Timer(0.5, stop)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(rp.RemotePerceptionCancelled):
            client.perceive(self.frame, self.out, NAME)
        returned = time.monotonic()
        self.assertLess(returned - flag["at"], 0.2, "a stop waited longer than one poll slice")
        self.assertEqual([], self.written())
        self.assertEqual("cancelled_by_stop", json.loads((self.out / f"{NAME}{rp.EXCHANGE_SUFFIX}").read_text())["refusal"])

    def test_a_cancel_exempt_wait_is_not_abandoned(self):
        """The read-only re-observation after a base pulse: the controller's predicate is false for it, and at this level a
        wait that is not cancellable runs to its answer even while the predicate says stop."""
        client = self.client(StandInService(self, correct, delay_s=0.3), should_cancel=lambda: True)
        header = rp.new_request(rp.OP_PING, robot=ROBOT, expect=IDENTITY, budget_ms=5000)
        accepted, _ = client.exchange(header, None, cancellable=False)
        self.assertEqual(rp.STATUS_OK, accepted.header["status"])
        with self.assertRaises(rp.RemotePerceptionCancelled):
            client.exchange(rp.new_request(rp.OP_PING, robot=ROBOT, expect=IDENTITY, budget_ms=5000), None, cancellable=True)

    def test_a_late_answer_is_refused_as_late(self):
        service = StandInService(self, correct, delay_s=0.6)
        client = self.client(service, deadline_s=0.2)
        payload = self.frame.read_bytes()
        image = cv2.imread(str(self.frame))
        capture = {"observation_id": NAME, "role": "wrist", "camera_service_session": None, "seq": None, "file_sha256": rp.sha256_hex(payload), "frame_digest": frame_digest(image)}
        sock, pending = client.send(rp.new_request(rp.OP_PERCEIVE, robot=ROBOT, expect=IDENTITY, budget_ms=200, capture=capture), payload,
                                    file_sha256=capture["file_sha256"], frame_digest=capture["frame_digest"])
        try:
            self.assertIsNone(client.wait(sock, pending, cancellable=False), "nothing before the deadline")
            parts = client.wait(sock, pending, until_ns=time.monotonic_ns() + 3_000_000_000, cancellable=False)
        finally:
            sock.close(0)
        self.assertIsNotNone(parts, "the stand-in answered after the deadline")
        with self.assertRaises(rp.RemotePerceptionRefused) as caught:
            rp.accept(parts, pending, pending.received_ns)
        self.assertEqual(rp.LATE, caught.exception.reason_code)
        self.assertGreater(pending.received_ns, pending.deadline_ns)

    def test_an_answer_to_an_abandoned_request_never_reaches_the_next_one(self):
        service = StandInService(self, correct, delay_s=0.5)
        client = self.client(service, deadline_s=0.2)
        with self.assertRaises(rp.RemotePerceptionRefused):
            client.perceive(self.frame, self.out, NAME)
        patient = self.client(service, deadline_s=5.0)
        second = self.tmp / "second.png"
        second.write_bytes(png(seed=2))
        patient.perceive(second, self.out, "obs-20260925T120001Z-wrist-0001")
        first_id, second_id = (request["request_id"] for request in service.received)
        self.assertNotEqual(first_id, second_id, "every request has its own id")
        self.assertFalse((self.out / f"{NAME}.json").exists(), "the first request's answer was never written")

    def test_the_ping_accepts_this_robots_service_and_names_its_model(self):
        client = self.client(StandInService(self, correct))
        client.ping()
        self.assertEqual(f"example-org/example-detector@{REVISION}", client.detections.model_identity)
        wrong = rp.PerceptionIdentity(IDENTITY.sources_sha256, IDENTITY.perceive_sha256, "sha256:" + "2" * 64, REVISION, FILES_DIGEST)
        other = self.client(StandInService(self, lambda parts: answer_for(json.loads(parts[0]), b"", identity=wrong)))
        with self.assertRaises(rp.RemotePerceptionRefused) as caught:
            other.ping()
        self.assertEqual(rp.TEMPLATE_MISMATCH, caught.exception.reason_code)

    def test_only_wrist_frames_and_plain_observation_ids(self):
        client = self.client(StandInService(self, correct))
        for kwargs, name in (({"role": "front"}, NAME), ({}, "../escape"), ({}, "a/b")):
            with self.subTest(name=name, **kwargs), self.assertRaises(rp.RemotePerceptionRefused):
                client.perceive(self.frame, self.out, name, **kwargs)


class TheModuleCommandsNothing(unittest.TestCase):
    def test_no_motion_vocabulary_and_no_robot_client(self):
        text = (ROOT / "autonomy" / "perception" / "remote_perception.py").read_text()
        for word in ("send_action", "LeKiwiClient", "move_base", "x.vel", "torque", "lease"):
            self.assertNotIn(word, text)

    def test_ascii_only(self):
        (ROOT / "autonomy" / "perception" / "remote_perception.py").read_bytes().decode("ascii")


if __name__ == "__main__":
    unittest.main()
