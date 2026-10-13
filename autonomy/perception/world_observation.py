#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""world_observation: the structured record of ONE camera observation, for
the active scan and for the observability atlas.

First invariant: "I know exactly which measured robot state produced this
image." The record holds the arm joints MEASURED (never commanded) immediately
before and immediately after the capture, the largest difference between the
two, and what the two reads show: STATIC_CONFIRMED and MOVED_DURING_CAPTURE
are measured facts, STATIC_UNCONFIRMED means there was no comparable second
read (so neither is claimed), and a camera-only capture with no robot state
says NO_ROBOT_STATE instead of inventing one.

Second invariant: calibration status is always explicit. The status of every
calibration item is embedded in every record, and a metric camera pose
(T_base_camera) can only appear when the calibration inventory supplies the
extrinsics it needs. With no calibration the pose is null and the record
names what is missing. A wrist pose is FK of the pre-capture joints, so it is
also withheld unless the arm was STATIC_CONFIRMED across the capture. No
metric world coordinate is ever derived here.

Both invariants are enforced twice: build_observation() derives the labels,
and validate_observation() re-derives them from the record's OWN joint reads
and calibration block, so a record from any other builder, or from a
regression here, cannot carry a label its own data contradicts.

Privacy: records reference the raw frame by a relative path inside an ignored
local directory and identify the camera by a fingerprint. A record is invalid
if it contains a raw device path anywhere, if its frame path is absolute or
escapes the observation directory, or if its observation_id (which the store
turns into a file name) is anything but the fixed id pattern. Nested blocks
are closed, so there is no unknown key for a serial to ride in; free-text
fields (notes, fingerprint) are NOT scanned for serials beyond the device-path
check.

Validation authority is validate_observation() in this module (no jsonschema
dependency, same convention as mobile_approach.TargetObservation). It returns
reasons and never raises. autonomy/schemas/world_observation.schema.json
documents the same shape, and a test pins every enum, const, pattern,
required list and closed property set in that file to the constants below.
The cross-field rules (listed in the schema description) exist only here. The
schema file itself is never executed: no jsonschema validator runs in the
tests.
"""
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manipulation"))

from calibration_inventory import AVAILABLE_UNVERIFIED, MISSING, REQUIRED_ITEMS, STATUSES, VERIFIED, CalibrationUnavailable  # noqa: E402
from camera_lifecycle import ROLES  # noqa: E402
from cartesian_lift import forward_kinematics  # noqa: E402

SCHEMA_VERSION = "world-observation/1"

MEASURED_JOINT_KEYS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
BASE_VELOCITY_KEYS = ("x.vel", "y.vel", "theta.vel")
JOINT_STABILITY_TOL_DEG = 0.5

ASSOCIATION_STATIC = "STATIC_CONFIRMED"
ASSOCIATION_MOVED = "MOVED_DURING_CAPTURE"
ASSOCIATION_UNCONFIRMED = "STATIC_UNCONFIRMED"
ASSOCIATION_NO_ROBOT_STATE = "NO_ROBOT_STATE"
ASSOCIATIONS = (ASSOCIATION_STATIC, ASSOCIATION_MOVED, ASSOCIATION_UNCONFIRMED, ASSOCIATION_NO_ROBOT_STATE)

SOURCE_MEASURED = "MEASURED_PRESENT_POSITION"
SOURCE_UNAVAILABLE = "UNAVAILABLE"
ROBOT_STATE_SOURCES = (SOURCE_MEASURED, SOURCE_UNAVAILABLE)

POSE_CALIBRATED = "CALIBRATED"
POSE_CALIBRATED_UNVERIFIED = "CALIBRATED_UNVERIFIED"
POSE_UNAVAILABLE = "UNAVAILABLE"
POSE_STATUSES = (POSE_CALIBRATED, POSE_CALIBRATED_UNVERIFIED, POSE_UNAVAILABLE)
POSE_FRAME = "BASE_FRAME"
_POSE_REQUIREMENTS = {
    "wrist": ("wrist_hand_eye", "arm_base_to_base_frame"),
    "front": ("front_to_base_extrinsics",),
}

GRIPPER_STATES = ("open", "closed", "holding", "unknown")
CUBE_HELD = ("yes", "no", "unknown")
LIGHTING_LABELS = ("daylight", "artificial", "mixed", "unknown")
ATLAS_OBJECTS = ("yellow_cube", "collection_pad", "cube_and_pad", "none")
_ATLAS_NUMBERS = ("range_m", "bearing_deg", "pad_yaw_deg")
_ATLAS_TEXT = ("clipping", "occlusion", "staging", "notes")
ATLAS_ANNOTATION_KEYS = ("object", "lighting") + _ATLAS_NUMBERS + _ATLAS_TEXT

SCAN_KEYS = (
    "scan_id",
    "pose_id",
    "band",
    "sector_index",
    "nominal_tool_depression_deg",
    "tool_azimuth_deg",
    "frame",
    "coverage_basis",
)

CAMERA_KEYS = ("role", "identity_fingerprint", "image_orientation", "width_px", "height_px")
ROBOT_STATE_KEYS = (
    "source",
    "measured_joints_deg",
    "measured_joints_post_capture_deg",
    "joint_stability_max_delta_deg",
    "base_velocity",
    "state_image_association",
)
CAMERA_POSE_KEYS = ("status", "frame", "T_base_camera", "missing_calibration", "reason")
CALIBRATION_KEYS = ("version", "items")

TOP_LEVEL_KEYS = (
    "observation_id",
    "schema_version",
    "timestamp_utc",
    "camera",
    "raw_frame_path",
    "robot_state",
    "camera_pose",
    "calibration",
    "scan",
    "gripper_state",
    "cube_held",
    "illumination",
    "lifecycle",
    "atlas_annotation",
    "notes",
)

REASON_ARM_NOT_STATIC = "ARM_NOT_STATIC_DURING_CAPTURE"

# The same strings are the "pattern" keywords of the schema file (a test pins
# them). The id becomes a file name in the observation store, so nothing
# outside this pattern may ever validate.
MAX_SEQUENCE = 9999
OBSERVATION_ID_PATTERN = "^obs-[0-9]{8}T[0-9]{6}Z-(" + "|".join(ROLES) + ")-[0-9]{4}$"
_TIMESTAMP_PREFIX = r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?"
TIMESTAMP_UTC_PATTERN = _TIMESTAMP_PREFIX + r"(Z|\+00:00)$"
_OBSERVATION_ID = re.compile(OBSERVATION_ID_PATTERN)
_TIMESTAMP_UTC = re.compile(TIMESTAMP_UTC_PATTERN)
_TIMESTAMP_ANY_ZONE = re.compile(_TIMESTAMP_PREFIX + r"(Z|[+-][0-9]{2}:[0-9]{2})$")

_RAW_DEVICE = re.compile(r"/dev/")
_TRANSFORM_TOL = 1e-9
_DELTA_TOL_DEG = 1e-9


def _parse_timestamp(text) -> datetime:
    """Offset-aware datetime (whole seconds) for an ISO 8601 timestamp that
    states its timezone; ValueError for anything else, including a naive
    time. One fixed grammar (YYYY-MM-DDTHH:MM:SS[.fraction] then Z or +HH:MM)
    instead of datetime.fromisoformat, whose accepted forms depend on the
    Python version (3.11 widened them), so validity would differ by machine."""
    match = _TIMESTAMP_ANY_ZONE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f"timestamp {text!r} is not ISO 8601 with an explicit timezone (YYYY-MM-DDTHH:MM:SS[.fff] followed by Z or +HH:MM)")
    year, month, day, hour, minute, second = (int(group) for group in match.groups()[:6])
    zone = match.group(8)
    offset = timedelta(0) if zone == "Z" else timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])) * (-1 if zone[0] == "-" else 1)
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone(offset))


def new_observation_id(timestamp_utc: str, camera_role: str, sequence: int) -> str:
    """A sortable id that matches OBSERVATION_ID_PATTERN, or ValueError. The id
    ends in Z, so the timestamp is CONVERTED to UTC: an offset-aware local time
    gives the UTC id, and a naive time is refused because it cannot be placed
    in UTC at all (stamping it Z would be a guess). Fractional seconds are
    dropped. The role and the 0..9999 sequence are checked here because the
    store turns the id into a file name."""
    if not isinstance(camera_role, str) or camera_role not in ROLES:
        raise ValueError(f"unknown camera role {camera_role!r}; expected one of {ROLES}")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or not 0 <= sequence <= MAX_SEQUENCE:
        raise ValueError(f"sequence {sequence!r} must be an integer in 0..{MAX_SEQUENCE}")
    try:
        utc = _parse_timestamp(timestamp_utc).astimezone(timezone.utc)
    except OverflowError as error:
        raise ValueError(f"timestamp {timestamp_utc!r} cannot be expressed in UTC: {error}") from error
    compact = f"{utc.year:04d}{utc.month:02d}{utc.day:02d}T{utc.hour:02d}{utc.minute:02d}{utc.second:02d}Z"
    return f"obs-{compact}-{camera_role}-{sequence:04d}"


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_transform(value) -> bool:
    """A finite 4x4 homogeneous transform (bottom row 0,0,0,1) as nested
    sequences. Never raises, whatever the value is."""
    rows = value.tolist() if isinstance(value, np.ndarray) else value
    if not isinstance(rows, (list, tuple)) or len(rows) != 4:
        return False
    if not all(isinstance(row, (list, tuple)) and len(row) == 4 and all(_finite(v) for v in row) for row in rows):
        return False
    return all(abs(v - expected) <= _TRANSFORM_TOL for v, expected in zip(rows[3], (0.0, 0.0, 0.0, 1.0)))


def _joints_ok(joints) -> bool:
    return isinstance(joints, dict) and set(joints) == set(MEASURED_JOINT_KEYS) and all(_finite(v) for v in joints.values())


def resolve_camera_pose(camera_role: str, measured_joints_deg, inventory, *, association: str = ASSOCIATION_STATIC) -> dict:
    """Camera pose in BASE_FRAME, or an explicit statement of why there is
    none. wrist: arm_base_to_base_frame @ FK(measured joints) @ hand-eye.
    front: the fixed extrinsic.

    The wrist pose is FK of the joints read BEFORE the capture, so it only
    describes the image if the arm was shown static across the capture. Any
    other `association` (moved, never re-read, no robot state) gives
    POSE_UNAVAILABLE with reason ARM_NOT_STATIC_DURING_CAPTURE: the trust label
    CALIBRATED never sits on a pose the same record contradicts. The front
    camera is fixed to the base and does not depend on the arm. The default is
    for callers that resolve a pose outside a capture; build_observation always
    passes the association it computed.

    Never raises on bad DATA: the inventory does not shape-check its values
    (CalibrationItem.value is 'Any'), and this runs in the middle of a scan,
    after arm motion, where an exception would skip the abort bookkeeping. A
    calibration value that is not a finite 4x4 homogeneous transform, or
    measured joints that are not finite values for MEASURED_JOINT_KEYS, give
    POSE_UNAVAILABLE with a reason naming the problem."""
    unavailable = {"status": POSE_UNAVAILABLE, "frame": POSE_FRAME, "T_base_camera": None, "missing_calibration": [], "reason": None}
    names = _POSE_REQUIREMENTS.get(camera_role)
    if names is None:
        return {**unavailable, "reason": f"unknown camera role {camera_role!r}"}
    try:
        values = inventory.require(*names, allow_unverified=True)
    except CalibrationUnavailable as error:
        return {**unavailable, "missing_calibration": sorted(error.unavailable), "reason": "CALIBRATION_MISSING"}
    malformed = sorted(name for name in names if not _is_transform(values[name]))
    if malformed:
        return {**unavailable, "reason": "CALIBRATION_VALUE_NOT_A_4X4_TRANSFORM: " + ", ".join(malformed)}

    if camera_role == "front":
        transform = np.array(values["front_to_base_extrinsics"], dtype=float)
    else:
        if measured_joints_deg is None:
            return {**unavailable, "reason": "NO_MEASURED_JOINTS"}
        if not _joints_ok(measured_joints_deg):
            return {**unavailable, "reason": "MEASURED_JOINTS_MALFORMED"}
        if association != ASSOCIATION_STATIC:
            return {**unavailable, "reason": REASON_ARM_NOT_STATIC}
        transform = (
            np.array(values["arm_base_to_base_frame"], dtype=float)
            @ forward_kinematics(measured_joints_deg)
            @ np.array(values["wrist_hand_eye"], dtype=float)
        )

    verified = all(inventory.status(name) == VERIFIED for name in names)
    return {
        "status": POSE_CALIBRATED if verified else POSE_CALIBRATED_UNVERIFIED,
        "frame": POSE_FRAME,
        "T_base_camera": transform.tolist(),
        "missing_calibration": [],
        "reason": None,
    }


def _association(measured, measured_post) -> tuple:
    """(state_image_association, largest joint difference in degrees) for a
    pre-capture and a post-capture joint read. The builder and the validator
    both use this, so a stored association can never disagree with the joint
    reads stored beside it. With no comparable post-capture read the arm was
    not shown static and no motion was measured either, which is
    STATIC_UNCONFIRMED: MOVED_DURING_CAPTURE is only ever a measured fact."""
    if measured is None:
        return ASSOCIATION_NO_ROBOT_STATE, None
    if measured_post is None or set(measured_post) != set(measured):
        return ASSOCIATION_UNCONFIRMED, None
    delta = max(abs(float(measured_post[k]) - float(measured[k])) for k in measured)
    return (ASSOCIATION_STATIC if delta <= JOINT_STABILITY_TOL_DEG else ASSOCIATION_MOVED), delta


def _robot_state(measured, measured_post, base_velocity) -> dict:
    association, delta = _association(measured, measured_post)
    if measured is None:
        return {
            "source": SOURCE_UNAVAILABLE,
            "measured_joints_deg": None,
            "measured_joints_post_capture_deg": None,
            "joint_stability_max_delta_deg": None,
            "base_velocity": None,
            "state_image_association": association,
        }
    return {
        "source": SOURCE_MEASURED,
        "measured_joints_deg": dict(measured),
        "measured_joints_post_capture_deg": None if measured_post is None else dict(measured_post),
        "joint_stability_max_delta_deg": delta,
        "base_velocity": None if base_velocity is None else dict(base_velocity),
        "state_image_association": association,
    }


def build_observation(
    *,
    observation_id: str,
    timestamp_utc: str,
    camera_role: str,
    identity_fingerprint: str,
    image_orientation: str,
    frame_shape,
    raw_frame_path,
    measured_joints_deg,
    measured_joints_post_capture_deg,
    base_velocity,
    calibration,
    scan,
    gripper_state: str,
    cube_held: str,
    illumination: dict,
    lifecycle: dict,
    atlas_annotation=None,
    notes: str = "",
) -> dict:
    """Assemble one record. Does not validate: call validate_observation()
    before persisting, so an invalid record is reported, never half-written.
    The robot state is derived first because its association decides whether a
    wrist pose may be stated at all. May raise on programmer misuse (arguments
    of the wrong type); bad calibration DATA gives a record with no pose."""
    height, width = (frame_shape[0], frame_shape[1]) if frame_shape else (None, None)
    robot_state = _robot_state(measured_joints_deg, measured_joints_post_capture_deg, base_velocity)
    return {
        "observation_id": observation_id,
        "schema_version": SCHEMA_VERSION,
        "timestamp_utc": timestamp_utc,
        "camera": {
            "role": camera_role,
            "identity_fingerprint": identity_fingerprint,
            "image_orientation": image_orientation,
            "width_px": width,
            "height_px": height,
        },
        "raw_frame_path": raw_frame_path,
        "robot_state": robot_state,
        "camera_pose": resolve_camera_pose(camera_role, measured_joints_deg, calibration, association=robot_state["state_image_association"]),
        "calibration": calibration.to_status_summary(),
        "scan": None if scan is None else dict(scan),
        "gripper_state": gripper_state,
        "cube_held": cube_held,
        "illumination": dict(illumination),
        "lifecycle": dict(lifecycle),
        "atlas_annotation": None if atlas_annotation is None else dict(atlas_annotation),
        "notes": notes,
    }


def _key_reasons(name: str, block: dict, keys) -> list:
    """Nested blocks are closed like the top level: an unknown key is where a
    metric coordinate, a commanded joint set or a device serial would hide."""
    reasons = []
    missing = [key for key in keys if key not in block]
    unknown = sorted((key for key in block if key not in keys), key=repr)
    if missing:
        reasons.append(f"{name} is missing field(s) {missing}")
    if unknown:
        reasons.append(f"{name} has unknown field(s) {unknown}")
    return reasons


def _identity_reasons(observation: dict, camera) -> list:
    reasons = []
    observation_id, timestamp = observation["observation_id"], observation["timestamp_utc"]
    match = _OBSERVATION_ID.fullmatch(observation_id) if isinstance(observation_id, str) else None
    if match is None:
        reasons.append(f"observation_id {observation_id!r} must match {OBSERVATION_ID_PATTERN} (it becomes a file name)")
    elif camera is not None and camera.get("role") != match.group(1):
        reasons.append(f"camera.role {camera.get('role')!r} must be the role named in observation_id {observation_id!r}")
    if not isinstance(timestamp, str) or _TIMESTAMP_UTC.fullmatch(timestamp) is None:
        reasons.append(f"timestamp_utc {timestamp!r} must be ISO 8601 with an explicit UTC designator (Z or +00:00): {TIMESTAMP_UTC_PATTERN}")
    else:
        try:
            _parse_timestamp(timestamp)
        except ValueError as error:
            reasons.append(f"timestamp_utc {timestamp!r} is not a real time: {error}")
    return reasons


def _camera_reasons(camera: dict) -> list:
    reasons = _key_reasons("camera", camera, CAMERA_KEYS)
    if camera.get("role") not in ROLES:
        reasons.append(f"camera.role {camera.get('role')!r} is not one of {ROLES}")
    for key in ("identity_fingerprint", "image_orientation"):
        if key in camera and not isinstance(camera[key], str):
            reasons.append(f"camera.{key} must be a string")
    for key in ("width_px", "height_px"):
        if camera.get(key) is not None and not _integer(camera[key]):
            reasons.append(f"camera.{key} must be an integer or null")
    return reasons


def _pose_reasons(pose: dict) -> list:
    reasons = _key_reasons("camera_pose", pose, CAMERA_POSE_KEYS)
    if "frame" in pose and pose["frame"] != POSE_FRAME:
        reasons.append(f"camera_pose.frame must be {POSE_FRAME!r}")
    if pose.get("reason") is not None and not isinstance(pose["reason"], str):
        reasons.append("camera_pose.reason must be a string or null")
    status, transform = pose.get("status"), pose.get("T_base_camera")
    if status not in POSE_STATUSES:
        reasons.append(f"camera_pose.status {status!r} is not one of {POSE_STATUSES}")
    calibrated = status in (POSE_CALIBRATED, POSE_CALIBRATED_UNVERIFIED)
    if calibrated and transform is None:
        reasons.append("camera_pose.T_base_camera is required when the status is calibrated")
    if not calibrated and transform is not None:
        reasons.append("camera_pose.T_base_camera must be null unless calibration supplies it (no invented metric pose)")
    if transform is not None and not _is_transform(transform):
        reasons.append("camera_pose.T_base_camera must be a finite 4x4 homogeneous matrix (bottom row 0, 0, 0, 1)")
    listed = pose.get("missing_calibration")
    if not isinstance(listed, list) or not all(isinstance(name, str) for name in listed):
        reasons.append("camera_pose.missing_calibration must be a list of calibration item names")
    return reasons


def _robot_state_reasons(state: dict) -> list:
    reasons = _key_reasons("robot_state", state, ROBOT_STATE_KEYS)
    source, association = state.get("source"), state.get("state_image_association")
    if source not in ROBOT_STATE_SOURCES:
        reasons.append(f"robot_state.source {source!r} is not one of {ROBOT_STATE_SOURCES} (measured, never commanded)")
    if association not in ASSOCIATIONS:
        reasons.append(f"robot_state.state_image_association {association!r} is not one of {ASSOCIATIONS}")
    measured, measured_post = state.get("measured_joints_deg"), state.get("measured_joints_post_capture_deg")
    joints_ok = True
    for key, joints in (("measured_joints_deg", measured), ("measured_joints_post_capture_deg", measured_post)):
        if joints is not None and not _joints_ok(joints):
            joints_ok = False
            reasons.append(f"robot_state.{key} must hold finite values for exactly {MEASURED_JOINT_KEYS}")
    base = state.get("base_velocity")
    if base is not None and not (isinstance(base, dict) and set(base) == set(BASE_VELOCITY_KEYS) and all(_finite(v) for v in base.values())):
        reasons.append(f"robot_state.base_velocity must hold finite values for exactly {BASE_VELOCITY_KEYS}")
    delta = state.get("joint_stability_max_delta_deg")
    if delta is not None and not (_finite(delta) and delta >= 0):
        reasons.append("robot_state.joint_stability_max_delta_deg must be a finite number >= 0 or null")
        delta = None
    if not joints_ok:
        return reasons

    # The labels must be the ones the stored joint reads actually support.
    if measured is None and measured_post is not None:
        reasons.append("robot_state.measured_joints_post_capture_deg must be null when there is no pre-capture read")
    expected_source = SOURCE_UNAVAILABLE if measured is None else SOURCE_MEASURED
    if source in ROBOT_STATE_SOURCES and source != expected_source:
        reasons.append(f"robot_state.source must be {expected_source!r} when measured_joints_deg is {'null' if measured is None else 'present'}")
    expected_association, expected_delta = _association(measured, measured_post)
    if association in ASSOCIATIONS and association != expected_association:
        reasons.append(f"robot_state.state_image_association {association!r} contradicts the stored joint reads, which give {expected_association!r}")
    if (delta is None) != (expected_delta is None) or (delta is not None and abs(delta - expected_delta) > _DELTA_TOL_DEG):
        stored = state.get("joint_stability_max_delta_deg")
        reasons.append(f"robot_state.joint_stability_max_delta_deg {stored!r} contradicts the stored joint reads, which give {expected_delta!r}")
    return reasons


def _calibration_reasons(calibration: dict) -> list:
    reasons = _key_reasons("calibration", calibration, CALIBRATION_KEYS)
    if not isinstance(calibration.get("version"), str):
        reasons.append("calibration.version must be a string")
    items = calibration.get("items")
    if not isinstance(items, dict):
        return reasons + ["calibration.items must be an object"]
    if set(items) != set(REQUIRED_ITEMS):
        reasons.append(f"calibration.items must hold exactly {REQUIRED_ITEMS}")
    bad = sorted(f"{name}={status!r}" for name, status in items.items() if status not in STATUSES)
    if bad:
        reasons.append(f"calibration.items statuses must be one of {STATUSES} (never collapsed), got {bad}")
    return reasons


def _pose_calibration_reasons(role: str, pose: dict, items: dict) -> list:
    """The pose label may claim no more than the record's OWN calibration
    block supports. Without this a record with every item MISSING could carry
    a CALIBRATED metric pose, or CALIBRATED could sit on an unverified item."""
    reasons = []
    names = _POSE_REQUIREMENTS[role]
    missing = sorted(name for name in names if items[name] == MISSING)
    unverified = sorted(name for name in names if items[name] == AVAILABLE_UNVERIFIED)
    status = pose.get("status")
    if status in (POSE_CALIBRATED, POSE_CALIBRATED_UNVERIFIED):
        supported = POSE_CALIBRATED_UNVERIFIED if unverified else POSE_CALIBRATED
        if missing:
            reasons.append(f"camera_pose.status {status!r} is not supported: calibration.items reports {missing} MISSING, so the pose must be {POSE_UNAVAILABLE!r}")
        elif status != supported:
            basis = f"{unverified} AVAILABLE_UNVERIFIED" if unverified else f"every one of {list(names)} VERIFIED"
            reasons.append(f"camera_pose.status {status!r} must be {supported!r}: calibration.items reports {basis}")
    listed = pose.get("missing_calibration")
    if isinstance(listed, list) and all(isinstance(name, str) for name in listed) and sorted(listed) != missing:
        reasons.append(f"camera_pose.missing_calibration must name exactly the required items calibration.items reports MISSING: {missing}")
    return reasons


_SCAN_TEXT = ("scan_id", "pose_id", "band", "frame", "coverage_basis")
_SCAN_NUMBERS = ("nominal_tool_depression_deg", "tool_azimuth_deg")


def _scan_reasons(scan: dict) -> list:
    if set(scan) != set(SCAN_KEYS):
        return [f"scan must hold exactly {SCAN_KEYS}"]
    reasons = [f"scan.{key} must be a string" for key in _SCAN_TEXT if not isinstance(scan[key], str)]
    reasons += [f"scan.{key} must be a finite number" for key in _SCAN_NUMBERS if not _finite(scan[key])]
    if not _integer(scan["sector_index"]):
        reasons.append("scan.sector_index must be an integer")
    return reasons


def _atlas_reasons(annotation: dict) -> list:
    reasons = []
    unknown = set(annotation) - set(ATLAS_ANNOTATION_KEYS)
    if unknown:
        reasons.append(f"atlas_annotation has unknown field(s) {sorted(unknown, key=repr)}")
    if "object" in annotation and annotation["object"] not in ATLAS_OBJECTS:
        reasons.append(f"atlas_annotation.object {annotation['object']!r} is not one of {ATLAS_OBJECTS}")
    if "lighting" in annotation and annotation["lighting"] not in LIGHTING_LABELS:
        reasons.append(f"atlas_annotation.lighting {annotation['lighting']!r} is not one of {LIGHTING_LABELS}")
    for key in _ATLAS_NUMBERS:
        if key in annotation and not _finite(annotation[key]):
            reasons.append(f"atlas_annotation.{key} must be a finite number")
    for key in _ATLAS_TEXT:
        if key in annotation and not isinstance(annotation[key], str):
            reasons.append(f"atlas_annotation.{key} must be a string")
    return reasons


def _frame_path_reasons(path, frame_valid: bool) -> list:
    if path is None:
        return ["raw_frame_path is required when the capture produced a valid frame"] if frame_valid else []
    if not isinstance(path, str) or os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
        return [f"raw_frame_path must be a relative path inside the observation directory, got {path!r}"]
    return []


_OBJECT_BLOCKS = ("camera", "robot_state", "camera_pose", "calibration", "illumination", "lifecycle")
_OPTIONAL_OBJECT_BLOCKS = ("scan", "atlas_annotation")


def _collect_reasons(observation, reasons: list) -> None:
    if not isinstance(observation, dict):
        reasons.append(f"record must be an object, got {type(observation).__name__}")
        return
    missing = [key for key in TOP_LEVEL_KEYS if key not in observation]
    unknown = [key for key in observation if key not in TOP_LEVEL_KEYS]
    reasons += [f"missing field {key!r}" for key in missing]
    reasons += [f"unknown field {key!r}" for key in unknown]
    if missing:
        return

    # Every nested block is type-checked BEFORE it is dereferenced: a block of
    # the wrong type is a reason, and the checks that need it are skipped.
    blocks = {}
    for key in _OBJECT_BLOCKS + _OPTIONAL_OBJECT_BLOCKS:
        value = observation[key]
        if isinstance(value, dict):
            blocks[key] = value
        elif value is not None or key in _OBJECT_BLOCKS:
            allowed = "an object" if key in _OBJECT_BLOCKS else "an object or null"
            reasons.append(f"{key} must be {allowed}, got {type(value).__name__}")

    if observation["schema_version"] != SCHEMA_VERSION:
        reasons.append(f"schema_version must be {SCHEMA_VERSION!r}")
    reasons += _identity_reasons(observation, blocks.get("camera"))
    if "camera" in blocks:
        reasons += _camera_reasons(blocks["camera"])
    if observation["gripper_state"] not in GRIPPER_STATES:
        reasons.append(f"gripper_state {observation['gripper_state']!r} is not one of {GRIPPER_STATES}")
    if observation["cube_held"] not in CUBE_HELD:
        reasons.append(f"cube_held {observation['cube_held']!r} is not one of {CUBE_HELD}")
    if (observation["cube_held"] == "yes") != (observation["gripper_state"] == "holding"):
        reasons.append("cube_held 'yes' and gripper_state 'holding' must be stated together")

    if "robot_state" in blocks:
        reasons += _robot_state_reasons(blocks["robot_state"])
    if "camera_pose" in blocks:
        reasons += _pose_reasons(blocks["camera_pose"])
    if all(key in blocks for key in ("camera", "robot_state", "camera_pose")):
        calibrated = blocks["camera_pose"].get("status") in (POSE_CALIBRATED, POSE_CALIBRATED_UNVERIFIED)
        if blocks["camera"].get("role") == "wrist" and calibrated and blocks["robot_state"].get("state_image_association") != ASSOCIATION_STATIC:
            reasons.append(f"camera_pose: a calibrated wrist pose requires state_image_association {ASSOCIATION_STATIC} ({REASON_ARM_NOT_STATIC})")
    if "calibration" in blocks:
        calibration_reasons = _calibration_reasons(blocks["calibration"])
        reasons += calibration_reasons
        role = blocks.get("camera", {}).get("role")
        if not calibration_reasons and "camera_pose" in blocks and isinstance(role, str) and role in _POSE_REQUIREMENTS:
            reasons += _pose_calibration_reasons(role, blocks["camera_pose"], blocks["calibration"]["items"])
    if "lifecycle" in blocks:
        reasons += _frame_path_reasons(observation["raw_frame_path"], bool(blocks["lifecycle"].get("frame_valid")))

    if "scan" in blocks:
        reasons += _scan_reasons(blocks["scan"])
    if "atlas_annotation" in blocks:
        reasons += _atlas_reasons(blocks["atlas_annotation"])
    if not isinstance(observation["notes"], str):
        reasons.append("notes must be a string")

    # allow_nan=False: json.dump would otherwise write a bare NaN or Infinity
    # token, which is not JSON, into a record that is kept as evidence.
    try:
        text = json.dumps(observation, allow_nan=False)
    except (TypeError, ValueError) as error:
        reasons.append(f"record is not strict JSON: {error}")
    else:
        if _RAW_DEVICE.search(text):
            reasons.append("record contains a raw device path; identify cameras by fingerprint only")


def validate_observation(observation) -> list:
    """Reasons the record is invalid; empty means valid. Never mutates and
    NEVER RAISES, whatever it is handed: the observation store uses this as
    its persistence gate in the middle of a scan, and a raise there would skip
    the scan's abort bookkeeping. Every nested block is type-checked before it
    is read, and as a last resort any failure nobody foresaw is itself
    reported as a reason (so the record is refused, never half-trusted)."""
    reasons = []
    try:
        _collect_reasons(observation, reasons)
    except Exception as error:  # noqa: BLE001 - the contract is "return reasons", for every input
        reasons.append(f"record could not be validated: {type(error).__name__}: {error}")
    return reasons
