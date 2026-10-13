# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The RETURN stage of the single-cube cycle: BASE_RETURN's policy and the end-of-run predicates.

Called by run_single_cube_cycle.Cycle.run_cycle after PLACED was stated, with ctx (the Cycle) and ctl (the controller's
namespace, stage_policy.ControllerView); stage_policy's module docstring says why a stage never imports the controller.
The reverse pulses are the proven primitive (Cycle.return_base and Cycle.measured_base_pulse, pinned by source); this
module decides when they run and what the run's ending is: the base returned within BASE_RETURN_TOLERANCE_MM, a placement
the EXIT view does not demonstrate, or a base that is not back.

THE PROVEN RETURN (no word). Cycle.return_base pulses back along the axis the advance ENDED on (the direction of the last
forward pulse longer than 10 mm) and gates the along-axis value only, summed pulse by pulse. On 2026-09-26 start 2 that
axis was tilted by a 14.9 mm last pulse, and the reverse pulses read -5.0 and -4.1 mm sideways against it (sideways -9.1
against the 10 mm tolerance), while the robot-fixed point came back within about 2.4 mm of where SEARCH saw it
(autonomy/tests/test_base_return_replay.py replays both from the recorded homographies).

THE DIRECT ERROR (base_return_error_2d, events only with no word). With the SEARCH view handed in (`start`, SEARCH's
outcome: its pad frame, its record, its pose), the start-to-now displacement of a floor point fixed to the robot is
measured from two pad frames, never summed: the SEARCH view and the last view the return took (with no word, when the
proven return needed no reverse pulse, the last view of the advance, taken with the cube held: the base has not moved
since, so the displacement is still the base's). It is stated along the START reach axis (the axis the advance was planned on, from the SEARCH view), across it (the left normal, the sign of
base_displacement's lateral_mm) and as a yaw (the turn of the segment between two robot-fixed pixels). The two views are
comparable only when every scan joint reads a number in both, the arm stood at the same joints within
BASE_MAX_ARM_DRIFT_DEG (the controller's own figure for about 5 mm on the pad) and the 50 mm cross keeps its shape within
BASE_MAX_DISTORTION_MM. With no word nothing is decided on it: no state line, no stdout, no command; a defect in it
costs the predicate, never the run.

THE WORD return_2d (plan section 2.6; OFF by default and not QUALIFIED). BASE_RETURN closes the loop on the direct error:
reverse pulses through the unchanged measured_base_pulse, each measured against the START axis (so the per-pulse lateral
drift is read against the axis the robot started on, and stated), until the along-axis error is within the tolerance; an
overshoot behind the start beyond the tolerance is never corrected (no forward pulse is commanded) and the base is not
returned; the verdict gates BOTH axes; the yaw is stated, never gated. The lateral is measured and gated, never
corrected: no lateral primitive is wired into the Cycle (the placement track's T6 3b) and lateral base motion is open
ruling 5. Without a comparable SEARCH view the word runs the proven return and says so.
"""
import math

import stage_policy

# The words of this stage (stage_policy, THE WORDS). A word joins QUALIFIED only in the commit that adds its
# docs/autonomous-demo/qualification-<word>-<date>.md record.
RETURN_2D = "return_2d"
WORDS = (RETURN_2D,)
QUALIFIED = frozenset()
POLICY = stage_policy.StagePolicy("return", __file__, ("base_returned", "base_return_error_2d"), WORDS, QUALIFIED)

# Two pixels of the low-pose wrist view, fixed to the robot, whose segment gives the yaw between two views (the pixels the
# return review of 2026-09-26 replayed; both lie on the pad in every recorded low-pose view).
YAW_PIXELS = ((300.0, 545.0), (800.0, 545.0))
# The reference the direct error names: SEARCH's selected view (SearchOutcome.measured and search_frame), the low-pose
# centre frame, or after a SEARCH SWEEP the sweep sector that saw the cube.
REFERENCE_SEARCH_VIEW = "search_view"


def _run_words(ctl) -> tuple:
    """This run's words of this stage; () (the proven return) when the controller's namespace names none, as the
    identity's own defect costs evidence only (stage_policy.StagePolicy.decides)."""
    try:
        return POLICY.run_words(ctl)
    except AttributeError:
        return ()


def start_reference(ctl, start):
    """The SEARCH view the return is measured against, from SEARCH's outcome (policy_search.SearchOutcome, or anything with
    its fields search_frame, seen, template and measured), or None when any of them is missing. {frame, record,
    anchor_pad_m (the cube's PAD_FRAME centre at SEARCH, a floor point whose pixel is fixed to the robot), axis (the reach
    direction at that cube's contact pixel, the axis the advance was planned on), pose (the joints of the SEARCH view)}."""
    if start is None:
        return None
    frame, seen = getattr(start, "search_frame", None), getattr(start, "seen", None)
    template, pose = getattr(start, "template", None) or {}, getattr(start, "measured", None)
    contact = ((seen or {}).get("cube") or {}).get("contact_px")
    anchor = template.get("cube_center_pad_m")
    if not frame or not frame.get("homography_image_to_pad") or not frame.get("homography_pad_to_image") or not contact or not anchor or not pose:
        return None
    return {"frame": frame, "record": seen, "anchor_pad_m": [float(anchor[0]), float(anchor[1])],
            "axis": tuple(float(v) for v in ctl.reach_direction_pad(frame["homography_image_to_pad"], contact)), "pose": dict(pose)}


def yaw_deg(ctl, image_to_pad_before, image_to_pad_after) -> float:
    """The turn, in degrees (counter-clockwise from pad +x toward pad +y), of the segment between the two YAW_PIXELS."""
    def heading(image_to_pad):
        first, second = (ctl._through(image_to_pad, pixel) for pixel in YAW_PIXELS)
        return math.atan2(float(second[1] - first[1]), float(second[0] - first[0]))
    turn = math.degrees(heading(image_to_pad_after) - heading(image_to_pad_before))
    return (turn + 180.0) % 360.0 - 180.0


def return_error_2d(ctl, reference: dict, frame_now: dict, pose_now: dict) -> dict:
    """The DIRECT displacement of the robot since the SEARCH view: where the pixel that saw reference's anchor at SEARCH
    sees now (base_displacement, the controller's own formula), along the start axis (+ = the base is still AHEAD of its
    start), across it (+ = to the left) and as a yaw; with the posture difference of the two views and whether they are
    comparable (same joints within BASE_MAX_ARM_DRIFT_DEG, the cross undistorted within BASE_MAX_DISTORTION_MM)."""
    moved = ctl.base_displacement(reference["anchor_pad_m"], reference["axis"], reference["frame"]["homography_pad_to_image"], frame_now["homography_image_to_pad"])
    offsets = [abs(float(pose_now.get(joint, float("nan"))) - float(reference["pose"].get(joint, float("nan")))) for joint in ctl.SCAN_JOINTS]
    # an unreadable joint (NaN or missing, in either view, ANY of them) is never comparable; max() alone skips a NaN that is not first
    posture = max(offsets) if all(math.isfinite(offset) for offset in offsets) else float("inf")
    return {"along_start_axis_mm": float(moved["advance_mm"]), "lateral_mm": float(moved["lateral_mm"]),
            "yaw_deg": yaw_deg(ctl, reference["frame"]["homography_image_to_pad"], frame_now["homography_image_to_pad"]),
            "distortion_mm": float(moved["distortion_mm"]), "posture_difference_deg": posture, "anchor_pad_m": list(moved["anchor_pad_m"]),
            "shift_pad_mm": list(moved["shift_pad_mm"]),
            "comparable": posture <= ctl.BASE_MAX_ARM_DRIFT_DEG and float(moved["distortion_mm"]) <= ctl.BASE_MAX_DISTORTION_MM}


def returned_2d(ctl, error: dict) -> bool:
    """return_2d's verdict: comparable views, both axes within BASE_RETURN_TOLERANCE_MM (the yaw is stated, never gated)."""
    tolerance = ctl.BASE_RETURN_TOLERANCE_MM
    return bool(error["comparable"]) and abs(error["along_start_axis_mm"]) <= tolerance and abs(error["lateral_mm"]) <= tolerance


def _error_measurements(error: dict, reference: dict, view) -> dict:
    records = (view or {}).get("perception") or []
    return {"along_start_axis_mm": error["along_start_axis_mm"], "lateral_mm": error["lateral_mm"], "yaw_deg": error["yaw_deg"],
            "distortion_mm": error["distortion_mm"], "posture_difference_deg": error["posture_difference_deg"], "comparable": error["comparable"],
            "start_axis_pad_x": reference["axis"][0], "start_axis_pad_y": reference["axis"][1], "reference": REFERENCE_SEARCH_VIEW,
            "end_observation_id": records[-1].get("observation_id") if records else None}


@POLICY.decides
def _state_error(ctx, ctl, error: dict, reference: dict, view) -> None:
    """The predicate base_return_error_2d: the direct 2D error of the return, with the value return_2d's verdict gives it."""
    ctx.events.predicate("base_return_error_2d", returned_2d(ctl, error), measured=max(abs(error["along_start_axis_mm"]), abs(error["lateral_mm"])),
                         threshold=ctl.BASE_RETURN_TOLERANCE_MM, unit="mm", frame_pair=(reference["record"], view), measurements=_error_measurements(error, reference, view))


@POLICY.decides
def _error_of_last_view(ctx, ctl, reference):
    """(error, view) of the last view a base pulse took (Cycle._pulse_view: the return's last, or the advance's last when
    the proven return commanded no pulse), or (None, view) when it has no pad frame."""
    view = getattr(ctx, "_pulse_view", None)
    frame = ctx._pad_frame(view) if view else None
    return (return_error_2d(ctl, reference, frame, view["measured"]) if frame is not None else None), view


@POLICY.decides
def return_base_to_start(ctx, ctl, reference: dict, advanced: float, rate: float) -> dict:
    """return_2d's BASE_RETURN, after the release with the arm settled at the low scan pose. A read-only observe first; its
    `returned` is None when that view is not comparable with the SEARCH view and nothing was commanded (the caller then
    runs the proven return and says so). Else reverse pulses through the unchanged measured_base_pulse, each measured
    against the start axis from the robot-fixed point where it stands, the direct error re-measured after each from the
    SEARCH view (never summed), at most BASE_MAX_PULSES, until the along-axis error is within the tolerance. An overshoot
    behind the start beyond the tolerance commands nothing. Returns {returned, error, overshoot, pulses, views}."""
    view = ctx.observe(False)
    frame = ctx._pad_frame(view)
    if frame is None:
        raise ctx.failed("base_return_2d_no_pad_frame", f"BASE_RETURN (return_2d): PAD_FRAME is not valid; the base stays {advanced:.0f} mm ahead of its start", advanced_mm=advanced)
    error = return_error_2d(ctl, reference, frame, view["measured"])
    if not error["comparable"]:
        return {"returned": None, "error": error, "overshoot": False, "pulses": [], "views": [view]}
    views, pulses, tolerance = [view], [], ctl.BASE_RETURN_TOLERANCE_MM
    for pulse in range(ctl.BASE_MAX_PULSES):
        along = error["along_start_axis_mm"]
        if abs(along) <= tolerance or along < -tolerance or not error["comparable"]:
            break
        duration = ctl.base_pulse_s(along - 3.0, rate, first=False)
        ctx.state("BASE_RETURN_2D", pulse=pulse, along_start_axis_mm=round(along, 1), lateral_mm=round(error["lateral_mm"], 1), yaw_deg=round(error["yaw_deg"], 2), duration_s=round(duration, 3))
        moved, frame = ctx.measured_base_pulse(-ctl.BASE_VX_MPS, duration, False, error["anchor_pad_m"], reference["axis"], frame)
        view = ctx._pulse_view
        views.append(view)
        error = return_error_2d(ctl, reference, frame, view["measured"])
        rate = min(max(abs(moved["advance_mm"]) / duration, ctl.BASE_RATE_BOUNDS_MM_S[0]), ctl.BASE_RATE_BOUNDS_MM_S[1])
        pulses.append({"pulse": pulse, "duration_s": duration, "pulse_along_mm": float(moved["advance_mm"]), "pulse_lateral_mm": float(moved["lateral_mm"])})
        ctx.state("BASE_RETURN_2D_MEASURED", pulse=pulse, pulse_along_mm=round(moved["advance_mm"], 1), pulse_lateral_mm=round(moved["lateral_mm"], 1),
                  along_start_axis_mm=round(error["along_start_axis_mm"], 1), lateral_mm=round(error["lateral_mm"], 1), yaw_deg=round(error["yaw_deg"], 2),
                  comparable=error["comparable"])
    overshoot = bool(error["comparable"]) and error["along_start_axis_mm"] < -tolerance
    return {"returned": returned_2d(ctl, error), "error": error, "overshoot": overshoot, "pulses": pulses, "views": views}


@POLICY.decides
def run_return(ctx, ctl, summary: dict, *, advanced: float, anchor, axis, rate: float, final: dict, worst: float, margin: float, placed: dict, start=None) -> dict:
    """BASE_RETURN, then the end of the run: `summary` (the cycle's result) when the cube is placed and the base is back,
    else the CycleFailed the ending names. `advanced` is the measured base advance of this run (0.0 when the base never
    moved), `anchor` and `axis` what the advance loop ended on, `rate` its measured rate; `final`, `worst`, `margin` and
    `placed` are the EXIT view, the predicted worst-case margin, the measured margin and the placement PLACED read.
    `start` is SEARCH's outcome (start_reference): the SEARCH view the direct error is measured from. Without it nothing
    of the direct error is stated and return_2d runs the proven return."""
    returned_views, not_returned = (), {}
    if advanced:  # whatever PLACED says: the next pickup needs the base where this one started
        summary["base_returned"] = False
        try:  # the SEARCH view is evidence first: a defect in reading it costs the direct error, never the return
            reference, unreadable = start_reference(ctl, start), False
        except Exception:  # noqa: BLE001 -- with no word the proven return decides; under the word it runs and says so
            reference, unreadable = None, True
        outcome = None
        if RETURN_2D in _run_words(ctl):
            if reference is None:
                ctx.state("BASE_RETURN_2D_UNAVAILABLE", reference=None, note="the SEARCH view handed to the return could not be read; the proven return runs" if unreadable
                          else "no SEARCH view was handed to the return; the proven return runs")
            else:
                outcome = return_base_to_start(ctx, ctl, reference, advanced, rate)
                if outcome["returned"] is None:
                    ctx.state("BASE_RETURN_2D_UNAVAILABLE", reference=REFERENCE_SEARCH_VIEW, posture_difference_deg=round(outcome["error"]["posture_difference_deg"], 2),
                              distortion_mm=round(outcome["error"]["distortion_mm"], 2), note="the SEARCH view is not comparable with this one; the proven return runs")
                    outcome = None
        if outcome is None:
            remaining, sideways = ctx.return_base(advanced, anchor, axis, rate)
            summary["base_returned"] = abs(remaining) <= ctl.BASE_RETURN_TOLERANCE_MM
            aside = ctx.events.base_aside_mm  # every measured pulse's lateral_mm, forward and reverse, accumulated by the events object (None with events off)
            ctx.state("BASE_RETURNED", value=summary["base_returned"], remaining_along_axis_mm=round(remaining, 1), sideways_mm=round(sideways, 1), tolerance_mm=ctl.BASE_RETURN_TOLERANCE_MM,
                      **ctx._envelope_fields(base_aside_mm=None if aside is None else round(aside, 1)))
            ctx.events.predicate("base_returned", summary["base_returned"], measured=abs(remaining), threshold=ctl.BASE_RETURN_TOLERANCE_MM, unit="mm", pad_measurements=ctx.events.pad_measurements(False),
                                 scalars=("remaining_along_axis_mm", "sideways_mm"), measurements={"base_aside_mm": aside})
            last_view = getattr(ctx, "_pulse_view", None)
            if reference is not None:
                try:  # events only: a defect here costs the predicate, never the run
                    error, last_view = _error_of_last_view(ctx, ctl, reference)
                    if error is not None:
                        _state_error(ctx, ctl, error, reference, last_view)
                except Exception:  # noqa: BLE001 -- the direct error is evidence beside the proven decision, never a part of it
                    pass
            returned_views = tuple(view for view in ((reference or {}).get("record"), last_view) if view)
            not_returned = {"remaining_along_axis_mm": remaining, "sideways_mm": sideways, "base_return_tolerance_mm": ctl.BASE_RETURN_TOLERANCE_MM}
        else:
            error, tolerance, last_view = outcome["error"], ctl.BASE_RETURN_TOLERANCE_MM, outcome["views"][-1]
            summary["base_returned"] = outcome["returned"]
            aside = ctx.events.base_aside_mm
            ctx.state("BASE_RETURNED", value=summary["base_returned"], remaining_along_axis_mm=round(error["along_start_axis_mm"], 1), sideways_mm=round(error["lateral_mm"], 1),
                      tolerance_mm=tolerance, yaw_deg=round(error["yaw_deg"], 2), reference=REFERENCE_SEARCH_VIEW, overshoot_refused=outcome["overshoot"],
                      pulse_lateral_mm=[round(pulse["pulse_lateral_mm"], 1) for pulse in outcome["pulses"]], **ctx._envelope_fields(base_aside_mm=None if aside is None else round(aside, 1)))
            measurements = {**_error_measurements(error, reference, last_view), "overshoot_refused": outcome["overshoot"], "reverse_pulses": len(outcome["pulses"]),
                            **{f"pulse_{pulse['pulse']}_lateral_mm": pulse["pulse_lateral_mm"] for pulse in outcome["pulses"]}}
            ctx.events.predicate("base_returned", summary["base_returned"], measured=max(abs(error["along_start_axis_mm"]), abs(error["lateral_mm"])), threshold=tolerance, unit="mm",
                                 frame_pair=(reference["record"], last_view), scalars=("along_start_axis_mm", "lateral_mm", "yaw_deg"), measurements=measurements)
            _state_error(ctx, ctl, error, reference, last_view)
            returned_views = (reference["record"], outcome["views"][0], last_view)
            not_returned = {"along_start_axis_mm": error["along_start_axis_mm"], "lateral_mm": error["lateral_mm"], "yaw_deg": error["yaw_deg"], "base_return_tolerance_mm": tolerance,
                            "overshoot_refused": outcome["overshoot"], "views_comparable": error["comparable"], "reverse_pulses": len(outcome["pulses"])}
    if not summary["placed"]:
        if ctx.target is not None:
            attribution = ctx._mission["placement"]
            why = ("the EXIT view shows no block on the pad at this run's own release point; other blocks on the pad are not looked at"
                   if attribution.reason_code == ctl.target_binding.PLACED_NO_CUBE_AT_RELEASE else "the blocks of the EXIT view could not be listed completely, so none is credited")
            raise ctx.failed(attribution.reason_code, f"PLACED not demonstrated for this mission target ({attribution.reason_code}): {why}", evidence=final,
                             on_pad_count=attribution.on_pad_count, release_distance_mm=attribution.release_distance_mm,
                             release_tolerance_mm=attribution.release_tolerance_mm, predicted_worst_case_centre_margin_mm=worst)
        raise ctx.failed("placed_not_demonstrated", "PLACED not demonstrated by the frozen perception from the low pose", evidence=final,
                         centre_to_edge_mm=margin, placed_min_edge_margin_mm=ctl.PLACED_MIN_EDGE_MARGIN_MM, inside_pad_outline=bool(placed.get("inside_pad_outline")),
                         predicted_worst_case_centre_margin_mm=worst)
    if not summary["base_returned"]:
        raise ctx.failed("base_not_returned", "PLACED is demonstrated, but the base is NOT back at its start within the tolerance", evidence=returned_views or None, **not_returned)
    return summary
