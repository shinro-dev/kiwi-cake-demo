#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_camera_service.py: the camera service against a FAKE capture device. Proves the software contract:
# a mission capture returns a frame STAMPED after the request also when older frames are still buffered (timestamps
# decide, not read order); without a trustworthy driver timestamp the metadata says dequeue_time_only and the buffer is
# drained; a stalled preview consumer cannot delay a mission capture and previews are dropped, never queued; names are
# unique and publication is atomic; the shared lock excludes the legacy probe in both directions; the front camera stays
# closed unless enabled; no output names a device.
# It does NOT establish what the real capture API reports on the real cameras, nor anything about USB.
# No cv2.VideoCapture, no device file. One test uses a REP/REQ pair on this machine's loopback.
# Run directly: python3 autonomy/tests/test_camera_service.py
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import queue
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


cs = load("camera_service_under_test", "bin/pi/camera_service.py")

from camera_lifecycle import CameraIdentity, CameraIdentityUnavailable, LifecycleRecord  # noqa: E402
from camera_lock import LOCK_DIR_ENV_VAR, CameraLock, CameraLockHeld, lock_status  # noqa: E402

# Built at runtime: a tracked file never carries a device identity, and the tests need one to prove nothing repeats it.
WRIST_ID = "/dev/v4l/" + "by-id/" + "usb-" + "Test_Cam_" + "SN" + "0001" + "-video-index0"
FRONT_ID = "/dev/v4l/" + "by-id/" + "usb-" + "Test_Cam_" + "SN" + "0002" + "-video-index0"

WIDTH, HEIGHT = 32, 24
PERIOD_NS = 33_333_333  # one frame of the 30 fps the fake device reports
T0 = 1_000_000_000_000  # an arbitrary point of the fake Pi monotonic clock
NEGOTIATED = {"pixel_format": "MJPG", "width": WIDTH, "height": HEIGHT, "source_fps_reported": 30.0, "buffer_depth": 4, "buffer_depth_source": "reported"}


def frame(index, width=WIDTH, height=HEIGHT):
    """A frame that says which one it is in every pixel of its first channel."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 0] = index % 256
    image[:, :, 1] = (index // 256) % 256
    return image


def frame_index(image):
    return int(image[0, 0, 0]) + 256 * int(image[0, 0, 1])


def wait_for(condition, timeout_s=5.0, what="condition"):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.002)
    raise AssertionError(f"timed out waiting for {what}")


class ScriptedDevice:
    """Delivers exactly the frames the test feeds, with the timestamps the test chose. grab() blocks until fed."""

    def __init__(self, log, role="wrist", negotiated=None, open_error=None):
        self.log, self.role, self._negotiated, self._open_error = log, role, dict(negotiated or NEGOTIATED), open_error
        self._frames, self._ended = queue.Queue(), False

    def open(self, identity):
        self.log.append(("open", self.role))
        if self._open_error is not None:
            raise self._open_error(identity)

    def negotiated(self):
        return dict(self._negotiated)

    def feed(self, index, driver_ns, dequeue_ns, image=None):
        self._frames.put(cs.Grabbed(frame=frame(index) if image is None else image, dequeue_mono_ns=dequeue_ns, driver_timestamp_ns=driver_ns))

    def feed_empty_read(self):
        self._frames.put(None)

    def feed_error(self, error):
        self._frames.put(error)

    def end(self):
        self._ended = True
        self._frames.put(None)

    def grab(self):
        if self._ended:
            time.sleep(0.001)
            return None
        item = self._frames.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.log.append(("close", self.role))


class RecyclingDevice(ScriptedDevice):
    """A capture API that hands back a VIEW of ONE buffer and fills that same buffer again on the next read, which is
    what a V4L2 backend may do with its mapped buffers. Every frame it ever delivers is the same object."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.buffer = frame(0)

    def grab(self):
        grabbed = super().grab()
        if grabbed is None:
            return None
        self.buffer[:] = grabbed.frame
        return cs.Grabbed(frame=self.buffer, dequeue_mono_ns=grabbed.dequeue_mono_ns, driver_timestamp_ns=grabbed.driver_timestamp_ns)


class LiveDevice:
    """Free running on the real monotonic clock: a frame every couple of milliseconds, stamped like a driver would."""

    def __init__(self, log, role="wrist", driver_timestamps=True, width=WIDTH, height=HEIGHT):
        self.log, self.role, self.driver_timestamps, self.size, self.count = log, role, driver_timestamps, (width, height), 0

    def open(self, identity):
        self.log.append(("open", self.role))

    def negotiated(self):
        return dict(NEGOTIATED, width=self.size[0], height=self.size[1], source_fps_reported=500.0)

    def grab(self):
        time.sleep(0.002)
        captured = time.monotonic_ns() - 500_000
        self.count += 1
        return cs.Grabbed(frame=frame(self.count, *self.size), dequeue_mono_ns=time.monotonic_ns(), driver_timestamp_ns=captured if self.driver_timestamps else None)

    def close(self):
        self.log.append(("close", self.role))


class CollectingPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)
        return True

    def close(self):
        pass


class StalledPublisher:
    """A preview consumer that never takes the message: publish() does not return until the test lets go."""

    def __init__(self):
        self.entered, self.let_go, self.calls = threading.Event(), threading.Event(), 0

    def publish(self, message):
        self.calls += 1
        self.entered.set()
        self.let_go.wait(30)
        return True

    def close(self):
        pass


def unpack(message):
    assert message[:5] == cs.PREVIEW_MAGIC
    (length,) = struct.unpack(">I", message[5:9])
    return json.loads(message[9:9 + length].decode()), message[9 + length:]


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.env = {"KC_WRIST_CAMERA_ID": WRIST_ID, "KC_FRONT_CAMERA_ID": FRONT_ID, LOCK_DIR_ENV_VAR: str(self.dir / "locks")}
        self.store_root = self.dir / "store"
        self.log, self.devices, self.publishers = [], {}, {}

    def build(self, *argv, device=ScriptedDevice, publisher=None, start=True, env=None, **device_kwargs):
        args = cs.build_parser().parse_args(["--width", str(WIDTH), "--height", str(HEIGHT), "--store-dir", str(self.store_root), "--lock-wait-s", "0.1", *argv])

        def device_factory(role, width, height, source_fps):
            self.devices[role] = device(self.log, role=role, **device_kwargs)
            return self.devices[role]

        def publisher_factory(role, port):
            self.publishers[role] = publisher()
            return self.publishers[role]

        service = cs.build_service(args, self.env if env is None else env, device_factory=device_factory,
                                   publisher_factory=publisher_factory if publisher is not None else None)
        if start:
            self.start(service, args.lock_wait_s)
        return service

    def start(self, service, lock_wait_s=0.1):
        service.start(lock_wait_s)
        self.addCleanup(self.shutdown, service)

    def shutdown(self, service):
        for publisher in self.publishers.values():
            if isinstance(publisher, StalledPublisher):
                publisher.let_go.set()
        for device in self.devices.values():
            if isinstance(device, ScriptedDevice):
                device.end()
        service.stop()

    def warm_up(self, device, count=6, driver_clock_offset_ns=0):
        """Frames from well before the request. With plausible driver timestamps (offset 0) the stream becomes trusted;
        driver_clock_offset_ns=None is a capture API that reports no timestamp, anything else is another clock."""
        for index in range(count):
            at = T0 - (count - index + 10) * PERIOD_NS
            device.feed(900 + index, driver_ns=None if driver_clock_offset_ns is None else at + driver_clock_offset_ns, dequeue_ns=at + 2_000_000)
        worker = next(iter(self.service_workers.values()))
        wait_for(lambda: worker.frames_seen >= count, what="warm-up frames")

    def capture_in_background(self, service, request, request_mono_ns):
        box = {}
        thread = threading.Thread(target=lambda: box.update(reply=service.handle(request, request_mono_ns)), daemon=True)
        thread.start()
        wait_for(lambda: service.workers[request["role"]]._pending or "reply" in box, what="the capture to be registered")
        return thread, box

    def read_png(self, metadata):
        import cv2

        path = self.store_root / metadata["relative_path"]
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), metadata["sha256"])
        self.assertEqual(len(data), metadata["bytes"])
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)


class CapturedAfterTheRequest(ServiceCase):
    def scripted(self, *argv):
        service = self.build("--no-preview", *argv)
        self.service_workers = service.workers
        return service, self.devices["wrist"]

    def test_buffered_frames_read_after_the_request_are_not_returned(self):
        service, device = self.scripted()
        self.warm_up(device)
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist", "settle_frames": 0}, T0)
        # Three frames the driver had already buffered: captured BEFORE the request, read AFTER it.
        for index, age in ((1, 3), (2, 2), (3, 1)):
            device.feed(index, driver_ns=T0 - age * PERIOD_NS, dequeue_ns=T0 + index * 1_000_000)
        # Its reception began after the request but within one frame period: the exposure may have started before it.
        device.feed(4, driver_ns=T0 + PERIOD_NS // 2, dequeue_ns=T0 + PERIOD_NS // 2 + 2_000_000)
        device.feed(5, driver_ns=T0 + 2 * PERIOD_NS, dequeue_ns=T0 + 2 * PERIOD_NS + 2_000_000)
        thread.join(5)
        reply = box["reply"]
        self.assertTrue(reply["ok"], reply)
        meta = reply["frame"]
        self.assertEqual(frame_index(self.read_png(meta)), 5, "read order alone would have returned frame 1")
        self.assertEqual(meta["timestamp_semantics"], "driver_buffer_timestamp")
        self.assertEqual((meta["timestamp_property"], meta["timestamp_clock"], meta["timestamp_marks"]), ("CAP_PROP_POS_MSEC", "CLOCK_MONOTONIC", "start_of_frame_reception"))
        self.assertEqual(meta["clock_domain"], "pi_monotonic")
        self.assertEqual(meta["request_mono_ns"], T0)
        self.assertEqual(meta["exposure_guard_ns"], int(1e9 / 30.0))
        self.assertEqual(meta["exposure_guard_source"], "source_fps_reported")
        self.assertGreater(meta["capture_mono_ns"], meta["request_mono_ns"] + meta["exposure_guard_ns"])
        self.assertEqual(meta["capture_mono_ns"], T0 + 2 * PERIOD_NS)
        self.assertEqual(meta["frames_discarded_stale"], 4)
        self.assertEqual(meta["frames_drained"], 0)
        self.assertIsNone(meta["fallback_reason"])
        self.assertEqual(meta["source_stamp"], {"session": service.session, "seq": 6 + 5, "sample_mono_ns": T0 + 2 * PERIOD_NS, "clock_domain": "pi_monotonic"})

    def test_settle_frames_are_counted_after_the_request_and_the_kept_frame_is_later_than_the_boundary(self):
        service, device = self.scripted()
        self.warm_up(device)
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist"}, T0)
        device.feed(1, driver_ns=T0 - PERIOD_NS, dequeue_ns=T0 + 1_000_000)  # buffered
        for index in range(2, 9):  # six settle frames (2..7), then the one that is kept (8)
            device.feed(index, driver_ns=T0 + index * PERIOD_NS, dequeue_ns=T0 + index * PERIOD_NS + 2_000_000)
        thread.join(5)
        meta = box["reply"]["frame"]
        self.assertEqual(frame_index(self.read_png(meta)), 8)
        self.assertEqual((meta["settle_frames"], meta["frames_discarded_settle"], meta["frames_discarded_stale"]), (6, 6, 1))
        self.assertEqual(meta["settle_boundary_mono_ns"], T0 + 7 * PERIOD_NS)
        self.assertGreater(meta["capture_mono_ns"], meta["settle_boundary_mono_ns"])
        self.assertGreater(meta["settle_boundary_mono_ns"], meta["fresh_after_mono_ns"])
        record = LifecycleRecord(**box["reply"]["lifecycle_record"])  # the legacy consumers' record, same dataclass
        self.assertTrue(record.frame_valid and record.open_ok)
        self.assertFalse(record.close_ok, "this provider does not close the device per capture, and says so")
        self.assertEqual(record.frame_shape, [HEIGHT, WIDTH, 3])
        self.assertEqual(record.settle_frames_requested, 6)

    def test_a_frame_dequeued_before_the_request_never_counts(self):
        selector = cs.FreshFrameSelector(request_mono_ns=T0, settle_frames=0, exposure_guard_ns=0, buffer_depth=0)
        early = cs.Stamped(seq=1, frame=frame(1), dequeue_mono_ns=T0 - 1, capture_mono_ns=None)
        self.assertFalse(selector.offer(early))
        self.assertEqual(selector.frames_before_request, 1)
        self.assertEqual(selector.semantics, "driver_buffer_timestamp", "a frame from before the request decides nothing")

    def test_only_dequeue_time_the_metadata_says_so_and_the_buffer_is_drained(self):
        service, device = self.scripted()
        self.warm_up(device, driver_clock_offset_ns=None)
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist", "settle_frames": 2}, T0)
        for index in range(1, 8):  # buffer depth 4 + settle 2 are drained; the 7th is kept
            device.feed(index, driver_ns=None, dequeue_ns=T0 + index * PERIOD_NS)
        thread.join(5)
        meta = box["reply"]["frame"]
        self.assertEqual(frame_index(self.read_png(meta)), 7)
        self.assertEqual(meta["timestamp_semantics"], "dequeue_time_only")
        self.assertEqual(meta["fallback_reason"], "no trustworthy driver timestamp on this stream")
        self.assertEqual((meta["frames_drained"], meta["buffer_depth"]), (6, 4))
        for key in ("capture_mono_ns", "timestamp_property", "timestamp_clock", "timestamp_marks", "settle_boundary_mono_ns", "fresh_after_mono_ns"):
            self.assertIsNone(meta[key], key)
        self.assertIsNone(meta["source_stamp"]["sample_mono_ns"], "a dequeue time is not the source's sample time")
        self.assertEqual(meta["dequeue_mono_ns"], T0 + 7 * PERIOD_NS)

    def test_a_driver_timestamp_from_another_clock_is_not_believed(self):
        service, device = self.scripted()
        wall = 1_790_000_000_000_000_000  # a realtime clock value: nowhere near the monotonic dequeue time
        self.warm_up(device, driver_clock_offset_ns=wall)
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist", "settle_frames": 0}, T0)
        for index in range(1, 6):
            device.feed(index, driver_ns=wall + index * PERIOD_NS, dequeue_ns=T0 + index * PERIOD_NS)
        thread.join(5)
        meta = box["reply"]["frame"]
        self.assertEqual(meta["timestamp_semantics"], "dequeue_time_only")
        self.assertIsNone(meta["capture_mono_ns"])
        status = service.handle({"op": "status"})["status"]["cameras"]["wrist"]["driver_timestamp"]
        self.assertFalse(status["trusted"])
        self.assertIn("not the monotonic clock", status["last_violation"])

    def test_trust_lost_in_the_middle_of_a_capture_restarts_the_weaker_count(self):
        service, device = self.scripted()
        self.warm_up(device)
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist", "settle_frames": 1}, T0)
        device.feed(1, driver_ns=T0 + 2 * PERIOD_NS, dequeue_ns=T0 + 2 * PERIOD_NS + 1_000_000)  # a settle frame, by timestamp
        device.feed(2, driver_ns=T0 + 2 * PERIOD_NS, dequeue_ns=T0 + 3 * PERIOD_NS)  # the timestamp did not increase
        for index in range(3, 9):
            device.feed(index, driver_ns=T0 + index * PERIOD_NS, dequeue_ns=T0 + index * PERIOD_NS + 1_000_000)
        thread.join(5)
        meta = box["reply"]["frame"]
        self.assertEqual(meta["timestamp_semantics"], "dequeue_time_only")
        self.assertIn("restarted", meta["fallback_reason"])
        self.assertEqual(meta["frames_drained"], 5)  # depth 4 + settle 1, counted from the frame that lost the trust
        self.assertEqual(frame_index(self.read_png(meta)), 7)

    def test_the_guard_follows_the_slower_of_the_two_rates_a_stream_shows(self):
        # A driver commonly REPORTS the rate that was asked for. When the stream really runs slower, one reported period
        # is shorter than one real frame and stops covering the exposure that precedes a start-of-reception timestamp.
        service, device = self.scripted()
        self.service_workers = service.workers
        slow = 100_000_000  # the stream really delivers 10 fps while the negotiated facts report 30
        for index in range(6):  # dequeued the instant they were stamped, so the measured rate is one clock only
            at = T0 - (16 - index) * slow
            device.feed(900 + index, driver_ns=at, dequeue_ns=at)
        worker = service.workers["wrist"]
        wait_for(lambda: worker.frames_seen >= 6, what="warm-up frames")
        self.assertEqual(worker.negotiated["source_fps_reported"], 30.0)
        self.assertEqual(worker.rate.fps(), 10.0)
        self.assertEqual(worker.exposure_guard(), (slow, "source_fps_measured"))
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist", "settle_frames": 0}, T0)
        # Reception began 40 ms after the request: fresh under one REPORTED period, still inside one REAL frame.
        device.feed(1, driver_ns=T0 + 40_000_000, dequeue_ns=T0 + 42_000_000)
        device.feed(2, driver_ns=T0 + 150_000_000, dequeue_ns=T0 + 152_000_000)
        thread.join(5)
        meta = box["reply"]["frame"]
        self.assertEqual(frame_index(self.read_png(meta)), 2, "the reported rate alone would have returned frame 1")
        self.assertEqual((meta["exposure_guard_ns"], meta["exposure_guard_source"]), (slow, "source_fps_measured"))
        self.assertEqual(meta["frames_discarded_stale"], 1)
        self.assertGreater(meta["capture_mono_ns"], meta["request_mono_ns"] + meta["exposure_guard_ns"])

    def test_a_capture_api_that_reuses_one_buffer_cannot_change_the_published_frame(self):
        service = self.build("--no-preview", device=RecyclingDevice)
        self.service_workers, device = service.workers, self.devices["wrist"]
        released, encoded = threading.Event(), []

        def gated_png(image):
            encoded.append(frame_index(image))
            released.wait(5)  # the capture thread reads on while the mission thread still holds "its" frame
            return cs.encode_png(image)

        service.store = cs.FrameStore(self.store_root, png_encoder=gated_png)
        self.warm_up(device)
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist", "settle_frames": 0}, T0)
        device.feed(11, driver_ns=T0 + 2 * PERIOD_NS, dequeue_ns=T0 + 2 * PERIOD_NS + 1_000_000)
        wait_for(lambda: encoded, what="the mission thread to reach the encoder")
        for index in (12, 13, 14):  # the same object, filled again
            device.feed(index, driver_ns=T0 + index * PERIOD_NS, dequeue_ns=T0 + index * PERIOD_NS + 1_000_000)
        wait_for(lambda: frame_index(device.buffer) == 14, what="the device to refill its buffer")
        released.set()
        thread.join(5)
        meta = box["reply"]["frame"]
        self.assertEqual(encoded, [11], "the frame handed to the store is the one that was selected")
        self.assertEqual(frame_index(self.read_png(meta)), 11, "the published image is not the one the timestamps describe")
        self.assertEqual(meta["capture_mono_ns"], T0 + 2 * PERIOD_NS)
        self.assertEqual(meta["source_stamp"]["seq"], 6 + 1)

    def test_no_frame_after_the_request_is_a_reported_timeout(self):
        service, device = self.scripted()
        reply = service.handle({"op": "capture", "role": "wrist", "timeout_s": 0.2}, T0)
        self.assertEqual((reply["ok"], reply["reason"]), (False, "capture_timeout"))
        self.assertEqual(list(self.store_root.glob("**/*.png")), [])

    def test_a_frame_of_another_size_is_refused(self):
        service, device = self.scripted()
        self.warm_up(device, driver_clock_offset_ns=None)
        thread, box = self.capture_in_background(service, {"op": "capture", "role": "wrist", "settle_frames": 0}, T0)
        for index in range(1, 7):
            device.feed(index, driver_ns=None, dequeue_ns=T0 + index * PERIOD_NS, image=frame(index, width=10, height=10))
        thread.join(5)
        self.assertEqual(box["reply"]["reason"], "resolution_mismatch")
        self.assertEqual(list(self.store_root.glob("**/*.png")), [])

    def test_requests_are_validated_and_always_answered(self):
        service, _ = self.scripted()
        for request in (None, [], {"role": "wrist"}, {"op": "fly"}, {"op": "capture", "role": "side"}, {"op": "capture", "role": "wrist", "session": "../x"},
                        {"op": "capture", "role": "wrist", "session": ".."}, {"op": "capture", "role": "wrist", "settle_frames": -1},
                        {"op": "capture", "role": "wrist", "settle_frames": True}, {"op": "capture", "role": "wrist", "timeout_s": 600}):
            reply = service.handle(request, T0)
            self.assertEqual((reply["ok"], reply["reason"]), (False, "invalid_request"), request)
        self.assertTrue(service.handle({"op": "ping"})["ok"])


class DriverClock(unittest.TestCase):
    def grabbed(self, driver_ns, dequeue_ns):
        return cs.Grabbed(frame=frame(0), dequeue_mono_ns=dequeue_ns, driver_timestamp_ns=driver_ns)

    def test_trust_needs_a_run_of_plausible_frames_and_one_bad_frame_withdraws_it(self):
        assessor = cs.DriverClockAssessor()
        results = [assessor.assess(self.grabbed(T0 + i * PERIOD_NS, T0 + i * PERIOD_NS + 1_000_000)) for i in range(6)]
        self.assertEqual(results[:4], [None] * 4)
        self.assertEqual(results[4:], [T0 + 4 * PERIOD_NS, T0 + 5 * PERIOD_NS])
        self.assertIsNone(assessor.assess(self.grabbed(T0 + 5 * PERIOD_NS, T0 + 6 * PERIOD_NS)))  # did not increase
        self.assertFalse(assessor.trusted)
        self.assertEqual(assessor.violations, 1)

    def test_a_timestamp_after_its_own_dequeue_or_far_before_it_is_implausible(self):
        assessor = cs.DriverClockAssessor()
        self.assertIsNone(assessor.assess(self.grabbed(T0 + 10_000_000, T0)))
        self.assertIsNone(assessor.assess(self.grabbed(T0, T0 + 6_000_000_000)))
        self.assertIsNone(assessor.assess(self.grabbed(None, T0)))
        self.assertEqual(assessor.violations, 3)


class FakeVideoCapture:
    """Stands in for the capture API object. Not cv2: no device is involved."""

    instances = []

    def __init__(self, source, backend):
        self.source, self.backend, self.sets, self.released = source, backend, [], False
        self.properties = {0: 12_345_678.901, 3: 1280.0, 4: 720.0, 5: 15.0, 6: float(ord("M") | (ord("J") << 8) | (ord("P") << 16) | (ord("G") << 24)), 38: 4.0}
        self.next_read = (True, frame(1, 1280, 720))
        FakeVideoCapture.instances.append(self)

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.sets.append((prop, value))
        return True

    def get(self, prop):
        return self.properties.get(prop, 0.0)

    def read(self):
        return self.next_read

    def release(self):
        self.released = True


class OpenCvDevice(unittest.TestCase):
    def test_it_opens_like_the_legacy_backend_and_adds_only_the_source_rate_request(self):
        device = cs.OpenCvCaptureDevice(1280, 720, source_fps=15, video_capture_factory=FakeVideoCapture, clock_ns=lambda: 99)
        device.open(CameraIdentity("wrist", WRIST_ID))
        capture = FakeVideoCapture.instances[-1]
        self.assertEqual((capture.source, capture.backend), (WRIST_ID, 200))
        mjpg = ord("M") | (ord("J") << 8) | (ord("P") << 16) | (ord("G") << 24)
        self.assertEqual(capture.sets, [(6, mjpg), (3, 1280), (4, 720), (5, 15.0)])
        self.assertEqual(device.negotiated(), {"pixel_format": "MJPG", "width": 1280, "height": 720, "source_fps_reported": 15.0, "buffer_depth": 4, "buffer_depth_source": "reported"})
        grabbed = device.grab()
        self.assertEqual((grabbed.dequeue_mono_ns, grabbed.driver_timestamp_ns), (99, 12_345_678_901_000))
        self.assertEqual(grabbed.frame.shape, (720, 1280, 3))
        device.close()
        self.assertTrue(capture.released)

    def test_no_rate_request_unless_asked_and_missing_properties_are_said_to_be_missing(self):
        device = cs.OpenCvCaptureDevice(1280, 720, video_capture_factory=FakeVideoCapture)
        device.open(CameraIdentity("wrist", WRIST_ID))
        capture = FakeVideoCapture.instances[-1]
        self.assertEqual([prop for prop, _ in capture.sets], [6, 3, 4])
        capture.properties = {3: 1280.0, 4: 720.0}
        self.assertEqual(device.negotiated(), {"pixel_format": None, "width": 1280, "height": 720, "source_fps_reported": None, "buffer_depth": 4, "buffer_depth_source": "assumed"})
        self.assertIsNone(device.grab().driver_timestamp_ns, "a zero POS_MSEC is no timestamp")
        capture.next_read = (False, None)
        self.assertIsNone(device.grab())
        capture.next_read = (True, frame(1, 640, 480))
        with self.assertRaises(OSError):
            device.grab()  # the legacy rule: a delivered frame of another size is an error, never a quiet downgrade


class PreviewIsolation(ServiceCase):
    def test_a_stalled_preview_consumer_does_not_delay_a_mission_capture(self):
        service = self.build(device=LiveDevice, publisher=StalledPublisher)
        publisher = self.publishers["wrist"]
        self.assertTrue(publisher.entered.wait(5), "the preview never reached its consumer")
        worker, preview = service.workers["wrist"], service.previews["wrist"]
        wait_for(lambda: worker.assessor.trusted, what="driver timestamps to be trusted")
        seen_before = worker.frames_seen
        started = time.monotonic()
        replies = [service.handle({"op": "capture", "role": "wrist"}) for _ in range(3)]
        elapsed = time.monotonic() - started
        self.assertTrue(all(reply["ok"] for reply in replies), replies)
        self.assertLess(elapsed, 3.0, "three captures of seven frames at 2 ms a frame")
        self.assertGreater(worker.frames_seen, seen_before + 20, "the capture thread kept reading all along")
        self.assertEqual(publisher.calls, 1, "the preview thread is still inside its stalled consumer")
        self.assertEqual(preview.published, 0)
        self.assertGreater(preview.slot.dropped, 20, "obsolete preview frames are dropped")
        self.assertLessEqual(preview.slot.depth(), 1, "and never queued")
        for reply in replies:
            self.assertEqual(reply["frame"]["timestamp_semantics"], "driver_buffer_timestamp")
            self.assertGreater(reply["frame"]["capture_mono_ns"], reply["frame"]["request_mono_ns"] + reply["frame"]["exposure_guard_ns"])

    def test_a_stalled_preview_encoder_does_not_delay_it_either(self):
        service = self.build(device=LiveDevice, publisher=CollectingPublisher, start=False)
        entered, let_go = threading.Event(), threading.Event()
        self.addCleanup(let_go.set)

        def stalled_encoder(image, width, quality):
            entered.set()
            let_go.wait(30)
            return b"", 1, 1

        service.previews["wrist"]._encoder = stalled_encoder
        self.start(service)
        self.assertTrue(entered.wait(5))
        reply = service.handle({"op": "capture", "role": "wrist", "timeout_s": 3})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(self.publishers["wrist"].messages, [])

    def test_the_slot_holds_one_item_and_put_never_waits(self):
        slot = cs.LatestSlot()
        started = time.monotonic()
        for index in range(10_000):
            slot.put(index)  # nobody takes anything
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual((slot.depth(), slot.dropped), (1, 9_999))
        self.assertEqual(slot.take(0), 9_999, "the latest one")
        self.assertIsNone(slot.take(0))

    def test_previews_are_the_latest_frame_downscaled_with_their_capture_clock(self):
        service = self.build("--preview-fps", "15", "--preview-width", "16", device=LiveDevice, publisher=CollectingPublisher)
        messages = self.publishers["wrist"].messages
        wait_for(lambda: len(messages) >= 4, what="four previews")
        headers = [unpack(message)[0] for message in list(messages)]
        header, jpeg = unpack(messages[-1])
        self.assertEqual(jpeg[:2], b"\xff\xd8", "a JPEG")
        self.assertEqual((header["schema"], header["role"], header["encoding"], header["clock_domain"]), ("kc.camera_service.preview/1", "wrist", "jpeg", "pi_monotonic"))
        self.assertEqual((header["width"], header["height"], header["source_width"], header["source_height"]), (16, 12, WIDTH, HEIGHT))
        self.assertEqual(header["session"], service.session)
        self.assertEqual((header["pixel_format"], header["source_fps_reported"]), ("MJPG", 500.0))
        self.assertEqual([h["preview_seq"] for h in headers], list(range(1, len(headers) + 1)))
        sources = [h["source_seq"] for h in headers]
        self.assertEqual(sources, sorted(set(sources)))
        self.assertGreater(sources[-1] - sources[0], len(sources), "a 15 fps preview of a 500 fps source skips frames instead of queueing them")
        self.assertNotIn(WRIST_ID, json.dumps(headers))

    def test_the_preview_stands_aside_during_a_mission_capture(self):
        service = self.build("--preview-fps", "15", device=LiveDevice, publisher=CollectingPublisher, start=False)
        service.mission_active.set()
        self.start(service)
        preview = service.previews["wrist"]
        wait_for(lambda: preview.skipped_for_mission >= 3, what="skipped previews")
        self.assertEqual(self.publishers["wrist"].messages, [])
        service.mission_active.clear()
        wait_for(lambda: len(self.publishers["wrist"].messages) >= 1, what="a preview after the capture")

    def test_a_capture_clears_the_flag_also_when_it_is_refused(self):
        service = self.build("--no-preview")
        self.assertFalse(service.handle({"op": "capture", "role": "wrist", "timeout_s": 0.1}, T0)["ok"])
        self.assertFalse(service.mission_active.is_set())


class Publication(ServiceCase):
    def test_the_final_name_appears_only_when_the_content_is_complete(self):
        data, seen = os.urandom(200_000), {}

        def before_link(temporary):
            seen["temporary"] = temporary.name
            seen["public"] = [p.name for p in self.dir.iterdir() if not p.name.startswith(".")]
            seen["complete"] = temporary.read_bytes() == data

        final = cs.publish_atomically(self.dir, "frame.png", data, _before_link=before_link)
        self.assertEqual(seen["public"], [], "no public name before the content is complete")
        self.assertTrue(seen["complete"])
        self.assertTrue(seen["temporary"].startswith(".") and seen["temporary"].endswith(".tmp"))
        self.assertEqual(final.read_bytes(), data)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["frame.png"], "the temporary name is gone")

    def test_a_name_is_never_reused(self):
        cs.publish_atomically(self.dir, "frame.png", b"first")
        with self.assertRaises(FileExistsError):
            cs.publish_atomically(self.dir, "frame.png", b"second")
        self.assertEqual((self.dir / "frame.png").read_bytes(), b"first")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["frame.png"])
        for name in (".hidden", "a/b", "", "../x"):
            with self.assertRaises(ValueError):
                cs.publish_atomically(self.dir, name, b"x")

    def test_a_failed_write_leaves_nothing_behind(self):
        def explode(temporary):
            raise OSError("disk full")

        with self.assertRaises(OSError):
            cs.publish_atomically(self.dir, "frame.png", b"data", _before_link=explode)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_reader_never_sees_a_partial_file(self):
        payloads = [os.urandom(1_500_000) for _ in range(12)]
        digests = {hashlib.sha256(p).hexdigest() for p in payloads}
        stop, problems, observed = threading.Event(), [], set()

        def reader():
            while not stop.is_set():
                for path in self.dir.glob("*.bin"):
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    observed.add(path.name)
                    if digest not in digests:
                        problems.append(path.name)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        for index, payload in enumerate(payloads):
            cs.publish_atomically(self.dir, f"{index:02d}.bin", payload)
            time.sleep(0.005)
        time.sleep(0.05)
        stop.set()
        thread.join(5)
        self.assertEqual(problems, [], "a reader saw a file whose content was not complete")
        self.assertGreaterEqual(len(observed), 1, "the reader was really reading while files were published")

    def test_every_capture_has_a_unique_name_also_across_a_restart_with_the_same_session_label(self):
        names = []
        for _ in range(2):  # the second round is a new service process on the same store: its counter starts again at 1
            service = self.build("--no-preview", device=LiveDevice)
            for _ in range(4):
                reply = service.handle({"op": "capture", "role": "wrist", "session": "run-0001", "settle_frames": 0})
                self.assertTrue(reply["ok"], reply)
                names.append(reply["frame"]["relative_path"])
            self.shutdown(service)
        self.assertEqual(len(set(names)), 8)
        self.assertTrue(all(name.startswith("run-0001/") for name in names))
        self.assertEqual([name.split("/")[1][:12] for name in names[:4]], [f"{n:06d}-wrist" for n in range(1, 5)])
        on_disk = sorted(p.name for p in (self.store_root / "run-0001").iterdir())
        self.assertEqual(len(on_disk), 16, "eight images and eight metadata files, no temporary file left")
        self.assertFalse([name for name in on_disk if name.startswith(".")])

    def test_metadata_is_published_last_and_describes_the_complete_image(self):
        service = self.build("--no-preview", device=LiveDevice)
        order = []
        original = cs.publish_atomically

        def recording(directory, name, data, _before_link=None):
            order.append(name.rsplit(".", 1)[1])
            return original(directory, name, data, _before_link)

        cs.publish_atomically = recording
        self.addCleanup(setattr, cs, "publish_atomically", original)
        reply = service.handle({"op": "capture", "role": "wrist", "settle_frames": 0})
        self.assertEqual(order, ["png", "json"])
        meta = reply["frame"]
        sidecar = json.loads((self.store_root / meta["session"] / meta["metadata_name"]).read_text())
        self.assertEqual(sidecar, meta)
        image = self.read_png(meta)
        self.assertEqual(image.shape, (HEIGHT, WIDTH, 3))
        self.assertEqual((meta["orientation"], meta["crop"], meta["encoding"], meta["provider"]), ("unrotated", "none", "png", "camera_service"))
        self.assertEqual(meta["negotiated"]["pixel_format"], "MJPG")
        self.assertEqual(meta["requested"], {"source_fps": None, "width": WIDTH, "height": HEIGHT, "pixel_format": "MJPG"})
        self.assertIsNotNone(meta["source_fps_measured"])
        self.assertFalse([key for key in meta if "publish" in key or "transfer" in key], "timestamps describe capture, never publication or transfer")

    def test_the_default_session_is_the_service_session(self):
        service = self.build("--no-preview", device=LiveDevice)
        meta = service.handle({"op": "capture", "role": "wrist", "settle_frames": 0})["frame"]
        self.assertEqual(meta["session"], service.session)
        self.assertRegex(service.session, r"^[A-Za-z0-9_.-]{1,64}$")

    def test_full_resolution_frames_come_back_pixel_identical(self):
        args = cs.build_parser().parse_args(["--no-preview", "--store-dir", str(self.store_root), "--lock-wait-s", "0.1"])
        self.assertEqual((args.width, args.height, args.settle_frames), (1280, 720, 6), "the legacy request: 1280x720 and six settle frames")
        device = LiveDevice(self.log, width=1280, height=720)
        self.devices["wrist"] = device
        service = cs.build_service(args, self.env, device_factory=lambda role, width, height, fps: device)
        self.start(service)
        reply = service.handle({"op": "capture", "role": "wrist"})  # at once: the first frames of a stream are waited for, not guessed at
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["frame"]["timestamp_semantics"], "driver_buffer_timestamp")
        image = self.read_png(reply["frame"])
        self.assertEqual(image.shape, (720, 1280, 3))
        self.assertTrue(np.array_equal(image, frame(frame_index(image), 1280, 720)), "lossless, unrotated, uncropped")
        self.assertEqual(reply["frame"]["frames_discarded_settle"], 6)


class SharedLock(ServiceCase):
    def run_probe(self, name):
        probe = load("camera_multiplex_probe_under_test", "bin/pi/camera_multiplex_probe.py")
        opened = []

        class Backend:
            def open(inner, identity):
                opened.append(identity.role)

            def read(inner):
                return frame(len(opened))

            def close(inner):
                pass

        out = self.dir / name
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = probe.main(["--cycles", "1", "--order", "wrist", "--settle-frames", "0", "--width", str(WIDTH), "--height", str(HEIGHT), "--out-dir", str(out), "--lock-wait-s", "0.1"],
                              env=self.env, backend_factory=lambda role, width, height: Backend())
        return code, opened, json.loads((out / "report.json").read_text())

    def test_the_legacy_probe_is_refused_while_the_service_owns_the_camera_and_runs_after_the_stop(self):
        service = self.build("--no-preview", device=LiveDevice)
        code, opened, report = self.run_probe("while-owned")
        self.assertEqual((code, opened), (3, []))
        self.assertEqual(report["refused"]["reason"], "camera_lock_held")
        self.assertIn(f"camera_service (pid {os.getpid()})", report["attempts"][0]["error"])
        self.assertTrue(service.handle({"op": "capture", "role": "wrist", "settle_frames": 0})["ok"], "the owner is not disturbed by the refused probe")
        self.shutdown(service)  # the explicit stop
        code, opened, report = self.run_probe("after-stop")
        self.assertEqual((code, opened, report["refused"]), (0, ["wrist"], None))

    def test_the_service_refuses_to_start_while_another_provider_holds_the_camera(self):
        with CameraLock("wrist", "camera_multiplex_probe", env=self.env):
            service = self.build("--no-preview", device=LiveDevice, start=False)
            with self.assertRaises(CameraLockHeld) as caught:
                service.start(0.1)
        self.assertIn("camera_multiplex_probe", str(caught.exception))
        self.assertEqual(self.log, [], "no device is opened without the lock")

    def test_lock_before_open_and_close_before_release(self):
        service = self.build("--no-preview", device=LiveDevice, start=False)
        worker, events = service.workers["wrist"], []
        acquire, release = worker.lock.acquire, worker.lock.release
        worker.lock.acquire = lambda **kw: (events.append("lock"), acquire(**kw))[1]
        worker.lock.release = lambda: (events.append("unlock"), release())[1]
        self.log = events
        self.devices["wrist"].log = events
        service.start(0.1)
        service.stop()
        self.assertEqual(events, ["lock", ("open", "wrist"), ("close", "wrist"), "unlock"])
        self.assertFalse(lock_status(["wrist"], env=self.env)["roles"]["wrist"]["held"])

    def test_a_failed_open_releases_the_lock_and_masks_the_device(self):
        def open_error(identity):
            return OSError(f"cannot open {identity.stable_id}: busy")

        service = self.build("--no-preview", start=False, open_error=open_error)
        with self.assertRaises(cs.CaptureFailed) as caught:
            service.start(0.1)
        self.assertEqual(caught.exception.reason, "camera_open_failed")
        self.assertNotIn(WRIST_ID, caught.exception.detail)
        self.assertIn("<wrist:", caught.exception.detail)
        self.assertEqual(self.log, [("open", "wrist"), ("close", "wrist")])
        self.assertFalse(lock_status(["wrist"], env=self.env)["roles"]["wrist"]["held"])

    def test_a_failed_camera_keeps_its_lock_until_the_service_is_stopped(self):
        service = self.build("--no-preview")
        self.devices["wrist"].feed_error(OSError(f"select() timeout on {WRIST_ID}"))
        wait_for(lambda: service.workers["wrist"].state == "failed", what="the camera to fail")
        reply = service.handle({"op": "capture", "role": "wrist"}, T0)
        self.assertEqual((reply["ok"], reply["reason"]), (False, "camera_failed"))
        self.assertNotIn(WRIST_ID, json.dumps(reply))
        self.assertIn(("close", "wrist"), self.log)
        self.assertTrue(lock_status(["wrist"], env=self.env)["roles"]["wrist"]["held"], "the handoff to the legacy path is never implicit")
        self.assertEqual(self.run_probe("after-failure")[0], 3)
        self.shutdown(service)
        self.assertFalse(lock_status(["wrist"], env=self.env)["roles"]["wrist"]["held"])

    def test_a_read_that_never_returns_keeps_the_device_and_the_lock_together(self):
        service = self.build("--no-preview")  # a scripted device that is fed nothing: grab() does not return
        worker = service.workers["wrist"]
        worker.STOP_JOIN_S = 0.2
        worker.stop()
        self.assertNotIn(("close", "wrist"), self.log, "a capture object is never closed under a read in progress")
        self.assertTrue(lock_status(["wrist"], env=self.env)["roles"]["wrist"]["held"], "and the lock is not released while the device is open")
        self.assertIn("left to process exit", worker.error)

    def test_a_stream_that_stops_delivering_fails_with_a_reason(self):
        service = self.build("--no-preview")
        service.workers["wrist"].MAX_CONSECUTIVE_EMPTY_READS = 5
        for _ in range(5):
            self.devices["wrist"].feed_empty_read()
        wait_for(lambda: service.workers["wrist"].state == "failed", what="the camera to fail")
        self.assertEqual(service.workers["wrist"].error, "no frame in 5 consecutive reads")

    def test_a_lock_that_no_longer_excludes_anybody_refuses_mission_captures(self):
        service = self.build("--no-preview", device=LiveDevice)
        os.unlink(service.workers["wrist"].lock.path)  # the runtime directory of an ended session was removed under the service
        reply = service.handle({"op": "capture", "role": "wrist", "settle_frames": 0})
        self.assertEqual((reply["ok"], reply["reason"]), (False, "camera_lock_lost"))
        self.assertFalse(service.handle({"op": "status"})["status"]["cameras"]["wrist"]["lock"]["excludes"])


class FrontCamera(ServiceCase):
    def test_the_front_camera_stays_closed_unless_enabled(self):
        service = self.build("--no-preview", device=LiveDevice)
        self.assertEqual(list(service.workers), ["wrist"])
        self.assertEqual(self.log, [("open", "wrist")])
        self.assertNotIn("front", self.devices, "not even a device object is made for a camera that is off")
        locks = lock_status(["wrist", "front"], env=self.env)["roles"]
        self.assertEqual((locks["wrist"]["held"], locks["front"]["held"]), (True, False))
        reply = service.handle({"op": "capture", "role": "front"})
        self.assertEqual((reply["ok"], reply["reason"]), (False, "camera_not_enabled"))
        self.assertIn("--enable-front", reply["detail"])
        self.assertEqual(self.log, [("open", "wrist")], "a request does not open it either")
        self.assertEqual(service.handle({"op": "status"})["status"]["cameras"]["front"], {"state": "off"})

    def test_off_means_its_identity_is_not_even_read(self):
        env = {key: value for key, value in self.env.items() if key != "KC_FRONT_CAMERA_ID"}
        service = self.build("--no-preview", device=LiveDevice, env=env)
        self.assertEqual(list(service.workers), ["wrist"])

    def test_enabled_explicitly_it_is_owned_like_the_wrist(self):
        service = self.build("--enable-front", device=LiveDevice, publisher=CollectingPublisher)
        self.assertEqual(sorted(service.workers), ["front", "wrist"])
        self.assertEqual(sorted(self.log), [("open", "front"), ("open", "wrist")])
        locks = lock_status(["wrist", "front"], env=self.env)["roles"]
        self.assertTrue(locks["wrist"]["held"] and locks["front"]["held"])
        reply = service.handle({"op": "capture", "role": "front", "settle_frames": 0})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["frame"]["role"], "front")
        self.assertEqual(sorted(self.publishers), ["front", "wrist"], "one preview socket per role")

    def test_enabling_it_without_an_identity_is_an_error_not_a_quiet_wrist_only_service(self):
        env = {key: value for key, value in self.env.items() if key != "KC_FRONT_CAMERA_ID"}
        with self.assertRaises(CameraIdentityUnavailable):
            self.build("--enable-front", device=LiveDevice, env=env, start=False)
        self.assertEqual(self.log, [])

    def test_a_second_camera_that_cannot_be_owned_releases_the_first(self):
        with CameraLock("front", "camera_multiplex_probe", env=self.env):
            service = self.build("--enable-front", "--no-preview", device=LiveDevice, start=False)
            with self.assertRaises(CameraLockHeld):
                service.start(0.1)
            self.assertEqual(self.log, [("open", "wrist"), ("close", "wrist")])
            self.assertFalse(lock_status(["wrist"], env=self.env)["roles"]["wrist"]["held"])


class Privacy(ServiceCase):
    def test_no_output_names_a_device(self):
        service = self.build("--enable-front", device=LiveDevice, publisher=CollectingPublisher)
        reply = service.handle({"op": "capture", "role": "wrist", "settle_frames": 0})
        wait_for(lambda: self.publishers["wrist"].messages, what="a preview")
        texts = [json.dumps(reply), json.dumps(service.handle({"op": "status"})), json.dumps(unpack(self.publishers["wrist"].messages[0])[0])]
        texts += [path.read_text() for path in self.store_root.glob("**/*.json")]
        texts += [path.read_text() for path in (self.dir / "locks").glob("**/*.lock")]
        for text in texts:
            self.assertNotIn(WRIST_ID, text)
            self.assertNotIn(FRONT_ID, text)
            self.assertNotIn("/dev/", text)
        self.assertEqual(reply["frame"]["identity_fingerprint"], CameraIdentity("wrist", WRIST_ID).fingerprint)

    def test_the_sockets_are_loopback_only_and_there_is_no_option_to_change_that(self):
        self.assertEqual(cs.BIND_HOST, "127.0.0.1")
        options = [option for action in cs.build_parser()._actions for option in action.option_strings]
        self.assertFalse([option for option in options if "host" in option or "bind" in option or "addr" in option], options)

    def test_without_an_identity_the_service_refuses_to_start_and_names_the_variable_not_a_device(self):
        with self.assertRaises(SystemExit) as caught:
            cs.main(["--store-dir", str(self.store_root)], env={LOCK_DIR_ENV_VAR: str(self.dir / "locks")})
        self.assertIn("KC_WRIST_CAMERA_ID", str(caught.exception))
        with self.assertRaises(SystemExit) as caught:
            cs.main(["--store-dir", str(self.store_root)], env={"KC_WRIST_CAMERA_ID": "/dev/" + "video0", LOCK_DIR_ENV_VAR: str(self.dir / "locks")})
        self.assertIn("does not survive", str(caught.exception))

    def test_lock_status_opens_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cs.main(["--lock-status"], env=self.env), 0)
        self.assertEqual(json.loads(out.getvalue())["roles"], {"wrist": {"held": False, "holder": None}, "front": {"held": False, "holder": None}})


class MissionSocket(ServiceCase):
    def test_request_reply_on_loopback_and_a_malformed_request_still_gets_its_reply(self):
        import zmq

        service = self.build("--no-preview", device=LiveDevice)
        wait_for(lambda: service.workers["wrist"].assessor.trusted, what="driver timestamps to be trusted")
        context = zmq.Context()
        rep, req = context.socket(zmq.REP), context.socket(zmq.REQ)
        for socket in (rep, req):
            socket.setsockopt(zmq.LINGER, 0)
        port = rep.bind_to_random_port(f"tcp://{cs.BIND_HOST}")
        req.connect(f"tcp://{cs.BIND_HOST}:{port}")
        req.setsockopt(zmq.RCVTIMEO, 5000)
        server = threading.Thread(target=service.serve, args=(rep, 20), daemon=True)
        server.start()
        try:
            before = time.monotonic_ns()
            req.send(json.dumps({"op": "capture", "role": "wrist", "session": "run-0002"}).encode())
            reply = json.loads(req.recv().decode())
            self.assertTrue(reply["ok"], reply)
            self.assertGreaterEqual(reply["frame"]["request_mono_ns"], before, "the request time is taken when the request arrives")
            self.assertGreater(reply["frame"]["capture_mono_ns"], reply["frame"]["request_mono_ns"])
            self.assertTrue((self.store_root / reply["frame"]["relative_path"]).exists(), "published before the reply is sent")
            req.send(b"\xff not json")
            self.assertEqual(json.loads(req.recv().decode())["reason"], "invalid_request")
            req.send(json.dumps({"op": "status"}).encode())
            self.assertEqual(json.loads(req.recv().decode())["status"]["captures_ok"], 1)
        finally:
            service.request_stop()
            server.join(5)
            req.close(0)
            rep.close(0)
            context.term()


if __name__ == "__main__":
    unittest.main()
