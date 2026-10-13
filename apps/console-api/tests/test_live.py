#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# apps/console-api/tests/test_live.py: the API as a real process, in front of a real runtime daemon in --synthetic mode.
# Nothing here is stubbed on either side: commands go over the runtime's Unix socket, the only child process anywhere is
# packages/demo-runtime/tests/fake_controller.py, and no camera, serial port, network host or robot is touched.
# This is where the claims that span both processes are proved: the API can be killed in the middle of a run without the
# run noticing, a duplicate command_id starts nothing twice, and the runtime (never the API) refuses a blocked command.
# Run directly: python3 apps/console-api/tests/test_live.py
import http.client
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import APP_DIR, REPO_ROOT, free_port  # noqa: E402

READY_S = 60.0
CYCLE_STEP_S = "0.3"  # 26 states: a cycle lasts long enough to kill and restart the API in the middle of it
FINGERPRINT_TRAP = "synthetic-fingerprint" + "-must-not-leave"
PATH_TRAP = "synthetic-path" + "-must-not-leave"


def child_environment(**extra) -> dict:
    environment = {key: value for key, value in os.environ.items() if key not in ("KC_RUNTIME_CONFIG", "KC_FAKE_SCENARIO", "PYTHONPATH")}
    environment["PYTHONPATH"] = os.pathsep.join([str(APP_DIR), str(REPO_ROOT / "packages" / "demo-runtime"), str(REPO_ROOT / "packages" / "demo-contracts" / "python")])
    environment["PYTHONUNBUFFERED"] = "1"
    environment.update({key: str(value) for key, value in extra.items()})
    return environment


class Process:
    """A child process whose stdout is read by a thread, so waiting for its ready line never deadlocks on a full pipe."""

    def __init__(self, argv, environment):
        self.lines: list = []
        self.proc = subprocess.Popen(argv, cwd=str(REPO_ROOT), env=environment, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, name="child-output", daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def wait_for(self, needle: str, timeout: float = READY_S) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in list(self.lines):
                if needle in line:
                    return line
            if self.proc.poll() is not None and time.monotonic() > deadline - timeout + 5.0:
                raise AssertionError(f"the process ended before saying {needle!r}: {self.lines[-5:]}")
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {needle!r}: {self.lines[-5:]}")

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        self._close()

    def terminate(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.kill()
        self._close()

    def _close(self) -> None:
        try:
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        except OSError:
            pass


class Stack(unittest.TestCase):
    """One runtime daemon and one API process, both real, on a private data directory outside the repository."""

    replay = False
    daemon_env: dict = {}

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="kc-console-api-live-")
        self.addCleanup(self._remove_data_dir)
        self.launch_log = os.path.join(self.data_dir, "launches.txt")
        self.credential_file = os.path.join(self.data_dir, "console-credential")
        mode = "--replay-only" if self.replay else "--synthetic"
        self.daemon = Process([sys.executable, "-m", "demo_runtime.daemon", "--data-dir", self.data_dir, mode],
                              child_environment(KC_FAKE_STEP_S=CYCLE_STEP_S, KC_FAKE_LAUNCH_LOG=self.launch_log, **self.daemon_env))
        self.addCleanup(self.daemon.terminate)
        self.daemon.wait_for("demo-runtime: ready")
        self.port = free_port()
        self.api = self.start_api()

    def _remove_data_dir(self) -> None:
        """The runtime pins each run bundle read-only, so a plain remove would leave the tree behind."""
        from demo_runtime.bundle import make_tree_writable

        try:
            make_tree_writable(Path(self.data_dir))
        except OSError:
            pass
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def start_api(self) -> Process:
        api = Process([sys.executable, "-m", "console_api", "--runtime-socket", os.path.join(self.data_dir, "runtime.sock"),
                       "--port", str(self.port), "--credential-file", self.credential_file], child_environment())
        self.addCleanup(api.terminate)
        api.wait_for("console-api: ready")
        return api

    # -- talking to the API
    @property
    def credential(self) -> str:
        return Path(self.credential_file).read_text(encoding="ascii").strip()

    def call(self, method: str, path: str, body=None, timeout: float = 30.0, credential=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        headers = {}
        if body is not None:
            headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}",
                       "X-Console-Request": "1", "X-Console-Credential": credential or self.credential}
        try:
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None), dict(response.getheaders())
        finally:
            connection.close()

    def open_events(self, since: int, timeout: float = 30.0):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        connection.request("GET", f"/api/events?since={since}")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.addCleanup(connection.close)
        return connection, response

    @staticmethod
    def next_frames(response, count: int) -> list:
        """SSE frames. Every data frame is unnamed; a heartbeat is a comment and is skipped here."""
        frames, current = [], {}
        while len(frames) < count:
            line = response.fp.readline()
            if not line:
                break
            text = line.decode("ascii").rstrip("\r\n")
            if text.startswith(":"):
                continue
            if text == "":
                if "data" in current:
                    frames.append(current)
                current = {}
                continue
            key, _, value = text.partition(":")
            value = value.lstrip()
            if key == "data":
                current["data"] = json.loads(value)
            elif key == "id":
                current["id"] = value
        return frames

    def command(self, command_type: str, args: dict, command_id=None) -> dict:
        return {"contract_version": "1", "command_id": command_id or uuid.uuid4().hex[:20], "type": command_type, "args": args,
                "client": {"session_id": "live-test-session", "contract_version": "1"}}

    def snapshot(self) -> dict:
        status, body, _ = self.call("GET", "/api/snapshot")
        self.assertEqual(status, 200)
        return body

    def wait_until(self, predicate, what: str, timeout: float = 90.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.1)
        self.fail(f"timed out waiting for {what}")

    def launch_count(self) -> int:
        try:
            return len([line for line in Path(self.launch_log).read_text(encoding="ascii").splitlines() if line.strip()])
        except OSError:
            return 0


class TestLiveStack(Stack):
    def test_the_handshake_reaches_the_real_runtime(self):
        status, body, _ = self.call("GET", "/api/hello?contract_version=1")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["runtime"]["reachable"])
        self.assertEqual(body["runtime"]["data_mode"], "synthetic")
        self.assertTrue(body["commands_enabled"])
        self.assertTrue(body["runtime"]["session"].startswith("rt-"))

    def test_a_cycle_runs_end_to_end_and_lands_in_the_history(self):
        command = self.command("start_cycle", {"plan_only": False})
        status, result, _ = self.call("POST", "/api/commands", command)
        self.assertEqual(status, 200)
        self.assertIn(result["status"], ("accepted", "spawning", "running"))
        run_id = result["run_id"]

        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the cycle to finish", timeout=120.0)
        status, history, _ = self.call("GET", "/api/history")
        entry = next(run for run in history["runs"] if run["run_id"] == run_id)
        self.assertEqual(entry["outcome"], "placed_new")
        self.assertEqual(entry["exit_status"], 0)
        self.assertEqual(self.launch_count(), 1)

        status, events, _ = self.call("GET", f"/api/history/{run_id}/events")
        self.assertEqual(status, 200)
        kinds = [event["kind"] for event in events["events"]]
        self.assertIn("run.started", kinds)
        self.assertIn("run.finished", kinds)

    def test_a_blocked_capability_is_refused_by_the_runtime_and_the_api_does_not_soften_it(self):
        start = self.command("start_cycle", {"plan_only": False})
        self.call("POST", "/api/commands", start)
        # The run is active from the moment it is ACCEPTED, which is before the controller exists: a stop in that window
        # is recorded as stopped_before_spawn and nothing is ever started. The launch log is what says the controller is
        # really running, so this waits for that and not for the run to be active.
        self.wait_until(lambda: self.launch_count() == 1, "the controller process to start")
        self.assertIsNotNone(self.snapshot()["active_run"])

        snapshot = self.snapshot()
        states = {item["id"]: (item["state"], item["reason_code"]) for item in snapshot["capabilities"]}
        self.assertEqual(states["observe"], ("blocked", "run_active"))
        self.assertEqual(states["stop"], ("enabled", None))

        status, result, _ = self.call("POST", "/api/commands", self.command("observe", {"holding": False}))
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason_code"], "run_active")

        status, stopped, _ = self.call("POST", "/api/stop", {})
        self.assertEqual(status, 200)
        self.assertTrue(stopped["ok"])
        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the run to end after stop")
        self.assertEqual(self.launch_count(), 1, "only the first command ever started a controller")

    def test_the_same_command_id_starts_the_controller_once(self):
        command = self.command("start_cycle", {"plan_only": False})
        first = self.call("POST", "/api/commands", command)[1]
        second = self.call("POST", "/api/commands", command)[1]
        third = self.call("POST", "/api/commands", command)[1]
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(second, third)
        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the cycle to finish", timeout=120.0)
        self.assertEqual(self.launch_count(), 1, "a repeated command_id must never spawn a second controller")

        status, looked_up, _ = self.call("GET", f"/api/commands/{command['command_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(looked_up["status"], "finished")

    def test_an_observation_loses_its_identity_and_its_paths_before_it_reaches_a_client(self):
        self.call("POST", "/api/commands", self.command("observe", {"holding": False}))
        observation_id = self.wait_until(self._first_observation_id, "an observation to be captured")

        status, record, headers = self.call("GET", f"/api/observations/{observation_id}/record")
        self.assertEqual(status, 200)
        text = json.dumps(record)
        self.assertNotIn(FINGERPRINT_TRAP, text)
        self.assertNotIn("identity_fingerprint", text)
        self.assertNotIn("raw_frame_path", text)
        self.assertNotIn("frames/", text)
        self.assertEqual(record["data"]["camera"]["role"], "wrist")
        self.assertIn("immutable", headers["Cache-Control"])

        status, perception, _ = self.call("GET", f"/api/observations/{observation_id}/perception")
        self.assertEqual(status, 200)
        self.assertNotIn(PATH_TRAP, json.dumps(perception))
        self.assertNotIn("source_file", json.dumps(perception))

    def test_a_decision_frame_is_served_as_an_image(self):
        self.call("POST", "/api/commands", self.command("observe", {"holding": False}))
        observation_id = self.wait_until(self._first_observation_id, "an observation to be captured")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30.0)
        self.addCleanup(connection.close)
        connection.request("GET", f"/api/observations/{observation_id}/frame?what=overlay&w=200")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "image/jpeg")
        body = response.read()
        self.assertTrue(body.startswith(b"\xff\xd8\xff"), "not a JPEG")
        self.assertGreater(len(body), 200)

    def _first_observation_id(self):
        status, history, _ = self.call("GET", "/api/history")
        for run in history["runs"]:
            status, events, _ = self.call("GET", f"/api/history/{run['run_id']}/events")
            for event in events.get("events", []):
                if event["kind"] == "observation.captured" and event.get("observation_id"):
                    return event["observation_id"]
        return None

    def test_telemetry_reports_what_the_runtime_actually_has(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30.0)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/telemetry")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        frames = self.next_frames(response, 2)
        named = {key: value for frame in frames for key, value in frame["data"].items()}
        # One topic per camera. The role is in the TOPIC, so a reader asking for the front camera can never be
        # handed the wrist record, and an unconfigured camera still answers honestly under its own name.
        self.assertIn("preview:wrist", named)
        self.assertFalse(named["preview:wrist"]["available"])
        self.assertEqual(named["preview:wrist"]["reason"], "preview_source_not_attached")
        self.assertEqual(named["preview:wrist"]["role"], "wrist")

    def test_the_preview_endpoint_says_there_is_no_source_instead_of_inventing_one(self):
        status, body, headers = self.call("GET", "/api/preview.mjpg?role=wrist")
        self.assertEqual(status, 503)
        self.assertFalse(body["available"])
        self.assertEqual(body["reason_code"], "preview_source_not_attached")
        self.assertNotIn("multipart", headers.get("Content-Type", ""))


class TestApiRestartDoesNotTouchTheRun(Stack):
    def test_the_run_continues_and_a_new_client_resumes_from_the_snapshot_cursor(self):
        command = self.command("start_cycle", {"plan_only": False})
        status, result, _ = self.call("POST", "/api/commands", command)
        self.assertEqual(status, 200)
        run_id = result["run_id"]

        connection, response = self.open_events(0)
        frames = self.next_frames(response, 6)
        seen_before = max(int(frame["id"]) for frame in frames if frame.get("id"))
        self.assertGreater(seen_before, 0)

        killed_at = time.monotonic()
        self.api.kill()
        connection.close()
        self.assertIsNotNone(self.api.proc.returncode)

        # The controller is the RUNTIME's child. Nothing about the run depends on this process being alive.
        self.api = self.start_api()
        self.assertLess(time.monotonic() - killed_at, 60.0)

        snapshot = self.snapshot()
        self.assertIsNotNone(snapshot["active_run"], "the run did not survive the API being killed")
        self.assertEqual(snapshot["active_run"]["run_id"], run_id)
        self.assertEqual(snapshot["owner"]["kind"], "autonomous")
        self.assertGreaterEqual(snapshot["seq_cursor"], seen_before)

        # A new client resumes from exactly where the old one stopped: no gap, and no repetition.
        _connection, resumed = self.open_events(seen_before)
        frames = self.next_frames(resumed, 4)
        events = [frame for frame in frames if "seq" in frame["data"]]
        self.assertTrue(events, "no event arrived after the restart")
        self.assertEqual(int(events[0]["id"]), seen_before + 1)
        self.assertEqual([frame for frame in frames if "resync" in frame["data"]], [], "there was no gap to report")

        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the cycle to finish", timeout=120.0)
        status, history, _ = self.call("GET", "/api/history")
        entry = next(run for run in history["runs"] if run["run_id"] == run_id)
        self.assertEqual(entry["outcome"], "placed_new", "the run did not finish normally across the restart")
        self.assertEqual(self.launch_count(), 1)

    def test_a_cursor_before_the_retained_window_gets_an_explicit_gap(self):
        self.call("POST", "/api/commands", self.command("start_cycle", {"plan_only": False}))
        self.wait_until(lambda: self.snapshot()["seq_cursor"] > 5, "some events")
        _connection, response = self.open_events(10 ** 6)  # a cursor from another log: ahead of this one
        frames = self.next_frames(response, 1)
        self.assertEqual(frames[0]["data"]["resync"]["reason"], "cursor_ahead")
        self.call("POST", "/api/stop", {})


class TestTheConsoleStopIsACommandWithAnIdentity(Stack):
    """The browser's Stop against a REAL runtime: one journaled command, idempotent by its own id, answered by the
    runtime out of its journal when the same id comes again. The only child process is the fake controller."""

    def test_a_stop_is_journaled_under_its_id_and_a_repeat_stops_nothing_twice(self):
        self.call("POST", "/api/commands", self.command("start_cycle", {"plan_only": False}))
        self.wait_until(lambda: self.launch_count() == 1, "the controller process to start")

        stop = self.command("stop", {})
        status, first, _ = self.call("POST", "/api/stop", stop)
        self.assertEqual(status, 200)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["command"]["command_id"], stop["command_id"])
        self.assertEqual(first["command"]["status"], "finished")

        status, second, _ = self.call("POST", "/api/stop", stop)
        self.assertEqual(status, 200)
        self.assertEqual(second["command"], first["command"], "the same id came back with the recorded receipt")

        # The runtime holds it by id, exactly like every other command: a lost answer is resolved, never pressed again.
        status, looked_up, _ = self.call("GET", f"/api/commands/{stop['command_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(looked_up["status"], "finished")
        self.assertEqual(looked_up["run_id"], first["command"]["run_id"])

        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the run to end after stop")
        self.assertEqual(self.launch_count(), 1, "no stop ever started anything")

    def test_a_stop_with_no_run_to_stop_is_not_reported_as_taken(self):
        status, answer, _ = self.call("POST", "/api/stop", self.command("stop", {}))
        self.assertEqual(status, 200)
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason_code"], "no_active_run")
        self.assertIsNone(answer["stop"])
        self.assertEqual(self.launch_count(), 0)


class TestAReusedCommandIdIsAnsweredAndNeverGuessedAt(Stack):
    def test_the_same_id_with_a_different_body_is_refused_and_starts_nothing(self):
        first = self.command("start_cycle", {"plan_only": False})
        status, accepted, _ = self.call("POST", "/api/commands", first)
        self.assertEqual(status, 200)
        self.assertIn(accepted["status"], ("accepted", "spawning", "running"))
        self.assertTrue(accepted["payload_digest"].startswith("sha256:"))

        other = dict(first, args={"plan_only": True})
        status, conflict, _ = self.call("POST", "/api/commands", other)
        self.assertEqual(status, 409)
        self.assertEqual(conflict["reason_code"], "command_id_reused")
        self.assertEqual(conflict["recorded_payload_digest"], accepted["payload_digest"])
        self.assertNotEqual(conflict["submitted_payload_digest"], accepted["payload_digest"])

        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the cycle to finish", timeout=120.0)
        self.assertEqual(self.launch_count(), 1, "the second body under that id started no controller")

    def test_a_receipt_survives_a_restart_of_the_runtime_and_still_names_its_request(self):
        """The digest is in the journal, not in anybody's memory, so a runtime that has been restarted still answers
        the same question about an id it admitted before."""
        command = self.command("observe", {"holding": False})
        status, accepted, _ = self.call("POST", "/api/commands", command)
        self.assertEqual(status, 200)
        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the observe to finish")

        self.daemon.terminate()
        self.daemon = Process([sys.executable, "-m", "demo_runtime.daemon", "--data-dir", self.data_dir, "--synthetic"],
                              child_environment(KC_FAKE_STEP_S=CYCLE_STEP_S, KC_FAKE_LAUNCH_LOG=self.launch_log))
        self.addCleanup(self.daemon.terminate)
        self.daemon.wait_for("demo-runtime: ready")

        status, receipt, _ = self.call("GET", f"/api/commands/{command['command_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(receipt["payload_digest"], accepted["payload_digest"])
        self.assertIsNotNone(receipt["first_seen_wall_utc"])


class TestTheCredentialOutlivesThisProcess(Stack):
    """A tab left open while the API is restarted keeps commanding. Nothing about the runtime changed, so nothing about
    what the page holds should have to change either."""

    def test_the_same_credential_is_served_and_accepted_after_the_api_is_restarted(self):
        before = self.credential
        session = self.call("GET", "/api/hello?contract_version=1")[1]["runtime_session"]
        self.assertTrue(session.startswith("rt-"))
        stored = Path(self.data_dir) / f"console-credential-{session}"
        self.assertTrue(stored.exists(), "the runtime session's credential is in the runtime's own directory")
        self.assertEqual(stat.S_IMODE(os.stat(stored).st_mode), 0o600)
        self.assertEqual(stored.read_text(encoding="ascii").strip(), before)

        self.api.kill()
        self.api = self.start_api()
        self.assertEqual(self.credential, before, "a restart against the same runtime session serves the same credential")

        # The tab never reloaded: it still presents the value it was served before the restart, and it is accepted.
        status, result, _ = self.call("POST", "/api/commands", self.command("observe", {"holding": False}), credential=before)
        self.assertEqual(status, 200)
        self.assertIn(result["status"], ("accepted", "spawning", "running", "finished"))
        hello = self.call("GET", "/api/hello?contract_version=1")[1]
        self.assertEqual(hello["credential_owner"], "runtime_session")
        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the observe to finish")

    def test_a_value_the_server_never_served_is_still_refused(self):
        status, body, _ = self.call("POST", "/api/commands", self.command("observe", {"holding": False}),
                                    credential="a-value-nobody-was-ever-given-0123")
        self.assertEqual(status, 403)
        self.assertEqual(body["reason_code"], "credential_invalid")
        self.assertEqual(self.launch_count(), 0)


class TestAReloadedTabRecoversWhatIsRunning(Stack):
    """The storage-free answer: the snapshot carries the command id of the run in flight and the seq it started at, so
    a tab that comes back knows which command is running, which run it belongs to and where the record began."""

    def test_the_snapshot_names_the_command_and_the_cursor_a_reloaded_tab_resumes_from(self):
        command = self.command("start_cycle", {"plan_only": False})
        status, accepted, _ = self.call("POST", "/api/commands", command)
        self.assertEqual(status, 200)
        self.wait_until(lambda: self.launch_count() == 1, "the controller process to start")

        active = self.wait_until(lambda: self.snapshot()["active_run"], "the run to be active")
        self.assertEqual(active["command_id"], command["command_id"])
        self.assertEqual(active["run_id"], accepted["run_id"])
        self.assertGreater(active["started_seq"], 0)

        # What a reloaded tab does with it: ask the runtime for that command by id, and resume the stream from the run.
        status, receipt, _ = self.call("GET", f"/api/commands/{command['command_id']}")
        self.assertEqual(status, 200)
        self.assertIn(receipt["status"], ("accepted", "spawning", "running"))
        self.assertEqual(receipt["run_id"], accepted["run_id"])
        # A command THIS daemon admitted is answered out of its live table, which the writer does not stamp, so the
        # receipt carries no first_seen_wall_utc and this tab does not invent one from its own clock. The field is
        # there once the journal has been folded again, which the runtime-restart test proves.
        self.assertIsNone(receipt.get("first_seen_wall_utc"))

        _connection, resumed = self.open_events(active["started_seq"] - 1)
        frames = self.next_frames(resumed, 1)
        self.assertEqual(int(frames[0]["id"]), active["started_seq"], "the run's own first record, and nothing repeated before it")

        self.call("POST", "/api/stop", self.command("stop", {}))
        self.wait_until(lambda: self.snapshot()["active_run"] is None, "the run to end after stop")


class TestReplayOnlyStack(Stack):
    replay = True

    def test_reading_works_and_every_command_is_refused(self):
        status, hello, _ = self.call("GET", "/api/hello?contract_version=1")
        self.assertEqual(hello["runtime"]["data_mode"], "replay")
        self.assertFalse(hello["commands_enabled"])

        snapshot = self.snapshot()
        self.assertEqual(snapshot["data_mode"], "replay")
        self.assertEqual({item["state"] for item in snapshot["capabilities"] if item["id"] == "start_cycle"}, {"disabled"})

        for command_type, args in (("start_cycle", {"plan_only": False}), ("observe", {"holding": False}), ("return_arm", {"plan_only": True})):
            status, result, _ = self.call("POST", "/api/commands", self.command(command_type, args))
            self.assertEqual(status, 200, command_type)
            self.assertEqual(result["status"], "rejected", command_type)
            self.assertEqual(result["reason_code"], "replay_only", command_type)

        status, body, _ = self.call("POST", "/api/stop", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason_code"], "replay_only")
        self.assertEqual(self.launch_count(), 0, "a replay-only stack started no process at all")


class TestNoStrayChildren(unittest.TestCase):
    def test_this_file_leaves_no_controller_behind(self):
        listing = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, check=False).stdout
        self.assertNotIn("fake_controller.py", listing, "a fake controller is still running")


if __name__ == "__main__":
    unittest.main(verbosity=2)
