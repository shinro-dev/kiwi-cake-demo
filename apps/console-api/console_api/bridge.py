# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api.bridge: the runtime's blocking client, used from an event loop.

RuntimeClient is deliberately blocking and connection-per-call. Each unary call runs in a worker thread, so a runtime that
is slow to answer costs one thread and never the loop.

A STREAM (events, telemetry) is a blocking generator. It is read in its own thread and handed to the loop through a
BOUNDED queue. When the queue overflows the subscriber is DROPPED: the queue is emptied, a sentinel replaces it and the
reader thread is told to stop. Nothing in this file ever waits for a browser, so a tab that stops reading cannot slow the
runtime, the controller or another tab. This is the same rule the runtime applies to its own socket readers, applied again
one layer out, because each layer can only drop what it owns.

RESUMING. The reader thread remembers the last seq it forwarded. The blocking generator is opened with a short idle
timeout so a thread whose tab has gone away ends within seconds instead of sitting in recv forever; when it times out the
thread reopens the stream with since=<last seq it saw>, which is the runtime's documented resume path. If the cursor is no
longer retained the runtime answers with an explicit resync gap, and that gap is forwarded like any other line: a gap is
never silently skipped here.

A LOST SUBMIT is not resent. RuntimeClient raises ResponseLost when the connection broke after the command went out;
whether it was accepted is not known, so this module answers with the contract's own word for that, status 'uncertain'
with reason_code 'response_lost'. The client then asks GET /api/commands/{command_id}, which is the only way to learn
what really happened.

THE STOP PATH HAS ITS OWN THREAD and does not share the loop's default executor with anything.

asyncio.to_thread runs on the event loop's default ThreadPoolExecutor, whose worker count is min(32, cpu + 4) and whose
work is a FIFO queue. Every other unary call in this file goes there, each one able to sit in the runtime's socket for
up to the client's timeout. Snapshots poll, a history read walks a directory, an observation read decodes an image: fill
those workers and a stop submitted a moment later waits its turn behind them, with the robot moving. The stop executor
is one dedicated worker that nothing else is ever handed, so the stop request reaches the runtime's socket in the time
it takes to connect to it, whatever else this process is in the middle of. It is not a promise about the robot: the
runtime decides what the stop does, and the contract's stop records are what say motion ceased.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Callable, Optional

from demo_runtime.rpc import ReadOnlyClient, ResponseLost, RuntimeClient, RuntimeUnavailable  # noqa: F401  (re-exported)

IDLE_REOPEN_S = 2.0
DEFAULT_QUEUE = 256


class StreamEnded:
    """The last thing a stream delivers: why it stopped."""

    def __init__(self, reason_code: str, detail: Optional[dict] = None):
        self.reason_code, self.detail = reason_code, detail or {}


class UpstreamStream:
    """One blocking runtime stream, read in a thread, delivered to the loop through a bounded queue."""

    def __init__(self, open_stream: Callable, *, loop: asyncio.AbstractEventLoop, maxsize: int = DEFAULT_QUEUE, idle_reopen_s: float = IDLE_REOPEN_S,
                 name: str = "runtime-stream"):
        self._open, self._loop, self._idle, self._name = open_stream, loop, idle_reopen_s, name
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, maxsize))
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.since = 0
        self.dropped = False

    # -- the loop side
    def start(self, since: int = 0) -> "UpstreamStream":
        self.since = since
        self._thread = threading.Thread(target=self._read, name=self._name, daemon=True)
        self._thread.start()
        return self

    async def get(self):
        return await self._queue.get()

    def close(self) -> None:
        self._halt.set()

    # -- the thread side
    def _read(self) -> None:
        while not self._halt.is_set():
            try:
                for line in self._open(self.since, self._idle):
                    if self._halt.is_set():
                        return
                    if isinstance(line, dict) and isinstance(line.get("seq"), int):
                        self.since = line["seq"]
                    self._deliver(line)
            except RuntimeUnavailable:
                self._deliver(StreamEnded("runtime_unavailable"))
                return
            except (OSError, ValueError):  # a broken connection or a line that is not JSON: say so, do not guess
                self._deliver(StreamEnded("runtime_stream_failed"))
                return
        self._deliver(StreamEnded("closed"))

    def _deliver(self, item) -> None:
        try:
            self._loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:  # the loop is gone; so is the reason to keep reading
            self._halt.set()

    def _put(self, item) -> None:
        """Runs in the loop. A full queue is a subscriber that cannot keep up: it is dropped here and now."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped = True
            self._halt.set()
            while not self._queue.empty():
                self._queue.get_nowait()
            self._queue.put_nowait(StreamEnded("slow_reader", {"resume_since": self.since}))


class Bridge:
    """Every runtime call the HTTP layer makes. client is a RuntimeClient, or anything with its surface."""

    def __init__(self, client, *, contract_version: str = "1"):
        self._client = client
        self.contract_version = contract_version
        self._stop_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-stop")

    @property
    def read_only(self) -> bool:
        """True once a hello was answered with contract_incompatible: this process may read and must not command."""
        return bool(getattr(self._client, "read_only", False))

    @property
    def runtime_session(self) -> Optional[str]:
        """The runtime session that answered the last handshake this client made, or None before the first one. Every
        command carries a handshake on its own connection, so this is what the last command actually reached."""
        session = getattr(self._client, "runtime_session", None)
        return session if isinstance(session, str) else None

    async def _call(self, name: str, *args, **kwargs):
        return await asyncio.to_thread(getattr(self._client, name), *args, **kwargs)

    async def _call_urgently(self, name: str, *args, **kwargs):
        """The same call on the dedicated stop worker, so a saturated default executor cannot delay it."""
        loop = asyncio.get_running_loop()
        method = getattr(self._client, name)
        return await loop.run_in_executor(self._stop_executor, lambda: method(*args, **kwargs))

    def close(self) -> None:
        self._stop_executor.shutdown(wait=False)

    async def hello(self) -> dict:
        return await self._call("hello")

    async def snapshot(self) -> dict:
        return await self._call("snapshot")

    async def status(self) -> dict:
        return await self._call("status")

    async def cake_status(self) -> dict:
        return await self._call("cake_status")

    async def robot_status(self) -> dict:
        return await self._call("robot_status")

    async def robot_confirm_session(self, session_uuid: str, host_instance: dict) -> dict:
        """The session and supervisor host instance the operator saw, and nothing else. The runtime compares each
        component with a fresh read of the robot."""
        return await self._call("robot_confirm_session", session_uuid, host_instance)

    async def capability_check_state(self) -> dict:
        return await self._call("capability_check_state")

    async def capability_check_apply(self, candidate_key: str, payload_digest: str, session_uuid: str) -> dict:
        """A prepared candidate by KEY, the digest the operator was shown for it and the resident session they were
        shown. On the default executor like the mission's activation: it is not a stop, and a stop never waits for it."""
        return await self._call("capability_check_apply", candidate_key, payload_digest, session_uuid)

    async def capability_signature_state(self) -> dict:
        return await self._call("capability_signature_state")

    async def capability_signature_check(self) -> dict:
        """Run the release verifier on the robot over every deployed capsule. It carries nothing. On the default executor
        like the slot check: it is not a stop, and a stop never waits for it."""
        return await self._call("capability_signature_check")

    async def result(self, command_id: str) -> dict:
        return await self._call("result", command_id)

    async def history(self) -> dict:
        return await self._call("history")

    async def history_events(self, run_id: str) -> dict:
        return await self._call("history_events", run_id)

    async def run_output(self, run_id: str, stream: str, max_bytes) -> dict:
        return await self._call("run_output", run_id, stream, max_bytes)

    async def observation(self, observation_id: str, what: str) -> dict:
        return await self._call("observation", observation_id, what)

    async def frame(self, observation_id: str, what: str, max_width: int) -> dict:
        return await self._call("frame", observation_id, what, max_width)

    async def stop(self) -> dict:
        """The runtime's un-journaled stop op, for a caller that sent no command of its own. On the stop worker."""
        return await self._call_urgently("stop")

    async def submit_stop(self, command: dict) -> dict:
        """A contract stop COMMAND, submitted so the runtime journals it under its own id and answers the recorded
        result to a repeat of it. On the stop worker, for the same reason as stop(): a stop that waits behind a
        snapshot poll is a stop that did not happen when it was pressed."""
        try:
            return await self._call_urgently("submit", command)
        except ResponseLost as lost:
            command_id = lost.command_id or command.get("command_id") or ""
            return {"command_id": command_id, "status": "uncertain", "reason_code": "response_lost", "run_id": None}

    async def resolve(self, command_id: str, finding: str, note=None) -> dict:
        """Ask the runtime to RECONCILE an uncertain command. This never resends the command and never clears anything
        here: the runtime re-reads its own evidence, records the operator's finding as an attestation beside it, and
        says what it found and whether the block lifted."""
        return await self._call("resolve", command_id, finding, note)

    # -- the mission, a surface of its own that carries no contract object
    async def mission_state(self) -> dict:
        return await self._call("mission_state")

    async def mission_candidates(self) -> dict:
        return await self._call("mission_candidates")

    async def mission_start(self, targets=None) -> dict:
        return await self._call("mission_start", targets)

    async def mission_stop(self) -> dict:
        """On the dedicated stop worker, for the same reason the run stop is: a stop that waits behind a snapshot poll
        or behind an activation that is in flight is a stop that did not happen when it was pressed. An activation
        holds a default-executor thread for as long as the resident takes, which is exactly the saturation this
        worker exists to be immune to."""
        return await self._call_urgently("mission_stop")

    async def mission_activate(self, candidate_key: str) -> dict:
        """One prepared candidate, by KEY. This process carries no bytes and no path, here or anywhere."""
        return await self._call("mission_activate", candidate_key)

    async def mission_retry(self, mission_id: Optional[str] = None) -> dict:
        """Retry the held attempt, naming at most the mission the operator saw held."""
        return await self._call("mission_retry", mission_id)

    async def submit(self, command: dict) -> dict:
        """The contract command_result. A lost answer becomes 'uncertain': the command is NEVER sent a second time."""
        try:
            return await self._call("submit", command)
        except ResponseLost as lost:
            command_id = lost.command_id or command.get("command_id") or ""
            return {"command_id": command_id, "status": "uncertain", "reason_code": "response_lost", "run_id": None}

    # -- streams
    def events(self, since: int, *, loop: asyncio.AbstractEventLoop, maxsize: int = DEFAULT_QUEUE, idle_reopen_s: float = IDLE_REOPEN_S) -> UpstreamStream:
        def open_stream(resume, idle):
            return self._client.events(resume, idle_timeout_s=idle)

        return UpstreamStream(open_stream, loop=loop, maxsize=maxsize, idle_reopen_s=idle_reopen_s, name="runtime-events").start(since)

    def telemetry(self, *, loop: asyncio.AbstractEventLoop, maxsize: int = DEFAULT_QUEUE, idle_reopen_s: float = IDLE_REOPEN_S) -> UpstreamStream:
        def open_stream(_resume, idle):
            return self._client.telemetry(idle_timeout_s=idle)

        return UpstreamStream(open_stream, loop=loop, maxsize=maxsize, idle_reopen_s=idle_reopen_s, name="runtime-telemetry").start(0)
