# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The ALIGN word align_left_clearance: the proven PREGRASP and ALIGN (policy_align.run_pregrasp_align, unchanged), then a
clearance correction at the descended view. ALIGN's proven window accepts the block anywhere 0 to 9 mm right of the fixed
(left) jaw (the controller's CAPTURE_WINDOW_MM); under this word the block must also lie inside LEFT_CLEARANCE_WINDOW_MM
before the grasp, and pan trims aimed at that window's middle bring it there, at most LEFT_CLEARANCE_TRIMS_MAX of them.

The operator's ruling of 2026-10-07: on every grasp miss of that day the fixed jaw pushed the block. Run
console-20261007T185345Z-m989a19c read 2.7 mm after its trim and 0.0 mm after its descent, both inside the proven window,
and the close pushed the block forward. The operator chose 6 to 9 mm, aimed at 7.5.

An additive policy (the controller's _ADDITIVE_POLICIES), like the placement words: policy_align stays byte for byte the
installed module (autonomy/tests/test_reach_policies_default_path.py), and this module raises nothing of its own: a block
lost from the view ends align_cube_not_detected, and one still outside the window after the trims ends
align_not_in_window_after_trims, both through policy_align's own raise sites, with the jaws above the block, never closed on
it. The trims run at the descended view, which the proven loop also trims at (the pan moves only with the jaw tips above
the cube top plane). The controller's run_cycle calls this module instead of policy_align only when the run names this word
for a run it applies to (additive_words_apply) and does not name align_v2.
"""
import policy_align
import stage_policy

WORD = "align_left_clearance"
WORDS = (WORD,)
QUALIFIED = frozenset()
POLICY = stage_policy.StagePolicy("align", __file__, ("cube_detected_in_the_pregrasp_view", "trim_pan_within_return_bound", "aligned"), WORDS, QUALIFIED)
LEFT_CLEARANCE_WINDOW_MM = (6.0, 9.0)  # the operator's numbers (2026-10-07); the moving jaw stands about 9 mm clear at 0
LEFT_CLEARANCE_TRIMS_MAX = 3  # as the proven loop's three, which the shared refusal's sentence names
FIRST_CORRECTION_ATTEMPT = 8  # the ALIGN states of the correction are numbered after the proven loop's at most eight


def engaged(words) -> bool:
    """Whether a run's words make this module run PREGRASP and ALIGN: the word named, and align_v2 not named (align_v2 keeps
    its own servo and window)."""
    words = tuple(words or ())
    return WORD in words and policy_align.ALIGN_V2 not in words


def alignment_decision(ctl, bbox_xyxy, pitch_deg: float) -> dict:
    """The controller's alignment_from_bbox on one box at one tool pitch, `aligned` decided on LEFT_CLEARANCE_WINDOW_MM."""
    decision = ctl.alignment_from_bbox(bbox_xyxy, pitch_deg)
    return {**decision, "aligned": LEFT_CLEARANCE_WINDOW_MM[0] <= decision["lateral_mm"] <= LEFT_CLEARANCE_WINDOW_MM[1]}


def lateral_trim(ctl, lateral_mm: float, cube_range_m: float) -> float:
    """The controller's lateral_trim_deg, which aims at the middle of CAPTURE_WINDOW_MM, given the lateral relative to the
    middle of LEFT_CLEARANCE_WINDOW_MM instead, so that it aims there."""
    shift = sum(LEFT_CLEARANCE_WINDOW_MM) / 2.0 - sum(ctl.CAPTURE_WINDOW_MM) / 2.0
    return ctl.lateral_trim_deg(lateral_mm - shift, cube_range_m)


@POLICY.decides
def run_pregrasp_align(ctx, ctl, measured: dict, coarse: dict, seen: dict, start_inside: float) -> policy_align.AlignOutcome:
    """policy_align.run_pregrasp_align, then the clearance correction (the module docstring). The same arguments and outcome:
    the view the grasp starts from and its last decision, whose radial places the grasp."""
    outcome = policy_align.run_pregrasp_align(ctx, ctl, measured, coarse, seen, start_inside)
    view = outcome.view
    cube = ctx._cube(view)
    decision = alignment_decision(ctl, cube["cube"]["bbox_xyxy"], view["tool"]["pitch_deg"]) if cube is not None else dict(outcome.decision, aligned=False)
    trims = 0
    while not decision["aligned"]:
        if trims == LEFT_CLEARANCE_TRIMS_MAX:
            policy_align._raise_not_in_window(ctx, view, decision, LEFT_CLEARANCE_WINDOW_MM, trims)
        cube_range = ctl.GRASP_TOOL_RANGE_M + (view["tool"]["out_mm"] + decision["radial_mm"]) / 1000.0
        trim = lateral_trim(ctl, decision["lateral_mm"], cube_range)
        ctx._refuse_trim_beyond_return_bound(view, trim)  # before the trim moves: the pan the arm holds can still plan the EXIT
        view = ctx.run_pose(ctl.pan_step_pose(view["measured"], trim, False), False, ctl.CONTACT_FLOOR_M, pan_allowance=ctl.STEP_MAX_PAN_DEG, label="ALIGN pan trim")
        trims += 1
        ctx.events.automatic_retry("pan_trim")
        attempt = FIRST_CORRECTION_ATTEMPT + trims - 1
        cube = ctx._cube(view)
        if cube is None:
            ctx.events.predicate("cube_detected_in_the_pregrasp_view", False, frames=view, scalars=("cube_detected",))
            policy_align._raise_cube_not_detected(ctx, view, attempt)
        if ctx.target is not None:
            ctx._mission_one_cube_in_view(cube, view, attempt)
        decision = alignment_decision(ctl, cube["cube"]["bbox_xyxy"], view["tool"]["pitch_deg"])
        ctx.state("ALIGN", attempt=attempt, lateral_mm=round(decision["lateral_mm"], 1), radial_mm=round(decision["radial_mm"], 1), aligned=decision["aligned"], tool=ctl.fmt_tool(view["tool"]), evidence=view)
        ctx.events.predicate("aligned", decision["aligned"], measured=decision["lateral_mm"], threshold=LEFT_CLEARANCE_WINDOW_MM[1], unit="mm", frames=view, scalars=("lateral_mm", "radial_mm", "capture_window_mm"),
                             measurements={"capture_window_min_mm": LEFT_CLEARANCE_WINDOW_MM[0], "capture_window_max_mm": LEFT_CLEARANCE_WINDOW_MM[1]})
    return policy_align.AlignOutcome(view=view, decision=decision)
