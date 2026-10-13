#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Stage-1 active wrist scan entry point, run ON THE LAPTOP against a running
CAMERA-LESS motor host. It wires existing pieces together and adds none:
scan_poses (targets, transit validation), scan_world (approval interlock,
base-zero checks around every segment, measured joints around every capture),
scan_arm_motion (bounded interpolation, wrist roll held, gripper held),
camera_lifecycle on the Pi (one camera open at a time, by stable identity),
observation_store, and the static pad/cube perception
(tools/static_world_observation.py) on every captured frame.

NO BASE COMMAND: every command goes through the manipulation action adapter,
which pins the base velocities to zero; this file never builds a base command.

Modes:
  coexistence  NO MOTION, no command sent: read the arm pose, do ONE front and
               ONE wrist camera lifecycle on the Pi, read the pose again.
  plan         NO MOTION: fresh measured pose, the exact targets of all bands,
               the transition path of the selected band, the approval digest.
  execute      ONE band, only with the digest and start pose of a plan the
               operator approved; then perception on the captured frame.

Metric rule: a PAD_FRAME position is reported only when the pad outline fit is
accepted in that frame; otherwise the observation stays in image space. No
camera pose or camera height is consumed as authoritative.

The Pi is reached over ssh (--pi-ssh or KC_PI_SSH); camera identities come
from KC_WRIST_CAMERA_ID / KC_FRONT_CAMERA_ID and are handed to the Pi process.
Raw frames and records stay in ignored local directories.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
for sub in ("autonomy/perception", "autonomy/manipulation", "tools"):
    sys.path.insert(0, str(ROOT / sub))

from base_rest import reads_at_rest  # noqa: E402
from calibration_inventory import CalibrationInventory  # noqa: E402
from camera_lifecycle import CaptureResult, LifecycleRecord  # noqa: E402
from observation_store import ObservationStore  # noqa: E402
from scan_arm_motion import freeze_wrist_roll, make_bounded_move_arm, manipulation_action_to_robot_command  # noqa: E402
import cartesian_lift  # noqa: E402
from scan_poses import SCAN_JOINTS, ScanPose, generate_scan_poses, link_heights_m, plan_scan_segments, tool_axis_heading_deg, validate_scan_pose  # noqa: E402
from scan_world import ScanRefused, ScanWorldDeps, run_scan_world  # noqa: E402
from world_observation import BASE_VELOCITY_KEYS, MEASURED_JOINT_KEYS  # noqa: E402

# camera_lock.py rides along because the probe now takes the shared per-role lock before it opens anything and REFUSES
# (it never opens unlocked) when that module is not beside camera_lifecycle.py on the robot computer. Without it here
# every capture would come back camera_lock_unavailable, so the cycle would fail at SEARCH with no frame.
PI_FILES = ("bin/pi/camera_multiplex_probe.py", "autonomy/perception/camera_cycle_probe.py", "autonomy/perception/camera_lifecycle.py",
            "autonomy/perception/camera_lock.py")
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6"]
STILL_TOLERANCE_DEG = 0.5
ELEVATED_LIFT_M = 0.03  # SCAN_ELEVATED_1: the proven Cartesian Z lift; 0.04 m is refused by the elbow singularity margin from the home pose
HOME_FILE = ROOT / ".local-dev" / "calibration" / "scan-home-current.json"

# ---------------------------------------------------------------------------------------------------------------------
# WHERE THE MUTABLE MACHINE DATA LIVES. Three things used to be found relative to __file__: the saved low scan pose
# (HOME_FILE above, and the same line in run_single_cube_cycle.py), the pad template (TEMPLATE_DIR in
# tools/static_world_observation.py) and the observation root (DEFAULT_ROOT in observation_store.py). runtime_paths() is the
# ONE place that decides all three. Without KC_RUNTIME_CONFIG it answers the legacy layout, imports nothing and changes
# nothing: HOME_FILE keeps the value above, the template and the store keep their own defaults because nothing is passed
# to them. With it, the three come from the explicit runtime configuration (packages/demo-runtime), which the controller
# also hands to the preflight BEFORE the robot is connected.
# ---------------------------------------------------------------------------------------------------------------------
RUNTIME_CONFIG_ENV = "KC_RUNTIME_CONFIG"  # demo_runtime.config.CONFIG_ENV_VAR, mirrored: a run without it must not import that package
_LEGACY_HOME_FILE = HOME_FILE  # what HOME_FILE is without a runtime configuration; use_runtime_paths() rebinds HOME_FILE, never this
_PAD_TEMPLATE_DIR = None  # None: static_world_observation.TEMPLATE_DIR, because no directory is passed to it
_OBSERVATION_ROOT = None  # None: observation_store.DEFAULT_ROOT, because no root is passed to the store


class RuntimePaths:
    """What runtime_paths() answers. config is None in the legacy layout; pad_template_dir and observation_root are None
    there as well, which means 'pass nothing', so the two modules that own those defaults behave exactly as before."""

    def __init__(self, config, saved_pose_file, pad_template_dir, observation_root):
        self.config, self.saved_pose_file, self.pad_template_dir, self.observation_root = config, saved_pose_file, pad_template_dir, observation_root


def import_runtime(name: str):
    """A module of packages/demo-runtime (or of the contract package it rests on), imported on first use. The two package
    directories are found from this checkout, so the code that ran is the code that was reviewed, not an installed copy."""
    import importlib

    for sub in ("packages/demo-contracts/python", "packages/demo-runtime"):
        if str(ROOT / sub) not in sys.path:
            sys.path.insert(0, str(ROOT / sub))
    return importlib.import_module(name)


def runtime_paths(environ=None) -> RuntimePaths:
    """The saved pose file, the pad template directory and the observation root of THIS run. An EMPTY KC_RUNTIME_CONFIG is
    unset (the runtime daemon exports it empty when it was started without a config). A config that is named and cannot
    be loaded RAISES demo_runtime's ConfigError: it never falls back to the legacy layout, because a run that silently
    used other files than the ones it was told to use is the failure the explicit configuration removes."""
    environ = os.environ if environ is None else environ
    if not environ.get(RUNTIME_CONFIG_ENV):
        return RuntimePaths(None, _LEGACY_HOME_FILE, None, None)
    config = import_runtime("demo_runtime.config").load_config(None, environ)
    return RuntimePaths(config, Path(config.saved_pose_file), Path(config.pad_template_dir), str(config.observation_root))


def use_runtime_paths(paths: RuntimePaths) -> RuntimePaths:
    """Make this module (return_low_poses, the pregrasp exit, the named pose files of world-scan-0, perceive, the store
    root) use these paths. HOME_FILE stays a module attribute for whoever reads it; a caller that keeps its own copy
    (run_single_cube_cycle.HOME_FILE) rebinds it from here."""
    global HOME_FILE, _PAD_TEMPLATE_DIR, _OBSERVATION_ROOT
    HOME_FILE, _PAD_TEMPLATE_DIR, _OBSERVATION_ROOT = paths.saved_pose_file, paths.pad_template_dir, paths.observation_root
    return paths


def store_root() -> dict:
    """Keyword arguments for ObservationStore: empty in the legacy layout, so the store is constructed exactly as before."""
    return {} if _OBSERVATION_ROOT is None else {"root_dir": _OBSERVATION_ROOT}


def config_refusal(error: BaseException) -> str:
    """The text of a refusal caused by the runtime configuration. A ConfigError names a field or a source and never a value
    or a path (packages/demo-runtime/demo_runtime/config.py); of anything else only the class is shown."""
    if type(error).__name__ == "ConfigError":
        return f"the runtime configuration named by {RUNTIME_CONFIG_ENV} was refused: {error}"
    return f"the runtime configuration named by {RUNTIME_CONFIG_ENV} could not be used ({type(error).__name__})"


HELD_IN_SWEEP = ("shoulder_lift", "elbow_flex", "wrist_flex")  # the pan sweep moves shoulder_pan ONLY

# PREGRASP_0 (non-contact, above the cube). Heights are arm base_link FK z with wrist_roll 0, the convention of this CLI.
# The proven grasp puts the tool at z -0.101 m; the floor keeps every link origin at least 41 mm above that. A target is
# refused unless its WORST realization inside the executor's arrival tolerance (every lift joint sagging by it) still
# clears the floor, so a run the executor accepts as "arrived" can never be below it.
PREGRASP_MIN_LINK_HEIGHT_M = -0.060
PREGRASP_DZ_WINDOW_M = (0.050, 0.070)  # vertical clearance above the proven grasp height: never closer, never a descent
PREGRASP_MAX_TILT_ERROR_DEG = 1.0  # the known-good grasp orientation is kept
GRIPPER_OPEN_TOLERANCE = 3.0  # host units (about 43 raw ticks) around the proven normal-open value
# BOUNDED NON-CONTACT DESCENT above the cube. The floor is the proven grasp tool height + 27 mm: the 25.4 mm cube plus a margin, so
# the jaw tips stay above the cube TOP plane even if they reach the floor at the proven grasp, whatever the lateral alignment.
APPROACH_MIN_LINK_HEIGHT_M = -0.074
APPROACH_DZ_WINDOW_M = (0.040, 0.070)  # clearance above the proven grasp height this mode may target (the unchanged sag rule decides the rest)
APPROACH_MAX_DROP_M = 0.030  # one step never lowers the tool by more than this from the MEASURED start
APPROACH_MIN_DROP_M = 0.005
APPROACH_MAX_RADIAL_CHANGE_M = 0.010
PREGRASP_CORRECT_MAX_PAN_DEG = 6.0  # one local correction at the non-contact pregrasp height: about 22 mm sideways at the cube, never more


def sweep_poses(start_joints: dict, sweep_deg: float) -> list:
    """SCAN_SWEEP from the named taught pose: CENTER, LEFT (pan - sweep), CENTER, RIGHT (pan + sweep), CENTER.
    Lift, elbow and wrist_flex keep the start values exactly, so no joint but the pan is commanded to move."""
    home = {name: float(start_joints[name]) for name in SCAN_JOINTS}
    poses = []
    for pose_id, offset in (("sweep-center", 0.0), ("sweep-left", -sweep_deg), ("sweep-center-2", 0.0), ("sweep-right", sweep_deg), ("sweep-center-3", 0.0)):
        joints = {**home, "shoulder_pan": round(home["shoulder_pan"] + offset, 3)}
        position = cartesian_lift.forward_kinematics({**joints, "wrist_roll": 0.0})[:3, 3]
        poses.append(ScanPose(pose_id=pose_id, band="MID", joints=joints, tool_azimuth_deg=tool_axis_heading_deg(joints),
                              nominal_tool_depression_deg=sum(joints[k] for k in HELD_IN_SWEEP), tool_position_m=tuple(float(v) for v in position)))
    return poses


def local_poses(start_joints: dict, which: str) -> list:
    """LOCAL registration excitation around the low pose L0 (the start pose): L1 = pan -15 deg, L2 = pan +15 deg with
    lift / elbow / wrist_flex at the start values; L3 = the largest accepted tool-forward step up to +30 mm from the
    proven Cartesian solver (height and tool orientation kept, pan kept). Every run ends by returning to L0."""
    home = {name: float(start_joints[name]) for name in SCAN_JOINTS}
    if which in ("L1", "L2"):
        target, note = {**home, "shoulder_pan": round(home["shoulder_pan"] + (-15.0 if which == "L1" else 15.0), 3)}, "pan only"
    else:
        target, note = None, None
        for forward_m in (0.030, 0.025, 0.020, 0.015):
            from hybrid_pick_place import _gate_cartesian_result  # the PROVEN acceptance gate of this solver (never gates on "converged")

            current = {**home, "wrist_roll": 0.0}
            solved = cartesian_lift.solve_cartesian_forward_and_z(current, forward_m, 0.0)
            joints = {name: round(float(solved["joint_deg"][name]), 3) for name in SCAN_JOINTS}
            accepted, _reason = _gate_cartesian_result(solved, current, {"delta_forward_m": forward_m, "delta_z_m": 0.0})
            if accepted and not validate_scan_pose(joints):
                target, note = joints, f"+{forward_m * 1000:.0f} mm forward (solver: {{k: round(float(v), 4) for k, v in solved.items() if k != 'joint_deg'}})"
                break
        if target is None:
            raise ScanRefused("no forward step between 15 and 30 mm passes the existing IK and pose guards from this pose")
    poses = []
    for pose_id, joints in ((f"local-{which}", target), (f"local-{which}-return-L0", home)):
        position = cartesian_lift.forward_kinematics({**joints, "wrist_roll": 0.0})[:3, 3]
        poses.append(ScanPose(pose_id=pose_id, band="FAR", joints=joints, tool_azimuth_deg=tool_axis_heading_deg(joints),
                              nominal_tool_depression_deg=sum(joints[k] for k in HELD_IN_SWEEP), tool_position_m=tuple(float(v) for v in position)))
    print(f"local {which}: {note}")
    return poses


def return_low_poses(start_joints: dict) -> list:
    """LOW-REGISTRATION ENTRY from an upright scan pose: (1) pan to the saved home pan while still upright and high,
    lift / elbow / wrist_flex held; (2) one bounded in-plane move to the saved SCAN_HOME_CURRENT joints (the planner's
    transit check must accept it: from the upright pose this path only retracts the tool)."""
    home = json.loads(HOME_FILE.read_text())["measured_joints_deg"]
    low = {name: float(home[name]) for name in SCAN_JOINTS}
    upright_center = {**{name: float(start_joints[name]) for name in SCAN_JOINTS}, "shoulder_pan": low["shoulder_pan"]}
    poses = []
    stages = [("return-upright-center", "MID", upright_center), ("low-L0", "FAR", low)]
    if abs(float(start_joints["shoulder_pan"]) - low["shoulder_pan"]) <= 1.0:
        stages = stages[1:]  # already at the home pan: only the in-plane move remains
    for pose_id, band, joints in stages:
        position = cartesian_lift.forward_kinematics({**joints, "wrist_roll": 0.0})[:3, 3]
        poses.append(ScanPose(pose_id=pose_id, band=band, joints=joints, tool_azimuth_deg=tool_axis_heading_deg(joints),
                              nominal_tool_depression_deg=sum(joints[k] for k in HELD_IN_SWEEP), tool_position_m=tuple(float(v) for v in position)))
    return poses


def _pose(pose_id: str, band: str, joints: dict) -> ScanPose:
    position = cartesian_lift.forward_kinematics({**joints, "wrist_roll": 0.0})[:3, 3]
    return ScanPose(pose_id=pose_id, band=band, joints=joints, tool_azimuth_deg=tool_axis_heading_deg(joints),
                    nominal_tool_depression_deg=sum(joints[k] for k in HELD_IN_SWEEP), tool_position_m=tuple(float(v) for v in position))


def _proven_grasp_shifted(forward_m: float, dz_m: float) -> dict:
    """lift / elbow / wrist_flex of the PROVEN grasp geometry moved forward_m outward and dz_m up by the proven Cartesian
    solver, accepted only by the proven gate and only with the known-good grasp orientation kept."""
    from hybrid_pick_place import KNOWN_GOOD_GRASP_POSE_DEG, _gate_cartesian_result  # the PROVEN geometry and acceptance gate

    grasp = {**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0}
    solved = cartesian_lift.solve_cartesian_forward_and_z(grasp, forward_m, dz_m)
    accepted, reason = _gate_cartesian_result(solved, grasp, {"delta_forward_m": forward_m, "delta_z_m": dz_m})
    if not accepted:
        raise ScanRefused(f"the proven gate refuses this target ({forward_m * 1000:+.0f} mm forward, {dz_m * 1000:+.0f} mm up from the proven grasp): {reason}")
    if solved["orientation_error_deg"] > PREGRASP_MAX_TILT_ERROR_DEG:
        raise ScanRefused(f"the grasp orientation is not kept at this target (tilt error {solved['orientation_error_deg']:.2f} deg)")
    return {name: round(float(solved["joint_deg"][name]), 3) for name in HELD_IN_SWEEP}


def pregrasp_descend_poses(start_joints: dict, forward_m: float, dz_m: float) -> list:
    """ONE BOUNDED NON-CONTACT DESCENT: the proven grasp geometry at a lower clearance dz_m (same orientation), the pan held
    exactly at its start value. Refused unless, against the MEASURED start (FK), it lowers the tool by 5..30 mm and changes
    its radius by at most 10 mm. The floor and the unchanged sag rule are applied by the planner."""
    if not APPROACH_DZ_WINDOW_M[0] <= dz_m < APPROACH_DZ_WINDOW_M[1]:
        raise ScanRefused(f"descent clearance {dz_m * 1000:.0f} mm is outside the window {[round(v * 1000) for v in APPROACH_DZ_WINDOW_M]} mm above the proven grasp height")
    home = {name: float(start_joints[name]) for name in SCAN_JOINTS}
    target = {"shoulder_pan": home["shoulder_pan"], **_proven_grasp_shifted(forward_m, dz_m)}
    before, after = (cartesian_lift.end_effector_position({**joints, "wrist_roll": 0.0}) for joints in (home, target))
    drop, radial = float(before[2] - after[2]), float(np.hypot(after[0], after[1]) - np.hypot(before[0], before[1]))
    if not APPROACH_MIN_DROP_M <= drop <= APPROACH_MAX_DROP_M:
        raise ScanRefused(f"one descent step lowers the tool by {APPROACH_MIN_DROP_M * 1000:.0f}..{APPROACH_MAX_DROP_M * 1000:.0f} mm from the measured start; this one would change it by {drop * 1000:+.1f} mm")
    if abs(radial) > APPROACH_MAX_RADIAL_CHANGE_M:
        raise ScanRefused(f"this descent would also move the tool {radial * 1000:+.1f} mm radially from the measured start (limit {APPROACH_MAX_RADIAL_CHANGE_M * 1000:.0f} mm)")
    return [_pose(f"pregrasp-descend-fwd{forward_m * 1000:.0f}-dz{dz_m * 1000:.0f}", "NEAR", target)]


def pregrasp_poses(start_joints: dict, pan_deg: float, forward_m: float, dz_m: float) -> list:
    """PREGRASP_0, NON-CONTACT: (1) pan to the coarse cube bearing at the START height, lift / elbow / wrist_flex held
    (the proven L1/L2 move); (2) one in-plane move, pan held, to the PROVEN grasp geometry shifted forward_m outward and
    dz_m UP by the proven Cartesian solver behind the proven gate, so the known-good grasp orientation is kept. The
    target does not depend on the start pose, and every parameter is in a pose id, so the approval digest binds them."""
    if not PREGRASP_DZ_WINDOW_M[0] <= dz_m <= PREGRASP_DZ_WINDOW_M[1]:
        raise ScanRefused(f"pregrasp clearance {dz_m * 1000:.0f} mm is outside the non-contact window {[round(v * 1000) for v in PREGRASP_DZ_WINDOW_M]} mm above the proven grasp height")
    home = {name: float(start_joints[name]) for name in SCAN_JOINTS}
    target = _proven_grasp_shifted(forward_m, dz_m)
    target["shoulder_pan"] = float(pan_deg)
    from scan_world import ARRIVAL_TOLERANCE_DEG

    worst = min(link_heights_m({**target, **{name: target[name] + ARRIVAL_TOLERANCE_DEG for name in HELD_IN_SWEEP}}).values())
    if worst < PREGRASP_MIN_LINK_HEIGHT_M:
        raise ScanRefused(f"this pregrasp could be realized as low as {worst:+.3f} m inside the {ARRIVAL_TOLERANCE_DEG} deg arrival tolerance, below the non-contact floor {PREGRASP_MIN_LINK_HEIGHT_M} m: raise the clearance")
    return [_pose(f"pregrasp-0-pan{pan_deg:+.1f}".replace("+", ""), "FAR", {**home, "shoulder_pan": float(pan_deg)}),
            _pose(f"pregrasp-0-fwd{forward_m * 1000:.0f}-dz{dz_m * 1000:.0f}", "NEAR", target)]


def pregrasp_correct_poses(start_joints: dict, pan_delta_deg: float) -> list:
    """LOCAL CORRECTION at the non-contact pregrasp height (SMALL MOVE -> CAPTURE -> RECOMPUTE): the pan alone, by a bounded
    delta decided from the wrist image; lift / elbow / wrist_flex stay exactly at the start values, so the height does not
    change. Deterministic in the start pose, so plan and execute derive the same target from the approved start pose."""
    if not 0.0 < abs(pan_delta_deg) <= PREGRASP_CORRECT_MAX_PAN_DEG:
        raise ScanRefused(f"a local pan correction must be non-zero and at most {PREGRASP_CORRECT_MAX_PAN_DEG} deg, got {pan_delta_deg}")
    home = {name: float(start_joints[name]) for name in SCAN_JOINTS}
    return [_pose(f"pregrasp-correct-pan{pan_delta_deg:+.2f}", "NEAR", {**home, "shoulder_pan": round(home["shoulder_pan"] + pan_delta_deg, 3)})]


def pregrasp_return_poses(home_joints: dict) -> list:
    """EXIT from a pregrasp (gripper open, nothing held): the planner's own entry does it in the safe order, an in-plane
    rise with the pan held, then the pan at scan height, ending at the saved low scan pose."""
    return [_pose("pregrasp-return-low-L0", "FAR", {name: float(home_joints[name]) for name in SCAN_JOINTS})]


def gripper_open_refusal(measured: dict):
    """PREGRASP_0 descends only with the gripper MEASURED in the proven normal-open region (raw about 3198): a limp
    over-open gripper reads clamped, a closed one reads the latch value. None means it may."""
    from hybrid_pick_place import GRIPPER_OPEN

    if not abs(float(measured["gripper"]) - GRIPPER_OPEN) <= GRIPPER_OPEN_TOLERANCE:  # written so that an unreadable (NaN) value refuses
        return f"the gripper measures {float(measured['gripper']):.2f}, not the proven normal-open {GRIPPER_OPEN} +/- {GRIPPER_OPEN_TOLERANCE}: return it by hand and acquire it first"
    return None


# How far above where the arm already is (or the saved low scan pose, for the pregrasp EXIT) a command may raise
# wrist_flex. 0.2 deg refused the 2026-10-08 scene's pregrasp entry by 0.15 deg and Return arm's settle by 0.06 deg; the
# operator ruled 0.5 deg (2026-10-08). One constant, read by every site (run_single_cube_cycle uses it too).
WRIST_FLEX_CEILING_MARGIN_DEG = 0.5


def wrist_flex_ceiling_for(measured: dict, return_poses) -> float:
    """Commands never raise wrist_flex above where the arm already is; the pregrasp EXIT alone may raise it, and only up
    to the saved low scan pose, which the arm has physically held."""
    ceiling = float(measured["wrist_flex"])
    if return_poses:
        ceiling = max(ceiling, max(float(pose.joints["wrist_flex"]) for pose in return_poses))
    return ceiling + WRIST_FLEX_CEILING_MARGIN_DEG


def fuse_sweep(records: list, out_path: Path, sweep_deg: float) -> list:
    """SCAN_SWEEP observation fusion record: per sector what was seen, and the spread of the cube estimates in PAD_FRAME."""
    import numpy as np

    sectors, cube_xy = [], []
    for pose_id, observation, perception in records:
        template = perception.get("pad_by_template") or {}
        cube = perception["cube"]
        xy = (perception.get("cube_by_template") or {}).get("cube_center_pad_m")
        if xy is None:
            xy = next((e["cube"]["cube_center_pad_m"] for e in perception["focal_sweep"] if "cube" in e and abs(e["focal_px"] - 1200.0) < 1e-6), None)
        if xy is not None:
            cube_xy.append(xy)
        sectors.append({"sector": pose_id, "observation_id": observation["observation_id"], "timestamp_utc": observation["timestamp_utc"],
                        "measured_joints_deg": observation["robot_state"]["measured_joints_deg"], "image": observation.get("raw_frame_path"),
                        "pad_recognized": bool(template.get("pad_identity")), "pad_landmarks": template.get("landmark_inliers"), "pad_frame_valid": bool(template.get("pad_frame_valid")),
                        "pad_frame_reason": template.get("reason"), "cube_detected": cube["detected"], "cube_bbox": cube.get("bbox_xyxy"), "cube_contact_px": cube.get("contact_px"),
                        "cube_clipped": cube.get("clipped"), "cube_pad_frame_xy_m": xy})
    fused = {"name": f"SCAN_SWEEP_{int(sweep_deg)}", "sectors": sectors, "pad_observed_in": [s["sector"] for s in sectors if s["pad_recognized"]],
             "pad_frame_valid_in": [s["sector"] for s in sectors if s["pad_frame_valid"]], "cube_observed_in": [s["sector"] for s in sectors if s["cube_detected"]],
             "cube_pad_frame": None, "base_motion": "ZERO"}
    if cube_xy:
        points = np.array(cube_xy)
        fused["cube_pad_frame"] = {"n": len(points), "median_xy_m": np.median(points, axis=0).round(4).tolist(), "mean_xy_m": points.mean(axis=0).round(4).tolist(),
                                   "max_spread_m": round(float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))), 4)}
    out_path.write_text(json.dumps(fused, indent=2, default=float))
    lines = [f"\n{fused['name']}  (fusion record: {out_path})"]
    for s in sectors:
        lines.append(f"  {s['sector']:15s} pan {s['measured_joints_deg']['shoulder_pan']:+7.2f} deg | pad recognized {s['pad_recognized']} ({s['pad_landmarks']} landmarks) PAD_FRAME {s['pad_frame_valid']} | cube {s['cube_detected']} xy {s['cube_pad_frame_xy_m']}")
    lines.append(f"  PAD observed in {fused['pad_observed_in']}; PAD_FRAME valid in {fused['pad_frame_valid_in']}")
    lines.append(f"  CUBE observed in {fused['cube_observed_in']}; PAD_FRAME estimate {fused['cube_pad_frame']}")
    return lines


def elevated_pose(start_joints: dict) -> ScanPose:
    """SCAN_ELEVATED_1 from a start pose (SCAN_JOINTS, degrees): cartesian_lift.solve_cartesian_z_lift raises the
    tool by ELEVATED_LIFT_M with the pan held and the tool orientation kept, so the view direction is preserved.
    Deterministic in the start pose, so plan and execute derive the same target from the approved start pose."""
    start = {**{name: float(start_joints[name]) for name in SCAN_JOINTS}, "wrist_roll": 0.0}  # the lift solution does not depend on wrist roll
    solved = cartesian_lift.solve_cartesian_z_lift(start, ELEVATED_LIFT_M)
    if not solved["converged"] or solved["xy_drift_m"] > 0.002 or solved["orientation_error_deg"] > 1.0:
        raise ScanRefused(f"the Cartesian lift did not solve cleanly from this pose: {solved}")
    joints = {name: round(float(solved["joint_deg"][name]), 3) for name in SCAN_JOINTS}
    position = cartesian_lift.forward_kinematics({**joints, "wrist_roll": 0.0})[:3, 3]
    return ScanPose(pose_id="elevated-1", band="FAR", joints=joints, tool_azimuth_deg=tool_axis_heading_deg(joints),  # band = coverage class of a 22 deg tool axis
                    nominal_tool_depression_deg=sum(joints[k] for k in ("shoulder_lift", "elbow_flex", "wrist_flex")), tool_position_m=tuple(float(v) for v in position))


def read_pose(robot) -> dict:
    observation = robot.get_observation()
    return {name: float(observation[f"arm_{name}.pos"]) for name in MEASURED_JOINT_KEYS}


def read_base(robot) -> dict:
    observation = robot.get_observation()
    return {key: float(observation[key]) for key in BASE_VELOCITY_KEYS}


def wait_for_live_pose(robot, timeout_s: float = 6.0) -> None:
    """The client hands out zeros until the first host message arrives. A zero pose taken for a
    measured one would make the first interpolation start from the wrong place: refuse instead."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if any(abs(value) > 1e-3 for value in read_pose(robot).values()):
            return
        time.sleep(0.1)
    sys.exit("REFUSED: no live observation from the motor host (the pose reads all zeros); nothing was commanded")


def arm_only_command(command: dict, wrist_flex_ceiling: float, keep_gripper: bool = False) -> dict:
    """What this CLI is allowed to send: the gripper key removed (scan mode: the limp gripper is never written), and a
    refusal, before anything is sent, for a wrist_flex above the ceiling or ANY non-zero base velocity (exact zero).
    keep_gripper (manipulation while HOLDING only): the key stays, carrying the value the bounded move built, which is
    the gripper position MEASURED at the start of that move: the proven runner's behaviour after the latch."""
    command = {key: value for key, value in command.items() if keep_gripper or key != "arm_gripper.pos"}
    if float(command["arm_wrist_flex.pos"]) > wrist_flex_ceiling:
        raise RuntimeError(f"refusing to command wrist_flex {command['arm_wrist_flex.pos']:.2f} above {wrist_flex_ceiling:.2f}: scan commands never increase it")
    if any(float(command[key]) != 0.0 for key in BASE_VELOCITY_KEYS):
        raise RuntimeError(f"refusing to send a command with a non-zero base velocity: { {k: command[k] for k in BASE_VELOCITY_KEYS} }")
    return command


class PiCamera:
    """capture(role) -> CaptureResult, by running the existing camera-only probe
    for ONE lifecycle on the Pi and copying the frame back."""

    def __init__(self, ssh_target: str, local_dir: Path):
        self.ssh_target, self.local_dir, self.count = ssh_target, local_dir, 0
        self.remote_dir = "m1-scan/" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        # TIMING ONLY (laptop monotonic, nanoseconds): how long the remote mkdir and the copy of the probe files took, and per
        # capture how long the ssh lifecycle and the copy back took, as two SEPARATE numbers. They are instance attributes
        # for whoever wants them; CaptureResult, LifecycleRecord and everything that is persisted stay as they were.
        self.setup_timing, self.last_timing = None, None
        setup_started_ns = time.monotonic_ns()
        self._ssh(f"mkdir -p m1-scan && mkdir '{self.remote_dir}' && mkdir -p '{self.remote_dir}/bin/pi' '{self.remote_dir}/autonomy/perception'")
        copy_started_ns = time.monotonic_ns()
        for name in PI_FILES:
            subprocess.run(["scp", "-q", *SSH_OPTIONS, str(ROOT / name), f"{ssh_target}:{self.remote_dir}/{name}"], check=True, timeout=30)
        self.setup_timing = {"ssh_ns": copy_started_ns - setup_started_ns, "copy_ns": time.monotonic_ns() - copy_started_ns}

    def _ssh(self, command: str, check: bool = True):
        return subprocess.run(["ssh", *SSH_OPTIONS, self.ssh_target, command], check=check, timeout=60, capture_output=True, text=True)

    def capture(self, role: str) -> CaptureResult:
        self.count += 1
        out = f"cap-{self.count:03d}-{role}"
        identities = " ".join(f"{key}='{os.environ[key]}'" for key in ("KC_WRIST_CAMERA_ID", "KC_FRONT_CAMERA_ID") if os.environ.get(key))
        self.last_timing = None  # a capture that fails below must not leave the PREVIOUS capture's numbers to be read as its own
        capture_started_ns = time.monotonic_ns()
        self._ssh(f"cd '{self.remote_dir}' && {identities} \"$HOME/miniconda3/bin/python3\" bin/pi/camera_multiplex_probe.py --cycles 1 --order {role} --settle-frames 6 --width 1280 --height 720 --out-dir {out}", check=False)
        capture_ns = time.monotonic_ns() - capture_started_ns
        # One local directory PER PiCamera instance: a shared name made scp nest into an existing directory and the
        # OLD frame was read back as the new capture (found 2026-09-19: five observations stored the same image).
        local = self.local_dir / "pi-captures" / self.remote_dir.split("/")[-1]
        local.mkdir(parents=True, exist_ok=True)
        transfer_started_ns = time.monotonic_ns()
        subprocess.run(["scp", "-q", "-r", *SSH_OPTIONS, f"{self.ssh_target}:{self.remote_dir}/{out}", str(local / out)], check=True, timeout=60)
        self.last_timing = {"role": role, "capture_ns": capture_ns, "transfer_ns": time.monotonic_ns() - transfer_started_ns}
        if (local / out / out).exists():
            raise RuntimeError(f"capture directory collision for {out}: refusing to read a possibly stale frame")
        attempt = json.loads((local / out / "report.json").read_text())["attempts"][0]
        frame = cv2.imread(str(local / out / "frames" / f"0000-{role}.png")) if attempt["frame_valid"] else None
        return CaptureResult(frame=frame, record=LifecycleRecord(**attempt))


CAPTURE_PROVIDER_LEGACY = "legacy_per_capture"
CAPTURE_PROVIDER_SERVICE = "camera_service"
CAPTURE_PROVIDERS = (CAPTURE_PROVIDER_LEGACY, CAPTURE_PROVIDER_SERVICE)

# How long the adapter waits for a forward it opened itself to start carrying traffic. Bounded on purpose: a tunnel
# that never comes up must fail the run quickly and with its own reason, not hang a capture inside a mission.
MISSION_FORWARD_TIMEOUT_S = 10.0

# The store root on the robot computer that the service writes frames under, taken from the service's own default
# (`bin/pi/camera_service.py --store-dir`, "~/kc-camera-service/store"). It is relative, so scp resolves it from the
# remote account's home exactly as the service expands it. The service's reply names a path RELATIVE to this root, and
# `scp_fetch` refuses an absolute path or one that climbs out, so a reply cannot point the copy at an arbitrary file.
MISSION_REMOTE_ROOT = "kc-camera-service/store"

# ON THE ROBOT ITSELF (the robot-side runtime, cake.robot.link local): the runtime's supervisor sets this to "1" and sets
# KC_MISSION_LOCAL_PORT to the camera service's mission port, because there is no other computer to forward from or copy
# over. The service is then asked on loopback directly and each frame is read from its store on this machine
# (local_fetch). Unset: the desktop behaviour, byte for byte (an ssh forward and scp_fetch).
CAMERA_SERVICE_LOCAL_ENV = "KC_CAMERA_SERVICE_LOCAL"


def local_fetch(store_root: Path):
    """A `fetch` that copies one named frame from the camera service's store ON THIS MACHINE, in place of scp_fetch.

    THE SAME RELATIVE-PATH CHECK scp_fetch makes: the path is the service's own, relative, and never climbs out. And one
    more that a local copy needs and a remote one did not: the file it resolves to (symlinks followed) must still be
    under the store root, so a link placed in the store cannot point the copy at any other file on the robot."""
    import shutil

    root = Path(store_root)

    def fetch(relative: str, destination: Path) -> None:
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("a frame path is relative and never climbs out of the store")
        source = (root / relative).resolve(strict=True)
        if root.resolve(strict=True) not in source.parents:
            raise ValueError("a frame path resolves outside the store")
        shutil.copyfile(source, destination)
    return fetch


class ServiceCamera:
    """capture(role) -> CaptureResult, by asking the camera service that OWNS the camera.

    THE SAME CONTRACT AS `PiCamera`, because the controller reads both the same way: `capture(role)` returns a
    `CaptureResult`, `setup_timing` is read once when the camera is made and `last_timing` after every capture, both
    through `getattr(..., None)`. What changes is who opens the device. The legacy path opens and closes the camera per
    capture over ssh; here the service holds it open for its whole life and answers one request at a time.

    NOTHING HERE FALLS BACK. A refusal from the service is returned as a capture WITHOUT a frame, carrying the
    service's own reason code, exactly as the legacy path reports a lifecycle that produced nothing. Reaching for
    `PiCamera` when the service refuses would open a device the service holds the lock on, which is the one thing the
    handoff rule exists to prevent, and it would also hide the refusal that says why.
    """

    def __init__(self, ssh_target: str, local_dir: Path, *, mission_port: int = 5570,
                 remote_root: str = MISSION_REMOTE_ROOT, local_port=None, local=None):
        # Imported HERE, not at module scope: the legacy provider must not require pyzmq to be installed, and
        # `mission_capture_client` keeps zmq out of its own import for the same reason.
        from mission_capture_client import MissionCaptureClient, endpoint_for, scp_fetch, zmq_request

        # ON THE ROBOT: no forward is opened and frames are read locally (CAMERA_SERVICE_LOCAL_ENV above). The port the
        # service is asked on is the reused one when named, else the service's own mission port, on loopback either way.
        local = os.environ.get(CAMERA_SERVICE_LOCAL_ENV) == "1" if local is None else bool(local)
        if local and local_port is None and not os.environ.get("KC_MISSION_LOCAL_PORT"):
            local_port = mission_port
        self.ssh_target, self.local_dir = ssh_target, Path(local_dir)
        self.setup_timing, self.last_timing = None, None
        self._forward = None
        setup_started_ns = time.monotonic_ns()

        # ONE OWNER FOR THE FORWARD. When something else already owns a forward to the mission socket it is named here
        # and reused; this adapter then opens nothing and, just as importantly, closes nothing on the way out. Only a
        # forward this object opened is ever torn down by it.
        reused = local_port if local_port is not None else os.environ.get("KC_MISSION_LOCAL_PORT")
        if reused:
            port = int(reused)
            self.owns_forward = False
        else:
            port = self._free_local_port()
            self._open_forward(port, mission_port)
            self.owns_forward = True
        self.local_port = port

        forwarded_ns = time.monotonic_ns()
        capture_dir = self.local_dir / "service-captures"
        capture_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._client = MissionCaptureClient(
            request=zmq_request(endpoint_for(port)),
            fetch=(local_fetch(Path(remote_root) if os.path.isabs(remote_root) else Path.home() / remote_root) if local
                   else scp_fetch(ssh_target, remote_root)),
            local_dir=capture_dir)
        # AVAILABILITY IS PROVEN BEFORE THE RUN, not discovered at the first capture inside a mission. `ping` refuses a
        # socket that answers as anything other than the camera service, so a forward pointed at the wrong port fails
        # here, with its own reason, rather than returning something that is not a mission frame.
        self.service = self._client.ping()
        self.setup_timing = {"ssh_ns": forwarded_ns - setup_started_ns, "copy_ns": time.monotonic_ns() - forwarded_ns}

    @staticmethod
    def _free_local_port() -> int:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _open_forward(self, local_port: int, mission_port: int) -> None:
        import socket

        self._forward = subprocess.Popen(
            ["ssh", *SSH_OPTIONS, "-N", "-L", f"127.0.0.1:{local_port}:127.0.0.1:{mission_port}", self.ssh_target],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + MISSION_FORWARD_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._forward.poll() is not None:
                self._forward = None
                raise RuntimeError("the mission forward exited at once; a local port may already be in use")
            with socket.socket() as probe:
                probe.settimeout(0.5)
                if probe.connect_ex(("127.0.0.1", local_port)) == 0:
                    return
            time.sleep(0.2)
        self.close()
        raise RuntimeError(f"the mission forward did not carry traffic within {MISSION_FORWARD_TIMEOUT_S:g}s")

    def capture(self, role: str) -> CaptureResult:
        from mission_capture_client import CaptureRefused

        self.last_timing = None  # a capture that fails must not leave the PREVIOUS capture's numbers to be read as its own
        try:
            frame = self._client.capture(role)
        except CaptureRefused as refusal:
            # The service's OWN reason code and sentence, carried into the record the controller stores. This is a
            # capture that produced no frame, which the controller already knows how to report.
            return CaptureResult(frame=None, record=LifecycleRecord(
                role=role, identity_fingerprint="camera_service", settle_frames_requested=self._client.settle_frames,
                frame_valid=False, error=f"{refusal.reason_code}: {refusal.detail}"))
        self.last_timing = {"role": role, "capture_ns": frame.request_ns, "transfer_ns": frame.transfer_ns}
        image = cv2.imread(str(frame.path))
        record = dict(frame.lifecycle_record)
        record.setdefault("role", role)
        record.setdefault("identity_fingerprint", "camera_service")
        record.setdefault("settle_frames_requested", self._client.settle_frames)
        record["frame_valid"] = image is not None
        if image is None:
            record["error"] = "the frame arrived and verified, but could not be decoded on this computer"
        # The service's record may carry keys this dataclass does not declare; they are dropped rather than guessed at.
        allowed = set(LifecycleRecord.__dataclass_fields__)
        return CaptureResult(frame=image, record=LifecycleRecord(**{k: v for k, v in record.items() if k in allowed}))

    def close(self) -> None:
        """Tear down ONLY what this object opened. A reused forward belongs to whoever opened it."""
        if self._forward is None:
            return
        self._forward.terminate()
        try:
            self._forward.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._forward.kill()
        self._forward = None


def make_camera(ssh_target: str, local_dir: Path, provider: str = CAPTURE_PROVIDER_LEGACY, **options):
    """THE ONE PLACE A CAPTURE PROVIDER IS CHOSEN.

    The default is the legacy per-capture path, byte for byte the behaviour every recorded run was produced with. A
    caller that names the service gets the service and never silently gets the other one: an unknown name is an error
    here rather than a quiet fall back to a provider that would open a camera the service holds the lock on.
    """
    if provider == CAPTURE_PROVIDER_LEGACY:
        return PiCamera(ssh_target, local_dir)
    if provider == CAPTURE_PROVIDER_SERVICE:
        return ServiceCamera(ssh_target, local_dir, **options)
    raise ValueError(f"capture provider must be one of {CAPTURE_PROVIDERS}, got {provider!r}")


def perceive(role: str, image, out_dir: Path, name: str, detector) -> list:
    """Frozen static perception on one frame; sidecar json + overlay; summary lines."""
    import static_world_observation as swo

    template = {} if _PAD_TEMPLATE_DIR is None else {"template_dir": _PAD_TEMPLATE_DIR}  # legacy: called exactly as before
    record, pad, cube = swo.observe_frame(role, image, detector, swo.NOMINAL_FOCAL_PX[role], **template)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(record, indent=2, default=float))
    swo.draw_overlay(image, pad, cube, out_dir / f"{name}-overlay.png")
    return swo.summarize(record)


def make_detector():
    from zero_shot_detector import ZeroShotDetector

    return ZeroShotDetector()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_coexistence(robot, camera, out_dir: Path, pose_name: str = "SCAN_HOME_CURRENT") -> int:
    before, base_before = read_pose(robot), read_base(robot)
    print(f"arm pose before (deg): { {k: round(v, 2) for k, v in before.items()} }\nbase velocity before: {base_before}")
    frames = {}
    for role in ("front", "wrist"):
        result = camera.capture(role)
        frames[role] = result.frame
        print(f"{role}: frame valid {result.record.frame_valid}, open {result.record.open_ok}, close {result.record.close_ok}, lifecycle {result.record.total_latency_s:.2f} s, error {result.record.error}")
        if result.frame is not None:
            cv2.imwrite(str(out_dir / f"{role}.png"), result.frame)
    after, base_after = read_pose(robot), read_base(robot)
    moved = {k: round(after[k] - before[k], 2) for k in before}
    print(f"arm pose after (deg):  { {k: round(v, 2) for k, v in after.items()} }\narm displacement (deg): {moved}\nbase velocity after: {base_after}")
    ok = all(f is not None for f in frames.values()) and all(abs(v) <= STILL_TOLERANCE_DEG for v in moved.values()) and reads_at_rest(base_before) and reads_at_rest(base_after)
    print(f"COEXISTENCE (laptop side): {'PASS' if ok else 'NOT PASSED'}  (both frames valid, arm within {STILL_TOLERANCE_DEG} deg, base zero). No command was sent.")
    HOME_FILE.parent.mkdir(parents=True, exist_ok=True)
    home_file = HOME_FILE.with_name(pose_name.lower().replace("_", "-") + ".json")
    if home_file.exists():  # a named pose file is evidence: never overwritten
        home_file = home_file.with_name(f"{home_file.stem}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json")
    home_file.write_text(json.dumps({"name": pose_name, "taken_utc": now_utc(), "source": "host-measured joints (degrees) while the arm holds the acquired pose",
                                     "measured_joints_deg": after, "base_velocity": base_after}, indent=2))
    print(f"{pose_name} (degrees) saved: {home_file}")
    if not ok:
        return 1
    import static_world_observation as swo

    print("\nWORLD_SCAN_0")
    swo.main(["--front", str(out_dir / "front.png"), "--wrist", str(out_dir / "wrist.png"), "--out-dir", str(out_dir / "world-scan-0")]
             + ([] if _PAD_TEMPLATE_DIR is None else ["--template-dir", str(_PAD_TEMPLATE_DIR)]))
    record = json.loads((out_dir / "world-scan-0" / "static-world-observation.json").read_text())
    for role in ("front", "wrist"):
        pad = record[role]["pad"] or {}
        print(f"{role.upper()} image: {out_dir / (role + '.png')}\n  PAD_FRAME valid: {bool(pad.get('detected'))}; homography image->pad: {pad.get('homography_image_to_pad')}")
    print(f"measured arm pose: {after}\nresults: {out_dir / 'world-scan-0'}\nBASE MOTION: ZERO   ARM MOTION: ZERO (no command was sent)")
    return 0


def print_targets(measured: dict) -> None:
    print(f"FRESH measured arm pose (deg): { {k: round(v, 2) for k, v in measured.items()} }")
    print(f"link heights now (m above the arm base origin): { {k: round(v, 3) for k, v in link_heights_m(measured).items()} }")
    print("ALL stage-1 band targets (deg), none executed by this call:")
    poses = generate_scan_poses(stage=1)
    for pose in poses:
        heights = {k: round(v, 3) for k, v in link_heights_m({**measured, **pose.joints}).items()}
        print(f"  {pose.pose_id}: {pose.joints}  tool axis {pose.nominal_tool_depression_deg:.0f} deg below horizontal, tool at {tuple(round(v, 3) for v in pose.tool_position_m)} m (arm frame), link heights {heights}, pose checks: {validate_scan_pose(pose.joints) or 'ok'}")
    for start, end in zip(poses, poses[1:]):
        try:
            hop = plan_scan_segments({**measured, **start.joints}, [end])
            print(f"  transition {start.band} -> {end.band}: {[(s.kind, round(s.duration_s, 1)) for s in hop]}")
        except ValueError as error:
            print(f"  transition {start.band} -> {end.band}: REFUSED by the planner: {error}")


def print_selected(result, measured: dict, robot_command_preview: dict, slow_factor: float = 1.0) -> None:
    print(f"SELECTED: {[p.pose_id for p in result.poses]}; transition path from the fresh pose:")
    for segment in result.segments:
        print(f"  {segment.kind:10s} -> {segment.target}  planner {segment.duration_s:.1f} s, COMMANDED over {segment.duration_s * slow_factor:.1f} s  (outward sweep while low: {segment.outward_sweep_while_low_m:.3f} m)  observation: {segment.pose_id or '-'}")
    print(f"wrist_roll: held at its measured value {measured['wrist_roll']:.2f} deg (never commanded elsewhere); gripper: held at its measured value {measured['gripper']:.2f}")
    print(f"base channel of every command this CLI can send (built from the current pose, NOT sent): { {k: v for k, v in robot_command_preview.items() if 'vel' in k} }")
    print(f"APPROVAL TOKEN  --approved-digest {result.plan_digest}  --approved-start-pose '{json.dumps(result.start_pose)}'")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run_stage1_scan.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", required=True, choices=("world-scan-0", "plan", "execute", "plan-elevated", "execute-elevated", "plan-sweep", "execute-sweep", "plan-return-low", "execute-return-low", "plan-local", "execute-local",
                                                         "plan-pregrasp-0", "execute-pregrasp-0", "plan-pregrasp-return", "execute-pregrasp-return",
                                                         "plan-pregrasp-correct", "execute-pregrasp-correct", "plan-pregrasp-descend", "execute-pregrasp-descend"))
    parser.add_argument("--correct-pan-deg", type=float, help="pregrasp-correct: bounded pan DELTA (reading convention: + turns the camera right, the scene shifts left)")
    parser.add_argument("--pregrasp-pan-deg", type=float, help="pregrasp-0: coarse shoulder_pan READING that points the tool at the cube")
    parser.add_argument("--pregrasp-forward-mm", type=float, help="pregrasp-0: tool range beyond the proven grasp, along the arm plane")
    parser.add_argument("--pregrasp-dz-mm", type=float, help="pregrasp-0: vertical clearance above the proven grasp height (50..70)")
    parser.add_argument("--local", choices=("L1", "L2", "L3"))
    parser.add_argument("--band", choices=("FAR", "MID", "NEAR"))
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--pi-ssh", default=os.environ.get("KC_PI_SSH"))
    parser.add_argument("--scan-id", default="stage1-" + time.strftime("%Y%m%d", time.gmtime()))
    parser.add_argument("--approved-digest")
    parser.add_argument("--approved-start-pose")
    parser.add_argument("--allow-low-entry", action="store_true")
    parser.add_argument("--pose-name", default="SCAN_HOME_CURRENT", help="world-scan-0: the name under which the measured pose is saved (never overwrites an existing file)")
    parser.add_argument("--sweep-deg", type=float, default=30.0)
    parser.add_argument("--slow-factor", type=float, default=2.0, help="every planned segment duration is multiplied by this (>= 1): first physical runs move at half the planned speed")
    parser.add_argument("--gripper-state", default="unknown", choices=("open", "closed", "holding", "unknown"))
    parser.add_argument("--cube-held", default="unknown", choices=("yes", "no", "unknown"))
    args = parser.parse_args(argv)
    if args.mode in ("plan", "execute") and not args.band:
        parser.error("--band is required for plan and execute")
    planning = args.mode in ("plan", "plan-elevated", "plan-sweep", "plan-return-low", "plan-local", "plan-pregrasp-0", "plan-pregrasp-return", "plan-pregrasp-correct", "plan-pregrasp-descend")
    if not planning and not args.pi_ssh:
        parser.error("--pi-ssh (or KC_PI_SSH) is required to capture on the Pi")
    try:  # without KC_RUNTIME_CONFIG this answers the legacy layout and nothing below changes
        use_runtime_paths(runtime_paths())
    except Exception as error:  # noqa: BLE001 -- a named config that cannot be used is a refusal, never a silent legacy run
        print(f"REFUSED before any motion: {config_refusal(error)}")
        return 3

    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig

    store = ObservationStore(session_id=args.scan_id, **store_root())
    session_dir = Path(store.session_dir)
    robot = LeKiwiClient(LeKiwiClientConfig(remote_ip=args.remote_ip, id=args.robot_id))
    robot.connect()
    wait_for_live_pose(robot)
    abort = {"set": False}
    signal.signal(signal.SIGINT, lambda *_: abort.update(set=True))  # first Ctrl-C: stop sending waypoints; the arm holds
    try:
        if args.mode == "world-scan-0":
            out_dir = session_dir / ("world-scan-0-" + time.strftime("%H%M%SZ", time.gmtime()))
            out_dir.mkdir(parents=True, exist_ok=True)
            return run_coexistence(robot, PiCamera(args.pi_ssh, out_dir), out_dir, args.pose_name)

        if args.slow_factor < 1.0:
            parser.error("--slow-factor may only slow the motion down")
        elevated = args.mode in ("plan-elevated", "execute-elevated")
        sweep = args.mode in ("plan-sweep", "execute-sweep")
        returning = args.mode in ("plan-return-low", "execute-return-low")
        local = args.mode in ("plan-local", "execute-local")
        pregrasp = args.mode in ("plan-pregrasp-0", "execute-pregrasp-0")
        pregrasp_return = args.mode in ("plan-pregrasp-return", "execute-pregrasp-return")
        pregrasp_correct = args.mode in ("plan-pregrasp-correct", "execute-pregrasp-correct")
        pregrasp_descend = args.mode in ("plan-pregrasp-descend", "execute-pregrasp-descend")
        floor = {}
        if pregrasp or pregrasp_return or pregrasp_correct or pregrasp_descend:
            refusal = gripper_open_refusal(read_pose(robot))
            if pregrasp_descend and None in (args.pregrasp_forward_mm, args.pregrasp_dz_mm):
                parser.error("pregrasp-descend needs --pregrasp-forward-mm and --pregrasp-dz-mm")
            if pregrasp and None in (args.pregrasp_pan_deg, args.pregrasp_forward_mm, args.pregrasp_dz_mm):
                parser.error("pregrasp-0 needs --pregrasp-pan-deg, --pregrasp-forward-mm and --pregrasp-dz-mm")
            if refusal or args.gripper_state != "open" or args.cube_held != "no":
                print(f"REFUSED before any motion: {refusal or 'a pregrasp needs --gripper-state open --cube-held no'}")
                return 3
        if pregrasp:
            origin = json.loads(args.approved_start_pose) if args.mode == "execute-pregrasp-0" and args.approved_start_pose else read_pose(robot)
            try:
                selection = dict(custom_poses=pregrasp_poses(origin, args.pregrasp_pan_deg, args.pregrasp_forward_mm / 1000.0, args.pregrasp_dz_mm / 1000.0))
            except ScanRefused as refusal:
                print(f"REFUSED before any motion: {refusal}")
                return 3
            floor = dict(min_link_height_m=PREGRASP_MIN_LINK_HEIGHT_M)
        elif pregrasp_descend:
            origin = json.loads(args.approved_start_pose) if args.mode == "execute-pregrasp-descend" and args.approved_start_pose else read_pose(robot)
            try:
                selection = dict(custom_poses=pregrasp_descend_poses(origin, args.pregrasp_forward_mm / 1000.0, args.pregrasp_dz_mm / 1000.0))
            except ScanRefused as refusal:
                print(f"REFUSED before any motion: {refusal}")
                return 3
            floor = dict(min_link_height_m=APPROACH_MIN_LINK_HEIGHT_M)
            args.allow_low_entry = True  # it starts at the pregrasp height by definition; the refusal above already required open and nothing held
        elif pregrasp_correct:
            if args.correct_pan_deg is None:
                parser.error("pregrasp-correct needs --correct-pan-deg")
            origin = json.loads(args.approved_start_pose) if args.mode == "execute-pregrasp-correct" and args.approved_start_pose else read_pose(robot)
            try:
                selection = dict(custom_poses=pregrasp_correct_poses(origin, args.correct_pan_deg), held_at={name: float(origin[name]) for name in HELD_IN_SWEEP})
            except ScanRefused as refusal:
                print(f"REFUSED before any motion: {refusal}")
                return 3
            floor = dict(min_link_height_m=PREGRASP_MIN_LINK_HEIGHT_M, low_pan_allowance_deg=PREGRASP_CORRECT_MAX_PAN_DEG)
            args.allow_low_entry = True  # it starts at the pregrasp height by definition; the refusal above already required open and nothing held
        elif pregrasp_return:
            selection = dict(custom_poses=pregrasp_return_poses(json.loads(HOME_FILE.read_text())["measured_joints_deg"]))
            args.allow_low_entry = True  # the exit starts low by definition; the refusal above already required open and nothing held
        elif local:
            if not args.local:
                parser.error("--local L1|L2|L3 is required")
            origin = json.loads(args.approved_start_pose) if args.mode == "execute-local" and args.approved_start_pose else read_pose(robot)
            try:
                selection = dict(custom_poses=local_poses(origin, args.local))
            except ScanRefused as refusal:
                print(f"REFUSED before any motion: {refusal}")
                return 3
        elif returning:
            origin = json.loads(args.approved_start_pose) if args.mode == "execute-return-low" and args.approved_start_pose else read_pose(robot)
            selection = dict(custom_poses=return_low_poses(origin), held_at={name: float(origin[name]) for name in HELD_IN_SWEEP})
        elif sweep:
            origin = json.loads(args.approved_start_pose) if args.mode == "execute-sweep" and args.approved_start_pose else read_pose(robot)
            selection = dict(custom_poses=sweep_poses(origin, args.sweep_deg), held_at={name: float(origin[name]) for name in HELD_IN_SWEEP})
        elif elevated:
            origin = json.loads(args.approved_start_pose) if args.mode == "execute-elevated" and args.approved_start_pose else read_pose(robot)
            try:
                selection = dict(custom_poses=[elevated_pose(origin)])
            except ScanRefused as refusal:
                print(f"REFUSED before any motion: {refusal}")
                return 3
        else:
            selection = dict(pose_ids=[next(p.pose_id for p in generate_scan_poses(stage=1) if p.band == args.band)])
        # the taught pose sits about 1.3 deg below its limit: never commanded higher (the pregrasp exit: up to the saved low pose only)
        wrist_flex_ceiling = wrist_flex_ceiling_for(read_pose(robot), selection["custom_poses"] if pregrasp_return else None)

        def send_arm_only(command: dict):
            return robot.send_action(arm_only_command(command, wrist_flex_ceiling))

        bounded_move = make_bounded_move_arm(lambda: read_pose(robot), send_arm_only)
        if pregrasp or pregrasp_return or pregrasp_correct or pregrasp_descend:
            _unguarded_move = bounded_move

            def bounded_move(target, duration_s, should_abort):  # the gripper gate is re-read before EVERY segment, not only at start
                refusal = gripper_open_refusal(read_pose(robot))
                if refusal:
                    raise RuntimeError(refusal)  # the executor turns this into an abort: no further motion
                return _unguarded_move(target, duration_s, should_abort)
        camera = None if planning else PiCamera(args.pi_ssh, session_dir)

        def timed_move(target, duration_s, should_abort):
            # [SCAN_SEGMENT] logs the PLANNER duration; the duration actually commanded is that x the slow factor. Both, and the
            # measured wall-clock, are recorded here so the run log itself shows the speed that was used.
            commanded_s, started = duration_s * args.slow_factor, time.monotonic()
            measured = bounded_move(target, commanded_s, should_abort)
            print(f"[ARM_MOVE] planner {duration_s:.2f} s x slow-factor {args.slow_factor} = {commanded_s:.2f} s commanded ({max(1, round(commanded_s * 30))} ticks at 30 Hz); wall-clock {time.monotonic() - started:.2f} s", flush=True)
            return measured

        deps = ScanWorldDeps(
            read_pose=lambda: read_pose(robot),
            read_base=lambda: read_base(robot),
            move_arm=timed_move,
            capture=(camera.capture if camera else (lambda role: (_ for _ in ()).throw(RuntimeError("plan mode never captures")))),
            now_utc=now_utc,
            sleep=time.sleep,
            should_abort=lambda: abort["set"],
            log=lambda event, **fields: print(f"[{event}] {fields}", flush=True),
        )
        common = dict(stage=1, store=store, calibration=CalibrationInventory.empty(), scan_id=args.scan_id, **selection, **floor,
                      allow_low_entry=args.allow_low_entry, gripper_state=args.gripper_state, cube_held=args.cube_held)
        try:
            if planning:
                measured = read_pose(robot)
                if local or returning or pregrasp or pregrasp_return or pregrasp_correct or pregrasp_descend:
                    print(f"LIVE start pose (deg): { {k: round(v, 3) for k, v in measured.items()} }")
                    for pose in selection["custom_poses"]:
                        print(f"  {pose.pose_id:22s} target (deg): { {k: round(v, 3) for k, v in pose.joints.items()} }  deltas from live { {k: round(pose.joints[k] - measured[k], 1) for k in SCAN_JOINTS} }")
                    if pregrasp or pregrasp_return or pregrasp_correct or pregrasp_descend:
                        from hybrid_pick_place import KNOWN_GOOD_GRASP_POSE_DEG

                        grasp_z = float(cartesian_lift.forward_kinematics({**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0})[2, 3])
                        for pose in selection["custom_poses"]:
                            heights = link_heights_m(pose.joints)
                            print(f"  {pose.pose_id:22s} FK tool at {tuple(round(v, 3) for v in pose.tool_position_m)} m (arm frame); lowest link {min(heights.values()):+.3f} m = {(min(heights.values()) - grasp_z) * 1000:+.0f} mm relative to the PROVEN GRASP tool height {grasp_z:+.3f} m")
                        if pregrasp_descend:
                            now_z = float(cartesian_lift.forward_kinematics({**{k: measured[k] for k in SCAN_JOINTS}, "wrist_roll": 0.0})[2, 3])
                            target_z = float(selection["custom_poses"][0].tool_position_m[2])
                            print(f"BOUNDED DESCENT: FK tool height {(now_z - grasp_z) * 1000:+.1f} mm (measured) -> {(target_z - grasp_z) * 1000:+.1f} mm above the proven grasp height = a drop of {(now_z - target_z) * 1000:.1f} mm, pan held, grasp orientation restored; "
                                  f"jaw tips stay about {(target_z - grasp_z) * 1000 - 25.4:.0f} mm above the cube TOP plane if they reach the floor at the proven grasp")
                        print(f"NON-CONTACT: link-height floor for this run {floor.get('min_link_height_m', 'scan default')} m (arm frame); measured gripper {measured['gripper']:.2f} (proven normal-open 80.193); wrist_flex ceiling {wrist_flex_ceiling:.2f}")
                        print("gripper NOT in any command (it holds its own acquired goal); wrist_roll held; base pinned to zero; " + (f"LOCAL CORRECTION: the pan alone moves, at most {PREGRASP_CORRECT_MAX_PAN_DEG} deg, at the pregrasp height; lift / elbow / wrist_flex are not commanded to move" if pregrasp_correct else "BOUNDED DESCENT: the pan is held; NO contact is planned" if pregrasp_descend else "the pan only moves at scan height"))
                    else:
                        print("gripper NOT in any command; wrist_roll held; base pinned to zero; wrist_flex only ever DEcreases on this path")
                elif sweep:
                    print(f"SCAN_HOME_UPRIGHT measured now (deg): { {k: round(v, 3) for k, v in measured.items()} }")
                    for pose in selection["custom_poses"]:
                        print(f"  {pose.pose_id:15s} target (deg): {pose.joints}   pan delta from home {pose.joints['shoulder_pan'] - measured['shoulder_pan']:+.1f}")
                    print(f"held exactly at their measured values (never commanded to move): {selection['held_at']}; wrist_roll held; gripper NOT in any command; base pinned to zero")
                elif elevated:
                    target = selection["custom_poses"][0]
                    print(f"SCAN_HOME_CURRENT measured now (deg): { {k: round(v, 2) for k, v in measured.items()} }")
                    print(f"SCAN_ELEVATED_1 target (deg): {target.joints}")
                    print(f"exact joint deltas (deg): { {k: round(target.joints[k] - measured[k], 2) for k in SCAN_JOINTS} }")
                    print(f"primitive: cartesian_lift.solve_cartesian_z_lift(+{ELEVATED_LIFT_M} m, pan held, tool orientation kept) executed by scan_arm_motion.make_bounded_move_arm (canonical_handoff interpolation, wrist roll and gripper held, base pinned to zero)")
                    now_m = tuple(round(float(v), 3) for v in cartesian_lift.forward_kinematics(dict(measured))[:3, 3])
                    heights = {k: round(v, 3) for k, v in link_heights_m({**measured, **target.joints}).items()}
                    print(f"tool position (arm frame, m): now {now_m} -> target {tuple(round(v, 3) for v in target.tool_position_m)}; link heights at target {heights}")
                else:
                    print_targets(measured)  # shown even when the planner then refuses the entry
                result = run_scan_world(deps, **common)
                current = {f"arm_{k}.pos": v for k, v in measured.items()}
                print_selected(result, measured, manipulation_action_to_robot_command(freeze_wrist_roll(current, measured["wrist_roll"])), args.slow_factor)
                print(f"segment durations above are the planner values; this CLI executes them x{args.slow_factor} (slower), i.e. at most {20.0 / args.slow_factor:.0f} deg/s per joint")
                print("PLAN ONLY: nothing was commanded.")
                return 0
            if not args.approved_digest or not args.approved_start_pose:
                parser.error("execute needs --approved-digest and --approved-start-pose from a plan the operator approved")
            before = {r["observation_id"] for r in store.load_all()}
            if sweep:  # the front camera does not move with the arm: one coarse environmental frame at scan start
                front = camera.capture("front")
                if front.frame is not None:
                    front_path = session_dir / f"sweep-front-{time.strftime('%H%M%SZ', time.gmtime())}.png"
                    cv2.imwrite(str(front_path), front.frame)
                    print(f"FRONT (once, at scan start): {front_path}")
            pose_before, base_before = read_pose(robot), read_base(robot)
            print(f"measured arm pose before the move (deg): {pose_before}\nbase velocity before: {base_before}")
            result = run_scan_world(deps, execute=True, approved_plan_digest=args.approved_digest, approved_start_pose=json.loads(args.approved_start_pose), **common)
        except ScanRefused as refusal:
            print(f"REFUSED before any motion: {refusal}")
            return 3

        print(f"\n{'PREGRASP_0' if pregrasp else 'PREGRASP BOUNDED DESCENT' if pregrasp_descend else 'PREGRASP LOCAL CORRECTION' if pregrasp_correct else 'PREGRASP EXIT' if pregrasp_return else 'LOCAL ' + args.local if local else 'LOW-REGISTRATION ENTRY' if returning else 'SCAN_SWEEP' if sweep else 'SCAN_ELEVATED_1' if elevated else args.band} OBSERVATION   abort reason: {result.abort_reason}")
        print(f"final measured arm pose (deg): {result.final_measured_pose}\nbase velocity now: {read_base(robot)}")
        if result.final_measured_pose is not None:
            final, requested = result.final_measured_pose, result.poses[-1].joints
            print(f"requested target (deg): {requested}\njoint errors, measured - target (deg): { {k: round(final[k] - requested[k], 2) for k in SCAN_JOINTS} }")
            moved = cartesian_lift.forward_kinematics(dict(final))[:3, 3] - cartesian_lift.forward_kinematics(dict(pose_before))[:3, 3]
            print(f"actual FK tool displacement from the MEASURED poses (arm frame, m): dx {moved[0]:+.4f}, dy {moved[1]:+.4f}, dz {moved[2]:+.4f}")
            held = {k: round(final[k] - pose_before[k], 2) for k in ("shoulder_pan", "wrist_roll", "gripper")}
            print(f"joints that must not have moved (deg): {held}")
        detector = make_detector()
        if sweep and front.frame is not None:
            print("\n".join(perceive("front", front.frame, session_dir / "perception", front_path.stem, detector)))
        swept = []
        for record in store.load_all():
            if record["observation_id"] in before:
                continue
            frame_path = session_dir / record["raw_frame_path"] if record.get("raw_frame_path") else None
            print(f"observation {record['observation_id']}: measured joints {record['robot_state']['measured_joints_deg']}\nimage: {frame_path}")
            if frame_path is not None:
                print("\n".join(perceive("wrist", cv2.imread(str(frame_path)), session_dir / "perception", record["observation_id"], detector)))
                swept.append((record["scan"]["pose_id"], record, json.loads((session_dir / "perception" / f"{record['observation_id']}.json").read_text())))
        if sweep and swept:
            print("\n".join(fuse_sweep(swept, session_dir / f"scan-sweep-{int(args.sweep_deg)}-{time.strftime('%H%M%SZ', time.gmtime())}.json", args.sweep_deg)))
        print("The arm HOLDS this pose. STOP: inspect before the next band.")
        return 0 if result.abort_reason is None else 4
    finally:
        robot.disconnect()


if __name__ == "__main__":
    sys.exit(main())
