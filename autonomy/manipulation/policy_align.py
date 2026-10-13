# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The PREGRASP and ALIGN stage of the single-cube cycle: the coarse pregrasp, the pickup band (the near-block refusal),
PREGRASP, and ALIGN with its pan trims and its non-contact descent.

Called by run_single_cube_cycle.Cycle.run_cycle with ctx (the Cycle) and ctl (the controller's namespace,
stage_policy.ControllerView); stage_policy's module docstring says why a stage never imports the controller. SEARCH asks
this stage for the coarse pregrasp and for the pickup band before anything is picked up (coarse_pregrasp, refuse_pickup_band);
the controller's Cycle._refuse_pregrasp_inside_grasp_radius and Cycle._refuse_trim_beyond_return_bound are these functions.
alignment_decision is the alignment decision on one box at one tool pitch; run_pregrasp_align makes each of its ALIGN
decisions with it. With no word it is the controller's alignment_from_bbox, read through ctl so that a constant patched on
the controller (a replay's counterfactual) is the constant it decides with. The coarse map (coarse_pregrasp_from_low_pose, whose clamp line the
frozen-primitive test pins), lateral_trim_deg and the geometry gates stay in the controller and are read through ctl.
"""
import math
from dataclasses import dataclass

import policy_grasp
import stage_policy

# The words of this stage (stage_policy, THE WORDS). With none of them the stage is the proven one, byte for byte.
#   coarse_v2  the coarse radial map refitted on the recorded pairs (coarse_pregrasp), and the pickup band decided on it
#   align_v2   ALIGN as a bounded servo to a 2D band with the view kept at every height (run_pregrasp_align, ALIGN V2 below)
# A word joins QUALIFIED only in the commit that adds its docs/autonomous-demo/qualification-<word>-<date>.md record.
COARSE_V2, ALIGN_V2 = "coarse_v2", "align_v2"
WORDS = (COARSE_V2, ALIGN_V2)
QUALIFIED = frozenset()
# The predicates PREGRASP and ALIGN decide.
POLICY = stage_policy.StagePolicy("align", __file__, ("pregrasp_cube_within_reach", "trim_pan_within_return_bound", "cube_detected_in_the_pregrasp_view", "aligned", "align_view_margin"),
                                  WORDS, QUALIFIED)

# COARSE_V2: the low-pose contact row against the cube out ALIGN measured at the first pregrasp view (tool out plus radial),
# on the three recorded runs of 2026-09-26: row 696.0 -> -17.9 mm (m8c5f9e9), 556.5 -> 21.3 (mb3c1048), 483.8 -> 45.2
# (m2476137). The least-squares line through them, about the legacy reference row: 26.2 mm at row 545, 3.39 px per mm
# (the legacy map reads 11.4 mm there, at 2.7 px per mm, and read the three runs 26.6, 14.2 and 11.1 mm nearer than
# measured). Its residuals are 0.5, -1.4 and 1.0 mm. Three pairs: thin evidence, one pad, one light.
COARSE_V2_REF_ROW_PX = 545.0
COARSE_V2_OUT_AT_REF_MM = 26.2
COARSE_V2_PX_PER_RADIAL_MM = 3.39


def alignment_decision(ctl, bbox_xyxy, pitch_deg: float, words=()) -> dict:
    """The ALIGN decision on one box at one tool pitch, under this run's ALIGN words (`words`, the stage's share of the run's
    words): with none of them, the controller's alignment_from_bbox, unchanged. Returns at least lateral_mm, radial_mm and
    aligned (the lateral inside CAPTURE_WINDOW_MM).

    Under align_v2 a box an image border cut is read as its rebuilt whole box (unclipped_box) when it can be rebuilt, and
    `aligned` is the 2D band: the lateral inside CAPTURE_WINDOW_MM AND |radial| within ALIGN_RADIAL_BAND_MM AND no border
    cut the box (a clipped box is never aligned). It adds lateral_in_window, radial_in_band, clipped_edges and rebuilt."""
    if ALIGN_V2 not in tuple(words or ()):
        return ctl.alignment_from_bbox(bbox_xyxy, pitch_deg)
    edges = clipped_edges(bbox_xyxy)
    whole = unclipped_box(bbox_xyxy, edges)
    decision = dict(ctl.alignment_from_bbox(whole if whole is not None else bbox_xyxy, pitch_deg))
    lateral_in, radial_in = bool(decision["aligned"]), abs(decision["radial_mm"]) <= ALIGN_RADIAL_BAND_MM
    decision.update(lateral_in_window=lateral_in, radial_in_band=radial_in, clipped_edges=list(edges), rebuilt=bool(edges) and whole is not None,
                    aligned=lateral_in and radial_in and not edges)
    return decision


# ---------------------------------------------------------------------------------------------------------------------
# THE VIEW MODEL (pure functions). ALIGN decides from one box at one tool pitch through the controller's
# alignment_from_bbox. These functions run that formula backwards, so that a box can be PREDICTED at a tool pose the arm
# has not reached yet, and forwards on a box the image border cut, so that a clipped box is never read as a whole one.
# They are fitted on the three recorded descents of 2026-09-26, each a pair of views of one block from two heights
# (autonomy/tests/test_recorded_pins_20260926.py: runs m8c5f9e9, mb3c1048 and m2476137). Three pairs: thin evidence.
# ---------------------------------------------------------------------------------------------------------------------
FRAME_WIDTH_PX, FRAME_HEIGHT_PX = 1280.0, 720.0  # the direct wrist frame alignment_from_bbox is written for
BORDER_PX = 2.0  # cube_recognizer.BORDER_PX: a box this close to an image border is clipped by it
# The box width scales as 1 / (VIEW_DEPTH_OFFSET_MM + tool height): the least-squares fit of the three recorded width
# ratios (221 -> 284, 264 -> 351, 235 -> 282 px across descents of 30, 32 and 22 mm; each pair alone gives 66.3, 58.9, 69.7).
VIEW_DEPTH_OFFSET_MM = 62.8
# alignment_from_bbox reads a block lower by this much per mm the tool descends, beyond the tool's own radial motion: the
# same blocks read 3.6 to 6.7 mm nearer after the descents (least-squares fit; each pair alone gives 0.172, 0.214, 0.162).
RADIAL_READ_PER_UP_MM = 0.187
# Box height over width of a whole cube box: 1.095 and 1.097 (mb3c1048), 1.128 and 1.131 (m2476137, a block yawed about 30
# deg); the near block of m8c5f9e9 read 1.18 and 1.20. The reconstruction of a clipped box uses the successes' middle.
BOX_ASPECT = 1.11


def clipped_edges(bbox_xyxy) -> tuple:
    """The image borders a box touches ("top", "bottom", "left", "right"), within BORDER_PX, in that order."""
    x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
    touching = (("top", y0 <= BORDER_PX), ("bottom", y1 >= FRAME_HEIGHT_PX - BORDER_PX), ("left", x0 <= BORDER_PX), ("right", x1 >= FRAME_WIDTH_PX - BORDER_PX))
    return tuple(edge for edge, touches in touching if touches)


def unclipped_box(bbox_xyxy, edges=None, aspect: float = BOX_ASPECT):
    """The whole box of a cube the top or the bottom border cut: its width is whole, so the missing side is the uncut row
    plus or minus aspect x width. None when a side border cut it (the width is not whole) or both rows are cut. A box no
    border cut is returned as it is."""
    x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
    edges = clipped_edges(bbox_xyxy) if edges is None else tuple(edges)
    if not edges:
        return [x0, y0, x1, y1]
    if "left" in edges or "right" in edges or ("top" in edges and "bottom" in edges):
        return None
    height = aspect * (x1 - x0)
    return [x0, y1 - height, x1, y1] if "top" in edges else [x0, y0, x1, y0 + height]


def box_from_alignment(ctl, lateral_mm: float, radial_mm: float, width_px: float, pitch_deg: float, aspect: float = BOX_ASPECT) -> list:
    """alignment_from_bbox run backwards: the unclipped box of a cube read at (lateral_mm, radial_mm) with a box width_px
    wide, seen at a tool pitch of pitch_deg. alignment_from_bbox of the result gives lateral_mm and radial_mm back."""
    scale = ctl.GRASP_REF_WIDTH_PX / max(float(width_px), 1.0)
    px_per_mm = ctl.GRASP_REF_WIDTH_PX / ctl.CUBE_EDGE_MM
    center_x = ctl.NADIR_COLUMN_PX + (ctl.GRASP_REF_CENTER_PX[0] + lateral_mm * px_per_mm - ctl.NADIR_COLUMN_PX) / scale
    row = ctl.nadir_row_px(pitch_deg)
    center_y = row + (ctl.GRASP_REF_CENTER_PX[1] - radial_mm * px_per_mm - row) / scale
    half_w, half_h = width_px / 2.0, aspect * width_px / 2.0
    return [center_x - half_w, center_y - half_h, center_x + half_w, center_y + half_h]


def width_across_height(width_px: float, up_before_mm: float, up_after_mm: float) -> float:
    """The box width of the same cube after the tool moved from up_before_mm to up_after_mm above the proven grasp."""
    return float(width_px) * (VIEW_DEPTH_OFFSET_MM + up_before_mm) / (VIEW_DEPTH_OFFSET_MM + up_after_mm)


def predict_box_after(ctl, bbox_xyxy, tool_before: dict, tool_after: dict, aspect=None) -> dict:
    """THE BOX PREDICTOR: the box of a block that stays where it is, seen in bbox_xyxy from tool_before (tool_state), as it
    will be seen from tool_after. The reading moves with the tool (radial minus the tool's outward motion) and by
    RADIAL_READ_PER_UP_MM per mm of descent; the lateral reading stays (the pan is held); the width scales with the height.
    `aspect` defaults to the box's own. Returns the predicted box (unclipped: it may leave the frame), its lateral_mm,
    radial_mm and width_px."""
    x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
    now = ctl.alignment_from_bbox(bbox_xyxy, tool_before["pitch_deg"])
    radial = now["radial_mm"] - (tool_after["out_mm"] - tool_before["out_mm"]) - RADIAL_READ_PER_UP_MM * (tool_before["up_mm"] - tool_after["up_mm"])
    width = width_across_height(x1 - x0, tool_before["up_mm"], tool_after["up_mm"])
    aspect = (y1 - y0) / max(x1 - x0, 1.0) if aspect is None else aspect
    return {"bbox_xyxy": box_from_alignment(ctl, now["lateral_mm"], radial, width, tool_after["pitch_deg"], aspect), "lateral_mm": now["lateral_mm"], "radial_mm": radial, "width_px": width}


def view_margins_px(bbox_xyxy) -> dict:
    """How far a box stands inside each border of the frame, in px (negative: that much of it is outside)."""
    x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
    return {"top": y0, "bottom": FRAME_HEIGHT_PX - y1, "left": x0, "right": FRAME_WIDTH_PX - x1}


def pregrasp_centring_bias_mm(ctl, bbox_xyxy, tool: dict) -> float:
    """How far outward of a block the tool must stand, at the height and pitch of `tool`, for the block's box to be centred
    vertically in the frame: the radial reading at which box_from_alignment puts the box centre on the middle row, negated."""
    x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
    width, row = x1 - x0, ctl.nadir_row_px(tool["pitch_deg"])
    scale = ctl.GRASP_REF_WIDTH_PX / max(width, 1.0)
    grasp_row = row + (FRAME_HEIGHT_PX / 2.0 - row) * scale  # the grasp-depth row whose box centre falls on the middle row
    return -(ctl.GRASP_REF_CENTER_PX[1] - grasp_row) / (ctl.GRASP_REF_WIDTH_PX / ctl.CUBE_EDGE_MM)


# How far one recorded box edge lay from the predictor's box, at most, on the three recorded descents (16.9 px, the bottom
# edge of mb3c1048's descent view; the top edges within 12.4 px).
PREDICTOR_TOLERANCE_PX = 17.0
# The descent's REALIZED tool against its target (tool_state of the measured joints minus that of the commanded ones), on
# the two recorded descents whose target is on the record: mb3c1048 -13.23 mm out, -5.72 mm up, +3.25 deg pitch;
# m2476137 -4.98, -3.23, +1.67. Each of the three moves the block UP the next frame, so the view check takes the worst
# of each: the block's top row is predicted where the worst recorded realization would put it.
DESCENT_WORST_REALIZATION = {"out_mm": -13.23, "up_mm": -5.72, "pitch_deg": 3.25}

# ALIGN V2 (the word align_v2). Empirical starting values, each to be qualified on the robot (plan, track A, Robot):
ALIGN_RADIAL_BAND_MM = 5.0  # the successes descended at +3.2 and +0.7, the failures at +12.4 and +14.5
ALIGN_VIEW_MARGIN_PX = 40.0  # a predicted box closer than this to the top or a side is corrected before the arm descends (above PREDICTOR_TOLERANCE_PX)
ALIGN_DESCENT_AIM_MAX_MM = 7.0  # the descent is aimed at the block within this, inside pregrasp_descend_poses' 10 mm radial limit
ALIGN_RADIAL_STEP_MAX_MM = 20.0  # one radial step, pan held, height held, through cartesian_step_pose (whose own bound is 60)
ALIGN_RADIAL_STEPS_MAX = 4
ALIGN_REACQUIRE_MAX = 2  # a lost block: one stationary re-capture, then a step back up to where it was last seen, at most twice
ALIGN_VIEWS_MAX = 14  # every decision is made on one view; the servo ends after this many
ALIGN_MAX_BOX_WIDTH_PX = 0.36 * FRAME_WIDTH_PX  # the recognizer refuses a box wider than 0.40 of the frame; the descent keeps its prediction below 0.36
ALIGN_DESCENT_CLEARANCES_M = (0.043, 0.046, 0.049, 0.052, 0.055)  # today's descent clearances, lowest first


@dataclass(frozen=True)
class AlignOutcome:
    """ALIGN's result: `view` the aligned non-contact view after the descent, `decision` its last alignment_from_bbox
    decision (lateral, radial, aligned), the radial of which places the grasp."""
    view: dict
    decision: dict


def coarse_cube_out_v2_mm(contact_px) -> float:
    """COARSE_V2's cube out, in mm beyond the proven grasp radius, of a low-pose contact pixel (only its row is used)."""
    return COARSE_V2_OUT_AT_REF_MM + (COARSE_V2_REF_ROW_PX - float(contact_px[1])) / COARSE_V2_PX_PER_RADIAL_MM


@POLICY.decides
def coarse_pregrasp(ctx, ctl, seen: dict, measured: dict) -> dict:
    """The coarse pregrasp of the SEARCH record `seen` from the pan of the SEARCH pose `measured`: the controller's linear
    low-pose map (coarse_pregrasp_from_low_pose), unchanged with no word. Under coarse_v2 the cube out is the refitted one
    (coarse_cube_out_v2_mm) and the pregrasp stands PREGRASP_OUTWARD_BIAS_MM outward of it, clamped as the legacy map
    clamps; the pan is the legacy map's, and the legacy cube out rides beside it as legacy_cube_out_mm. The bias stays 13
    mm: the four recorded pregrasp views centre their block at 10.5 to 17.9 mm outward (pregrasp_centring_bias_mm), and
    what lowers the tool's real outward offset from the 24 to 40 mm of today's runs is the refit, not the bias."""
    legacy = ctl.coarse_pregrasp_from_low_pose(seen["cube"]["contact_px"], float(measured["shoulder_pan"]))
    if COARSE_V2 not in POLICY.run_words(ctl):
        return legacy
    out = coarse_cube_out_v2_mm(seen["cube"]["contact_px"])
    return {"pan_deg": legacy["pan_deg"], "forward_mm": round(min(max(out + ctl.PREGRASP_OUTWARD_BIAS_MM, 0.0), 60.0), 1), "cube_out_mm": round(out, 1),
            "legacy_cube_out_mm": legacy["cube_out_mm"], "coarse_map": COARSE_V2}


def pickup_band_min_cube_out_mm(ctl) -> float:
    """THE PICKUP BAND's bound on the coarse cube out, under this run's words: the nearest grasp APPROACH plans
    (policy_grasp.min_grasp_forward_mm under the run's GRASP words, 0.0 with none) less the band's margin. With no ALIGN word
    the margin is PREGRASP_OUTWARD_BIAS_MM (the placement build's bound, PREGRASP_MIN_CUBE_OUT_MM = -13 with no word at
    all): a legacy coarse value nearer than that has its pregrasp clamped. Under coarse_v2 the value is the refitted
    estimate of where the block is, and the margin is APPROACH_REACH_LEAD_MM: a block nearer than the nearest grasp less
    the lead has its grasp target clamped, and the jaws would close deeper beyond it than the lead."""
    nearest = policy_grasp.min_grasp_forward_mm(policy_grasp.POLICY.run_words(ctl))
    if COARSE_V2 in POLICY.run_words(ctl):
        return nearest - ctl.APPROACH_REACH_LEAD_MM
    return ctl.PREGRASP_MIN_CUBE_OUT_MM + nearest


@POLICY.decides
def refuse_pickup_band(ctx, ctl, coarse: dict, seen) -> None:
    """THE NEAR BLOCK (pickup_band_min_cube_out_mm), decided from the SEARCH frame before anything is picked up: a block nearer
    than the band is refused with the distance it must move, never closed on short of its centre. The coarse cube out is
    the one coarse_pregrasp gave: the refitted one under coarse_v2."""
    cube_out, bound = float(coarse["cube_out_mm"]), pickup_band_min_cube_out_mm(ctl)
    ctx.events.predicate("pregrasp_cube_within_reach", cube_out >= bound, measured=cube_out, threshold=bound, unit="mm", frames=seen,
                          scalars=("coarse_cube_out_mm",))
    if cube_out < bound:
        nearer, move = bound - cube_out, bound + (ctl.PREGRASP_OUTWARD_BIAS_MM - ctl.PREGRASP_MIN_CUBE_OUT_MM) - cube_out
        raise ctx.failed("search_cube_inside_pregrasp_reach", f"SEARCH: the block stands {nearer:.1f} mm nearer than the pregrasp can reach (coarse cube_out {cube_out:.1f} mm, "
                          f"the pregrasp clamps at {bound:.0f}); move it at least {move:.1f} mm away from the robot, or let the base reverse (T6 step 2b); "
                          "nothing was picked up", evidence=seen, cube_out_mm=cube_out, min_cube_out_mm=bound, move_block_at_least_mm=round(move, 1))


@POLICY.decides
def refuse_trim_beyond_return_bound(ctx, ctl, view: dict, trim_deg: float) -> None:
    """RETURN BOUND through ALIGN: a pan trim is commanded from the measured pan, and the pan it commands is held to
    RETURN_PAN_BOUND_DEG of the saved low pose's pan before it moves, so that the pan the arm holds at every stop of this
    cycle is one the EXIT can be planned from. Refused, the arm stays at the pregrasp with nothing gripped."""
    home_pan = ctx._home_pan_deg()
    commanded = float(view["measured"]["shoulder_pan"]) + trim_deg
    from_home = abs(commanded - home_pan)
    ctx.events.predicate("trim_pan_within_return_bound", from_home <= ctl.RETURN_PAN_BOUND_DEG, measured=from_home, threshold=ctl.RETURN_PAN_BOUND_DEG, unit="deg", frames=view,
                          scalars=("commanded_pan_deg", "home_pan_deg"), stamp_of=view["measured"])
    if from_home > ctl.RETURN_PAN_BOUND_DEG:
        raise ctx.failed("align_trim_beyond_return_bound", f"ALIGN: a pan trim of {trim_deg:+.2f} deg would command a pan {commanded:.2f}, {from_home:.1f} deg from the saved low pose's "
                          f"pan {home_pan:.2f}, beyond the {ctl.RETURN_PAN_BOUND_DEG:.0f} deg a return to that pose can be planned from; the trim is not commanded, nothing was picked up",
                          evidence=view, commanded_pan_deg=round(commanded, 3), trim_deg=trim_deg, home_pan_deg=home_pan, pan_from_home_deg=round(from_home, 2), return_pan_bound_deg=ctl.RETURN_PAN_BOUND_DEG)



# One raise site per reason code (autonomy/tests/test_cycle_events.py): the codes ALIGN raises from more than one place are
# raised here, with the sentence each place gives them.
def _raise_cube_not_detected(ctx, view: dict, attempt: int, recaptured: bool = False):
    why = ctx._why_no_cube(view)
    where = "in the pregrasp view, nor in a stationary re-capture" if recaptured else "in the pregrasp view"
    raise ctx.failed("align_cube_not_detected", f"ALIGN: the cube is not detected {where}. The recognizer's own reason: {why}",
                      evidence=view, attempt=attempt, recognizer_reason=why)


def _raise_not_in_window(ctx, view: dict, decision: dict, capture_window_mm, trims: int):
    raise ctx.failed("align_not_in_window_after_trims", f"ALIGN: still {decision['lateral_mm']:+.1f} mm off the capture window after three trims", evidence=view,
                      lateral_mm=decision["lateral_mm"], radial_mm=decision["radial_mm"], capture_window_min_mm=capture_window_mm[0], capture_window_max_mm=capture_window_mm[1], trims=trims)


def _raise_no_descent(ctx, view: dict, refusal):
    raise ctx.failed("align_no_non_contact_descent", f"ALIGN: no non-contact descent passes the guards at this reach: {refusal}", evidence=view,
                      tool_out_mm=view["tool"]["out_mm"], tool_up_mm=view["tool"]["up_mm"])


def _raise_exhausted(ctx, view: dict, attempts: int, trims: int, descended: bool):
    raise ctx.failed("align_bounded_corrections_exhausted", "ALIGN: not aligned within the bounded number of corrections", evidence=view, attempts=attempts, trims=trims, descended=descended)


def _raise_lost(ctx, view: dict, sentence: str, **values):
    """align_cube_lost_after_reacquire: the block left the view and the bounded re-acquire did not bring it back; the arm
    holds where it stands, at a non-contact clearance."""
    raise ctx.failed("align_cube_lost_after_reacquire", sentence, evidence=view, tool_out_mm=round(view["tool"]["out_mm"], 1), tool_up_mm=round(view["tool"]["up_mm"], 1), **values)


@POLICY.decides
def run_pregrasp_align(ctx, ctl, measured: dict, coarse: dict, seen: dict, start_inside: float) -> AlignOutcome:
    """PREGRASP from the SEARCH pose (`measured`) over the coarse estimate (`coarse`, coarse_pregrasp), the outward view bias
    given up in 5 mm steps while the unchanged non-contact guard refuses it; then ALIGN from the image: at most three pan
    trims (each held to the return bound first) and one non-contact descent, the cube seen in every view. `seen` is the
    SEARCH record and `start_inside` its signed margin, both stated in the PREGRASP state."""
    ctx.state("PREGRASP", coarse=coarse, cube_inside_pad_mm=round(start_inside, 1), evidence=seen)
    poses, forward_mm = None, coarse["forward_mm"]
    while poses is None:  # the outward view bias is optional: give it up in 5 mm steps when the unchanged non-contact guard refuses the farther target
        try:
            poses = ctl.cli.pregrasp_poses(measured, coarse["pan_deg"], forward_mm / 1000.0, 0.070)
        except ctl.ScanRefused as refusal:
            if forward_mm - 5.0 < max(coarse["cube_out_mm"] - 5.0, 0.0):
                raise ctx.failed("pregrasp_no_non_contact_pose", f"PREGRASP: no non-contact pregrasp over this cube passes the guards: {refusal}",
                                  evidence=seen, forward_mm=forward_mm, cube_out_mm=coarse["cube_out_mm"])
            forward_mm -= 5.0
    if forward_mm != coarse["forward_mm"]:
        ctx.state("PREGRASP_BIAS_REDUCED", forward_mm=forward_mm)
    view = ctx.run_pose(poses, False, ctl.cli.PREGRASP_MIN_LINK_HEIGHT_M, manipulation_step=False, label="PREGRASP (non-contact)")
    words = POLICY.run_words(ctl)
    if ALIGN_V2 in words:
        return _align_v2(ctx, ctl, view, words)

    trims, descended = 0, False
    for attempt in range(8):  # ALIGN: decide from the image; at most three pan trims, one non-contact descent (which sharpens the estimate)
        cube = ctx._cube(view)
        if cube is None:
            ctx.events.predicate("cube_detected_in_the_pregrasp_view", False, frames=view, scalars=("cube_detected",))
            _raise_cube_not_detected(ctx, view, attempt)
        if ctx.target is not None:
            ctx._mission_one_cube_in_view(cube, view, attempt)
        decision = alignment_decision(ctl, cube["cube"]["bbox_xyxy"], view["tool"]["pitch_deg"], POLICY.run_words(ctl))
        ctx.state("ALIGN", attempt=attempt, lateral_mm=round(decision["lateral_mm"], 1), radial_mm=round(decision["radial_mm"], 1), aligned=decision["aligned"], tool=ctl.fmt_tool(view["tool"]), evidence=view)
        ctx.events.predicate("aligned", decision["aligned"], measured=decision["lateral_mm"], threshold=ctl.CAPTURE_WINDOW_MM[1], unit="mm", frames=view, scalars=("lateral_mm", "radial_mm", "capture_window_mm"))
        if decision["aligned"] and descended:
            break
        if not decision["aligned"]:
            if trims == 3:
                _raise_not_in_window(ctx, view, decision, ctl.CAPTURE_WINDOW_MM, trims)
            cube_range = ctl.GRASP_TOOL_RANGE_M + (view["tool"]["out_mm"] + decision["radial_mm"]) / 1000.0
            trim = ctl.lateral_trim_deg(decision["lateral_mm"], cube_range)
            ctx._refuse_trim_beyond_return_bound(view, trim)  # before the trim moves: the pan the arm holds can still plan the EXIT
            view = ctx.run_pose(ctl.pan_step_pose(view["measured"], trim, False), False, ctl.CONTACT_FLOOR_M, pan_allowance=ctl.STEP_MAX_PAN_DEG, label="ALIGN pan trim")
            trims += 1
            ctx.events.automatic_retry("pan_trim")
        else:
            for clearance_m in (0.043, 0.046, 0.049, 0.052, 0.055):  # the lowest clearance the unchanged sag rule admits at this reach
                try:
                    view = ctx.run_pose(ctl.cli.pregrasp_descend_poses(view["measured"], max(view["tool"]["out_mm"], 0.0) / 1000.0 + 0.003, clearance_m), False, ctl.cli.APPROACH_MIN_LINK_HEIGHT_M, label="ALIGN non-contact descent")
                    break
                except ctl.ScanRefused as refusal:
                    last_refusal = refusal
            else:
                _raise_no_descent(ctx, view, last_refusal)
            descended = True
    else:
        _raise_exhausted(ctx, view, 8, trims, descended)
    return AlignOutcome(view=view, decision=decision)


# ---------------------------------------------------------------------------------------------------------------------
# ALIGN V2 (the word align_v2): a bounded servo to a 2D band, the view kept at every height. Every move is one the
# unchanged gates accept (cartesian_step_pose, pan_step_pose, pregrasp_descend_poses) run through ctx.run_pose, which polls
# the abort flag; every pose is above the non-contact floor, so a refusal leaves the arm holding clear of the block.
#   - the decision is alignment_decision's 2D band: lateral in the capture window, |radial| within the band, the box whole;
#   - a box an image border cut is never accepted: it re-centres (a radial step toward a top or bottom cut, rebuilt from
#     its uncut row and its width; a pan trim toward a side cut);
#   - a lateral outside the window is trimmed as today (three trims, each held to the return bound first);
#   - a radial outside the band is stepped out at the height it was read, pan and height held; a step the gate refuses
#     before the descent leaves the correction to the aimed descent and to a step at the descent height;
#   - the descent is aimed at the block (the tool out plus the radial, within ALIGN_DESCENT_AIM_MAX_MM) and, before it
#     moves, the block's box is predicted where the worst recorded descent would leave it: a top or side margin below
#     ALIGN_VIEW_MARGIN_PX is corrected first (the aim, then a step at this height), and align_descent_would_lose_view
#     refuses the descent only when no correction passes the guards;
#   - a view with no block gets one stationary re-capture (ctx.observe), then a step back to the last view the block was
#     whole in, at most ALIGN_REACQUIRE_MAX times, then align_cube_lost_after_reacquire with the arm at a non-contact clearance.
# ---------------------------------------------------------------------------------------------------------------------
_EDGE_ORDER = ("top", "bottom", "left", "right")


def frame_sharpness(image, bbox_xyxy=None):
    """The variance of the Laplacian of the grey frame inside the box (the whole frame without one), or None without a
    frame. Evidence only: nothing decides on it, since no absolute threshold is measured yet."""
    if image is None:
        return None
    import cv2

    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if getattr(image, "ndim", 2) == 3 else image
    if bbox_xyxy is not None:
        height, width = grey.shape[:2]
        x0, y0, x1, y1 = (int(round(float(v))) for v in bbox_xyxy)
        x0, x1, y0, y1 = max(0, x0), min(width, x1), max(0, y0), min(height, y1)
        if x1 - x0 >= 3 and y1 - y0 >= 3:
            grey = grey[y0:y1, x0:x1]
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def _sharpness(ctx, view: dict, bbox_xyxy):
    try:  # evidence: a frame that cannot be read costs the number, never the stage
        value = frame_sharpness(ctx._frame(view), bbox_xyxy)
    except Exception:  # noqa: BLE001
        return None
    return None if value is None else round(value, 1)


def _record_edges(cube: dict) -> tuple:
    flags = cube.get("clipped") if isinstance(cube.get("clipped"), dict) else {}
    return tuple(edge for edge in _EDGE_ORDER if flags.get(edge))


def _clamp(value: float, bound: float) -> float:
    return max(-bound, min(bound, value))


def _floor(ctl, descended: bool) -> float:
    """The link floor of an ALIGN move: the pregrasp's above the descent, the non-contact descent's after it."""
    return ctl.cli.APPROACH_MIN_LINK_HEIGHT_M if descended else ctl.cli.PREGRASP_MIN_LINK_HEIGHT_M


def _non_contact_up_mm(ctl) -> float:
    """The lowest tool height above the proven grasp an ALIGN move may target: the jaw tips above the cube top plane."""
    return (ctl.NON_CONTACT_FLOOR_M - ctl.GRASP_TOOL_Z_M) * 1000.0


def inner_bound_mm(ctl) -> float:
    """THE INNER BOUND of ALIGN V2, in mm of tool out beyond the proven grasp radius: the nearest grasp the grasp stage plans
    under this run's words (policy_grasp.min_grasp_forward_mm), which is the proven grasp radius (0.0) with no grasp word and
    near_grasp's NEAR_GRASP_MIN_FORWARD_MM only when near_grasp is named too. No guard models the chassis (policy_grasp), and
    no recorded run commanded the tool nearer than that radius: it is the one measured bound on how near ALIGN may reach."""
    return policy_grasp.min_grasp_forward_mm(policy_grasp.POLICY.run_words(ctl))


@POLICY.decides
def refuse_inside_inner_bound(ctx, ctl, view: dict, poses, move: str) -> None:
    """Refuse, before it moves, an ALIGN V2 move (`poses`, from `view`) whose innermost commanded tool radius lies inside
    inner_bound_mm: align_inside_grasp_radius, the arm where it holds, clear of the block. The radius is read on the
    commanded joints, to the 0.1 mm the tool is stated in."""
    bound = inner_bound_mm(ctl)
    commanded = min(ctl.tool_state({**view["measured"], **pose.joints})["out_mm"] for pose in poses)
    if round(commanded, 1) < bound:
        near = policy_grasp.NEAR_GRASP in policy_grasp.POLICY.run_words(ctl)
        raise ctx.failed("align_inside_grasp_radius", f"ALIGN: the {move} would command the tool {commanded:+.1f} mm from the proven grasp radius, nearer than the "
                          f"{bound:+.1f} mm bound ({'the nearest grasp near_grasp plans' if near else 'the proven grasp radius; near_grasp is not named'}); the block "
                          f"stands nearer than ALIGN may reach, the move is not commanded, the arm holds clear of the block, nothing was picked up", evidence=view,
                          move=move, commanded_tool_out_mm=round(commanded, 1), inner_bound_mm=bound, near_grasp=near, tool_out_mm=round(view["tool"]["out_mm"], 1))


def _radial_step_pose(ctl, view: dict, forward_mm: float):
    """The pose of one radial step (pan and height held), or the refusal's sentence."""
    try:
        pose, _report = ctl.cartesian_step_pose(view["measured"], _clamp(forward_mm, ALIGN_RADIAL_STEP_MAX_MM) / 1000.0, 0.0, False)
    except ctl.ScanRefused as refusal:
        return None, str(refusal)
    if ctl.tool_state(pose.joints)["up_mm"] < _non_contact_up_mm(ctl):
        return None, "the step would bring the jaw tips below the cube top plane"
    return pose, None


def _descent_prediction(ctl, view: dict, box, poses) -> dict:
    planned = ctl.tool_state({**view["measured"], **poses[-1].joints})
    realized = {**planned, **{key: planned[key] + DESCENT_WORST_REALIZATION[key] for key in DESCENT_WORST_REALIZATION}}
    predicted = predict_box_after(ctl, box, view["tool"], realized)
    margins = view_margins_px(predicted["bbox_xyxy"])
    return {**predicted, "margins": margins, "planned": planned,
            "ok": min(margins["top"], margins["left"], margins["right"]) >= ALIGN_VIEW_MARGIN_PX and predicted["width_px"] <= ALIGN_MAX_BOX_WIDTH_PX}


def plan_aimed_descent(ctl, view: dict, box, radial_mm: float) -> dict:
    """The aimed non-contact descent from `view`, whose whole box is `box` read at `radial_mm`: for each clearance, lowest
    first, the aim tool out + radial (within ALIGN_DESCENT_AIM_MAX_MM), raised once by what a short top margin asks for.
    Returns the first plan whose predicted view keeps every margin: {poses, aim_mm, clearance_m, predicted}, with ok True;
    else the best plan found with ok False and top_short_mm, what the top margin still lacks, in mm at the block (None
    when no clearance passes the guards at all, and `refusal` then says why)."""
    best, refusal = None, None
    for clearance_m in ALIGN_DESCENT_CLEARANCES_M:
        extra = 0.0
        for _ in range(2):
            aim = _clamp(radial_mm + extra, ALIGN_DESCENT_AIM_MAX_MM)
            try:
                poses = ctl.cli.pregrasp_descend_poses(view["measured"], (view["tool"]["out_mm"] + aim) / 1000.0, clearance_m)
            except ctl.ScanRefused as refused:
                refusal = str(refused)
                break
            predicted = _descent_prediction(ctl, view, box, poses)
            px_per_mm = predicted["width_px"] / ctl.CUBE_EDGE_MM
            short_mm = max(0.0, (ALIGN_VIEW_MARGIN_PX - predicted["margins"]["top"]) / px_per_mm)
            # what a step outward at this height must add when the aim the top margin asks for (aim + short_mm, with half a
            # millimetre spare) lies beyond the aim's bound: after that step, the bounded aim reaches the same point
            plan = {"poses": poses, "aim_mm": aim, "clearance_m": clearance_m, "predicted": predicted, "ok": predicted["ok"],
                    "top_short_mm": max(0.0, aim + short_mm + 0.5 - ALIGN_DESCENT_AIM_MAX_MM) if short_mm > 0.0 else 0.0}
            if plan["ok"]:
                return plan
            if best is None or predicted["margins"]["top"] > best["predicted"]["margins"]["top"]:
                best = plan
            if short_mm <= 0.0 or extra != 0.0:
                break
            extra = short_mm + 0.5  # the aim that brings the predicted top row back to the margin, with half a millimetre spare
    return best if best is not None else {"poses": None, "ok": False, "refusal": refusal, "top_short_mm": None}


def _last_whole_view(seen_views: list, now: dict):
    """The newest view the block was whole in whose tool stands elsewhere than `now`'s (1 mm, 0.2 deg), with whether it was
    after a descent; None when there is none."""
    for view, descended in reversed(seen_views):
        if (abs(view["tool"]["out_mm"] - now["tool"]["out_mm"]) > 1.0 or abs(view["tool"]["up_mm"] - now["tool"]["up_mm"]) > 1.0
                or abs(float(view["measured"]["shoulder_pan"]) - float(now["measured"]["shoulder_pan"])) > 0.2):
            return view, descended
    return None


@POLICY.decides
def _step_back(ctx, ctl, now: dict, back: dict, descended: bool) -> dict:
    """Back to where the block was last whole: bounded Cartesian steps (up and out, pan held), then the pan. A step the
    gates refuse ends the servo with the arm where it holds, at a non-contact clearance."""
    view = now
    for _ in range(3):
        d_out, d_up = back["tool"]["out_mm"] - view["tool"]["out_mm"], back["tool"]["up_mm"] - view["tool"]["up_mm"]
        if abs(d_out) <= 1.0 and abs(d_up) <= 1.0:
            break
        try:
            pose, _report = ctl.cartesian_step_pose(view["measured"], _clamp(d_out, ctl.STEP_MAX_FORWARD_M * 1000.0) / 1000.0, _clamp(d_up, ctl.STEP_MAX_DZ_M * 1000.0) / 1000.0, False)
            view = ctx.run_pose(pose, False, _floor(ctl, descended or d_up > 0.0), label="ALIGN re-acquire step back")
        except ctl.ScanRefused as refusal:
            _raise_lost(ctx, view, f"ALIGN: the block left the view and the step back to where it was last seen is refused: {refusal}; the arm holds "
                        f"{view['tool']['up_mm']:+.1f} mm above the grasp height, nothing was picked up", reacquire_refused=True)
    d_pan = float(back["measured"]["shoulder_pan"]) - float(view["measured"]["shoulder_pan"])
    if abs(d_pan) > 0.2:
        try:
            view = ctx.run_pose(ctl.pan_step_pose(view["measured"], round(_clamp(d_pan, ctl.STEP_MAX_PAN_DEG), 2), False), False, ctl.CONTACT_FLOOR_M, pan_allowance=ctl.STEP_MAX_PAN_DEG,
                                label="ALIGN re-acquire pan back")
        except ctl.ScanRefused as refusal:
            _raise_lost(ctx, view, f"ALIGN: the block left the view and the pan back to where it was last seen is refused: {refusal}; nothing was picked up", reacquire_refused=True)
    return view


@POLICY.decides
def _trim(ctx, ctl, view: dict, decision: dict) -> dict:
    cube_range = ctl.GRASP_TOOL_RANGE_M + (view["tool"]["out_mm"] + decision["radial_mm"]) / 1000.0
    trim = ctl.lateral_trim_deg(decision["lateral_mm"], cube_range)
    ctx._refuse_trim_beyond_return_bound(view, trim)  # before the trim moves: the pan the arm holds can still plan the EXIT
    view = ctx.run_pose(ctl.pan_step_pose(view["measured"], trim, False), False, ctl.CONTACT_FLOOR_M, pan_allowance=ctl.STEP_MAX_PAN_DEG, label="ALIGN pan trim")
    ctx.events.automatic_retry("pan_trim")
    return view


@POLICY.decides
def _align_v2(ctx, ctl, view: dict, words) -> AlignOutcome:
    trims = steps = reacquires = 0
    descended, seen_views, decision, margin_step = False, [], None, False
    for attempt in range(ALIGN_VIEWS_MAX):
        cube = ctx._cube(view)
        if cube is None and "tool" not in view:  # a plan-only run: nothing was captured, today's refusal
            ctx.events.predicate("cube_detected_in_the_pregrasp_view", False, frames=view, scalars=("cube_detected",))
            _raise_cube_not_detected(ctx, view, attempt)
        if cube is None:
            ctx.events.predicate("cube_detected_in_the_pregrasp_view", False, frames=view, scalars=("cube_detected",))
            again = ctx.observe(False)  # one stationary re-capture: the arm has had longer to settle
            found = ctx._cube(again) is not None
            ctx.state("ALIGN_RECAPTURE", attempt=attempt, cube_detected=found, tool=ctl.fmt_tool(again["tool"]), evidence=again)
            if found:
                view = again
                continue
            if not seen_views:  # never whole in ALIGN: there is nowhere to step back to
                _raise_cube_not_detected(ctx, again, attempt, recaptured=True)
            back = _last_whole_view(seen_views, again)
            if back is None or reacquires == ALIGN_REACQUIRE_MAX:  # the budget is spent, or the arm already stands where the block was last whole
                last_box = ctx._cube(seen_views[-1][0])["cube"]["bbox_xyxy"]
                _raise_lost(ctx, again, f"ALIGN: the block left the view again after {reacquires} steps back to where it was last seen; the arm holds "
                            f"{again['tool']['up_mm']:+.1f} mm above the grasp height, clear of the block, nothing was picked up",
                            attempt=attempt, reacquires=reacquires, last_seen_top_px=float(last_box[1]), last_seen_bottom_px=float(last_box[3]))
            reacquires += 1
            ctx.state("ALIGN_REACQUIRE", attempt=attempt, reacquire=reacquires, to_tool=ctl.fmt_tool(back[0]["tool"]), evidence=again)
            ctx.events.automatic_retry("align_reacquire")
            view = _step_back(ctx, ctl, again, back[0], back[1])
            descended = back[1]
            continue
        if ctx.target is not None:
            ctx._mission_one_cube_in_view(cube, view, attempt)
        box = cube["cube"]["bbox_xyxy"]
        decision = alignment_decision(ctl, box, view["tool"]["pitch_deg"], words)
        edges = tuple(edge for edge in _EDGE_ORDER if edge in decision["clipped_edges"] or edge in _record_edges(cube["cube"]))
        if edges:
            decision = {**decision, "clipped_edges": list(edges), "aligned": False}
        whole = unclipped_box(box, edges)
        ctx.state("ALIGN", attempt=attempt, lateral_mm=round(decision["lateral_mm"], 1), radial_mm=round(decision["radial_mm"], 1), aligned=decision["aligned"], tool=ctl.fmt_tool(view["tool"]),
                  radial_in_band=decision["radial_in_band"], clipped=list(edges), sharpness=_sharpness(ctx, view, box), evidence=view)
        ctx.events.predicate("aligned", decision["aligned"], measured=decision["lateral_mm"], threshold=ctl.CAPTURE_WINDOW_MM[1], unit="mm", frames=view,
                              scalars=("lateral_mm", "radial_mm", "capture_window_mm"), measurements={"radial_mm": decision["radial_mm"], "radial_band_mm": ALIGN_RADIAL_BAND_MM})
        if not edges:
            seen_views.append((view, descended))
        if decision["aligned"] and descended:
            return AlignOutcome(view=view, decision=decision)
        above_trim_height = view["tool"]["up_mm"] >= ctl.PAN_NEEDS_TOOL_ABOVE_GRASP_M * 1000.0
        if edges and ("left" in edges or "right" in edges) and above_trim_height or (not edges and not decision["lateral_in_window"]):
            if trims == 3:
                _raise_not_in_window(ctx, view, decision, ctl.CAPTURE_WINDOW_MM, trims)
            view = _trim(ctx, ctl, view, decision)
            trims += 1
            continue
        # after a step taken for the view margin, the radial it leaves is the one the descent wants: no band step undoes it
        if edges or not (decision["radial_in_band"] or (margin_step and not descended)):
            if whole is None and edges:  # both rows or a side cut below the trim height: step toward the cut side
                forward_mm = ALIGN_RADIAL_STEP_MAX_MM if "top" in edges else -ALIGN_RADIAL_STEP_MAX_MM if "bottom" in edges else 0.0
            else:
                forward_mm = decision["radial_mm"]
            pose, refusal = _radial_step_pose(ctl, view, forward_mm) if abs(forward_mm) >= 0.5 else (None, "the step is below half a millimetre")
            if pose is not None and steps < ALIGN_RADIAL_STEPS_MAX:
                refuse_inside_inner_bound(ctx, ctl, view, [pose], "ALIGN radial step")
                ctx.state("ALIGN_RADIAL_STEP", attempt=attempt, forward_mm=round(_clamp(forward_mm, ALIGN_RADIAL_STEP_MAX_MM), 1), radial_mm=round(decision["radial_mm"], 1), clipped=list(edges), evidence=view)
                ctx.events.automatic_retry("align_radial_step")
                view = ctx.run_pose(pose, False, _floor(ctl, descended), label="ALIGN radial step")
                steps, margin_step = steps + 1, False
                continue
            if descended or edges or steps >= ALIGN_RADIAL_STEPS_MAX:
                raise ctx.failed("align_radial_not_in_band", f"ALIGN: the block reads {decision['radial_mm']:+.1f} mm radially, outside the {ALIGN_RADIAL_BAND_MM:.0f} mm band"
                                  f"{', its box cut by the ' + ' and '.join(edges) + ' border' if edges else ''}, and no radial step corrects it "
                                  f"({refusal or f'{steps} steps taken'}); the arm holds clear of the block, nothing was picked up", evidence=view,
                                  radial_mm=round(decision["radial_mm"], 1), radial_band_mm=ALIGN_RADIAL_BAND_MM, radial_steps=steps, tool_out_mm=round(view["tool"]["out_mm"], 1))
            # before the descent a step the gate refuses (a block nearer than the pregrasp can stand over) is left to the aimed
            # descent and to a step at the descent height, where the gate admits it
        plan = plan_aimed_descent(ctl, view, whole, decision["radial_mm"])
        if plan["poses"] is None:
            _raise_no_descent(ctx, view, plan["refusal"])
        margins, predicted = plan["predicted"]["margins"], plan["predicted"]
        ctx.events.predicate("align_view_margin", plan["ok"], measured=min(margins["top"], margins["left"], margins["right"]), threshold=ALIGN_VIEW_MARGIN_PX, unit="px", frames=view,
                              scalars=("predicted_top_px", "predicted_left_px", "predicted_right_px"),
                              measurements={"predicted_top_px": margins["top"], "predicted_left_px": margins["left"], "predicted_right_px": margins["right"],
                                            "predicted_width_px": predicted["width_px"], "aim_mm": plan["aim_mm"]})
        if not plan["ok"]:
            correction = plan["top_short_mm"] or 0.0
            pose, refusal = _radial_step_pose(ctl, view, correction) if correction >= 0.5 and steps < ALIGN_RADIAL_STEPS_MAX else (None, None)
            if pose is None:
                raise ctx.failed("align_descent_would_lose_view", f"ALIGN: the descent would leave the block {margins['top']:.0f} px from the top of the view and "
                                  f"{min(margins['left'], margins['right']):.0f} px from a side (at least {ALIGN_VIEW_MARGIN_PX:.0f} wanted), and no correction passes the guards"
                                  f"{': ' + refusal if refusal else ''}; the descent is not commanded, nothing was picked up", evidence=view,
                                  predicted_top_px=round(margins["top"], 1), predicted_left_px=round(margins["left"], 1), predicted_right_px=round(margins["right"], 1),
                                  view_margin_px=ALIGN_VIEW_MARGIN_PX, radial_mm=round(decision["radial_mm"], 1), lateral_mm=round(decision["lateral_mm"], 1))
            refuse_inside_inner_bound(ctx, ctl, view, [pose], "ALIGN radial step")
            ctx.state("ALIGN_RADIAL_STEP", attempt=attempt, forward_mm=round(_clamp(correction, ALIGN_RADIAL_STEP_MAX_MM), 1), radial_mm=round(decision["radial_mm"], 1),
                      predicted_top_px=round(margins["top"], 1), evidence=view)
            ctx.events.automatic_retry("align_radial_step")
            view = ctx.run_pose(pose, False, _floor(ctl, descended), label="ALIGN radial step")
            steps, margin_step = steps + 1, True
            continue
        refuse_inside_inner_bound(ctx, ctl, view, plan["poses"], "ALIGN non-contact descent")
        ctx.state("ALIGN_DESCENT", attempt=attempt, aim_mm=round(plan["aim_mm"], 1), clearance_mm=round(plan["clearance_m"] * 1000.0), predicted_top_px=round(margins["top"], 1), evidence=view)
        try:
            view = ctx.run_pose(plan["poses"], False, ctl.cli.APPROACH_MIN_LINK_HEIGHT_M, label="ALIGN non-contact descent")
        except ctl.ScanRefused as refusal:
            _raise_no_descent(ctx, view, refusal)
        descended, margin_step = True, False
    _raise_exhausted(ctx, view, ALIGN_VIEWS_MAX, trims, descended)
