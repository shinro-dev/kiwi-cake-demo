# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The PLACEMENT stage of the single-cube cycle: PLACEMENT_PLAN, CARRY, the base advance's placement side, PLACE_APPROACH,
RELEASE_ADMISSION, the release and PLACED.

Called by run_single_cube_cycle.Cycle.run_cycle after OBJECT_SECURED, with ctx (the Cycle) and ctl (the controller's
namespace, stage_policy.ControllerView); stage_policy's module docstring says why a stage never imports the controller.
The base advance itself is the pinned Cycle.advance_base (with _advance_base_held and measured_base_pulse), which calls the
placement rule base_advance_needed_mm by name in the controller; the rule, placement_plan and the release margins stay in
the controller and are read through ctl, and so does the run's PLACEMENT_ENVELOPE (the words the run named, which the
controller rebinds while it runs). WORDS below is this stage's own declaration of the words it knows. The controller's Cycle._carry_arm_only,
Cycle._release_offset_plan and Cycle._release_offset_realized are carry_arm_only, plan_release_offset and
release_offset_realized here.
"""
import math
from dataclasses import dataclass

import placement_envelope
import stage_policy
from stage_policy import CycleFailed

# The predicates the PLACEMENT stage decides. The pinned base primitives' own (base_measured, pad_frame_valid_while_holding,
# arm_held_during_base_translation, the base-pulse gate's) are not a stage's rule and carry none.
# The words of this stage (stage_policy, THE WORDS): the placement track's (docs/autonomous-demo/one-block-demo-2026-09-26.md,
# T6 steps 1b, 2b, 3a, 3b), every later word building on projected, the first. A word joins QUALIFIED only in the commit
# that adds its docs/autonomous-demo/placement-envelope-qualification-<date>.md record.
WORDS = ("projected", "near_reverse", "side_offset", "base_lateral")
QUALIFIED = frozenset()
POLICY = stage_policy.StagePolicy("placement", __file__, ("placement_plan", "arm_only_sufficient", "release_offset_measured", "release_margin", "release_height", "release_admitted", "placed", "clearance_target_met"), WORDS, QUALIFIED)


@dataclass(frozen=True)
class PlacementOutcome:
    """What RETURN rests on: `advanced` the measured base advance (0.0 when the base never moved), `anchor` and `axis` the
    anchor and the reach axis the advance ended on, `rate` its measured rate, `final` the EXIT view PLACED was read from,
    `worst` the predicted worst-case centre margin of the release, `margin` the measured centre-to-edge margin (nan when
    none) and `placed` the placement PLACED read (its PAD_FRAME record, or the mission's attributed cube)."""
    advanced: float
    anchor: list
    axis: tuple
    rate: float
    final: dict
    worst: float
    margin: float
    placed: dict


@POLICY.decides
def classify_for_binding(ctx, ctl, anchor, direction, grasp_out_mm: float, seen=None) -> tuple:
    """The placement mode a block at `anchor` (PAD_FRAME) reached along `direction` from a grasp `grasp_out_mm` beyond the
    proven radius would be driven in, as SEARCH and a mission binding decide it (placement_plan with the plan-time side
    allowance): (mode, None), or (None, why) when no bounded placement exists, `why` a dict of scalars: 'sentence', the
    controller's refusal sentence, and placement_envelope.explain_refusal's values that name the binding limit (constraint,
    deficit_mm, hint, explanation and the side and along numbers). A reading that is not finite is refused here, before
    placement_plan, with the explanation's reason and no limit. Commands nothing and states nothing."""
    if not all(math.isfinite(float(v)) for v in (anchor[0], anchor[1], direction[0], direction[1], grasp_out_mm)):
        return None, {"sentence": placement_envelope.NOT_FINITE_EXPLANATION, **placement_envelope.explain_refusal(ctl, anchor, direction, grasp_out_mm)}
    try:
        return ctl.placement_plan(anchor, direction, grasp_out_mm, ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM)["mode"], None
    except CycleFailed as infeasible:
        # The solver names the binding constraint, the deficit and the hint (placement_envelope.explain_refusal); the mode
        # above stays placement_plan's own, so the binding and the executor decide alike.
        return None, {"sentence": str(infeasible), **placement_envelope.explain_refusal(ctl, anchor, direction, grasp_out_mm, ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM)}


@POLICY.decides
def carry_arm_only(ctx, ctl, carry: dict, grasp_out: float, travel_mm: float) -> dict:
    """CARRY arm-only: at most six proven-orientation steps outward toward grasp_out + travel_mm at the carry height, ending
    at CARRY_REACH_LIMIT when a step gains under CARRY_MIN_GAIN_MM or the gate refuses it. Returns the last carry view."""
    target_out = grasp_out + travel_mm
    for step in range(6):
        ctx.state("CARRY", step=step, fk_travel_from_grasp_mm=round(carry["tool"]["out_mm"] - grasp_out, 1), target_travel_mm=round(travel_mm, 1), tool=ctl.fmt_tool(carry["tool"]))
        if carry["tool"]["out_mm"] >= target_out - 5.0:
            break
        try:  # absolute proven-orientation target; +12 mm because the loaded elbow lags
            forward = min(target_out + 12.0, carry["tool"]["out_mm"] + 55.0)
            previous_out = carry["tool"]["out_mm"]
            carry = ctx.run_pose(ctl.proven_orientation_pose(carry["measured"], forward / 1000.0, ctl.CARRY_HEIGHT_MM / 1000.0)[0], True, ctl.CONTACT_FLOOR_M, label="CARRY forward")
            if carry["tool"]["out_mm"] - previous_out < ctl.CARRY_MIN_GAIN_MM:
                ctx.state("CARRY_REACH_LIMIT", reason="the last step gained under 5 mm", fk_travel_from_grasp_mm=round(carry["tool"]["out_mm"] - grasp_out, 1))
                break
        except ctl.ScanRefused as limit:
            ctx.state("CARRY_REACH_LIMIT", reason=str(limit)[:200], fk_travel_from_grasp_mm=round(carry["tool"]["out_mm"] - grasp_out, 1))
            break
    return carry


@POLICY.decides
def plan_release_offset(ctx, ctl, anchor, direction, grasp_pan: float, frame_pan: float, grasp_out: float, fk_travel: float, seen):
    """T6 step 3a, under the word side_offset only: the pan delta of the release, planned (release_offset_plan) or forced
    for a qualification run, stated as RELEASE_OFFSET before PLACE_APPROACH is commanded. None when no offset is taken
    (delta 0, or no admissible delta: the unchanged admission then decides on the anchor itself). A forced delta outside
    the return bound ends the run with the cube held: nothing is picked up that could not be returned from."""
    r = ctl.GRASP_TOOL_RANGE_M * 1000.0 + grasp_out
    home = ctx._home_pan_deg()
    if ctl.PLACEMENT_SIDE_OFFSET_FORCE_DEG is not None:
        delta = float(ctl.PLACEMENT_SIDE_OFFSET_FORCE_DEG)
        geometry = ctl.offset_release_point(anchor, direction, r, delta, grasp_pan - frame_pan)
        plan = {"delta_deg": delta, "release_point_pad_m": geometry["point_pad_m"], "release_direction": geometry["direction"], "chord_mm": geometry["chord_mm"],
                "shrink_mm": geometry["shrink_mm"], "pan_from_home_deg": abs(grasp_pan + delta - home), "r_release_mm": r}
    else:
        plan = ctl.release_offset_plan(anchor, direction, grasp_pan, frame_pan, home, r, fk_travel_mm=fk_travel)
    if plan is None or plan["delta_deg"] == 0.0:
        return None
    if plan["pan_from_home_deg"] > ctl.RETURN_PAN_BOUND_DEG:
        raise ctx.failed("release_offset_beyond_return_bound", f"RELEASE_OFFSET: the offset pan would lie {plan['pan_from_home_deg']:.1f} deg from the saved low pose's pan, beyond the "
                          f"{ctl.RETURN_PAN_BOUND_DEG:.0f} deg a return can be planned from; the cube stays held", evidence=seen, delta_deg=plan["delta_deg"], pan_from_home_deg=plan["pan_from_home_deg"])
    ctx.state("RELEASE_OFFSET", delta_commanded_deg=round(plan["delta_deg"], 2), forced=ctl.PLACEMENT_SIDE_OFFSET_FORCE_DEG is not None, predicted_chord_mm=round(plan["chord_mm"], 1),
               predicted_shrink_mm=round(plan["shrink_mm"], 1), predicted_release_point_pad_m=[round(v, 4) for v in plan["release_point_pad_m"]], r_release_mm=round(r, 1),
               pan_from_home_deg=round(plan["pan_from_home_deg"], 2), cap_deg=ctl.PLACE_SIDE_PAN_MAX_DEG, evidence=seen)
    return plan


@POLICY.decides
def release_offset_realized(ctx, ctl, plan: dict, over: dict, anchor, direction, grasp_pan: float, frame_pan: float) -> tuple:
    """The offset as the pan READBACK after PLACE_APPROACH realized it, never as it was commanded: the release point and
    direction the admission is decided from, and the uncertainty it takes off. RELEASE_OFFSET_MEASURED names the readback."""
    readback = float(over["measured"]["shoulder_pan"])
    delta_measured = readback - grasp_pan
    geometry = ctl.offset_release_point(anchor, direction, plan["r_release_mm"], delta_measured * ctl.SIDE_OFFSET_REALIZATION, grasp_pan - frame_pan)
    ctx.offset_realized = (plan["delta_deg"], round(delta_measured, 2), round(geometry["chord_mm"], 1))
    ctx.state("RELEASE_OFFSET_MEASURED", delta_commanded_deg=round(plan["delta_deg"], 2), delta_measured_deg=round(delta_measured, 2), pan_readback_deg=round(readback, 3),
               realization=ctl.SIDE_OFFSET_REALIZATION, chord_mm=round(geometry["chord_mm"], 1), shrink_mm=round(geometry["shrink_mm"], 1),
               release_point_pad_m=[round(v, 4) for v in geometry["point_pad_m"]], uncertainty_mm=ctl.SIDE_OFFSET_UNCERTAINTY_MM)
    ctx.events.predicate("release_offset_measured", abs(delta_measured) <= ctl.PLACE_SIDE_PAN_MAX_DEG, measured=delta_measured, threshold=ctl.PLACE_SIDE_PAN_MAX_DEG, unit="deg",
                          scalars=("pan_readback_deg", "delta_commanded_deg"), empirical=("SIDE_OFFSET_REALIZATION", "SIDE_OFFSET_UNCERTAINTY_MM"), stamp_of=over["measured"])
    return geometry["point_pad_m"], geometry["direction"], ctl.SIDE_OFFSET_UNCERTAINTY_MM


@POLICY.decides
def _envelope_requirements(ctx, ctl, anchor, direction) -> dict:
    """PLACEMENT_PLAN's requirement fields, which exist only under an envelope word: with no word nothing is computed, so the
    default path evaluates nothing it did not evaluate before (and nothing that can raise on an axis)."""
    if not ctl.PLACEMENT_ENVELOPE:
        return {}
    values = placement_envelope.refusal_values(ctl, anchor, direction, ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
    return ctx._envelope_fields(**{k: v for k, v in values.items() if k.startswith("required_")})


@POLICY.decides
def run_placement(ctx, ctl, summary: dict, measured: dict, seen: dict, template: dict, search_frame: dict, view: dict, third: dict) -> PlacementOutcome:
    """From the held cube (`view` the grasp, `third` the last verification lift) and SEARCH's measurements (`measured` its
    pose, `seen` its record, `template` its PAD_FRAME placement, `search_frame` its homography): PLACEMENT_PLAN, the mission
    and LIVE refusals of the plan, CARRY arm-only or the measured base advance (the pinned advance_base) with PLACE_APPROACH,
    the lowering, RELEASE_ADMISSION, the release, the retreat, the EXIT to the low pose and PLACED from the EXIT view. Fills
    summary's base_advance_mm, placed, footprint_clearance_mm and clearance_target_met."""
    grasp_out, grasp_pan = view["tool"]["out_mm"], float(view["measured"]["shoulder_pan"])
    anchor = [float(v) for v in template["cube_center_pad_m"]]  # the pad point under the tool at the grasp configuration
    direction = ctl.reach_direction_pad(search_frame["homography_image_to_pad"], seen["cube"]["contact_px"])
    try:
        plan = ctl.placement_plan(anchor, direction, grasp_out, ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
    except CycleFailed as infeasible:
        ctx.events.predicate("placement_plan", False, measured=ctl.placed_margin_mm(anchor), threshold=ctl.PLACE_REQUIRED_CENTRE_MM, unit="mm", anchor=seen, scalars=("anchor_margin_mm", "grasp_out_mm"), empirical=ctl._PLACEMENT_EMPIRICAL, stamp_of=view["measured"])
        raise ctx.failed("placement_infeasible_while_held", f"{infeasible}; the cube stays held", evidence=seen,
                          anchor_pad_x_m=anchor[0], anchor_pad_y_m=anchor[1], reach_direction_pad_x=direction[0], reach_direction_pad_y=direction[1],
                          anchor_margin_mm=ctl.placed_margin_mm(anchor), required_centre_margin_mm=ctl.PLACE_REQUIRED_CENTRE_MM, grasp_out_mm=grasp_out, bounded_base_advance_limit_mm=ctl.BASE_MAX_TOTAL_ADVANCE_MM,
                          **placement_envelope.explain_refusal(ctl, anchor, direction, grasp_out, ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM))
    run_mode = None if ctx.target is None else ctx.target.run_mode(ctx._mission["selection"])  # the module docstring, MODE
    ctx.state("PLACEMENT_PLAN", **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in plan.items()}, anchor_pad_m=[round(v, 4) for v in anchor], anchor_margin_mm=round(ctl.placed_margin_mm(anchor), 1),
               reach_direction_pad=[round(v, 3) for v in direction], required_centre_margin_mm=round(ctl.PLACE_REQUIRED_CENTRE_MM, 1), evidence=seen,
               **({} if run_mode is None else {"bound_mode": run_mode}), **_envelope_requirements(ctx, ctl, anchor, direction))
    # The anchor is the cube centre of the SEARCH observation; the grasp radius is FK of the pose read behind view. Which constants entered is said by name, the empirical ones marked.
    ctx.events.predicate("placement_plan", True, measured=ctl.placed_margin_mm(anchor), threshold=ctl.PLACE_REQUIRED_CENTRE_MM, unit="mm", anchor=seen, scalars=("anchor_margin_mm", "grasp_out_mm"), empirical=ctl._PLACEMENT_EMPIRICAL, stamp_of=view["measured"])
    if ctx.target is not None and plan["mode"] != run_mode:
        # The grasp radius is now measured rather than coarse, and it can move a cube near the boundary to the other mode.
        # For a DECLARED target neither direction is followed: a target that declares no capability never drives the base
        # (D5), and a target that declares it is placed only through the capability's approach, which an arm-only carry
        # would never use. For an AUTO target the one flip that is followed is ARM_ONLY to BASE_ADVANCE in a run that
        # CARRIES the capability: the plan below then drives the base exactly as a capability run does, and the record
        # shows the flip (TARGET_SELECTED and bound_mode above say ARM_ONLY, this plan says BASE_ADVANCE). Without the
        # capability the base cannot move for this run, and a flip the other way would place a BASE_ADVANCE run without
        # the capability's own output: both end the run with the cube held, and the operator resets the scene.
        if not (ctx.target.auto and plan["mode"] == ctl.target_binding.MODE_BASE_ADVANCE and ctx.target.carries_capability):
            if ctx.target.auto:
                why = ("this run carries no capability, so the base does not move for it" if plan["mode"] == ctl.target_binding.MODE_BASE_ADVANCE
                       else "a run that carries the capability is placed only through its approach")
                sentence = f"PLACEMENT_PLAN: this target was bound as {run_mode} and the plan from the measured grasp is {plan['mode']}; {why}; the cube stays held"
            else:
                sentence = f"PLACEMENT_PLAN: this target declares {run_mode} and the plan from the measured grasp is {plan['mode']}; the cube stays held"
            raise ctx.failed("mission_mode_changed_while_held", sentence, evidence=seen, declared_mode=ctx.target.mode, run_mode=run_mode, measured_mode=plan["mode"],
                              carries_capability=ctx.target.carries_capability, grasp_out_mm=grasp_out)
    if ctx.target is None and ctx.runtime_live and plan["mode"] == "BASE_ADVANCE":  # THE SYNTHETIC OBSERVATION IS OUT OF A LIVE RUN
        raise ctx.failed(ctl.LIVE_FORWARD_EXCLUDED, "PLACEMENT_PLAN: the plan from the measured grasp needs the base, and the base advance of a LIVE run without a "
                          "mission target would be fed the synthetic observation; the cube stays held", evidence=seen, grasp_out_mm=grasp_out)
    ctx.state("CARRY", mode=plan["mode"], tool=ctl.fmt_tool(third["tool"]))
    carry, advanced, rate, axis = third, 0.0, ctl.BASE_RATE_PRIOR_MM_S, direction
    offset_realized = None  # (release point, release direction, uncertainty) of a sideways release offset (step 3a), under its word only
    if plan["mode"] == "ARM_ONLY":
        carry = ctx._carry_arm_only(third, grasp_out, plan["fk_travel_mm"])
        if ctl.release_margin_worst_mm(anchor, direction, carry["tool"]["out_mm"] - grasp_out) < ctl.PLACE_REQUIRED_CENTRE_MM:
            ctx.state("ARM_ONLY_INSUFFICIENT", fk_travel_mm=round(carry["tool"]["out_mm"] - grasp_out, 1), worst_case_centre_margin_mm=round(ctl.release_margin_worst_mm(anchor, direction, carry["tool"]["out_mm"] - grasp_out), 1))
            ctx.events.predicate("arm_only_sufficient", False, measured=ctl.release_margin_worst_mm(anchor, direction, carry["tool"]["out_mm"] - grasp_out), threshold=ctl.PLACE_REQUIRED_CENTRE_MM, unit="mm", anchor=seen,
                                  scalars=("fk_travel_from_grasp_mm",), empirical=ctl._PLACEMENT_EMPIRICAL, stamp_of=carry["measured"])
            if ctx.target is not None and not ctx.target.carries_capability:
                # The fallback below is the base path, and a run that carries no capability never drives the base. Refused
                # HERE, before the carry back to the grasp radius, which only prepares the rise that a base advance needs:
                # a refusal commands nothing further, the cube stays held where the carry stopped.
                raise ctx.failed("mission_arm_only_insufficient_while_held", "ARM_ONLY_INSUFFICIENT: the loaded reach falls short and this run carries no capability, so the base does not move for it; "
                                  "the cube stays held", fk_travel_from_grasp_mm=carry["tool"]["out_mm"] - grasp_out,
                                  worst_case_centre_margin_mm=ctl.release_margin_worst_mm(anchor, direction, carry["tool"]["out_mm"] - grasp_out), required_centre_margin_mm=ctl.PLACE_REQUIRED_CENTRE_MM)
            if ctx.target is None and ctx.runtime_live:  # as above, for a LIVE run without a mission target: the fallback would be fed the synthetic observation
                raise ctx.failed(ctl.LIVE_FORWARD_EXCLUDED, "ARM_ONLY_INSUFFICIENT: the loaded reach falls short, and the base fallback of a LIVE run without a mission "
                                  "target would be fed the synthetic observation; the cube stays held", fk_travel_from_grasp_mm=carry["tool"]["out_mm"] - grasp_out)
            for _ in range(3):  # back to the grasp radius first: the lift joint cannot raise the extended arm
                if carry["tool"]["out_mm"] - grasp_out <= 15.0:
                    break
                carry = ctx.run_pose(ctl.proven_orientation_pose(carry["measured"], max(grasp_out, carry["tool"]["out_mm"] - 55.0) / 1000.0, ctl.CARRY_HEIGHT_MM / 1000.0)[0], True, ctl.CONTACT_FLOOR_M, label="CARRY back to the grasp radius")
            plan = {**plan, "mode": "BASE_ADVANCE"}
    if plan["mode"] == "BASE_ADVANCE":
        anchor, advanced, rate, axis = ctx.advance_base(anchor, direction)
        summary["base_advance_mm"] = round(advanced, 1)
        wanted_mm = max(grasp_out + ctl.PLACE_LEAD_MM, 0.0)
        offset = ctx._release_offset_plan(anchor, direction, grasp_pan, float(measured["shoulder_pan"]), grasp_out, wanted_mm - grasp_out, seen) if "side_offset" in ctl.PLACEMENT_ENVELOPE else None
        approach_pan = grasp_pan if offset is None else grasp_pan + offset["delta_deg"]  # the one motion change of step 3a: the pan argument, planned by the unchanged planner
        forward_mm, poses = min(wanted_mm, ctl.GRASP_POSE_MAX_FORWARD_M * 1000.0), None
        while poses is None:  # as at PREGRASP: the unchanged non-contact guard may refuse the farther target; the descent below restores the wanted radius
            try:
                poses = ctl.cli.pregrasp_poses(ctx.pose(), approach_pan, forward_mm / 1000.0, 0.070)
            except ctl.ScanRefused as refusal:
                if forward_mm < 5.0:
                    raise ctx.failed("place_approach_no_pose", f"PLACE_APPROACH: no pose above the release point passes the guards: {refusal}; the cube stays held",
                                      forward_mm=forward_mm, wanted_mm=wanted_mm, base_advance_measured_mm=advanced)
                forward_mm -= 5.0
        over = ctx.run_pose(poses, True, ctl.cli.PREGRASP_MIN_LINK_HEIGHT_M, manipulation_step=False, label="PLACE_APPROACH over the release point (cube held)")
        if offset is not None:
            offset_realized = ctx._release_offset_realized(offset, over, anchor, direction, grasp_pan, float(measured["shoulder_pan"]))
        carry = ctx.run_pose(ctl.proven_orientation_pose(over["measured"], wanted_mm / 1000.0, ctl.CARRY_HEIGHT_MM / 1000.0)[0], True, ctl.CONTACT_FLOOR_M, label="PLACE descend to the carry height")
    try:  # down to the proven release height, same radius, proven orientation
        carry = ctx.run_pose(ctl.proven_orientation_pose(carry["measured"], carry["tool"]["out_mm"] / 1000.0 + 0.006, ctl.PLACE_HEIGHT_MM / 1000.0)[0], True, ctl.CONTACT_FLOOR_M, label="PLACE lower")
    except ctl.ScanRefused as note:
        ctx.state("PLACE_LOWER_REFUSED", reason=str(note)[:160], height_mm=round(carry["tool"]["up_mm"], 1))
    travel = carry["tool"]["out_mm"] - grasp_out
    release_anchor, release_direction, offset_uncertainty = (anchor, direction, 0.0) if offset_realized is None else offset_realized
    worst = ctl.release_margin_worst_mm(release_anchor, release_direction, travel) - offset_uncertainty  # the proven admission on the moved anchor, exactly, whenever no offset was taken
    admitted = worst >= ctl.PLACE_REQUIRED_CENTRE_MM and carry["tool"]["up_mm"] <= ctl.RELEASE_MAX_HEIGHT_MM  # and never a drop: a cube that could not be lowered stays held
    side_after = placement_envelope.side_margin_mm(ctl, anchor, direction)  # the moved anchor's raw side margin at the admission: the record field the projected rule is re-sized from
    per_edge = placement_envelope.release_edge_margins(ctl, release_anchor, release_direction, travel, offset_uncertainty) if ctl.PLACEMENT_ENVELOPE else {}  # under a word only
    ctx.state("RELEASE_ADMISSION", admitted=admitted, worst_case_centre_margin_mm=round(worst, 1), required_mm=round(ctl.PLACE_REQUIRED_CENTRE_MM, 1), anchor_pad_m=[round(v, 4) for v in anchor],
               fk_travel_from_grasp_mm=round(travel, 1), base_advance_measured_mm=round(advanced, 1), release_height_mm=round(carry["tool"]["up_mm"], 1), max_release_height_mm=ctl.RELEASE_MAX_HEIGHT_MM, tool=ctl.fmt_tool(carry["tool"]), evidence=seen,
               **ctx._envelope_fields(side_margin_after_advance_mm=round(side_after, 1), **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in per_edge.items()}))
    # The admission has two halves and both are stated with what they rest on: the margin (the SEARCH anchor, moved by every base displacement MEASURED
    # between two pad frames while the cube was held, plus the discounted FK travel) and the measured release height; then the conjunction the code acts on.
    ctx.events.predicate("release_margin", worst >= ctl.PLACE_REQUIRED_CENTRE_MM, measured=worst, threshold=ctl.PLACE_REQUIRED_CENTRE_MM, unit="mm", anchor=seen, pad_measurements=ctx.events.pad_measurements(True),
                          scalars=("fk_travel_from_grasp_mm", "base_advance_measured_mm"), empirical=ctl._PLACEMENT_EMPIRICAL, stamp_of=carry["measured"],
                          measurements={"side_margin_after_advance_mm": side_after, **per_edge})
    ctx.events.predicate("release_height", carry["tool"]["up_mm"] <= ctl.RELEASE_MAX_HEIGHT_MM, measured=carry["tool"]["up_mm"], threshold=ctl.RELEASE_MAX_HEIGHT_MM, unit="mm", scalars=("tool_up_mm_vs_proven_grasp",), stamp_of=carry["measured"])
    ctx.events.predicate("release_admitted", admitted, scalars=("release_margin", "release_height"))
    if not admitted:
        raise ctx.failed("release_not_admitted", f"release NOT admitted: the pad-relative evidence puts the cube centre at worst {worst:.0f} mm from the nearest pad edge (required {ctl.PLACE_REQUIRED_CENTRE_MM:.0f}), "
                          f"tool {carry['tool']['up_mm']:+.0f} mm up (release at most {ctl.RELEASE_MAX_HEIGHT_MM:.0f}); the cube stays held", evidence=seen,
                          worst_case_centre_margin_mm=worst, required_centre_margin_mm=ctl.PLACE_REQUIRED_CENTRE_MM, release_height_mm=carry["tool"]["up_mm"], max_release_height_mm=ctl.RELEASE_MAX_HEIGHT_MM,
                          fk_travel_from_grasp_mm=travel, base_advance_measured_mm=advanced)
    released = ctx.grip(ctl.GRIPPER_OPEN, "open")
    ctx.state("RELEASED", gripper=round(released["gripper"], 3), fk_travel_from_grasp_mm=round(travel, 1), tool=ctl.fmt_tool(carry["tool"]))
    try:  # back first: the lift joint cannot raise the fully extended arm
        ctx.run_pose(ctl.cartesian_step_pose(released["measured"], -0.025, 0.012, False)[0], False, ctl.CONTACT_FLOOR_M, label="RETREAT back")  # back AND up: never press the pad
    except CycleFailed:  # a refused desktop perception answer holds the arm HERE: it is never a skipped retreat followed by the exit move
        raise
    except (ctl.ScanRefused, RuntimeError) as note:
        print(f"RETREAT back skipped: {note}")
    final = ctx.return_to_low_pose()
    placed = (final["perception"][-1].get("cube_by_template") or {}) if final.get("perception") else {}
    margin = ctl.placed_margin_mm(placed["cube_center_pad_m"]) if placed.get("cube_center_pad_m") else float("nan")
    summary["placed"] = bool(placed.get("inside_pad_outline")) and margin >= ctl.PLACED_MIN_EDGE_MARGIN_MM
    if ctx.target is not None:  # the frame may hold other blocks on the pad: PLACED is the block at THIS run's release, never "the" cube of the record
        placed, margin, summary["placed"] = ctx._mission_placed(final, ctl.release_segment_pad(release_anchor, release_direction, travel), offset_uncertainty)
    summary["footprint_clearance_mm"] = None if math.isnan(margin) else round(margin - ctl.PLACED_MIN_EDGE_MARGIN_MM, 1)
    summary["clearance_target_met"] = summary["placed"] and margin >= ctl.PLACE_REQUIRED_CENTRE_MM
    ctx.state("PLACED", value=summary["placed"], cube_pad_m=placed.get("cube_center_pad_m"), centre_to_edge_mm=None if math.isnan(margin) else round(margin, 1),
               footprint_margin_any_yaw_mm=summary["footprint_clearance_mm"], clearance_target_mm=ctl.PLACE_CLEARANCE_TARGET_MM, clearance_target_met=summary["clearance_target_met"],
               predicted_worst_case_centre_margin_mm=round(worst, 1), **ctx._mission_placed_fields(), evidence=final)
    # final is what return_to_low_pose() RETURNS: the EXIT observation. The SETTLE observation that follows it is never looked at by PLACED.
    ctx.events.predicate("placed", summary["placed"], measured=margin, threshold=ctl.PLACED_MIN_EDGE_MARGIN_MM, unit="mm", frames=final, scalars=("cube_centre_to_pad_edge_mm", "inside_pad_outline"))
    ctx.events.predicate("clearance_target_met", summary["clearance_target_met"], measured=margin, threshold=ctl.PLACE_REQUIRED_CENTRE_MM, unit="mm", frames=final, scalars=("cube_centre_to_pad_edge_mm",))
    return PlacementOutcome(advanced=advanced, anchor=anchor, axis=axis, rate=rate, final=final, worst=worst, margin=margin, placed=placed)
