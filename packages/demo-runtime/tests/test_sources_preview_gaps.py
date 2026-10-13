#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_sources_preview_gaps.py: a preview gap the camera service DECLARED is not a stall.
#
# THE DEFECT THIS PINS. A mission capture makes the camera service stand aside: it publishes no picture until the frame
# is selected, encoded and published (bin/pi/camera_service.py, _capture). The preview source called any silence longer
# than its 2 s window "stalled" and published a record WITHOUT a picture; a record without a picture ENDS the API's
# MJPEG response (apps/console-api/console_api/app.py, _stream_preview), and the browser's image element keeps its last
# frame until the page replaces the element. A decision capture therefore read as a dead camera, and a stalled record
# that the page's store replaced before drawing it (inside its 120 ms publication window) left a frozen picture labelled
# as streaming.
#
# What holds now: a yield notice from the SAME service session, arriving while the source is connected, keeps it
# connected with the reason paused_for_decision_capture; each notice republishes the KEPT picture (the same frame, the
# same number, its receive age growing), so the browser's stream stays open and says why nothing new arrives; the slot
# carries the service's skipped_for_mission count. Silence with no notice is still a stall, a notice never revives a
# stalled or connecting source, and a slot from a service without counters is still a valid slot.
# No socket, no camera: the real PreviewTracker behind a queue.
# Run directly: python3 packages/demo-runtime/tests/test_sources_preview_gaps.py
import base64
import json
import pathlib
import struct
import sys
import time
import unittest
from collections import deque
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import sources  # noqa: E402
from demo_runtime.preview_client import PREVIEW_MAGIC, PREVIEW_SCHEMA, PREVIEW_YIELD_SCHEMA, PreviewTracker  # noqa: E402
from demo_runtime.read_models import TelemetryHub  # noqa: E402

SECOND = 1_000_000_000
LAPTOP_T0 = 4_000 * SECOND
PI_T0 = 90_000 * SECOND
SESSION = "cs-20260927T000000Z-0a1b2c3d"
OTHER_SESSION = "cs-20260927T010000Z-99887766"
JPEG = b"\xff\xd8" + b"a-picture-for-a-human" + b"\xff\xd9"
PAUSED = "paused_for_decision_capture"


class Clock:
    def __init__(self, start=LAPTOP_T0):
        self.value = start

    def __call__(self):
        return self.value

    def advance_ms(self, milliseconds):
        self.value += int(milliseconds * 1_000_000)


def pack(header, payload=b""):
    blob = json.dumps(header).encode("utf-8")
    return PREVIEW_MAGIC + struct.pack(">I", len(blob)) + blob + payload


def frame(preview_seq, *, session=SESSION, counters=True, skipped=0):
    header = {"schema": PREVIEW_SCHEMA, "role": "wrist", "session": session, "preview_seq": preview_seq, "source_seq": preview_seq * 5,
              "capture_mono_ns": PI_T0 + preview_seq * SECOND // 5, "dequeue_mono_ns": PI_T0 + preview_seq * SECOND // 5 + 3_000_000,
              "clock_domain": "pi_monotonic", "timestamp_semantics": "driver_buffer_timestamp", "encoding": "jpeg", "width": 320, "height": 180,
              "source_width": 1280, "source_height": 720, "pixel_format": "MJPG", "source_fps_reported": 30.0, "source_fps_measured": 19.9}
    if counters:
        header.update({"published": preview_seq - 1, "skipped_for_mission": skipped, "dropped_at_socket": 0})
    return pack(header, JPEG)


def notice(preview_seq, yielding_for_ms, *, session=SESSION, skipped=30):
    return pack({"schema": PREVIEW_YIELD_SCHEMA, "role": "wrist", "session": session, "preview_seq": preview_seq, "source_seq": 99,
                 "clock_domain": "pi_monotonic", "reason": "mission_capture_in_flight", "yielding_for_ms": yielding_for_ms,
                 "published": preview_seq, "skipped_for_mission": skipped, "dropped_at_socket": 0})


class Subscriber:
    """The surface demo_runtime.sources asks of a PreviewSubscriber, around the REAL PreviewTracker."""

    def __init__(self, clock):
        self.tracker, self._clock, self.queue = PreviewTracker("wrist"), clock, deque()

    def push(self, *messages):
        self.queue.extend(messages)

    def open(self):
        pass

    def poll_once(self, timeout_ms=None):
        if not self.queue:
            time.sleep(0.001)
            return False
        return self.tracker.ingest(self.queue.popleft(), self._clock())

    def latest(self):
        return self.tracker.latest()

    def latest_yield(self):
        return self.tracker.latest_yield()

    def stats(self):
        return self.tracker.stats(self._clock())

    def stop(self):
        pass


def schema_errors(slot):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "demo-contracts" / "python"))
    try:
        import demo_contracts
    except ImportError:  # pragma: no cover - a checkout without the contracts package
        raise unittest.SkipTest("demo_contracts is not in this checkout")
    return demo_contracts.validate("source#/$defs/preview_slot", {key: value for key, value in slot.items() if key != "bytes_b64"})


class Case(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.subscriber = Subscriber(self.clock)
        self.source = sources.PreviewSource(subscriber_factory=lambda: self.subscriber, clock_ns=self.clock)
        self.source.start()

    def deliver(self, *messages, after_ms=0):
        self.clock.advance_ms(after_ms)
        self.subscriber.push(*messages)
        for _ in messages:
            self.source.pump(0)


class TheSlotCarriesTheSkipCount(Case):
    def test_a_connected_slot_carries_skipped_for_mission_and_validates(self):
        self.deliver(frame(4, skipped=17))
        slot = self.source.slot()
        self.assertEqual((slot["state"], slot["reason"], slot["skipped_for_mission"]), ("connected", None, 17))
        self.assertEqual(schema_errors(slot), [])

    def test_a_slot_from_a_service_without_counters_is_still_a_valid_slot(self):
        self.deliver(frame(4, counters=False))
        slot = self.source.slot()
        self.assertIsNone(slot["skipped_for_mission"], "nobody counted: unknown, never zero")
        self.assertEqual(schema_errors(slot), [])


class ADeclaredPauseIsNotAStall(Case):
    def test_a_notice_keeps_the_source_connected_past_the_window_and_says_why(self):
        self.deliver(frame(1))
        self.deliver(notice(1, 1000, skipped=21), after_ms=1200)
        self.deliver(notice(1, 1500, skipped=31), after_ms=500)
        self.deliver(notice(1, 2000, skipped=41), after_ms=500)
        self.clock.advance_ms(400)  # 2600 ms after the last picture, 400 ms after the last notice
        slot = self.source.slot()
        self.assertEqual((slot["available"], slot["state"], slot["reason"]), (True, "connected", PAUSED))
        self.assertEqual((slot["seq"], slot["receive_age_ms"]), (1, 2600.0), "the kept picture, and its true receive age")
        self.assertEqual(base64.b64decode(slot["bytes_b64"]), JPEG, "the kept picture stays in the slot so the stream stays open")
        self.assertEqual(slot["skipped_for_mission"], 41, "the newest count, from the newest notice")
        self.assertIsNone(slot["measured_fps"], "no rate is invented from notices")
        self.assertEqual(schema_errors(slot), [])

    def test_the_next_picture_ends_the_pause(self):
        self.deliver(frame(1))
        self.deliver(notice(1, 1000), after_ms=1200)
        self.deliver(frame(2, skipped=45), after_ms=600)
        slot = self.source.slot()
        self.assertEqual((slot["state"], slot["reason"], slot["seq"], slot["skipped_for_mission"]), ("connected", None, 2, 45))

    def test_silence_after_the_last_notice_is_a_stall(self):
        self.deliver(frame(1))
        self.deliver(notice(1, 1000), after_ms=1200)
        self.clock.advance_ms(2100)
        slot = self.source.slot()
        self.assertEqual((slot["available"], slot["state"], slot["reason"]), (False, "stalled", "no_sample_within_window"))
        self.assertNotIn("bytes_b64", slot)

    def test_silence_with_no_notice_is_a_stall_as_before(self):
        self.deliver(frame(1))
        self.clock.advance_ms(2100)
        self.assertEqual((self.source.slot()["state"], self.source.slot()["reason"]), ("stalled", "no_sample_within_window"))

    def test_a_notice_never_revives_a_stalled_source(self):
        self.deliver(frame(1))
        self.clock.advance_ms(2500)
        self.source.slot()
        self.deliver(notice(1, 2300))
        slot = self.source.slot()
        self.assertEqual((slot["state"], slot["reason"]), ("stalled", "no_sample_within_window"))
        self.assertNotIn("bytes_b64", slot, "a picture that stopped is not shown again because the service says it is busy")

    def test_a_notice_before_any_picture_is_not_a_connection(self):
        self.deliver(notice(0, 1000))
        slot = self.source.slot()
        self.assertEqual((slot["state"], slot["reason"]), ("connecting", "no_frame_yet"))

    def test_a_notice_from_another_service_session_keeps_nothing_alive(self):
        self.deliver(frame(1))
        self.deliver(notice(0, 1000, session=OTHER_SESSION), after_ms=1200)
        self.clock.advance_ms(900)
        self.assertEqual(self.source.slot()["state"], "stalled", "a restarted service's notice says nothing about the old picture")


class ThePublisherKeepsTheStreamOpen(unittest.TestCase):
    def test_every_notice_republishes_the_kept_picture_and_no_record_without_one_is_sent(self):
        clock = Clock()
        subscriber = Subscriber(clock)
        hub = TelemetryHub()
        supervisor = sources.SourceSupervisor(hub, tap=sources.TapSource(),
                                              preview=sources.PreviewSource(subscriber_factory=lambda: subscriber, clock_ns=clock), clock_ns=clock)
        supervisor.start()
        self.addCleanup(supervisor.stop)
        published = []
        original = hub.publish
        hub.publish = lambda stream, record: (published.append((stream, record)), original(stream, record))[1]
        topic = sources.preview_topic(sources.DEFAULT_ROLE)

        def step(message, after_ms):
            clock.advance_ms(after_ms)
            subscriber.push(message)
            supervisor.pump_once()

        step(frame(1), 0)
        for index in range(4):
            step(notice(1, 1000 + 500 * index, skipped=20 + 10 * index), 1200 if index == 0 else 500)
        step(frame(2, skipped=62), 400)
        records = [record for stream, record in published if stream == topic]
        self.assertEqual([(record["state"], record["reason"], record["seq"]) for record in records],
                         [("connected", None, 1)] + [("connected", PAUSED, 1)] * 4 + [("connected", None, 2)])
        self.assertTrue(all("bytes_b64" in record for record in records), "a record without a picture would end the browser's stream")
        self.assertEqual([record["skipped_for_mission"] for record in records], [0, 20, 30, 40, 50, 62])
        report = supervisor.report()["previews"]["wrist"]
        self.assertEqual((report["yield_notices"], report["service"]["skipped_for_mission"]), (4, 62))
        self.assertNotIn("tcp://", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
