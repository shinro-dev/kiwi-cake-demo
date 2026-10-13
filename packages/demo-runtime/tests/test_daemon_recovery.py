#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_daemon_recovery.py: a REAL daemon process is killed (SIGKILL) at each state of a command and
# a new daemon is started on the same data directory. What must hold every time: a live controller is ADOPTED, anything
# unprovable becomes UNCERTAIN with Start blocked until an operator resolves it, and the controller is NEVER started a second
# time. Synthetic mode only: the one child these daemons can start is tests/fake_controller.py. No robot, camera or network.
# Run directly: python3 packages/demo-runtime/tests/test_daemon_recovery.py
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import daemon  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.journal import read_json_lines  # noqa: E402
from demo_runtime.rpc import ResponseLost, RuntimeClient, RuntimeUnavailable  # noqa: E402
from demo_runtime.supervisor import CLASSIFICATIONS, FAULT_ENV_VAR, FINDINGS, is_alive  # noqa: E402

import demo_contracts  # noqa: E402

WAIT_S = 40.0


class Daemons:
    """Daemon processes on ONE data directory, one after another. Cleanup ends the daemon by pid and asks any fake controller
    that is still running to stop, by pid as well."""

    def __init__(self, test, step_s="0.03", scenario_env=None):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-recovery-"))
        self.data, self.launch_log = self.tmp / "data", self.tmp / "launches.txt"
        self.base_env = {**os.environ, "PYTHONPATH": str(PACKAGE_DIR), "KC_FAKE_STEP_S": step_s, "KC_FAKE_LAUNCH_LOG": str(self.launch_log), **(scenario_env or {})}
        self.base_env.pop(FAULT_ENV_VAR, None)
        self.process = None
        test.addCleanup(self.cleanup)

    def start(self, fault=None):
        env = dict(self.base_env)
        if fault:
            env[FAULT_ENV_VAR] = fault
        self.output = open(self.tmp / f"daemon-{time.monotonic_ns()}.out", "wb")
        self.process = subprocess.Popen([sys.executable, "-m", "demo_runtime.daemon", "--data-dir", str(self.data), "--synthetic"], env=env, cwd=str(REPO_ROOT),
                                        stdin=subprocess.DEVNULL, stdout=self.output, stderr=subprocess.STDOUT)
        client = RuntimeClient(self.data / "runtime.sock", timeout_s=20.0)
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.test.fail(f"the daemon ended at start with status {self.process.returncode}: {(self.tmp / Path(self.output.name).name).read_text()[-600:]}")
            try:
                if client.hello().get("ok"):
                    return client
            except (RuntimeUnavailable, OSError):
                time.sleep(0.05)
        self.test.fail("the daemon's socket did not come up")

    def wait_dead(self):
        self.process.wait(timeout=WAIT_S)
        self.output.close()
        return self.process.returncode

    def kill_daemon(self):
        """What a power cut or an OOM kill does to the daemon. The controller is in its own session and must survive it."""
        self.process.send_signal(signal.SIGKILL)
        return self.wait_dead()

    def launches(self):
        return [int(line.split()[0]) for line in self.launch_log.read_text().splitlines()] if self.launch_log.exists() else []

    def cleanup(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()  # the DAEMON under test, by pid. Never a controller: the daemon itself has no such call
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if getattr(self, "output", None) is not None and not self.output.closed:
            self.output.close()
        for pid in self.launches():
            for _ in range(300):
                if not is_alive(pid):
                    break
                try:
                    os.kill(pid, signal.SIGINT)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)


def wait_for(test, predicate, what):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    test.fail(f"timed out waiting for {what}")


def valid_snapshot(test, client):
    """The snapshot, checked against the contract WHOLE: 'unresolved_command' is an optional property of the contract
    object now, so a client validates the answer it was given and strips nothing first. The contract object on its own
    is checked as well, because a snapshot carrying none of the optional keys has to stay valid. The keys are named by
    daemon.ADDITIVE_SNAPSHOT_KEYS, which a contracts test keeps equal to the schema's optional set."""
    snapshot = client.snapshot()
    test.assertEqual(demo_contracts.validate("snapshot", snapshot), [])
    contract_part = {key: value for key, value in snapshot.items() if key not in daemon.ADDITIVE_SNAPSHOT_KEYS}
    test.assertEqual(demo_contracts.validate("snapshot", contract_part), [])
    test.assertEqual(snapshot["data_mode"], "synthetic")
    if snapshot["owner"]["kind"] == "unresolved":
        detail = snapshot["unresolved_command"]
        test.assertEqual((detail["block_reason_code"], detail["findings"]), ("owner_unresolved", list(FINDINGS)))
        test.assertIn(detail["classification"], CLASSIFICATIONS)
        test.assertTrue(detail["question"], "the operator is told what to confirm, in the snapshot itself")
    else:
        test.assertNotIn("unresolved_command", snapshot, "nothing is unresolved: the key is absent, not an empty object")
    return snapshot


def capability(snapshot, capability_id):
    return next(item for item in snapshot["capabilities"] if item["id"] == capability_id)


class KilledAtEachState(unittest.TestCase):
    def _submit_into_the_fault(self, daemons, client, command_id):
        command = client.new_command("start_cycle", {"plan_only": False}, command_id=command_id)
        with self.assertRaises(ResponseLost, msg="the daemon was meant to die inside this submit"):
            client.submit(command)
        self.assertEqual(daemons.wait_dead(), -signal.SIGKILL)
        return command

    def _uncertain_then_resolved(self, fault, reason_code):
        daemons = Daemons(self)
        command = self._submit_into_the_fault(daemons, daemons.start(fault=fault), f"{fault}-0001")
        self.assertEqual(daemons.launches(), [], "no controller existed when the daemon died")
        client = daemons.start()
        result = client.result(command["command_id"])  # the ONLY way to learn what became of a command whose answer was lost
        self.assertEqual((result["status"], result["reason_code"]), ("uncertain", reason_code))
        self.assertEqual(demo_contracts.validate("command_result", result), [])
        snapshot = valid_snapshot(self, client)
        self.assertEqual(snapshot["owner"]["kind"], "unresolved")
        self.assertIsNone(snapshot["active_run"])
        start = capability(snapshot, "start_cycle")
        self.assertEqual((start["state"], start["reason_code"]), ("blocked", "owner_unresolved"), "Start must be BLOCKED while a command is unresolved")
        blocked = client.submit(client.new_command("start_cycle", {"plan_only": False}, command_id=f"{fault}-0002"))
        self.assertEqual((blocked["status"], blocked["reason_code"]), ("rejected", "owner_unresolved"))
        again = client.submit(command)
        self.assertEqual(again["status"], "uncertain", "resubmitting the lost command returns its recorded state; it is never run")
        self.assertEqual(daemons.launches(), [], "NEVER a respawn, and nothing queued for later")
        self.assertFalse(snapshot["evidence"]["complete"], "an interrupted command is a gap in the record")
        detail = snapshot["unresolved_command"]
        self.assertEqual((detail["command_id"], detail["classification"]), (command["command_id"], "never_started"))
        self.assertEqual((detail["evidence"]["process"]["alive"], detail["evidence"]["run_files"]["events_present"]), (False, False))
        resolution = client.resolve(command["command_id"], "no_controller_running", note="looked at the robot; nothing moved")
        self.assertEqual((resolution["ok"], resolution["cleared"], resolution["cleared_by"], resolution["classification"]),
                         (True, True, "machine_evidence", "never_started"), "the daemon reconciled it itself; the finding was recorded beside that")
        self.assertEqual(resolution["attestation"]["note"], "looked at the robot; nothing moved")
        self.assertEqual(valid_snapshot(self, client)["owner"]["kind"], "free")
        fresh = client.submit(client.new_command("start_cycle", {"plan_only": False}, command_id=f"{fault}-0003"))
        self.assertEqual(fresh["status"], "running")
        done = wait_for(self, lambda: (client.result(f"{fault}-0003")["status"] == "finished") and client.snapshot(), "the run after the resolution")
        self.assertEqual(done["last_outcome"]["result"]["outcome"], "placed_new")
        self.assertEqual(len(daemons.launches()), 1, "exactly the ONE run the operator started after resolving")
        self.assertEqual(client.status()["counters"]["daemon_starts"], 2)

    def test_killed_after_accepted_is_uncertain_and_start_stays_blocked_until_resolved(self):
        self._uncertain_then_resolved("accepted", "no_child_evidence")

    def test_killed_after_spawning_before_any_child_is_uncertain_and_start_stays_blocked_until_resolved(self):
        self._uncertain_then_resolved("spawning", "child_identity_not_recorded")

    def _adopted(self, fault, journal_has_identity):
        daemons = Daemons(self, step_s="0.08")
        command = self._submit_into_the_fault(daemons, daemons.start(fault=fault), f"{fault}-0001")
        pids = wait_for(self, daemons.launches, "the controller's launch record")
        self.assertEqual(len(pids), 1)
        self.assertTrue(is_alive(pids[0]), "the controller must outlive its daemon: own session, output to files")
        before = [record for record in read_json_lines(daemons.data / "journal.jsonl")[0] if record.get("command_id") == command["command_id"]]
        self.assertEqual(any(record.get("child") for record in before), journal_has_identity)
        client = daemons.start()
        result = client.result(command["command_id"])
        self.assertEqual(result["status"], "running", "a live child is ADOPTED")
        snapshot = valid_snapshot(self, client)
        self.assertEqual((snapshot["owner"]["kind"], snapshot["active_run"]["run_id"]), ("autonomous", result["run_id"]))
        second = client.submit(client.new_command("start_cycle", {"plan_only": False}, command_id=f"{fault}-0002"))
        self.assertEqual((second["status"], second["reason_code"]), ("rejected", "run_active"))
        self.assertEqual(client.submit(command)["status"], "running", "the lost command, sent again, returns its recorded state and spawns nothing")
        done = wait_for(self, lambda: (client.result(command["command_id"])["status"] == "finished") and valid_snapshot(self, client), "the adopted run to end")
        self.assertEqual(done["last_outcome"]["result"]["outcome"], "placed_new", "the outcome of an adopted run comes from the controller's run.finished")
        self.assertIsNone(done["last_outcome"]["result"]["exit_status"], "the exit status of a process this daemon did not start is UNAVAILABLE, and says so")
        self.assertEqual(done["owner"]["kind"], "free")
        self.assertEqual(daemons.launches(), pids, "NEVER a respawn")
        history = client.history()["runs"]
        self.assertEqual([(run["run_id"], run["outcome"], run["adopted"]) for run in history], [(result["run_id"], "placed_new", True)])
        events = client.history_events(result["run_id"])
        self.assertEqual([error for event in events["events"] for error in demo_contracts.validate("event", event)], [])
        merged = [(origin["source_session"], origin["source_seq"]) for origin in events["origins"] if origin["source_seq"] is not None]
        self.assertEqual(len(merged), len(set(merged)), "tailing resumed by byte offset: no controller record was merged twice")
        self.assertGreaterEqual(sum(1 for event in events["events"] if event["kind"] == "state.transition"), 25, "and none was lost across the restart")

    def test_killed_after_the_spawn_before_the_identity_was_journaled_is_adopted_by_scan_id(self):
        self._adopted("spawned", journal_has_identity=False)

    def test_killed_while_running_is_adopted_by_recorded_identity(self):
        self._adopted("running", journal_has_identity=True)


class AfterAnOutsideKill(unittest.TestCase):
    def test_an_adopted_controller_can_be_stopped_through_the_new_daemon(self):
        daemons = Daemons(self, step_s="0.1")
        client = daemons.start()
        started = client.submit(client.new_command("start_cycle", {"plan_only": False}, command_id="outside-0001"))
        self.assertEqual(started["status"], "running")
        wait_for(self, lambda: client.snapshot()["active_run"]["state"], "the controller to get into its script")
        self.assertEqual(daemons.kill_daemon(), -signal.SIGKILL)
        pid = wait_for(self, daemons.launches, "the controller's launch record")[0]
        self.assertTrue(is_alive(pid))
        client = daemons.start()
        self.assertEqual(valid_snapshot(self, client)["active_run"]["status"], "running")
        stop = client.stop()
        self.assertEqual(stop["stop"]["run_id"], started["run_id"])
        done = wait_for(self, lambda: (client.result("outside-0001")["status"] == "finished") and valid_snapshot(self, client), "the stopped run to end")
        self.assertEqual(done["last_outcome"]["result"]["outcome"], "stopped")
        kinds = [(event["kind"], event["reason_code"]) for event in client.history_events(started["run_id"])["events"]]
        self.assertIn(("stop.outcome", "stop_acknowledged"), kinds)
        self.assertFalse(is_alive(pid))
        self.assertEqual(len(daemons.launches()), 1)

    def test_a_controller_that_finished_while_no_daemon_ran_is_finished_from_its_own_record(self):
        daemons = Daemons(self, step_s="0.01")
        client = daemons.start()
        started = client.submit(client.new_command("start_cycle", {"plan_only": False}, command_id="orphan-0001"))
        self.assertEqual(started["status"], "running")
        # 'running' is answered as soon as the child exists; the fake writes its launch record once its interpreter is up.
        pid = wait_for(self, daemons.launches, "the controller's launch record")[0]
        daemons.kill_daemon()
        wait_for(self, lambda: not is_alive(pid), "the orphaned controller to finish on its own")
        client = daemons.start()
        self.assertEqual(client.result("orphan-0001")["status"], "finished")
        snapshot = valid_snapshot(self, client)
        self.assertEqual((snapshot["owner"]["kind"], snapshot["last_outcome"]["run_id"], snapshot["last_outcome"]["result"]["outcome"]), ("free", started["run_id"], "placed_new"))
        self.assertIsNone(snapshot["last_outcome"]["result"]["exit_status"])
        self.assertEqual(len(daemons.launches()), 1)

    def test_a_controller_that_died_without_a_terminal_record_is_uncertain(self):
        daemons = Daemons(self, step_s="0.05", scenario_env={"KC_FAKE_SCENARIO": "crash"})
        client = daemons.start()
        self.assertEqual(client.submit(client.new_command("start_cycle", {"plan_only": False}, command_id="died-0001"))["status"], "running")
        pid = wait_for(self, daemons.launches, "the controller's launch record")[0]
        daemons.kill_daemon()
        wait_for(self, lambda: not is_alive(pid), "the crashing controller to be gone")
        client = daemons.start()
        result = client.result("died-0001")
        self.assertEqual((result["status"], result["reason_code"]), ("uncertain", "child_gone_without_terminal_record"))
        snapshot = valid_snapshot(self, client)
        self.assertEqual(snapshot["owner"]["kind"], "unresolved")
        self.assertGreater(len(client.history_events(result["run_id"])["events"]), 5, "what the controller recorded before it died is kept as evidence")
        # A controller that crashed: the machine can see that it RAN and that nothing is running now, and it cannot see
        # how it ended. That is not conclusive by itself, so the block clears on the operator's statement, beside it.
        detail = snapshot["unresolved_command"]
        self.assertEqual(detail["classification"], "ran_and_was_interrupted")
        self.assertEqual((detail["evidence"]["run_files"]["terminal_record"], detail["evidence"]["process"]["alive"]), (False, False))
        self.assertTrue(detail["evidence"]["journal"]["child_identity"], "the child's identity was journalled before it died")
        resolution = client.resolve("died-0001", "controller_stopped_by_operator")
        self.assertEqual((resolution["cleared"], resolution["cleared_by"], resolution["classification"]), (True, "operator_attestation", "ran_and_was_interrupted"))
        self.assertEqual(valid_snapshot(self, client)["owner"]["kind"], "free")

    def test_two_daemons_cannot_share_a_data_directory(self):
        daemons = Daemons(self)
        daemons.start()
        second = subprocess.run([sys.executable, "-m", "demo_runtime.daemon", "--data-dir", str(daemons.data), "--synthetic"], env=daemons.base_env, cwd=str(REPO_ROOT),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        self.assertEqual(second.returncode, 3)
        self.assertIn("another runtime daemon holds this data directory", second.stderr)
        self.assertNotIn(str(daemons.data), second.stderr)


if __name__ == "__main__":
    unittest.main()
