#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/replay_20260926.py: THE RECORDED-DECISION REPLAY HARNESS of the one-block session of 2026-09-26 (track P0c
# of docs/autonomous-demo/overnight-plan-2026-09-26.md). Not a test file: the two test files beside it use it.
#
#   test_recorded_pins_20260926.py   always runs. It carries the recorded NUMBERS of that session (joints, boxes, contact
#                                    pixels, pad homographies, gripper readbacks, the [STATE] fields) and nothing else.
#   test_replay_20260926.py          runs only with KC_EVIDENCE_ROOT. It reads the recorded runs themselves and proves that
#                                    the pinned numbers ARE the recorded inputs, then replays both placed runs from them.
#
# WHAT IT READS. A run's events JSON and its output JSON (the controller's stdout), and, for the two placed runs, the
# robot-runtime-data archive that holds their observation and perception records and frames. The archive is read member by
# member with tarfile, in memory: nothing is ever extracted, written or copied, and no path of the evidence is ever printed.
#
# WHAT IT YIELDS.
#   RecordedRun.decision_inputs()    each stage's decision inputs, in order: the stage, the attempt (or pulse), the
#                                    observation it names, the measured joints and tool state of the arm when it decided,
#                                    the cube record and the pad frame of that observation, the step it rests on and the
#                                    recorded [STATE] fields. A stage track feeds these to its own policy.
#   RecordedRun.script()             the run as a SCRIPT of steps (observe, arm pose, gripper, base pulse), each with the
#                                    joints the robot measured and the perception records it produced. JSON-shaped, so the
#                                    pins file can carry it as a literal.
#   replay(controller, script)       drives the CURRENT controller's Cycle.run_cycle through that script: run_pose, grip,
#                                    observe, pose and the base primitive are answered from the record, and EVERYTHING
#                                    between them (every stage decision, every planner and gate, the pinned base loop) is
#                                    the controller's own code. The result holds the [STATE] lines it printed, the poses it
#                                    commanded, the pulses it asked for and how it ended; compare_states() diffs them
#                                    against the recorded [STATE] lines.
#
# THE REPLAY RUNS THE LEGACY CYCLE (no mission target), on purpose: the mission layer (target file, candidate listing,
# capability relay) is not a stage decision and would need the robot's runtime. Three recorded differences follow, and
# each is handled by name, never by a tolerance:
#   - TARGET_SELECTED exists only in a mission run: it is not compared.
#   - PLACED carries attribution fields only in a mission run: the fields the legacy cycle states are compared.
#   - A run that carries the capability takes one stamped capture right after the HOLD_OBSERVE rise, and the loop measures
#     the first pulse against THAT frame (_advance_base_held). The legacy cycle takes no such capture, so legacy_script()
#     folds it into the rise view as its last record: _pad_frame reads the last record, so the loop measures against the
#     same frame the recorded run did.
# The retention score is computed from the archived frames when a frame source is given; without one (the pins) both lift
# views get the same synthetic picture and only the decision, not the score, is compared.
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "bin" / "laptop" / "manipulation", ROOT / "autonomy" / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

EVIDENCE_ROOT_ENV = "KC_EVIDENCE_ROOT"
EVIDENCE_DIR_NAME = "one-block-20260926"  # the evidence directory of the session, under the root or the root itself

GRASP_MISS = "console-20260926T191919Z-m8c5f9e9"  # 19:19 UTC, grasp_missed after three empty closes (item 1)
START_1 = "console-20260926T201540Z-mb3c1048"  # 20:15 UTC, placed from start 1 (item 2)
START_2 = "console-20260926T203744Z-m2476137"  # 20:37 UTC, placed from start 2, the far left (item 3)
CYCLE_RUNS = (GRASP_MISS, START_1, START_2)
PLACED_RUNS = (START_1, START_2)
ARCHIVES = {START_1: "robot-runtime-data-20260926T2015.tgz", START_2: "robot-runtime-data-20260926T2037.tgz"}

STATE_LINE = re.compile(r"^\[STATE\] (\S+) (.*)$", re.M)
EVENT_LABEL_TEXT = re.compile(r"[^A-Za-z0-9_.+ -]")  # what the events keep of a label (common#/$defs/named_value.value)
REFUSED_POSE = re.compile(r"scan pose (\S+) refused")
MISSION_ONLY_STATES = frozenset({"TARGET_SELECTED"})
# The recorded runs carry the attribution fields of their day; a mission run since the pad policy of 2026-09-29 states
# on_pad_cubes and the release agreement's two numbers in place of the retired unclaimed_placed_cubes and
# earlier_targets_undisturbed. Both sets are the mission's, never the legacy cycle's.
MISSION_ONLY_PLACED_FIELDS = frozenset({"target_id", "attributed", "unclaimed_placed_cubes", "earlier_targets_undisturbed", "attribution_reason",
                                        "on_pad_cubes", "release_distance_mm", "release_tolerance_mm"})
SCAN_JOINT_NAMES = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")


# ---------------------------------------------------------------------------------------------------------------------
# Where the evidence is
# ---------------------------------------------------------------------------------------------------------------------
def evidence_dir(environ=None) -> Optional[Path]:
    """The session's evidence directory, or None. KC_EVIDENCE_ROOT may name it or a directory that holds it under its own
    name; it is recognized by the events of start 1, never by anything else, and nothing is read before that."""
    environ = os.environ if environ is None else environ
    root = environ.get(EVIDENCE_ROOT_ENV)
    if not root:
        return None
    for candidate in (Path(root), Path(root) / EVIDENCE_DIR_NAME):
        if (candidate / f"{START_1}.events.json").is_file():
            return candidate
    return None


class EvidenceArchive:
    """One robot-runtime-data archive, read in memory. Members are found by their run directory and their name; nothing
    is extracted."""

    def __init__(self, path: Path):
        self._tar = tarfile.open(path, "r:gz")
        self._members = {member.name.lstrip("./"): member for member in self._tar.getmembers() if member.isfile()}

    def close(self) -> None:
        self._tar.close()

    def _read(self, name: str) -> Optional[bytes]:
        member = self._members.get(name)
        if member is None:
            return None
        handle = self._tar.extractfile(member)
        return None if handle is None else handle.read()

    def json(self, name: str):
        data = self._read(name)
        return None if data is None else json.loads(data.decode("utf-8"))

    def observation(self, run_id: str, observation_id: str):
        return self.json(f"observations/{run_id}/{observation_id}.json")

    def perception(self, run_id: str, observation_id: str):
        return self.json(f"observations/{run_id}/perception/{observation_id}.json")

    def frame_bytes(self, run_id: str, observation_id: str) -> Optional[bytes]:
        return self._read(f"observations/{run_id}/frames/{observation_id}.png")

    def cycle_record(self, run_id: str):
        names = sorted(name for name in self._members if name.startswith(f"observations/{run_id}/cycle-") and name.endswith(".json"))
        return self.json(names[-1]) if names else None


# ---------------------------------------------------------------------------------------------------------------------
# One recorded run
# ---------------------------------------------------------------------------------------------------------------------
def _cube_record(measurements: dict) -> dict:
    """The recognizer's result as the controller reads it: detected, the box and the contact pixel, the clipping flags."""
    box = [measurements.get(f"bbox_{k}") for k in ("x0", "y0", "x1", "y1")]
    contact = [measurements.get("contact_x"), measurements.get("contact_y")] if measurements.get("contact_available") else None
    return {"detected": all(v is not None for v in box), "bbox_xyxy": box if all(v is not None for v in box) else None, "contact_px": contact,
            "clipped": {k[len("clipped_"):]: v for k, v in measurements.items() if k.startswith("clipped_")}}


def perception_record(observation_id: str, cube: dict, pad_frame_valid: bool, pad_by_template=None, cube_by_template=None) -> dict:
    """The minimal perception record the controller consumes: the cube, the pad frame (the two homographies only where the
    record holds them) and the cube in PAD_FRAME. The controller reads nothing else of a record."""
    template = {"pad_frame_valid": bool(pad_frame_valid)}
    if pad_by_template and pad_by_template.get("homography_image_to_pad") and pad_by_template.get("homography_pad_to_image"):
        template.update(homography_image_to_pad=pad_by_template["homography_image_to_pad"], homography_pad_to_image=pad_by_template["homography_pad_to_image"])
    return {"observation_id": observation_id, "cube": {k: cube.get(k) for k in ("detected", "bbox_xyxy", "contact_px")}, "pad_by_template": template,
            "cube_by_template": cube_by_template}


@dataclass
class DecisionInput:
    """What one stage decided from, in the order the run decided it."""
    index: int  # position of the [STATE] line in the run
    stage: str
    attempt: Optional[int]  # the state's attempt, or its pulse, when it has one
    observation_id: Optional[str]  # the observation the transition names (its evidence)
    joints: Optional[dict]  # the arm's measured joints when it decided: the latest view before the transition
    tool: Optional[dict]  # tool_state of those joints
    cube: Optional[dict]  # the cube record of the named observation
    pad_frame: Optional[dict]  # the pad frame of the named observation (with homographies when the evidence holds them)
    step: Optional[dict]  # the recorded step the decision followed (a view, a close, a pulse)
    recorded: dict = field(default_factory=dict)  # the [STATE] fields as recorded


class RecordedRun:
    """One recorded run of the session, read-only."""

    def __init__(self, directory: Path, run_id: str, archive: Optional[EvidenceArchive] = None):
        self.run_id = run_id
        self.events = json.loads((directory / f"{run_id}.events.json").read_text())["events"]
        self.stdout = json.loads((directory / f"{run_id}.output.json").read_text())["text"]
        self.archive = archive
        self.states = [(match.group(1), json.loads(match.group(2))) for match in STATE_LINE.finditer(self.stdout)]
        self.transitions = [event for event in self.events if event["kind"] == "state.transition" and event["payload"].get("machine") == "cycle"]
        if [name for name, _fields in self.states] != [event["payload"]["to_state"] for event in self.transitions]:
            raise ValueError(f"{run_id}: the [STATE] lines and the state.transition events do not name the same states in the same order")
        self._built = None

    @classmethod
    def load(cls, directory: Path, run_id: str):
        name = ARCHIVES.get(run_id)
        archive = EvidenceArchive(directory / name) if name and (directory / name).is_file() else None
        return cls(directory, run_id, archive)

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()

    # ------------------------------------------------------------------ the events, read once
    def _build(self):
        """Walk the events in order and assemble the steps. An arm pose, a gripper move and a base pulse are the actions the
        controller planned (action.planned or action.refused); a wrist observation outside any action is an observe()."""
        if self._built is not None:
            return self._built
        cube, centre, pad_valid = {}, {}, {}
        for event in self.events:
            if event["kind"] != "perception.result":
                continue
            payload, observation = event["payload"], event["observation_id"]
            values = {m["name"]: m["value"] for m in payload.get("measurements") or []}
            if payload["detector"] == "cube-recognizer":
                cube[observation] = _cube_record(values)
            elif payload["detector"] == "cube-in-pad-frame" and payload.get("result") == "detected":
                centre[observation] = {"cube_center_pad_m": [values["cube_center_pad_x"], values["cube_center_pad_y"]], "inside_pad_outline": bool(values.get("inside_pad_outline"))}
            elif payload["detector"] == "pad-print-landmarks":
                pad_valid[observation] = bool(values.get("pad_frame_valid"))

        def record(observation: str) -> dict:
            full = self.archive.perception(self.run_id, observation) if self.archive is not None else None
            template = (full or {}).get("pad_by_template") or {}
            by_template = (full or {}).get("cube_by_template") or centre.get(observation)
            if by_template is not None:
                by_template = {"cube_center_pad_m": by_template.get("cube_center_pad_m"), "inside_pad_outline": bool(by_template.get("inside_pad_outline"))}
            return perception_record(observation, cube.get(observation, {"detected": False}), pad_valid.get(observation, False), template, by_template)

        def post_capture(observation: str, fallback):
            full = self.archive.observation(self.run_id, observation) if self.archive is not None else None
            joints = ((full or {}).get("robot_state") or {}).get("measured_joints_post_capture_deg")
            return dict(joints) if joints else (dict(fallback) if fallback else None)

        steps, open_steps, last_joints, start_joints, home, marks = [], {}, None, None, None, []
        observation_step = {}
        for event in self.events:
            kind, payload, action = event["kind"], event["payload"], event.get("action_id")
            if kind == "measured.state":
                last_joints = dict(payload["joints"])
                if start_joints is None and not steps:
                    start_joints = dict(last_joints)
                if action in open_steps:
                    step = open_steps[action]
                    step["joints"] = dict(last_joints)
                    if step["kind"] == "gripper":
                        step["readback"] = float(last_joints["gripper"])
            elif kind == "action.planned":
                parameters = {p["name"]: p["value"] for p in payload.get("parameters") or []}
                step = {"kind": payload["action_type"], "records": []}
                if step["kind"] == "arm_pose":
                    step.update(label=parameters.get("label"), pose_id=parameters.get("pose_id"),
                                target={k: parameters[f"target.{k}"] for k in SCAN_JOINT_NAMES if f"target.{k}" in parameters})
                    if parameters.get("pose_id") == "pregrasp-return-low-L0" and home is None:
                        home = dict(step["target"])
                elif step["kind"] == "gripper":
                    step.update(label=str(parameters.get("label", "")).replace("grip ", "", 1))
                elif step["kind"] == "base_pulse":
                    step.update(vx_mps=parameters.get("vx_mps"), duration_s=parameters.get("duration_s"))
                open_steps[action] = step
                steps.append(step)
                marks.append(event["seq"])
            elif kind == "action.refused":
                detail = str(payload.get("detail", ""))
                found = REFUSED_POSE.search(detail)
                steps.append({"kind": "refused", "action_type": payload.get("action_type"), "pose_id": found.group(1) if found else None, "detail": detail})
                marks.append(event["seq"])
            elif kind == "observation.captured" and str(event.get("observation_id", "")).startswith("obs-"):
                observation = event["observation_id"]
                if action in open_steps:
                    open_steps[action]["records"].append(record(observation))
                    open_steps[action].setdefault("observation_ids", []).append(observation)
                    observation_step[observation] = open_steps[action]
                else:
                    step = {"kind": "observe", "records": [record(observation)], "observation_ids": [observation], "joints": post_capture(observation, last_joints)}
                    steps.append(step)
                    marks.append(event["seq"])
                    observation_step[observation] = step
        for step in steps:  # a gripper move returns observe()'s view: the joints read AFTER its capture, when the archive holds them
            if step["kind"] == "gripper" and step.get("observation_ids"):
                step["joints"] = post_capture(step["observation_ids"][-1], step.get("joints"))
        self._built = {"steps": steps, "marks": marks, "start_joints": start_joints, "home": home, "observation_step": observation_step}
        return self._built

    # ------------------------------------------------------------------ what the harness yields
    def script(self) -> dict:
        """The run as a script: where it started, the saved low pose it returned to, its steps and its [STATE] lines."""
        built = self._build()
        order = ("kind", "label", "pose_id", "target", "vx_mps", "duration_s", "readback", "action_type", "detail", "joints", "observation_ids", "records")
        steps = [{key: step[key] for key in order if key in step} for step in built["steps"]]
        return {"run_id": self.run_id, "start_joints": built["start_joints"], "home": built["home"], "steps": steps,
                "states": [[name, fields] for name, fields in self.states]}

    def decision_inputs(self) -> list:
        import run_single_cube_cycle as controller

        built = self._build()
        out = []
        for index, ((name, fields), event) in enumerate(zip(self.states, self.transitions)):
            before = [step for mark, step in zip(built["marks"], built["steps"]) if mark < event["seq"] and step.get("joints")]
            step = before[-1] if before else None
            joints = step["joints"] if step else built["start_joints"]
            observation = event.get("observation_id")
            evidence = built["observation_step"].get(observation)
            named = next((r for r in (evidence or {}).get("records", []) if r["observation_id"] == observation), None)
            frame = (named or {}).get("pad_by_template")
            out.append(DecisionInput(index=index, stage=name, attempt=fields.get("attempt", fields.get("pulse")), observation_id=observation, joints=joints,
                                     tool=controller.tool_state(joints) if joints else None, cube=(named or {}).get("cube"),
                                     pad_frame=frame if frame and frame.get("pad_frame_valid") else None, step=step, recorded=fields))
        return out

    def saved_pan_deg(self) -> Optional[float]:
        """The pan of the saved low scan pose this run was decided against: the target of its return to the low pose, or,
        for a run that never returned, what its return-bound predicate measured from the coarse pregrasp pan (the side
        nearer the pan it started from)."""
        home = self._build()["home"]
        if home is not None:
            return float(home["shoulder_pan"])
        coarse = next((fields["coarse"]["pan_deg"] for name, fields in self.states if name == "PREGRASP"), None)
        bound = next((event["payload"]["measured"] for event in self.events if event["kind"] == "predicate.evaluated" and event["payload"].get("predicate") == "pregrasp_pan_within_return_bound"), None)
        if coarse is None or bound is None:
            return None
        start = float(self._build()["start_joints"]["shoulder_pan"])
        return min((coarse + bound, coarse - bound), key=lambda pan: abs(pan - start))

    def frame_source(self):
        """observation id -> the archived frame as an image, or None without the archive."""
        if self.archive is None:
            return None

        def frame(observation_id: str):
            import cv2
            import numpy as np

            data = self.archive.frame_bytes(self.run_id, observation_id)
            return None if data is None else cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

        return frame


# ---------------------------------------------------------------------------------------------------------------------
# The replay: the CURRENT controller driven through a recorded script
# ---------------------------------------------------------------------------------------------------------------------
def legacy_script(script: dict) -> dict:
    """The script as the legacy cycle asks for it: the capability run's stamped capture after the HOLD_OBSERVE rise is
    folded into the rise view as its last record (the module docstring says why)."""
    steps, out, index = [dict(step) for step in script["steps"]], [], 0
    while index < len(steps):
        step = steps[index]
        out.append(step)
        if step["kind"] == "arm_pose" and str(step.get("label", "")).startswith("HOLD_OBSERVE") and index + 1 < len(steps) and steps[index + 1]["kind"] == "observe":
            step["records"] = list(step["records"]) + list(steps[index + 1]["records"])
            step["folded_capture"] = steps[index + 1].get("observation_ids")
            index += 1
        index += 1
    return {**script, "steps": out}


def event_label(label: str) -> str:
    return EVENT_LABEL_TEXT.sub("", str(label))[:96]


@dataclass
class ReplayResult:
    transitions: list  # the controller's own transition records (state, utc, fields)
    stdout: str
    summary: Optional[dict]  # what run_cycle returned, or None when it raised
    error: Optional[BaseException]  # the CycleFailed (or anything else) it raised
    commanded: list  # (event label, pose id, target joints) of every arm pose the controller asked for, refused ones included
    pulses: list  # (vx_mps, duration_s) of every base pulse
    mismatches: list  # every place where what the controller asked for is not what the record did next
    left_over: list  # recorded steps the controller never asked for

    @property
    def states(self) -> list:
        return [[entry["state"], {k: v for k, v in entry.items() if k not in ("state", "utc")}] for entry in self.transitions]


class _Divergence(Exception):
    """The controller asked for something the record did not do next: the replay stops there."""


def replay(controller, script: dict, frames=None, legacy: bool = True) -> ReplayResult:
    """Run controller.Cycle.run_cycle against the recorded script. frames: observation id -> image (the archive), or None
    for a synthetic picture. Nothing reaches a robot, a camera, a file outside a temporary directory or the network."""
    import numpy as np

    script = legacy_script(script) if legacy else script
    steps = list(script["steps"])
    commanded, pulses, mismatches = [], [], []
    current = {"joints": dict(script["start_joints"])}
    machine = controller.Cycle.__new__(controller.Cycle)
    machine.transitions, machine.plan_only, machine.base_settle_s, machine.abort = [], False, 0.0, {"set": False}
    machine.runtime_live, machine.holding = False, False

    def next_step(kind: str, what: str) -> dict:
        if not steps:
            mismatches.append(f"the controller asked for {what} after the record ended")
            raise _Divergence(what)
        step = steps.pop(0)
        wanted = {"arm_pose": ("arm_pose", "refused"), "gripper": ("gripper",), "base_pulse": ("base_pulse",), "observe": ("observe",)}[kind]
        if step["kind"] not in wanted:
            mismatches.append(f"the controller asked for {what}; the record did {step['kind']} {step.get('label') or step.get('pose_id') or ''}".rstrip())
            raise _Divergence(what)
        return step

    def view(step: dict) -> dict:
        joints = dict(step["joints"])
        current["joints"] = joints
        return {"measured": joints, "tool": controller.tool_state(joints), "perception": [dict(record) for record in step["records"]]}

    def run_pose(target, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
        poses = list(target) if isinstance(target, (list, tuple)) else [target]
        last = poses[-1]
        entry = (event_label(label), getattr(last, "pose_id", None), {k: float(last.joints[k]) for k in SCAN_JOINT_NAMES if k in last.joints})
        commanded.append(entry)
        step = next_step("arm_pose", f"the arm pose {entry[0]!r} ({entry[1]})")
        if step["kind"] == "refused":
            if step.get("pose_id") and step["pose_id"] != entry[1]:
                mismatches.append(f"the record refused {step['pose_id']} where the controller planned {entry[1]}")
            raise controller.ScanRefused(step["detail"])
        if step.get("label") != entry[0]:
            mismatches.append(f"the controller asked for {entry[0]!r}; the record ran {step.get('label')!r}")
        if step.get("pose_id") != entry[1]:
            mismatches.append(f"{entry[0]}: the controller planned pose {entry[1]}; the record planned {step.get('pose_id')}")
        for joint, value in (step.get("target") or {}).items():
            if joint in entry[2] and abs(entry[2][joint] - float(value)) > 5e-4:
                mismatches.append(f"{entry[0]}: target {joint} {entry[2][joint]:.4f} planned now, {float(value):.4f} recorded")
        machine.holding = holding
        return view(step)

    def grip(target, label):
        step = next_step("gripper", f"the gripper {label}")
        if step.get("label") != label:
            mismatches.append(f"the controller asked the gripper to {label}; the record moved it to {step.get('label')}")
        return {"gripper": float(step["readback"]), **view(step)}

    def observe(holding):
        return view(next_step("observe", "an observation"))

    def move_base(vx_mps, duration_s):
        step = next_step("base_pulse", f"a base pulse {vx_mps:+.2f} m/s for {duration_s:.3f} s")
        pulses.append((vx_mps, duration_s))
        if abs(float(step["vx_mps"]) - vx_mps) > 1e-12 or abs(float(step["duration_s"]) - duration_s) > 1e-9:
            mismatches.append(f"the controller pulsed {vx_mps:+.2f} m/s for {duration_s:.6f} s; the record pulsed {step['vx_mps']:+.2f} m/s for {float(step['duration_s']):.6f} s")
        current["joints"] = dict(step["joints"])
        return True

    synthetic = np.full((720, 1280), 120, np.uint8)  # a scene rigid with the camera: the same textured picture for both lift views
    synthetic[300:660, 380:800] = 200
    synthetic[300:380, 380:800] = 90

    def frame_of(result):
        if frames is None:
            return synthetic.copy()
        return frames(result["perception"][-1]["observation_id"])

    machine.pose = lambda: dict(current["joints"])
    machine.run_pose, machine.grip, machine.observe = run_pose, grip, observe
    machine.move_base, machine.base_is_zero, machine._frame = move_base, (lambda: True), frame_of

    home = {k: float(v) for k, v in script["home"].items()}
    summary, error, printed = None, None, io.StringIO()
    with tempfile.TemporaryDirectory(prefix="kc-replay-") as scratch:
        home_file = Path(scratch) / "scan-home.json"
        home_file.write_text(json.dumps({"name": "recorded saved low pose of 2026-09-26", "measured_joints_deg": home}))
        with mock.patch.object(controller, "HOME_FILE", home_file), mock.patch.object(controller.cli, "HOME_FILE", home_file), redirect_stdout(printed):
            try:
                summary = machine.run_cycle()
            except _Divergence as divergence:
                error = divergence
            except Exception as failure:  # noqa: BLE001 -- a CycleFailed is a recorded outcome; anything else is reported, not hidden
                error = failure
    return ReplayResult(transitions=list(machine.transitions), stdout=printed.getvalue(), summary=summary, error=error, commanded=commanded, pulses=pulses,
                        mismatches=mismatches, left_over=steps)


def compare_states(recorded: list, replayed: list, compare_retention: bool) -> list:
    """Every difference between the recorded [STATE] lines and the replayed ones, as sentences; [] when they agree. The
    mission-only state and fields are left out by name (the module docstring); nothing else is."""
    wanted = [(name, fields) for name, fields in recorded if name not in MISSION_ONLY_STATES]
    got = [(name, fields) for name, fields in replayed if name not in MISSION_ONLY_STATES]
    differences = []
    if [name for name, _ in wanted] != [name for name, _ in got]:
        differences.append(f"the states differ: recorded {[name for name, _ in wanted]}, replayed {[name for name, _ in got]}")
    for index, ((name, fields), (_other, now)) in enumerate(zip(wanted, got)):
        keys = set(fields) | set(now)
        if name == "PLACED":
            keys -= MISSION_ONLY_PLACED_FIELDS
        if name == "PLACEMENT_PLAN":
            keys.discard("bound_mode")  # the mission's binding, stated beside the plan; the plan itself is compared
        if name == "OBJECT_SECURED" and not compare_retention:
            keys.discard("retention_score")
        for key in sorted(keys):
            if key not in fields or key not in now:
                differences.append(f"{index} {name}.{key}: {'recorded only' if key in fields else 'replayed only'}")
            elif fields[key] != now[key]:
                differences.append(f"{index} {name}.{key}: recorded {fields[key]!r}, replayed {now[key]!r}")
    return differences


# ---------------------------------------------------------------------------------------------------------------------
# Decisions the controller takes inline, RESTATED here so that a pin can name them: each mirrors one line of run_cycle
# with the controller's own constants, and a pin through them checks the restatement, not the controller's line. What
# checks the controller's lines themselves, and what does not:
#   - APPROACH (grasp_forward_mm): the replay of the placed runs runs this line only on positive targets (their grasp
#     poses are fwd+29 and fwd+54), so its lower clamp branch is never run by the replay, and no test in
#     autonomy/tests guards that branch (a changed clamp floor leaves the suite green).
#   - GRASP_RETRY_ADVANCE (forward_mm): neither placed run retries, so the replay never runs this line; the default
#     path's retry line is guarded by test_run_single_cube_cycle.py and test_cycle_behavior_preserved.py.
# ---------------------------------------------------------------------------------------------------------------------
def approach_target_mm(controller, tool_out_mm: float, radial_mm: float) -> dict:
    """APPROACH: the grasp target from the last ALIGN view (the tool radius plus the ALIGN radial plus the reach lead),
    clamped to the proven grasp pose's range. Returns the unclamped value beside the one the controller commands."""
    wanted = float(tool_out_mm) + float(radial_mm) + controller.APPROACH_REACH_LEAD_MM
    return {"unclamped_mm": wanted, "target_mm": min(max(wanted, 0.0), controller.GRASP_POSE_MAX_FORWARD_M * 1000.0)}


def retry_forward_mm(controller, reopened_out_mm: float) -> float:
    """GRASP_RETRY_ADVANCE: after an empty close the next close is 10 + 4 mm beyond the reopened tool radius."""
    return min(float(reopened_out_mm) + 10.0 + 4.0, controller.GRASP_POSE_MAX_FORWARD_M * 1000.0)


# ---------------------------------------------------------------------------------------------------------------------
# The pinned numbers, regenerated from the evidence: python3 autonomy/tests/replay_20260926.py --pins
# ---------------------------------------------------------------------------------------------------------------------
def grasp_miss_inputs(run: RecordedRun) -> dict:
    """The numbers the pins of run m8c5f9e9 rest on: the SEARCH contact pixel and pan, the saved low pose's pan, every
    ALIGN view (box and joints), every close (readback) and reopen (joints), the APPROACH pose the controller commanded
    (its pose id and the joints it was commanded from) and the GRASP_RETRY_ADVANCE targets it stated."""
    inputs = run.decision_inputs()
    search = next(item for item in inputs if item.stage == "PREGRASP")
    closes = [item for item in inputs if item.stage == "GRASP_CLOSE"]
    steps = run.script()["steps"]
    reopens = [step for step in steps if step["kind"] == "gripper" and step.get("label") == "open"]
    arm = [step for step in steps if step["kind"] == "arm_pose"]
    approach = next(index for index, step in enumerate(arm) if step.get("label") == "APPROACH proven grasp geometry")
    return {"run_id": run.run_id, "search_pan_deg": run.script()["start_joints"]["shoulder_pan"], "home_pan_deg": run.saved_pan_deg(),
            "search_contact_px": search.cube["contact_px"],
            "align": [{"bbox_xyxy": item.cube["bbox_xyxy"], "joints": item.joints} for item in inputs if item.stage == "ALIGN"],
            "closes": [item.step["readback"] for item in closes], "reopened_joints": [step["joints"] for step in reopens],
            "approach_pose_id": arm[approach]["pose_id"], "approach_from_joints": arm[approach - 1]["joints"],
            "retry_to_forward_mm": [fields["to_forward_mm"] for name, fields in run.states if name == "GRASP_RETRY_ADVANCE"]}


PINS_BEGIN = "# BEGIN RECORDED NUMBERS: generated by python3 autonomy/tests/replay_20260926.py --pins; do not edit by hand"
PINS_END = "# END RECORDED NUMBERS"


def _literal(value) -> str:
    return repr(value)  # dicts, lists, str, float, int, bool and None only: repr is a Python literal, and a float's repr is exact


def pins_text(directory: Path) -> str:
    """The block the pins file carries between PINS_BEGIN and PINS_END, from the evidence: one step or one [STATE] line
    per line, so that a change to a recorded number shows as the one line it changes."""
    runs = {run_id: RecordedRun.load(directory, run_id) for run_id in CYCLE_RUNS}
    try:
        placed = {run_id: runs[run_id].script() for run_id in PLACED_RUNS}
        miss = grasp_miss_inputs(runs[GRASP_MISS])
    finally:
        for run in runs.values():
            run.close()
    lines = [PINS_BEGIN, "GRASP_MISS_INPUTS = {"]
    lines += [f"    {_literal(key)}: {_literal(value)}," for key, value in miss.items()]
    lines += ["}", "", "PLACED_SCRIPTS = {"]
    for run_id, script in placed.items():
        lines += [f"    {_literal(run_id)}: {{", f"        'run_id': {_literal(script['run_id'])},", f"        'start_joints': {_literal(script['start_joints'])},",
                  f"        'home': {_literal(script['home'])},", "        'steps': ["]
        lines += [f"            {_literal(step)}," for step in script["steps"]]
        lines += ["        ],", "        'states': ["]
        lines += [f"            {_literal(state)}," for state in script["states"]]
        lines += ["        ],", "    },"]
    lines += ["}", PINS_END]
    return "\n".join(lines) + "\n"


PINS_FILE = Path(__file__).resolve().parent / "test_recorded_pins_20260926.py"


def splice_pins(path: Path, block: str) -> None:
    """Replace the block between PINS_BEGIN and PINS_END of the pins file by `block` (which carries both markers)."""
    text = path.read_text()
    start, end = text.index(PINS_BEGIN), text.index(PINS_END) + len(PINS_END) + 1
    path.write_text(text[:start] + block + text[end:])


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    directory = evidence_dir()
    if directory is None:
        print(f"{EVIDENCE_ROOT_ENV} does not name the evidence of the session (or a directory holding {EVIDENCE_DIR_NAME})", file=sys.stderr)
        return 2
    if argv[:1] == ["--pins"]:
        print(pins_text(directory), end="")
        return 0
    if argv[:1] == ["--write-pins"]:
        splice_pins(PINS_FILE, pins_text(directory))
        print(f"rewrote the recorded numbers of {PINS_FILE.relative_to(ROOT)}")
        return 0
    import run_single_cube_cycle as controller

    for run_id in PLACED_RUNS:
        run = RecordedRun.load(directory, run_id)
        try:
            result = replay(controller, run.script(), run.frame_source())
            differences = compare_states(run.script()["states"], result.states, compare_retention=True)
            print(f"{run_id}: {len(result.states)} states replayed, {len(differences)} difference(s), {len(result.mismatches)} command mismatch(es), "
                  f"{len(result.left_over)} recorded step(s) left over, ended {'placed' if result.error is None else type(result.error).__name__}")
            for line in differences + result.mismatches:
                print(f"  {line}")
        finally:
            run.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
