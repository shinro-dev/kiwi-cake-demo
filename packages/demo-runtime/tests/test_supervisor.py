#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_supervisor.py: the supervisor against the FAKE controller only (tests/fake_controller.py).
# No robot, camera, network or ssh: every child process here is the synthetic fake, in a temporary directory.
#
# The first group of tests pins defects this module really had. A build of it released the robot while a controller was
# running: os.pidfd_open is missing from some interpreter builds, the call raised AFTER the spawn, the broad handler recorded
# "the controller was never started" and the owner went back to free while the child ran a whole cycle. The same shape sat
# in the stop path (a stop parked forever when there was no pidfd), in the waiter (it died and the run never ended) and in
# adoption. The rule they all break: ONCE A CHILD EXISTS, NOTHING MAY LET GO OF IT EXCEPT ITS OWN EXIT.
# Run directly: python3 packages/demo-runtime/tests/test_supervisor.py
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import preflight as pf  # noqa: E402
from demo_runtime import read_models as rm  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.config import RuntimeConfig  # noqa: E402
from demo_runtime.journal import EventLog, Journal, read_json_lines  # noqa: E402

import demo_contracts  # noqa: E402  (importable once demo_runtime.journal was imported)

FAKE_ENTRY = "packages/demo-runtime/tests/fake_controller.py"
WAIT_S = 30.0


def command(command_id, kind="start_cycle", args=None):
    return {"contract_version": "1", "command_id": command_id, "type": kind, "args": {"plan_only": False} if args is None else args,
            "client": {"session_id": "test-client", "contract_version": "1"}}


class Harness:
    """One data directory and one synthetic supervisor. The cleanup never leaves a fake controller behind."""

    def __init__(self, test, *, env=None, entry=FAKE_ENTRY, step_s="0.01", **options):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-supervisor-"))
        test.addCleanup(self._remove)
        self.data = self.tmp / "data"
        self.data.mkdir(mode=0o700)
        self.launch_log = self.tmp / "launches.txt"
        self.env = {"KC_FAKE_STEP_S": step_s, "KC_FAKE_LAUNCH_LOG": str(self.launch_log), **(env or {})}
        self.entry, self.options = entry, options
        self.supervisors = []
        self.supervisor = self.new_supervisor()

    def new_supervisor(self, **overrides):
        options = {"mode": sv.SYNTHETIC, "extra_env": self.env, **self.options, **overrides}
        supervisor = sv.Supervisor(data_dir=self.data, journal=Journal(self.data / "journal.jsonl"), event_log=EventLog(self.data / "events.jsonl"),
                                   runtime_session=f"rt-test-{len(self.supervisors)}", source_root=REPO_ROOT, controller_entry=self.entry, **options)
        self.supervisors.append(supervisor)
        return supervisor

    def _remove(self):
        for supervisor in self.supervisors:
            if supervisor.active_run() is not None and not supervisor._detached.is_set():
                supervisor.stop()
                self.wait_idle(supervisor, fail=False)
            supervisor.detach()
        for line in self.launches():  # a child a detached supervisor let go of: ask it to stop, by PID, and wait for it
            pid = int(line.split()[0])
            for _ in range(200):
                if not sv.is_alive(pid):
                    break
                try:
                    os.kill(pid, signal.SIGINT)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def launches(self):
        return self.launch_log.read_text().splitlines() if self.launch_log.exists() else []

    def wait_idle(self, supervisor=None, fail=True):
        supervisor = supervisor or self.supervisor
        deadline = time.monotonic() + WAIT_S
        while supervisor.active_run() is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        if fail:
            self.test.assertIsNone(supervisor.active_run(), "the run did not end in time")

    def wait_state(self, minimum_transitions=3):
        """Until the fake has installed its handler and is inside its script."""
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            if sum(1 for event in self.events() if event["kind"] == "state.transition") >= minimum_transitions:
                return
            time.sleep(0.02)
        self.test.fail("the fake controller did not get into its script in time")

    def events(self):
        return [record["event"] for record in read_json_lines(self.data / "events.jsonl")[0]]

    def journal_statuses(self, command_id):
        return [record.get("status") for record in read_json_lines(self.data / "journal.jsonl")[0] if record.get("command_id") == command_id and record.get("rec") == "command"]

    def completion(self, command_id, supervisor=None):
        return (supervisor or self.supervisor).table()[command_id].get("completion") or {}


class OnceAChildExistsNothingLetsGoOfIt(unittest.TestCase):
    """Regression tests for the defects described at the top of this file."""

    def test_a_failure_right_after_the_spawn_never_reads_as_never_started(self):
        harness = Harness(self, step_s="0.02")

        def identity_step_fails(run):
            raise AttributeError("forced: what a missing os.pidfd_open did")

        harness.supervisor._identify_child = identity_step_fails
        first = harness.supervisor.submit(command("regress-0001"))
        self.assertNotEqual((first["status"], first["reason_code"]), ("finished", "spawn_failed"), "a live controller was recorded as never started")
        self.assertIn(first["status"], ("spawning", "running"))
        self.assertEqual(harness.supervisor.owner()["kind"], "autonomous", "the robot was released while the controller runs")
        self.assertIsNotNone(harness.supervisor.active_run())
        self.assertEqual(harness.supervisor.submit(command("regress-0002"))["reason_code"], "run_active", "a second controller could have been started")
        harness.wait_idle()
        completion = harness.completion("regress-0001")
        self.assertEqual((completion["outcome"], completion["exit_status"]), ("placed_new", 0), "the run's REAL outcome must be recorded")
        self.assertFalse(completion["evidence_complete"], "the failed identity step must show as incomplete evidence, not vanish")
        self.assertGreaterEqual(completion["evidence_gaps"], 1)
        self.assertEqual(len(harness.launches()), 1)
        self.assertEqual(harness.supervisor.owner()["kind"], "free")
        self.assertNotIn("spawn_failed", [event["reason_code"] for event in harness.events()])

    def test_a_failure_before_the_spawn_is_a_spawn_failure_and_starts_nothing(self):
        harness = Harness(self, entry="packages/demo-runtime/tests/no_such_controller.py")
        result = harness.supervisor.submit(command("prespawn-0001"))
        self.assertEqual((result["status"], result["reason_code"]), ("finished", "spawn_failed"))
        self.assertEqual(harness.launches(), [])
        self.assertEqual(harness.supervisor.launches, 0)
        self.assertEqual(harness.supervisor.owner()["kind"], "free")
        self.assertEqual(harness.completion("prespawn-0001")["outcome"], "failed")

    def test_the_interpreter_without_os_pidfd_open_still_gets_a_pidfd_or_none_and_never_an_exception(self):
        original = getattr(os, "pidfd_open", None)
        if original is not None:
            self.addCleanup(setattr, os, "pidfd_open", original)
            del os.pidfd_open
        descriptor = sv.pidfd_open(os.getpid())
        self.assertTrue(descriptor is None or descriptor >= 0)
        if descriptor is not None:
            os.close(descriptor)
        self.assertIsNone(sv.pidfd_open(2 ** 22 + 12345), "a pid that does not exist gives None, not an exception")

    def test_stop_reaches_the_child_when_there_is_no_pidfd_at_all(self):
        harness = Harness(self, step_s="0.05")
        original = sv.pidfd_open
        self.addCleanup(setattr, sv, "pidfd_open", original)
        sv.pidfd_open = lambda pid: None
        harness.supervisor.submit(command("nopidfd-0001"))
        harness.wait_state()
        self.assertIsNotNone(harness.supervisor.stop(), "the stop was not taken")
        harness.wait_idle()
        stop = harness.completion("nopidfd-0001")["stop"]
        self.assertEqual((stop["mechanism"], stop["classification"], stop["signal_delivered"]), ("own_child_handle", "acknowledged", True),
                         "without a pidfd a stop was once parked as pending forever and never delivered")
        self.assertEqual(harness.completion("nopidfd-0001")["exit_status"], 4)

    def test_the_waiter_ends_the_run_even_when_its_descriptor_is_unusable(self):
        harness = Harness(self)
        harness.supervisor.submit(command("waiter-0001"))
        with harness.supervisor._state_lock:
            descriptor, harness.supervisor._active.pidfd = harness.supervisor._active.pidfd, None  # what a platform without pidfds looks like
        if descriptor is not None:
            os.close(descriptor)
        harness.wait_idle()
        self.assertEqual(harness.supervisor.result("waiter-0001")["status"], "finished", "a waiter that died left the run active forever")
        self.assertEqual(harness.supervisor.owner()["kind"], "free")

    def test_the_tailer_is_a_working_thread(self):
        """Its file handle was once stored as _handle, the name threading.Thread (3.13) uses for the OS thread: is_alive() raised."""
        path = Path(tempfile.mkdtemp(prefix="kc-tailer-"))
        self.addCleanup(shutil.rmtree, path, True)
        lines = []
        tailer = sv.Tailer(path / "events.jsonl", 0, lambda line, end: lines.append((line, end)), lambda end: None, lambda: None, 0.01)
        self.assertFalse(set(vars(tailer)) & {"_handle"} - set(vars(threading.Thread(target=len))), "a Tailer attribute shadows one of threading.Thread's")
        tailer.start()
        self.assertTrue(tailer.is_alive())
        with open(path / "events.jsonl", "ab") as handle:
            handle.write(b'{"a":1}\n{"b":')
            handle.flush()
            deadline = time.monotonic() + 5
            while not lines and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(lines, [(b'{"a":1}', 8)], "a complete line is handed on with its end offset; the torn one is held back")
            handle.write(b'2}\n')
        tailer.finish()
        tailer.join(timeout=5)
        self.assertFalse(tailer.is_alive())
        self.assertEqual([line for line, _ in lines], [b'{"a":1}', b'{"b":2}'])
        self.assertEqual(tailer.partial, b"")

    def test_a_run_whose_end_cannot_be_recorded_at_all_still_releases_the_robot(self):
        # The mirror of the defect at the top of this file, and MEASURED the same way. _wait's recovery path announced the
        # failure BEFORE it released, through the very event log whose failure had broken the finalize. With the log
        # refusing writes, _say raised, the waiter thread died, and a controller that had ALREADY EXITED kept the robot for
        # the life of the daemon: owner autonomous, every command refused run_active, and nothing an operator could
        # resolve, because the uncertain record had not been written either. The release now sits in a finally.
        harness = Harness(self, step_s="0.02")
        supervisor = harness.supervisor
        supervisor.submit(command("wedge-0001"))
        harness.wait_state(1)
        original = sv.Supervisor._emit

        def no_event_can_be_written(*_arguments, **_options):
            raise OSError("the event log cannot be written")

        sv.Supervisor._emit = no_event_can_be_written
        self.addCleanup(setattr, sv.Supervisor, "_emit", original)
        harness.wait_idle()  # the controller exits on its own; the owner must go back to free with no event written at all
        sv.Supervisor._emit = original
        self.assertIsNone(supervisor.active_run(), "a dead controller still holds the robot")
        self.assertEqual(supervisor.owner()["kind"], "free")
        self.assertIsNone(supervisor.block(), "the next command must not be refused by a run that is over")
        # What was lost is the RECORD, and that is stated rather than guessed: the last status the journal took is the one
        # it keeps, so the next daemon's reconcile finds a command it cannot account for and asks an operator.
        self.assertEqual(supervisor.table()["wedge-0001"]["status"], "running")

    def test_no_escalation_exists_in_the_source(self):
        import io
        import tokenize

        source = (PACKAGE_DIR / "demo_runtime" / "supervisor.py").read_text()
        # CODE only: tokenize keeps strings (docstrings included) and comments apart, so the sentence in the module docstring
        # that says there is no SIGTERM does not count, and a real one anywhere in the code does.
        code = " ".join(token.string for token in tokenize.generate_tokens(io.StringIO(source).readline) if token.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER))
        self.assertEqual(re.findall(r"SIGTERM|SIGQUIT|SIGHUP|killpg|\bterminate\b", code), [], "stop is SIGINT to the PID and nothing more")
        self.assertEqual(re.findall(r"signal \. SIGKILL", code), ["signal . SIGKILL"], "the ONE SIGKILL is the fault hook aimed at the daemon itself")
        self.assertIn("os . kill ( os . getpid ( ) , signal . SIGKILL )", code)
        self.assertEqual(re.findall(r"os \. kill \( (?!os \. getpid)[^)]*\)", code), ["os . kill ( run . pid , signal . SIGINT )"], "the only other kill is SIGINT to the verified controller pid")
        self.assertEqual(sorted(re.findall(r"send_signal \( [^)]*\)", code)), ["send_signal ( pidfd , signal . SIGINT )", "send_signal ( signal . SIGINT )"],
                         "every signal this module can send is SIGINT, written out at the call: no helper takes the signal as a parameter")
        self.assertEqual(re.findall(r"c_int \( int \( signal \. (\w+)", code), ["SIGINT"], "the raw system call path hardcodes SIGINT as well")
        self.assertNotIn("def pidfd_signal", source, "a generic signal-sending helper existed once; escalation must be impossible by construction")


class CommandArbitration(unittest.TestCase):
    def test_the_same_command_id_twice_spawns_once_and_returns_the_recorded_result(self):
        harness = Harness(self, step_s="0.02")
        first = harness.supervisor.submit(command("dup-0001"))
        second = harness.supervisor.submit(command("dup-0001"))
        self.assertEqual(first["status"], "running")
        self.assertEqual((second["command_id"], second["run_id"]), (first["command_id"], first["run_id"]))
        harness.wait_idle()
        self.assertEqual(harness.supervisor.submit(command("dup-0001"))["status"], "finished", "after the run the recorded result is the finished one")
        self.assertEqual(len(harness.launches()), 1)

    def test_concurrent_submits_of_one_command_spawn_once(self):
        harness = Harness(self, step_s="0.02")
        results, barrier = [], threading.Barrier(8)

        def submit():
            barrier.wait()
            results.append(harness.supervisor.submit(command("race-0001")))

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT_S)
        self.assertEqual(len(results), 8)
        self.assertEqual({result["run_id"] for result in results}, {results[0]["run_id"]})
        harness.wait_idle()
        self.assertEqual(len(harness.launches()), 1)
        self.assertEqual(harness.journal_statuses("race-0001"), ["accepted", "spawning", "running", "finished"])

    def test_a_different_command_during_a_run_is_rejected_and_stop_is_not(self):
        harness = Harness(self, step_s="0.05")
        harness.supervisor.submit(command("busy-0001"))
        for index, (kind, args) in enumerate((("start_cycle", {"plan_only": True}), ("observe", {"holding": False}), ("manual_pan", {"deg": 2.0, "holding": False, "plan_only": True}))):
            rejected = harness.supervisor.submit(command(f"busy-other-{index}", kind, args))
            self.assertEqual((rejected["status"], rejected["reason_code"], rejected["run_id"]), ("rejected", "run_active", None))
        harness.wait_state()
        stopped = harness.supervisor.submit(command("busy-stop", "stop", {}))
        self.assertEqual(stopped["status"], "finished")
        harness.wait_idle()
        self.assertEqual(len(harness.launches()), 1, "observe during a run must never start a second controller")

    def test_an_invalid_command_is_refused_by_the_contract_before_anything_else(self):
        harness = Harness(self)
        out_of_bounds = harness.supervisor.submit(command("bad-0001", "manual_pan", {"deg": 40.0, "holding": False, "plan_only": False}))
        self.assertEqual((out_of_bounds["status"], out_of_bounds["reason_code"]), ("rejected", "command_invalid"))
        self.assertEqual(harness.supervisor.submit({"type": "start_cycle"}), {"ok": False, "reason_code": "command_invalid"})
        self.assertEqual(harness.supervisor.submit("rm -rf"), {"ok": False, "reason_code": "command_invalid"})
        self.assertEqual(harness.launches(), [])

    def test_the_argv_mapping_is_the_whole_allowlist(self):
        cases = {
            "start_cycle": ({"plan_only": True}, ["--do", "cycle", "--plan-only"]), "observe": ({"holding": True}, ["--do", "observe", "--holding"]),
            "return_arm": ({"plan_only": False}, ["--do", "exit"]), "manual_pan": ({"deg": -4.5, "holding": True, "plan_only": False}, ["--do", "pan:-4.5", "--holding"]),
            "manual_move": ({"forward_mm": 20, "dz_mm": -5.5, "holding": False, "plan_only": True}, ["--do", "move:20.0,-5.5", "--plan-only"]),
            "manual_grip": ({"action": "latch", "plan_only": False}, ["--do", "grip:latch"]),
            "manual_base_pulse": ({"seconds": -0.4, "holding": False, "plan_only": False}, ["--do", "base:-0.4"]),
        }
        for kind, (args, expected) in cases.items():
            built = command("argv", kind, args)
            self.assertEqual(demo_contracts.validate("command", built), [], kind)
            argv = sv.controller_arguments(built, "console-x", address="192.0.2.7", robot_id="example-robot", pi_ssh="")
            self.assertEqual(argv, expected + ["--remote-ip", "192.0.2.7", "--robot-id", "example-robot", "--scan-id", "console-x"], kind)
        with self.assertRaises(KeyError):
            sv.action_text({"type": "stop", "args": {}})

    def test_live_mode_without_the_hardware_flag_blocks_every_command(self):
        class NoConfig:  # a live supervisor needs a config object; nothing here reads a value from it
            pass

        harness = Harness(self, mode=sv.LIVE, config=NoConfig(), hardware_enabled=False)
        self.assertEqual(harness.supervisor.block(), ("hardware_commands_not_enabled", None))
        rejected = harness.supervisor.submit(command("live-0001"))
        self.assertEqual((rejected["status"], rejected["reason_code"]), ("rejected", "hardware_commands_not_enabled"))
        self.assertEqual(harness.launches(), [])

    def test_an_external_controller_blocks_commands(self):
        harness = Harness(self, external_probe=lambda: [4242])
        self.assertEqual(harness.supervisor.owner()["kind"], "external_detected")
        self.assertEqual(harness.supervisor.submit(command("ext-0001"))["reason_code"], "owner_external")


class Stop(unittest.TestCase):
    def _stopped(self, harness, command_id):
        harness.wait_idle()
        return harness.completion(command_id)

    def test_acknowledged_stop_goes_to_the_pid_only_and_not_to_its_process_group(self):
        sentinel_file = Path(tempfile.mkdtemp(prefix="kc-sentinel-")) / "sentinel.txt"
        self.addCleanup(shutil.rmtree, sentinel_file.parent, True)
        harness = Harness(self, step_s="0.05", env={"KC_FAKE_GROUP_SENTINEL": str(sentinel_file)})
        harness.supervisor.submit(command("stop-0001"))
        harness.wait_state()
        helper_pid, helper_group, controller_group = (int(part) for part in sentinel_file.read_text().split())
        self.addCleanup(lambda: sv.is_alive(helper_pid) and os.kill(helper_pid, signal.SIGKILL))  # the TEST's own helper, never a controller
        self.assertEqual(helper_group, controller_group, "the helper must share the controller's process group for this test to mean anything")
        answer = harness.supervisor.stop()
        completion = self._stopped(harness, "stop-0001")
        self.assertEqual(answer["run_id"], completion["run_id"])
        self.assertEqual((completion["outcome"], completion["exit_status"]), ("stopped", 4))
        stop = completion["stop"]
        self.assertEqual((stop["classification"], stop["acknowledged"], stop["acknowledged_by"]), ("acknowledged", True, "event"))
        self.assertIn(stop["mechanism"], ("pidfd", "own_child_handle"))
        self.assertTrue(sv.is_alive(helper_pid), "the stop signalled the process GROUP: the controller's ssh and scp children would have died with it")
        self.assertEqual(stop["ack_latency_basis"], "controller_event_clock", "the acknowledgement time is the controller's own timestamp on the shared monotonic clock")
        self.assertGreaterEqual(stop["exit_latency_ns"], stop["ack_latency_ns"], "a process cannot acknowledge after it has exited: the ack was once stamped when the tailer READ it")
        self.assertGreater(stop["ack_latency_ns"], 0)
        kinds = [(event["kind"], event["reason_code"]) for event in harness.events()]
        self.assertIn(("stop.outcome", "stop_acknowledged"), kinds)
        self.assertEqual(sum(1 for kind, _ in kinds if kind == "stop.acknowledged"), 1)
        requested = [event for event in harness.events() if event["kind"] == "stop.requested" and event["payload"]["origin"] == "operator_console"]
        self.assertEqual(len(requested), 1)

    def test_a_controller_that_never_acknowledges_is_never_escalated_and_is_classified_completed_anyway(self):
        harness = Harness(self, step_s="0.04", env={"KC_FAKE_SCENARIO": "never_acknowledges"})
        harness.supervisor.submit(command("noack-0001"))
        harness.wait_state()
        pid = int(harness.launches()[0].split()[0])
        harness.supervisor.stop()
        time.sleep(0.6)
        self.assertTrue(sv.is_alive(pid), "a controller that ignores SIGINT must be left running: there is no SIGTERM or SIGKILL escalation")
        self.assertEqual(harness.supervisor.active_run()["status"], "stopping")
        completion = self._stopped(harness, "noack-0001")
        self.assertEqual((completion["outcome"], completion["exit_status"], completion["stop"]["classification"]), ("placed_new", 0, "completed_anyway"))
        self.assertFalse(completion["stop"]["acknowledged"])
        self.assertGreater(completion["stop"]["signals_sent"], 1, "an unacknowledged stop is repeated")
        self.assertLessEqual(completion["stop"]["signals_sent"], 1 + sv.STOP_REPEAT_LIMIT, "and the repeats are BOUNDED: the same SIGINT, never anything stronger")

    def test_a_stop_before_the_handler_is_installed_is_classified_pre_handler_exit(self):
        harness = Harness(self, env={"KC_FAKE_PRE_HANDLER_S": "5"})
        harness.supervisor.submit(command("early-0001"))
        deadline = time.monotonic() + WAIT_S
        while not harness.launches() and time.monotonic() < deadline:  # the interpreter is up and inside the window before its handler
            time.sleep(0.02)
        harness.supervisor.stop()
        completion = self._stopped(harness, "early-0001")
        self.assertEqual(completion["stop"]["classification"], "pre_handler_exit")
        self.assertIn(completion["exit_status"], (-signal.SIGINT, 128 + signal.SIGINT))
        self.assertEqual(completion["outcome"], "stopped", "a requested stop that ended the process is a STOP, not a failure; the thin evidence is in the classification")
        self.assertFalse(completion["stop"]["acknowledged"])
        self.assertFalse(completion["evidence_complete"])
        self.assertIn(("stop.outcome", "stop_pre_handler_exit"), [(event["kind"], event["reason_code"]) for event in harness.events()])

    def test_an_early_stop_is_a_stop_whatever_millisecond_it_lands_in(self):
        """MEASURED on this interpreter: the same SIGINT ends a starting Python process with -2 (before any handler), with 1 and
        a KeyboardInterrupt traceback (inside an import, after Python's own handler exists) or with 4 (acknowledged). The second
        was once recorded as a crash with outcome failed: a stop that worked, shown to the operator as a failure."""
        for index in range(24):
            harness = Harness(self, step_s="0.04")
            harness.supervisor.submit(command(f"sweep-{index:04d}"))
            time.sleep(0.004 * (index % 12))
            harness.supervisor.stop()
            harness.wait_idle()
            completion = harness.completion(f"sweep-{index:04d}")
            stop = completion["stop"]
            self.assertEqual(completion["outcome"], "stopped", f"exit status {completion['exit_status']}, classified {stop['classification']}")
            self.assertIn(stop["classification"], ("acknowledged", "pre_handler_exit"))
            if stop["classification"] == "pre_handler_exit":
                self.assertFalse(completion["evidence_complete"], "a stop without an acknowledgement is a stop on THIN evidence, and says so")
                self.assertIn(stop["pre_handler_evidence"], ("killed_by_sigint", "keyboard_interrupt_traceback"))
                self.assertFalse(stop["acknowledged"])
        # Which shapes a sweep hits depends on the machine, so nothing here requires one; the window before the handler is
        # entered deterministically by test_a_stop_before_the_handler_is_installed_is_classified_pre_handler_exit.

    def test_a_lost_first_signal_is_repeated_and_the_run_still_stops(self):
        """MEASURED under load: about 1 early SIGINT in 360 was lost and the run went on to place the cube with a stop on record.
        Here the loss is made certain: the first signal is swallowed, and only the runtime's own repeat can stop the run."""
        harness = Harness(self, step_s="0.05")
        original, calls = sv.Supervisor._signal_locked, []

        def lose_the_first(supervisor, run):
            calls.append(time.monotonic_ns())
            if len(calls) == 1:  # recorded as sent, never delivered: what a lost signal looks like from the inside
                run.stop_mechanism, run.stop_signals_sent, run.stop_last_signal_mono_ns, run.stop_signal_mono_ns = "lost_by_test", 1, calls[0], calls[0]
                return
            original(supervisor, run)

        sv.Supervisor._signal_locked = lose_the_first
        self.addCleanup(setattr, sv.Supervisor, "_signal_locked", original)
        harness.supervisor.submit(command("lost-sig-0001"))
        harness.wait_state()
        harness.supervisor.stop()
        harness.wait_idle()
        completion = harness.completion("lost-sig-0001")
        self.assertEqual((completion["outcome"], completion["exit_status"]), ("stopped", 4), "a lost signal must not turn a stop into a completed cycle")
        self.assertEqual((completion["stop"]["classification"], completion["stop"]["signals_sent"]), ("acknowledged", 2))
        self.assertGreaterEqual(calls[1] - calls[0], sv.STOP_REPEAT_INTERVAL_NS, "the repeat is slow on purpose: it is not a burst")
        self.assertIn("stop_repeated", [event["reason_code"] for event in harness.events()], "the operator is told that the stop had to be repeated")

    def test_a_keyboard_interrupt_traceback_counts_only_when_a_stop_was_delivered(self):
        harness = Harness(self)
        run = sv._Run(run_id="console-kbd", command_id="kbd-0001", command_type="start_cycle", run_dir=harness.data / "history" / "console-kbd")
        run.run_dir.mkdir(parents=True)
        (run.run_dir / sv.STDERR_FILE).write_text("Traceback (most recent call last):\n  File \"x.py\", line 1, in <module>\nKeyboardInterrupt\n")
        self.assertEqual(harness.supervisor._classify_stop(run, 1, "failed"), "crash", "no signal was sent by this runtime: a KeyboardInterrupt is then not OUR stop")
        run.stop_signal_mono_ns = time.monotonic_ns()
        self.assertEqual(harness.supervisor._classify_stop(run, 1, "failed"), "pre_handler_exit")
        run.stop_signal_failed = True
        self.assertEqual(harness.supervisor._classify_stop(run, 1, "failed"), "crash", "a signal that could not be delivered did not cause it either")
        run.stop_signal_failed = False
        measured_during_site_import = ("Fatal Python error: init_import_site: Failed to import the site module\nPython runtime state: initialized\nKeyboardInterrupt\n\n"
                                       "Current thread 0x0000000000000000 (most recent call first):\n  <no Python frame>\n")
        (run.run_dir / sv.STDERR_FILE).write_text(measured_during_site_import)
        self.assertEqual(harness.supervisor._classify_stop(run, 1, "failed"), "pre_handler_exit", "the exception line is NOT the last line in this shape; it was once missed")
        (run.run_dir / sv.STDERR_FILE).write_text("Traceback (most recent call last):\nValueError: something else\n")
        self.assertEqual(harness.supervisor._classify_stop(run, 1, "failed"), "crash")
        (run.run_dir / sv.STDERR_FILE).write_text("Traceback (most recent call last):\n  File \"x.py\", line 9, in main\n    except KeyboardInterrupt:\nValueError: a real crash\n")
        self.assertEqual(harness.supervisor._classify_stop(run, 1, "failed"), "crash", "a source line that mentions the name is not the exception line")

    def test_a_crash_after_a_stop_request_is_classified_crash(self):
        harness = Harness(self, step_s="0.05", env={"KC_FAKE_SCENARIO": "crash", "KC_FAKE_PRE_HANDLER_S": "0"})
        original = sv.Supervisor._signal_locked

        def record_only(supervisor, run):  # the request is recorded and the signal is NOT sent, so the scripted crash is what ends the process
            run.stop_mechanism, run.stop_signal_mono_ns = "withheld_by_test", time.monotonic_ns()

        sv.Supervisor._signal_locked = record_only
        self.addCleanup(setattr, sv.Supervisor, "_signal_locked", original)
        harness.supervisor.submit(command("crash-0001"))
        harness.wait_state(1)
        harness.supervisor.stop()
        completion = self._stopped(harness, "crash-0001")
        self.assertEqual((completion["exit_status"], completion["stop"]["classification"], completion["outcome"]), (70, "crash", "failed"))
        gaps = [event["payload"]["detail"] for event in harness.events() if event["kind"] == "evidence.gap"]
        self.assertTrue(any("incomplete line" in detail for detail in gaps), "the torn last line of the controller's file must be reported")
        self.assertTrue(any("without a run.finished" in detail for detail in gaps))

    def test_stop_without_a_run_says_so(self):
        harness = Harness(self)
        self.assertIsNone(harness.supervisor.stop())
        self.assertIn(("stop.outcome", "no_active_run"), [(event["kind"], event["payload"].get("outcome")) for event in harness.events()])

    def test_a_stop_while_the_accept_record_is_being_written_is_not_lost(self):
        # The defect this pins, MEASURED: the run became the active one AFTER the accept record was journaled, and
        # journaling is a write plus an fsync. A stop that arrived inside that window saw no active run at all, answered
        # no_active_run, and the command went on to spawn and finish placed_new. Here the accept append is held open, so
        # the window is entered on purpose rather than by luck; nothing else about the supervisor is changed.
        harness = Harness(self, step_s="0.05")
        supervisor = harness.supervisor
        inside, release = threading.Event(), threading.Event()
        original = Journal.append

        def held_append(journal, record):
            if record.get("rec") == "command" and record.get("status") == "accepted":
                inside.set()
                release.wait(WAIT_S)
            return original(journal, record)

        Journal.append = held_append
        self.addCleanup(setattr, Journal, "append", original)
        worker = threading.Thread(target=lambda: supervisor.submit(command("accept-race-0001")), daemon=True)
        worker.start()
        self.assertTrue(inside.wait(WAIT_S), "the accept record was never written")
        answer = supervisor.stop(origin="operator_console")
        release.set()
        worker.join(WAIT_S)
        self.assertIsNotNone(answer, "the stop found no run although a command had been accepted")
        self.assertEqual(answer["run_id"], supervisor.table()["accept-race-0001"]["run_id"])
        harness.wait_idle()
        state = supervisor.table()["accept-race-0001"]
        self.assertEqual((state["status"], state["reason_code"]), ("finished", "stopped_before_spawn"))
        self.assertEqual(state["completion"]["outcome"], "stopped")
        self.assertEqual(harness.launches(), [], "a stopped command must never start a controller")

    def test_a_journal_that_refuses_the_accept_record_does_not_leave_the_robot_held(self):
        # The other side of publishing the run first: if the record cannot be written the command fails, and the owner
        # must go back to free rather than stay held by a run that never existed.
        harness = Harness(self)
        supervisor = harness.supervisor
        original = Journal.append

        def refuse(journal, record):
            if record.get("rec") == "command" and record.get("status") == "accepted":
                raise OSError("the journal refused this record")
            return original(journal, record)

        Journal.append = refuse
        self.addCleanup(setattr, Journal, "append", original)
        with self.assertRaises(OSError):
            supervisor.submit(command("accept-fails-0001"))
        Journal.append = original
        self.assertIsNone(supervisor.active_run())
        self.assertEqual(supervisor.owner()["kind"], "free")
        self.assertIsNone(supervisor.block())
        self.assertEqual(harness.launches(), [])


class TheMissionStopDoorReachesTheControllerAsTheSameGuardedStop(unittest.TestCase):
    """The daemon's one stop path (RuntimeCore._stop_run_and_mission) reaches the controller through supervisor.stop
    whichever door it came through. From the MISSION STOP door, with no mission surface at all, it is still SIGINT to the
    controller's pid only, repeated a bounded number of times and never escalated, and it is recorded as the operator
    console stop that contract v1's stop.requested can name."""

    def test_a_stop_from_the_mission_door_is_sigint_to_the_pid_only_and_nothing_escalates(self):
        from demo_runtime import daemon

        tmp = Path(tempfile.mkdtemp(prefix="kc-supervisor-door-"))
        self.addCleanup(lambda: (make_tree_writable(tmp), shutil.rmtree(tmp, ignore_errors=True)))
        launch_log = tmp / "launches.txt"
        core = daemon.RuntimeCore(data_dir=tmp / "data", mode=daemon.SYNTHETIC, source_root=REPO_ROOT,
                                  cake_probe=lambda: {"artifacts": False, "resident": False, "admin_socket": False},
                                  extra_env={"KC_FAKE_STEP_S": "0.04", "KC_FAKE_SCENARIO": "never_acknowledges",
                                             "KC_FAKE_LAUNCH_LOG": str(launch_log)},
                                  controller_entry=daemon.FAKE_CONTROLLER_ENTRY)
        self.addCleanup(core.close)
        self.assertIsNone(core.mission, "no mission surface: the door is all that is being exercised")
        accepted = core.handle("submit", {"command": command("door-0001")}, None)
        self.assertIn(accepted["status"], ("accepted", "running"))
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline and sum(1 for record in read_json_lines(tmp / "data" / "events.jsonl")[0]
                                                  if record["event"]["kind"] == "state.transition") < 3:
            time.sleep(0.02)
        pid = int(launch_log.read_text().splitlines()[0].split()[0])

        answer = core.handle("mission_stop", {}, None)
        self.assertEqual(answer["reason_code"], "mission_unavailable", "the mission half says there is no mission surface")
        self.assertEqual(answer["run_stop"]["run_id"], core.supervisor.active_run()["run_id"], "and the run half was taken")
        time.sleep(0.6)
        self.assertTrue(sv.is_alive(pid), "a controller that ignores SIGINT is left running: no SIGTERM, no SIGKILL")
        deadline = time.monotonic() + WAIT_S
        while core.supervisor.active_run() is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        completion = core.supervisor.table()["door-0001"].get("completion") or {}
        stop = completion["stop"]
        self.assertIn(stop["mechanism"], ("pidfd", "own_child_handle"), "delivered to the controller's own pid")
        self.assertGreater(stop["signals_sent"], 1)
        self.assertLessEqual(stop["signals_sent"], 1 + sv.STOP_REPEAT_LIMIT, "repeated a bounded number of times, never escalated")
        self.assertEqual(stop["origin"], "operator_console")
        events = [record["event"] for record in read_json_lines(tmp / "data" / "events.jsonl")[0]]
        requested = [event for event in events if event["kind"] == "stop.requested"]
        self.assertEqual([event["payload"]["origin"] for event in requested], ["operator_console"])
        self.assertFalse([event for event in events if event["kind"] == "evidence.gap" and "own contract" in event["payload"]["detail"]],
                         "the stop.requested event fitted the contract: its origin is one the schema names")


class TheRunSlotSharedWithAMission(unittest.TestCase):
    """The supervisor's half of the single run slot: a gate that refuses every command but stop while something else
    holds the slot, and a start that runs only while no controller run does."""

    def test_the_gate_refuses_every_command_but_stop_and_names_its_reason(self):
        holder = ["mission_running"]
        harness = Harness(self, run_slot_gate=lambda: holder[0])
        refused = harness.supervisor.submit(command("gated-0001"))
        self.assertEqual((refused["status"], refused["reason_code"]), ("rejected", "mission_running"))
        self.assertEqual(harness.supervisor.block(), ("mission_running", None), "the snapshot reads the same arbitration")
        stopped = harness.supervisor.submit(command("gated-stop", "stop", {}))
        self.assertEqual((stopped["status"], stopped["reason_code"]), ("finished", "no_active_run"), "stop is never gated")
        self.assertEqual(harness.launches(), [])
        holder[0] = None
        self.assertEqual(harness.supervisor.submit(command("gated-0002"))["status"], "running")
        harness.wait_idle()

    def test_a_start_beside_the_runs_is_refused_while_a_run_is_active_and_made_when_none_is(self):
        harness = Harness(self, step_s="0.05")
        started: list = []
        self.assertEqual(harness.supervisor.start_if_run_slot_free(lambda: started.append(1) or {"ok": True}), (None, {"ok": True}))
        harness.supervisor.submit(command("slot-0001"))
        harness.wait_state()
        self.assertEqual(harness.supervisor.start_if_run_slot_free(lambda: started.append(2) or {"ok": True}), ("run_active", None))
        self.assertEqual(started, [1], "the refused start was never called")
        harness.supervisor.stop()
        harness.wait_idle()


class EventsAndScenarios(unittest.TestCase):
    def test_a_full_cycle_is_merged_in_order_and_every_event_is_valid(self):
        harness = Harness(self)
        harness.supervisor.submit(command("cycle-0001"))
        harness.wait_idle()
        events = harness.events()
        self.assertEqual([error for event in events for error in demo_contracts.validate("event", event)], [])
        self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)), "one ordered runtime seq, no holes")
        self.assertGreaterEqual(sum(1 for event in events if event["kind"] == "state.transition"), 25)
        self.assertEqual(sum(1 for event in events if event["kind"] == "run.started"), 1, "the controller's own run.started is folded into the runtime's")
        finished = [event for event in events if event["kind"] == "run.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["payload"]["outcome"], finished[0]["payload"]["exit_status"], finished[0]["payload"]["evidence_complete"]), ("placed_new", 0, True))
        origins = [record["origin"] for record in read_json_lines(harness.data / "events.jsonl")[0] if record["origin"]]
        self.assertTrue(all(origin["source_session"].startswith("fake-controller-") and isinstance(origin["seq"], int) for origin in origins),
                        "the controller's own seq and session are kept next to every merged event")
        self.assertEqual([origin["end_offset"] for origin in origins], sorted(origin["end_offset"] for origin in origins))
        self.assertTrue(any(event["producer"]["name"] == "fake-controller" for event in events))

    def test_scenarios_end_with_their_own_outcome(self):
        for scenario, outcome, status in (("already_placed", "already_placed_noop", 0), ("refused", "refused", 3)):
            with self.subTest(scenario=scenario):
                harness = Harness(self, env={"KC_FAKE_SCENARIO": scenario})
                harness.supervisor.submit(command(f"{scenario[:8]}-0001"))
                harness.wait_idle()
                completion = harness.completion(f"{scenario[:8]}-0001")
                self.assertEqual((completion["outcome"], completion["exit_status"], completion["evidence_complete"]), (outcome, status, True))

    def test_a_subscriber_that_never_reads_is_dropped_and_never_slows_the_run(self):
        harness = Harness(self)
        stalled = harness.supervisor.log.subscribe(0, maxsize=4)  # nobody ever takes anything from this queue
        started = time.monotonic()
        harness.supervisor.submit(command("stall-0001"))
        harness.wait_idle()
        self.assertLess(time.monotonic() - started, 15.0)
        self.assertTrue(stalled.dropped)
        self.assertEqual(harness.supervisor.log.dropped_subscribers, 1)
        self.assertEqual(harness.completion("stall-0001")["outcome"], "placed_new")
        resumed = harness.supervisor.log.subscribe(stalled.queue.queue[-1]["seq"])
        self.assertIsNone(resumed.resync, "the ring still holds everything after the last seq the reader saw")
        self.assertEqual(resumed.backlog[-1]["seq"], harness.supervisor.log.seq_cursor)

    def test_a_private_value_in_a_controller_record_is_redacted_or_refused(self):
        from demo_runtime.read_models import Sanitizer

        secret = "kiwi" + "-unit-" + "0042"
        harness = Harness(self, sanitizer=Sanitizer([secret]))
        run = sv._Run(run_id="console-redact", command_id="redact-0001", command_type="observe", run_dir=harness.data / "history" / "console-redact")
        template = {"schema_version": "1", "seq": 1, "run_id": "console-redact", "action_id": None, "observation_id": None, "kind": "diagnostic.text",
                    "producer": {"name": "fake-controller", "version": "0", "artifact_digest": None, "contract_version": "1"}, "source_session": "fake-1", "t_mono_ns": 1,
                    "clock_domain": "laptop_monotonic", "t_wall_utc": None, "reason_code": None, "provenance": None,
                    "payload": {"stream": "stdout", "level": "info", "text": f"connected to {secret} at " + ".".join(("192", "168", "7", "41")), "redacted": False}}
        harness.supervisor._ingest(run, json.dumps(template).encode(), 10)
        harness.supervisor._ingest(run, json.dumps({**template, "seq": 2, "source_session": secret}).encode(), 20)
        text = json.dumps(harness.events())
        self.assertNotIn(secret, text)
        self.assertNotIn(".".join(("192", "168", "7", "41")), text)
        merged = [event for event in harness.events() if event["kind"] == "diagnostic.text"]
        self.assertEqual((len(merged), merged[0]["payload"]["redacted"]), (1, True))
        self.assertEqual(run.daemon_gaps, 1, "the record whose session id was private fails the contract after redaction and is reported as a gap")


class Recovery(unittest.TestCase):
    def test_a_live_child_is_adopted_by_the_next_supervisor_and_never_respawned(self):
        harness = Harness(self, step_s="0.06")
        harness.supervisor.submit(command("adopt-0001"))
        harness.wait_state()
        harness.supervisor.detach()  # what a daemon shutdown does: let go WITHOUT touching the child
        pid = int(harness.launches()[0].split()[0])
        self.assertTrue(sv.is_alive(pid))
        second = harness.new_supervisor()
        self.assertEqual(second.reconcile(), [("adopt-0001", "adopted")])
        self.assertEqual(second.owner()["kind"], "autonomous")
        self.assertEqual(second.submit(command("adopt-0002"))["reason_code"], "run_active")
        harness.wait_idle(second)
        completion = harness.completion("adopt-0001", second)
        self.assertEqual(completion["outcome"], "placed_new", "the outcome of an adopted run comes from the controller's run.finished")
        self.assertTrue(completion["adopted"])
        self.assertEqual(len(harness.launches()), 1, "NEVER a respawn")
        events = harness.events()
        self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
        transitions = [event["payload"]["to_state"] for event in events if event["kind"] == "state.transition"]
        self.assertGreaterEqual(len(transitions), 25)
        self.assertEqual(len(transitions), len([record for record in read_json_lines(harness.data / "events.jsonl")[0]
                                                 if record["event"]["kind"] == "state.transition" and record["origin"]]), "tailing resumed at the recorded offset: nothing repeated")
        self.assertEqual(len({(record["origin"]["source_session"], record["origin"]["seq"]) for record in read_json_lines(harness.data / "events.jsonl")[0] if record["origin"] and record["origin"]["seq"]}),
                         len([record for record in read_json_lines(harness.data / "events.jsonl")[0] if record["origin"] and record["origin"]["seq"]]), "no controller record was merged twice")

    def test_an_adopted_child_can_be_stopped(self):
        harness = Harness(self, step_s="0.06")
        harness.supervisor.submit(command("adoptstop-0001"))
        harness.wait_state()
        harness.supervisor.detach()
        second = harness.new_supervisor()
        second.reconcile()
        self.assertIsNotNone(second.stop())
        harness.wait_idle(second)
        completion = harness.completion("adoptstop-0001", second)
        self.assertEqual((completion["outcome"], completion["stop"]["classification"]), ("stopped", "acknowledged"))

    def test_a_process_with_the_same_scan_id_from_another_data_directory_is_a_stranger(self):
        """Found by running this package's tests in parallel: the scan id is unique per second and per first eight characters of
        a client-minted command id, and two daemons produced the same one. A daemon must never adopt, stop, or be blocked by
        another daemon's controller."""
        import subprocess

        harness = Harness(self)
        scan_id = "console-20260101T000000Z-twin-000"
        ours = harness.data / "history" / scan_id / sv.CONTROLLER_EVENTS_FILE
        idle = "import time; time.sleep(60)"

        def start(events_path):  # an idle process of THIS TEST that only looks like a controller; ended by pid below
            process = subprocess.Popen([sys.executable, "-c", idle, "--scan-id", scan_id], env={**os.environ, "KC_EVENTS_PATH": str(events_path)},
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.addCleanup(process.wait, 10)
            self.addCleanup(process.kill)
            deadline = time.monotonic() + 10
            while scan_id not in " ".join(sv.proc_cmdline(process.pid)) and time.monotonic() < deadline:
                time.sleep(0.01)
            return process

        stranger = start(harness.tmp / "another-daemon" / "history" / scan_id / sv.CONTROLLER_EVENTS_FILE)
        self.assertTrue(sv._carries_scan_id(sv.proc_cmdline(stranger.pid), scan_id), "the stranger really carries our scan id")
        self.assertFalse(sv.is_our_controller(stranger.pid, scan_id, ours))
        self.assertEqual(sv.find_by_scan_id(scan_id, ours), [])
        harness.supervisor.journal.append({"rec": "command", "command_id": "twin-0001", "status": "running", "run_id": scan_id, "scan_id": scan_id, "command_type": "start_cycle",
                                           "reason_code": None, "child": {"pid": stranger.pid, "start_ticks": sv.proc_stat(stranger.pid)[1], "scan_id": scan_id}})
        second = harness.new_supervisor()
        self.assertEqual(second.reconcile(), [("twin-0001", "uncertain")], "even with its pid and start time on record, a process started for another data directory is not adopted")
        self.assertIsNone(second.active_run())
        self.assertTrue(second.resolve("twin-0001", "no_controller_running")["ok"], "and a stranger does not keep OUR command unresolvable")
        self.assertTrue(sv.is_alive(stranger.pid), "the stranger was never signalled")
        twin = start(ours)
        self.assertTrue(sv.is_our_controller(twin.pid, scan_id, ours))
        self.assertEqual(sv.find_by_scan_id(scan_id, ours), [twin.pid])
        linked = harness.tmp / "link-to-data"
        os.symlink(harness.data, linked)
        self.assertTrue(sv.is_our_controller(twin.pid, scan_id, linked / "history" / scan_id / sv.CONTROLLER_EVENTS_FILE), "the same directory through a link is the same directory")

    def test_an_interrupted_command_without_a_child_is_uncertain_and_blocks_until_resolved(self):
        harness = Harness(self)
        harness.supervisor.journal.append({"rec": "command", "command_id": "lost-0001", "status": "accepted", "run_id": "console-20260101T000000Z-lost-000",
                                           "scan_id": "console-20260101T000000Z-lost-000", "command_type": "start_cycle", "reason_code": None})
        second = harness.new_supervisor()
        self.assertEqual(second.reconcile(), [("lost-0001", "uncertain")])
        self.assertEqual(second.result("lost-0001"), {"command_id": "lost-0001", "status": "uncertain", "reason_code": "no_child_evidence", "run_id": "console-20260101T000000Z-lost-000"})
        self.assertEqual(demo_contracts.validate("command_result", second.result("lost-0001")), [])
        self.assertEqual(second.owner()["kind"], "unresolved")
        self.assertEqual(second.block(), ("owner_unresolved", None))
        self.assertEqual(second.submit(command("lost-0002"))["reason_code"], "owner_unresolved", "Start stays BLOCKED while a command is unresolved")
        self.assertEqual(second.resolve("lost-0001", "it is probably fine")["reason_code"], "finding_not_allowed")
        self.assertEqual(second.resolve("nope", "no_controller_running")["reason_code"], "command_not_uncertain")
        answer = second.resolve("lost-0001", "no_controller_running")
        self.assertTrue(answer["ok"])
        # The resolution RECONCILES: the daemon looked at the process table, the run's files and the store itself, and
        # the operator's finding was recorded beside what it found rather than instead of it.
        self.assertEqual((answer["classification"], answer["cleared"], answer["cleared_by"]), ("never_started", True, "machine_evidence"))
        self.assertEqual(answer["attestation"]["finding"], "no_controller_running")
        self.assertEqual(second.owner()["kind"], "free")
        third = harness.new_supervisor()
        self.assertEqual(third.reconcile(), [], "a resolved command is not reconciled again")
        self.assertEqual(third.owner()["kind"], "free", "the resolution is durable")
        self.assertEqual(third.result("lost-0001")["status"], "uncertain", "it stays uncertain: what happened was never learned, only acknowledged")
        self.assertIsNone(third.unresolved_detail(), "and nothing is being asked of the operator any more")
        self.assertEqual(harness.launches(), [], "NEVER a respawn")


# ---- the host command lease ------------------------------------------------------------------------------------------------
#
# A host with its boundary on drops every command that does not carry the live lease, so a controller started without one
# would work through a whole cycle with nothing reaching a motor. The supervisor takes the lease before it spawns, hands the
# two numbers to the child, renews while the run lives and gives it back when the run ends, however it ends.
#
# The lease here is a FAKE with the surface demo_runtime.lease_client.LeaseClient has (the real pair is tested against the
# real host in tests/test_lease_client.py). No socket is opened, no robot exists, and the child is still the fake controller.

class FakeLease:
    """A lease client a test drives. Every call is recorded; nothing leaves this process."""

    def __init__(self, *, session="770011223344556", ttl_s=0.09, refuse_acquire=None, renew_plan=(), release_reply=True, status_says_free=False):
        self.owner_label, self.ttl_s, self.host_session_value = "runtime-under-test", ttl_s, session
        self.generation, self.host_session, self.lost_reason = None, None, None
        self._lock = threading.Lock()
        self._generation_counter = 0
        self._refuse_acquire, self._release_reply, self._status_says_free = refuse_acquire, release_reply, status_says_free
        self._renew_plan = list(renew_plan)
        self.calls, self.acquires, self.renews, self.releases, self.statuses, self.closes = [], 0, 0, 0, 0, 0
        self.last_reply = None

    @property
    def held(self):
        return self.generation is not None

    def _status(self, ok=True, reason=None):
        state = "held" if self.held else "free"
        reply = {"ok": ok, "state": state, "host_session": self.host_session_value, "generation": self._generation_counter,
                 "owner_label": self.owner_label, "ttl_s": self.ttl_s, "expires_in_ms": self.ttl_s * 1000.0 if self.held else None}
        if reason:
            reply["reason"] = reason
        self.last_reply = reply
        return reply

    def acquire(self, ttl_s=None):
        with self._lock:
            self.calls.append("acquire")
            self.acquires += 1
            if self.held:
                return {"ok": False, "reason": "already_held_by_this_client"}
            if self._refuse_acquire:
                return self._status(ok=False, reason=self._refuse_acquire)
            self._generation_counter += 1
            self.generation, self.host_session, self.lost_reason = self._generation_counter, self.host_session_value, None
            return self._status()

    def renew(self):
        with self._lock:
            self.calls.append("renew")
            self.renews += 1
            outcome = self._renew_plan.pop(0) if self._renew_plan else "ok"
            if outcome == "ok":
                return self._status()
            if outcome == "lost":  # the host says the lease is not ours any more
                self.generation, self.host_session, self.lost_reason = None, None, "wrong_generation"
                return self._status(ok=False, reason="wrong_generation")
            return {"ok": False, "reason": "timeout"}  # this side could not reach the host; the lease is still ours

    def release(self):
        with self._lock:
            self.calls.append("release")
            self.releases += 1
            if not self._release_reply:
                return {"ok": False, "reason": "timeout"}
            self.generation, self.host_session = None, None
            return self._status()

    def status(self):
        with self._lock:
            self.calls.append("status")
            self.statuses += 1
            if self._status_says_free:  # the host does not have it; the real client stops believing it does
                self.generation, self.host_session, self.lost_reason = None, None, "no_lease"
                return self._status(ok=True)
            return self._status()

    def snapshot_lease(self):
        reply = self.last_reply
        if not reply or reply.get("state") != "held":
            return None
        return {"host_session": reply["host_session"], "generation": reply["generation"], "expires_in_ms": reply["expires_in_ms"]}

    def last_reply_age_ms(self):
        return None if self.last_reply is None else 0.0

    def close(self):
        self.closes += 1


def live_config(root, backend="boundary_lease_host"):
    """A runtime config of reserved documentation values only. Nothing in it names a machine that exists.

    The backend is NAMED here rather than left out: a configuration that says nothing gets the baseline, which takes no
    lease at all, so the lease tests below would be testing the wrong path in silence. They are the boundary backend's
    tests, and they say so."""
    address = ".".join(("192", "0", "2", "13"))
    return RuntimeConfig.from_mapping({
        "data_root": str(Path(root) / "demo-data"), "saved_pose_file": "calibration/scan-home-current.json",
        "pad_template_dir": "calibration/pad-template", "observation_root": "observations",
        "host": {"address": address, "robot_id": "unit" + "-0042"}, "pi_ssh_target": "operator" + "name" + "@" + address,
        "cameras": {"wrist_id": "usb-" + "Vendor_Cam_" + "SN" + "1234567" + "-video-index0", "front_id": None},
        "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None},
        "execution_backend": backend})


class WithALease(unittest.TestCase):
    """A LIVE supervisor whose child is still the fake controller and whose lease client is the fake above."""

    def setUp(self):
        # The renewer's floor, so a test measures the behaviour and not the wall clock.
        patched = mock.patch.object(sv, "LEASE_RENEW_MIN_S", 0.05)
        patched.start()
        self.addCleanup(patched.stop)

    def harness(self, *, lease=None, env=None, step_s="0.01", entry=FAKE_ENTRY, hardware_enabled=True, mode=None, backend="boundary_lease_host"):
        holder = Path(tempfile.mkdtemp(prefix="kc-lease-config-"))
        self.addCleanup(shutil.rmtree, holder, True)
        return Harness(self, env=env, step_s=step_s, entry=entry, mode=mode or sv.LIVE, config=live_config(holder, backend),
                       hardware_enabled=hardware_enabled, lease=lease)

    def reasons(self, harness):
        return [event.get("reason_code") for event in harness.events()]

    def wait_release(self, lease, count=1):
        """A run leaves the active slot BEFORE its lease goes back: nothing may sit between a dead controller and the
        release of the robot, so the release is the step AFTER that. wait_idle() therefore returns first, and a test that
        asserts on the release has to wait for the release itself. (The runtime handles the same window: a command that
        arrives inside it finds this runtime's own lease still out and asks the host before it adopts or retakes it.)"""
        deadline = time.monotonic() + WAIT_S
        while lease.releases < count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(lease.releases, count, "the lease was not given back")

    def finish(self, harness, lease, releases=1):
        harness.wait_idle()
        self.wait_release(lease, releases)


class ALeaseThatCannotBeTaken(WithALease):
    def test_the_command_is_refused_and_not_one_controller_is_started(self):
        lease = FakeLease(refuse_acquire="lease_held")
        harness = self.harness(lease=lease)
        result = harness.supervisor.submit(command("lease-0001"))
        self.assertEqual((result["status"], result["reason_code"]), ("rejected", "lease_unavailable"),
                         "a controller whose every command the host would drop must never be started")
        self.assertEqual((harness.supervisor.launches, harness.launches()), (0, []))
        self.assertIsNone(harness.supervisor.active_run())
        self.assertEqual(harness.supervisor.owner()["kind"], "free", "a refused command holds nothing")
        self.assertEqual(lease.calls, ["acquire"], "nothing else was asked of the host, and nothing was released")
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["reason"], report["required"]), (False, "lease_held", True))
        self.assertIn("command_rejected.lease_unavailable", self.reasons(harness))
        self.assertEqual(harness.supervisor.submit(command("lease-0001"))["reason_code"], "lease_unavailable",
                         "the recorded refusal is returned again; the host is not asked a second time")
        self.assertEqual(lease.acquires, 1)

    def test_a_runtime_with_no_lease_client_at_all_refuses_the_same_way(self):
        harness = self.harness(lease=None)
        result = harness.supervisor.submit(command("noclient-0001"))
        self.assertEqual((result["status"], result["reason_code"]), ("rejected", "lease_unavailable"))
        self.assertEqual((harness.supervisor.launches, harness.launches()), (0, []))
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["attached"], report["reason"]), (False, False, "lease_client_not_attached"))

    def test_a_synthetic_runtime_needs_no_lease_and_never_asks_for_one(self):
        lease = FakeLease(refuse_acquire="lease_held")
        harness = self.harness(lease=lease, mode=sv.SYNTHETIC)
        self.assertEqual(harness.supervisor.submit(command("synth-0001"))["status"], "running")
        harness.wait_idle()
        self.assertEqual(lease.calls, [], "a synthetic run never reaches a robot, so it takes nothing from one")
        self.assertFalse(harness.supervisor.lease_report()["held"])

    def test_a_plan_only_run_takes_no_lease_and_still_starts(self):
        lease = FakeLease(refuse_acquire="lease_held")
        harness = self.harness(lease=lease)
        self.assertEqual(harness.supervisor.submit(command("plan-0001", args={"plan_only": True}))["status"], "running",
                         "a plan-only run sends no command, so a lease it cannot take does not stop it")
        harness.wait_idle()
        self.assertEqual(lease.calls, [])
        self.assertFalse(sv.lease_required(sv.LIVE, True, {"args": {"plan_only": True}}))
        self.assertTrue(sv.lease_required(sv.LIVE, True, {"args": {"plan_only": False}}))
        self.assertFalse(sv.lease_required(sv.SYNTHETIC, True, {"args": {}}))
        self.assertFalse(sv.lease_required(sv.LIVE, False, {"args": {}}), "without --enable-hardware-commands the command is refused earlier")


class WhatTheControllerIsGiven(WithALease):
    def test_the_child_carries_this_generation_and_host_session_and_never_an_inherited_pair(self):
        lease = FakeLease()
        stale = {sv.LEASE_ENV_GENERATION: "999", sv.LEASE_ENV_SESSION: "111111111111111"}
        harness = self.harness(lease=lease, env=stale, step_s="0.25")
        result = harness.supervisor.submit(command("env-0001"))
        self.assertEqual(result["status"], "running")
        pid = harness.supervisor.table()["env-0001"]["child"]["pid"]
        self.assertEqual(sv.proc_environ_value(pid, sv.LEASE_ENV_GENERATION), "1", "the generation THIS runtime took, not the one in the shell")
        self.assertEqual(sv.proc_environ_value(pid, sv.LEASE_ENV_SESSION), lease.host_session_value)
        harness.supervisor.stop()
        harness.wait_idle()

    def test_a_run_that_needs_no_lease_gets_the_variables_removed_rather_than_inherited(self):
        stale = {sv.LEASE_ENV_GENERATION: "999", sv.LEASE_ENV_SESSION: "111111111111111"}
        harness = self.harness(lease=FakeLease(), env=stale, step_s="0.25")
        self.assertEqual(harness.supervisor.submit(command("env-0002", args={"plan_only": True}))["status"], "running")
        pid = harness.supervisor.table()["env-0002"]["child"]["pid"]
        self.assertIsNone(sv.proc_environ_value(pid, sv.LEASE_ENV_GENERATION),
                          "a stale pair would make the host reject every command, which looks exactly like a robot that is not listening")
        self.assertIsNone(sv.proc_environ_value(pid, sv.LEASE_ENV_SESSION))
        harness.supervisor.stop()
        harness.wait_idle()

    def test_the_two_variable_names_are_the_ones_the_controller_reads(self):
        """The one place this runtime and the controller meet. Read as TEXT: that file imports numpy and the whole
        perception stack, and what has to agree is a literal, not a running import."""
        self.assertEqual((sv.LEASE_ENV_GENERATION, sv.LEASE_ENV_SESSION), ("KC_HOST_LEASE", "KC_HOST_SESSION"))
        controller = (REPO_ROOT / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"KC_HOST_[A-Z_]+", controller)), {sv.LEASE_ENV_GENERATION, sv.LEASE_ENV_SESSION},
                         "the controller reads a different pair: one side of the handover is wrong and every command would be dropped")


class ReleasedOnEveryExitPath(WithALease):
    def test_a_run_that_finishes_gives_the_lease_back(self):
        lease = FakeLease()
        harness = self.harness(lease=lease)
        self.assertEqual(harness.supervisor.submit(command("rel-0001"))["status"], "running")
        self.finish(harness, lease)
        self.assertFalse(lease.held)
        self.assertFalse(harness.supervisor.lease_report()["held"])
        self.assertEqual(harness.completion("rel-0001")["outcome"], "placed_new")
        self.assertIn("lease_released", self.reasons(harness))

    def test_a_controller_that_crashes_gives_the_lease_back(self):
        lease = FakeLease()
        harness = self.harness(lease=lease, env={"KC_FAKE_SCENARIO": "crash"}, step_s="0.01")
        self.assertEqual(harness.supervisor.submit(command("rel-0002"))["status"], "running")
        self.finish(harness, lease)
        self.assertEqual(harness.completion("rel-0002")["exit_status"], 70, "it really did crash")
        self.assertFalse(lease.held, "a lease is not left held by a controller that is gone")

    def test_a_stopped_run_gives_the_lease_back(self):
        lease = FakeLease()
        harness = self.harness(lease=lease, step_s="0.05")
        self.assertEqual(harness.supervisor.submit(command("rel-0003"))["status"], "running")
        harness.wait_state()
        harness.supervisor.stop()
        self.finish(harness, lease)
        self.assertFalse(lease.held)

    def test_a_command_that_never_started_a_controller_gives_the_lease_back(self):
        lease = FakeLease()
        harness = self.harness(lease=lease, entry="packages/demo-runtime/tests/no_such_controller.py")
        result = harness.supervisor.submit(command("rel-0004"))
        self.assertEqual((result["status"], result["reason_code"]), ("finished", "spawn_failed"))
        self.wait_release(lease)
        self.assertEqual((harness.launches(), lease.held), ([], False),
                         "the lease was taken and nothing used it: it goes straight back")

    def test_the_next_command_takes_a_fresh_generation(self):
        lease = FakeLease()
        harness = self.harness(lease=lease)
        for index, command_id in enumerate(("seq-0001", "seq-0002"), start=1):
            self.assertEqual(harness.supervisor.submit(command(command_id))["status"], "running")
            self.assertEqual(harness.supervisor.lease_report()["generation"], index)
            self.finish(harness, lease, releases=index)
        self.assertEqual(lease.acquires, 2, "each run takes a generation of its own")


class RenewingWhileTheRunLives(WithALease):
    def test_a_renewal_that_fails_is_reported_and_neither_ends_the_run_nor_releases_the_lease(self):
        lease = FakeLease(renew_plan=["fail"] * 50)
        harness = self.harness(lease=lease, step_s="0.04")
        self.assertEqual(harness.supervisor.submit(command("renew-0001"))["status"], "running")
        deadline = time.monotonic() + WAIT_S
        while lease.renews < 2 and harness.supervisor.active_run() is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertGreaterEqual(lease.renews, 2, "a failed renewal is tried again, it does not give up after one")
        self.assertEqual(lease.releases, 0, "a controller that is still commanding keeps the lease, whatever this side can reach")
        self.finish(harness, lease)
        completion = harness.completion("renew-0001")
        self.assertEqual((completion["outcome"], completion["exit_status"]), ("placed_new", 0), "the run ran to its normal end")
        self.assertIn("lease_renew_failed", self.reasons(harness))
        self.assertNotIn("lease_lost", self.reasons(harness), "a renewal this side could not send is not evidence that the lease is gone")
        self.assertGreaterEqual(harness.supervisor.lease_renew_failures, 2)

    def test_a_lease_the_host_took_away_stops_being_claimed_and_the_run_still_finishes(self):
        lease = FakeLease(renew_plan=["lost"])
        harness = self.harness(lease=lease, step_s="0.04")
        self.assertEqual(harness.supervisor.submit(command("renew-0002"))["status"], "running")
        deadline = time.monotonic() + WAIT_S
        while lease.renews < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        wait = time.monotonic() + WAIT_S
        while harness.supervisor.lease_report()["held"] and time.monotonic() < wait:
            time.sleep(0.02)
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["reason"]), (False, "wrong_generation"), "the runtime stops claiming what it does not hold")
        self.assertIsNone(harness.supervisor.owner()["lease"])
        self.assertIsNotNone(harness.supervisor.active_run(), "losing the lease does not end a run: the controller is still there")
        harness.wait_idle()
        self.assertEqual(harness.completion("renew-0002")["outcome"], "placed_new")
        self.assertIn("lease_lost", self.reasons(harness))
        self.assertEqual(lease.releases, 0, "there was nothing left to give back, so nothing was taken from anybody")

    def test_a_detached_daemon_stops_renewing_and_leaves_the_lease_with_the_controller(self):
        lease = FakeLease(renew_plan=["ok"] * 200)
        harness = self.harness(lease=lease, step_s="0.25")
        self.assertEqual(harness.supervisor.submit(command("detach-0001"))["status"], "running")
        deadline = time.monotonic() + WAIT_S
        while lease.renews < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        harness.supervisor.detach()
        renews_at_detach = lease.renews
        time.sleep(0.4)
        self.assertEqual(lease.renews, renews_at_detach, "the renewer stopped with the daemon")
        self.assertEqual(lease.releases, 0, "a controller that is still commanding must NOT lose the robot when the daemon ends")
        self.assertTrue(lease.held)


class WhatTheSnapshotSays(WithALease):
    def test_a_lease_the_daemon_does_not_hold_is_never_shown_as_held(self):
        lease = FakeLease()
        lease.acquire()  # the CLIENT holds one; this daemon took nothing
        self.assertTrue(lease.held)
        harness = self.harness(lease=lease)
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["generation"], report["host_session"], report["expires_in_ms"]), (False, None, None, None))
        self.assertEqual((report["reason"], report["attached"], report["required"]), ("no_lease_taken", True, True))
        self.assertIsNone(harness.supervisor.owner()["lease"], "the contract's owner.lease is null unless this daemon holds one")

    def test_a_held_lease_states_its_generation_its_session_and_the_time_left(self):
        lease = FakeLease(ttl_s=120.0, renew_plan=["ok"] * 200)
        harness = self.harness(lease=lease, step_s="0.25")
        self.assertEqual(harness.supervisor.submit(command("snap-0001"))["status"], "running")
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["generation"], report["host_session"]), (True, 1, lease.host_session_value))
        self.assertEqual(report["run_id"], harness.supervisor.active_run()["run_id"])
        self.assertEqual(report["expires_in_ms"], 120_000.0, "the host's own number, less how long ago it said it")
        self.assertEqual(harness.supervisor.owner(), {"kind": "autonomous", "run_id": report["run_id"],
                                                      "lease": {"host_session": lease.host_session_value, "generation": 1, "expires_in_ms": 120_000.0}})
        self.assertEqual(demo_contracts.validate("snapshot#/properties/owner", harness.supervisor.owner()), [])
        harness.supervisor.stop()
        self.finish(harness, lease)
        self.assertIsNone(harness.supervisor.owner()["lease"], "and it is null again once the run is over")

    def test_the_reason_says_why_none_is_held_in_each_case(self):
        self.assertEqual(self.harness(lease=None, mode=sv.SYNTHETIC).supervisor.lease_report()["reason"], "lease_client_not_attached")
        self.assertEqual(self.harness(lease=FakeLease(), mode=sv.SYNTHETIC).supervisor.lease_report()["reason"], "lease_not_required")
        self.assertEqual(self.harness(lease=FakeLease()).supervisor.lease_report()["reason"], "no_lease_taken")

    def test_a_release_the_host_never_answered_stops_the_claim_all_the_same(self):
        lease = FakeLease(release_reply=False)
        harness = self.harness(lease=lease)
        self.assertEqual(harness.supervisor.submit(command("relfail-0001"))["status"], "running")
        self.finish(harness, lease)
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["reason"]), (False, "timeout"), "the runtime claims nothing it cannot prove")
        self.assertIn("lease_release_failed", self.reasons(harness))


class ALeaseThisRuntimeStillHoldsFromTheRunBefore(WithALease):
    """The awkward window: a run leaves the active slot BEFORE its lease goes back, so the next command can arrive while
    this runtime's own lease is still out. What must never happen is a controller started under a generation the host has
    already dropped: every one of its commands would be discarded in silence."""

    def test_a_lease_the_host_confirms_is_adopted_for_the_next_command(self):
        lease = FakeLease()
        lease.acquire()  # as if a release had not gone out yet
        lease.calls.clear()
        harness = self.harness(lease=lease)
        self.assertEqual(harness.supervisor.submit(command("adopt-0001"))["status"], "running")
        self.assertEqual(lease.calls[:2], ["acquire", "status"], "the HOST is asked, not this side's belief")
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["generation"], report["adopted"]), (True, 1, True))
        self.finish(harness, lease)

    def test_a_lease_the_host_does_not_have_is_taken_again_rather_than_adopted(self):
        lease = FakeLease(status_says_free=True)
        lease.acquire()  # the client believes it holds generation 1; the host has already forgotten it
        lease.calls.clear()
        harness = self.harness(lease=lease)
        self.assertEqual(harness.supervisor.submit(command("adopt-0002"))["status"], "running")
        self.assertEqual(lease.calls[:3], ["acquire", "status", "acquire"])
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["generation"], report["adopted"]), (True, 2, False),
                         "a fresh generation, because commands under the old one would all be dropped")
        harness.wait_idle()

    def test_a_host_that_answers_nothing_refuses_the_command_instead_of_guessing(self):
        lease = FakeLease(status_says_free=True, refuse_acquire="lease_held")
        lease._refuse_acquire = None
        lease.acquire()
        lease._refuse_acquire = "lease_held"  # the host now says somebody else has it
        lease.calls.clear()
        harness = self.harness(lease=lease)
        result = harness.supervisor.submit(command("adopt-0003"))
        self.assertEqual((result["status"], result["reason_code"]), ("rejected", "lease_unavailable"))
        self.assertEqual((harness.supervisor.launches, harness.launches()), (0, []))


class TheLeasePathCommandsNothing(WithALease):
    def test_taking_renewing_and_releasing_never_builds_a_robot_client_or_a_lease_client(self):
        from demo_runtime import fresh_client, lease_client

        def explode(*_args, **_kwargs):
            raise AssertionError("the daemon tried to build a client while handling the lease")

        lease = FakeLease(renew_plan=["ok"] * 200)
        harness = self.harness(lease=lease, step_s="0.05")
        with mock.patch.object(fresh_client, "fresh_client", explode), mock.patch.object(lease_client, "LeaseClient", explode):
            self.assertEqual(harness.supervisor.submit(command("quiet-0001"))["status"], "running")
            self.finish(harness, lease)
        self.assertEqual(lease.acquires, 1)
        self.assertEqual(set(lease.calls) - {"acquire", "renew", "release", "status"}, set(),
                         "the lease client was asked for the four lease operations and nothing else")
        self.assertEqual(harness.completion("quiet-0001")["outcome"], "placed_new")


# ---- the baseline backend: the proven path, and what owns the robot on it ----------------------------------------------------
#
# The motor host in its NORMAL mode asks for no lease and drops nothing, so a lease taken against it would be a number from a
# boundary that is not running. The supervisor therefore takes none and attaches none, and it says in plain words that the only
# thing owning the robot then is its own single run slot on this computer. Nothing here weakens the boundary path: the class
# above runs unchanged, against a config that names that backend.

class BaselineBackendCase(WithALease):
    """The harness both baseline classes below use. It carries no test of its own, so neither class re-runs the other's."""

    def baseline(self, **options):
        return self.harness(backend="baseline_normal_host", **options)

    def host_preflight_only(self, probe):
        """The supervisor's preflight, narrowed to the ONE check these tests are about. The local checks have no fixture
        files in this temporary data root and would fail first; what has to be shown is that the host check's answer
        reaches the submit. The check itself is the real one, with a fake probe."""
        def report(config, action, motor_host=probe):
            checks = () if motor_host is None else (pf._check_motor_host(config, motor_host),)
            return pf.PreflightReport(ok=all(check.ok for check in checks), checks=checks, action_kind=None, config_digest="0" * 64)

        return report


class TheBaselineBackend(BaselineBackendCase):
    def test_the_gate_is_the_backend_first_and_then_everything_it_always_was(self):
        baseline, boundary = "baseline_normal_host", "boundary_lease_host"
        moving = {"args": {"plan_only": False}}
        self.assertTrue(sv.lease_required(sv.LIVE, True, moving, boundary), "the deferred path still needs one")
        self.assertFalse(sv.lease_required(sv.LIVE, True, moving, baseline), "the NORMAL host asks for no lease, so none is taken")
        self.assertFalse(sv.lease_required(sv.LIVE, True, {"args": {"plan_only": True}}, boundary))
        self.assertFalse(sv.lease_required(sv.SYNTHETIC, True, moving, baseline))
        self.assertFalse(sv.lease_required(sv.SYNTHETIC, True, moving, boundary), "synthetic takes none today and gains none here")
        self.assertFalse(sv.lease_required(sv.LIVE, False, moving, baseline))
        self.assertTrue(sv.lease_required(sv.LIVE, True, moving), "a caller that says nothing is told a lease is needed: the refusing answer")

    def test_a_live_hardware_command_runs_the_controller_with_no_lease_taken_and_none_asked_for(self):
        lease = FakeLease(refuse_acquire="lease_held")  # attached, and it would refuse: it is never asked
        harness = self.baseline(lease=lease)
        self.assertEqual(harness.supervisor.submit(command("base-0001"))["status"], "running",
                         "the proven path is not blocked by a lease that belongs to the other backend")
        harness.wait_idle()
        self.assertEqual(lease.calls, [], "nothing was asked of a host boundary that is not running")
        self.assertEqual(len(harness.launches()), 1)
        self.assertEqual(harness.completion("base-0001")["outcome"], "placed_new")

    def test_the_controller_carries_no_lease_metadata_not_even_one_inherited_from_the_shell(self):
        stale = {sv.LEASE_ENV_GENERATION: "999", sv.LEASE_ENV_SESSION: "111111111111111"}
        harness = self.baseline(lease=FakeLease(), env=stale, step_s="0.25")
        self.assertEqual(harness.supervisor.submit(command("base-0002"))["status"], "running")
        pid = harness.supervisor.table()["base-0002"]["child"]["pid"]
        self.assertIsNone(sv.proc_environ_value(pid, sv.LEASE_ENV_GENERATION), "the NORMAL host expects no lease metadata, and a stale pair is not metadata")
        self.assertIsNone(sv.proc_environ_value(pid, sv.LEASE_ENV_SESSION))
        harness.supervisor.stop()
        harness.wait_idle()

    def test_the_controller_is_started_with_the_proven_arguments_and_the_scan_id_of_the_run(self):
        harness = self.baseline(step_s="0.25")
        result = harness.supervisor.submit(command("base-0003"))
        self.assertEqual(result["status"], "running")
        pid = harness.supervisor.table()["base-0003"]["child"]["pid"]
        argv = sv.proc_cmdline(pid)
        address = ".".join(("192", "0", "2", "13"))
        self.assertEqual(["--do", "cycle", "--remote-ip", address, "--robot-id", "unit" + "-0042", "--pi-ssh", "operator" + "name" + "@" + address,
                          "--scan-id", result["run_id"]], argv[2:], "the same entry and the same flags the proven launcher passes")
        harness.supervisor.stop()
        harness.wait_idle()

    def test_it_says_what_owns_the_robot_and_claims_no_enforcement_on_the_robot(self):
        harness = self.baseline(lease=FakeLease())
        execution = harness.supervisor.execution_report()
        self.assertEqual(("baseline_normal_host", "runtime_single_run_slot", False),
                         (execution["backend"], execution["ownership_basis"], execution["robot_side_enforcement"]))
        self.assertIn("single run slot", execution["detail"])
        self.assertIn("any other process on that computer can still open the servo bus", execution["detail"],
                      "the sentence states the gap instead of leaving it to be discovered")
        report = harness.supervisor.lease_report()
        self.assertEqual((report["held"], report["required"], report["reason"]), (False, False, "baseline_backend_takes_no_host_lease"))
        self.assertEqual((report["generation"], report["host_session"], report["run_id"]), (None, None, None))
        self.assertIsNone(harness.supervisor.snapshot_lease(), "no lease is shown for a backend that takes none")
        self.assertIsNone(harness.supervisor.owner()["lease"])

    def test_the_boundary_backend_says_the_other_thing_and_only_while_it_really_holds_the_lease(self):
        lease = FakeLease()
        harness = self.harness(lease=lease)  # the deferred backend, unchanged
        before = harness.supervisor.execution_report()
        self.assertEqual(("boundary_lease_host", "host_command_lease", None), (before["backend"], before["ownership_basis"], before["robot_side_enforcement"]),
                         "nothing on the robot has answered yet, so nothing is established")
        self.assertEqual(harness.supervisor.submit(command("bound-0001"))["status"], "running")
        holding = harness.supervisor.execution_report()
        self.assertIs(True, holding["robot_side_enforcement"], "the host granted the lease, so the boundary is answering")
        self.assertIn("drops every command that does not carry the live generation", holding["detail"])
        self.assertEqual(demo_contracts.validate("common#/$defs/safe_text", holding["detail"]), [], "every sentence that reaches a screen passes the contract's tripwire")
        self.finish(harness, lease)
        self.assertIsNone(harness.supervisor.execution_report()["robot_side_enforcement"], "the lease went back; the claim goes with it")

    def test_the_single_run_slot_is_the_ownership_so_a_second_command_is_still_refused(self):
        harness = self.baseline(step_s="0.25")
        self.assertEqual(harness.supervisor.submit(command("base-0004"))["status"], "running")
        self.assertEqual(harness.supervisor.submit(command("base-0005"))["reason_code"], "run_active",
                         "one run at a time is the whole of the ownership on this backend, so it holds")
        self.assertEqual(harness.supervisor.owner()["kind"], "autonomous")
        self.assertEqual(harness.supervisor.launches, 1, "the refused command started no second controller")
        harness.supervisor.stop()
        harness.wait_idle()
        self.assertEqual(harness.supervisor.owner()["kind"], "free")

    def test_a_start_with_no_motor_host_running_is_refused_by_name_and_starts_no_controller(self):
        asked = []
        harness = self.baseline(lease=FakeLease())
        harness.supervisor._preflight = self.host_preflight_only(lambda target: asked.append(target) or "not_running")
        result = harness.supervisor.submit(command("host-0001"))
        self.assertEqual((result["status"], result["reason_code"]), ("rejected", "motor_host_not_running"),
                         "the proven launcher refuses the same way, and it refuses BEFORE anything is commanded")
        self.assertEqual((harness.supervisor.launches, harness.launches()), (0, []), "nothing was started to fail at connect later")
        self.assertIsNone(harness.supervisor.active_run())
        self.assertEqual(harness.supervisor.owner()["kind"], "free")
        self.assertEqual(len(asked), 1, "the robot computer was asked exactly once, for this submit")
        self.assertEqual(harness.supervisor.block("cycle")[0], "motor_host_not_running", "and the same refusal is what a blocked capability shows")

    def test_the_same_command_runs_once_that_host_answers_that_it_is_up(self):
        harness = self.baseline(lease=FakeLease())
        harness.supervisor._preflight = self.host_preflight_only(lambda target: "running_normal")
        self.assertEqual(harness.supervisor.submit(command("host-0002"))["status"], "running")
        harness.wait_idle()
        self.assertEqual(harness.completion("host-0002")["outcome"], "placed_new")

    def test_a_synthetic_runtime_is_untouched_by_either_backend(self):
        for backend in ("baseline_normal_host", "boundary_lease_host"):
            with self.subTest(backend=backend):
                lease = FakeLease(refuse_acquire="lease_held")
                harness = self.harness(lease=lease, mode=sv.SYNTHETIC, backend=backend)
                self.assertEqual(harness.supervisor.submit(command("synth-" + backend[:5]))["status"], "running")
                harness.wait_idle()
                self.assertEqual(lease.calls, [], "a synthetic run never reaches a robot, so it takes nothing from one")
                self.assertEqual(harness.supervisor.lease_report()["reason"], "lease_not_required")


class FakeReadiness:
    """A reading this test decides. The reader itself is exercised in tests/test_preflight.py against a fake probe; what
    matters here is only WHEN the supervisor consults one and what it does with the answer. asked counts every look, so
    a test can show that the submit path never takes one."""

    def __init__(self, state="running_normal"):
        self.state, self.asked = state, 0

    def reading(self):
        self.asked += 1
        return pf.readiness_of(self.state, None if self.state == "never_read" else 1000.0)


class WhatTheScreenSaysAboutTheRobotItCannotSee(BaselineBackendCase):
    """An enabled software capability is not a ready robot. Everything the supervisor checks before this is a fact about
    THIS laptop, and a robot that is off the network or has no motor host running passes every one of them; before this,
    the refusal arrived only when Start was pressed. The reading is consulted for a snapshot and NEVER for a submit: a
    start has to rest on the question asked fresh, at the moment it is asked."""

    def ready(self, harness, state):
        harness.supervisor.readiness = FakeReadiness(state)
        return harness.supervisor.readiness

    def local_failure(self, check_id, reason_code):
        def report(config, action, motor_host=None):
            failed = pf.PreflightCheck(check_id, False, reason_code, "an input this run needs is not on this laptop")
            return pf.PreflightReport(ok=False, checks=(failed,), action_kind=None, config_digest="0" * 64)

        return report

    def test_each_thing_that_can_be_wrong_with_the_robot_reaches_the_screen_as_its_own_reason(self):
        expected = {"unreachable": "motor_host_unreachable", "not_running": "motor_host_not_running", "scan_mode": "motor_host_in_scan_mode",
                    "mode_unknown": "motor_host_mode_unknown", "never_read": "robot_readiness_not_read", "reading_stale": "robot_readiness_stale"}
        harness = self.baseline(lease=FakeLease())
        sentences = set()
        for state, reason_code in expected.items():
            with self.subTest(state=state):
                self.ready(harness, state)
                code, detail = harness.supervisor.block()
                self.assertEqual(reason_code, code)
                self.assertTrue(detail)
                self.assertEqual(demo_contracts.validate("common#/$defs/safe_text", detail), [], "every sentence that reaches a screen passes the contract's tripwire")
                sentences.add(detail)
        self.assertEqual(len(expected), len(sentences), "each one says its own thing: unreachable and no-host-running are different facts")

    def test_a_running_host_leaves_every_command_capability_enabled(self):
        harness = self.baseline(lease=FakeLease())
        reading = self.ready(harness, "running_normal")
        self.assertIsNone(harness.supervisor.block())
        self.assertEqual(1, reading.asked, "the snapshot looked once and did not probe anything itself")

    def test_the_control_that_would_start_motion_carries_the_blocker_instead_of_looking_ready(self):
        harness = self.baseline(lease=FakeLease())
        self.ready(harness, "not_running")
        code, detail = harness.supervisor.block()
        served = rm.capabilities(block_code=code, block_detail=detail, run_active=False, replay_only=False)
        start = next(capability for capability in served if capability["id"] == "start_cycle")
        self.assertEqual(("blocked", "motor_host_not_running", detail, "robot"), (start["state"], start["reason_code"], start["reason"], start["dependency"]))
        self.assertEqual(demo_contracts.validate("capability", start), [])

    def test_a_reading_nobody_has_taken_yet_is_unknown_and_is_never_drawn_as_ready(self):
        harness = self.baseline(lease=FakeLease())
        for state in ("never_read", "reading_stale"):
            with self.subTest(state=state):
                self.ready(harness, state)
                code, detail = harness.supervisor.block()
                served = rm.capabilities(block_code=code, block_detail=detail, run_active=False, replay_only=False)
                start = next(capability for capability in served if capability["id"] == "start_cycle")
                self.assertEqual("unknown", start["state"], "staleness must never read as readiness")
                self.assertEqual(demo_contracts.validate("capability", start), [])

    def test_a_cached_reading_that_says_the_robot_is_ready_cannot_satisfy_a_submit(self):
        """THE POINT OF THE WHOLE THING. A snapshot may act on an answer seconds old because all it does is draw a
        control. A start may not, so the submit asks again and the cache has no vote there, whatever it holds."""
        asked = []
        harness = self.baseline(lease=FakeLease())
        harness.supervisor._preflight = self.host_preflight_only(lambda target: asked.append(target) or "not_running")
        reading = self.ready(harness, "running_normal")
        result = harness.supervisor.submit(command("ready-0001"))
        self.assertEqual((result["status"], result["reason_code"]), ("rejected", "motor_host_not_running"))
        self.assertEqual(1, len(asked), "the robot computer was asked again, fresh, for this submit")
        self.assertEqual(0, reading.asked, "the cached reading was not consulted on the submit path at all")
        self.assertEqual((harness.supervisor.launches, harness.launches()), (0, []), "nothing was started on the strength of an old reading")

    def test_a_cached_reading_that_says_the_robot_is_down_does_not_refuse_a_submit_whose_fresh_answer_passes(self):
        """The other direction of the same rule: the cache decides nothing about a submit, so it cannot hold one back
        either. A robot brought up a moment ago is commandable before the next background reading lands."""
        harness = self.baseline(lease=FakeLease())
        harness.supervisor._preflight = self.host_preflight_only(lambda target: "running_normal")
        reading = self.ready(harness, "reading_stale")
        self.assertEqual(harness.supervisor.submit(command("ready-0002"))["status"], "running")
        harness.wait_idle()
        self.assertEqual(0, reading.asked)
        self.assertEqual(harness.completion("ready-0002")["outcome"], "placed_new")

    def test_a_fault_on_this_laptop_is_still_reported_before_a_fact_about_the_robot(self):
        harness = self.baseline(lease=FakeLease())
        harness.supervisor._preflight = self.local_failure("saved_pose", "saved_pose_missing")
        self.ready(harness, "not_running")
        self.assertEqual("saved_pose_missing", harness.supervisor.block()[0], "the local checks keep the order they always had")

    def test_a_supervisor_with_nothing_to_read_behaves_exactly_as_it_did_before(self):
        harness = self.baseline(lease=FakeLease())
        self.assertIsNone(harness.supervisor.readiness, "a harness builds no reader; one is handed in by whoever owns its thread")
        self.assertIsNone(harness.supervisor.block())
        self.assertIsNone(harness.supervisor.readiness_report())

    def test_a_synthetic_runtime_asks_nothing_about_a_robot_because_there_is_no_robot_to_be_ready(self):
        harness = self.harness(lease=FakeLease(), mode=sv.SYNTHETIC, backend="baseline_normal_host")
        reading = self.ready(harness, "not_running")
        self.assertIsNone(harness.supervisor.block(), "a synthetic run reaches no robot, so no robot can block it")
        self.assertEqual(0, reading.asked)
        self.assertEqual(harness.supervisor.submit(command("synth-ready"))["status"], "running")
        harness.wait_idle()

    def test_the_status_report_carries_the_last_reading_and_how_old_it_is(self):
        harness = self.baseline(lease=FakeLease())
        self.ready(harness, "not_running")
        report = harness.supervisor.readiness_report()
        self.assertEqual(("not_running", False, "motor_host_not_running", 1000.0), (report["state"], report["ready"], report["reason_code"], report["age_ms"]))
        self.assertIn("this rests on a reading taken 1 s ago", report["detail"])


# ---- a mission's run (start_mission_run) ----------------------------------------------------------------------------------------

sys.path.insert(0, str(REPO_ROOT / "autonomy" / "manipulation"))
import target_binding  # noqa: E402  (standard library only: the mission target file's schema)

MISSION_ID = "mission-0123456789ab"
MISSION_TARGETS = [{"target_id": "cube_near", "requires_capability": False}, {"target_id": "cube_far", "requires_capability": False}]


def mission_run(cancel=None, *, target_id="cube_near", started=None, writer=None):
    """A MissionRun whose writer writes a real first-execution target file with target_binding's own writer."""
    cancel = cancel or threading.Event()

    def write_target(run_dir, run_id):
        path = str(Path(run_dir) / sv.MISSION_TARGET_FILE)
        document = target_binding.mission_target_document(
            mission_id=MISSION_ID, target_id=target_id, run_id=run_id,
            targets=target_binding.declared_targets(MISSION_TARGETS), capability=None,
            result_path=str(Path(run_dir) / sv.MISSION_RESULT_FILE))
        target_binding.write_mission_target(path, document)
        return path

    return sv.MissionRun(mission_id=MISSION_ID, target_id=target_id, cancel=cancel, write_target=writer or write_target,
                         on_run_started=(started.append if started is not None else None))


class AMissionRunGoesThroughTheSameAdmissionAsASubmit(unittest.TestCase):
    """start_mission_run: the motion gate twice, block() minus only the mission's own run-slot gate, the cancel check at
    publication, and the mission target file written into the run directory before the spawn."""

    def test_the_name_of_the_variable_is_the_one_the_controller_reads(self):
        self.assertEqual(sv.MISSION_TARGET_ENV, target_binding.MISSION_TARGET_ENV)

    def test_refused_without_motion_permission_and_nothing_is_started(self):
        calls = []

        def gate():
            calls.append(1)
            return ("robot_session_not_confirmed", "not confirmed"), None

        harness = Harness(self, motion_gate=gate)
        answer = harness.supervisor.start_mission_run(mission_run())
        self.assertEqual((answer["ok"], answer["status"], answer["reason_code"]), (False, "rejected", "robot_session_not_confirmed"))
        self.assertEqual(len(calls), 1, "the gate is asked first, before anything else is asked")
        self.assertEqual(harness.launches(), [])
        self.assertEqual(harness.supervisor.table()[answer["command_id"]]["mission"], {"mission_id": MISSION_ID, "target_id": "cube_near"})

    def test_a_detached_supervisor_starts_no_mission_run_at_entry_or_at_publication(self):
        harness = Harness(self)
        harness.supervisor.detach()
        answer = harness.supervisor.start_mission_run(mission_run())
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, sv.MISSION_RUNTIME_DETACHED))
        self.assertEqual(harness.launches(), [])
        # Detached between the entry check and publication: the gate is asked twice, and the second ask detaches.
        calls = []
        holder = {}

        def gate():
            calls.append(1)
            if len(calls) == 2:
                holder["supervisor"].detach()
            return None, {"session_uuid": "ab" * 8}

        harness = Harness(self, motion_gate=gate)
        holder["supervisor"] = harness.supervisor
        answer = harness.supervisor.start_mission_run(mission_run())
        self.assertEqual(len(calls), 2, "the second ask is the one right before publication")
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, sv.MISSION_RUNTIME_DETACHED))
        self.assertEqual(harness.launches(), [], "a supervisor that let go of its runs started a controller")

    def test_a_live_runtime_without_a_motion_gate_refuses_a_mission_run(self):
        class NoConfig:
            pass

        harness = Harness(self, mode=sv.LIVE, config=NoConfig(), hardware_enabled=True)
        answer = harness.supervisor.start_mission_run(mission_run())
        self.assertEqual(answer["reason_code"], sv.MISSION_MOTION_GATE_ABSENT)
        self.assertEqual(harness.launches(), [])

    def test_refused_with_hardware_commands_off(self):
        class NoConfig:
            pass

        harness = Harness(self, mode=sv.LIVE, config=NoConfig(), hardware_enabled=False, motion_gate=lambda: (None, {"session_uuid": "ab" * 8}))
        answer = harness.supervisor.start_mission_run(mission_run())
        self.assertEqual((answer["status"], answer["reason_code"]), ("rejected", "hardware_commands_not_enabled"))
        self.assertEqual(harness.launches(), [])

    def test_only_the_missions_own_run_slot_gate_is_left_out(self):
        harness = Harness(self, run_slot_gate=lambda: "mission_running")
        self.assertEqual(harness.supervisor.submit(command("slot-0001"))["reason_code"], "mission_running", "a command is still refused")
        answer = harness.supervisor.start_mission_run(mission_run())
        self.assertTrue(answer["ok"], answer)
        harness.wait_idle()
        blocked = Harness(self, run_slot_gate=lambda: "mission_running", external_probe=lambda: [4242])
        self.assertEqual(blocked.supervisor.start_mission_run(mission_run())["reason_code"], "owner_external",
                         "every other refusal of block() still applies")

    def test_the_run_is_started_with_the_target_file_the_mission_fields_and_the_proven_argv(self):
        started = []
        admitted = {"session_uuid": "cd" * 8, "build_identity": "ef" * 32}
        harness = Harness(self, motion_gate=lambda: (None, admitted))
        answer = harness.supervisor.start_mission_run(mission_run(started=started))
        self.assertTrue(answer["ok"], answer)
        harness.wait_idle()
        run_id = answer["run_id"]
        self.assertEqual(started, [run_id], "the mission's record names the run before the spawn")
        run_dir = harness.data / "history" / run_id
        target = target_binding.load_mission_target(str(run_dir / sv.MISSION_TARGET_FILE))
        self.assertEqual((target.mission_id, target.target_id, target.run_id), (MISSION_ID, "cube_near", run_id))
        result = target_binding.load_result(target)
        self.assertEqual((result["outcome"], result["run_id"]), ("placed_new", run_id), "the child read KC_MISSION_TARGET and answered for THIS run")
        accepted = [record for record in read_json_lines(harness.data / "journal.jsonl")[0]
                    if record.get("command_id") == answer["command_id"] and record.get("status") == "accepted"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["mission"], {"mission_id": MISSION_ID, "target_id": "cube_near"})
        self.assertEqual(accepted[0]["robot_admission"], admitted)
        self.assertEqual(accepted[0]["command"]["type"], "start_cycle")
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertEqual(demo_contracts.validate("manifest", manifest), [], "the contract manifest is unchanged")
        self.assertEqual(harness.completion(answer["command_id"])["outcome"], "placed_new")
        self.assertEqual(len(harness.launches()), 1)

    def test_a_stop_before_publication_starts_nothing(self):
        cancel = threading.Event()
        harness = Harness(self)
        calls = []

        def gate():
            calls.append(1)
            if len(calls) == 2:
                # THE DAEMON'S ORDER, landing between the admission and the publication: the cancel event first, then the
                # supervisor's stop, which finds no run to stop.
                cancel.set()
                self.assertIsNone(harness.supervisor.stop())
            return None, {"session_uuid": "ab" * 8}

        harness.supervisor._motion_gate = gate
        answer = harness.supervisor.start_mission_run(mission_run(cancel))
        self.assertEqual((answer["status"], answer["reason_code"]), ("rejected", sv.MISSION_STOPPED_AT_PUBLICATION))
        self.assertEqual(harness.supervisor.launches, 0)
        self.assertEqual(harness.launches(), [])
        self.assertIsNone(harness.supervisor.active_run())
        self.assertEqual(harness.supervisor.start_mission_run(mission_run(cancel))["reason_code"], sv.MISSION_STOPPING,
                         "a stopped mission's next run is refused on entry")

    def test_a_stop_after_publication_is_a_sigint_to_the_child(self):
        cancel = threading.Event()
        harness = Harness(self, step_s="0.2")
        answer = harness.supervisor.start_mission_run(mission_run(cancel))
        self.assertTrue(answer["ok"], answer)
        harness.wait_state()
        cancel.set()
        stopped = harness.supervisor.stop()
        self.assertEqual(stopped["run_id"], answer["run_id"])
        harness.wait_idle()
        completion = harness.completion(answer["command_id"])
        self.assertEqual((completion["outcome"], completion["exit_status"]), ("stopped", 4))
        self.assertIn(completion["stop"]["mechanism"], ("pidfd", "own_child_handle"))
        self.assertEqual(len(harness.launches()), 1)

    def test_a_target_file_that_cannot_be_written_starts_nothing(self):
        def refuses(run_dir, run_id):
            raise target_binding.MissionFileRefused("binding_target_not_declared", "target_id")

        harness = Harness(self)
        answer = harness.supervisor.start_mission_run(mission_run(writer=refuses))
        self.assertEqual(answer["reason_code"], sv.MISSION_TARGET_REFUSED)
        self.assertEqual(harness.launches(), [])
        self.assertIsNone(harness.supervisor.active_run())

    def test_a_legacy_command_never_inherits_a_mission_target(self):
        harness = Harness(self, env={"KC_MISSION_TARGET": "/nonexistent/mission-target.json"})
        answer = harness.supervisor.submit(command("legacy-0001"))
        harness.wait_idle()
        run_dir = harness.data / "history" / answer["run_id"]
        self.assertFalse((run_dir / sv.MISSION_RESULT_FILE).exists())
        self.assertEqual(harness.completion("legacy-0001")["outcome"], "placed_new", "the inherited value did not reach the child")



class TheDesktopPerceptionReachesOnlyALiveRunOnTheRobot(unittest.TestCase):
    """_child_environment and the desktop perception (config perception.remote): the four variables, the resident session
    the run was admitted under and the runtime's mode are REMOVED first and without a condition, and set only where they
    belong. A desktop runtime never hands the perception on, whatever its configuration says: the reverse forward's port
    exists on the robot only."""

    ROBOT_BUILD = "cd" * 32
    SESSION = "0a" * 16
    PIN = {"config.json": "a" * 64, "model.safetensors": "b" * 64}
    REVISION = "0123456789abcdef0123456789abcdef01234567"
    STALE = {sv.PERCEPTION_PORT_ENV: "5999", sv.PERCEPTION_DEADLINE_ENV: "1.0", sv.PERCEPTION_MODEL_REVISION_ENV: "f" * 40,
             sv.PERCEPTION_MODEL_FILES_ENV: "sha256:" + "f" * 64, sv.RESIDENT_SESSION_ENV: "ff" * 16, sv.RUNTIME_MODE_ENV: "live"}

    def raw(self, *, local=True, remote=True) -> dict:
        tmp = tempfile.mkdtemp(prefix="kc-sv-perception-")
        self.addCleanup(shutil.rmtree, tmp, True)
        robot = ({"link": "local", "local_admin_socket_path": "/run/example/cake-admin.sock", "expected_build_identity": self.ROBOT_BUILD} if local else
                 {"remote_admin_socket_path": "/run/example/remote.sock", "local_admin_socket_path": "/run/example/local.sock", "expected_build_identity": self.ROBOT_BUILD})
        raw = {"data_root": os.path.join(tmp, "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad", "observation_root": "observations",
               "host": {"address": "127.0.0.1", "robot_id": "example_robot"}, "pi_ssh_target": "", "cameras": {"wrist_id": "usb-test-wrist", "front_id": None},
               "cake": {"admin_probe_path": "/opt/example/admin-probe", "robot": robot}}
        if remote:
            raw["perception"] = {"remote": {"port": 45590, "deadline_s": 42.5, "model_revision": self.REVISION, "model_files_sha256": dict(self.PIN)}}
        return raw

    def environment(self, raw, *, mode=sv.LIVE, inherited=None, admission=None) -> dict:
        tmp = tempfile.mkdtemp(prefix="kc-sv-perception-env-")
        self.addCleanup(shutil.rmtree, tmp, True)
        supervisor = sv.Supervisor(data_dir=tmp, journal=Journal(os.path.join(tmp, "journal.jsonl")), event_log=EventLog(os.path.join(tmp, "events.jsonl")),
                                   runtime_session="rt-test-perception", mode=mode, source_root=REPO_ROOT, controller_entry="unused.py",
                                   config=None if raw is None else RuntimeConfig.from_mapping(raw), extra_env=dict(inherited or {}))
        run = sv._Run(run_id="console-perception", command_id="perception-0001", command_type="start_cycle", run_dir=Path(tmp) / "history" / "x")
        run.robot_admission = admission

        class Bundle:
            root, digest = Path(tmp), "0" * 64

        return supervisor._child_environment(run, Bundle())

    def test_a_live_run_on_the_robot_gets_all_four_and_its_admitted_session(self):
        from demo_runtime.config import model_files_digest

        env = self.environment(self.raw(), inherited=self.STALE, admission={"session_uuid": self.SESSION, "build_identity": self.ROBOT_BUILD})
        self.assertEqual(("45590", "42.5", self.REVISION, model_files_digest(self.PIN)),
                         (env[sv.PERCEPTION_PORT_ENV], env[sv.PERCEPTION_DEADLINE_ENV], env[sv.PERCEPTION_MODEL_REVISION_ENV], env[sv.PERCEPTION_MODEL_FILES_ENV]))
        self.assertEqual((self.SESSION, "live"), (env[sv.RESIDENT_SESSION_ENV], env[sv.RUNTIME_MODE_ENV]))

    def test_a_desktop_runtime_never_hands_it_on_and_drops_what_it_inherited(self):
        env = self.environment(self.raw(local=False), inherited=self.STALE, admission={"session_uuid": self.SESSION})
        for name in sv.PERCEPTION_ENV_NAMES:
            self.assertNotIn(name, env)
        self.assertEqual(self.SESSION, env[sv.RESIDENT_SESSION_ENV], "the session names the run wherever it runs")

    def test_without_the_section_nothing_is_set_and_nothing_inherited_survives(self):
        env = self.environment(self.raw(remote=False), inherited=self.STALE)
        for name in sv.PERCEPTION_ENV_NAMES + (sv.RESIDENT_SESSION_ENV,):
            self.assertNotIn(name, env)
        self.assertEqual("live", env[sv.RUNTIME_MODE_ENV])

    def test_a_synthetic_runtime_gets_neither_the_perception_nor_a_session_and_says_synthetic(self):
        env = self.environment(None, mode=sv.SYNTHETIC, inherited=self.STALE, admission={"session_uuid": self.SESSION})
        for name in sv.PERCEPTION_ENV_NAMES + (sv.RESIDENT_SESSION_ENV,):
            self.assertNotIn(name, env)
        self.assertEqual("synthetic", env[sv.RUNTIME_MODE_ENV], "the mode is this supervisor's, never the one the shell exported")

    def test_the_placement_envelope_reaches_the_child_only_from_the_config_and_never_from_the_shell(self):
        stale = {**self.STALE, sv.PLACEMENT_ENVELOPE_ENV: "projected", sv.PLACEMENT_ENVELOPE_ATTENDED_ENV: "stale-label"}
        env = self.environment(self.raw(), inherited=stale)
        self.assertNotIn(sv.PLACEMENT_ENVELOPE_ENV, env, "the shell's word reached the child")
        self.assertNotIn(sv.PLACEMENT_ENVELOPE_ATTENDED_ENV, env)
        raw = self.raw()
        raw["placement_envelope"] = ["projected"]
        env = self.environment(raw, inherited=stale)
        self.assertEqual("projected", env[sv.PLACEMENT_ENVELOPE_ENV])
        self.assertNotIn(sv.PLACEMENT_ENVELOPE_ATTENDED_ENV, env)
        raw["placement_envelope"], raw["placement_envelope_attended"] = ["projected", "near_reverse"], "qual-2026-09-27-a"
        env = self.environment(raw, inherited=stale, mode=sv.SYNTHETIC)
        self.assertEqual(("projected,near_reverse", "qual-2026-09-27-a"), (env[sv.PLACEMENT_ENVELOPE_ENV], env[sv.PLACEMENT_ENVELOPE_ATTENDED_ENV]))
        self.assertEqual(("KC_PLACEMENT_ENVELOPE", "KC_PLACEMENT_ENVELOPE_ATTENDED"), (sv.PLACEMENT_ENVELOPE_ENV, sv.PLACEMENT_ENVELOPE_ATTENDED_ENV))

    def test_the_forced_side_offset_reaches_the_child_only_from_the_config_and_never_from_the_shell(self):
        """The verifier's note on the placement build: KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG was neither removed nor set, so a
        value left in the daemon's shell refused every run (no word) or was taken silently as the forced offset (a config
        naming side_offset). It is removed first and without a condition, and set only from the config."""
        stale = {**self.STALE, sv.PLACEMENT_SIDE_OFFSET_FORCE_ENV: "+10"}
        env = self.environment(self.raw(), inherited=stale)
        self.assertNotIn(sv.PLACEMENT_SIDE_OFFSET_FORCE_ENV, env, "the shell's forced offset reached the child")
        raw = self.raw()
        raw["placement_envelope"] = ["projected", "side_offset"]
        env = self.environment(raw, inherited=stale)
        self.assertNotIn(sv.PLACEMENT_SIDE_OFFSET_FORCE_ENV, env, "a config naming the word and no offset got the shell's offset")
        raw["placement_side_offset_force_deg"], raw["placement_envelope_attended"] = -7.5, "qual-2026-09-27-c"
        env = self.environment(raw, inherited=stale)
        self.assertEqual(("-7.5", "projected,side_offset", "qual-2026-09-27-c"),
                         (env[sv.PLACEMENT_SIDE_OFFSET_FORCE_ENV], env[sv.PLACEMENT_ENVELOPE_ENV], env[sv.PLACEMENT_ENVELOPE_ATTENDED_ENV]))
        raw["placement_side_offset_force_deg"] = 10
        self.assertEqual("10.0", self.environment(raw, inherited=stale)[sv.PLACEMENT_SIDE_OFFSET_FORCE_ENV])
        self.assertEqual("KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG", sv.PLACEMENT_SIDE_OFFSET_FORCE_ENV)

    def test_the_forced_offset_variable_is_the_one_the_controller_reads(self):
        import inspect
        source = (REPO_ROOT / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py").read_text()
        self.assertIn('PLACEMENT_SIDE_OFFSET_FORCE_ENV = "%s"' % sv.PLACEMENT_SIDE_OFFSET_FORCE_ENV, source)
        self.assertNotIn("placement", inspect.getsource(sv.controller_arguments).lower())

    def test_every_stages_words_reach_the_child_through_the_same_variable(self):
        """The one word mechanism (docs/autonomous-demo/overnight-plan-2026-09-26.md section 2.6): a stage word travels
        beside the placement words in KC_PLACEMENT_ENVELOPE, in the config's order; the controller decides membership."""
        raw = self.raw()
        raw["placement_envelope"] = ["align_v2", "projected", "scene_scan"]
        env = self.environment(raw, inherited={**self.STALE, sv.PLACEMENT_ENVELOPE_ENV: "near_grasp"})
        self.assertEqual("align_v2,projected,scene_scan", env[sv.PLACEMENT_ENVELOPE_ENV])

    def test_the_childs_argv_never_carries_the_envelope(self):
        import inspect
        self.assertNotIn("placement", inspect.getsource(sv.controller_arguments).lower(), "the word travels in the environment; the proven argv is byte for byte what it was")

    def test_the_admission_is_kept_on_the_run_it_admitted(self):
        admitted = {"session_uuid": self.SESSION, "build_identity": self.ROBOT_BUILD}
        harness = Harness(self, motion_gate=lambda: (None, dict(admitted)))
        seen = []

        def start_child(run, _command):
            seen.append(run.robot_admission)
            raise sv._StoppedBeforeSpawn()  # nothing is spawned: only what the run carried into the spawn is read

        harness.supervisor._start_child = start_child
        harness.supervisor.start_mission_run(mission_run())
        self.assertEqual([admitted], seen)
        self.assertEqual(harness.launches(), [])

    def test_the_names_are_the_ones_the_controller_reads(self):
        controller = (REPO_ROOT / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py").read_text()
        for name in sv.PERCEPTION_ENV_NAMES + (sv.RESIDENT_SESSION_ENV,):
            self.assertIn(f'"{name}"', controller)


if __name__ == "__main__":
    unittest.main()
