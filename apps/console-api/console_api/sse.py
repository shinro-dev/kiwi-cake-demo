# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api.sse: server-sent events, and the rule that a slow reader is dropped rather than tolerated.

THE SHAPE. One SSE frame per runtime line, and the line is forwarded VERBATIM as the frame's data. The runtime's lines
are already a discriminated union ({"resync": ...}, {"dropped": ...}, a contract event), so re-encoding them under SSE
event names would be a second envelope this process invented, and the page would have to know both. A contract event
carries `id: <seq>`, so a browser that reconnects sends Last-Event-ID and the page resumes from exactly there.

NO FRAME IS NAMED, deliberately. A named frame does not reach EventSource.onmessage: a page has to addEventListener for
each name, and a name it does not know about is delivered nowhere and reported nowhere. A gap is still explicit, because
{"resync": {...}} is its own line and is never folded into an event: a client can always tell 'you missed 40 to 92' from
'nothing happened'.

THE HEARTBEAT IS A COMMENT (`: heartbeat`), every two seconds. That is what an SSE keepalive is: it holds the connection
open through anything that times out an idle socket, a browser ignores it by definition, and it carries no id, so it
cannot move a reader's cursor or look like data.

THE DROP RULE, in two layers. The runtime drops a socket reader that stops reading. This module drops an HTTP client the
same way: the upstream queue is bounded (overflow is a drop), and every write is bounded in TIME, because a client whose
receive window is full makes write() wait, and the queue alone would never notice. Either way the response is ended and,
when a write is stuck, the transport is aborted so the pending write cannot hold the task open. One tab that stops reading
must cost nothing to any other tab, to the runtime or to the run.

THE LAST LINE APPLIES HERE TOO. A stream is a JSON body served one line at a time, so it leaves through the same
sanitizer every unary body does. `sanitize` is a REQUIRED argument of pump(), with no identity default, because this was
a real defect: the frames went straight to json.dumps, and the very text that GET /api/history/<run>/events scrubbed
(an absolute path, a dotted quad, a user@host pair) reached the browser verbatim on GET /api/events. A new stream must
now name what scrubs it or it does not compile.
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct
from typing import Callable, Optional

from aiohttp import web

from .bridge import StreamEnded

HEARTBEAT_S = 2.0
WRITE_TIMEOUT_S = 5.0
RETRY_MS = 2000
# A stream must not hold a backlog for a client that is not reading: those frames are stale before they could arrive, and
# until they are refused nobody knows the client is gone. Two buffers decide how long that takes, and BOTH are bounded.
# MEASURED on this machine, loopback, against a client with a 2 KiB receive buffer that never reads: with the socket's
# send buffer left to the kernel, a write first waited after 1 391 000 bytes; with it bounded here, after 131 300. The
# second number is the one that matters, because it is how much goes nowhere before the drop rule can fire.
WRITE_BUFFER_HIGH = 16 * 1024
WRITE_BUFFER_LOW = 4 * 1024
SOCKET_SEND_BUFFER = 64 * 1024

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}


class SlowReader(Exception):
    """A write did not complete in time: this client is not reading."""


HEARTBEAT_FRAME = b": heartbeat\n\n"


def frame(data, event_id: Optional[str] = None) -> bytes:
    """One SSE frame. The payload is compact JSON on a single line, so no data field ever needs continuation."""
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}data: {body}\n\n".encode("ascii")


class Channel:
    """One client's end of a stream: bounded writes, and an abort for a client that stops taking bytes."""

    def __init__(self, request: web.Request, response: web.StreamResponse, write_timeout_s: float = WRITE_TIMEOUT_S):
        self._request, self._response, self._timeout = request, response, write_timeout_s

    async def send(self, chunk: bytes) -> None:
        try:
            await asyncio.wait_for(self._response.write(chunk), timeout=self._timeout)
        except (asyncio.TimeoutError, TimeoutError):
            reset(self._request)  # the pending write must not keep this task alive, nor the bytes it could not deliver
            raise SlowReader("the client did not take the bytes in time") from None

    async def event(self, data, event_id: Optional[str] = None) -> None:
        await self.send(frame(data, event_id))


def reset(request: web.Request) -> None:
    """End a stalled client's connection so that BOTH sides know. A plain abort closes the socket, but the bytes already
    handed to the kernel stay queued for a peer whose window is shut: the connection lingers on zero-window probes, the
    memory stays pinned, and the client sees an open stream that never moves. SO_LINGER with a zero timeout turns the
    close into a reset, which frees the queue at once and tells the client to start again. Nothing is lost that mattered:
    a client reconnects with the seq it last saw and the runtime states the gap."""
    transport = getattr(request, "transport", None)
    if transport is None:
        return
    try:
        raw_socket = transport.get_extra_info("socket")
        if raw_socket is not None:
            raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except (AttributeError, OSError):
        pass
    transport.abort()


def limit_write_buffer(request: web.Request) -> None:
    """Bound what this connection may hold for a reader that is not taking it. Failure is not fatal: the write timeout
    still ends a stalled client, it just takes the larger amount of buffering to get there."""
    transport = getattr(request, "transport", None)
    if transport is None:
        return
    try:
        transport.set_write_buffer_limits(high=WRITE_BUFFER_HIGH, low=WRITE_BUFFER_LOW)
    except (AttributeError, NotImplementedError, OSError):
        pass
    try:
        raw_socket = transport.get_extra_info("socket")
        if raw_socket is not None:
            raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_SEND_BUFFER)
    except (AttributeError, OSError):
        pass


async def open_stream(request: web.Request, *, headers: Optional[dict] = None, write_timeout_s: float = WRITE_TIMEOUT_S) -> tuple:
    response = web.StreamResponse(status=200, headers={**SSE_HEADERS, **(headers or {})})
    await response.prepare(request)
    limit_write_buffer(request)
    channel = Channel(request, response, write_timeout_s)
    await channel.send(f"retry: {RETRY_MS}\n\n".encode("ascii"))
    return response, channel


async def pump(request: web.Request, stream, render: Callable, *, sanitize: Callable, heartbeat_s: float = HEARTBEAT_S,
               write_timeout_s: float = WRITE_TIMEOUT_S) -> web.StreamResponse:
    """Forward one upstream runtime stream to this client until either end stops. render(line) -> (data, id) or None, and
    sanitize(data) -> data is the same last line a unary body goes through. The SSE id is taken from the rendered seq
    BEFORE scrubbing, because it is an integer the sanitizer never touches and a reader's cursor must not move."""
    response, channel = await open_stream(request, write_timeout_s=write_timeout_s)
    try:
        while True:
            try:
                line = await asyncio.wait_for(stream.get(), timeout=heartbeat_s)
            except (asyncio.TimeoutError, TimeoutError):
                await channel.send(HEARTBEAT_FRAME)
                continue
            if isinstance(line, StreamEnded):
                await channel.event(sanitize({"stream_ended": {"reason_code": line.reason_code, **line.detail}}))
                break
            rendered = render(line)
            if rendered is None:
                continue
            data, event_id = rendered
            await channel.event(sanitize(data), event_id)
    except SlowReader:
        pass  # the transport is already aborted; this client is gone and no other is affected
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError):
        pass  # the tab went away
    finally:
        stream.close()
    return response


def render_event(line):
    """A line of the runtime's event stream, forwarded as it is. A contract event is the only line with a seq, and that
    seq is the SSE id: a resync gap and a drop notice carry none, so neither can move a reader's cursor."""
    if not isinstance(line, dict):
        return None
    seq = line.get("seq")
    return line, str(seq) if isinstance(seq, int) else None


def telemetry_renderer() -> Callable:
    """The runtime's latest-only lines: {"tap": ...} and one per camera, {"preview:wrist": ...}, {"preview:front": ...}.
    A record identical to the one this client already has is not sent again: reopening an idle upstream stream must not
    look like fresh telemetry.

    EACH CAMERA IS DEDUPED ON ITS OWN, under its own key. Sharing one key across roles would have let a busy camera
    suppress the other one's records, so a stalled feed would have gone on looking healthy behind a working one.

    THE PICTURE IS NOT SENT ON THIS STREAM. The runtime's preview record carries the frame as `bytes_b64`, and the
    console has exactly one endpoint that serves pictures, GET /api/preview.mjpg, which reads the same records over its
    OWN upstream subscription. Leaving the bytes on here sent every preview frame to the browser twice: once base64 on
    the SSE stream, where nothing reads it (the page's PreviewSample has no such field and the panel uses the mjpg URL),
    and once as the jpeg the picture is actually drawn from. What goes out here is the metadata: role, sequence numbers,
    clocks, rates, counters, state and reason.

    The DEDUPE KEY is still computed over the whole record, bytes included, so this changes what is sent and never which
    records are considered identical.
    """
    seen: dict = {}

    def render(line):
        if not isinstance(line, dict):
            return None
        for name in line:
            if name != "tap" and not name.startswith("preview"):
                continue
            record = line[name]
            key = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if seen.get(name) == key:
                return None
            seen[name] = key
            if name.startswith("preview") and isinstance(record, dict) and "bytes_b64" in record:
                record = {field: value for field, value in record.items() if field != "bytes_b64"}
            return {name: record}, None
        return None

    return render
