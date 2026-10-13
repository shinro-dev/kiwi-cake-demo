#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_camera_lock.py: the per-role camera lock excludes a second owner (in this process and in another
# one), never blocks beyond its bounded wait, names the holder without naming a device, is dropped by the kernel when the
# holder dies, notices when its file was replaced under it, and makes the legacy probe FAIL CLOSED with a reported
# refusal. Every lock lives in a temporary directory. No camera, no device file, no network.
# Run directly: python3 autonomy/tests/test_camera_lock.py
import ast
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PERCEPTION = ROOT / "autonomy" / "perception"
sys.path.insert(0, str(PERCEPTION))

import camera_lock  # noqa: E402
from camera_lifecycle import LifecycleRecord  # noqa: E402
from camera_lock import (  # noqa: E402
    LOCK_DIR_ENV_VAR,
    CameraLock,
    CameraLockError,
    CameraLockHeld,
    CameraLockLost,
    lock_status,
    resolve_lock_dir,
)

# Built at runtime: a tracked file never carries a device identity, and the tests need one to prove nothing repeats it.
WRIST_ID = "/dev/v4l/" + "by-id/" + "usb-" + "Test_Cam_" + "SN" + "0001" + "-video-index0"
FRONT_ID = "/dev/v4l/" + "by-id/" + "usb-" + "Test_Cam_" + "SN" + "0002" + "-video-index0"


def load_probe():
    spec = importlib.util.spec_from_file_location("camera_multiplex_probe_under_test", ROOT / "bin" / "pi" / "camera_multiplex_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOLD_LOCK_SCRIPT = """
import sys, time
sys.path.insert(0, sys.argv[1])
from camera_lock import CameraLock
lock = CameraLock(sys.argv[3], "other_process", directory=sys.argv[2]).acquire(wait_s=0)
print("held", flush=True)
time.sleep(60)
"""


class LockDirectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_override_wins_and_only_the_source_is_reportable(self):
        directory, source = resolve_lock_dir({LOCK_DIR_ENV_VAR: str(self.dir / "locks"), "XDG_RUNTIME_DIR": str(self.dir)})
        self.assertEqual(source, "env_override")
        self.assertEqual(directory, self.dir / "locks" / camera_lock.LOCK_SUBDIR)
        self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)

    def test_runtime_dir_is_used_when_there_is_no_override(self):
        directory, source = resolve_lock_dir({"XDG_RUNTIME_DIR": str(self.dir)})
        self.assertEqual((directory.parent, source), (self.dir, "xdg_runtime_dir"))

    def test_both_sides_resolve_the_same_file_from_the_same_environment(self):
        env = {"XDG_RUNTIME_DIR": str(self.dir)}
        self.assertEqual(CameraLock("wrist", "a", env=env).path, CameraLock("wrist", "b", env=env).path)
        self.assertNotEqual(CameraLock("wrist", "a", env=env).path, CameraLock("front", "a", env=env).path)

    def test_a_directory_open_to_others_is_refused(self):
        shared = self.dir / camera_lock.LOCK_SUBDIR
        shared.mkdir(mode=0o777)
        os.chmod(shared, 0o777)
        with self.assertRaises(CameraLockError):
            resolve_lock_dir({"XDG_RUNTIME_DIR": str(self.dir)})

    def test_role_and_holder_are_plain_names(self):
        for role in ("../wrist", "", "Wrist", "wrist/0"):
            with self.assertRaises(CameraLockError):
                CameraLock(role, "holder", directory=self.dir)
        with self.assertRaises(CameraLockError):
            CameraLock("wrist", "holder with spaces", directory=self.dir)


class Exclusion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_second_owner_is_refused_and_told_who_holds_it(self):
        first = CameraLock("wrist", "camera_service", directory=self.dir).acquire(wait_s=0)
        self.addCleanup(first.release)
        with self.assertRaises(CameraLockHeld) as caught:
            CameraLock("wrist", "camera_multiplex_probe", directory=self.dir).acquire(wait_s=0)
        self.assertEqual(caught.exception.role, "wrist")
        self.assertEqual(caught.exception.holder["holder"], "camera_service")
        self.assertEqual(caught.exception.holder["pid"], os.getpid())
        self.assertIn("camera_service", str(caught.exception))
        self.assertIn("explicit stop", str(caught.exception))

    def test_roles_do_not_exclude_each_other(self):
        with CameraLock("wrist", "a", directory=self.dir), CameraLock("front", "b", directory=self.dir) as front:
            self.assertTrue(front.held)

    def test_the_wait_is_bounded(self):
        with CameraLock("wrist", "camera_service", directory=self.dir):
            started = time.monotonic()
            with self.assertRaises(CameraLockHeld) as caught:
                CameraLock("wrist", "probe", directory=self.dir).acquire(wait_s=0.3, poll_s=0.05)
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 2.0)
        self.assertGreaterEqual(caught.exception.waited_s, 0.3)

    def test_a_short_wait_gets_a_lock_that_is_released_in_time(self):
        first = CameraLock("wrist", "camera_service", directory=self.dir).acquire(wait_s=0)
        sleeps = []

        def sleep_then_release(seconds):
            sleeps.append(seconds)
            first.release()

        second = CameraLock("wrist", "probe", directory=self.dir, sleep=sleep_then_release).acquire(wait_s=1.0)
        self.addCleanup(second.release)
        self.assertEqual(len(sleeps), 1)
        self.assertEqual(lock_status(["wrist"], directory=self.dir)["roles"]["wrist"]["holder"]["holder"], "probe")

    def test_release_frees_it_and_clears_the_record(self):
        lock = CameraLock("wrist", "camera_service", directory=self.dir).acquire(wait_s=0)
        self.assertTrue(lock_status(["wrist"], directory=self.dir)["roles"]["wrist"]["held"])
        lock.release()
        self.assertFalse(lock.held)
        self.assertEqual(lock_status(["wrist"], directory=self.dir)["roles"]["wrist"], {"held": False, "holder": None})
        self.assertEqual(lock.path.read_bytes(), b"")
        self.assertTrue(lock.path.exists(), "the lock file is never unlinked: unlinking splits a flock in two")
        lock.release()  # twice is harmless

    def test_status_of_a_role_nobody_ever_locked(self):
        self.assertEqual(lock_status(["front"], directory=self.dir)["roles"]["front"], {"held": False, "holder": None})
        self.assertFalse((self.dir / "front.lock").exists(), "asking must not create the file")

    def test_the_same_object_cannot_acquire_twice(self):
        with CameraLock("wrist", "camera_service", directory=self.dir) as lock:
            with self.assertRaises(CameraLockError):
                lock.acquire(wait_s=0)

    def test_record_names_no_device(self):
        with CameraLock("wrist", "camera_service", directory=self.dir) as lock:
            record = json.loads(lock.path.read_text())
        self.assertEqual(sorted(record), ["acquired_wall_utc", "holder", "pid", "role"])

    def test_another_process_excludes_us_and_its_death_frees_the_camera(self):
        holder = subprocess.Popen([sys.executable, "-c", HOLD_LOCK_SCRIPT, str(PERCEPTION), str(self.dir), "wrist"], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(CameraLockHeld) as caught:
                CameraLock("wrist", "camera_service", directory=self.dir).acquire(wait_s=0.1)
            self.assertEqual(caught.exception.holder["holder"], "other_process")
            self.assertEqual(caught.exception.holder["pid"], holder.pid)
            holder.kill()  # no cleanup code runs: the kernel drops the flock with the descriptor
            holder.wait(10)
            with CameraLock("wrist", "camera_service", directory=self.dir).acquire(wait_s=2.0) as lock:
                self.assertTrue(lock.held)
        finally:
            holder.kill()
            holder.wait(10)
            holder.stdout.close()


class LostLock(unittest.TestCase):
    def test_verify_notices_a_replaced_or_removed_lock_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = CameraLock("wrist", "camera_service", directory=Path(tmp)).acquire(wait_s=0)
            lock.verify()
            os.unlink(lock.path)  # what the removal of a session's runtime directory does under a living holder
            with self.assertRaises(CameraLockLost):
                lock.verify()
            # The hazard itself: a second owner now locks a NEW file of the same name without any conflict.
            intruder = CameraLock("wrist", "probe", directory=Path(tmp)).acquire(wait_s=0)
            with self.assertRaises(CameraLockLost):
                lock.verify()
            intruder.release()
            lock.release()

    def test_verify_without_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CameraLockLost):
                CameraLock("wrist", "camera_service", directory=Path(tmp)).verify()


class RecordingBackend:
    """A probe backend that never touches a device and remembers whether it was asked to."""

    def __init__(self, log, role):
        self.log, self.role, self.count = log, role, 0

    def open(self, identity):
        self.log.append(("open", self.role))

    def read(self):
        self.count += 1
        return np.full((4, 6, 3), self.count, dtype=np.uint8)

    def close(self):
        self.log.append(("close", self.role))


class LegacyProbeFailsClosed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.env = {"KC_WRIST_CAMERA_ID": WRIST_ID, "KC_FRONT_CAMERA_ID": FRONT_ID, LOCK_DIR_ENV_VAR: str(self.dir / "locks")}
        self.probe = load_probe()
        self.log = []

    def run_probe(self, order, name):
        out = self.dir / name
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            code = self.probe.main(["--cycles", "2", "--order", order, "--settle-frames", "1", "--width", "6", "--height", "4", "--out-dir", str(out), "--lock-wait-s", "0.1"],
                                   env=self.env, backend_factory=lambda role, width, height: RecordingBackend(self.log, role))
        return code, json.loads((out / "report.json").read_text()), (out / "report.json").read_text()

    def test_probe_refuses_while_the_service_owns_the_camera_and_reports_it(self):
        with CameraLock("wrist", "camera_service", env=self.env):
            code, report, text = self.run_probe("wrist", "refused")
        self.assertEqual(code, self.probe.EXIT_REFUSED)
        self.assertEqual(self.log, [], "no backend may be opened, read or closed after a refusal")
        self.assertEqual(report["refused"]["reason"], "camera_lock_held")
        self.assertIn("camera_service", report["refused"]["detail"])
        self.assertFalse(report["all_cycles_clean"])
        self.assertEqual(len(report["attempts"]), 1)
        attempt = report["attempts"][0]
        self.assertFalse(attempt["open_ok"])
        self.assertFalse(attempt["frame_valid"])
        self.assertIn("camera_service", attempt["error"])
        self.assertIn("explicit stop", attempt["error"])
        record = LifecycleRecord(**attempt)  # what the laptop side does with attempts[0] of the legacy path
        self.assertFalse(record.frame_valid)
        self.assertNotIn(WRIST_ID, text)
        self.assertEqual(list((self.dir / "refused" / "frames").iterdir()), [])
        self.assertIn("REFUSED (camera_lock_held), no camera was opened", self.stderr.getvalue())
        self.assertNotIn(WRIST_ID, self.stdout.getvalue() + self.stderr.getvalue())

    def test_one_held_role_refuses_the_whole_order_and_releases_what_was_taken(self):
        with CameraLock("wrist", "camera_service", env=self.env):
            code, report, _ = self.run_probe("front,wrist,front", "partial")
            self.assertEqual(code, self.probe.EXIT_REFUSED)
            self.assertEqual(self.log, [])
            self.assertEqual([a["role"] for a in report["attempts"]], ["front", "wrist", "front"])
            self.assertTrue(all("wrist camera is owned by camera_service" in a["error"] for a in report["attempts"]))
            status = lock_status(["wrist", "front"], env=self.env)["roles"]
            self.assertTrue(status["wrist"]["held"])
            self.assertFalse(status["front"]["held"], "the front lock the probe had taken must be released on refusal")

    def test_probe_runs_once_the_service_has_stopped_and_frees_the_lock_after(self):
        lock = CameraLock("wrist", "camera_service", env=self.env).acquire(wait_s=0)
        lock.release()  # the explicit stop
        code, report, _ = self.run_probe("wrist", "allowed")
        self.assertEqual(code, 0)
        self.assertIsNone(report["refused"])
        self.assertEqual(self.log, [("open", "wrist"), ("close", "wrist")] * 2)
        self.assertFalse(lock_status(["wrist"], env=self.env)["roles"]["wrist"]["held"])

    def test_probe_holds_the_lock_while_it_owns_a_camera(self):
        seen = []

        class Watching(RecordingBackend):
            def open(inner, identity):
                seen.append(lock_status(["wrist"], env=self.env)["roles"]["wrist"])

        out = self.dir / "watching"
        with contextlib.redirect_stdout(io.StringIO()):
            self.probe.main(["--cycles", "1", "--order", "wrist", "--settle-frames", "0", "--width", "6", "--height", "4", "--out-dir", str(out)],
                            env=self.env, backend_factory=lambda role, width, height: Watching(self.log, role))
        self.assertTrue(seen[0]["held"])
        self.assertEqual(seen[0]["holder"]["holder"], "camera_multiplex_probe")

    def test_probe_without_the_lock_module_refuses_instead_of_opening(self):
        self.probe.CameraLock = None  # what the probe sees when camera_lock.py was not copied next to it
        code, report, _ = self.run_probe("wrist", "no-module")
        self.assertEqual(code, self.probe.EXIT_REFUSED)
        self.assertEqual(self.log, [])
        self.assertEqual(report["refused"]["reason"], "camera_lock_unavailable")
        self.assertIn("camera_lock.py", report["attempts"][0]["error"])


class LegacyDeployment(unittest.TestCase):
    def test_the_legacy_path_copies_every_module_the_probe_imports(self):
        """The laptop copies a fixed list of files to the Pi before it runs the probe there. The probe now imports
        camera_lock; a list without it makes every legacy capture a reported refusal (camera_lock_unavailable)."""
        tree = ast.parse((ROOT / "bin" / "laptop" / "perception" / "run_stage1_scan.py").read_text())
        copied = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PI_FILES" for t in node.targets):
                copied = ast.literal_eval(node.value)
        self.assertIsNotNone(copied, "PI_FILES was not found in run_stage1_scan.py")
        self.assertIn("autonomy/perception/camera_lock.py", copied,
                      "add autonomy/perception/camera_lock.py to PI_FILES in bin/laptop/perception/run_stage1_scan.py: the probe refuses to open a camera without it")


if __name__ == "__main__":
    unittest.main()
