#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""hybrid_pick_place: packages the exact physical chain that achieved
HYBRID PICK-PLACE PHYSICAL SUCCESS on 2026-09-18 (see
.local-dev/confirmed_grasp_handoff.md) into one reproducible, explicit state
machine -- WITHOUT changing any proven behavior. This module does not
retrain, tune, or reintroduce the VLA; it orchestrates the already-validated
primitives (autonomy/manipulation/cartesian_lift.py's
solve_cartesian_z_lift/solve_cartesian_forward_and_z, the deterministic
grasp geometry, the deterministic gripper latch/open values, and the
refreshed-lease StableMotionGateway sustained-motion pattern) in the same
order and with the same physical-judgment gates that worked.

Pure, hardware-free, matching this project's existing boundary
(action_adapter.py, pose_preflight.py, canonical_handoff.py,
cartesian_lift.py -- none import lerobot). All device I/O is injected via
PickPlaceDeps: real hardware wiring (LeKiwiClient reads/writes, the
canonical_handoff-based bounded interpolation, the
MobileApproachController/StableMotionGateway/LeKiwiTransport refreshed
lease) lives in a separate, uncommitted wiring script
(.local-dev/run_hybrid_pick_place.py) -- exactly the split
run_canonical_handoff()/move_to_target.py and mobile_approach.py/
sustained_pulse_test.py already use. This module is therefore fully
unit-testable without a robot (see test_hybrid_pick_place.py), and the one
thing it does call directly -- cartesian_lift's real solvers -- is
deliberately NOT faked in tests, so the singularity self-refusal path is
exercised against the real solver, not a stand-in.

Scope: this machine assumes it is invoked from an already-connected,
already-torque-safe robot (the standing fresh-host/Present-vs-Goal safety
procedure in confirmed_grasp_handoff.md remains a separate, manual,
operator-run step before every session -- it is Pi/host infrastructure, not
part of the proven pick-place motion chain, and is deliberately out of
scope here).

Proven geometry preserved exactly (do not change without a new physical
validation):
  - known-good floor grasp pose: pan -10.549, lift 43.121, elbow 54.286,
    wrist_flex -33.714 (wrist_roll is NEVER part of this or any other
    override -- always a runtime-only hold, read fresh, never forced).
  - gripper latch = 66.8, gripper open = 80.193.
  - Cartesian steps are always solved from a FRESH measured pose and
    executed via bounded interpolation, then re-measured -- Cartesian
    motion on this arm is not linear/composable from stale deltas (see
    cartesian_lift.py's own docstring and the ~72-101% realization-ratio
    findings in confirmed_grasp_handoff.md).
  - the singularity-margin rejection (elbow_flex within ~10deg of 0/180)
    is preserved as a REFUSAL, not an auto-shrink: PLACE_APPROACH computes
    the requested target, checks it, and if unsafe, aborts that attempt
    with a clear reason instead of picking a smaller one itself -- the
    2026-09-18 run needed a human to choose 60mm/27mm after 100mm/45mm was
    refused, and this module preserves that same human-in-the-loop
    response rather than inventing a new automatic-retry algorithm (which
    would be new, unvalidated behavior, not "packaging the exact chain").
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from base_rest import reads_at_rest  # noqa: E402
from cartesian_lift import (  # noqa: E402
    ACTIVE_LIFT_JOINTS,
    JOINT_ORDER,
    solve_cartesian_forward_and_z,
    solve_cartesian_z_lift,
)

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

STATE_PRECHECK = "PRECHECK"
STATE_GRASP_POSITION = "GRASP_POSITION"
STATE_GRASP_CLOSE = "GRASP_CLOSE"
STATE_GRASP_VERIFY = "GRASP_VERIFY"
STATE_CLEARANCE_LIFT = "CLEARANCE_LIFT"
STATE_CLEARANCE_VERIFY = "CLEARANCE_VERIFY"
STATE_MOBILE_READY = "MOBILE_READY"
STATE_WAITING_FOR_CAPABILITY = "WAITING_FOR_CAPABILITY"
STATE_MOBILE_APPROACH = "MOBILE_APPROACH"
STATE_BASE_STOP_VERIFY = "BASE_STOP_VERIFY"
STATE_PLACE_APPROACH = "PLACE_APPROACH"
STATE_PLACE_READY = "PLACE_READY"
STATE_RELEASE = "RELEASE"
STATE_RETREAT = "RETREAT"
STATE_DONE = "DONE"
STATE_ABORTED = "ABORTED"

STATES = (
    STATE_PRECHECK,
    STATE_GRASP_POSITION,
    STATE_GRASP_CLOSE,
    STATE_GRASP_VERIFY,
    STATE_CLEARANCE_LIFT,
    STATE_CLEARANCE_VERIFY,
    STATE_MOBILE_READY,
    STATE_WAITING_FOR_CAPABILITY,
    STATE_MOBILE_APPROACH,
    STATE_BASE_STOP_VERIFY,
    STATE_PLACE_APPROACH,
    STATE_PLACE_READY,
    STATE_RELEASE,
    STATE_RETREAT,
    STATE_DONE,
    STATE_ABORTED,
)

# ---------------------------------------------------------------------------
# Proven constants -- the exact successful geometry (see module docstring)
# ---------------------------------------------------------------------------

KNOWN_GOOD_GRASP_POSE_DEG = {
    "shoulder_pan": -10.549,
    "shoulder_lift": 43.121,
    "elbow_flex": 54.286,
    "wrist_flex": -33.714,
}
GRIPPER_LATCH = 66.8
GRIPPER_OPEN = 80.193

DEFAULT_VERIFICATION_LIFT_M = 0.015
DEFAULT_CLEARANCE_STEP_M = 0.020
DEFAULT_MAX_CLEARANCE_STEPS = 3
DEFAULT_MAX_PLACE_STEPS = 6
DEFAULT_RETREAT_STEP_M = 0.020
DEFAULT_MOVE_DURATION_S = 2.0
DEFAULT_GRIPPER_MOVE_DURATION_S = 0.5
DEFAULT_GRIPPER_HOLD_S = 0.75
DEFAULT_BASE_VX_MPS = 0.05
DEFAULT_BASE_MOVE_DURATION_S = 1.5

MOBILE_APPROACH_CAPABILITY = "mobile_approach"

# Mirrors cartesian_lift's own margin choice, used consistently throughout
# this session's live safety gates.
SINGULARITY_MARGIN_DEG = 10.0
JOINT_LIMIT_MARGIN_DEG = 3.0
JOINT_LIMITS_DEG = {"shoulder_lift": 100.0000, "elbow_flex": 96.8494, "wrist_flex": 95.0000}
MAX_LATERAL_DRIFT_M = 0.005
MAX_XY_DRIFT_M = 0.005


# ---------------------------------------------------------------------------
# Dependency injection boundary (real hardware wiring lives outside this
# module -- see .local-dev/run_hybrid_pick_place.py)
# ---------------------------------------------------------------------------


@dataclass
class PickPlaceDeps:
    read_pose: Callable[[], dict]
    read_base: Callable[[], dict]
    move_arm: Callable[[dict, float, float], dict]
    move_base_sustained: Callable[[float, float], bool]
    confirm: Callable[[str], str]
    log: Callable[..., None]
    # Capability boundary (Cake integration). Defaults preserve prior
    # behavior exactly for every caller that predates this boundary:
    # "always available" means MOBILE_READY -> MOBILE_APPROACH directly,
    # same as before WAITING_FOR_CAPABILITY existed. Real/Cake-backed
    # callers pass a CapabilityRegistry's has_capability/wait_for_change
    # (autonomy/motion/capability_registry.py) instead.
    capability_available: Callable[[str], bool] = lambda name: True
    wait_for_capability_change: Callable[[], None] = lambda: None


@dataclass
class PickPlaceResult:
    final_state: str
    object_secured: bool = False
    end_effector_clearance_confirmed: bool = False
    placement_ready: bool = False
    arm_frozen: bool = False
    base_stationary: bool = False
    fresh_observation_available: bool = False
    final_pose: Optional[dict] = None
    abort_reason: Optional[str] = None
    events: list = field(default_factory=list)


@dataclass
class _Ctx:
    object_secured: bool = False
    end_effector_clearance_confirmed: bool = False
    placement_ready: bool = False
    arm_frozen: bool = False
    base_stationary: bool = False
    fresh_observation_available: bool = False
    last_pose: Optional[dict] = None
    abort_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Capability contracts (section 3) -- checked explicitly before the
# transitions they gate, not folded silently into procedural code.
# ---------------------------------------------------------------------------


def check_mobile_approach_preconditions(ctx: _Ctx):
    checks = {
        "object_secured": ctx.object_secured,
        "end_effector_clearance_confirmed": ctx.end_effector_clearance_confirmed,
        "arm_frozen": ctx.arm_frozen,
        "base_stationary": ctx.base_stationary,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return (not failed, f"MOBILE_APPROACH precondition failed: {failed}" if failed else "")


def check_post_mobility_manipulation_preconditions(ctx: _Ctx):
    checks = {
        "base_stationary": ctx.base_stationary,
        "fresh_observation_available": ctx.fresh_observation_available,
        "object_secured": ctx.object_secured,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return (not failed, f"post-mobility manipulation precondition failed: {failed}" if failed else "")


def check_release_preconditions(ctx: _Ctx):
    checks = {
        "base_stationary": ctx.base_stationary,
        "arm_frozen": ctx.arm_frozen,
        "placement_ready": ctx.placement_ready,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return (not failed, f"RELEASE precondition failed: {failed}" if failed else "")


# ---------------------------------------------------------------------------
# Cartesian step safety gate -- the same checks used live on 2026-09-18,
# applied uniformly to both solve_cartesian_z_lift and
# solve_cartesian_forward_and_z results before any actuation.
# ---------------------------------------------------------------------------


TARGET_HIT_TOL_M = 0.001  # 1mm -- matches the live-session ad hoc check exactly


def _gate_cartesian_result(result: dict, current_pose: dict, requested: dict) -> "tuple[bool, str]":
    """requested: {"delta_z_m": ...} or {"delta_forward_m": ..., "delta_z_m": ...},
    matching whichever solver produced `result`.

    Deliberately does NOT gate on result["converged"]: both cartesian_lift
    solvers can report converged=False on a numerical noise-floor plateau
    (residual ~0.05-0.4mm, see cartesian_lift.py's tol_m discussion and this
    module's test suite) that is still a physically-accurate solution. The
    live 2026-09-18 session used achieved-vs-requested directly as the real
    "did it actually hit the target" check -- reproduced here exactly,
    within TARGET_HIT_TOL_M."""
    joint_deg = result["joint_deg"]
    reasons = []

    if not all(abs(joint_deg[n]) < 1e6 for n in ACTIVE_LIFT_JOINTS):
        reasons.append("non-finite target")

    for name, limit in JOINT_LIMITS_DEG.items():
        if abs(joint_deg[name]) >= limit - JOINT_LIMIT_MARGIN_DEG:
            reasons.append(f"{name}={joint_deg[name]:.2f} within {JOINT_LIMIT_MARGIN_DEG}deg of its +/-{limit} limit")

    elbow = joint_deg["elbow_flex"]
    if abs(elbow) <= SINGULARITY_MARGIN_DEG or abs(elbow - 180) <= SINGULARITY_MARGIN_DEG or abs(elbow + 180) <= SINGULARITY_MARGIN_DEG:
        reasons.append(f"elbow_flex target {elbow:.2f}deg is within {SINGULARITY_MARGIN_DEG}deg of a singularity (0/180)")

    if joint_deg["shoulder_pan"] != current_pose["shoulder_pan"]:
        reasons.append("shoulder_pan target does not match current (must never move)")
    if joint_deg["wrist_roll"] != current_pose["wrist_roll"]:
        reasons.append("wrist_roll target does not match current (must never move)")

    drift = result.get("xy_drift_m", result.get("lateral_drift_m"))
    if drift is not None and abs(drift) >= MAX_XY_DRIFT_M:
        reasons.append(f"drift {drift * 1000:.2f}mm exceeds {MAX_XY_DRIFT_M * 1000:.1f}mm bound")

    dz_err = abs(result["achieved_delta_z_m"] - requested["delta_z_m"])
    if dz_err >= TARGET_HIT_TOL_M:
        reasons.append(f"achieved delta_z off by {dz_err * 1000:.3f}mm (>{TARGET_HIT_TOL_M * 1000:.1f}mm)")
    if "delta_forward_m" in requested:
        fwd_err = abs(result["achieved_delta_forward_m"] - requested["delta_forward_m"])
        if fwd_err >= TARGET_HIT_TOL_M:
            reasons.append(f"achieved forward off by {fwd_err * 1000:.3f}mm (>{TARGET_HIT_TOL_M * 1000:.1f}mm)")

    return (len(reasons) == 0, "; ".join(reasons))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _full_pose_from(ctx: _Ctx, deps: PickPlaceDeps) -> dict:
    pose = deps.read_pose()
    ctx.last_pose = pose
    ctx.fresh_observation_available = True
    return pose


def _base_is_stationary(base: dict) -> bool:
    """base_rest.reads_at_rest: no wheel above one velocity count. Formerly exact zero (1e-6) on all three axes, which one
    count of dither on a held wheel failed (operator ruling 2026-09-27)."""
    return reads_at_rest(base)


def _log_enter(deps, state):
    deps.log("STATE_ENTER", state=state)


def _log_pass(deps, state, **fields):
    deps.log("STATE_PASS", state=state, **fields)


def _abort(deps, ctx, state, reason):
    ctx.abort_reason = f"{state}: {reason}"
    deps.log("STATE_ABORT", state=state, reason=reason)
    return STATE_ABORTED


def _parse_extend_command(line: str):
    """'EXTEND <forward_mm> <down_mm>' -> (forward_m, down_m); down_m is
    returned negative (a descent), matching delta_z_m's sign convention."""
    parts = line.split()
    forward_mm = float(parts[1])
    down_mm = float(parts[2])
    return forward_mm / 1000.0, -down_mm / 1000.0


def _parse_descend_command(line: str):
    """'DESCEND <mm>' -> delta_z_m (negative)."""
    parts = line.split()
    return -float(parts[1]) / 1000.0


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


def _state_precheck(deps: PickPlaceDeps, ctx: _Ctx) -> str:
    _log_enter(deps, STATE_PRECHECK)
    pose = _full_pose_from(ctx, deps)
    base = deps.read_base()
    ctx.base_stationary = _base_is_stationary(base)
    ctx.arm_frozen = True
    if not ctx.base_stationary:
        return _abort(deps, ctx, STATE_PRECHECK, f"base not stationary: {base}")
    _log_pass(deps, STATE_PRECHECK, pose=pose, base=base)
    return STATE_GRASP_POSITION


def _state_grasp_position(deps: PickPlaceDeps, ctx: _Ctx, grasp_pose_deg: dict, move_duration_s: float) -> str:
    _log_enter(deps, STATE_GRASP_POSITION)
    overrides = dict(grasp_pose_deg)
    deps.log("ARM_MOVE_REQUEST", state=STATE_GRASP_POSITION, overrides=overrides)
    measured = deps.move_arm(overrides, move_duration_s, 0.0)
    ctx.last_pose = measured
    ctx.arm_frozen = True
    _log_pass(deps, STATE_GRASP_POSITION, measured=measured)
    return STATE_GRASP_CLOSE


def _state_grasp_close(deps: PickPlaceDeps, ctx: _Ctx, gripper_latch: float, gripper_move_duration_s: float, gripper_hold_s: float) -> str:
    _log_enter(deps, STATE_GRASP_CLOSE)
    # The proven procedure this packages was never "arrive then close blind"
    # -- confirmed_grasp_handoff.md's original workflow explicitly waits for
    # the operator to place the cube first. Missing this gate was a real
    # packaging gap (closing on nothing, or closing while the operator's
    # hand is still in the jaws, is not something the live session ever
    # actually did) -- caught and fixed before the first reproduction run,
    # not discovered by running it.
    answer = deps.confirm("PLACE CUBE BETWEEN JAWS, then confirm CUBE READY/ABORT: ").strip().upper()
    if answer == "ABORT":
        return _abort(deps, ctx, STATE_GRASP_CLOSE, "operator ABORT")
    if answer != "CUBE READY":
        return _abort(deps, ctx, STATE_GRASP_CLOSE, f"CUBE READY not confirmed (operator said {answer!r})")

    overrides = {"gripper": gripper_latch}
    deps.log("ARM_MOVE_REQUEST", state=STATE_GRASP_CLOSE, overrides=overrides)
    measured = deps.move_arm(overrides, gripper_move_duration_s, gripper_hold_s)
    ctx.last_pose = measured
    ctx.arm_frozen = True
    _log_pass(deps, STATE_GRASP_CLOSE, measured=measured)
    return STATE_GRASP_VERIFY


def _state_grasp_verify(deps: PickPlaceDeps, ctx: _Ctx, verification_lift_m: float, move_duration_s: float) -> str:
    _log_enter(deps, STATE_GRASP_VERIFY)
    current = _full_pose_from(ctx, deps)
    result = solve_cartesian_z_lift(current, delta_z_m=verification_lift_m)
    ok, reason = _gate_cartesian_result(result, current, {"delta_z_m": verification_lift_m})
    deps.log("IK_REQUEST", state=STATE_GRASP_VERIFY, delta_z_m=verification_lift_m)
    deps.log("IK_RESULT", state=STATE_GRASP_VERIFY, result=result, gate_ok=ok, gate_reason=reason)
    if not ok:
        return _abort(deps, ctx, STATE_GRASP_VERIFY, f"verification-lift target unsafe: {reason}")

    overrides = {n: result["joint_deg"][n] for n in ACTIVE_LIFT_JOINTS}
    measured = deps.move_arm(overrides, move_duration_s, 0.0)
    ctx.last_pose = measured

    answer = deps.confirm("CONFIRM GRASP (cube visibly lifted with the gripper? YES/NO/ABORT): ").strip().upper()
    if answer == "ABORT":
        return _abort(deps, ctx, STATE_GRASP_VERIFY, "operator ABORT")
    if answer != "YES":
        ctx.object_secured = False
        return _abort(deps, ctx, STATE_GRASP_VERIFY, f"GRASP NOT CONFIRMED (operator said {answer!r})")

    ctx.object_secured = True
    _log_pass(deps, STATE_GRASP_VERIFY, measured=measured)
    return STATE_CLEARANCE_LIFT


def _state_clearance_lift(deps: PickPlaceDeps, ctx: _Ctx, clearance_step_m: float, move_duration_s: float, step_count: int) -> str:
    _log_enter(deps, STATE_CLEARANCE_LIFT)
    current = _full_pose_from(ctx, deps)
    result = solve_cartesian_z_lift(current, delta_z_m=clearance_step_m)
    ok, reason = _gate_cartesian_result(result, current, {"delta_z_m": clearance_step_m})
    deps.log("IK_REQUEST", state=STATE_CLEARANCE_LIFT, delta_z_m=clearance_step_m, step=step_count)
    deps.log("IK_RESULT", state=STATE_CLEARANCE_LIFT, result=result, gate_ok=ok, gate_reason=reason)
    if not ok:
        return _abort(deps, ctx, STATE_CLEARANCE_LIFT, f"clearance step unsafe: {reason}")

    overrides = {n: result["joint_deg"][n] for n in ACTIVE_LIFT_JOINTS}
    measured = deps.move_arm(overrides, move_duration_s, 0.0)
    ctx.last_pose = measured
    ctx.arm_frozen = True
    _log_pass(deps, STATE_CLEARANCE_LIFT, measured=measured)
    return STATE_CLEARANCE_VERIFY


def _state_clearance_verify(deps: PickPlaceDeps, ctx: _Ctx, step_count: int, max_clearance_steps: int) -> str:
    _log_enter(deps, STATE_CLEARANCE_VERIFY)
    answer = deps.confirm("CONFIRM CLEARANCE (YES/PARTIAL/NO/ABORT): ").strip().upper()
    if answer == "ABORT":
        return _abort(deps, ctx, STATE_CLEARANCE_VERIFY, "operator ABORT")
    if answer == "YES":
        ctx.end_effector_clearance_confirmed = True
        _log_pass(deps, STATE_CLEARANCE_VERIFY)
        return STATE_MOBILE_READY
    if step_count >= max_clearance_steps:
        return _abort(deps, ctx, STATE_CLEARANCE_VERIFY, f"max_clearance_steps ({max_clearance_steps}) reached without confirmed clearance")
    return STATE_CLEARANCE_LIFT


def _state_mobile_ready(deps: PickPlaceDeps, ctx: _Ctx) -> str:
    _log_enter(deps, STATE_MOBILE_READY)
    base = deps.read_base()
    ctx.base_stationary = _base_is_stationary(base)
    ok, reason = check_mobile_approach_preconditions(ctx)
    if not ok:
        return _abort(deps, ctx, STATE_MOBILE_READY, reason)

    available = deps.capability_available(MOBILE_APPROACH_CAPABILITY)
    deps.log("CAPABILITY_REQUIRED", capability=MOBILE_APPROACH_CAPABILITY)
    deps.log("CAPABILITY_AVAILABLE", capability=MOBILE_APPROACH_CAPABILITY, available=available)
    _log_pass(deps, STATE_MOBILE_READY, base=base)
    if not available:
        return STATE_WAITING_FOR_CAPABILITY
    return STATE_MOBILE_APPROACH


def _state_waiting_for_capability(deps: PickPlaceDeps, ctx: _Ctx) -> str:
    """Physical invariants (object_secured, end_effector_clearance_confirmed,
    arm_frozen, base_stationary) are already true on entry -- this state
    sends no arm or base command of any kind, so they can only be preserved,
    never advanced, while here. Blocks on deps.wait_for_capability_change()
    (the real adapter polls Cake's admin socket per mobile-approach-spec.md
    section 6; tests drive availability directly and never rely on this
    actually blocking) until capability_available() reports True."""
    _log_enter(deps, STATE_WAITING_FOR_CAPABILITY)
    max_polls = 10_000  # backstop against a genuine infinite loop, not a business timeout
    for _ in range(max_polls):
        if deps.capability_available(MOBILE_APPROACH_CAPABILITY):
            deps.log("CAPABILITY_AVAILABLE", capability=MOBILE_APPROACH_CAPABILITY, available=True)
            _log_pass(deps, STATE_WAITING_FOR_CAPABILITY)
            return STATE_MOBILE_APPROACH
        deps.log("CAPABILITY_AVAILABLE", capability=MOBILE_APPROACH_CAPABILITY, available=False)
        deps.wait_for_capability_change()
    return _abort(deps, ctx, STATE_WAITING_FOR_CAPABILITY, f"capability still unavailable after {max_polls} polls")


def _state_mobile_approach(deps: PickPlaceDeps, ctx: _Ctx, base_vx_mps: float, base_move_duration_s: float) -> str:
    _log_enter(deps, STATE_MOBILE_APPROACH)
    ok, reason = check_mobile_approach_preconditions(ctx)
    if not ok:
        return _abort(deps, ctx, STATE_MOBILE_APPROACH, reason)
    if not deps.capability_available(MOBILE_APPROACH_CAPABILITY):
        # Capability disappeared between WAITING_FOR_CAPABILITY's check and
        # here (mobile-approach-spec.md section 7's "capability crash"
        # row) -- back to waiting, not an abort, no motion sent.
        deps.log("CAPABILITY_AVAILABLE", capability=MOBILE_APPROACH_CAPABILITY, available=False)
        return STATE_WAITING_FOR_CAPABILITY

    deps.log("BASE_MOVE_REQUEST", state=STATE_MOBILE_APPROACH, vx_mps=base_vx_mps, duration_s=base_move_duration_s)
    stopped_confirmed = deps.move_base_sustained(base_vx_mps, base_move_duration_s)
    deps.log("BASE_MOVE_RESULT", state=STATE_MOBILE_APPROACH, stopped_confirmed=stopped_confirmed)
    if not stopped_confirmed:
        ctx.base_stationary = False
        return _abort(deps, ctx, STATE_MOBILE_APPROACH, "sustained move did not report a confirmed full stop")

    _log_pass(deps, STATE_MOBILE_APPROACH)
    return STATE_BASE_STOP_VERIFY


def _state_base_stop_verify(deps: PickPlaceDeps, ctx: _Ctx) -> str:
    _log_enter(deps, STATE_BASE_STOP_VERIFY)
    base = deps.read_base()
    ctx.base_stationary = _base_is_stationary(base)
    if not ctx.base_stationary:
        return _abort(deps, ctx, STATE_BASE_STOP_VERIFY, f"base velocity not zero after stop: {base}")
    ctx.fresh_observation_available = True
    deps.log("BASE_ZERO_CONFIRMED", state=STATE_BASE_STOP_VERIFY, base=base)
    _log_pass(deps, STATE_BASE_STOP_VERIFY, base=base)
    return STATE_PLACE_APPROACH


def _state_place_approach(deps: PickPlaceDeps, ctx: _Ctx, move_duration_s: float, step_count: int, max_place_steps: int) -> str:
    _log_enter(deps, STATE_PLACE_APPROACH)
    ok, reason = check_post_mobility_manipulation_preconditions(ctx)
    if not ok:
        return _abort(deps, ctx, STATE_PLACE_APPROACH, reason)
    if step_count > max_place_steps:
        return _abort(deps, ctx, STATE_PLACE_APPROACH, f"max_place_steps ({max_place_steps}) exceeded")

    current = _full_pose_from(ctx, deps)
    line = deps.confirm(
        "PLACE_APPROACH: enter 'DESCEND <mm>', 'EXTEND <forward_mm> <down_mm>', 'READY' (no move), or 'ABORT': "
    ).strip()
    upper = line.strip().upper()
    if upper == "ABORT":
        return _abort(deps, ctx, STATE_PLACE_APPROACH, "operator ABORT")
    if upper == "READY":
        _log_pass(deps, STATE_PLACE_APPROACH, note="no further descent requested")
        return STATE_PLACE_READY

    if upper.startswith("EXTEND"):
        forward_m, down_m = _parse_extend_command(upper)
        result = solve_cartesian_forward_and_z(current, delta_forward_m=forward_m, delta_z_m=down_m)
        requested = {"delta_forward_m": forward_m, "delta_z_m": down_m}
        deps.log("IK_REQUEST", state=STATE_PLACE_APPROACH, mode="EXTEND", delta_forward_m=forward_m, delta_z_m=down_m, step=step_count)
    elif upper.startswith("DESCEND"):
        down_m = _parse_descend_command(upper)
        result = solve_cartesian_z_lift(current, delta_z_m=down_m)
        requested = {"delta_z_m": down_m}
        deps.log("IK_REQUEST", state=STATE_PLACE_APPROACH, mode="DESCEND", delta_z_m=down_m, step=step_count)
    else:
        return _abort(deps, ctx, STATE_PLACE_APPROACH, f"unrecognized command: {line!r}")

    ok, reason = _gate_cartesian_result(result, current, requested)
    deps.log("IK_RESULT", state=STATE_PLACE_APPROACH, result=result, gate_ok=ok, gate_reason=reason)
    if not ok:
        # Preserve the refusal as a feature: do not actuate, do not
        # auto-shrink. The operator chooses the next (smaller) attempt on a
        # fresh invocation.
        return _abort(deps, ctx, STATE_PLACE_APPROACH, f"placement step unsafe, refused: {reason}")

    overrides = {n: result["joint_deg"][n] for n in ACTIVE_LIFT_JOINTS}
    measured = deps.move_arm(overrides, move_duration_s, 0.0)
    ctx.last_pose = measured
    ctx.arm_frozen = True
    _log_pass(deps, STATE_PLACE_APPROACH, measured=measured)
    # Mirrors CLEARANCE_LIFT -> CLEARANCE_VERIFY: one increment per visit,
    # then the operator gate decides whether to loop back for another.
    return STATE_PLACE_READY


def _state_place_ready(deps: PickPlaceDeps, ctx: _Ctx, step_count: int, max_place_steps: int) -> str:
    _log_enter(deps, STATE_PLACE_READY)
    answer = deps.confirm("CONFIRM PLACEMENT (RELEASE READY? YES/NO/ABORT): ").strip().upper()
    if answer == "ABORT":
        return _abort(deps, ctx, STATE_PLACE_READY, "operator ABORT")
    if answer == "YES":
        ctx.placement_ready = True
        _log_pass(deps, STATE_PLACE_READY)
        return STATE_RELEASE
    if step_count >= max_place_steps:
        return _abort(deps, ctx, STATE_PLACE_READY, f"max_place_steps ({max_place_steps}) reached without RELEASE READY")
    # NO/PARTIAL -- another placement increment, mirroring CLEARANCE_VERIFY.
    return STATE_PLACE_APPROACH


def _state_release(deps: PickPlaceDeps, ctx: _Ctx, gripper_open: float, gripper_move_duration_s: float, gripper_hold_s: float) -> str:
    _log_enter(deps, STATE_RELEASE)
    base = deps.read_base()
    ctx.base_stationary = _base_is_stationary(base)
    ok, reason = check_release_preconditions(ctx)
    if not ok:
        return _abort(deps, ctx, STATE_RELEASE, reason)

    overrides = {"gripper": gripper_open}
    deps.log("ARM_MOVE_REQUEST", state=STATE_RELEASE, overrides=overrides)
    measured = deps.move_arm(overrides, gripper_move_duration_s, gripper_hold_s)
    ctx.last_pose = measured

    answer = deps.confirm("CONFIRM RELEASE (cube left jaws and landed on pad? SUCCESS/FAIL/ABORT): ").strip().upper()
    if answer == "ABORT":
        return _abort(deps, ctx, STATE_RELEASE, "operator ABORT")
    if answer != "SUCCESS":
        return _abort(deps, ctx, STATE_RELEASE, f"RELEASE PLACEMENT FAIL (operator said {answer!r})")

    _log_pass(deps, STATE_RELEASE, measured=measured)
    return STATE_RETREAT


def _state_retreat(deps: PickPlaceDeps, ctx: _Ctx, retreat_step_m: float, move_duration_s: float) -> str:
    _log_enter(deps, STATE_RETREAT)
    current = _full_pose_from(ctx, deps)
    result = solve_cartesian_z_lift(current, delta_z_m=retreat_step_m)
    ok, reason = _gate_cartesian_result(result, current, {"delta_z_m": retreat_step_m})
    deps.log("IK_REQUEST", state=STATE_RETREAT, delta_z_m=retreat_step_m)
    deps.log("IK_RESULT", state=STATE_RETREAT, result=result, gate_ok=ok, gate_reason=reason)
    if not ok:
        # A failed retreat is not a placement failure -- the object is
        # already safely placed. Log and stop here rather than abort the
        # (already-successful) outcome.
        deps.log("RETREAT_SKIPPED", state=STATE_RETREAT, reason=reason)
        return STATE_DONE

    overrides = {n: result["joint_deg"][n] for n in ACTIVE_LIFT_JOINTS}
    measured = deps.move_arm(overrides, move_duration_s, 0.0)
    ctx.last_pose = measured
    _log_pass(deps, STATE_RETREAT, measured=measured)
    return STATE_DONE


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_hybrid_pick_place(
    deps: PickPlaceDeps,
    grasp_pose_deg: dict = None,
    gripper_latch: float = GRIPPER_LATCH,
    gripper_open: float = GRIPPER_OPEN,
    verification_lift_m: float = DEFAULT_VERIFICATION_LIFT_M,
    clearance_step_m: float = DEFAULT_CLEARANCE_STEP_M,
    max_clearance_steps: int = DEFAULT_MAX_CLEARANCE_STEPS,
    max_place_steps: int = DEFAULT_MAX_PLACE_STEPS,
    retreat_step_m: float = DEFAULT_RETREAT_STEP_M,
    move_duration_s: float = DEFAULT_MOVE_DURATION_S,
    gripper_move_duration_s: float = DEFAULT_GRIPPER_MOVE_DURATION_S,
    gripper_hold_s: float = DEFAULT_GRIPPER_HOLD_S,
    base_vx_mps: float = DEFAULT_BASE_VX_MPS,
    base_move_duration_s: float = DEFAULT_BASE_MOVE_DURATION_S,
) -> PickPlaceResult:
    """Run the full GRASP_SETUP -> GRIPPER_LATCH -> CARTESIAN_CLEARANCE ->
    MOBILE_APPROACH -> CARTESIAN_FORWARD_DOWN_PLACEMENT -> RELEASE ->
    RETREAT -> DONE chain, or stop at ABORTED with a specific reason. Every
    Cartesian step is solved fresh from deps.read_pose() and re-measured
    after actuation; every physical-judgment gate (CONFIRM GRASP, CONFIRM
    CLEARANCE, CONFIRM PLACEMENT, CONFIRM RELEASE) is a line answered via
    deps.confirm(), never inferred from sensors."""
    grasp_pose_deg = dict(KNOWN_GOOD_GRASP_POSE_DEG if grasp_pose_deg is None else grasp_pose_deg)
    ctx = _Ctx()
    events: list = []
    caller_log = deps.log

    def _record(event, **fields):
        events.append({"event": event, **fields})
        caller_log(event, **fields)

    # A shallow copy with logging wrapped so every event both reaches the
    # caller's own logger AND is collected into PickPlaceResult.events --
    # never mutates the caller's deps object.
    deps = PickPlaceDeps(
        read_pose=deps.read_pose,
        read_base=deps.read_base,
        move_arm=deps.move_arm,
        move_base_sustained=deps.move_base_sustained,
        confirm=deps.confirm,
        log=_record,
        capability_available=deps.capability_available,
        wait_for_capability_change=deps.wait_for_capability_change,
    )

    state = STATE_PRECHECK
    clearance_step_count = 0
    place_step_count = 0

    while state not in (STATE_DONE, STATE_ABORTED):
        if state == STATE_PRECHECK:
            state = _state_precheck(deps, ctx)
        elif state == STATE_GRASP_POSITION:
            state = _state_grasp_position(deps, ctx, grasp_pose_deg, move_duration_s)
        elif state == STATE_GRASP_CLOSE:
            state = _state_grasp_close(deps, ctx, gripper_latch, gripper_move_duration_s, gripper_hold_s)
        elif state == STATE_GRASP_VERIFY:
            state = _state_grasp_verify(deps, ctx, verification_lift_m, move_duration_s)
        elif state == STATE_CLEARANCE_LIFT:
            clearance_step_count += 1
            state = _state_clearance_lift(deps, ctx, clearance_step_m, move_duration_s, clearance_step_count)
        elif state == STATE_CLEARANCE_VERIFY:
            state = _state_clearance_verify(deps, ctx, clearance_step_count, max_clearance_steps)
        elif state == STATE_MOBILE_READY:
            state = _state_mobile_ready(deps, ctx)
        elif state == STATE_WAITING_FOR_CAPABILITY:
            state = _state_waiting_for_capability(deps, ctx)
        elif state == STATE_MOBILE_APPROACH:
            state = _state_mobile_approach(deps, ctx, base_vx_mps, base_move_duration_s)
        elif state == STATE_BASE_STOP_VERIFY:
            state = _state_base_stop_verify(deps, ctx)
        elif state == STATE_PLACE_APPROACH:
            place_step_count += 1
            state = _state_place_approach(deps, ctx, move_duration_s, place_step_count, max_place_steps)
        elif state == STATE_PLACE_READY:
            state = _state_place_ready(deps, ctx, place_step_count, max_place_steps)
        elif state == STATE_RELEASE:
            state = _state_release(deps, ctx, gripper_open, gripper_move_duration_s, gripper_hold_s)
        elif state == STATE_RETREAT:
            state = _state_retreat(deps, ctx, retreat_step_m, move_duration_s)
        else:
            raise AssertionError(f"unknown state {state!r}")

    if state == STATE_DONE:
        deps.log("DONE")

    return PickPlaceResult(
        final_state=state,
        object_secured=ctx.object_secured,
        end_effector_clearance_confirmed=ctx.end_effector_clearance_confirmed,
        placement_ready=ctx.placement_ready,
        arm_frozen=ctx.arm_frozen,
        base_stationary=ctx.base_stationary,
        fresh_observation_available=ctx.fresh_observation_available,
        final_pose=ctx.last_pose,
        abort_reason=ctx.abort_reason,
        events=events,
    )
