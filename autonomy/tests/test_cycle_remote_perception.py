#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cycle_remote_perception.py: the single-cube controller with its perception on the desktop (DESKTOP
# PERCEPTION in run_single_cube_cycle.py). The desktop service here is the REAL one (bin/laptop/perception/
# perception_service.py) in a thread on this machine's loopback, answering the REAL client over pyzmq; only the model is a
# scripted stand-in, exactly as in the local tests. What must hold:
#   - a mission run through the desktop does, commands, prints and records exactly what the same run does with its
#     perception local (the scripted World of test_cycle_mission_mode.py, run both ways and compared);
#   - the cube listing on the robot, fed by the desktop's proposals, writes the same bytes as the local listing
#     (perception record, overlay and candidates sidecar), through Cycle._mission_candidates itself;
#   - a stop abandons an outstanding desktop request within 0.3 s and nothing is appended or written; the read-only
#     re-observation after a base pulse is not abandoned;
#   - a service that is missing, not this run's, or named without all four values refuses BEFORE the robot is constructed;
#   - an answer refused after the release holds the arm at the RETREAT: nothing more is commanded (no EXIT move).
# No robot, no camera, no model, no network beyond 127.0.0.1.
# Run directly: python3 autonomy/tests/test_cycle_remote_perception.py
import contextlib
import functools
import io
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("autonomy/tests", "autonomy/motion", "bin/laptop/manipulation", "bin/laptop/perception", "autonomy/perception", "tools"):
    sys.path.insert(0, str(ROOT / _sub))

import cv2  # noqa: E402

import run_single_cube_cycle as current  # noqa: E402
import test_cycle_behavior_preserved as differential  # noqa: E402
import test_cycle_mission_mode as mission  # noqa: E402  (the scripted multi-cube World and its scene)
import test_cube_candidates as scenes  # noqa: E402  (the synthetic frame and the fixed stand-in detector)
import cube_candidates  # noqa: E402
import perception_service as ps  # noqa: E402
import remote_perception as rp  # noqa: E402
import static_world_observation as swo  # noqa: E402
from observation_store import ObservationStore  # noqa: E402
from scan_arm_motion import make_bounded_move_arm  # noqa: E402
from zero_shot_detector import CUBE_LABELS  # noqa: E402

REVISION = "0123456789abcdef0123456789abcdef01234567"
FILES_DIGEST = rp.model_files_digest({"config.json": "a" * 64, "model.safetensors": "b" * 64})
OVERLAY_BYTES = b"\x89PNG scripted overlay"


def service_identity(revision=REVISION):
    """What the robot will expect in these tests: this tree's code, the print the scan module would read, the pin below."""
    digests = rp.code_digests(ROOT)
    template = swo.TEMPLATE_DIR if current.cli._PAD_TEMPLATE_DIR is None else current.cli._PAD_TEMPLATE_DIR
    return rp.PerceptionIdentity(digests["sources_sha256"], digests["perceive_sha256"], rp.template_digest(template), revision, FILES_DIGEST)


class ServiceThread:
    """The real PerceptionService on 127.0.0.1:<ephemeral>, in its own thread, for the length of one test. `tamper`, when
    given, may replace an answer (the service's own bytes) before it is sent: a stand-in for a wrong service."""

    def __init__(self, test, detector, *, identity=None, tamper=None):
        import zmq

        self.context = zmq.Context()
        recorder = ps._Recording(detector)
        self.service = ps.PerceptionService(identity=identity or service_identity(), detector=cube_candidates.CubeDetectionCache(recorder), recorder=recorder,
                                            info={"model_name": "example-org/example-detector", "device": "cpu", "service_session": "t" * 8})
        if tamper is not None:
            handle = self.service.handle
            self.service.handle = lambda parts, received_ns: tamper(parts, handle(parts, received_ns))
        self.sock, self.port = ps.bind_loopback(self.context, 0)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.service.serve, args=(self.sock, self.stop), daemon=True)
        self.thread.start()
        test.addCleanup(self.close)

    def close(self):
        self.stop.set()
        self.thread.join(5)
        self.sock.close(0)
        self.context.term()

    def environment(self, deadline_s=30.0, revision=REVISION) -> dict:
        return {current.PERCEPTION_PORT_ENV: str(self.port), current.PERCEPTION_DEADLINE_ENV: repr(float(deadline_s)),
                current.PERCEPTION_MODEL_REVISION_ENV: revision, current.PERCEPTION_MODEL_FILES_ENV: FILES_DIGEST,
                current.RESIDENT_SESSION_ENV: "0c" * 16}


def run_world(test, world, directory: str, *, target_path=None, remote=None, extra_env=None) -> dict:
    """test_cycle_mission_mode.run_world, with ONE difference: the scripted perceive also writes an overlay, as the real one
    does (the service answers with it). `remote` is a ServiceThread, or None for the local path; the same scripted perceive
    answers either way, through run_stage1_scan.perceive, which the service looks up on the module at every call."""
    observations = os.path.join(directory, "observations")
    shutil.rmtree(observations, ignore_errors=True)
    scan_cli, made = current.cli, []

    def perceive(role, image, out_dir, name, detector):
        world.detector_types.append(type(detector).__name__)
        detector.detect(image, ("a cube",))
        record = world.perception()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.json").write_text(json.dumps(record))
        (out_dir / f"{name}-overlay.png").write_bytes(OVERLAY_BYTES)
        return [f"{role.upper()}: synthetic perception"]

    def move_base_factory(remote_ip, robot_id, **options):
        made.append(options)
        return world.base_primitive(options)

    environment = {"KC_RUNTIME_CONFIG": "", "KC_EVENTS_PATH": "", "KC_RUN_ID": "", "KC_ARTIFACT_DIGEST": "", "KC_HOST_LEASE": "", "KC_HOST_SESSION": "",
                   **({} if target_path is None else {"KC_MISSION_TARGET": target_path}), **({} if remote is None else remote.environment()), **(extra_env or {})}
    patches = [mock.patch.object(current, "time", world.clock),
               mock.patch.object(current, "make_bounded_move_arm", lambda read_pose, send: make_bounded_move_arm(read_pose, send, sleep=world.clock.sleep, monotonic=world.clock.perf_counter)),
               mock.patch.object(scan_cli, "now_utc", world.clock.now_utc), mock.patch.object(scan_cli, "PiCamera", lambda ssh_target, local_dir: mission.CountingCamera(world)),
               mock.patch.object(scan_cli, "perceive", perceive), mock.patch.object(scan_cli, "make_detector", lambda: mission.ScriptedDetector(world)),
               mock.patch.object(cube_candidates, "enumerate_observation", world.listing), mock.patch.object(current, "make_move_base", move_base_factory),
               mock.patch.dict(sys.modules, differential.synthetic_lerobot(world)), mock.patch.dict(os.environ, environment),
               mock.patch.object(current, "ObservationStore", functools.partial(ObservationStore, root_dir=observations)),
               mock.patch.object(current, "HOME_FILE", Path(directory) / "calibration" / "scan-home-current.json")]
    for name in (current.PERCEPTION_PORT_ENV, current.PERCEPTION_DEADLINE_ENV, current.PERCEPTION_MODEL_REVISION_ENV, current.PERCEPTION_MODEL_FILES_ENV):
        if remote is None and name not in (extra_env or {}):
            patches.append(mock.patch.dict(os.environ, {name: ""}))  # absent in effect: an EMPTY port is no desktop perception
    handler, stdout, stderr = signal.getsignal(signal.SIGINT), io.StringIO(), io.StringIO()
    try:
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            if target_path is None:
                os.environ.pop("KC_MISSION_TARGET", None)
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            try:
                ending = ("returned", current.main(["--do", "cycle", "--remote-ip", mission.LOCAL, "--robot-id", "synthetic-unit", "--pi-ssh",
                                                    "synthetic-operator@" + mission.ELSEWHERE, "--scan-id", mission.SCAN_ID]))
            except SystemExit as leaving:
                ending = ("SystemExit", leaving.code)
            except Exception as error:  # noqa: BLE001
                ending = (type(error).__name__, str(error))
    finally:
        signal.signal(signal.SIGINT, handler)
    records = sorted(Path(observations, mission.SCAN_ID).glob("cycle-*.json")) if os.path.isdir(os.path.join(observations, mission.SCAN_ID)) else []
    transitions = json.loads(records[-1].read_text()) if records else []
    result = json.loads(Path(world.result_path).read_text()) if world.result_path and os.path.exists(world.result_path) else None
    perception = Path(observations, mission.SCAN_ID, "perception")
    return {"ending": ending, "stdout": stdout.getvalue(), "stderr": stderr.getvalue(), "sent": list(world.sent), "base_pulses": list(world.base_pulses),
            "captures": list(world.captures), "states": [entry["state"] for entry in transitions], "transitions": transitions, "result": result,
            "disconnects": world.disconnects, "perception_files": sorted(p.name for p in perception.iterdir()) if perception.is_dir() else []}


class RemoteCase(unittest.TestCase):
    def setUp(self):
        self.directory = mission.fixtures.use_synthetic_runtime(self)
        current.use_runtime_paths(current.cli.runtime_paths({}))
        # These runs walk the path AFTER the capability gate at SEARCH, which the real target_observation_source refuses
        # today (test_cycle_mission_mode.stand_in_source says why): stand in an available source, as that module does.
        # It changes nothing about the desktop perception compared here; TheCapabilityFollowsTheMeasurement in
        # test_cycle_mission_mode.py pins the real, unavailable answer.
        mission.stand_in_source(self)

    def second_execution(self, world, remote=None, **options) -> dict:
        path, target = mission.second_execution(self.directory)
        world.result_path = target.result_path
        return run_world(self, world, self.directory, target_path=path, remote=remote, **options)


class TheDesktopRunIsTheLocalRun(RemoteCase):
    def test_a_mission_run_through_the_desktop_commands_prints_and_records_what_the_local_run_does(self):
        local = self.second_execution(mission.placed_first_world())
        world = mission.placed_first_world()
        remote = self.second_execution(world, remote=ServiceThread(self, mission.ScriptedDetector(world)))
        self.assertEqual(("returned", 0), local["ending"], local["stdout"][-2000:])
        self.assertEqual(local["ending"], remote["ending"], remote["stdout"][-2000:])
        for key in ("sent", "base_pulses", "captures", "states", "transitions", "result", "stdout", "disconnects"):
            self.assertEqual(local[key], remote[key], key)
        self.assertEqual(set(local["perception_files"]), {name for name in remote["perception_files"] if not name.endswith(rp.EXCHANGE_SUFFIX)},
                         "the same records and overlays, where perceive writes them")
        exchanges = [name for name in remote["perception_files"] if name.endswith(rp.EXCHANGE_SUFFIX)]
        self.assertEqual(len([name for name in local["perception_files"] if name.endswith("-overlay.png")]), len(exchanges), "one exchange per perceived frame")
        self.assertIn("RemoteCubeDetections", {type(entry["detector"]).__name__ for entry in world.enumerated}, "the listing reads the desktop's proposals")

    def test_every_exchange_names_this_run_and_its_capture(self):
        world = mission.placed_first_world()
        service = ServiceThread(self, mission.ScriptedDetector(world))
        run = self.second_execution(world, remote=service)
        self.assertEqual(("returned", 0), run["ending"])
        folder = Path(self.directory, "observations", mission.SCAN_ID, "perception")
        for name in (name for name in run["perception_files"] if name.endswith(rp.EXCHANGE_SUFFIX)):
            exchange = json.loads((folder / name).read_text())
            self.assertTrue(exchange["accepted"])
            robot, capture = exchange["request"]["robot"], exchange["request"]["capture"]
            self.assertEqual(("0c" * 16, mission.SCAN_ID, mission.MISSION_ID, "cube-b", mission.SCAN_ID, os.getpid()),
                             (robot["resident_session"], robot["run_id"], robot["mission_id"], robot["target_id"], robot["scan_id"], robot["controller_pid"]))
            self.assertEqual(name[: -len(rp.EXCHANGE_SUFFIX)], capture["observation_id"])
            stored = Path(self.directory, "observations", mission.SCAN_ID, "frames", f"{capture['observation_id']}.png")
            self.assertEqual(rp.file_sha256(stored), capture["file_sha256"], "the request names the stored frame's bytes")


class TheListingOnTheRobotIsTheLocalListing(unittest.TestCase):
    """cube_candidates on the robot, fed by the desktop's proposals, through Cycle._mission_candidates itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-remote-listing-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.image = scenes.scene(scenes.TWO)
        self.frame = self.tmp / "frame.png"
        cv2.imwrite(str(self.frame), self.image)
        self.name = scenes.observation_id(5)

    def detector(self):
        return scenes.FixedDetector([d for d in scenes.proposals(scenes.TWO, [0.6, 0.45]) if d["label"] in CUBE_LABELS])

    def cycle(self, session_dir, detector):
        made = current.Cycle.__new__(current.Cycle)  # only what _mission_candidates reads
        made.session_dir, made.detector, made.abort = session_dir, detector, {"set": False}
        return made

    def test_the_record_overlay_and_candidates_sidecar_are_the_same_bytes(self):
        local_dir, remote_dir = self.tmp / "local", self.tmp / "remote"
        cache = cube_candidates.CubeDetectionCache(self.detector())
        current.cli.perceive("wrist", cv2.imread(str(self.frame)), local_dir / "perception", self.name, cache)
        local = self.cycle(local_dir, cache)._mission_candidates({"frame": str(self.frame), "observation_id": self.name})

        service = ServiceThread(self, self.detector())
        client = rp.RemotePerception(port=service.port, deadline_s=30.0, expect=service_identity(), robot={"run_id": "listing"})
        client.perceive(self.frame, remote_dir / "perception", self.name)
        remote = self.cycle(remote_dir, client.detections)._mission_candidates({"frame": str(self.frame), "observation_id": self.name})

        self.assertEqual((None, None), local[1:])
        self.assertEqual(local, remote)
        self.assertEqual(2, len(remote[0]), "both cubes of the frame are listed from the desktop's proposals")
        for suffix in (".json", "-overlay.png", cube_candidates.SIDECAR_SUFFIX):
            self.assertEqual((local_dir / "perception" / f"{self.name}{suffix}").read_bytes(), (remote_dir / "perception" / f"{self.name}{suffix}").read_bytes(), suffix)


class TheStopDuringADesktopRequest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-remote-stop-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.frame = self.tmp / "frame.png"
        cv2.imwrite(str(self.frame), scenes.scene(scenes.TWO))
        patch = mock.patch.object(current, "ObservationStore", functools.partial(ObservationStore, root_dir=str(self.tmp / "observations")))
        patch.start()
        self.addCleanup(patch.stop)

    def slow_service(self, delay_s):
        detector = scenes.FixedDetector([])
        slow = type("Slow", (), {"detect": lambda _self, image, labels, *a, **k: (time.sleep(delay_s), detector.detect(image, labels))[1], "_model": None})()
        return ServiceThread(self, slow)

    def cycle(self, service, deadline_s=30.0):
        client = rp.RemotePerception(port=service.port, deadline_s=deadline_s, expect=service_identity(), robot={"run_id": "stop"})
        made = current.Cycle(robot=object(), pi_ssh=None, scan_id="remote-stop", plan_only=True, perception=client)  # plan-only: no camera is made
        made.target, made._mission = object(), {"reason_code": None}  # a mission run, for the reason code
        return made

    def test_a_stop_abandons_the_request_within_a_third_of_a_second_and_nothing_is_written(self):
        cycle = self.cycle(self.slow_service(3.0))
        flagged = {}

        def stop():
            flagged["at"] = time.monotonic()
            cycle.abort.update(set=True)

        timer = threading.Timer(0.5, stop)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(RuntimeError) as caught:
            cycle._remote_perceive("obs-20260925T000000Z-wrist-0001", self.frame)
        self.assertLess(time.monotonic() - flagged["at"], 0.3)
        self.assertNotIsInstance(caught.exception, current.CycleFailed, "a stop is exit 4 (stopped), never a failure")
        self.assertEqual("operator_abort_perception", cycle._mission["reason_code"])
        written = [path.name for path in (cycle.session_dir / "perception").iterdir() if not path.name.endswith(rp.EXCHANGE_SUFFIX)]
        self.assertEqual([], written, "nothing is written as a result")

    def test_the_re_observation_after_a_base_pulse_is_not_abandoned(self):
        cycle = self.cycle(self.slow_service(0.5))
        cycle.abort["set"], cycle._post_pulse_reobservation = True, True
        lines = cycle._remote_perceive("obs-20260925T000001Z-wrist-0001", self.frame)
        self.assertTrue(lines)
        self.assertTrue((cycle.session_dir / "perception" / "obs-20260925T000001Z-wrist-0001.json").exists())

    def test_a_refused_answer_is_a_cycle_failure_with_its_reason_code(self):
        cycle = self.cycle(self.slow_service(1.0), deadline_s=0.3)
        with self.assertRaises(current.CycleFailed):
            cycle._remote_perceive("obs-20260925T000002Z-wrist-0001", self.frame)
        self.assertEqual("perception_remote_deadline", cycle._mission["reason_code"])


class RefusedBeforeTheRobotIsConstructed(RemoteCase):
    def assertRefusedBeforeConnect(self, run, reason_text):
        self.assertEqual(("returned", 3), run["ending"], run["stdout"][-2000:])
        self.assertEqual(([], [], 0), (run["sent"], run["captures"], run["disconnects"]), "the robot was never constructed")
        self.assertIn(reason_text, run["stdout"])

    def test_a_service_that_does_not_answer(self):
        import socket

        with socket.socket() as holder:  # a port nothing listens on: for this run, the service is down
            holder.bind(("127.0.0.1", 0))
            nobody = holder.getsockname()[1]
        dead = {current.PERCEPTION_PORT_ENV: str(nobody), current.PERCEPTION_DEADLINE_ENV: "0.3", current.PERCEPTION_MODEL_REVISION_ENV: REVISION,
                current.PERCEPTION_MODEL_FILES_ENV: FILES_DIGEST}
        run = self.second_execution(mission.placed_first_world(), extra_env=dead)
        self.assertRefusedBeforeConnect(run, "(deadline)")

    def test_a_service_that_is_not_this_runs(self):
        world = mission.placed_first_world()
        service = ServiceThread(self, mission.ScriptedDetector(world), identity=service_identity(revision="1" * 40))
        run = self.second_execution(world, remote=service)
        self.assertRefusedBeforeConnect(run, "model_mismatch")

    def test_a_desktop_perception_named_without_all_four_values(self):
        for missing in (current.PERCEPTION_DEADLINE_ENV, current.PERCEPTION_MODEL_REVISION_ENV, current.PERCEPTION_MODEL_FILES_ENV):
            with self.subTest(missing=missing):
                world = mission.placed_first_world()
                service = ServiceThread(self, mission.ScriptedDetector(world))
                run = self.second_execution(world, remote=service, extra_env={missing: ""})
                self.assertRefusedBeforeConnect(run, "without all four of its values")


class ARefusalAfterTheReleaseHoldsTheArm(RemoteCase):
    def test_the_retreat_perception_refused_is_a_hold_and_no_exit_move_follows(self):
        world = mission.placed_first_world()
        tripped = {}

        def tamper(parts, answer):
            request = json.loads(parts[0])
            if request.get("op") == rp.OP_PERCEIVE and world.released:
                tripped["after_release"] = tripped.get("after_release", 0) + 1
            # The first frame after the release is the opening grip's own observation; the SECOND is the RETREAT move's.
            if tripped.get("after_release") == 2 and "sent" not in tripped:
                tripped["sent"] = len(world.sent)
                header = json.loads(answer[0])
                header["request_id"] = "0" * 32  # an answer to another request: refused on the robot
                return [rp.canonical(header)] + list(answer[1:])
            return answer

        run = self.second_execution(world, remote=ServiceThread(self, mission.ScriptedDetector(world), tamper=tamper))
        self.assertIn("sent", tripped, "the scene released the cube and perceived twice after it")
        self.assertEqual(("returned", 5), run["ending"], run["stdout"][-2000:])
        self.assertIn("RELEASED", run["states"])
        self.assertEqual("RELEASED", run["states"][-1], "no state after the release was reached")
        self.assertEqual(tripped["sent"], len(run["sent"]), "a command followed the refused answer")
        self.assertEqual(("failed", "perception_remote_request_id_mismatch"), (run["result"]["outcome"], run["result"]["reason_code"]))


class TheNamesAreTheSupervisors(unittest.TestCase):
    def test_the_controller_reads_what_the_runtime_exports(self):
        supervisor = current.cli.import_runtime("demo_runtime.supervisor")
        self.assertEqual((supervisor.PERCEPTION_PORT_ENV, supervisor.PERCEPTION_DEADLINE_ENV, supervisor.PERCEPTION_MODEL_REVISION_ENV, supervisor.PERCEPTION_MODEL_FILES_ENV,
                          supervisor.RESIDENT_SESSION_ENV),
                         (current.PERCEPTION_PORT_ENV, current.PERCEPTION_DEADLINE_ENV, current.PERCEPTION_MODEL_REVISION_ENV, current.PERCEPTION_MODEL_FILES_ENV,
                          current.RESIDENT_SESSION_ENV))

    def test_without_the_port_nothing_is_asked_and_nothing_is_made(self):
        arguments = type("Args", (), {"plan_only": False, "scan_id": "x"})()
        self.assertIsNone(current.make_remote_perception(arguments, None, environ={}))
        self.assertIsNone(current.make_remote_perception(arguments, None, environ={current.PERCEPTION_PORT_ENV: ""}))
        planning = type("Args", (), {"plan_only": True, "scan_id": "x"})()
        self.assertIsNone(current.make_remote_perception(planning, None, environ={current.PERCEPTION_PORT_ENV: "5590"}), "a plan perceives nothing")


if __name__ == "__main__":
    unittest.main()
