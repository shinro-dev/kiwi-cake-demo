#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""scan_poses: the deterministic, bounded set of elevated arm observation
poses for the SCAN_WORLD active-perception primitive, plus the FK checks that
gate them. Pure kinematics: nothing here talks to a robot.

STATUS: PROPOSED, NEVER ACTUATED (2026-09-19). Offline FK checks are a
necessary condition, not a safety proof. Before the first physical scan the
operator must approve the exact pose set (see pose_set_digest and
scan_world.py); cable strain, chassis/mast clearance and realized-vs-
commanded error on the real rig are not modeled here.

Design (autonomous-single-cube-plan.md section 7):

  bands    Three elevated arm shapes that tilt the tool approach axis
           progressively further below horizontal. The depression of the tool
           axis equals shoulder_lift + elbow_flex + wrist_flex for this arm
           (verified against cartesian_lift's FK in the tests), so a band is a
           deliberate viewing elevation, and different bands also sit at
           different heights and radii, which gives parallax between views of
           the same ground region.
  sectors  shoulder_pan targets. Pan only ever changes while the arm is
           elevated. Sectors are staged so that the first physical scan
           changes one thing at a time: stage 1 keeps pan where the arm has
           already worked, stage 2 adds +/-30 degrees, stage 3 adds +/-60.
  order    Serpentine: each step changes pan or band, never both.

FAR is anchored on the physically held CANONICAL_REVEAL_POSE: same
shoulder_lift and elbow_flex within 0.5 degree, wrist_flex about 10 degrees
further. MID and NEAR have not been physically held. Every band keeps the
height bound even if shoulder_lift sags by SCAN_SAG_ALLOWANCE_DEG, because
the executor re-checks link heights on the MEASURED joints after every
segment and a band with no margin would abort on ordinary gravity sag.

Entry: the scan does not own the floor-to-elevated transition. A joint-space
rise from a floor pose swings the tool OUTWARD before it is up (about 10 cm
at floor level from the known-good grasp pose, FK-computed), which would
push anything standing in front of the gripper. A low start is therefore
refused unless the caller passes allow_low_entry=True, in which case the
ENTRY_RISE segment reports outward_sweep_while_low_m so the operator approves
that sweep knowingly (clear workspace, nothing held). A start pan more than
one sector step outside the stage's own pan range is refused as well. The approval token (scan_plan_digest) binds
the pose set, the start pose the plan was shown for, and the low-entry
permission, so an approval cannot carry over to a different entry motion.

What is NOT claimed: camera coverage. The wrist camera's pose relative to the
tool frame is uncalibrated, so bands are labelled by the nominal TOOL-AXIS
depression and sectors by the FK heading of the tool approach axis in the
ARM base_link frame (insensitive to wrist_roll, under 0.001 degree, and to
where the frame origin sits relative to the pan axis). Both are scan bookkeeping labels, not a
camera footprint. The pan sign is never assumed: the heading comes from FK
(negative shoulder_pan points the tool toward +y of the arm frame).

wrist_roll, the gripper and the base are never part of a scan pose: the
executor holds wrist_roll and the gripper at their freshly measured values
and the base is zero by construction (action_adapter).
"""
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manipulation"))

import cartesian_lift  # noqa: E402
from hybrid_pick_place import JOINT_LIMIT_MARGIN_DEG, JOINT_LIMITS_DEG, SINGULARITY_MARGIN_DEG  # noqa: E402

SCAN_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")

# Scan policy bounds. These are deliberately tighter than the mechanical
# limits and are NOT measurements of them.
SCAN_PAN_ABS_MAX_DEG = 60.0
SCAN_MIN_LINK_HEIGHT_M = 0.10  # every link origin past the shoulder, above the arm base_link origin
SCAN_EXTRA_SINGULARITY_MARGIN_DEG = 5.0
SCAN_SAG_ALLOWANCE_DEG = 5.0  # shoulder_lift sag a pose must tolerate and still clear the height bound
MAX_ENTRY_PAN_SWEEP_DEG = 30.0  # a start pan further than this outside the stage's pan range is refused, not swept
TRANSIT_DIP_TOLERANCE_M = 0.002
TRANSIT_SAMPLES = 21
PAN_HELD_TOLERANCE_DEG = 0.5

SCAN_MAX_JOINT_SPEED_DEG_S = 20.0
SCAN_MIN_SEGMENT_DURATION_S = 1.0
SCAN_CONTROL_FPS = 30

FRAME = "ARM_BASE_LINK_FK"
COVERAGE_BASIS = "NOMINAL_TOOL_AXIS_UNCALIBRATED"

# wrist_roll is unknown at planning time (runtime value, never forced). It
# only spins the tool about its own axis, so nominal geometry uses 0.
_NOMINAL_WRIST_ROLL_DEG = 0.0
_HEIGHT_LINKS = ("elbow_flex", "wrist_flex", "wrist_roll")


@dataclass(frozen=True)
class ScanBand:
    name: str
    shoulder_lift: float
    elbow_flex: float
    wrist_flex: float

    def joints(self) -> dict:
        return {"shoulder_lift": self.shoulder_lift, "elbow_flex": self.elbow_flex, "wrist_flex": self.wrist_flex}

    @property
    def nominal_tool_depression_deg(self) -> float:
        return self.shoulder_lift + self.elbow_flex + self.wrist_flex


DEFAULT_BANDS = (
    ScanBand("FAR", shoulder_lift=-58.0, elbow_flex=18.0, wrist_flex=60.0),
    ScanBand("MID", shoulder_lift=-52.0, elbow_flex=22.0, wrist_flex=72.0),
    ScanBand("NEAR", shoulder_lift=-42.0, elbow_flex=20.0, wrist_flex=77.0),
)

STAGE_PAN_TARGETS_DEG = {
    1: (-10.0,),
    2: (-30.0, 0.0, 30.0),
    3: (-60.0, -30.0, 0.0, 30.0, 60.0),
}


@dataclass(frozen=True)
class ScanPose:
    pose_id: str
    band: str
    joints: dict
    nominal_tool_depression_deg: float
    tool_azimuth_deg: float
    tool_position_m: tuple
    frame: str = FRAME
    coverage_basis: str = COVERAGE_BASIS


@dataclass(frozen=True)
class ScanSegment:
    kind: str  # ENTRY_RISE | ENTRY_PAN | SCAN
    target: dict
    duration_s: float
    pose_id: Optional[str] = None  # set when an observation is taken at the end of this segment
    outward_sweep_while_low_m: float = 0.0  # how far the tool radius grows while any link is below the scan height


def _fk_joints(joints: dict) -> dict:
    return {"wrist_roll": _NOMINAL_WRIST_ROLL_DEG, **joints}


def link_heights_m(joints: dict) -> dict:
    """z of each moving link origin past the shoulder and of the tool frame,
    in the arm base_link frame. Walks the same chain as
    cartesian_lift.forward_kinematics (the tests pin the two together)."""
    full = _fk_joints(joints)
    transform = np.eye(4)
    heights = {}
    for name in cartesian_lift.JOINT_ORDER:
        origin_xyz, origin_rpy = cartesian_lift._JOINT_GEOMETRY[name]
        transform = transform @ cartesian_lift._joint_transform(origin_xyz, origin_rpy, np.deg2rad(full[name]))
        if name in _HEIGHT_LINKS:
            heights[name] = float(transform[2, 3])
    heights["tool"] = float((transform @ cartesian_lift._GRIPPER_FRAME_TRANSFORM)[2, 3])
    return heights


def _lowest_link_m(joints: dict) -> float:
    return min(link_heights_m(joints).values())


def _shape_reasons(joints: dict) -> list:
    keys = set(joints)
    reasons = []
    if keys - set(SCAN_JOINTS):
        reasons.append(f"unexpected key(s) {sorted(keys - set(SCAN_JOINTS))}: a scan pose names only {SCAN_JOINTS}")
    if set(SCAN_JOINTS) - keys:
        reasons.append(f"missing joint(s) {sorted(set(SCAN_JOINTS) - keys)}")
    if not reasons:
        for name in SCAN_JOINTS:
            value = joints[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                reasons.append(f"{name} must be a finite real number, got {value!r}")
    return reasons


def validate_scan_pose(joints: dict, held_at: dict = None, min_link_height_m: float = None, elbow_band: bool = True) -> list:
    """Reasons this pose may not be used as a scan pose; empty means it
    passed the offline checks (which is not a physical safety proof).

    min_link_height_m: None means the scan minimum. A caller that is NOT
    scanning (the non-contact pregrasp above the cube) passes its own lower
    floor explicitly; the sag margin is checked against that same floor.

    held_at (named hand-taught scan poses only, e.g. SCAN_HOME_UPRIGHT): joints
    whose target IS their measured value, i.e. joints this pose does not move.
    Such a joint cannot be driven toward its limit by this pose, so the
    approach-to-limit margin is not applied to it. The exemption never covers
    a joint whose target differs from the held value; every other check stays."""
    reasons = _shape_reasons(joints)
    if reasons:
        return reasons

    for name, limit in JOINT_LIMITS_DEG.items():
        if held_at is not None and name in held_at and abs(joints[name] - float(held_at[name])) <= 0.01:
            continue  # held at its measured value: not commanded to move
        if abs(joints[name]) >= limit - JOINT_LIMIT_MARGIN_DEG:
            reasons.append(f"{name}={joints[name]:.2f} is within {JOINT_LIMIT_MARGIN_DEG} deg of its +/-{limit} limit")

    # elbow_band=False (manipulation steps only): the caller owns the singularity guard there and applies it to the
    # target AND to the interpolated path; this scan-policy band around elbow 0 / 180 stays on for every scan pose.
    elbow_margin = SINGULARITY_MARGIN_DEG + SCAN_EXTRA_SINGULARITY_MARGIN_DEG
    elbow = joints["elbow_flex"]
    if elbow_band and min(abs(elbow), abs(elbow - 180.0), abs(elbow + 180.0)) <= elbow_margin:
        reasons.append(f"elbow_flex={elbow:.2f} is within {elbow_margin} deg of a singularity (0/180)")

    if abs(joints["shoulder_pan"]) > SCAN_PAN_ABS_MAX_DEG:
        reasons.append(f"shoulder_pan={joints['shoulder_pan']:.2f} exceeds the scan policy bound +/-{SCAN_PAN_ABS_MAX_DEG}")

    floor = SCAN_MIN_LINK_HEIGHT_M if min_link_height_m is None else min_link_height_m
    lowest = _lowest_link_m(joints)
    if lowest < floor:
        reasons.append(f"lowest link height {lowest:.3f} m is below the scan minimum {floor} m")
    else:
        sagged = _lowest_link_m({**joints, "shoulder_lift": joints["shoulder_lift"] + SCAN_SAG_ALLOWANCE_DEG})
        if sagged < floor:
            reasons.append(
                f"no sag margin: {SCAN_SAG_ALLOWANCE_DEG} deg of shoulder_lift sag drops the lowest link to "
                f"{sagged:.3f} m, below the scan minimum {floor} m"
            )
    return reasons


def validate_transit(start: dict, end: dict, min_link_height_m: float = SCAN_MIN_LINK_HEIGHT_M, low_pan_allowance_deg: float = 0.0) -> list:
    """FK-sample the straight joint-space path the bounded interpolator will
    follow. `start` may be a low measured pose (scan entry); `end` must be
    elevated. Rules: the path never dips below min(start, scan minimum); pan
    may only change while every sample is elevated. A lower min_link_height_m
    (pregrasp) moves the height floor only: "elevated" for the pan rule is
    always the scan minimum, so the pan never changes near the floor. The one
    exception is explicit and bounded: low_pan_allowance_deg (default 0) lets a
    local correction at the non-contact pregrasp height change the pan by at
    most that much; the height rules above still apply to every sample."""
    start = {name: start[name] for name in SCAN_JOINTS}
    end = {name: end[name] for name in SCAN_JOINTS}
    reasons = []

    end_lowest = _lowest_link_m(end)
    if end_lowest < min_link_height_m:
        reasons.append(f"transit ends at {end_lowest:.3f} m, below the scan minimum {min_link_height_m} m")

    samples = [
        _lowest_link_m({name: start[name] + t * (end[name] - start[name]) for name in SCAN_JOINTS})
        for t in np.linspace(0.0, 1.0, TRANSIT_SAMPLES)
    ]
    if not all(math.isfinite(sample) for sample in samples):
        return reasons + ["path could not be evaluated: a joint value is not finite"]
    floor = min(samples[0], min_link_height_m) - TRANSIT_DIP_TOLERANCE_M
    if min(samples) < floor:
        reasons.append(f"path dips to {min(samples):.3f} m, below the allowed {floor:.3f} m")

    pan_changes = abs(end["shoulder_pan"] - start["shoulder_pan"]) > PAN_HELD_TOLERANCE_DEG
    pan_change = abs(end["shoulder_pan"] - start["shoulder_pan"])
    if pan_changes and min(samples) < max(min_link_height_m, SCAN_MIN_LINK_HEIGHT_M) - TRANSIT_DIP_TOLERANCE_M and not pan_change <= low_pan_allowance_deg:
        reasons.append(f"shoulder_pan may only change while the arm is elevated: rise first with pan held (low pan allowance {low_pan_allowance_deg} deg, requested {pan_change:.2f})")
    return reasons


def segment_duration_s(start: dict, end: dict) -> float:
    largest = max(abs(end[name] - start[name]) for name in SCAN_JOINTS)
    return max(SCAN_MIN_SEGMENT_DURATION_S, largest / SCAN_MAX_JOINT_SPEED_DEG_S)


def tool_axis_heading_deg(joints: dict) -> float:
    """Heading of the tool approach axis (tool frame +z) in the arm base_link
    xy plane. Insensitive to wrist_roll (under 0.001 degree), which only spins
    the tool about that axis. Ill-defined for a tool pointing straight down; scan bands stay far
    from that."""
    axis = cartesian_lift.forward_kinematics(_fk_joints(joints))[:3, 2]
    return math.degrees(math.atan2(axis[1], axis[0]))


def _outward_sweep_while_low_m(start: dict, end: dict) -> float:
    start_radius = None
    sweep = 0.0
    for t in np.linspace(0.0, 1.0, TRANSIT_SAMPLES):
        joints = {name: start[name] + t * (end[name] - start[name]) for name in SCAN_JOINTS}
        x, y, _z = cartesian_lift.end_effector_position(_fk_joints(joints))
        radius = math.hypot(x, y)
        start_radius = radius if start_radius is None else start_radius
        if _lowest_link_m(joints) < SCAN_MIN_LINK_HEIGHT_M:
            sweep = max(sweep, radius - start_radius)
    return float(sweep)


def generate_scan_poses(stage: int, bands=DEFAULT_BANDS) -> list:
    if stage not in STAGE_PAN_TARGETS_DEG:
        raise ValueError(f"unknown scan stage {stage!r}; expected one of {sorted(STAGE_PAN_TARGETS_DEG)}")
    pans = STAGE_PAN_TARGETS_DEG[stage]
    poses = []
    for band_index, band in enumerate(bands):
        for pan in pans if band_index % 2 == 0 else tuple(reversed(pans)):
            joints = {"shoulder_pan": float(pan), **band.joints()}
            transform = cartesian_lift.forward_kinematics(_fk_joints(joints))
            x, y, z = transform[:3, 3]
            poses.append(
                ScanPose(
                    pose_id=f"s{stage}-{band.name}-p{pan:+04.0f}",
                    band=band.name,
                    joints=joints,
                    nominal_tool_depression_deg=band.nominal_tool_depression_deg,
                    tool_azimuth_deg=tool_axis_heading_deg(joints),
                    tool_position_m=(float(x), float(y), float(z)),
                )
            )
    return poses


def plan_scan_segments(measured_start: dict, poses: list, allow_low_entry: bool = False, held_at: dict = None, min_link_height_m: float = None,
                       low_pan_allowance_deg: float = 0.0, elbow_band: bool = True) -> list:
    """Bounded segments from the freshly measured start pose through every
    scan pose: rise with pan held, then pan, then the serpentine. Raises
    ValueError (nothing is returned, so nothing can be executed) if the start
    pose, any pose or any transit fails its offline check."""
    reasons = _shape_reasons({name: measured_start.get(name) for name in SCAN_JOINTS if name in measured_start})
    if reasons:
        raise ValueError(f"scan entry refused: the measured start pose is unusable: {reasons}")
    current = {name: float(measured_start[name]) for name in SCAN_JOINTS}
    for pose in poses:
        reasons = validate_scan_pose(pose.joints, held_at, min_link_height_m, elbow_band)
        if reasons:
            raise ValueError(f"scan pose {pose.pose_id} refused: {reasons}")

    start_lowest = _lowest_link_m(current)
    if start_lowest < SCAN_MIN_LINK_HEIGHT_M and not allow_low_entry:
        raise ValueError(
            f"scan entry refused: the arm starts low (lowest link {start_lowest:.3f} m, scan minimum "
            f"{SCAN_MIN_LINK_HEIGHT_M} m). A joint-space rise from a low pose swings the tool outward near the floor; "
            "elevate the arm with an approved lift first, or plan with allow_low_entry=True and approve the reported sweep"
        )

    first = poses[0]
    # The stage's own pan range is what the operator approves. A start pan
    # further than one sector step outside it means the arm is not where this
    # stage expects it, and sweeping it back would be unapproved motion.
    stage_pans = [pose.joints["shoulder_pan"] for pose in poses]
    pan_excess = max(min(stage_pans) - current["shoulder_pan"], current["shoulder_pan"] - max(stage_pans), 0.0)
    if pan_excess > MAX_ENTRY_PAN_SWEEP_DEG:
        raise ValueError(
            f"scan entry refused: the start pan {current['shoulder_pan']:.1f} is {pan_excess:.1f} deg outside this stage's pan "
            f"range [{min(stage_pans):.0f}, {max(stage_pans):.0f}], more than one sector step ({MAX_ENTRY_PAN_SWEEP_DEG} deg)"
        )
    rise_target = {**first.joints, "shoulder_pan": current["shoulder_pan"]}
    # The rise holds the MEASURED pan, so an unexpected start pan (outside the
    # scan policy bound) is refused here instead of being swept back silently.
    reasons = validate_scan_pose(rise_target, held_at, min_link_height_m, elbow_band)
    if reasons:
        raise ValueError(f"scan entry refused from the measured start pose: {reasons}")
    steps = [("ENTRY_RISE", rise_target, None), ("ENTRY_PAN", dict(first.joints), first.pose_id)]
    steps += [("SCAN", dict(pose.joints), pose.pose_id) for pose in poses[1:]]

    segments = []
    for kind, target, pose_id in steps:
        reasons = validate_transit(current, target, SCAN_MIN_LINK_HEIGHT_M if min_link_height_m is None else min_link_height_m, low_pan_allowance_deg)
        if reasons:
            raise ValueError(f"scan transit {kind} -> {pose_id or 'rise'} refused: {reasons}")
        segments.append(
            ScanSegment(
                kind=kind,
                target=target,
                duration_s=segment_duration_s(current, target),
                pose_id=pose_id,
                outward_sweep_while_low_m=_outward_sweep_while_low_m(current, target),
            )
        )
        current = target
    return segments


def pose_set_digest(poses: list) -> str:
    """Identifies an exact pose set. The operator approves a digest, so any
    change to any joint target invalidates an earlier approval."""
    canonical = [[pose.pose_id, [[name, round(float(pose.joints[name]), 6)] for name in SCAN_JOINTS]] for pose in poses]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()[:16]


def scan_plan_digest(poses: list, start_pose: dict, allow_low_entry: bool, min_link_height_m: float = None, low_pan_allowance_deg: float = 0.0) -> str:
    """The operator approval token. It binds everything that determines the
    commanded motion: the exact pose set, the start pose the plan-only output
    was shown for (the entry segments are derived from it) and whether a low
    entry was permitted. The executor refuses to run unless the freshly
    measured start is still within a small tolerance of this start pose.
    A non-default link-height floor is bound as well (it decides which
    descents are accepted); None leaves every earlier digest unchanged."""
    canonical = [
        pose_set_digest(poses),
        [[name, round(float(start_pose[name]), 1)] for name in SCAN_JOINTS],
        bool(allow_low_entry),
    ]
    if min_link_height_m is not None:
        canonical.append(["min_link_height_m", round(float(min_link_height_m), 4)])
    if low_pan_allowance_deg:
        canonical.append(["low_pan_allowance_deg", round(float(low_pan_allowance_deg), 3)])
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()[:16]
