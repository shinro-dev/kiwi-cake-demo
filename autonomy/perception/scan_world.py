#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""scan_world: the SCAN_WORLD active-perception executor. From a STATIONARY
base it walks the arm through the bounded observation pose set of
scan_poses.py and, at every pose, stores one observation that pairs the image
with the joints MEASURED immediately before and after it.

STATUS: NEVER RUN ON HARDWARE (2026-09-19). The contracts below are proven
against a fake robot only.

Contracts:

  plan-only by default   With execute=False nothing moves and nothing is
                         captured; the caller gets the pose set, the planned
                         segments, the measured start pose and the plan digest
                         to show the operator.
  operator approval      execute=True is refused unless the caller passes the
                         plan digest AND the start pose that plan was shown
                         for. The digest binds the exact pose set, that start
                         pose and the low-entry permission, and the freshly
                         measured start must still be within
                         START_POSE_TOLERANCE_DEG of it. The entry motion is
                         derived from the start pose, so an approval cannot
                         carry over to an arm that has moved since, to another
                         stage, or to a different entry.
  low entry              A low start pose is refused unless low entry is part
                         of the approved plan (its floor-level outward sweep is
                         reported per segment), and never while holding.
  zero base motion       This module has no channel to the base. Its only
                         actuation dependency is move_arm(target, duration_s,
                         should_abort), built by scan_arm_motion on the
                         existing bounded interpolation + wrist_roll freeze +
                         manipulation/base boundary (that boundary hard-zeroes
                         the base fields and rejects any other key). It holds
                         no gateway, no transport and no robot handle. On top
                         of that it CONFIRMS base zero from repeated, spaced
                         reads immediately before EVERY segment and after it,
                         and treats a non-zero base read taken with an
                         observation as an abort: that view is stored as
                         evidence but is not coverage.
  refuse before motion   A missing or mismatched approval, a non-zero base, or
                         a start pose / transit that fails the offline FK
                         checks is refused before the first command
                         (ScanRefused).
  stop, never recover    After motion has started, any anomaly (pose not
                         reached within tolerance, MEASURED link height below
                         the scan minimum, base not zero, operator abort
                         before or during a segment, store refusal, or any
                         exception from the robot, camera or store) ends the
                         scan with an abort_reason instead of propagating. The
                         arm is left where it is, torqued; nothing is commanded
                         to "recover". Observations already stored are kept.
  measured, not commanded  Scan labels (azimuth, depression) are computed
                         from the measured joints of that view.
  evidence over success  A failed or dark capture is stored as a record and
                         simply does not count as coverage.

The scan ends at the last (elevated) pose. It never descends on its own:
returning the arm to a low pose is a separate motion with its own approval.
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manipulation"))

from base_rest import reads_at_rest  # noqa: E402
from frame_illumination import illumination_diagnostics  # noqa: E402
from observation_store import ObservationStoreError  # noqa: E402
from scan_coverage import COVERAGE_BASIS, FRAME, ScanCoverage, sector_index_for_azimuth  # noqa: E402
from scan_poses import (  # noqa: E402
    SCAN_JOINTS,
    SCAN_MIN_LINK_HEIGHT_M,
    generate_scan_poses,
    link_heights_m,
    plan_scan_segments,
    pose_set_digest,
    scan_plan_digest,
    tool_axis_heading_deg,
)
from world_observation import BASE_VELOCITY_KEYS, MEASURED_JOINT_KEYS, build_observation, new_observation_id  # noqa: E402

ARRIVAL_TOLERANCE_DEG = 5.0  # this project's existing blanket arm arrival tolerance
START_POSE_TOLERANCE_DEG = 2.0  # how far the arm may have drifted since the plan the operator approved
DEFAULT_SETTLE_S = 1.0
# "Zero" is judged on the host's KINEMATIC base velocity, at the resolution that measurement really has: one velocity
# count per WHEEL (base_rest.py). Observed 2026-09-19 with the base torque OFF and the wheels not moving: the host
# alternates between exact zero and one count on one wheel, which an exact-zero test (the former 1e-6) rejected about two
# reads in three. The operator decided then that one count is noise and about two counts is not zero (2026-09-19); the
# per-axis band that implemented it (x 0.0025 m/s, y 0.0030 m/s, theta 0.70 deg/s) was sized for ONE wheel at one count.
# Two wheels at one count each at the same instant add up on the body axes (theta 1.17 deg/s or x 0.0044 m/s), were
# recorded six times (probably five distinct samples) in 975 reads at rest, and one of them stopped run console-20260927T142146Z-m6ed2dd9 holding the
# block with no base motion commanded. Operator ruling 2026-09-27 (docs/autonomous-demo/decision-log.md): the count is
# judged per wheel, by the one rule every site shares. The three-consecutive-reads rule is unchanged.
BASE_ZERO_CONSECUTIVE_READS = 3
BASE_ZERO_MAX_READS = 6
BASE_ZERO_POLL_S = 0.15  # several host publish periods, so consecutive reads are independent samples
DEFAULT_IMAGE_ORIENTATION = "direct-v4l-unrotated"


class ScanRefused(RuntimeError):
    """Raised only BEFORE any motion has been commanded."""


@dataclass
class ScanWorldDeps:
    read_pose: Callable[[], dict]  # fresh measured joints: MEASURED_JOINT_KEYS, degrees
    read_base: Callable[[], dict]  # fresh measured base velocity: BASE_VELOCITY_KEYS
    move_arm: Callable[[dict, float, Callable[[], bool]], dict]  # (SCAN_JOINTS targets, duration_s, should_abort) -> fresh measured joints
    capture: Callable[[str], object]  # camera role -> camera_lifecycle.CaptureResult
    now_utc: Callable[[], str]
    sleep: Callable[[float], None]
    should_abort: Callable[[], bool] = lambda: False
    log: Callable[..., None] = lambda event, **fields: None


@dataclass
class ScanWorldResult:
    executed: bool
    pose_set_digest: str
    poses: list
    segments: list
    plan_digest: Optional[str] = None  # the operator approval token for THIS start pose
    start_pose: Optional[dict] = None  # measured SCAN_JOINTS the plan was made from
    observation_ids: list = field(default_factory=list)
    coverage: Optional[dict] = None
    abort_reason: Optional[str] = None
    final_measured_pose: Optional[dict] = None


def _base_is_zero(deps: ScanWorldDeps) -> bool:
    """At rest (_reads_zero: no wheel above one velocity count) on BASE_ZERO_CONSECUTIVE_READS consecutive reads.
    Transient motion has been observed on this base, so a read that is not at rest is
    retried (bounded) and must settle; it is never averaged away. Reads are
    spaced by BASE_ZERO_POLL_S: the robot client returns its cached state
    when no new message has arrived, so back-to-back reads would be one
    sample counted three times."""
    consecutive = 0
    for attempt in range(BASE_ZERO_MAX_READS):
        if attempt:
            deps.sleep(BASE_ZERO_POLL_S)
        if _reads_zero(deps.read_base()):
            consecutive += 1
            if consecutive >= BASE_ZERO_CONSECUTIVE_READS:
                return True
        else:
            consecutive = 0
    return False


def _reads_zero(base: dict) -> bool:
    """One read at rest: base_rest.reads_at_rest, the rule every site that judges the base at rest shares."""
    return reads_at_rest({key: float(base[key]) for key in BASE_VELOCITY_KEYS})


def _measured_height_reason(measured: dict, min_link_height_m: Optional[float] = None) -> Optional[str]:
    """The offline plan only knows commanded joints. The arm sags and never
    lands exactly, so the 'elevated' invariant is re-checked on what was
    actually measured."""
    floor = SCAN_MIN_LINK_HEIGHT_M if min_link_height_m is None else min_link_height_m
    heights = link_heights_m({name: float(measured[name]) for name in SCAN_JOINTS})
    lowest = min(heights, key=heights.get)
    if heights[lowest] < floor:
        return f"measured {lowest} link height {heights[lowest]:.3f} m is below the scan minimum {floor} m: stopping all motion"
    return None


def _arrival_error(target: dict, measured: dict) -> Optional[str]:
    for name in SCAN_JOINTS:
        error = abs(float(measured[name]) - float(target[name]))
        if error > ARRIVAL_TOLERANCE_DEG:
            return f"{name} measured {measured[name]:.2f}, target {target[name]:.2f} (error {error:.2f} > {ARRIVAL_TOLERANCE_DEG} deg)"
    return None


def _measured_joints(pose: dict) -> dict:
    return {key: float(pose[key]) for key in MEASURED_JOINT_KEYS}


def _scan_block(scan_id: str, pose, measured: dict) -> dict:
    azimuth = tool_axis_heading_deg({name: measured[name] for name in SCAN_JOINTS})
    return {
        "scan_id": scan_id,
        "pose_id": pose.pose_id,
        "band": pose.band,
        "sector_index": sector_index_for_azimuth(azimuth),
        "nominal_tool_depression_deg": measured["shoulder_lift"] + measured["elbow_flex"] + measured["wrist_flex"],
        "tool_azimuth_deg": azimuth,
        "frame": FRAME,
        "coverage_basis": COVERAGE_BASIS,
    }


def run_scan_world(
    deps: ScanWorldDeps,
    *,
    stage: int,
    store,
    calibration,
    scan_id: str,
    execute: bool = False,
    approved_plan_digest: Optional[str] = None,
    approved_start_pose: Optional[dict] = None,
    allow_low_entry: bool = False,
    settle_s: float = DEFAULT_SETTLE_S,
    include_front: bool = False,
    gripper_state: str = "unknown",
    cube_held: str = "unknown",
    atlas_annotation: Optional[dict] = None,
    image_orientation: str = DEFAULT_IMAGE_ORIENTATION,
    pose_ids: Optional[list] = None,
    custom_poses: Optional[list] = None,
    held_at: Optional[dict] = None,
    min_link_height_m: Optional[float] = None,
    low_pan_allowance_deg: float = 0.0,
    manipulation_step: bool = False,
) -> ScanWorldResult:
    # custom_poses: ScanPose objects supplied by the caller (e.g. one pose derived from the measured start by a
    # proven Cartesian primitive). They pass through the same pose/transit validation, digest and approval.
    # min_link_height_m: None = the scan minimum. The non-contact pregrasp passes its own lower floor; it is used by the
    # offline pose/transit checks, by the MEASURED height check after every segment, and is bound into the approval digest.
    poses = list(custom_poses) if custom_poses is not None else generate_scan_poses(stage=stage)
    if pose_ids is not None:
        # One operator approval may cover a SUBSET of the stage (first physical runs: one band at a time).
        # The plan digest is computed from the filtered list, so an approval never extends to other poses.
        unknown = sorted(set(pose_ids) - {pose.pose_id for pose in poses})
        poses = [pose for pose in poses if pose.pose_id in pose_ids]
        if unknown or not poses:
            raise ScanRefused(f"unknown or empty pose selection for stage {stage}: {unknown or pose_ids}")
    poses_by_id = {pose.pose_id: pose for pose in poses}
    # manipulation_step: one bounded manipulation move (not a scan entry). The caller owns the gripper / held-object
    # predicates there, so the scan-entry rule below does not apply; everything else in this executor still does.
    if allow_low_entry and not manipulation_step and (gripper_state != "open" or cube_held != "no"):
        raise ScanRefused("low entry sweeps the tool outward near the floor: it is only allowed with the gripper open and nothing held")

    measured_start = deps.read_pose()
    try:
        segments = plan_scan_segments(measured_start, poses, allow_low_entry=allow_low_entry, held_at=held_at, min_link_height_m=min_link_height_m, low_pan_allowance_deg=low_pan_allowance_deg,
                                      elbow_band=not manipulation_step)
    except ValueError as error:
        raise ScanRefused(str(error))
    start_pose = {name: float(measured_start[name]) for name in SCAN_JOINTS}
    result = ScanWorldResult(
        executed=False,
        pose_set_digest=pose_set_digest(poses),
        poses=poses,
        segments=segments,
        plan_digest=scan_plan_digest(poses, start_pose, allow_low_entry, min_link_height_m, low_pan_allowance_deg),
        start_pose=start_pose,
    )
    if not execute:
        return result

    if approved_plan_digest is None or approved_start_pose is None:
        raise ScanRefused("no operator approval: show the operator the plan-only output, then pass its plan digest and start pose")
    if scan_plan_digest(poses, approved_start_pose, allow_low_entry, min_link_height_m, low_pan_allowance_deg) != approved_plan_digest:
        raise ScanRefused("the approved digest does not match this pose set, start pose and low-entry setting; re-plan and ask again")
    drift = {name: abs(start_pose[name] - float(approved_start_pose[name])) for name in SCAN_JOINTS}
    worst = max(drift, key=drift.get)
    if drift[worst] > START_POSE_TOLERANCE_DEG:
        raise ScanRefused(
            f"the arm moved since the approved plan ({worst} differs by {drift[worst]:.2f} deg, tolerance "
            f"{START_POSE_TOLERANCE_DEG} deg): the entry motion would differ from what was approved; re-plan"
        )
    try:
        new_observation_id(deps.now_utc(), "wrist", 0)
    except ValueError as error:
        raise ScanRefused(f"the clock does not give an explicit UTC time, so observations could not be named: {error}")
    if not _base_is_zero(deps):
        raise ScanRefused("base velocity is not confirmed zero; the scan only runs from a stationary base")

    result.executed = True
    coverage = ScanCoverage()
    sequence = 0

    def observe(role: str, pose) -> Optional[str]:
        nonlocal sequence
        sequence += 1
        before = _measured_joints(deps.read_pose())
        captured = deps.capture(role)
        after = _measured_joints(deps.read_pose())
        base_now = deps.read_base()
        base_velocity = {key: float(base_now[key]) for key in BASE_VELOCITY_KEYS}
        base_still = _reads_zero(base_velocity)
        timestamp = deps.now_utc()
        observation_id = new_observation_id(timestamp, role, sequence)
        frame = captured.frame
        illumination = illumination_diagnostics(frame)
        observation = build_observation(
            observation_id=observation_id,
            timestamp_utc=timestamp,
            camera_role=role,
            identity_fingerprint=captured.record.identity_fingerprint,
            image_orientation=image_orientation,
            frame_shape=captured.record.frame_shape,
            raw_frame_path=store.frame_relative_path(observation_id) if frame is not None else None,
            measured_joints_deg=before,
            measured_joints_post_capture_deg=after,
            base_velocity=base_velocity,
            calibration=calibration,
            scan=None if pose is None else _scan_block(scan_id, pose, before),
            gripper_state=gripper_state,
            cube_held=cube_held,
            illumination=illumination,
            lifecycle=captured.record.to_dict(),
            atlas_annotation=atlas_annotation,
        )
        try:
            store.save(observation, frame)
        except ObservationStoreError as error:
            return f"observation could not be stored: {error}"
        result.observation_ids.append(observation_id)
        if pose is not None:
            coverage.mark(
                observation_id=observation_id,
                tool_azimuth_deg=observation["scan"]["tool_azimuth_deg"],
                band=pose.band,
                usable=bool(captured.record.frame_valid) and illumination["usable"] and base_still,
                timestamp_utc=timestamp,
            )
        deps.log("SCAN_OBSERVATION", observation_id=observation_id, role=role, pose_id=None if pose is None else pose.pose_id)
        if not base_still:
            return "base velocity was not zero while an observation was taken: stopping all motion (the view is kept as evidence, not as coverage)"
        return None

    def run_segments() -> Optional[str]:
        for segment in segments:
            if deps.should_abort():
                return "operator abort: no further motion commanded"
            if not _base_is_zero(deps):
                return "base velocity not confirmed zero before an arm segment: stopping all motion"
            deps.log("SCAN_SEGMENT", kind=segment.kind, pose_id=segment.pose_id, duration_s=segment.duration_s)
            measured = deps.move_arm(dict(segment.target), segment.duration_s, deps.should_abort)
            if deps.should_abort():
                return "operator abort during a segment: no further motion commanded"
            reason = _arrival_error(segment.target, measured) or _measured_height_reason(measured, min_link_height_m)
            if reason is None and not _base_is_zero(deps):
                reason = "base velocity not confirmed zero after an arm segment: stopping all motion"
            if reason is None and segment.pose_id is not None:
                deps.sleep(settle_s)
                if min_link_height_m is not None:  # a lowered floor: the arm may settle lower than the read taken right after the last waypoint
                    reason = _measured_height_reason(deps.read_pose(), min_link_height_m)
                reason = reason or observe("wrist", poses_by_id[segment.pose_id])
            if reason is not None:
                return reason
        return observe("front", None) if include_front else None

    try:
        result.abort_reason = run_segments()
    except Exception as error:  # noqa: BLE001 -- after motion started nothing may escape without the abort bookkeeping
        result.abort_reason = f"{type(error).__name__}: {error}: stopping all motion"

    result.coverage = coverage.summary()
    try:
        result.final_measured_pose = _measured_joints(deps.read_pose())
    except Exception:  # noqa: BLE001 -- the robot may be the thing that failed
        result.final_measured_pose = None
    deps.log("SCAN_END", abort_reason=result.abort_reason, observations=len(result.observation_ids))
    return result
