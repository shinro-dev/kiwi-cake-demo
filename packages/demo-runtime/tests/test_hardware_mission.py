#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_hardware_mission.py: the LIVE mission builder, which ships OFF, and the robot-local
# adaptations it rests on (the local admin link, the local motor-host runner, the local frame fetch). Nothing here starts a
# controller, opens a device, runs ssh or touches a resident: every runner, socket and probe is a fake or a temp file.
# Run directly: python3 packages/demo-runtime/tests/test_hardware_mission.py
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import cake_activation, cake_admin, config as config_module, daemon, forwards  # noqa: E402
from demo_runtime import mission, mission_execution, mission_service, preflight, robot_link  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402
from demo_runtime.config import ConfigError, RuntimeConfig, config_digest  # noqa: E402

SLOT = "0123456789abcdef0123456789abcdef"
CAPSULE = "ab" * 32
ROBOT_BUILD = "cd" * 32
OBSERVATIONS = "ipc:///run/example/approach-observations.ipc"
COMMANDS = "ipc:///run/example/approach-commands.ipc"


def mapping(tmp: str, **changes) -> dict:
    """Every condition of the LIVE builder present. `changes` replace or (with None) remove a dotted key."""
    candidate = os.path.join(tmp, "swapped.cakepln")
    if not os.path.exists(candidate):
        with open(candidate, "wb") as handle:
            handle.write(b"the prepared swapped candidate")
    raw = {"data_root": os.path.join(tmp, "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad",
           "observation_root": "observations", "host": {"address": "127.0.0.1", "robot_id": "example_robot"},
           "pi_ssh_target": "", "cameras": {"wrist_id": "usb-test-wrist", "front_id": None},
           "cake": {"admin_probe_path": "/opt/example/admin-probe",
                    "robot": {"link": "local", "local_admin_socket_path": "/run/example/cake-admin.sock",
                              "expected_build_identity": ROBOT_BUILD},
                    "activation_candidates": {"mobile_approach.activate": candidate},
                    "capability": {"name": "mobile_approach", "slot_uuid": SLOT, "active_capsule": CAPSULE}},
           "mission": {"execution": "proven_controller", "capability_observation_endpoint": OBSERVATIONS,
                       "capability_command_endpoint": COMMANDS}}
    for dotted, value in changes.items():
        *parents, leaf = dotted.split(".")
        node = raw
        for name in parents:
            node = node[name]
        if value is None:
            node.pop(leaf, None)
        else:
            node[leaf] = value
    return raw


class TheConfiguration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kc-hw-config-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))

    def test_the_mission_section_ships_unset_and_changes_no_digest_when_absent(self):
        without = RuntimeConfig.from_mapping(mapping(self.tmp, mission=None))
        self.assertIsNone(config_module.mission_of(without))
        self.assertNotIn("mission", without.to_private_dict())
        unset = RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.execution": None}))
        self.assertIsNone(config_module.mission_of(unset).execution, "absent execution is unset, never a default")
        self.assertNotEqual(config_digest(without), config_digest(unset))

    def test_the_retry_ships_off_and_only_a_true_switches_it_on(self):
        base = RuntimeConfig.from_mapping(mapping(self.tmp))
        self.assertIs(config_module.mission_of(base).retry, False)
        off = RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.retry": False}))
        self.assertIs(config_module.mission_of(off).retry, False)
        self.assertEqual(config_digest(base), config_digest(off), "a Retry left off keeps the digest")
        on = RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.retry": True}))
        self.assertIs(config_module.mission_of(on).retry, True)
        self.assertNotEqual(config_digest(base), config_digest(on))
        for bad in ("true", 1, 0, [True]):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.retry": bad}))

    def test_the_attended_label_needs_an_envelope_word_or_the_retry(self):
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(mapping(self.tmp, placement_envelope_attended="retry-check-1"))
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(mapping(self.tmp, placement_envelope_attended="retry-check-1", **{"mission.retry": False}))
        labelled = RuntimeConfig.from_mapping(mapping(self.tmp, placement_envelope_attended="retry-check-1", **{"mission.retry": True}))
        self.assertEqual(labelled.placement_envelope_attended, "retry-check-1")
        self.assertEqual(labelled.placement_envelope, ())

    def test_only_the_one_execution_is_accepted(self):
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.execution": "hybrid"}))
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.unknown": 1}))

    def test_an_endpoint_is_an_absolute_ipc_path_and_the_two_differ(self):
        for bad in ("tcp://127.0.0.1:5555", "ipc://relative.ipc", "ipc:///run/../etc/x", "ipc:///run//x"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.capability_command_endpoint": bad}))
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(mapping(self.tmp, **{"mission.capability_command_endpoint": OBSERVATIONS}))

    def test_a_local_link_has_no_remote_path_and_the_default_link_keeps_its_digest(self):
        local = RuntimeConfig.from_mapping(mapping(self.tmp))
        self.assertTrue(config_module.robot_link_is_local(local))
        self.assertEqual(local.cake.admin_socket_path, "/run/example/cake-admin.sock")
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(mapping(self.tmp, **{"cake.robot.remote_admin_socket_path": "/run/remote.sock"}))
        with self.assertRaises(ConfigError):
            RuntimeConfig.from_mapping(mapping(self.tmp, **{"cake.robot.link": "tcp"}))
        forwarded = mapping(self.tmp, **{"cake.robot.link": None, "cake.robot.remote_admin_socket_path": "/run/remote.sock"})
        explicit = mapping(self.tmp, **{"cake.robot.link": "ssh_forward", "cake.robot.remote_admin_socket_path": "/run/remote.sock"})
        self.assertEqual(config_digest(RuntimeConfig.from_mapping(forwarded)), config_digest(RuntimeConfig.from_mapping(explicit)))
        self.assertNotIn("link", RuntimeConfig.from_mapping(forwarded).to_private_dict()["cake"]["robot"])


class TheLiveBuilderShipsOff(unittest.TestCase):
    """Every missing condition is a reason code and no coordinator; everything present builds the proven path only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kc-hw-builder-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)

    def build(self, *, mode="live", hardware_enabled=True, supervisor=object(), **changes):
        return mission_service.build_hardware_mission_coordinator(
            mode=mode, config=RuntimeConfig.from_mapping(mapping(self.tmp, **changes)), data_dir=self.data,
            supervisor=supervisor, hardware_enabled=hardware_enabled)

    def test_the_live_mode_constant_is_the_daemons(self):
        self.assertEqual(mission_service.LIVE_MODE, daemon.LIVE)

    def test_every_missing_condition_answers_mission_unavailable(self):
        cases = [
            ({"mode": "synthetic"}, mission_service.HARDWARE_NOT_LIVE),
            ({"mission": None}, mission_service.EXECUTION_NOT_CONFIGURED),
            ({"mission.execution": None}, mission_service.EXECUTION_NOT_CONFIGURED),
            ({"hardware_enabled": False}, mission_service.HARDWARE_COMMANDS_OFF),
            ({"cake.robot": None, "cake.admin_socket_path": "/run/example/cake-admin.sock"}, mission_service.ROBOT_NOT_CONFIGURED),
            ({"cake.robot.link": None, "cake.robot.remote_admin_socket_path": "/run/remote.sock"}, mission_service.ROBOT_LINK_NOT_LOCAL),
            ({"supervisor": None}, mission_service.NO_SUPERVISOR),
            ({"host.address": "203.0.113.10"}, mission_service.CONTROLLER_HOST_NOT_LOOPBACK),
            ({"cake.activation_candidates": None}, mission_service.NO_CANDIDATE),
            ({"cake.capability": None}, mission_service.NO_CAPABILITY),
            ({"cake.capability.slot_uuid": "0123456789abcdef"}, mission_service.CAPABILITY_NOT_BINDABLE),
            ({"mission.capability_command_endpoint": None}, mission_service.NO_CAPABILITY_ENDPOINTS),
        ]
        for changes, reason in cases:
            with self.subTest(reason=reason, changes=changes):
                options = {name: changes.pop(name) for name in ("mode", "hardware_enabled", "supervisor") if name in changes}
                coordinator, candidates, answered = self.build(**options, **changes)
                self.assertEqual((coordinator, candidates, answered), (None, None, reason))
                self.assertEqual(mission_service.unavailable(answered),
                                 {"ok": False, "reason_code": "mission_unavailable", "detail": reason})
        self.assertEqual(os.listdir(self.data), [], "a runtime that cannot build a mission leaves no mission file")

    def test_everything_present_builds_the_proven_path_and_nothing_else(self):
        coordinator, candidates, reason = self.build()
        self.addCleanup(coordinator.close)
        self.assertIsNone(reason)
        self.assertTrue(coordinator.performs_motion)
        self.assertIsInstance(coordinator._run_target, mission_execution.ProvenTargetRunner)
        self.assertIs(coordinator._run_target.recorder, coordinator)
        self.assertEqual(coordinator._execution.label, mission_execution.LABEL)
        self.assertEqual(coordinator._run_target.capability_endpoints,
                         {"admin_probe": "/opt/example/admin-probe", "admin_socket": "/run/example/cake-admin.sock",
                          "slot_uuid": SLOT, "capsule": CAPSULE, "observation_endpoint": OBSERVATIONS, "command_endpoint": COMMANDS})
        self.assertEqual(candidates.keys(), ["mobile_approach.activate"])
        # No qualified radii in this configuration: they reach the execution as None, which fails every target before any
        # run (mission_execution, test_mission_execution.py). Nothing here fills them in.
        self.assertIsNone(coordinator._run_target.hardware.radii)
        self.assertIsInstance(coordinator._run_target.evidence, mission_execution.EvidenceJournal)
        self.assertEqual(os.path.join(self.data, mission.MISSION_EVIDENCE_FILE_NAME), coordinator._run_target.evidence.path)
        self.assertNotIn(mission.MISSION_EVIDENCE_FILE_NAME, os.listdir(self.data), "the evidence file is created by its first record")

    def test_the_configured_radii_reach_the_execution_as_they_are(self):
        radii = {"r_claim_mm": 12.5, "r_match_mm": 20.0, "basis": "example qualification record QR-1"}
        coordinator, _candidates, reason = self.build(**{"mission.radii": radii})
        self.addCleanup(coordinator.close)
        self.assertIsNone(reason)
        self.assertEqual(radii, coordinator._run_target.hardware.radii)

    def test_the_word_cube_half_edge_reaches_the_execution_resolved_with_its_basis(self):
        """The LIVE builder hands the execution the RESOLVED radii (target_binding.radii_from_cube_geometry: half a cube
        edge for both, with its basis), so the mission target file the controller reads carries numbers and a basis and
        never the word."""
        coordinator, _candidates, reason = self.build(**{"mission.radii": config_module.RADII_CUBE_HALF_EDGE})
        self.addCleanup(coordinator.close)
        self.assertIsNone(reason)
        binding = mission_execution.target_binding_module()
        derived = binding.radii_from_cube_geometry()
        self.assertEqual(derived.to_document(), coordinator._run_target.hardware.radii)
        self.assertEqual((12.7, 12.7), (derived.r_claim_mm, derived.r_match_mm))
        self.assertEqual(binding.CUBE_HALF_EDGE_BASIS, coordinator._run_target.hardware.radii["basis"])
        # What the file parser will read: the resolved document passes the mission target file's own checks.
        self.assertEqual(derived, binding._radii(coordinator._run_target.hardware.radii))

    def test_the_rehearsal_builder_refuses_a_hardware_run_target(self):
        rehearsal = mapping(self.tmp, mission=None)
        rehearsal["cake"].pop("robot")
        rehearsal["cake"]["admin_socket_path"] = "/run/example/cake-admin.sock"
        config = RuntimeConfig.from_mapping(rehearsal)
        runner = mission_execution.ProvenTargetRunner(hardware=mission_execution.HardwareExecution(), supervisor=object(),
                                                      capability_endpoints=None)
        for run_target in (runner, mission_execution.run_proven_target):
            for mode in ("synthetic", "live"):
                with self.subTest(run_target=type(run_target).__name__, mode=mode):
                    self.assertEqual(mission_service.build_mission_coordinator(mode=mode, config=config, data_dir=self.data,
                                                                               run_target=run_target),
                                     (None, None, mission_service.HARDWARE_RUN_TARGET_REFUSED))
        built, _candidates, reason = mission_service.build_mission_coordinator(mode="synthetic", config=config, data_dir=self.data)
        self.addCleanup(built.close)
        self.assertIsNone(reason, "without a hardware run_target the rehearsal is built exactly as before")
        self.assertFalse(built.performs_motion)


class TheRobotIdentityBeforeEveryApplyPlan(unittest.TestCase):
    def adapter(self):
        return mission_service.capability_adapter(probe_path="/opt/example/admin-probe", socket_path="/run/example/a.sock",
                                                  slot_uuid=SLOT, active_capsule=CAPSULE, candidates=None,
                                                  robot_build_identity=ROBOT_BUILD)

    def submit_with(self, answered_build):
        status = {"ok": True, "fields": {"build_identity": answered_build}}
        with mock.patch.object(cake_admin, "run_probe", return_value=status) as probe, \
                mock.patch.object(cake_activation, "submit_apply_plan", return_value={"outcome": cake_activation.ACCEPTED}) as submit:
            answer = self.adapter().submit("op-1", b"bytes")
        return answer, probe, submit

    def test_a_resident_that_is_not_the_robot_gets_nothing_submitted(self):
        for answered, reason in (("ef" * 32, mission_service.NOT_THE_ROBOT), (None, mission_service.BUILD_UNREADABLE)):
            with self.subTest(reason=reason):
                answer, probe, submit = self.submit_with(answered)
                self.assertEqual((answer["outcome"], answer["reason_code"]), (cake_activation.REQUEST_FAILED, reason))
                submit.assert_not_called()
                self.assertEqual(probe.call_args[0][2], "get-status")

    def test_the_robot_resident_proven_by_what_answers_is_submitted_to(self):
        answer, _probe, submit = self.submit_with(ROBOT_BUILD)
        self.assertEqual(answer["outcome"], cake_activation.ACCEPTED)
        submit.assert_called_once()

    def test_the_fixture_rule_is_unchanged(self):
        with mock.patch.object(cake_admin, "run_probe") as probe:
            refused = mission_service.refuse_unless_fixture("/p", "/s", fixture_build_identity=None)
        self.assertEqual(refused["reason_code"], mission_service.FIXTURE_NOT_CONFIGURED)
        probe.assert_not_called()


class TheLocalAdminLink(unittest.TestCase):
    def config(self, tmp):
        return RuntimeConfig.from_mapping(mapping(tmp))

    def test_connected_means_something_listens_on_the_residents_socket(self):
        tmp = tempfile.mkdtemp(prefix="kc-hw-link-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        raw = mapping(tmp, **{"cake.robot.local_admin_socket_path": os.path.join(tmp, "a.sock")})
        link = robot_link.LocalRobotLink(RuntimeConfig.from_mapping(raw))
        self.assertFalse(link.start().connected())
        self.assertEqual((link.report()["state"], link.report()["reason"]), (forwards.FAILED, "local_resident_socket_not_listening"))
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(os.path.join(tmp, "a.sock"))
        listener.listen(1)
        self.assertTrue(link.connected())
        report = link.report()
        self.assertEqual((report["state"], report["link"], report["child"]), (forwards.CONNECTED, "local", False))
        self.assertNotIn(tmp, repr(report), "the report names no path")
        link.stop()
        self.assertEqual(link.report()["state"], forwards.STOPPED)

    def test_it_asks_the_injected_probe_with_the_configured_socket_only(self):
        tmp = tempfile.mkdtemp(prefix="kc-hw-link-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        asked = []
        link = robot_link.LocalRobotLink(self.config(tmp), accepts=lambda path: asked.append(path) or True)
        self.assertTrue(link.connected())
        self.assertEqual(asked, ["/run/example/cake-admin.sock"])


class TheLocalMotorHostRunner(unittest.TestCase):
    def test_the_same_script_runs_here_with_the_same_input_and_bound(self):
        calls = []

        def fake(argv, **options):
            calls.append((argv, options))
            return subprocess.CompletedProcess(argv, 0, stdout="pid 4242\n", stderr="")

        state, pids = preflight.probe_motor_host_detail_local(runner=fake, timeout_s=3.0)
        self.assertEqual((state, pids), ("running_normal", (4242,)))
        (argv, options), = calls
        self.assertEqual(argv, ["bash", "-s"])
        self.assertEqual(options["input"], preflight._MOTOR_HOST_SCRIPT)
        self.assertEqual((options["timeout"], options["capture_output"], options["text"]), (3.0, True, True))
        self.assertEqual(preflight.probe_motor_host_local(runner=lambda argv, **o: subprocess.CompletedProcess(argv, 3, "", "")),
                         "not_running")

    def test_it_runs_nothing_but_the_probes_own_command(self):
        with self.assertRaises(ValueError):
            preflight.local_runner(["ssh", "target", "rm -rf /"], runner=lambda *a, **k: None)
        with self.assertRaises(ValueError):
            preflight.local_runner(["bash", "-s"], runner=lambda *a, **k: None)

    def test_a_runner_that_fails_is_unreachable_not_an_answer(self):
        def fails(argv, **options):
            raise subprocess.TimeoutExpired(argv, 1.0)

        self.assertEqual(preflight.probe_motor_host_detail_local(runner=fails), ("unreachable", ()))

    def test_the_check_asks_a_local_probe_even_without_an_ssh_target(self):
        tmp = tempfile.mkdtemp(prefix="kc-hw-pf-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        config = RuntimeConfig.from_mapping(mapping(tmp))
        asked = []

        def local(target):
            asked.append(target)
            return "not_running"

        local.asks_locally = True
        check = preflight._check_motor_host(config, local)
        self.assertEqual((check.ok, check.reason_code), (False, "motor_host_not_running"))
        self.assertEqual(asked, [""])
        ssh_only = preflight._check_motor_host(config, lambda target: asked.append("ssh") or "not_running")
        self.assertTrue(ssh_only.ok, "an ssh probe with no usable target still asks nothing, as before")
        self.assertEqual(asked, [""])


class TheLocalFrameFetch(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO_ROOT / "bin" / "laptop" / "perception"))
        try:
            import run_stage1_scan  # noqa: F401  (needs OpenCV and numpy, like the controller)
        except ImportError as missing:
            self.skipTest(f"the capture adapter's dependencies are not installed here ({type(missing).__name__})")
        self.scan = sys.modules["run_stage1_scan"]
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-hw-fetch-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.store = self.tmp / "store"
        (self.store / "frames").mkdir(parents=True)
        (self.store / "frames" / "a.png").write_bytes(b"frame bytes")

    def test_a_frame_is_copied_from_the_store(self):
        destination = self.tmp / "out.png"
        self.scan.local_fetch(self.store)("frames/a.png", destination)
        self.assertEqual(destination.read_bytes(), b"frame bytes")

    def test_the_relative_path_check_is_kept_and_a_link_out_of_the_store_is_refused(self):
        fetch = self.scan.local_fetch(self.store)
        (self.tmp / "secret").write_bytes(b"not a frame")
        os.symlink(self.tmp / "secret", self.store / "frames" / "link.png")
        for relative in ("/etc/hostname", "../secret", "frames/../../secret", "frames/link.png"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                fetch(relative, self.tmp / "never.png")
        self.assertFalse((self.tmp / "never.png").exists())

    def test_the_switch_is_the_one_the_supervisor_sets(self):
        self.assertEqual(self.scan.CAMERA_SERVICE_LOCAL_ENV, sv.CAMERA_SERVICE_LOCAL_ENV)


class TheChildOnTheRobotAsksTheCameraServiceLocally(unittest.TestCase):
    """The supervisor tells a controller it runs beside the camera service only when the robot link is local."""

    def environment(self, raw, inherited):
        from demo_runtime.journal import EventLog, Journal

        tmp = tempfile.mkdtemp(prefix="kc-hw-env-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        supervisor = sv.Supervisor(data_dir=tmp, journal=Journal(os.path.join(tmp, "journal.jsonl")),
                                   event_log=EventLog(os.path.join(tmp, "events.jsonl")), runtime_session="rt-test-env",
                                   mode=sv.LIVE, source_root=REPO_ROOT, controller_entry="unused.py",
                                   config=RuntimeConfig.from_mapping(raw), extra_env=inherited)
        run = sv._Run(run_id="console-env", command_id="env-0001", command_type="start_cycle", run_dir=Path(tmp) / "history" / "x")

        class Bundle:
            root, digest = Path(tmp), "0" * 64

        return supervisor._child_environment(run, Bundle())

    def test_a_local_link_sets_the_switch_and_the_services_own_port(self):
        tmp = tempfile.mkdtemp(prefix="kc-hw-env-map-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        env = self.environment(mapping(tmp), {})
        self.assertEqual((env[sv.CAMERA_SERVICE_LOCAL_ENV], env["KC_MISSION_LOCAL_PORT"]), ("1", "5570"))
        self.assertNotIn(sv.MISSION_TARGET_ENV, env, "no mission target for a run that is not a mission's")

    def test_a_forwarded_link_never_passes_the_switch_on_even_when_inherited(self):
        tmp = tempfile.mkdtemp(prefix="kc-hw-env-map-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        raw = mapping(tmp, **{"cake.robot.link": None, "cake.robot.remote_admin_socket_path": "/run/remote.sock"})
        env = self.environment(raw, {sv.CAMERA_SERVICE_LOCAL_ENV: "1", sv.MISSION_TARGET_ENV: "/tmp/stale.json"})
        self.assertNotIn(sv.CAMERA_SERVICE_LOCAL_ENV, env)
        self.assertNotIn(sv.MISSION_TARGET_ENV, env)


if __name__ == "__main__":
    unittest.main()
