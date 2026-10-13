#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_streams.py: the three live surfaces. The one that matters most is the drop rule, so it is
# proved twice: once on the bounded queue with no socket at all, and once on a real TCP client that sets a tiny receive
# buffer and then never reads, while a second client on the SAME API keeps receiving throughout.
# Run directly: python3 apps/console-api/tests/test_streams.py
import asyncio
import base64
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ApiCase, StubRuntime, event_object, read_frames  # noqa: E402

from console_api.app import Options  # noqa: E402
from console_api.bridge import StreamEnded, UpstreamStream  # noqa: E402

JPEG = base64.b64encode(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")).decode("ascii")
UNAVAILABLE_PREVIEW = {"role": "wrist", "seq": None, "capture_clock": None, "receive_age_ms": None, "measured_fps": None,
                       "available": False, "reason": "preview_source_not_attached"}
LONG_TEXT = "x" * 1000  # within the contract's safe_text bound; big enough to fill a small receive window quickly
EVENTS_IN_BURST = 300  # about 390 KB, comfortably past the measured 131 KB at which a stalled client's write first waits


def preview_record(seq: int, *, image: str = JPEG) -> dict:
    return {"preview": {"role": "wrist", "seq": seq, "capture_clock": {"domain": "pi_monotonic", "capture_mono_ns": seq},
                        "receive_age_ms": 12.0, "measured_fps": 5.0, "available": True, "reason": None,
                        "content_type": "image/jpeg", "bytes_b64": image}}


class TestEventStream(ApiCase):
    async def start_api(self, **kwargs):
        await super().start_api(options=Options(heartbeat_s=0.2, write_timeout_s=2.0, queue_size=64), **kwargs)

    async def test_every_event_arrives_with_its_seq_as_the_sse_id(self):
        for seq in (1, 2, 3):
            self.stub.emit(event_object(seq))
        response = await self.client.get("/api/events?since=0")
        self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        frames = await read_frames(response, 3)
        self.assertEqual([frame["id"] for frame in frames], ["1", "2", "3"])
        self.assertEqual([frame["data"]["seq"] for frame in frames], [1, 2, 3])
        self.assertEqual(frames[0]["data"], event_object(1), "the runtime's event is forwarded as it is")
        response.close()

    async def test_a_silent_stream_still_beats(self):
        response = await self.client.get("/api/events?since=0")
        frames = await read_frames(response, 2)
        self.assertEqual([frame["comment"] for frame in frames], ["heartbeat", "heartbeat"])
        self.assertNotIn("data", frames[0], "a heartbeat is a comment: it is not data and carries no cursor")
        response.close()

    async def test_a_cursor_the_runtime_no_longer_retains_gets_an_explicit_gap(self):
        self.stub.retained_from_seq = 40
        for seq in (40, 41):
            self.stub.emit(event_object(seq))
        response = await self.client.get("/api/events?since=5")
        frames = await read_frames(response, 3)
        self.assertEqual(frames[0]["data"], {"resync": {"retained_from_seq": 40, "missed": [6, 39]}})
        self.assertNotIn("id", frames[0], "a gap carries no cursor of its own")
        self.assertEqual([frame["data"]["seq"] for frame in frames[1:]], [40, 41])
        response.close()

    async def test_a_browser_resumes_from_last_event_id_when_no_since_is_given(self):
        for seq in (1, 2, 3):
            self.stub.emit(event_object(seq))
        response = await self.client.get("/api/events", headers={"Last-Event-ID": "2"})
        frames = await read_frames(response, 1)
        self.assertEqual(frames[0]["data"]["seq"], 3)
        response.close()

    async def test_a_since_that_is_not_a_cursor_is_refused(self):
        for value in ("-1", "later", "1.5"):
            response = await self.client.get(f"/api/events?since={value}")
            self.assertEqual(response.status, 400, value)

    async def test_an_upstream_failure_is_named_and_the_stream_ends(self):
        response = await self.client.get("/api/events?since=0")
        await read_frames(response, 1)  # a heartbeat: the stream is open
        self.stub.break_stream = True
        frames = await read_frames(response, 6)
        endings = [frame["data"]["stream_ended"]["reason_code"] for frame in frames if "data" in frame and "stream_ended" in frame["data"]]
        self.assertEqual(endings, ["runtime_stream_failed"])
        response.close()


class TestSlowReaderIsDropped(ApiCase):
    async def start_api(self, **kwargs):
        await super().start_api(options=Options(heartbeat_s=0.2, write_timeout_s=0.5, queue_size=4096), **kwargs)

    async def _stalled_client(self) -> socket.socket:
        """A real TCP client with a tiny receive buffer that asks for the stream and then never reads a byte."""
        stalled = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)  # set BEFORE connect: it decides the advertised window
        stalled.setblocking(True)
        stalled.connect(("127.0.0.1", self.port))
        stalled.sendall(f"GET /api/events?since=0 HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAccept: text/event-stream\r\n\r\n".encode("ascii"))
        return stalled

    async def test_the_client_that_never_reads_is_dropped_and_the_other_is_not(self):
        good = await self.client.get("/api/events?since=0")
        self.assertEqual(good.status, 200)
        stalled = await self._stalled_client()
        self.addCleanup(stalled.close)
        await asyncio.sleep(0.2)

        # MEASURED: with the send buffer bounded, a write to a client with a 2 KiB receive buffer first waits after about
        # 131 KB. This burst is comfortably past that, so the stalled client's write really does stall.
        payload = {"stream": "log", "level": "info", "text": LONG_TEXT, "redacted": False}
        for seq in range(1, EVENTS_IN_BURST + 1):
            self.stub.emit(event_object(seq, payload=payload))

        # Not one byte is read for well past the write timeout, so the premise holds the whole time: this client really
        # never reads. Only then is the socket drained, which no longer changes anything because the server has already
        # let go. What the drain shows is the proof: the connection has ENDED, and it holds one receive buffer rather
        # than the whole burst, so the server stopped writing to it instead of queueing events nobody was taking.
        await asyncio.sleep(3.0)  # six times the write timeout
        stalled.setblocking(False)
        drained, ended = 0, None
        deadline = asyncio.get_running_loop().time() + 15.0
        while ended is None and asyncio.get_running_loop().time() < deadline:
            try:
                chunk = stalled.recv(1 << 16)
                if chunk == b"":
                    ended = "closed"
                else:
                    drained += len(chunk)
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except ConnectionResetError:
                ended = "reset"
            except OSError as error:
                ended = type(error).__name__
        self.assertIn(ended, ("reset", "closed"), "the client that never read was not dropped")
        self.assertLess(drained, 64 * 1024, "the server kept writing to a client that was not reading")

        # The other client, on the SAME API, received everything up to the last event.
        seen = 0
        while seen < EVENTS_IN_BURST:
            frames = await read_frames(good, 50, timeout=15.0)
            if not frames:
                break
            seen = max([frame["data"]["seq"] for frame in frames if "data" in frame and "seq" in frame["data"]] + [seen])
        self.assertEqual(seen, EVENTS_IN_BURST, "the healthy client lost events while the other one stalled")
        good.close()


class TestBoundedQueueDrop(unittest.IsolatedAsyncioTestCase):
    """The queue rule on its own: overflow costs the SUBSCRIBER. The producer is never made to wait, and once the
    subscriber is gone the reader stops pulling from the runtime instead of filling a queue nobody reads."""

    async def test_overflow_drops_the_subscriber_and_says_where_to_resume(self):
        produced, blocked_for = [], []
        thread_ended = threading.Event()

        def open_stream(since, _idle):
            try:
                for seq in range(since + 1, since + 2000):
                    started = time.monotonic()
                    produced.append(seq)
                    yield {"seq": seq}
                    blocked_for.append(time.monotonic() - started)
            finally:
                thread_ended.set()

        stream = UpstreamStream(open_stream, loop=asyncio.get_running_loop(), maxsize=2, idle_reopen_s=0.2).start(0)
        self.addCleanup(stream.close)
        for _ in range(50):
            await asyncio.sleep(0.02)
            if stream.dropped:
                break
        self.assertTrue(stream.dropped, "the queue did not overflow")
        self.assertGreater(len(produced), 2, "the producer stopped at the queue's capacity instead of running past it")
        self.assertLess(max(blocked_for or [0.0]), 0.05, "the producer was held back by the slow consumer")
        item = await stream.get()
        self.assertIsInstance(item, StreamEnded)
        self.assertEqual(item.reason_code, "slow_reader")
        self.assertIn("resume_since", item.detail)
        self.assertTrue(thread_ended.wait(2.0), "the reader kept pulling from the runtime for a subscriber that is gone")
        self.assertLess(len(produced), 2000, "the reader drained the whole upstream after the drop")


class TestTelemetryStream(ApiCase):
    async def start_api(self, **kwargs):
        await super().start_api(options=Options(heartbeat_s=0.2, write_timeout_s=2.0, queue_size=64), **kwargs)

    async def test_tap_and_preview_arrive_under_their_own_names(self):
        self.stub.publish({"tap": {"available": True, "joints": {"shoulder_pan": 1.5}}})
        self.stub.publish({"preview": UNAVAILABLE_PREVIEW})
        response = await self.client.get("/api/telemetry")
        frames = await read_frames(response, 2)
        self.assertEqual({key for frame in frames for key in frame["data"]}, {"tap", "preview"})
        self.assertNotIn("id", frames[0], "telemetry is latest-only: it carries no cursor")
        response.close()

    async def test_the_picture_is_not_sent_on_this_stream(self):
        # The runtime's preview record carries the frame. This endpoint serves METADATA; the picture has exactly one
        # endpoint, /api/preview.mjpg, which reads the same records on its own subscription. Leaving the bytes here
        # sent every frame to the browser twice, and nothing on the page reads the base64 copy.
        self.stub.publish(preview_record(1))
        response = await self.client.get("/api/telemetry")
        frames = await read_frames(response, 1)
        record = frames[0]["data"]["preview"]
        self.assertNotIn("bytes_b64", record)
        # Everything that is not the picture still arrives, including what the screen routes on.
        for field in ("role", "seq", "capture_clock", "measured_fps", "available"):
            self.assertIn(field, record, field)
        response.close()

    async def test_two_frames_that_differ_only_in_the_picture_are_still_two_records(self):
        # The dedupe key is computed over the WHOLE record, bytes included, so dropping the bytes from what is sent
        # must not make a new frame look like a repeat of the last one.
        other = "".join(reversed(JPEG))
        self.stub.publish(preview_record(1))
        self.stub.publish(preview_record(1, image=other))
        response = await self.client.get("/api/telemetry")
        frames = await read_frames(response, 2)
        previews = [frame["data"]["preview"] for frame in frames if "data" in frame and "preview" in frame["data"]]
        self.assertEqual(len(previews), 2, "a frame with a new picture was dropped as a repeat")
        response.close()

    async def test_a_record_that_has_not_changed_is_not_sent_again(self):
        record = {"tap": {"available": True, "joints": {"shoulder_pan": 1.5}}}
        self.stub.publish(record)
        self.stub.publish(dict(record))
        self.stub.publish({"tap": {"available": True, "joints": {"shoulder_pan": 2.5}}})
        response = await self.client.get("/api/telemetry")
        frames = await read_frames(response, 3)
        taps = [frame["data"]["tap"]["joints"]["shoulder_pan"] for frame in frames if "data" in frame and "tap" in frame["data"]]
        self.assertEqual(taps, [1.5, 2.5])
        self.assertEqual(frames[-1]["comment"], "heartbeat")
        response.close()


class TestPreview(ApiCase):
    async def start_api(self, **kwargs):
        await super().start_api(options=Options(heartbeat_s=0.2, write_timeout_s=2.0, queue_size=64, preview_first_frame_s=0.5), **kwargs)

    async def test_a_runtime_with_no_preview_source_says_so_and_sends_no_image(self):
        self.stub.publish({"preview": UNAVAILABLE_PREVIEW})
        response = await self.client.get("/api/preview.mjpg?role=wrist")
        self.assertEqual(response.status, 503)
        answer = await self.json_of(response)
        self.assertFalse(answer["available"])
        self.assertEqual(answer["reason_code"], "preview_source_not_attached")
        self.assertNotIn("image", response.headers.get("Content-Type", ""))

    async def test_silence_is_reported_and_not_filled_in(self):
        response = await self.client.get("/api/preview.mjpg")
        self.assertEqual(response.status, 503)
        self.assertEqual((await self.json_of(response))["reason_code"], "preview_unavailable")

    async def test_an_unknown_role_is_refused(self):
        response = await self.client.get("/api/preview.mjpg?role=ceiling")
        self.assertEqual(response.status, 400)

    async def test_frames_the_runtime_does_hold_are_streamed_as_multipart(self):
        self.stub.publish(preview_record(1))
        self.stub.publish(preview_record(2))
        response = await self.client.get("/api/preview.mjpg?role=wrist")
        self.assertEqual(response.status, 200)
        content_type = response.headers["Content-Type"]
        self.assertTrue(content_type.startswith("multipart/x-mixed-replace; boundary="))
        boundary = content_type.partition("boundary=")[2]
        body = await asyncio.wait_for(response.content.readexactly(2 * (len(f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: 23\r\n\r\n") + 23 + 2)), timeout=5.0)
        self.assertEqual(body.count(f"--{boundary}".encode("ascii")), 2)
        self.assertEqual(body.count(base64.b64decode(JPEG)), 2)
        response.close()

    async def test_a_source_that_goes_away_ends_the_stream_instead_of_repeating_a_stale_picture(self):
        self.stub.publish(preview_record(1))
        response = await self.client.get("/api/preview.mjpg?role=wrist")
        self.assertEqual(response.status, 200)
        self.stub.publish({"preview": UNAVAILABLE_PREVIEW})
        body = await asyncio.wait_for(response.content.read(), timeout=10.0)
        self.assertEqual(body.count(base64.b64decode(JPEG)), 1, "one real frame, and nothing invented after it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
