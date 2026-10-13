# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.emit: the controller states facts as contract v1 events, and stating them can never hold up a motion.

WHY A QUEUE AND A THREAD. The controller knows facts at the moment they happen (an action started, a predicate was
evaluated, a frame was consumed) and the operator console wants them as records. A record ends up in a file, and a file
write can block for as long as the disk likes: a full disk, a stalled network mount, a laptop that is busy syncing. The
controller is in the middle of moving an arm when it emits. So emit() only builds the record and puts it on a bounded
in-process queue; a separate daemon thread owns the file. What emit() costs is one JSON encoding and one short lock.

WHAT EMIT() GUARANTEES.
  - It never raises into the caller: every Exception inside is swallowed and counted (emit_errors). KeyboardInterrupt and
    SystemExit are NOT swallowed, on purpose: Ctrl-C is how an operator stops a run, and an emitter that ate it would be a
    safety defect of its own.
  - It never waits for the disk. The one lock it takes is also taken by the writer, but only to swap two queue references
    and to read a few counters, never around I/O. That is the same bound queue.Queue gives (its mutex), and as with any
    Python lock the worst case is one interpreter thread switch.
  - seq is assigned under that lock, together with the enqueue, so seq order is call order and the file is in seq order.
  - The record is serialized inside emit(), so it is a snapshot: a payload dict the caller changes afterwards does not
    change the record. It also means a payload that cannot be JSON (an object, a NaN) is refused at the call that made it.

POLICIES. A full queue means the writer is not keeping up or the disk is not answering; the two classes of event differ in
what is done about it.
  - MISSION events (telemetry=False) are never dropped silently. On a full queue the NEW event is dropped (the older ones
    are the ones that explain how the run got here), evidence becomes incomplete, the gap is counted, and the writer puts
    an evidence.gap record naming the missing seq range into the file as soon as it can write again.
  - TELEMETRY events (telemetry=True) drop OLDEST first, because a newer sample replaces an older one. The drops are
    counted and surfaced as ONE telemetry.dropped record per reporting interval, not one per drop.
  - A WRITER FAILURE (disk full, any OSError) stops nothing: evidence becomes incomplete, the writer keeps what it holds,
    reopens the file and retries with a backoff. It never discards by itself, so loss happens only where it is accounted:
    at the queues (above) or at close(), which counts what never reached the file (unwritten).
  - A caller's own evidence.gap event counts as a gap too: the emitter is the one place that sees every gap of a run, and
    run.finished needs the total (status()).

EVIDENCE IS NOT OUTCOME. The emitter has no opinion about what physically happened. It never reads or edits a payload; an
incomplete record changes nothing about outcome or predicates, and a placed cube stays placed on thin evidence. The caller
builds run.finished from its own outcome plus status()["evidence_complete"] and status()["evidence_gaps"]. That snapshot is
taken BEFORE run.finished itself is written, so close() is the final word: it can still turn incomplete.

SIGNAL SAFETY. emit(), span(), status() and close() must NOT be called from a signal handler. The lock is not re-entrant:
a handler that interrupts emit() in the main thread while it holds the lock, and then calls emit() itself, deadlocks the
process with the arm mid-motion. A handler sets a flag and returns; the main loop sees the flag and emits stop.requested
with origin operator_signal from normal code. This module deliberately offers no helper that could be reached from a handler.

WHAT IT DOES NOT DO. It does not validate records against the contract at run time (the validator is pure Python and would
compete with the control loop for the interpreter) and it does not sanitize text: a payload that breaks the schema, or a
safe_text that names a path, is written as given and is the caller's defect. tests/test_emit.py validates every record
kind this module can produce. demo_contracts is never imported here; CONTRACT_VERSION below mirrors it and a test pins it.

THE FILE. One JSON object per line, ASCII, newline-terminated, appended (O_APPEND) with one write call per line on an
unbuffered file, so a reader that tails the file meets whole lines. fsync happens once, in close(). Without
KC_EVENTS_PATH, make_emitter() returns a NullEmitter: no thread, no file, nothing imported beyond this module.
"""
import collections
import errno
import json
import os
import re
import threading
import time
from typing import Mapping, Optional

CONTRACT_VERSION = "1"  # demo_contracts.CONTRACT_VERSION; mirrored, not imported (see the docstring), pinned by tests/test_emit.py
CLOCK_DOMAIN = "laptop_monotonic"  # time.monotonic_ns() of the machine the controller runs on
EVENTS_PATH_ENV, RUN_ID_ENV, ARTIFACT_DIGEST_ENV = "KC_EVENTS_PATH", "KC_RUN_ID", "KC_ARTIFACT_DIGEST"
# payload_timing_span.span in event.schema.json; mirrored and pinned by a test. An unknown span name is refused HERE because
# span() is the one place where this module, not the caller, builds the payload.
SPAN_NAMES = ("capture", "transfer", "model_init", "inference", "planning", "motion", "settle", "base_measurement", "waiting")

_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")  # common#/$defs/id
_VERSION = re.compile(r"[A-Za-z0-9_.+-]{1,64}")  # common#/$defs/producer.version
_DIGEST = re.compile(r"(sha256:)?[0-9a-f]{12,64}")  # common#/$defs/digest
_BACKOFF_MIN_S, _BACKOFF_MAX_S = 0.05, 1.0
_IDLE_WAKE_S = 0.5  # a safety net only: emit() wakes the writer. It also bounds how late a telemetry.dropped report can be
_TELEMETRY_REPORT_INTERVAL_S = 1.0
_STATUS_KEYS = ("evidence_complete", "evidence_gaps", "dropped_telemetry", "written", "dropped_mission", "unwritten", "emit_errors", "write_errors")
# One encoder for every record. allow_nan=False: NaN and Infinity are not JSON, and a line no consumer can parse is worse
# than a counted refusal at the call that produced it. ensure_ascii keeps the file ASCII whatever a diagnostic line holds.
_encode = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode


def _error_label(error: BaseException) -> str:
    """An exception CLASS and, for an OSError, the errno NAME. Never str(error): an OSError's text carries the path."""
    code = errno.errorcode.get(getattr(error, "errno", None) or 0)
    return f"{type(error).__name__} {code}" if code else type(error).__name__


class _Span:
    """Measures one duration and hands it to the emitter on exit. An exception inside the block still gets its span (how
    long a failed motion took is a fact too) and is never swallowed."""
    __slots__ = ("_emitter", "_name", "_fields", "_t0", "duration_ns")

    def __init__(self, emitter, name, fields):
        self._emitter, self._name, self._fields, self._t0, self.duration_ns = emitter, name, fields, 0, None

    def __enter__(self):
        self._t0 = time.monotonic_ns()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.duration_ns = time.monotonic_ns() - self._t0
        self._emitter._emit_span(self._name, self.duration_ns, self._fields)
        return False


class _NullSpan:
    """The same shape without an emitter: two monotonic reads, so code that reads span.duration_ns works either way."""
    __slots__ = ("_t0", "duration_ns")

    def __init__(self):
        self._t0, self.duration_ns = 0, None

    def __enter__(self):
        self._t0 = time.monotonic_ns()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.duration_ns = time.monotonic_ns() - self._t0
        return False


class NullEmitter:
    """What the controller holds when nobody asked for events: every method exists and none does anything."""
    __slots__ = ()

    def emit(self, kind, payload, *, action_id=None, observation_id=None, reason_code=None, provenance=None, telemetry=False):
        return None

    def span(self, name, **fields):
        return _NullSpan()

    def status(self) -> dict:
        # evidence_complete is False, not True: nothing was recorded, and "complete" would claim a record that does not exist.
        return {key: False if key == "evidence_complete" else 0 for key in _STATUS_KEYS}

    def close(self, timeout_s: float = 2.0) -> dict:
        return self.status()


class EventEmitter:
    """See the module docstring. Construction validates the constant envelope fields and raises ValueError naming the FIELD
    (never the value): it happens at start, before any motion, and an emitter that would write only invalid records must
    not start quietly. After construction nothing here raises into the caller."""

    def __init__(self, path, *, producer: dict, source_session: str, run_id: Optional[str], mission_queue_max: int = 4096, telemetry_queue_max: int = 512):
        if not isinstance(producer, dict) or set(producer) - {"name", "version", "artifact_digest", "contract_version"}:
            raise ValueError("producer must be a dict of name, version, artifact_digest and contract_version")
        producer = {"name": producer.get("name"), "version": producer.get("version"), "artifact_digest": producer.get("artifact_digest"),
                    "contract_version": producer.get("contract_version", CONTRACT_VERSION)}
        for field, value, shape, nullable in (("producer.name", producer["name"], _ID, False), ("producer.version", producer["version"], _VERSION, False),
                                              ("producer.artifact_digest", producer["artifact_digest"], _DIGEST, True),
                                              ("source_session", source_session, _ID, False), ("run_id", run_id, _ID, True)):
            if not (nullable and value is None) and not (isinstance(value, str) and shape.fullmatch(value)):
                raise ValueError(f"{field} does not have the shape the contract requires")
        if producer["contract_version"] != CONTRACT_VERSION:
            raise ValueError(f"producer.contract_version must be {CONTRACT_VERSION!r}")
        for field, value in (("mission_queue_max", mission_queue_max), ("telemetry_queue_max", telemetry_queue_max)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        self._path = os.fspath(path)
        self._mission_max, self._telemetry_max = mission_queue_max, telemetry_queue_max
        # The constant part of every record is encoded once. A line is head + seq + middle + the per-event part: seq is
        # spliced in by the writer because it is only known under the lock, and the encoding must stay outside the lock.
        self._head = '{"schema_version":' + _encode(CONTRACT_VERSION) + ',"seq":'
        self._middle = (',"run_id":' + _encode(run_id) + ',"producer":' + _encode(producer) + ',"source_session":' + _encode(source_session)
                        + ',"clock_domain":' + _encode(CLOCK_DOMAIN) + ",")
        self._wall_cache = (-1, "")
        self._telemetry_report_interval_s = _TELEMETRY_REPORT_INTERVAL_S

        # Everything below the lock is shared between emit() callers and the writer and is touched only under it.
        self._lock = threading.Lock()  # NOT re-entrant: see SIGNAL SAFETY
        # Two deques under this lock, not two queue.Queue objects. The enqueue is the same thing put_nowait is (bounded, never
        # waits), but a Queue cannot drop its oldest entry in the same step that assigns the seq, cannot be handed to the
        # writer in one reference swap (it would be emptied entry by entry while emit() waits), and two of them cannot be
        # drained as ONE seq-ordered stream without a lock around both anyway.
        self._mission, self._telemetry = collections.deque(), collections.deque()  # (seq, encoded per-event part)
        self._next_seq = 0
        self._closed = False
        self._incomplete, self._gaps = False, 0
        self._dropped_mission, self._dropped_telemetry, self._emit_errors = 0, 0, 0
        self._gap_pending = None  # [first_seq, last_seq, count] of mission events dropped since the last drain
        self._emit_errors_pending = 0  # mission events that could not be built since the last drain
        self._telemetry_pending = None  # [first_seq, last_seq, count] of telemetry dropped since the last report
        self._telemetry_reported_at = float("-inf")
        self._close_result = None

        # Writer-thread state. _written and _write_errors are read elsewhere, as plain integers.
        self._file = None
        self._written, self._write_errors = 0, 0
        self._failing, self._episode_errors, self._episode_label, self._episode_pending = False, 0, "", None
        self._deadline = float("inf")  # set by close() BEFORE _closing, so the writer never sees closing without a deadline
        self._wake, self._closing = threading.Event(), threading.Event()
        # A daemon: a disk that never answers must never keep the process alive after the controller has decided to exit.
        self._thread = threading.Thread(target=self._run, name="demo-runtime-event-writer", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ the caller's side
    def emit(self, kind, payload, *, action_id=None, observation_id=None, reason_code=None, provenance=None, telemetry=False):
        """Queue one record. Returns its seq, or None when the record will not be in the file (dropped on a full mission
        queue, refused because it could not be built, or emitted after close()). Never raises, never waits for the disk."""
        try:
            if not isinstance(payload, dict):
                raise TypeError("payload must be a dict")
            part = self._event_part(kind, payload, action_id, observation_id, reason_code, provenance)
            with self._lock:
                if self._closed:
                    self._emit_errors += 1
                    return None
                seq = self._next_seq
                self._next_seq = seq + 1  # a dropped mission event keeps its seq: the hole in the file IS the missing range
                if kind == "evidence.gap":
                    self._incomplete, self._gaps = True, self._gaps + 1
                if telemetry:
                    if len(self._telemetry) >= self._telemetry_max:
                        self._telemetry_pending = self._extended(self._telemetry_pending, self._telemetry.popleft()[0])
                        self._dropped_telemetry += 1
                    self._telemetry.append((seq, part))
                elif len(self._mission) >= self._mission_max:
                    if self._gap_pending is None:  # one gap per run of drops: nothing can leave the queue between two drains
                        self._incomplete, self._gaps = True, self._gaps + 1
                    self._gap_pending = self._extended(self._gap_pending, seq)
                    self._dropped_mission += 1
                    seq = None
                else:
                    self._mission.append((seq, part))
            if not self._wake.is_set():  # the unlocked read saves the Event's own lock on a busy stream; the writer clears BEFORE it drains
                self._wake.set()
            return seq
        except Exception:
            self._note_emit_error(telemetry)
            return None

    def span(self, name, **fields):
        """with emitter.span("motion", action_id=...): a timing.span mission record on exit. fields are emit()'s envelope
        keywords. The object returned by `with ... as span` carries span.duration_ns afterwards."""
        return _Span(self, name, fields)

    def status(self) -> dict:
        """What is known NOW. evidence_complete means "no loss so far"; records still queued are unwritten, not lost."""
        with self._lock:
            allocated, incomplete, gaps = self._next_seq, self._incomplete, self._gaps
            dropped_mission, dropped_telemetry, emit_errors = self._dropped_mission, self._dropped_telemetry, self._emit_errors
        written = self._written
        return {"evidence_complete": not incomplete, "evidence_gaps": gaps, "dropped_telemetry": dropped_telemetry, "written": written,
                "dropped_mission": dropped_mission, "unwritten": max(0, allocated - written - dropped_mission - dropped_telemetry),
                "emit_errors": emit_errors, "write_errors": self._write_errors}

    def close(self, timeout_s: float = 2.0) -> dict:
        """A bounded final flush: waits at most timeout_s for the writer to drain, fsync and close, then reports. A writer
        that is stuck in the disk stays stuck (it is a daemon) and the result says incomplete. Idempotent."""
        try:
            timeout_s = max(0.0, float(timeout_s))
            with self._lock:
                if self._close_result is not None:
                    return dict(self._close_result)
                self._closed = True  # under the lock: every record accepted before this line is seen by the writer's last drain
            self._deadline = time.monotonic() + timeout_s
            self._closing.set()
            self._wake.set()
            self._thread.join(timeout_s)
            result = self.status()
            if self._thread.is_alive() or result["unwritten"]:
                with self._lock:
                    # Truncation at close is a gap that no evidence.gap record can name. While the writer is in a failure
                    # episode it is the SAME gap the episode already counted.
                    self._gaps += 0 if self._failing and self._incomplete else 1
                    self._incomplete = True
                result = self.status()
            with self._lock:
                self._close_result = result
            return dict(result)
        except Exception:
            return {**{key: 0 for key in _STATUS_KEYS}, "evidence_complete": False, "evidence_gaps": 1}

    # ------------------------------------------------------------------ building records
    def _wall_utc(self) -> str:
        ns = time.time_ns()
        second = ns // 1_000_000_000
        cached = self._wall_cache  # one tuple, replaced whole: two threads can at worst both format the same second
        if cached[0] != second:
            cached = self._wall_cache = (second, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(second)))
        return "%s.%06dZ" % (cached[1], ns % 1_000_000_000 // 1000)

    def _event_part(self, kind, payload, action_id, observation_id, reason_code, provenance) -> str:
        return _encode({"action_id": action_id, "observation_id": observation_id, "kind": kind, "t_mono_ns": time.monotonic_ns(), "t_wall_utc": self._wall_utc(),
                        "reason_code": reason_code, "payload": payload, "provenance": provenance})

    def _line(self, seq: int, part: str) -> bytes:
        return (self._head + str(seq) + self._middle + part[1:] + "\n").encode("ascii")

    @staticmethod
    def _extended(pending, seq: int) -> list:
        """[first_seq, last_seq, count] with one more dropped seq. Drops arrive in seq order, so last is simply the newest."""
        return [seq, seq, 1] if pending is None else [pending[0], seq, pending[2] + 1]

    def _note_emit_error(self, telemetry) -> None:
        try:
            with self._lock:
                self._emit_errors += 1
                if not telemetry:  # a mission record that could not be built is missing from the evidence like any other
                    if not self._emit_errors_pending:
                        self._incomplete, self._gaps = True, self._gaps + 1
                    self._emit_errors_pending += 1
            if not self._wake.is_set():
                self._wake.set()
        except Exception:
            pass  # counting is best effort; never raising is not

    def _emit_span(self, name, duration_ns, fields) -> None:
        try:
            if name not in SPAN_NAMES:
                raise ValueError("unknown span name")
            self.emit("timing.span", {"span": name, "duration_ns": duration_ns, "clock_domain": CLOCK_DOMAIN}, **fields)
        except Exception:  # an unknown name, or a keyword emit() does not take: counted, never raised out of a with block
            self._note_emit_error(False)

    # ------------------------------------------------------------------ the writer thread
    def _open_file(self):
        """Unbuffered on purpose: write() is then ONE write(2) of one whole line, a failed call wrote nothing, and a retry
        cannot duplicate bytes that a buffer had already taken. Tests override this to stall or to break the disk."""
        fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            return os.fdopen(fd, "ab", buffering=0)
        except Exception:
            os.close(fd)
            raise

    def _drop_file(self) -> None:
        handle, self._file = self._file, None
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass

    def _drain(self, final: bool) -> list:
        """Take everything queued, as (seq, per-event part) in seq order, plus the reports that are due. The lock covers two
        reference swaps and some integers, nothing else. The reports get their seq here, after every record taken, so the
        file stays in seq order."""
        mission, telemetry, reports = (), (), []
        with self._lock:
            # A queue is taken only when it holds something, and an empty one is never even referenced outside the lock: an
            # emit() could append to it after the lock is gone, and that record would then be written twice.
            if self._mission:
                mission, self._mission = self._mission, collections.deque()
            if self._telemetry:
                telemetry, self._telemetry = self._telemetry, collections.deque()
            if self._gap_pending is not None:
                reports.append(("mission_queue_full", self._gap_pending))
                self._gap_pending = None
            if self._emit_errors_pending:
                reports.append(("event_not_built", self._emit_errors_pending))
                self._emit_errors_pending = 0
            if self._episode_pending is not None and not self._failing:
                reports.append(("event_writer_failed", self._episode_pending))
                self._episode_pending = None
            now = time.monotonic()
            if self._telemetry_pending is not None and (final or now - self._telemetry_reported_at >= self._telemetry_report_interval_s):
                reports.append(("telemetry_dropped", self._telemetry_pending))
                self._telemetry_pending, self._telemetry_reported_at = None, now
            report_seq = self._next_seq
            self._next_seq += len(reports)
        records = sorted(list(mission) + list(telemetry))  # two sorted runs, so close to linear; seq is unique, so the parts are never compared
        for index, (what, facts) in enumerate(reports):
            records.append((report_seq + index, self._report_part(what, facts)))
        return records

    def _report_part(self, what: str, facts) -> str:
        """The emitter's own records. Their text is built from this module's literals, integers and exception class names only."""
        if what == "telemetry_dropped":
            first, last, count = facts
            return self._event_part("telemetry.dropped", {"count": count, "stage": "producer_queue", "first_seq": first, "last_seq": last}, None, None, None, None)
        if what == "mission_queue_full":
            first, last, count = facts
            gap, detail = "record_truncated", f"the producer queue was full: {count} mission event(s) with seq from {first} to {last} were dropped before they reached the record"
        elif what == "event_not_built":
            gap, detail = "other", f"{facts} mission event(s) could not be built into a record and were refused before a seq was assigned"
        else:
            count, label = facts
            gap, detail = "other", f"the event writer failed {count} time(s) ({label}) and recovered; it discards nothing itself, a queue overflow meanwhile is reported separately"
        return self._event_part("evidence.gap", {"gap": gap, "detail": detail, "observation_ids": []}, None, None, what, None)

    def _note_write_failure(self, error: BaseException) -> None:
        self._write_errors += 1
        self._episode_errors += 1
        self._episode_label = _error_label(error)
        if not self._failing:  # one gap per episode, not per retry
            self._failing = True
            with self._lock:
                self._incomplete, self._gaps = True, self._gaps + 1

    def _run(self) -> None:
        try:
            self._write_loop()
        except Exception as error:  # a defect in this module: the emitter degrades to counted drops, the controller never hears of it
            try:
                self._note_write_failure(error)
            except Exception:
                pass
        finally:
            self._drop_file()

    def _write_loop(self) -> None:
        batch = collections.deque()  # records taken from the queues and not yet in the file; only this thread sees it
        # The line of batch[0] and how much of it is in the file: a short write is continued, never restarted. A line is built
        # right before its write, not for the whole batch at once: each write gives the interpreter back to the controller, so
        # the longest the writer ever keeps it is one line, not four thousand.
        data, offset = None, 0
        backoff = _BACKOFF_MIN_S
        while True:
            closing = self._closing.is_set()  # read BEFORE the drain: a drain that follows it has seen every accepted record
            try:
                if self._file is None:
                    self._file = self._open_file()  # here and not in __init__: open() can block too
                if not batch:
                    batch.extend(self._drain(closing))
                while batch:
                    if data is None:
                        data = self._line(*batch[0])
                    while offset < len(data):
                        count = self._file.write(data[offset:] if offset else data)
                        if count is not None and count <= 0:
                            raise OSError(errno.EIO, "the file took no bytes")
                        offset = len(data) if count is None else offset + count
                    self._file.flush()
                    batch.popleft()
                    data, offset = None, 0
                    self._written += 1
                backoff = _BACKOFF_MIN_S
                if self._failing:
                    self._failing, self._episode_pending, self._episode_errors = False, (self._episode_errors, self._episode_label), 0
                    continue  # drain again at once: the recovery report belongs in the file now, not at the next event
            except Exception as error:
                self._note_write_failure(error)
                self._drop_file()  # reopen on the next attempt: a handle that failed may never work again, while the path may
                remaining = self._deadline - time.monotonic()
                if closing and remaining <= 0:
                    return  # close() has already answered "incomplete"; retrying for nobody would only keep the thread alive
                if closing:
                    time.sleep(min(backoff, remaining))
                else:
                    self._closing.wait(backoff)  # close() cuts the wait short
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
                continue
            if closing:
                break
            self._wake.wait(_IDLE_WAKE_S)
            self._wake.clear()  # BEFORE the next drain: an emit() that lands after the clear sets the event again
        try:
            os.fsync(self._file.fileno())  # once, here: durability matters when the run is over, latency matters while it runs
        except Exception as error:
            self._note_write_failure(error)


def make_emitter(environ: Optional[Mapping[str, str]] = None, *, producer_name: str = "demo-controller", producer_version: str = "0"):
    """The emitter a controller should hold. Without KC_EVENTS_PATH it is a NullEmitter, and nothing else happens. With it,
    the run id comes from KC_RUN_ID and the producer's artifact digest from KC_ARTIFACT_DIGEST (both optional, both null
    when unset); the source session is minted here, opaque and random, never derived from the machine. A value with the
    wrong shape raises ValueError naming the variable's field: this runs at start, before any motion."""
    environ = os.environ if environ is None else environ
    path = environ.get(EVENTS_PATH_ENV)
    if not path:
        return NullEmitter()
    producer = {"name": producer_name, "version": producer_version, "artifact_digest": environ.get(ARTIFACT_DIGEST_ENV) or None, "contract_version": CONTRACT_VERSION}
    return EventEmitter(path, producer=producer, source_session="session-" + os.urandom(8).hex(), run_id=environ.get(RUN_ID_ENV) or None)


__all__ = ["ARTIFACT_DIGEST_ENV", "CLOCK_DOMAIN", "CONTRACT_VERSION", "EVENTS_PATH_ENV", "RUN_ID_ENV", "SPAN_NAMES", "EventEmitter", "NullEmitter", "make_emitter"]
