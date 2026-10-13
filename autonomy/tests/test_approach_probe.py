#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_approach_probe.py: bin/pi/approach_probe.py, the robot-side non-motion probe of the capability.
#   - it imports no lerobot and never lerobot_transport, checked by importing it in a fresh interpreter and by its AST,
#     under both contracts (the v2 mode imports the executor, whose closure loads no lerobot either);
#   - its module scope imports only what bin/pi/lekiwi-deploy.sh's APP_FILES deploys (the set is parsed from the script,
#     and the probe is imported from a scratch tree holding only those files): the deployed v1 mode must keep importing
#     while the executor and the observation source are the v2 path's, from the caller's --executor-dir;
#   - the gateway half, driven with a command of the shape the capability publishes;
#   - the v2 derivation: a stored perception record becomes the executor's own remaining advance, in exactly the
#     schema's fields, or a refusal by name; and the v2 judge;
#   - END TO END on this machine: the REAL capability process and the probe itself over real Unix-domain ZeroMQ, with a
#     stand-in admin-probe that reports the slot ACTIVE and get-health arg1 = the pid of that process. Two launches:
#     the RENDERED run-capability.sh the way Cake starts a child (env PATH=/usr/bin:/bin, four positionals), which is
#     contract v2 since the brief's change, and the service launched directly on --contract v1 (the deployed wrapper
#     no longer speaks v1, so the v1 fixture mode is proven against a v1 service started the same exec way). The
#     end-to-end cases skip, per method, when pyzmq is not importable here. Nothing here touches a device.
# Run directly: python3 autonomy/tests/test_approach_probe.py
import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROBE = os.path.join(ROOT, "bin", "pi", "approach_probe.py")
SERVICE = os.path.join(ROOT, "autonomy", "motion", "mobile_approach_service.py")
DEPLOY = os.path.join(ROOT, "bin", "pi", "lekiwi-deploy.sh")
sys.path.insert(0, os.path.join(ROOT, "bin", "pi"))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "motion"))

import approach_probe  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import target_observation_source as source  # noqa: E402
from clock import FakeClock  # noqa: E402
from mobile_approach import DesiredBaseMotion  # noqa: E402
from mobile_approach_service import V2_FIELDS  # noqa: E402

SLOT = "5050543040508050000000000000cafe"
MOBILE = "b" * 64
OBSERVATION_ID = "20260926T120000Z-wrist-1"
CONTACT = [553.0, 545.0]
ANCHOR = [0.0145, -0.1825]  # 35 mm outside the pad's near edge: a BASE_ADVANCE start, inside the recorded 37-45 mm band's neighbourhood
try:
    import zmq  # noqa: F401
    HAVE_ZMQ = True
except ImportError:
    HAVE_ZMQ = False

ADMIN_PROBE = """#!/bin/sh
# stands in for admin-probe: request SOCKET OP
pid="$(cat "$KC_TEST_PID_FILE")"
case "$3" in
  list-slots) printf 'status ok\\nslot capsule=%s config=%s generation=2 instance=7 name=mobile_approach state=ACTIVE uuid=%s\\n' "$KC_TEST_CAPSULE" "$KC_TEST_CAPSULE" "$KC_TEST_SLOT" ;;
  get-health) printf 'status ok\\nhealth arg0=6 arg1=%s code=0 flags=1 sequence=2 slot=%s state=1 state_name=HEALTHY surface=reported\\n' "${KC_TEST_HEALTH_PID:-$pid}" "$KC_TEST_SLOT" ;;
  *) echo "status refused"; exit 1 ;;
esac
"""


def perception_record(anchor=ANCHOR, contact=CONTACT, *, pad_frame_valid=True, scale=0.0004) -> dict:
    """A record of the shape perceive() writes, on the synthetic low-pose PAD_FRAME the executor's tests use: the contact
    pixel sees `anchor`, image-up is pad +y (the reach direction). Only the fields the probe reads are meaningful."""
    import numpy as np

    image_to_pad = np.array([[scale, 0.0, anchor[0] - contact[0] * scale], [0.0, -scale, anchor[1] + contact[1] * scale], [0.0, 0.0, 1.0]])
    return {"camera": "wrist", "image_size_px": [1280, 720],
            "cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419], "contact_px": list(contact)},
            "cube_by_template": {"cube_center_pad_m": list(anchor), "inside_pad_outline": False},
            "pad_by_template": {"pad_identity": True, "pad_frame_valid": pad_frame_valid, "homography_image_to_pad": image_to_pad.tolist(),
                                "homography_pad_to_image": np.linalg.inv(image_to_pad).tolist(), "landmark_inliers": 41, "reprojection_rms_px": 1.3}}


def write_record(directory: str, record: dict, observation_id: str = OBSERVATION_ID) -> str:
    perception = os.path.join(directory, "perception")
    os.makedirs(perception, exist_ok=True)
    path = os.path.join(perception, f"{observation_id}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    return path


def app_files() -> list:
    """The files bin/pi/lekiwi-deploy.sh installs on the robot computer, read from its APP_FILES block: the whole of what
    the deployed probe can import beside the standard library."""
    with open(DEPLOY, encoding="utf-8") as handle:
        block = re.search(r"^APP_FILES=\(\n(.*?)^\)", handle.read(), re.S | re.M)
    assert block is not None, "bin/pi/lekiwi-deploy.sh has no APP_FILES=( ... ) block"
    return [line.strip() for line in block.group(1).splitlines() if line.strip() and not line.strip().startswith("#")]


def top_level_imports(path: str) -> set:
    """The module names a file imports at module scope (not inside a function): what importing it costs."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
    return names | {node.module for node in tree.body if isinstance(node, ast.ImportFrom) if node.module}


class NoLerobot(unittest.TestCase):
    def test_importing_the_probe_loads_no_lerobot_and_no_real_transport(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import approach_probe; "
                "bad = sorted(m for m in sys.modules if m == 'lerobot' or m.startswith('lerobot.') or m == 'lerobot_transport'); "
                "print(repr(bad)); sys.exit(1 if bad else 0)")
        done = subprocess.run([sys.executable, "-c", code, os.path.dirname(PROBE)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), "[]")

    def test_loading_the_executor_for_v2_loads_no_lerobot_and_no_real_transport(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import approach_probe; approach_probe.load_executor(approach_probe.EXECUTOR_DIR); "
                "bad = sorted(m for m in sys.modules if m == 'lerobot' or m.startswith('lerobot.') or m == 'lerobot_transport'); "
                "print(repr(bad)); sys.exit(1 if bad else 0)")
        done = subprocess.run([sys.executable, "-c", code, os.path.dirname(PROBE)], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), "[]")

    def test_its_source_imports_no_lerobot_and_constructs_no_lekiwi_transport(self):
        with open(PROBE, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertFalse({m for m in imported if m.split(".")[0] in ("lerobot", "lerobot_transport")}, imported)
        called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertFalse({name for name in called if "LeKiwi" in name}, called)
        self.assertIn("FakeBaseMotionTransport", called)
        self.assertEqual(approach_probe.RECORDING_DESTINATION, "transport.FakeBaseMotionTransport")

    def test_the_executor_is_imported_only_inside_the_v2_path(self):
        # The module-level imports stay what they were: numpy and cv2 are the v2 mode's cost, not the fixture mode's.
        top = top_level_imports(PROBE)
        self.assertFalse(top & {"run_single_cube_cycle", "target_observation_source", "numpy", "cv2"}, top)

    def test_its_module_scope_imports_only_what_the_deploy_script_installs(self):
        # Every module-level import that names a file of this checkout must be one lekiwi-deploy.sh deploys (APP_FILES),
        # or the deployed v1 probe stops importing at the next install: the guard the first v2 round lacked.
        deployed = {os.path.basename(f)[:-3] for f in app_files() if f.endswith(".py")}
        self.assertIn("approach_probe", deployed)
        local = {name for name in top_level_imports(PROBE)
                 if any(os.path.isfile(os.path.join(ROOT, *parts, f"{name}.py")) for parts in (("autonomy", "motion"), ("bin", "pi")))}
        self.assertEqual(local, {"cake_capability_adapter", "clock", "mobile_approach", "stable_motion_gateway", "transport"})
        self.assertFalse(local - deployed, f"imported at module scope but not deployed: {sorted(local - deployed)}")

    def test_the_probe_imports_from_a_tree_holding_only_the_app_files(self):
        # The deployed layout itself: the APP_FILES copied into a scratch tree, nothing else, and the probe imported from
        # its bin/pi there (it locates autonomy/motion from its own location, as on the robot).
        with tempfile.TemporaryDirectory() as tmp:
            for relative in app_files():
                destination = os.path.join(tmp, relative)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copyfile(os.path.join(ROOT, relative), destination)
            code = ("import sys; sys.path.insert(0, sys.argv[1]); import approach_probe; "
                    "print(approach_probe.CONTRACT_V2, approach_probe.RECORDING_DESTINATION)")
            done = subprocess.run([sys.executable, "-c", code, os.path.join(tmp, "bin", "pi")], capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(done.stdout.strip(), "v2 transport.FakeBaseMotionTransport")


class GatewayHalf(unittest.TestCase):
    def command(self, now):
        return DesiredBaseMotion(command_id="mobile_approach.pid4242.cmd_1", target_id="cube_far", timestamp_ns=now,
                                 vx_mps=0.10, vy_mps=0.06, omega_rps=0.0, expiry_ns=now + 250_000_000)

    def test_disabled_forwards_one_zero_and_enabled_applies_the_slew_bounded_vector(self):
        clock = FakeClock(1_000_000_000)
        gateways = approach_probe.through_gateways(self.command(clock.now_ns()), clock)
        self.assertEqual(gateways["disabled"]["outcome"], "ZERO_AUTONOMOUS_DISABLED")
        self.assertEqual(gateways["disabled"]["recorded_calls"], [["zero"]])
        self.assertEqual(gateways["enabled"]["outcome"], "APPLIED")
        self.assertEqual(gateways["enabled"]["applied"], [0.05, 0.05, 0.0])
        self.assertEqual(gateways["enabled"]["recorded_calls"], [["velocity", 0.05, 0.05, 0.0]])
        self.assertEqual(approach_probe.judge(gateways), [])

    def test_an_expired_command_is_judged_a_failure(self):
        clock = FakeClock(1_000_000_000)
        command = self.command(clock.now_ns())
        clock.advance(300_000_000)
        failures = approach_probe.judge(approach_probe.through_gateways(command, clock))
        self.assertTrue(any("not APPLIED" in f for f in failures), failures)


class RecordDerivation(unittest.TestCase):
    """The v2 observation is the executor's own quantity, computed with the executor's own functions from a stored record."""

    def setUp(self):
        self.executor = approach_probe.load_executor(approach_probe.EXECUTOR_DIR)
        self.assertIs(self.executor, cycle)

    def derive(self, record, **overrides):
        arguments = {"observation_id": OBSERVATION_ID, "target_id": "cube_b", "capture_ns": 1_000, "now_ns": 5_000, "perception_deadline_s": 60.0, **overrides}
        return approach_probe.observation_from_record(self.executor, record, **arguments)

    def test_the_observation_is_the_executors_remaining_advance_in_exactly_the_schemas_fields(self):
        observed, approach = self.derive(perception_record())
        axis = cycle.reach_direction_pad(perception_record()["pad_by_template"]["homography_image_to_pad"], CONTACT)
        need = cycle.base_advance_needed_mm(ANCHOR, axis)
        self.assertEqual(approach["axis_pad"], [0.0, 1.0])
        self.assertEqual(approach["need_mm"], need)
        self.assertEqual(approach, cycle.Cycle._approach_state({"perception": [{"observation_id": OBSERVATION_ID, **perception_record()}]}, ANCHOR, axis))
        self.assertGreater(need, cycle.BASE_ADVANCE_TOLERANCE_MM)
        self.assertLessEqual(need, cycle.BASE_MAX_TOTAL_ADVANCE_MM)
        wire = observed.wire_v2(published_ns=7_000)
        self.assertEqual(tuple(wire), V2_FIELDS)
        self.assertEqual(wire, {"contract": "kc.target_observation/2", "target_id": "cube_b", "capture_ns": 1_000, "published_ns": 7_000, "motion_epoch": 0,
                                "frame": "anchor_reach", "x_m": need / 1000.0, "y_m": 0.0, "max_age_ns": source.max_age_ns_for(60.0), "stationary": True,
                                "observation_id": OBSERVATION_ID})
        self.assertEqual(observed.evidence()["landmark_inliers"], 41)
        self.assertEqual(observed.evidence()["reprojection_rms_px"], 1.3)
        self.assertEqual(observed.identity()["frame_sha256"], None, "a perceive() record names no frame; nothing is invented")

    def test_the_bound_follows_the_perception_deadline_the_caller_gave(self):
        observed, _ = self.derive(perception_record(), perception_deadline_s=30.0)
        self.assertEqual(observed.max_age_ns, source.max_age_ns_for(30.0))
        self.assertEqual(observed.max_age_ns, round((30.0 + 2 * cycle.CAPABILITY_ADMIN_TIMEOUT_S + cycle.CAPABILITY_LINK_TIMEOUT_S) * 1e9))

    def test_a_record_without_a_valid_pad_frame_refuses_by_name(self):
        with self.assertRaises(approach_probe.Refused) as refused:
            self.derive(perception_record(pad_frame_valid=False))
        self.assertEqual(refused.exception.reasons, ("pad_frame_invalid",))

    def test_a_record_without_a_contact_or_a_pad_position_refuses_naming_what_is_missing(self):
        record = perception_record()
        record["cube"].pop("contact_px")
        record["cube_by_template"] = None
        with self.assertRaises(approach_probe.Refused) as refused:
            self.derive(record)
        self.assertEqual(refused.exception.reasons, ("cube.contact_px", "cube_by_template.cube_center_pad_m"))

    def test_a_cube_beyond_the_bounded_advance_is_refused_with_the_sources_reason(self):
        with self.assertRaises(approach_probe.Refused) as refused:
            self.derive(perception_record(anchor=[0.0145, -0.1475 - 0.200]))  # 200 mm outside: no bounded advance covers it
        self.assertEqual(refused.exception.reasons, ("advance_out_of_bounds",))

    def test_a_stale_capture_is_refused_with_the_sources_reason(self):
        with self.assertRaises(approach_probe.Refused) as refused:
            self.derive(perception_record(), capture_ns=0, now_ns=source.max_age_ns_for(60.0) + 1)
        self.assertEqual(refused.exception.reasons, ("capture_older_than_budget",))

    def test_the_observation_id_is_the_record_file_name_as_the_executor_names_it(self):
        self.assertEqual(approach_probe.record_observation_id(os.path.join("session", "perception", f"{OBSERVATION_ID}.json")), OBSERVATION_ID)
        self.assertEqual(approach_probe.record_observation_id("plain-name"), "plain-name")

    def test_the_source_is_the_one_the_executor_packages_with_in_a_run(self):
        # Loaded through the executor (its checkout's autonomy/motion), so in this checkout it IS the module the tests import.
        self.assertIs(approach_probe.load_source(self.executor), source)
        self.assertIs(approach_probe.load_source(self.executor), self.executor._motion_module("target_observation_source"))

    def test_a_checkout_without_the_v2_source_is_refused_by_name(self):
        class V1Source:
            """The former source's surface: no CONTRACT, no max_age_ns_for, a resolve that always raised."""

            def resolve(self, *args, **kwargs):
                raise AssertionError("never reached")

        class OldCheckout:
            @staticmethod
            def _motion_module(name):
                assert name == "target_observation_source"
                return V1Source()

        class NoSourceCheckout:
            @staticmethod
            def _motion_module(name):
                raise ModuleNotFoundError(f"No module named {name!r}")

        with self.assertRaises(approach_probe.Refused) as refused:
            approach_probe.load_source(OldCheckout)
        self.assertIn("not the contract v2 source", str(refused.exception))
        self.assertIn("CONTRACT, ObservationUnavailable, max_age_ns_for", str(refused.exception))
        with self.assertRaises(approach_probe.Refused) as refused:
            approach_probe.load_source(NoSourceCheckout)
        self.assertIn("--executor-dir", str(refused.exception))
        with self.assertRaises(approach_probe.Refused):
            approach_probe.observation_from_record(OldCheckout, perception_record(), observation_id=OBSERVATION_ID, target_id="cube_b",
                                                   capture_ns=1_000, now_ns=5_000, perception_deadline_s=60.0)

    def test_evidence_paths_write_the_home_directory_as_a_tilde(self):
        kept = os.environ.get("HOME")
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            try:
                self.assertEqual(approach_probe.home_relative(os.path.join(home, "session", "perception", "x.json")), "~/session/perception/x.json")
                self.assertEqual(approach_probe.home_relative(os.path.join(home, "session", "..", "y.json")), "~/y.json")
                self.assertEqual(approach_probe.home_relative(home), "~")
                self.assertEqual(approach_probe.home_relative(home + "-other/z.json"), home + "-other/z.json", "a sibling that merely shares the prefix is not home")
                self.assertEqual(approach_probe.home_relative("/usr/lib/z.py"), "/usr/lib/z.py")
            finally:
                if kept is None:
                    del os.environ["HOME"]
                else:
                    os.environ["HOME"] = kept


class JudgeV2(unittest.TestCase):
    OBSERVATION = {"target_id": "cube_b", "published_ns": 10_000_000_000}

    def command(self, **changes):
        fields = {"command_id": "mobile_approach.pid4242.cmd_1", "target_id": "cube_b", "timestamp_ns": 10_000_000_000, "vx_mps": cycle.BASE_VX_MPS,
                  "vy_mps": 0.0, "omega_rps": 0.0, "expiry_ns": 10_000_000_000 + 250_000_000, **changes}
        return DesiredBaseMotion(**fields)

    def test_the_answer_to_this_publication_at_the_pulse_speed_passes(self):
        self.assertEqual(approach_probe.judge_v2(self.command(), self.OBSERVATION, cycle.BASE_VX_MPS), [])

    def test_every_departure_is_named(self):
        for changes, words in ((dict(timestamp_ns=9_999_999_999), "published_ns"), (dict(expiry_ns=10_200_000_000), "expiry_ns"),
                               (dict(vx_mps=0.04), "pulse speed"), (dict(vy_mps=0.01), "straight ahead"), (dict(omega_rps=0.1), "straight ahead"),
                               (dict(target_id="cube_a"), "target")):
            failures = approach_probe.judge_v2(self.command(**changes), self.OBSERVATION, cycle.BASE_VX_MPS)
            self.assertEqual(len(failures), 1, (changes, failures))
            self.assertIn(words, failures[0])

    def test_an_enabled_gateway_that_applied_less_than_the_pulse_is_named(self):
        gateways = {"enabled": {"outcome": "APPLIED", "applied": [0.04, 0.0, 0.0]}}
        failures = approach_probe.judge_v2(self.command(), self.OBSERVATION, cycle.BASE_VX_MPS, gateways)
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("pulse", failures[0])


@unittest.skipUnless(shutil.which("bash") and os.path.isdir("/usr/bin"), "bash is not installed")
class EndToEnd(unittest.TestCase):
    """The shared harness: a real capability process, a stand-in admin-probe bound to its pid, and the probe as a process.
    `launch` chooses how the capability starts (see the subclasses)."""

    launch = None

    def setUp(self):
        if not HAVE_ZMQ:
            self.skipTest("pyzmq is not importable by this interpreter")
        if self.launch is None:
            self.skipTest("the shared harness runs through its subclasses")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.run_dir = os.path.join(t, "run")
        os.mkdir(self.run_dir)
        os.mkdir(os.path.join(t, "child-cwd"))
        self.admin_probe = os.path.join(t, "admin-probe")
        with open(self.admin_probe, "w", encoding="utf-8") as handle:
            handle.write(ADMIN_PROBE)
        os.chmod(self.admin_probe, 0o755)
        self.pid_file = os.path.join(t, "capability.pid")
        self.service = self.start_wrapper() if self.launch == "wrapper" else self.start_v1_service()
        self.addCleanup(self.stop_service)
        with open(self.pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(self.service.pid))
        commands = os.path.join(self.run_dir, "approach-commands.ipc")
        deadline = time.monotonic() + 20
        while not os.path.exists(commands):
            if self.service.poll() is not None or time.monotonic() > deadline:
                self.fail("the capability process did not bind its command endpoint: " + (self.service.stdout.read() or ""))
            time.sleep(0.05)

    def start_wrapper(self):
        """The RENDERED run-capability.sh, started the way Cake starts a child: contract v2 since the brief's change."""
        t = self.tmp.name
        with open(os.path.join(ROOT, "bin", "templates", "run-capability.sh.in"), encoding="utf-8") as handle:
            text = handle.read()
        for name, value in (("PYTHON", sys.executable), ("APP_DIR", ROOT), ("RUN_DIR", self.run_dir)):
            text = text.replace(f"@@{name}@@", value)
        wrapper = os.path.join(t, "run-capability.sh")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(wrapper, 0o755)
        return subprocess.Popen([wrapper, "3", "0", "900", "kc-test-run-tag"], env={"PATH": "/usr/bin:/bin"},
                                cwd=os.path.join(t, "child-cwd"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def start_v1_service(self):
        """The service on --contract v1, exec'd from a shell the same way the wrapper does it, so $$ is the service's pid
        and its commands are labelled with it: what the deployed v1 capability was before the wrapper became v2."""
        script = 'exec "$0" -u "$1" --observations "ipc://$2/approach-observations.ipc" --commands "ipc://$2/approach-commands.ipc" --contract v1 --identity "mobile_approach.pid$$"'
        return subprocess.Popen(["bash", "-c", script, sys.executable, SERVICE, self.run_dir], cwd=os.path.join(self.tmp.name, "child-cwd"),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def stop_service(self):
        if self.service.poll() is None:
            self.service.send_signal(signal.SIGINT)
            try:
                self.service.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.service.kill()
                self.service.wait()
        self.service.stdout.close()

    def probe(self, *extra, **env):
        args = [sys.executable, PROBE, "--admin-probe", self.admin_probe, "--admin-socket", os.path.join(self.tmp.name, "a.sock"),
                "--slot-uuid", SLOT, "--capsule", MOBILE,
                "--observations", "ipc://" + os.path.join(self.run_dir, "approach-observations.ipc"),
                "--commands", "ipc://" + os.path.join(self.run_dir, "approach-commands.ipc"), *extra]
        full_env = {**os.environ, "KC_TEST_PID_FILE": self.pid_file, "KC_TEST_SLOT": SLOT, "KC_TEST_CAPSULE": MOBILE, **env}
        done = subprocess.run(args, env=full_env, capture_output=True, text=True, timeout=120)
        return done.returncode, json.loads(done.stdout.strip().splitlines()[-1]), done.stderr

    def probe_v2(self, record=None, *extra, **env):
        path = write_record(self.tmp.name, perception_record() if record is None else record)
        return self.probe("--contract", "v2", "--record", path, "--timeout-s", "3", *extra, **env)


class V1FixtureAgainstAV1Capability(EndToEnd):
    """The v1 fixture mode, kept as it was, proven against a v1 capability (launched directly: the deployed wrapper is v2)."""

    launch = "v1"

    def test_the_command_of_the_launched_process_passes_both_gateways(self):
        status, record, err = self.probe()
        self.assertEqual(status, 0, json.dumps(record, indent=1) + err)
        self.assertEqual(record["verdict"], "PASS")
        self.assertEqual(record["producer_pid"], self.service.pid, "the probe did not bind the command to the process Cake launched")
        self.assertTrue(record["command"]["command_id"].startswith(f"mobile_approach.pid{self.service.pid}."))
        self.assertEqual((record["command"]["vx_mps"], record["command"]["vy_mps"]), (0.1, 0.06))
        self.assertEqual(record["gateways"]["disabled"]["outcome"], "ZERO_AUTONOMOUS_DISABLED")
        self.assertEqual(record["gateways"]["disabled"]["recorded_calls"], [["zero"]])
        self.assertEqual(record["gateways"]["enabled"]["outcome"], "APPLIED")
        self.assertEqual(record["gateways"]["enabled"]["applied"], [0.05, 0.05, 0.0])
        self.assertEqual(record["recording_destination"], "transport.FakeBaseMotionTransport")
        self.assertIs(record["physical_execution"], False)
        self.assertEqual(record["lerobot_modules_loaded"], [])
        self.assertEqual((record["slot_generation"], record["slot_capsule"]), (2, MOBILE))
        self.assertEqual(record["fixture"]["target_id"], "cube_far")
        self.assertIn("submitted_ms_after_fixture", record)
        # The v1 record is what it always was: none of the v2 keys appear in it.
        self.assertFalse({"contract", "contract_answered", "observation", "observation_evidence"} & set(record), sorted(record))

    def test_a_command_from_another_pid_is_not_submitted_to_any_gateway(self):
        status, record, err = self.probe(KC_TEST_HEALTH_PID="4242")
        self.assertEqual(status, 1, json.dumps(record, indent=1) + err)
        self.assertEqual(record["binding"]["reason"], "command_not_from_launched_process")
        self.assertNotIn("gateways", record)

    def test_an_inert_slot_refuses_before_anything_is_published(self):
        status, record, err = self.probe(KC_TEST_CAPSULE="a" * 64)
        self.assertEqual(status, 3, json.dumps(record, indent=1) + err)
        self.assertIn("not ACTIVE on the expected mobile capsule", record["refused"])
        self.assertNotIn("fixture", record)

    def test_a_v2_publication_to_a_v1_capability_is_not_answered_and_the_record_says_so(self):
        # THE FAIL-CLOSED NEGATIVE: the v1 service counts the v2 document as missing_fields and publishes nothing.
        status, record, err = self.probe_v2()
        self.assertEqual(status, 1, json.dumps(record, indent=1) + err)
        self.assertEqual(record["verdict"], "FAIL")
        self.assertIs(record["contract_answered"], False)
        self.assertEqual(record["observation"]["contract"], "kc.target_observation/2")
        self.assertNotIn("command", record)
        self.assertNotIn("gateways", record)
        self.assertEqual(len(record["failures"]), 1, record["failures"])
        self.assertIn("contract_answered false", record["failures"][0])
        self.assertEqual(record["cake_after"], record["cake_before"])


class V2AgainstTheRenderedWrapper(EndToEnd):
    """The v2 mode against the capability as Cake launches it (the rendered wrapper, contract v2)."""

    launch = "wrapper"

    def test_a_record_derived_observation_is_answered_at_the_pulse_speed_and_applied(self):
        status, record, err = self.probe_v2(None, "--target-id", "cube_b")
        self.assertEqual(status, 0, json.dumps(record, indent=1) + err)
        self.assertEqual(record["verdict"], "PASS")
        self.assertIs(record["contract_answered"], True)
        self.assertEqual(record["contract"], "kc.target_observation/2")
        self.assertIn("STORED PERCEPTION RECORD", record["fixture_label"])
        observation, command = record["observation"], record["command"]
        self.assertEqual(sorted(observation), sorted(V2_FIELDS))  # the record is printed with sorted keys; RecordDerivation pins the wire order
        axis = cycle.reach_direction_pad(perception_record()["pad_by_template"]["homography_image_to_pad"], CONTACT)
        need = cycle.base_advance_needed_mm(ANCHOR, axis)
        self.assertEqual(observation["x_m"], need / 1000.0)
        self.assertEqual((observation["target_id"], observation["observation_id"], observation["motion_epoch"]), ("cube_b", OBSERVATION_ID, 0))
        self.assertLess(observation["capture_ns"], observation["published_ns"])
        self.assertEqual(observation["max_age_ns"], source.max_age_ns_for(approach_probe.DEFAULT_PERCEPTION_DEADLINE_S))
        # The answer: to THIS publication, at exactly the executor's pulse speed, straight ahead, from the launched pid.
        self.assertEqual(record["producer_pid"], self.service.pid)
        self.assertTrue(command["command_id"].startswith(f"mobile_approach.pid{self.service.pid}."))
        self.assertEqual(command["timestamp_ns"], observation["published_ns"])
        self.assertEqual(command["expiry_ns"], observation["published_ns"] + 250_000_000)
        self.assertEqual((command["vx_mps"], command["vy_mps"], command["omega_rps"]), (cycle.BASE_VX_MPS, 0.0, 0.0))
        self.assertEqual(record["gateways"]["disabled"]["outcome"], "ZERO_AUTONOMOUS_DISABLED")
        self.assertEqual(record["gateways"]["disabled"]["recorded_calls"], [["zero"]])
        self.assertEqual(record["gateways"]["enabled"]["outcome"], "APPLIED")
        self.assertEqual(record["gateways"]["enabled"]["applied"], [cycle.BASE_VX_MPS, 0.0, 0.0])
        self.assertIn("submitted_ms_after_publication", record)
        evidence = record["observation_evidence"]
        self.assertIs(evidence["stationary_asserted_by_probe"], True)
        self.assertIs(evidence["motion_epoch_asserted_by_probe"], True)
        self.assertEqual((evidence["need_mm"], evidence["landmark_inliers"], evidence["reprojection_rms_px"]), (need, 41, 1.3))
        self.assertEqual(evidence["approach_state"]["axis_pad"], [0.0, 1.0])
        self.assertEqual(evidence["expected_vx_mps"], cycle.BASE_VX_MPS)
        self.assertEqual(record["lerobot_modules_loaded"], [])
        self.assertIs(record["physical_execution"], False)
        self.assertEqual((record["slot_generation"], record["slot_capsule"]), (2, MOBILE))

    def test_a_tilde_record_path_is_expanded_and_the_evidence_names_no_home_directory(self):
        # The deploy script hands `~/...` over quoted, so it reaches the probe literally; the probe expands it, and the
        # evidence writes the home directory back as '~' (the record is pasted into documents the docs gate scans).
        home = os.path.join(self.tmp.name, "robot-home")
        write_record(home, perception_record())
        status, record, err = self.probe("--contract", "v2", "--record", os.path.join("~", "perception", f"{OBSERVATION_ID}.json"),
                                         "--timeout-s", "3", HOME=home)
        self.assertEqual(status, 0, json.dumps(record, indent=1) + err)
        evidence = record["observation_evidence"]
        self.assertEqual(evidence["record_path"], f"~/perception/{OBSERVATION_ID}.json")
        self.assertNotIn(home, json.dumps(record))
        self.assertTrue(evidence["executor_module"].endswith(os.path.join("bin", "laptop", "manipulation", "run_single_cube_cycle.py")), evidence)
        self.assertTrue(evidence["source_module"].endswith(os.path.join("autonomy", "motion", "target_observation_source.py")), evidence)
        self.assertEqual(record["contract"], "kc.target_observation/2")

    def test_the_v1_fixture_is_dropped_by_the_v2_capability(self):
        # The other direction of the same fail-closed rule: the deployed wrapper drops a v1 document (contract_mismatch).
        status, record, err = self.probe("--timeout-s", "3")
        self.assertEqual(status, 1, json.dumps(record, indent=1) + err)
        self.assertEqual(record["fixture"]["frame"], "base")
        self.assertEqual(record["failures"], ["the capability published no command within the timeout"])
        self.assertNotIn("contract_answered", record, "the v1 record is unchanged")

    def test_a_command_from_another_pid_is_not_submitted_to_any_gateway(self):
        status, record, err = self.probe_v2(None, KC_TEST_HEALTH_PID="4242")
        self.assertEqual(status, 1, json.dumps(record, indent=1) + err)
        self.assertIs(record["contract_answered"], True)
        self.assertEqual(record["binding"]["reason"], "command_not_from_launched_process")
        self.assertNotIn("gateways", record)

    def test_a_record_with_no_valid_pad_frame_refuses_before_anything_is_published(self):
        status, record, err = self.probe_v2(perception_record(pad_frame_valid=False))
        self.assertEqual(status, 3, json.dumps(record, indent=1) + err)
        self.assertEqual(record["observation_unavailable"], ["pad_frame_invalid"])
        self.assertIn("pad_frame_valid", record["refused"])
        self.assertNotIn("observation", record)
        self.assertNotIn("contract_answered", record)

    def test_a_cube_no_bounded_advance_reaches_refuses_with_the_sources_reason(self):
        status, record, err = self.probe_v2(perception_record(anchor=[0.0145, -0.1475 - 0.200]))
        self.assertEqual(status, 3, json.dumps(record, indent=1) + err)
        self.assertEqual(record["observation_unavailable"], ["advance_out_of_bounds"])
        self.assertNotIn("observation", record)

    def test_v2_without_a_record_is_a_usage_error(self):
        args = [sys.executable, PROBE, "--admin-probe", self.admin_probe, "--admin-socket", "x", "--slot-uuid", SLOT, "--capsule", MOBILE,
                "--observations", "ipc:///o", "--commands", "ipc:///c", "--contract", "v2"]
        done = subprocess.run(args, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 5, done.stderr)
        self.assertIn("--record", done.stderr)


if __name__ == "__main__":
    unittest.main()
