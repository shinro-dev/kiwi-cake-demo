#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_camera_service_preview_gaps.py: what the camera service says about the gaps in its preview.
# Every preview header carries the service's own counters (published, skipped for a mission capture, dropped at the
# socket), and a stand-aside that lasts longer than a short bound is announced by a picture-less yield notice, so the
# reader can tell "the service is serving a decision capture" from "the feed died". A short stand-aside stays silent
# exactly as before, a notice never carries a picture, never consumes a preview number, and a camera that delivers no
# frame sends no notice either. Against fake devices and a collecting publisher: no camera, no socket.
# Run directly: python3 autonomy/tests/test_camera_service_preview_gaps.py
import importlib.util
import json
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "perception"))


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cs = load("camera_service_under_gap_test", "bin/pi/camera_service.py")

WIDTH, HEIGHT = 32, 24
SESSION = "cs-20260927T000000Z-0a1b2c3d"


def wait_for(condition, timeout_s=5.0, what="condition"):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.002)
    raise AssertionError(f"timed out waiting for {what}")


def unpack(message):
    assert message[:5] == cs.PREVIEW_MAGIC
    (length,) = struct.unpack(">I", message[5:9])
    return json.loads(message[9:9 + length].decode()), message[9 + length:]


class CollectingPublisher:
    def __init__(self, accept=True):
        self.messages, self.accept = [], accept
        self._lock = threading.Lock()

    def publish(self, message):
        with self._lock:
            self.messages.append(message)
        return self.accept

    def snapshot(self):
        with self._lock:
            return [unpack(message) for message in self.messages]

    def close(self):
        pass


class Feeder:
    """The capture side of one preview worker: frames go into the worker's slot on a thread, a frame every period, until
    stopped or paused. paused models a camera that delivers nothing."""

    def __init__(self, slot, period_s=0.004):
        self.slot, self.period_s, self.seq = slot, period_s, 0
        self.paused, self._stop = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(2)

    def _run(self):
        while not self._stop.is_set():
            time.sleep(self.period_s)
            if self.paused.is_set():
                continue
            self.seq += 1
            now = time.monotonic_ns()
            self.slot.put(cs.Stamped(seq=self.seq, frame=np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8), dequeue_mono_ns=now, capture_mono_ns=now - 1_000_000))


class GapCase(unittest.TestCase):
    def worker(self, *, publisher=None, max_fps=50.0, **options):
        self.mission = threading.Event()
        self.slot = cs.LatestSlot()
        self.publisher = publisher or CollectingPublisher()
        worker = cs.PreviewWorker("wrist", SESSION, self.slot, self.publisher, max_fps=max_fps, width=16, quality=70,
                                  mission_active=self.mission, stream_facts=lambda: {"pixel_format": "MJPG"}, **options)
        self.feeder = Feeder(self.slot).start()
        self.addCleanup(self.feeder.stop)
        worker.start()
        self.addCleanup(worker.stop)
        return worker

    def frames(self):
        return [(header, jpeg) for header, jpeg in self.publisher.snapshot() if header["schema"] == cs.PREVIEW_SCHEMA]

    def notices(self):
        return [(header, payload) for header, payload in self.publisher.snapshot() if header["schema"] == cs.PREVIEW_YIELD_SCHEMA]


class TheHeaderCarriesTheServiceCounters(GapCase):
    def test_every_preview_header_carries_the_three_counters_as_of_its_own_sending(self):
        worker = self.worker()
        wait_for(lambda: len(self.frames()) >= 4, what="four previews")
        headers = [header for header, _ in self.frames()]
        for index, header in enumerate(headers):
            with self.subTest(preview_seq=header["preview_seq"]):
                for key in ("published", "skipped_for_mission", "dropped_at_socket"):
                    self.assertIs(type(header[key]), int, f"{key} is a plain integer")
                self.assertEqual(header["published"], index, "published counts the previews sent BEFORE this one")
                self.assertEqual(header["dropped_at_socket"], 0)
                self.assertEqual(header["skipped_for_mission"], 0, "no mission capture has happened")
        self.assertGreaterEqual(worker.published, len(headers))

    def test_a_preview_after_a_mission_capture_says_how_many_it_stood_aside_for(self):
        worker = self.worker(yield_notice_after_s=60.0)
        wait_for(lambda: len(self.frames()) >= 1, what="a first preview")
        self.mission.set()
        wait_for(lambda: worker.skipped_for_mission >= 5, what="previews standing aside")
        self.mission.clear()
        wait_for(lambda: self.frames()[-1][0]["skipped_for_mission"] >= 5, what="a preview carrying the count")

    def test_a_socket_that_refuses_is_counted_in_the_next_header(self):
        publisher = CollectingPublisher(accept=False)
        worker = self.worker(publisher=publisher)
        wait_for(lambda: len(publisher.messages) >= 3, what="three attempts")
        headers = [header for header, _ in publisher.snapshot()]
        self.assertEqual([header["dropped_at_socket"] for header in headers[:3]], [0, 1, 2])
        self.assertEqual(worker.published, 0)


class AStandAsideIsAnnouncedOnlyWhenItIsLong(GapCase):
    def test_a_short_stand_aside_sends_nothing_at_all(self):
        self.worker(yield_notice_after_s=5.0)
        wait_for(lambda: len(self.frames()) >= 1, what="a first preview")
        before = len(self.publisher.messages)
        self.mission.set()
        time.sleep(0.2)
        during = len(self.publisher.messages)
        self.mission.clear()
        self.assertEqual(during, before, "a stand-aside shorter than the bound publishes nothing, as before")

    def test_a_long_stand_aside_sends_picture_less_notices_with_the_counters(self):
        worker = self.worker(yield_notice_after_s=0.05, yield_notice_interval_s=0.05)
        wait_for(lambda: len(self.frames()) >= 2, what="previews")
        self.mission.set()
        time.sleep(0.02)  # the frame being encoded when the flag rose may still go out
        last_seq = self.frames()[-1][0]["preview_seq"]
        frames_before = len(self.frames())
        wait_for(lambda: len(self.notices()) >= 3, what="three yield notices")
        self.mission.clear()
        self.assertEqual(len(self.frames()) - frames_before, 0, "no picture while the service stands aside")
        notices = self.notices()
        for header, payload in notices:
            with self.subTest(notice=header):
                self.assertEqual(payload, b"", "a notice never carries a picture")
                self.assertEqual((header["role"], header["session"], header["clock_domain"]), ("wrist", SESSION, "pi_monotonic"))
                self.assertEqual(header["preview_seq"], last_seq, "a notice names the last preview and consumes no number")
                self.assertIs(type(header["skipped_for_mission"]), int)
                self.assertGreater(header["skipped_for_mission"], 0)
                self.assertGreaterEqual(header["yielding_for_ms"], 50)
                for key in ("published", "dropped_at_socket"):
                    self.assertIs(type(header[key]), int)
        spacing = [b[0]["yielding_for_ms"] - a[0]["yielding_for_ms"] for a, b in zip(notices, notices[1:])]
        self.assertTrue(all(step >= 45 for step in spacing), f"notices are rate-limited: {spacing}")
        self.assertEqual(worker.status()["yield_notices"], len(notices))

    def test_previews_resume_in_sequence_after_the_notices(self):
        self.worker(yield_notice_after_s=0.03, yield_notice_interval_s=0.03)
        wait_for(lambda: len(self.frames()) >= 1, what="a preview")
        self.mission.set()
        wait_for(lambda: len(self.notices()) >= 2, what="notices")
        self.mission.clear()
        wait_for(lambda: len(self.frames()) >= 3, what="previews after the stand-aside")
        seqs = [header["preview_seq"] for header, _ in self.frames()]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)), "the notices left no hole in the preview numbering")

    def test_a_second_long_stand_aside_is_timed_from_its_own_start(self):
        self.worker(yield_notice_after_s=0.08, yield_notice_interval_s=0.02)
        wait_for(lambda: len(self.frames()) >= 1, what="a preview")
        self.mission.set()
        wait_for(lambda: len(self.notices()) >= 1, what="a notice")
        self.mission.clear()
        wait_for(lambda: len(self.frames()) >= 2, what="a preview between")
        count = len(self.notices())
        self.mission.set()
        time.sleep(0.04)
        self.assertEqual(len(self.notices()), count, "the bound starts again at each stand-aside")
        wait_for(lambda: len(self.notices()) > count, what="a notice of the second stand-aside")
        self.mission.clear()
        self.assertGreaterEqual(self.notices()[-1][0]["yielding_for_ms"], 80)

    def test_a_camera_that_delivers_nothing_sends_no_notice(self):
        worker = self.worker(yield_notice_after_s=0.02, yield_notice_interval_s=0.02)
        wait_for(lambda: len(self.frames()) >= 1, what="a preview")
        self.feeder.paused.set()
        time.sleep(0.02)
        self.mission.set()
        time.sleep(0.3)
        self.mission.clear()
        self.assertEqual(self.notices(), [], "silence from the camera stays silence: the reader must see it as a stall")
        self.assertEqual(worker.status()["yield_notices"], 0)


class TheServiceBuildsWorkersWithTheDefaults(unittest.TestCase):
    def test_the_default_bound_is_well_inside_the_reader_s_two_second_window(self):
        self.assertLessEqual(cs.YIELD_NOTICE_AFTER_S + cs.YIELD_NOTICE_INTERVAL_S, 2.0,
                             "a notice must arrive before a reader's freshness window of 2 s ends")
        self.assertGreater(cs.YIELD_NOTICE_AFTER_S, 0.5, "an ordinary capture (about 0.45 s at 20 fps) stays silent")

    def test_pack_yield_notice_is_the_preview_frame_without_a_picture(self):
        message = cs.pack_yield_notice({"schema": cs.PREVIEW_YIELD_SCHEMA, "role": "wrist"})
        header, payload = unpack(message)
        self.assertEqual((header["schema"], payload), (cs.PREVIEW_YIELD_SCHEMA, b""))


if __name__ == "__main__":
    unittest.main()
