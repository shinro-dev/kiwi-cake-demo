#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_emit.py: the event emitter never holds up its caller. Every record it writes is a valid
# contract v1 event in seq order; a full mission queue, a telemetry overflow, a disk that refuses and a disk that stops
# answering are each ACCOUNTED (evidence incomplete, a gap record, a counter) and never felt by emit(); close() is bounded;
# the NullEmitter is nothing at all; and the emitter never touches what a payload says about the physical outcome.
# Tempdir only. The "disk" is stalled or broken through EventEmitter._open_file, the one seam the module offers for it.
# Run directly: python3 packages/demo-runtime/tests/test_emit.py
import copy
import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
CONTRACTS_PYTHON = REPO_ROOT / "packages" / "demo-contracts" / "python"
FIXTURES = REPO_ROOT / "packages" / "demo-contracts" / "fixtures" / "event"
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(CONTRACTS_PYTHON))

import demo_contracts  # noqa: E402
from demo_runtime import emit as emit_module  # noqa: E402
from demo_runtime.emit import EventEmitter, NullEmitter, make_emitter  # noqa: E402

PRODUCER = {"name": "synthetic-controller", "version": "0.0.0", "artifact_digest": None, "contract_version": "1"}
SESSION, RUN_ID = "synthetic-session-0001", "synthetic-run-0001"
TRANSITION = {"machine": "cycle", "from_state": None, "to_state": "observe"}
SPAN = {"span": "waiting", "duration_ns": 1, "clock_domain": "laptop_monotonic"}
# What "emit() was not blocked" means in numbers. A blocked emit() waits for the stalled writer, that is for ever; an emit()
# that is merely unlucky meets a thread switch or a garbage collection. The bound sits far above the second and far below the first.
EMIT_BOUND_S = 0.05


def report(text: str) -> None:
    sys.stderr.write(f"test_emit: {text}\n")


class GatedFile:
    """A real file behind a gate. While the gate is shut, write() does what a disk that stopped answering does: it does not return."""

    def __init__(self, real, gate, entered):
        self.real, self.gate, self.entered = real, gate, entered

    def write(self, data):
        self.entered.set()
        self.gate.wait()
        return self.real.write(data)

    def flush(self):
        self.real.flush()

    def fileno(self):
        return self.real.fileno()

    def close(self):
        self.real.close()


class StalledEmitter(EventEmitter):
    """The writer stalls inside write() until the test opens the gate. The events are set BEFORE the thread exists."""

    def __init__(self, *args, **kwargs):
        self.gate, self.entered = threading.Event(), threading.Event()
        super().__init__(*args, **kwargs)

    def _open_file(self):
        return GatedFile(super()._open_file(), self.gate, self.entered)

    def stall_with(self, test, kind=None, payload=None) -> int:
        """Shut the gate, emit one record and wait until the writer is inside write() with it: from here on nothing leaves the queues."""
        self.entered.clear()
        self.gate.clear()
        seq = self.emit(kind or "state.transition", payload or TRANSITION)
        test.assertTrue(self.entered.wait(5), "the writer never reached write()")
        return seq


class BrokenFile:
    """A disk that is full, for good."""

    def write(self, data):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def flush(self):
        pass

    def fileno(self):
        raise OSError(errno.EBADF, os.strerror(errno.EBADF))

    def close(self):
        pass


class BrokenDiskEmitter(EventEmitter):
    def _open_file(self):
        return BrokenFile()


class ScriptedFile:
    """A real file whose next write() calls follow a script shared across reopens: "short" takes 10 bytes, "fail" raises ENOSPC."""

    def __init__(self, real, script, calls):
        self.real, self.script, self.calls = real, script, calls

    def write(self, data):
        step = self.script.pop(0) if self.script else "ok"
        if step == "fail":
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        self.calls.append(bytes(data))
        return self.real.write(bytes(data)[:10]) if step == "short" else self.real.write(data)

    def flush(self):
        self.real.flush()

    def fileno(self):
        return self.real.fileno()

    def close(self):
        self.real.close()


class ScriptedEmitter(EventEmitter):
    script = ()

    def __init__(self, *args, **kwargs):
        self.steps, self.calls, self.opened = list(self.script), [], 0
        super().__init__(*args, **kwargs)

    def _open_file(self):
        self.opened += 1
        return ScriptedFile(super()._open_file(), self.steps, self.calls)


class FlakyEmitter(ScriptedEmitter):
    script = ("short", "fail", "fail")  # ten bytes of the first line, then two refusals, then a disk that works again


class EmitterCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.directory = Path(holder.name)
        self.path = self.directory / "events.jsonl"

    def make(self, cls=EventEmitter, path=None, run_id=RUN_ID, **kwargs):
        emitter = cls(path or self.path, producer=PRODUCER, source_session=SESSION, run_id=run_id, **kwargs)
        self.addCleanup(self.finish, emitter)  # runs before the tempdir cleanup, and leaves no writer thread for the next test to count
        return emitter

    @staticmethod
    def finish(emitter):
        if hasattr(emitter, "gate"):
            emitter.gate.set()
        emitter.close(2.0)
        emitter._thread.join(5)

    def wait_for(self, condition, what: str, timeout_s: float = 5.0):
        deadline = time.monotonic() + timeout_s
        while not condition():
            self.assertLess(time.monotonic(), deadline, f"timed out waiting for {what}")
            time.sleep(0.005)

    def records(self, path=None) -> list:
        """Every line of the file, parsed, and EVERY one validated against the contract."""
        raw = Path(path or self.path).read_bytes()
        self.assertTrue(raw.endswith(b"\n"), "the file does not end with a newline")
        records = []
        for line in raw.split(b"\n")[:-1]:
            record = json.loads(line)
            self.assertEqual(demo_contracts.validate("event", record), [], line[:240])
            records.append(record)
        return records


class ContractRecords(EmitterCase):
    def test_every_kind_is_written_as_a_valid_event_in_gapless_seq_order(self):
        fixtures = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(FIXTURES.glob("*.json"))]
        self.assertEqual({fixture["kind"] for fixture in fixtures}, set(demo_contracts.load_schema("event#/$defs/kind")["enum"]), "the fixtures no longer cover every kind")
        emitter = self.make()
        before = time.monotonic_ns()
        for index, fixture in enumerate(fixtures):
            seq = emitter.emit(fixture["kind"], fixture["payload"], action_id=fixture["action_id"], observation_id=fixture["observation_id"],
                               reason_code=fixture["reason_code"], provenance=fixture["provenance"])
            self.assertEqual(seq, index)
        after = time.monotonic_ns()
        result = emitter.close(5.0)
        records = self.records()
        self.assertEqual([record["seq"] for record in records], list(range(len(fixtures))))
        stamps = [record["t_mono_ns"] for record in records]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(before <= stamps[0] and stamps[-1] <= after, "t_mono_ns is not time.monotonic_ns() of this process")
        for record, fixture in zip(records, fixtures):
            for key in ("kind", "payload", "action_id", "observation_id", "reason_code", "provenance"):
                self.assertEqual(record[key], fixture[key], key)
            self.assertEqual((record["schema_version"], record["run_id"], record["producer"], record["source_session"], record["clock_domain"]),
                             ("1", RUN_ID, PRODUCER, SESSION, "laptop_monotonic"))
            self.assertRegex(record["t_wall_utc"], r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")
        # The fixtures contain ONE evidence.gap, and a caller's gap counts like the emitter's own: run.finished needs the total.
        self.assertEqual(result, {"evidence_complete": False, "evidence_gaps": 1, "dropped_telemetry": 0, "written": len(fixtures),
                                  "dropped_mission": 0, "unwritten": 0, "emit_errors": 0, "write_errors": 0})

    def test_a_run_without_a_run_id_writes_an_explicit_null(self):
        emitter = self.make(run_id=None)
        emitter.emit("state.transition", TRANSITION)
        self.assertEqual(emitter.close(5.0)["evidence_complete"], True)
        self.assertIsNone(self.records()[0]["run_id"])

    def test_the_mirrored_constants_are_the_contract_s(self):
        self.assertEqual(emit_module.CONTRACT_VERSION, demo_contracts.CONTRACT_VERSION)
        self.assertEqual(list(emit_module.SPAN_NAMES), demo_contracts.load_schema("event#/$defs/payload_timing_span")["properties"]["span"]["enum"])
        self.assertIn(emit_module.CLOCK_DOMAIN, demo_contracts.load_schema("common#/$defs/clock_domain")["enum"])

    def test_the_record_is_a_snapshot_of_the_payload_at_the_call(self):
        emitter = self.make(StalledEmitter)
        emitter.stall_with(self)
        payload = copy.deepcopy(TRANSITION)
        emitter.emit("state.transition", payload)
        payload["to_state"] = "changed-after-emit"  # the record is still in the queue: a reference would pick this up
        emitter.gate.set()
        emitter.close(5.0)
        self.assertEqual(self.records()[1]["payload"], TRANSITION)

    def test_seq_order_is_file_order_with_several_producer_threads(self):
        # Both queues are in play (odd threads emit telemetry), so the file order also proves the writer merges them by seq. The
        # telemetry queue is sized for the burst: four busy threads leave the writer hardly a turn, and drops are another test's subject.
        emitter = self.make(telemetry_queue_max=2048)
        returned = {}

        def produce(name):
            returned[name] = [emitter.emit("timing.span", SPAN, telemetry=name % 2 == 1) for _ in range(400)]

        threads = [threading.Thread(target=produce, args=(name,)) for name in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        result = emitter.close(10.0)
        self.assertEqual((result["evidence_complete"], result["written"], result["dropped_telemetry"]), (True, 1600, 0))
        self.assertEqual([record["seq"] for record in self.records()], list(range(1600)))
        for seqs in returned.values():
            self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(sorted(seq for seqs in returned.values() for seq in seqs), list(range(1600)))


class HookedLock:
    """The emitter's lock, plus a call right AFTER each release: the widest possible version of "another thread got in here"."""

    def __init__(self, real, after_release):
        self.real, self.after_release = real, after_release

    def __enter__(self):
        return self.real.__enter__()

    def __exit__(self, *exc):
        self.real.__exit__(*exc)
        self.after_release()


class DrainRace(EmitterCase):
    def test_a_record_that_arrives_right_after_the_drain_released_the_lock_is_written_once(self):
        # The drain found the telemetry queue empty and let go of the lock; a record lands in that queue before the drain has
        # built its batch. A drain that still held a reference to the live queue would write the record now AND at the next drain.
        emitter = self.make()
        state = {"budget": 3, "inside": False}

        def after_release():
            if threading.current_thread() is emitter._thread and state["budget"] and not state["inside"]:
                state["inside"], state["budget"] = True, state["budget"] - 1
                emitter.emit("timing.span", SPAN, telemetry=True)
                state["inside"] = False

        emitter._lock = HookedLock(emitter._lock, after_release)
        emitter.emit("state.transition", TRANSITION)
        self.wait_for(lambda: state["budget"] == 0 and emitter.status()["unwritten"] == 0, "the three late records")
        result = emitter.close(5.0)
        self.assertEqual([record["seq"] for record in self.records()], [0, 1, 2, 3])
        self.assertEqual((result["evidence_complete"], result["written"]), (True, 4))


class TheFile(EmitterCase):
    def test_one_write_call_per_line_appended_to_what_is_already_there(self):
        self.path.write_bytes(b'{"written":"by an earlier process"}\n')
        emitter = self.make(ScriptedEmitter)
        for _ in range(25):
            emitter.emit("state.transition", TRANSITION)
        self.assertEqual(emitter.close(5.0)["written"], 25)
        self.assertEqual(len(emitter.calls), 25)
        for data in emitter.calls:
            self.assertTrue(data.endswith(b"\n") and data.count(b"\n") == 1, data[:80])
        lines = self.path.read_bytes().split(b"\n")
        self.assertEqual(lines[0], b'{"written":"by an earlier process"}')  # O_APPEND, never truncated
        self.assertEqual(b"\n".join(lines[1:]), b"".join(emitter.calls))
        self.assertEqual(emitter.opened, 1)

    def test_a_new_file_is_readable_by_its_owner_only(self):
        emitter = self.make()
        emitter.emit("state.transition", TRANSITION)
        emitter.close(5.0)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode) & 0o077, 0)


class MissionQueueFull(EmitterCase):
    def test_a_full_queue_costs_the_caller_nothing_and_the_record_names_the_missing_range(self):
        emitter = self.make(StalledEmitter, mission_queue_max=4)
        self.assertEqual(emitter.stall_with(self), 0)  # the writer holds seq 0 inside write(); the queue is empty and nothing will leave it
        results, durations = [], []
        for _ in range(7):
            started = time.perf_counter()
            results.append(emitter.emit("state.transition", TRANSITION))
            durations.append(time.perf_counter() - started)
        self.assertEqual(results, [1, 2, 3, 4, None, None, None])
        self.assertLess(max(durations), EMIT_BOUND_S)
        report(f"emit() against a stalled writer and a full mission queue: worst of 7 = {max(durations) * 1e6:.1f} us")
        status = emitter.status()
        self.assertEqual((status["evidence_complete"], status["evidence_gaps"], status["dropped_mission"], status["written"]), (False, 1, 3, 0))

        emitter.gate.set()
        result = emitter.close(5.0)
        records = self.records()
        self.assertEqual([record["seq"] for record in records], [0, 1, 2, 3, 4, 8])  # 5, 6 and 7 are the hole; the gap record took the next seq
        gap = records[-1]
        self.assertEqual((gap["kind"], gap["reason_code"], gap["payload"]["gap"]), ("evidence.gap", "mission_queue_full", "record_truncated"))
        self.assertIn("3 mission event(s) with seq from 5 to 7", gap["payload"]["detail"])
        self.assertEqual(result, {"evidence_complete": False, "evidence_gaps": 1, "dropped_telemetry": 0, "written": 6, "dropped_mission": 3,
                                  "unwritten": 0, "emit_errors": 0, "write_errors": 0})

    def test_two_separate_overflows_are_two_gaps(self):
        emitter = self.make(StalledEmitter, mission_queue_max=1)
        for expected_written in (3, 6):
            emitter.stall_with(self)
            self.assertIsNotNone(emitter.emit("state.transition", TRANSITION))
            self.assertIsNone(emitter.emit("state.transition", TRANSITION))
            emitter.gate.set()
            self.wait_for(lambda: emitter.status()["written"] == expected_written, f"{expected_written} written records")
        result = emitter.close(5.0)
        self.assertEqual((result["evidence_complete"], result["evidence_gaps"], result["dropped_mission"]), (False, 2, 2))
        self.assertEqual([record["seq"] for record in self.records() if record["kind"] == "evidence.gap"], [3, 7])


class TelemetryOverflow(EmitterCase):
    def setUp(self):
        super().setUp()
        self.measured = json.loads((FIXTURES / "measured.state.json").read_text(encoding="utf-8"))["payload"]

    def test_the_oldest_telemetry_goes_first_and_the_loss_is_reported_without_touching_the_evidence(self):
        emitter = self.make(StalledEmitter, telemetry_queue_max=8)
        emitter.stall_with(self)
        durations = []
        for seq in range(1, 21):
            started = time.perf_counter()
            self.assertEqual(emitter.emit("measured.state", self.measured, telemetry=True), seq)
            durations.append(time.perf_counter() - started)
        self.assertLess(max(durations), EMIT_BOUND_S)
        emitter.gate.set()
        result = emitter.close(5.0)
        records = self.records()
        self.assertEqual([record["seq"] for record in records], [0] + list(range(13, 21)) + [21])  # 1..12 were the oldest
        self.assertEqual((records[-1]["kind"], records[-1]["payload"]), ("telemetry.dropped", {"count": 12, "stage": "producer_queue", "first_seq": 1, "last_seq": 12}))
        # Telemetry is droppable by policy: the drops are counted and reported, and the mission evidence is still complete.
        self.assertEqual((result["evidence_complete"], result["evidence_gaps"], result["dropped_telemetry"], result["dropped_mission"]), (True, 0, 12, 0))

    def test_drops_inside_one_reporting_interval_become_one_record_not_many(self):
        emitter = self.make(StalledEmitter, telemetry_queue_max=8)
        emitter._telemetry_report_interval_s = 3600.0  # the first report is immediate; a second one inside the hour waits for close()
        for written in (10, 19):  # 1 mission + 8 kept + the report; then 1 mission + 8 kept and NO second report yet
            emitter.stall_with(self)
            for _ in range(20):
                emitter.emit("measured.state", self.measured, telemetry=True)
            emitter.gate.set()
            self.wait_for(lambda: emitter.status()["written"] == written, f"{written} written records")
        self.assertEqual([record["payload"]["count"] for record in self.records() if record["kind"] == "telemetry.dropped"], [12])
        result = emitter.close(5.0)
        dropped = [record["payload"] for record in self.records() if record["kind"] == "telemetry.dropped"]
        self.assertEqual([(payload["count"], payload["first_seq"], payload["last_seq"]) for payload in dropped], [(12, 1, 12), (12, 23, 34)])
        self.assertEqual((result["dropped_telemetry"], result["evidence_complete"]), (24, True))


class WriterFailure(EmitterCase):
    def test_a_disk_that_refuses_every_write_is_never_felt_by_emit_and_close_says_incomplete(self):
        emitter = self.make(BrokenDiskEmitter)
        durations = []
        for index in range(100):
            started = time.perf_counter()
            self.assertEqual(emitter.emit("timing.span", SPAN, telemetry=index % 2 == 1), index)
            durations.append(time.perf_counter() - started)
        self.assertLess(max(durations), EMIT_BOUND_S)
        self.wait_for(lambda: emitter.status()["write_errors"] >= 2, "a retry")  # it retries, with a backoff, instead of giving up or spinning
        self.assertFalse(emitter.status()["evidence_complete"])
        started = time.monotonic()
        result = emitter.close(0.4)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.5)
        self.assertEqual((result["evidence_complete"], result["evidence_gaps"], result["written"], result["unwritten"]), (False, 1, 0, 100))
        self.assertGreaterEqual(result["write_errors"], 2)
        emitter._thread.join(5)
        self.assertFalse(emitter._thread.is_alive(), "the writer kept retrying after close() had answered")
        self.assertFalse(self.path.exists())

    def test_a_path_that_cannot_be_opened_is_the_same_failure(self):
        emitter = self.make(path=self.directory / "no-such-directory" / "events.jsonl")
        self.assertEqual(emitter.emit("state.transition", TRANSITION), 0)
        result = emitter.close(0.3)
        self.assertEqual((result["evidence_complete"], result["written"], result["unwritten"]), (False, 0, 1))
        self.assertFalse((self.directory / "no-such-directory").exists())

    def test_a_disk_that_recovers_loses_nothing_tears_nothing_and_the_episode_is_on_record(self):
        emitter = self.make(FlakyEmitter)
        for index in range(5):
            self.assertEqual(emitter.emit("state.transition", TRANSITION), index)
        self.wait_for(lambda: emitter.status()["written"] == 6, "5 records and the episode's gap record")
        result = emitter.close(5.0)
        records = self.records()  # parses every line: a restarted or duplicated partial write would not be JSON
        self.assertEqual([record["seq"] for record in records], list(range(6)))
        gaps = [record for record in records if record["kind"] == "evidence.gap"]
        self.assertEqual([(gap["reason_code"], gap["payload"]["gap"]) for gap in gaps], [("event_writer_failed", "other")])
        self.assertIn("failed 2 time(s) (OSError ENOSPC)", gaps[0]["payload"]["detail"])
        self.assertEqual(emitter.opened, 3)  # reopened after each refusal
        # A writer failure marks the evidence incomplete even though this one lost nothing: what a failed write left behind is not knowable.
        self.assertEqual(result, {"evidence_complete": False, "evidence_gaps": 1, "dropped_telemetry": 0, "written": 6, "dropped_mission": 0,
                                  "unwritten": 0, "emit_errors": 0, "write_errors": 2})


class Close(EmitterCase):
    def test_close_returns_within_its_timeout_while_the_writer_is_stuck_in_the_disk(self):
        emitter = self.make(StalledEmitter)
        emitter.stall_with(self)
        for _ in range(10):
            emitter.emit("state.transition", TRANSITION)
        started = time.monotonic()
        result = emitter.close(0.3)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.5)
        report(f"close(0.3) with a stalled writer returned after {elapsed:.3f} s")
        self.assertEqual((result["evidence_complete"], result["evidence_gaps"], result["written"], result["unwritten"]), (False, 1, 0, 11))
        self.assertTrue(emitter._thread.daemon, "a writer stuck in the disk would keep the process alive")
        self.assertTrue(emitter._thread.is_alive())
        started = time.monotonic()
        self.assertEqual(emitter.close(30.0), result)  # idempotent: the first answer stands, and nobody waits again
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertIsNone(emitter.emit("state.transition", TRANSITION))  # after close(): refused and counted, never raised
        self.assertEqual(emitter.status()["emit_errors"], 1)

    def test_a_process_exits_although_its_writer_is_stuck_for_ever(self):
        code = "\n".join((
            "import sys, threading",
            f"sys.path.insert(0, {str(PACKAGE_DIR)!r})",
            "from demo_runtime.emit import EventEmitter",
            "entered, never = threading.Event(), threading.Event()",
            "class Stuck:",
            "    def write(self, data):",
            "        entered.set()",
            "        never.wait()",
            "class Emitter(EventEmitter):",
            "    def _open_file(self):",
            "        return Stuck()",
            f"emitter = Emitter({str(self.path)!r}, producer={{'name': 'synthetic', 'version': '0'}}, source_session='synthetic-session', run_id=None)",
            "emitter.emit('state.transition', {'machine': 'cycle', 'from_state': None, 'to_state': 'observe'})",
            "assert entered.wait(10)",
            "print('leaving with the writer stuck')",
        ))
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "leaving with the writer stuck"), done.stderr)

    def test_a_clean_close_is_complete_and_the_writer_is_gone(self):
        emitter = self.make()
        for _ in range(3):
            emitter.emit("state.transition", TRANSITION)
        result = emitter.close()
        self.assertEqual((result["evidence_complete"], result["evidence_gaps"], result["written"], result["unwritten"]), (True, 0, 3, 0))
        self.assertFalse(emitter._thread.is_alive())


class EmitNeverRaises(EmitterCase):
    def test_what_cannot_be_a_record_is_refused_counted_and_a_gap_when_it_was_a_mission_event(self):
        emitter = self.make(StalledEmitter)
        emitter.stall_with(self)  # so that the three refusals below meet ONE drain and become one gap record, whatever the scheduler does
        self.assertIsNone(emitter.emit("diagnostic.text", {"text": object()}, telemetry=True))  # telemetry: counted only
        self.assertEqual((emitter.status()["emit_errors"], emitter.status()["evidence_complete"]), (1, True))
        self.assertIsNone(emitter.emit("state.transition", {"value": float("nan")}))  # NaN is not JSON
        self.assertIsNone(emitter.emit("state.transition", ["not", "a", "dict"]))
        self.assertIsNone(emitter.emit("state.transition", {"value": object()}))
        self.assertEqual(emitter.emit("state.transition", TRANSITION), 1)  # a refused record took no seq
        emitter.gate.set()
        result = emitter.close(5.0)
        records = self.records()
        self.assertEqual([(record["seq"], record["kind"], record["reason_code"]) for record in records],
                         [(0, "state.transition", None), (1, "state.transition", None), (2, "evidence.gap", "event_not_built")])
        self.assertIn("3 mission event(s)", records[2]["payload"]["detail"])
        self.assertEqual((result["evidence_complete"], result["evidence_gaps"], result["emit_errors"], result["unwritten"]), (False, 1, 4, 0))

    def test_ctrl_c_is_not_swallowed_and_does_not_leave_the_lock_held(self):
        emitter = self.make()
        with mock.patch.object(emitter, "_event_part", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):  # the operator's stop must reach the controller
                emitter.emit("state.transition", TRANSITION)
        self.assertEqual(emitter.emit("state.transition", TRANSITION), 0)


class Spans(EmitterCase):
    def test_a_span_is_a_mission_record_with_the_measured_duration(self):
        emitter = self.make()
        with emitter.span("motion", action_id="synthetic-action-0001") as span:
            time.sleep(0.02)
        emitter.close(5.0)
        (record,) = self.records()
        self.assertEqual((record["kind"], record["action_id"]), ("timing.span", "synthetic-action-0001"))
        self.assertEqual(record["payload"], {"span": "motion", "duration_ns": span.duration_ns, "clock_domain": "laptop_monotonic"})
        self.assertGreaterEqual(span.duration_ns, 20_000_000)

    def test_every_span_name_of_the_contract_is_accepted(self):
        emitter = self.make()
        for name in emit_module.SPAN_NAMES:
            with emitter.span(name):
                pass
        self.assertEqual(emitter.close(5.0)["evidence_complete"], True)
        self.assertEqual([record["payload"]["span"] for record in self.records()], list(emit_module.SPAN_NAMES))

    def test_an_exception_inside_the_block_still_gets_its_span_and_is_not_swallowed(self):
        emitter = self.make()
        with self.assertRaises(ZeroDivisionError):
            with emitter.span("planning"):
                1 / 0
        emitter.close(5.0)
        self.assertEqual([record["payload"]["span"] for record in self.records()], ["planning"])

    def test_a_span_the_contract_does_not_know_never_raises_and_never_reaches_the_file(self):
        emitter = self.make()
        with emitter.span("not-a-span"):
            pass
        with emitter.span("motion", not_an_envelope_field=1):
            pass
        result = emitter.close(5.0)
        self.assertEqual((result["emit_errors"], result["evidence_complete"]), (2, False))
        records = self.records()  # one gap record or two, depending on whether the writer drained between the two refusals
        self.assertEqual({(record["kind"], record["reason_code"]) for record in records}, {("evidence.gap", "event_not_built")})
        self.assertEqual(result["evidence_gaps"], len(records))


class EvidenceIsNotOutcome(EmitterCase):
    PREDICATES = {"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True}

    def finished(self, outcome, status):
        return {"outcome": outcome, "exit_status": 0, "predicates": dict(self.PREDICATES), "automatic_retries": 0, "task_control_interventions": 0,
                "evidence_complete": status["evidence_complete"], "evidence_gaps": status["evidence_gaps"]}

    def test_a_placed_cube_stays_placed_on_incomplete_evidence(self):
        emitter = self.make(StalledEmitter, mission_queue_max=1)
        emitter.stall_with(self)
        emitter.emit("state.transition", TRANSITION)
        self.assertIsNone(emitter.emit("state.transition", TRANSITION))  # the evidence is now incomplete
        emitter.gate.set()
        self.wait_for(lambda: emitter.status()["written"] == 3, "the two records and the gap record")
        status = emitter.status()
        self.assertEqual((status["evidence_complete"], status["evidence_gaps"]), (False, 1))
        self.assertFalse({"outcome", "predicates", "exit_status"} & set(status), "the emitter has no opinion on the physical outcome")
        payload = self.finished("placed_new", status)
        given = copy.deepcopy(payload)
        self.assertIsNotNone(emitter.emit("run.finished", payload))
        self.assertEqual(payload, given)  # the emitter edits nothing
        emitter.close(5.0)
        self.assertEqual(self.records()[-1]["payload"], given)  # and the record says exactly what the controller said: placed, on thin evidence

    def test_a_failed_run_stays_failed_on_complete_evidence(self):
        emitter = self.make()
        payload = self.finished("failed", emitter.status())
        payload["predicates"] = {name: False for name in self.PREDICATES}
        self.assertEqual((payload["evidence_complete"], payload["evidence_gaps"]), (True, 0))
        emitter.emit("run.finished", payload)
        self.assertEqual(emitter.close(5.0)["evidence_complete"], True)
        self.assertEqual(self.records()[-1]["payload"], payload)


class TheNullEmitter(EmitterCase):
    def test_no_thread_no_file_and_the_same_methods(self):
        threads, count = set(threading.enumerate()), threading.active_count()
        for environ in ({}, {emit_module.EVENTS_PATH_ENV: ""}, {emit_module.RUN_ID_ENV: RUN_ID}):
            emitter = make_emitter(environ)
            self.assertIs(type(emitter), NullEmitter)
            self.assertIsNone(emitter.emit("state.transition", TRANSITION, action_id=None, observation_id=None, reason_code=None, provenance=None, telemetry=False))
            with emitter.span("motion", action_id="synthetic-action-0001") as span:
                pass
            self.assertGreaterEqual(span.duration_ns, 0)
            # Not "complete": nothing was recorded, and True would claim a record that does not exist.
            self.assertEqual(emitter.close(), {"evidence_complete": False, "evidence_gaps": 0, "dropped_telemetry": 0, "written": 0, "dropped_mission": 0,
                                               "unwritten": 0, "emit_errors": 0, "write_errors": 0})
            self.assertEqual(emitter.status(), emitter.close(timeout_s=0.0))
        self.assertEqual(threading.active_count(), count)
        self.assertEqual(set(threading.enumerate()) - threads, set())
        self.assertEqual(list(self.directory.iterdir()), [])
        public = {name for name in dir(EventEmitter) if not name.startswith("_")}
        self.assertEqual(public, {"emit", "span", "status", "close"})
        self.assertEqual({name for name in dir(NullEmitter) if not name.startswith("_")}, public)

    def test_a_null_span_costs_two_monotonic_reads_and_nothing_else(self):
        emitter = NullEmitter()
        forbidden = mock.Mock(side_effect=AssertionError("a null span read the wall clock"))
        with mock.patch.object(emit_module.time, "monotonic_ns", side_effect=[1000, 4000]) as monotonic, mock.patch.object(emit_module.time, "time_ns", forbidden):
            with emitter.span("motion") as span:
                pass
        self.assertEqual((monotonic.call_count, span.duration_ns), (2, 3000))

    def test_the_null_path_imports_no_contract_package_and_starts_nothing(self):
        code = "\n".join((
            "import sys, threading",
            f"sys.path.insert(0, {str(PACKAGE_DIR)!r})",
            "from demo_runtime.emit import NullEmitter, make_emitter",
            "emitter = make_emitter({})",
            "assert type(emitter) is NullEmitter",
            "emitter.emit('state.transition', {})",
            "with emitter.span('motion'):",
            "    pass",
            "emitter.close()",
            "assert 'demo_contracts' not in sys.modules, 'demo_contracts was imported'",
            "assert threading.active_count() == 1, threading.enumerate()",
            "print('nothing')",
        ))
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=self.directory)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "nothing"), done.stderr)
        self.assertEqual(list(self.directory.iterdir()), [])


class MakeEmitter(EmitterCase):
    DIGEST = "sha256:" + "0123456789abcdef" * 4

    def test_the_environment_names_the_file_the_run_and_the_artifact(self):
        environ = {emit_module.EVENTS_PATH_ENV: str(self.path), emit_module.RUN_ID_ENV: RUN_ID, emit_module.ARTIFACT_DIGEST_ENV: self.DIGEST}
        first = make_emitter(environ, producer_name="synthetic-controller", producer_version="0.0.0")
        self.addCleanup(self.finish, first)
        second = make_emitter({emit_module.EVENTS_PATH_ENV: str(self.directory / "second.jsonl")})
        self.addCleanup(self.finish, second)
        self.assertEqual((type(first), type(second)), (EventEmitter, EventEmitter))
        for emitter in (first, second):
            emitter.emit("state.transition", TRANSITION)
            self.assertEqual(emitter.close(5.0)["evidence_complete"], True)
        (record,), (other,) = self.records(), self.records(self.directory / "second.jsonl")
        self.assertEqual((record["run_id"], record["producer"]), (RUN_ID, {**PRODUCER, "artifact_digest": self.DIGEST}))
        self.assertEqual((other["run_id"], other["producer"]["artifact_digest"]), (None, None))  # unset is an explicit null
        self.assertNotEqual(record["source_session"], other["source_session"])  # minted per emitter, opaque

    def test_a_value_the_contract_refuses_stops_the_start_and_is_never_echoed(self):
        threads = threading.active_count()
        bad_run, bad_digest = "not a contract id!", "not-a-digest"
        for environ, field, value in (({emit_module.RUN_ID_ENV: bad_run}, "run_id", bad_run), ({emit_module.ARTIFACT_DIGEST_ENV: bad_digest}, "producer.artifact_digest", bad_digest)):
            with self.assertRaises(ValueError) as raised:
                make_emitter({emit_module.EVENTS_PATH_ENV: str(self.path), **environ})
            self.assertIn(field, str(raised.exception))
            self.assertNotIn(value, str(raised.exception))
        for kwargs in ({"mission_queue_max": 0}, {"telemetry_queue_max": True}, {"producer": {**PRODUCER, "contract_version": "2"}}, {"producer": {**PRODUCER, "extra": 1}},
                       {"source_session": "has a space"}):
            with self.assertRaises(ValueError):
                EventEmitter(self.path, **{"producer": PRODUCER, "source_session": SESSION, "run_id": None, **kwargs})
        self.assertEqual(threading.active_count(), threads)  # a refused construction leaves no writer behind
        self.assertFalse(self.path.exists())


class Cost(EmitterCase):
    def test_ten_thousand_emits_from_the_main_thread_while_the_writer_runs(self):
        # The queue is sized for the burst on purpose. A tight loop of emits never gives the interpreter away, so the writer
        # gets a turn only every switch interval and the whole burst has to fit the queue; a control loop that sleeps or
        # waits for the robot between emits does not have that problem. What is measured here is the cost of emit() itself.
        emitter = self.make(mission_queue_max=16384)
        count = 10_000
        durations = [0] * count
        for index in range(count):
            started = time.perf_counter_ns()
            seq = emitter.emit("state.transition", TRANSITION)
            durations[index] = time.perf_counter_ns() - started
            if seq != index:
                self.fail(f"emit {index} returned {seq}")
        total_ns = sum(durations)
        ordered = sorted(durations)
        report(f"{count} emits: mean {total_ns / count / 1000:.2f} us, median {ordered[count // 2] / 1000:.2f} us, p99 {ordered[count * 99 // 100] / 1000:.2f} us, "
               f"worst {ordered[-1] / 1000:.1f} us, total {total_ns / 1e6:.1f} ms")
        self.assertLess(total_ns / count, 100_000, "mean cost of emit() above 100 us")
        self.assertLess(ordered[-1] / 1e9, EMIT_BOUND_S)
        result = emitter.close(30.0)
        self.assertEqual(result, {"evidence_complete": True, "evidence_gaps": 0, "dropped_telemetry": 0, "written": count, "dropped_mission": 0,
                                  "unwritten": 0, "emit_errors": 0, "write_errors": 0})
        lines = self.path.read_bytes().split(b"\n")[:-1]
        self.assertEqual([json.loads(line)["seq"] for line in lines], list(range(count)))
        for line in lines[:50] + lines[50::97] + lines[-50:]:  # the validator is pure Python: a sample, the other tests validate every line
            self.assertEqual(demo_contracts.validate("event", json.loads(line)), [])


if __name__ == "__main__":
    unittest.main()
