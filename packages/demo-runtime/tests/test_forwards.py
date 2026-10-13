#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_forwards.py: the loopback forwards the runtime owns.
#
# NO NETWORK AND NO ROBOT. Every child here is a STUB ssh written into a temporary directory and put at the front of PATH.
# The stub records the argv it was given, binds the LOCAL side of its -L the way a real forward does (so a port that is
# taken really is taken, and a port that was released really is free again), and then waits. It never leaves this machine.
# What is proved: the argv is one loopback tunnel and nothing else; a forward is starting until it has stayed up; a death
# is noticed with the status it died on and is NEVER retried; a stop ends the child and releases the port; not one report,
# repr or reason carries the ssh target, a port or an endpoint; the whole thing is off until the configuration enables the
# tap; and no path in the module constructs a robot client or sends anything.
# Strings the repository's text gates refuse are BUILT AT RUN TIME, never written out.
# Run directly: python3 packages/demo-runtime/tests/test_forwards.py
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import forwards, sources, tap_client  # noqa: E402
from demo_runtime.config import RuntimeConfig  # noqa: E402

WAIT_S = 20.0
# Reserved documentation values only (RFC 5737): nothing here names a machine that exists.
TEST_ADDRESS = ".".join(("192", "0", "2", "11"))
TEST_USER = "operator" + "name"
TEST_TARGET = TEST_USER + "@" + TEST_ADDRESS
TEST_ROBOT_ID = "unit" + "-0042"
TEST_WRIST_ID = "usb-" + "Vendor_Cam_" + "SN" + "1234567" + "-video-index0"

STUB_SSH = '''#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""A stand-in for ssh. It records its argv, binds the local side of -L and waits. It opens nothing beyond this machine."""
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
        local = int(value.split(":")[1])
if local is None:  # a -R forward: its listener would be on the robot computer, so nothing is bound on this one
    while True:
        time.sleep(0.05)
holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    holder.bind(("127.0.0.1", local))
    holder.listen(1)
except OSError:
    sys.exit(255)  # what ssh does under ExitOnForwardFailure when the local side cannot be opened
while True:
    time.sleep(0.05)
'''


class Clock:
    """A monotonic clock a test moves by hand, so 'it stayed up long enough' is a decision and not a measurement."""

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


def port_is_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


class WithStubSsh(unittest.TestCase):
    """One temporary directory with a stub `ssh` in it, at the front of PATH for the length of the test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-forwards-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        stub = self.tmp / "ssh"
        stub.write_text(STUB_SSH, encoding="ascii")
        stub.chmod(0o700)
        self.log = self.tmp / "ssh-argv.jsonl"
        self.clock = Clock()
        self.started = []
        self.addCleanup(self._stop_everything)
        patched = mock.patch.dict(os.environ, {"PATH": f"{self.tmp}{os.pathsep}{os.environ.get('PATH', '')}", "KC_STUB_SSH_LOG": str(self.log)})
        patched.start()
        self.addCleanup(patched.stop)

    def _stop_everything(self):
        for item in self.started:
            try:
                item.stop()
            except Exception:  # noqa: BLE001 -- a cleanup must never hide the failure that brought it here
                pass

    def forward(self, purpose=forwards.CONTROL, **options):
        made = forwards.Forward(purpose, ssh_target=TEST_TARGET, remote_port=forwards.HOST_CONTROL_PORT,
                                clock_ns=self.clock, settle_s=0.5, stop_grace_s=5.0, **options)
        self.started.append(made)
        return made

    def argv_lines(self):
        return [json.loads(line) for line in self.log.read_text(encoding="ascii").splitlines()] if self.log.exists() else []

    def up(self, forward):
        """Start a forward and wait until its child has really bound the local port."""
        self.assertTrue(forward.start(), "the stub ssh was not started")
        wait_for(self, lambda: forward.local_port and not port_is_free(forward.local_port), "the stub to bind the local port")
        return forward


class TheTunnelItAsksFor(WithStubSsh):
    def test_one_loopback_tunnel_the_target_last_and_no_remote_command(self):
        forward = self.up(self.forward())
        argv = wait_for(self, lambda: self.argv_lines() or None, "the stub to record its argv")[0]
        self.assertEqual(argv[-1], TEST_TARGET, "the target is the last word, as ssh takes it")
        self.assertIn("-N", argv, "a forward runs NO remote command: no shell, no script, nothing on the robot")
        self.assertEqual(argv.count("-L"), 1, "exactly one tunnel per child")
        tunnel = argv[argv.index("-L") + 1]
        self.assertEqual(tunnel, f"127.0.0.1:{forward.local_port}:127.0.0.1:{forwards.HOST_CONTROL_PORT}",
                         "both ends are loopback: this laptop's, and the robot computer's own")
        self.assertIn("ExitOnForwardFailure=yes", argv, "an ssh that could not open the tunnel must EXIT, not sit there")
        self.assertIn("BatchMode=yes", argv, "a daemon has no terminal to be asked for a passphrase on")
        self.assertEqual(forward.endpoint(), f"tcp://127.0.0.1:{forward.local_port}")

    def test_the_chosen_port_is_on_the_loopback_interface_and_the_subscribers_would_find_it(self):
        forward = self.up(self.forward())
        self.assertTrue(0 < forward.local_port < 65536)
        self.assertEqual(tap_client.check_endpoint(forward.endpoint()), forward.endpoint(),
                         "the endpoint a forward hands out is one the subscribers accept: loopback, never another machine")

    def test_a_fixed_local_port_is_used_as_it_is_because_the_subscriber_connects_to_that_one(self):
        wanted = forwards.free_loopback_port()
        forward = self.up(self.forward(forwards.TAP, local_port=wanted, local_port_fixed=True))
        self.assertEqual(forward.local_port, wanted)
        self.assertEqual(forward.endpoint(), f"tcp://127.0.0.1:{wanted}")


class StatesAndTheirReasons(WithStubSsh):
    def test_a_forward_is_starting_until_it_has_stayed_up_and_running_only_afterwards(self):
        forward = self.up(self.forward())
        self.assertEqual((forward.check(), forward.reason), (forwards.STARTING, "waiting_for_the_forward"),
                         "a child that was just created has not proved anything yet")
        self.clock.advance(0.49)
        self.assertEqual(forward.check(), forwards.STARTING)
        self.clock.advance(0.02)
        self.assertEqual((forward.check(), forward.reason), (forwards.RUNNING, None))
        self.assertEqual((forward.starts, forward.deaths), (1, 0))

    def test_a_death_is_noticed_with_its_status_reported_once_and_never_retried(self):
        with mock.patch.dict(os.environ, {"KC_STUB_SSH_EXIT": "42"}):
            forward = self.forward()
            self.started.append(forward)
            self.assertTrue(forward.start())
            wait_for(self, lambda: forward.check() == forwards.DEAD, "the forward to notice the death")
        self.assertEqual((forward.state, forward.reason, forward.last_exit_status), (forwards.DEAD, "ssh_exited_42", 42))
        self.assertIsNone(forward.endpoint(), "a dead forward hands out no endpoint: nothing is listening on that port")
        for _ in range(5):
            self.assertEqual(forward.check(), forwards.DEAD, "it stays dead")
        self.assertEqual((forward.starts, forward.deaths), (1, 1), "checking a dead forward NEVER starts another one")
        self.assertEqual(len(self.argv_lines()), 1, "exactly one ssh was ever run: there is no retry loop here")

    def test_a_local_port_that_is_already_taken_kills_the_forward_instead_of_pretending(self):
        taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        forward = self.forward(forwards.TAP, local_port=port, local_port_fixed=True)
        self.started.append(forward)
        self.assertTrue(forward.start())
        wait_for(self, lambda: forward.check() == forwards.DEAD, "the forward to die on a port it could not bind")
        self.assertEqual(forward.reason, "ssh_exited_255", "ssh's own 'the session failed', which is what the operator has to see")

    def test_an_ssh_that_is_not_there_and_a_spawn_that_fails_each_carry_their_own_reason(self):
        missing = self.forward(ssh_binary="kc-no-such-ssh-binary")
        self.assertFalse(missing.start())
        self.assertEqual((missing.state, missing.reason), (forwards.FAILED, "ssh_not_found"))

        def explode(*_args, **_kwargs):
            raise OSError(8, "Exec format error", "the argv that would carry the target")

        forward = self.forward()
        with mock.patch.object(forwards.subprocess, "Popen", explode):
            self.assertFalse(forward.start())
        self.assertEqual((forward.state, forward.reason), (forwards.FAILED, "spawn_failed"))
        self.assertNotIn("target", json.dumps(forward.report()), "the message of the failure is dropped: it carries the argv")
        self.assertEqual(self.argv_lines(), [])

    def test_nothing_is_configured_without_a_target_a_remote_port_or_the_local_port_a_subscriber_needs(self):
        for made, reason in ((forwards.Forward(forwards.CONTROL), "ssh_target_not_configured"),
                             (forwards.Forward(forwards.CONTROL, ssh_target=TEST_TARGET, remote_port=0), "remote_port_not_configured"),
                             (forwards.Forward(forwards.TAP, ssh_target=TEST_TARGET, remote_port=5558, local_port=None, local_port_fixed=True),
                              "local_port_not_configured")):
            self.assertFalse(made.configured)
            self.assertEqual((made.state, made.reason), (forwards.NOT_CONFIGURED, reason))
            self.assertFalse(made.start(), "an unconfigured forward starts nothing at all")
            self.assertIsNone(made.endpoint())
        self.assertEqual(self.argv_lines(), [])


    def test_the_raw_host_command_port_is_never_forwarded(self):
        """The LeKiwi host binds its command socket on the robot's loopback only; a forward to it from here would be the
        bypass the binding closes. Refused as a state: nothing is spawned, nothing listens, for the default port and for a
        configured one."""
        for made in (forwards.Forward(forwards.CONTROL, ssh_target=TEST_TARGET, remote_port=forwards.RAW_HOST_COMMAND_PORT),
                     forwards.Forward(forwards.TAP, ssh_target=TEST_TARGET, remote_port=5555, local_port=40555, local_port_fixed=True),
                     forwards.Forward(forwards.CONTROL, ssh_target=TEST_TARGET, remote_port=6555, refused_remote_ports=(6555,))):
            self.assertFalse(made.configured)
            self.assertEqual((made.state, made.reason), (forwards.NOT_CONFIGURED, forwards.REFUSED_RAW_COMMAND_PORT))
            self.assertFalse(made.start())
            self.assertIsNone(made.endpoint())
        self.assertEqual(self.argv_lines(), [], "no ssh was run for a raw command port")
        allowed = forwards.Forward(forwards.CONTROL, ssh_target=TEST_TARGET, remote_port=forwards.HOST_CONTROL_PORT, refused_remote_ports=(6555,))
        self.assertTrue(allowed.configured, "the refusal names the command port only")


class TheReverseForwardOfTheDesktopPerception(WithStubSsh):
    """The one forward in the other direction: the robot's loopback reaches THIS computer's perception service. Its robot
    port may never be one of the robot's own service ports, because a listener there would stand in for that service."""

    def reverse(self, robot_port=5590, service_port=None, **options):
        made = forwards.ReverseForward(ssh_target=TEST_TARGET, robot_port=robot_port, service_port=service_port or forwards.free_loopback_port(),
                                       clock_ns=self.clock, settle_s=0.5, stop_grace_s=5.0, **options)
        self.started.append(made)
        return made

    def test_one_reverse_tunnel_from_the_robots_loopback_to_this_computers_and_no_remote_command(self):
        forward = self.reverse(robot_port=5590, service_port=47123)
        self.assertTrue(forward.start())
        argv = wait_for(self, lambda: self.argv_lines() or None, "the stub to record its argv")[0]
        self.assertEqual(argv[-1], TEST_TARGET)
        self.assertIn("-N", argv)
        self.assertNotIn("-L", argv, "this forward does not open a port on this computer")
        self.assertEqual(argv.count("-R"), 1, "exactly one tunnel")
        self.assertEqual(argv[argv.index("-R") + 1], "127.0.0.1:5590:127.0.0.1:47123", "the robot's loopback to this computer's loopback, both named")
        self.assertIn("ExitOnForwardFailure=yes", argv, "an sshd that cannot open the robot-side listener must end the forward")
        self.assertIsNone(forward.endpoint(), "nothing on this computer connects through a reverse forward")
        self.clock.advance(1.0)
        self.assertEqual(forward.check(), forwards.RUNNING)
        forward.stop()
        self.assertEqual(forward.state, forwards.STOPPED)

    def test_every_robot_service_port_is_refused_as_a_state_and_no_ssh_runs(self):
        for port in forwards.ROBOT_SERVICE_PORTS:
            with self.subTest(port=port):
                made = self.reverse(robot_port=port)
                self.assertFalse(made.configured)
                self.assertEqual((made.state, made.reason), (forwards.NOT_CONFIGURED, forwards.REFUSED_ROBOT_SERVICE_PORT))
                self.assertFalse(made.start())
        configured = self.reverse(robot_port=6556, refused_robot_ports=(6556,))  # a configured host port that differs from the default
        self.assertEqual(configured.reason, forwards.REFUSED_ROBOT_SERVICE_PORT)
        self.assertFalse(configured.start())
        self.assertEqual(self.argv_lines(), [], "no ssh was run for a refused robot port")

    def test_the_refused_ports_are_the_robots_own_services(self):
        from demo_runtime import config as config_module

        self.assertEqual(set(forwards.ROBOT_SERVICE_PORTS), {5555, 5556, forwards.HOST_CONTROL_PORT, forwards.HOST_TAP_PORT, 5570,
                                                             *forwards.CAMERA_SERVICE_PREVIEW_PORTS.values()})
        self.assertEqual(tuple(forwards.ROBOT_SERVICE_PORTS), tuple(config_module.ROBOT_FIXED_SERVICE_PORTS),
                         "the configuration refuses the same robot ports this module refuses")
        self.assertEqual(forwards.CAMERA_SERVICE_MISSION_PORT, config_module.CAMERA_SERVICE_MISSION_PORT)
        self.assertEqual(forwards.HOST_OBSERVATION_PORT, config_module.DEFAULT_OBS_PORT)

    def test_it_needs_a_target_and_both_ports(self):
        for made, reason in ((forwards.ReverseForward(ssh_target="", robot_port=5590, service_port=47000), "ssh_target_not_configured"),
                             (forwards.ReverseForward(ssh_target=TEST_TARGET, robot_port=0, service_port=47000), "remote_port_not_configured"),
                             (forwards.ReverseForward(ssh_target=TEST_TARGET, robot_port=5590, service_port=0), "local_port_not_configured")):
            self.assertEqual((made.state, made.reason), (forwards.NOT_CONFIGURED, reason))
            self.assertFalse(made.start())

    def test_nothing_private_in_its_report_or_repr(self):
        forward = self.reverse(robot_port=5590, service_port=47124)
        forward.start()
        text = json.dumps(forward.report()) + repr(forward) + str(forward.reason)
        for secret in (TEST_ADDRESS, TEST_USER, "5590", "47124"):
            self.assertNotIn(secret, text)


class StoppingCleanly(WithStubSsh):
    def test_a_stop_ends_the_child_releases_the_port_and_leaves_no_endpoint(self):
        forward = self.up(self.forward())
        port = forward.local_port
        self.assertFalse(port_is_free(port), "the child holds the local port while the forward is up")
        forward.stop()
        self.assertEqual((forward.state, forward.reason), (forwards.STOPPED, "stopped"))
        self.assertIsNone(forward.local_port)
        self.assertIsNone(forward.endpoint())
        wait_for(self, lambda: port_is_free(port), "the local port to be free again")
        self.assertIsNotNone(forward.last_exit_status, "the child was waited for, not left behind")

    def test_a_stop_is_safe_twice_and_on_a_forward_that_never_started(self):
        forward = self.up(self.forward())
        forward.stop()
        forward.stop()
        self.assertEqual(forward.state, forwards.STOPPED)
        never = self.forward()
        never.stop()
        self.assertEqual(never.state, forwards.STOPPED)
        self.assertEqual(len(self.argv_lines()), 1)

    def test_starting_twice_does_not_make_a_second_child(self):
        forward = self.up(self.forward())
        self.assertTrue(forward.start())
        self.assertEqual((forward.starts, len(self.argv_lines())), (1, 1))


class NothingPrivateLeaves(WithStubSsh):
    def test_no_report_reason_or_repr_carries_the_target_a_port_or_an_endpoint(self):
        forward = self.up(self.forward())
        self.clock.advance(1.0)
        forward.check()
        report, shown = json.dumps(forward.report()), repr(forward)
        for private in (TEST_TARGET, TEST_USER, TEST_ADDRESS, str(forward.local_port), str(forwards.HOST_CONTROL_PORT), "tcp://"):
            self.assertNotIn(private, report, "a report names no target, no port and no endpoint")
            self.assertNotIn(private, shown, "a repr can end up in a traceback: it names none of them either")
        self.assertEqual(set(forward.report()), {"purpose", "configured", "state", "reason", "starts", "deaths", "forced_stops", "exit_status", "child"})


class FromTheConfiguration(WithStubSsh):
    """What the runtime configuration asks for, and what it deliberately does not."""

    def config(self, tap=None, root=None, preview=None):
        root = root or self.tmp
        document = {"data_root": str(root / "demo-data"), "saved_pose_file": "calibration/scan-home-current.json",
                    "pad_template_dir": "calibration/pad-template", "observation_root": "observations",
                    "host": {"address": TEST_ADDRESS, "robot_id": TEST_ROBOT_ID}, "pi_ssh_target": TEST_TARGET,
                    "cameras": {"wrist_id": TEST_WRIST_ID, "front_id": None},
                    "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None}}
        if tap is not None:
            document["tap"] = tap
        if preview is not None:
            document["preview"] = preview
        return RuntimeConfig.from_mapping(document)

    def test_a_configuration_with_neither_a_tap_nor_a_preview_builds_and_starts_nothing(self):
        for tap in (None, {"enabled": False, "port": 5558}):
            built = forwards.Forwards.from_config(self.config(tap))
            self.started.append(built)
            self.assertFalse(built.configured, "with neither switch on, the runtime forwards nothing")
            self.assertEqual({purpose: state for purpose, state in built.start().items()}, {purpose: False for purpose in forwards.PURPOSES})
            self.assertEqual([one["state"] for one in built.report().values()], [forwards.NOT_CONFIGURED] * len(forwards.PURPOSES))
            self.assertIsNone(built.endpoint(forwards.CONTROL))
        self.assertEqual(self.argv_lines(), [], "not one ssh was run")

    def test_a_preview_forwards_on_its_own_with_no_tap_anywhere(self):
        """The baseline NORMAL host runs cameras and no boundary. Gating the preview on the tap made that impossible."""
        built = forwards.Forwards.from_config(self.config(preview={"enabled": True, "wrist_port": 5601, "front_port": 5602}))
        self.started.append(built)
        self.assertTrue(built.configured)
        self.assertFalse(built.get(forwards.TAP).configured, "no tap was asked for, so no tap tunnel exists")
        for role, local in (("wrist", 5601), ("front", 5602)):
            forward = built.get(forwards.preview_purpose(role))
            self.assertTrue(forward.configured)
            self.assertEqual(forward._wanted_local_port, local)
            self.assertEqual(forward._remote_port, forwards.CAMERA_SERVICE_PREVIEW_PORTS[role])
        self.assertEqual(self.argv_lines(), [], "reading a configuration starts nothing")

    def test_a_tap_and_two_previews_forward_four_purposes_onto_the_ports_their_subscribers_connect_to(self):
        tap_port, wrist_port, front_port = (forwards.free_loopback_port() for _ in range(3))
        config = self.config({"enabled": True, "port": tap_port},
                             preview={"enabled": True, "wrist_port": wrist_port, "front_port": front_port})
        built = forwards.Forwards.from_config(config, clock_ns=self.clock, settle_s=0.5, stop_grace_s=5.0)
        self.started.append(built)
        self.assertEqual(built.start(), {purpose: True for purpose in forwards.PURPOSES})
        wait_for(self, lambda: len(self.argv_lines()) == len(forwards.PURPOSES), "every stub to record its argv")
        tunnels = {line[line.index("-L") + 1] for line in self.argv_lines()}
        control_port = forwards.endpoint_port(built.endpoint(forwards.CONTROL))
        self.assertEqual(tunnels, {f"127.0.0.1:{control_port}:127.0.0.1:{forwards.HOST_CONTROL_PORT}",
                                   f"127.0.0.1:{tap_port}:127.0.0.1:{forwards.HOST_TAP_PORT}",
                                   f"127.0.0.1:{wrist_port}:127.0.0.1:{forwards.CAMERA_SERVICE_PREVIEW_PORTS['wrist']}",
                                   f"127.0.0.1:{front_port}:127.0.0.1:{forwards.CAMERA_SERVICE_PREVIEW_PORTS['front']}"})
        self.assertEqual(built.endpoint(forwards.TAP), sources.tap_endpoint(config),
                         "the tap forward lands exactly where demo_runtime.sources will subscribe")
        for role in ("wrist", "front"):
            self.assertEqual(built.endpoint(forwards.preview_purpose(role)), sources.preview_endpoint(config, role))
        self.assertNotEqual(built.endpoint(forwards.preview_purpose("wrist")), built.endpoint(forwards.preview_purpose("front")),
                            "two cameras must never be given one tunnel")
        self.assertNotIn(control_port, (tap_port, wrist_port, front_port), "the control forward gets a port of its own")
        self.clock.advance(1.0)
        self.assertEqual(set(built.check().values()), {forwards.RUNNING})
        self.assertEqual(built.dead(), [])
        built.stop()
        self.assertEqual([one["state"] for one in built.report().values()], [forwards.STOPPED] * len(forwards.PURPOSES))

    def test_the_local_ports_are_the_ones_the_sources_module_connects_to(self):
        config = self.config({"enabled": True, "port": 5558}, preview={"enabled": True, "wrist_port": 5601, "front_port": 5602})
        built = forwards.Forwards.from_config(config)
        self.started.append(built)
        self.assertEqual(forwards.endpoint_port(sources.tap_endpoint(config)), 5558)
        self.assertEqual(forwards.endpoint_port(sources.preview_endpoint(config, "wrist")), 5601)
        self.assertEqual(forwards.endpoint_port(sources.preview_endpoint(config, "front")), 5602)
        self.assertTrue(all(one["configured"] for one in built.report().values()))
        self.assertEqual(self.argv_lines(), [], "reading a configuration starts nothing")

    def test_the_remote_ports_are_the_ones_the_boundary_and_the_camera_service_really_bind(self):
        """Mirrored constants, pinned against their sources AS TEXT: no import, no zmq, no camera."""
        boundary = (REPO_ROOT / "bin" / "pi" / "host_boundary.py").read_text(encoding="utf-8")
        self.assertIn(f"DEFAULT_CONTROL_PORT, DEFAULT_TAP_PORT = {forwards.HOST_CONTROL_PORT}, {forwards.HOST_TAP_PORT}", boundary)
        self.assertIn('LOOPBACK = "127.0.0.1"', boundary, "the boundary binds the loopback address, which is why a forward exists at all")
        service = (REPO_ROOT / "bin" / "pi" / "camera_service.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_MISSION_PORT = 5570", service)
        declared = re.search(r"DEFAULT_PREVIEW_PORTS = (\{[^}]*\})", service).group(1)
        self.assertEqual(json.loads(declared.replace("'", '"')), forwards.CAMERA_SERVICE_PREVIEW_PORTS)
        self.assertEqual(forwards.CAMERA_SERVICE_PREVIEW_PORTS, sources.CAMERA_SERVICE_PREVIEW_PORTS,
                         "the two mirrors of the service's ports must not drift apart")

    def test_each_role_is_forwarded_to_that_role_s_own_remote_socket(self):
        config = self.config({"enabled": True, "port": 5558}, preview={"enabled": True, "wrist_port": 5601, "front_port": 5602})
        built = forwards.Forwards.from_config(config)
        self.started.append(built)
        self.assertEqual(built.get(forwards.preview_purpose("front"))._remote_port, forwards.CAMERA_SERVICE_PREVIEW_PORTS["front"])
        self.assertEqual(built.get(forwards.preview_purpose("wrist"))._remote_port, forwards.CAMERA_SERVICE_PREVIEW_PORTS["wrist"])


class NothingIsEverCommanded(WithStubSsh):
    """Opening a forward contacts the robot computer. It must still be unable to touch a motor, a camera or the torque."""

    def test_no_forward_path_constructs_a_robot_client_or_a_lease_client(self):
        from demo_runtime import fresh_client, lease_client

        def explode(*_args, **_kwargs):
            raise AssertionError("the forwards module tried to reach the robot")

        forward = self.forward()
        with mock.patch.object(fresh_client, "fresh_client", explode), mock.patch.object(lease_client, "LeaseClient", explode):
            self.up(forward)
            self.clock.advance(1.0)
            self.assertEqual(forward.check(), forwards.RUNNING)
            forward.report()
            forward.endpoint()
            forward.stop()

    def test_the_module_holds_no_motor_vocabulary_and_no_remote_command(self):
        text = (PACKAGE_DIR / "demo_runtime" / "forwards.py").read_text(encoding="utf-8")
        for word in ("send_action", "set_torque", "disable_torque", "enable_torque", "open_gripper", "close_gripper", "goal_pos", "torque_enable"):
            self.assertNotIn(word, text, "the module that opens the tunnels must have no motor path at all")
        self.assertIn('"-N"', text, "a forward always runs ssh with -N: no remote command, ever")

    def test_the_argv_never_grows_a_remote_command(self):
        self.up(self.forward())
        argv = wait_for(self, lambda: self.argv_lines() or None, "the stub to record its argv")[0]
        after_target = argv[argv.index(TEST_TARGET) + 1:]
        self.assertEqual(after_target, [], "nothing follows the target: ssh would run it on the robot computer")
        self.assertTrue(all(word.startswith("-") or "=" in word or ":" in word or word == TEST_TARGET or word.endswith("ssh")
                            for word in argv), "every word is a flag, an option, the tunnel or the target")


if __name__ == "__main__":
    unittest.main()
