#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_robot_link.py: the ROBOT's Cake resident, reached through a forward the runtime owns.
#
# NO NETWORK AND NO ROBOT. The ssh here is a STUB written into a temporary directory and put at the front of PATH: it
# records its argv, binds the LOCAL unix socket of its -L and waits. The resident is fake_robot_probe.py, which answers
# in the real admin-probe line shapes from files a test writes. No test here opens a device or leaves this machine.
# What is proved, item by item:
#   1. the forward: its argv, its states, its restart with a bounded backoff, and a stale socket read as a connection
#      failure rather than as a Cake refusal;
#   2. with cake.robot configured, no local probe stands in for the robot, and a forward that is down answers
#      robot_unreachable with the last good reading retained and marked stale;
#   3. the robot_status surface validates against its own schema, and poll traffic never reads as module activity;
#   4. permission to move binds the resident session AND the supervisor HOST INSTANCE (slot instance handle, spawn
#      sequence, pid); a change of any component revokes it, only an explicit confirm naming every current value
#      restores it, a daemon restart needs a fresh confirmation, observe is never gated by it, and the supervisor reads
#      it all FRESH again immediately before it admits a motion command and journals what it admitted it under;
#   5. a resident that does not report the operator-pinned build identity is refused: nothing it reports is shown as the
#      robot resident's and it moves nothing, whatever target profile it reports (the laptop x86 resident reports the
#      robot's own). The build identity identifies the SOFTWARE and the pin refuses a different build, nothing more; which
#      deployment is observed is the runtime's connection to the robot and the session that resident reports now;
#   6. a synthetic runtime refuses cake.robot, a rehearsal submits ONLY to a resident reporting the named fixture's
#      pinned build, and a mission records what its execution adapter said about itself;
#   7. every live runtime built here runs the FAKE controller entry, and no controller is launched at all.
# Strings the repository's text gates refuse are BUILT AT RUN TIME, never written out.
# Run directly: python3 packages/demo-runtime/tests/test_robot_link.py
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import cake_admin, daemon, forwards, mission, mission_service, read_models, robot_link, sources  # noqa: E402
from demo_runtime import supervisor as supervisor_module  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.config import ConfigError, RuntimeConfig, config_digest, robot_of  # noqa: E402
from demo_runtime.mission_coordinator import CapabilityAdapter, ExecutionAdapter, MissionCoordinator  # noqa: E402
from demo_runtime.preflight import readiness_of  # noqa: E402
from demo_runtime.rpc import MUTATING_OPS  # noqa: E402

import demo_contracts  # noqa: E402

WAIT_S = 20.0
TEST_ADDRESS = ".".join(("192", "0", "2", "11"))  # RFC 5737: names no machine that exists
TEST_TARGET = "operator" + "name" + "@" + TEST_ADDRESS
ROBOT_ID = "unit" + "-0042"
REMOTE_SOCKET = "/run/user/1000/kc-lekiwi.sock"
SESSION_ONE, SESSION_TWO = "01a0cdba8c9c7b09b03c1f945f489104", "7c3e0f1a2b4d5e6f708192a3b4c5d6e7"
SUPERVISOR_SLOT, CAPABILITY_SLOT = "50505430405080500000737570657276", "6d6f62696c655f617070726f61636801"
SUPERVISOR_CAPSULE, MOBILE_CAPSULE, INERT_CAPSULE = "a1" * 32, "b2" * 32, "c3" * 32
HOST_PID, CAPABILITY_PID = 4242, 4343
# A realistic supervisor slot instance handle: larger than 2**53, so a browser could not hold it as a number.
SLOT_INSTANCE, SPAWNS = "6003298303284871169", 2
HOST_INSTANCE = {"slot_instance": SLOT_INSTANCE, "spawn_sequence": SPAWNS, "pid": HOST_PID}
# The build identity the operator pinned for the robot, and another resident's. The TARGET PROFILE is the LeKiwi aarch64
# one for BOTH, as the captured laptop x86 cake-resident's really was: it must decide nothing.
ROBOT_BUILD, LAPTOP_BUILD = "d3" * 32, "e4" * 32
ROBOT_PROFILE = "841c3e3f73e01e48ece200ce11385aa2dc4945df51de6bef6d1f8e8bd97d15b3"

STUB_SSH = '''#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""A stand-in for ssh. It records its argv, binds the local unix socket of -L and waits. It reaches nothing."""
import json
import os
import socket
import sys
import time

argv = sys.argv[1:]
log = os.environ.get("KC_STUB_SSH_LOG")
if log:
    with open(log, "a", encoding="ascii") as handle:
        handle.write(json.dumps(argv) + "\\n")
status = os.environ.get("KC_STUB_SSH_EXIT")
if status:
    sys.exit(int(status))
local = None
for flag, value in zip(argv, argv[1:]):
    if flag == "-L":
        local = value.split(":")[0]
if os.environ.get("KC_STUB_SSH_NO_BIND") != "1":
    if os.path.lexists(local):
        sys.exit(255)  # no StreamLocalBindUnlink: ssh cannot bind over an existing path, and ExitOnForwardFailure exits
    holder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    holder.bind(local)
    holder.listen(16)
while True:
    time.sleep(0.05)
'''

STATUS_LINES = ("config_identity " + "8b" * 32,
                "plan_digest " + "c9" * 32, "epoch 3", "slots 2", "bindings 1", "resources 0", "transaction_state none",
                "flight_sequence_oldest 0", "flight_sequence_next 4")


class Clock:
    def __init__(self, start=5_000_000_000_000):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += int(seconds * 1e9)


def wait_for(test, predicate, what):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    test.fail(f"timed out waiting for {what}")


class Listening:
    """A unix socket that accepts and closes: what the forward's local end looks like while ssh holds it."""

    def __init__(self, path):
        self.path = str(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(16)
        self.sock.settimeout(0.1)
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._halt.is_set():
            try:
                peer, _ = self.sock.accept()
                peer.close()
            except OSError:
                continue

    def close(self, *, leave_file=False):
        self._halt.set()
        self._thread.join(timeout=2.0)
        self.sock.close()
        if not leave_file:
            try:
                os.unlink(self.path)
            except OSError:
                pass


class FakeLink:
    """RobotLink's surface, steered by a test: `up` is whether the forward says connected."""

    def __init__(self):
        self.up, self.starts, self.stops = True, 0, 0

    def start(self):
        self.starts += 1
        return self

    def check(self):
        return forwards.CONNECTED if self.up else forwards.FAILED

    def connected(self):
        return self.up

    def report(self):
        return {"state": self.check(), "reason": None if self.up else "ssh_exited_255", "since_wall_utc": "2026-09-23T10:00:00.000Z",
                "since_age_ms": 5.0, "starts": 1, "deaths": 0 if self.up else 1, "restarts": 0,
                "next_attempt_in_ms": None if self.up else 1000.0}

    def stop(self):
        self.stops += 1


class FakeWatch:
    """The ssh readiness reading, answered from memory: a state and the robot-side pids it found."""

    def __init__(self, state="running_normal", pids=(HOST_PID,)):
        self.state, self.pids, self.starts, self.stops = state, pids, 0, 0

    def start(self):
        self.starts += 1
        return self

    def stop(self):
        self.stops += 1

    def reading(self):
        return readiness_of(self.state, None if self.state == "never_read" else 0.0)

    def host_pids(self):
        return None if self.state in ("never_read", "reading_stale") else tuple(self.pids)


class Robot:
    """The fake robot: a listening local socket, its state directory, and the fake admin-probe, all in one tmp dir."""

    def __init__(self, test):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-rl-"))
        test.addCleanup(self.remove)
        self.socket_path = self.tmp / "a.sock"
        self.state = Path(str(self.socket_path) + ".state")
        self.state.mkdir()
        self.probe = self.tmp / "admin-probe"
        shutil.copy(PACKAGE_DIR / "tests" / "fake_robot_probe.py", self.probe)
        os.chmod(self.probe, 0o755)
        self.listening = Listening(self.socket_path)
        test.addCleanup(self.listening.close)
        self.write(session=SESSION_ONE)

    def remove(self):
        """A live runtime pins its code bundle read-only inside its data directory; that is made writable first, or
        the directory outlives the test."""
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, *, session, supervisor_state="ACTIVE", host_pid=HOST_PID, capability_capsule=MOBILE_CAPSULE, health=True,
              profile=ROBOT_PROFILE, build=ROBOT_BUILD, slot_instance=SLOT_INSTANCE, spawns=SPAWNS, health_state="HEALTHY"):
        profile_line = () if profile is None else ("target_profile_digest " + profile,)
        build_line = () if build is None else ("build_identity " + build,)
        (self.state / "status").write_text("\n".join(("session_uuid " + session,) + build_line + profile_line + STATUS_LINES) + "\n",
                                           encoding="ascii")
        (self.state / "slots").write_text(
            f"slot capsule={SUPERVISOR_CAPSULE} config={'0d' * 32} generation=1 instance={slot_instance} name=supervisor.lekiwi state={supervisor_state} uuid={SUPERVISOR_SLOT}\n"
            f"slot capsule={capability_capsule} config={'0e' * 32} generation=2 instance=1 name=mobile_approach state=ACTIVE uuid={CAPABILITY_SLOT}\n",
            encoding="ascii")
        (self.state / "health").write_text(
            f"health arg0=12 arg1={host_pid} code=0 flags=1 sequence={spawns} slot={SUPERVISOR_SLOT} state=1 state_name={health_state} surface=reported\n"
            f"health arg0=3 arg1={CAPABILITY_PID} code=0 flags=1 sequence=2 slot={CAPABILITY_SLOT} state=1 state_name=HEALTHY surface=reported\n",
            encoding="ascii")
        (self.state / "flight").write_text(
            "record arg0=0 arg1=1 correlation=0 epoch=3 event=0x05fffffc flags=0 sequence=0 severity=0 subject=1\n"
            "record arg0=2 arg1=0 correlation=0 epoch=3 event=0x03000006 flags=0 sequence=1 severity=0 subject=1\n",
            encoding="ascii")
        unavailable = self.state / "health_unavailable"
        if health and unavailable.exists():
            unavailable.unlink()
        elif not health:
            unavailable.write_text("", encoding="ascii")

    def config(self, **options) -> RuntimeConfig:
        return RuntimeConfig.from_mapping(self.mapping(**options))

    def mapping(self, *, robot=True, robot_id=ROBOT_ID, capability=True, **robot_keys) -> dict:
        cake = {"admin_probe_path": str(self.probe)}
        if robot:
            cake["robot"] = {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": str(self.socket_path),
                             "expected_build_identity": ROBOT_BUILD, **robot_keys}
        else:
            cake["admin_socket_path"] = str(self.socket_path)
        if capability:
            cake["capability"] = {"name": "mobile_approach", "slot_uuid": CAPABILITY_SLOT, "active_capsule": MOBILE_CAPSULE}
        return {"data_root": str(self.tmp / "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad",
                "observation_root": "observations", "host": {"address": TEST_ADDRESS, "robot_id": robot_id}, "pi_ssh_target": TEST_TARGET,
                "cameras": {"wrist_id": "usb-test-wrist", "front_id": None}, "cake": cake}


def base_mapping(tmp: Path, cake: dict) -> dict:
    return {"data_root": str(tmp / "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad", "observation_root": "obs",
            "host": {"robot_id": ROBOT_ID}, "pi_ssh_target": TEST_TARGET, "cake": cake}


# ---- the configuration ------------------------------------------------------------------------------------------------

class TheRobotSection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-rl-cfg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.local = str(self.tmp / "robot-admin.sock")

    def load(self, **cake):
        robot = cake.get("robot")
        if isinstance(robot, dict) and "expected_build_identity" not in robot and not robot.pop("no_build", False):
            cake["robot"] = {**robot, "expected_build_identity": ROBOT_BUILD}
        elif isinstance(robot, dict):
            robot.pop("no_build", None)
        return RuntimeConfig.from_mapping(base_mapping(self.tmp, {"admin_probe_path": "/opt/cake/admin-probe", **cake}))

    def test_the_admin_socket_every_reader_uses_is_the_forward_s_local_end(self):
        config = self.load(robot={"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local})
        self.assertEqual(cake_admin.configured_paths(config), ("/opt/cake/admin-probe", self.local))
        self.assertEqual(robot_of(config).remote_admin_socket_path, REMOTE_SOCKET)

    def test_a_different_admin_socket_beside_a_robot_is_refused(self):
        with self.assertRaises(ConfigError):
            self.load(admin_socket_path="/run/other.sock", robot={"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local})
        same = self.load(admin_socket_path=self.local, robot={"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local})
        self.assertEqual(cake_admin.configured_paths(same)[1], self.local)

    def test_every_malformed_robot_section_is_refused_by_field(self):
        for robot in ({"remote_admin_socket_path": "relative.sock", "local_admin_socket_path": self.local},
                      {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": "relative.sock"},
                      {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": REMOTE_SOCKET},
                      {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": "/" + "x" * 120},
                      {"remote_admin_socket_path": REMOTE_SOCKET},
                      {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local, "surprise": 1}):
            with self.subTest(robot=sorted(robot)), self.assertRaises(ConfigError) as refused:
                self.load(robot=robot)
            self.assertNotIn(REMOTE_SOCKET, str(refused.exception))
            self.assertNotIn(self.local, str(refused.exception))

    def test_the_digest_moves_with_the_robot_and_not_without_it(self):
        plain = self.load(admin_socket_path=self.local)
        robot = self.load(robot={"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local})
        self.assertNotIn("robot", plain.to_private_dict()["cake"], "a config without a robot keeps the digest it had")
        self.assertNotEqual(config_digest(plain), config_digest(robot))
        self.assertNotIn(REMOTE_SOCKET, repr(robot))

    def test_the_expected_build_identity_is_required_and_checked(self):
        robot = {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local}
        self.assertEqual(robot_of(self.load(robot=robot)).expected_build_identity, ROBOT_BUILD)
        with self.assertRaises(ConfigError) as missing:
            self.load(robot={**robot, "no_build": True})
        self.assertIn("expected_build_identity is required", str(missing.exception))
        for bad in ("d3" * 31, "D3" * 32, "", 7, None):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.load(robot={**robot, "expected_build_identity": bad})
        with self.assertRaises(ConfigError, msg="the refuted profile key is gone, not silently accepted"):
            self.load(robot={**robot, "expected_target_profile_digest": ROBOT_PROFILE})

    def test_a_rehearsal_fixture_build_identity_is_optional_checked_and_never_the_robot_s(self):
        self.assertIsNone(self.load(admin_socket_path=self.local).cake.rehearsal_fixture_build_identity)
        self.assertEqual(self.load(admin_socket_path=self.local, rehearsal_fixture_build_identity=LAPTOP_BUILD).cake.rehearsal_fixture_build_identity,
                         LAPTOP_BUILD)
        for bad in ("e4" * 31, "E4" * 32, "", 3):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.load(admin_socket_path=self.local, rehearsal_fixture_build_identity=bad)
        robot = {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local}
        with self.assertRaises(ConfigError):
            self.load(robot=robot, rehearsal_fixture_build_identity=ROBOT_BUILD)
        plain, fixture = self.load(admin_socket_path=self.local), self.load(admin_socket_path=self.local, rehearsal_fixture_build_identity=LAPTOP_BUILD)
        self.assertNotIn("rehearsal_fixture_build_identity", plain.to_private_dict()["cake"])
        self.assertNotEqual(config_digest(plain), config_digest(fixture))

    def test_a_display_label_is_optional_public_and_never_the_robot_id(self):
        robot = {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local}
        self.assertIsNone(robot_of(self.load(robot=robot)).display_label)
        self.assertEqual(robot_of(self.load(robot={**robot, "display_label": "bench LeKiwi 2"})).display_label, "bench LeKiwi 2")
        self.assertEqual(robot_of(self.load(robot={**robot, "display_label": None})).display_label, None)
        refused = ("", " leading space", "x" * 49, "a/b", "user" + "@" + "host", ".".join(("198", "51", "100", "7")),
                   "rig " + ROBOT_ID, "the " + TEST_ADDRESS.split(".")[0] + ":22", 5)
        for label in refused:
            with self.subTest(label=label), self.assertRaises(ConfigError) as caught:
                self.load(robot={**robot, "display_label": label})
            self.assertNotIn(ROBOT_ID, str(caught.exception), "the refusal never echoes the private value")

    def test_the_sanitizer_removes_both_socket_paths(self):
        config = self.load(robot={"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local})
        clean = read_models.Sanitizer.for_runtime(config).text(f"through {self.local} to {REMOTE_SOCKET}")
        self.assertNotIn(self.local, clean)
        self.assertNotIn(REMOTE_SOCKET, clean)


# ---- 1. the forward -----------------------------------------------------------------------------------------------------

class TheRobotAdminForward(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-rl-fwd-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        stub = self.tmp / "ssh"
        stub.write_text(STUB_SSH, encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.tmp / "argv.jsonl"
        patched = mock.patch.dict(os.environ, {"PATH": f"{self.tmp}{os.pathsep}{os.environ.get('PATH', '')}", "KC_STUB_SSH_LOG": str(self.log)})
        patched.start()
        self.addCleanup(patched.stop)
        self.local = str(self.tmp / "fwd.sock")
        self.clock = Clock()

    def forward(self, **options):
        made = forwards.UnixSocketForward(ssh_target=TEST_TARGET, remote_socket=REMOTE_SOCKET, local_socket=self.local,
                                          clock_ns=self.clock, **options)
        self.addCleanup(made.stop)
        return made

    def argv_lines(self):
        return [json.loads(line) for line in self.log.read_text(encoding="ascii").splitlines()] if self.log.exists() else []

    def connected(self, forward):
        return wait_for(self, lambda: forward.check() == forwards.CONNECTED, "the forward to connect")

    def test_the_argv_is_one_unix_socket_tunnel_and_nothing_else(self):
        forward = self.forward()
        self.assertTrue(forward.start())
        self.connected(forward)
        argv, = self.argv_lines()
        self.assertEqual(argv, ["-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
                                "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=6", "-L", f"{self.local}:{REMOTE_SOCKET}", TEST_TARGET])
        self.assertNotIn("StreamLocalBindUnlink=yes", argv, "ssh must never replace whatever is at the local path")
        from demo_runtime import preflight

        self.assertEqual(forwards.FORWARD_CONNECT_TIMEOUT_S, preflight.SSH_CONNECT_TIMEOUT_S, "the same bound as the readiness probe")

    def test_it_is_connecting_until_its_socket_exists_and_connected_after(self):
        with mock.patch.dict(os.environ, {"KC_STUB_SSH_NO_BIND": "1"}):
            forward = self.forward()
            forward.start()
        self.assertEqual(forward.check(), forwards.CONNECTING)
        self.assertIsNone(forward.local_socket(), "no socket is handed out before the forward is up")
        forward.stop()
        forward.start()
        self.connected(forward)
        self.assertEqual(forward.local_socket(), self.local)

    def test_a_death_is_reported_the_stale_socket_removed_and_the_forward_restarted_after_its_backoff(self):
        forward = self.forward()
        forward.start()
        self.connected(forward)
        forward._proc.kill()
        wait_for(self, lambda: forward.check() == forwards.FAILED, "the death to be noticed")
        report = forward.report()
        self.assertEqual((report["reason"], report["deaths"], report["next_attempt_in_ms"]), ("ssh_killed_by_signal_9", 1, 1000.0))
        self.assertFalse(os.path.lexists(self.local), "a killed ssh leaves its socket; the forward removes it")
        self.assertEqual(forward.check(), forwards.FAILED, "nothing restarts before the backoff has run out")
        self.clock.advance(1.0)
        forward.check()
        self.assertEqual(forward.report()["restarts"], 1)
        self.connected(forward)

    def test_the_backoff_doubles_and_is_bounded(self):
        with mock.patch.dict(os.environ, {"KC_STUB_SSH_EXIT": "255"}):
            forward = self.forward(backoff_max_s=4.0)
            forward.start()
            waits = []
            for _ in range(4):
                wait_for(self, lambda: forward.check() == forwards.FAILED and forward._proc is None, "the stub to exit")
                report = forward.report()
                self.assertEqual(report["reason"], "ssh_exited_255")
                waits.append(report["next_attempt_in_ms"])
                self.clock.advance(report["next_attempt_in_ms"] / 1000.0)
                forward.check()
        self.assertEqual(waits, [1000.0, 2000.0, 4000.0, 4000.0])

    def test_a_stale_socket_before_start_is_removed_and_a_foreign_file_is_left_alone(self):
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(self.local)
        stale.close()  # the file stays: nothing listens on it
        forward = self.forward()
        forward.start()
        self.connected(forward)
        forward.stop()
        self.assertFalse(os.path.lexists(self.local), "a stopped forward removes its socket")
        Path(self.local).write_text("somebody else's file", encoding="ascii")
        self.assertFalse(forward.start())
        self.assertEqual((forward.state, forward.reason), (forwards.FAILED, "local_socket_path_occupied"))
        self.assertTrue(Path(self.local).is_file(), "a path that is not a socket is never removed")

    def test_a_live_socket_at_the_local_path_is_never_replaced(self):
        """Something ANSWERS at the local path (a misconfigured path could name this laptop's own resident): the forward
        refuses to start, spawns no ssh, removes nothing, and that socket still answers afterwards."""
        live = Listening(self.local)
        self.addCleanup(live.close)
        forward = self.forward()
        self.assertFalse(forward.start())
        self.assertEqual((forward.state, forward.reason), (forwards.FAILED, "local_socket_path_live"))
        self.clock.advance(60.0)
        forward.check()
        self.assertEqual(forward.reason, "local_socket_path_live", "a retry after the backoff refuses the same way")
        forward.stop()
        self.assertEqual(self.argv_lines(), [], "no ssh was started")
        self.assertTrue(robot_link.local_socket_accepts(self.local), "the live socket is still there and still answers")

    def test_only_a_refused_connection_makes_a_socket_stale(self):
        live = Listening(self.local)
        self.assertFalse(forwards.socket_is_stale(self.local))
        live.close(leave_file=True)
        self.assertTrue(forwards.socket_is_stale(self.local), "a socket file nobody listens on refuses the connection")
        with mock.patch.object(forwards.socket.socket, "connect", side_effect=socket.timeout("slow")):
            self.assertFalse(forwards.socket_is_stale(self.local), "a socket whose state cannot be told is not stale")

    def test_a_stopped_forward_is_never_restarted(self):
        forward = self.forward()
        forward.start()
        self.connected(forward)
        forward.stop()
        self.clock.advance(60.0)
        self.assertEqual(forward.check(), forwards.STOPPED)
        self.assertEqual(len(self.argv_lines()), 1)

    def test_the_link_keeps_the_forward_up_on_its_own_thread(self):
        """RobotLink with the REAL forward, built from the configuration: the keeper thread notices a death and restarts
        it after the backoff (a real one second here), and a stop ends the child and removes the socket."""
        config = RuntimeConfig.from_mapping(base_mapping(self.tmp, {"admin_probe_path": "/opt/cake/admin-probe", "robot": {
            "remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": self.local, "expected_build_identity": ROBOT_BUILD}}))
        link = robot_link.RobotLink(config, interval_s=0.02)
        self.addCleanup(link.stop)
        link.start()
        wait_for(self, lambda: link.report()["state"] == forwards.CONNECTED, "the keeper to see the forward connect")
        link.forward._proc.kill()
        wait_for(self, lambda: link.report()["restarts"] >= 1 and link.report()["state"] == forwards.CONNECTED, "the keeper to restart it")
        argv = self.argv_lines()[-1]
        self.assertEqual(argv[-3:], ["-L", f"{self.local}:{REMOTE_SOCKET}", TEST_TARGET])
        link.stop()
        self.assertEqual(link.report()["state"], forwards.STOPPED)
        self.assertFalse(os.path.lexists(self.local))

    def test_no_report_or_repr_carries_the_target_or_a_path(self):
        forward = self.forward()
        forward.start()
        self.connected(forward)
        text = json.dumps(forward.report()) + repr(forward)
        for secret in (TEST_TARGET, TEST_ADDRESS, self.local, REMOTE_SOCKET):
            self.assertNotIn(secret, text)


class TheHostPidsTheReadinessProbeReports(unittest.TestCase):
    """The same ssh question as before, with the pid of every host process the script READ printed beside it."""

    def run_with(self, returncode, stdout):
        import subprocess

        from demo_runtime import preflight

        calls = []

        def runner(argv, **options):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, stdout, "")

        return preflight.probe_motor_host_detail(TEST_TARGET, runner=runner), calls

    def test_the_pids_come_back_with_the_state_and_the_argv_is_unchanged(self):
        (state, pids), calls = self.run_with(0, "pid 4242\npid 77\nnoise\npid -3\n")
        self.assertEqual((state, pids), ("running_normal", (4242, 77)))
        self.assertEqual(calls, [["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", TEST_TARGET, "bash -s"]])
        self.assertEqual(self.run_with(255, "pid 4242\n")[0], ("unreachable", ()), "no answer carries no pid")
        from demo_runtime import preflight

        self.assertIn('echo "pid $pid"', preflight._MOTOR_HOST_SCRIPT)

    def test_the_watch_hands_out_pids_only_from_a_current_reading(self):
        from demo_runtime import preflight

        now = [1000.0]
        watch = preflight.ReadinessWatch(TEST_TARGET, probe=lambda target: ("running_normal", (4242,)), clock=lambda: now[0])
        self.assertIsNone(watch.host_pids(), "never read")
        watch.read_once()
        self.assertEqual((watch.reading().state, watch.host_pids()), ("running_normal", (4242,)))
        now[0] += preflight.READINESS_MAX_AGE_S + 1
        self.assertIsNone(watch.host_pids(), "a stale list of pids is not evidence about the host now")
        plain = preflight.ReadinessWatch(TEST_TARGET, probe=lambda target: "running_normal", clock=lambda: now[0])
        plain.read_once()
        self.assertEqual((plain.reading().state, plain.host_pids()), ("running_normal", ()), "the old probe shape still works")


# ---- reading the robot --------------------------------------------------------------------------------------------------

class TheReadings(unittest.TestCase):
    def test_list_slots_and_get_health_are_read_in_their_real_shapes(self):
        robot = Robot(self)
        reading = robot_link.read_robot(str(robot.probe), str(robot.socket_path))
        self.assertTrue(reading["reachable"])
        detail = reading["robot_detail"]
        self.assertEqual(detail["slots"][1], {"name": "supervisor.lekiwi", "uuid": SUPERVISOR_SLOT, "capsule": SUPERVISOR_CAPSULE,
                                              "generation": 1, "instance": SLOT_INSTANCE, "state": "ACTIVE"})
        self.assertIsInstance(detail["slots"][1]["instance"], str, "a u64 handle travels as a decimal string")
        self.assertEqual(detail["health"][0], {"slot": SUPERVISOR_SLOT, "state_name": "HEALTHY", "child_pid": HOST_PID,
                                               "restarts": 1, "spawns": SPAWNS, "last_status": 0})
        self.assertEqual(robot_link.supervisor_host_instance(detail), (HOST_INSTANCE, True, None))

    def test_poll_traffic_is_counted_and_never_listed_as_an_update(self):
        robot = Robot(self)
        robot_link.read_robot(str(robot.probe), str(robot.socket_path))
        reading = robot_link.read_robot(str(robot.probe), str(robot.socket_path))
        updates = reading["robot_detail"]["updates"]
        self.assertEqual([item["name"] for item in updates["items"]], ["EVT_SUPERVISOR_CHILD_STARTED", "EVT_TXN_TRANSITION"])
        self.assertGreaterEqual(updates["excluded_admin_records"], 4, "every earlier request of this runtime left one")
        self.assertTrue(all(item["event"] != "0x0B000001" for item in updates["items"]))

    def test_updates_are_newest_last_and_bounded(self):
        records = [f"arg0=0 arg1={n} correlation=0 epoch=1 event=0x05fffff8 flags=0 sequence={n} severity=0 subject=1"
                   for n in reversed(range(robot_link.MAX_UPDATES + 5))]
        updates = robot_link.parse_updates(records)
        self.assertEqual(len(updates["items"]), robot_link.MAX_UPDATES)
        self.assertEqual(updates["dropped"], 5)
        self.assertEqual(updates["items"][-1]["sequence"], robot_link.MAX_UPDATES + 4)

    def test_no_local_probe_is_consulted_and_a_down_forward_is_robot_unreachable(self):
        robot = Robot(self)
        link = FakeLink()
        link.up = False
        with mock.patch.object(read_models, "probe_cake", side_effect=AssertionError("a local probe answered for the robot")):
            probe = robot_link.probe_robot(link, str(robot.probe), str(robot.socket_path), expected_build_identity=ROBOT_BUILD)
        self.assertEqual(probe["scope"], "robot_resident")
        self.assertEqual(probe["inventory"]["reason_code"], "robot_unreachable")
        self.assertIsNone(probe["artifacts"], "this laptop's release directory is not the robot's")

    def test_a_stale_socket_file_reads_as_a_connection_failure_not_a_cake_refusal(self):
        robot = Robot(self)
        robot.listening.close(leave_file=True)
        self.assertTrue(os.path.lexists(robot.socket_path))
        probe = robot_link.probe_robot(FakeLink(), str(robot.probe), str(robot.socket_path), expected_build_identity=ROBOT_BUILD)
        self.assertFalse(probe["inventory"]["reachable"])
        self.assertEqual(probe["inventory"]["reason_code"], "robot_forward_socket_stale")

    def test_a_live_forward_to_a_resident_that_does_not_answer_is_not_read_as_a_refusal(self):
        robot = Robot(self)
        shutil.rmtree(robot.state)  # the fake client connects and then gets nothing it can read, as through a live ssh
        probe = robot_link.probe_robot(FakeLink(), str(robot.probe), str(robot.socket_path), expected_build_identity=ROBOT_BUILD)
        self.assertEqual(probe["inventory"]["reason_code"], "robot_resident_not_answering")

    def test_only_the_pinned_build_identity_passes_and_the_profile_decides_nothing(self):
        """The refuted check, as the verifier found it: the laptop x86 resident reports the robot's LeKiwi aarch64 target
        profile. It does not report the pinned build, so it is refused; a resident that does report it passes whatever
        profile it reports. The pin identifies software; the connection and the session say which deployment answered."""
        robot = Robot(self)
        robot.write(session=SESSION_ONE, build=LAPTOP_BUILD, profile=ROBOT_PROFILE)
        probe = robot_link.probe_robot(FakeLink(), str(robot.probe), str(robot.socket_path), expected_build_identity=ROBOT_BUILD)
        inventory = probe["inventory"]
        self.assertEqual((inventory["reachable"], inventory["reason_code"]), (False, robot_link.NOT_THE_ROBOT))
        self.assertEqual((inventory["identity"], probe["resident"]), ({}, False), "nothing of its answer is kept as the robot resident's")
        self.assertNotIn("robot_detail", inventory)
        self.assertEqual((inventory["answering_build_identity"], inventory["answering_target_profile_digest"]), (LAPTOP_BUILD, ROBOT_PROFILE))
        robot.write(session=SESSION_ONE, build=None)
        unreadable = robot_link.probe_robot(FakeLink(), str(robot.probe), str(robot.socket_path), expected_build_identity=ROBOT_BUILD)
        self.assertEqual(unreadable["inventory"]["reason_code"], robot_link.BUILD_UNREADABLE)
        robot.write(session=SESSION_ONE, profile="9300143a" + "5e" * 28)
        other_profile = robot_link.probe_robot(FakeLink(), str(robot.probe), str(robot.socket_path), expected_build_identity=ROBOT_BUILD)
        self.assertTrue(other_profile["inventory"]["reachable"], "the pinned build identity decides; a profile never does")
        robot.write(session=SESSION_ONE)
        self.assertEqual(robot_link.probe_robot(FakeLink(), str(robot.probe), str(robot.socket_path), expected_build_identity=LAPTOP_BUILD)["inventory"]["reason_code"],
                         robot_link.NOT_THE_ROBOT, "the configured build identity decides")

    def test_the_verifier_s_captured_laptop_status_does_not_report_the_pinned_build(self):
        """The real capture's field lines (identities only): target profile 841c3e3f..., build d392ef91..."""
        status = {"ok": True, "fields": {"session_uuid": "01a0ce11cf9b7681bd94e1a4f3b27afa", "target_profile_digest": ROBOT_PROFILE,
                                         "build_identity": "d392ef91e3fbffd825c693665f6b49341efb02666a2a7d23731ed4764450ee33"}}
        inventory = cake_admin.inventory_of(status, None, cake_admin.observed_now())
        self.assertTrue(inventory["reachable"])
        self.assertEqual(robot_link.verify_identity(inventory, ROBOT_BUILD)["reason_code"], robot_link.NOT_THE_ROBOT)

    def test_a_host_instance_is_given_only_while_a_host_runs_and_every_component_was_read(self):
        robot = Robot(self)
        cases = {
            ("supervisor_slot_not_active", False): {"supervisor_state": "INACTIVE"},
            ("supervisor_child_not_running", False): {"host_pid": 0},
            ("supervisor_host_not_healthy", False): {"health_state": "DEGRADED"},
            ("health_unavailable", None): {"health": False},
            ("supervisor_slot_instance_unreadable", None): {"slot_instance": "0"},
            ("supervisor_spawn_sequence_unreadable", None): {"spawns": 1 << 53},
            ("supervisor_child_pid_unreadable", None): {"host_pid": (1 << 22) + 1},
        }
        for (reason, running), write in cases.items():
            with self.subTest(reason=reason):
                robot.write(session=SESSION_ONE, **write)
                detail = robot_link.read_robot(str(robot.probe), str(robot.socket_path))["robot_detail"]
                self.assertEqual(robot_link.supervisor_host_instance(detail), (None, running, reason))

    def test_the_supervisor_slot_and_its_counters_are_kept_whatever_the_host_instance_answer(self):
        """supervisor_host_instance stops at the first thing that decides whether a host runs, before the counters are
        kept. supervisor_slot_reading keeps them for every reading, so a caller comparing two readings can see a spawn
        between them; binding_of carries it beside the binding, and a reading that answered nothing carries None."""
        robot = Robot(self)
        for write, expected in (
                ({"host_pid": 0, "health_state": "DEGRADED", "spawns": 5},
                 {"slot_present": True, "slot_state": "ACTIVE", "slot_instance": SLOT_INSTANCE, "slot_generation": 1,
                  "health_read": True, "health_row": True, "child_pid": 0, "spawns": 5, "restarts": 1, "health_state": "DEGRADED"}),
                ({"supervisor_state": "INACTIVE", "host_pid": 4242},
                 {"slot_present": True, "slot_state": "INACTIVE", "slot_instance": SLOT_INSTANCE, "slot_generation": 1,
                  "health_read": True, "health_row": True, "child_pid": 4242, "spawns": SPAWNS, "restarts": 1, "health_state": "HEALTHY"}),
                ({"supervisor_state": "INACTIVE", "health": False},
                 {"slot_present": True, "slot_state": "INACTIVE", "slot_instance": SLOT_INSTANCE, "slot_generation": 1,
                  "health_read": False, "health_row": False, "child_pid": None, "spawns": None, "restarts": None, "health_state": None})):
            with self.subTest(**write):
                robot.write(session=SESSION_ONE, **write)
                reading = robot_link.read_robot(str(robot.probe), str(robot.socket_path))
                self.assertEqual(robot_link.supervisor_slot_reading(reading["robot_detail"]), expected)
                self.assertEqual(robot_link.binding_of(reading)["supervisor_slot"], expected)
        self.assertIsNone(robot_link.supervisor_slot_reading({"slots_reason": "admin_answer_not_ok"}))
        self.assertIsNone(robot_link.binding_of(cake_admin.unavailable(robot_link.UNREACHABLE))["supervisor_slot"])

    def test_the_client_shape_of_a_host_instance_is_exact(self):
        self.assertEqual(robot_link.host_instance_of(dict(HOST_INSTANCE)), HOST_INSTANCE)
        for bad in (None, [], {}, {**HOST_INSTANCE, "extra": 1}, {"slot_instance": SLOT_INSTANCE, "pid": HOST_PID},
                    {**HOST_INSTANCE, "slot_instance": int(SLOT_INSTANCE)}, {**HOST_INSTANCE, "slot_instance": "0"},
                    {**HOST_INSTANCE, "slot_instance": "0" + SLOT_INSTANCE}, {**HOST_INSTANCE, "slot_instance": str(1 << 64)},
                    {**HOST_INSTANCE, "spawn_sequence": 0}, {**HOST_INSTANCE, "spawn_sequence": True},
                    {**HOST_INSTANCE, "spawn_sequence": 2.0}, {**HOST_INSTANCE, "spawn_sequence": 1 << 53},
                    {**HOST_INSTANCE, "pid": 0}, {**HOST_INSTANCE, "pid": "4242"}, {**HOST_INSTANCE, "pid": (1 << 22) + 1}):
            with self.subTest(bad=bad):
                self.assertIsNone(robot_link.host_instance_of(bad))

    def test_records_outside_the_allowlist_are_counted(self):
        records = ["arg0=0 arg1=0 correlation=0 epoch=1 event=0x05000003 flags=0 sequence=1 severity=0 subject=1",
                   "arg0=0 arg1=0 correlation=0 epoch=1 event=0x05000006 flags=0 sequence=2 severity=0 subject=1",
                   "arg0=0 arg1=0 correlation=0 epoch=1 event=0x0b000001 flags=0 sequence=3 severity=0 subject=1",
                   "arg0=0 arg1=0 correlation=0 epoch=1 event=0x05fffffc flags=0 sequence=4 severity=0 subject=1"]
        updates = robot_link.parse_updates(records)
        self.assertEqual((len(updates["items"]), updates["excluded_admin_records"], updates["other_records"]), (1, 1, 2))

    def test_the_cake_ladder_about_the_robot_rests_on_nothing_this_laptop_holds(self):
        view = read_models.cake_view({"scope": "robot_resident", "artifacts": None, "resident": False, "admin_socket": False,
                                      "inventory": cake_admin.unavailable("robot_unreachable")})
        self.assertEqual(demo_contracts.validate("cake", view), [])
        text = json.dumps(view)
        self.assertNotIn("this machine", text)
        self.assertIn("nothing on this laptop is consulted", view["ladder"]["resident_reachable"]["evidence"])
        local = read_models.cake_view(read_models.probe_cake(REPO_ROOT, proc_root=str(REPO_ROOT / "no-such-proc"), runtime_dirs=[]))
        self.assertIn("this laptop", local["ladder"]["resident_reachable"]["evidence"], "the laptop path says whose it is")


# ---- 3. the robot_status surface ------------------------------------------------------------------------------------------

class TheRobotStatusSurface(unittest.TestCase):
    def setUp(self):
        self.robot = Robot(self)
        self.config = self.robot.config()

    def view(self, reading=None, *, retained=None, watch=None, permission=None, config=None, link=None):
        link = link or FakeLink()
        if reading is None:
            robot = robot_of(config or self.config)
            expected = robot.expected_build_identity if robot is not None else ROBOT_BUILD
            reading = robot_link.verify_identity(robot_link.read_robot(str(self.robot.probe), str(self.robot.socket_path)), expected)
        watch = watch if watch is not None else FakeWatch()
        answer = robot_link.robot_status_view(config=config or self.config, probe={"inventory": reading}, retained=retained,
                                              session_changed=None, forward_report=link.report(), readiness=watch.reading(),
                                              host_pids=watch.host_pids(), permission=permission)
        self.assertEqual(demo_contracts.validate_robot_status(answer), [], "every answer validates against its own schema")
        return answer

    def test_a_connected_robot_is_reported_whole(self):
        answer = self.view()
        self.assertEqual((answer["configured"], answer["deployment"], answer["reachable"], answer["stale"]), (True, "robot_resident", True, False))
        self.assertEqual(answer["resident"]["session_uuid"], SESSION_ONE)
        self.assertEqual(answer["resident"]["epoch"], 3)
        self.assertEqual(answer["connection"]["forward"]["state"], "connected")
        self.assertNotIn(ROBOT_ID, json.dumps(answer), "the robot id is private and is sent in no form")
        self.assertNotIn("robot_id_digest", answer["connection"])
        identity = answer["identity"]
        self.assertEqual((identity["is_robot"], identity["reason_code"], identity["answering_build_identity"]), (True, None, ROBOT_BUILD))
        self.assertEqual((identity["expected_build_identity"], identity["answering_target_profile_digest"]), (ROBOT_BUILD, ROBOT_PROFILE))
        self.assertEqual((identity["display_label"], identity["labelled"]), (None, False), "absent label: unlabelled, never the id")
        self.assertEqual((identity["build_identity"], identity["session_uuid"]), (ROBOT_BUILD, SESSION_ONE))
        self.assertEqual(answer["robot_status_version"], "2")
        self.assertTrue(answer["host"]["supervised"])
        host = answer["host"]
        self.assertEqual((host["slot_instance"], host["spawn_sequence"], host["child_pid"], host["health_state"]),
                         (SLOT_INSTANCE, SPAWNS, HOST_PID, "HEALTHY"))
        self.assertEqual((answer["capability"]["active"], answer["capability"]["child_pid"]), (True, CAPABILITY_PID))
        for section in ("resident", "slots", "health", "updates", "host", "capability"):
            self.assertIsInstance(answer[section]["observed"]["age_ms"], float, section)

    def test_which_lekiwi_is_the_operator_s_public_label(self):
        config = self.robot.config(display_label="bench LeKiwi")
        answer = self.view(config=config)
        self.assertEqual((answer["identity"]["display_label"], answer["identity"]["labelled"]), ("bench LeKiwi", True))
        self.assertNotIn(ROBOT_ID, json.dumps(answer))

    def test_a_resident_that_does_not_report_the_pinned_build_is_never_shown_as_the_robot_resident(self):
        """This laptop's x86 resident answering through the forward, reporting the ROBOT's target profile as the real one
        does: its build identity is not the pinned one, so it is refused, nothing it reports is shown as the robot
        resident's and nothing moves."""
        self.robot.write(session=SESSION_TWO, build=LAPTOP_BUILD, profile=ROBOT_PROFILE)
        permission = robot_link.MotionPermission(self.robot.tmp)
        answer = self.view(permission=permission)
        self.assertEqual((answer["configured"], answer["reachable"], answer["reason_code"]), (True, False, robot_link.NOT_THE_ROBOT))
        self.assertEqual((answer["identity"]["is_robot"], answer["identity"]["answering_build_identity"]), (False, LAPTOP_BUILD))
        self.assertEqual(answer["identity"]["answering_target_profile_digest"], ROBOT_PROFILE, "shown as information only")
        self.assertEqual(answer["identity"]["expected_build_identity"], ROBOT_BUILD)
        self.assertIsNone(answer["identity"]["build_identity"], "its build is not shown as the robot's")
        self.assertIsNone(answer["resident"]["session_uuid"], "its session is not shown as the robot's")
        self.assertEqual((answer["slots"]["items"], answer["health"]["items"], answer["updates"]["items"]), ([], [], []))
        self.assertEqual((answer["host"]["supervised"], answer["host"]["reason_code"]), (False, robot_link.NOT_THE_ROBOT))
        self.assertFalse(answer["capability"]["active"])
        self.assertEqual((answer["motion_permission"]["permitted"], answer["motion_permission"]["reason_code"]), (False, robot_link.NOT_THE_ROBOT))
        motion = answer["motion_permission"]
        self.assertEqual((motion["current_session_uuid"], motion["current_host_instance"], motion["confirmable"]), (None, None, False),
                         "nothing to confirm is offered")
        self.assertNotIn(SESSION_TWO, json.dumps(answer))

    def test_the_motion_permission_names_what_a_confirmation_must_carry(self):
        motion = self.view(permission=robot_link.MotionPermission(self.robot.tmp))["motion_permission"]
        self.assertEqual((motion["current_session_uuid"], motion["current_host_instance"], motion["confirmable"]),
                         (SESSION_ONE, HOST_INSTANCE, True))
        self.assertIsInstance(motion["current_host_instance"]["slot_instance"], str)
        self.assertEqual((motion["confirmed_session_uuid"], motion["confirmed_host_instance"], motion["reason_code"], motion["changed_component"]),
                         (None, None, robot_link.NOT_CONFIRMED, None))

    def test_nothing_is_offered_for_confirmation_without_a_running_identified_host(self):
        cases = {"robot_host_not_running": ({"host_pid": 0}, {"health_state": "DEGRADED"}, {"supervisor_state": "INACTIVE"}),
                 "robot_host_unreadable": ({"health": False}, {"slot_instance": "0"})}
        for reason, writes in cases.items():
            for write in writes:
                with self.subTest(write=write):
                    self.robot.write(session=SESSION_ONE, **write)
                    motion = self.view(permission=robot_link.MotionPermission(self.robot.tmp))["motion_permission"]
                    self.assertEqual((motion["confirmable"], motion["current_host_instance"], motion["current_host_reason_code"]),
                                     (False, None, reason))
        self.robot.write(session=SESSION_ONE)
        good = robot_link.read_robot(str(self.robot.probe), str(self.robot.socket_path))
        link = FakeLink()
        link.up = False
        retained = self.view(cake_admin.unavailable("robot_unreachable"), retained=good, link=link,
                             permission=robot_link.MotionPermission(self.robot.tmp))["motion_permission"]
        self.assertEqual((retained["confirmable"], retained["current_session_uuid"], retained["current_host_instance"]), (False, None, None),
                         "a retained reading offers nothing to confirm")

    def test_other_records_are_counted_on_the_surface(self):
        with open(self.robot.state / "flight", "a", encoding="ascii") as handle:
            handle.write("record arg0=0 arg1=0 correlation=0 epoch=3 event=0x05000004 flags=0 sequence=2 severity=0 subject=1\n")
        self.assertEqual(self.view()["updates"]["other_records"], 1)

    def test_supervised_needs_all_three_witnesses(self):
        cases = {
            "supervisor_slot_not_active": dict(write={"supervisor_state": "INACTIVE"}),
            "supervisor_child_not_running": dict(write={"host_pid": 0}),
            "supervisor_host_not_healthy": dict(write={"health_state": "DEGRADED"}),
            "health_unavailable": dict(write={"health": False}),
            "host_pid_mismatch": dict(watch=FakeWatch(pids=(HOST_PID + 1,))),
            "host_not_running": dict(watch=FakeWatch(state="not_running", pids=())),
            "host_readiness_not_current": dict(watch=FakeWatch(state="reading_stale")),
        }
        for reason, case in cases.items():
            with self.subTest(reason=reason):
                self.robot.write(session=SESSION_ONE, **case.get("write", {}))
                answer = self.view(watch=case.get("watch"))
                self.assertEqual((answer["host"]["supervised"], answer["host"]["reason_code"]), (False, reason))
        self.robot.write(session=SESSION_ONE)
        answer = robot_link.robot_status_view(config=self.config, probe={"inventory": robot_link.read_robot(str(self.robot.probe), str(self.robot.socket_path))},
                                              retained=None, session_changed=None, forward_report=None, readiness=None, host_pids=None)
        self.assertEqual(answer["host"]["reason_code"], "host_readiness_not_read")

    def test_an_inert_capsule_is_not_the_capability(self):
        self.robot.write(session=SESSION_ONE, capability_capsule=INERT_CAPSULE)
        capability = self.view()["capability"]
        self.assertEqual((capability["active"], capability["reason_code"]), (False, "capability_capsule_not_active"))

    def test_a_lost_robot_keeps_its_last_reading_marked_stale_and_moves_nothing(self):
        good = robot_link.read_robot(str(self.robot.probe), str(self.robot.socket_path))
        link = FakeLink()
        link.up = False
        answer = self.view(cake_admin.unavailable("robot_unreachable"), retained=good, link=link)
        self.assertEqual((answer["reachable"], answer["reason_code"], answer["stale"]), (False, "robot_unreachable", True))
        self.assertEqual(len(answer["slots"]["items"]), 2, "retained, not emptied")
        self.assertTrue(answer["slots"]["stale"])
        self.assertFalse(answer["host"]["supervised"], "a retained reading never says the host is supervised now")
        self.assertFalse(answer["capability"]["active"], "a retained reading never makes a capability active")
        self.assertEqual(answer["connection"]["forward"]["state"], "failed")

    def test_a_laptop_resident_is_never_reported_as_the_robot_s(self):
        config = self.robot.config(robot=False)
        reading = robot_link.read_robot(str(self.robot.probe), str(self.robot.socket_path))
        answer = self.view(reading, config=config)
        self.assertEqual((answer["configured"], answer["deployment"], answer["reachable"]), (False, "this_laptop", False))
        self.assertIsNone(answer["resident"]["session_uuid"])
        self.assertEqual(answer["slots"]["items"], [])
        self.assertEqual(answer["motion_permission"]["applies"], False)

    def test_the_schema_says_poll_traffic_is_excluded(self):
        schema = demo_contracts.load_robot_status_schema()
        self.assertIn("POLL TRAFFIC IS EXCLUDED", schema["properties"]["updates"]["description"])
        self.assertEqual(schema["properties"]["robot_status_version"]["const"], robot_link.ROBOT_STATUS_VERSION)


# ---- 2, 4 and 5 through the daemon ------------------------------------------------------------------------------------------

def command(command_type, args, command_id):
    return {"contract_version": demo_contracts.CONTRACT_VERSION, "command_id": command_id, "type": command_type, "args": args,
            "client": {"session_id": "tab-0123456789ab", "contract_version": demo_contracts.CONTRACT_VERSION}}


MOVE = command("manual_pan", {"deg": 5.0, "holding": False, "plan_only": False}, "0a1b2c3d4e5f60718293")
COMPONENT_COMMAND_IDS = {"spawn_sequence": "7a1b2c3d4e5f60718201", "slot_instance": "7a1b2c3d4e5f60718202", "pid": "7a1b2c3d4e5f60718203",
                         "host_stopped": "7a1b2c3d4e5f60718204", "session": "7a1b2c3d4e5f60718205"}
OBSERVE = command("observe", {"holding": False}, "1a1b2c3d4e5f60718293")


class TheDaemonWithARobot(unittest.TestCase):
    def setUp(self):
        self.robot = Robot(self)
        self.data = self.robot.tmp / "data"
        self.link, self.watch = FakeLink(), FakeWatch()
        # THE SECOND BARRIER. Every live runtime here runs the FAKE controller, which touches nothing and writes one line
        # per launch to this file; the file must never appear, so not even the fake is launched by anything here.
        self.launches = self.robot.tmp / "controller-launches.log"
        self.addCleanup(lambda: self.assertFalse(self.launches.exists(), "a controller was launched"))

    def core(self, *, mode=daemon.LIVE, config=None):
        core = daemon.RuntimeCore(data_dir=self.data, mode=mode, config=config or self.robot.config(), source_root=REPO_ROOT,
                                  robot_link=self.link, robot_host_watch=self.watch, controller_entry=daemon.FAKE_CONTROLLER_ENTRY,
                                  extra_env={"KC_FAKE_LAUNCH_LOG": str(self.launches)},
                                  source_supervisor=(lambda hub: sources.SourceSupervisor(hub)) if mode == daemon.LIVE else None)
        self.addCleanup(self.close, core)
        return core

    @staticmethod
    def close(core):
        if not getattr(core, "_closed_by_test", False):
            core._closed_by_test = True
            core.close()

    def ask(self, core, op, **request):
        core.cake.invalidate()
        return core.handle(op, {"op": op, **request}, None)

    def confirm(self, core, session=SESSION_ONE, **instance):
        return self.ask(core, "robot_confirm_session", session_uuid=session, host_instance={**HOST_INSTANCE, **instance})

    def submit(self, core, body):
        return core.handle("submit", {"op": "submit", "command": body}, None)

    def motion_capabilities(self, core):
        """The snapshot's capabilities with nothing SHARED blocking, so only the robot session gate is left to show.
        The patch lasts for this one snapshot: a submit must still meet the real arbitration, which in this runtime
        (no --enable-hardware-commands) refuses before anything is started."""
        core.cake.invalidate()
        with mock.patch.object(core.supervisor, "block", lambda action=None: None):
            return {item["id"]: item for item in core.snapshot()["capabilities"]}

    def test_every_live_runtime_here_runs_the_fake_controller(self):
        core = self.core()
        self.assertEqual(core.supervisor.controller_entry, daemon.FAKE_CONTROLLER_ENTRY)

    def test_the_forward_is_owned_and_no_local_probe_answers_for_the_robot(self):
        with mock.patch.object(read_models, "probe_cake", side_effect=AssertionError("a local probe answered for the robot")):
            core = self.core()
            answer = self.ask(core, "robot_status")
            self.assertEqual(demo_contracts.validate_robot_status(answer), [])
            self.assertTrue(answer["reachable"])
            self.assertEqual(demo_contracts.validate("snapshot", core.snapshot()), [])
        self.assertEqual(self.link.starts, 1)
        self.assertTrue(answer["host"]["supervised"])
        self.assertEqual(self.watch.starts, 1)

    def test_a_down_forward_is_robot_unreachable_with_the_last_reading_retained_and_aged(self):
        core = self.core()
        self.ask(core, "robot_status")
        self.link.up = False
        time.sleep(0.01)
        answer = self.ask(core, "robot_status")
        self.assertEqual((answer["reachable"], answer["reason_code"], answer["stale"]), (False, "robot_unreachable", True))
        self.assertGreater(answer["resident"]["observed"]["age_ms"], 0.0)
        self.assertEqual(answer["resident"]["session_uuid"], SESSION_ONE)
        cake = self.ask(core, "cake_status")
        self.assertEqual((cake["reachable"], cake["stale"], cake["reason_code"]), (False, True, "robot_unreachable"))

    def test_without_a_confirmation_every_motion_command_is_refused_and_observe_is_not(self):
        core = self.core()
        capabilities = self.motion_capabilities(core)
        for capability in read_models.MOTION_CAPABILITIES:
            self.assertEqual((capabilities[capability]["state"], capabilities[capability]["reason_code"]), ("blocked", "robot_session_not_confirmed"))
        self.assertEqual(capabilities["observe"]["state"], "enabled")
        self.assertEqual(self.submit(core, MOVE)["reason_code"], "robot_session_not_confirmed")
        observed = self.submit(core, OBSERVE)
        self.assertEqual((observed["status"], observed["reason_code"]), ("rejected", "hardware_commands_not_enabled"),
                         "observe meets the ordinary arbitration and never the robot session gate")
        self.assertIsNone(core.supervisor.active_run(), "no controller was started by any of this")

    def test_the_confirmation_takes_exactly_the_session_and_the_host_instance(self):
        core = self.core()
        for request in ({"session_uuid": SESSION_ONE}, {"host_instance": HOST_INSTANCE},
                        {"session_uuid": SESSION_ONE, "host_instance": HOST_INSTANCE, "extra": 1},
                        {"session_uuid": SESSION_ONE, "host_pid": HOST_PID}, {"session_uuid": "not hex", "host_instance": HOST_INSTANCE},
                        {"session_uuid": SESSION_ONE, "host_instance": {**HOST_INSTANCE, "pid": 0}},
                        {"session_uuid": SESSION_ONE, "host_instance": {**HOST_INSTANCE, "slot_instance": int(SLOT_INSTANCE)}},
                        {"session_uuid": SESSION_ONE, "host_instance": {"slot_instance": SLOT_INSTANCE, "pid": HOST_PID}},
                        {"session_uuid": SESSION_ONE, "host_instance": {**HOST_INSTANCE, "spawn_sequence": True}}):
            with self.subTest(request=sorted(request)):
                self.assertEqual(self.ask(core, "robot_confirm_session", **request)["reason_code"], "bad_request")
        self.assertIsNone(core.motion_permission.confirmed())

    def test_only_an_explicit_confirm_with_every_current_component_restores_it(self):
        core = self.core()
        self.assertEqual(self.confirm(core, session=SESSION_TWO)["reason_code"], "robot_session_mismatch")
        refusals = [self.confirm(core, pid=HOST_PID + 1), self.confirm(core, spawn_sequence=SPAWNS + 1),
                    self.confirm(core, slot_instance="6003298303284871170")]
        self.assertEqual([answer["reason_code"] for answer in refusals],
                         ["robot_host_pid_mismatch", "robot_host_spawn_sequence_mismatch", "robot_host_slot_instance_mismatch"])
        for answer in refusals + [self.confirm(core, session=SESSION_TWO)]:
            text = json.dumps(answer)
            self.assertNotIn(SESSION_ONE, text, "a refusal never hands the caller the session to confirm")
            self.assertNotIn(str(HOST_PID), text, "nor the host pid")
            self.assertNotIn(SLOT_INSTANCE, text, "nor the slot instance")
        confirmed = self.confirm(core)
        self.assertEqual((confirmed["ok"], confirmed["motion_permitted"], confirmed["confirmed_host_instance"]), (True, True, HOST_INSTANCE))
        self.assertEqual(self.motion_capabilities(core)["manual_pan"]["state"], "enabled")
        motion = self.ask(core, "robot_status")["motion_permission"]
        self.assertEqual((motion["permitted"], motion["confirmed_session_uuid"], motion["confirmed_host_instance"]), (True, SESSION_ONE, HOST_INSTANCE))

    def test_a_host_that_is_not_running_cannot_be_confirmed(self):
        core = self.core()
        for write in ({"host_pid": 0}, {"health_state": "DEGRADED"}, {"supervisor_state": "INACTIVE"}):
            with self.subTest(write=write):
                self.robot.write(session=SESSION_ONE, **write)
                self.assertEqual(self.confirm(core)["reason_code"], "robot_host_not_running")
        self.robot.write(session=SESSION_ONE, health=False)
        self.assertEqual(self.confirm(core)["reason_code"], "robot_host_unreadable")
        self.assertIsNone(core.motion_permission.confirmed())

    def test_a_resident_restart_revokes_it(self):
        core = self.core()
        self.confirm(core)
        self.robot.write(session=SESSION_TWO)
        capabilities = self.motion_capabilities(core)
        self.assertEqual(capabilities["manual_move"]["reason_code"], "robot_session_changed")
        self.assertEqual(self.submit(core, MOVE)["reason_code"], "robot_session_changed")
        status = self.ask(core, "robot_status")
        self.assertEqual((status["motion_permission"]["permitted"], status["resident"]["session_changed"]), (False, True))
        self.assertEqual(self.confirm(core, session=SESSION_ONE)["reason_code"], "robot_session_mismatch")
        self.assertEqual(self.confirm(core, session=SESSION_TWO)["ok"], True)
        self.assertEqual(self.motion_capabilities(core)["manual_move"]["state"], "enabled")

    def test_a_host_restart_revokes_it_and_it_stays_revoked(self):
        core = self.core()
        self.confirm(core)
        self.robot.write(session=SESSION_ONE, host_pid=HOST_PID + 7, spawns=SPAWNS + 1)
        self.assertEqual(self.submit(core, MOVE)["reason_code"], "robot_host_changed")
        self.robot.write(session=SESSION_ONE, host_pid=HOST_PID)
        self.assertEqual(self.submit(core, command("manual_pan", {"deg": 1.0, "holding": False, "plan_only": False}, "3a1b2c3d4e5f60718293"))["reason_code"],
                         "robot_host_changed", "the old pid coming back does not restore a revoked confirmation")
        self.assertIsNone(core.motion_permission.confirmed())
        self.assertEqual(self.motion_capabilities(core)["manual_grip"]["reason_code"], "robot_host_changed")
        self.assertEqual(self.confirm(core)["ok"], True)
        self.assertEqual(self.motion_capabilities(core)["manual_grip"]["state"], "enabled")

    def test_every_component_of_the_host_instance_revokes_it_on_its_own(self):
        """A host restart that REUSES the pid still moves the spawn sequence; a slot re-create moves the instance handle."""
        for component, write in (("spawn_sequence", {"spawns": SPAWNS + 1}), ("slot_instance", {"slot_instance": "911746368697286402"}),
                                 ("pid", {"host_pid": HOST_PID + 1}), ("host_stopped", {"health_state": "DEGRADED"}),
                                 ("session", {"session": SESSION_TWO})):
            with self.subTest(component=component):
                core = self.core()
                self.robot.write(session=SESSION_ONE)
                self.assertEqual(self.confirm(core)["ok"], True)
                self.robot.write(**{"session": SESSION_ONE, **write})
                command_id = COMPONENT_COMMAND_IDS[component]
                refused = self.submit(core, command("manual_pan", {"deg": 1.0, "holding": False, "plan_only": False}, command_id))
                self.assertEqual(refused["reason_code"], "robot_session_changed" if component == "session" else "robot_host_changed")
                status = self.ask(core, "robot_status")
                self.assertEqual((status["motion_permission"]["permitted"], status["motion_permission"]["changed_component"]), (False, component))
                self.assertIsNone(core.motion_permission.confirmed())
                self.close(core)
                self.robot.write(session=SESSION_ONE)

    def test_a_host_that_stops_revokes_it(self):
        core = self.core()
        self.confirm(core)
        self.robot.write(session=SESSION_ONE, supervisor_state="DEGRADED")
        self.assertEqual(self.submit(core, MOVE)["reason_code"], "robot_host_changed")

    def test_an_unreadable_robot_refuses_motion_even_when_confirmed_and_revokes_nothing(self):
        core = self.core()
        self.confirm(core)
        self.link.up = False
        self.assertEqual(self.submit(core, MOVE)["reason_code"], "robot_unreachable")
        self.assertEqual(self.confirm(core)["reason_code"], "robot_unreachable")
        self.assertIsNotNone(core.motion_permission.confirmed(), "a dropped link is not a change")
        self.link.up = True
        self.robot.write(session=SESSION_ONE, health=False)
        self.assertEqual(self.motion_capabilities(core)["manual_pan"]["reason_code"], "robot_host_unreadable")
        self.assertIsNotNone(core.motion_permission.confirmed(), "an unreadable host is not a change either")
        self.robot.write(session=SESSION_ONE)
        self.assertEqual(self.motion_capabilities(core)["manual_pan"]["state"], "enabled")

    def test_a_resident_that_is_not_the_robot_moves_nothing_revokes_and_cannot_be_confirmed(self):
        core = self.core()
        self.confirm(core)
        self.robot.write(session=SESSION_ONE, build=LAPTOP_BUILD)
        self.assertEqual(self.submit(core, MOVE)["reason_code"], robot_link.NOT_THE_ROBOT)
        self.assertEqual(self.motion_capabilities(core)["manual_pan"]["reason_code"], robot_link.NOT_THE_ROBOT)
        self.assertEqual(self.confirm(core)["reason_code"], robot_link.NOT_THE_ROBOT)
        status = self.ask(core, "robot_status")
        self.assertEqual((status["reachable"], status["identity"]["is_robot"]), (False, False))
        cake = self.ask(core, "cake_status")
        self.assertEqual((cake["reachable"], cake["reason_code"]), (False, robot_link.NOT_THE_ROBOT))
        self.robot.write(session=SESSION_ONE)
        self.assertIsNone(core.motion_permission.confirmed(), "a non-robot answer revoked the confirmation")
        self.assertEqual(self.submit(core, command("manual_pan", {"deg": 1.0, "holding": False, "plan_only": False}, "5a1b2c3d4e5f60718293"))["reason_code"],
                         robot_link.IDENTITY_CHANGED, "the robot returning with the same session and host does not restore it")
        self.assertEqual(self.ask(core, "robot_status")["motion_permission"]["changed_component"], "identity")
        self.assertEqual(self.confirm(core)["ok"], True)

    def admit(self, core, body, *, during_block=None):
        """Drive a motion command to ADMISSION without starting anything: the shared arbitration is patched open (this
        runtime has no --enable-hardware-commands), and the spawn is stopped before any child exists."""
        def block(action=None):
            if during_block is not None:
                during_block()
            return None

        def never_spawn(run, command):
            raise supervisor_module._StoppedBeforeSpawn()

        gate_calls = []
        real_gate = core.supervisor._motion_gate

        def counting_gate():
            gate_calls.append(1)
            return real_gate()

        with mock.patch.object(core.supervisor, "block", block), mock.patch.object(core.supervisor, "_start_child", never_spawn), \
                mock.patch.object(core.supervisor, "_motion_gate", counting_gate):
            answer = self.submit(core, body)
        return answer, len(gate_calls)

    def accepted_records(self, core, command_id):
        records, _damaged = core.journal.read()
        return [record for record in records if record.get("command_id") == command_id and record.get("status") == "accepted"]

    def test_admission_re_reads_the_identity_and_journals_what_it_admitted_under(self):
        core = self.core()
        self.confirm(core)
        answer, calls = self.admit(core, MOVE)
        self.assertEqual(calls, 2, "once before anything is asked, once immediately before admission")
        self.assertNotEqual(answer["status"], "rejected")
        accepted = self.accepted_records(core, MOVE["command_id"])
        self.assertEqual(len(accepted), 1)
        admission = accepted[0]["robot_admission"]
        self.assertEqual((admission["session_uuid"], admission["build_identity"], admission["host_instance"]),
                         (SESSION_ONE, ROBOT_BUILD, HOST_INSTANCE))
        self.assertIsInstance(admission["read_wall_utc"], str)
        self.assertIsInstance(admission["confirmed_wall_utc"], str)

    def test_a_change_between_the_first_check_and_admission_is_refused_at_admission(self):
        core = self.core()
        self.confirm(core)
        body = command("manual_pan", {"deg": 2.0, "holding": False, "plan_only": False}, "6a1b2c3d4e5f60718293")
        answer, calls = self.admit(core, body, during_block=lambda: self.robot.write(session=SESSION_ONE, spawns=SPAWNS + 1))
        self.assertEqual((calls, answer["status"], answer["reason_code"]), (2, "rejected", "robot_host_changed"))
        self.assertEqual(self.accepted_records(core, body["command_id"]), [], "nothing was admitted")
        self.assertIsNone(core.supervisor.active_run())

    def test_observe_is_never_asked_about_the_robot(self):
        core = self.core()
        answer, calls = self.admit(core, OBSERVE)
        self.assertEqual(calls, 0)
        accepted = self.accepted_records(core, OBSERVE["command_id"])
        self.assertTrue(accepted and "robot_admission" not in accepted[0], answer)

    def test_a_daemon_restart_needs_a_fresh_confirmation(self):
        """Departure 2 made conservative: the robot is exactly as it was, and the new daemon still grants nothing. The
        last confirmation is on disk as an AUDIT record, which nothing reads back as permission."""
        core = self.core()
        self.confirm(core)
        self.close(core)
        again = self.core()
        self.assertIsNone(again.motion_permission.confirmed())
        self.assertEqual(self.motion_capabilities(again)["manual_grip"]["reason_code"], "robot_session_not_confirmed")
        self.assertEqual(self.submit(again, MOVE)["reason_code"], "robot_session_not_confirmed")
        audit = again.motion_permission.last_audit()
        self.assertEqual((audit["session_uuid"], audit["host_instance"], audit["grants_permission"], audit["runtime_session"]),
                         (SESSION_ONE, HOST_INSTANCE, False, core.runtime_session))
        self.assertEqual(audit["build_identity"], ROBOT_BUILD)
        self.assertEqual(self.confirm(again)["ok"], True)
        self.assertEqual(self.motion_capabilities(again)["manual_grip"]["state"], "enabled")

    def test_the_audit_record_is_private_and_confirmation_needs_a_handshake(self):
        core = self.core()
        self.confirm(core)
        self.assertEqual(os.stat(self.data / robot_link.CONFIRMATION_AUDIT_FILE).st_mode & 0o777, 0o600)
        self.assertNotIn(ROBOT_ID, (self.data / robot_link.CONFIRMATION_AUDIT_FILE).read_text(encoding="utf-8"))
        self.assertIn("robot_confirm_session", MUTATING_OPS)
        self.assertNotIn("robot_status", MUTATING_OPS)

    def test_a_replay_runtime_confirms_nothing_and_owns_no_forward(self):
        core = self.core()
        self.close(core)
        replay = self.core(mode=daemon.REPLAY)
        self.assertEqual(self.confirm(replay)["reason_code"], "replay_only")
        self.assertIsNone(replay.robot)
        self.assertEqual(self.ask(replay, "robot_status")["reason_code"], "robot_link_not_started")

    def test_a_runtime_without_a_robot_is_not_gated_and_is_labelled_this_laptop(self):
        core = self.core(config=self.robot.config(robot=False))
        self.assertIsNone(core.motion_permission)
        self.assertIsNone(core.robot)
        self.assertEqual(self.motion_capabilities(core)["manual_pan"]["state"], "enabled")
        answer = self.ask(core, "robot_status")
        self.assertEqual((answer["configured"], answer["deployment"]), (False, "this_laptop"))
        self.assertEqual(self.confirm(core)["reason_code"], "robot_not_configured")


class EveryLiveRuntimeInTheTestsRunsTheFakeController(unittest.TestCase):
    """Defect 2 of the W4 review, closed by construction: a test that builds a LIVE runtime names the fake controller
    entry, so no test can spawn the real controller even if every guard in front of a spawn regressed. Read from the
    test sources themselves, so a new test that forgets it fails here."""

    def test_every_runtime_core_built_by_a_test_names_the_fake_entry_or_is_not_live(self):
        import ast

        checked = 0
        for directory in (PACKAGE_DIR / "tests", REPO_ROOT / "apps" / "console-api" / "tests"):
            for path in sorted(directory.glob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                    if name != "RuntimeCore":
                        continue
                    checked += 1
                    keywords = {keyword.arg: keyword.value for keyword in node.keywords}
                    mode = keywords.get("mode")
                    not_live = isinstance(mode, ast.Attribute) and mode.attr in ("SYNTHETIC", "REPLAY")
                    entry = keywords.get("controller_entry")
                    names_fake = isinstance(entry, ast.Attribute) and entry.attr == "FAKE_CONTROLLER_ENTRY"
                    with self.subTest(file=path.name, line=node.lineno):
                        self.assertTrue(not_live or names_fake, "a runtime that can be live must name daemon.FAKE_CONTROLLER_ENTRY")
        self.assertGreaterEqual(checked, 4, "the scan found the builders it exists for")


class ASyntheticRuntimeNeverReachesTheRobot(unittest.TestCase):
    def setUp(self):
        self.robot = Robot(self)

    def test_the_daemon_refuses_to_build_one(self):
        with self.assertRaises(daemon.RobotConfigRefused):
            daemon.RuntimeCore(data_dir=self.robot.tmp / "data", mode=daemon.SYNTHETIC, config=self.robot.config(), source_root=REPO_ROOT,
                               robot_link=FakeLink(), robot_host_watch=FakeWatch())
        self.assertFalse((self.robot.tmp / "data").exists(), "refused before anything was created or locked")

    def test_the_command_line_says_so_and_exits(self):
        path = self.robot.tmp / "runtime-config.json"
        path.write_text(json.dumps(self.robot.mapping()), encoding="utf-8")
        errors = io.StringIO()
        with redirect_stderr(errors):
            status = daemon.main(["--synthetic", "--config", str(path), "--data-dir", str(self.robot.tmp / "cli-data")])
        self.assertEqual(status, 2)
        self.assertIn("synthetic runtime refuses", errors.getvalue())
        self.assertNotIn(str(self.robot.socket_path), errors.getvalue())

    def test_the_rehearsal_builder_refuses_it_too(self):
        coordinator, candidates, reason = mission_service.build_mission_coordinator(mode="synthetic", config=self.robot.config(),
                                                                                    data_dir=self.robot.tmp / "m")
        self.assertEqual((coordinator, candidates, reason), (None, None, mission_service.ROBOT_CONFIGURED))

    def rehearsal_against(self, build, *, fixture=LAPTOP_BUILD):
        """A rehearsal whose configuration does NOT say cake.robot, whose admin socket answers with build identity `build`
        (and the robot's target profile, as the laptop resident's does), and whose configuration names `fixture` as the
        resident it may submit to. The config-key refusal cannot see any of it: only the identity check decides."""
        self.robot.write(session=SESSION_ONE, build=build, profile=ROBOT_PROFILE)
        candidate = self.robot.tmp / "swapped.cakepln"
        candidate.write_bytes(b"a prepared candidate")
        mapping = self.robot.mapping(robot=False)
        mapping["cake"]["activation_candidates"] = {"mobile_approach.activate": str(candidate)}
        if fixture is not None:
            mapping["cake"]["rehearsal_fixture_build_identity"] = fixture
        target = self.robot.tmp / ("m-" + str(fixture)[:4] + "-" + str(build)[:4])
        target.mkdir()
        coordinator, _candidates, reason = mission_service.build_mission_coordinator(
            mode="synthetic", config=RuntimeConfig.from_mapping(mapping), data_dir=target)
        self.assertIsNone(reason, "the configuration alone does not refuse it")
        self.addCleanup(coordinator.close)
        return coordinator._capability

    def attempts(self):
        path = self.robot.state / "apply_plan_attempts"
        return path.read_text(encoding="ascii").splitlines() if path.exists() else []

    def test_a_rehearsal_never_submits_to_the_robot_s_build(self):
        capability = self.rehearsal_against(ROBOT_BUILD)
        answer = capability.submit("0f" * 16, b"a prepared candidate")
        self.assertEqual((answer["outcome"], answer["reason_code"]), ("REQUEST_FAILED", mission_service.NOT_THE_FIXTURE))
        self.assertEqual(self.attempts(), [], "no ApplyPlan reached the resident")

    def test_nor_to_one_whose_build_cannot_be_read_or_that_does_not_answer(self):
        capability = self.rehearsal_against(None)
        self.assertEqual(capability.submit("0f" * 16, b"x")["reason_code"], mission_service.BUILD_UNREADABLE)
        self.robot.listening.close()
        self.assertEqual(capability.submit("0f" * 16, b"x")["reason_code"], mission_service.BUILD_UNREADABLE, "nor to one that does not answer")
        self.assertEqual(self.attempts(), [])

    def test_nor_to_anything_when_no_fixture_is_named(self):
        capability = self.rehearsal_against(LAPTOP_BUILD, fixture=None)
        self.assertEqual(capability.submit("0f" * 16, b"x")["reason_code"], mission_service.FIXTURE_NOT_CONFIGURED)
        self.assertEqual(self.attempts(), [], "a key's absence refuses before anything is read")

    def test_only_the_named_fixture_is_submitted_to_whatever_profile_it_reports(self):
        capability = self.rehearsal_against(LAPTOP_BUILD)
        capability.submit("0f" * 16, b"x")
        self.assertEqual(len(self.attempts()), 1, "the laptop fixture reports the robot's profile and is still the fixture")


class TheMissionSaysWhatExecuted(unittest.TestCase):
    def test_the_record_and_the_view_carry_the_adapter_s_own_answer(self):
        tmp = Path(tempfile.mkdtemp(prefix="kc-rl-mission-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        execution = ExecutionAdapter(read_pose=lambda: {}, read_base=lambda: {}, move_arm=lambda *a, **k: {"ok": True},
                                     move_base_sustained=lambda *a, **k: True, confirm=lambda prompt: "y",
                                     performs_motion=False, label="recording-rehearsal")
        capability = CapabilityAdapter(available=lambda *args: True, submit=lambda *args: {}, observe=lambda *args: True,
                                       candidates=None)
        coordinator = MissionCoordinator(str(tmp / mission.MISSIONS_FILE_NAME), execution, capability, data_mode="synthetic",
                                         run_target=lambda *args: {"target_state": mission.TARGET_PLACED, "detail": None, "run_id": "run-1"})
        self.addCleanup(coordinator.close)
        started = coordinator.start([{"target_id": "cube_near", "label": "the near cube", "reachable": True, "requires_capability": False}])
        first = json.loads((tmp / mission.MISSIONS_FILE_NAME).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((first["performs_motion"], first["execution_label"]), (False, "recording-rehearsal"))
        view = coordinator.state(started["mission_id"])
        self.assertEqual((view["performs_motion"], view["execution_label"]), (False, "recording-rehearsal"))

    def test_a_record_written_before_this_is_unknown_and_never_false(self):
        record = mission.mission_started("m1", [], t_wall_utc="2026-09-23T00:00:00Z", data_mode="synthetic",
                                         performs_motion=False, execution_label="x")
        for key in ("performs_motion", "execution_label"):
            record.pop(key)
        view = mission.mission_view(mission.fold_missions([record])["m1"])
        self.assertIsNone(view["performs_motion"])


class ALiveMissionIsUnavailableUntilEveryConditionHolds(unittest.TestCase):
    """The LIVE mission builder ships OFF: mission.execution unset answers exactly what a live runtime always answered, and
    each missing condition is its own reason code. Nothing here launches anything (the same second barrier as above)."""

    def setUp(self):
        self.robot = Robot(self)
        self.data = self.robot.tmp / "data"
        self.link, self.watch = FakeLink(), FakeWatch()
        self.launches = self.robot.tmp / "controller-launches.log"
        self.addCleanup(lambda: self.assertFalse(self.launches.exists(), "a controller was launched"))
        self.candidate = self.robot.tmp / "swapped.cakepln"
        self.candidate.write_bytes(b"the prepared swapped candidate")

    def mapping(self, *, execution="proven_controller", local=True):
        raw = self.robot.mapping()
        if local:
            raw["cake"]["robot"].pop("remote_admin_socket_path")
            raw["cake"]["robot"]["link"] = "local"
        raw["host"]["address"] = "127.0.0.1"
        raw["cake"]["activation_candidates"] = {"mobile_approach.activate": str(self.candidate)}
        raw["mission"] = {"execution": execution, "capability_observation_endpoint": "ipc:///run/example/approach-observations.ipc",
                          "capability_command_endpoint": "ipc:///run/example/approach-commands.ipc"}
        return raw

    def core(self, raw, *, hardware_enabled=True):
        core = daemon.RuntimeCore(data_dir=self.data, mode=daemon.LIVE, config=RuntimeConfig.from_mapping(raw), source_root=REPO_ROOT,
                                  hardware_enabled=hardware_enabled, robot_link=self.link, robot_host_watch=self.watch,
                                  readiness=self.watch, controller_entry=daemon.FAKE_CONTROLLER_ENTRY,
                                  extra_env={"KC_FAKE_LAUNCH_LOG": str(self.launches)},
                                  source_supervisor=lambda hub: sources.SourceSupervisor(hub))
        self.addCleanup(core.close)
        return core

    @staticmethod
    def ask(core, op, **request):
        core.cake.invalidate()
        return core.handle(op, {"op": op, **request}, None)

    def test_execution_unset_answers_exactly_what_a_live_runtime_always_answered(self):
        for raw in (self.mapping(execution=None), {key: value for key, value in self.mapping().items() if key != "mission"}):
            with self.subTest(mission=raw.get("mission")):
                core = self.core(raw)
                self.assertEqual(self.ask(core, "mission_state"),
                                 {"ok": False, "reason_code": "mission_unavailable", "detail": mission_service.NOT_SYNTHETIC})
                self.assertEqual(self.ask(core, "mission_start")["reason_code"], "mission_unavailable")
                core.close()

    def test_each_missing_condition_is_named(self):
        cases = ((self.mapping(), False, mission_service.HARDWARE_COMMANDS_OFF),
                 (self.mapping(local=False), True, mission_service.ROBOT_LINK_NOT_LOCAL))
        for raw, hardware_enabled, reason in cases:
            with self.subTest(reason=reason):
                core = self.core(raw, hardware_enabled=hardware_enabled)
                self.assertEqual(self.ask(core, "mission_state"),
                                 {"ok": False, "reason_code": "mission_unavailable", "detail": reason})
                core.close()

    def test_everything_present_builds_it_and_a_mission_start_is_admitted_like_a_motion_command(self):
        core = self.core(self.mapping())
        self.assertIsInstance(core.robot, FakeLink)
        self.assertTrue(core.mission.performs_motion)
        self.assertEqual(self.ask(core, "mission_state"), {"ok": False, "reason_code": "no_mission_recorded"})
        refused = self.ask(core, "mission_start")
        self.assertEqual((refused["ok"], refused["reason_code"]), (False, robot_link.NOT_CONFIRMED),
                         "no confirmed robot session: nothing is written down and nothing starts")
        self.assertEqual(self.ask(core, "mission_state"), {"ok": False, "reason_code": "no_mission_recorded"})

    def test_the_stop_sets_the_missions_cancel_event_before_anything_else(self):
        core = self.core(self.mapping())
        order = []
        for owner, name in ((core.mission, "cancel_now"), (core.supervisor, "stop"), (core.mission, "stop")):
            real = getattr(owner, name)

            def recording(*args, _real=real, _label=f"{type(owner).__name__}.{name}", **kwargs):
                order.append(_label)
                return _real(*args, **kwargs)

            setattr(owner, name, recording)
        core._stop_run_and_mission(daemon.STOP_BY_STOP_OP)
        self.assertEqual(order, ["MissionCoordinator.cancel_now", "Supervisor.stop", "MissionCoordinator.stop"])


if __name__ == "__main__":
    unittest.main()
