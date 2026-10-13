# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""autonomy/tests/stage_harness.py: a stage policy procedure run alone, on a scripted context, with a call log.

A stage procedure (policy_search.run_search, policy_align.run_pregrasp_align, policy_grasp.run_grasp,
policy_placement.run_placement, policy_return.run_return) takes ctx, the Cycle, and ctl, the controller's namespace
(autonomy/manipulation/stage_policy.py). HarnessCycle is a REAL Cycle, built without __init__ (so no robot, camera or
store is made), whose primitives are replaced:

  run_pose   arrives at the planned target (every joint of the target, the rest of the pose held), unless the script
             answers the label; the view's perception is the script's answer for "perception:<label>" (default none), a
             list of records per call, so a script names it as a list of such lists: {"perception:PREGRASP": [[record]]}
  grip       the gripper reaches the script's answer for "grip:<label>" (a number, or a whole result)
  observe    the script's answer for "observe"
  _frame     the script's answer for "frame"
  advance_base, return_base   the script's answers (advance_base defaults to "the base did not move")

Everything else a stage calls on ctx is the Cycle's own code: state() (the transition record and the [STATE] line),
failed() (the reason code), the delegating helpers (_search_sweep, _refuse_*, _carry_arm_only, ...), _cube, _pad_frame,
_why_no_cube, _envelope_fields. The events object records every hook call instead of emitting.

A script answer is keyed by a label or a label prefix (the longest matching key wins). A list answer is consumed in order
and fails the test when it runs out; any other value (or a callable of (ctx, *call arguments)) answers every call. Every
primitive call is appended to ctx.calls as (kind, label).

Tracks extend it by subclassing HarnessCycle (or RecordingEvents) in their own test files.
Run the self-test directly: python3 autonomy/tests/test_stage_seams.py
"""
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import run_single_cube_cycle as cycle  # noqa: E402
import stage_policy  # noqa: E402

# A measured pose of each phase, recorded on the robot (2026-09-19) and used by the scripted machine of
# test_run_single_cube_cycle.py: the geometry gates the stages call run for real on them.
LOW_POSE = {"shoulder_pan": -9.58, "shoulder_lift": -79.4, "elbow_flex": 34.4, "wrist_flex": 68.5, "wrist_roll": -97.3, "gripper": 79.9}
PREGRASP_POSE = {**LOW_POSE, "shoulder_pan": -9.495, "shoulder_lift": 7.429, "elbow_flex": 49.451, "wrist_flex": 8.044}
DESCENDED_POSE = {**PREGRASP_POSE, "shoulder_lift": 21.495, "elbow_flex": 49.626, "wrist_flex": -4.0}
GRASP_POSE = {**PREGRASP_POSE, "shoulder_lift": 41.451, "elbow_flex": 50.330, "wrist_flex": -28.088}
HELD_POSE = {**GRASP_POSE, "shoulder_lift": 41.0, "elbow_flex": 50.0, "wrist_flex": -28.0, "gripper": 68.4}


def controller_view(**overrides) -> stage_policy.ControllerView:
    """The controller's live namespace, with `overrides` in front of it (a constant, a formula, the envelope words).

    An override reaches what the stage reads through the ctl it is handed. It does not reach a helper the stage calls
    through a ctx delegator (ctx._search_sweep, ctx._refuse_*, ctx._carry_arm_only, ...): those hand the helper the
    controller's own view. For a name such a helper reads, patch the controller module (mock.patch.object(cycle, NAME,
    value)), which reaches the stage and the helper alike."""
    return stage_policy.ControllerView(collections.ChainMap(dict(overrides), vars(cycle)))


def view(measured: dict, perception=None, tool=None) -> dict:
    """A run_pose / observe result: the measured pose, its tool state (the controller's FK unless given) and perception."""
    return {"measured": dict(measured), "tool": dict(tool) if tool is not None else cycle.tool_state(measured), "perception": list(perception or [])}


def pad_frame(advance_mm: float = 0.0, lateral_mm: float = 0.0, scale: float = 0.0004) -> dict:
    """A valid synthetic low-pose PAD_FRAME (the scripted machine's): pixel (553, 545) sees PAD (0.0145, -0.1825), image-up is
    pad +y; a base that advanced by advance_mm sees every fixed pixel that much deeper in the pad."""
    import numpy as np

    image_to_pad = np.array([[scale, 0.0, 0.0145 - 553.0 * scale + lateral_mm / 1000.0], [0.0, -scale, -0.1825 + 545.0 * scale + advance_mm / 1000.0], [0.0, 0.0, 1.0]])
    return {"pad_identity": True, "pad_frame_valid": True, "homography_image_to_pad": image_to_pad.tolist(), "homography_pad_to_image": np.linalg.inv(image_to_pad).tolist()}


def cube_record(bbox_xyxy=None, contact_px=None, cube_center_pad_m=None, pad_frame=None, **extra) -> dict:
    """One frozen perception record: a detected cube with its box (None: nothing detected), its contact pixel, its PAD_FRAME
    centre, and a pad_by_template (pad_frame: a dict, e.g. a test's homography pair; None: not valid)."""
    detected = bbox_xyxy is not None
    record = {"cube": {"detected": detected, "bbox_xyxy": list(bbox_xyxy) if detected else None, "contact_px": list(contact_px) if contact_px else None,
                       "notes": [] if detected else ["harness: no proposal verified"]},
              "pad_by_template": dict(pad_frame) if pad_frame else {"pad_identity": True, "pad_frame_valid": False, "reason": "harness"},
              "cube_by_template": {"cube_center_pad_m": list(cube_center_pad_m)} if cube_center_pad_m is not None else None, "frame": "harness-frame",
              "observation_id": extra.pop("observation_id", "harness-observation")}
    record.update(extra)
    return record


class ScriptExhausted(AssertionError):
    """A primitive was called more often than the script answered it."""


class RecordingEvents:
    """The events object of a harness run: every hook call is recorded as (name, args, kwargs), nothing is emitted."""

    def __init__(self):
        self.calls = []
        self.base_aside_mm = None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record

    def pad_measurements(self, holding: bool) -> list:
        self.calls.append(("pad_measurements", (holding,), {}))
        return []

    def predicates(self, name=None) -> list:
        """The predicate calls in order, as dicts of their keywords plus `predicate` and `value`; only `name`'s when given."""
        found = [{"predicate": args[0], "value": args[1], **kwargs} for kind, args, kwargs in self.calls if kind == "predicate"]
        return [entry for entry in found if name is None or entry["predicate"] == name]


class HarnessCycle(cycle.Cycle):
    """A Cycle whose primitives answer from a script (the module docstring)."""

    def __init__(self, script=None, pose=None, home_pan_deg=None):  # noqa: D401 -- deliberately NOT Cycle.__init__: no robot, camera or store
        self.script = dict(script or {})
        self._consumed = {}
        self.calls = []
        self.transitions = []
        self.plan_only = False
        self.base_settle_s = 0.0
        self.abort = {"set": False}
        self.events = RecordingEvents()
        self.current = dict(pose or LOW_POSE)
        self.home_pan = LOW_POSE["shoulder_pan"] if home_pan_deg is None else float(home_pan_deg)

    # --- the script -------------------------------------------------------------------------------------------------
    def _key(self, label: str):
        keys = [key for key in self.script if label == key or label.startswith(key)]
        return max(keys, key=len) if keys else None

    def answer(self, label: str, default, *args):
        """The script's answer for `label`, or `default` when the script does not name it."""
        key = self._key(label)
        if key is None:
            return default(*args) if callable(default) else default
        value = self.script[key]
        if isinstance(value, list):
            index = self._consumed.get(key, 0)
            if index >= len(value):
                raise ScriptExhausted(f"the script answered {key!r} {len(value)} times and it was called again ({label!r})")
            self._consumed[key] = index + 1
            value = value[index]
        return value(self, *args) if callable(value) else value

    # --- the primitives ---------------------------------------------------------------------------------------------
    def pose(self) -> dict:
        return dict(self.current)

    def _arrive(self, target, holding):
        last = target[-1] if isinstance(target, (list, tuple)) else target
        self.current = {**self.current, **{name: float(value) for name, value in last.joints.items() if name in self.current and name != "gripper"}}
        return view(self.current)

    def run_pose(self, target, holding: bool, floor_m, pan_allowance: float = 0.0, label: str = "", manipulation_step: bool = True) -> dict:
        self.calls.append(("run_pose", label))
        result = self.answer(label, lambda: self._arrive(target, holding))
        if isinstance(result, BaseException):
            raise result
        result = {**result, "perception": result.get("perception") or list(self.answer("perception:" + label, []))}
        self.current = {**self.current, **result["measured"]}
        return result

    def grip(self, target: float, label: str) -> dict:
        self.calls.append(("grip", label))
        result = self.answer("grip:" + label, float(target))
        if isinstance(result, (int, float)):
            self.current["gripper"] = float(result)
            result = {"gripper": float(result), "measured": dict(self.current), "tool": cycle.tool_state(self.current), "perception": []}
        return result

    def observe(self, holding: bool) -> dict:
        self.calls.append(("observe", "observe"))
        return self.answer("observe", lambda: view(self.current))

    def _frame(self, result: dict):
        self.calls.append(("frame", "frame"))
        return self.answer("frame", None)

    def advance_base(self, anchor, direction) -> tuple:
        self.calls.append(("advance_base", "advance_base"))
        return self.answer("advance_base", (anchor, 0.0, cycle.BASE_RATE_PRIOR_MM_S, direction))

    def return_base(self, advanced_mm: float, reference_pad_m, direction, rate: float) -> tuple:
        self.calls.append(("return_base", "return_base"))
        return self.answer("return_base", (0.0, 0.0))

    def _home_pan_deg(self) -> float:
        return self.home_pan

    def labels(self, kind=None) -> list:
        """The labels of the logged primitive calls, of one kind when given."""
        return [label for call_kind, label in self.calls if kind is None or call_kind == kind]

    def states(self) -> list:
        return [entry["state"] for entry in self.transitions]


def failure_code(ctx, error) -> str:
    """The reason code the last ctx.failed() minted (the events record it; the CycleFailed carries the sentence only)."""
    codes = [args[0] for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"]
    assert codes and isinstance(error, stage_policy.CycleFailed), "no failure was minted through ctx.failed"
    return codes[-1]
