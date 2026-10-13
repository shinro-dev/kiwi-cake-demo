#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_preview_client_gap_reasons.py: the laptop side reads what the camera service says
# about its gaps. The header counters (published, skipped_for_mission, dropped_at_socket) round-trip through the
# service's own pack_preview(), a header without them (a service older than the counters) is still a frame with the
# counters unknown rather than zero, and a yield notice (pack_yield_notice) is counted and remembered but is NEVER a
# frame: it does not replace the picture, does not move the frame rate and is not a gap. No socket, no camera.
# Run directly: python3 packages/demo-runtime/tests/test_preview_client_gap_reasons.py
import importlib.util
import json
import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import preview_client  # noqa: E402
from demo_runtime.preview_client import PreviewTracker, unpack_preview, unpack_yield  # noqa: E402


def load_service():
    spec = importlib.util.spec_from_file_location("camera_service_for_gap_reason_tests", REPO_ROOT / "bin" / "pi" / "camera_service.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


service = load_service()

JPEG = b"\xff\xd8" + b"preview-bytes" + b"\xff\xd9"
SECOND = 1_000_000_000
PI_T0 = 50_000 * SECOND
LAPTOP_T0 = 900 * SECOND
SESSION = "cs-20260927T000000Z-0a1b2c3d"
COUNTERS = {"published": 11, "skipped_for_mission": 40, "dropped_at_socket": 2}


def header(preview_seq, counters=True, **changes):
    base = {"schema": service.PREVIEW_SCHEMA, "role": "wrist", "session": SESSION, "preview_seq": preview_seq, "source_seq": preview_seq * 5,
            "capture_mono_ns": PI_T0 + preview_seq * SECOND // 4, "dequeue_mono_ns": PI_T0 + preview_seq * SECOND // 4 + 2_000_000,
            "clock_domain": "pi_monotonic", "timestamp_semantics": "driver_buffer_timestamp", "encoding": "jpeg", "width": 320, "height": 180,
            "source_width": 1280, "source_height": 720, "pixel_format": "MJPG", "source_fps_reported": 30.0, "source_fps_measured": 19.9}
    if counters:
        base.update(COUNTERS)
    base.update(changes)
    return base


def frame(preview_seq, **changes):
    return service.pack_preview(header(preview_seq, **changes), JPEG)


def notice(preview_seq, yielding_for_ms=1000, **changes):
    body = {"schema": service.PREVIEW_YIELD_SCHEMA, "role": "wrist", "session": SESSION, "preview_seq": preview_seq, "source_seq": preview_seq * 5 + 9,
            "clock_domain": "pi_monotonic", "reason": "mission_capture_in_flight", "yielding_for_ms": yielding_for_ms,
            "published": 12, "skipped_for_mission": 61, "dropped_at_socket": 2}
    body.update(changes)
    return service.pack_yield_notice(body)


class BothEndsAgree(unittest.TestCase):
    def test_the_yield_schema_is_the_service_s(self):
        self.assertEqual(preview_client.PREVIEW_YIELD_SCHEMA, service.PREVIEW_YIELD_SCHEMA)

    def test_the_header_round_trip_with_the_counters(self):
        decoded, jpeg = unpack_preview(frame(3))
        self.assertEqual((decoded, jpeg), (header(3), JPEG))
        self.assertEqual(preview_client.service_counters(decoded), COUNTERS)

    def test_the_header_round_trip_without_the_counters(self):
        decoded, _ = unpack_preview(frame(3, counters=False))
        self.assertEqual(preview_client.service_counters(decoded), {"published": None, "skipped_for_mission": None, "dropped_at_socket": None},
                         "a service older than the counters counted nothing this reader can see: unknown, never zero")

    def test_a_counter_of_the_wrong_shape_is_unknown_rather_than_clamped(self):
        decoded, _ = unpack_preview(frame(3, skipped_for_mission=-1, published=True, dropped_at_socket=2.5))
        self.assertEqual(preview_client.service_counters(decoded), {"published": None, "skipped_for_mission": None, "dropped_at_socket": None})

    def test_a_notice_unpacks_and_a_frame_unpacker_refuses_it(self):
        decoded = unpack_yield(notice(4))
        self.assertEqual((decoded["schema"], decoded["preview_seq"], decoded["yielding_for_ms"]), (service.PREVIEW_YIELD_SCHEMA, 4, 1000))
        with self.assertRaises(preview_client.MalformedPreview) as refused:
            unpack_preview(notice(4))
        self.assertEqual(refused.exception.code, "unknown_schema")

    def test_a_notice_with_a_picture_or_a_bad_identity_is_malformed(self):
        with_picture = service.pack_preview(json.loads(unpack_header(notice(4))), JPEG)
        for raw, code in ((with_picture, "yield_with_payload"), (notice(4, role="Wrist!"), "bad_identity"),
                          (notice(4, yielding_for_ms=-5), "bad_numbers"), (notice(-1), "bad_numbers")):
            with self.subTest(code=code):
                with self.assertRaises(preview_client.MalformedPreview) as refused:
                    unpack_yield(raw)
                self.assertEqual(refused.exception.code, code)


def unpack_header(raw):
    import struct

    (length,) = struct.unpack(">I", raw[5:9])
    return raw[9:9 + length].decode()


class TheTrackerKeepsTheReasonsApart(unittest.TestCase):
    def test_a_frame_carries_the_service_counters(self):
        tracker = PreviewTracker("wrist")
        self.assertTrue(tracker.ingest(frame(1), LAPTOP_T0))
        self.assertEqual(tracker.latest().service, COUNTERS)
        self.assertEqual(tracker.stats(LAPTOP_T0)["service"], COUNTERS)

    def test_a_notice_is_remembered_and_is_never_a_frame(self):
        tracker = PreviewTracker("wrist")
        tracker.ingest(frame(1), LAPTOP_T0)
        self.assertFalse(tracker.ingest(notice(1, yielding_for_ms=1000), LAPTOP_T0 + SECOND), "a notice does not become the latest frame")
        self.assertEqual(tracker.latest().preview_seq, 1)
        self.assertEqual(tracker.latest().jpeg, JPEG, "the picture held is still the last picture")
        last = tracker.latest_yield()
        self.assertEqual((last.rx_mono_ns, last.session, last.preview_seq, last.yielding_for_ms), (LAPTOP_T0 + SECOND, SESSION, 1, 1000))
        self.assertEqual(last.service, {"published": 12, "skipped_for_mission": 61, "dropped_at_socket": 2})
        stats = tracker.stats(LAPTOP_T0 + SECOND)
        self.assertEqual((stats["received"], stats["yield_notices"]), (1, 1), "received counts frames only")
        self.assertEqual(stats["service"], {"published": 12, "skipped_for_mission": 61, "dropped_at_socket": 2}, "the newest counters win")
        self.assertEqual(stats["malformed"], {})

    def test_notices_move_neither_the_rate_nor_the_gap_count(self):
        tracker = PreviewTracker("wrist")
        tracker.ingest(frame(1), LAPTOP_T0)
        for index in range(4):
            tracker.ingest(notice(1, yielding_for_ms=1000 + 500 * index), LAPTOP_T0 + SECOND + index * SECOND // 2)
        tracker.ingest(frame(2), LAPTOP_T0 + 3 * SECOND)
        stats = tracker.stats(LAPTOP_T0 + 3 * SECOND)
        self.assertEqual((stats["gaps"], stats["regressions"], stats["received"], stats["yield_notices"]),
                         ({"events": 0, "missing_previews": 0}, 0, 2, 4))
        self.assertEqual(stats["longest_interval_ns"], 3 * SECOND, "the interval between PICTURES is what it was")

    def test_a_notice_for_another_role_is_refused_like_a_frame_for_another_role(self):
        tracker = PreviewTracker("front")
        self.assertFalse(tracker.ingest(notice(1), LAPTOP_T0))
        self.assertIsNone(tracker.latest_yield())
        self.assertEqual(tracker.stats(LAPTOP_T0)["malformed"], {"other_role": 1})

    def test_nothing_yet_reads_as_unknown(self):
        tracker = PreviewTracker("wrist")
        self.assertIsNone(tracker.latest_yield())
        stats = tracker.stats(LAPTOP_T0)
        self.assertEqual((stats["yield_notices"], stats["service"]), (0, None))


if __name__ == "__main__":
    unittest.main()
