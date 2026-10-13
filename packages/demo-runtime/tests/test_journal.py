#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_journal.py: the two durable files of the runtime. The command journal is fsynced on every
# append and survives a torn last line; the event log validates every event, keeps ONE ordered seq across restarts, has a
# FINITE ring with an explicit resync gap, and drops a subscriber that does not read instead of waiting for it.
# Files in a temporary directory only. Run directly: python3 packages/demo-runtime/tests/test_journal.py
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import journal as jn  # noqa: E402

import demo_contracts  # noqa: E402


def text_event(text="a line", run_id=None):
    return jn.make_event("diagnostic.text", {"stream": "log", "level": "info", "text": text, "redacted": False}, source_session="rt-test", run_id=run_id)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-journal-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class CommandJournal(Base):
    def test_every_append_is_on_disk_before_it_returns(self):
        journal = jn.Journal(self.tmp / "journal.jsonl")
        real, synced = os.fsync, []
        with mock.patch("demo_runtime.journal.os.fsync", side_effect=lambda fd: (synced.append(fd), real(fd))[1]):
            journal.append({"rec": "command", "command_id": "c1", "status": "accepted"})
            self.assertEqual(len(synced), 1, "accepted must be synced BEFORE the caller goes on to spawn anything")
            journal.append({"rec": "command", "command_id": "c1", "status": "spawning"})
        self.assertEqual(len(synced), 2)
        self.assertEqual(stat.S_IMODE(os.stat(journal.path).st_mode), 0o600)
        self.assertEqual([record["status"] for record in journal.read()[0]], ["accepted", "spawning"])

    def test_a_torn_last_line_costs_one_record_and_never_the_next_one(self):
        path = self.tmp / "journal.jsonl"
        first = jn.Journal(path)
        first.append({"rec": "command", "command_id": "c1", "status": "accepted"})
        first.close()
        with open(path, "ab") as handle:
            handle.write(b'{"rec":"command","command_id":"c1","status":"spaw')  # the process died inside this write
        second = jn.Journal(path)
        second.append({"rec": "command", "command_id": "c1", "status": "uncertain", "reason_code": "no_child_evidence"})
        records, damaged = second.read()
        self.assertEqual(([record["status"] for record in records], damaged), (["accepted", "uncertain"], 1), "the next record starts on a fresh line and is not glued to the torn one")

    def test_the_fold_keeps_the_last_state_and_a_resolution_leaves_the_command_uncertain(self):
        records = [{"rec": "daemon_start"}, {"rec": "command", "command_id": "c1", "status": "accepted", "run_id": "console-1", "command": {"type": "start_cycle"}},
                   {"rec": "command", "command_id": "c1", "status": "running", "child": {"pid": 7}, "adopted": False}, {"rec": "daemon_start"},
                   {"rec": "command", "command_id": "c1", "status": "running", "child": {"pid": 7}, "adopted": True}, {"rec": "command", "command_id": "c1", "status": "uncertain", "reason_code": "x"},
                   {"rec": "resolution", "command_id": "c1", "finding": "no_controller_running"}, {"rec": "resolution", "command_id": "unknown", "finding": "y"}, {"rec": "command", "status": "accepted"}]
        table = jn.fold_commands(records)
        self.assertEqual(list(table), ["c1"])
        self.assertEqual((table["c1"]["status"], table["c1"]["resolved"], table["c1"]["finding"], table["c1"]["run_id"]), ("uncertain", True, "no_controller_running", "console-1"))
        self.assertEqual(demo_contracts.validate("command_result", jn.command_result(table["c1"])), [])
        self.assertEqual(jn.journal_counters(records), {"daemon_starts": 2, "controller_launches": 1}, "an adoption is not a launch")

    def test_an_attestation_is_kept_beside_the_evidence_and_resolves_nothing_by_itself(self):
        """What the operator declared is durable whatever came of it. Only a 'resolution' record, which the daemon writes
        after reconciling the command against the evidence ITSELF, sets resolved and therefore unblocks commands."""
        records = [{"rec": "command", "command_id": "c1", "status": "accepted", "run_id": "console-1"},
                   {"rec": "command", "command_id": "c1", "status": "uncertain", "reason_code": "no_child_evidence"},
                   {"rec": "attestation", "command_id": "c1", "finding": "no_controller_running", "note": "I looked at the robot", "classification": "still_unknown",
                    "clears_block": False, "refused": None, "t_wall_utc": "2026-09-21T00:00:01.000Z", "evidence": {"process": {"readable": False}}},
                   {"rec": "attestation", "command_id": "c1", "finding": "no_controller_running", "note": None, "classification": None, "clears_block": False,
                    "refused": "controller_alive", "t_wall_utc": "2026-09-21T00:00:02.000Z"}]
        table = jn.fold_commands(records)
        self.assertEqual((table["c1"]["status"], table["c1"]["resolved"]), ("uncertain", False), "two statements, and the command is still blocked")
        self.assertEqual([item["refused"] for item in table["c1"]["attestations"]], [None, "controller_alive"])
        self.assertNotIn("evidence", table["c1"]["attestations"][0], "the folded view keeps the statement, not the whole evidence blob")
        self.assertFalse(any(item["clears_block"] for item in table["c1"]["attestations"]), "neither statement even asked for the block to go")
        resolved = jn.fold_commands(records + [{"rec": "resolution", "command_id": "c1", "finding": "controller_finished", "classification": "ran_and_finished",
                                                "cleared_by": "machine_evidence", "t_wall_utc": "2026-09-21T00:00:03.000Z"}])
        self.assertEqual((resolved["c1"]["resolved"], resolved["c1"]["classification"], resolved["c1"]["cleared_by"]), (True, "ran_and_finished", "machine_evidence"))
        self.assertEqual(len(resolved["c1"]["attestations"]), 2, "and the statements stay on the record next to it")
        self.assertEqual(demo_contracts.validate("command_result", jn.command_result(resolved["c1"])), [])

    def test_a_receipt_says_which_request_it_is_a_receipt_for(self):
        """The digest is over the recorded command object, so two different bodies under one id cannot look alike."""
        first = {"contract_version": "1", "command_id": "c1", "type": "start_cycle", "args": {"plan_only": False},
                 "client": {"session_id": "tab-1", "contract_version": "1"}}
        table = jn.fold_commands([{"rec": "command", "command_id": "c1", "status": "accepted", "run_id": "console-1", "command": first,
                                   "t_wall_utc": "2026-09-21T00:00:01.000Z"},
                                  {"rec": "command", "command_id": "c1", "status": "running", "t_wall_utc": "2026-09-21T00:00:02.000Z"}])
        receipt = jn.command_result(table["c1"])
        self.assertEqual(demo_contracts.validate("command_result", receipt), [])
        self.assertEqual(receipt["payload_digest"], jn.payload_digest(first))
        self.assertEqual(receipt["first_seen_wall_utc"], "2026-09-21T00:00:01.000Z", "the FIRST record of the command, not the last one")
        # The same request in another key order is the same request; a different plan_only is not.
        reordered = {"client": {"contract_version": "1", "session_id": "tab-1"}, "args": {"plan_only": False}, "type": "start_cycle",
                     "command_id": "c1", "contract_version": "1"}
        self.assertEqual(jn.payload_digest(reordered), receipt["payload_digest"])
        self.assertNotEqual(jn.payload_digest({**first, "args": {"plan_only": True}}), receipt["payload_digest"])

    def test_a_receipt_with_nothing_recorded_under_it_claims_nothing(self):
        """A rejected command records no command object and no digest may be invented for it: a caller that compares
        must be able to tell "this journal cannot say" from "the two payloads agree"."""
        table = jn.fold_commands([{"rec": "command", "command_id": "c9", "status": "rejected", "reason_code": "run_active"}])
        receipt = jn.command_result(table["c9"])
        self.assertNotIn("payload_digest", receipt)
        self.assertNotIn("first_seen_wall_utc", receipt, "no stamp was written, so none is reported")
        self.assertEqual(demo_contracts.validate("command_result", receipt), [])
        self.assertIsNone(jn.payload_digest(None))
        self.assertIsNone(jn.canonical_command("not a command"))

    def test_a_stamp_that_is_not_a_contract_time_is_left_off_the_receipt(self):
        """A receipt that fails its own contract is worse than one that says less than it could."""
        table = jn.fold_commands([{"rec": "command", "command_id": "c1", "status": "accepted", "t_wall_utc": "yesterday afternoon"}])
        receipt = jn.command_result(table["c1"])
        self.assertNotIn("first_seen_wall_utc", receipt)
        self.assertEqual(demo_contracts.validate("command_result", receipt), [])

    def test_history_and_last_outcome_come_from_the_journal_alone(self):
        result = {"outcome": "placed_new", "exit_status": 0, "predicates": {"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True},
                  "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": True, "evidence_gaps": 0}
        completion = {"command_type": "start_cycle", "outcome": "placed_new", "exit_status": 0, "evidence_complete": True, "evidence_gaps": 0, "artifact_digest": "ab" * 32,
                      "finished_seq": 41, "result": result, "stop": {"requested_mono_ns": 1}, "started_wall_utc": "2026-01-01T00:00:00Z", "finished_wall_utc": "2026-01-01T00:00:09Z"}
        table = jn.fold_commands([{"rec": "command", "command_id": "c1", "status": "finished", "run_id": "console-1", "completion": completion},
                                  {"rec": "command", "command_id": "c2", "status": "rejected", "reason_code": "run_active"},
                                  {"rec": "command", "command_id": "c3", "status": "finished", "command_type": "stop"}])
        self.assertEqual([entry["run_id"] for entry in jn.history_entries(table)], ["console-1"], "rejected commands and stop commands are not runs")
        self.assertNotIn("stop", jn.history_entries(table)[0])
        outcome = jn.last_outcome(table)
        self.assertEqual((outcome["run_id"], outcome["finished_seq"], outcome["result"]["outcome"]), ("console-1", 41, "placed_new"))
        self.assertIsNone(jn.last_outcome({}))

    def test_a_history_entry_states_a_data_mode_only_when_the_run_recorded_one(self):
        # Whether a run drove hardware, replayed a recording or was synthetic is not in the completion record, so the
        # caller that owns the data directory resolves it from the manifest the run wrote for itself. A run that
        # recorded none leaves the field ABSENT, which the contract reads as not recorded; it is never filled in from
        # the mode the runtime happens to be in now, because that would answer a question about a past run with a fact
        # about the present one.
        completion = {"command_type": "start_cycle", "outcome": "placed_new", "exit_status": 0, "evidence_complete": True, "evidence_gaps": 0,
                      "artifact_digest": "ab" * 32, "started_wall_utc": "2026-01-01T00:00:00Z", "finished_wall_utc": "2026-01-01T00:00:09Z"}
        table = jn.fold_commands([{"rec": "command", "command_id": "c1", "status": "finished", "run_id": "console-1", "completion": completion}])
        plain = jn.history_entries(table)[0]
        self.assertNotIn("data_mode", plain)
        self.assertEqual(demo_contracts.validate("history_entry", plain), [], "an entry without it is a valid entry: the field is an extension")
        recorded = jn.history_entries(table, lambda run_id: "live" if run_id == "console-1" else None)[0]
        self.assertEqual(recorded["data_mode"], "live")
        self.assertEqual(demo_contracts.validate("history_entry", recorded), [])
        self.assertNotIn("bundle_digest", recorded, "artifact_digest on the same entry already IS the pinned bundle's digest")
        forgotten = jn.history_entries(table, lambda run_id: None)[0]
        self.assertNotIn("data_mode", forgotten, "a run whose manifest is gone says nothing, and nothing is filled in for it")


class RuntimeEventLog(Base):
    def test_an_invalid_event_is_refused_and_nothing_is_written(self):
        log = jn.EventLog(self.tmp / "events.jsonl")
        bad = text_event()
        bad["payload"]["text"] = "see /" + "home" + "/someone/secret"  # the contract's safe_text tripwire
        with self.assertRaises(jn.InvalidEvent):
            log.append(bad)
        with self.assertRaises(jn.InvalidEvent):
            log.append({**text_event(), "kind": "command.accepted"})
        self.assertEqual((log.seq_cursor, os.path.getsize(log.path)), (0, 0))

    def test_one_ordered_seq_that_continues_across_a_restart(self):
        log = jn.EventLog(self.tmp / "events.jsonl")
        self.assertEqual((log.seq_cursor, log.retained_from_seq), (0, 1))
        self.assertEqual([log.append(text_event(str(index)))["seq"] for index in range(5)], [1, 2, 3, 4, 5])
        log.close()
        with open(self.tmp / "events.jsonl", "ab") as handle:
            handle.write(b'{"origin":null,"event":{"seq":6,"kind":"diag')
        reopened = jn.EventLog(self.tmp / "events.jsonl")
        self.assertEqual((reopened.seq_cursor, reopened.damaged_lines), (5, 1))
        self.assertEqual(reopened.append(text_event("after"))["seq"], 6)
        self.assertEqual([record["event"]["seq"] for record in jn.read_json_lines(reopened.path)[0]], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(demo_contracts.validate("event", record["event"]) == [] for record in jn.read_json_lines(reopened.path)[0]))

    def test_the_ring_is_finite_and_a_subscription_older_than_it_starts_with_the_gap(self):
        log = jn.EventLog(self.tmp / "events.jsonl", ring_size=10)
        for index in range(25):
            log.append(text_event(str(index)), sync=False)
        self.assertEqual((log.seq_cursor, log.retained_from_seq), (25, 16))
        old = log.subscribe(3)
        self.assertEqual(old.resync, {"retained_from_seq": 16, "missed": [4, 15]})
        self.assertEqual([event["seq"] for event in old.backlog], list(range(16, 26)))
        edge = log.subscribe(15)
        self.assertIsNone(edge.resync, "since=15 wants 16 next, and 16 is still retained: no gap")
        current = log.subscribe(25)
        self.assertEqual((current.resync, current.backlog), (None, []))
        ahead = log.subscribe(900)
        self.assertEqual((ahead.resync["reason"], ahead.resync["missed"]), ("cursor_ahead", None))
        log.append(text_event("live"))
        self.assertEqual(current.queue.get_nowait()["seq"], 26)
        log.unsubscribe(current)
        log.append(text_event("after unsubscribe"))
        self.assertTrue(current.queue.empty())

    def test_a_subscriber_that_does_not_read_is_dropped_and_append_never_waits(self):
        log = jn.EventLog(self.tmp / "events.jsonl")
        stalled, reader = log.subscribe(0, maxsize=8), log.subscribe(0, maxsize=100000)
        started = time.monotonic()
        for index in range(3000):
            log.append(text_event(str(index)), sync=False)
        self.assertLess(time.monotonic() - started, 20.0)
        self.assertTrue(stalled.dropped)
        self.assertFalse(reader.dropped)
        self.assertEqual((stalled.queue.qsize(), reader.queue.qsize(), log.dropped_subscribers, log.subscriber_count()), (8, 3000, 1, 1))

    def test_appends_from_many_threads_keep_one_gapless_order(self):
        log = jn.EventLog(self.tmp / "events.jsonl")
        threads = [threading.Thread(target=lambda: [log.append(text_event("t"), sync=False) for _ in range(200)]) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([record["event"]["seq"] for record in jn.read_json_lines(log.path)[0]], list(range(1, 1201)))

    def test_origins_give_the_resume_offset_and_a_run_can_be_read_back(self):
        log = jn.EventLog(self.tmp / "events.jsonl", ring_size=4)
        seen = []
        log.add_listener(lambda event: seen.append(event["seq"]))
        for index in range(1, 9):
            log.append(text_event(str(index), run_id="console-a" if index % 2 else "console-b"),
                       origin={"run_id": "console-a" if index % 2 else "console-b", "source_session": "fake-1", "seq": index, "end_offset": index * 100})
        self.assertEqual(seen, list(range(1, 9)))
        self.assertEqual((log.resume_offset("console-a"), log.resume_offset("console-b"), log.resume_offset("console-none")), (700, 800, 0))
        self.assertEqual([record["origin"]["seq"] for record in log.read_run("console-a")], [1, 3, 5, 7], "the file serves a whole run although the ring kept four events")
        log.close()
        reopened = jn.EventLog(self.tmp / "events.jsonl", ring_size=4, read_only=True)
        self.assertEqual((reopened.resume_offset("console-a"), reopened.seq_cursor, reopened.retained_from_seq), (700, 8, 5), "offsets and the cursor are rebuilt from the file")
        with self.assertRaises(RuntimeError):
            reopened.append(text_event())
        with self.assertRaises(RuntimeError):
            jn.Journal(self.tmp / "journal.jsonl", read_only=True).append({"rec": "command"})

    def test_a_runtime_event_has_the_runtime_as_its_producer(self):
        event = jn.make_event("stop.requested", {"origin": "operator_console", "command_id": None}, source_session="rt-1", run_id="console-1", artifact_digest="ab" * 32)
        self.assertEqual(demo_contracts.validate("event", event), [])
        self.assertEqual((event["producer"]["name"], event["producer"]["contract_version"], event["clock_domain"]), ("demo-runtime", jn.CONTRACT_VERSION, "laptop_monotonic"))
        json.dumps(event)

    def test_sync_takes_the_descriptor_under_the_lock(self):
        # The defect this pins, MEASURED as OSError(9): sync() read self._fd and then fsynced it with nothing in between.
        # The tailer calls sync() after every batch while a shutdown closes the log, so the number could already be
        # closed, and the fsync then raised into the tailer thread (which never closed its own handle) or reached a
        # descriptor that had since been given to another file.
        log = jn.EventLog(self.tmp / "events.jsonl")
        log.append(text_event())
        held, real_fsync = [], os.fsync

        def watching_fsync(descriptor):
            taken = log._lock.acquire(blocking=False)
            held.append(taken)
            if taken:
                log._lock.release()
            return real_fsync(descriptor)

        with mock.patch.object(jn.os, "fsync", watching_fsync):
            log.sync()
        log.close()
        self.assertEqual(held, [False], "sync must hold the log's lock while it fsyncs, so close cannot take the descriptor away")

    def test_sync_and_close_from_two_threads_never_raise(self):
        errors = []
        for _ in range(40):
            log = jn.EventLog(self.tmp / "events.jsonl")
            halt = threading.Event()

            def syncer(log=log, halt=halt):
                while not halt.is_set():
                    try:
                        log.sync()
                    except Exception as error:  # noqa: BLE001 -- whatever it is, the tailer thread would have died of it
                        errors.append(repr(error))
                        return

            thread = threading.Thread(target=syncer, daemon=True)
            thread.start()
            time.sleep(0.001)
            log.close()
            halt.set()
            thread.join(10.0)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
