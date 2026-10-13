# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""block_selection: which of several blocks off the pad a run takes. The SEARCH stage's word multi_block, OFF by default and
unqualified: a LIVE run names it only under the attended label. The lead's decision 4 of 2026-09-30: several off-pad blocks
bind an admissible block instead of a refusal; the lead's decision of 2026-10-04 (below, THE RULE) says which.

It never imports the controller. THE BINDING (bind_targets, select_for_execution) is pure: like target_binding it takes
the proven functions' ANSWERS as inputs, here the SEARCH decision of every listed block (placement_selector.Decision,
aligned with the listed cubes; None for a block with no SEARCH decision). It builds the same bindings and the same
selection as target_binding and raises the same refusals with the same values (through target_binding's own refusal
helpers where it can); the one thing it changes is WHICH admissible block a target takes, and among the blocks the proven
policy places it takes the installed controller's own (THE RULE), the pickup bands aside (THE PICKUP BANDS AT THE BINDING).
SEARCH FROM THE SCENE (search_from_scene, below) is a procedure on ctx, as policy_search's is.

THE RULE (the lead's decision of 2026-10-04, which settles verify r13 and r14 safety and policy MF1 and verify r14 gates MF1,
under the binding loss accounting of 2026-10-02: a loss of a start 31c546c places, under the recorded loaded models, is a
must-fix). Among the admissible blocks off the pad an auto target may take (target_binding, THE PREDICATES: a candidate
whose SEARCH decision chose a plan), the blocks of the first tier any of them is in (THE BINDING TIERS, below: a block the
proven policy places before any other), and among those:
  - in the first tier (proven_admits: the proven policy places the block), the block the installed controller's own rule
    takes (target_binding, THE SELECTION RULE: the largest signed placed margin, the block nearest the pad, then the
    listing order) among the blocks of that tier the binding reads (past the pickup bands), whatever base or arm travel
    the blocks' plans name: rule nearest_the_pad. Why: the verifiers found the least motion taking a block whose proven
    plan is the arm alone (0 mm of base) over a block nearer the pad that the proven policy places with the base, the
    block 31c546c binds and places, and the installed arm-only carry places no block under the recorded loaded models (48
    to 68 mm of FK travel: held where it stopped past +95 mm out under the recorded loaded sequences, held lowered under
    the recorded loaded lag inside the reach), whether the arm-alone plan survives the record's grasps or not. The bound
    block is then planned by the placement selector exactly as a single listed block is (the operator's ruling of
    2026-10-02, Q8, and the loaded reach cap);
  - in a later tier (the proven policy places none of the listed blocks), the operator's directive of 2026-09-30, "Driving
    may be involved, but should be avoided when unnecessary": the block whose chosen plan moves the base least, then the
    least arm travel, then the block nearest the pad, then the listing order: rule least_motion.
Every other decision is the proven binding's own, unchanged: no candidate, no bounded placement (the refusal is
target_binding's own, its explanation included), one cube to one target, and every later execution's selection by the
bound position (target_binding.select_target). TARGET_SELECTED and SEARCH_SCENE_LEAST_MOTION name the rule that chose
(binding_rule).

DECLARED TARGETS (the lead's decision of 2026-10-01: the words apply to auto targets only). A mission with a target that
declares its mode (it requires the capability, or declares that it does not), alone or beside an auto target, is bound
exactly as target_binding binds it: bind_targets and select_for_execution answer target_binding's own bindings, selection
and refusals for it, word for word. The controller never hands such a mission this word at all
(run_single_cube_cycle.additive_words_apply), so its SEARCH, its scene search and its binding are the proven ones.

SEARCH FROM THE SCENE (search_from_scene; the word scene_search with this one). The proven scene search
(policy_search._search_from_scene) refuses a scene with more than one block off the pad (search_scene_not_one_unplaced_block:
the default of item 7 of the open rulings of docs/autonomous-demo/overnight-plan-2026-09-26.md, section 11, which the lead's
decisions of 2026-09-30 say was never an operator ruling). Under this word the controller lends SEARCH this procedure in
its place for the length of SEARCH only (run_single_cube_cycle._placement_policies_at_search); policy_search does not
change a byte. It is the proven procedure, restated step for step through policy_search's own functions (the centre
frame's early exit, the views, the fused scene with every block classed by policy_search.classify_block, the refusals of a
scan refused, stopped or empty), with THE RULE at the one decision the proven procedure refuses: with more than one block
off the pad, the admissible ones (classed ARM_ONLY or BASE_ADVANCE, the classes SEARCH goes on with) and the ones SEARCH's
pickup bands refuse (classed OUTSIDE_PAN_BAND or TOO_NEAR: BAND_REFUSED) are decided again through the run's SEARCH
portfolio (placement_selector.Portfolio, the one the binding decides with) from the inputs their class was decided from;
THE BINDING TIERS read them all and the bands then read the first tier, as at the mission binding (past_the_tiers_and_the_
bands): among the admissible blocks of the first tier the one THE RULE takes is taken, and where the first tier holds only
blocks the bands refuse, the one THE RULE takes among those, which SEARCH then refuses with its band's own code before
anything is picked up (SEARCH_SCENE_LEAST_MOTION states the choice and the rule that chose with every candidate's plan, tier
and whether the bands refuse it, and the predicate scene_least_motion_block); none admissible is refused before anything moves
(search_multi_block_no_placeable_block, naming the blocks). With one block off the pad, or none, it is the proven procedure
state for state. Every raise site here mints a code of its own (search_multi_block_...), as the project's rule has a code
name its site; the three the proven procedure also has (a scan refused before it moved, a view refused after, an empty
scene) say what the proven ones say. A mission target then binds in the chosen block's best view, by THE RULE as well.

THE PICKUP BANDS AT THE BINDING (verify r8 replay MUST-FIX 3; the lead's decision 4: "bind the least-motion admissible
block instead of refusing"). SEARCH takes three refusals AFTER the binding and before anything moves: the return pan bound,
the pregrasp entry bound and the near block band (policy_search.refuse_pregrasp_beyond_pan_bounds, policy_align.
refuse_pickup_band). The proven binding does not ask them, and with one placeable block it does not need to. The additive
words make more blocks placeable, so a binding could take a newly placeable block the bands then refuse while the frame
holds a block the installed controller picks up and places (a block near a side edge, 31 deg of pan from the saved low
pose, beside one on the centreline). Under any additive word, for a mission whose every target is auto, the controller
asks the bands of every listed block that has a placement (Cycle._mission_pickup_band, from the coarse pregrasp SEARCH
computes for it) and the binding reads them through past_the_pickup_bands, AFTER THE BINDING TIERS (past_the_tiers_and_the_
bands; the lead's decision of 2026-10-03 on every binding path, verify r13 gates MUST-FIX 1): among the candidates of the
first tier, when one passes the bands, every one a band refuses is read without a placement, so that no binding rule takes
it (the nearest the pad, or THE RULE's least motion); when none passes, the cubes are read as they are and SEARCH refuses
the bound block with its band's own code before anything moves, as the installed controller refuses a block it binds that
the bands refuse. A block the bands refuse still holds its tier: beside it no block of a later tier is bound. TARGET_SELECTED
says how many the bands set aside (band_refused_candidates).

THE BINDING TIERS (the lead's decision of 2026-10-03, which settles verify r12 safety MF1 and replay MUST-FIX 1; verify r11
safety MF1, policy MF1 and replay MUST-FIX 1 before it; the binding loss accounting of 2026-10-02: a loss of a start
31c546c places, under the recorded loaded models, is a must-fix). Every binding that chooses among several listed blocks
under the additive words (both binding rules of the mission binding, and THE RULE of the scene search) reads each
candidate's SEARCH decision in tiers (TIERS, tier):
  proven_admits  the proven policy admits the block at SEARCH (its plan, planned beside the others from the coarse grasp
                 with no charge for the grasp's under-read, exactly as the installed controller's SEARCH plans it, is
                 admitted): a block the installed controller may bind (THE RULE then binds the one it binds);
                 whether it then places it is the held grasp's to say;
  capped_robust  the proven policy refuses it, its chosen plan stays under the loaded reach cap (placement_policy, THE
                 LOADED REACH CAP; the operator's ruling of 2026-10-02, Q8), and a plan under the cap still fits the 120 mm
                 budget with the grasp the record allows past SEARCH's charge, the coarse grasp plus 30.5 mm where SEARCH
                 charges 22 (placement_selector.Decision.capped_robust, placement_policy.capped_plan_robust);
  capped         the proven policy refuses it and its chosen plan stays under the cap at SEARCH's charge only: held past
                 the charge, the plan under the cap may no longer fit, and the run is then planned with the long stretch;
  long_stretch   only the long stretch places it (placement_selector.Decision.long_stretch): the proven policy refuses it
                 and no plan of the run's policies under the cap fits the budget.
When a candidate with a placement lies in a lower tier, every candidate with a placement in a higher one is read without one
(past_the_binding_tiers, before the pickup bands: a candidate SEARCH's bands refuse holds its tier as any other does), so
that neither binding rule takes it (the nearest the pad, or THE RULE), however much less base its plan names or however
much nearer the pad it lies; THE RULE reads the first tier only (the_rule), and the scene search reads the same tiers over
the same blocks. So a block the proven policy refuses is bound only where no listed block is one the proven
policy places, the ones the bands refuse included (the binding then takes such a block and SEARCH refuses it before
anything moves, as the installed controller does); among such blocks one whose plan under the cap is robust to the record's
grasps comes before one whose plan fits only inside SEARCH's charge, and a long stretch is bound only where no listed block
has the proven plan or one under the cap. Why: a plan under the cap is decided at SEARCH from the coarse grasp plus SEARCH's
charge for the grasp's under-read (reach_model, THE PLANNING GRASP OUT), and once held the run plans again from the MEASURED
grasp, reading the cap again for that situation. The verifiers found multi_block's least motion binding a block the proven
policy refuses whose plan under the cap named 1 to 4 mm less base at SEARCH than the proven plan of a block the installed
controller places; held past SEARCH's charge (the committed World grasps 26.2 mm past the coarse estimate, the record shows
30.5 mm) no plan under the cap fit the budget any more, the run carried the long stretch and ended with the cube held on the
stretched arm (reach_way_back_held), where 31c546c places the other block. The long stretch read at SEARCH loses the same
way (verify r11): its plan can name less base than a plan under the cap, or none (the arm alone by a side edge), and its
block can lie nearer the pad. One block with a placement is bound with its own plan, whatever its tier (Q8 exactly), and
when every candidate with a placement shares one tier the cubes are read as they are. TARGET_SELECTED names the bound
block's tier (binding_tier) and how many candidates the tiers set aside (tier_set_aside_candidates);
SEARCH_SCENE_LEAST_MOTION states every candidate's tier.

A NAMED RESIDUAL. ALIGN still counts blocks and is the proven one, untouched: it refuses a pregrasp view that verifies more
than one cube (align_more_than_one_cube_verified), so blocks close enough to share the pregrasp view are still refused there
(which cube the jaws would close on is not a measurement there; the question is the operator's).
"""
import placement_selector
import policy_align
import policy_search
import stage_policy
import target_binding
from placement_policy import PROVEN

WORD = "multi_block"
WORDS = (WORD,)
QUALIFIED = frozenset()  # joins only in the commit that adds its qualification record
POLICY = stage_policy.StagePolicy("search", __file__, ("scene_least_motion_block",), WORDS, QUALIFIED)
ADMISSIBLE = (policy_search.ARM_ONLY, policy_search.BASE_ADVANCE)  # the classes SEARCH goes on with
BAND_REFUSED = (policy_search.OUTSIDE_PAN_BAND, policy_search.TOO_NEAR)  # the classes SEARCH's pickup bands refuse
TIERS = ("proven_admits", "capped_robust", "capped", "long_stretch")  # THE BINDING TIERS, the first one first


def tier(decision) -> int:
    """THE BINDING TIERS (module docstring): the tier of a SEARCH decision that chose a plan, as its index in TIERS. The
    proven policy admits the block when its plan, which placement_selector.decide plans beside the others, is admitted."""
    if any(plan.policy == PROVEN and plan.admitted for plan in decision.plans):
        return TIERS.index("proven_admits")
    if decision.long_stretch:
        return TIERS.index("long_stretch")
    return TIERS.index("capped_robust") if decision.capped_robust else TIERS.index("capped")


def tier_name(decision) -> str:
    """The name of tier(decision), as the records state it."""
    return TIERS[tier(decision)]


BINDING_RULES = ("nearest_the_pad", "least_motion")  # THE RULE in the first tier, and in every later one


def binding_rule(decision) -> str:
    """THE RULE (module docstring) for a block of `decision`'s tier: the installed controller's nearest the pad in the first
    tier, the least motion in every later one."""
    return BINDING_RULES[0] if tier(decision) == TIERS.index("proven_admits") else BINDING_RULES[1]


def motion_key(index: int, cube, decision) -> tuple:
    """The least motion's order for one admissible block (the smallest first): its tier (THE BINDING TIERS), then the base
    advance, the arm travel, the margin and the listing order."""
    plan = decision.selected
    return (tier(decision), float(plan.base_advance_mm), float(plan.fk_travel_mm or 0.0), -float(cube.margin_mm), index)


def the_rule(entries, decisions) -> tuple:
    """THE RULE (module docstring) among `entries`, (index, cube) pairs whose SEARCH decision (decisions[index]) chose a plan:
    (the entry it takes, the rule that took it). The entries of the first tier only are read: in the first tier the entry
    target_binding's THE SELECTION RULE takes (the largest signed placed margin, then the listing order), in a later one the
    least motion_key. Pure."""
    first = min(tier(decisions[index]) for index, _cube in entries)
    tiered = [(index, cube) for index, cube in entries if tier(decisions[index]) == first]
    rule = binding_rule(decisions[tiered[0][0]])
    if rule == BINDING_RULES[0]:
        return max(tiered, key=lambda entry: (entry[1].margin_mm, -entry[0])), rule
    return min(tiered, key=lambda entry: motion_key(entry[0], entry[1], decisions[entry[0]])), rule


def _chosen(decisions, index: int) -> bool:
    return index < len(decisions) and decisions[index] is not None and decisions[index].selected is not None


def past_the_pickup_bands(cubes, bands, rules) -> tuple:
    """THE PICKUP BANDS AT THE BINDING (module docstring): (the cubes the binding reads, how many candidates it set aside).
    `bands` is aligned with `cubes`: the code SEARCH's pickup bands would refuse that cube with once bound, or None. When a
    candidate with a placement mode passes the bands, every candidate with a mode that a band refuses is read without one;
    otherwise the cubes are as they were. Pure."""
    cubes, bands = list(cubes), list(bands)
    if len(cubes) != len(bands):
        raise ValueError("one pickup band answer per listed cube")
    placeable = [index for index, cube in enumerate(cubes) if target_binding.is_candidate(cube, rules) and cube.mode is not None]
    refused = [index for index in placeable if bands[index] is not None]
    if not refused or len(refused) == len(placeable):
        return tuple(cubes), 0
    return tuple(target_binding.SeenCube(cube.center_pad_m, cube.margin_mm, None) if index in refused else cube for index, cube in enumerate(cubes)), len(refused)


def past_the_binding_tiers(cubes, decisions, rules) -> tuple:
    """THE BINDING TIERS (module docstring): (the cubes the binding reads, how many candidates it set aside). `decisions` is
    aligned with `cubes`: the SEARCH decision of each listed cube, or None. Every candidate with a placement mode whose
    decision chose a plan of a later tier than another such candidate's is read without one; when they all share one tier
    (one candidate included), the cubes are as they were. Pure."""
    cubes, decisions = list(cubes), list(decisions)
    if len(cubes) != len(decisions):
        raise ValueError("one SEARCH decision, or None, per listed cube")
    tiers = {index: tier(decisions[index]) for index, cube in enumerate(cubes) if target_binding.is_candidate(cube, rules) and cube.mode is not None and _chosen(decisions, index)}
    first = min(tiers.values(), default=None)
    later = [index for index, level in tiers.items() if level > first]
    if not later:
        return tuple(cubes), 0
    return tuple(target_binding.SeenCube(cube.center_pad_m, cube.margin_mm, None) if index in later else cube for index, cube in enumerate(cubes)), len(later)


def past_the_tiers_and_the_bands(cubes, decisions, bands, rules) -> tuple:
    """THE BINDING TIERS, then THE PICKUP BANDS AT THE BINDING (module docstring; the lead's decision of 2026-10-03 on every
    binding path, verify r13 gates MUST-FIX 1): (the cubes the binding reads, how many candidates the tiers set aside, how
    many the bands set aside). The tiers read every candidate with a placement, the ones SEARCH's pickup bands refuse
    included, so such a block holds its tier; the bands then read the candidates of the first tier. Pure."""
    cubes, tiered = past_the_binding_tiers(cubes, decisions, rules)
    cubes, set_aside = past_the_pickup_bands(cubes, bands, rules)
    return cubes, tiered, set_aside


def bind_targets(targets, cubes, decisions, rules) -> tuple:
    """target_binding.bind_targets with THE RULE for a mission whose every target is auto: the same bindings, refusals and
    values, THE RULE's choice among several. A mission with a declared target is target_binding's own (DECLARED TARGETS)."""
    if not all(target.auto for target in targets):
        return target_binding.bind_targets(targets, cubes, rules)
    modes = [target.mode for target in targets]
    if len(set(modes)) != len(modes):
        raise target_binding.TargetRefused(target_binding.REFUSED_TARGETS_SHARE_A_MODE, "BINDING: two declared targets share a placement mode, so no measurement tells "
                                           "their cubes apart; nothing was picked up", declared_targets=len(targets))
    candidates = [(index, cube) for index, cube in enumerate(cubes) if target_binding.is_candidate(cube, rules)]
    placed_cubes = sum(1 for cube in cubes if target_binding.is_placed(cube, rules))
    bindings = []
    for target in targets:
        feasible = [(index, cube) for index, cube in candidates if cube.mode is not None and _chosen(decisions, index)]
        if not feasible:  # no candidate, or none with a bounded placement: the proven rule's own refusal, word for word
            target_binding._nearest_feasible(target, candidates, len(bindings), len(cubes), placed_cubes)
            raise AssertionError("the proven selection rule found a feasible block that the decisions did not")
        (index, cube), _rule = the_rule(feasible, decisions)
        bindings.append(target_binding.TargetBinding(target.target_id, cube.center_pad_m, cube.mode, index))
        candidates = [entry for entry in candidates if entry[0] != index]
    return tuple(bindings)


def select_for_execution(target, cubes, decisions, rules):
    """target_binding.select_for_execution with THE RULE at the first execution's binding; a later execution selects by the
    bound position exactly as before. A mission with a declared target is target_binding's own (DECLARED TARGETS)."""
    if not all(declared.auto for declared in target.targets):
        return target_binding.select_for_execution(target, cubes, rules)
    bindings = None
    bound, wanted = target.bound_pad_m, target.mode
    if target.first_execution:
        bindings = bind_targets(target.targets, cubes, list(decisions), rules)
        bound, wanted = bindings[target.index].bound_pad_m, bindings[target.index].mode
    return bindings, target_binding.select_target(bound, wanted, cubes, rules)


def _scene_decision(ctx, ctl, portfolio, taken: list, block: dict):
    """(the PAD_FRAME centre, the SEARCH decision) of one block of the scene, from the inputs its class was decided from
    (policy_search.classify_block at its best sighting): that sighting's record, its view's measured pan, the record's pad
    frame, the coarse grasp clamped at 0 and the plan-time side allowance, exactly as SEARCH asks placement_plan. None when
    that record does not place the block in a direct pad frame."""
    view = next(item for item in taken if item["name"] == block["best_view"])
    entry = view["entries"][block["best_entry_index"]]
    record = {**(view["record"] or {}), "cube": entry.get("cube"), "cube_by_template": entry.get("cube_by_template")}
    centre = (record.get("cube_by_template") or {}).get("cube_center_pad_m")
    contact = (record.get("cube") or {}).get("contact_px")
    frame = ctx._pad_frame({"perception": [record]})
    if not centre or not contact or frame is None:
        return None
    coarse = policy_align.coarse_pregrasp(ctx, ctl, record, {"shoulder_pan": float(view["pan_deg"])})
    direction = ctl.reach_direction_pad(frame["homography_image_to_pad"], contact)
    return tuple(centre), portfolio.decide(centre, direction, max(float(coarse["cube_out_mm"]), 0.0), ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM)


def _least_motion_block(ctx, ctl, taken: list, unplaced: list) -> tuple:
    """(the block THE RULE takes, or None; every candidate with a placement as a state records it; the chosen block's tier and
    the rule that took it, or None).
    SEARCH FROM THE SCENE (module docstring): THE BINDING TIERS read the admissible blocks and the ones SEARCH's pickup bands
    refuse (BAND_REFUSED) alike, then the bands read the first tier: its admissible blocks, or where it holds none, its
    blocks the bands refuse (SEARCH then refuses the one taken with its band's own code). None without an admissible block."""
    portfolio = getattr(ctx, "_search_portfolio", None) or placement_selector.Portfolio(ctl, ctl.placement_plan, placement_selector.enabled(ctl.PLACEMENT_ENVELOPE))
    passing, refused, decisions, candidates = [], [], {}, []
    for index, block in enumerate(unplaced):
        if block["class"] not in ADMISSIBLE + BAND_REFUSED:
            continue
        decided = _scene_decision(ctx, ctl, portfolio, taken, block)
        if decided is None or decided[1].selected is None:
            continue
        centre, decision = decided
        (passing if block["class"] in ADMISSIBLE else refused).append((index, target_binding.SeenCube(centre, ctl.placed_margin_mm(centre), decision.selected.mode)))
        decisions[index] = decision
        candidates.append({"block": block["id"], "position_pad_m": block["position_pad_m"], "class": block["class"], **decision.selected.record(),
                           "long_stretch": bool(decision.long_stretch), "binding_tier": tier_name(decision), "band_refused": block["class"] in BAND_REFUSED})
    if not passing:
        return None, candidates, None
    first = min(tier(decisions[index]) for index, _cube in passing + refused)
    entries = [entry for entry in passing if tier(decisions[entry[0]]) == first] or [entry for entry in refused if tier(decisions[entry[0]]) == first]
    (index, _cube), rule = the_rule(entries, decisions)
    return unplaced[index], candidates, {"binding_tier": tier_name(decisions[index]), "binding_rule": rule}


@POLICY.decides
def search_from_scene(ctx, ctl, search: dict, measured: dict) -> tuple:
    """policy_search._search_from_scene under this word (module docstring, SEARCH FROM THE SCENE): the same answer, (the pose
    of the SEARCH frame, the SEARCH frame, the chosen block's record or None, the pose the arm stands at or None)."""
    words = policy_search.POLICY.run_words(ctl)
    decided, listing = policy_search._centre_decides(ctx, ctl, search, measured)
    ctx.state("SEARCH_SCENE", early_exit=decided is not None, centre_listing=listing, centre_class=None if decided is None else decided["class"], evidence=search)
    if decided is not None:
        return measured, search, None, None
    try:
        taken, ended = policy_search._take_views(ctx, ctl, words, "SEARCH_SCENE")
    except ctl.ScanRefused as refusal:
        raise ctx.failed("search_multi_block_scan_refused", f"SEARCH: the scene scan was refused before it moved: {refusal}; nothing was picked up", evidence=search)
    if ctx.plan_only:
        print("PLAN ONLY: SEARCH from the scene planned its views; the plan goes on from the low-pose frame.", flush=True)
        return measured, search, None, None
    scene = policy_search._fused(ctx, ctl, taken, words, None if ended is None else ended[0])
    if ended is not None:
        cause, error = ended
        if cause == "refused":
            raise ctx.failed("search_multi_block_scan_step_refused", f"SEARCH: a view of the scene scan was refused after the arm had moved ({error}); nothing was picked up; "
                             "the arm holds where it stands", evidence=[view["record"] for view in taken if view["record"]], views_taken=len(taken))
        raise error
    unplaced = [block for block in scene["blocks"] if block["class"] != policy_search.ALREADY_PLACED]
    records = [view["record"] for view in taken if view["record"]]
    if len(unplaced) > 1:  # THE RULE (module docstring): an admissible block, where the proven procedure refuses
        block, candidates, choice = _least_motion_block(ctx, ctl, taken, unplaced)
        admissible = sum(1 for candidate in candidates if not candidate["band_refused"])
        ctx.events.predicate("scene_least_motion_block", block is not None, measured=admissible, threshold=1, frames=records)
        if block is None:
            raise ctx.failed("search_multi_block_no_placeable_block", f"SEARCH: the scene scan saw {len(unplaced)} blocks that are not on the pad "
                             f"({'; '.join(policy_search._block_text(block) for block in unplaced)}), and none of them is one this run can pick up and place; nothing was picked up",
                             evidence=records, unplaced_blocks=len(unplaced), admissible_blocks=0,
                             **{f"block_{index + 1}_{axis}_m": (block["position_pad_m"] or [None, None])[k] for index, block in enumerate(unplaced[:2]) for k, axis in enumerate(("pad_x", "pad_y"))},
                             **{f"block_{index + 1}_class": block["class"] for index, block in enumerate(unplaced[:2])})
        view = next(item for item in taken if item["name"] == block["best_view"])
        entry = view["entries"][block["best_entry_index"]]
        chosen = {**view["record"], "cube": entry.get("cube"), "cube_by_template": entry.get("cube_by_template") if block["position_source"] == "direct" else None}
        plan = next(candidate for candidate in candidates if candidate["block"] == block["id"])
        ctx.state("SEARCH_SCENE_LEAST_MOTION", block=block["id"], block_class=block["class"], best_view=block["best_view"], pan_deg=round(view["pan_deg"], 2),
                  position_pad_m=block["position_pad_m"], position_source=block["position_source"], unplaced_blocks=len(unplaced), admissible_blocks=admissible,
                  policy=plan["policy"], base_advance_mm=plan["base_advance_mm"], candidates=candidates, **choice, evidence=view["record"])
        return dict(view["result"]["measured"]), view["result"], chosen, ctx.pose()
    ctx.events.predicate("scene_one_unplaced_block", len(unplaced) == 1, measured=len(unplaced), threshold=1, frames=records)
    if not scene["blocks"]:
        raise ctx.failed("search_multi_block_no_block", f"SEARCH: the scene scan saw no block in any of its {len(taken)} views; nothing was picked up; the arm is back at the saved low pose",
                         evidence=records, views_taken=len(taken))
    block = unplaced[0] if unplaced else scene["blocks"][0]  # no unplaced block: a placed one, and SEARCH ends DONE_ALREADY_PLACED as it always does
    view = next(item for item in taken if item["name"] == block["best_view"])
    entry = view["entries"][block["best_entry_index"]]
    chosen = {**view["record"], "cube": entry.get("cube"), "cube_by_template": entry.get("cube_by_template") if block["position_source"] == "direct" else None}
    ctx.state("SEARCH_SCENE_SELECTED", block=block["id"], block_class=block["class"], best_view=block["best_view"], pan_deg=round(view["pan_deg"], 2),
              position_pad_m=block["position_pad_m"], position_source=block["position_source"], evidence=view["record"])
    return dict(view["result"]["measured"]), view["result"], chosen, ctx.pose()
