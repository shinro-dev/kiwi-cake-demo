# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.journal: the two durable files of the runtime daemon.

THE COMMAND JOURNAL (Journal, fold_commands). Append-only JSON lines, fsynced on EVERY append. It is what makes a command
idempotent and a crash recoverable: a command is written as 'accepted' and synced BEFORE anything is spawned, as 'spawning'
before the child is created, and again with the child's identity (pid, /proc start time, the unique scan id) once it exists.
After a crash the journal says exactly how far a command got; it never says more than was synced.

WHAT A RECEIPT SAYS IT IS A RECEIPT FOR. command_result() carries the contract's optional payload_digest: the digest of
the canonical command object as this journal recorded it. Idempotency by command_id alone answers a repeat of the SAME
request correctly and answers a DIFFERENT request under a reused id with the first one's outcome, which is the wrong
answer and a silent one. With the digest on the receipt a caller can see that the two are not the same request and say
so, and it can do that after a restart as well, because the recorded command is in the journal and not in anybody's
memory. first_seen_wall_utc is the stamp of the FIRST record of that command and is folded the same way. Both are
optional on the contract: a receipt built from a state that carries neither carries neither, and a reader then knows
exactly what it knew before.

THE RUNTIME EVENT LOG (EventLog). Append-only JSON lines of contract events in ONE ordered stream: the daemon's own events
and the controller's, re-sequenced into the runtime seq. Each line is {"origin": ..., "event": ...}; origin keeps what the
contract envelope has no field for: the controller's OWN seq, its source_session and the byte offset its line ended at in
the controller's file (that offset is how tailing resumes after a restart without repeating a record). Every event is
validated against the contract before it is appended; an event that fails is refused, never written.

Delivery. The log keeps a FINITE ring in memory. A subscriber gets a bounded queue; when it overflows the subscriber is
dropped (append never waits for anybody) and resumes later with since=<seq>. When since is older than the ring the
subscription starts with an explicit resync gap. Nothing here promises gapless delivery; retained_from_seq says what can
still be served.

A partial last line (the process died in the middle of a write) is tolerated in both files: it is skipped when read, and
the next append starts on a fresh line.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

PRODUCER_NAME = "demo-runtime"
RUNTIME_VERSION = "0.1.0"
DEFAULT_RING_SIZE = 4096
DEFAULT_SUBSCRIBER_QUEUE = 512
ACTIVE_STATUSES = ("accepted", "spawning", "running")
# common.schema.json#/$defs/nullable_wall_utc. A stamp this journal wrote always matches it; a stamp read back from a
# file somebody else edited might not, and a receipt that fails its own contract is worse than one that says nothing.
WALL_UTC_SHAPE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?Z$")


def ensure_contracts_importable() -> None:
    """demo_contracts lives next to this package (packages/demo-contracts/python), in the checkout and in a pinned bundle
    alike. The runtime puts that directory on sys.path itself so that `python3 -m demo_runtime.daemon` needs one path entry."""
    try:
        import demo_contracts  # noqa: F401
        return
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[2] / "demo-contracts" / "python"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


ensure_contracts_importable()
import demo_contracts  # noqa: E402

CONTRACT_VERSION = demo_contracts.CONTRACT_VERSION


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _open_append(path: str) -> int:
    """Open for append, private. A file that does not end in a newline holds a torn record: start a fresh line so the next
    record cannot be glued onto it."""
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    size = os.fstat(fd).st_size
    if size and os.pread(fd, 1, size - 1) != b"\n":
        os.write(fd, b"\n")
        os.fsync(fd)
    return fd


def read_json_lines(path: str) -> tuple:
    """(records, damaged). A line that is not a JSON object is counted, never raised: one torn write must not cost the file."""
    records, damaged = [], 0
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return records, damaged
    with handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                damaged += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                damaged += 1
    return records, damaged


class Journal:
    """The command journal. append() returns only after the record is on disk."""

    def __init__(self, path, read_only: bool = False):
        self.path = os.fspath(path)
        self._lock = threading.Lock()
        self._fd = None if read_only else _open_append(self.path)

    def append(self, record: dict) -> None:
        if self._fd is None:
            raise RuntimeError("the journal is read-only in this mode")
        line = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        with self._lock:
            os.write(self._fd, line)
            os.fsync(self._fd)

    def read(self) -> tuple:
        return read_json_lines(self.path)

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None


def fold_commands(records: list) -> dict:
    """The journal folded into one state per command_id, in first-seen order. A later record of a command overrides the
    fields it names.

    TWO RECORDS SPEAK FOR THE OPERATOR, AND ONLY ONE OF THEM UNBLOCKS.
      'attestation'  what the operator declared, kept BESIDE the machine evidence the daemon gathered. It is durable and
                     it is shown, and it never sets resolved by itself: an attestation whose evidence could not be read,
                     or one that was refused, must still be on the record without clearing the block.
      'resolution'   the daemon reconciled the command and accepted the resolution: resolved becomes true. The command
                     stays 'uncertain' (what happened was never learned, only reconciled and acknowledged), and the
                     record carries the classification the machine evidence gave and what cleared the block."""
    table: dict = collections.OrderedDict()
    for record in records:
        kind, command_id = record.get("rec"), record.get("command_id")
        if not isinstance(command_id, str):
            continue
        if kind == "command":
            state = table.setdefault(command_id, {"command_id": command_id, "status": None, "reason_code": None, "run_id": None, "resolved": False})
            # The FIRST record of this command is when this runtime admitted the id, and no later record may move it: the
            # later ones say what became of the command, not when it arrived. It is kept under its own name because
            # t_wall_utc is overwritten by every record that follows.
            stamp = record.get("t_wall_utc")
            if state.get("first_seen_wall_utc") is None and isinstance(stamp, str):
                state["first_seen_wall_utc"] = stamp
            state.update({key: value for key, value in record.items() if key != "rec"})
        elif kind == "attestation" and command_id in table:
            attestation = {key: record.get(key) for key in ("finding", "note", "classification", "clears_block", "refused", "t_wall_utc")}
            table[command_id].setdefault("attestations", []).append(attestation)
        elif kind == "resolution" and command_id in table:
            table[command_id].update({"resolved": True, "finding": record.get("finding"), "resolved_wall_utc": record.get("t_wall_utc"),
                                      "classification": record.get("classification"), "cleared_by": record.get("cleared_by")})
    return table


def journal_counters(records: list) -> dict:
    return {
        "daemon_starts": sum(1 for record in records if record.get("rec") == "daemon_start"),
        "controller_launches": sum(1 for record in records if record.get("rec") == "command" and record.get("status") == "running" and not record.get("adopted")),
    }


def canonical_command(command) -> Optional[str]:
    """The ONE text a command's identity is taken over: its own object, keys sorted, no whitespace, ASCII.

    It is the same text on both sides of the socket because it is computed from the object each side holds, and the
    object the API forwards is the object the journal records. Anything that is not a command object has no canonical
    text and no identity here; None says that, and a caller then compares nothing rather than comparing a guess."""
    if not isinstance(command, dict):
        return None
    try:
        return json.dumps(command, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):  # a value json cannot write is not a command that crossed this socket
        return None


def payload_digest(command) -> Optional[str]:
    """The contract digest of a command object, or None when there is no command to take one over."""
    text = canonical_command(command)
    return None if text is None else "sha256:" + hashlib.sha256(text.encode("ascii")).hexdigest()


def command_result(state: dict) -> dict:
    """The contract command_result of a folded command state.

    The four required fields are always there. The two optional ones are added only when this journal actually holds
    them: payload_digest when the accepted command itself was recorded, first_seen_wall_utc when the fold kept the
    stamp of the first record. A rejected command records no command object, so its receipt carries no digest, and a
    caller must treat that as "this journal cannot say", never as "the payloads agree".

    WHERE first_seen_wall_utc COMES FROM, AND WHERE IT DOES NOT. fold_commands() reads it off the first record of a
    command, so a receipt built from a folded journal carries it: every command a previous daemon admitted, read back
    after a restart. The live command table of a running supervisor is not folded, it is updated in memory as each
    record is written, and the writer does not put its stamp into that update, so a command THIS daemon admitted is
    answered without the field until the journal is folded again. Absent means absent, which the contract already says
    a consumer must read as "not recorded": no caller may fill it in from its own clock. Making it uniform is one
    statement in the supervisor's _record (keep the stamp it is already writing), and that file is not this one's."""
    result = {"command_id": state["command_id"], "status": state["status"], "reason_code": state.get("reason_code"), "run_id": state.get("run_id")}
    digest = payload_digest(state.get("command"))
    if digest is not None:
        result["payload_digest"] = digest
    first_seen = state.get("first_seen_wall_utc")
    if isinstance(first_seen, str) and WALL_UTC_SHAPE.match(first_seen):
        result["first_seen_wall_utc"] = first_seen
    return result


def history_entries(table: dict, data_mode_of: Optional[Callable] = None) -> list:
    """The finished console runs of a folded journal, oldest first. Only fields that are safe for a client.

    data_mode IS THE ONE OPTIONAL FIELD, AND IT IS NEVER GUESSED. A completion record carries no data_mode, so whether a
    run drove hardware, replayed a recording or was synthetic could only be told by opening the run's own records. The
    caller that owns the data directory passes data_mode_of(run_id), which reads it from the manifest that run wrote for
    itself; a run whose manifest is gone, or a journal written before manifests carried one, leaves the field ABSENT,
    which the contract reads as not recorded. It is never filled in from the mode this runtime happens to be in now:
    that would answer a question about a past run with a fact about the present one.

    The contract also allows an optional bundle_digest, and this list does not write it: artifact_digest on the same
    entry already IS the digest of the pinned bundle the run ran from, and one value under two names in one record is a
    way for the two to disagree later."""
    out = []
    for state in table.values():
        if state.get("status") == "finished" and state.get("run_id") and isinstance(state.get("completion"), dict):
            completion = state["completion"]
            entry = {"run_id": state["run_id"], "command_id": state["command_id"], "command_type": completion.get("command_type"),
                     "started_wall_utc": completion.get("started_wall_utc"), "finished_wall_utc": completion.get("finished_wall_utc"),
                     "outcome": completion.get("outcome"), "exit_status": completion.get("exit_status"), "evidence_complete": completion.get("evidence_complete"),
                     "evidence_gaps": completion.get("evidence_gaps"), "artifact_digest": completion.get("artifact_digest"), "adopted": bool(completion.get("adopted"))}
            recorded_mode = data_mode_of(state["run_id"]) if data_mode_of is not None else None
            if isinstance(recorded_mode, str):
                entry["data_mode"] = recorded_mode
            out.append(entry)
    return out


def last_outcome(table: dict, mode: Optional[str] = None, data_mode_of: Optional[Callable] = None) -> Optional[dict]:
    """The snapshot's last_outcome, recovered from the journal: the newest finished run that recorded its result AND was
    recorded in the mode this runtime is in now.

    THE MODE FILTER IS NOT A REFINEMENT. A data directory outlives the mode it was used in: a synthetic run leaves its
    journal entry and its manifest behind, and a live runtime started on the same directory afterwards folds the same
    journal. Without the filter the newest finished run wins whatever it was, so a live console reports a SYNTHETIC
    placement, with its four predicates true, as the outcome of a session in which the robot never moved. That was
    observed on this branch: a live snapshot carried run console-20260921T085717Z-aa8a8231, outcome placed_new, whose
    own manifest says synthetic.

    The mode a run was recorded in comes from the manifest that run wrote for itself, through data_mode_of, exactly as
    history_entries reads it, and it is never assumed from the mode this runtime happens to be in now. A run whose
    manifest is gone, or one written before manifests carried a mode, CANNOT be shown to belong to this mode, so it is
    skipped rather than shown: an unlabelled past placement is the one this must not put on a live screen.

    REPLAY IS EXEMPT, and it has to be. A replay-only runtime commands nothing and has no current outcome of its own:
    showing a recorded run IS its whole purpose, and the mode that run was recorded in is its provenance, not a reason
    to hide it. Filtering there answered a question nobody asked and left the screen empty. The filter is for a runtime
    that can command, where a past run of another mode could be read as this session's result.

    With no mode given the filter does not apply, which keeps every caller that has no mode to offer reading as before.

    THIS IS PRESENTATION ONLY. It decides what a snapshot shows; it does not touch the journal, does not drop a record
    and does not decide recovery. An unresolved command whose provenance is missing is still unresolved, still folded
    and still blocks conflicting work: fold_commands and the reconciliation path never consult this.
    """
    filtering = mode is not None and mode != "replay"
    for state in reversed(list(table.values())):
        completion = state.get("completion")
        if not (state.get("status") == "finished" and state.get("run_id") and isinstance(completion, dict) and isinstance(completion.get("result"), dict)):
            continue
        if filtering:
            recorded = data_mode_of(state["run_id"]) if data_mode_of is not None else None
            if recorded != mode:
                continue
        return {"run_id": state["run_id"], "finished_seq": int(completion.get("finished_seq") or 0),
                "finished_wall_utc": completion.get("finished_wall_utc"), "result": completion["result"]}
    return None


# ---- events -------------------------------------------------------------------------------------------------------------

class InvalidEvent(ValueError):
    """An event failed the contract. errors holds the validator's messages; they can quote values and are never shown to a client."""

    def __init__(self, errors: list):
        super().__init__(f"{len(errors)} contract error(s)")
        self.errors = errors


def make_event(kind: str, payload: dict, *, source_session: str, run_id: Optional[str] = None, reason_code: Optional[str] = None, action_id: Optional[str] = None,
               observation_id: Optional[str] = None, artifact_digest: Optional[str] = None, provenance: Optional[dict] = None) -> dict:
    """One contract event from the runtime itself. seq is assigned by EventLog.append."""
    return {
        "schema_version": CONTRACT_VERSION, "seq": 0, "run_id": run_id, "action_id": action_id, "observation_id": observation_id, "kind": kind,
        "producer": {"name": PRODUCER_NAME, "version": RUNTIME_VERSION, "artifact_digest": artifact_digest, "contract_version": CONTRACT_VERSION},
        "source_session": source_session, "t_mono_ns": time.monotonic_ns(), "clock_domain": "laptop_monotonic", "t_wall_utc": utc_now_text(),
        "reason_code": reason_code, "payload": payload, "provenance": provenance,
    }


class Subscription:
    """One consumer of the live stream. resync is None or the explicit gap; backlog is what the ring still held; queue
    receives everything appended after the subscription was taken. dropped turns True when the queue overflowed."""

    def __init__(self, maxsize: int):
        self.queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self.resync: Optional[dict] = None
        self.backlog: list = []
        self.dropped = False


class EventLog:
    def __init__(self, path, *, ring_size: int = DEFAULT_RING_SIZE, read_only: bool = False, validate: Callable = demo_contracts.validate):
        self.path = os.fspath(path)
        self._validate = validate
        self._lock = threading.Lock()
        self._ring: collections.deque = collections.deque(maxlen=max(1, ring_size))
        self._subscribers: list = []
        self._listeners: list = []
        self._cursor = 0
        self.dropped_subscribers = 0
        self.damaged_lines = 0
        self._resume_offsets: dict = {}
        self._load()
        self._fd = None if read_only else _open_append(self.path)

    def _load(self) -> None:
        records, self.damaged_lines = read_json_lines(self.path)
        for record in records:
            event, origin = record.get("event"), record.get("origin")
            if not isinstance(event, dict) or not isinstance(event.get("seq"), int):
                self.damaged_lines += 1
                continue
            self._cursor = max(self._cursor, event["seq"])
            self._ring.append(event)
            self._note_origin(origin)

    def _note_origin(self, origin) -> None:
        if isinstance(origin, dict) and isinstance(origin.get("run_id"), str) and isinstance(origin.get("end_offset"), int):
            self._resume_offsets[origin["run_id"]] = max(self._resume_offsets.get(origin["run_id"], 0), origin["end_offset"])

    # -- reading
    @property
    def seq_cursor(self) -> int:
        with self._lock:
            return self._cursor

    @property
    def retained_from_seq(self) -> int:
        with self._lock:
            return self._retained()

    def _retained(self) -> int:
        return self._ring[0]["seq"] if self._ring else self._cursor + 1

    def resume_offset(self, run_id: str) -> int:
        """The byte offset in a run's controller file up to which its records are already in this log."""
        with self._lock:
            return self._resume_offsets.get(run_id, 0)

    def read_run(self, run_id: str) -> list:
        """Every logged event of one run, with its origin, read back from the file (the ring is finite, the file is not)."""
        records, _ = read_json_lines(self.path)
        return [record for record in records if isinstance(record.get("event"), dict) and record["event"].get("run_id") == run_id]

    # -- writing
    def add_listener(self, listener: Callable) -> None:
        """listener(event) runs under the log's lock on every append: it must be quick and must never block."""
        self._listeners.append(listener)

    def append(self, event: dict, *, origin: Optional[dict] = None, sync: bool = True) -> dict:
        if self._fd is None:
            raise RuntimeError("the event log is read-only in this mode")
        event = dict(event)
        event["seq"] = 0
        errors = self._validate("event", event)
        if errors:
            raise InvalidEvent(errors)
        with self._lock:
            self._cursor += 1
            event["seq"] = self._cursor
            os.write(self._fd, (json.dumps({"origin": origin, "event": event}, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii"))
            if sync:
                os.fsync(self._fd)
            self._ring.append(event)
            self._note_origin(origin)
            for listener in self._listeners:
                listener(event)
            for subscriber in list(self._subscribers):
                try:
                    subscriber.queue.put_nowait(event)
                except queue.Full:  # a slow reader is dropped; it never holds the writer
                    subscriber.dropped = True
                    self._subscribers.remove(subscriber)
                    self.dropped_subscribers += 1
        return event

    def sync(self) -> None:
        """Called from the tailer thread after a batch, while another thread may be closing the log. The descriptor is
        taken UNDER the lock: reading self._fd and then fsyncing it left a window in which close() had already closed the
        number, and the fsync raised EBADF into the tailer thread (measured, not reasoned) or reached a descriptor that
        had since been handed to another file."""
        with self._lock:
            descriptor = self._fd
            if descriptor is not None:
                os.fsync(descriptor)

    # -- streaming
    def subscribe(self, since: int, *, maxsize: int = DEFAULT_SUBSCRIBER_QUEUE) -> Subscription:
        subscription = Subscription(maxsize)
        with self._lock:
            retained = self._retained()
            if since > self._cursor:  # a cursor from another log: say so instead of waiting forever for a seq that will not come
                subscription.resync = {"retained_from_seq": retained, "missed": None, "reason": "cursor_ahead"}
                since = retained - 1
            elif since + 1 < retained:
                subscription.resync = {"retained_from_seq": retained, "missed": [since + 1, retained - 1]}
            subscription.backlog = [event for event in self._ring if event["seq"] > since]
            self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
