# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.rpc: the runtime's one door. A Unix domain socket (mode 0600, inside the private data directory) that speaks
newline-delimited JSON: one request object per line, one or more response lines.

The API server, the command line and every manual action go through THIS socket, so there is one command authority and one
arbitration, whoever asks.

  {"op":"hello","contract_version":"1"}        -> {"ok":true,"contract_version":"1","runtime_session":"..."}
                                                  or {"ok":false,"reason_code":"contract_incompatible"}: the client turns read-only
  {"op":"snapshot"}                             -> a contract snapshot, plus "unresolved_command" when one holds the block
  {"op":"events","since":N}                     -> stream: first {"resync":{...}} when N is older than the ring (an explicit GAP),
                                                  then one contract event per line
  {"op":"telemetry"}                            -> stream, latest-only: {"tap":{...}} and {"preview":{...}}; a preview record
                                                  carries the latest JPEG as bytes_b64, which is what /api/preview.mjpg serves
  {"op":"submit","command":{...}}               -> a contract command_result. A STOP command in a runtime with a mission
                                                  surface also stops the mission, and its answer carries the mission's
                                                  part beside the command_result's fields under "mission"; console_api
                                                  takes it off before the receipt goes anywhere
  {"op":"result","command_id":"..."}            -> a contract command_result: the ONLY way to resolve a lost response
  {"op":"stop"}                                 -> {"ok":true,"stop":{"requested_mono_ns":..,"run_id":..}|null,"mission":{..}|null}:
                                                  the run first, then the mission; neither part says motion ceased
  {"op":"resolve","command_id":"..","finding":"..","note":".."} -> the daemon RECONCILES the uncertain command against the
                                                  evidence and records the operator's finding as an attestation beside it:
                                                  {"ok":true,"classification":"..","evidence":{..},"cleared":bool,"cleared_by":".."}
                                                  or a refusal ({"reason_code":"controller_alive","contradiction":{..}})
  {"op":"mission_state"} {"op":"mission_candidates"}            -> the mission surface's two READS: the folded mission
                                                  view (its own MISSION_RECORD_VERSION, never a contract object), and
                                                  the prepared activation candidates by key and digest
  {"op":"mission_start","targets":[..]} {"op":"mission_stop"}   -> start one mission (refused with run_active while a
                                                  controller run holds the run slot), or stop: the run first, then the
                                                  mission, answered with the mission's part and the run's under
                                                  "run_stop"; a stop is answered while a target waits on a capability
                                                  just as while one runs, and with nothing to stop it says stopping
                                                  false, no_running_mission
  {"op":"mission_activate","candidate_key":".."} -> submit ONE prepared candidate by key: {"ok":true,"operation_uuid",
                                                  "activation_state","outcome","transaction_phase","detail",
                                                  "transaction","slot_before","slot_after","stopped_during_activation"},
                                                  or mission_stopping with nothing submitted, or activation_in_flight
                                                  (with the in-flight "operation_uuid" when recorded) with nothing
                                                  submitted while this mission's previous operation is still with the
                                                  resident. A caller names a key and supplies no bytes, no path and no
                                                  operation
  {"op":"mission_retry","mission_id":".."}      -> retry the attempt a mission HOLDS on for the operator's decision
                                                  (mission_id optional: the hold the caller saw). {"ok":true,
                                                  "mission_id","target_id","attempt","retrying"}, or a refusal with
                                                  nothing written (mission_not_held, mission_retry_not_offered,
                                                  mission_retry_already_requested, mission_park_unavailable,
                                                  mission_retry_not_enabled, mission_retry_not_attended, ...)
  {"op":"robot_status"}                         -> the robot's resident as the robot_status surface (its own version, never a
                                                  contract object); a READ of the cached reading
  {"op":"robot_confirm_session","session_uuid":"..","host_instance":{"slot_instance":"..","spawn_sequence":N,"pid":N}}
                                               -> the operator confirms the robot resident session and the supervisor.lekiwi
                                                  host instance they saw; refused unless EVERY component equals what the
                                                  robot reports now, read fresh. Held by this runtime only
  {"op":"capability_check_state"}               -> the capability slot check's READ: the prepared candidates by key and
                                                  digest, the robot's resident as the cached reading reports it (session,
                                                  whether a host runs, the capability slot), the first reason an apply
                                                  would be refused now, and every check on the record; or
                                                  capability_check_unavailable with a detail saying what is missing
  {"op":"capability_check_apply","candidate_key":"..","payload_digest":"sha256:..","session_uuid":".."}
                                               -> apply ONE prepared candidate to the robot's capability slot and read it
                                                  back, WITHOUT MOTION and never for a mission: refused unless a fresh read
                                                  reports the pinned build, exactly that session and NO host running, and
                                                  answered with the slot and the host read before and after
  {"op":"capability_signature_state"}           -> the signature policy check's READ: whether a run is in flight, the first
                                                  reason one would be refused now, the policy (require) and every run on
                                                  the record; or signature_check_unavailable with a detail
  {"op":"capability_signature_check"}           -> run the release's own verifier on the robot over every deployed capsule,
                                                  under the resident's trusted keys, a throwaway key and any unsigned twin,
                                                  WITHOUT MOTION and without touching a slot; answered with the verdict
                                                  table. It takes nothing: no capsule, no key, no path, no policy
  {"op":"preview"}                              -> the LATEST preview frame as one answer, instead of the stream
  {"op":"manifest","run_id":".."}               -> the manifest that run wrote about itself, checked against the contract
                                                  first: {"ok":false,"reason_code":"manifest_invalid"} rather than a
                                                  document a client typed against the contract cannot read
  {"op":"export","run_id":".."}                 -> one run packaged for somebody else: {"index","records","images"}, bounded
                                                  in size and in frames, with anything it could not carry stated on the index
  {"op":"observation",...} {"op":"frame",...} {"op":"history"} {"op":"history_events",...} {"op":"status"}

SLOW READERS. Every connection has its own thread and its own bounded queue. A send that does not complete within
SEND_TIMEOUT_S ends the connection; a queue that overflows drops the subscriber. Neither ever reaches the supervisor or the
tailer: a reader that stops reading costs one thread that goes away after the timeout. A dropped reader reconnects with
since=<last seq it saw> and is told about the gap if there is one.

RuntimeClient is the small blocking client. It never resends a command by itself: when the response to a submit is lost it
raises ResponseLost, and the caller asks result(command_id).
"""
from __future__ import annotations

import json
import os
import queue
import select
import socket
import threading
import uuid
from typing import Callable, Iterator, Optional

from .journal import CONTRACT_VERSION

SEND_TIMEOUT_S = 2.0
MAX_REQUEST_BYTES = 1 << 20
# The ops a connection may only ask for after a handshake. The three mission ops that change something are here for
# the same reason the other three are: they start, stop or submit, and a client that has not said which contract it
# speaks has not said who it is. The two mission READS are not, exactly like snapshot and cake_status.
# robot_confirm_session is here because it GRANTS permission to move: a client that has not said who it is may not.
# capability_check_apply is here because it submits an ApplyPlan to the robot's resident: it moves no motor and it does
# move a Cake slot, and a client that has not said who it is may not do that either. Its read is not here.
# capability_signature_check is here although it mutates nothing: it spawns the release verifier on the robot, and a client
# that has not said who it is may not start work there either. Its read is not here.
MUTATING_OPS = frozenset({"submit", "stop", "resolve", "mission_start", "mission_stop", "mission_activate", "mission_retry", "robot_confirm_session",
                          "capability_check_apply", "capability_signature_check"})
STREAMING_OPS = frozenset({"events", "telemetry"})


class _Address:
    """A Unix socket address is limited to about 100 bytes, and a private data directory can sit deeper than that. A long
    path is reached through a descriptor of its directory (/proc/self/fd/<n>/<name>), which is short whatever the depth."""

    def __init__(self, path: str):
        self._path, self._dirfd = path, None

    def __enter__(self) -> str:
        if len(os.fsencode(self._path)) <= 100:
            return self._path
        self._dirfd = os.open(os.path.dirname(self._path) or ".", os.O_RDONLY | os.O_DIRECTORY)
        return f"/proc/self/fd/{self._dirfd}/{os.path.basename(self._path)}"

    def __exit__(self, *_) -> None:
        if self._dirfd is not None:
            os.close(self._dirfd)


class _Connection:
    """One accepted socket. send() is bounded in time; a reader that does not take the bytes loses the connection."""

    def __init__(self, sock: socket.socket):
        self.sock, self._buffer, self.hello_ok = sock, b"", False

    def send(self, obj) -> None:
        self.sock.settimeout(SEND_TIMEOUT_S)
        self.sock.sendall((json.dumps(obj, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii"))

    def read_line(self) -> Optional[bytes]:
        """The next request line, or None when the peer closed. Blocks without a timeout: an idle client is not an error."""
        self.sock.settimeout(None)
        while b"\n" not in self._buffer:
            if len(self._buffer) > MAX_REQUEST_BYTES:
                return None
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                return None
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line

    def peer_closed(self) -> bool:
        """True when the peer closed. Bytes a streaming client sends are ignored: a stream takes no further request."""
        try:
            readable, _, _ = select.select([self.sock], [], [], 0)
            if not readable:
                return False
            self.sock.settimeout(0.0)
            return self.sock.recv(4096) == b""
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            return True


class RuntimeServer:
    """handle(op, request, connection) -> a response object, or None when the handler streamed by itself."""

    def __init__(self, socket_path, handle: Callable, stream: Callable):
        self.socket_path, self._handle, self._stream = os.fspath(socket_path), handle, stream
        self._sock: Optional[socket.socket] = None
        self._closing = threading.Event()
        self.connections_served = 0

    def start(self) -> None:
        try:
            os.unlink(self.socket_path)  # the caller holds the data directory's lock: a socket file found here is stale
        except FileNotFoundError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o177)  # the file is born 0600; chmod afterwards would leave a window
        try:
            with _Address(self.socket_path) as address:
                self._sock.bind(address)
        finally:
            os.umask(previous)
        os.chmod(self.socket_path, 0o600)
        self._sock.listen(64)
        threading.Thread(target=self._accept_loop, name="rpc-accept", daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._closing.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections_served += 1
            threading.Thread(target=self._serve, args=(conn,), name="rpc-connection", daemon=True).start()

    def _serve(self, sock: socket.socket) -> None:
        connection = _Connection(sock)
        try:
            while not self._closing.is_set():
                line = connection.read_line()
                if line is None:
                    return
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                except ValueError:
                    request = None
                if not isinstance(request, dict) or not isinstance(request.get("op"), str):
                    connection.send({"ok": False, "reason_code": "bad_request"})
                    continue
                op = request["op"]
                if op in MUTATING_OPS and not connection.hello_ok:
                    connection.send({"ok": False, "reason_code": "hello_required"})
                    continue
                if op in STREAMING_OPS:
                    self._stream(op, request, connection, self._closing)
                    return  # a stream ends with its connection
                connection.send(self._handle(op, request, connection))
        except (OSError, socket.timeout):
            return  # the peer went away or stopped reading: its thread ends here and nothing else notices
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def close(self) -> None:
        self._closing.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass


def stream_events(log, since: int, connection: _Connection, closing: threading.Event, *, sanitize: Callable) -> None:
    """sanitize has NO identity default on purpose: everything that leaves this process goes through the sanitizer, and a
    stream is no exception. A caller that forgets it fails at the call, not silently on the wire."""
    subscription = log.subscribe(since)
    last = since
    try:
        if subscription.resync is not None:
            connection.send({"resync": subscription.resync})
        for event in subscription.backlog:
            connection.send(sanitize(event))
            last = event["seq"]
        while not closing.is_set():
            try:
                event = subscription.queue.get(timeout=0.25)
            except queue.Empty:
                if subscription.dropped:
                    connection.send({"dropped": {"reason": "slow_reader", "resume_since": last}})
                    return
                if connection.peer_closed():
                    return
                continue
            connection.send(sanitize(event))
            last = event["seq"]
    finally:
        log.unsubscribe(subscription)


def stream_telemetry(hub, connection: _Connection, closing: threading.Event, unavailable: dict, *, sanitize: Callable) -> None:
    """Latest-only. When nothing was ever published the client is told so once, instead of waiting in silence.

    Every record leaves through sanitize, like every other answer this socket gives. It used to be the ONE outgoing path
    with no scrub at all: a tap or preview record carries a host session, a source stamp and a free-text reason, and none
    of it was ever looked at on the way out. Image bytes are unaffected: the sanitizer leaves bytes_b64 alone."""
    seen: dict = {}
    hub.add_reader(1)
    try:
        for stream, record in unavailable.items():
            if hub.latest(stream)[0] is None:
                connection.send({stream: sanitize(record)})
        while not closing.is_set():
            fresh = hub.newer_than(seen, timeout=0.5)
            for stream, record in fresh.items():
                connection.send({stream: sanitize(record)})
            if not fresh and connection.peer_closed():
                return
    finally:
        hub.add_reader(-1)


# ---- the client ---------------------------------------------------------------------------------------------------------------

class RuntimeUnavailable(ConnectionError):
    """The runtime socket could not be reached."""


class ReadOnlyClient(PermissionError):
    """The runtime speaks another contract version: this client may read and must not command."""


class ResponseLost(ConnectionError):
    """The connection broke after a command was sent. Whether it was accepted is NOT known: ask result(command_id). Never resend."""

    def __init__(self, command_id: str):
        super().__init__("the response to a submitted command was lost")
        self.command_id = command_id


class RuntimeClient:
    def __init__(self, socket_path, *, contract_version: str = CONTRACT_VERSION, client_session: Optional[str] = None, timeout_s: float = 30.0):
        self.socket_path, self.contract_version, self.timeout_s = os.fspath(socket_path), contract_version, timeout_s
        self.client_session = client_session or f"client-{uuid.uuid4().hex[:12]}"
        self.read_only = False
        self.runtime_session: Optional[str] = None

    # -- plumbing
    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_s)
        try:
            with _Address(self.socket_path) as address:
                sock.connect(address)
        except OSError:
            sock.close()
            raise RuntimeUnavailable("the runtime socket cannot be reached") from None
        return sock

    @staticmethod
    def _send(sock: socket.socket, obj: dict) -> None:
        sock.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode("ascii"))

    @staticmethod
    def _lines(sock: socket.socket) -> Iterator[dict]:
        buffer = b""
        while True:
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                if line.strip():
                    yield json.loads(line)
            chunk = sock.recv(1 << 16)
            if not chunk:
                return
            buffer += chunk

    def _hello_on(self, sock: socket.socket, lines: Iterator[dict]) -> dict:
        self._send(sock, {"op": "hello", "contract_version": self.contract_version, "client_session": self.client_session})
        answer = next(lines, None)
        if answer is None:
            raise RuntimeUnavailable("the runtime closed the connection during hello")
        if answer.get("ok"):
            self.read_only, self.runtime_session = False, answer.get("runtime_session")
        elif answer.get("reason_code") == "contract_incompatible":
            self.read_only = True
        return answer

    def _call(self, request: dict, *, hello: bool = False) -> dict:
        sock = self._connect()
        try:
            lines = self._lines(sock)
            if hello:
                self._hello_on(sock, lines)
                if self.read_only:
                    raise ReadOnlyClient("the runtime speaks another contract version: commands are disabled in this client")
            self._send(sock, request)
            answer = next(lines, None)
            if answer is None:
                raise RuntimeUnavailable("the runtime closed the connection without an answer")
            return answer
        finally:
            sock.close()

    # -- reading
    def hello(self) -> dict:
        sock = self._connect()
        try:
            return self._hello_on(sock, self._lines(sock))
        finally:
            sock.close()

    def snapshot(self) -> dict:
        return self._call({"op": "snapshot"})

    def status(self) -> dict:
        return self._call({"op": "status"})

    def cake_status(self) -> dict:
        """The Cake resident status surface, which carries its own version and is not part of the contract snapshot."""
        return self._call({"op": "cake_status"})

    def robot_status(self) -> dict:
        """The robot's resident status surface, which carries its own version and is not part of the contract snapshot."""
        return self._call({"op": "robot_status"})

    def robot_confirm_session(self, session_uuid: str, host_instance: dict) -> dict:
        """Confirm the robot resident session and supervisor host instance (slot_instance, spawn_sequence, pid) the operator
        saw. The runtime compares each with a fresh read and refuses on any difference; a refusal never carries the
        current values."""
        self._refuse_when_read_only()
        return self._call({"op": "robot_confirm_session", "session_uuid": session_uuid, "host_instance": host_instance}, hello=True)

    def result(self, command_id: str) -> dict:
        return self._call({"op": "result", "command_id": command_id})

    def history(self) -> dict:
        return self._call({"op": "history"})

    def history_events(self, run_id: str) -> dict:
        return self._call({"op": "history_events", "run_id": run_id})

    def run_output(self, run_id: str, stream: str = "stdout", max_bytes: int = None) -> dict:
        """The controller's own stdout or stderr for one run, as a bounded tail. The runtime resolves the path from the
        run id: a caller never names a file."""
        request = {"op": "run_output", "run_id": run_id, "stream": stream}
        if max_bytes is not None:
            request["max_bytes"] = max_bytes
        return self._call(request)

    def manifest(self, run_id: str) -> dict:
        """What one run states about itself. The runtime refuses a manifest that does not fit the contract rather than
        serving it, so a caller reads {"ok": true, "manifest": ...} or a reason code, never a half-checked document."""
        return self._call({"op": "manifest", "run_id": run_id})

    def export(self, run_id: str) -> dict:
        """One run packaged for somebody else: {"index", "records", "images"}. Bounded, read-only, and a frame it could
        not carry is a stated gap on the index rather than a missing picture nobody mentions."""
        return self._call({"op": "export", "run_id": run_id})

    def observation(self, observation_id: str, what: str = "record") -> dict:
        return self._call({"op": "observation", "observation_id": observation_id, "what": what})

    def frame(self, observation_id: str, what: str = "raw", max_width: int = 640) -> dict:
        return self._call({"op": "frame", "observation_id": observation_id, "what": what, "max_width": max_width})

    def preview(self, *, with_bytes: bool = True) -> dict:
        """The latest preview frame, or an unavailable record with the reason. Never an old frame after a newer one."""
        return self._call({"op": "preview", "with_bytes": with_bytes})

    def _stream(self, request: dict, idle_timeout_s: Optional[float]) -> Iterator[dict]:
        sock = self._connect()
        sock.settimeout(idle_timeout_s)
        try:
            self._send(sock, request)
            try:
                yield from self._lines(sock)
            except socket.timeout:
                return
        finally:
            sock.close()

    def events(self, since: int = 0, *, idle_timeout_s: Optional[float] = None) -> Iterator[dict]:
        """Lines as they arrive: {"resync": ...} first when there is a gap, then contract events. The generator ends when the
        runtime closes the stream or nothing arrived for idle_timeout_s."""
        return self._stream({"op": "events", "since": since}, idle_timeout_s)

    def telemetry(self, *, idle_timeout_s: Optional[float] = None) -> Iterator[dict]:
        return self._stream({"op": "telemetry"}, idle_timeout_s)

    # -- commanding
    def _refuse_when_read_only(self) -> None:
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version: commands are disabled in this client")

    def submit(self, command: dict) -> dict:
        self._refuse_when_read_only()
        sock = self._connect()
        try:
            lines = self._lines(sock)
            self._hello_on(sock, lines)
            self._refuse_when_read_only()
            sent = False
            try:
                self._send(sock, {"op": "submit", "command": command})
                sent = True
                answer = next(lines, None)
            except OSError:
                answer = None
            if answer is None:
                if sent:
                    raise ResponseLost(command.get("command_id", ""))
                raise RuntimeUnavailable("the command could not be sent")
            return answer
        finally:
            sock.close()

    def stop(self) -> dict:
        self._refuse_when_read_only()
        return self._call({"op": "stop"}, hello=True)

    # -- the mission, which is its own surface and carries its own version
    def mission_state(self) -> dict:
        """The mission view, or a refusal. A READ: it starts nothing and resubmits nothing, which is what makes it the
        thing a browser reload asks for."""
        return self._call({"op": "mission_state"})

    def mission_candidates(self) -> dict:
        """The prepared candidates by key and digest. No bytes and no path: a caller names a key and supplies nothing."""
        return self._call({"op": "mission_candidates"})

    def mission_start(self, targets: Optional[list] = None) -> dict:
        self._refuse_when_read_only()
        request = {"op": "mission_start"}
        if targets is not None:
            request["targets"] = targets
        return self._call(request, hello=True)

    def mission_stop(self) -> dict:
        """Answered while a target is waiting on a capability, not only while one is being carried."""
        self._refuse_when_read_only()
        return self._call({"op": "mission_stop"}, hello=True)

    def mission_activate(self, candidate_key: str) -> dict:
        """Submit ONE prepared candidate by KEY. The bytes are the runtime's; this carries none of them."""
        self._refuse_when_read_only()
        return self._call({"op": "mission_activate", "candidate_key": candidate_key}, hello=True)

    def mission_retry(self, mission_id: Optional[str] = None) -> dict:
        """Retry the attempt the mission holds on. Names at most the mission the caller saw held: the hold on the
        record decides the target, the attempt and the park."""
        self._refuse_when_read_only()
        request = {"op": "mission_retry"}
        if mission_id is not None:
            request["mission_id"] = mission_id
        return self._call(request, hello=True)

    # -- the capability slot check, its own surface: one prepared Plan applied and read back, with no motion
    def capability_check_state(self) -> dict:
        """The check's READ: candidates by key and digest, the cached reading of the robot's resident, the first reason
        an apply would be refused now, and the checks on the record. It submits nothing."""
        return self._call({"op": "capability_check_state"})

    def capability_check_apply(self, candidate_key: str, payload_digest: str, session_uuid: str) -> dict:
        """Apply ONE prepared candidate by KEY, naming the digest and the resident session the operator was shown. The
        bytes are the runtime's; this carries none of them. Never resent: a lost answer is read back from the state."""
        self._refuse_when_read_only()
        return self._call({"op": "capability_check_apply", "candidate_key": candidate_key, "payload_digest": payload_digest,
                           "session_uuid": session_uuid}, hello=True)

    # -- the signature policy check, its own surface: the release verifier run on the robot, with no motion and no slot
    def capability_signature_state(self) -> dict:
        """The check's READ: in flight, the first refusal a run would meet now, the policy and the runs on the record."""
        return self._call({"op": "capability_signature_state"})

    def capability_signature_check(self) -> dict:
        """Run the release verifier over every deployed capsule. It carries nothing: no capsule, key, path or policy.
        Never resent: a lost answer is read back from the state."""
        self._refuse_when_read_only()
        return self._call({"op": "capability_signature_check"}, hello=True)

    def resolve(self, command_id: str, finding: str, note: Optional[str] = None) -> dict:
        """Ask the runtime to reconcile an uncertain command. The finding is the operator's STATEMENT: the runtime checks
        the evidence itself and may refuse the statement. This never retries the command, here or on the other side."""
        self._refuse_when_read_only()
        return self._call({"op": "resolve", "command_id": command_id, "finding": finding, "note": note}, hello=True)

    def new_command(self, command_type: str, args: dict, command_id: Optional[str] = None) -> dict:
        # Random from the FIRST character: the runtime's scan id keeps only the first eight characters of a command id, and a
        # fixed prefix ("cmd-") would leave four random digits in it.
        return {"contract_version": self.contract_version, "command_id": command_id or f"{uuid.uuid4().hex[:20]}", "type": command_type, "args": args,
                "client": {"session_id": self.client_session, "contract_version": self.contract_version}}
