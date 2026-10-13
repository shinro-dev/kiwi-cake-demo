#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_preview_client.py: the laptop side of the preview tap holds ONE frame, names the clock
# domain of its capture time, reports a RECEIVE age in the laptop clock (never a difference between two clocks), measures
# the frame rate, counts the gaps it observed, and survives whatever arrives on the socket. The messages are packed by the
# service's own pack_preview() (bin/pi/camera_service.py), so the two ends cannot drift apart unnoticed. One test uses a
# PUB/SUB pair on this machine's loopback; no camera, no robot, no other host.
# Run directly: python3 packages/demo-runtime/tests/test_preview_client.py
import importlib.util
import json
import struct
import subprocess
import sys
import time
import unittest
from collections import deque
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import preview_client  # noqa: E402
from demo_runtime.preview_client import MalformedPreview, PreviewSubscriber, PreviewTracker, check_endpoint, unpack_preview  # noqa: E402


def load_service():
    spec = importlib.util.spec_from_file_location("camera_service_for_preview_tests", REPO_ROOT / "bin" / "pi" / "camera_service.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


service = load_service()

JPEG = b"\xff\xd8" + b"preview-bytes" + b"\xff\xd9"
SECOND = 1_000_000_000
PI_T0 = 50_000 * SECOND  # the Pi's monotonic clock: another machine, another epoch
LAPTOP_T0 = 900 * SECOND


def header(preview_seq, **changes):
    base = {"schema": service.PREVIEW_SCHEMA, "role": "wrist", "session": "cs-20260920T000000Z-0a1b2c3d", "preview_seq": preview_seq,
            "source_seq": preview_seq * 7, "capture_mono_ns": PI_T0 + preview_seq * SECOND // 4, "dequeue_mono_ns": PI_T0 + preview_seq * SECOND // 4 + 2_000_000,
            "clock_domain": "pi_monotonic", "timestamp_semantics": "driver_buffer_timestamp", "encoding": "jpeg", "width": 320, "height": 180,
            "source_width": 1280, "source_height": 720, "pixel_format": "MJPG", "source_fps_reported": 30.0, "source_fps_measured": 29.97}
    base.update(changes)
    return base


def message(preview_seq, jpeg=JPEG, **changes):
    return service.pack_preview(header(preview_seq, **changes), jpeg)


class Contract(unittest.TestCase):
    def test_both_ends_use_one_wire_format(self):
        self.assertEqual((preview_client.PREVIEW_MAGIC, preview_client.PREVIEW_SCHEMA), (service.PREVIEW_MAGIC, service.PREVIEW_SCHEMA))
        decoded, jpeg = unpack_preview(message(3))
        self.assertEqual((decoded, jpeg), (header(3), JPEG))

    def test_clock_domains_and_the_session_pattern_are_the_contract_ones(self):
        common = json.loads((REPO_ROOT / "packages" / "demo-contracts" / "schema" / "v1" / "common.schema.json").read_text())["$defs"]
        self.assertEqual(list(preview_client.CLOCK_DOMAINS), common["clock_domain"]["enum"])
        self.assertEqual(preview_client.SESSION_PATTERN, common["source_stamp"]["properties"]["session"]["pattern"])
        self.assertIn(service.CLOCK_PI, common["clock_domain"]["enum"])
        self.assertEqual("^" + service.SESSION_PATTERN.pattern + "$", common["source_stamp"]["properties"]["session"]["pattern"])

    def test_importing_the_module_pulls_in_no_socket_library_and_no_array_library(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import demo_runtime.preview_client; "
                "print(sorted(name for name in ('zmq', 'numpy', 'cv2') if name in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code, str(PACKAGE_DIR)], capture_output=True, text=True, timeout=60, check=True)
        self.assertEqual(out.stdout.strip(), "[]")


class LatestFrame(unittest.TestCase):
    def test_nothing_yet(self):
        tracker = PreviewTracker("wrist")
        self.assertIsNone(tracker.latest())
        stats = tracker.stats(LAPTOP_T0)
        self.assertEqual((stats["received"], stats["measured_fps"], stats["rx_age_ns"], stats["buffered_frames"]), (0, None, None, 0))

    def test_the_latest_frame_names_the_clock_of_its_capture_time_and_ages_in_the_laptop_clock(self):
        tracker = PreviewTracker("wrist")
        self.assertTrue(tracker.ingest(message(1), LAPTOP_T0))
        shown = tracker.latest()
        self.assertEqual((shown.jpeg, shown.role, shown.preview_seq, shown.source_seq, shown.width, shown.height), (JPEG, "wrist", 1, 7, 320, 180))
        self.assertEqual((shown.capture_mono_ns, shown.capture_clock_domain), (PI_T0 + SECOND // 4, "pi_monotonic"))
        self.assertEqual((shown.rx_mono_ns, shown.rx_clock_domain), (LAPTOP_T0, "laptop_monotonic"))
        self.assertEqual(shown.timestamp_semantics, "driver_buffer_timestamp")
        self.assertEqual(shown.stream, {"pixel_format": "MJPG", "source_fps_reported": 30.0, "source_fps_measured": 29.97, "source_width": 1280, "source_height": 720})
        # The age is a receive age: laptop now minus laptop receive time. The Pi's capture time is far away and plays no part.
        self.assertEqual(shown.age_ns(LAPTOP_T0 + 3 * SECOND), 3 * SECOND)
        self.assertEqual(tracker.stats(LAPTOP_T0 + 3 * SECOND)["rx_age_ns"], 3 * SECOND)
        self.assertEqual(tracker.stats(LAPTOP_T0)["clock_domain"], "laptop_monotonic")
        self.assertNotIn("preview-bytes", repr(shown))

    def test_only_one_frame_is_ever_held(self):
        tracker = PreviewTracker("wrist")
        for seq in range(1, 200):
            tracker.ingest(message(seq, jpeg=JPEG + bytes([seq])), LAPTOP_T0 + seq * 1_000_000)
        self.assertEqual(tracker.latest().preview_seq, 199)
        self.assertEqual(tracker.latest().jpeg, JPEG + bytes([199]))
        self.assertEqual(tracker.stats(LAPTOP_T0 + SECOND)["buffered_frames"], 1)
        frames_held = 0
        for value in vars(tracker).values():  # the one frame, and no collection anywhere that could hold another
            items = value.values() if isinstance(value, dict) else value if isinstance(value, (list, tuple, set, deque)) else [value]
            frames_held += sum(isinstance(item, (bytes, preview_client.PreviewFrame)) for item in items)
        self.assertEqual(frames_held, 1)

    def test_a_capture_time_without_a_trustworthy_source_stays_absent_and_an_unknown_domain_is_called_unknown(self):
        tracker = PreviewTracker("wrist")
        tracker.ingest(message(1, capture_mono_ns=None, timestamp_semantics="dequeue_time_only", clock_domain="gps"), LAPTOP_T0)
        shown = tracker.latest()
        self.assertEqual((shown.capture_mono_ns, shown.timestamp_semantics, shown.capture_clock_domain), (None, "dequeue_time_only", "unknown"))
        self.assertIsNotNone(shown.dequeue_mono_ns)


class FeedQuality(unittest.TestCase):
    def test_measured_fps_is_the_receive_rate_in_the_laptop_clock(self):
        tracker = PreviewTracker("wrist")
        for seq in range(1, 22):  # 21 frames, one every 250 ms: 4 fps
            tracker.ingest(message(seq), LAPTOP_T0 + seq * SECOND // 4)
        now = LAPTOP_T0 + 21 * SECOND // 4
        stats = tracker.stats(now)
        self.assertEqual(stats["measured_fps"], 4.0)
        self.assertEqual(stats["gaps"], {"events": 0, "missing_previews": 0})
        self.assertEqual(stats["longest_interval_ns"], SECOND // 4)

    def test_a_feed_that_stopped_reads_as_no_rate_and_a_growing_age_never_as_its_old_rate(self):
        tracker = PreviewTracker("wrist")
        for seq in range(1, 10):
            tracker.ingest(message(seq), LAPTOP_T0 + seq * SECOND // 4)
        later = LAPTOP_T0 + 60 * SECOND
        stats = tracker.stats(later)
        self.assertIsNone(stats["measured_fps"])
        self.assertEqual(stats["rx_age_ns"], later - (LAPTOP_T0 + 9 * SECOND // 4))
        self.assertEqual(tracker.latest().preview_seq, 9, "the last frame is still there, with its age")

    def test_gaps_are_the_previews_this_subscriber_did_not_get(self):
        tracker = PreviewTracker("wrist")
        for index, seq in enumerate((1, 2, 5, 6, 10)):
            tracker.ingest(message(seq), LAPTOP_T0 + index * SECOND)
        stats = tracker.stats(LAPTOP_T0 + 5 * SECOND)
        self.assertEqual(stats["gaps"], {"events": 2, "missing_previews": 5})
        self.assertEqual(stats["received"], 5)

    def test_skipped_source_frames_are_not_gaps(self):
        tracker = PreviewTracker("wrist")
        for seq in (1, 2, 3):
            tracker.ingest(message(seq, source_seq=seq * 100), LAPTOP_T0 + seq * SECOND)
        self.assertEqual(tracker.stats(LAPTOP_T0 + 4 * SECOND)["gaps"], {"events": 0, "missing_previews": 0})

    def test_a_restarted_service_is_a_session_change_not_a_gap(self):
        tracker = PreviewTracker("wrist")
        tracker.ingest(message(40), LAPTOP_T0)
        tracker.ingest(message(1, session="cs-20260920T000500Z-99887766"), LAPTOP_T0 + SECOND)
        tracker.ingest(message(3, session="cs-20260920T000500Z-99887766"), LAPTOP_T0 + 2 * SECOND)
        stats = tracker.stats(LAPTOP_T0 + 3 * SECOND)
        self.assertEqual((stats["session_changes"], stats["regressions"]), (1, 0))
        self.assertEqual(stats["gaps"], {"events": 1, "missing_previews": 1})

    def test_a_repeated_or_older_number_and_a_frozen_capture_clock_are_counted(self):
        tracker = PreviewTracker("wrist")
        tracker.ingest(message(5), LAPTOP_T0)
        tracker.ingest(message(5), LAPTOP_T0 + SECOND)
        tracker.ingest(message(6, capture_mono_ns=header(5)["capture_mono_ns"]), LAPTOP_T0 + 2 * SECOND)
        stats = tracker.stats(LAPTOP_T0 + 3 * SECOND)
        self.assertEqual((stats["regressions"], stats["capture_time_stalls"]), (1, 2))


class HostileInput(unittest.TestCase):
    def test_malformed_messages_are_counted_and_change_nothing(self):
        good = message(1)
        head = json.dumps(header(2)).encode()
        cases = {
            "bad_magic": b"nope" + good,
            "bad_header_length": service.PREVIEW_MAGIC + struct.pack(">I", 10_000_000) + b"{}",
            "header_not_json": service.PREVIEW_MAGIC + struct.pack(">I", 4) + b"\xff\xfe{}" + JPEG,
            "unknown_schema": service.pack_preview(header(2, schema="something/9"), JPEG),
            "not_a_jpeg": service.PREVIEW_MAGIC + struct.pack(">I", len(head)) + head + b"<html>",
            "bad_identity": service.pack_preview(header(2, session="../../etc"), JPEG),
            "bad_numbers": service.pack_preview(header(True), JPEG),  # a boolean is not a sequence number
            "other_role": service.pack_preview(header(2, role="front"), JPEG),
        }
        tracker = PreviewTracker("wrist")
        tracker.ingest(good, LAPTOP_T0)
        for code, raw in cases.items():
            self.assertFalse(tracker.ingest(raw, LAPTOP_T0 + SECOND), code)
        self.assertFalse(tracker.ingest("text", LAPTOP_T0 + SECOND))
        stats = tracker.stats(LAPTOP_T0 + SECOND)
        self.assertEqual(stats["malformed"], dict({code: 1 for code in cases}, not_bytes=1))
        self.assertEqual((stats["received"], tracker.latest().preview_seq), (1, 1))

    def test_a_message_over_the_size_limit_is_refused(self):
        with self.assertRaises(MalformedPreview) as caught:
            unpack_preview(service.PREVIEW_MAGIC + b"\x00" * preview_client.MAX_MESSAGE_BYTES)
        self.assertEqual(caught.exception.code, "too_large")

    def test_only_a_loopback_endpoint_is_accepted_and_a_refused_one_is_not_repeated(self):
        self.assertEqual(check_endpoint("tcp://127.0.0.1:5571"), "tcp://127.0.0.1:5571")
        self.assertEqual(check_endpoint("tcp://localhost:5571"), "tcp://localhost:5571")
        elsewhere = "tcp://" + ".".join(("192", "0", "2", "41")) + ":5571"
        for endpoint in (elsewhere, "tcp://0.0.0.0:5571", "tcp://*:5571", "tcp://127.0.0.1:0", "tcp://127.0.0.1:70000", "ipc:///tmp/x", None):
            with self.assertRaises(ValueError) as caught:
                PreviewSubscriber(endpoint)
            self.assertNotIn("41", str(caught.exception).replace("127.0.0.1", ""))


class OverTheSocket(unittest.TestCase):
    def setUp(self):
        import zmq

        self.zmq, self.context = zmq, zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.setsockopt(zmq.LINGER, 0)
        self.port = self.publisher.bind_to_random_port("tcp://127.0.0.1")
        self.addCleanup(self.context.term)
        self.addCleanup(self.publisher.close, 0)

    def send_until_received(self, subscriber, seq):
        """A SUB socket connects in the background; the first messages of a PUB may go to nobody."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.publisher.send(message(seq))
            if subscriber.poll_once(50):
                return
        self.fail("the subscriber never received a preview")

    def test_the_socket_keeps_the_last_message_only(self):
        ticks = iter(range(LAPTOP_T0, LAPTOP_T0 + 1000 * SECOND, SECOND))
        subscriber = PreviewSubscriber(f"tcp://127.0.0.1:{self.port}", role="wrist", clock_ns=lambda: next(ticks), context=self.context)
        subscriber.open()
        self.addCleanup(subscriber.stop)
        self.send_until_received(subscriber, 1)
        for seq in range(2, 12):  # ten previews while the reader is away
            self.publisher.send(message(seq))
        time.sleep(0.3)
        self.assertTrue(subscriber.poll_once(1000))
        self.assertEqual(subscriber.latest().preview_seq, 11, "the newest one")
        self.assertFalse(subscriber.poll_once(100), "and nothing was queued behind it")
        stats = subscriber.stats()
        self.assertEqual(stats["gaps"], {"events": 1, "missing_previews": 9})
        self.assertEqual(stats["buffered_frames"], 1)

    def test_a_started_subscriber_follows_the_feed_and_stops_cleanly(self):
        subscriber = PreviewSubscriber(f"tcp://127.0.0.1:{self.port}", role="wrist", poll_ms=20, context=self.context)
        subscriber.start()
        seq, deadline = 0, time.monotonic() + 5
        while time.monotonic() < deadline and (subscriber.latest() is None or subscriber.latest().preview_seq < 5):
            seq += 1
            self.publisher.send(message(seq))
            time.sleep(0.02)
        shown = subscriber.latest()
        self.assertIsNotNone(shown)
        self.assertGreaterEqual(shown.preview_seq, 5)
        self.assertEqual(shown.capture_clock_domain, "pi_monotonic")
        self.assertLess(shown.age_ns(time.monotonic_ns()), 2 * SECOND)
        self.assertIsNotNone(subscriber.stats()["measured_fps"])
        with self.assertRaises(RuntimeError):
            subscriber.start()
        subscriber.stop()
        subscriber.stop()  # twice is harmless


if __name__ == "__main__":
    unittest.main()
