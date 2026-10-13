# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The SEARCH stage of the single-cube cycle: SEARCH, SEARCH_SWEEP, the selection and binding of the block, and every
refusal decided from the SEARCH frame before anything is picked up.

Called by run_single_cube_cycle.Cycle.run_cycle with ctx (the Cycle) and ctl (the controller's namespace,
stage_policy.ControllerView); stage_policy's module docstring says why a stage never imports the controller. The
controller's Cycle._search_sweep, Cycle._sweep_sector_outcome and Cycle._refuse_pregrasp_beyond_pan_bounds are these
functions. The coarse pregrasp and the pickup band are ALIGN's (policy_align), asked for here in the order SEARCH always
decided them. The mission selection (Cycle._mission_search and the target_binding module) stays with the controller.
"""
import json
import math
from dataclasses import dataclass
from typing import Optional

import placement_envelope
import policy_align
import policy_placement
import scene_scan as scene_model  # autonomy/perception/scene_scan.py: the fused scene (named apart from scene_scan below)
import stage_policy
from stage_policy import CycleFailed

# The predicates SEARCH decides. placement_plan is the pre-pickup feasibility, stated through the events hook placement_infeasible.
# The words of this stage (stage_policy, THE WORDS; docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2.6), each OFF
# by default: scene_scan opens --do scan with the views at +/-15 deg; scene_scan_25 adds the +/-25 deg views and builds on
# scene_scan (or scene_search); scene_search makes SEARCH decide from the scene. With none of them the stage is the proven
# one. A word joins QUALIFIED only in the commit that adds its docs/autonomous-demo/qualification-<word>-<date>.md record.
# search_sweep opens the SEARCH SWEEP (search_sweep below): no recorded run has panned the low pose 25 degrees either way,
# so with no word a low-pose frame with no usable cube is refused before any motion, in a LIVE run, a rehearsal and a
# plan-only run alike: search_no_cube in a single-cube run, as the 2026-09-20 tag refused it, and in a mission run the
# target selection's own refusal of that frame (mission_binding_no_candidate when it lists no cube), as before the sweep
# was added. The sweep's qualification is the attended sequence of docs/autonomous-demo/one-block-demo-2026-09-26.md (T2):
# a plan-only sweep, then one attended sweep, then three starts.
SCENE_SCAN_WORD, SCENE_SCAN_25_WORD, SCENE_SEARCH_WORD, SEARCH_SWEEP_WORD = "scene_scan", "scene_scan_25", "scene_search", "search_sweep"
WORDS = (SCENE_SCAN_WORD, SCENE_SCAN_25_WORD, SCENE_SEARCH_WORD, SEARCH_SWEEP_WORD)
QUALIFIED = frozenset()
POLICY = stage_policy.StagePolicy("search", __file__, ("cube_seen_with_a_contact_point", "sweep_sector_cube_seen_with_a_contact_point", "pregrasp_pan_within_return_bound", "already_placed", "placement_plan",
                                                       "scene_one_unplaced_block"), WORDS, QUALIFIED)


@dataclass(frozen=True)
class SearchOutcome:
    """SEARCH's result. `done` is the cycle's summary when the cube is already on the pad (DONE_ALREADY_PLACED: nothing
    is picked up and every other field is None); otherwise `measured` is the pose of the SEARCH frame (the centre, the
    sweep sector that saw the cube, or under scene_search the chosen block's best view; after a scene scan the arm stands
    at the saved low pose, which only the pregrasp entry bound reads), `seen` its selected cube record, `template` that record's PAD_FRAME placement,
    `search_frame` its pad homography, `start_inside` the cube's signed margin inside the pad and `coarse` the coarse
    pregrasp."""
    measured: Optional[dict] = None
    seen: Optional[dict] = None
    template: Optional[dict] = None
    search_frame: Optional[dict] = None
    start_inside: Optional[float] = None
    coarse: Optional[dict] = None
    done: Optional[dict] = None


@POLICY.decides
def sweep_sector_outcome(ctx, ctl, view: dict):
    """(the record, "cube") when the LAST record of a sweep sector view verifies a cube with a contact pixel whose centre
    is placed in a valid PAD_FRAME, the two things the unchanged SEARCH needs of its frame; (the record, "pad_frame_invalid")
    for a verified cube the pad print does not place from that pan; (None, "no_cube") otherwise."""
    seen = ctx._cube(view)
    if seen is None or not seen["cube"].get("contact_px"):
        return None, "no_cube"
    if not (seen.get("cube_by_template") or {}).get("cube_center_pad_m") or ctx._pad_frame(view) is None:
        return seen, "pad_frame_invalid"
    return seen, "cube"


@POLICY.decides
def search_sweep(ctx, ctl, search: dict, measured: dict):
    """SEARCH SWEEP (the module docstring). The frame already taken verifies a cube with a contact pixel: nothing here runs
    and (measured, search) are returned as they were. Otherwise the wrist pans through two sectors, LEFT then RIGHT, at
    SEARCH_SWEEP_DEG either side of the FRESH measured pan, built by the proven scan tooling (run_stage1_scan.sweep_poses:
    lift, elbow and wrist_flex held exactly) and each run through run_pose with every check an arm move has (the abort
    flag per segment, base zero before and after, arrival, the measured height, the wrist ceiling); RIGHT is reached
    through the centre pose, because the planner's entry bound refuses a 2 x SEARCH_SWEEP_DEG pan in one call. Every
    sector frame is perceived by the unchanged frozen perception, one SEARCH_SWEEP state names the sector, its measured
    pan, the outcome and the observation, and the first sector with a usable cube ends the sweep there: its view is the
    SEARCH frame from then on and its measured pose the pose the coarse pregrasp and a mission selection are computed from.
    No usable sector: the arm returns to the centre pose through run_pose and the cycle fails search_no_cube_after_sweep.
    An executor abort inside a sector propagates as it does from any run_pose (exit 4, the arm holds where it stopped).
    Before the first sector move both sector pans are stated against the scan policy bound (SCAN_PAN_ABS_MAX_DEG): a
    sector beyond it is refused here, search_sweep_sector_beyond_pan_policy, with nothing commanded.
    Only under the word search_sweep (WORDS): without it nothing here runs either, and SEARCH refuses the frame it has
    with nothing commanded: search_no_cube in a single-cube run, the target selection's refusal in a mission run."""
    seen = ctx._cube(search)
    if seen is not None and seen["cube"].get("contact_px"):
        return measured, search
    if SEARCH_SWEEP_WORD not in POLICY.run_words(ctl):  # the proven SEARCH: the frame it has decides, and nothing moves
        return measured, search
    # seen is None when nothing was recognized at all, and the record itself when it was recognized without a contact
    # point: either way the predicate names exactly the frames the code had, which is none in the first case.
    ctx.events.predicate("cube_seen_with_a_contact_point", False, frames=seen, scalars=("cube_detected", "cube_contact_px"))
    centre, left, centre_2, right, centre_3 = ctl.cli.sweep_poses(ctx.pose(), ctl.SEARCH_SWEEP_DEG)
    # Both sector pans against the scan policy bound BEFORE the first sector move: the planner checks each pose when its
    # call comes to be planned, which for RIGHT is after LEFT has already been run, a refusal after motion.
    pans = {"LEFT": float(left.joints["shoulder_pan"]), "RIGHT": float(right.joints["shoulder_pan"])}
    beyond = [sector for sector, pan in pans.items() if abs(pan) > ctl.SCAN_PAN_ABS_MAX_DEG]
    if beyond:
        raise ctx.failed("search_sweep_sector_beyond_pan_policy", f"SEARCH: the {beyond[0]} sweep sector at pan {pans[beyond[0]]:.2f} lies beyond the scan policy bound of "
                          f"plus or minus {ctl.SCAN_PAN_ABS_MAX_DEG:.0f} deg (the sectors are {ctl.SEARCH_SWEEP_DEG:.0f} deg either side of the pan the arm stands at, "
                          f"{float(centre.joints['shoulder_pan']):.2f}); nothing was commanded", evidence=search, sector=beyond[0], left_pan_deg=pans["LEFT"], right_pan_deg=pans["RIGHT"],
                          centre_pan_deg=float(centre.joints["shoulder_pan"]), scan_pan_abs_max_deg=ctl.SCAN_PAN_ABS_MAX_DEG, sweep_deg=ctl.SEARCH_SWEEP_DEG)
    outcomes, view = {}, None
    for sector, path in (("LEFT", [left]), ("RIGHT", [centre_2, right])):
        view = ctx.run_pose(path, False, None, label=f"SEARCH_SWEEP {sector}")
        seen, outcome = ctx._sweep_sector_outcome(view)
        pan = float(view["measured"]["shoulder_pan"])
        outcomes[sector] = (outcome, pan)
        ctx.events.predicate("sweep_sector_cube_seen_with_a_contact_point", outcome == "cube", measured=pan, unit="deg", frames=view,
                              scalars=("cube_detected", "cube_contact_px", "pad_frame_valid", "shoulder_pan_deg"))
        ctx.state("SEARCH_SWEEP", sector=sector, pan_deg=round(pan, 2), outcome=outcome, observation_id=(view.get("perception") or [{}])[-1].get("observation_id"), evidence=view)
        if outcome == "cube":
            return view["measured"], view
    ctx.run_pose(centre_3, False, None, label="SEARCH_SWEEP return to centre")
    raise ctx.failed("search_no_cube_after_sweep", f"SEARCH: no cube with a usable contact point in the low-pose wrist frame, nor in its sweep sectors LEFT and RIGHT "
                      f"({ctl.SEARCH_SWEEP_DEG:.0f} deg of pan either side); the arm is back at the centre pose", evidence=view, sweep_deg=ctl.SEARCH_SWEEP_DEG,
                      left_outcome=outcomes["LEFT"][0], left_pan_deg=outcomes["LEFT"][1], right_outcome=outcomes["RIGHT"][0], right_pan_deg=outcomes["RIGHT"][1])


@POLICY.decides
def refuse_pregrasp_beyond_pan_bounds(ctx, ctl, coarse_pan_deg: float, pan_now_deg: float, seen) -> None:
    """RETURN BOUND (the module docstring, SEARCH SWEEP), decided from the SEARCH frame before anything is picked up. The
    coarse pregrasp pan is held to two bounds of the planner the cycle's arm moves all go through. First the return: the
    EXIT after the release, and the HOLD_OBSERVE rise of a base advance with the cube held, each plan ONE pose at the
    saved low pose's pan, which plan_scan_segments refuses from a pan more than MAX_ENTRY_PAN_SWEEP_DEG away; a pregrasp
    commanded farther than RETURN_PAN_BOUND_DEG from that pan could be planned, picked and released, and then not returned
    from, which is a refusal AFTER motion, against this controller's rule. Second the entry: the pregrasp call itself is
    planned from the pan the arm stands at (the centre, or the sweep sector that saw the cube) and is refused by the same
    bound from farther than MAX_ENTRY_PAN_SWEEP_DEG; stated here by name instead of surfacing as a refused non-contact pose.
    Both leave the arm where the frame was taken, a pan every return can plan from."""
    home_pan = ctx._home_pan_deg()
    from_home = abs(coarse_pan_deg - home_pan)
    ctx.events.predicate("pregrasp_pan_within_return_bound", from_home <= ctl.RETURN_PAN_BOUND_DEG, measured=from_home, threshold=ctl.RETURN_PAN_BOUND_DEG, unit="deg", frames=seen,
                          scalars=("coarse_pan_deg", "home_pan_deg"))
    if from_home > ctl.RETURN_PAN_BOUND_DEG:
        raise ctx.failed("search_cube_beyond_return_bound", f"SEARCH: the cube's coarse pregrasp pan {coarse_pan_deg:.2f} lies {from_home:.1f} deg from the saved low pose's pan "
                          f"{home_pan:.2f}, beyond the {ctl.RETURN_PAN_BOUND_DEG:.0f} deg a return to that pose can be planned from; nothing was picked up", evidence=seen,
                          coarse_pan_deg=coarse_pan_deg, home_pan_deg=home_pan, pan_from_home_deg=round(from_home, 2), return_pan_bound_deg=ctl.RETURN_PAN_BOUND_DEG)
    from_now = abs(coarse_pan_deg - pan_now_deg)
    if from_now > ctl.MAX_ENTRY_PAN_SWEEP_DEG:
        raise ctx.failed("search_cube_beyond_pregrasp_entry_bound", f"SEARCH: the cube's coarse pregrasp pan {coarse_pan_deg:.2f} lies {from_now:.1f} deg from the pan the arm stands at "
                          f"({pan_now_deg:.2f}), beyond the {ctl.MAX_ENTRY_PAN_SWEEP_DEG:.0f} deg the planner enters a pregrasp from; nothing was picked up", evidence=seen,
                          coarse_pan_deg=coarse_pan_deg, pan_now_deg=pan_now_deg, pan_from_now_deg=round(from_now, 2), entry_pan_bound_deg=ctl.MAX_ENTRY_PAN_SWEEP_DEG)


# One class per seen block (classify_block): what SEARCH, a mission binding and a scene map say of it.
ALREADY_PLACED, ARM_ONLY, BASE_ADVANCE, NOT_PLACEABLE = "ALREADY_PLACED", "ARM_ONLY", "BASE_ADVANCE", "NOT_PLACEABLE"
TOO_NEAR, OUTSIDE_PAN_BAND, SEEN_NOT_MEASURABLE, NO_PAD_FRAME = "TOO_NEAR", "OUTSIDE_PAN_BAND", "SEEN_NOT_MEASURABLE", "NO_PAD_FRAME"


@POLICY.decides
def classify_block(ctx, ctl, record: dict, pan_deg: float, stands_at_pan_deg: Optional[float] = None) -> dict:
    """The class of ONE seen block, its perception `record` taken at the measured pan `pan_deg`, by the checks SEARCH makes
    before anything moves, in SEARCH's order, with SEARCH's reason code for every class that is a refusal there:
    SEEN_NOT_MEASURABLE (no verified cube with a contact pixel), NO_PAD_FRAME (no PAD_FRAME placement, or no pad
    homography), ALREADY_PLACED, OUTSIDE_PAN_BAND (the return bound, then the pregrasp entry bound), TOO_NEAR (the pickup
    band), NOT_PLACEABLE (no bounded placement), else the placement mode, ARM_ONLY or BASE_ADVANCE. Returns {"class",
    "reason_code" (None for ALREADY_PLACED and the two modes), "values"}. An OUTSIDE_PAN_BAND block's values also carry
    "placeable", with "placement_mode" when a bounded placement admits it, else the placement limits a NOT_PLACEABLE block
    carries (placement_refusal_values). Commands nothing and states nothing: SEARCH states
    its own predicates and states. The pregrasp entry bound is taken from the pan the arm will stand at when the pregrasp
    is commanded: `stands_at_pan_deg` when given (after a scene scan the arm stands at the saved low pose, not at the view
    that saw the block), else the view's own pan, as SEARCH from one frame has it."""
    cube = (record or {}).get("cube") or {}
    if not cube.get("detected") or not cube.get("contact_px"):
        return {"class": SEEN_NOT_MEASURABLE, "reason_code": "search_no_cube", "values": {}}
    template = record.get("cube_by_template") or {}
    if not template.get("cube_center_pad_m"):
        return {"class": NO_PAD_FRAME, "reason_code": "search_cube_not_in_pad_frame", "values": {}}
    frame = ctx._pad_frame({"perception": [record]})
    if frame is None:
        return {"class": NO_PAD_FRAME, "reason_code": "search_no_pad_frame", "values": {}}
    centre = template["cube_center_pad_m"]
    inside = ctl.placed_margin_mm(centre)
    if inside < 0.0:
        inside = -ctl.distance_outside_pad_mm(centre)
    if inside >= ctl.PLACED_MIN_EDGE_MARGIN_MM:
        return {"class": ALREADY_PLACED, "reason_code": None, "values": {"cube_centre_to_pad_edge_mm": inside}}
    coarse = policy_align.coarse_pregrasp(ctx, ctl, record, {"shoulder_pan": pan_deg})

    def beyond_band(reason_code: str, band: dict) -> dict:
        # The pan band decides first, in SEARCH's order; the placement is still stated, so a block refused on the band
        # also names whether a placement admits it and, when none does, the placement limits.
        try:
            direction = ctl.reach_direction_pad(frame["homography_image_to_pad"], cube["contact_px"])
            mode, why = policy_placement.classify_for_binding(ctx, ctl, centre, direction, max(float(coarse["cube_out_mm"]), 0.0), record)
        except Exception:  # noqa: BLE001 -- the placement beside a band refusal is evidence; its failure costs that evidence, never the class
            return {"class": OUTSIDE_PAN_BAND, "reason_code": reason_code, "values": band}
        placement = {"placeable": True, "placement_mode": mode} if mode is not None else {"placeable": False, **{k: v for k, v in why.items() if k != "sentence"}}
        return {"class": OUTSIDE_PAN_BAND, "reason_code": reason_code, "values": {**band, **placement}}

    from_home = abs(coarse["pan_deg"] - ctx._home_pan_deg())
    if from_home > ctl.RETURN_PAN_BOUND_DEG:
        return beyond_band("search_cube_beyond_return_bound", {"pan_from_home_deg": from_home, "return_pan_bound_deg": ctl.RETURN_PAN_BOUND_DEG})
    from_now = abs(coarse["pan_deg"] - float(pan_deg if stands_at_pan_deg is None else stands_at_pan_deg))
    if from_now > ctl.MAX_ENTRY_PAN_SWEEP_DEG:
        return beyond_band("search_cube_beyond_pregrasp_entry_bound", {"pan_from_now_deg": from_now, "entry_pan_bound_deg": ctl.MAX_ENTRY_PAN_SWEEP_DEG})
    cube_out = float(coarse["cube_out_mm"])
    if cube_out < ctl.PREGRASP_MIN_CUBE_OUT_MM:
        return {"class": TOO_NEAR, "reason_code": "search_cube_inside_pregrasp_reach", "values": {"cube_out_mm": cube_out, "move_block_at_least_mm": round(ctl.PREGRASP_OUTWARD_BIAS_MM - cube_out, 1)}}
    direction = ctl.reach_direction_pad(frame["homography_image_to_pad"], cube["contact_px"])
    mode, why = policy_placement.classify_for_binding(ctx, ctl, centre, direction, max(cube_out, 0.0), record)
    if mode is None:
        return {"class": NOT_PLACEABLE, "reason_code": "search_placement_infeasible", "values": why}
    return {"class": mode, "reason_code": None, "values": {"cube_out_mm": cube_out}}


# THE SCENE SCAN (--do scan, and SEARCH under scene_search). Pan-only views at the low-pose shape, each one single-pose
# call through the unchanged run_pose (every check an arm move has: the abort flag per segment, base zero before and after,
# arrival, the measured height, the wrist ceiling), so every captured frame is a view and none is a transit duplicate.
# Every step between two views is at most 15 deg under scene_scan and at most 25 deg under scene_scan_25 (minus_25 to
# centre, plus_25 to the EXIT), inside the planner's entry bound MAX_ENTRY_PAN_SWEEP_DEG (30). The scan ends with the
# EXIT to the saved low pose and its SETTLE (Cycle.return_to_low_pose), whose EXIT frame is the last home view.
SCAN_VIEWS = (("home", 0.0), ("minus_15", -15.0), ("centre", 0.0), ("plus_15", 15.0))
SCAN_VIEWS_25 = (("home", 0.0), ("minus_15", -15.0), ("minus_25", -25.0), ("centre", 0.0), ("plus_15", 15.0), ("plus_25", 25.0))
EXIT_VIEW = "exit"  # the EXIT frame at the saved low pose, the scan's last view


def scan_views(words) -> tuple:
    """The (name, pan offset from the saved low pose) of every view a scan under `words` commands, before its EXIT."""
    return SCAN_VIEWS_25 if SCENE_SCAN_25_WORD in words else SCAN_VIEWS


@POLICY.decides
def _enumerating_detector(ctx, ctl) -> None:
    """Every verified block of a view is listed by cube_candidates from the detector's own cached proposals, as a mission
    run lists them (Cycle._caching_detector). A run that perceives on this computer and has made no detector yet gets that
    caching detector here, stated as the controller states a detector it makes; a desktop perception run already lists
    through its own detections."""
    if getattr(ctx, "perception", None) is not None or getattr(ctx, "detector", None) is not None or ctx.plan_only:
        return
    started = ctl.time.monotonic_ns()
    ctx.detector = ctx._caching_detector()
    ctx.events.detector_made(True, started, ctx.detector)


def _view_entries(ctx, record) -> tuple:
    """(every verified cube of the view as {cube, cube_by_template}, how the list was made). A listing that refused, raised
    or stopped with proposals left is not used: the view then carries its frozen record's own cube only. An error raised
    once the stop is requested propagates as the stop it is."""
    if not record:
        return [], "no_record"
    try:
        listed, code, _cause = ctx._mission_candidates(record)
    except Exception:  # noqa: BLE001 -- a listing is evidence beside the frozen record; its failure costs the listing, never the scan
        if (getattr(ctx, "abort", None) or {}).get("set"):
            raise  # a stop the listing observed is the scan's stop, never a failed listing
        listed, code = [], "listing_failed"
    if code is None:
        return [{"cube": entry.get("cube"), "cube_by_template": entry.get("cube_by_template")} for entry in listed], "complete"
    return [{"cube": record.get("cube"), "cube_by_template": record.get("cube_by_template")}], code


def _scene_view(ctx, name: str, result: dict) -> dict:
    record = (result.get("perception") or [None])[-1]
    entries, listing = _view_entries(ctx, record)
    return {"name": name, "pan_deg": float(result["measured"]["shoulder_pan"]), "joints": dict(result["measured"]), "observation_id": (record or {}).get("observation_id"),
            "record": record, "entries": entries, "listing": listing, "result": result}


def _refuse_scan_start(ctl, measured: dict, home: dict, views) -> None:
    """Before anything is commanded: the gripper open, the arm at the saved low pose, every view pan inside the scan
    policy bound and every step between views inside the planner's entry bound. A ScanRefused names the first that fails."""
    refusal = ctl.gripper_refusal(measured, False)
    if refusal:
        raise ctl.ScanRefused(f"SCAN: {refusal}; the scene scan starts only with the gripper open and nothing held; nothing was commanded")
    if ctl.base_posture_refusal(measured, home):  # every arm joint within BASE_POSTURE_TOLERANCE_DEG of the saved low pose (fails closed)
        off = {name: round(float(measured.get(name, float("nan"))) - float(home[name]), 2) for name in ctl.SCAN_JOINTS}
        raise ctl.ScanRefused(f"SCAN: the arm is not at the saved low scan pose, where every view of the scene scan is taken (off by {off} deg, tolerance "
                              f"{ctl.BASE_POSTURE_TOLERANCE_DEG}); nothing was commanded")
    home_pan = float(home["shoulder_pan"])
    pans = [(name, home_pan + offset) for name, offset in views]
    beyond = [(name, pan) for name, pan in pans if abs(pan) > ctl.SCAN_PAN_ABS_MAX_DEG]
    if beyond:
        raise ctl.ScanRefused(f"SCAN: the {beyond[0][0]} view at pan {beyond[0][1]:.2f} lies beyond the scan policy bound of plus or minus {ctl.SCAN_PAN_ABS_MAX_DEG:.0f} deg "
                              f"(the saved low pose's pan is {home_pan:.2f}); nothing was commanded")
    path = [float(measured["shoulder_pan"])] + [pan for _name, pan in pans] + [home_pan]
    if any(abs(b - a) > ctl.MAX_ENTRY_PAN_SWEEP_DEG for a, b in zip(path, path[1:])):
        raise ctl.ScanRefused(f"SCAN: a step between two views changes the pan by more than the {ctl.MAX_ENTRY_PAN_SWEEP_DEG:.0f} deg the planner enters a pose from; nothing was commanded")


@POLICY.decides
def _take_views(ctx, ctl, words, label: str) -> tuple:
    """The views of one scan, taken. Returns (the views, a partial-ending cause or None). Refusals before the first motion
    raise ScanRefused; an abort, a stop or a refused perception after motion is returned as the partial cause with the
    exception, so the caller writes the partial scene before it propagates."""
    home = json.loads(ctl.HOME_FILE.read_text())["measured_joints_deg"]
    views = scan_views(words)
    measured = ctx.pose()
    _refuse_scan_start(ctl, measured, home, views)
    _enumerating_detector(ctx, ctl)
    low = {name: float(home[name]) for name in ctl.SCAN_JOINTS}
    taken, moved = [], False
    try:
        for name, offset in views:
            pose = ctl.cli._pose(f"scene-{name}", "FAR", {**low, "shoulder_pan": round(low["shoulder_pan"] + offset, 3)})
            result = ctx.run_pose(pose, False, None, label=f"{label} {name}")
            moved = moved or not ctx.plan_only
            if not ctx.plan_only:
                taken.append(_scene_view(ctx, name, result))
                _state_view(ctx, taken[-1])
        final = ctx.return_to_low_pose()  # the EXIT to the saved low pose, then its SETTLE (the verify-only host refuses the next start without it)
        if not ctx.plan_only:
            taken.append(_scene_view(ctx, EXIT_VIEW, final))
            _state_view(ctx, taken[-1])
    except ctl.ScanRefused as refusal:
        if not moved:
            raise
        return taken, ("refused", refusal)
    except (RuntimeError, CycleFailed) as stopped:
        return taken, ("stopped", stopped)
    return taken, None


def _state_view(ctx, view: dict) -> None:
    template = (view["record"] or {}).get("pad_by_template") or {}
    ctx.state("SCENE_SCAN", view=view["name"], pan_deg=round(view["pan_deg"], 2), observation_id=view["observation_id"], pad_frame_valid=bool(template.get("pad_frame_valid")),
              blocks_seen=sum(1 for entry in view["entries"] if (entry.get("cube") or {}).get("detected")), evidence=view["result"])


@POLICY.decides
def _fused(ctx, ctl, taken: list, words, partial: Optional[str]) -> dict:
    """The scene of the views taken (scene_scan.fuse), each block classed by classify_block at its best view's pan with the
    pregrasp entered from the saved low pose, written as one JSON in the run's directory and stated as perception.result
    events (scene-view, scene-pad, scene-block) and one SCENE state."""
    home_pan = ctx._home_pan_deg()
    scene = scene_model.fuse(taken, lambda record, pan: classify_block(ctx, ctl, record, pan, stands_at_pan_deg=home_pan), home_pan,
                             tuple(ctl.LOW_POSE_REF_CONTACT_PX), ctl.RETURN_PAN_BOUND_DEG)
    scene.update(words=list(words), partial=partial, return_pan_bound_deg=ctl.RETURN_PAN_BOUND_DEG)
    name = f"scene-{ctl.time.strftime('%Y%m%dT%H%M%SZ', ctl.time.gmtime())}.json"
    try:
        ctx.session_dir.mkdir(parents=True, exist_ok=True)  # a scan stopped before its first capture has no session directory yet
        (ctx.session_dir / name).write_text(json.dumps(scene, indent=1, default=float))
        written = name
    except (OSError, AttributeError, TypeError, ValueError):
        written = None
    _emit_scene(ctx, ctl, scene)
    ctx.state("SCENE", views=len(scene["views"]), blocks=len(scene["blocks"]), fit_accepted=scene["fit"]["accepted"], partial=partial, scene_file=written,
              classes=[block["class"] for block in scene["blocks"]], evidence=[view["record"] for view in taken if view["record"]])
    for block in scene["blocks"]:
        where = "no position" if block["position_pad_m"] is None else f"PAD_FRAME ({block['position_pad_m'][0]:+.3f}, {block['position_pad_m'][1]:+.3f}) m ({block['position_source']})"
        bearing = "" if block["inferred_pan_from_home_deg"] is None else f", about {block['inferred_pan_from_home_deg']:+.1f} deg of pan from home by the pan-axis fit (inferred)"
        print(f"SCENE {block['id']}: {block['class']} at {where}, seen in {len(block['views_seen'])} view(s), best {block['best_view']}{bearing}", flush=True)
    return scene


def _block_measurements(ctl, block: dict) -> list:
    named = ctl._named
    position = block["position_pad_m"] or [None, None]
    values = block["values"] or {}
    out = [named("pad_x", position[0], "m"), named("pad_y", position[1], "m"), named("class", block["class"]), named("reason_code", block["reason_code"]),
           named("inferred", block["position_source"] != "direct"), named("best_observation", block["best_observation_id"]), named("best_view", block["best_view"]),
           named("best_pan_deg", block["best_pan_deg"], "deg"), named("views_seen", len(block["views_seen"])), named("spread_mm", block["spread_mm"], "mm")]
    if block["class"] == OUTSIDE_PAN_BAND and "pan_from_home_deg" in values:
        out += [named("pan_from_home_deg", round(values["pan_from_home_deg"], 1), "deg"), named("pan_band_deg", values.get("return_pan_bound_deg"), "deg"), named("pan_inferred", False)]
    elif block["outside_pan_band_inferred"] is not None:
        out += [named("pan_from_home_deg", block["inferred_pan_from_home_deg"], "deg"), named("pan_band_deg", ctl.RETURN_PAN_BOUND_DEG, "deg"), named("pan_inferred", True),
                named("outside_pan_band_inferred", block["outside_pan_band_inferred"])]
    if block["class"] == TOO_NEAR:
        out += [named("distance_to_move_mm", values.get("move_block_at_least_mm"), "mm"), named("cube_out_mm", values.get("cube_out_mm"), "mm")]
    if block["class"] == OUTSIDE_PAN_BAND and "placeable" in values:
        out.append(named("placeable", values["placeable"]))
    if block["class"] == NOT_PLACEABLE or (block["class"] == OUTSIDE_PAN_BAND and values.get("placeable") is False):
        side, required, need = values.get("side_margin_mm"), values.get("required_side_margin_mm"), values.get("axis_advance_needed_unbounded_mm")
        constraint = "side_edge" if side is not None and required is not None and side < required else ("along_budget" if need is not None else None)
        out += [named("binding_constraint", constraint), named("side_margin_mm", side, "mm"), named("required_side_margin_mm", required, "mm"),
                named("along_axis_need_mm", need, "mm"), named("advance_budget_mm", ctl.BASE_MAX_TOTAL_ADVANCE_MM, "mm"),
                named("deficit_mm", None if need is None else round(need - ctl.BASE_MAX_TOTAL_ADVANCE_MM, 1), "mm")]
    if block["class"] in (ARM_ONLY, BASE_ADVANCE):
        out.append(named("cube_out_mm", values.get("cube_out_mm"), "mm"))
    return out


@POLICY.decides
def _emit_scene(ctx, ctl, scene: dict) -> None:
    """The scene as perception.result events, through the events object's own emitter, as the frozen perception layers are
    stated (no contract change): one scene-view per view, one scene-pad for the fit, one scene-block per block. Events off:
    nothing. Whatever goes wrong here is counted as a failed hook and never changes the scan."""
    events = ctx.events
    if getattr(events, "enabled", None) is False:
        return
    try:
        named = ctl._named
        for index, view in enumerate(scene["views"]):
            ids = [view["observation_id"]] if view["observation_id"] else []
            corners = view["footprint_pad_m"][:: max(1, len(view["footprint_pad_m"]) // 8)][:8] if view["footprint_pad_m"] else []
            measurements = [named("view_index", index), named("view", view["name"]), named("pan_deg", view["pan_deg"], "deg"), named("pan_from_home_deg", view["pan_from_home_deg"], "deg"),
                            named("pad_frame_valid", view["pad_frame_source"] == "direct"), named("pad_frame_source", view["pad_frame_source"]),
                            named("accepted", view["pad_frame_source"] != "none"), named("landmark_inliers", view["landmark_inliers"]), named("blocks_seen", view["blocks_seen"])]
            for corner, (x, y) in enumerate(corners):
                measurements += [named(f"footprint_{corner}_x", x, "m"), named(f"footprint_{corner}_y", y, "m")]
            _emit_result(events, "scene-view", ids, "detected" if view["pad_frame_source"] != "none" else "not_detected", measurements)
        fit, reference = scene["fit"], scene["home_reference_point_pad_m"]
        pair = max(fit["pairs"], key=lambda item: abs(item.get("pan_change_deg") or 0.0) if item.get("used") else -1.0, default=None)
        axis = fit["axis_pad_m"] or [None, None]
        bearing = None
        if fit["axis_pad_m"] and reference:
            bearing = round(math.degrees(math.atan2(reference[1] - axis[1], reference[0] - axis[0])), 2)
        ids = [view["observation_id"] for view in scene["views"] if view["observation_id"]]
        _emit_result(events, "scene-pad", ids, "detected" if fit["accepted"] else "not_detected",
                     [named("axis_x", axis[0], "m"), named("axis_y", axis[1], "m"), named("home_pan_deg", scene["home_pan_deg"], "deg"), named("home_bearing_deg", bearing, "deg"),
                      named("pan_band_deg", ctl.RETURN_PAN_BOUND_DEG, "deg"), named("fit_accepted", bool(fit["accepted"])), named("reference_view", fit["reference"]),
                      named("fit_rotation_deg", None if not pair or not pair.get("used") else pair.get("rotation_deg"), "deg"),
                      named("measured_pan_change_deg", None if not pair or not pair.get("used") else pair.get("pan_change_deg"), "deg"),
                      named("fit_residual_mm", None if not pair or not pair.get("used") else pair.get("residual_median_mm"), "mm"),
                      named("landmark_inliers", (scene["pad"] or {}).get("landmark_inliers")), named("views_seen", len(scene["views"])), named("partial", scene.get("partial"))])
        for block in scene["blocks"]:
            _emit_result(events, "scene-block", block["observation_ids"] or ([block["best_observation_id"]] if block["best_observation_id"] else []),
                         "detected" if block["position_pad_m"] is not None else "ambiguous", _block_measurements(ctl, block))
    except Exception:  # noqa: BLE001 -- evidence only: a defect here costs the scene's events, never the scan
        failed = getattr(events, "_hook_failed", None)
        if callable(failed):
            failed("scene_scan_results")


def _emit_result(events, detector: str, observation_ids: list, result: str, measurements: list) -> None:
    envelope = {"provenance": events._provenance(observation_ids, calibration=True)}
    if observation_ids:
        envelope["observation_id"] = observation_ids[0]
    events._emit("perception.result", {"detector": detector, "observation_ids": list(observation_ids), "result": result, "confidence": None, "measurements": measurements}, **envelope)


@POLICY.decides
def scene_scan(ctx, ctl, plan_only: bool) -> dict:
    """--do scan (docs/autonomous-demo/overnight-plan-2026-09-26.md, track S): the SEARCH stage's scene scan, under the word
    scene_scan (the +/-25 deg views under scene_scan_25 as well). Without it: refused before any motion, the arm where it
    stands. With it: refused before any motion unless the gripper is open, the arm stands at the saved low pose and every
    view lies inside the scan policy bound; then the views (SCAN_VIEWS), the EXIT and its SETTLE, and the fused scene (one
    JSON in the run's directory, the scene-* perception.result events, the SCENE state). An abort or a stop after motion
    ends with the scene of the views taken, marked partial, and propagates (exit 4, the arm holds where it stopped); a
    step refused after motion is the cycle failure scene_scan_step_refused. A ScanRefused is the controller's exit 3.
    Plan-only prints every view's plan and commands nothing; `plan_only` must agree with the run's own plan-only mode, else
    the scan is refused before any motion. Returns the scene (or {"planned": True})."""
    words = POLICY.run_words(ctl)
    if SCENE_SCAN_WORD not in words:
        if SCENE_SCAN_25_WORD in words:
            raise ctl.ScanRefused("SCAN: scene_scan_25 adds the 25 deg views to the scene scan and builds on scene_scan, which this run does not name; nothing was commanded "
                                  "and the arm stands where it stood")
        raise ctl.ScanRefused("SCAN: the scene scan runs only under the word scene_scan, which this run does not name; nothing was commanded and the arm stands where it stood")
    if bool(plan_only) != bool(ctx.plan_only):  # the views are planned or moved by the run's own mode; an argument that disagrees is never guessed at
        raise ctl.ScanRefused(f"SCAN: the scan was asked for {'a plan only' if plan_only else 'motion'} and this run is {'plan-only' if ctx.plan_only else 'not plan-only'}; "
                              "nothing was commanded and the arm stands where it stood")
    taken, ended = _take_views(ctx, ctl, words, "SCENE_SCAN")
    if ctx.plan_only:
        print(f"PLAN ONLY: the scene scan planned {len(scan_views(words))} views and its return to the saved low pose; nothing was commanded.", flush=True)
        return {"planned": True}
    scene = _fused(ctx, ctl, taken, words, None if ended is None else ended[0])
    if ended is not None:
        cause, error = ended
        if cause == "refused":
            raise ctx.failed("scene_scan_step_refused", f"SCAN: a view of the scene scan was refused after the arm had moved ({error}); the scene of the {len(taken)} views taken is "
                             "written, marked partial; the arm holds where it stands", evidence=[view["record"] for view in taken if view["record"]], views_taken=len(taken))
        raise error
    return scene


@POLICY.decides
def run_search(ctx, ctl, summary: dict, measured: dict) -> SearchOutcome:
    """SEARCH from the low scan pose (`measured`, the start pose): the low-pose frame, the SEARCH SWEEP when that frame has
    no usable cube, the selection (the frozen record, or a mission target's binding), the pad frame, DONE_ALREADY_PLACED,
    the coarse pregrasp and every refusal taken before anything is picked up (the return and entry pan bounds, the pickup
    band, the placement feasibility, a mission target's mode and capability, a LIVE run's base need)."""
    ctx.state("SEARCH", pose={k: round(measured[k], 2) for k in ctl.SCAN_JOINTS})
    from_scene = SCENE_SEARCH_WORD in POLICY.run_words(ctl)  # SEARCH FROM THE SCENE (_search_from_scene), under its word only
    if from_scene:
        _enumerating_detector(ctx, ctl)
    search = ctx.observe(False)
    chosen = stands = None
    if from_scene:
        measured, search, chosen, stands = _search_from_scene(ctx, ctl, search, measured)
    else:
        measured, search = ctx._search_sweep(search, measured)  # the frame already taken, or the first sweep sector frame with a usable cube (SEARCH SWEEP)
    best = None if chosen is None else f"the scene's best view of the chosen block (observation {chosen.get('observation_id')})"  # SEARCH's frame when the scene chose
    if chosen is not None and ctx.target is None:
        seen = chosen
    else:
        seen = ctx._cube(search) if ctx.target is None else ctx._mission_search(search, measured)
    if seen is None or not seen["cube"].get("contact_px"):
        # seen is None when nothing was recognized at all, and the record itself when it was recognized without a
        # contact point: either way the predicate names exactly the frames the code had, which is none in the first case.
        ctx.events.predicate("cube_seen_with_a_contact_point", False, frames=seen, scalars=("cube_detected", "cube_contact_px"))
        raise ctx.failed("search_no_cube", f"SEARCH: no cube with a usable contact point in {'the low-pose wrist frame' if chosen is None else best}", evidence=seen)
    template = seen.get("cube_by_template") or {}
    if not template.get("cube_center_pad_m"):
        raise ctx.failed("search_cube_not_in_pad_frame", f"SEARCH: PAD_FRAME is not valid in {'the low-pose frame' if chosen is None else best}, so the carry distance to the pad is unknown", evidence=seen)
    search_frame = ctx._pad_frame({"perception": [seen]})
    if search_frame is None:
        raise ctx.failed("search_no_pad_frame", f"SEARCH: the pad homography is missing from {'the low-pose perception record' if chosen is None else 'the perception record of ' + best}", evidence=seen)
    start_inside = ctl.placed_margin_mm(template["cube_center_pad_m"])  # signed: negative = that far OUTSIDE the pad
    if start_inside < 0.0:
        start_inside = -ctl.distance_outside_pad_mm(template["cube_center_pad_m"])
    ctx.events.predicate("already_placed", start_inside >= ctl.PLACED_MIN_EDGE_MARGIN_MM, measured=start_inside, threshold=ctl.PLACED_MIN_EDGE_MARGIN_MM, unit="mm", frames=seen, scalars=("cube_centre_to_pad_edge_mm",))
    if start_inside >= ctl.PLACED_MIN_EDGE_MARGIN_MM:
        if ctx.target is not None:  # unreachable after a selection (a candidate is never placed); kept as the refusal it must be if it ever is
            raise ctx.failed("mission_selected_cube_already_placed", "SEARCH: the cube selected for this mission target is already on the pad; a mission target never finishes on a placed cube; "
                              "nothing was picked up", evidence=seen, cube_centre_to_pad_edge_mm=start_inside, placed_min_edge_margin_mm=ctl.PLACED_MIN_EDGE_MARGIN_MM)
        ctx.state("DONE_ALREADY_PLACED", cube_pad_m=template["cube_center_pad_m"], margin_mm=start_inside, evidence=seen)
        return SearchOutcome(done={**summary, "placed": True, "note": "the cube footprint is already inside the pad"})
    coarse = policy_align.coarse_pregrasp(ctx, ctl, seen, measured)
    ctx._refuse_pregrasp_beyond_pan_bounds(coarse["pan_deg"], float((measured if stands is None else stands)["shoulder_pan"]), seen)  # decided BEFORE anything is picked up (RETURN BOUND)
    ctx._refuse_pregrasp_inside_grasp_radius(coarse, seen)  # THE NEAR BLOCK: after the selection was written back, before anything moves
    search_direction = ctl.reach_direction_pad(search_frame["homography_image_to_pad"], seen["cube"]["contact_px"])
    try:  # decided BEFORE anything is picked up: a cube this cycle could not place is left where it lies
        search_plan = ctl.placement_plan(template["cube_center_pad_m"], search_direction, max(coarse["cube_out_mm"], 0.0), ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
    except CycleFailed as infeasible:
        # The post-grasp twin of this decision states a placement_plan predicate; so does this one, or a refusal that
        # happens BEFORE anything is picked up would be the one failure with no predicate of its own.
        ctx.events.placement_infeasible(template["cube_center_pad_m"], search_direction, max(coarse["cube_out_mm"], 0.0), seen, measured)
        raise ctx.failed("search_placement_infeasible", f"SEARCH: {infeasible}; nothing was picked up", evidence=seen,
                          anchor_pad_x_m=template["cube_center_pad_m"][0], anchor_pad_y_m=template["cube_center_pad_m"][1],
                          reach_direction_pad_x=search_direction[0], reach_direction_pad_y=search_direction[1],
                          anchor_margin_mm=ctl.placed_margin_mm(template["cube_center_pad_m"]), required_centre_margin_mm=ctl.PLACE_REQUIRED_CENTRE_MM,
                          bounded_base_advance_limit_mm=ctl.BASE_MAX_TOTAL_ADVANCE_MM, arm_loaded_reach_out_mm=ctl.ARM_LOADED_REACH_OUT_MM, grasp_out_mm=max(coarse["cube_out_mm"], 0.0),
                          **placement_envelope.explain_refusal(ctl, template["cube_center_pad_m"], search_direction, max(coarse["cube_out_mm"], 0.0), ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM))
    if ctx.target is not None:
        # The mode this run drives (the module docstring, MODE): the declaration, or for an auto target the mode its
        # binding measured from this very view. D5: a target that declares no capability and measures BASE_ADVANCE is
        # refused, never driven; an auto target takes the measured mode and the same check cannot fail for it.
        run_mode = ctx.target.run_mode(ctx._mission["selection"])
        if search_plan["mode"] != run_mode:
            raise ctx.failed(ctl.target_binding.REFUSED_MODE_MISMATCH, f"SEARCH: this target {'was bound as' if ctx.target.auto else 'declares'} {run_mode} and its cube "
                              f"measures {search_plan['mode']}; nothing was picked up", evidence=seen, declared_mode=ctx.target.mode, run_mode=run_mode, measured_mode=search_plan["mode"])
        ctx._write_bound_result()  # on disk before the first motion past SEARCH, which is the PREGRASP move below
        if search_plan["mode"] == ctl.target_binding.MODE_BASE_ADVANCE:  # the need follows the MEASURED reachability, and is decided before anything is picked up
            ctx._mission_capability_gate(seen)
    elif ctx.runtime_live and search_plan["mode"] == "BASE_ADVANCE":  # the module docstring, THE SYNTHETIC OBSERVATION IS OUT OF A LIVE RUN
        raise ctx.failed(ctl.LIVE_FORWARD_EXCLUDED, "SEARCH: this cube's placement needs the base, and the base advance of a LIVE run without a mission target "
                          "would be fed the synthetic observation, which is for rehearsal only; nothing was picked up", evidence=seen)
    return SearchOutcome(measured=measured, seen=seen, template=template, search_frame=search_frame, start_inside=start_inside, coarse=coarse)


# SEARCH FROM THE SCENE (scene_search). The low-pose frame alone decides when it shows exactly one verified block (a
# complete listing), unclipped, with a contact pixel, placed in a direct pad frame, and classed ARM_ONLY or BASE_ADVANCE:
# SEARCH then goes on exactly as it does from one frame. Otherwise SEARCH takes the scene scan's views (the ones --do scan
# takes, the 25 deg ones under scene_scan_25 as well), fuses them, and selects over the WHOLE scene (ruling 7): exactly one
# block not already on the pad, else a refusal before anything is picked up that names the blocks. The chosen block's
# best view is then the SEARCH frame: the coarse pregrasp is computed from that view's contact pixel and measured pan,
# and the pregrasp is entered from the saved low pose, where the scan leaves the arm. The SEARCH_SWEEP does not run.
@POLICY.decides
def _centre_decides(ctx, ctl, search: dict, measured: dict):
    record = (search.get("perception") or [None])[-1]
    entries, listing = _view_entries(ctx, record)
    if listing != "complete" or len(entries) != 1:
        return None, listing
    cube = entries[0].get("cube") or {}
    if not cube.get("detected") or not cube.get("contact_px") or any((cube.get("clipped") or {}).values()):
        return None, listing
    classified = classify_block(ctx, ctl, {**record, **entries[0]}, float(measured["shoulder_pan"]))
    return (classified if classified["class"] in (ARM_ONLY, BASE_ADVANCE) else None), listing


def _block_text(block: dict) -> str:
    where = "no position" if block["position_pad_m"] is None else f"({block['position_pad_m'][0]:+.3f}, {block['position_pad_m'][1]:+.3f}) m"
    return f"{block['id']} at {where}, {block['class']}"


@POLICY.decides
def _search_from_scene(ctx, ctl, search: dict, measured: dict) -> tuple:
    """(the pose of the SEARCH frame, the SEARCH frame, the chosen block's record or None, the pose the arm stands at or
    None). None, None for the last two: the low-pose frame decided, and SEARCH goes on from it as it always does."""
    words = POLICY.run_words(ctl)
    decided, listing = _centre_decides(ctx, ctl, search, measured)
    ctx.state("SEARCH_SCENE", early_exit=decided is not None, centre_listing=listing, centre_class=None if decided is None else decided["class"], evidence=search)
    if decided is not None:
        return measured, search, None, None
    try:
        taken, ended = _take_views(ctx, ctl, words, "SEARCH_SCENE")
    except ctl.ScanRefused as refusal:
        raise ctx.failed("search_scene_scan_refused", f"SEARCH: the scene scan was refused before it moved: {refusal}; nothing was picked up", evidence=search)
    if ctx.plan_only:
        print("PLAN ONLY: SEARCH from the scene planned its views; the plan goes on from the low-pose frame.", flush=True)
        return measured, search, None, None
    scene = _fused(ctx, ctl, taken, words, None if ended is None else ended[0])
    if ended is not None:
        cause, error = ended
        if cause == "refused":
            raise ctx.failed("search_scene_scan_step_refused", f"SEARCH: a view of the scene scan was refused after the arm had moved ({error}); nothing was picked up; "
                             "the arm holds where it stands", evidence=[view["record"] for view in taken if view["record"]], views_taken=len(taken))
        raise error
    unplaced = [block for block in scene["blocks"] if block["class"] != ALREADY_PLACED]
    records = [view["record"] for view in taken if view["record"]]
    ctx.events.predicate("scene_one_unplaced_block", len(unplaced) == 1, measured=len(unplaced), threshold=1, frames=records)
    if len(unplaced) > 1:
        raise ctx.failed("search_scene_not_one_unplaced_block", f"SEARCH: the scene scan saw {len(unplaced)} blocks that are not on the pad ({'; '.join(_block_text(block) for block in unplaced)}); "
                         "this robot picks only when exactly one unplaced block is in the whole scene; nothing was picked up", evidence=records, unplaced_blocks=len(unplaced),
                         **{f"block_{index + 1}_{axis}_m": (block["position_pad_m"] or [None, None])[k] for index, block in enumerate(unplaced[:2]) for k, axis in enumerate(("pad_x", "pad_y"))},
                         **{f"block_{index + 1}_class": block["class"] for index, block in enumerate(unplaced[:2])})
    if not scene["blocks"]:
        raise ctx.failed("search_scene_no_block", f"SEARCH: the scene scan saw no block in any of its {len(taken)} views; nothing was picked up; the arm is back at the saved low pose",
                         evidence=records, views_taken=len(taken))
    block = unplaced[0] if unplaced else scene["blocks"][0]  # no unplaced block: a placed one, and SEARCH ends DONE_ALREADY_PLACED as it always does
    view = next(item for item in taken if item["name"] == block["best_view"])
    entry = view["entries"][block["best_entry_index"]]
    chosen = {**view["record"], "cube": entry.get("cube"), "cube_by_template": entry.get("cube_by_template") if block["position_source"] == "direct" else None}
    ctx.state("SEARCH_SCENE_SELECTED", block=block["id"], block_class=block["class"], best_view=block["best_view"], pan_deg=round(view["pan_deg"], 2),
              position_pad_m=block["position_pad_m"], position_source=block["position_source"], evidence=view["record"])
    return dict(view["result"]["measured"]), view["result"], chosen, ctx.pose()
