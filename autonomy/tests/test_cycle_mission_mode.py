#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cycle_mission_mode.py: the single-cube controller carrying ONE target of a multi-cube mission
# (KC_MISSION_TARGET, the MISSION MODE of run_single_cube_cycle.py). The scripted WORLD of test_cycle_behavior_preserved.py
# runs main() for real (run_pose, grip, observe, base_pulse, the planner, the executor, the store); only the boundary is
# scripted: the robot client, the camera, the frozen perception, the detector, the cube listing of cube_candidates and,
# where a test says so, the base primitive. Where the World cannot produce a physical situation (a loaded reach that falls
# short), the scripted MACHINE of test_run_single_cube_cycle.py is used instead.
#
# What is pinned here, against the operator's rulings:
#   - the second execution selects the unresolved cube past the one already placed, and a scene with only that placed cube
#     is a refusal, never DONE_ALREADY_PLACED; PLACED is the block on the pad at this run's own release (the pad policy of
#     2026-09-29): an earlier target's block is never credited when it stands away from that release, and taking it off
#     the pad never fails the run;
#   - ALIGN with two verified cubes refuses; a target without the capability never drives the base (the arm-only fallback
#     is refused with the cube held, and so is a mode that changes once the cube is held);
#   - for a capability target the in-process approach controller is never imported or constructed: the forward half is the
#     relay, and a relay that refuses ends the cycle with the cube held;
#   - a stop during a perception: nothing is sent or pulsed after it and the run ends "stopped"; an aborted gripper move is
#     not followed by an observation; the read-only re-observation after a base pulse still happens after a stop; a stop
#     that arrives while the REAL relay reads the capability builds no transport and commands no pulse;
#   - a cube listing that raises refuses at SEARCH, and at the EXIT view it is "not placed" with the base still returned;
#   - THE CAPABILITY FOLLOWS THE MEASUREMENT: a cube that measures BASE_ADVANCE is refused at SEARCH, before PREGRASP and
#     after the selection is written back: mission_capability_required when the run carries no capability (the operator's
#     scene: the first cube already on the pad, the gripper empty). The availability question the gate asks
#     (target_observation_source.capability_observation_available) is answered True under contract v2, so a run that
#     carries the capability passes it, and whether it has an observation is decided per pulse by resolve: without a
#     perception budget (the scripted World perceives here) the first pulse is refused perception_budget_unknown, with
#     the cube held. A file without radii is refused before the robot exists;
#   - G1: a cube staged on the pad before the mission, away from this run's release, is never credited to a run that
#     dropped its own;
#   - THE APPROACH VIEW (C1): for a run that carries the capability, advance_base takes one STAMPED observe() right after
#     the HOLD_OBSERVE rise, so the latest stamped capture at the first pulse is that view, at the current motion epoch,
#     and resolves to a v2 observation whose x_m is the executor's own remaining advance; the approach state (_approach)
#     is kept per pulse on the scripted machine and is None once advance_base returns;
#   - CONTRACT V2 END TO END on the scripted World: the REAL relay, on the World's own clock, publishes the resolved v2
#     observation to a scripted v2 capability, every answer (timestamp == published_ns) is APPLIED by the gateway into a
#     transport that moves the World's base, the executor measures the displacement against the pad, the cube is PLACED,
#     the base returns, and the result credits the applied commands;
#   - a LIVE run without a mission target (KC_RUNTIME_MODE=live) never imports the synthetically fed primitive and refuses
#     a cube whose placement needs the base, and a forward base action before the robot exists; past SEARCH (the scripted
#     MACHINE), it refuses a plan from the measured grasp that needs the base, the arm-only fallback to the base, and any
#     forward pulse in base_pulse, each with nothing forward commanded;
#   - THE AUTO TARGET (requires_capability "auto", the ruling of 2026-09-26): SEARCH takes the mode it measured, with no D5
#     refusal: a measured ARM_ONLY runs the proven arm-only cycle with no base primitive at all; a measured BASE_ADVANCE
#     in a run that carries no capability is the same SEARCH refusal (mission_capability_required) and in a run that
#     carries it is driven as a capability run; a plan that flips to BASE_ADVANCE while the cube is held is driven when
#     the run carries the capability and ends the run with the cube held when it does not;
#   - without KC_MISSION_TARGET nothing of this is reached (test_cycle_behavior_preserved.py pins the legacy behaviour
#     against the physically passed tag; the last class here checks the mission seams themselves stay closed).
# No robot, no camera, no network, no lerobot, no model, no capability process.
# Run directly: python3 autonomy/tests/test_cycle_mission_mode.py
import contextlib
import functools
import io
import json
import os
import shutil
import signal
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "motion"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import run_single_cube_cycle as current  # noqa: E402
import test_cycle_behavior_preserved as differential  # noqa: E402  (the scripted World, the synthetic lerobot, the virtual clock)
import test_run_single_cube_cycle as fixtures  # noqa: E402  (the synthetic runtime, the scripted machine)
import ast  # noqa: E402
import capability_pulse  # noqa: E402
import cube_candidates  # noqa: E402  (on the path through the controller's own imports)
import mobile_approach  # noqa: E402
import target_binding as tb  # noqa: E402
import target_observation_source as tos  # noqa: E402
from observation_store import ObservationStore  # noqa: E402
from scan_arm_motion import make_bounded_move_arm  # noqa: E402

SCAN_ID = differential.SCAN_ID
LOCAL, ELSEWHERE = "127.0.0.1", "192.0.2.10"  # the loopback host a capability target needs, and a documentation address
MISSION_ID = "mission-" + "5eed" * 3
# PAD_FRAME positions (metres). A_* is the arm-only target, B_* the one that needs the capability; both starts are the World's
# own scenarios (its arm-only placement and its base-advance placement), so every step after SEARCH is a path it already walks.
A_START, A_FINAL = [0.0145, -0.1475 + 0.012], [0.0145, -0.1475 + 0.045]
B_START, B_FINAL = [0.0145, -0.1825], [0.0145, -0.1475 + 0.058]
A_PLACED = [-0.100, 0.020]  # where the first target was placed, far from where the second one is released
CONTACT = [553.0, 545.0]  # the contact pixel both World scenarios see their cube at
RIGHT_CONTACT = [470.0, 545.0]  # from the RIGHT sweep sector the reference column maps 0.09 deg beyond the return band and the World then trims +2.2 deg at ALIGN; this column maps 18 deg from home, inside it with room for the trims
B_CONTACT_BESIDE_A = [553.0, 660.0]  # B, seen lower in the same frame as A (farther from the pad)
# TEST VALUES for the claim and match radii (the design's D3 starting points). Not qualified; the file must carry some.
TEST_RADII = {"r_claim_mm": 30.0, "r_match_mm": 40.0, "basis": "test values only (design D3 starting points)"}
MISSION_EXECUTION = ROOT / "packages" / "demo-runtime" / "demo_runtime" / "mission_execution.py"
SUPERVISOR = ROOT / "packages" / "demo-runtime" / "demo_runtime" / "supervisor.py"
PERCEPTION_DEADLINE_S = 60.0  # the perception budget a LIVE run exports (KC_PERCEPTION_DEADLINE_S); the World perceives here and has none unless a test says so


def v2_stand_in(target_id, clock, motion_epoch):
    """observe() -> a v2 stand-in observation of `target_id`, captured on the relay's clock at the first call, at the relay's
    epoch, with a stated remaining advance: so that a relay gets as far as its Cake reads in a test about what happens
    THERE. It measures nothing and says nothing about any robot."""
    stamps = {"capture": None}

    def observe():
        if stamps["capture"] is None:
            stamps["capture"] = clock.now_ns()
        return tos.ObservedTarget(target_id=target_id, x_m=0.065, capture_monotonic_ns=stamps["capture"], motion_epoch=motion_epoch(),
                                  observation_id="obs-stand-in-0001", max_age_ns=tos.max_age_ns_for(PERCEPTION_DEADLINE_S), stationary=True)

    return observe


def _assigned(path: Path, name: str):
    """The literal assigned to NAME at module level in PATH (a Name target, or one element of a tuple assignment), parsed
    and never imported: those modules run in the runtime, and what must agree with the controller is the literal."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name:
                return node.value.value
            if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                for element, value in zip(target.elts, node.value.elts):
                    if isinstance(element, ast.Name) and element.id == name:
                        return value.value
    return None


def inside(pad_m) -> bool:
    return abs(pad_m[0]) <= current.PAD_SIZE_M[0] / 2.0 and abs(pad_m[1]) <= current.PAD_SIZE_M[1] / 2.0


def declared(target_id, requires_capability, bound=None, placed=None, effective=None) -> dict:
    """One `targets` entry of the mission target file. `effective` is the mode an AUTO target's binding measured, given
    only once it is bound (target_binding, THE AUTO TARGET); a declared target never carries one."""
    return {"target_id": target_id, "requires_capability": requires_capability, "effective_mode": effective, "bound_pad_m": bound, "placed_pad_m": placed}


def capability_block(directory: str) -> dict:
    """Endpoints and identities that name nothing that exists: the admin probe is a path with no file behind it, so a relay
    that reaches for the capability refuses at its first read instead of finding one."""
    return {"admin_probe": os.path.join(directory, "no-admin-probe"), "admin_socket": os.path.join(directory, "no-admin.sock"), "slot_uuid": "0f" * 16,
            "capsule": "ab" * 32, "observation_endpoint": "ipc://" + os.path.join(directory, "obs.sock"), "command_endpoint": "ipc://" + os.path.join(directory, "cmd.sock")}


def write_target(directory: str, target_id: str, targets: list, run_id: str = SCAN_ID, carry=None, radii=TEST_RADII) -> tuple:
    """(the target file's path, the MissionTarget) written the way the runtime writes it: checked by the same parser. The
    capability is CARRIED when the target declares it, unless `carry` says otherwise (a first execution the runtime runs
    without it); `radii` None is the file of a configuration with no qualified radii."""
    requires = next(target["requires_capability"] for target in targets if target["target_id"] == target_id)
    carried = requires if carry is None else carry
    path, result = os.path.join(directory, "mission-target.json"), os.path.join(directory, "mission-result.json")
    for stale in (path, result):
        if os.path.exists(stale):
            os.unlink(stale)
    document = tb.mission_target_document(mission_id=MISSION_ID, target_id=target_id, run_id=run_id, targets=targets,
                                          capability=capability_block(directory) if carried else None, result_path=result, radii=radii)
    return path, tb.write_mission_target(path, document)


def second_execution(directory: str) -> tuple:
    """Target B (capability) after A (no capability) was placed at A_PLACED."""
    return write_target(directory, "cube-b", [declared("cube-a", False, A_START, A_PLACED), declared("cube-b", True, B_START)])


def first_execution(directory: str) -> tuple:
    return write_target(directory, "cube-a", [declared("cube-a", False), declared("cube-b", True)])


class MissionWorld(differential.World):
    """The scripted World with more than one cube on the floor. `floor` lists them as the recognizer ranks them: the first is
    the one the frozen record names, every one is in the cube listing. `carried` is the one the jaws hold; once released it
    lies at cube_final_pad_m (or the EXIT listing is `after_release`, when a test scripts it whole). Pregrasp views report
    `candidates_verified`. The detector can deliver the operator's Ctrl-C while it runs, and so can the first closing
    gripper tick. Every capture is noted with the number of commands sent before it."""

    def __init__(self, **script):
        super().__init__(**{"floor": [{"pad_m": B_START, "contact_px": CONTACT}], "carried": 0, "candidates_verified": 1, "after_release": None,
                            "signal_at_detect": None, "signal_at_first_close": False, "enumeration": None, "relay_refusal": None, "listing_raises_at": None, "pass_limit": False, **script})
        self.detects, self.captures, self.enumerated, self.detector_types = 0, [], [], []
        self.signalled_at, self.result_path, self.stage_at_first_send, self.relay_pulses = None, None, "no command was sent", []

    def floor_now(self) -> list:
        if self.released and self.script["after_release"] is not None:
            return [dict(cube) for cube in self.script["after_release"]]
        cubes = [dict(cube) for cube in self.script["floor"]]
        if self.released:
            cubes[self.script["carried"]] = {**cubes[self.script["carried"]], "pad_m": list(self.script["cube_final_pad_m"])}
        return cubes

    def perception(self) -> dict:
        record = super().perception()
        if self.joints["shoulder_lift"] < -60.0:  # the low scan pose: the record names the recognizer's best cube
            floor = self.floor_now()
            record["cube"] = {**record["cube"], "contact_px": list(floor[0]["contact_px"]), "candidates_verified": len(floor)}
            record["cube_by_template"] = {"cube_center_pad_m": list(floor[0]["pad_m"]), "inside_pad_outline": inside(floor[0]["pad_m"])}
        else:
            record["cube"]["candidates_verified"] = self.script["candidates_verified"]
        return record

    def listing(self, image, detector, out_dir, name, max_passes=cube_candidates.MAX_PASSES) -> dict:
        """Stands in for cube_candidates.enumerate_observation: pass 1 IS the frozen record's cube (as the real one
        guarantees), every other floor cube follows."""
        self.enumerated.append({"name": name, "detector": detector, "out_dir": Path(out_dir), "shape": tuple(image.shape)})
        if self.script["listing_raises_at"] == len(self.enumerated):
            raise OSError("synthetic: the candidates sidecar cannot be written")
        record = json.loads((Path(out_dir) / f"{name}.json").read_text())
        if self.script["enumeration"] is not None:
            return {"schema": cube_candidates.SCHEMA, "observation_id": name, **self.script["enumeration"]}
        self.enumerated[-1]["max_passes"] = int(max_passes)
        cubes = [{"pass": 1, "cube": record["cube"], "cube_by_template": record.get("cube_by_template")}]
        for number, cube in enumerate(self.floor_now()[1:], start=2):
            cubes.append({"pass": number, "cube": {**record["cube"], "contact_px": list(cube["contact_px"])},
                          "cube_by_template": {"cube_center_pad_m": list(cube["pad_m"]), "inside_pad_outline": inside(cube["pad_m"])}})
        if not self.script["pass_limit"]:
            return {"schema": cube_candidates.SCHEMA, "observation_id": name, "refused": None, "complete": True, "cubes": cubes}
        # the real listing's pass limit: one pass per cube, then a pass that finds none; stopped at max_passes, complete false
        listed = cubes[:int(max_passes)]
        passes = [{"pass": entry["pass"], "result": "cube"} for entry in listed]
        if len(cubes) < int(max_passes):
            passes.append({"pass": len(cubes) + 1, "result": "no_verified_cube"})
        return {"schema": cube_candidates.SCHEMA, "observation_id": name, "refused": None, "complete": len(cubes) <= int(max_passes), "cubes": listed,
                "passes": passes}

    def apply(self, action: dict) -> None:
        if not self.sent and self.result_path:  # what the runtime could read at the moment the FIRST command left
            self.stage_at_first_send = json.loads(Path(self.result_path).read_text())["stage"] if os.path.exists(self.result_path) else None
        closing = "arm_gripper.pos" in action and float(action["arm_gripper.pos"]) < self.joints["gripper"] - 1e-9 and not self.closing
        super().apply(action)
        if closing and self.script["signal_at_first_close"] and self.signalled_at is None:
            self.signalled_at = (len(self.sent), len(self.captures), len(self.base_pulses))
            signal.raise_signal(signal.SIGINT)

    def move_base(self, vx_mps, duration_s) -> bool:
        if self.script["abort_in_pulse"] and self.signalled_at is None:  # the World delivers the stop inside the pulse
            self.signalled_at = (len(self.sent), len(self.captures), len(self.base_pulses) + 1)
        return super().move_base(vx_mps, duration_s)

    def detect(self) -> None:
        self.detects += 1
        if self.script["signal_at_detect"] == self.detects:
            self.signalled_at = (len(self.sent), len(self.captures), len(self.base_pulses))
            signal.raise_signal(signal.SIGINT)  # the operator's Ctrl-C arrives while the model is still busy with this frame

    def base_primitive(self, options: dict):
        """What make_move_base hands back. With a relay (`forward`), a stand-in that carries the relay's evidence and its
        refusal of the pulse just run, as make_move_base's own relayed dispatch does; without, the World's pulse as is."""
        if "forward" not in options:
            return self.move_base
        relay = type("RelayEvidence", (), {"pulses": self.relay_pulses})()

        def pulse(vx_mps, duration_s):
            pulse.last_refusal = None
            stopped = self.move_base(vx_mps, duration_s)
            if vx_mps > 0:
                self.relay_pulses.append({"slot_generation": 3, "producer_pid": 4321, "commands_accepted": 5, "commands_applied": 4,
                                          "commands_dropped": {"not_the_publication_just_made": 1}})
                pulse.last_refusal = self.script["relay_refusal"]
            return stopped

        pulse.capability, pulse.last_refusal = relay, None
        return pulse


class CountingCamera(differential.SyntheticCamera):
    def capture(self, role):
        self.world.captures.append(len(self.world.sent))
        return super().capture(role)


class ScriptedDetector:
    def __init__(self, world):
        self.world = world

    def detect(self, image, labels):
        self.world.detect()
        return []


def run_world(world: MissionWorld, directory: str, *, target_path=None, remote_ip=LOCAL, do="cycle", real_move_base=False, configured=False, events_path=None,
              runtime_mode="", extra_env=None) -> dict:
    """main() on the scripted World, as test_cycle_behavior_preserved.run_scripted_world runs it, with the mission seams
    scripted: KC_MISSION_TARGET (ABSENT when target_path is None), the cube listing, the detector. real_move_base keeps the
    controller's own make_move_base (the relay a capability target builds is then the real one)."""
    observations = os.path.join(directory, "observations")
    shutil.rmtree(observations, ignore_errors=True)
    scan_cli, made = current.cli, []

    def perceive(role, image, out_dir, name, detector):
        world.detector_types.append(type(detector).__name__)
        detector.detect(image, ("a cube",))
        record = world.perception()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.json").write_text(json.dumps(record))
        return [f"{role.upper()}: synthetic perception"]

    def move_base_factory(remote_ip, robot_id, **options):
        made.append(options)
        return world.base_primitive(options)

    environment = {"KC_RUNTIME_CONFIG": os.path.join(directory, "runtime-config.json") if configured else "", "KC_EVENTS_PATH": events_path or "", "KC_RUN_ID": "",
                   "KC_ARTIFACT_DIGEST": "", "KC_HOST_LEASE": "", "KC_HOST_SESSION": "", "KC_RUNTIME_MODE": runtime_mode,
                   "KC_PLACEMENT_ENVELOPE": "", "KC_PLACEMENT_ENVELOPE_ATTENDED": "", **(extra_env or {}),
                   **({} if target_path is None else {"KC_MISSION_TARGET": target_path})}
    patches = [mock.patch.object(current, "time", world.clock),
               mock.patch.object(current, "make_bounded_move_arm", lambda read_pose, send: make_bounded_move_arm(read_pose, send, sleep=world.clock.sleep, monotonic=world.clock.perf_counter)),
               mock.patch.object(scan_cli, "now_utc", world.clock.now_utc), mock.patch.object(scan_cli, "PiCamera", lambda ssh_target, local_dir: CountingCamera(world)),
               mock.patch.object(scan_cli, "perceive", perceive), mock.patch.object(scan_cli, "make_detector", lambda: ScriptedDetector(world)),
               mock.patch.object(cube_candidates, "enumerate_observation", world.listing),
               mock.patch.dict(sys.modules, differential.synthetic_lerobot(world)), mock.patch.dict(os.environ, environment)]
    if not real_move_base:
        patches.append(mock.patch.object(current, "make_move_base", move_base_factory))
    if not configured:
        patches += [mock.patch.object(current, "ObservationStore", functools.partial(ObservationStore, root_dir=observations)),
                    mock.patch.object(current, "HOME_FILE", Path(directory) / "calibration" / "scan-home-current.json")]
    handler, stdout, stderr = signal.getsignal(signal.SIGINT), io.StringIO(), io.StringIO()
    try:
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            if target_path is None:
                os.environ.pop("KC_MISSION_TARGET", None)  # absent, not empty: the patch above puts back whatever was there
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            try:
                ending = ("returned", current.main(["--do", do, "--remote-ip", remote_ip, "--robot-id", "synthetic-unit", "--pi-ssh", "synthetic-operator@" + ELSEWHERE, "--scan-id", SCAN_ID]))
            except SystemExit as leaving:
                ending = ("SystemExit", leaving.code)
            except Exception as error:  # noqa: BLE001 -- a traceback is an ending too, and must fail the assertion that reads it
                ending = (type(error).__name__, str(error))
    finally:
        signal.signal(signal.SIGINT, handler)
        if configured:
            current.use_runtime_paths(current.cli.runtime_paths({}))
    records = sorted(Path(observations, SCAN_ID).glob("cycle-*.json")) if os.path.isdir(os.path.join(observations, SCAN_ID)) else []
    transitions = json.loads(records[-1].read_text()) if records else []
    result = json.loads(Path(world.result_path).read_text()) if world.result_path and os.path.exists(world.result_path) else None
    return {"ending": ending, "stdout": stdout.getvalue(), "stderr": stderr.getvalue(), "sent": world.sent, "base_pulses": world.base_pulses, "captures": world.captures,
            "states": [entry["state"] for entry in transitions], "transitions": transitions, "result": result, "made": made, "disconnects": world.disconnects}


def stand_in_source(test) -> None:
    """Nothing is stood in any more: under contract v2 target_observation_source.capability_observation_available answers
    (True, None) itself, which is what this used to patch in. Kept, as a no-op, because autonomy/tests/test_cycle_remote_perception.py
    calls it; it can go with those calls."""
    del test


class MissionCase(unittest.TestCase):
    """Every run below asks the REAL target_observation_source: its availability answer is (True, None) under contract v2,
    so a capability target's run passes the SEARCH gate on its own, and the path after it is what the executor does."""

    def setUp(self):
        self.directory = fixtures.use_synthetic_runtime(self)
        current.use_runtime_paths(current.cli.runtime_paths({}))  # a run starts from the legacy layout and says itself when it is configured

    def mission(self, world: MissionWorld, writer, **options) -> tuple:
        path, target = writer(self.directory)
        world.result_path = target.result_path
        return target, run_world(world, self.directory, target_path=path, **options)

    def assertEnded(self, run, status):
        self.assertEqual(("returned", status), run["ending"], run["stdout"][-3000:])

    def gripper_closed(self, run) -> bool:
        return any(float(action["arm_gripper.pos"]) < current.GRIPPER_OPEN - current.GRIPPER_OPEN_TOLERANCE for action in run["sent"] if "arm_gripper.pos" in action)


def placed_first_world(**script) -> MissionWorld:
    """The second execution's scene: A already on the pad (the recognizer's best cube, the one the frozen record names), B
    outside it, where the World's base-advance scenario has its cube."""
    return MissionWorld(**{"floor": [{"pad_m": A_PLACED, "contact_px": [700.0, 330.0]}, {"pad_m": B_START, "contact_px": CONTACT}], "carried": 1, **script})


class TheSecondExecutionCarriesTheUnresolvedCube(MissionCase):
    def test_the_legacy_cycle_on_this_scene_finishes_on_the_placed_cube_which_is_the_hazard(self):
        run = run_world(placed_first_world(), self.directory)
        self.assertEnded(run, 0)
        self.assertEqual("DONE_ALREADY_PLACED", run["states"][-1])
        self.assertEqual([], run["sent"])

    def test_selection_passes_the_placed_cube_and_places_the_bound_one(self):
        world = placed_first_world()
        target, run = self.mission(world, second_execution)
        self.assertEnded(run, 0)
        self.assertNotIn("DONE_ALREADY_PLACED", run["states"])
        selected = run["transitions"][run["states"].index("TARGET_SELECTED")]
        self.assertEqual((B_START, 2, "BASE_ADVANCE"), (selected["seen_pad_m"], selected["listed_pass"], selected["mode"]))
        order = [run["states"].index(name) for name in ("SEARCH", "TARGET_SELECTED", "PREGRASP", "OBJECT_SECURED", "BASE_ADVANCE", "RELEASED", "PLACED", "BASE_RETURNED")]
        self.assertEqual(sorted(order), order)
        placed = run["transitions"][run["states"].index("PLACED")]
        self.assertTrue(placed["value"] and placed["attributed"])
        self.assertEqual(2, placed["on_pad_cubes"])  # A (ignored) and B
        self.assertLessEqual(placed["release_distance_mm"], placed["release_tolerance_mm"])
        self.assertEqual(B_FINAL, placed["cube_pad_m"])  # the attributed cube, not the record's (which names A)
        self.assertTrue(run["base_pulses"])
        result = run["result"]
        self.assertEqual(result, tb.parse_result(result, target))
        self.assertEqual(("finished", "placed_new", None, "BASE_RETURNED"), (result["stage"], result["outcome"], result["reason_code"], result["final_state"]))
        self.assertEqual({"object_secured": True, "placed": True, "base_returned": True}, result["predicates"])
        self.assertIsNone(result["bindings"])  # a later execution carries the bindings of the first, it does not make new ones
        forward = len(world.relay_pulses)
        self.assertGreater(forward, 0)
        self.assertEqual({"generation": 3, "pid": 4321, "accepted": 5 * forward, "applied": 4 * forward, "dropped": {"not_the_publication_just_made": forward}}, result["capability"])
        self.assertEqual(["forward"], [name for name in run["made"][0] if name == "forward"], "the capability target's forward half is the relay")
        self.assertTrue(tb.result_claims_placement(result, target))
        self.assertEqual(B_FINAL, tb.targets_after_placement(target, result)[1]["placed_pad_m"])

    def test_the_bound_stage_is_on_disk_before_the_first_command(self):
        world = placed_first_world()
        _, run = self.mission(world, second_execution)
        self.assertEnded(run, 0)
        self.assertEqual("bound", world.stage_at_first_send)
        self.assertEqual(["CubeDetectionCache"], sorted(set(world.detector_types)))  # the model's proposals are kept for the listing
        self.assertEqual(2, len(world.enumerated), "the cubes are listed at SEARCH and at the EXIT view, and nowhere else")

    def test_with_events_on_the_hooks_state_the_mission_run_without_a_complaint(self):
        events_path = os.path.join(self.directory, "mission-events.jsonl")
        _, run = self.mission(placed_first_world(), second_execution, configured=True, events_path=events_path)
        self.assertEnded(run, 0)
        self.assertEqual("", run["stderr"])
        self.assertIn("TARGET_SELECTED", Path(events_path).read_text())

    def test_a_scene_with_only_the_placed_cube_is_a_refusal_never_done_already_placed(self):
        target, run = self.mission(MissionWorld(floor=[{"pad_m": A_PLACED, "contact_px": CONTACT}]), second_execution)
        self.assertEnded(run, 5)
        self.assertEqual([], run["sent"])
        self.assertNotIn("DONE_ALREADY_PLACED", run["states"])
        self.assertEqual(("finished", "failed", tb.REFUSED_TARGET_NOT_FOUND, "SEARCH"), (run["result"]["stage"], run["result"]["outcome"], run["result"]["reason_code"], run["result"]["final_state"]))
        self.assertFalse(tb.result_claims_placement(run["result"], target))

    def test_an_earlier_target_taken_off_the_pad_never_fails_the_run(self):
        # CHANGED by the pad policy of 2026-09-29 ("Human can subtract blocks from the pad"): this scene ended
        # mission_earlier_target_disturbed; the run is PLACED on its own block at its own release.
        world = placed_first_world(after_release=[{"pad_m": B_FINAL, "contact_px": CONTACT}])  # A is gone from the pad
        target, run = self.mission(world, second_execution)
        self.assertEnded(run, 0)
        self.assertIn("BASE_RETURNED", run["states"])
        self.assertEqual(("placed_new", None), (run["result"]["outcome"], run["result"]["reason_code"]))
        self.assertEqual({"object_secured": True, "placed": True, "base_returned": True}, run["result"]["predicates"])
        self.assertEqual((True, B_FINAL, 1), (run["result"]["placement"]["placed"], run["result"]["placement"]["placed_pad_m"], run["result"]["placement"]["on_pad_count"]))
        self.assertTrue(tb.result_claims_placement(run["result"], target))

    def test_a_listing_that_did_not_run_out_of_cubes_refuses_before_anything_is_picked_up(self):
        incomplete = {"refused": None, "complete": False, "cubes": [{"pass": 1, "cube": {"contact_px": CONTACT}, "cube_by_template": {"cube_center_pad_m": B_START, "inside_pad_outline": False}}]}
        _, run = self.mission(placed_first_world(enumeration=incomplete), second_execution)
        self.assertEnded(run, 5)
        self.assertEqual([], run["sent"])
        self.assertEqual("mission_candidates_incomplete", run["result"]["reason_code"])


class TheFirstExecutionBindsEveryTarget(MissionCase):
    def world(self) -> MissionWorld:
        return MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}, {"pad_m": B_START, "contact_px": B_CONTACT_BESIDE_A}], carried=0,
                            cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)

    def test_the_scene_is_what_the_test_says_one_arm_only_cube_and_one_base_advance_cube(self):
        pan = fixtures.SYNTHETIC_LOW_POSE["shoulder_pan"]
        for centre, contact, mode in ((A_START, CONTACT, "ARM_ONLY"), (B_START, B_CONTACT_BESIDE_A, "BASE_ADVANCE")):
            coarse = current.coarse_pregrasp_from_low_pose(contact, pan)
            self.assertEqual(mode, current.placement_plan(centre, current.reach_direction_pad(fixtures._pad_view()["homography_image_to_pad"], contact), max(coarse["cube_out_mm"], 0.0))["mode"])

    def test_both_targets_are_bound_the_arm_only_one_is_placed_and_the_base_never_moves(self):
        world = self.world()
        target, run = self.mission(world, first_execution)
        self.assertEnded(run, 0)
        self.assertEqual("bound", world.stage_at_first_send)
        self.assertEqual([], run["made"], "a target without the capability is given no base primitive at all")
        self.assertEqual([], run["base_pulses"])
        result = run["result"]
        self.assertEqual([("cube-a", A_START, "ARM_ONLY"), ("cube-b", B_START, "BASE_ADVANCE")], [(b["target_id"], b["bound_pad_m"], b["mode"]) for b in result["bindings"]])
        self.assertEqual("placed_new", result["outcome"])
        self.assertIsNone(result["capability"])
        self.assertTrue(tb.result_claims_placement(result, target))
        following = tb.targets_after_placement(target, result)
        self.assertEqual([A_START, B_START], [entry["bound_pad_m"] for entry in following])
        self.assertEqual([A_FINAL, None], [entry["placed_pad_m"] for entry in following])

    def test_a_target_without_the_capability_whose_cube_measures_base_advance_is_refused(self):
        """D5. A later execution of a no-capability target, bound where its cube now needs the base."""
        world = MissionWorld(floor=[{"pad_m": B_START, "contact_px": CONTACT}])
        writer = functools.partial(write_target, target_id="cube-a", targets=[declared("cube-b", True, [0.10, 0.05], [0.10, 0.05]), declared("cube-a", False, B_START)])
        _, run = self.mission(world, lambda directory: writer(directory))
        self.assertEnded(run, 5)
        self.assertEqual(([], [], []), (run["sent"], run["base_pulses"], run["made"]))
        self.assertEqual(tb.REFUSED_MODE_MISMATCH, run["result"]["reason_code"])


class ALIGNNeedsOneCubeInView(MissionCase):
    def test_two_verified_cubes_in_the_pregrasp_view_refuse_before_any_contact_motion(self):
        _, run = self.mission(placed_first_world(candidates_verified=2), second_execution)
        self.assertEnded(run, 5)
        self.assertEqual("PREGRASP", run["states"][-1])
        self.assertNotIn("ALIGN", run["states"])
        self.assertFalse(self.gripper_closed(run))
        self.assertEqual([], run["base_pulses"])
        self.assertEqual("align_more_than_one_cube_verified", run["result"]["reason_code"])

    def test_a_view_that_does_not_record_the_count_is_refused_as_well(self):
        _, run = self.mission(placed_first_world(candidates_verified=None), second_execution)
        self.assertEnded(run, 5)
        self.assertEqual("align_cube_count_not_recorded", run["result"]["reason_code"])

    def test_the_legacy_cycle_does_not_look_at_the_count(self):
        run = run_world(MissionWorld(candidates_verified=2), self.directory)
        self.assertEnded(run, 0)
        self.assertIn("BASE_RETURNED", run["states"])


class TheStop(MissionCase):
    def test_a_stop_during_a_perception_ends_the_run_and_its_late_result_starts_nothing(self):
        # The SEARCH frame; then the first and the LAST of the two frames the PREGRASP move captures. After the last one, only the
        # late-result gate stands between that frame and the ALIGN decision (after the others, the next perception's gate does).
        for detect in (1, 2, 3):
            with self.subTest(signal_at_detect=detect):
                world = placed_first_world(signal_at_detect=detect)
                _, run = self.mission(world, second_execution)
                self.assertEnded(run, 4)
                sent, captured, pulsed = world.signalled_at
                self.assertEqual((sent, captured, pulsed), (len(run["sent"]), len(run["captures"]), len(run["base_pulses"])), "something was sent, captured or pulsed after the stop")
                self.assertNotIn("ALIGN", run["states"])
                self.assertEqual(("stopped", "operator_abort_perception"), (run["result"]["outcome"], run["result"]["reason_code"]))
                if detect == 1:
                    self.assertEqual(([], "SEARCH"), (run["sent"], run["states"][-1]))
                    self.assertIsNone(run["result"]["selection"], "a selection taken on a late result would be acted on")

    def test_the_legacy_cycle_plans_on_that_late_result_which_is_what_the_gate_removes(self):
        world = placed_first_world(signal_at_detect=2, floor=[{"pad_m": B_START, "contact_px": CONTACT}], carried=0)
        run = run_world(world, self.directory)
        self.assertEnded(run, 4)
        self.assertIn("ALIGN", run["states"])  # decided on the frame that finished after the stop
        self.assertEqual(world.signalled_at[0], len(run["sent"]))  # its motion entry then refused on its own: nothing was sent either way

    def test_an_aborted_gripper_move_is_not_followed_by_an_observation(self):
        world = placed_first_world(signal_at_first_close=True)
        _, run = self.mission(world, second_execution)
        self.assertEnded(run, 4)
        sent, captured, pulsed = world.signalled_at
        self.assertEqual(captured, len(run["captures"]), "the aborted grip was followed by a capture")
        self.assertEqual(sent, len(run["sent"]))
        self.assertEqual(("stopped", "operator_abort_gripper"), (run["result"]["outcome"], run["result"]["reason_code"]))
        legacy_world = MissionWorld(signal_at_first_close=True)
        legacy = run_world(legacy_world, self.directory)
        self.assertGreater(len(legacy["captures"]), legacy_world.signalled_at[1], "not vacuous: the legacy grip observes after its abort")

    def test_the_read_only_re_observation_after_a_base_pulse_still_happens_after_a_stop(self):
        world = placed_first_world(abort_in_pulse=True)
        _, run = self.mission(world, second_execution)
        self.assertEnded(run, 5)
        sent, captured, pulsed = world.signalled_at  # taken as the pulse delivered the stop
        self.assertEqual((1, 1), (pulsed, len(run["base_pulses"])))
        self.assertEqual(captured + 1, len(run["captures"]), "exactly the one re-observation followed the pulse")
        self.assertEqual(sent, len(run["sent"]), "a command followed the stop")
        self.assertEqual("BASE_MEASURED", run["states"][-1])
        self.assertEqual(("stopped", "operator_abort_base_path"), (run["result"]["outcome"], run["result"]["reason_code"]))

    def test_without_the_exemption_the_stop_would_refuse_that_re_observation(self):
        """Not vacuous: the re-observation above runs BECAUSE of _reobserve_after_pulse, not because the gate missed it."""
        with mock.patch.object(current.Cycle, "_reobserve_after_pulse", lambda self, holding: self.observe(holding)):
            _, run = self.mission(placed_first_world(abort_in_pulse=True), second_execution)
        self.assertEnded(run, 4)
        self.assertNotIn("BASE_MEASURED", run["states"])
        self.assertEqual("operator_abort_perception", run["result"]["reason_code"])


class TheCapabilityReplacesTheInProcessController(MissionCase):
    def test_make_move_base_with_a_relay_never_imports_the_proven_forward_primitive(self):
        world, relayed = differential.World(), []

        class Relay:
            last_refusal, pulses = None, []

            def __call__(self, vx_mps, duration_s):
                relayed.append((vx_mps, duration_s))
                self.last_refusal = "no_capability_output_applied"
                return True

        def construct(*_args, **_kwargs):
            raise AssertionError("the in-process MobileApproachController was constructed")

        relay = Relay()
        with mock.patch.dict(sys.modules, {**differential.synthetic_lerobot(world), "run_hybrid_pick_place": None}), \
                mock.patch.object(mobile_approach, "MobileApproachController", construct), mock.patch.object(current, "time", world.clock):
            move_base = current.make_move_base(LOCAL, "synthetic-unit", forward=relay)
            self.assertTrue(move_base(current.BASE_VX_MPS, current.BASE_PULSE_S[0]))
            self.assertEqual(("no_capability_output_applied", relay), (move_base.last_refusal, move_base.capability))
            self.assertTrue(move_base(-current.BASE_VX_MPS, current.BASE_PULSE_S[0]), "the proven reverse primitive did not confirm its stop")
            self.assertIsNone(move_base.last_refusal)
            with self.assertRaises(ImportError):  # not vacuous: without a relay the proven primitive IS imported, and here it cannot be
                current.make_move_base(LOCAL, "synthetic-unit")
        self.assertEqual([(current.BASE_VX_MPS, current.BASE_PULSE_S[0])], relayed)
        self.assertTrue(world.sent and all(action["x.vel"] < 0 for action in world.sent if action["x.vel"] != 0.0), "the reverse pulse sent a forward velocity")

    def test_a_capability_target_builds_the_real_relay_and_a_relay_that_refuses_ends_the_cycle_with_the_cube_held(self):
        built = []
        real = current.make_capability_relay

        def construct(*_args, **_kwargs):
            raise AssertionError("the in-process MobileApproachController was constructed")

        def building(target, robot_id, transport_options, should_stop, observe, motion_epoch):
            built.append(real(target, robot_id, transport_options, should_stop=should_stop, observe=observe, motion_epoch=motion_epoch))
            return built[-1]

        world = placed_first_world()
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}), mock.patch.object(mobile_approach, "MobileApproachController", construct), \
                mock.patch.object(current, "make_capability_relay", building):
            target, run = self.mission(world, second_execution, real_move_base=True)
        self.assertEnded(run, 5)
        self.assertEqual(1, len(built))
        relay = built[0]
        endpoints = target.capability
        self.assertEqual((endpoints.slot_uuid, endpoints.capsule, endpoints.observation_endpoint, endpoints.command_endpoint, "cube-b"),
                         (relay.slot_uuid, relay.capsule, relay.observations, relay.commands, relay.target_id))
        self.assertEqual([capability_pulse.OBSERVATION_UNAVAILABLE], [pulse["refusal"] for pulse in relay.pulses],
                         "the relay was given the controller's own source, which has no observation for a run without a perception budget, and refused once, before any read")
        self.assertEqual([tos.REASON_PERCEPTION_BUDGET_UNKNOWN], relay.pulses[0]["refusal_detail"].split(","),
                         "the approach view, the stationary capture and the selection were all there: the one missing requirement is the budget")
        self.assertIn("BASE_ADVANCE", run["states"])
        self.assertTrue(self.gripper_closed(run))
        self.assertNotIn("RELEASED", run["states"], "the cube stays held")
        self.assertFalse([action for action in run["sent"] if action.get("x.vel", 0.0) != 0.0], "a base velocity was sent")
        self.assertEqual(("failed", "base_capability_output_refused"), (run["result"]["outcome"], run["result"]["reason_code"]))
        self.assertIsNone(run["result"]["capability"], "no pulse read the capability's slot, so there is no generation to state")
        with mock.patch.dict(sys.modules, differential.synthetic_lerobot(MissionWorld())):
            transport = relay._transport_factory()
        self.assertEqual((LOCAL, "synthetic-unit"), (transport._client.config.remote_ip, transport._client.config.id))

    def test_a_stop_while_the_real_relay_reads_the_capability_commands_no_pulse(self):
        """The relay's Cake reads and link wait take seconds after base_pulse checked the flag. The operator's stop arrives
        during the reads: the REAL relay, given this run's own flag by _run, builds no transport, and the run ends stopped
        with the cube held."""
        built, transports = [], []
        real = current.make_capability_relay

        def building(target, robot_id, transport_options, should_stop, observe, motion_epoch):
            relay = real(target, robot_id, transport_options, should_stop=should_stop, observe=observe, motion_epoch=motion_epoch)
            # A stand-in observation, so that the relay gets as far as its Cake reads (the World's run has no perception budget).
            relay._observe = v2_stand_in(target.target_id, relay._clock, motion_epoch)
            endpoints = target.capability
            answers = {"list-slots": f"status ok\nslot capsule={endpoints.capsule} config={endpoints.capsule} generation=3 instance=7 name=mobile_approach state=ACTIVE uuid={endpoints.slot_uuid}\n",
                       "get-health": f"status ok\nhealth arg0=6 arg1=4321 code=0 flags=1 sequence=2 slot={endpoints.slot_uuid} state=1 state_name=HEALTHY surface=reported\n"}

            def read(op):  # admin-probe answers ACTIVE with a launched pid, and the Ctrl-C arrives while it does
                if op == "get-health":
                    signal.raise_signal(signal.SIGINT)
                return answers[op]

            relay._read = read
            relay._transport_factory = lambda: transports.append("built") or self.fail("a transport was built after the stop")
            built.append(relay)
            return relay

        world = placed_first_world()
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}), mock.patch.object(current, "make_capability_relay", building):
            _, run = self.mission(world, second_execution, real_move_base=True)
        self.assertEnded(run, 5)
        self.assertEqual(([], 1), (transports, len(built)))
        self.assertEqual([capability_pulse.STOPPED_BEFORE_MOTION], [pulse["refusal"] for pulse in built[0].pulses])
        self.assertFalse([action for action in run["sent"] if action.get("x.vel", 0.0) != 0.0], "a base velocity was sent")
        self.assertTrue(self.gripper_closed(run))
        self.assertNotIn("RELEASED", run["states"])
        self.assertNotIn("BASE_MEASURED", run["states"])
        self.assertEqual(("stopped", "operator_abort_before_capability_pulse"), (run["result"]["outcome"], run["result"]["reason_code"]))

    def test_the_relays_stop_refusal_is_the_literal_the_controller_reads(self):
        self.assertEqual(capability_pulse.STOPPED_BEFORE_MOTION, current.RELAY_STOPPED_BEFORE_MOTION)

    def test_a_relay_that_refuses_after_a_clean_stop_ends_the_cycle_with_the_cube_held(self):
        _, run = self.mission(placed_first_world(relay_refusal="capability_changed_during_pulse"), second_execution)
        self.assertEnded(run, 5)
        self.assertEqual(1, len(run["base_pulses"]))
        self.assertNotIn("RELEASED", run["states"])
        self.assertEqual("base_capability_output_refused", run["result"]["reason_code"])

    def test_a_capability_that_changed_between_pulses_is_refused(self):
        world = placed_first_world()
        original = world.base_primitive

        def changing(options):
            pulse = original(options)
            inner = pulse

            def second_generation(vx_mps, duration_s):
                stopped = inner(vx_mps, duration_s)
                if len(world.relay_pulses) == 2:
                    world.relay_pulses[-1]["slot_generation"] = 4
                second_generation.last_refusal = inner.last_refusal
                return stopped

            second_generation.capability, second_generation.last_refusal = inner.capability, None
            return second_generation

        world.base_primitive = changing
        _, run = self.mission(world, second_execution)
        self.assertEnded(run, 5)
        self.assertEqual(2, len(run["base_pulses"]))
        self.assertEqual("base_capability_output_refused", run["result"]["reason_code"])
        self.assertIn("capability_changed_between_pulses", run["stdout"])


def assert_refused_at_search(test, run, world, code) -> dict:
    """The run ended at SEARCH with `code`, after the selection (mode BASE_ADVANCE) was written back and before anything
    moved: the shape the runtime recognizes as the measuring run's refusal (mission_execution._search_measured_capability)."""
    test.assertEnded(run, 5)
    test.assertEqual(([], []), (run["sent"], run["base_pulses"]), "something moved")
    test.assertEqual("no command was sent", world.stage_at_first_send)
    test.assertNotIn("PREGRASP", run["states"])
    result = run["result"]
    test.assertEqual(("finished", "failed", code), (result["stage"], result["outcome"], result["reason_code"]))
    test.assertEqual(_assigned(MISSION_EXECUTION, "SEARCH_REFUSAL_FINAL_STATE"), result["final_state"])
    test.assertEqual(tb.MODE_BASE_ADVANCE, result["selection"]["mode"], "the selection was written back first")
    test.assertEqual({"object_secured": None, "placed": None, "base_returned": None}, result["predicates"])
    test.assertIsNone(result["placement"])
    return result


class TheCapabilityFollowsTheMeasurement(MissionCase):
    """The need for the capability is decided on what SEARCH measured, after the selection is written back and before
    anything moves; the availability question is the REAL source's answer."""

    def prestaged_scene(self, **script) -> MissionWorld:
        """The operator's intended scene: the first cube already on the pad, the gripper empty; the mission carries the
        second, whose cube measures BASE_ADVANCE (the World's base-advance scenario)."""
        return placed_first_world(**script)

    def assert_refused_at_search(self, run, world, code):
        return assert_refused_at_search(self, run, world, code)

    def test_a_run_that_carries_no_capability_refuses_the_cube_that_measures_base_advance(self):
        world = self.prestaged_scene()
        target, run = self.mission(world, lambda d: write_target(d, "cube-b", [declared("cube-b", True)], carry=False))
        self.assertFalse(target.carries_capability)
        result = self.assert_refused_at_search(run, world, _assigned(MISSION_EXECUTION, "CAPABILITY_REQUIRED"))
        self.assertEqual("mission_capability_required", result["reason_code"])
        self.assertEqual([("cube-b", B_START, tb.MODE_BASE_ADVANCE)], [(b["target_id"], b["bound_pad_m"], b["mode"]) for b in result["bindings"]])
        self.assertEqual([], run["made"], "a run that carries no capability is given no base primitive at all")
        selected = run["transitions"][run["states"].index("TARGET_SELECTED")]
        # CHANGED by the pad policy of 2026-09-29: the block on the pad is no longer bookkept as occupied, only counted as ignored
        self.assertNotIn("occupied_pad_m", selected)
        self.assertEqual((1, 1), (selected["candidates"], selected["on_pad_ignored"]))

    def test_a_run_that_carries_it_passes_the_gate_and_its_observation_is_decided_per_pulse(self):
        """Under contract v2 the source answers that a mapping exists, so the gate passes; the World perceives here and has
        no perception budget, so the first pulse's resolve refuses (perception_budget_unknown) and the cycle ends with the
        cube held, after the base advance began and before any base velocity was sent."""
        self.assertEqual((True, None), tos.capability_observation_available())
        world = self.prestaged_scene()
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
            target, run = self.mission(world, second_execution, real_move_base=True)
        self.assertTrue(target.carries_capability)
        self.assertEnded(run, 5)
        self.assertIn("PREGRASP", run["states"])
        self.assertIn("BASE_ADVANCE", run["states"])
        self.assertNotIn("mission_capability_observation_unavailable", run["stdout"])
        self.assertIn(tos.REASON_PERCEPTION_BUDGET_UNKNOWN, run["stdout"])
        self.assertEqual(("failed", "base_capability_output_refused"), (run["result"]["outcome"], run["result"]["reason_code"]))
        self.assertFalse([action for action in run["sent"] if action.get("x.vel", 0.0) != 0.0], "a base velocity was sent")
        self.assertNotIn("RELEASED", run["states"], "the cube stays held")

    def test_a_source_that_is_unavailable_still_refuses_at_search(self):
        """The refusal path stays: a source that answers False (stood in here) refuses the run at SEARCH, before PREGRASP."""
        world = self.prestaged_scene()
        with mock.patch.object(tos, "capability_observation_available", lambda: (False, "stood_in_unavailable")):
            target, run = self.mission(world, second_execution)
        self.assertTrue(target.carries_capability)
        self.assert_refused_at_search(run, world, "mission_capability_observation_unavailable")
        self.assertIn("stood_in_unavailable", run["stdout"])

    def test_the_arm_only_target_needs_no_capability_and_is_not_asked(self):
        asked = []
        with mock.patch.object(tos, "capability_observation_available", lambda: asked.append(1) or (False, "never")):
            _, run = self.mission(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL),
                                  lambda d: write_target(d, "cube-a", [declared("cube-a", False)]))
        self.assertEnded(run, 0)
        self.assertEqual([], asked)

    def test_a_file_without_radii_is_refused_before_the_robot_exists(self):
        path, _ = write_target(self.directory, "cube-a", [declared("cube-a", False)], radii=None)
        run = run_world(MissionWorld(), self.directory, target_path=path)
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))
        self.assertIn("claim and match radii", run["stdout"])


class TheAutoTargetTakesTheMeasuredMode(MissionCase):
    """requires_capability "auto" on the scripted World: one block, and SEARCH's own measurement chooses the branch."""

    @staticmethod
    def auto_target(directory, carry=False):
        return write_target(directory, "cube", [declared("cube", tb.AUTO)], carry=carry)

    def test_a_block_within_arm_reach_runs_the_proven_arm_only_cycle_and_the_base_never_moves(self):
        asked = []
        world = MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)
        with mock.patch.object(tos, "capability_observation_available", lambda: asked.append(1) or (False, "never")):
            target, run = self.mission(world, self.auto_target)
        self.assertTrue(target.auto)
        self.assertFalse(target.carries_capability)
        self.assertEnded(run, 0)
        self.assertEqual("bound", world.stage_at_first_send)
        self.assertEqual(([], [], []), (run["made"], run["base_pulses"], asked), "no base primitive, no pulse, no capability question")
        self.assertNotIn(tb.REFUSED_MODE_MISMATCH, run["stdout"])
        result = run["result"]
        self.assertEqual([("cube", A_START, tb.MODE_ARM_ONLY)], [(b["target_id"], b["bound_pad_m"], b["mode"]) for b in result["bindings"]])
        self.assertEqual((tb.MODE_ARM_ONLY, "placed_new", None), (result["selection"]["mode"], result["outcome"], result["capability"]))
        self.assertTrue(tb.result_claims_placement(result, target))
        self.assertEqual([{"target_id": "cube", "requires_capability": tb.AUTO, "effective_mode": tb.MODE_ARM_ONLY, "bound_pad_m": A_START, "placed_pad_m": A_FINAL}],
                         tb.targets_after_placement(target, result))
        selected = run["transitions"][run["states"].index("TARGET_SELECTED")]
        self.assertEqual(("cube", tb.MODE_ARM_ONLY), (selected["target_id"], selected["mode"]))

    def test_a_block_beyond_arm_reach_in_a_run_without_the_capability_is_the_same_search_refusal(self):
        world = MissionWorld()  # the World's base-advance scenario: one cube at B_START
        target, run = self.mission(world, self.auto_target)
        self.assertFalse(target.carries_capability)
        result = assert_refused_at_search(self, run, world, _assigned(MISSION_EXECUTION, "CAPABILITY_REQUIRED"))
        self.assertEqual("mission_capability_required", result["reason_code"])
        self.assertEqual([("cube", B_START, tb.MODE_BASE_ADVANCE)], [(b["target_id"], b["bound_pad_m"], b["mode"]) for b in result["bindings"]])
        self.assertEqual([], run["made"])
        self.assertNotIn(tb.REFUSED_MODE_MISMATCH, run["stdout"], "no D5 refusal: the auto target takes the measured mode")

    def test_a_block_beyond_arm_reach_in_a_run_that_carries_the_capability_is_driven_as_a_capability_run(self):
        self.assertEqual((True, None), tos.capability_observation_available())
        world = MissionWorld()
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
            target, run = self.mission(world, lambda d: self.auto_target(d, carry=True), real_move_base=True)
        self.assertTrue(target.auto and target.carries_capability)
        self.assertEnded(run, 5)
        self.assertIn("PREGRASP", run["states"])
        self.assertIn("BASE_ADVANCE", run["states"], "the base advance began through the relay")
        self.assertIn(tos.REASON_PERCEPTION_BUDGET_UNKNOWN, run["stdout"])  # the World perceives here and has no budget: the first pulse refuses
        self.assertEqual(("failed", "base_capability_output_refused", tb.MODE_BASE_ADVANCE), (run["result"]["outcome"], run["result"]["reason_code"], run["result"]["selection"]["mode"]))
        self.assertFalse([action for action in run["sent"] if action.get("x.vel", 0.0) != 0.0], "a base velocity was sent")
        self.assertNotIn("RELEASED", run["states"], "the cube stays held")

    def test_a_scene_with_two_blocks_binds_the_nearest_feasible_one(self):
        # CHANGED by the pad policy of 2026-09-29 (THE SELECTION RULE): this scene was refused mission_binding_candidate_not_unique.
        world = MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}, {"pad_m": B_START, "contact_px": B_CONTACT_BESIDE_A}], carried=0,
                             cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)
        target, run = self.mission(world, self.auto_target)
        self.assertEnded(run, 0)
        self.assertEqual("bound", world.stage_at_first_send)
        self.assertEqual([("cube", A_START, tb.MODE_ARM_ONLY)], [(b["target_id"], b["bound_pad_m"], b["mode"]) for b in run["result"]["bindings"]])
        self.assertTrue(tb.result_claims_placement(run["result"], target))


class SweepWorld(MissionWorld):
    """The scripted World with the block in ONE sweep sector of the low pose (`cube_seen_at`: sector -> True) and nothing
    verified from any other pan: the frame at the centre pan is empty, so SEARCH sweeps (the controller's SEARCH SWEEP).
    The pad print is fixed from every pan; only the cube's presence follows the sector."""

    def __init__(self, **script):
        super().__init__(**{"cube_seen_at": {}, **script})

    def sector(self) -> str:
        offset = self.joints["shoulder_pan"] - fixtures.SYNTHETIC_LOW_POSE["shoulder_pan"]
        return "CENTER" if abs(offset) < 1.0 else ("LEFT" if offset < 0.0 else "RIGHT")

    def perception(self) -> dict:
        record = super().perception()
        if self.joints["shoulder_lift"] < -60.0 and not self.released and not self.script["cube_seen_at"].get(self.sector()):  # once released it lies on the pad, which the centre frame sees
            record["cube"] = {**record["cube"], "detected": False, "bbox_xyxy": None, "contact_px": None, "candidates_verified": 0, "notes": ["scripted: no proposal verified in this sector"]}
            record["cube_by_template"] = None
        return record


class TheSearchSweepFindsTheBlockOffCentre(MissionCase):
    """SEARCH SWEEP on the scripted World, main() for real: the planner plans the sectors from the low pose, the executor
    runs them with its checks, the frozen perception sees each sector frame, and an AUTO target is bound from the sector
    frame that shows the block, its measured pan the one the pregrasp is computed from."""

    LOW_PAN = fixtures.SYNTHETIC_LOW_POSE["shoulder_pan"]
    LEFT_PAN, RIGHT_PAN = round(LOW_PAN - current.SEARCH_SWEEP_DEG, 3), round(LOW_PAN + current.SEARCH_SWEEP_DEG, 3)

    def mission(self, world: MissionWorld, writer, **options) -> tuple:
        """The sweep runs only under its word, search_sweep (policy_search.WORDS): every run here names it. The
        PLACEMENT_ENVELOPE record a run that names a word states first is left out of the states and transitions."""
        options["extra_env"] = {"KC_PLACEMENT_ENVELOPE": "search_sweep", **(options.get("extra_env") or {})}
        target, run = super().mission(world, writer, **options)
        run["transitions"] = [entry for entry in run["transitions"] or [] if entry["state"] != "PLACEMENT_ENVELOPE"]
        run["states"] = [state for state in run["states"] if state != "PLACEMENT_ENVELOPE"]
        return target, run

    @staticmethod
    def auto(directory):
        return TheAutoTargetTakesTheMeasuredMode.auto_target(directory)

    @staticmethod
    def sweeps(run) -> list:
        return [(entry["sector"], entry["outcome"], entry["pan_deg"]) for entry in run["transitions"] if entry["state"] == "SEARCH_SWEEP"]

    @staticmethod
    def pans_sent(run, upto=None) -> list:
        return [float(action["arm_shoulder_pan.pos"]) for action in (run["sent"] if upto is None else run["sent"][:upto]) if "arm_shoulder_pan.pos" in action]

    def test_a_block_in_the_left_sector_is_bound_from_that_frame_and_the_pregrasp_is_computed_from_that_pan(self):
        world = SweepWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL, cube_seen_at={"LEFT": True})
        target, run = self.mission(world, self.auto)
        self.assertEnded(run, 0)
        self.assertEqual(["SEARCH", "SEARCH_SWEEP", "TARGET_SELECTED", "PREGRASP"], run["states"][:4])
        self.assertEqual([("LEFT", "cube", self.LEFT_PAN)], self.sweeps(run))
        sweep, pregrasp = run["transitions"][1], run["transitions"][3]
        result = run["result"]
        self.assertEqual(sweep["observation_id"], result["selection"]["observation_id"], "the binding rests on the sector frame")
        self.assertEqual([("cube", A_START, tb.MODE_ARM_ONLY, sweep["observation_id"])], [(b["target_id"], b["bound_pad_m"], b["mode"], b["observation_id"]) for b in result["bindings"]])
        self.assertEqual(current.coarse_pregrasp_from_low_pose(CONTACT, self.LEFT_PAN)["pan_deg"], pregrasp["coarse"]["pan_deg"])
        self.assertNotEqual(current.coarse_pregrasp_from_low_pose(CONTACT, self.LOW_PAN)["pan_deg"], pregrasp["coarse"]["pan_deg"])
        self.assertEqual(([], []), (run["made"], run["base_pulses"]), "arm-only: no base primitive, no pulse")
        self.assertTrue(tb.result_claims_placement(result, target))
        self.assertIsNone(world.stage_at_first_send, "the sweep moves the arm before any cube is selected, so the first command precedes the bound stage")
        self.assertAlmostEqual(self.LEFT_PAN, min(self.pans_sent(run)), places=2)  # the sweep's own pan, and the pregrasp near it
        self.assertLessEqual(max(abs(pan - self.LOW_PAN) for pan in self.pans_sent(run)), 30.0)

    def test_a_block_in_the_right_sector_is_found_after_the_left_one_and_the_transit_passes_the_centre(self):
        world = SweepWorld(floor=[{"pad_m": A_START, "contact_px": RIGHT_CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL, cube_seen_at={"RIGHT": True})
        target, run = self.mission(world, self.auto)
        self.assertEnded(run, 0)
        self.assertEqual(["SEARCH", "SEARCH_SWEEP", "SEARCH_SWEEP", "TARGET_SELECTED", "PREGRASP"], run["states"][:5])
        self.assertEqual([("LEFT", "no_cube", self.LEFT_PAN), ("RIGHT", "cube", self.RIGHT_PAN)], self.sweeps(run))
        self.assertEqual(current.coarse_pregrasp_from_low_pose(RIGHT_CONTACT, self.RIGHT_PAN)["pan_deg"], run["transitions"][4]["coarse"]["pan_deg"])
        # captures: the SEARCH frame before any command, LEFT, then the centre and RIGHT of the transit call, each after commands
        search, left, centre, right = run["captures"][:4]
        self.assertEqual(0, search)
        self.assertAlmostEqual(self.LEFT_PAN, self.pans_sent(run, left)[-1], places=2)
        self.assertAlmostEqual(self.LOW_PAN, self.pans_sent(run, centre)[-1], places=2)  # through the centre pose, never a 2 x amplitude pan in one call
        self.assertAlmostEqual(self.RIGHT_PAN, self.pans_sent(run, right)[-1], places=2)
        self.assertTrue(tb.result_claims_placement(run["result"], target))

    def test_a_block_beyond_the_return_bound_is_left_where_it_lies_with_the_arm_at_the_sector(self):
        """The block the LEFT sector sees at column 300 maps to a coarse pan 46 deg from the saved low pose's pan: the planner
        would admit the pregrasp and refuse the EXIT after the release. The run refuses at SEARCH instead, main() for real:
        the mission result names it, nothing was gripped, no base pulse ran, the arm stands at the LEFT sector (a pan the
        Return arm action can plan from), and no command follows the sector frame."""
        world = SweepWorld(floor=[{"pad_m": A_START, "contact_px": [300.0, 545.0]}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL, cube_seen_at={"LEFT": True})
        target, run = self.mission(world, self.auto)
        self.assertEnded(run, 5)
        self.assertEqual(["SEARCH", "SEARCH_SWEEP", "TARGET_SELECTED"], run["states"])
        self.assertEqual([("LEFT", "cube", self.LEFT_PAN)], self.sweeps(run))
        result = run["result"]
        self.assertEqual(("finished", "failed", "search_cube_beyond_return_bound"), (result["stage"], result["outcome"], result["reason_code"]))
        self.assertFalse(tb.result_claims_placement(result, target))
        self.assertFalse(self.gripper_closed(run))
        self.assertEqual([], run["base_pulses"])
        self.assertAlmostEqual(self.LEFT_PAN, world.joints["shoulder_pan"], places=2)
        self.assertAlmostEqual(self.LEFT_PAN, self.pans_sent(run)[-1], places=2)
        self.assertEqual(2, len(run["captures"]), "the SEARCH frame and the LEFT sector frame, nothing after the refusal")
        self.assertIn("search_cube_beyond_return_bound", run["stdout"])
        self.assertIn("nothing was picked up", run["stdout"])

    def test_no_block_in_any_sector_ends_at_the_centre_with_nothing_bound(self):
        world = SweepWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)
        target, run = self.mission(world, self.auto)
        self.assertEnded(run, 5)
        self.assertEqual(["SEARCH", "SEARCH_SWEEP", "SEARCH_SWEEP"], run["states"])
        self.assertEqual([("LEFT", "no_cube", self.LEFT_PAN), ("RIGHT", "no_cube", self.RIGHT_PAN)], self.sweeps(run))
        result = run["result"]
        self.assertEqual(("finished", "failed", "search_no_cube_after_sweep", "SEARCH_SWEEP"), (result["stage"], result["outcome"], result["reason_code"], result["final_state"]))
        self.assertEqual((None, None, None), (result["bindings"], result["selection"], result["placement"]))
        self.assertAlmostEqual(self.LOW_PAN, world.joints["shoulder_pan"], places=2)  # the arm is back at the centre pose
        self.assertAlmostEqual(self.LOW_PAN, self.pans_sent(run)[-1], places=2)
        self.assertFalse(self.gripper_closed(run))
        self.assertEqual([], run["base_pulses"])
        self.assertIn("search_no_cube_after_sweep", run["stdout"] + json.dumps(result))
        self.assertFalse(tb.result_claims_placement(result, target))

    def test_a_stop_during_a_sector_commands_nothing_further(self):
        world = SweepWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL, cube_seen_at={"RIGHT": True}, abort_at_send=5)
        target, run = self.mission(world, self.auto)
        self.assertEnded(run, 4)
        self.assertEqual(5, len(run["sent"]), "one more command would mean the stop was not honoured")
        self.assertEqual(["SEARCH"], run["states"], "no sector outcome is stated for a sector that was stopped")
        result = run["result"]
        # As for every arm step of a mission run: run_pose's perception gate sees the stop before the executor's own abort sentence is raised.
        self.assertEqual(("stopped", "operator_abort_perception", None), (result["outcome"], result["reason_code"], result["bindings"]))
        self.assertEqual([], run["base_pulses"])
        self.assertEqual(1, len(run["captures"]), "no capture after the stop: the sector frame was never taken")


class TheAutoTargetWhileHeld(unittest.TestCase):
    """The scripted MACHINE: an auto target bound ARM_ONLY whose plan from the measured grasp needs the base. A run that
    carries the capability drives the base as a capability run does; a run that carries none ends with the cube held."""

    def setUp(self):
        self.directory = fixtures.use_synthetic_runtime(self)

    def machine(self, carry, **script):
        _, target = write_target(self.directory, "cube", [declared("cube", tb.AUTO)], carry=carry)
        machine = fixtures.scripted_machine(current, fixtures.machine_script(**script))
        machine.target, machine._mission = target, current._mission_record()
        machine._mission_candidates = lambda record: ([{"pass": 1, "cube": record["cube"], "cube_by_template": {**record["cube_by_template"], "inside_pad_outline": True}}], None, None)
        scripted = machine.run_pose

        def run_pose(*args, **kwargs):  # the machine's pregrasp views carry no count; the recognizer's records always do
            view = scripted(*args, **kwargs)
            for record in view["perception"]:
                record["cube"] = {**record["cube"], "candidates_verified": 1}
            return view

        machine.run_pose = run_pose
        if not carry:
            machine.move_base = None  # what main() wires for a run that carries no capability: no base primitive at all
        return machine

    @staticmethod
    def flipping_plan(calls):
        real = current.placement_plan

        def plan(*args):
            calls.append(args)
            answer = real(*args)
            return answer if len(calls) <= 2 else {"mode": "BASE_ADVANCE", "fk_travel_mm": 0.0, "base_advance_mm": 50.0}  # SEARCH twice, then the plan from the grasp

        return plan

    def test_without_the_capability_the_flip_ends_the_run_with_the_cube_held(self):
        calls = []
        machine = self.machine(False, cube_start_pad_m=A_START)
        with mock.patch.object(current, "placement_plan", self.flipping_plan(calls)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(current.CycleFailed) as failed:
                machine.run_cycle()
        self.assertEqual(3, len(calls))
        self.assertEqual("mission_mode_changed_while_held", machine._mission["reason_code"])
        self.assertIn("carries no capability", str(failed.exception))
        self.assertIn("OBJECT_SECURED", machine.transitions)
        self.assertNotIn("CARRY", machine.transitions)
        self.assertFalse([call for call in machine.calls if call.startswith(("base:", "grip:open"))])

    def test_with_the_capability_the_flip_is_driven_as_a_capability_run(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}
        calls, planned = [], []
        machine = self.machine(True, cube_start_pad_m=A_START, final=deep)
        listed = machine.state
        machine.state = lambda name, **fields: (planned.append(fields) if name == "PLACEMENT_PLAN" else None, listed(name, **fields))[1]
        with mock.patch.object(current, "placement_plan", self.flipping_plan(calls)), contextlib.redirect_stdout(io.StringIO()):
            summary = machine.run_cycle()
        self.assertEqual(3, len(calls))
        self.assertIsNone(machine._mission["reason_code"])
        self.assertTrue(summary["placed"] and summary["base_returned"])
        self.assertIn("BASE_ADVANCE", machine.transitions)
        # The record shows the flip: the plan says BASE_ADVANCE beside the mode the target was bound in.
        (plan,) = planned
        self.assertEqual((tb.MODE_ARM_ONLY, tb.MODE_BASE_ADVANCE), (plan["bound_mode"], plan["mode"]))
        self.assertTrue([call for call in machine.calls if call.startswith("base:+")], "the base was not driven")
        self.assertLess(machine.calls.index("grip:open"), machine.calls.index(next(call for call in machine.calls if call.startswith("base:-"))))

    def test_the_arm_only_fallback_without_the_capability_is_the_existing_refusal(self):
        machine = self.machine(False, cube_start_pad_m=A_START, carry_stalls=True)
        with self.assertRaises(current.CycleFailed), contextlib.redirect_stdout(io.StringIO()):
            machine.run_cycle()
        self.assertEqual("mission_arm_only_insufficient_while_held", machine._mission["reason_code"])
        self.assertIn("ARM_ONLY_INSUFFICIENT", machine.transitions)
        self.assertFalse([call for call in machine.calls if call.startswith(("base:", "grip:open", "HOLD_OBSERVE", "CARRY back"))], machine.calls)

    def test_the_arm_only_fallback_with_the_capability_drives_the_base(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}
        machine = self.machine(True, cube_start_pad_m=A_START, carry_stalls=True, final=deep)
        with contextlib.redirect_stdout(io.StringIO()):
            summary = machine.run_cycle()
        self.assertTrue(summary["placed"])
        self.assertIn("ARM_ONLY_INSUFFICIENT", machine.transitions)
        self.assertTrue([call for call in machine.calls if call.startswith("base:+")])


class APrestagedCubeIsNeverCredited(MissionCase):
    """G1, end to end, with the stand-in source (the run has to get as far as its EXIT view): the mission carries ONE
    target, B; A was placed by hand before the mission. B is released short of the pad. The EXIT view shows A placed, far
    from B's release: THE RELEASE AGREEMENT (the pad policy of 2026-09-29, in place of the retired OCCUPIED) never
    credits it."""

    def world(self):
        dropped = [B_START[0], -0.1475 + 0.004]  # on the outline, not placed
        return placed_first_world(after_release=[{"pad_m": A_PLACED, "contact_px": [700.0, 330.0]}, {"pad_m": dropped, "contact_px": CONTACT}])

    def writer(self, directory):
        return write_target(directory, "cube-b", [declared("cube-b", True)])

    def test_the_dropped_cube_is_not_placed_and_the_prestaged_one_is_not_credited(self):
        _, run = self.mission(self.world(), self.writer)
        self.assertEnded(run, 5)
        self.assertIn("RELEASED", run["states"])
        self.assertEqual((False, tb.PLACED_NO_CUBE_AT_RELEASE), (run["result"]["placement"]["placed"], run["result"]["reason_code"]))  # CHANGED code: was mission_placed_no_unclaimed_cube
        self.assertGreater(run["result"]["placement"]["release_distance_mm"], run["result"]["placement"]["release_tolerance_mm"])

    def test_not_vacuous_without_the_release_agreement_it_would_be_credited(self):
        # CHANGED (operator ruling (ii) of 2026-09-29): the SEARCH listing no longer protects A, so with the tolerance
        # widened A is credited; the release agreement alone keeps it from being credited.
        with mock.patch.object(tb, "RELEASE_AGREEMENT_MM", 999.0):
            _, run = self.mission(self.world(), self.writer)
        self.assertEqual(A_PLACED, run["result"]["placement"]["placed_pad_m"], "the pre-staged cube was credited to B")


class TheApproachView(MissionCase):
    """C1. run_pose never stamps _capture, so before this the latest stamped capture at the first pulse was the grasp's
    observe(), whose motion epoch the lifts and the HOLD_OBSERVE rise had left behind, and the relay refused every pulse
    (observation_epoch_changed). Now, for a run that carries the capability, advance_base takes one stamped observe()
    right after the rise: that view is the approach state's, it is at the current epoch, and it resolves."""

    def test_the_latest_stamped_capture_at_the_first_pulse_is_the_approach_view_at_the_current_epoch_and_it_resolves(self):
        seen = []
        real = current.make_capability_relay

        def building(target, robot_id, transport_options, should_stop, observe, motion_epoch):
            relay = real(target, robot_id, transport_options, should_stop=should_stop, observe=observe, motion_epoch=motion_epoch)

            def recording():
                cycle = observe.__closure__ and next((cell.cell_contents for cell in observe.__closure__ if isinstance(cell.cell_contents, current.Cycle)), None)
                # The controller's own resolve, with the perception budget the World does not have handed in for this reading
                resolved = tos.resolve(cycle._approach, cycle._capture_with_identity(), cycle._mission["selection"], current.time.monotonic_ns(),
                                       perception_deadline_s=PERCEPTION_DEADLINE_S, target_id=target.target_id)
                seen.append((dict(cycle._capture), cycle.motion_epoch, dict(cycle._approach), resolved))
                return observe()  # the controller's own answer: refused without a budget, so the relay reads nothing

            relay._observe = recording
            return relay

        world = placed_first_world()
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}), mock.patch.object(current, "make_capability_relay", building):
            _, run = self.mission(world, second_execution, real_move_base=True)
        self.assertEnded(run, 5)
        self.assertEqual(1, len(seen))
        capture, epoch, approach, resolved = seen[0]
        self.assertEqual(capture["motion_epoch"], epoch, "the approach view is not at the current epoch: a pulse on it would be refused")
        self.assertEqual(approach["observation_id"], capture["observation_id"], "the approach state is not the latest stamped capture's view")
        self.assertIs(capture["stationary"], True)
        self.assertEqual(64, len(capture["frame_sha256"]))
        self.assertEqual((SCAN_ID, None), (capture["run_id"], capture["perception_request_id"]))
        direction = current.reach_direction_pad(fixtures._pad_view()["homography_image_to_pad"], CONTACT)
        need = current.base_advance_needed_mm(tuple(B_START), direction)
        self.assertEqual((need, True), (approach["need_mm"], approach["pad_frame_valid"]))
        self.assertEqual(need / 1000.0, resolved.x_m)
        self.assertEqual(("anchor_reach", True, capture["capture_monotonic_ns"], epoch, capture["observation_id"], tos.max_age_ns_for(PERCEPTION_DEADLINE_S)),
                         (resolved.frame, resolved.stationary, resolved.capture_monotonic_ns, resolved.motion_epoch, resolved.observation_id, resolved.max_age_ns))
        self.assertIsNone(tos.check_for_pulse(resolved, target_id="cube-b", motion_epoch=epoch, now_ns=capture["capture_monotonic_ns"]))
        # Not vacuous: the capture BEFORE the approach view (the grasp's, at the SEARCH view's epoch or later) is behind the epoch
        grasp_epoch = capture["motion_epoch"] - 1
        stale = tos.ObservedTarget(target_id="cube-b", x_m=resolved.x_m, capture_monotonic_ns=capture["capture_monotonic_ns"], motion_epoch=grasp_epoch,
                                   observation_id=capture["observation_id"], max_age_ns=resolved.max_age_ns, stationary=True)
        self.assertEqual(tos.PULSE_EPOCH_CHANGED, tos.check_for_pulse(stale, target_id="cube-b", motion_epoch=epoch, now_ns=capture["capture_monotonic_ns"]))
        self.assertEqual(("failed", "base_capability_output_refused"), (run["result"]["outcome"], run["result"]["reason_code"]))

    def test_the_legacy_cycle_and_a_run_without_the_capability_take_no_extra_capture(self):
        """The same scene, three ways: the legacy cycle, the mission run that carries the capability (one more capture, the
        approach view) and the arm-only mission target (none). Counted at the capture boundary, not inferred."""
        legacy_world = MissionWorld()
        legacy = run_world(legacy_world, self.directory)
        self.assertEnded(legacy, 0)
        capability_world = placed_first_world()
        _, carried = self.mission(capability_world, second_execution)
        self.assertEnded(carried, 0)
        self.assertEqual(len(legacy["captures"]) + 1, len(carried["captures"]), "the capability run takes exactly one capture more: the approach view")
        arm_only_world = MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)
        _, arm_only = self.mission(arm_only_world, lambda d: write_target(d, "cube-a", [declared("cube-a", False)]))
        self.assertEnded(arm_only, 0)
        legacy_arm_only = run_world(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL), self.directory)
        self.assertEqual(len(legacy_arm_only["captures"]), len(arm_only["captures"]))


class TheApproachStateOnTheScriptedMachine(unittest.TestCase):
    """The scripted MACHINE with a target that carries the capability: advance_base keeps _approach from the view before
    the first pulse and from the re-observation after every measured pulse (the moved anchor, the measured axis, the
    recomputed remaining advance), and it is None once advance_base returns. The machine's base primitive is the plain
    scripted one (no relay), so the whole base advance runs and every pulse's state can be read."""

    def setUp(self):
        self.directory = fixtures.use_synthetic_runtime(self)
        _, self.target = write_target(self.directory, "cube-b", [declared("cube-b", True)])
        self.assertTrue(self.target.carries_capability)

    def machine(self, **script):
        machine = fixtures.scripted_machine(current, fixtures.machine_script(**script))
        machine.target, machine._mission = self.target, current._mission_record()
        machine._mission_candidates = lambda record: ([{"pass": 1, "cube": record["cube"], "cube_by_template": {**record["cube_by_template"], "inside_pad_outline": True}}], None, None)
        scripted, pulse = machine.run_pose, machine.move_base

        def run_pose(*args, **kwargs):  # the machine's pregrasp views carry no count; the recognizer's records always do
            view = scripted(*args, **kwargs)
            for record in view["perception"]:
                record["cube"] = {**record["cube"], "candidates_verified": 1}
            return view

        def recording_pulse(vx_mps, duration_s):
            machine.approach_at_pulse.append(None if machine._approach is None else dict(machine._approach))
            return pulse(vx_mps, duration_s)

        machine.run_pose, machine.move_base, machine.approach_at_pulse = run_pose, recording_pulse, []
        return machine

    def test_the_state_is_kept_per_pulse_and_cleared_after(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}
        machine = self.machine(final=deep)
        with contextlib.redirect_stdout(io.StringIO()):
            summary = machine.run_cycle()
        self.assertTrue(summary["placed"] and summary["base_returned"])
        self.assertIsNone(machine._approach, "the approach state outlived advance_base")
        forward = [state for state, call in zip(machine.approach_at_pulse, [call for call in machine.calls if call.startswith("base:")]) if call.startswith("base:+")]
        self.assertEqual(2, len(forward))
        direction = current.reach_direction_pad(fixtures._pad_view()["homography_image_to_pad"], (553.0, 545.0))
        first, second = forward
        self.assertEqual(current.base_advance_needed_mm((0.0145, -0.1825), direction), first["need_mm"])
        self.assertEqual(([0.0145, -0.1825], [round(v, 6) for v in direction], True, None), (first["anchor_pad_m"], [round(v, 6) for v in first["axis_pad"]], first["pad_frame_valid"], first["observation_id"]))
        self.assertEqual({"observation_id", "anchor_pad_m", "axis_pad", "need_mm", "pad_frame_valid", "landmark_inliers", "reprojection_rms_px"}, set(first))
        self.assertLess(second["need_mm"], first["need_mm"], "the remaining advance was not recomputed on the moved anchor")
        self.assertGreater(second["anchor_pad_m"][1], first["anchor_pad_m"][1], "the anchor did not move along the reach axis")
        self.assertAlmostEqual(first["need_mm"] - second["need_mm"], machine.base["advance_mm"] - (machine.base["advance_mm"] - 90.0 * 0.47), delta=2.0)
        reverse = [state for state, call in zip(machine.approach_at_pulse, [call for call in machine.calls if call.startswith("base:")]) if call.startswith("base:-")]
        self.assertTrue(reverse and all(state is None for state in reverse), "the approach state was still there during the base return")

    def test_the_capability_run_takes_one_stamped_observation_more_than_the_legacy_machine(self):
        deep = {"cube": {"detected": True, "bbox_xyxy": [515, 332, 595, 419]}, "cube_by_template": {"cube_center_pad_m": [0.0145, -0.1475 + 0.058], "inside_pad_outline": True}, "frame": "f"}
        machine = self.machine(final=deep)
        legacy = fixtures.scripted_machine(current, fixtures.machine_script(final=deep))
        with contextlib.redirect_stdout(io.StringIO()):
            machine.run_cycle()
            legacy.run_cycle()
        self.assertEqual(legacy.calls.count("observe") + 1, machine.calls.count("observe"))
        hold = machine.calls.index(next(call for call in machine.calls if call.startswith("HOLD_OBSERVE")))
        self.assertEqual("observe", machine.calls[hold + 1], "the approach view is not taken right after the HOLD_OBSERVE rise")
        self.assertTrue(machine.calls[hold + 2].startswith("base:+"))
        legacy_hold = legacy.calls.index(next(call for call in legacy.calls if call.startswith("HOLD_OBSERVE")))
        self.assertTrue(legacy.calls[legacy_hold + 1].startswith("base:+"), "not vacuous: the legacy machine pulses right after the rise")
        self.assertIsNone(legacy._approach)


class TheCapabilityDrivesTheWorldOnContractV2(MissionCase):
    """END TO END on the scripted World with the REAL relay (capability_pulse.CapabilityPulse, built by the controller's own
    make_capability_relay) on the World's clock: the SEARCH gate passes, the approach view resolves to a v2 observation,
    the relay publishes it (the eleven schema fields, a fresh published_ns per period) to a scripted v2 capability, every
    answer (timestamp == published_ns) is APPLIED by the gateway into a transport that moves the World's base at the
    World's rate, the executor measures each pulse against the pad and stops at its tolerance, the cube is PLACED, the
    base returns through the World's reverse pulse, and the result credits the applied commands. The perception budget
    a LIVE run exports is handed to the controller (patched, as KC_PERCEPTION_DEADLINE_S would); nothing else is."""

    def test_the_bounded_stop_observe_move_loop_runs_on_the_capabilitys_own_output(self):
        from test_capability_pulse import FakeLink, RecordingTransport, ScriptedCapability, forward_command, listing
        from test_target_observation_source import validate_against_schema

        world, built, links = placed_first_world(), [], []
        real = current.make_capability_relay

        class WorldClock:  # the relay on the World's virtual time, the same `time` the controller runs on
            @staticmethod
            def now_ns():
                return world.clock.monotonic_ns()

        class MovingTransport(RecordingTransport):
            """FakeBaseMotionTransport whose applied velocities move the World's base: one period of the pulse per send."""

            def send_velocity(self, vx_mps, vy_mps, omega_rps):
                super().send_velocity(vx_mps, vy_mps, omega_rps)
                world.base_mm += world.script["base_rate_mm_s"] * capability_pulse.REFRESH_INTERVAL_S * (1.0 if vx_mps > 0 else -1.0)

        def building(target, robot_id, transport_options, should_stop, observe, motion_epoch):
            relay = real(target, robot_id, transport_options, should_stop=should_stop, observe=observe, motion_epoch=motion_epoch)
            endpoints = target.capability
            answers = listing(generation=3, pid=4321, slot=endpoints.slot_uuid, capsule=endpoints.capsule)
            relay._read, relay._clock, relay._sleep = (lambda op: answers[op]), WorldClock(), world.clock.sleep
            relay._listens, relay._exists = (lambda path: False), (lambda path: True)
            capability = ScriptedCapability(lambda o, n: forward_command(o, n, vx_mps=0.05))  # the v2 service's answer: vx saturated at the pulse speed

            def open_link(observations, commands, timeout_s):
                links.append(FakeLink(capability))
                return links[-1]

            relay._open_link, relay._transport_factory = open_link, (lambda: MovingTransport([], relay._clock))
            built.append(relay)
            return relay

        def dispatch_for(relay):  # make_move_base's relayed dispatch, with the World's own reverse
            def dispatch(vx_mps, duration_s):
                if vx_mps > 0:
                    stopped = relay(vx_mps, duration_s)
                    dispatch.last_refusal = relay.last_refusal
                    return stopped
                dispatch.last_refusal = None
                return world.move_base(vx_mps, duration_s)

            dispatch.capability, dispatch.last_refusal = relay, None
            return dispatch

        world.base_primitive = lambda options: dispatch_for(options["forward"])
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}), mock.patch.object(current, "make_capability_relay", building), \
                mock.patch.object(current.Cycle, "_perception_deadline_s", lambda self: PERCEPTION_DEADLINE_S):
            target, run = self.mission(world, second_execution)
        self.assertEnded(run, 0)
        self.assertEqual(1, len(built))
        relay = built[0]
        pulses = relay.pulses
        self.assertGreaterEqual(len(pulses), 2)
        self.assertEqual([None] * len(pulses), [pulse["refusal"] for pulse in pulses], pulses)
        self.assertEqual(len(pulses), len(links))
        for pulse in pulses:
            self.assertEqual(pulse["contract"], "kc.target_observation/2")
            self.assertGreater(pulse["commands_applied"], 0)
            self.assertEqual((pulse["commands_accepted"], pulse["commands_applied"], pulse["observations_published"], pulse["published_stamps"]),
                             (pulse["commands_applied"],) * 4, "a command was accepted and not applied, or a stamp was reused")
            self.assertEqual(pulse["gateway_outcomes"], {"APPLIED": pulse["commands_applied"]})
            self.assertEqual(pulse["commands_dropped"], {})
            self.assertEqual(64, len(pulse["observation"]["frame_sha256"]))
        with open(str(ROOT / "autonomy" / "schemas" / "target_observation.v2.schema.json"), encoding="utf-8") as handle:
            schema = json.load(handle)
        direction = current.reach_direction_pad(fixtures._pad_view()["homography_image_to_pad"], CONTACT)
        need_first = current.base_advance_needed_mm(tuple(B_START), direction)
        stamps, x_by_pulse = [], []
        for link in links:
            self.assertTrue(link.published)
            for document in link.published:
                self.assertEqual([], validate_against_schema(document, schema), document)
                self.assertEqual(("cube-b", True, "anchor_reach", 0.0), (document["target_id"], document["stationary"], document["frame"], document["y_m"]))
                stamps.append(document["published_ns"])
            self.assertEqual(1, len({d["capture_ns"] for d in link.published}), "one pulse published more than one capture")
            self.assertEqual(1, len({d["observation_id"] for d in link.published}))
            x_by_pulse.append(link.published[0]["x_m"])
        self.assertEqual(stamps, sorted(set(stamps)), "published_ns did not strictly increase across the run")
        self.assertEqual(need_first / 1000.0, x_by_pulse[0], "the first pulse's x_m is not the SEARCH anchor's remaining advance")
        self.assertEqual(sorted(x_by_pulse, reverse=True), x_by_pulse, "the remaining advance did not fall pulse by pulse")
        self.assertEqual(len({d["observation_id"] for link in links for d in link.published}), len(links), "two pulses published one capture: the re-observation was not the approach view")
        # the executor's side: measured, placed, returned
        measured = [entry for entry in run["transitions"] if entry["state"] == "BASE_MEASURED" and entry["vx_mps"] > 0]  # the return's pulses are the World's own
        self.assertEqual(len(pulses), len(measured))
        for pulse, entry in zip(pulses, measured):
            self.assertAlmostEqual(entry["advance_mm"], pulse["commands_applied"] * world.script["base_rate_mm_s"] * capability_pulse.REFRESH_INTERVAL_S, delta=0.5,
                                   msg="the displacement the executor measured is not what the applied commands moved")
        order = [run["states"].index(name) for name in ("HOLD_OBSERVE", "BASE_ADVANCE", "BASE_MEASURED", "RELEASE_ADMISSION", "RELEASED", "PLACED", "BASE_RETURN", "BASE_RETURNED")]
        self.assertEqual(sorted(order), order)
        self.assertTrue(all(vx < 0 for vx, _ in run["base_pulses"]), "the World's own primitive was asked for a forward pulse: the relay did not drive it")
        self.assertLess(abs(world.base_mm), current.BASE_RETURN_TOLERANCE_MM)
        result = run["result"]
        applied = sum(pulse["commands_applied"] for pulse in pulses)
        self.assertEqual({"generation": 3, "pid": 4321, "accepted": applied, "applied": applied, "dropped": {}}, result["capability"])
        self.assertEqual(("finished", "placed_new", None), (result["stage"], result["outcome"], result["reason_code"]))
        self.assertTrue(tb.result_claims_placement(result, target))
        self.assertEqual(B_FINAL, tb.targets_after_placement(target, result)[1]["placed_pad_m"])


class TheSyntheticObservationIsOutOfALiveRun(MissionCase):
    """KC_RUNTIME_MODE=live, no mission target. The synthetically fed primitive is never imported: run_hybrid_pick_place
    is made unimportable, and the controller's own make_move_base is used."""

    def live(self, world, **options):
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
            return run_world(world, self.directory, real_move_base=True, runtime_mode=_assigned(SUPERVISOR, "LIVE"), **options)

    def test_the_mode_words_are_the_runtimes(self):
        self.assertEqual((current.RUNTIME_MODE_ENV, current.RUNTIME_MODE_LIVE), (_assigned(SUPERVISOR, "RUNTIME_MODE_ENV"), _assigned(SUPERVISOR, "LIVE")))

    def test_a_cube_whose_placement_needs_the_base_is_refused_at_search(self):
        world = MissionWorld()
        run = self.live(world)
        self.assertEnded(run, 5)
        self.assertEqual(([], []), (run["sent"], run["base_pulses"]))
        self.assertEqual("SEARCH", run["states"][-1])
        self.assertIn("synthetic observation", run["stdout"])

    def test_the_arm_only_cycle_is_the_proven_cycle(self):
        run = self.live(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL))
        self.assertEnded(run, 0)
        self.assertEqual([], run["base_pulses"])
        self.assertIn("PLACED", run["states"])

    def test_a_forward_base_action_is_refused_before_the_robot_exists(self):
        run = self.live(MissionWorld(), do="base:0.5")
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))

    def test_the_forward_half_is_a_refusal_that_moves_nothing(self):
        self.assertIs(current.live_forward_refused(current.BASE_VX_MPS, 0.5), False)
        self.assertEqual(current.LIVE_FORWARD_EXCLUDED, current.live_forward_refused.last_refusal)

    def test_not_live_the_same_scene_is_the_proven_base_advance(self):
        run = run_world(MissionWorld(), self.directory)
        self.assertEnded(run, 0)
        self.assertIn("BASE_RETURNED", run["states"])


class ThePlacementEnvelopeWord(MissionCase):
    """T6 step 1b: KC_PLACEMENT_ENVELOPE names the envelope words of a run. Absent, every decision is the proven one; an
    unknown word is refused before the robot exists; a LIVE run whose words are not all qualified is refused before the
    robot exists unless the config names the attended label, which is then stamped into the run."""

    SIDE_BLOCK = [0.2075 - 0.052, -0.1475 - 0.030]  # 52 mm inside the right edge, 30 mm outside the near edge: refused at 58.96, planned at 49.96
    PROJECTED = {"KC_PLACEMENT_ENVELOPE": "projected"}

    @staticmethod
    def auto_target(directory):
        return write_target(directory, "cube", [declared("cube", tb.AUTO)], carry=False)  # the first execution: no capability carried

    def side_world(self):
        return MissionWorld(floor=[{"pad_m": self.SIDE_BLOCK, "contact_px": CONTACT}], cube_start_pad_m=self.SIDE_BLOCK, cube_final_pad_m=A_FINAL)

    def test_without_the_word_the_side_block_is_nobodys_candidate(self):
        _, run = self.mission(self.side_world(), self.auto_target)
        self.assertEnded(run, 5)
        self.assertEqual(([], "no command was sent"), (run["sent"], run["made"] and "" or "no command was sent"))
        self.assertEqual(tb.REFUSED_CANDIDATE_NOT_PLACEABLE, run["result"]["reason_code"])
        self.assertNotIn("PLACEMENT_ENVELOPE", run["states"], "no word, no state: the default record is byte for byte the proven one")

    def test_under_the_word_the_side_block_binds_base_advance_and_the_run_asks_for_the_capability(self):
        world = self.side_world()
        _, run = self.mission(world, self.auto_target, extra_env=self.PROJECTED)
        result = assert_refused_at_search(self, run, world, _assigned(MISSION_EXECUTION, "CAPABILITY_REQUIRED"))
        self.assertEqual([("cube", self.SIDE_BLOCK, tb.MODE_BASE_ADVANCE)], [(b["target_id"], b["bound_pad_m"], b["mode"]) for b in result["bindings"]])
        envelope = run["transitions"][run["states"].index("PLACEMENT_ENVELOPE")]
        self.assertEqual((["projected"], None), (envelope["words"], envelope["attended_label"]))

    def test_an_unknown_word_is_refused_before_the_robot_exists(self):
        run = run_world(MissionWorld(), self.directory, extra_env={"KC_PLACEMENT_ENVELOPE": "sideways"})
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))
        self.assertIn("REFUSED before any motion", run["stdout"])
        self.assertNotIn("sideways", run["stdout"], "the refusal echoes the value")

    def live(self, world, **options):
        with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
            return run_world(world, self.directory, real_move_base=True, runtime_mode=_assigned(SUPERVISOR, "LIVE"), **options)

    def test_a_live_run_with_an_unqualified_word_is_refused_before_the_robot_exists(self):
        self.assertEqual(frozenset(), current.PLACEMENT_ENVELOPE_QUALIFIED)
        run = self.live(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL), extra_env=self.PROJECTED)
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))
        self.assertIn("not qualified", run["stdout"])
        path, _ = self.auto_target(self.directory)  # the mission path refuses the same way
        run = self.live(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL), target_path=path, extra_env=self.PROJECTED)
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))

    def test_a_live_run_with_the_attended_label_runs_and_stamps_the_label(self):
        run = self.live(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL),
                        extra_env={**self.PROJECTED, "KC_PLACEMENT_ENVELOPE_ATTENDED": "qual-2026-09-27-a"})
        self.assertEnded(run, 0)
        self.assertIn("PLACED", run["states"])
        envelope = run["transitions"][run["states"].index("PLACEMENT_ENVELOPE")]
        self.assertEqual((["projected"], "qual-2026-09-27-a"), (envelope["words"], envelope["attended_label"]))
        self.assertIn("qual-2026-09-27-a", run["stdout"])

    def test_not_live_the_word_runs_without_the_label_and_the_states_carry_the_record_fields(self):
        run = run_world(MissionWorld(), self.directory, extra_env=self.PROJECTED)  # the World's base-advance scenario
        self.assertEnded(run, 0)
        admission = run["transitions"][run["states"].index("RELEASE_ADMISSION")]
        self.assertIn("side_margin_after_advance_mm", admission)
        returned = run["transitions"][run["states"].index("BASE_RETURNED")]
        self.assertIn("base_aside_mm", returned)
        self.assertIn("side_margin_after_advance_mm", run["stdout"])
        plain = run_world(MissionWorld(), self.directory)
        self.assertNotIn("side_margin_after_advance_mm", plain["transitions"][plain["states"].index("RELEASE_ADMISSION")])
        self.assertNotIn("base_aside_mm", plain["transitions"][plain["states"].index("BASE_RETURNED")])
        self.assertNotIn("side_margin_after_advance_mm", plain["stdout"])

    def test_a_live_run_with_a_forced_release_offset_needs_the_attended_label(self):
        arm_only = MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)
        forced = {"KC_PLACEMENT_ENVELOPE": "projected,side_offset", "KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG": "+10"}
        run = self.live(arm_only, extra_env=forced)
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]))
        self.assertIn("forced release offset", run["stdout"])
        run = self.live(arm_only, extra_env={**forced, "KC_PLACEMENT_ENVELOPE": "projected"})  # the force without its word is refused everywhere
        self.assertEnded(run, 3)
        run = self.live(arm_only, extra_env={**forced, "KC_PLACEMENT_ENVELOPE_ATTENDED": "qual-2026-09-27-b"})
        self.assertEnded(run, 0)
        self.assertNotIn("RELEASE_OFFSET", run["states"], "an arm-only placement takes no offset: the offset belongs to the base-advance branch only")
        self.assertEqual((), current.PLACEMENT_ENVELOPE)
        self.assertIsNone(current.PLACEMENT_SIDE_OFFSET_FORCE_DEG)

    def test_the_environment_names_and_the_words_are_the_runtimes(self):
        self.assertEqual((current.PLACEMENT_ENVELOPE_ENV, current.PLACEMENT_ENVELOPE_ATTENDED_ENV),
                         (_assigned(SUPERVISOR, "PLACEMENT_ENVELOPE_ENV"), _assigned(SUPERVISOR, "PLACEMENT_ENVELOPE_ATTENDED_ENV")))
        from demo_runtime import config as runtime_config
        self.assertEqual(tuple(current.PLACEMENT_ENVELOPE_WORDS), tuple(runtime_config.PLACEMENT_ENVELOPE_WORDS))

    def test_the_module_state_is_reset_after_every_run(self):
        run_world(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL), self.directory, extra_env=self.PROJECTED)
        self.assertEqual((), current.PLACEMENT_ENVELOPE)


class TheNearBlockRefusal(MissionCase):
    """T6 step 2a: a block nearer than the pregrasp clamp (coarse cube_out_mm below PREGRASP_MIN_CUBE_OUT_MM, -13: the clamp
    forward_mm = max(out + 13, 0) means the pregrasp cannot come nearer, and ALIGN only advances) is refused at SEARCH before
    any motion, with the distance the block must move. Run m8c5f9e9 of 2026-09-26 (cube_out -44.5, three empty closes,
    grasp_missed, the retries advancing) is the evidence; the threshold is the clamp, a geometric argument, not a measurement."""

    NEAR = [553.0, 545.0 + 2.7 * 55.9]  # run m8c5f9e9: cube_out -44.5

    @staticmethod
    def row_for(cube_out_mm: float) -> list:
        return [553.0, 545.0 + 2.7 * (11.4 - cube_out_mm)]

    def test_the_recorded_miss_is_refused_at_search_and_nothing_moves(self):
        world = MissionWorld(floor=[{"pad_m": A_START, "contact_px": self.NEAR}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)
        events = os.path.join(self.directory, "near-events.jsonl")
        run = run_world(world, self.directory, events_path=events)
        self.assertEnded(run, 5)
        self.assertEqual(([], []), (run["sent"], run["base_pulses"]), "something moved")
        self.assertEqual("SEARCH", run["states"][-1])
        self.assertIn("nearer than the pregrasp can reach", run["stdout"])
        self.assertIn("57.5 mm", run["stdout"])  # move it at least 13 + 44.5 mm away
        lines = [json.loads(line) for line in Path(events).read_text().splitlines() if line.strip()]
        finished = [line for line in lines if line["kind"] == "run.finished"][0]["payload"]
        self.assertEqual("search_cube_inside_pregrasp_reach", finished["reason_code"])
        values = {item["name"]: item["value"] for item in finished["failure"]["measurements"]}
        self.assertEqual((-44.5, -13.0, 57.5), (values["cube_out_mm"], values["min_cube_out_mm"], values["move_block_at_least_mm"]))
        predicate = [line for line in lines if line["kind"] == "predicate.evaluated" and line["payload"]["predicate"] == "pregrasp_cube_within_reach"]
        self.assertEqual(1, len(predicate))
        self.assertEqual((False, -44.5, -13.0), (predicate[0]["payload"]["value"], predicate[0]["payload"]["measured"], predicate[0]["payload"]["threshold"]))

    def test_the_threshold_is_the_clamp_minus_13_passes_and_minus_13_1_refuses(self):
        self.assertEqual(-13.0, current.PREGRASP_MIN_CUBE_OUT_MM)
        self.assertEqual(-current.PREGRASP_OUTWARD_BIAS_MM, current.PREGRASP_MIN_CUBE_OUT_MM)
        for cube_out, states in ((-13.0, "PREGRASP"), (-13.1, "SEARCH")):
            with self.subTest(cube_out):
                self.assertEqual(cube_out, current.coarse_pregrasp_from_low_pose(self.row_for(cube_out), -9.582)["cube_out_mm"])
                machine = fixtures.scripted_machine(current, fixtures.machine_script(contact_px=self.row_for(cube_out), cube_start_pad_m=A_START))
                machine.events = RecordingEvents()
                current.use_runtime_paths(current.cli.runtime_paths())  # the synthetic saved pose: the pan-bound refusal before this one reads it
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        machine.run_cycle()
                except current.CycleFailed:
                    pass
                finally:
                    current.use_runtime_paths(current.cli.runtime_paths({}))
                self.assertIn(states, machine.transitions)
                if states == "SEARCH":
                    self.assertEqual(["search_cube_inside_pregrasp_reach"], machine.events.failures)
                    self.assertEqual(["observe"], machine.calls, "an arm move after SEARCH")
                else:
                    self.assertNotIn("search_cube_inside_pregrasp_reach", machine.events.failures)

    def test_the_reference_pixel_still_yields_11_4_and_passes(self):
        self.assertEqual(11.4, current.coarse_pregrasp_from_low_pose(CONTACT, -9.582)["cube_out_mm"])
        run = run_world(MissionWorld(floor=[{"pad_m": A_START, "contact_px": CONTACT}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL), self.directory)
        self.assertEnded(run, 0)

    def test_for_an_auto_target_the_refusal_comes_after_the_selection_and_the_finished_stage_carries_it(self):
        world = MissionWorld(floor=[{"pad_m": A_START, "contact_px": self.NEAR}], cube_start_pad_m=A_START, cube_final_pad_m=A_FINAL)
        _, run = self.mission(world, lambda d: write_target(d, "cube", [declared("cube", tb.AUTO)], carry=False))
        self.assertEnded(run, 5)
        self.assertEqual(([], "no command was sent"), (run["sent"], world.stage_at_first_send))
        self.assertEqual(["SEARCH", "TARGET_SELECTED"], run["states"])
        result = run["result"]
        self.assertEqual(("finished", "failed", "search_cube_inside_pregrasp_reach", tb.MODE_ARM_ONLY), (result["stage"], result["outcome"], result["reason_code"], result["selection"]["mode"]))
        self.assertEqual(_assigned(MISSION_EXECUTION, "SEARCH_REFUSAL_FINAL_STATE"), result["final_state"])


class RecordingEvents(current._NoEvents):
    """The controller's silent events, keeping the reason code of every Cycle.failed: a legacy run records no mission
    result, so this is where the code that ended it can be read."""

    def __init__(self):
        self.failures = []

    def cycle_failed(self, code, message, values, evidence):
        self.failures.append(code)


class TheLiveRefusalsPastSearch(unittest.TestCase):
    """KC_RUNTIME_MODE=live, no mission target, on the scripted MACHINE: the three refusals SEARCH does not reach. A cube
    that measures ARM_ONLY at SEARCH passes it, and the base can still be asked for later: by the plan from the measured
    grasp (PLACEMENT_PLAN), by the arm-only fallback (ARM_ONLY_INSUFFICIENT), and by any forward pulse (base_pulse). Each
    is refused with the synthetically fed primitive never called. Every test runs the same script NOT live as well, and
    shows that it does reach the base there, so no refusal here is vacuous."""

    def setUp(self):
        self.directory = fixtures.use_synthetic_runtime(self)

    def machine(self, live, **script):
        machine = fixtures.scripted_machine(current, fixtures.machine_script(**script))
        machine.events, machine.runtime_live = RecordingEvents(), live
        return machine

    @staticmethod
    def base_calls(machine, prefix="base:"):
        return [call for call in machine.calls if call.startswith(prefix)]

    def test_a_forward_pulse_is_refused_in_base_pulse_and_the_reverse_is_not(self):
        live = self.machine(True)
        with self.assertRaises(current.CycleFailed), contextlib.redirect_stdout(io.StringIO()):
            live.base_pulse(current.BASE_VX_MPS, 0.5, False)
        self.assertEqual(([current.LIVE_FORWARD_EXCLUDED], []), (live.events.failures, self.base_calls(live)))
        reverse = self.machine(True)
        with contextlib.redirect_stdout(io.StringIO()):
            reverse.base_pulse(-current.BASE_VX_MPS, 0.5, False)  # the proven reverse commands no observation: unchanged
        self.assertEqual(["base:-0.05x0.50"], self.base_calls(reverse))
        legacy = self.machine(False)
        with contextlib.redirect_stdout(io.StringIO()):
            legacy.base_pulse(current.BASE_VX_MPS, 0.5, False)
        self.assertEqual(["base:+0.05x0.50"], self.base_calls(legacy))

    def test_a_plan_from_the_measured_grasp_that_needs_the_base_is_refused_with_the_cube_held(self):
        real = current.placement_plan

        def plan_for(machine):
            # SEARCH plans ARM_ONLY for this cube; the plan from the measured grasp (after OBJECT_SECURED) needs the base.
            return lambda *args: real(*args) if "OBJECT_SECURED" not in machine.transitions else {**real(*args), "mode": "BASE_ADVANCE"}

        live = self.machine(True, cube_start_pad_m=A_START)
        with mock.patch.object(current, "placement_plan", plan_for(live)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(current.CycleFailed):
                live.run_cycle()
        self.assertEqual([current.LIVE_FORWARD_EXCLUDED], live.events.failures)
        self.assertEqual(["OBJECT_SECURED", "PLACEMENT_PLAN"], live.transitions[-2:])
        self.assertEqual([], self.base_calls(live) + self.base_calls(live, "grip:open") + self.base_calls(live, "CARRY"))
        legacy = self.machine(False, cube_start_pad_m=A_START)
        with mock.patch.object(current, "placement_plan", plan_for(legacy)), contextlib.redirect_stdout(io.StringIO()):
            legacy.run_cycle()
        self.assertTrue(self.base_calls(legacy, "base:+"), "not live, the same plan drives the base forward")

    def test_the_arm_only_fallback_to_the_base_is_refused_with_the_cube_held(self):
        live = self.machine(True, cube_start_pad_m=A_START, carry_stalls=True)
        with self.assertRaises(current.CycleFailed), contextlib.redirect_stdout(io.StringIO()):
            live.run_cycle()
        self.assertEqual([current.LIVE_FORWARD_EXCLUDED], live.events.failures)
        self.assertEqual("ARM_ONLY_INSUFFICIENT", live.transitions[-1])
        self.assertEqual([], self.base_calls(live) + self.base_calls(live, "grip:open") + self.base_calls(live, "CARRY back"))
        legacy = self.machine(False, cube_start_pad_m=A_START, carry_stalls=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(legacy.run_cycle()["placed"])
        self.assertTrue(self.base_calls(legacy, "base:+"), "not live, the same carry falls back to the base")


class AListingThatRaisesIsNeverUsed(MissionCase):
    def test_at_search_it_refuses_before_anything_is_picked_up(self):
        _, run = self.mission(placed_first_world(listing_raises_at=1), second_execution)
        self.assertEnded(run, 5)
        self.assertEqual([], run["sent"])
        self.assertEqual(("failed", "mission_candidates_failed", "SEARCH"), (run["result"]["outcome"], run["result"]["reason_code"], run["result"]["final_state"]))

    def test_at_the_exit_view_it_is_not_placed_and_the_base_still_returns(self):
        target, run = self.mission(placed_first_world(listing_raises_at=2), second_execution)
        self.assertEnded(run, 5)
        self.assertIn("RELEASED", run["states"])
        self.assertIn("BASE_RETURNED", run["states"], "the base was left advanced")
        self.assertEqual("mission_candidates_failed", run["result"]["reason_code"])
        self.assertEqual({"object_secured": True, "placed": False, "base_returned": True}, run["result"]["predicates"])
        self.assertFalse(tb.result_claims_placement(run["result"], target))


class TheArmOnlyTargetNeverDrivesTheBase(unittest.TestCase):
    """The scripted MACHINE: the World realizes every carry exactly, so a loaded reach that falls short is scripted here."""

    def setUp(self):
        self.directory = fixtures.use_synthetic_runtime(self)
        _, self.target = write_target(self.directory, "cube-a", [declared("cube-a", False)])

    def machine(self, **script):
        machine = fixtures.scripted_machine(current, fixtures.machine_script(**script))
        machine.target, machine._mission = self.target, current._mission_record()
        machine._mission_candidates = lambda record: ([{"pass": 1, "cube": record["cube"], "cube_by_template": {**record["cube_by_template"], "inside_pad_outline": True}}], None, None)
        scripted = machine.run_pose

        def run_pose(*args, **kwargs):  # the machine's pregrasp views carry no count; the recognizer's records always do
            view = scripted(*args, **kwargs)
            for record in view["perception"]:
                record["cube"] = {**record["cube"], "candidates_verified": 1}
            return view

        machine.run_pose = run_pose
        return machine

    def test_the_arm_only_fallback_to_the_base_is_refused_with_the_cube_held(self):
        machine = self.machine(cube_start_pad_m=A_START, carry_stalls=True)
        with self.assertRaises(current.CycleFailed), contextlib.redirect_stdout(io.StringIO()):
            machine.run_cycle()
        self.assertEqual("mission_arm_only_insufficient_while_held", machine._mission["reason_code"])
        self.assertIn("ARM_ONLY_INSUFFICIENT", machine.transitions)
        self.assertNotIn("grip:open", machine.calls)
        self.assertFalse([call for call in machine.calls if call.startswith(("base:", "HOLD_OBSERVE", "CARRY back"))], machine.calls)
        legacy = fixtures.scripted_machine(current, fixtures.machine_script(cube_start_pad_m=A_START, carry_stalls=True))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(legacy.run_cycle()["placed"])  # not vacuous: the legacy cycle falls back to the base here
        self.assertTrue([call for call in legacy.calls if call.startswith("base:+")])

    def test_a_mode_that_changes_once_the_cube_is_held_is_refused_with_the_cube_held(self):
        real, calls = current.placement_plan, []

        def plan(*args):
            calls.append(args)
            answer = real(*args)
            return answer if len(calls) <= 2 else {"mode": "BASE_ADVANCE", "fk_travel_mm": 0.0, "base_advance_mm": 50.0}  # SEARCH twice, then the plan from the grasp

        machine = self.machine(cube_start_pad_m=A_START)
        with mock.patch.object(current, "placement_plan", plan), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(current.CycleFailed):
                machine.run_cycle()
        self.assertEqual(3, len(calls))
        self.assertEqual("mission_mode_changed_while_held", machine._mission["reason_code"])
        self.assertIn("OBJECT_SECURED", machine.transitions)
        self.assertNotIn("CARRY", machine.transitions)
        self.assertFalse([call for call in machine.calls if call.startswith(("base:", "grip:open"))])


class TheMissionIsRefusedBeforeTheRobotExists(MissionCase):
    def refused(self, run, words):
        self.assertEnded(run, 3)
        self.assertEqual(([], 0), (run["sent"], run["disconnects"]), "the robot was constructed or commanded")
        self.assertIn("REFUSED before any motion", run["stdout"])
        self.assertIn(words, run["stdout"])
        self.assertNotIn(self.directory, run["stdout"], "a refusal printed a path")

    def test_a_target_file_that_does_not_parse(self):
        path = os.path.join(self.directory, "mission-target.json")
        Path(path).write_text("{}")
        self.refused(run_world(MissionWorld(), self.directory, target_path=path), "binding_missing_key")

    def test_a_target_this_invocation_cannot_carry(self):
        for name, writer, options, words in (
                ("another run", lambda d: write_target(d, "cube-a", [declared("cube-a", False)], run_id="another-run"), {}, "another run"),
                ("a capability target away from the robot", second_execution, {"remote_ip": ELSEWHERE}, "loopback"),
                ("an action other than the cycle", first_execution, {"do": "observe"}, "full cycle")):
            with self.subTest(name):
                path, _ = writer(self.directory)
                self.refused(run_world(MissionWorld(), self.directory, target_path=path, **options), words)


class TheNotPlaceableRefusalNamesItsLimit(MissionCase):
    """Plan section 8 rows 4 and 5: a block with no bounded placement is refused at SEARCH, before anything moves, with the
    limit that decided it and the shortfall in mm, not only the counts. Start 3 of 2026-09-26 is short on a side edge and
    21:17's block is past the along-axis advance budget. The scripted World's reach axis is not the recorded one, so the
    shortfalls here are the World's (14.5 and 11.5 mm), not the recorded 9.4 and 11.6 mm."""

    START_3, LATE_2117 = [0.163, -0.210], [0.064, -0.220]
    EXPECTED = ((START_3, "side_edge", 14.5), (LATE_2117, "along_budget", 11.5))

    def refused(self, run, world, events) -> tuple:
        self.assertEnded(run, 5)
        self.assertEqual(([], []), (run["sent"], run["base_pulses"]), "something moved")
        self.assertEqual("no command was sent", world.stage_at_first_send)
        self.assertNotIn("PREGRASP", run["states"])
        lines = [json.loads(line) for line in Path(events).read_text().splitlines() if line.strip()]
        finished = [line for line in lines if line["kind"] == "run.finished"][0]["payload"]
        return finished["reason_code"], {item["name"]: item["value"] for item in finished["failure"]["measurements"]}

    def assert_names_the_limit(self, run, values, constraint, deficit_mm, said=True):
        self.assertEqual((constraint, deficit_mm), (values.get("constraint"), values.get("deficit_mm")), values)
        self.assertIn(f"{deficit_mm} mm", values["explanation"])
        self.assertTrue(values["hint"])
        if said:  # a mission's BINDING sentence states it; the single-cube SEARCH sentence is the proven one and does not
            self.assertIn(values["explanation"], run["stdout"])

    def world(self, anchor):
        return MissionWorld(floor=[{"pad_m": anchor, "contact_px": CONTACT}], cube_start_pad_m=anchor, cube_final_pad_m=A_FINAL)

    def test_an_auto_target_is_refused_with_the_deciding_limit_and_the_shortfall(self):
        for anchor, constraint, deficit_mm in self.EXPECTED:
            with self.subTest(anchor=anchor):
                world, events = self.world(anchor), os.path.join(self.directory, f"auto-{constraint}-events.jsonl")
                _, run = self.mission(world, lambda d: write_target(d, "cube", [declared("cube", tb.AUTO)], carry=False), events_path=events)
                code, values = self.refused(run, world, events)
                self.assertEqual(tb.REFUSED_CANDIDATE_NOT_PLACEABLE, code)
                self.assertEqual(1, values["candidates"])
                self.assert_names_the_limit(run, values, constraint, deficit_mm)

    def test_a_declared_base_advance_target_is_refused_with_the_limit_of_the_one_cube_seen(self):
        for anchor, constraint, deficit_mm in self.EXPECTED:
            with self.subTest(anchor=anchor):
                world, events = self.world(anchor), os.path.join(self.directory, f"declared-{constraint}-events.jsonl")
                _, run = self.mission(world, lambda d: write_target(d, "cube", [declared("cube", True)]), events_path=events)
                code, values = self.refused(run, world, events)
                self.assertEqual(tb.REFUSED_NO_CANDIDATE, code)
                self.assert_names_the_limit(run, values, constraint, deficit_mm)

    def test_without_a_mission_the_search_refusal_records_the_same_limit(self):
        for anchor, constraint, deficit_mm in self.EXPECTED:
            with self.subTest(anchor=anchor):
                world, events = self.world(anchor), os.path.join(self.directory, f"legacy-{constraint}-events.jsonl")
                run = run_world(world, self.directory, events_path=events)
                code, values = self.refused(run, world, events)
                self.assertEqual("search_placement_infeasible", code)
                self.assert_names_the_limit(run, values, constraint, deficit_mm, said=False)


class WithoutAMissionTargetNothingOfThisIsReached(MissionCase):
    def test_the_legacy_run_lists_nothing_wraps_nothing_and_writes_no_result(self):
        world = MissionWorld()
        world.result_path = os.path.join(self.directory, "mission-result.json")
        run = run_world(world, self.directory)
        self.assertEnded(run, 0)
        self.assertEqual([], world.enumerated)
        self.assertEqual({"ScriptedDetector"}, set(world.detector_types))
        self.assertEqual([{}], run["made"], "make_move_base is called as it always was, with no relay")
        self.assertFalse(os.path.exists(world.result_path))
        self.assertNotIn("TARGET_SELECTED", run["states"])
        self.assertNotIn("MISSION RESULT", run["stdout"])

    def test_every_mission_hook_of_a_cycle_without_a_target_returns_at_once(self):
        machine = current.Cycle.__new__(current.Cycle)
        machine.abort, machine.move_base = {"set": True}, None
        self.assertIsNone(machine.target)
        self.assertIsNone(machine._perception_may_start())  # the flag is set, and still nothing is refused
        self.assertIsNone(machine._capability_relay_refusal(current.BASE_VX_MPS))
        self.assertEqual({}, machine._mission_placed_fields())
        self.assertEqual(5, machine.mission_finished(5))


if __name__ == "__main__":
    unittest.main()
