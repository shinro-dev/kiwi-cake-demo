#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_reach_policies_default_path.py: a DIFFERENTIAL test of the additive policies (arm_reach,
# advance_then_reach, multi_block). The controller the operator had installed on 2026-09-30 is commit 31c546c. Its source
# is read from that commit with `git show`, loaded next to the current code, and both are driven through the SAME mission
# scenarios on the scripted World with NONE of the new words named. What must be identical: everything printed to stdout,
# the transition record, every payload handed to robot.send_action, the base pulses, the base primitives made, the way
# the run ends and the mission result file. Beside that: every proven stage module, the placement solver, the target
# binding and the stage seam are byte for byte 31c546c's; the proven placement functions and the mission binding's
# functions are source for source 31c546c's; and run.config's effective constants and stage policies are 31c546c's.
# SKIPPED when the commit is not in this checkout (a shallow clone or a source archive): there is nothing to compare with.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_reach_policies_default_path.py
import ast
import contextlib
import functools
import inspect
import io
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import run_single_cube_cycle as current  # noqa: E402
import cube_candidates  # noqa: E402  (on the path through the controller's own imports)
import target_binding as tb  # noqa: E402
import test_cycle_behavior_preserved as differential  # noqa: E402  (the synthetic lerobot)
import test_cycle_mission_mode as mission  # noqa: E402  (the scripted World with several cubes, the target file writers)
import test_run_single_cube_cycle as fixtures  # noqa: E402
from observation_store import ObservationStore  # noqa: E402
from scan_arm_motion import make_bounded_move_arm  # noqa: E402

BASELINE_COMMIT = "31c546c"
CONTROLLER = "bin/laptop/manipulation/run_single_cube_cycle.py"
# The proven modules the additive policies may not change a byte of.
PROVEN_MODULES = tuple(f"autonomy/manipulation/{name}.py" for name in ("policy_search", "policy_align", "policy_grasp", "policy_placement", "policy_return", "placement_envelope",
                                                                       "target_binding", "stage_policy", "holonomic_placement", "hybrid_pick_place", "cartesian_lift"))
# The proven functions of the controller the additive policies plan beside and bind with, source for source.
PROVEN_FUNCTIONS = ("placement_plan", "base_advance_needed_mm", "release_margin_worst_mm", "release_segment_pad", "placed_margin_mm", "edge_margins_mm", "reach_direction_pad",
                    "coarse_pregrasp_from_low_pose", "required_edge_margins_mm", "axis_advance_needed_unbounded_mm", "placement_refusal_values", "gate_step", "proven_orientation_pose")
PROVEN_METHODS = ("_mission_seen_cube", "_mission_candidates", "_mission_capability_gate", "_write_bound_result", "_mission_placed", "_advance_base_held", "advance_base")
LIVE = [-0.035768012099579305, -0.2129612198129184]
LIVE_CONTACT = [454.8, 589.7]
START_3 = [0.1604, -0.2110]


def _git_show(path: str):
    result = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASELINE_COMMIT}:{path}"], capture_output=True)
    return result.stdout if result.returncode == 0 and result.stdout else None


def definitions(source: str) -> dict:
    """Every module-level function and every method of class Cycle of a controller's source, by name ("Cycle.<name>" for a
    method), as its source text: a function built from the commit's text has no file for inspect to read."""
    tree, out = ast.parse(source), {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = ast.get_source_segment(source, node, padded=True)
        elif isinstance(node, ast.ClassDef) and node.name == "Cycle":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"Cycle.{item.name}"] = ast.get_source_segment(source, item, padded=True)
    return out


@functools.lru_cache(maxsize=None)
def load_baseline():
    """The 31c546c controller as a module of its own. It imports the same stage modules as the current code, which the test
    below holds byte for byte to that commit's. None when the commit is not in this checkout."""
    source = _git_show(CONTROLLER)
    if source is None:
        return None
    module = types.ModuleType("baseline_31c546c_run_single_cube_cycle")
    module.__file__ = str(ROOT / CONTROLLER)
    exec(compile(source, f"{BASELINE_COMMIT}:{CONTROLLER}", "exec"), module.__dict__)
    return module


def run_mission(module, world, directory: str, target_path: str, extra_env=None) -> dict:
    """module.main() on the scripted World, as test_cycle_mission_mode.run_world runs the current code, for either controller."""
    observations = os.path.join(directory, "observations")
    shutil.rmtree(observations, ignore_errors=True)
    scan_cli, made = module.cli, []

    def perceive(role, image, out_dir, name, detector):
        world.detector_types.append(type(detector).__name__)
        detector.detect(image, ("a cube",))
        record = world.perception()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.json").write_text(json.dumps(record))
        return [f"{role.upper()}: synthetic perception"]

    def move_base_factory(remote_ip, robot_id, **options):
        made.append(sorted(options))
        return world.base_primitive(options)

    environment = {"KC_RUNTIME_CONFIG": "", "KC_EVENTS_PATH": "", "KC_RUN_ID": "", "KC_ARTIFACT_DIGEST": "", "KC_HOST_LEASE": "", "KC_HOST_SESSION": "", "KC_RUNTIME_MODE": "",
                   "KC_PLACEMENT_ENVELOPE": "", "KC_PLACEMENT_ENVELOPE_ATTENDED": "", "KC_MISSION_TARGET": target_path, **(extra_env or {})}
    patches = [mock.patch.object(module, "time", world.clock), mock.patch.object(module, "make_move_base", move_base_factory),
               mock.patch.object(module, "make_bounded_move_arm", lambda read_pose, send: make_bounded_move_arm(read_pose, send, sleep=world.clock.sleep, monotonic=world.clock.perf_counter)),
               mock.patch.object(scan_cli, "now_utc", world.clock.now_utc), mock.patch.object(scan_cli, "PiCamera", lambda ssh_target, local_dir: mission.CountingCamera(world)),
               mock.patch.object(scan_cli, "perceive", perceive), mock.patch.object(scan_cli, "make_detector", lambda: mission.ScriptedDetector(world)),
               mock.patch.object(cube_candidates, "enumerate_observation", world.listing), mock.patch.dict(sys.modules, differential.synthetic_lerobot(world)),
               mock.patch.dict(os.environ, environment), mock.patch.object(module, "ObservationStore", functools.partial(ObservationStore, root_dir=observations)),
               mock.patch.object(module, "HOME_FILE", Path(directory) / "calibration" / "scan-home-current.json")]
    handler, stdout, stderr = signal.getsignal(signal.SIGINT), io.StringIO(), io.StringIO()
    try:
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            try:
                ending = ("returned", module.main(["--do", "cycle", "--remote-ip", mission.LOCAL, "--robot-id", "synthetic-unit", "--pi-ssh", "synthetic-operator@" + mission.ELSEWHERE,
                                                   "--scan-id", mission.SCAN_ID]))
            except SystemExit as leaving:
                ending = ("SystemExit", leaving.code)
            except Exception as error:  # noqa: BLE001 -- a traceback is an ending too, and must be the SAME ending
                ending = (type(error).__name__, str(error))
    finally:
        signal.signal(signal.SIGINT, handler)
    records = sorted(Path(observations, mission.SCAN_ID).glob("cycle-*.json")) if os.path.isdir(os.path.join(observations, mission.SCAN_ID)) else []
    result = json.loads(Path(world.result_path).read_text()) if world.result_path and os.path.exists(world.result_path) else None
    return {"ending": ending, "stdout": stdout.getvalue(), "stderr": stderr.getvalue(), "sent": world.sent, "base_pulses": world.base_pulses, "made": made,
            "transitions": json.loads(records[-1].read_text()) if records else None, "result": result}


AUTO = [mission.declared("cube", tb.AUTO)]
SCENARIOS = {
    # (the floor, the World's extra script, the target writer: target id, targets, carry)
    "the operator's block of 2026-09-30, no capability": ([{"pad_m": LIVE, "contact_px": LIVE_CONTACT}], {}, ("cube", AUTO, False)),
    "the operator's block of 2026-09-30, with the capability": ([{"pad_m": LIVE, "contact_px": LIVE_CONTACT}], {}, ("cube", AUTO, True)),
    "an arm-only placement": ([{"pad_m": mission.A_START, "contact_px": mission.CONTACT}], {"cube_start_pad_m": mission.A_START, "cube_final_pad_m": mission.A_FINAL}, ("cube", AUTO, False)),
    "a base-advance placement through the capability": ([{"pad_m": mission.B_START, "contact_px": mission.CONTACT}], {"cube_start_pad_m": mission.B_START, "cube_final_pad_m": mission.B_FINAL},
                                                        ("cube", AUTO, True)),
    "two blocks off the pad, the nearer one needing the base": ([{"pad_m": [-0.08, -0.1325], "contact_px": [553.0, 545.0 - 48.6 * current.LOW_POSE_PX_PER_RADIAL_MM]},
                                                                 {"pad_m": mission.A_START, "contact_px": mission.CONTACT}], {"carried": 1}, ("cube", AUTO, False)),
    "two declared targets, the first execution": ([{"pad_m": mission.A_START, "contact_px": mission.CONTACT}, {"pad_m": mission.B_START, "contact_px": mission.B_CONTACT_BESIDE_A}],
                                                  {"carried": 0, "cube_start_pad_m": mission.A_START, "cube_final_pad_m": mission.A_FINAL},
                                                  ("cube-a", [mission.declared("cube-a", False), mission.declared("cube-b", True)], None)),
    "start 3 of 2026-09-26 on its side edge": ([{"pad_m": START_3, "contact_px": LIVE_CONTACT}], {}, ("cube", AUTO, False)),
    # verify r11 safety MF1, policy MF1, replay MUST-FIX 1: the floors on which the words bound a block only the long stretch
    # places over one the proven rule places (block_selection THE BINDING TIERS, its last tier), with no word
    "a block past the capped plans' budget beside one the proven rule places, with the capability": (
        [{"pad_m": [LIVE[0], -0.238], "contact_px": LIVE_CONTACT}, {"pad_m": [0.0145, -0.1925], "contact_px": mission.CONTACT}],
        {"carried": 1, "cube_final_pad_m": mission.B_FINAL}, ("cube", AUTO, True)),
    "a block by the side edge only the arm alone reaches beside one the proven rule places, with the capability": (
        [{"pad_m": [0.156, -0.157], "contact_px": [553.0, 545.0 + 1.4 * current.LOW_POSE_PX_PER_RADIAL_MM]},
         {"pad_m": [0.0145, -0.158], "contact_px": [553.0, 545.0 + 1.4 * current.LOW_POSE_PX_PER_RADIAL_MM]}],
        {"carried": 1, "cube_final_pad_m": [0.0145, -0.100]}, ("cube", AUTO, True)),
    # verify r12 safety MF1 (its floor V1), replay MUST-FIX 1: multi_block bound a block the proven policy refuses that the
    # words plan under the loaded reach cap over one the proven rule places near its budget (block_selection THE BINDING
    # TIERS), with no word
    "a block planned under the cap at SEARCH beside one the proven rule places near its budget, with the capability": (
        [{"pad_m": [0.0145, -0.234], "contact_px": [553.0, 545.0 + 11.4 * current.LOW_POSE_PX_PER_RADIAL_MM]},
         {"pad_m": [-0.08, -0.2075], "contact_px": [600.0, 545.0 - 16.6 * current.LOW_POSE_PX_PER_RADIAL_MM]}],
        {"carried": 1, "cube_final_pad_m": [-0.08, -0.088]}, ("cube", AUTO, True)),
}


class BaselineCase(unittest.TestCase):
    def setUp(self):
        self.baseline = load_baseline()
        if self.baseline is None:
            self.skipTest(f"the commit {BASELINE_COMMIT} is not in this checkout: there is nothing to compare the controller with")
        self.directory = fixtures.use_synthetic_runtime(self)
        current.use_runtime_paths(current.cli.runtime_paths({}))
        self.baseline.use_runtime_paths(self.baseline.cli.runtime_paths({}))


class TheProvenCodeIsTheInstalledCode(BaselineCase):
    def test_every_proven_module_is_byte_for_byte_the_installed_one(self):
        for path in PROVEN_MODULES:
            with self.subTest(path):
                self.assertEqual(_git_show(path), (ROOT / path).read_bytes())

    def test_every_proven_function_and_binding_method_is_source_for_source_the_installed_one(self):
        installed, now = definitions(_git_show(CONTROLLER).decode("utf-8")), definitions((ROOT / CONTROLLER).read_text(encoding="utf-8"))
        for name in PROVEN_FUNCTIONS + tuple(f"Cycle.{method}" for method in PROVEN_METHODS):
            with self.subTest(name):
                self.assertIn(name, installed)
                self.assertEqual(installed[name], now[name])
        self.assertEqual(inspect.getsource(current.placement_plan), now["placement_plan"] + "\n", "the source read here is the source the running module was built from")

    def test_run_config_states_the_same_constants_and_stage_policies_with_no_new_word(self):
        # operator ruling 2026-10-09: GRIPPER_EMPTY_LATCH_MARGIN moved 0.87 -> 0.73 on the default path (a hold read 67.633,
        # step 980, with the block squeezed between both jaws on the wrist camera, and 0.87 classed it empty). That one
        # constant is the ruled difference from the proven baseline; every other constant still matches it.
        ruled = "constant.gripper_empty_latch_margin"
        baseline, now = self.baseline.effective_constants(), current.effective_constants()
        self.assertEqual([0.87], [item["value"] for item in baseline if item["name"] == ruled])
        self.assertEqual([0.73], [item["value"] for item in now if item["name"] == ruled])
        self.assertEqual([item for item in baseline if item["name"] != ruled], [item for item in now if item["name"] != ruled])
        for environ in ({}, {current.PLACEMENT_ENVELOPE_ENV: "projected"}, {current.PLACEMENT_ENVELOPE_ENV: "scene_scan,search_sweep"}):
            with self.subTest(environ=environ):
                self.assertEqual(self.baseline.stage_policy_summary(environ), current.stage_policy_summary(environ))


class TheMissionPathWithNoNewWord(BaselineCase):
    def _run(self, module, name: str) -> dict:
        floor, script, (target_id, targets, carry) = SCENARIOS[name]
        world = mission.MissionWorld(floor=[dict(cube) for cube in floor], **script)
        path, target = mission.write_target(self.directory, target_id, [dict(entry) for entry in targets], carry=carry)
        world.result_path = target.result_path
        return run_mission(module, world, self.directory, path)

    def test_every_scenario_prints_records_commands_and_ends_as_the_installed_controller_does(self):
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                installed, now = self._run(self.baseline, name), self._run(current, name)
                self.assertEqual(installed["stdout"], now["stdout"])
                self.assertEqual(installed["transitions"], now["transitions"])
                self.assertEqual(installed["sent"], now["sent"])
                self.assertEqual(installed["base_pulses"], now["base_pulses"])
                self.assertEqual(installed["made"], now["made"])
                self.assertEqual(installed["ending"], now["ending"])
                self.assertEqual(installed["result"], now["result"])
                self.assertEqual("", now["stderr"])

    def test_the_scenarios_reach_what_their_names_say(self):
        """A differential is only as good as its scenarios: two runs that both die at the first step agree on everything."""
        expected = {"the operator's block of 2026-09-30, no capability": (5, tb.REFUSED_CANDIDATE_NOT_PLACEABLE, False),
                    "the operator's block of 2026-09-30, with the capability": (5, tb.REFUSED_CANDIDATE_NOT_PLACEABLE, False),
                    "an arm-only placement": (0, None, True), "a base-advance placement through the capability": (0, None, True),
                    "two blocks off the pad, the nearer one needing the base": (5, "mission_capability_required", False),
                    "two declared targets, the first execution": (0, None, True), "start 3 of 2026-09-26 on its side edge": (5, tb.REFUSED_CANDIDATE_NOT_PLACEABLE, False),
                    "a block past the capped plans' budget beside one the proven rule places, with the capability": (0, None, True),
                    "a block by the side edge only the arm alone reaches beside one the proven rule places, with the capability": (0, None, True),
                    "a block planned under the cap at SEARCH beside one the proven rule places near its budget, with the capability": (0, None, True)}
        self.assertEqual(set(SCENARIOS), set(expected))
        for name, (status, code, commanded) in expected.items():
            with self.subTest(scenario=name):
                run = self._run(current, name)
                self.assertEqual(("returned", status), run["ending"], run["stdout"][-2000:])
                self.assertEqual(code, run["result"]["reason_code"])
                self.assertEqual(commanded, bool(run["sent"]))
                if name == "a base-advance placement through the capability":
                    self.assertTrue(run["base_pulses"], "not vacuous: the base moved")
                if name.startswith(("a block past the capped plans' budget", "a block by the side edge only the arm alone", "a block planned under the cap at SEARCH")):
                    floor, script, _target = SCENARIOS[name]
                    selected = [entry for entry in run["transitions"] if entry["state"] == "TARGET_SELECTED"][0]
                    self.assertEqual([round(v, 4) for v in floor[script["carried"]]["pad_m"]], selected["seen_pad_m"], "the block the proven rule places is bound")
                    self.assertEqual("placed_new", run["result"]["outcome"])


# THE WORDS APPLY TO AUTO TARGETS ONLY (the lead's decision of 2026-10-01). A mission with a target that declares its mode
# (it requires the capability, or declares that it does not), and every mission with such a target beside an auto one, is
# bound, planned and executed as 31c546c binds, plans and executes it with no word, whatever words the run names: the
# additive placement policies and multi_block apply to auto targets only. The scenes are the ones the verifiers of rounds 4
# and 5 found a word changing: a mixed mission whose auto target the words bound to the block the declared target needed
# (under multi_block on the stock World; under the placement words on a pad seen 10.8 deg off its axis, as recorded starts
# see it), a target that declares the capability bound by the placement words to a block the proven rule places with the
# arm alone (the recorded start md81525f), and the declared two-target missions.
PX = current.LOW_POSE_PX_PER_RADIAL_MM
TILT_DEG = -10.84  # the pad frame turned so that image-up reads (0.188, 0.982) in PAD_FRAME (start 3 of 2026-09-26 read (0.119, 0.993))
WORD_SETS = ("arm_reach,advance_then_reach", "arm_reach,advance_then_reach,multi_block", "multi_block", "advance_then_reach")


def _turned(view: dict) -> dict:
    """A pad view whose pad frame is turned TILT_DEG about the pad point its reference pixel (553, 545) sees."""
    image_to_pad = np.asarray(view["homography_image_to_pad"], dtype=float)
    pivot = image_to_pad @ np.array([553.0, 545.0, 1.0])
    pivot = pivot[:2] / pivot[2]
    c, s = math.cos(math.radians(TILT_DEG)), math.sin(math.radians(TILT_DEG))
    turn = np.array([[c, -s, pivot[0] - c * pivot[0] + s * pivot[1]], [s, c, pivot[1] - s * pivot[0] - c * pivot[1]], [0.0, 0.0, 1.0]])
    turned = turn @ image_to_pad
    return {**view, "homography_image_to_pad": turned.tolist(), "homography_pad_to_image": np.linalg.inv(turned).tolist()}


class TiltedWorld(mission.MissionWorld):
    """The scripted World with its pad seen TILT_DEG off its axis: every reach axis reads (0.188, 0.982) in PAD_FRAME."""

    def perception(self) -> dict:
        record = super().perception()
        if record.get("pad_by_template"):
            record["pad_by_template"] = _turned(record["pad_by_template"])
        return record


def _row(coarse_mm: float) -> float:
    """The contact row at which the World's coarse estimate puts a block coarse_mm beyond the proven grasp radius."""
    return 545.0 + (11.4 - coarse_mm) * PX


DECLARED = {
    # name: (the World, (the target id, the targets, carry)); the World's carried index is the block the jaws close on
    "a mixed mission whose auto target multi_block would bind to the declared target's only block": (
        lambda: mission.MissionWorld(floor=[{"pad_m": [-0.05, -0.1575], "contact_px": [553.0, _row(0.0)]}, {"pad_m": [0.06, -0.1525], "contact_px": [553.0, _row(45.0)]}], carried=1),
        ("cube-a", [mission.declared("cube-a", tb.AUTO), mission.declared("cube-b", False)], False)),
    "a mixed mission whose auto target the placement words would bind away, its pad seen 10.8 deg off its axis": (
        lambda: TiltedWorld(floor=[{"pad_m": [-0.1775, -0.1473], "contact_px": [553.0, _row(20.9)]}, {"pad_m": [0.0, -0.1650], "contact_px": [553.0, _row(20.9)]},
                                   {"pad_m": [0.06, -0.1900], "contact_px": [553.0, _row(20.9)]}], carried=1),
        ("cube-a", [mission.declared("cube-a", tb.AUTO), mission.declared("cube-b", True)], False)),
    "a target that declares the capability and a block the proven rule places with the arm alone (md81525f)": (
        lambda: mission.MissionWorld(floor=[{"pad_m": [-0.0148, -0.1297], "contact_px": [553.0, _row(12.2)]}], cube_start_pad_m=[-0.0148, -0.1297],
                                     cube_final_pad_m=[-0.0148, -0.08]),
        ("cube", [mission.declared("cube", True)], False)),
    "the same target and block in a run that carries the capability": (
        lambda: mission.MissionWorld(floor=[{"pad_m": [-0.0148, -0.1297], "contact_px": [553.0, _row(12.2)]}], cube_start_pad_m=[-0.0148, -0.1297],
                                     cube_final_pad_m=[-0.0148, -0.08]),
        ("cube", [mission.declared("cube", True)], True)),
    "the declared two-target mission of 2026-09-26 with two blocks the proven rule places with the arm alone": (
        lambda: mission.MissionWorld(floor=[{"pad_m": [-0.060, -0.1300], "contact_px": [480.0, _row(12.2)]}, {"pad_m": [0.060, -0.1297], "contact_px": [620.0, _row(12.2)]}],
                                     carried=1, cube_start_pad_m=[0.060, -0.1297], cube_final_pad_m=[0.060, -0.100]),
        ("cube-a", [mission.declared("cube-a", False), mission.declared("cube-b", True)], False)),
    "the declared two-target mission with a block of each mode": (
        lambda: mission.MissionWorld(floor=[{"pad_m": mission.A_START, "contact_px": mission.CONTACT}, {"pad_m": [-0.06, -0.158], "contact_px": [553.0, _row(10.0)]}],
                                     carried=0, cube_start_pad_m=mission.A_START, cube_final_pad_m=mission.A_FINAL),
        ("cube-a", [mission.declared("cube-a", False), mission.declared("cube-b", True)], None)),
    "a target that declares the capability, in the run that carries it, and a block the proven rule places with a 70 mm advance": (
        lambda: mission.MissionWorld(floor=[{"pad_m": [0.0145, -0.158], "contact_px": [553.0, _row(10.0)]}], cube_start_pad_m=[0.0145, -0.158], cube_final_pad_m=[0.0145, -0.10]),
        ("cube", [mission.declared("cube", True)], True)),
    "a target that declares no capability and a block its measured grasp takes past the proven arm-alone band": (
        lambda: mission.MissionWorld(floor=[{"pad_m": [0.0145, -0.1480], "contact_px": [553.0, _row(0.0)]}], cube_start_pad_m=[0.0145, -0.1480], cube_final_pad_m=[0.0145, -0.10]),
        ("cube", [mission.declared("cube", False)], False)),
}


def _kept(run) -> dict:
    """What a run of a mission with a declared target keeps from 31c546c's run of it with no word: its ending, its result
    file, every command, the base pulses and the base primitives made, the TARGET_SELECTED record (its binding) and the
    states in order. The record of the words a run names is its own: the PLACEMENT_ENVELOPE state and the result's
    summary.placement_envelope, which the installed controller writes for any word."""
    result = json.loads(json.dumps(run["result"])) if run["result"] is not None else None
    if result is not None and isinstance(result.get("summary"), dict):
        result["summary"].pop("placement_envelope", None)
    transitions = run["transitions"] or []
    selected = [{key: value for key, value in entry.items() if key != "utc"} for entry in transitions if entry["state"] == "TARGET_SELECTED"]
    return {"ending": run["ending"], "result": result, "sent": run["sent"], "base_pulses": run["base_pulses"], "made": run["made"], "target_selected": selected,
            "states": [entry["state"] for entry in transitions if entry["state"] != "PLACEMENT_ENVELOPE"]}


class TheWordsApplyToAutoTargetsOnly(BaselineCase):
    def _run(self, module, name: str, words: str = "") -> dict:
        make_world, (target_id, targets, carry) = DECLARED[name]
        world = make_world()
        path, target = mission.write_target(self.directory, target_id, [dict(entry) for entry in targets], carry=carry)
        world.result_path = target.result_path
        return run_mission(module, world, self.directory, path, {"KC_PLACEMENT_ENVELOPE": words} if words else None)

    def test_a_mission_with_a_declared_target_runs_under_every_word_as_the_installed_controller_runs_it_with_none(self):
        for name in DECLARED:
            installed = _kept(self._run(self.baseline, name))
            for words in WORD_SETS:
                with self.subTest(scenario=name, words=words):
                    self.assertEqual(installed, _kept(self._run(current, name, words)))

    # The scenes whose cube is picked up or refused on its measured grasp, again with the jaws closing where the recorded
    # grasp under-reads put them (verify r5 replay MUST-FIX 1: +0 to +21.7 mm beyond the coarse estimate; the stock World
    # closes them 26.2 mm beyond it, past SEARCH's 22 mm charge): the approach lead at -12, 0 and +9 mm puts the grasp about
    # +0.2, +12.2 and +21.2 mm beyond the coarse estimate.
    UNDER_READ_SCENES = ("the same target and block in a run that carries the capability",
                         "a target that declares the capability, in the run that carries it, and a block the proven rule places with a 70 mm advance",
                         "a target that declares no capability and a block its measured grasp takes past the proven arm-alone band")

    def test_at_the_recorded_grasp_under_reads_a_declared_target_runs_as_the_installed_controller_runs_it(self):
        for name in self.UNDER_READ_SCENES:
            for lead in (-12.0, 0.0, 9.0):
                with mock.patch.object(self.baseline, "APPROACH_REACH_LEAD_MM", lead), mock.patch.object(current, "APPROACH_REACH_LEAD_MM", lead):
                    installed = _kept(self._run(self.baseline, name))
                    for words in ("arm_reach,advance_then_reach", "arm_reach,advance_then_reach,multi_block"):
                        with self.subTest(scenario=name, lead=lead, words=words):
                            self.assertEqual(installed, _kept(self._run(current, name, words)))

    def test_the_scenarios_reach_what_their_names_say(self):
        """Each scene ends as its name says, under the words as with none: not vacuous."""
        expected = {"a mixed mission whose auto target multi_block would bind to the declared target's only block": (5, "mission_capability_required", False),
                    "a mixed mission whose auto target the placement words would bind away, its pad seen 10.8 deg off its axis": (5, "mission_capability_required", False),
                    "a target that declares the capability and a block the proven rule places with the arm alone (md81525f)": (5, tb.REFUSED_NO_CANDIDATE, False),
                    "the same target and block in a run that carries the capability": (5, tb.REFUSED_NO_CANDIDATE, False),
                    "the declared two-target mission of 2026-09-26 with two blocks the proven rule places with the arm alone": (5, tb.REFUSED_CANDIDATE_NOT_UNIQUE, False),
                    "the declared two-target mission with a block of each mode": (0, None, True),
                    "a target that declares the capability, in the run that carries it, and a block the proven rule places with a 70 mm advance": (0, None, True),
                    "a target that declares no capability and a block its measured grasp takes past the proven arm-alone band": (5, "mission_mode_changed_while_held", True)}
        self.assertEqual(set(DECLARED), set(expected))
        for name, (status, code, commanded) in expected.items():
            for words in ("",) + WORD_SETS:
                with self.subTest(scenario=name, words=words):
                    run = self._run(current, name, words)
                    self.assertEqual(("returned", status), run["ending"], run["stdout"][-2000:])
                    self.assertEqual(code, run["result"]["reason_code"])
                    self.assertEqual(commanded, bool(run["sent"]))


if __name__ == "__main__":
    unittest.main()
