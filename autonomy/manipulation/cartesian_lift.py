#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""cartesian_lift: forward/inverse kinematics for the SO-101 arm (the LeKiwi
follower's manipulator), used to compute CARTESIAN_CLEARANCE_LIFT -- a small,
approximately vertical end-effector motion from a known-good floor grasp
pose, derived from the arm's actual link geometry instead of another guessed
joint delta. See .local-dev/confirmed_grasp_handoff.md's "NEXT-SESSION
TECHNICAL OBJECTIVE" and "Mobility handoff pose INVALIDATED" sections for why:
three prior guessed (lift-, wrist_flex+) deltas from a different pan/elbow
region dragged the cube horizontally instead of lifting it -- a joint-space
guess does not reliably correspond to a Cartesian direction.

Link geometry source: the joint origins/axes below are transcribed directly
from the SO-101 URDF so101_new_calib.urdf (robot name
"so101_new_calib", the same URDF lerobot's own placo-based
lerobot.model.kinematics.RobotKinematics loads for this exact arm, target
frame "gripper_frame_link"). They are embedded as constants rather than
parsed from the URDF file at runtime so this module has zero filesystem
dependency and stays inside this project's existing hardware-free pure-module
boundary (action_adapter.py, pose_preflight.py, canonical_handoff.py -- none
import lerobot, torch, or touch a device).

Why not just use lerobot.model.kinematics.RobotKinematics directly: it works,
but requires the optional `placo` extra (pulls in Pinocchio + cmeel binary
wheels) which is not installed in this environment, and importing lerobot at
all would cross this project's established "never imports lerobot" line for
manipulation math. This module reimplements the same URDF-chain forward
kinematics placo would compute (matching its exact convention: joint values
are motor-reading degrees, deg2rad'd and applied directly as each revolute
joint's rotation about its own local Z axis) with plain numpy, and inverse
kinematics with a small numeric damped-least-squares solve instead of
placo's general nonlinear solver -- appropriate here because only 3 joints
are ever unlocked (see below), so the problem is a small, well-conditioned,
one-shot solve, not a real-time controller.

Held joints: shoulder_pan and wrist_roll are never modified by
solve_cartesian_z_lift.
  - shoulder_pan's axis is parallel to base_link's Z axis (see
    _JOINT_GEOMETRY / the by-hand check in this module's tests): rotating it
    sweeps the end effector sideways in proportion to reach and does not
    itself change height, so holding it fixed is what "minimize X/Y
    displacement" *means* for this arm, not an arbitrary restriction.
  - wrist_roll is this project's standing invariant: runtime-hold only,
    never programmatically forced to any value (see canonical_handoff.py's
    docstring and every trial in confirmed_grasp_handoff.md). It is left
    exactly as read in the input pose.
That leaves shoulder_lift/elbow_flex/wrist_flex (ACTIVE_LIFT_JOINTS) to
solve a 3-unknown, position-dominant task -- soft orientation weighting
mirrors lerobot's own IK default (position_weight=1.0, small nonzero
orientation_weight) so the tool orientation drifts as little as the 3-DOF
solution allows, without fighting the position target for it.

IMPORTANT MODELING ASSUMPTION this module cannot verify from the URDF alone:
base_link's local Z axis is assumed to be world-vertical, i.e. the arm is
mounted upright on the LeKiwi chassis in its designed orientation. Everything
here is otherwise pure, relative Cartesian geometry -- it has no notion of
the floor plane, so a computed delta_z_m of e.g. 0.02 is a claim about
end-effector displacement relative to its own current pose, not a claim about
absolute floor clearance. Confirming the requested lift actually clears the
floor still requires physical verification (operator or a diagnostic camera),
exactly as confirmed_grasp_handoff.md's SAFE RESTART POINT step D already
specifies -- this module computes the *how far to move*, not the *did it
clear* judgment.
"""

import numpy as np

JOINT_ORDER = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")

ACTIVE_LIFT_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex")
FIXED_LIFT_JOINTS = ("shoulder_pan", "wrist_roll")

# joint_name -> (origin_xyz (m), origin_rpy (rad)), transcribed from each
# <joint>'s <origin> in so101_new_calib.urdf. Every one of these joints
# rotates about "0 0 1" in its OWN local frame (post-origin-rpy), matching
# forward_kinematics's use of a plain Z-axis rotation after each origin
# transform.
_JOINT_GEOMETRY = {
    "shoulder_pan": ((0.0388353, -8.97657e-09, 0.0624), (3.14159, 4.18253e-17, -3.14159)),
    "shoulder_lift": ((-0.0303992, -0.0182778, -0.0542), (-1.5708, -1.5708, 0.0)),
    "elbow_flex": ((-0.11257, -0.028, 1.73763e-16), (-3.63608e-16, 8.74301e-16, 1.5708)),
    "wrist_flex": ((-0.1349, 0.0052, 3.62355e-17), (4.02456e-15, 8.67362e-16, -1.5708)),
    "wrist_roll": ((5.55112e-17, -0.0611, 0.0181), (1.5708, 0.0486795, 3.14159)),
}

# gripper_frame_joint: fixed joint from gripper_link to gripper_frame_link
# (the end-effector frame lerobot's own kinematics targets by default).
_GRIPPER_FRAME_ORIGIN_XYZ = (-0.0079, -0.000218121, -0.0981274)
_GRIPPER_FRAME_ORIGIN_RPY = (0.0, 3.14159, 0.0)


def _rotation_from_rpy(rpy) -> np.ndarray:
    """URDF fixed-axis convention: R = Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    roll, pitch, yaw = rpy
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _homogeneous(xyz, rotation: np.ndarray) -> np.ndarray:
    t = np.eye(4)
    t[:3, :3] = rotation
    t[:3, 3] = xyz
    return t


def _joint_transform(origin_xyz, origin_rpy, angle_rad: float) -> np.ndarray:
    """origin transform (fixed) composed with this joint's own Z rotation."""
    origin = _homogeneous(origin_xyz, _rotation_from_rpy(origin_rpy))
    cz, sz = np.cos(angle_rad), np.sin(angle_rad)
    rot_z = np.array([[cz, -sz, 0, 0], [sz, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    return origin @ rot_z


_GRIPPER_FRAME_TRANSFORM = _homogeneous(_GRIPPER_FRAME_ORIGIN_XYZ, _rotation_from_rpy(_GRIPPER_FRAME_ORIGIN_RPY))


def forward_kinematics(joint_deg: dict) -> np.ndarray:
    """4x4 world(base_link)-to-gripper_frame_link transform for the given
    joint angles (degrees, motor-reading convention -- same values this
    project reads from/sends to the robot). joint_deg must have all of
    JOINT_ORDER; extra keys (e.g. "gripper") are ignored."""
    t = np.eye(4)
    for name in JOINT_ORDER:
        origin_xyz, origin_rpy = _JOINT_GEOMETRY[name]
        angle_rad = np.deg2rad(joint_deg[name])
        t = t @ _joint_transform(origin_xyz, origin_rpy, angle_rad)
    return t @ _GRIPPER_FRAME_TRANSFORM


def end_effector_position(joint_deg: dict) -> np.ndarray:
    """Convenience: just the (x, y, z) translation from forward_kinematics."""
    return forward_kinematics(joint_deg)[:3, 3]


def _orientation_error_rotvec(r_current: np.ndarray, r_target: np.ndarray) -> np.ndarray:
    """Small-angle rotation-vector error (r_current -> r_target), valid for
    the small per-iteration corrections this solver takes. Avoids adding a
    scipy dependency for a single log-map."""
    r_err = r_target @ r_current.T
    # skew-symmetric part of (R - R^T) / 2 recovers sin(theta) * axis for
    # small theta, which is exactly the regime each Gauss-Newton step uses.
    return np.array(
        [
            r_err[2, 1] - r_err[1, 2],
            r_err[0, 2] - r_err[2, 0],
            r_err[1, 0] - r_err[0, 1],
        ]
    ) / 2.0


def _solve_to_target_pos(
    current_joint_deg: dict,
    target_pos: np.ndarray,
    max_iters: int,
    tol_m: float,
    orientation_weight: float,
    max_step_deg: float,
):
    """Shared Gauss-Newton core: solve ACTIVE_LIFT_JOINTS so the end effector
    reaches target_pos (holding orientation as close as possible to the
    current orientation), holding FIXED_LIFT_JOINTS unchanged. Returns
    (q_final, converged, iterations, t_start, t_final, r_final) -- callers
    interpret t_start/t_final/r_final into their own reported deltas, since
    "delta_z_m" vs "delta_forward_m/delta_z_m" mean different things to
    solve_cartesian_z_lift and solve_cartesian_forward_and_z."""
    t_start = forward_kinematics(current_joint_deg)
    target_rot = t_start[:3, :3].copy()

    fixed = {name: current_joint_deg[name] for name in FIXED_LIFT_JOINTS}
    q = np.array([current_joint_deg[name] for name in ACTIVE_LIFT_JOINTS], dtype=float)

    def fk_active(q_deg: np.ndarray) -> np.ndarray:
        full = {**fixed, **dict(zip(ACTIVE_LIFT_JOINTS, q_deg))}
        return forward_kinematics(full)

    def residual(q_deg: np.ndarray) -> np.ndarray:
        t = fk_active(q_deg)
        pos_err = t[:3, 3] - target_pos
        rot_err = _orientation_error_rotvec(t[:3, :3], target_rot) * orientation_weight
        return np.concatenate([pos_err, rot_err])

    eps_deg = 1e-4
    converged = False
    iterations = 0
    r = residual(q)
    for iterations in range(1, max_iters + 1):
        if np.linalg.norm(r[:3]) < tol_m:
            converged = True
            break

        jac = np.zeros((6, 3))
        for i in range(3):
            dq = np.zeros(3)
            dq[i] = eps_deg
            jac[:, i] = (residual(q + dq) - r) / eps_deg

        step, *_ = np.linalg.lstsq(jac, -r, rcond=None)
        step = np.clip(step, -max_step_deg, max_step_deg)
        q = q + step
        r = residual(q)
    else:
        converged = np.linalg.norm(r[:3]) < tol_m

    t_final = fk_active(q)
    return q, converged, iterations, t_start, t_final, r


def solve_cartesian_z_lift(
    current_joint_deg: dict,
    delta_z_m: float,
    max_iters: int = 50,
    tol_m: float = 1e-6,
    orientation_weight: float = 0.05,
    max_step_deg: float = 8.0,
) -> dict:
    """Solve for new shoulder_lift/elbow_flex/wrist_flex values that move the
    end effector by approximately (0, 0, delta_z_m) in the base_link frame,
    holding shoulder_pan and wrist_roll fixed (see module docstring).

    Numeric damped Gauss-Newton over a 6-dim residual (3 position + 3
    orientation, orientation down-weighted by orientation_weight so position
    dominates -- mirrors lerobot's own IK default weighting) and 3 unknowns
    (ACTIVE_LIFT_JOINTS, in degrees). Each step is clamped to max_step_deg to
    keep the (small, near-linear) local Jacobian approximation valid; this is
    a one-shot offline solve, not a control-loop primitive, so a few extra
    iterations cost nothing.

    Returns {"joint_deg": <current_joint_deg with ACTIVE_LIFT_JOINTS updated,
    all other keys incl. FIXED_LIFT_JOINTS and any extras like "gripper"
    passed through unchanged>, "converged": bool, "iterations": int,
    "achieved_delta_z_m": float, "xy_drift_m": float,
    "orientation_error_deg": float}.
    """
    t_start = forward_kinematics(current_joint_deg)
    target_pos = t_start[:3, 3].copy()
    target_pos[2] += delta_z_m

    q, converged, iterations, t_start, t_final, r = _solve_to_target_pos(
        current_joint_deg, target_pos, max_iters, tol_m, orientation_weight, max_step_deg
    )

    achieved_dz = float(t_final[2, 3] - t_start[2, 3])
    xy_drift = float(np.linalg.norm(t_final[:2, 3] - t_start[:2, 3]))
    orientation_error_deg = float(np.rad2deg(np.linalg.norm(r[3:] / max(orientation_weight, 1e-12))))

    joint_deg = dict(current_joint_deg)
    joint_deg.update(dict(zip(ACTIVE_LIFT_JOINTS, (float(v) for v in q))))

    return {
        "joint_deg": joint_deg,
        "converged": converged,
        "iterations": iterations,
        "achieved_delta_z_m": achieved_dz,
        "xy_drift_m": xy_drift,
        "orientation_error_deg": orientation_error_deg,
    }


def solve_cartesian_forward_and_z(
    current_joint_deg: dict,
    delta_forward_m: float,
    delta_z_m: float,
    max_iters: int = 50,
    tol_m: float = 1e-4,
    orientation_weight: float = 0.05,
    max_step_deg: float = 8.0,
) -> dict:
    """Solve for new shoulder_lift/elbow_flex/wrist_flex values that move the
    end effector by delta_forward_m along its current horizontal "reach"
    direction and delta_z_m vertically, holding shoulder_pan and wrist_roll
    fixed.

    "Forward" needs no separate azimuthal degree of freedom here: since
    shoulder_pan never moves, ACTIVE_LIFT_JOINTS motion is already confined
    to the single fixed vertical plane containing the base Z axis and the
    current end-effector position (this is the same fact
    solve_cartesian_z_lift's "shoulder_pan holds Z, minimizing X/Y" property
    rests on). "Forward" is simply increasing radial distance within that
    plane: the horizontal unit vector from the base's pan axis through the
    current end-effector XY position. Built for the descent step of the
    grasp->clearance->mobile_approach->descent->release chain, once an
    operator identifies that a pure vertical descent doesn't reach far
    enough over the target (see confirmed_grasp_handoff.md) -- pure Z was
    solving the wrong problem, not solving Z incorrectly.

    Returns the same shape as solve_cartesian_z_lift, plus
    "achieved_delta_forward_m" (the true intended in-plane motion) and
    "lateral_drift_m" (motion perpendicular to that plane -- the actual
    unwanted drift here, since some horizontal motion is now intentional).

    tol_m defaults looser than solve_cartesian_z_lift's (1e-4 vs 1e-6): this
    combined problem's finite-difference Jacobian plateaus around ~5e-5m
    residual (numerical noise floor, not a real solution error -- confirmed
    by achieved values landing within a fraction of a mm of the target well
    before that point), so 1e-6 never triggers early exit and burns the full
    max_iters for no benefit. 1e-4m is still two orders of magnitude tighter
    than anything physically meaningful on this hardware.
    """
    t_start = forward_kinematics(current_joint_deg)
    start_pos = t_start[:3, 3].copy()
    radial_xy = start_pos[:2] / np.linalg.norm(start_pos[:2])
    lateral_xy = np.array([-radial_xy[1], radial_xy[0]])

    target_pos = start_pos.copy()
    target_pos[:2] += delta_forward_m * radial_xy
    target_pos[2] += delta_z_m

    q, converged, iterations, t_start, t_final, r = _solve_to_target_pos(
        current_joint_deg, target_pos, max_iters, tol_m, orientation_weight, max_step_deg
    )

    delta_xy = t_final[:2, 3] - t_start[:2, 3]
    achieved_forward = float(np.dot(delta_xy, radial_xy))
    lateral_drift = float(np.dot(delta_xy, lateral_xy))
    achieved_dz = float(t_final[2, 3] - t_start[2, 3])
    orientation_error_deg = float(np.rad2deg(np.linalg.norm(r[3:] / max(orientation_weight, 1e-12))))

    joint_deg = dict(current_joint_deg)
    joint_deg.update(dict(zip(ACTIVE_LIFT_JOINTS, (float(v) for v in q))))

    return {
        "joint_deg": joint_deg,
        "converged": converged,
        "iterations": iterations,
        "achieved_delta_forward_m": achieved_forward,
        "achieved_delta_z_m": achieved_dz,
        "lateral_drift_m": lateral_drift,
        "orientation_error_deg": orientation_error_deg,
    }
