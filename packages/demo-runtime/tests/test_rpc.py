#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_rpc.py: the runtime's socket, from the outside. A RuntimeCore and its RuntimeServer run in
# this process (so a test can give it a small ring, a private-looking config and a fixed Cake probe) and every assertion goes
# through a real Unix socket with RuntimeClient or a raw socket. The only child process is tests/fake_controller.py.
# Strings the repository's text gates refuse (a private address, a user name, device ids) are BUILT AT RUN TIME.
# Run directly: python3 packages/demo-runtime/tests/test_rpc.py
import base64
import functools
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import cli, daemon, forwards, preflight, read_models, sources  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.config import RuntimeConfig  # noqa: E402
from demo_runtime.journal import make_event  # noqa: E402
from demo_runtime.rpc import ReadOnlyClient, RuntimeClient, RuntimeServer, RuntimeUnavailable  # noqa: E402

import demo_contracts  # noqa: E402

WAIT_S = 40.0
PRIVATE_ADDRESS = ".".join(("192", "168", "7", "41"))
USER_NAME = "operator" + "name"
SSH_TARGET = USER_NAME + "@" + PRIVATE_ADDRESS
ROBOT_ID = "kiwi" + "-unit-" + "0042"
WRIST_ID = "usb-" + "Vendor_Cam_" + "SN" + "7654321" + "-video-index0"
PRIVATE_DIR = "private-" + USER_NAME
FIXED_CAKE = {"artifacts": False, "resident": False, "admin_socket": False}


def wait_for(test, predicate, what):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    test.fail(f"timed out waiting for {what}")


class SilentWatch:
    """A robot-readiness reader that asks nothing and runs no thread.

    EVERY runtime in this file is built with one, so no test in here can open an ssh to a machine by accident: the real
    reader's first act is to probe, and the target of these fixtures is a plausible address. The tests that care about
    the reading say what it should answer; the rest never look at it."""

    def __init__(self, state="never_read"):
        self.state, self.starts, self.stops, self.reads = state, 0, 0, 0

    def start(self):
        self.starts += 1
        return self

    def stop(self):
        self.stops += 1

    def reading(self):
        from demo_runtime.preflight import readiness_of

        self.reads += 1
        return readiness_of(self.state, None if self.state == "never_read" else 0.0)


class Runtime:
    """One in-process runtime behind a real socket."""

    def __init__(self, test, *, mode=daemon.SYNTHETIC, data=None, config=None, ring_size=None, step_s="0.01", deep=False, lease=None, forwards=None,
                 hardware_enabled=False, readiness=None):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-rpc-"))
        self.data = Path(data) if data else (self.tmp / PRIVATE_DIR / ("nested-" * 12 if deep else "") / "data")
        self.watch = readiness if readiness is not None else (lambda target: SilentWatch())
        self.core = daemon.RuntimeCore(data_dir=self.data, mode=mode, config=config, config_path=str(self.tmp / PRIVATE_DIR / "runtime-config.json") if config else None,
                                       source_root=REPO_ROOT, ring_size=ring_size, cake_probe=lambda: dict(FIXED_CAKE), extra_env={"KC_FAKE_STEP_S": step_s},
                                       lease=lease, forwards=forwards, hardware_enabled=hardware_enabled, readiness=self.watch,
                                       # a LIVE runtime here runs the fake controller: no test can start the real one
                                       controller_entry=daemon.FAKE_CONTROLLER_ENTRY,
                                       source_supervisor=(lambda hub: sources.SourceSupervisor(hub)) if mode == daemon.LIVE else None)
        self.core.start()
        self.server = RuntimeServer(self.data / daemon.SOCKET_NAME, self.core.handle, self.core.stream)
        self.server.start()
        self.client = RuntimeClient(self.data / daemon.SOCKET_NAME, timeout_s=20.0)
        test.addCleanup(self.close)

    def shutdown(self):
        """End the runtime and KEEP its data directory (a replay-only runtime is opened on it afterwards)."""
        if self.core is None:
            return
        supervisor = self.core.supervisor
        if supervisor is not None and supervisor.active_run() is not None:
            supervisor.stop()
            wait_for(self.test, lambda: supervisor.active_run() is None, "the run to end at cleanup")
        self.server.close()
        self.core.close()
        self.core = None

    def close(self):
        self.shutdown()
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cycle(self, command_id):
        result = self.client.submit(self.client.new_command("start_cycle", {"plan_only": False}, command_id=command_id))
        self.test.assertEqual(result["status"], "running")
        wait_for(self.test, lambda: self.client.result(command_id)["status"] == "finished", "the cycle to finish")
        return result


def private_config(root: Path, **overrides) -> RuntimeConfig:
    return RuntimeConfig.from_mapping({"data_root": str(root / PRIVATE_DIR / "demo-data"), "saved_pose_file": "calibration/scan-home-current.json", "pad_template_dir": "calibration/pad-template",
                                       "observation_root": "observations", "host": {"address": PRIVATE_ADDRESS, "robot_id": ROBOT_ID}, "pi_ssh_target": SSH_TARGET,
                                       "cameras": {"wrist_id": WRIST_ID, "front_id": None}, "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None},
                                       **overrides})


class HelloAndReadOnly(unittest.TestCase):
    def test_hello_names_the_contract_and_the_runtime_session(self):
        runtime = Runtime(self)
        answer = runtime.client.hello()
        self.assertEqual((answer["ok"], answer["contract_version"]), (True, demo_contracts.CONTRACT_VERSION))
        self.assertEqual(answer["runtime_session"], runtime.core.runtime_session)
        self.assertFalse(runtime.client.read_only)

    def test_an_incompatible_contract_version_makes_the_client_read_only(self):
        runtime = Runtime(self)
        old = RuntimeClient(runtime.data / daemon.SOCKET_NAME, contract_version="2")
        answer = old.hello()
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, "contract_incompatible"))
        self.assertTrue(old.read_only)
        for call in (lambda: old.submit(old.new_command("start_cycle", {"plan_only": True})), old.stop, lambda: old.resolve("x", "no_controller_running")):
            with self.assertRaises(ReadOnlyClient):
                call()
        self.assertEqual(demo_contracts.validate("snapshot", old.snapshot()), [], "reading still works")
        self.assertEqual(runtime.core.supervisor.launches, 0)
        fresh = RuntimeClient(runtime.data / daemon.SOCKET_NAME, contract_version="2")  # a client that never said hello learns it at its first command
        with self.assertRaises(ReadOnlyClient):
            fresh.submit(fresh.new_command("start_cycle", {"plan_only": True}))
        self.assertTrue(fresh.read_only)

    def test_the_server_refuses_a_command_on_a_connection_without_a_compatible_hello(self):
        runtime = Runtime(self)
        for hello in (None, {"op": "hello", "contract_version": "2"}):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
                raw.settimeout(10)
                raw.connect(str(runtime.data / daemon.SOCKET_NAME))
                lines = raw.makefile("rb")
                if hello:
                    raw.sendall((json.dumps(hello) + "\n").encode())
                    self.assertEqual(json.loads(lines.readline())["reason_code"], "contract_incompatible")
                for request in ({"op": "submit", "command": runtime.client.new_command("start_cycle", {"plan_only": True})}, {"op": "stop"}, {"op": "resolve", "command_id": "x", "finding": "y"}):
                    raw.sendall((json.dumps(request) + "\n").encode())
                    self.assertEqual(json.loads(lines.readline()), {"ok": False, "reason_code": "hello_required"})
                raw.sendall(b"not json\n" + json.dumps({"op": "no_such_op"}).encode() + b"\n" + json.dumps({"op": "_op_submit"}).encode() + b"\n")
                self.assertEqual([json.loads(lines.readline())["reason_code"] for _ in range(3)], ["bad_request", "unknown_op", "unknown_op"])
        self.assertEqual(runtime.core.supervisor.launches, 0)

    def test_the_socket_is_private_and_a_deep_data_directory_still_works(self):
        runtime = Runtime(self, deep=True)
        path = runtime.data / daemon.SOCKET_NAME
        self.assertGreater(len(os.fsencode(str(path))), 108, "deeper than a Unix socket address can hold: the first build failed to bind here")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(runtime.data).st_mode), 0o700)
        self.assertTrue(runtime.client.hello()["ok"])
        with self.assertRaises(RuntimeUnavailable):
            RuntimeClient(runtime.tmp / "no-such.sock").snapshot()

    def test_a_reconnecting_client_is_counted(self):
        runtime = Runtime(self)
        for _ in range(3):
            runtime.client.hello()
        RuntimeClient(runtime.data / daemon.SOCKET_NAME).hello()
        self.assertEqual(runtime.client.status()["counters"]["client_reconnects"], 2)


class SnapshotsEventsAndHistory(unittest.TestCase):
    def test_every_snapshot_and_every_event_validates_through_a_whole_run(self):
        runtime = Runtime(self, step_s="0.02")
        idle = runtime.client.snapshot()
        self.assertEqual(demo_contracts.validate("snapshot", idle), [])
        self.assertEqual((idle["data_mode"], idle["owner"]["kind"], idle["active_run"], idle["last_outcome"]), ("synthetic", "free", None, None))
        states = {item["id"]: (item["state"], item["reason_code"]) for item in idle["capabilities"]}
        self.assertEqual((states["start_cycle"], states["stop"], states["cake_update"]), (("enabled", None), ("disabled", "no_active_run"), ("disabled", "not_built")))
        started = runtime.client.submit(runtime.client.new_command("start_cycle", {"plan_only": False}, command_id="snap-0001"))
        seen = []
        while runtime.client.result("snap-0001")["status"] != "finished":
            snapshot = runtime.client.snapshot()
            self.assertEqual(demo_contracts.validate("snapshot", snapshot), [])
            self.assertEqual(snapshot["data_mode"], "synthetic")
            seen.append(snapshot)
            time.sleep(0.05)
        running = [snapshot for snapshot in seen if snapshot["active_run"]]
        self.assertTrue(running)
        during = {item["id"]: (item["state"], item["reason_code"]) for item in running[-1]["capabilities"]}
        self.assertEqual((during["start_cycle"], during["observe"], during["stop"]), (("blocked", "run_active"), ("blocked", "run_active"), ("enabled", None)))
        self.assertEqual((running[-1]["owner"]["kind"], running[-1]["active_run"]["run_id"]), ("autonomous", started["run_id"]))
        self.assertTrue(any(snapshot["active_run"]["state"] for snapshot in running), "the state machine's state is served while the run goes on")
        after = runtime.client.snapshot()
        self.assertEqual(demo_contracts.validate("snapshot", after), [])
        self.assertEqual((after["last_outcome"]["run_id"], after["last_outcome"]["result"]["outcome"], after["evidence"]), (started["run_id"], "placed_new", {"complete": True, "gaps": 0, "dropped_telemetry": 0}))
        layers = {item["id"]: item["layer"] for item in after["components"]}
        self.assertEqual((layers["runtime-daemon"], layers["cycle-state-machine"], layers["wrist-camera"]), ("process", "logical_python", "device"))
        self.assertNotIn("cake_module", layers.values(), "there are NO cake modules today")
        ladder = after["cake"]["ladder"]
        self.assertEqual((ladder["mission_participation"]["value"], ladder["mission_participation"]["evidence"]), (False, "the controller has no Cake adapter"))
        self.assertEqual((after["cake"]["slots"], ladder["artifacts_available"]["value"], ladder["admin_interface_compatible"]["value"]), ([], False, None))
        events = list(runtime.client.events(0, idle_timeout_s=0.5))
        self.assertEqual([error for event in events for error in demo_contracts.validate("event", event)], [])
        self.assertEqual([event["seq"] for event in events], list(range(1, after["seq_cursor"] + 1)))

    def test_since_older_than_the_ring_yields_an_explicit_resync_gap(self):
        runtime = Runtime(self, ring_size=16)
        runtime.run_cycle("ring-0001")
        snapshot = runtime.client.snapshot()
        self.assertGreater(snapshot["retained_from_seq"], 3, "the ring is finite and says from where it can still serve")
        self.assertEqual(snapshot["retained_from_seq"], snapshot["seq_cursor"] - 15)
        lines = list(runtime.client.events(2, idle_timeout_s=0.5))
        self.assertEqual(lines[0], {"resync": {"retained_from_seq": snapshot["retained_from_seq"], "missed": [3, snapshot["retained_from_seq"] - 1]}}, "a GAP is stated, never skipped in silence")
        self.assertEqual([event["seq"] for event in lines[1:]], list(range(snapshot["retained_from_seq"], snapshot["seq_cursor"] + 1)))
        current = list(runtime.client.events(snapshot["seq_cursor"] - 4, idle_timeout_s=0.5))
        self.assertNotIn("resync", current[0])
        self.assertEqual(len(current), 4)
        ahead = list(runtime.client.events(snapshot["seq_cursor"] + 1000, idle_timeout_s=0.5))
        self.assertEqual(ahead[0]["resync"]["reason"], "cursor_ahead", "a cursor from another log is answered, not waited on forever")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.settimeout(10)
            raw.connect(str(runtime.data / daemon.SOCKET_NAME))
            raw.sendall(b'{"op":"events","since":-4}\n')
            self.assertEqual(json.loads(raw.makefile("rb").readline())["reason_code"], "bad_request")

    def test_live_events_arrive_while_a_run_goes_on(self):
        runtime = Runtime(self, step_s="0.02")
        stream = runtime.client.events(runtime.client.snapshot()["seq_cursor"], idle_timeout_s=10.0)
        runtime.client.submit(runtime.client.new_command("start_cycle", {"plan_only": False}, command_id="live-0001"))
        kinds = []
        for event in stream:
            kinds.append(event["kind"])
            if event["kind"] == "run.finished":
                break
        self.assertEqual((kinds.count("run.started"), kinds.count("run.finished")), (1, 1))
        self.assertGreaterEqual(kinds.count("state.transition"), 25)

    def test_a_reader_that_stops_reading_is_dropped_and_the_run_is_not_slowed(self):
        with mock.patch("demo_runtime.rpc.SEND_TIMEOUT_S", 0.3):
            runtime = Runtime(self)
            for index in range(3000):  # a backlog far larger than a socket buffer, so the server's send to the dead reader must block
                runtime.core.log.append(make_event("diagnostic.text", {"stream": "log", "level": "debug", "text": "filler " + "x" * 600, "redacted": False}, source_session="filler"), sync=False)
            stalled = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(stalled.close)
            stalled.connect(str(runtime.data / daemon.SOCKET_NAME))
            stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            stalled.sendall(b'{"op":"events","since":0}\n')  # and then it never reads a byte
            wait_for(self, lambda: runtime.core.log.subscriber_count() == 1, "the stalled reader to be subscribed")
            started = time.monotonic()
            runtime.run_cycle("stalled-0001")
            self.assertLess(time.monotonic() - started, 12.0, "a reader that does not read must never hold the supervisor, the tailer or the log")
            self.assertEqual(runtime.client.snapshot()["last_outcome"]["result"]["outcome"], "placed_new")
            wait_for(self, lambda: runtime.core.log.subscriber_count() == 0, "the stalled reader's subscription to be released")
            self.assertTrue(runtime.client.hello()["ok"], "the socket still serves everybody else")

    def test_history_lists_finished_runs_and_serves_their_events_with_origins(self):
        runtime = Runtime(self)
        first, second = runtime.run_cycle("hist-0001"), runtime.run_cycle("hist-0002")
        runs = runtime.client.history()["runs"]
        self.assertEqual([run["run_id"] for run in runs], [first["run_id"], second["run_id"]])
        self.assertEqual({(run["outcome"], run["evidence_complete"], run["exit_status"], run["adopted"]) for run in runs}, {("placed_new", True, 0, False)})
        self.assertTrue(all(demo_contracts.validate("common#/$defs/digest", run["artifact_digest"]) == [] for run in runs))
        self.assertEqual(runs[0]["artifact_digest"], runs[1]["artifact_digest"], "the same code is the same pinned bundle, built once")
        events = runtime.client.history_events(first["run_id"])
        self.assertTrue(all(event["run_id"] == first["run_id"] for event in events["events"]))
        self.assertTrue(all(origin["source_session"].startswith("fake-controller-") for origin in events["origins"] if origin["source_seq"] is not None))
        self.assertEqual(runtime.client.history_events("console-none")["reason_code"], "unknown_run")
        self.assertEqual(runtime.client.history_events("../etc")["reason_code"], "bad_request")
        self.assertEqual(runtime.client.result("never-sent")["reason_code"], "unknown_command")
        self.assertEqual(runtime.client.status()["counters"]["controller_launches"], 2)

    def test_the_history_list_states_the_data_mode_each_run_recorded(self):
        runtime = Runtime(self)
        runtime.run_cycle("mode-0001")
        entry = runtime.client.history()["runs"][0]
        self.assertEqual(demo_contracts.validate("history_entry", entry), [])
        self.assertEqual(entry["data_mode"], "synthetic", "read from the manifest that run wrote, never from the mode this runtime is in now")
        next((runtime.data / "history").glob("*/manifest.json")).write_text(json.dumps({"schema": "demo-runtime-run-manifest/1"}) + "\n")
        runtime.core._manifests.clear()
        self.assertNotIn("data_mode", runtime.client.history()["runs"][0], "a manifest that does not fit the contract states nothing, and nothing is guessed for it")

    def test_the_snapshot_carries_the_runs_own_timing_figures_and_its_evidence(self):
        runtime = Runtime(self)
        run_id = runtime.run_cycle("timing-0001")["run_id"]
        snapshot = runtime.client.snapshot()
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [], "the whole answer validates: a client strips nothing before checking it")
        timings = snapshot["timings"]
        self.assertEqual(timings["run_id"], run_id, "the figures belong to ONE run and say which")
        self.assertTrue(timings["spans"], "the controller measured spans and the runtime aggregated them")
        for figure in timings["spans"]:
            with self.subTest(span=figure["span"]):
                self.assertGreaterEqual(figure["count"], 1)
                self.assertGreaterEqual(figure["total_ns"], figure["max_ns"])
                self.assertLessEqual(figure["first_seq"], figure["last_seq"])
                self.assertEqual(figure["clock_domain"], "laptop_monotonic")
        self.assertEqual(len({(figure["span"], figure["clock_domain"]) for figure in timings["spans"]}), len(timings["spans"]), "one figure per span and domain")
        self.assertEqual(snapshot["run_evidence"]["run_id"], run_id)
        self.assertEqual(snapshot["run_evidence"]["complete"], True, "the run's own run.finished record is what says it")
        self.assertGreater(snapshot["run_evidence"]["records"], 100)
        fresh = Runtime(self).client.snapshot()
        self.assertEqual((fresh["timings"], fresh["run_evidence"]), ({"run_id": None, "spans": []}, None), "nothing is reported before a run exists")


class ManifestAndExport(unittest.TestCase):
    """What one run states about itself, and the same run packaged for somebody else. Both are read-only, both refuse a
    run this data directory never recorded, and both refuse a manifest that does not fit the contract rather than serving
    a document a client typed against the contract cannot read."""

    def test_the_daemon_names_the_run_directory_the_supervisor_writes(self):
        from demo_runtime import supervisor as sv

        self.assertEqual((daemon.HISTORY_DIR_NAME, daemon.MANIFEST_FILE_NAME), (sv.HISTORY_DIR, sv.MANIFEST_FILE),
                         "the daemon may not import the supervisor, so it repeats the two names and this keeps them equal")
        self.assertEqual((daemon.STDOUT_FILE_NAME, daemon.STDERR_FILE_NAME), (sv.STDOUT_FILE, sv.STDERR_FILE),
                         "the same for the controller output files, which the daemon now serves")


class TheControllersOwnOutput(unittest.TestCase):
    """The supervisor has always written the controller's stdout and stderr into the run directory and nothing served
    them, so the one record of what the controller actually did needed a shell on this machine to read. That is what
    sent the operator of the 2026-09-22 align failure to a terminal."""

    def test_the_output_of_a_real_run_is_served_for_both_streams(self):
        runtime = Runtime(self)
        run_id = runtime.run_cycle("output-0001")["run_id"]
        out = runtime.client.run_output(run_id, "stdout")
        self.assertTrue(out["ok"])
        self.assertEqual((out["run_id"], out["stream"], out["truncated"]), (run_id, "stdout", False))
        self.assertGreater(len(out["text"]), 0, "the fake controller prints, so its stdout is not empty")
        self.assertEqual(out["bytes"], len(out["text"].encode("utf-8")))
        err = runtime.client.run_output(run_id, "stderr")
        self.assertTrue(err["ok"], "an empty stderr is the ordinary case for a good run, not an error")

    def test_the_path_is_composed_from_the_run_id_so_nothing_in_the_request_can_point_at_a_file(self):
        runtime = Runtime(self)
        runtime.run_cycle("output-0002")
        # `.` and `..` pass the contract id shape, because the dot is in its character class. The shared resolver is
        # what refuses them; a separator never gets that far because the shape has none.
        for run_id in ("../journal.jsonl", "a/b", "", "sc-2026/../..", "sc 1"):
            answer = runtime.client.run_output(run_id, "stdout")
            self.assertFalse(answer["ok"], run_id)
            self.assertEqual(answer["reason_code"], "bad_request", run_id)
        for run_id in (".", ".."):
            answer = runtime.client.run_output(run_id, "stdout")
            self.assertFalse(answer["ok"], run_id)
            self.assertEqual(answer["reason_code"], "unknown_run", run_id)

    def test_a_stream_it_does_not_serve_is_refused(self):
        runtime = Runtime(self)
        run_id = runtime.run_cycle("output-0003")["run_id"]
        for stream in ("manifest.json", "controller-events.jsonl", "../journal.jsonl", "STDOUT", ""):
            answer = runtime.client.run_output(run_id, stream)
            self.assertFalse(answer["ok"], stream)
            self.assertEqual(answer["reason_code"], "bad_request", stream)

    def test_a_run_this_data_directory_never_recorded_is_refused(self):
        runtime = Runtime(self)
        self.assertEqual(runtime.client.run_output("sc-20260101T000000Z-nothing", "stdout")["reason_code"], "unknown_run")

    def test_the_read_is_bounded_and_says_so_and_never_returns_half_a_line(self):
        runtime = Runtime(self)
        run_id = runtime.run_cycle("output-0004")["run_id"]
        whole = runtime.client.run_output(run_id, "stdout")["text"]
        self.assertIn("\n", whole, "this test needs output with more than one line in it")
        tail = runtime.client.run_output(run_id, "stdout", max_bytes=16)
        self.assertTrue(tail["ok"])
        self.assertTrue(tail["truncated"], "a reader is never quietly shown a part and told it is the whole")
        self.assertEqual(tail["total_bytes"], len(whole.encode("utf-8")), "the size of the whole file is still stated")
        self.assertLess(len(tail["text"]), len(whole))
        # A tail begins mid-line, and half a line read as a whole one is a misquote, so the partial first line is dropped.
        self.assertTrue(tail["text"] == "" or whole.endswith(tail["text"]))

    def test_a_bound_that_is_not_a_positive_number_is_refused(self):
        runtime = Runtime(self)
        run_id = runtime.run_cycle("output-0005")["run_id"]
        for value in (0, -1, True, "4096", 1.5):
            self.assertEqual(runtime.client.run_output(run_id, "stdout", max_bytes=value)["reason_code"], "bad_request", value)

    def test_a_runs_manifest_is_served_and_checked_against_the_contract_first(self):
        runtime = Runtime(self)
        run_id = runtime.run_cycle("manifest-0001")["run_id"]
        answer = runtime.client.manifest(run_id)
        self.assertTrue(answer["ok"])
        self.assertEqual(demo_contracts.validate("manifest", answer["manifest"]), [])
        self.assertEqual((answer["manifest"]["run_id"], answer["manifest"]["data_mode"]), (run_id, "synthetic"))
        self.assertEqual(len(answer["manifest"]["bundle_digest"]), 64)
        self.assertEqual(runtime.client.manifest("console-none")["reason_code"], "unknown_run")
        self.assertEqual(runtime.client.manifest("../etc")["reason_code"], "bad_request")
        (runtime.data / "history" / run_id / "manifest.json").write_text(json.dumps({"schema": "demo-runtime-run-manifest/1"}) + "\n")
        runtime.core._manifests.clear()
        self.assertEqual(runtime.client.manifest(run_id)["reason_code"], "manifest_invalid", "half a manifest is worse than a named refusal")
        (runtime.data / "history" / run_id / "manifest.json").unlink()
        self.assertEqual(runtime.client.manifest(run_id)["reason_code"], "manifest_not_recorded")

    def test_a_run_is_exported_with_its_records_and_says_what_it_could_not_carry(self):
        runtime = Runtime(self)
        run_id = runtime.run_cycle("export-0001")["run_id"]
        export = runtime.client.export(run_id)
        self.assertTrue(export["ok"])
        index = export["index"]
        self.assertEqual(demo_contracts.validate("run_export_index", index), [])
        self.assertEqual(index["run_id"], run_id)
        self.assertEqual(index["records"]["count"], len(export["records"]), "the index counts exactly what the export carries")
        self.assertEqual(index["records"]["count"], len(runtime.client.history_events(run_id)["events"]))
        self.assertEqual([error for record in export["records"] for error in demo_contracts.validate("event", record)], [])
        self.assertIn(runtime.core.runtime_session, index["records"]["source_sessions"])
        self.assertEqual(index["summaries"]["evidence_complete"], True)
        self.assertTrue(index["images"], "the run captured observations, so the export names them")
        # This runtime builds its observations in memory, so there is no recorded frame to carry. The export says so for
        # every one of them rather than exporting a rendition and calling it the recording.
        self.assertEqual({image["included"] for image in index["images"]}, {False})
        self.assertEqual({image["reason_code"] for image in index["images"]}, {"frame_not_available"})
        self.assertEqual((len(index["gaps"]), export["images"]), (len(index["images"]), []))
        self.assertLessEqual(index["size_bytes"], index["size_limit_bytes"])
        self.assertEqual(runtime.client.export("console-none")["reason_code"], "unknown_run")
        self.assertEqual(runtime.client.export("../etc")["reason_code"], "bad_request")

    def test_a_replay_only_runtime_exports_a_recorded_run_and_still_has_no_command_path(self):
        recorded = Runtime(self)
        run_id = recorded.run_cycle("replay-export-0001")["run_id"]
        recorded.shutdown()
        replay = Runtime(self, mode=daemon.REPLAY, data=recorded.data)
        export = replay.client.export(run_id)
        self.assertEqual(demo_contracts.validate("run_export_index", export["index"]), [])
        self.assertGreater(export["index"]["records"]["count"], 100)
        self.assertIsNone(replay.core.supervisor)


class ObservationsAndTelemetry(unittest.TestCase):
    def _observation_id(self, runtime):
        runtime.run_cycle("obs-0001")
        events = runtime.client.history_events(runtime.client.history()["runs"][0]["run_id"])["events"]
        return next(event["observation_id"] for event in events if event["kind"] == "observation.captured")

    def test_an_observation_is_an_allowlisted_projection(self):
        runtime = Runtime(self)
        observation_id = self._observation_id(runtime)
        record = runtime.client.observation(observation_id, "record")
        self.assertTrue(record["ok"])
        self.assertEqual(set(record["data"]["camera"]), {"role", "image_orientation", "width_px", "height_px"}, "never the camera's identity fingerprint")
        self.assertNotIn("raw_frame_path", record["data"])
        self.assertTrue(record["data"]["has_frame"])
        perception = runtime.client.observation(observation_id, "perception")
        self.assertEqual((perception["data"]["cube"]["detected"], perception["data"]["pad_by_template"]["pad_frame_valid"]), (True, True))
        text = json.dumps([record, perception])
        for forbidden in ("must-not-leave", "fingerprint", "frames/", "source_file", "notes"):
            self.assertNotIn(forbidden, text)
        for bad in ("../../etc/passwd", "..", ".hidden", "a/b", "", "x" * 80):
            self.assertEqual(runtime.client.observation(bad)["reason_code"], "bad_request", bad)
        self.assertEqual(runtime.client.observation("obs-20260101T000000Z-wrist-0001x")["reason_code"], "not_found")
        self.assertEqual(runtime.client.observation(observation_id, "everything")["reason_code"], "bad_request")

    def test_a_frame_is_a_jpeg_rendered_on_demand_and_cached(self):
        import cv2
        import numpy as np

        runtime = Runtime(self)
        observation_id = self._observation_id(runtime)
        raw = runtime.client.frame(observation_id, "raw", 320)
        self.assertEqual((raw["content_type"], raw["width_px"], raw["height_px"]), ("image/jpeg", 320, 240))
        data = base64.b64decode(raw["bytes_b64"])
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        self.assertEqual(cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).shape, (240, 320, 3))
        overlay = runtime.client.frame(observation_id, "overlay", 320)
        self.assertNotEqual(overlay["bytes_b64"], raw["bytes_b64"])
        with mock.patch.object(runtime.core._observations, "image", side_effect=AssertionError("a cached rendition must not be rendered again")):
            self.assertEqual(runtime.client.frame(observation_id, "raw", 320)["bytes_b64"], raw["bytes_b64"])
        self.assertEqual(runtime.client.frame(observation_id, "raw", 5)["width_px"], 64, "max_width is bounded below")
        self.assertEqual(runtime.client.frame(observation_id, "thumbnail")["reason_code"], "bad_request")
        self.assertEqual(list((runtime.data / "history").rglob("*.jpg")) + list((runtime.data / "history").rglob("*.png")), [], "nothing is written next to the evidence")

    def test_telemetry_is_latest_only_and_says_when_a_source_is_missing(self):
        runtime = Runtime(self)
        lines = []
        for line in runtime.client.telemetry(idle_timeout_s=3.0):
            lines.append(line)
            if sum(1 for item in lines if "tap" in item and item["tap"].get("available")) >= 3:
                break
        # One topic per camera: a reader asking for the front camera can never be handed the wrist record.
        topic = sources.preview_topic(sources.DEFAULT_ROLE)
        preview = next(item[topic] for item in lines if topic in item)
        self.assertEqual((preview["available"], preview["reason"], preview["role"]), (False, "preview_source_not_attached", "wrist"))
        self.assertEqual(set(preview), set(sources.unavailable_preview()), "an unattached preview has the shape of an attached one, with every number null")
        self.assertEqual([key for key, value in preview.items() if isinstance(value, (int, float)) and not isinstance(value, bool)], [],
                         "a slot with nothing behind it NEVER carries a measured number")
        taps = [item["tap"] for item in lines if "tap" in item and item["tap"].get("available")]
        self.assertEqual(demo_contracts.validate("common#/$defs/source_stamp", taps[0]["source_stamp"]), [])
        self.assertEqual(set(taps[0]), set(sources.unavailable_tap()), "the synthetic tap publishes the SAME record shape a real source does")
        self.assertEqual(taps[0]["clock_domains"], {"sample": "laptop_monotonic", "receive": "laptop_monotonic"},
                         "a synthetic sample is this laptop's own and says so: it never borrows the robot's clock domain")
        sequence = [tap["source_stamp"]["seq"] for tap in taps]
        self.assertEqual(sequence, sorted(sequence))
        self.assertEqual(runtime.client.snapshot()["link"]["state"], "connected")
        replay = Runtime(self, mode=daemon.REPLAY)
        first = next(iter(replay.client.telemetry(idle_timeout_s=1.0)))
        self.assertEqual((first["tap"]["available"], first["tap"]["reason"]), (False, "tap_source_not_attached"))


class ReplayOnly(unittest.TestCase):
    def test_replay_only_constructs_no_supervisor_and_has_no_command_path(self):
        recorded = Runtime(self)
        finished = recorded.run_cycle("replay-0001")
        recorded.shutdown()
        with mock.patch("demo_runtime.supervisor.Supervisor.__init__", side_effect=AssertionError("replay-only must never construct a Supervisor")):
            replay = Runtime(self, mode=daemon.REPLAY, data=recorded.data)
            self.assertIsNone(replay.core.supervisor)
            snapshot = replay.client.snapshot()
            self.assertEqual(demo_contracts.validate("snapshot", snapshot), [])
            self.assertEqual((snapshot["data_mode"], snapshot["last_outcome"]["run_id"], snapshot["owner"]["kind"]), ("replay", finished["run_id"], "free"))
            self.assertEqual({(item["state"], item["reason_code"]) for item in snapshot["capabilities"] if not item["planned"]}, {("disabled", "replay_only")})
            for answer in (replay.client.submit(replay.client.new_command("start_cycle", {"plan_only": True})), replay.client.stop(), replay.client.resolve("replay-0001", "no_controller_running")):
                self.assertEqual(answer, {"ok": False, "reason_code": "replay_only"})
            self.assertEqual(replay.client.result("replay-0001")["status"], "finished")
            self.assertEqual([run["run_id"] for run in replay.client.history()["runs"]], [finished["run_id"]])
            self.assertGreater(len(replay.client.history_events(finished["run_id"])["events"]), 100)
            self.assertGreater(len(list(replay.client.events(0, idle_timeout_s=0.5))), 100)
            self.assertEqual(replay.client.status()["supervisor"], False)
        journal = (recorded.data / daemon.JOURNAL_NAME).read_bytes()
        self.assertEqual(journal.count(b'"daemon_start"'), 1, "a replay-only runtime writes NOTHING: not even its own start")

    def test_a_replay_only_process_never_even_imports_the_supervisor_module(self):
        data = Path(tempfile.mkdtemp(prefix="kc-replay-"))
        self.addCleanup(shutil.rmtree, data, True)
        code = ("import sys\nfrom demo_runtime import daemon\n"
                f"core = daemon.RuntimeCore(data_dir={str(data / 'data')!r}, mode=daemon.REPLAY, cake_probe=lambda: {FIXED_CAKE!r})\n"
                "core.start()\nprint(core.supervisor is None, 'demo_runtime.supervisor' in sys.modules, 'demo_runtime.bundle' in sys.modules, core.handle('submit', {'op': 'submit'}, None))\n")
        result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(PACKAGE_DIR)}, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(result.stdout.strip(), "True False False {'ok': False, 'reason_code': 'replay_only'}", result.stderr[-400:])


class NothingPrivateLeavesTheProcess(unittest.TestCase):
    def test_no_rpc_response_carries_a_private_value(self):
        holder = Path(tempfile.mkdtemp(prefix="kc-private-"))
        self.addCleanup(shutil.rmtree, holder, True)
        runtime = Runtime(self, config=private_config(holder), step_s="0.02")
        responses = [runtime.client.hello(), runtime.client.snapshot()]
        complete = runtime.run_cycle("private-0000")  # a whole cycle, so that observations exist to be asked for
        started = runtime.client.submit(runtime.client.new_command("start_cycle", {"plan_only": False}, command_id="private-0001"))
        pid = wait_for(self, lambda: runtime.core.supervisor._active and runtime.core.supervisor._active.pid, "the child")
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode()
        self.assertIn(PRIVATE_ADDRESS, argv, "the private values really are in play: the controller gets them in its argv")
        self.assertIn(ROBOT_ID, argv)
        responses += [started, runtime.client.snapshot(), runtime.client.status(), runtime.client.stop()]
        wait_for(self, lambda: runtime.client.result("private-0001")["status"] == "finished", "the run to end")
        observation_id = next(event["observation_id"] for event in runtime.client.history_events(complete["run_id"])["events"] if event["kind"] == "observation.captured")
        responses += [runtime.client.result("private-0001"), runtime.client.history_events(complete["run_id"]), runtime.client.snapshot(), runtime.client.status(), runtime.client.history(), runtime.client.history_events(started["run_id"]),
                      runtime.client.observation(observation_id, "record"), runtime.client.observation(observation_id, "perception"), runtime.client.observation("nope"),
                      {**runtime.client.frame(observation_id, "raw", 64), "bytes_b64": "(image bytes)"}, runtime.client.resolve("private-0001", "no_controller_running"),
                      runtime.client.submit({"command_id": "bad", "type": ROBOT_ID}), runtime.client.result(ROBOT_ID), *list(runtime.client.events(0, idle_timeout_s=0.5)),
                      *[line for _, line in zip(range(3), runtime.client.telemetry(idle_timeout_s=1.0))]]
        self.assertGreater(len(responses), 100)
        text = json.dumps(responses)
        forbidden = {"the host address": PRIVATE_ADDRESS, "the robot id": ROBOT_ID, "the ssh target": SSH_TARGET, "the user name": USER_NAME, "a camera id": WRIST_ID,
                     "a private path component": PRIVATE_DIR, "the data directory": str(runtime.data), "the source checkout": str(REPO_ROOT), "the home directory": os.path.expanduser("~"),
                     "the temporary root": str(runtime.tmp), "argv": "--remote-ip", "the process table": "/proc/", "the child's pid": f'"pid": {pid}'}
        for what, value in forbidden.items():
            self.assertNotIn(value, text, f"{what} appears in an RPC response")
        manifest = json.loads(next((runtime.data / "history").glob("*/manifest.json")).read_text())
        self.assertNotIn(PRIVATE_ADDRESS, json.dumps(manifest))
        self.assertEqual((manifest["model"]["identity"], manifest["contract_version"], len(manifest["config_digest"])), ("google/owlv2-base-patch16-ensemble", "1", 64))

    def test_an_internal_error_is_reported_by_class_only(self):
        runtime = Runtime(self)
        with mock.patch.object(runtime.core, "snapshot", side_effect=RuntimeError(f"cannot read {runtime.data}/journal.jsonl for {ROBOT_ID}")):
            self.assertEqual(runtime.client.snapshot(), {"ok": False, "reason_code": "internal_error", "error_class": "RuntimeError"})

    def test_a_telemetry_record_leaves_through_the_sanitizer_as_well(self):
        # The defect this pins: the telemetry stream was the ONE outgoing path with no scrub at all. No test could have
        # caught it from the outside, because nothing publishes a tap or a preview today and the synthetic tap carries no
        # private value; the record is therefore published straight into the hub here.
        holder = Path(tempfile.mkdtemp(prefix="kc-private-"))
        self.addCleanup(shutil.rmtree, holder, True)
        runtime = Runtime(self, config=private_config(holder))
        picture = base64.b64encode(b"not really an image").decode("ascii")
        runtime.core.hub.publish("preview", {"role": "wrist", "seq": 1, "available": True, "bytes_b64": picture,
                                             "reason": f"opened by {SSH_TARGET} for {WRIST_ID} under {runtime.data}"})
        previews = []
        for line in runtime.client.telemetry(idle_timeout_s=3.0):
            if "preview" in line:
                previews.append(line["preview"])
                break
        self.assertEqual(len(previews), 1)
        text = json.dumps(previews)
        for what, value in {"the ssh target": SSH_TARGET, "the user name": USER_NAME, "the host address": PRIVATE_ADDRESS,
                            "a camera id": WRIST_ID, "the data directory": str(runtime.data)}.items():
            self.assertNotIn(value, text, f"{what} left the process on the telemetry stream")
        self.assertIn(picture, text, "image bytes are not text and must pass through untouched")

    def test_a_stream_cannot_be_opened_without_naming_its_sanitizer(self):
        # Structural, not a habit: neither stream helper has an identity default any more, so a new caller that forgets
        # the scrub fails at the call instead of quietly putting raw records on the wire.
        import inspect

        from demo_runtime import rpc

        for helper in (rpc.stream_events, rpc.stream_telemetry):
            parameter = inspect.signature(helper).parameters["sanitize"]
            self.assertIs(parameter.default, inspect.Parameter.empty, f"{helper.__name__} still has a default sanitizer")
            self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)


UNCERTAIN_RUN_ID = "console-20260921T000000Z-lost-000"


def runtime_with_an_uncertain_command(test):
    """A daemon started on a data directory whose journal holds a command a previous daemon never finished: the state
    every recovery path ends in, without killing a daemon to get there."""
    holder = Path(tempfile.mkdtemp(prefix="kc-uncertain-"))
    test.addCleanup(shutil.rmtree, holder, True)
    data = holder / PRIVATE_DIR / "data"
    data.mkdir(mode=0o700, parents=True)
    (data / "journal.jsonl").write_text(json.dumps({"rec": "command", "command_id": "lost-0001", "status": "accepted", "run_id": UNCERTAIN_RUN_ID, "scan_id": UNCERTAIN_RUN_ID,
                                                    "command_type": "start_cycle", "reason_code": None, "t_wall_utc": "2026-09-21T00:00:00.000Z"}) + "\n")
    return Runtime(test, data=data)


class UncertainCommandsOverTheSocket(unittest.TestCase):
    """What the browser is told about an interrupted command, and what a resolution through the socket actually does."""

    def test_the_snapshot_carries_the_evidence_and_the_question_and_resolve_answers_with_both(self):
        runtime = runtime_with_an_uncertain_command(self)
        self.assertEqual(runtime.client.result("lost-0001")["status"], "uncertain")
        snapshot = runtime.client.snapshot()
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [], "the whole answer validates: the optional keys are inside the contract object now")
        contract_part = {key: value for key, value in snapshot.items() if key not in daemon.ADDITIVE_SNAPSHOT_KEYS}
        self.assertEqual(demo_contracts.validate("snapshot", contract_part), [], "the contract object is untouched; the key beside it is additive")
        detail = snapshot["unresolved_command"]
        self.assertEqual((detail["command_id"], detail["classification"], detail["block_reason_code"]), ("lost-0001", "never_started", "owner_unresolved"))
        self.assertEqual(detail["findings"], ["no_controller_running", "controller_finished", "controller_stopped_by_operator"])
        self.assertIn("confirm", detail["question"], "the browser can say what to check without a terminal")
        self.assertEqual(detail["evidence"]["process"], {"readable": True, "alive": False, "found_by": None, "pid": None, "candidates": 0,
                                                         "recorded_pid_present": False, "recorded_identity_matches": None})
        blocked = runtime.client.submit(runtime.client.new_command("start_cycle", {"plan_only": False}, command_id="blocked-0001"))
        self.assertEqual((blocked["status"], blocked["reason_code"]), ("rejected", "owner_unresolved"))
        answer = runtime.client.resolve("lost-0001", "no_controller_running", "looked at the bench: the arm is parked")
        self.assertEqual((answer["ok"], answer["cleared"], answer["cleared_by"], answer["classification"]), (True, True, "machine_evidence", "never_started"))
        self.assertEqual(answer["attestation"]["note"], "looked at the bench: the arm is parked")
        after = runtime.client.snapshot()
        self.assertNotIn("unresolved_command", after, "nothing is unresolved any more, so nothing is asked")
        self.assertEqual(after["owner"]["kind"], "free")
        self.assertEqual(runtime.client.status()["unresolved"], [])
        self.assertEqual(runtime.core.supervisor.launches, 0, "resolving never runs the ambiguous command")

    def test_a_note_that_is_not_plain_text_is_refused_over_the_socket_too(self):
        runtime = runtime_with_an_uncertain_command(self)
        self.assertEqual(runtime.client.resolve("lost-0001", "no_controller_running", "two\nlines")["reason_code"], "note_not_allowed")
        self.assertEqual(runtime.client.snapshot()["owner"]["kind"], "unresolved")

    def test_the_preview_op_says_there_is_no_picture_rather_than_inventing_one(self):
        runtime = Runtime(self)
        answer = runtime.client.preview()
        self.assertEqual((answer["ok"], answer["reason_code"], answer["available"]), (False, "preview_source_not_attached", False))
        self.assertNotIn("bytes_b64", answer)


class TheForwardsAndTheLeaseOverTheSocket(unittest.TestCase):
    """The daemon's side of the two things that let a browser-started cycle reach a host with its boundary on: the ssh
    forwards it owns, and the command lease it holds. Nothing here opens a forward (test_forwards.py does that against a
    stub ssh) and nothing here talks to a host: the forwards are a fake and the lease client is a fake."""

    class FakeForwards:
        def __init__(self, control_endpoint=None):
            self.control_endpoint, self.starts, self.stops, self.checks = control_endpoint, 0, 0, 0
            self.state = "running" if control_endpoint else "not_configured"

        def start(self):
            self.starts += 1
            return {purpose: self.control_endpoint is not None for purpose in ("control", "tap", "preview")}

        def stop(self):
            self.stops += 1

        def check(self):
            self.checks += 1
            return {purpose: self.state for purpose in ("control", "tap", "preview")}

        def endpoint(self, purpose):
            return self.control_endpoint if purpose == "control" else None

        def report(self):
            return {purpose: {"purpose": purpose, "configured": bool(self.control_endpoint), "state": self.state, "reason": None,
                              "starts": self.starts, "deaths": 0, "forced_stops": 0, "exit_status": None, "child": bool(self.control_endpoint)}
                    for purpose in ("control", "tap", "preview")}

    class LeaseThatClaimsToHoldOne:
        """A client that believes it holds a lease. The DAEMON took none, so no snapshot may show one."""

        ttl_s, lost_reason = 180.0, None
        generation, host_session = 9, "770011223344556"
        held = True

        def __init__(self):
            self.closes = 0

        def snapshot_lease(self):
            return {"host_session": self.host_session, "generation": self.generation, "expires_in_ms": 1000.0}

        def last_reply_age_ms(self):
            return 5.0

        def close(self):
            self.closes += 1

    def test_the_daemon_opens_the_forwards_and_builds_its_lease_client_on_the_control_one(self):
        from demo_runtime.lease_client import LeaseClient

        holder = Path(tempfile.mkdtemp(prefix="kc-fwd-"))
        self.addCleanup(shutil.rmtree, holder, True)
        endpoint = "tcp://127.0.0.1:5567"
        tunnels = self.FakeForwards(endpoint)
        runtime = Runtime(self, mode=daemon.LIVE, config=private_config(holder), forwards=tunnels)
        self.assertEqual(tunnels.starts, 1, "the forwards are opened before anything asks for a lease")
        self.assertIsInstance(runtime.core.lease, LeaseClient)
        self.assertEqual(runtime.core.lease._endpoint, endpoint, "the lease client speaks through the control forward, not to a machine")
        self.assertIs(runtime.core.supervisor._lease, runtime.core.lease, "and the supervisor was given that same client")
        status = runtime.client.status()
        self.assertEqual(status["forwards"]["control"]["state"], "running")
        self.assertGreaterEqual(tunnels.checks, 1, "asking for the status LOOKS at the children, which is how a death is noticed")
        self.assertEqual((status["lease"]["held"], status["lease"]["attached"], status["lease"]["required"]), (False, True, False))
        self.assertNotIn(SSH_TARGET, json.dumps(status), "a forward report names no target")
        self.assertNotIn(PRIVATE_ADDRESS, json.dumps(status))
        tunnels.state = "dead"  # the ssh child exited; nothing restarts it and the next status says so
        self.assertEqual(runtime.client.status()["forwards"]["tap"]["state"], "dead")
        self.assertEqual(tunnels.starts, 1, "a dead forward is reported, never retried into a loop")
        runtime.shutdown()
        self.assertEqual(tunnels.stops, 1, "the forwards are torn down with the daemon")

    def test_a_configuration_without_a_tap_forwards_nothing_and_has_no_lease_client(self):
        holder = Path(tempfile.mkdtemp(prefix="kc-fwd-off-"))
        self.addCleanup(shutil.rmtree, holder, True)
        runtime = Runtime(self, mode=daemon.LIVE, config=private_config(holder))
        self.assertFalse(runtime.core.forwards.configured, "the tap is the switch, and this configuration has none")
        self.assertIsNone(runtime.core.lease)
        status = runtime.client.status()
        self.assertEqual([one["state"] for one in status["forwards"].values()], ["not_configured"] * len(forwards.PURPOSES))
        self.assertIsNone(status["lease"], "a runtime with nothing to say about a lease says nothing")
        self.assertIsNone(runtime.client.snapshot()["owner"]["lease"])
        self.assertNotIn("lease", runtime.client.snapshot(), "the additive key is absent rather than an empty object")

    def test_a_snapshot_never_shows_a_lease_the_daemon_does_not_hold(self):
        lease = self.LeaseThatClaimsToHoldOne()
        runtime = Runtime(self, lease=lease)
        snapshot = runtime.client.snapshot()
        self.assertIsNone(snapshot["owner"]["lease"], "the client believes it holds one; the DAEMON took none")
        self.assertEqual(snapshot["lease"]["held"], False)
        self.assertEqual(snapshot["lease"]["reason"], "lease_not_required")
        for field in ("generation", "host_session", "expires_in_ms", "run_id", "adopted"):
            self.assertIsNone(snapshot["lease"][field], f"{field} is not shown beside a lease that is not held")
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [], "the whole answer validates, lease included: nothing is stripped before checking it")
        contract_part = {key: value for key, value in snapshot.items() if key not in daemon.ADDITIVE_SNAPSHOT_KEYS}
        self.assertEqual(demo_contracts.validate("snapshot", contract_part), [], "the contract object is untouched by the additive key")
        self.assertIn("lease", daemon.ADDITIVE_SNAPSHOT_KEYS, "the runtime names it among the contract's OPTIONAL keys, and a contracts test keeps the two equal")
        runtime.shutdown()
        self.assertEqual(lease.closes, 1, "the socket is closed on shutdown; close() releases nothing")

    def test_the_lease_view_shows_only_the_allowlisted_fields(self):
        lease = self.LeaseThatClaimsToHoldOne()
        runtime = Runtime(self, lease=lease)
        self.assertEqual(set(runtime.client.snapshot()["lease"]), set(read_models.LEASE_VIEW_KEYS))
        self.assertNotIn("owner_label", json.dumps(runtime.client.status()["lease"]))

    def test_the_command_line_prints_the_forwards_and_the_lease(self):
        lease = self.LeaseThatClaimsToHoldOne()
        runtime = Runtime(self, lease=lease)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--socket", str(runtime.data / daemon.SOCKET_NAME), "status"])
        shown = json.loads(out.getvalue())
        self.assertEqual((code, shown["lease"]["held"]), (0, False))
        self.assertIn("forwards", shown)
        self.assertEqual(shown["execution"]["backend"], "baseline_normal_host", "the status SAYS which path commands take, instead of leaving it to be inferred")


class WhichBackendAndWhatOwnsTheRobot(unittest.TestCase):
    """The runtime states its execution backend and the ownership that backend really has, over the socket and in the
    view a console reads. Nothing here touches a robot: every runtime below is synthetic or a live one with hardware
    commands not enabled, and no ssh runs in either."""

    def config_holder(self, prefix):
        holder = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, holder, True)
        return holder

    def test_the_snapshot_carries_the_backend_and_says_what_owns_the_robot_under_it(self):
        runtime = Runtime(self, mode=daemon.LIVE, config=private_config(self.config_holder("kc-backend-base-")))
        snapshot = runtime.client.snapshot()
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [], "the whole answer validates: the key is inside the contract object")
        contract_part = {key: value for key, value in snapshot.items() if key not in daemon.ADDITIVE_SNAPSHOT_KEYS}
        self.assertEqual(demo_contracts.validate("snapshot", contract_part), [], "the contract object is untouched by the additive key")
        self.assertIn("execution", daemon.ADDITIVE_SNAPSHOT_KEYS, "the runtime names it among the contract's OPTIONAL keys, and a contracts test keeps the two equal")
        execution = snapshot["execution"]
        self.assertEqual(set(execution), set(read_models.EXECUTION_VIEW_KEYS))
        self.assertEqual(("baseline_normal_host", "runtime_single_run_slot", False),
                         (execution["backend"], execution["ownership_basis"], execution["robot_side_enforcement"]))
        self.assertIn("single run slot", execution["detail"])
        self.assertNotIn("lease", execution["detail"], "the baseline claims no lease anywhere, not even in words")
        self.assertEqual(runtime.client.status()["execution"], execution, "the status and the snapshot say the same thing")
        for private in (SSH_TARGET, PRIVATE_ADDRESS, ROBOT_ID, USER_NAME):
            self.assertNotIn(private, json.dumps(execution))

    def test_a_configuration_that_asks_for_the_deferred_backend_says_the_other_thing(self):
        holder = self.config_holder("kc-backend-bound-")
        runtime = Runtime(self, mode=daemon.LIVE, config=private_config(holder, execution_backend="boundary_lease_host"))
        snapshot = runtime.client.snapshot()
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [], "what the deferred backend says validates too, its sentence included")
        execution = snapshot["execution"]
        self.assertEqual(("boundary_lease_host", "host_command_lease", None),
                         (execution["backend"], execution["ownership_basis"], execution["robot_side_enforcement"]),
                         "no boundary has answered anything, so nothing on the robot is claimed to be enforcing anything")

    def test_a_replay_runtime_has_no_command_path_so_it_states_no_backend(self):
        runtime = Runtime(self, mode=daemon.REPLAY)
        self.assertIsNone(runtime.core.execution_view())
        self.assertNotIn("execution", runtime.client.snapshot(), "the additive key is absent rather than an empty object")
        self.assertIsNone(runtime.client.status()["execution"])

    def test_the_baseline_arms_the_host_check_of_the_proven_launcher_and_the_deferred_backend_does_not(self):
        """The preflight the supervisor runs for a SUBMIT carries the probe that asks the robot computer whether the
        motor host is up, which is what bin/laptop/run-cycle.sh establishes before it commands anything. It is bound
        here and nowhere else, so this is where it can be seen. Nothing is submitted in this test and no ssh runs."""
        from demo_runtime.preflight import probe_motor_host, run_preflight

        baseline = Runtime(self, mode=daemon.LIVE, config=private_config(self.config_holder("kc-armed-")))
        self.assertEqual(baseline.core.supervisor._preflight.func, run_preflight)
        self.assertEqual(baseline.core.supervisor._preflight.keywords, {"motor_host": probe_motor_host})
        deferred = Runtime(self, mode=daemon.LIVE, config=private_config(self.config_holder("kc-unarmed-"), execution_backend="boundary_lease_host"))
        self.assertIs(deferred.core.supervisor._preflight, run_preflight, "that host is not the one the boundary backend talks to")

    def test_a_snapshot_never_asks_the_robot_computer_however_often_it_is_taken(self):
        """A snapshot is taken every few seconds by every open console. The probe is switched off for it, so a robot
        computer that is off cannot stall one: the submit's report is where the question is asked and answered."""
        asked = []
        runtime = Runtime(self, mode=daemon.LIVE, config=private_config(self.config_holder("kc-quiet-")), hardware_enabled=True)
        runtime.core.supervisor._preflight = functools.partial(runtime.core.supervisor._preflight, motor_host=lambda target: asked.append(target) or "running_normal")
        for _ in range(3):
            runtime.client.snapshot()
            runtime.core.supervisor._preflight_cache = None  # every snapshot builds a fresh report, not one from the cache
        self.assertEqual([], asked, "no snapshot reached out to the robot computer")


class TheBackgroundReadingOfTheRobot(unittest.TestCase):
    """What a snapshot says about the robot comes from a reader that runs off the request path. This is where it is
    decided whether that reader exists at all, and where the daemon starts and stops it. Every runtime here is given a
    reader that asks nothing, so nothing in this class opens an ssh to anything."""

    def config_holder(self, prefix):
        holder = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, holder, True)
        return holder

    def built(self, **options):
        """(the runtime, the targets a reader was built for). A target is appended only when the daemon decided it
        wanted a reading, so an empty list is the daemon deciding not to ask the robot computer anything."""
        targets = []
        runtime = Runtime(self, readiness=lambda target: targets.append(target) or SilentWatch(), **options)
        return runtime, targets

    def test_the_robot_is_asked_only_on_the_path_that_asks_it_for_a_submit_too(self):
        cases = {
            "the baseline backend with hardware commands enabled": ({"mode": daemon.LIVE, "config": private_config(self.config_holder("kc-read-on-")), "hardware_enabled": True}, True),
            "hardware commands not enabled": ({"mode": daemon.LIVE, "config": private_config(self.config_holder("kc-read-off-"))}, False),
            "the deferred boundary backend": ({"mode": daemon.LIVE, "hardware_enabled": True,
                                               "config": private_config(self.config_holder("kc-read-bound-"), execution_backend="boundary_lease_host")}, False),
            "a synthetic runtime": ({"mode": daemon.SYNTHETIC, "config": private_config(self.config_holder("kc-read-synth-"))}, False),
            "a replay runtime": ({"mode": daemon.REPLAY}, False),
        }
        for what, (options, wanted) in cases.items():
            with self.subTest(what=what):
                runtime, targets = self.built(**options)
                self.assertEqual(wanted, runtime.core.readiness is not None, what)
                self.assertEqual([SSH_TARGET] if wanted else [], targets, "the reader is built for the configured target, or not built at all")

    def test_an_ssh_target_that_cannot_be_handed_to_ssh_is_not_asked_and_is_not_refused_twice(self):
        holder = self.config_holder("kc-read-badtarget-")
        runtime, targets = self.built(mode=daemon.LIVE, config=private_config(holder, pi_ssh_target="-oProxyCommand=something"), hardware_enabled=True)
        self.assertEqual([], targets, "nothing that cannot be handed to ssh is handed to ssh")
        self.assertIsNone(runtime.core.readiness)
        start = next(item for item in runtime.client.snapshot()["capabilities"] if item["id"] == "start_cycle")
        self.assertNotIn(start["reason_code"], read_models.ROBOT_BLOCK_STATES,
                         "the preflight's own checks say what is wrong with this configuration; no reading repeats it as a fact about the robot")

    def test_the_daemon_starts_the_reader_and_stops_it_when_it_ends(self):
        runtime, _targets = self.built(mode=daemon.LIVE, config=private_config(self.config_holder("kc-read-life-")), hardware_enabled=True)
        watch = runtime.core.readiness
        self.assertEqual((1, 0), (watch.starts, watch.stops), "it is started with the runtime and not before its supervisor exists")
        runtime.shutdown()
        self.assertEqual(1, watch.stops, "and it does not outlive the runtime that owns it")

    def test_a_snapshot_shows_the_reading_as_the_reason_a_command_is_off_and_the_status_shows_its_age(self):
        runtime, _targets = self.built(mode=daemon.LIVE, config=private_config(self.config_holder("kc-read-says-")), hardware_enabled=True)
        # These fixtures name calibration files that do not exist here, and the local checks are reported first, which is
        # the order the runtime keeps everywhere. What this test is about is the check AFTER them, so the local report is
        # replaced by one that passes and the robot is left as the only thing that can still be wrong.
        runtime.core.supervisor._preflight = lambda config, action, **options: preflight.PreflightReport(ok=True, checks=(), action_kind=None, config_digest="0" * 64)
        runtime.core.readiness.state = "not_running"
        start = next(item for item in runtime.client.snapshot()["capabilities"] if item["id"] == "start_cycle")
        self.assertEqual(("blocked", "motor_host_not_running", "robot"), (start["state"], start["reason_code"], start["dependency"]),
                         "an enabled software capability is not a ready robot, and the control says which robot fact is stopping it")
        self.assertIn("is not running on the robot computer", start["reason"])
        status = runtime.client.status()
        self.assertEqual(("not_running", False, "motor_host_not_running"), (status["robot_readiness"]["state"], status["robot_readiness"]["ready"], status["robot_readiness"]["reason_code"]))
        self.assertEqual(0.0, status["robot_readiness"]["age_ms"], "the age of the reading travels with it, so staleness can be seen")
        for private in (SSH_TARGET, PRIVATE_ADDRESS, ROBOT_ID, USER_NAME):
            self.assertNotIn(private, json.dumps(status["robot_readiness"]))

    def test_a_runtime_that_reads_nothing_about_a_robot_says_so_instead_of_claiming_readiness(self):
        runtime, _targets = self.built(mode=daemon.SYNTHETIC)
        self.assertIsNone(runtime.client.status()["robot_readiness"])
        states = {item["state"] for item in runtime.client.snapshot()["capabilities"] if item["id"] in read_models.COMMAND_CAPABILITIES}
        self.assertEqual({"enabled"}, states, "there is no robot to be ready, so no robot holds a synthetic command back")


class CommandLine(unittest.TestCase):
    def _cli(self, runtime, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = cli.main(["--data-dir", str(runtime.data), *arguments])
        return status, out.getvalue(), err.getvalue()

    def test_the_command_line_shares_the_one_command_authority(self):
        runtime = Runtime(self, step_s="0.04")
        status, out, _ = self._cli(runtime, "run", "cycle")
        self.assertEqual(status, 0)
        command_id = out.splitlines()[0].split()[1]
        self.assertTrue(out.startswith("command_id "), "the handle of a lost answer is printed BEFORE the command is sent")
        self.assertEqual(json.loads(out.split("\n", 1)[1])["status"], "running")
        rejected_status, rejected, _ = self._cli(runtime, "run", "observe", "--holding")
        self.assertEqual((rejected_status, json.loads(rejected.split("\n", 1)[1])["reason_code"]), (1, "run_active"), "a rejected command exits 1")
        browser = runtime.client.submit(runtime.client.new_command("start_cycle", {"plan_only": False}, command_id="from-the-console"))
        self.assertEqual(browser["reason_code"], "run_active", "a cycle started from the command line blocks Start in the console")
        self.assertEqual(self._cli(runtime, "result", command_id)[0], 0)
        self.assertEqual(self._cli(runtime, "stop")[0], 0)
        wait_for(self, lambda: runtime.client.result(command_id)["status"] == "finished", "the stopped run")
        status, out, _ = self._cli(runtime, "status")
        shown = json.loads(out)
        self.assertEqual((status, shown["owner"]["kind"], shown["last_outcome"]["result"]["outcome"], shown["counters"]["controller_launches"]), (0, "free", "stopped", 1))
        self.assertEqual(self._cli(runtime, "result", "never-sent")[0], 1)
        self.assertEqual(self._cli(runtime, "resolve", command_id, "no_controller_running")[0], 1, "a finished command is not uncertain")

    def test_resolve_from_the_command_line_shows_the_evidence_and_status_shows_the_question(self):
        runtime = runtime_with_an_uncertain_command(self)
        status, out, _ = self._cli(runtime, "status")
        shown = json.loads(out)
        self.assertEqual((status, shown["unresolved"], shown["unresolved_command"]["classification"]), (0, ["lost-0001"], "never_started"))
        self.assertEqual(shown["link"], {"state": "unknown", "source_stamp": None, "receive_age_ms": None},
                         "the synthetic tap runs only while somebody reads it, so with no reader NOTHING is known about a link")
        self.assertIsNone(shown["sources"], "and a synthetic runtime has no source supervisor: that is said, not faked")
        code, out, _ = self._cli(runtime, "resolve", "lost-0001", "no_controller_running", "--note", "the arm is parked")
        answer = json.loads(out)
        self.assertEqual((code, answer["cleared"], answer["classification"]), (0, True, "never_started"))
        self.assertEqual(answer["evidence"]["run_files"]["terminal_record"], False)
        self.assertEqual(self._cli(runtime, "run", "cycle")[0], 0, "and commands are accepted again")

    def test_the_command_line_says_when_the_runtime_is_not_there(self):
        holder = Path(tempfile.mkdtemp(prefix="kc-cli-"))
        self.addCleanup(shutil.rmtree, holder, True)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = cli.main(["--socket", str(holder / "nothing.sock"), "status"])
        self.assertEqual((status, "cannot be reached" in err.getvalue(), str(holder) in err.getvalue()), (2, True, False))


if __name__ == "__main__":
    unittest.main()
