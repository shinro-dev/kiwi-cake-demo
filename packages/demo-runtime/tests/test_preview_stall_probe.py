#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_preview_stall_probe.py: tools/preview_stall_probe.py samples, once a second, what the
# runtime's preview slot says (state, reason, the kept frame's number and receive age, skipped_for_mission), what the
# runtime's status says about that camera (yield notices, the service counters) and, when given the camera service's
# loopback endpoint, what the service's own stream says; each sample is one JSON line in a local file. It is READ-ONLY:
# the runtime is asked only preview (without the picture), status and mission_state, never a mutating op and never a
# handshake, and the camera service is read through a SUB socket that cannot send. A runtime that is not there is a
# sample with a reason code, never a crash. No robot, no camera, no port outside this test's own unix socket.
# Run directly: python3 packages/demo-runtime/tests/test_preview_stall_probe.py
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime.rpc import MUTATING_OPS  # noqa: E402


def load_probe():
    spec = importlib.util.spec_from_file_location("preview_stall_probe_under_test", REPO_ROOT / "tools" / "preview_stall_probe.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = load_probe()

SESSION = "cs-20260927T000000Z-0a1b2c3d"


def preview_answer(state="connected", reason=None, seq=7, skipped=12, receive_age_ms=180.0):
    connected = state == "connected"
    return {"ok": connected, "available": connected, "state": state, "reason": reason, "role": "wrist", "seq": seq, "source_seq": seq * 5,
            "capture_clock": {"session": SESSION, "seq": seq, "sample_mono_ns": 1, "clock_domain": "pi_monotonic"} if connected else None,
            "receive_age_ms": receive_age_ms, "measured_fps": 3.9 if connected else None, "gaps": 0 if connected else None,
            "skipped_for_mission": skipped if connected else None, "frame": {"width_px": 320, "height_px": 180} if connected else None}


def status_answer(yield_notices=2, skipped=12):
    return {"ok": True, "sources": {"previews": {"wrist": {"configured": True, "role": "wrist", "state": "connected", "reason": None,
                                                          "open_failures": 0, "transport_faults": 0, "yield_notices": yield_notices,
                                                          "service": {"published": 90, "skipped_for_mission": skipped, "dropped_at_socket": 0}}}}}


class FakeRuntime:
    def __init__(self, previews, statuses=None, mission=None):
        self.previews, self.statuses, self.mission, self.ops = list(previews), list(statuses or []), mission, []

    def call(self, request):
        self.ops.append(request)
        op = request["op"]
        if op == "preview":
            return self.previews.pop(0) if self.previews else preview_answer()
        if op == "status":
            return self.statuses.pop(0) if self.statuses else status_answer()
        if op == "mission_state":
            return self.mission or {"ok": False, "reason_code": "no_mission_recorded"}
        raise AssertionError(f"the probe asked for {op}")


class FakeClock:
    def __init__(self):
        self.mono_ns = 5_000_000_000

    def mono(self):
        return self.mono_ns

    def sleep(self, seconds):
        self.mono_ns += int(seconds * 1e9)


class Sampling(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-stall-probe-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def lines(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_one_line_a_sample_and_a_summary_that_counts_the_states(self):
        runtime = FakeRuntime([preview_answer(), preview_answer(reason="paused_for_decision_capture", seq=7, receive_age_ms=1400.0),
                               preview_answer(state="stalled", reason="no_sample_within_window"), preview_answer(seq=9)])
        clock = FakeClock()
        out = self.tmp / "probe.jsonl"
        code = probe.run(role="wrist", samples=4, interval_s=1.0, out_path=out, runtime=runtime.call, service=None,
                         clock_ns=clock.mono, sleep=clock.sleep, wall=lambda: "2026-09-27T08:00:00Z")
        self.assertEqual(code, 0)
        lines = self.lines(out)
        self.assertEqual(len(lines), 5, "four samples and one summary")
        first = lines[0]
        self.assertEqual(first["probe"], probe.PROBE_SCHEMA)
        self.assertEqual((first["i"], first["t_mono_ns"], first["clock_domain"]), (0, 5_000_000_000, "laptop_monotonic"))
        self.assertEqual(first["runtime"]["preview"], {"state": "connected", "reason": None, "available": True, "seq": 7, "source_seq": 35,
                                                       "session": SESSION, "receive_age_ms": 180.0, "measured_fps": 3.9, "gaps": 0,
                                                       "skipped_for_mission": 12})
        self.assertEqual(first["runtime"]["camera"], {"state": "connected", "reason": None, "yield_notices": 2, "transport_faults": 0,
                                                      "open_failures": 0, "service": {"published": 90, "skipped_for_mission": 12, "dropped_at_socket": 0}})
        self.assertEqual(first["runtime"]["mission"], {"reason_code": "no_mission_recorded"})
        self.assertEqual([line["t_mono_ns"] - first["t_mono_ns"] for line in lines[:4]], [0, 1_000_000_000, 2_000_000_000, 3_000_000_000])
        summary = lines[-1]["summary"]
        self.assertEqual(summary["samples"], 4)
        self.assertEqual(summary["runtime_states"], {"connected": 3, "stalled": 1})
        self.assertEqual(summary["paused_samples"], 1)
        self.assertEqual(summary["longest_not_connected_run"], 1)

    def test_only_reads_are_asked_and_never_a_picture(self):
        runtime = FakeRuntime([])
        clock = FakeClock()
        probe.run(role="wrist", samples=3, interval_s=1.0, out_path=self.tmp / "p.jsonl", runtime=runtime.call, service=None,
                  clock_ns=clock.mono, sleep=clock.sleep, wall=lambda: "x")
        asked = {request["op"] for request in runtime.ops}
        self.assertEqual(asked, {"preview", "status", "mission_state"})
        self.assertFalse(asked & MUTATING_OPS)
        self.assertTrue(all(request.get("with_bytes") is False for request in runtime.ops if request["op"] == "preview"))

    def test_a_runtime_that_is_not_there_is_a_reason_not_a_crash(self):
        def absent(request):
            raise probe.RuntimeUnavailable("the runtime socket cannot be reached")

        clock = FakeClock()
        out = self.tmp / "p.jsonl"
        self.assertEqual(probe.run(role="wrist", samples=2, interval_s=1.0, out_path=out, runtime=absent, service=None,
                                   clock_ns=clock.mono, sleep=clock.sleep, wall=lambda: "x"), 0)
        lines = self.lines(out)
        self.assertEqual(lines[0]["runtime"], {"error": "runtime_unavailable"})
        self.assertEqual(lines[-1]["summary"]["runtime_states"], {"runtime_unavailable": 2})

    def test_the_output_file_is_never_overwritten(self):
        out = self.tmp / "p.jsonl"
        out.write_text("earlier evidence\n", encoding="utf-8")
        clock = FakeClock()
        with self.assertRaises(FileExistsError):
            probe.run(role="wrist", samples=1, interval_s=1.0, out_path=out, runtime=FakeRuntime([]).call, service=None,
                      clock_ns=clock.mono, sleep=clock.sleep, wall=lambda: "x")
        self.assertEqual(out.read_text(encoding="utf-8"), "earlier evidence\n")

    def test_the_camera_service_stream_is_sampled_beside_the_runtime(self):
        class Service:
            def stats(self):
                return {"received": 40, "measured_fps": 3.9, "rx_age_ns": 250_000_000, "gaps": {"events": 1, "missing_previews": 2},
                        "longest_interval_ns": 2_400_000_000, "yield_notices": 3, "malformed": {},
                        "service": {"published": 41, "skipped_for_mission": 70, "dropped_at_socket": 0}}

            def latest(self):
                return type("Frame", (), {"preview_seq": 41, "session": SESSION})()

            def latest_yield(self):
                return type("Notice", (), {"rx_mono_ns": 4_900_000_000, "yielding_for_ms": 1500})()

        clock = FakeClock()
        out = self.tmp / "p.jsonl"
        probe.run(role="wrist", samples=1, interval_s=1.0, out_path=out, runtime=None, service=Service(), clock_ns=clock.mono,
                  sleep=clock.sleep, wall=lambda: "x")
        sample = self.lines(out)[0]
        self.assertIsNone(sample["runtime"])
        self.assertEqual(sample["service"], {"received": 40, "measured_fps": 3.9, "rx_age_ms": 250.0, "gap_events": 1, "missing_previews": 2,
                                             "longest_interval_ms": 2400.0, "yield_notices": 3, "malformed": 0, "preview_seq": 41,
                                             "session": SESSION, "last_notice_age_ms": 100.0, "last_notice_yielding_for_ms": 1500,
                                             "counters": {"published": 41, "skipped_for_mission": 70, "dropped_at_socket": 0}})


class OverARealRuntimeSocket(unittest.TestCase):
    """The shipped reader (RuntimeClient) against a unix socket that records every request line. The probe must send
    only its three reads, one per connection, and no hello."""

    def test_the_real_reader_sends_only_the_three_reads(self):
        tmp = Path(tempfile.mkdtemp(prefix="kc-probe-sock-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = str(tmp / "rt.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(8)
        self.addCleanup(server.close)
        received, stop = [], threading.Event()

        def serve():
            server.settimeout(0.2)
            while not stop.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    line = connection.makefile("rb").readline()
                    request = json.loads(line)
                    received.append(request)
                    answer = preview_answer() if request.get("op") == "preview" else status_answer() if request.get("op") == "status" else {"ok": False, "reason_code": "no_mission_recorded"}
                    connection.sendall((json.dumps(answer) + "\n").encode())

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(2)))
        clock = FakeClock()
        out = tmp / "p.jsonl"
        probe.run(role="wrist", samples=2, interval_s=1.0, out_path=out, runtime=probe.runtime_reader(path), service=None,
                  clock_ns=clock.mono, sleep=clock.sleep, wall=lambda: "x")
        self.assertEqual([request["op"] for request in received], ["preview", "status", "mission_state"] * 2)
        self.assertNotIn("hello", [request["op"] for request in received])
        self.assertNotIn(path, out.read_text(encoding="utf-8"), "the evidence names no local path")


class AgainstAPublisherOnLoopback(unittest.TestCase):
    """The command line end to end: a PUB on this machine's loopback sends messages packed by the camera service's own
    functions, frames and yield notices, and the probe's subscriber reads them."""

    def test_the_command_line_samples_the_service_stream(self):
        import time as clock_module

        import zmq

        spec = importlib.util.spec_from_file_location("camera_service_for_probe_tests", REPO_ROOT / "bin" / "pi" / "camera_service.py")
        service = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = service
        spec.loader.exec_module(service)
        context = zmq.Context()
        publisher = context.socket(zmq.PUB)
        publisher.setsockopt(zmq.LINGER, 0)
        port = publisher.bind_to_random_port("tcp://127.0.0.1")
        self.addCleanup(context.term)
        self.addCleanup(publisher.close, 0)
        stop = threading.Event()
        jpeg = b"\xff\xd8" + b"x" + b"\xff\xd9"

        def publish():
            seq = 0
            while not stop.is_set():
                seq += 1
                header = {"schema": service.PREVIEW_SCHEMA, "role": "wrist", "session": SESSION, "preview_seq": seq, "source_seq": seq,
                          "capture_mono_ns": seq, "dequeue_mono_ns": seq, "clock_domain": "pi_monotonic", "encoding": "jpeg", "width": 4,
                          "height": 3, "published": seq - 1, "skipped_for_mission": 5, "dropped_at_socket": 0}
                publisher.send(service.pack_preview(header, jpeg))
                clock_module.sleep(0.02)  # a conflating subscriber keeps the last message: space them so each kind is read
                publisher.send(service.pack_yield_notice({"schema": service.PREVIEW_YIELD_SCHEMA, "role": "wrist", "session": SESSION,
                                                          "preview_seq": seq, "source_seq": seq, "clock_domain": "pi_monotonic",
                                                          "yielding_for_ms": 1000, "published": seq, "skipped_for_mission": 9,
                                                          "dropped_at_socket": 0}))
                clock_module.sleep(0.02)

        thread = threading.Thread(target=publish, daemon=True)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(2)))
        tmp = Path(tempfile.mkdtemp(prefix="kc-probe-cli-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "p.jsonl"
        code = probe.main(["--camera-endpoint", f"tcp://127.0.0.1:{port}", "--samples", "4", "--interval", "0.25", "--out", str(out)])
        self.assertEqual(code, 0)
        lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 5)
        last = lines[3]["service"]
        self.assertGreater(last["received"], 0, "the subscriber read frames")
        self.assertGreater(last["yield_notices"], 0, "and notices, which are not frames")
        self.assertEqual(last["session"], SESSION)
        self.assertIsNotNone(last["counters"])
        self.assertEqual(last["malformed"], 0)
        self.assertEqual(probe.main(["--camera-endpoint", f"tcp://127.0.0.1:{port}", "--samples", "1", "--out", str(out)]), 3,
                         "an existing evidence file is never overwritten")


class CommandLine(unittest.TestCase):
    def test_a_camera_endpoint_must_be_loopback_and_one_source_is_required(self):
        self.assertEqual(probe.main(["--out", "/nonexistent/x.jsonl", "--samples", "1"]), 2)
        self.assertEqual(probe.main(["--camera-endpoint", "tcp://" + ".".join(("192", "0", "2", "7")) + ":5571", "--out", "/nonexistent/x.jsonl"]), 2)

    def test_the_file_carries_the_license_header(self):
        head = (REPO_ROOT / "tools" / "preview_stall_probe.py").read_text(encoding="utf-8").splitlines()[:3]
        self.assertIn("Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.", "\n".join(head))


if __name__ == "__main__":
    unittest.main()
