#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_sources.py: the daemon's two read-only sources, and what a resolution of an uncertain
# command is allowed to do.
#
# PART 1, the sources. A fake publisher drives the tap through connected, stalled, failed and disconnected, each with its
# reason code; a sample that stopped arriving never reads as fresh again, in the slot or in the snapshot's link; a preview
# with nothing behind it carries a reason and NOT ONE number; a preview reader that stops reading cannot delay a command.
# PART 2, resolution. The daemon reconciles an uncertain command against real files and real processes, classifies what it
# finds, records the operator's statement beside that evidence, refuses a statement the evidence contradicts, and keeps
# every command blocked while the evidence cannot be read. Nothing in either path starts a process, signals one, builds a
# robot client or sends an action, and a test proves it by making every such call explode first.
#
# No robot, no camera, no network: fake sockets and in-process fakes only. The one child process here is an idle
# interpreter that carries a scan id, started to prove that a live controller REFUSES a resolution.
# Run directly: python3 packages/demo-runtime/tests/test_sources.py
import base64
import json
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import daemon, sources  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.config import PreviewConfig, RuntimeConfig  # noqa: E402
from demo_runtime.journal import EventLog, Journal, make_event, read_json_lines  # noqa: E402
from demo_runtime.preview_client import PREVIEW_MAGIC, PREVIEW_SCHEMA, PreviewTracker  # noqa: E402
from demo_runtime.read_models import TelemetryHub  # noqa: E402
from demo_runtime.rpc import RuntimeClient, RuntimeServer  # noqa: E402
from demo_runtime.tap_client import TAP_VERSION, TapClient  # noqa: E402

import demo_contracts  # noqa: E402

SECOND = 1_000_000_000
LAPTOP_T0 = 4_000 * SECOND  # this laptop's monotonic clock
PI_T0 = 90_000 * SECOND  # another machine, another epoch: never subtracted from the one above
HOST_SESSION = "770011223344556"
CAMERA_SESSION = "cs-20260921T000000Z-0a1b2c3d"
JPEG = b"\xff\xd8" + b"a-picture-for-a-human" + b"\xff\xd9"
WAIT_S = 40.0
# Strings the repository's text gates refuse are BUILT AT RUN TIME, never written out.
TEST_ADDRESS = ".".join(("192", "0", "2", "7"))
TEST_USER = "operator" + "name"
TEST_ROBOT_ID = "unit" + "-0042"
TEST_WRIST_ID = "usb-" + "Vendor_Cam_" + "SN" + "1234567" + "-video-index0"


class Clock:
    """A monotonic clock a test moves by hand: every age in a slot is then an exact number, not a measurement."""

    def __init__(self, start=LAPTOP_T0):
        self.value = start

    def __call__(self):
        return self.value

    def advance_ms(self, milliseconds):
        self.value += int(milliseconds * 1_000_000)


# ---- the fake publishers -----------------------------------------------------------------------------------------------------

def tap_message(seq, *, session=HOST_SESSION, sample_mono_ns=None, drop_count=0, observation=None, lease=None):
    """One message shaped exactly like the host tap's (bin/pi/host_boundary.py publishes this object)."""
    body = {
        "kc.tap": TAP_VERSION, "session": session, "seq": seq, "t_mono_ns": PI_T0 + seq * SECOND // 10 if sample_mono_ns is None else sample_mono_ns,
        "read_ns": 1_200_000, "wall_time_s": 1789000000.5,
        "observation": observation if observation is not None else {
            "arm_shoulder_pan.pos": -15.5, "arm_shoulder_lift.pos": -42.0, "arm_elbow_flex.pos": 61.5, "arm_wrist_flex.pos": 70.0,
            "arm_wrist_roll.pos": 0.0, "arm_gripper.pos": 12.0, "x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0},
        "lease": lease if lease is not None else {"state": "held", "host_session": session, "generation": 3, "owner_label": "console", "request_id": "secret-adoption-token",
                                                  "ttl_s": 2.0, "expires_in_ms": 1800.0, "acquisitions": 1, "releases": 0, "expiries": 0},
        "rejections": {"no_lease": 0, "wrong_generation": 2, "expired": 0},
        "commands": {"since_last_ms": 40.0, "max_gap_ms_in_lease": 120.0},
        "ingestion": {"received": seq, "superseded": 0, "drain_cap_hits": 0, "internal_errors": 0},
        "publisher": {"sent_count": seq, "drop_count": drop_count},
    }
    return json.dumps(body).encode("utf-8")


class FakeTapSocket:
    """A SUB socket that hands out what a test put in it. poll() can be made to fault, which is what a broken forward
    looks like from here."""

    def __init__(self):
        self.queue, self.faults, self.closed = deque(), 0, 0
        self.fault_next = False

    def push(self, *messages):
        self.queue.extend(messages)

    def poll(self, timeout=0):
        if self.fault_next:
            self.fault_next = False
            self.faults += 1
            raise OSError("the forward went away")
        return bool(self.queue)

    def recv_multipart(self):
        return [self.queue.popleft()]

    def close(self, linger=None):
        self.closed += 1


def pack_preview(header, jpeg=JPEG):
    """The preview wire format, as demo_runtime.preview_client documents it and bin/pi/camera_service.py writes it.
    packages/demo-runtime/tests/test_preview_client.py packs with the SERVICE's own function, so the two ends cannot
    drift apart unnoticed; this one keeps the source module out of a test that must run without a camera library."""
    blob = json.dumps(header).encode("utf-8")
    return PREVIEW_MAGIC + struct.pack(">I", len(blob)) + blob + jpeg


def preview_header(preview_seq, *, session=CAMERA_SESSION, role="wrist", **changes):
    header = {"schema": PREVIEW_SCHEMA, "role": role, "session": session, "preview_seq": preview_seq, "source_seq": preview_seq * 6,
              "capture_mono_ns": PI_T0 + preview_seq * SECOND // 5, "dequeue_mono_ns": PI_T0 + preview_seq * SECOND // 5 + 3_000_000,
              "clock_domain": "pi_monotonic", "timestamp_semantics": "driver_buffer_timestamp", "encoding": "jpeg", "width": 320, "height": 180,
              "source_width": 1280, "source_height": 720, "pixel_format": "MJPG", "source_fps_reported": 30.0, "source_fps_measured": 29.9}
    header.update(changes)
    return header


class FakePreviewSubscriber:
    """The surface demo_runtime.sources asks of a PreviewSubscriber, around the REAL PreviewTracker: no socket, no zmq."""

    def __init__(self, clock, role="wrist"):
        self.tracker, self._clock = PreviewTracker(role), clock
        self.queue, self.opens, self.stops, self.polls = deque(), 0, 0, 0
        self.fault_next = False

    def push(self, *messages):
        self.queue.extend(messages)

    def open(self):
        self.opens += 1

    def poll_once(self, timeout_ms=None):
        self.polls += 1
        if self.fault_next:
            self.fault_next = False
            raise OSError("the preview forward went away")
        if not self.queue:
            time.sleep(min(0.01, (timeout_ms or 0) / 1000.0))  # a real socket waits for its timeout; a reader thread must not spin
            return False
        return self.tracker.ingest(self.queue.popleft(), self._clock())

    def latest(self):
        return self.tracker.latest()

    def stats(self):
        return self.tracker.stats(self._clock())

    def stop(self):
        self.stops += 1


def tap_source(clock, socket_object):
    return sources.TapSource(client_factory=lambda: TapClient("inproc://tap-under-test", socket_factory=lambda: socket_object, clock_ns=clock), clock_ns=clock)


def preview_source(clock, subscriber):
    return sources.PreviewSource(subscriber_factory=lambda: subscriber, clock_ns=clock)


# ---- part 1: the tap -----------------------------------------------------------------------------------------------------------

class TapStates(unittest.TestCase):
    def test_a_tap_that_was_never_configured_says_so_and_publishes_no_number(self):
        source = sources.TapSource()
        self.assertFalse(source.configured)
        self.assertFalse(source.start(), "nothing to attach: start does nothing at all")
        slot = source.slot()
        self.assertEqual((slot["available"], slot["state"], slot["reason"]), (False, "disconnected", "tap_not_configured"))
        self.assertEqual([key for key, value in slot.items() if isinstance(value, (int, float)) and not isinstance(value, bool)], [],
                         "not one measured number in a slot with nothing behind it")
        self.assertEqual(source.link(), {"state": "unknown", "source_stamp": None, "receive_age_ms": None},
                         "no tap at all is UNKNOWN, not a robot that is merely disconnected")

    def test_connected_stalled_failed_and_disconnected_each_carry_their_reason(self):
        clock, wire = Clock(), FakeTapSocket()
        source = tap_source(clock, wire)
        source.start()
        self.assertEqual((source.state, source.reason), ("connecting", "no_sample_yet"))
        self.assertFalse(source.slot()["available"], "connecting is not connected: nothing has arrived yet")

        wire.push(tap_message(1), tap_message(2))
        self.assertEqual(source.pump(), 2)
        slot = source.slot()
        self.assertEqual((slot["available"], slot["state"], slot["reason"]), (True, "connected", None))

        clock.advance_ms(1999)
        self.assertTrue(source.slot()["available"], "inside the freshness window it is still connected")
        clock.advance_ms(2)
        stale = source.slot()
        self.assertEqual((stale["available"], stale["state"], stale["reason"]), (False, "stalled", "no_sample_within_window"))
        self.assertEqual(stale["receive_age_ms"], 2001.0, "the age keeps growing and says exactly how old the last sample is")
        self.assertIsNone(stale["joints"], "a value is NEVER reported as current because it arrived a while ago")
        self.assertIsNone(stale["base_velocity"])
        self.assertIsNotNone(stale["source_stamp"], "which sample went stale is still said")

        wire.push(tap_message(3))
        source.pump()
        self.assertEqual(source.slot()["state"], "connected", "a fresh sample brings it back")

        wire.fault_next = True
        source.pump()
        failed = source.slot()
        self.assertEqual((failed["available"], failed["state"], failed["reason"]), (False, "failed", "transport_error"))

        source.stop()
        self.assertEqual((source.state, source.reason), ("disconnected", "stopped"))
        self.assertEqual(wire.closed, 1, "the subscriber socket is closed, and nothing is told anything on the robot")

    def test_a_subscriber_that_cannot_be_opened_is_failed_and_never_repeats_the_endpoint(self):
        def explode():
            raise ValueError("the tap endpoint must be tcp://127.0.0.1:<port>")

        source = sources.TapSource(client_factory=explode)
        self.assertFalse(source.start())
        slot = source.slot()
        self.assertEqual((slot["state"], slot["reason"], source.open_failures), ("failed", "subscriber_failed", 1))
        self.assertNotIn("tcp://", json.dumps(slot), "a refused endpoint never travels in a reason")

    def test_the_slot_passes_the_source_s_own_identity_time_and_counters_through_untouched(self):
        clock, wire = Clock(), FakeTapSocket()
        source = tap_source(clock, wire)
        source.start()
        wire.push(tap_message(11, drop_count=4))
        clock.advance_ms(0)
        source.pump()
        received_at = clock.value
        clock.advance_ms(120)
        slot = source.slot()
        self.assertEqual(slot["source_stamp"], {"session": HOST_SESSION, "seq": 11, "sample_mono_ns": PI_T0 + 11 * SECOND // 10, "clock_domain": "pi_monotonic"})
        self.assertEqual(demo_contracts.validate("common#/$defs/source_stamp", slot["source_stamp"]), [], "the identity is the contract's source_stamp")
        self.assertEqual(slot["clock_domains"], {"sample": "pi_monotonic", "receive": "laptop_monotonic"}, "each time says which clock it was read from")
        self.assertEqual((slot["receive_mono_ns"], slot["receive_age_ms"]), (received_at, 120.0), "the receive time and its age are this laptop's own")
        self.assertEqual(slot["joints"], {"shoulder_pan": -15.5, "shoulder_lift": -42.0, "elbow_flex": 61.5, "wrist_flex": 70.0, "wrist_roll": 0.0, "gripper": 12.0})
        self.assertEqual(slot["base_velocity"], {"x_mps": 0.0, "y_mps": 0.0, "theta_dps": 0.0})
        self.assertEqual(slot["publisher_drop_count"], 4, "the PUBLISHER's own number, not a laptop measurement")
        self.assertEqual(slot["rejections"], {"no_lease": 0.0, "wrong_generation": 2.0, "expired": 0.0}, "what the host refused at the source, for the operator to see")
        self.assertEqual(slot["lease"]["generation"], 3.0)
        self.assertNotIn("request_id", slot["lease"], "the token an acquire is adopted by never leaves this process")
        self.assertNotIn("secret-adoption-token", json.dumps(slot))

    def test_a_gap_this_subscriber_saw_is_reported_apart_from_what_the_publisher_dropped(self):
        clock, wire = Clock(), FakeTapSocket()
        source = tap_source(clock, wire)
        source.start()
        wire.push(tap_message(1, drop_count=0), tap_message(5, drop_count=2))  # four missing, two of them the publisher's own
        source.pump()
        slot = source.slot()
        self.assertEqual((slot["subscriber_gap_count"], slot["publisher_drop_count"]), (3, 2))

    def test_the_measured_rate_is_this_laptop_s_and_falls_away_instead_of_staying_at_its_old_value(self):
        clock, wire = Clock(), FakeTapSocket()
        source = tap_source(clock, wire)
        source.start()
        for seq in range(1, 6):
            wire.push(tap_message(seq))
            source.pump()
            clock.advance_ms(100)
        self.assertAlmostEqual(source.slot()["measured_rate_hz"], 10.0, places=3)
        clock.advance_ms(6000)  # nothing arrives for longer than the rate window
        quiet = source.slot()
        self.assertEqual(quiet["state"], "stalled")
        self.assertIsNone(quiet["measured_rate_hz"], "a feed that stopped has NO rate; it never keeps the one it used to have")


class TheSnapshotLink(unittest.TestCase):
    def test_the_link_follows_the_tap_and_not_the_fact_that_somebody_republished_a_slot(self):
        clock, wire = Clock(), FakeTapSocket()
        source = tap_source(clock, wire)
        hub = TelemetryHub()
        supervisor = sources.SourceSupervisor(hub, tap=source, preview=sources.PreviewSource(), clock_ns=clock)
        source.start()
        wire.push(tap_message(1))
        supervisor.pump_once()
        self.assertEqual(supervisor.link()["state"], "connected")
        for _ in range(4):
            clock.advance_ms(1250)
            supervisor.pump_once()  # the slot is republished by the heartbeat, again and again, with NO new sample
        self.assertGreater(supervisor.tap_publishes, 2, "the console keeps being told, so the age on the screen grows")
        self.assertEqual(supervisor.link(), {"state": "disconnected", "source_stamp": {"session": HOST_SESSION, "seq": 1, "sample_mono_ns": PI_T0 + SECOND // 10,
                                                                                       "clock_domain": "pi_monotonic"}, "receive_age_ms": 5000.0},
                         "a republished slot is not a live robot: the link is decided by the arrival of SAMPLES")
        self.assertEqual(demo_contracts.validate("snapshot#/properties/link", supervisor.link()), [], "and it is the contract's link object")

    def test_a_configured_tap_with_nothing_arriving_is_disconnected_rather_than_unknown(self):
        clock, wire = Clock(), FakeTapSocket()
        supervisor = sources.SourceSupervisor(TelemetryHub(), tap=tap_source(clock, wire), preview=sources.PreviewSource(), clock_ns=clock)
        supervisor.start()
        self.addCleanup(supervisor.stop)
        self.assertEqual(supervisor.link(), {"state": "disconnected", "source_stamp": None, "receive_age_ms": None})
        self.assertEqual(supervisor.tap.reason, "no_sample_yet", "the reason is in the tap slot: the contract's link has no field for one")


# ---- part 1: the preview ---------------------------------------------------------------------------------------------------------

class PreviewStates(unittest.TestCase):
    def test_nothing_publishing_means_a_reason_and_no_invented_number(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        source = preview_source(clock, subscriber)
        source.start()
        source.pump(0)
        slot = source.slot()
        self.assertEqual((slot["available"], slot["state"], slot["reason"], slot["role"]), (False, "connecting", "no_frame_yet", "wrist"))
        for key in ("seq", "source_seq", "capture_clock", "receive_age_ms", "measured_fps", "capture_age_ms", "service_latency_ms", "frame"):
            self.assertIsNone(slot[key], f"{key} was invented while no frame had ever arrived")
        self.assertEqual(set(slot["stream"].values()), {None}, "a format nobody reported is null, not a guess")
        self.assertNotIn("bytes_b64", slot, "no picture is offered when there is none")

    def test_a_frame_carries_its_capture_clock_its_receive_age_and_the_picture_itself(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        source = preview_source(clock, subscriber)
        source.start()
        subscriber.push(pack_preview(preview_header(7)))
        self.assertTrue(source.pump(0))
        clock.advance_ms(250)
        slot = source.slot()
        self.assertEqual((slot["available"], slot["state"], slot["role"], slot["seq"], slot["source_seq"]), (True, "connected", "wrist", 7, 42))
        self.assertEqual(slot["capture_clock"], {"session": CAMERA_SESSION, "seq": 7, "sample_mono_ns": PI_T0 + 7 * SECOND // 5, "clock_domain": "pi_monotonic"})
        self.assertEqual(slot["capture_semantics"], "driver_buffer_timestamp")
        self.assertEqual(slot["receive_age_ms"], 250.0, "how long ago the LAPTOP got it")
        self.assertIsNone(slot["capture_age_ms"], "the age of the picture needs a clock relation this runtime does not have")
        self.assertEqual(slot["capture_age_reason"], "no_clock_relation_between_source_and_laptop")
        self.assertEqual(slot["service_latency_ms"], 3.0, "capture to dequeue is measurable: both times are the SAME clock")
        self.assertEqual(slot["stream"], {"pixel_format": "MJPG", "source_fps_reported": 30.0, "source_fps_measured": 29.9, "source_width": 1280.0, "source_height": 720.0})
        self.assertEqual(slot["frame"], {"width_px": 320, "height_px": 180})
        self.assertEqual(base64.b64decode(slot["bytes_b64"]), JPEG, "the picture the API's preview route serves is IN the slot")

    def test_a_feed_that_stops_goes_stalled_and_stops_offering_a_picture(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        source = preview_source(clock, subscriber)
        source.start()
        subscriber.push(pack_preview(preview_header(1)))
        source.pump(0)
        clock.advance_ms(2500)
        slot = source.slot()
        self.assertEqual((slot["available"], slot["state"], slot["reason"]), (False, "stalled", "no_sample_within_window"))
        self.assertNotIn("bytes_b64", slot, "a stale picture is never shown again: the browser's stream ends instead")
        self.assertEqual((slot["seq"], slot["receive_age_ms"]), (1, 2500.0), "which frame it was, and how old, is still said")
        self.assertIsNone(slot["measured_fps"])

    def test_a_faulted_preview_socket_is_failed_and_the_thread_reopens_it(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        source = preview_source(clock, subscriber)
        source.start()
        subscriber.fault_next = True
        self.assertFalse(source.pump(0))
        self.assertEqual((source.state, source.reason), ("failed", "transport_error"))
        self.assertEqual(subscriber.stops, 1, "the broken socket is dropped")
        self.assertTrue(source.start(), "and a new one is opened in its place")
        self.assertEqual((source.state, source.reason), ("connecting", "no_frame_yet"))

    def test_a_record_without_a_picture_is_only_published_while_the_source_is_not_connected(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        hub = TelemetryHub()
        supervisor = sources.SourceSupervisor(hub, tap=sources.TapSource(), preview=preview_source(clock, subscriber), clock_ns=clock)
        supervisor.start()
        self.addCleanup(supervisor.stop)
        published = []
        hub_publish = hub.publish
        hub.publish = lambda stream, record: (published.append((stream, record)), hub_publish(stream, record))[1]
        subscriber.push(pack_preview(preview_header(1)))
        supervisor.pump_once()
        clock.advance_ms(1500)
        supervisor.pump_once()  # a heartbeat would fall due here, and must NOT be sent while frames are healthy
        topic = sources.preview_topic(sources.DEFAULT_ROLE)
        previews = [record for stream, record in published if stream == topic]
        self.assertEqual(len(previews), 1, "one publish, for the one frame: a heartbeat in between would cut the browser's stream")
        clock.advance_ms(1000)
        supervisor.pump_once()  # now it is stalled: the state changed, so the console is told
        previews = [record for stream, record in published if stream == topic]
        self.assertEqual([record["state"] for record in previews], ["connected", "stalled"])
        self.assertNotIn("bytes_b64", previews[-1])


class TwoCamerasAreIndependent(unittest.TestCase):
    """One camera must never speak for the other, nor take the other down with it.

    Both roles used to share ONE source, ONE hub topic and ONE publish counter, so there was no shape in which two
    cameras could disagree: a stalled feed and a healthy one were the same slot. Each role now has its own source, its
    own thread, its own topic and its own state, and these hold that apart.
    """

    @staticmethod
    def roled(clock, subscriber, role):
        """A preview source that KNOWS its role, which is what every record it publishes is labelled with."""
        return sources.PreviewSource(None, role, subscriber_factory=lambda: subscriber, clock_ns=clock)

    def supervisor(self, clock, wrist, front):
        return sources.SourceSupervisor(
            hub=TelemetryHub(), tap=sources.TapSource(),
            previews={"wrist": self.roled(clock, wrist, "wrist"), "front": self.roled(clock, front, "front")}, clock_ns=clock)

    def test_each_role_publishes_on_its_own_topic_and_never_the_other_s(self):
        clock = Clock()
        wrist, front = FakePreviewSubscriber(clock), FakePreviewSubscriber(clock, role="front")
        supervisor = self.supervisor(clock, wrist, front)
        hub = supervisor.hub
        supervisor.start()
        self.addCleanup(supervisor.stop)
        wrist.push(pack_preview(preview_header(1, role="wrist")))
        front.push(pack_preview(preview_header(1, role="front")))
        supervisor.pump_once()
        for role in ("wrist", "front"):
            record = hub.latest(sources.preview_topic(role))[0]
            self.assertEqual(record["role"], role, "a camera's topic carries that camera's record")
            self.assertTrue(record["available"])
        self.assertNotEqual(hub.latest(sources.preview_topic("wrist"))[0], hub.latest(sources.preview_topic("front"))[0])

    def test_one_camera_failing_neither_clears_nor_freezes_the_other(self):
        clock = Clock()
        wrist, front = FakePreviewSubscriber(clock), FakePreviewSubscriber(clock, role="front")
        supervisor = self.supervisor(clock, wrist, front)
        hub = supervisor.hub
        supervisor.start()
        self.addCleanup(supervisor.stop)
        wrist.push(pack_preview(preview_header(1, role="wrist")))
        front.push(pack_preview(preview_header(1, role="front")))
        supervisor.pump_once()
        # The front camera's transport faults. The wrist camera goes on delivering frames.
        front.fault_next = True
        supervisor.pump_once()
        wrist.push(pack_preview(preview_header(2, role="wrist")))
        supervisor.pump_once()
        self.assertEqual(supervisor.previews["front"].state, sources.FAILED)
        self.assertEqual(supervisor.previews["wrist"].state, sources.CONNECTED, "a dead camera must not take the live one with it")
        healthy = hub.latest(sources.preview_topic("wrist"))[0]
        self.assertTrue(healthy["available"], "the working camera keeps its picture while the other has none")
        broken = hub.latest(sources.preview_topic("front"))[0]
        self.assertFalse(broken["available"])
        self.assertEqual(broken["role"], "front", "the failure is reported under the camera it belongs to")
        self.assertIsNone(broken["frame"], "a failed camera shows no picture rather than a stale one")

    def test_the_report_names_every_camera_separately(self):
        clock = Clock()
        supervisor = self.supervisor(clock, FakePreviewSubscriber(clock), FakePreviewSubscriber(clock, role="front"))
        supervisor.start()
        self.addCleanup(supervisor.stop)
        report = supervisor.report()
        self.assertEqual(set(report["previews"]), {"wrist", "front"})
        self.assertEqual(report["previews"]["front"]["role"], "front")

    def test_a_role_that_is_not_configured_answers_honestly_under_its_own_name(self):
        clock = Clock()
        supervisor = sources.SourceSupervisor(hub=TelemetryHub(), tap=sources.TapSource(),
                                              previews={"wrist": self.roled(clock, FakePreviewSubscriber(clock), "wrist")}, clock_ns=clock)
        record = supervisor.latest_preview(role="front")
        self.assertFalse(record["available"])
        self.assertEqual(record["role"], "front", "an absent camera is named, never answered with another camera's slot")
        self.assertEqual(record["reason"], "preview_role_not_configured")
        self.assertIsNone(record["frame"])


class WhereTheEndpointsComeFrom(unittest.TestCase):
    """The tap is configured; the preview endpoint is DERIVED, because the runtime config has no field for it and refuses
    unknown keys. What is assumed is pinned here so a change to the camera service is caught by a test, not by an operator."""

    class _Tap:
        def __init__(self, enabled, port):
            self.enabled, self.port = enabled, port

    class _Config:
        def __init__(self, tap=None, preview=None, pi_ssh_target=""):
            self.tap, self.pi_ssh_target = tap, pi_ssh_target
            if preview is not None:
                self.preview = preview

    def test_each_role_s_preview_port_is_the_camera_service_s_own(self):
        text = (REPO_ROOT / "bin" / "pi" / "camera_service.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_MISSION_PORT = 5570", text)
        declared = re.search(r"DEFAULT_PREVIEW_PORTS = (\{[^}]*\})", text).group(1)
        self.assertEqual(json.loads(declared.replace("'", '"')), sources.CAMERA_SERVICE_PREVIEW_PORTS)
        self.assertNotIn("PREVIEW_PORT_OFFSET", text, "a per-role offset is what let one port serve both cameras")

    def test_the_tap_is_its_own_switch_and_never_implies_a_preview(self):
        """A preview is a camera picture and the tap is the host's observation stream. Gating one on the other made two
        real cameras unreachable without also enabling the boundary, and the baseline NORMAL host wants preview alone."""
        config = self._Config(tap=self._Tap(True, 5558))
        self.assertEqual(sources.tap_endpoint(config), "tcp://127.0.0.1:5558")
        self.assertIsNone(sources.preview_endpoint(config, "wrist"), "an enabled tap says nothing about a camera")
        self.assertIsNone(sources.preview_endpoint(config, "front"))
        self.assertEqual(sources.preview_roles(config), ())

    def test_no_tap_means_no_preview_either_and_a_daemon_flag_overrides_both_assumptions(self):
        self.assertIsNone(sources.tap_endpoint(self._Config(tap=self._Tap(False, 5558))))
        self.assertIsNone(sources.preview_endpoint(self._Config(tap=self._Tap(False, 5558))), "a tap that is off configures nothing")
        self.assertIsNone(sources.preview_endpoint(self._Config()), "a config with no preview section at all is read defensively, not by attribute error")
        self.assertEqual(sources.preview_endpoint(self._Config(), port=5999), "tcp://127.0.0.1:5999", "--preview-port needs no config field")

    def test_the_forwards_land_exactly_where_these_two_subscribers_connect(self):
        """The contract between the two modules: demo_runtime.forwards decides WHERE the tunnels are opened, and it has
        to be the ports this module will connect to. A change to either endpoint rule fails here, not on a robot."""
        from demo_runtime import forwards

        target = TEST_USER + "@" + TEST_ADDRESS
        preview = PreviewConfig(enabled=True, wrist_port=5601, front_port=5602)
        config = self._Config(tap=self._Tap(True, 5558), preview=preview, pi_ssh_target=target)
        built = forwards.Forwards.from_config(config)
        self.addCleanup(built.stop)
        self.assertTrue(built.configured)
        self.assertEqual(forwards.endpoint_port(sources.tap_endpoint(config)), built.get(forwards.TAP)._wanted_local_port)
        for role in ("wrist", "front"):
            self.assertEqual(forwards.endpoint_port(sources.preview_endpoint(config, role)),
                             built.get(forwards.preview_purpose(role))._wanted_local_port,
                             f"the {role} forward lands exactly where its subscriber connects")
        self.assertEqual(built.get(forwards.CONTROL)._wanted_local_port, None, "the control socket has no configured port: one is chosen")
        self.assertFalse(forwards.Forwards.from_config(self._Config(tap=self._Tap(False, 5558), pi_ssh_target=target)).configured,
                         "with neither a tap nor a preview nothing is forwarded, so an existing deployment is untouched")
        self.assertTrue(forwards.Forwards.from_config(self._Config(preview=preview, pi_ssh_target=target)).configured,
                        "a preview with no tap forwards on its own: the baseline NORMAL host wants exactly that")

    def test_each_role_carries_its_own_port_and_never_another_role_s(self):
        """THE DEFECT: an explicitly given port used to be returned verbatim for every role, so both cameras resolved to
        one socket and the front preview would have carried the wrist picture."""
        section = PreviewConfig(enabled=True, wrist_port=5601, front_port=5602)
        config = self._Config(preview=section)
        self.assertEqual(sources.preview_endpoint(config, "wrist"), "tcp://127.0.0.1:5601")
        self.assertEqual(sources.preview_endpoint(config, "front"), "tcp://127.0.0.1:5602")
        self.assertNotEqual(sources.preview_endpoint(config, "wrist"), sources.preview_endpoint(config, "front"))
        self.assertEqual(sources.preview_roles(config), ("wrist", "front"))

    def test_a_preview_works_with_no_tap_at_all(self):
        config = self._Config(preview=PreviewConfig(enabled=True, wrist_port=5601))
        self.assertIsNone(sources.tap_endpoint(config))
        self.assertEqual(sources.preview_endpoint(config, "wrist"), "tcp://127.0.0.1:5601")
        self.assertEqual(sources.preview_roles(config), ("wrist",), "a role with no port has no preview, and says so")
        self.assertIsNone(sources.preview_endpoint(config, "front"))

    def test_a_disabled_preview_section_resolves_to_nothing(self):
        config = self._Config(preview=PreviewConfig(enabled=False, wrist_port=5601, front_port=5602))
        self.assertIsNone(sources.preview_endpoint(config, "wrist"))
        self.assertIsNone(sources.preview_endpoint(config, "front"))
        self.assertEqual(sources.preview_roles(config), ())

    def test_a_real_runtime_config_without_a_tap_attaches_nothing_and_says_so_on_both_slots(self):
        """The whole from_config path, on a configuration that names no tap: not one socket is opened, and the console
        is still told what each source is and why it has nothing."""
        tmp = Path(tempfile.mkdtemp(prefix="kc-live-config-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        config = RuntimeConfig.from_mapping({
            "data_root": str(tmp / "demo-data"), "saved_pose_file": "calibration/scan-home-current.json", "pad_template_dir": "calibration/pad-template",
            "observation_root": "observations", "host": {"address": TEST_ADDRESS, "robot_id": TEST_ROBOT_ID}, "pi_ssh_target": f"{TEST_USER}@{TEST_ADDRESS}",
            "cameras": {"wrist_id": TEST_WRIST_ID, "front_id": None}, "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None}})
        hub = TelemetryHub()
        supervisor = sources.SourceSupervisor.from_config(hub, config)
        supervisor.start()
        self.addCleanup(supervisor.stop)
        self.assertEqual((supervisor.tap.configured, supervisor.preview.configured), (False, False))
        self.assertEqual(supervisor.report()["tap"], {"configured": False, "state": "disconnected", "reason": "tap_not_configured", "open_failures": 0,
                                                      "transport_faults": 0, "publishes": 1})
        self.assertEqual(supervisor.report()["preview"]["reason"], "preview_not_configured")
        tap_slot = hub.latest("tap")[0]
        preview_slot = hub.latest(sources.preview_topic(sources.DEFAULT_ROLE))[0]
        self.assertEqual((tap_slot["available"], tap_slot["reason"]), (False, "tap_not_configured"), "the console is told once at start, not left with an empty panel")
        self.assertEqual((preview_slot["available"], preview_slot["reason"]), (False, "preview_not_configured"))
        self.assertEqual(supervisor.link()["state"], "unknown")
        text = json.dumps([tap_slot, preview_slot, supervisor.report()])
        for private in (TEST_ADDRESS, TEST_ROBOT_ID, TEST_WRIST_ID, TEST_USER):
            self.assertNotIn(private, text, "a slot names no address, no robot and no device")


# ---- part 1: nothing about a preview may delay a command -------------------------------------------------------------------------

class SlowReaders(unittest.TestCase):
    def test_a_preview_reader_that_stops_reading_cannot_delay_a_command(self):
        """A browser tab that stopped reading is a socket whose buffer is full. The source thread goes on publishing
        frames of a size that cannot fit in one, and the command path must not feel any of it."""
        subscriber = FakePreviewSubscriber(time.monotonic_ns)
        tmp = Path(tempfile.mkdtemp(prefix="kc-sources-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        big = b"\xff\xd8" + os.urandom(180_000) + b"\xff\xd9"  # one frame larger than a socket buffer
        core = daemon.RuntimeCore(data_dir=tmp / "data", mode=daemon.SYNTHETIC, source_root=REPO_ROOT, extra_env={"KC_FAKE_STEP_S": "0.01"},
                                  cake_probe=lambda: {"artifacts": False, "resident": False, "admin_socket": False},
                                  source_supervisor=lambda hub: sources.SourceSupervisor(hub, tap=sources.TapSource(), preview=preview_source(time.monotonic_ns, subscriber)))
        self.addCleanup(core.close)
        core.start()
        server = RuntimeServer(core.data_dir / daemon.SOCKET_NAME, core.handle, core.stream)
        server.start()
        self.addCleanup(server.close)
        client = RuntimeClient(core.data_dir / daemon.SOCKET_NAME, timeout_s=20.0)
        feeding = threading.Event()

        def feed():
            seq = 0
            while not feeding.is_set():  # the daemon's OWN source thread reads these and publishes them
                seq += 1
                subscriber.push(pack_preview(preview_header(seq), big))
                time.sleep(0.004)

        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(20)
        raw.connect(str(core.data_dir / daemon.SOCKET_NAME))
        raw.sendall(b'{"op":"telemetry"}\n')  # and then NEVER a single read: the kernel buffer fills and stays full
        self.addCleanup(raw.close)
        pump = threading.Thread(target=feed, name="preview-feeder", daemon=True)
        pump.start()
        self.addCleanup(lambda: (feeding.set(), pump.join(10)))

        deadline = time.monotonic() + WAIT_S
        while core.sources.preview_publishes < 40 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(core.sources.preview_publishes, 40, "the source kept publishing while the reader read nothing")
        self.assertGreaterEqual(core.hub.readers, 1, "the stuck reader is still attached: this is measured WHILE it holds its connection")

        started = time.monotonic()
        result = client.submit(client.new_command("start_cycle", {"plan_only": False}, command_id="slow-reader-0001"))
        elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "running")
        self.assertLess(elapsed, 10.0, f"the command path waited {elapsed:.2f}s on a stuck preview reader")
        deadline = time.monotonic() + WAIT_S
        while client.result("slow-reader-0001")["status"] != "finished" and time.monotonic() < deadline:
            time.sleep(0.03)
        self.assertEqual(client.result("slow-reader-0001")["status"], "finished", "and the run it started went through to the end")
        self.assertEqual(client.status()["sources"]["preview"]["state"], "connected")
        feeding.set()
        pump.join(10)


# ---- part 2: reconciling an uncertain command ---------------------------------------------------------------------------------

def command_record(command_id, run_id, status="accepted", **fields):
    return {"rec": "command", "command_id": command_id, "status": status, "run_id": run_id, "scan_id": run_id, "command_type": "start_cycle",
            "reason_code": None, "t_wall_utc": "2026-09-21T00:00:00.000Z", **fields}


def controller_line(run_id, seq, kind="state.transition", payload=None):
    event = make_event(kind, payload if payload is not None else {"from_state": "IDLE", "to_state": "SCAN", "trigger": "command"}, source_session="ctl-test", run_id=run_id)
    event["seq"] = seq
    return json.dumps(event) + "\n"


def finished_line(run_id, seq, outcome="placed_new"):
    payload = {"outcome": outcome, "exit_status": 0, "predicates": {"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True},
               "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": True, "evidence_gaps": 0}
    return controller_line(run_id, seq, "run.finished", payload)


class Harness:
    """One data directory and a supervisor on it, with a second one for 'after a restart'."""

    def __init__(self, test, *, proc_root="/proc", config=None):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-resolve-"))
        test.addCleanup(self._remove)
        self.data = self.tmp / "data"
        self.data.mkdir(mode=0o700)
        self.proc_root, self.config = proc_root, config
        self.supervisors = []

    def journal_records(self, *records):
        journal = Journal(self.data / "journal.jsonl")
        for record in records:
            journal.append(record)
        journal.close()

    def new_supervisor(self, **overrides):
        options = {"mode": sv.SYNTHETIC, "proc_root": self.proc_root, "config": self.config, **overrides}
        supervisor = sv.Supervisor(data_dir=self.data, journal=Journal(self.data / "journal.jsonl"), event_log=EventLog(self.data / "events.jsonl"),
                                   runtime_session=f"rt-test-{len(self.supervisors)}", source_root=REPO_ROOT, controller_entry="packages/demo-runtime/tests/fake_controller.py",
                                   **options)
        self.supervisors.append(supervisor)
        return supervisor

    def run_dir(self, run_id):
        path = self.data / "history" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _remove(self):
        for supervisor in self.supervisors:
            supervisor.detach()
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)


class FourClassifications(unittest.TestCase):
    def test_never_started_is_concluded_by_the_machine_and_clears_the_block(self):
        harness = Harness(self)
        harness.journal_records(command_record("never-0001", "console-20260921T000000Z-never-00"))
        supervisor = harness.new_supervisor()
        self.assertEqual(supervisor.reconcile(), [("never-0001", "uncertain")])
        self.assertEqual(supervisor.block(), ("owner_unresolved", None))
        evidence = supervisor.reconcile_evidence("never-0001")
        self.assertEqual(evidence["classification"], "never_started")
        self.assertEqual((evidence["process"]["alive"], evidence["process"]["readable"]), (False, True))
        self.assertEqual((evidence["run_files"]["events_present"], evidence["run_files"]["output_present"]), (False, False))
        self.assertEqual(evidence["unreadable"], [])
        answer = supervisor.resolve("never-0001", "no_controller_running")
        self.assertEqual((answer["ok"], answer["cleared"], answer["cleared_by"], answer["classification"]), (True, True, "machine_evidence", "never_started"))
        self.assertIsNone(supervisor.block(), "every command is accepted again")
        self.assertEqual(supervisor.result("never-0001")["status"], "uncertain", "it stays uncertain: what happened was reconciled, not learned")

    def test_a_terminal_record_that_appears_later_is_read_and_names_the_outcome(self):
        harness = Harness(self)
        run_id = "console-20260921T000000Z-late-000"
        harness.journal_records(command_record("late-0001", run_id))
        supervisor = harness.new_supervisor()
        self.assertEqual(supervisor.reconcile(), [("late-0001", "uncertain")])
        # The controller was not found at the restart; it finished afterwards and wrote its own terminal record.
        (harness.run_dir(run_id) / sv.CONTROLLER_EVENTS_FILE).write_text(controller_line(run_id, 1) + finished_line(run_id, 2, "placed_new"))
        answer = supervisor.resolve("late-0001", "controller_finished")
        self.assertEqual((answer["cleared"], answer["cleared_by"], answer["classification"]), (True, "machine_evidence", "ran_and_finished"))
        self.assertEqual(answer["evidence"]["run_files"]["outcome"], "placed_new", "the outcome comes from the controller's own record, not from the operator")
        self.assertIsNone(supervisor.block())

    def test_a_controller_that_ran_and_left_no_terminal_record_needs_the_operator_s_statement(self):
        harness = Harness(self)
        run_id = "console-20260921T000000Z-inter-00"
        dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dead.wait(30)  # a REAL pid whose process is gone, with the start time the journal recorded for it
        harness.journal_records(command_record("inter-0001", run_id, status="running", child={"pid": dead.pid, "start_ticks": 7, "scan_id": run_id}))
        (harness.run_dir(run_id) / sv.CONTROLLER_EVENTS_FILE).write_text(controller_line(run_id, 1) + controller_line(run_id, 2))
        (harness.run_dir(run_id) / sv.STDOUT_FILE).write_text("the controller said something and then died\n")
        supervisor = harness.new_supervisor()
        self.assertEqual(supervisor.reconcile(), [("inter-0001", "uncertain")])
        evidence = supervisor.reconcile_evidence("inter-0001")
        self.assertEqual(evidence["classification"], "ran_and_was_interrupted")
        self.assertEqual((evidence["run_files"]["terminal_record"], evidence["run_files"]["output_present"], evidence["journal"]["child_identity"]), (False, True, True))
        answer = supervisor.resolve("inter-0001", "controller_stopped_by_operator", "I found the arm parked and the gripper closed")
        self.assertEqual((answer["cleared"], answer["cleared_by"], answer["classification"]), (True, "operator_attestation", "ran_and_was_interrupted"))
        self.assertEqual(answer["attestation"]["note"], "I found the arm parked and the gripper closed")
        self.assertIsNone(supervisor.block())

    def test_captures_written_after_the_command_was_accepted_prove_that_it_ran(self):
        tmp = Path(tempfile.mkdtemp(prefix="kc-observations-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        config = RuntimeConfig.from_mapping({
            "data_root": str(tmp / "demo-data"), "saved_pose_file": "calibration/scan-home-current.json", "pad_template_dir": "calibration/pad-template",
            "observation_root": "observations", "host": {"address": TEST_ADDRESS, "robot_id": TEST_ROBOT_ID}, "pi_ssh_target": f"{TEST_USER}@{TEST_ADDRESS}",
            "cameras": {"wrist_id": TEST_WRIST_ID, "front_id": None}, "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None}})
        harness = Harness(self, config=config)
        run_id = "console-20260921T000000Z-store-00"
        harness.journal_records(command_record("store-0001", run_id, t_wall_utc=sv.utc_now_text()))
        captures = Path(config.observation_root) / run_id / "frames"
        captures.mkdir(parents=True)
        (captures / "obs-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        supervisor = harness.new_supervisor()
        self.assertEqual(supervisor.reconcile(), [("store-0001", "uncertain")])
        evidence = supervisor.reconcile_evidence("store-0001")
        self.assertEqual((evidence["observations"]["checked"], evidence["observations"]["session_present"], evidence["observations"]["files_after_accept"]), (True, True, 1))
        self.assertEqual(evidence["classification"], "ran_and_was_interrupted", "the store gained a capture after the accept: something ran")

    def test_a_process_table_that_cannot_be_read_stays_unknown_and_keeps_every_command_blocked(self):
        harness = Harness(self, proc_root=str(Path(tempfile.gettempdir()) / f"kc-absent-proc-{os.getpid()}"))
        harness.journal_records(command_record("dark-0001", "console-20260921T000000Z-dark-000"))
        supervisor = harness.new_supervisor()
        supervisor.reconcile()
        evidence = supervisor.reconcile_evidence("dark-0001")
        self.assertEqual((evidence["classification"], evidence["unreadable"]), ("still_unknown", ["process_table"]))
        answer = supervisor.resolve("dark-0001", "no_controller_running", "I looked and saw nothing")
        self.assertEqual((answer["ok"], answer["cleared"], answer["cleared_by"], answer["reason_code"]), (True, False, None, "evidence_unreadable"))
        self.assertEqual(supervisor.block(), ("owner_unresolved", None), "still_unknown does NOT unblock: a live controller could not be ruled out")
        self.assertEqual(supervisor.submit({"contract_version": "1", "command_id": "dark-0002", "type": "start_cycle", "args": {"plan_only": False},
                                            "client": {"session_id": "test-client", "contract_version": "1"}})["reason_code"], "owner_unresolved")
        records = [record for record in read_json_lines(str(harness.data / "journal.jsonl"))[0] if record.get("rec") in ("attestation", "resolution")]
        self.assertEqual([record["rec"] for record in records], ["attestation"], "the statement is durable, and it resolved nothing")
        after_restart = harness.new_supervisor()
        self.assertEqual(after_restart.unresolved(), ["dark-0001"], "and the block survives a restart")
        self.assertEqual(after_restart.unresolved_detail()["attestations"][0]["finding"], "no_controller_running")


class ContradictedStatements(unittest.TestCase):
    def test_declaring_that_nothing_runs_while_a_live_child_carries_the_scan_id_is_refused(self):
        harness = Harness(self)
        run_id = "console-20260921T000000Z-alive-00"
        events_path = harness.run_dir(run_id) / sv.CONTROLLER_EVENTS_FILE
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "--scan-id", run_id],
                                   env={**os.environ, "KC_EVENTS_PATH": str(events_path)}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(process.wait, 10)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 10
        while run_id not in " ".join(sv.proc_cmdline(process.pid)) and time.monotonic() < deadline:
            time.sleep(0.01)
        harness.journal_records(command_record("alive-0001", run_id, status="uncertain", reason_code="child_gone_without_terminal_record"))
        supervisor = harness.new_supervisor()
        answer = supervisor.resolve("alive-0001", "no_controller_running", "I am sure nothing is running")
        self.assertEqual((answer["ok"], answer["reason_code"], answer["cleared"]), (False, "controller_alive", False))
        self.assertEqual(answer["contradiction"]["declared"], "no_controller_running")
        self.assertEqual(answer["contradiction"]["found"], "a process with this command's identity is alive now")
        self.assertEqual(answer["evidence"]["process"]["found_by"], "scan_id")
        self.assertEqual(supervisor.block(), ("owner_unresolved", None), "a refused statement leaves the block exactly where it was")
        self.assertTrue(sv.is_alive(process.pid), "and the live process was never signalled by the resolution")
        attestations = [record for record in read_json_lines(str(harness.data / "journal.jsonl"))[0] if record.get("rec") == "attestation"]
        self.assertEqual((len(attestations), attestations[0]["refused"]), (1, "controller_alive"), "what the operator declared is recorded even when it is refused")

    def test_a_finding_outside_the_list_and_a_note_that_is_not_plain_text_are_refused(self):
        harness = Harness(self)
        harness.journal_records(command_record("note-0001", "console-20260921T000000Z-note-000", status="uncertain", reason_code="no_child_evidence"))
        supervisor = harness.new_supervisor()
        self.assertEqual(supervisor.resolve("note-0001", "it is probably fine")["reason_code"], "finding_not_allowed")
        self.assertEqual(supervisor.resolve("note-0001", "no_controller_running", "line one\nline two")["reason_code"], "note_not_allowed")
        self.assertEqual(supervisor.resolve("note-0001", "no_controller_running", "x" * 201)["reason_code"], "note_not_allowed")
        self.assertEqual(supervisor.block(), ("owner_unresolved", None))
        self.assertTrue(supervisor.resolve("note-0001", "no_controller_running", "  ")["cleared"], "an empty note is simply no note")


class NothingIsEverCommanded(unittest.TestCase):
    """The rule a crashed controller must not be able to break: the daemon has no motor path. It never opens a gripper,
    never releases torque and never sends an action, whatever an operator resolves or a reconciliation finds."""

    def test_no_reconcile_or_resolve_path_builds_a_robot_client_signals_or_spawns_anything(self):
        from demo_runtime import fresh_client, lease_client

        harness = Harness(self)
        run_id = "console-20260921T000000Z-quiet-00"
        harness.journal_records(command_record("quiet-0001", run_id, status="running", child={"pid": 999_999_000, "start_ticks": 3, "scan_id": run_id}))
        (harness.run_dir(run_id) / sv.CONTROLLER_EVENTS_FILE).write_text(controller_line(run_id, 1))

        def explode(*_args, **_kwargs):
            raise AssertionError("the daemon tried to reach the robot while reconciling a command")

        supervisor = harness.new_supervisor()
        with mock.patch.object(fresh_client, "fresh_client", explode), mock.patch.object(lease_client, "LeaseClient", explode), \
             mock.patch.object(sv.subprocess, "Popen", explode), mock.patch.object(sv.os, "kill", explode), \
             mock.patch.object(sv, "pidfd_sigint", explode), mock.patch.object(sv, "pidfd_open", explode):
            self.assertEqual(supervisor.reconcile(), [("quiet-0001", "uncertain")])
            evidence = supervisor.reconcile_evidence("quiet-0001")
            self.assertEqual(evidence["classification"], "ran_and_was_interrupted")
            detail = supervisor.unresolved_detail()
            self.assertEqual(detail["command_id"], "quiet-0001")
            self.assertTrue(supervisor.resolve("quiet-0001", "controller_stopped_by_operator")["cleared"])
        self.assertEqual(supervisor.launches, 0, "NEVER a respawn of an ambiguous command, and nothing else was started either")

    def test_the_daemon_side_modules_hold_no_motor_vocabulary_at_all(self):
        forbidden = ("send_action", "set_torque", "disable_torque", "enable_torque", "open_gripper", "close_gripper", "goal_pos", "torque_enable")
        for name in ("daemon.py", "supervisor.py", "sources.py", "rpc.py", "read_models.py", "journal.py", "cli.py", "forwards.py"):
            text = (PACKAGE_DIR / "demo_runtime" / name).read_text(encoding="utf-8")
            for word in forbidden:
                self.assertNotIn(word, text, f"{name} names a motor operation; the daemon must have no motor path at all")

    def test_reading_a_configuration_for_forwards_starts_no_process_and_builds_no_client(self):
        """from_config READS. It opens no ssh, builds no lease client and constructs no robot client: the daemon calls it
        while it is starting, and a start must never be the thing that reaches the robot."""
        from demo_runtime import forwards, fresh_client, lease_client

        def explode(*_args, **_kwargs):
            raise AssertionError("building the forwards reached the robot")

        config = WhereTheEndpointsComeFrom._Config(tap=WhereTheEndpointsComeFrom._Tap(True, 5558), pi_ssh_target=TEST_USER + "@" + TEST_ADDRESS)
        with mock.patch.object(fresh_client, "fresh_client", explode), mock.patch.object(lease_client, "LeaseClient", explode), \
             mock.patch.object(forwards.subprocess, "Popen", explode):
            built = forwards.Forwards.from_config(config)
            self.addCleanup(built.stop)
            self.assertTrue(built.configured)
            self.assertEqual(built.report()["tap"]["starts"], 0)
            self.assertEqual(built.dead(), [], "nothing was started, so nothing can be dead")
            self.assertIsNone(built.endpoint(forwards.CONTROL))

    def test_a_resolution_never_runs_the_ambiguous_command_again(self):
        harness = Harness(self)
        harness.journal_records(command_record("once-0001", "console-20260921T000000Z-once-000"))
        supervisor = harness.new_supervisor()
        supervisor.reconcile()
        self.assertTrue(supervisor.resolve("once-0001", "no_controller_running")["cleared"])
        resubmitted = supervisor.submit({"contract_version": "1", "command_id": "once-0001", "type": "start_cycle", "args": {"plan_only": False},
                                         "client": {"session_id": "test-client", "contract_version": "1"}})
        self.assertEqual(resubmitted["status"], "uncertain", "the same command id returns its recorded state; it is never run")
        self.assertEqual(supervisor.launches, 0)


class WhatTheOperatorIsAsked(unittest.TestCase):
    def test_the_unresolved_detail_carries_the_evidence_the_question_and_the_allowed_findings(self):
        harness = Harness(self)
        run_id = "console-20260921T000000Z-ask-0000"
        harness.journal_records(command_record("ask-0001", run_id))
        supervisor = harness.new_supervisor()
        supervisor.reconcile()
        detail = supervisor.unresolved_detail()
        self.assertEqual((detail["command_id"], detail["run_id"], detail["block_reason_code"]), ("ask-0001", run_id, "owner_unresolved"))
        self.assertEqual(detail["classification"], "never_started")
        self.assertEqual(detail["findings"], list(sv.FINDINGS))
        self.assertIn("confirm", detail["question"])
        self.assertEqual(detail["blocked_commands"], list(sv.COMMANDS_BLOCKED_WHILE_UNRESOLVED))
        text = json.dumps(detail)
        self.assertNotIn(str(harness.data), text, "the evidence names no path")
        self.assertNotIn(str(Path.home()), text)
        supervisor.resolve("ask-0001", "no_controller_running")
        self.assertIsNone(supervisor.unresolved_detail(), "once nothing is unresolved there is nothing to ask about")


if __name__ == "__main__":
    unittest.main()


class PreviewSlotIsContractShaped(unittest.TestCase):
    """The counters the schema declares as integers, VALIDATED as values and not just as key names.

    `packages/demo-contracts/tests/test_runtime_surfaces.py` compares this producer with the schema by parsing the
    source, so it checks that the right KEYS are published and cannot see what type they carry. `gaps` and `malformed`
    were published as the preview tracker's own objects, `{"events": n, "missing_previews": m}` and a count per error
    code, against a schema that declares both `integer|null, minimum 0`. Every reader that typed them as numbers threw
    them away, so the one frame-progression fault signal this runtime computes reached nothing. This is the test that
    would have caught it: it validates a real produced slot against the real schema.
    """

    def schema_errors(self, slot: dict) -> list:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "demo-contracts" / "python"))
        try:
            import demo_contracts
        except ImportError:  # pragma: no cover - a checkout without the contracts package
            raise unittest.SkipTest("demo_contracts is not in this checkout")
        return demo_contracts.validate("source#/$defs/preview_slot", slot)

    def test_a_connected_slot_validates_against_the_schema(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        source = preview_source(clock, subscriber)
        source.start()
        subscriber.push(pack_preview(preview_header(7)))
        self.assertTrue(source.pump(0))
        slot = {key: value for key, value in source.slot().items() if key != "bytes_b64"}
        self.assertEqual(self.schema_errors(slot), [], "the slot this runtime publishes does not satisfy its own schema")

    def test_the_counters_are_integers_or_null_and_never_the_tracker_s_objects(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        source = preview_source(clock, subscriber)
        source.start()
        subscriber.push(pack_preview(preview_header(7)))
        source.pump(0)
        slot = source.slot()
        for key in ("gaps", "session_changes", "capture_time_stalls", "malformed"):
            value = slot[key]
            self.assertTrue(value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0),
                            f"{key} is {value!r}, which the schema cannot carry")

    def test_a_gap_the_subscriber_saw_reaches_the_slot_as_a_number(self):
        clock = Clock()
        subscriber = FakePreviewSubscriber(clock)
        source = preview_source(clock, subscriber)
        source.start()
        subscriber.push(pack_preview(preview_header(1)))
        source.pump(0)
        subscriber.push(pack_preview(preview_header(5)))  # 2, 3 and 4 never arrived: one gap event
        source.pump(0)
        self.assertEqual(source.slot()["gaps"], 1, "the gap event the tracker counted did not reach the slot")
        self.assertEqual(self.schema_errors({k: v for k, v in source.slot().items() if k != "bytes_b64"}), [])

    def test_an_unavailable_slot_says_not_known_rather_than_zero(self):
        slot = sources.unavailable_preview()
        for key in ("gaps", "session_changes", "capture_time_stalls", "malformed"):
            self.assertIsNone(slot[key], f"{key} reads as a counted zero while nothing has ever been counted")
        self.assertEqual(self.schema_errors(slot), [])


class ContractCounterConversion(unittest.TestCase):
    """_count, on its own. Not known must stay not known."""

    def test_a_counter_nobody_kept_is_null_and_never_zero(self):
        self.assertIsNone(sources._count(None))
        self.assertIsNone(sources._count({}, key="events"))
        self.assertIsNone(sources._count("7"))
        self.assertIsNone(sources._count(True), "a bool is not a count")
        self.assertIsNone(sources._count(2.5), "a non-integral value is not a count")
        self.assertIsNone(sources._count(-1), "a negative value is refused rather than clamped to zero")

    def test_a_real_zero_survives(self):
        self.assertEqual(sources._count(0), 0)
        self.assertEqual(sources._count({"events": 0, "missing_previews": 0}, key="events"), 0)

    def test_it_picks_the_named_member_and_sums_a_mapping_without_one(self):
        self.assertEqual(sources._count({"events": 3, "missing_previews": 11}, key="events"), 3)
        self.assertEqual(sources._count({"short_read": 2, "bad_header": 5}), 7)
        self.assertEqual(sources._count({}), 0, "an empty error table is a real zero: nothing was malformed")

    def test_one_unreadable_member_makes_the_whole_count_unknown(self):
        self.assertIsNone(sources._count({"short_read": 2, "bad_header": None}))
