#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_lekiwi_templates.py: the three templates of the LeKiwi deployment (bin/templates/run-host.sh.in,
# run-capability.sh.in, kiwi-cake-lekiwi.service.in). The two wrappers are RUN the way Cake runs a child, measured:
# argv [wrapper, 3, 0, 900, run_tag], environment exactly PATH=/usr/bin:/bin, a separate working directory. The
# interpreter they are rendered with is a RECORDER script that writes down its own pid and argument vector and exits:
# no host, no python program, no device is ever started here. The host wrapper's fuser and ss are rendered as stub
# scripts written per test (and, in one test, as this machine's own fuser and ss run against a temporary file), its
# servo "device" is a temporary file, and its arm token is a file in the test's temporary directory. Its flock is this
# machine's own, on a lock file in that directory: the wheel stop's lock, which the host wrapper takes before its owner
# check and hands to the host through its exec. A stand-in host (STAND_IN_HOST) stays up until the test lets it exit, so
# a test can see the lock held for the host's whole life, and a lock holder (LOCK_HOLDER) stands in for a wheel stop.
# Run directly: python3 autonomy/tests/test_lekiwi_templates.py
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES = os.path.join(ROOT, "bin", "templates")
COMMON = os.path.join(ROOT, "bin", "lib", "common.sh")
CAKE_POSITIONALS = ["3", "0", "900", "kc-lekiwi-run-tag"]
CAKE_ENV = {"PATH": "/usr/bin:/bin"}
RECORDER = """#!/bin/sh
date +%s%N >"$0.started"
printf '%s\\n' "$$" >"$0.pid"
for a in "$@"; do printf '%s\\n' "$a"; done >"$0.argv"
env >"$0.env"
exit 0
"""
# A stand-in for a host that has started and runs on: it records its pid, then stays up until the file $0.exit appears
# (at most 30 s), so that what it holds (the wheel stop's lock, handed down by the wrapper's exec) can be checked while
# it lives and after it has exited.
STAND_IN_HOST = """#!/bin/sh
printf '%s\\n' "$$" >"$0.pid"
n=0
while [ ! -e "$0.exit" ] && [ "$n" -lt 300 ]; do sleep 0.1; n=$((n + 1)); done
exit 0
"""
# A stand-in for a wheel stop holding the lock: it takes the lock, says so, holds it for the given seconds, prints the
# time (ns since the epoch) just before it lets go, and exits.
LOCK_HOLDER = """import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("holding", flush=True)
time.sleep(float(sys.argv[2]))
print(time.time_ns(), flush=True)
os.close(fd)
"""
FLOCK = shutil.which("flock", path="/usr/bin:/bin")
# The stub fuser, one body per behaviour. The servo device (DEVICE, filled in per test) is owned or not as the test
# says; any other path, the wrapper's own file (its positive control) among them, reads as owned, which is what the
# real fuser reports for a file its caller holds open.
FUSER_STUBS = {
    "unowned": 'case "$1" in "DEVICE") exit 1 ;; esac\nexit 0\n',
    "owned": 'case "$1" in "DEVICE") echo " 4242"; echo "$1:" >&2; exit 0 ;; esac\nexit 0\n',
    "cannot-run": 'echo "fuser: command not found" >&2\nexit 127\n',
    "error": 'echo "fuser: cannot read the process table" >&2\nexit 1\n',
    "blind": 'exit 1\n',
}
SS_STUBS = {
    "free": "printf 'LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\\nLISTEN 0 100 127.0.0.1:8770 0.0.0.0:*\\n'\n",
    "5555": "printf 'LISTEN 0 100 0.0.0.0:5555 0.0.0.0:*\\n'\n",
    "5556-v6": "printf 'LISTEN 0 100 [::]:5556 [::]:*\\n'\n",
    "error": 'echo "Cannot open netlink socket: Permission denied" >&2\nexit 1\n',
}


def render(template, values, out_path):
    with open(os.path.join(TEMPLATES, template), encoding="utf-8") as handle:
        text = handle.read()
    for name, value in values.items():
        text = text.replace(f"@@{name}@@", value)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(out_path, 0o755)
    return text


def write_exec(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o755)


def has_placeholder(path):
    """bin/lib/common.sh's own check, run as the installer runs it."""
    return subprocess.run(["bash", "-c", '. "$1"; kc_template_has_placeholder "$2"', "_", COMMON, path],
                          capture_output=True, text=True).returncode == 0


def code_lines(text):
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


@unittest.skipUnless(shutil.which("bash") and FLOCK, "bash or flock is not installed")
class Wrappers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.cwd = os.path.join(t, "child-cwd")
        self.run_dir = os.path.join(t, "run")
        for d in (self.cwd, self.run_dir):
            os.mkdir(d)
        os.chmod(self.run_dir, 0o700)
        self.recorder = os.path.join(t, "interpreter")
        write_exec(self.recorder, RECORDER)
        self.token = os.path.join(self.run_dir, "arm-token")
        self.lock = os.path.join(self.run_dir, "wheels-safe-stop.lock")
        self.stand_in = os.path.join(t, "stand-in-host")
        write_exec(self.stand_in, STAND_IN_HOST)
        self.device = os.path.join(t, "servo-device")
        with open(self.device, "w", encoding="utf-8"):
            pass
        self.fuser, self.ss = os.path.join(t, "fuser"), os.path.join(t, "ss")
        self.tools("unowned", "free")

    def tools(self, fuser, ss):
        write_exec(self.fuser, "#!/bin/bash\n" + FUSER_STUBS[fuser].replace("DEVICE", self.device))
        write_exec(self.ss, "#!/bin/bash\n" + SS_STUBS[ss])

    def host_values(self, **override):
        values = {"PYTHON": self.recorder, "HOST_ENTRY": "/opt/example/lekiwi_host_safe_attach.py",
                  "ROBOT_ID": "example_robot", "ROBOT_PORT": self.device, "ARM_TOKEN": self.token,
                  "FUSER": self.fuser, "SS": self.ss, "FLOCK": FLOCK, "WHEELS_LOCK": self.lock}
        values.update(override)
        return values

    def host_wrapper(self, **override):
        path = os.path.join(self.tmp.name, "run-host.sh")
        render("run-host.sh.in", self.host_values(**override), path)
        return path

    def arm(self, age_s=0):
        with open(self.token, "w", encoding="utf-8") as handle:
            handle.write("armed\n")
        os.chmod(self.token, 0o600)
        if age_s:
            then = time.time() - age_s
            os.utime(self.token, (then, then))

    def run_as_cake(self, wrapper, env=None):
        process = subprocess.Popen([wrapper, *CAKE_POSITIONALS], env=env or CAKE_ENV, cwd=self.cwd,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = process.communicate(timeout=30)
        self.last_stderr = err
        return process.pid, process.returncode, out + err

    def recorded(self):
        with open(self.recorder + ".pid", encoding="utf-8") as handle:
            pid = int(handle.read())
        with open(self.recorder + ".argv", encoding="utf-8") as handle:
            argv = handle.read().splitlines()
        return pid, argv

    def recorded_env(self):
        with open(self.recorder + ".env", encoding="utf-8") as handle:
            return handle.read().splitlines()

    def assert_host_not_started(self, out):
        self.assertFalse(os.path.exists(self.recorder + ".pid"), "the wrapper reached its exec: " + out)

    def assert_no_token_left(self):
        left = [n for n in os.listdir(self.run_dir) if n != os.path.basename(self.lock)]
        self.assertEqual(left, [], "an arm token, or a claimed copy of one, was left behind")

    def lock_is_held(self):
        """Whether another open of the wheel stop's lock file finds it held, the way a wheel stop tries it."""
        fd = os.open(self.lock, os.O_RDONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            os.close(fd)
        return False

    def hold_lock(self, hold_s):
        """A stand-in wheel stop holding the lock for HOLD_S seconds; returns once it holds it."""
        holder = subprocess.Popen([sys.executable, "-c", LOCK_HOLDER, self.lock, str(hold_s)], stdout=subprocess.PIPE, text=True)

        def stop():
            if holder.poll() is None:
                holder.kill()
            holder.wait()
            holder.stdout.close()
        self.addCleanup(stop)
        self.assertEqual(holder.stdout.readline().strip(), "holding")
        return holder

    def start_as_cake(self, wrapper):
        """The wrapper started as Cake starts a child, left running; the test ends it."""
        process = subprocess.Popen([wrapper, *CAKE_POSITIONALS], env=CAKE_ENV, cwd=self.cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)

        def stop():
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.addCleanup(stop)
        return process

    def wait_for(self, path, timeout_s=10.0):
        end = time.monotonic() + timeout_s
        while not os.path.exists(path) or not os.path.getsize(path):
            self.assertLess(time.monotonic(), end, f"{path} never appeared")
            time.sleep(0.01)
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()

    def expected_host_argv(self, entry="/opt/example/lekiwi_host_safe_attach.py"):
        return ["-u", entry, "--robot.id=example_robot", f"--robot.port={self.device}",
                "--robot.max_relative_target=15.0", "--robot.disable_torque_on_disconnect=false", "--robot.cameras={}",
                "--host.connection_time_s=86400"]

    def test_unrendered_wrappers_are_refused_by_the_installer_check_and_refuse_to_run(self):
        self.arm()
        for template in ("run-host.sh.in", "run-capability.sh.in"):
            path = os.path.join(self.tmp.name, template.replace(".in", ""))
            shutil.copy(os.path.join(TEMPLATES, template), path)
            os.chmod(path, 0o755)
            self.assertTrue(has_placeholder(path), f"{template}: the installer's check does not see the placeholders")
            _, status, out = self.run_as_cake(path)
            self.assertEqual(status, 78, out)
            self.assertIn("refusing to run: a placeholder was never filled in", out)
            self.assert_host_not_started(out)
        self.assertTrue(os.path.exists(self.token), "an unrendered wrapper consumed the arm token")

    def test_one_placeholder_left_is_enough_to_refuse(self):
        self.arm()
        for name in ("ROBOT_PORT", "ARM_TOKEN", "FUSER", "SS", "FLOCK", "WHEELS_LOCK"):
            path = os.path.join(self.tmp.name, "run-host.sh")
            values = self.host_values()
            values.pop(name)
            render("run-host.sh.in", values, path)
            self.assertTrue(has_placeholder(path), name)
            _, status, out = self.run_as_cake(path)
            self.assertEqual(status, 78, name + ": " + out)
            self.assert_host_not_started(out)
        self.assertTrue(os.path.exists(self.token), "a wrapper with a placeholder left consumed the arm token")

    def test_the_host_wrapper_execs_the_interpreter_with_exactly_the_proven_flags_and_ignores_the_positionals(self):
        path = self.host_wrapper()
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertFalse(has_placeholder(path))
        self.assertTrue(text.startswith("#!/bin/bash\n"), "the shebang must name its interpreter by absolute path")
        self.assertNotRegex("\n".join(code_lines(text)), r'"\$@"|\$[1-9*]', "the wrapper forwards Cake's positionals")
        self.arm()
        wrapper_pid, status, out = self.run_as_cake(path)
        self.assertEqual(status, 0, out)
        pid, argv = self.recorded()
        self.assertEqual(pid, wrapper_pid, "the wrapper did not exec: the host would not be the pid Cake reports")
        self.assertEqual(argv, self.expected_host_argv())
        self.assertIn("KC_HOST_COMMAND_LOOPBACK=1", self.recorded_env(), "the host must be started with its command socket on loopback only")
        self.assert_no_token_left()
        self.assertFalse(self.lock_is_held(), "the wheel stop's lock is still held after the host exited")

    def test_without_an_arm_token_the_host_is_held_with_one_line(self):
        path = self.host_wrapper()
        _, status, out = self.run_as_cake(path)
        self.assertEqual(status, 77, out)
        self.assertEqual(len(self.last_stderr.splitlines()), 1, self.last_stderr)
        self.assertIn("HOST HELD", self.last_stderr)
        self.assertIn("lekiwi-deploy.sh arm", self.last_stderr)
        self.assert_host_not_started(out)

    def test_the_token_is_one_shot_so_a_restart_is_held(self):
        path = self.host_wrapper()
        self.arm()
        first_pid, status, out = self.run_as_cake(path)
        self.assertEqual(status, 0, out)
        self.assertEqual(self.recorded()[0], first_pid)
        self.assert_no_token_left()
        os.remove(self.recorder + ".pid")
        for _ in range(5):  # the supervisor's five spawns, then DEGRADED
            _, status, out = self.run_as_cake(path)
            self.assertEqual(status, 77, out)
            self.assert_host_not_started(out)

    def test_a_stale_token_is_deleted_and_refused(self):
        path = self.host_wrapper()
        self.arm(age_s=600)
        _, status, out = self.run_as_cake(path)
        self.assertEqual(status, 77, out)
        self.assertIn("stale", out)
        self.assert_host_not_started(out)
        self.assert_no_token_left()

    def test_a_token_that_is_a_symlink_or_a_directory_does_not_arm(self):
        path = self.host_wrapper()
        target = os.path.join(self.tmp.name, "elsewhere")
        with open(target, "w", encoding="utf-8"):
            pass
        os.symlink(target, self.token)
        _, status, out = self.run_as_cake(path)
        self.assertEqual(status, 77, out)
        self.assert_host_not_started(out)
        os.remove(self.token)
        os.mkdir(self.token)
        _, status, out = self.run_as_cake(path)
        self.assertEqual(status, 77, out)
        self.assert_host_not_started(out)

    def test_the_ownership_check_refuses_an_owner_or_a_listener_and_fails_closed(self):
        cases = (
            ("owned", "free", 75, "servo device already has an owner"),
            ("cannot-run", "free", 69, "cannot run"),
            ("error", "free", 69, "cannot run"),
            ("blind", "free", 69, "cannot run"),
            ("unowned", "5555", 75, "5555 or 5556"),
            ("unowned", "5556-v6", 75, "5555 or 5556"),
            ("unowned", "error", 69, "cannot run"),
        )
        path = self.host_wrapper()
        for fuser, ss, want_status, words in cases:
            with self.subTest(fuser=fuser, ss=ss):
                self.tools(fuser, ss)
                self.arm()
                _, status, out = self.run_as_cake(path)
                self.assertEqual(status, want_status, out)
                self.assertIn(words, out)
                self.assertEqual(len(self.last_stderr.splitlines()), 1, self.last_stderr)
                self.assert_host_not_started(out)
                self.assert_no_token_left()

    def test_a_missing_tool_or_servo_device_fails_closed(self):
        missing = os.path.join(self.tmp.name, "not-installed")
        for override, want_status in (({"FUSER": missing}, 69), ({"SS": missing}, 69), ({"ROBOT_PORT": missing}, 75)):
            with self.subTest(override=override):
                path = self.host_wrapper(**override)
                self.arm()
                _, status, out = self.run_as_cake(path)
                self.assertEqual(status, want_status, out)
                self.assert_host_not_started(out)
                self.assert_no_token_left()

    @unittest.skipUnless(os.path.exists("/usr/bin/fuser") and os.path.exists("/usr/bin/ss"), "fuser or ss is not in /usr/bin")
    def test_this_machines_own_fuser_and_ss_let_an_armed_start_through(self):
        listeners = subprocess.run(["/usr/bin/ss", "-H", "-ltn"], capture_output=True, text=True).stdout
        if re.search(r"[:.](5555|5556)\s", listeners):
            self.skipTest("something listens on 5555 or 5556 on this machine")
        path = self.host_wrapper(FUSER="/usr/bin/fuser", SS="/usr/bin/ss")
        self.arm()
        wrapper_pid, status, out = self.run_as_cake(path)
        self.assertEqual(status, 0, out)
        self.assertEqual(self.recorded(), (wrapper_pid, self.expected_host_argv()))
        self.assert_no_token_left()
        # The same real fuser on a device this test holds open: an owner, refused.
        os.remove(self.recorder + ".pid")
        with open(self.device, encoding="utf-8"):
            self.arm()
            _, status, out = self.run_as_cake(path)
        self.assertEqual(status, 75, out)
        self.assert_host_not_started(out)

    def test_the_host_holds_the_wheel_stop_lock_from_before_its_owner_check_until_it_exits(self):
        # The owner check (fuser) is a look at /proc at one moment; a wheel stop that starts after it and before the host
        # opens the bus would see no owner. So the wrapper takes the wheel stop's lock first, and the host inherits it:
        # a wheel stop writes only while it holds that lock. fuser here reports, for the servo device, whether the lock
        # is already held at the moment of the owner check.
        probe = os.path.join(self.tmp.name, "lock-at-owner-check")
        write_exec(self.fuser, "#!/bin/bash\n"
                   f'case "$1" in "{self.device}") "{FLOCK}" -n "{self.lock}" true; echo "$?" >"{probe}"; exit 1 ;; esac\n'
                   "exit 0\n")
        path = self.host_wrapper(PYTHON=self.stand_in)
        self.arm()
        process = self.start_as_cake(path)
        self.assertEqual(int(self.wait_for(self.stand_in + ".pid")), process.pid, "the wrapper did not exec the host")
        self.assertEqual(self.wait_for(probe), "1", "the lock was not held when the owner check ran")
        self.assertTrue(self.lock_is_held(), "the running host does not hold the wheel stop's lock")
        time.sleep(0.3)
        self.assertTrue(self.lock_is_held(), "the running host let the wheel stop's lock go")
        with open(self.stand_in + ".exit", "w", encoding="utf-8"):
            pass
        _, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, err)
        self.assertFalse(self.lock_is_held(), "the lock outlived the host")
        self.assert_no_token_left()

    def test_a_start_waits_for_a_wheel_stop_that_holds_the_lock_and_starts_the_host_after_it(self):
        path = self.host_wrapper()
        self.arm()
        holder = self.hold_lock(0.4)
        wrapper_pid, status, out = self.run_as_cake(path)
        released_ns = int(holder.stdout.readline())
        self.assertEqual(status, 0, out)
        self.assertEqual(self.recorded(), (wrapper_pid, self.expected_host_argv()))
        with open(self.recorder + ".started", encoding="utf-8") as handle:
            started_ns = int(handle.read())
        self.assertGreater(started_ns, released_ns, "the host was started while the wheel stop still held the lock")
        self.assert_no_token_left()

    def test_a_lock_held_past_the_wait_refuses_the_start_with_one_line(self):
        path = self.host_wrapper()
        self.arm()
        self.hold_lock(30)
        start = time.monotonic()
        _, status, out = self.run_as_cake(path)
        elapsed = time.monotonic() - start
        self.assertEqual(status, 75, out)
        self.assertEqual(len(self.last_stderr.splitlines()), 1, self.last_stderr)
        self.assertIn("HOST HELD", self.last_stderr)
        self.assertIn("wheel stop's lock is still held after 2 s", self.last_stderr)
        self.assertGreaterEqual(elapsed, 2.0, "the wrapper did not wait for the lock")
        self.assertLess(elapsed, 10.0)
        self.assert_host_not_started(out)
        self.assert_no_token_left()
        self.assertTrue(self.lock_is_held(), "the refused start took the lock from its holder")

    def test_the_wheel_stop_lock_fails_closed(self):
        missing = os.path.join(self.tmp.name, "not-installed")
        blind = os.path.join(self.tmp.name, "flock-that-never-locks")
        write_exec(blind, "#!/bin/sh\nexit 0\n")
        broken = os.path.join(self.tmp.name, "flock-that-fails")
        write_exec(broken, "#!/bin/sh\necho 'flock: stands in for a failure' >&2\nexit 71\n")
        cases = (({"FLOCK": missing}, "flock is missing"),
                 ({"WHEELS_LOCK": os.path.join(self.tmp.name, "no-such-dir", "wheels-safe-stop.lock")}, "could not be opened"),
                 ({"FLOCK": blind}, "did not find it held"),
                 ({"FLOCK": broken}, "flock failed"))
        for override, words in cases:
            with self.subTest(override=override):
                path = self.host_wrapper(**override)
                self.arm()
                _, status, out = self.run_as_cake(path)
                self.assertEqual(status, 69, out)
                self.assertIn(words, out)
                self.assertIn("fails closed", out)
                self.assertEqual(len(self.last_stderr.splitlines()), 1, self.last_stderr)
                self.assert_host_not_started(out)
                self.assert_no_token_left()

    def test_the_capability_wrapper_labels_commands_with_its_own_pid(self):
        path = os.path.join(self.tmp.name, "run-capability.sh")
        text = render("run-capability.sh.in", {"PYTHON": self.recorder, "APP_DIR": "/opt/example/app",
                                               "RUN_DIR": "/opt/example/run"}, path)
        self.assertFalse(has_placeholder(path))
        self.assertTrue(text.startswith("#!/bin/bash\n"))
        self.assertNotRegex("\n".join(code_lines(text)), r'"\$@"|\$[1-9*]')
        wrapper_pid, status, out = self.run_as_cake(path)
        self.assertEqual(status, 0, out)
        pid, argv = self.recorded()
        self.assertEqual(pid, wrapper_pid, "the wrapper did not exec: $$ would not be the service's pid")
        self.assertEqual(argv, ["-u", "/opt/example/app/autonomy/motion/mobile_approach_service.py",
                                "--observations", "ipc:///opt/example/run/approach-observations.ipc",
                                "--commands", "ipc:///opt/example/run/approach-commands.ipc",
                                "--contract", "v2",
                                "--identity", f"mobile_approach.pid{wrapper_pid}"])
        self.assertFalse([line for line in self.recorded_env() if line.startswith("KC_HOST_COMMAND_LOOPBACK=")], "only the host's launch sets it")

    def test_a_relative_path_is_refused(self):
        self.arm()
        for name in ("PYTHON", "ARM_TOKEN", "FUSER", "SS", "FLOCK", "WHEELS_LOCK"):
            path = self.host_wrapper(**{name: "relative-" + name.lower()})
            _, status, out = self.run_as_cake(path)
            self.assertEqual(status, 78, name + ": " + out)
            self.assertIn("absolute", out)
            self.assert_host_not_started(out)
        path = os.path.join(self.tmp.name, "run-capability.sh")
        render("run-capability.sh.in", {"PYTHON": "python3", "APP_DIR": "/opt/example/app", "RUN_DIR": "/opt/example/run"}, path)
        _, status, out = self.run_as_cake(path)
        self.assertEqual(status, 78, out)
        self.assertIn("absolute", out)


class Unit(unittest.TestCase):
    def test_the_unit_keeps_the_stop_path_fields_and_cannot_be_enabled(self):
        with open(os.path.join(TEMPLATES, "kiwi-cake-lekiwi.service.in"), encoding="utf-8") as handle:
            text = handle.read()
        with open(os.path.join(TEMPLATES, "kiwi-cake-demo.service.in"), encoding="utf-8") as handle:
            model = handle.read()
        lines = text.splitlines()
        self.assertIn("ExecStart=@@CAKE_RESIDENT@@ --config @@RESIDENT_CONF@@", lines)
        for field in ("Type=simple", "Restart=on-abnormal", "RestartSec=2", "KillMode=mixed", "KillSignal=SIGTERM", "TimeoutStopSec=60"):
            self.assertIn(field, lines)
            self.assertIn(field, model.splitlines(), f"{field} is not the model unit's own field")
        self.assertEqual(sum(1 for line in lines if line.startswith("KillMode=")), 1)
        self.assertNotIn("[Install]", [line.strip() for line in code_lines(text)], "an [Install] section would let the unit be enabled and start the host at boot")
        self.assertNotRegex(text, r"(?m)^WantedBy=")
        self.assertIn("kiwi-cake-lekiwi", text)
        self.assertEqual(sorted(set(re.findall(r"@@([A-Z_]+)@@", "\n".join(code_lines(text))))), ["CAKE_RESIDENT", "RESIDENT_CONF"])


if __name__ == "__main__":
    unittest.main()
