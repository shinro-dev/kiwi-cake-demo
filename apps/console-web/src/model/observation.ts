// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The sanitized observation DTO.
//
// This is NOT a contract record and there is no generated type for it. The runtime serves an allowlisted projection of
// the controller's stored observation: `RECORD_ALLOW` and `PERCEPTION_ALLOW` in
// `packages/demo-runtime/demo_runtime/read_models.py`. Fields outside the allowlist never arrive, and the runtime may
// change its allowlist, so everything here is read defensively: a key that is absent or the wrong type becomes null and
// the layer that needed it is reported unavailable with a reason. Nothing is guessed and nothing is filled in.

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function child(value: unknown, key: string): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const inner = value[key];
  return isRecord(inner) ? inner : null;
}

/** A tuple of exactly `length` finite numbers, or null. */
function numbers(value: unknown, length: number): number[] | null {
  if (!Array.isArray(value) || value.length !== length) return null;
  const out: number[] = [];
  for (const item of value) {
    const parsed = num(item);
    if (parsed === null) return null;
    out.push(parsed);
  }
  return out;
}

/** A list of [x, y] pairs, or null. */
function points(value: unknown, min: number): [number, number][] | null {
  if (!Array.isArray(value) || value.length < min) return null;
  const out: [number, number][] = [];
  for (const item of value) {
    const pair = numbers(item, 2);
    if (pair === null) return null;
    out.push([pair[0] as number, pair[1] as number]);
  }
  return out;
}

function strings(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const out: string[] = [];
  for (const item of value) {
    const parsed = text(item);
    if (parsed === null) return null;
    out.push(parsed);
  }
  return out;
}

/** A row-major 3x3 matrix given as three rows of three, or null. */
export function matrix3(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length !== 3) return null;
  const out: number[] = [];
  for (const row of value) {
    const parsed = numbers(row, 3);
    if (parsed === null) return null;
    out.push(...parsed);
  }
  return out;
}

export interface ObservationRecordDto {
  observation_id: string | null;
  timestamp_utc: string | null;
  gripper_state: string | null;
  cube_held: boolean | null;
  camera: { role: string | null; image_orientation: string | null; width_px: number | null; height_px: number | null } | null;
  robot_state: {
    source: string | null;
    measured_joints_deg: Record<string, number | null> | null;
    joint_stability_max_delta_deg: number | null;
  } | null;
  camera_pose: { status: string | null; frame: string | null; reason: string | null; missing_calibration: boolean | null } | null;
  scan: { pose_id: string | null; band: string | null; sector_index: number | null; nominal_tool_depression_deg: number | null; tool_azimuth_deg: number | null } | null;
}

export interface PerceptionDto {
  pad_note: string | null;
  pad: {
    detected: boolean | null;
    confidence: number | null;
    visible_fraction: number | null;
    clipped_sides: string[] | null;
    orientation_resolved: boolean | null;
    boundary_rms_px: number | null;
    camera_height_m: number | null;
    view_elevation_deg: number | null;
    corners_px: [number, number][] | null;
  } | null;
  pad_by_template: { pad_identity: string | null; landmark_inliers: number | null; pad_frame_valid: boolean | null; reason: string | null } | null;
  cube: {
    detected: boolean | null;
    confidence: number | null;
    clipped: boolean | null;
    box_px: [number, number, number, number] | null;
    contact_px: [number, number] | null;
    mask_polygon_px: [number, number][] | null;
  } | null;
  cube_by_template: { cube_center_pad_m: [number, number] | null; inside_pad_outline: boolean | null } | null;
  /** Only when a runtime chooses to serve it. Not in the allowlist today; a homography is derived from the corners. */
  homography_pad_to_image: number[] | null;
  /** The controller's grasp reference window in image pixels. Not in the allowlist today. */
  jaw_window_px: [number, number, number, number] | null;
  /** Pad landmark points in image pixels. Not in the allowlist today; `landmark_inliers` is a count, not points. */
  landmarks_px: [number, number][] | null;
}

export function parseObservationRecord(value: unknown): ObservationRecordDto {
  const camera = child(value, 'camera');
  const robot = child(value, 'robot_state');
  const pose = child(value, 'camera_pose');
  const scan = child(value, 'scan');
  const joints = robot !== null ? child(robot, 'measured_joints_deg') : null;
  return {
    observation_id: isRecord(value) ? text(value['observation_id']) : null,
    timestamp_utc: isRecord(value) ? text(value['timestamp_utc']) : null,
    gripper_state: isRecord(value) ? text(value['gripper_state']) : null,
    cube_held: isRecord(value) ? bool(value['cube_held']) : null,
    camera:
      camera === null
        ? null
        : {
            role: text(camera['role']),
            image_orientation: text(camera['image_orientation']),
            width_px: num(camera['width_px']),
            height_px: num(camera['height_px']),
          },
    robot_state:
      robot === null
        ? null
        : {
            source: text(robot['source']),
            measured_joints_deg:
              joints === null ? null : Object.fromEntries(Object.entries(joints).map(([key, item]) => [key, num(item)])),
            joint_stability_max_delta_deg: num(robot['joint_stability_max_delta_deg']),
          },
    camera_pose:
      pose === null
        ? null
        : {
            status: text(pose['status']),
            frame: text(pose['frame']),
            reason: text(pose['reason']),
            missing_calibration: bool(pose['missing_calibration']),
          },
    scan:
      scan === null
        ? null
        : {
            pose_id: text(scan['pose_id']),
            band: text(scan['band']),
            sector_index: num(scan['sector_index']),
            nominal_tool_depression_deg: num(scan['nominal_tool_depression_deg']),
            tool_azimuth_deg: num(scan['tool_azimuth_deg']),
          },
  };
}

export function parsePerception(value: unknown): PerceptionDto {
  const pad = child(value, 'pad');
  const template = child(value, 'pad_by_template');
  const cube = child(value, 'cube');
  const cubeTemplate = child(value, 'cube_by_template');
  const box = cube !== null ? numbers(cube['box_px'], 4) ?? numbers(cube['bbox_xyxy'], 4) : null;
  const contact = cube !== null ? numbers(cube['contact_px'], 2) : null;
  const centre = cubeTemplate !== null ? numbers(cubeTemplate['cube_center_pad_m'], 2) : null;
  const window = isRecord(value) ? numbers(value['jaw_window_px'], 4) : null;
  return {
    pad_note: isRecord(value) ? text(value['pad_note']) : null,
    pad:
      pad === null
        ? null
        : {
            detected: bool(pad['detected']),
            confidence: num(pad['confidence']),
            visible_fraction: num(pad['visible_fraction']),
            clipped_sides: strings(pad['clipped_sides']),
            orientation_resolved: bool(pad['orientation_resolved']),
            boundary_rms_px: num(pad['boundary_rms_px']),
            camera_height_m: num(pad['camera_height_m']),
            view_elevation_deg: num(pad['view_elevation_deg']),
            corners_px: points(pad['corners_px'], 4),
          },
    pad_by_template:
      template === null
        ? null
        : {
            pad_identity: text(template['pad_identity']),
            landmark_inliers: num(template['landmark_inliers']),
            pad_frame_valid: bool(template['pad_frame_valid']),
            reason: text(template['reason']),
          },
    cube:
      cube === null
        ? null
        : {
            detected: bool(cube['detected']),
            confidence: num(cube['confidence']),
            clipped: bool(cube['clipped']),
            box_px: box === null ? null : [box[0] as number, box[1] as number, box[2] as number, box[3] as number],
            contact_px: contact === null ? null : [contact[0] as number, contact[1] as number],
            mask_polygon_px: points(cube['mask_polygon_px'], 3),
          },
    cube_by_template:
      cubeTemplate === null
        ? null
        : {
            cube_center_pad_m: centre === null ? null : [centre[0] as number, centre[1] as number],
            inside_pad_outline: bool(cubeTemplate['inside_pad_outline']),
          },
    homography_pad_to_image: isRecord(value) ? matrix3(value['homography_pad_to_image']) : null,
    jaw_window_px: window === null ? null : [window[0] as number, window[1] as number, window[2] as number, window[3] as number],
    landmarks_px: isRecord(value) ? points(value['landmarks_px'], 1) : null,
  };
}
