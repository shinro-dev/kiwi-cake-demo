# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The GRASP stage of the single-cube cycle: APPROACH, STRADDLING, GRASP_CLOSE and its bounded retries, OBJECT_SECURED.

Called by run_single_cube_cycle.Cycle.run_cycle after ALIGN, with ctx (the Cycle) and ctl (the controller's namespace,
stage_policy.ControllerView); stage_policy's module docstring says why a stage never imports the controller. Every arm
move goes through ctx.run_pose and every gripper move through ctx.grip, exactly as run_cycle made them; the geometry gates
(cartesian_step_pose, proven_grasp_pose) and the gripper predicate (closed_on_something) stay in the controller and are read
through ctl.

THE WORDS OF THIS STAGE (docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2.6 and track G). With none of them the
stage is the proven one. Each word opens one behaviour, and none is qualified:

  near_grasp  the grasp target is the measured block plus the reach lead, clamped to [NEAR_GRASP_MIN_FORWARD_MM, 60] mm
              around the proven grasp radius instead of [0, 60]; a target nearer than the proven radius is reached through
              near_grasp_pose (the gates of proven_grasp_pose with the word's lower bound); a clamp of more than
              APPROACH_CLAMP_REFUSAL_MM is the refusal approach_cube_beyond_grasp_range, before the arm moves.
  grasp_measured_retry
              after an empty close: reopen, lift open to the ALIGN observation height, look (one stationary re-capture
              when the lifted view has no block), re-run the ALIGN decision (policy_align.alignment_decision, at most
              RETRY_TRIMS_MAX pan trims; for a mission target each look first held to ALIGN's one-cube-in-view rule, whose
              refusals it raises) and aim at the new estimate, never within EMPTY_RADIUS_EXCLUSION_MM of a radius that
              closed empty; when the estimate falls there, the side the reopen frame's far-edge row shows decides. Three
              closes, as with no word. After the last one the arm lifts open and looks once more, so grasp_missed leaves
              nothing held, the jaws open above the block and its last estimate recorded, for the mission's hold and retry
              (the in-run re-plan of the pick is the runtime's, section 2.2). A retry's approach the geometry gates refuse is
              grasp_retry_approach_refused, nothing held, the jaws open.
  latch_midpoint
              a close held something above the midpoint of the recorded classes (empties at most
              LATCH_EMPTY_MAX_RECORDED, holds at least LATCH_HELD_MIN_RECORDED) instead of GRIPPER_LATCH +
              GRIPPER_EMPTY_LATCH_MARGIN; the controller's closed_on_something and its margin are untouched (open ruling 2).
  grasp_recover
              a failed retention with the block still between the jaws (the CONFIRM frame's far-edge row in the held band
              AND the CONFIRM pose's gripper readback still held): lower it, holding, to the height it was closed at through
              the unchanged step gate, open, lift open to the observation height, and end
              retention_not_demonstrated_set_down with nothing held, instead of holding the block up with the gripper
              closed. A lowering the gate refuses is grasp_set_down_refused, the block still held; a refused open lift after
              it is grasp_set_down_lift_refused, nothing held. Otherwise the proven
              hold, retention_not_demonstrated, its sentence saying why nothing was set down.
"""
import math
from dataclasses import dataclass

import policy_align
import stage_policy

# The words of this stage (stage_policy, THE WORDS; the module docstring says what each opens). A word joins QUALIFIED only in
# the commit that adds its docs/autonomous-demo/qualification-<word>-<date>.md record.
NEAR_GRASP, MEASURED_RETRY, LATCH_MIDPOINT, GRASP_RECOVER = "near_grasp", "grasp_measured_retry", "latch_midpoint", "grasp_recover"
WORDS = (NEAR_GRASP, MEASURED_RETRY, LATCH_MIDPOINT, GRASP_RECOVER)
QUALIFIED = frozenset()
# The predicates the GRASP stage decides.
POLICY = stage_policy.StagePolicy("grasp", __file__, ("straddling", "closed_on_something", "object_secured", "straddle_far_edge_row", "gripper_opened", "grasp_target_within_range",
                                                      "cube_seen_after_empty_close"),
                                  WORDS, QUALIFIED)

# near_grasp: the nearest grasp target, in mm from the proven grasp radius. The proven gate accepts the grasp geometry
# shifted inward to -40 mm offline (the grasp review of 2026-09-26); -25 covers the near-block refusal's -14 mm plus about
# 10 mm of ALIGN noise. No guard models the chassis: the word's no-block check comes first (open ruling 3).
NEAR_GRASP_MIN_FORWARD_MM = -25.0
APPROACH_CLAMP_REFUSAL_MM = 5.0  # under near_grasp a grasp target clamped by more than this is refused, never closed on
# grasp_measured_retry
EMPTY_RADIUS_EXCLUSION_MM = 3.0  # never close again within this of a radius that closed empty
ZONE_EDGE_TOLERANCE_MM = 1e-9  # the rounding of radius +/- EMPTY_RADIUS_EXCLUSION_MM (2.9999999999999982 across a power of two)
RETRY_OBSERVE_UP_MM = 43.0  # the lift before a look: ALIGN's lowest non-contact clearance (policy_align's descent, 0.043 m)
RETRY_OBSERVE_MIN_UP_MM = 35.0  # no look below this: the recorded unclipped views at the grasp's radius were at +37.3..+42.8 mm
RETRY_LIFT_STEP_MM = 25.0
RETRY_TRIMS_MAX = 2
# latch_midpoint: the gripper readbacks after a close, recorded (2026-09-20 and 2026-09-26): 11 empty, 10 held; one readback
# step is about 0.069, and the controller's threshold (66.8 + 0.6) sits 0.043 above the worst empty.
LATCH_EMPTY_MAX_RECORDED = 67.357
LATCH_HELD_MIN_RECORDED = 67.909

# The far-edge row (bbox y0) of a block between the open jaws at grasp height. Held or straddled blocks: 351..397 px (n=10,
# 2026-09-20 and 2026-09-26); the 19:19 miss: 524 at the straddle, 560..616 on the reopens, the block nearer. INFERRED band,
# evidence only: no decision reads it with no word.
STRADDLE_FAR_EDGE_ROW_BAND_PX = (340.0, 420.0)


def min_grasp_forward_mm(words=()) -> float:
    """The nearest grasp target APPROACH plans, in mm beyond the proven grasp radius, under this run's GRASP words (`words`,
    the stage's share of the run's words): with none of them 0.0, the proven grasp radius, where proven_grasp_pose's forward
    bound starts; under near_grasp NEAR_GRASP_MIN_FORWARD_MM. APPROACH (run_grasp) clamps its grasp target to it: it never
    plans a grasp nearer than this."""
    return NEAR_GRASP_MIN_FORWARD_MM if NEAR_GRASP in tuple(words or ()) else 0.0


def grasp_target_mm(estimate_mm: float, empty_radii_mm=(), bounds=(0.0, 60.0), lead_mm: float = 14.0, prefer=None, exclusion_mm: float = EMPTY_RADIUS_EXCLUSION_MM) -> dict:
    """The grasp target for a block measured at `estimate_mm` (tool out plus ALIGN radial, mm from the proven grasp radius):
    the estimate plus the reach lead, clamped to `bounds`, and never nearer than `exclusion_mm` to a radius in
    `empty_radii_mm` (where a close already found nothing). A target inside such a zone moves to the zone's edge on the side
    `prefer` names (-1 inward, +1 outward), or with no preference on the side the wanted value lies; the other side when
    that one leaves the bounds.

    Returns wanted_mm (estimate plus lead), target_mm (None when no radius in the bounds is left untried), clamped_by_mm
    (how far the bounds moved the wanted value) and moved_mm (how far the exclusion moved it after that). With no empty
    radius target_mm is min(max(estimate + lead, low), high), APPROACH's own expression."""
    low, high = float(bounds[0]), float(bounds[1])
    wanted = estimate_mm + lead_mm
    clamped = min(max(wanted, low), high)
    empties = [float(radius) for radius in empty_radii_mm]

    def blocking(value):  # a value moved to a zone's edge has left the zone: radius +/- exclusion can round back inside it
        return [radius for radius in empties if abs(value - radius) < exclusion_mm - ZONE_EDGE_TOLERANCE_MM]

    def edge(value, side):
        for _ in range(len(empties) + 1):  # each move leaves one zone; zones that overlap are left one after the other
            inside = blocking(value)
            if not inside:
                return value if low <= value <= high else None
            value = (min(inside) - exclusion_mm) if side < 0 else (max(inside) + exclusion_mm)
        return None

    target = clamped
    inside = blocking(clamped)
    if inside:
        first = prefer if prefer in (-1, 1) else (-1 if wanted < sum(inside) / len(inside) else 1)
        target = edge(clamped, first)
        if target is None:
            target = edge(clamped, -first)
    return {"wanted_mm": wanted, "target_mm": target, "clamped_by_mm": abs(clamped - wanted), "moved_mm": None if target is None else target - clamped}


def near_grasp_pose(ctl, measured: dict, forward_m: float, dz_m: float, words=()):
    """The grasp geometry shifted forward_m (m; negative is nearer than the proven radius) and dz_m up. At or beyond the proven
    radius it IS ctl.proven_grasp_pose. Nearer, and only down to min_grasp_forward_mm(words): the construction and the gates of
    proven_grasp_pose (the proven solver and gate through cli._proven_grasp_shifted, the tool travel bound against the
    measured pose, no sample of the joint-linear path below the proven grasp tool height), proven_grasp_pose itself unchanged.
    With no word nothing nearer is accepted. Raises ctl.ScanRefused with the reason."""
    if forward_m >= 0.0:
        return ctl.proven_grasp_pose(measured, forward_m, dz_m)
    nearest_m = min_grasp_forward_mm(words) / 1000.0
    if not (math.isfinite(forward_m) and math.isfinite(dz_m) and 0.0 <= dz_m <= ctl.STEP_MAX_DZ_M and nearest_m - 1e-9 <= forward_m < 0.0):
        raise ctl.ScanRefused(f"the grasp geometry may be shifted {nearest_m * 1000:+.0f}..{ctl.GRASP_POSE_MAX_FORWARD_M * 1000:.0f} mm and 0..{ctl.STEP_MAX_DZ_M * 1000:.0f} mm up under "
                              f"this run's words, got {forward_m * 1000:+.1f} / {dz_m * 1000:+.1f}")
    target = {"shoulder_pan": float(measured["shoulder_pan"]), **ctl.cli._proven_grasp_shifted(forward_m, dz_m)}
    before, after = ctl.tool_state(measured), ctl.tool_state(target)
    travel = math.hypot(after["out_mm"] - before["out_mm"], after["up_mm"] - before["up_mm"]) / 1000.0
    if travel > ctl.GRASP_POSE_MAX_TOOL_TRAVEL_M:
        raise ctl.ScanRefused(f"the tool would travel {travel * 1000:.1f} mm to this grasp pose (limit {ctl.GRASP_POSE_MAX_TOOL_TRAVEL_M * 1000:.0f}): approach in bounded steps first")
    report = ctl.path_report(measured, target)
    if report["lowest_link_m"] < ctl.GRASP_TOOL_Z_M - ctl.LOWEST_TARGET_BELOW_GRASP_M:
        raise ctl.ScanRefused(f"the path to this grasp pose dips to {report['lowest_link_m']:+.3f} m, below the proven grasp tool height {ctl.GRASP_TOOL_Z_M:+.3f} m")
    return ctl.cli._pose(f"near-grasp-pose-fwd{forward_m * 1000:+.0f}-dz{dz_m * 1000:+.0f}", "NEAR", target), report


@dataclass(frozen=True)
class GraspOutcome:
    """What the stages after the grasp rest on: `view` the arm at the grasp (its tool radius is the grasp radius), `closed`
    the gripper close that held, `third` the last verification lift, the pose the carry starts from."""
    view: dict
    closed: dict
    third: dict


def latch_threshold(ctl, words=()) -> float:
    """The gripper readback above which a close held something: GRIPPER_LATCH + GRIPPER_EMPTY_LATCH_MARGIN, or under
    latch_midpoint the midpoint of the recorded empty and held classes."""
    if LATCH_MIDPOINT in tuple(words or ()):
        return (LATCH_EMPTY_MAX_RECORDED + LATCH_HELD_MIN_RECORDED) / 2.0
    return ctl.GRIPPER_LATCH + ctl.GRIPPER_EMPTY_LATCH_MARGIN


def close_held(ctl, gripper_after_latch: float, words=()) -> bool:
    """The controller's closed_on_something; under latch_midpoint the same test at latch_threshold."""
    if LATCH_MIDPOINT not in tuple(words or ()):
        return ctl.closed_on_something(gripper_after_latch)
    return math.isfinite(gripper_after_latch) and gripper_after_latch > latch_threshold(ctl, words) and gripper_after_latch <= ctl.GRIPPER_HOLDING_BAND[1]


def far_edge_side(record) -> tuple:
    """(row, side) of a cube record seen between the open jaws at grasp height: the far-edge row (bbox y0, px) and where the
    block lies against STRADDLE_FAR_EDGE_ROW_BAND_PX: "nearer" (below the band in the image), "farther" (above it) or
    "in_band". (None, None) with no detected box or a box cut by the top border (its far edge is not seen)."""
    cube = (record or {}).get("cube") or {}
    if not cube.get("detected") or not cube.get("bbox_xyxy") or (cube.get("clipped") or {}).get("top"):
        return None, None
    row = float(cube["bbox_xyxy"][1])
    low, high = STRADDLE_FAR_EDGE_ROW_BAND_PX
    return row, ("nearer" if row > high else "farther" if row < low else "in_band")


def _last_record(result):
    records = (result or {}).get("perception") or []
    return records[-1] if records else None


# --- the evidence (events only: no state, line, command or decision reads it) -----------------------------------------
def _state_far_edge(ctx, view) -> tuple:
    """The straddle or reopen frame's far-edge row, as the predicate straddle_far_edge_row."""
    row, side = far_edge_side(_last_record(view))
    low, high = STRADDLE_FAR_EDGE_ROW_BAND_PX
    ctx.events.predicate("straddle_far_edge_row", None if side is None else side == "in_band", measured=row, threshold=high, unit="px", frames=view, scalars=("far_edge_row_px",),
                          measurements={"far_edge_row_px": row, "band_min_px": low, "band_max_px": high, "block_nearer": None if side is None else side == "nearer",
                                        "block_farther": None if side is None else side == "farther"})
    return row, side


@POLICY.decides
def _state_opened(ctx, ctl, reopened) -> bool:
    """gripper_opened, from the readback the reopen consumed."""
    reading = reopened.get("gripper") if isinstance(reopened, dict) else None
    opened = None if reading is None else abs(float(reading) - ctl.GRIPPER_OPEN) <= ctl.GRIPPER_OPEN_TOLERANCE
    ctx.events.predicate("gripper_opened", opened, measured=reading, threshold=ctl.GRIPPER_OPEN_TOLERANCE, unit="gripper_units", scalars=("gripper_readback",), gripper_read=reading,
                          measurements={"gripper": reading, "gripper_open": ctl.GRIPPER_OPEN, "gripper_open_tolerance": ctl.GRIPPER_OPEN_TOLERANCE})
    return bool(opened)


# --- the moves ------------------------------------------------------------------------------------------------------
@POLICY.decides
def _approach(ctx, ctl, view, grasp_forward_mm, words, descent_label, grasp_label):
    """APPROACH's bounded steps down to the grasp geometry at grasp_forward_mm, then the grasp geometry itself."""
    for _ in range(6):  # bounded steps until the proven grasp geometry is ONE bounded move away. Each step also tracks the grasp
        # radius: a pure descent drifts the tool inward (the elbow lags small commands), seen as 31.6 mm on the first whole run.
        gap_out, gap_up = grasp_forward_mm - view["tool"]["out_mm"], view["tool"]["up_mm"] - 2.0
        if math.hypot(gap_out, gap_up) <= 25.0 and view["tool"]["up_mm"] <= 20.0:
            break
        forward_m, dz_m = max(-0.020, min(0.020, gap_out / 1000.0)), -min(0.018, max(gap_up - 6.0, 0.0) / 1000.0)
        if abs(forward_m) < 0.001 and abs(dz_m) < 0.001:
            break
        pose, _report = ctl.cartesian_step_pose(view["measured"], forward_m, dz_m, False)
        view = ctx.run_pose(pose, False, ctl.CONTACT_FLOOR_M, label=descent_label)
    if NEAR_GRASP in words:
        pose, _report = near_grasp_pose(ctl, view["measured"], grasp_forward_mm / 1000.0, 0.002, words)
    else:
        pose, _report = ctl.proven_grasp_pose(view["measured"], grasp_forward_mm / 1000.0, 0.002)
    return ctx.run_pose(pose, False, ctl.CONTACT_FLOOR_M, label=grasp_label)


@POLICY.decides
def _straddle(ctx, ctl, view) -> tuple:
    """STRADDLING as the CODE decides it: the measured tool height alone, a scalar. The straddle frame's far-edge row is
    stated beside it as evidence and decides nothing."""
    ctx.events.predicate("straddling", not view["tool"]["up_mm"] > 8.0, measured=view["tool"]["up_mm"], threshold=8.0, unit="mm", scalars=("tool_up_mm_vs_proven_grasp",), stamp_of=view["measured"])
    far_edge = _state_far_edge(ctx, view)
    if view["tool"]["up_mm"] > 8.0:
        raise ctx.failed("straddling_not_reached", f"STRADDLING not reached: the measured tool is still {view['tool']['up_mm']:+.1f} mm above the grasp height",
                          evidence=view, tool_up_mm=view["tool"]["up_mm"], straddling_max_up_mm=8.0)
    ctx.state("STRADDLING", tool=ctl.fmt_tool(view["tool"]))
    return far_edge


@POLICY.decides
def _refuse_clamp(ctx, ctl, aim, estimate_mm, bounds, view) -> None:
    """A grasp target the bounds moved by more than APPROACH_CLAMP_REFUSAL_MM is refused before the arm moves toward it,
    with the distances it rests on: nothing is closed short of the block. The arm holds where it is, nothing gripped."""
    within = aim["clamped_by_mm"] <= APPROACH_CLAMP_REFUSAL_MM
    ctx.events.predicate("grasp_target_within_range", within, measured=aim["clamped_by_mm"], threshold=APPROACH_CLAMP_REFUSAL_MM, unit="mm", frames=view, scalars=("grasp_target_clamped_by_mm",),
                          measurements={"block_estimate_mm": estimate_mm, "wanted_mm": aim["wanted_mm"], "nearest_mm": bounds[0], "farthest_mm": bounds[1]})
    if not within:
        side = "nearer" if aim["wanted_mm"] < bounds[0] else "farther"
        raise ctx.failed("approach_cube_beyond_grasp_range", f"APPROACH: the block was measured at {estimate_mm:+.1f} mm, so the grasp wants {aim['wanted_mm']:+.1f} mm, {aim['clamped_by_mm']:.1f} mm "
                          f"{side} than this run's grasp range {bounds[0]:+.0f}..{bounds[1]:+.0f} mm allows; nothing is closed short of the block, nothing was picked up",
                          evidence=view, block_estimate_mm=round(estimate_mm, 1), wanted_mm=round(aim["wanted_mm"], 1), nearest_mm=bounds[0], farthest_mm=bounds[1],
                          clamped_by_mm=round(aim["clamped_by_mm"], 1))


@POLICY.decides
def _close(ctx, ctl, words, attempt):
    closed = ctx.grip(ctl.GRIPPER_LATCH, "latch")
    held = close_held(ctl, closed["gripper"], words)
    ctx.state("GRASP_CLOSE", attempt=attempt, gripper=round(closed["gripper"], 3), closed_on_something=held, tool=ctl.fmt_tool(closed["tool"]))
    ctx.events.predicate("closed_on_something", held, measured=closed["gripper"], threshold=latch_threshold(ctl, words), unit="gripper_units",
                          scalars=("gripper_readback", "gripper_latch_value", "gripper_empty_latch_margin", "gripper_holding_band_max"), gripper_read=closed["gripper"])
    return closed, held


# --- the stage ------------------------------------------------------------------------------------------------------
@POLICY.decides
def run_grasp(ctx, ctl, summary: dict, view: dict, decision: dict) -> GraspOutcome:
    """From the aligned non-contact view (`view`) and ALIGN's last decision (`decision`: its radial estimate places the
    grasp): APPROACH in bounded steps to the proven grasp geometry, STRADDLING on the measured tool height, at most three
    closes with their bounded retries, then the three verification lifts and OBJECT_SECURED on the retention score. Sets
    summary["object_secured"]. Every failure is ctx.failed, the arm holding where it stopped."""
    words = POLICY.run_words(ctl)
    ctx.state("APPROACH", radial_mm=round(decision["radial_mm"], 1), evidence=view)
    bounds = (min_grasp_forward_mm(words), ctl.GRASP_POSE_MAX_FORWARD_M * 1000.0)
    if NEAR_GRASP in words:  # where the block was measured, down to the word's bound; a clamp of more than 5 mm is refused
        estimate = view["tool"]["out_mm"] + decision["radial_mm"]
        aim = grasp_target_mm(estimate, (), bounds, ctl.APPROACH_REACH_LEAD_MM)
        _refuse_clamp(ctx, ctl, aim, estimate, bounds, view)
        grasp_forward_mm = aim["target_mm"]
    else:
        grasp_forward_mm = min(max(view["tool"]["out_mm"] + decision["radial_mm"] + ctl.APPROACH_REACH_LEAD_MM, bounds[0]), bounds[1])
    view = _approach(ctx, ctl, view, grasp_forward_mm, words, "APPROACH descent", "APPROACH proven grasp geometry")
    _straddle(ctx, ctl, view)
    if MEASURED_RETRY in words:
        closed, view = _close_measured(ctx, ctl, words, bounds, view)
    else:
        closed, view = _close_advancing(ctx, ctl, words, view)
    return _secure(ctx, ctl, words, summary, view, closed)


@POLICY.decides
def _close_advancing(ctx, ctl, words, view):
    """The proven retry: after an empty close, reopen and advance 14 mm outward from the reopened tool; three closes."""
    for advance in range(3):  # bounded recovery: an EMPTY close at grasp height, after ALIGN, means the cube lies beyond the jaw tips
        closed, held = _close(ctx, ctl, words, advance)
        if held:
            break
        reopened = ctx.grip(ctl.GRIPPER_OPEN, "open")
        _state_opened(ctx, ctl, reopened)
        if advance == 2:
            _missed(ctx, ctl, closed, f"GRASP missed three times: the jaw reached {closed['gripper']:.2f} (empty-latch value {ctl.GRIPPER_LATCH}); gripper re-opened, arm holds",
                    (closed, reopened), closes=advance + 1)
        _state_far_edge(ctx, reopened)
        forward_mm = min(reopened["tool"]["out_mm"] + 10.0 + 4.0, ctl.GRASP_POSE_MAX_FORWARD_M * 1000.0)
        ctx.state("GRASP_RETRY_ADVANCE", to_forward_mm=round(forward_mm, 1))
        ctx.events.automatic_retry("grasp_retry_advance")
        view = ctx.run_pose(ctl.proven_grasp_pose(reopened["measured"], forward_mm / 1000.0, 0.002)[0], False, ctl.CONTACT_FLOOR_M, label="APPROACH advance after an empty close")
    return closed, view


@POLICY.decides
def _lift_open(ctx, ctl, view, label):
    """Open jaws straight up to RETRY_OBSERVE_UP_MM in bounded steps through the unchanged step gate: (view, None), or (the
    view it stopped at, why) for a lift refused or short of RETRY_OBSERVE_MIN_UP_MM. The caller names the refusal."""
    refused = None
    for _ in range(4):
        up = view["tool"]["up_mm"]
        if up >= RETRY_OBSERVE_UP_MM:
            break
        try:
            pose, _report = ctl.cartesian_step_pose(view["measured"], 0.0, min(RETRY_LIFT_STEP_MM, RETRY_OBSERVE_UP_MM - up + 1.0) / 1000.0, False)
        except ctl.ScanRefused as refusal:
            refused = f"is refused at {up:+.1f} mm above the grasp height: {refusal}"
            break
        view = ctx.run_pose(pose, False, ctl.CONTACT_FLOOR_M, label=label)
    if refused is None and view["tool"]["up_mm"] < RETRY_OBSERVE_MIN_UP_MM:
        refused = f"reached only {view['tool']['up_mm']:+.1f} mm above the grasp height, below the {RETRY_OBSERVE_MIN_UP_MM:.0f} mm a look needs"
    return view, refused


@POLICY.decides
def _lift_to_look(ctx, ctl, view):
    """The retry's open lift to look again (_lift_open); a refused lift is grasp_retry_lift_refused."""
    view, refused = _lift_open(ctx, ctl, view, "GRASP_RETRY lift open")
    if refused is not None:
        raise ctx.failed("grasp_retry_lift_refused", f"GRASP: the open lift to look again {refused}; the jaws are open, the arm holds", evidence=view,
                          tool_up_mm=round(view["tool"]["up_mm"], 1), observe_up_mm=RETRY_OBSERVE_UP_MM)
    return view


def _look(ctx, view, closes):
    """The block in the lifted view, else in ONE stationary re-capture: (cube record or None, the view it rests on)."""
    cube = ctx._cube(view)
    if cube is None:
        view = ctx.observe(False)
        cube = ctx._cube(view)
    ctx.events.predicate("cube_seen_after_empty_close", cube is not None, frames=view, scalars=("cube_detected",), measurements={"closes": closes})
    return cube, view


def _lost(ctx, view, closes, where):
    raise ctx.failed("grasp_retry_cube_lost", f"GRASP: the block is not seen {where}, nor on a second look; nothing is held, the jaws are open above the floor",
                      evidence=view, closes=closes, tool_up_mm=round(view["tool"]["up_mm"], 1))


@POLICY.decides
def _missed(ctx, ctl, closed, message, evidence, **values):
    """grasp_missed: three closes found nothing. The sentence says where the jaws are; the values name the last reading."""
    raise ctx.failed("grasp_missed", message, evidence=evidence, gripper=closed["gripper"], gripper_latch=ctl.GRIPPER_LATCH, empty_latch_margin=ctl.GRIPPER_EMPTY_LATCH_MARGIN, **values)


@POLICY.decides
def _realign(ctx, ctl, view, cube, closes):
    """ALIGN's decision on the lifted view (policy_align.alignment_decision under this run's ALIGN words), with at most
    RETRY_TRIMS_MAX pan trims, each held to the return bound before it moves: (decision, view, cube). For a mission target
    every look is first held to ALIGN's mission rule (ctx._mission_one_cube_in_view: its cube the one cube in view), so the
    arm never re-aims at a view that rule refuses."""
    align_words = policy_align.POLICY.run_words(ctl)
    for trims in range(RETRY_TRIMS_MAX + 1):
        if ctx.target is not None:
            ctx._mission_one_cube_in_view(cube, view, closes)
        decision = policy_align.alignment_decision(ctl, cube["cube"]["bbox_xyxy"], view["tool"]["pitch_deg"], align_words)
        if decision["aligned"] or trims == RETRY_TRIMS_MAX:
            break
        trim = ctl.lateral_trim_deg(decision["lateral_mm"], ctl.GRASP_TOOL_RANGE_M + (view["tool"]["out_mm"] + decision["radial_mm"]) / 1000.0)
        ctx._refuse_trim_beyond_return_bound(view, trim)  # before the trim moves: the pan the arm holds can still plan the EXIT
        view = ctx.run_pose(ctl.pan_step_pose(view["measured"], trim, False), False, ctl.CONTACT_FLOOR_M, pan_allowance=ctl.STEP_MAX_PAN_DEG, label="GRASP_RETRY pan trim")
        ctx.events.automatic_retry("grasp_retry_pan_trim")
        cube, view = _look(ctx, view, closes)
        if cube is None:
            _lost(ctx, view, closes, "after a pan trim to look again")
    if not decision["aligned"]:
        raise ctx.failed("grasp_retry_not_aligned", f"GRASP: the block is still {decision['lateral_mm']:+.1f} mm off the capture window after {RETRY_TRIMS_MAX} trims to look again; "
                          "nothing is held, the jaws are open above the floor", evidence=view, lateral_mm=round(decision["lateral_mm"], 1), radial_mm=round(decision["radial_mm"], 1),
                          capture_window_min_mm=ctl.CAPTURE_WINDOW_MM[0], capture_window_max_mm=ctl.CAPTURE_WINDOW_MM[1], trims=RETRY_TRIMS_MAX)
    return decision, view, cube


@POLICY.decides
def _refuse_unopened(ctx, ctl, reopened, where):
    reading = reopened.get("gripper") if isinstance(reopened, dict) else None
    raise ctx.failed("gripper_did_not_open", f"GRASP: the gripper did not open {where}: it reads {reading}, not {ctl.GRIPPER_OPEN} +/- {ctl.GRIPPER_OPEN_TOLERANCE}; the arm holds",
                      evidence=reopened, gripper=reading, gripper_open=ctl.GRIPPER_OPEN, gripper_open_tolerance=ctl.GRIPPER_OPEN_TOLERANCE)


@POLICY.decides
def _close_measured(ctx, ctl, words, bounds, view):
    """grasp_measured_retry (the module docstring): three closes, each retry aimed from a fresh look."""
    empties = []
    for attempt in range(3):
        closed, held = _close(ctx, ctl, words, attempt)
        if held:
            return closed, view
        empties.append(float(closed["tool"]["out_mm"]))
        reopened = ctx.grip(ctl.GRIPPER_OPEN, "open")
        if not _state_opened(ctx, ctl, reopened):
            _refuse_unopened(ctx, ctl, reopened, "after an empty close")
        _row, side = _state_far_edge(ctx, reopened)
        lifted = _lift_to_look(ctx, ctl, reopened)
        cube, lifted = _look(ctx, lifted, attempt + 1)
        empty_text = ", ".join(f"{radius:+.1f}" for radius in empties)
        if attempt == 2:
            estimate = None
            if cube is not None:
                last = policy_align.alignment_decision(ctl, cube["cube"]["bbox_xyxy"], lifted["tool"]["pitch_deg"], policy_align.POLICY.run_words(ctl))
                estimate = round(lifted["tool"]["out_mm"] + last["radial_mm"], 1)
            _missed(ctx, ctl, closed, f"GRASP missed three times: the jaw reached {closed['gripper']:.2f} (empty-latch value {ctl.GRIPPER_LATCH}) at {empty_text} mm; the jaws are open "
                    f"and lifted {lifted['tool']['up_mm']:+.1f} mm above the grasp height, nothing is held", (closed, reopened, lifted), closes=attempt + 1, block_estimate_mm=estimate,
                    tool_up_mm=round(lifted["tool"]["up_mm"], 1))
        if cube is None:
            _lost(ctx, lifted, attempt + 1, "from the observation height after an empty close")
        decision, lifted, cube = _realign(ctx, ctl, lifted, cube, attempt + 1)
        estimate = lifted["tool"]["out_mm"] + decision["radial_mm"]
        ctx.state("GRASP_REOBSERVE", attempt=attempt, block_estimate_mm=round(estimate, 1), lateral_mm=round(decision["lateral_mm"], 1), radial_mm=round(decision["radial_mm"], 1),
                  tool=ctl.fmt_tool(lifted["tool"]), evidence=lifted)
        aim = grasp_target_mm(estimate, empties, bounds, ctl.APPROACH_REACH_LEAD_MM, prefer={"nearer": -1, "farther": 1}.get(side))
        _refuse_clamp(ctx, ctl, aim, estimate, bounds, lifted)
        if aim["target_mm"] is None:
            raise ctx.failed("grasp_retry_no_untried_radius", f"GRASP: the block is measured at {estimate:+.1f} mm and every grasp radius near it has closed empty ({empty_text} mm); "
                              "nothing is held, the jaws are open above the floor", evidence=lifted, block_estimate_mm=round(estimate, 1), closes=attempt + 1)
        direction = "inward" if aim["target_mm"] < empties[-1] else "outward"
        ctx.state("GRASP_RETRY", attempt=attempt + 1, direction=direction, block_estimate_mm=round(estimate, 1), last_close_mm=round(empties[-1], 1), to_forward_mm=round(aim["target_mm"], 1),
                  far_edge=side, observation_id=cube.get("observation_id"), evidence=lifted)
        ctx.events.automatic_retry("grasp_retry_measured")
        try:
            view = _approach(ctx, ctl, lifted, aim["target_mm"], words, "GRASP_RETRY descent", "GRASP_RETRY grasp geometry")
        except ctl.ScanRefused as refusal:
            raise ctx.failed("grasp_retry_approach_refused", f"GRASP: the approach to retry at {aim['target_mm']:+.1f} mm is refused ({refusal}); nothing is held, the jaws are open "
                              "and the arm holds where it stopped", evidence=lifted, block_estimate_mm=round(estimate, 1), to_forward_mm=round(aim["target_mm"], 1), closes=attempt + 1) from refusal
        _straddle(ctx, ctl, view)
    raise AssertionError("unreachable: the third close returns or raises")


@POLICY.decides
def _secure(ctx, ctl, words, summary, view, closed) -> GraspOutcome:
    """The three verification lifts and OBJECT_SECURED on the retention score between the two upper frames."""
    first = ctx.run_pose(ctl.cartesian_step_pose(closed["measured"], 0.0, 0.015, True)[0], True, ctl.CONTACT_FLOOR_M, label="VERIFICATION lift")
    second = ctx.run_pose(ctl.cartesian_step_pose(first["measured"], 0.0, 0.028, True)[0], True, ctl.CONTACT_FLOOR_M, label="CLEARANCE lift")
    third = ctx.run_pose(ctl.cartesian_step_pose(second["measured"], 0.0, 0.008, True)[0], True, ctl.CONTACT_FLOOR_M, label="CONFIRM lift")
    # scored between the two frames taken well ABOVE the floor: the first lift pair is weaker (the cube settles in the jaws: 0.50..0.63
    # on the recorded runs) than later held pairs (0.83..0.85); not-held pairs stayed below 0.41
    score = ctl.retention_score(ctx._frame(second), ctx._frame(third))
    summary["object_secured"] = score >= ctl.RETENTION_MIN_SCORE
    ctx.state("OBJECT_SECURED", value=summary["object_secured"], retention_score=round(score, 3), threshold=ctl.RETENTION_MIN_SCORE, gripper=round(closed["gripper"], 3), evidence=(second, third))
    ctx.events.predicate("object_secured", summary["object_secured"], measured=score, threshold=ctl.RETENTION_MIN_SCORE, frame_pair=(second, third), scalars=("retention_score",), empirical=("RETENTION_MIN_SCORE",))
    if not summary["object_secured"]:
        why, withheld = "", {}
        if GRASP_RECOVER in words:
            reason = _set_down_withheld(ctl, words, third)
            if reason is None:
                _set_down(ctx, ctl, closed, third, score)
            row, side = far_edge_side(_last_record(third))
            why = f"; the block is not set down ({reason})"
            withheld = {"confirm_gripper": (third.get("measured") or {}).get("gripper"), "far_edge_row_px": row, "far_edge_side": side}
        raise ctx.failed("retention_not_demonstrated", f"retention NOT demonstrated: the image between the jaws changed with height{why}; the arm holds with the gripper closed",
                          evidence=(second, third), retention_score=score, retention_min_score=ctl.RETENTION_MIN_SCORE, gripper=closed["gripper"], **withheld)
    return GraspOutcome(view=view, closed=closed, third=third)


def _set_down_withheld(ctl, words, third):
    """grasp_recover sets the block down only while it is still between the jaws: the CONFIRM frame shows its far edge in
    STRADDLE_FAR_EDGE_ROW_BAND_PX AND the CONFIRM pose's gripper readback still classes as held (close_held). None when both
    hold, else why not: a block seen elsewhere (commonly left below when the jaws rose without it) or jaws closed on to the
    empty latch are never lowered onto."""
    row, side = far_edge_side(_last_record(third))
    if side != "in_band":
        return "no block is seen in the CONFIRM frame" if side is None else f"the CONFIRM frame shows the block {side} than the jaws, its far edge at row {row:.0f}, not between them"
    reading = (third.get("measured") or {}).get("gripper")
    if reading is None or not close_held(ctl, float(reading), words):
        return f"the gripper reads {reading} at the CONFIRM pose, not a held block ({latch_threshold(ctl, words):.3f} or more)"
    return None


@POLICY.decides
def _set_down(ctx, ctl, closed, third, score):
    """grasp_recover (the module docstring): the block set down where it was closed on, the jaws opened and lifted open."""
    floor_up = max(float(closed["tool"]["up_mm"]), 0.0) + 1.0
    ctx.state("GRASP_SET_DOWN", retention_score=round(score, 3), from_up_mm=round(third["tool"]["up_mm"], 1), to_up_mm=round(floor_up, 1), evidence=third)
    view, refused = third, None
    for _ in range(4):
        up = view["tool"]["up_mm"]
        if up <= floor_up + 3.0:
            break
        try:
            pose, _report = ctl.cartesian_step_pose(view["measured"], 0.0, -min(RETRY_LIFT_STEP_MM, up - floor_up) / 1000.0, True)
        except ctl.ScanRefused as refusal:
            refused = str(refusal)
            break
        view = ctx.run_pose(pose, True, ctl.CONTACT_FLOOR_M, label="GRASP_SET_DOWN lower")
    if refused is not None or view["tool"]["up_mm"] > floor_up + 3.0:
        raise ctx.failed("grasp_set_down_refused", f"GRASP: the block cannot be lowered back to {floor_up:+.1f} mm to set it down ({refused or 'the bounded steps ran out'}); the arm holds it "
                          f"{view['tool']['up_mm']:+.1f} mm above the grasp height with the gripper closed", evidence=view, tool_up_mm=round(view["tool"]["up_mm"], 1),
                          set_down_up_mm=round(floor_up, 1), retention_score=score)
    reopened = ctx.grip(ctl.GRIPPER_OPEN, "open")
    if not _state_opened(ctx, ctl, reopened):
        _refuse_unopened(ctx, ctl, reopened, "to set the block down")
    lifted, refused = _lift_open(ctx, ctl, reopened, "GRASP_SET_DOWN lift open")
    if refused is not None:
        raise ctx.failed("grasp_set_down_lift_refused", f"GRASP: the block is set down and the jaws opened, but the open lift off it {refused}; nothing is held, the jaws are open, "
                          "the arm holds", evidence=lifted, tool_up_mm=round(lifted["tool"]["up_mm"], 1), observe_up_mm=RETRY_OBSERVE_UP_MM)
    raise ctx.failed("retention_not_demonstrated_set_down", f"retention NOT demonstrated (score {score:.3f}, {ctl.RETENTION_MIN_SCORE} required): the block was set down where it was "
                      f"closed on, and the jaws opened and lifted {lifted['tool']['up_mm']:+.1f} mm above it; nothing is held", evidence=(third, reopened, lifted), retention_score=score,
                      retention_min_score=ctl.RETENTION_MIN_SCORE, gripper=closed["gripper"], tool_up_mm=round(lifted["tool"]["up_mm"], 1))
