# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""placement_executor: the placement once the cube is held, in a run that names an additive placement word (arm_reach,
advance_then_reach). With none of them the controller never calls it: the proven stage (policy_placement.run_placement)
places the block, byte for byte as before. Nor for a mission with a target that declares its mode (it requires the
capability, or declares that it does not): the words apply to auto targets only (the lead's decision of 2026-10-01,
run_single_cube_cycle.additive_words_apply), so such a mission's held cube goes to the proven stage as with no word, and
every mission target this module sees is an auto one.

It has no word and moves nothing itself: it decides which policy places the held block, hands it to that policy's executor
(policy_arm_reach.execute, policy_advance_then_reach.execute, each under its own rule) or to the proven stage itself, and
goes on in order when an executor hands the block back. It never imports the controller (ctx is the Cycle, ctl the
controller's namespace, stage_policy's module docstring).

THE CHOICE ONCE HELD (place). Every policy the run names and the proven one are planned again from the MEASURED grasp out
(placement_selector.decide, a Situation measured True, the plan-time side allowance), under the loaded reach cap
(placement_policy THE LOADED REACH CAP: a block the proven policy places, the held state's proven_admits, True when the
proven policy placed the block at SEARCH and places it again from the measured grasp, and any other block whenever a plan of
the run under the cap fits what is left of the budget; every re-plan of the run reads it), and the choice
is the least base motion among the admitted plans this run may execute (placement_selector.select: a tie within the advance loop's tolerance
goes to the proven policy). What this run may execute is the proven stage's own rule for the mode of a held cube:
  - a mission target (an auto one) runs the mode its binding measured; the one flip allowed is the proven stage's own, a
    target bound ARM_ONLY whose plan now needs the base in a run that CARRIES the capability (a run that carries the
    capability is placed only through its approach, and a run that carries none never drives the base);
  - a LIVE run without a mission target never drives the base (its base observation would be the synthetic one);
  - the proven plan is always eligible: the proven stage applies its own mission rules when it runs.
A run bound BASE_ADVANCE may not execute an ARM_ONLY plan, and its mission result claims the capability's applied output
(target_binding.result_claims_placement): its policies are planned with the least advance the advance loop pulses for
(the Situation's min_advance_mm, _first_advance_mm), so advance_then_reach offers its smallest pulsed advance where a
measured grasp better than SEARCH's planning grasp would otherwise make it offer the arm alone, which such a run may not take
(it would leave only the proven stage's whole advance, or no plan and the cube held).
PLACEMENT_POLICY states the choice: the chosen policy and its plan, every policy's plan, what this run may execute. The
proven policy chosen, the proven stage places the block exactly as it does with no word. No plan admitted is
reach_placement_infeasible_while_held with the best near-miss the selector names; plans admitted but none this run may
execute is reach_mode_changed_while_held (the proven stage's mission_mode_changed_while_held, raised from here with a code of
its own) or, for a LIVE run without a mission target, LIVE_FORWARD_EXCLUDED, before anything moves.

THE IN-RUN FALLBACK (the lead's decision 3 of 2026-09-30). An executor that does not place the block has brought the
loaded arm back to the carry pose, holding it, and hands back where the run stands. It runs only where the way back can
step: a way back that would leave what the records show the loaded arm doing holds the arm where it stands instead
(reach_carry, THE WAY BACK UNDER LOAD: reach_way_back_held), the gripper closed, and the run ends there for the operator,
its record saying why the in-run fallback did not run (the lead's ruling of 2026-10-02 on operator question Q7, A: keep
the hold until a loaded session is measured; no loaded move outside the recorded envelope). On the recorded loaded
behaviour a loaded arm lags its commands, and every model of the way back holds a block lowered for a release: a release
refused after a lowering is expected to end held, and the fallback to run from a refusal that leaves the arm at the carry
height where it was commanded (a carry short of the admission, a lowering the executor's planner refused). The executor
hands back the out its loaded arm reached (the run's measured out cap), the anchor moved by every measured advance, and
the advance so far. Every step of the fallback moves
the base, so it happens only in a run that may drive the base (may_drive_the_base): a mission run that carries the
capability (THE CAPABILITY OF AN ARM-ALONE BINDING, below), or a run without a mission target that is not LIVE. The same
run then goes on with the policies it asks from that state (asked): the advance member advance_then_reach again, once, when
it is the one that just handed the block back (its own re-plan with a pulsed advance from where the run stands: verify r7
policy MF2, it was skipped for the proven policy's larger advance, or for a refusal, while it placed the block), then the
policies after the one that failed in the order arm_reach, advance_then_reach, proven; of them the one whose plan from that
state MOVES THE BASE (the arm alone from here was just tried, so every plan asks a least advance, the first one the
advance loop pulses for) inside what is left of the 120 mm budget and in a mode this run may execute, by the same least
motion choice (PLACEMENT_FALLBACK states it, with every candidate asked). The chain is bounded: arm_reach once,
advance_then_reach at most twice, then the proven policy. The proven policy's candidate is its plan from where the run stands; when that plan is the
arm alone (the anchor a measured advance ended on can lie inside the proven arm-only band), it is the proven rule's BASE
BRANCH instead (proven_base_branch: base_advance_needed_mm from there and a release at about the grasp radius, what the
proven stage itself does after its own arm-only carry fell short), since the arm alone from this stance was just refused.
The proven policy goes on as the proven stage itself (policy_placement.
run_placement, unchanged; handed the base branch as its placement_plan when that branch is the plan, through a read-only
overlay of the controller's view): from the SEARCH anchor when the base has not moved, else from the anchor the measured advance
ended on, with its placement rule (base_advance_needed_mm) held to the budget left for the length of that stage
(ctl._advance_rule), and the base's travel counted from the start of the run: the frozen advance loop checks its own
total after every pulse, and in a continuation that total is the continuation's alone, so the held rule makes the same
check for the whole run (reach_base_advanced_past_bound: nothing further is commanded, the cube held at the low scan pose,
as the proven loop's base_advanced_past_bound ends it). advance_then_reach asked again makes the same check in its own rule,
the base's path charged (policy_advance_then_reach.remaining_need_rule, THE RUN'S BUDGET: reach_further_advance_past_bound,
verify r9 safety MF1). When nothing is left: a run that may not drive
the base is reach_arm_only_insufficient_while_held (or LIVE_FORWARD_EXCLUDED), the proven stage's refusal for it
(mission_arm_only_insufficient_while_held) under a code of its own, which says what refused the arm alone (refused_by, one
of reach_carry.REFUSALS: a carry short of the admission, or an admission that refused a carry that arrived) and never that
the reach fell short when it did not, and anything else is reach_policies_exhausted_while_held, whose sentence names the
policies asked from there and the budget left; the cube stays held at the carry pose (the way back's own refusals,
reach_way_back_held and the two retract codes, and reach_release_hold_lost leave the loaded arm where it stands). The
gripper opens only after an admitted release, inside an executor. Every raise site of this module and of the policies'
executors mints a code of its own (reach_...), so a record names the site that decided, but for a LIVE run without a
mission target, which is refused with the controller's own LIVE_FORWARD_EXCLUDED as the proven stage refuses it.

THE CAPABILITY OF AN ARM-ALONE BINDING (the lead's decision 3 on the runtime's own path; operator question Q1, option A).
The runtime gives a mission run the capability only before it spawns it (operator decision D1: never with a cube held),
and spawns an auto target's first execution without it, respawning once, carrying it, after a SEARCH refusal that asks
for it. A block the words bind to the arm alone (an additive policy's ARM_ONLY plan) would then run without it, and a
refused release would end held although the fallback that moves the base is admitted. So, at SEARCH, before anything is
picked up, the controller asks for the capability for an auto target it binds to the arm alone whenever the fallback
that binding would go on with moves the base (fallback_at_search: planned from SEARCH's situation as the fallback plans
it): mission_fallback_capability_required, the selection the arm alone. In the runtime's respawn, which carries the
capability, the block binds to the arm alone again, and the arm alone places it with no base motion (an arm-alone
selection's result needs no relayed command, target_binding.result_claims_placement), or, should its release be refused,
the fallback moves the base only if the way back brings the loaded arm to the carry pose (reach_carry, THE WAY BACK UNDER
LOAD: a loaded arm it cannot bring back is held where it stands, the cube held for the operator, the lead's ruling of
2026-10-02 on Q7), planned again from where the run then stands (SEARCH's advance is a plan, never a promise). The ruling
keeps decision 3's chain where the way back can step, so this capability ask stays. A target that declares its mode never
meets these words.
Nothing here moves.

THE PROVEN TIE (verify r4 policy MF1). A tie at no base motion goes to the proven policy, so SEARCH can bind the proven
arm-alone plan of a block an additive policy also places with the arm alone. Once held, the executor plans again from the
measured grasp; where the proven plan then needs the base, the least motion could be an additive policy's arm alone, whose
refused release goes on only by moving the base. Under the loaded reach cap (placement_policy THE LOADED REACH CAP) it never
is: the proven policy placed that block at SEARCH, so no additive arm-alone plan of it reaches past the proven arm-alone
plan's own reach. Two rules keep lead decision 3 on that path for a block without the cap:
  - AT SEARCH, an auto target bound to the proven arm-alone plan in a run that carries no capability is asked for it, as an
    additive arm-alone binding is, when the executor may hand the held cube to an additive policy (handed_at_search: the
    proven plan needs the base at SEARCH's planning grasp, the coarse grasp plus the worst recorded under-read, and the block
    carries no loaded reach cap) and that policy's fallback moves the base (fallback_at_search); a block under the cap is
    never asked for it there, as the installed controller never asks;
  - ONCE HELD, an auto target's run that carries no capability and that SEARCH bound to the proven policy is never handed to
    an additive policy (additive_barred): its fallback could run only with the capability, which the runtime gives before a
    spawn when SEARCH asks for it, so the proven stage answers, exactly as with no word (the proven arm-alone plan is
    planned at SEARCH from the coarse grasp with no charge for the grasp's under-read, as the installed controller plans
    it, so a grasp held past that plan's own arm-alone band, a few mm for a block near its edge, ends
    mission_mode_changed_while_held, the cube held at the grasp, never carried; among several listed blocks the proven
    policy places, the binding takes the block the installed controller takes: block_selection THE RULE). A LIVE run
    without a mission target is never given it: there a refused additive release ends held by design (below), and the
    executor may still hand it the cube.

THE RECORD. summary gains placement_policy (the policy that placed the block, or the proven stage) and placement_fallbacks
(each step of the chain), in this path only: a run with no additive word records what it did before them.
"""
import dataclasses
import math

import placement_policy
import placement_selector
import policy_placement
import reach_carry
import reach_model
from placement_policy import MODE_ARM_ONLY, MODE_BASE_ADVANCE, PROVEN
from stage_policy import CycleFailed

MODULES = {module.WORD: module for module in placement_selector.POLICIES}
# THE IN-RUN FALLBACK's advance member: the one additive policy that moves the base. Handed back after its own attempt, it
# is asked again, once, from where the run stands (its re-plan with a pulsed advance), before the proven policy.
REPLANNED = "advance_then_reach"


def engaged(words) -> bool:
    """Whether a run's words name an additive placement policy: only then does the controller hand it the held cube."""
    return bool(placement_selector.enabled(words))


def may_drive_the_base(ctx) -> bool:
    """Whether this run may move the base (the module docstring, THE CHOICE ONCE HELD)."""
    if ctx.target is not None:
        return bool(ctx.target.carries_capability)
    return not ctx.runtime_live


def may_execute(ctx, plan, run_mode) -> bool:
    """Whether this run may execute an additive policy's admitted plan (the module docstring, THE CHOICE ONCE HELD)."""
    if ctx.target is None:
        return plan.mode == MODE_ARM_ONLY or not ctx.runtime_live
    if plan.mode == run_mode:
        return True
    return bool(ctx.target.auto and plan.mode == MODE_BASE_ADVANCE and ctx.target.carries_capability)


def _run_mode(ctx):
    return None if ctx.target is None else ctx.target.run_mode(ctx._mission["selection"])


def search_bound_policy(ctx):
    """The policy SEARCH chose for this run's block (the decision of the run's SEARCH portfolio, kept after SEARCH), or None
    without one."""
    portfolio = getattr(ctx, "_search_portfolio", None)
    decision = None if portfolio is None else portfolio.last
    return None if decision is None or decision.selected is None else decision.selected.policy


def _proven_admitted_at_search(ctx) -> bool:
    """Whether the proven policy placed this run's block at SEARCH (the situation of the run's SEARCH decision, THE LOADED
    REACH CAP); True when no SEARCH decision says, so that the cap never lapses for want of one."""
    portfolio = getattr(ctx, "_search_portfolio", None)
    decision = None if portfolio is None else portfolio.last
    situation = None if decision is None else decision.situation
    return True if situation is None or situation.proven_admits is None else bool(situation.proven_admits)


def additive_barred(ctx) -> bool:
    """THE PROVEN TIE (module docstring), once held: an auto target's run that carries no capability and that SEARCH bound to
    the proven policy is never handed to an additive policy."""
    target = ctx.target
    return target is not None and bool(target.auto) and not target.carries_capability and search_bound_policy(ctx) == PROVEN


def _records(plans) -> list:
    return [plan.record() for plan in plans]


def _refuse_mode(ctx, ctl, plans, run_mode, grasp_out_mm: float, seen):
    """The proven stage's own refusal for admitted plans this run may not execute: raised, before anything moves."""
    least = placement_selector.select(ctl, [plan for plan in plans if plan.admitted])
    if ctx.target is None:
        raise ctx.failed(ctl.LIVE_FORWARD_EXCLUDED, "PLACEMENT_PLAN: the plan from the measured grasp needs the base, and the base advance of a LIVE run without a mission "
                         "target would be fed the synthetic observation; the cube stays held", evidence=seen, grasp_out_mm=grasp_out_mm, placement_policy=least.policy)
    why = ("this run carries no capability, so the base does not move for it" if least.mode == MODE_BASE_ADVANCE else "a run that carries the capability is placed only through its approach")
    raise ctx.failed("reach_mode_changed_while_held", f"PLACEMENT_PLAN: this target was bound as {run_mode} and the least-motion plan from the measured grasp is {least.policy} "
                     f"({least.mode}); {why}; the cube stays held", evidence=seen, declared_mode=ctx.target.mode, run_mode=run_mode, measured_mode=least.mode,
                     carries_capability=ctx.target.carries_capability, grasp_out_mm=grasp_out_mm, placement_policy=least.policy)


def place(ctx, ctl, summary: dict, measured: dict, seen: dict, template: dict, search_frame: dict, view: dict, third: dict) -> policy_placement.PlacementOutcome:
    """THE CHOICE ONCE HELD and THE IN-RUN FALLBACK (module docstring), with policy_placement.run_placement's own arguments."""
    grasp_out = float(view["tool"]["out_mm"])
    anchor = tuple(float(v) for v in template["cube_center_pad_m"])
    direction = tuple(float(v) for v in ctl.reach_direction_pad(search_frame["homography_image_to_pad"], seen["cube"]["contact_px"]))
    policies, run_mode = placement_selector.enabled(ctl.PLACEMENT_ENVELOPE), _run_mode(ctx)
    held = reach_carry.Held(view=third, grasp_out_mm=grasp_out, grasp_pan_deg=float(view["measured"]["shoulder_pan"]), anchor=anchor, direction=direction, axis=direction,
                            advanced_mm=0.0, rate=float(ctl.BASE_RATE_PRIOR_MM_S), out_cap_mm=None, seen=seen,
                            min_advance_mm=_first_advance_mm(ctl) if _arm_alone_barred(ctx, run_mode) else None)
    at_grasp = placement_selector.proven_plan(ctl, ctl.placement_plan, held.situation(ctl)).admitted
    held = held.but(proven_admits=bool(at_grasp and _proven_admitted_at_search(ctx)))
    decision = placement_selector.decide(ctl, held.situation(ctl), policies, ctl.placement_plan)
    barred = additive_barred(ctx)
    eligible = [plan for plan in decision.plans if plan.admitted and (plan.policy == PROVEN or (not barred and may_execute(ctx, plan, run_mode)))]
    chosen = placement_selector.select(ctl, eligible)
    ctx.state("PLACEMENT_POLICY", policy=None if chosen is None else chosen.policy, plan=None if chosen is None else chosen.record(), policies=_records(decision.plans),
              eligible=[plan.policy for plan in eligible], bound_mode=run_mode, may_drive_the_base=may_drive_the_base(ctx), grasp_out_mm=round(grasp_out, 1),
              bound_policy=search_bound_policy(ctx), additive_barred=barred, evidence=seen)
    summary["placement_fallbacks"] = []
    if barred:  # THE PROVEN TIE: the proven stage answers, placing the block or refusing it with its own code, as with no word
        summary["placement_policy"] = PROVEN
        return policy_placement.run_placement(ctx, ctl, summary, measured, seen, template, search_frame, view, third)
    if chosen is None:
        if any(plan.admitted for plan in decision.plans):
            _refuse_mode(ctx, ctl, decision.plans, run_mode, grasp_out, seen)
        explained = decision.explanation or {}
        raise ctx.failed("reach_placement_infeasible_while_held", f"PLACEMENT_POLICY: no placement policy of this run places the block from the measured grasp: {explained.get('explanation')}; the cube stays held",
                         evidence=seen, anchor_pad_x_m=anchor[0], anchor_pad_y_m=anchor[1], reach_direction_pad_x=direction[0], reach_direction_pad_y=direction[1],
                         anchor_margin_mm=ctl.placed_margin_mm(anchor), required_centre_margin_mm=ctl.PLACE_REQUIRED_CENTRE_MM, grasp_out_mm=grasp_out,
                         bounded_base_advance_limit_mm=ctl.BASE_MAX_TOTAL_ADVANCE_MM, **explained)
    if chosen.policy == PROVEN:
        summary["placement_policy"] = PROVEN
        return policy_placement.run_placement(ctx, ctl, summary, measured, seen, template, search_frame, view, third)
    plan = chosen
    while True:
        attempt = MODULES[plan.policy].execute(ctx, ctl, summary, held, plan)
        if attempt.placement is not None:
            summary["placement_policy"] = plan.policy
            return attempt.placement
        held, previous = attempt.held.but(min_advance_mm=_first_advance_mm(ctl)), plan.policy
        plan, candidates = _next(ctx, ctl, held, policies, run_mode, previous)
        step = {"from_policy": previous, "to_policy": None if plan is None else plan.policy, "why": attempt.why}
        summary["placement_fallbacks"].append(step)
        ctx.state("PLACEMENT_FALLBACK", **step, out_cap_mm=None if held.out_cap_mm is None else round(held.out_cap_mm, 1), base_advance_so_far_mm=round(held.advanced_mm, 1),
                  plan=None if plan is None else plan.record(), candidates=_records(candidates), may_drive_the_base=may_drive_the_base(ctx))
        if plan is None:
            _exhausted(ctx, ctl, held, candidates, attempt)
        if plan.policy == PROVEN:
            summary["placement_policy"] = PROVEN
            return _proven_continuation(ctx, ctl, summary, measured, seen, template, search_frame, view, held, plan)


def _first_advance_mm(ctl) -> float:
    """The least advance a plan must name after the arm alone was refused, and in a run bound BASE_ADVANCE: the first one the
    frozen advance loop really pulses for (policy_advance_then_reach.first_advance_mm)."""
    return MODULES["advance_then_reach"].first_advance_mm(ctl)


def _arm_alone_barred(ctx, run_mode) -> bool:
    """Whether this run may not execute an ARM_ONLY plan (the module docstring, THE CHOICE ONCE HELD): a mission target bound
    BASE_ADVANCE."""
    return ctx.target is not None and run_mode != MODE_ARM_ONLY


def asked(policies, previous: str, tried=()) -> list:
    """The additive policies THE IN-RUN FALLBACK asks after `previous` handed the block back (module docstring): the advance
    member (REPLANNED) again, once, when it is the one that just failed, then the policies after `previous` in the
    selector's order. `tried` is the policies the run has tried, `previous` last."""
    order = list(placement_selector.ORDER)
    again = [module for module in policies if module.WORD == previous == REPLANNED and tuple(tried).count(previous) < 2]
    return again + [module for module in policies if order.index(module.WORD) > order.index(previous)]


def _candidates(ctl, situation, policies, previous: str, tried=()) -> list:
    """Every candidate plan of THE IN-RUN FALLBACK after `previous`: the additive policies it asks (asked), then the proven
    policy's candidate (_proven_candidate), each planned for `situation`."""
    return [module.plan(ctl, situation) for module in asked(policies, previous, tried)] + [_proven_candidate(ctl, situation)]


def _moves_the_base(plan, remaining_mm: float) -> bool:
    """An admitted plan whose advance is more than none and inside `remaining_mm` of the budget."""
    return bool(plan.admitted and plan.base_advance_mm is not None and 0.0 < float(plan.base_advance_mm) <= float(remaining_mm) + 1e-9)


def _next(ctx, ctl, held, policies, run_mode, previous: str) -> tuple:
    """(the next plan of THE IN-RUN FALLBACK or None, every candidate's plan): the policies it asks after `previous` (asked:
    advance_then_reach again once, then the policies after `previous` in the selector's order) and the proven policy's
    candidate, planned from `held`, each admitted one that moves the base inside the budget left and that this run may
    execute, by the least motion."""
    candidates = _candidates(ctl, held.situation(ctl), policies, previous, held.tried)
    remaining = float(ctl.BASE_MAX_TOTAL_ADVANCE_MM) - float(held.advanced_mm)
    eligible = [plan for plan in candidates if _moves_the_base(plan, remaining) and may_drive_the_base(ctx) and (plan.policy == PROVEN or may_execute(ctx, plan, run_mode))]
    return placement_selector.select(ctl, eligible), candidates


def handed_at_search(ctl, situation, policies, proven):
    """THE PROVEN TIE (module docstring): the additive policy the executor may hand the held cube to when SEARCH chose the
    proven policy's arm-alone plan, or None when it never does. `proven` is the controller's placement_plan. The proven
    arm-alone band only shrinks as the grasp moves out (placement_plan: the travels up to the loaded reach less the grasp),
    so a proven plan that still places the block with the arm alone at SEARCH's planning grasp (reach_model.
    planning_grasp_out_mm: the coarse grasp plus the worst recorded under-read) keeps the cube, a tie going to the proven
    policy, for every grasp within that charge: None. So does a block whose loaded reach cap (placement_policy THE LOADED
    REACH CAP) is no farther than the proven arm-alone plan's own reach, ctl.ARM_LOADED_REACH_OUT_MM (a block the proven
    policy chose at SEARCH): no additive arm-alone plan of it reaches past the proven one, so once held an additive policy
    places it only by moving the base, as the proven stage's own flip does. Past the charge, for a block whose cap lies
    farther, the proven plan needs the base, and the least-motion choice once held takes the first additive policy the run
    names (ORDER; each plans the arm alone with no advance there)."""
    if not policies or placement_policy.loaded_reach_cap_mm(ctl, situation, situation.proven_admits) <= float(ctl.ARM_LOADED_REACH_OUT_MM) + 1e-9:
        return None
    grasp = reach_model.planning_grasp_out_mm(situation.grasp_out_mm, situation.measured)
    try:
        mode = proven(situation.anchor_pad_m, situation.direction, grasp, situation.side_allowance_mm)["mode"]
    except CycleFailed:
        mode = None
    return None if mode == MODE_ARM_ONLY else policies[0].WORD


def fallback_at_search(ctl, situation, policies, previous: str):
    """THE CAPABILITY OF AN ARM-ALONE BINDING (module docstring): the plan THE IN-RUN FALLBACK would go on with if `previous`,
    an additive policy that places the block with the arm alone, were refused, planned from SEARCH's `situation` with the
    least advance the advance loop pulses for and the candidates the fallback asks (asked: advance_then_reach's own
    re-plan when it is `previous`), as the fallback plans them: the least-motion admitted candidate that moves the base
    inside the budget, or None when no candidate does."""
    situation = dataclasses.replace(situation, min_advance_mm=_first_advance_mm(ctl))
    eligible = [plan for plan in _candidates(ctl, situation, policies, previous, (previous,)) if _moves_the_base(plan, float(ctl.BASE_MAX_TOTAL_ADVANCE_MM))]
    return placement_selector.select(ctl, eligible)


def proven_base_branch(ctl):
    """placement_plan's signature, its BASE BRANCH alone (the module docstring, THE IN-RUN FALLBACK): the proven rule's base
    advance from the anchor (ctl.base_advance_needed_mm, read when it is called, so inside the proven continuation's rule
    window it is the rule held to the budget this run has left) and a release at about the grasp radius, as placement_plan
    answers BASE_ADVANCE. CycleFailed, with placement_plan's own sentence, when no bounded advance brings the anchor there."""
    def plan(anchor_pad_m, direction, grasp_out_mm, side_allowance_mm=0.0):
        del grasp_out_mm  # the base branch releases at about the grasp radius, wherever that is
        advance = ctl.base_advance_needed_mm(anchor_pad_m, direction, side_allowance_mm)
        if advance is None:
            raise CycleFailed(f"no bounded base advance (at most {float(ctl.BASE_MAX_TOTAL_ADVANCE_MM):.0f} mm) brings the grasp point "
                              f"{float(ctl.PLACE_REQUIRED_CENTRE_MM):.0f} mm inside the pad from here")
        return {"mode": MODE_BASE_ADVANCE, "fk_travel_mm": 0.0, "base_advance_mm": advance}

    plan.base_branch = True
    return plan


def _proven_candidate(ctl, situation):
    """The proven policy's candidate in THE IN-RUN FALLBACK: its plan from where the run stands, or, when that plan is the arm
    alone, its base branch (proven_base_branch): the arm alone from this stance was just refused, and the next plan moves the
    base, as the proven stage itself goes on after its own arm-only carry fell short."""
    proven = placement_selector.proven_plan(ctl, ctl.placement_plan, situation)
    if not (proven.admitted and proven.mode == MODE_ARM_ONLY):
        return proven
    branch = placement_selector.proven_plan(ctl, proven_base_branch(ctl), situation)
    if branch.admitted:
        advance = placement_policy.mm(branch.base_advance_mm)
        sentence = placement_policy.shaped(f"proven advances the base {advance} mm by its base rule and releases at about the grasp radius",
                                           f"proven advances the base {advance} mm by its base rule")
    else:
        sentence = placement_policy.shaped("proven places no release by its base rule within its bounded advance from here", "proven places no bounded release by its base rule")
    return dataclasses.replace(branch, sentence=sentence, values={**branch.values, "base_branch": True})


class _ControllerWith:
    """The controller's view `ctl` with some names answered first (a read-only overlay, as stage_policy.ControllerView reads a
    namespace): what the proven stage is handed when it goes on with its base branch alone."""

    __slots__ = ("_ctl", "_names")

    def __init__(self, ctl, **names):
        object.__setattr__(self, "_ctl", ctl)
        object.__setattr__(self, "_names", dict(names))

    def __getattr__(self, name: str):
        names = object.__getattribute__(self, "_names")
        return names[name] if name in names else getattr(object.__getattribute__(self, "_ctl"), name)

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("a stage policy never assigns into the controller")


def _exhausted(ctx, ctl, held, candidates, attempt):
    """No policy is left (the module docstring, THE IN-RUN FALLBACK): raised with the cube held at the carry pose."""
    moving = [plan for plan in candidates if plan.admitted and plan.base_advance_mm and float(plan.base_advance_mm) > 0.0]
    travel = float(held.view["tool"]["out_mm"]) - float(held.grasp_out_mm)
    reached = None if held.out_cap_mm is None else float(held.out_cap_mm) - float(held.grasp_out_mm)
    if moving and not may_drive_the_base(ctx):
        # Whatever refused the arm alone (attempt.refusal: a carry short of the admission, or an admission that refused a carry
        # that arrived), the policies left place the block only by moving the base.
        if ctx.target is None:
            raise ctx.failed(ctl.LIVE_FORWARD_EXCLUDED, f"PLACEMENT_FALLBACK: the arm alone did not place the block ({attempt.why}), and the base fallback of a LIVE run "
                             "without a mission target would be fed the synthetic observation; the cube stays held at the carry pose", fk_travel_from_grasp_mm=reached,
                             placement_policy=moving[0].policy, refused_by=attempt.refusal)
        raise ctx.failed("reach_arm_only_insufficient_while_held", f"PLACEMENT_FALLBACK: the arm alone did not place the block ({attempt.why}), and the policies left place it "
                         "only by moving the base, which this run carries no capability to move; the cube stays held at the carry pose", fk_travel_from_grasp_mm=reached,
                         worst_case_centre_margin_mm=None if reached is None else ctl.release_margin_worst_mm(held.anchor, held.direction, reached),
                         required_centre_margin_mm=ctl.PLACE_REQUIRED_CENTRE_MM, carry_travel_now_mm=travel, policies_tried="+".join(held.tried), refused_by=attempt.refusal)
    names = [plan.policy for plan in candidates]
    left = max(float(ctl.BASE_MAX_TOTAL_ADVANCE_MM) - float(held.advanced_mm), 0.0)
    raise ctx.failed("reach_policies_exhausted_while_held", f"PLACEMENT_FALLBACK: {attempt.why}, and none of the placement policies asked from here ({', '.join(names)}) places "
                     f"the block by moving the base within the {left:.0f} mm left of the advance budget; the cube stays held at the carry pose", policies_tried="+".join(held.tried),
                     policies_asked="+".join(names), advance_budget_left_mm=left, last_why=attempt.why,
                     out_cap_mm=held.out_cap_mm, base_advance_measured_mm=held.advanced_mm, required_centre_margin_mm=ctl.PLACE_REQUIRED_CENTRE_MM,
                     bounded_base_advance_limit_mm=ctl.BASE_MAX_TOTAL_ADVANCE_MM)


def _cumulative_rule(ctx, ctl, held, proven):
    """The proven placement rule (`proven`, base_advance_needed_mm's own function) held to the advance budget this run has
    left after its measured advance (held.advanced_mm, the frozen loop's own total of the advance before).

    The frozen loop (Cycle._advance_base_held) reads the rule by name before every pulse and, through the capability's
    approach state (Cycle._approach_state), again right after every pulse with the anchor that pulse was measured to. The
    base's path since this rule began is the sum of the moves between the anchors it is read with, in order: the loop's own
    total, pulse for pulse. After a pulse that carries the run's advance (the advance before plus that path) past
    ctl.BASE_MAX_TOTAL_ADVANCE_MM it raises reach_base_advanced_past_bound, before the loop reads anything else: the loop's
    own check after the pulse (base_advanced_past_bound) counts the continuation alone, and on its last pulse nothing else
    would stop it. The loop ends there, as the proven loop alone ends at its own check: at the low scan pose, the cube held,
    nothing further commanded. Before a pulse: None when the run's advance and the need pass the budget, which the loop
    refuses (base_advance_exceeds_bound)."""
    prior, budget = float(held.advanced_mm), float(ctl.BASE_MAX_TOTAL_ADVANCE_MM)
    trail = {"at": (float(held.anchor[0]), float(held.anchor[1])), "path_mm": 0.0}

    def need(anchor, axis, side_allowance_mm=0.0):
        here = (float(anchor[0]), float(anchor[1]))
        trail["path_mm"] += math.hypot(here[0] - trail["at"][0], here[1] - trail["at"][1]) * 1000.0
        trail["at"] = here
        advanced = prior + trail["path_mm"]
        if advanced > budget + 1e-9:
            raise ctx.failed("reach_base_advanced_past_bound", f"the base has advanced {advanced:.0f} mm in this run ({prior:.0f} mm before the proven policy went on, "
                             f"{trail['path_mm']:.0f} mm since), more than the bounded total ({budget:.0f} mm); nothing further is commanded, the cube stays held",
                             advanced_mm=advanced, advanced_before_mm=prior, advanced_since_mm=trail["path_mm"], bounded_total_advance_mm=budget)
        value = proven(anchor, axis, side_allowance_mm)
        if value is None:
            return None
        return value if advanced + float(value) <= budget + 1e-9 else None

    need.advance_rule = PROVEN
    return need


def _proven_continuation(ctx, ctl, summary, measured, seen, template, search_frame, view, held, plan=None) -> policy_placement.PlacementOutcome:
    """The proven policy goes on as the proven stage itself (the module docstring, THE IN-RUN FALLBACK), handed its base
    branch alone when `plan` is that branch."""
    stage_ctl = _ControllerWith(ctl, placement_plan=proven_base_branch(ctl)) if plan is not None and plan.values.get("base_branch") else ctl
    if not held.advanced_mm:
        return policy_placement.run_placement(ctx, stage_ctl, summary, measured, seen, template, search_frame, view, held.view)
    moved = {**template, "cube_center_pad_m": [float(held.anchor[0]), float(held.anchor[1])]}
    summary["base_advance_mm"] = 0.0
    try:
        with ctl._advance_rule(lambda proven: _cumulative_rule(ctx, ctl, held, proven)):
            outcome = policy_placement.run_placement(ctx, stage_ctl, summary, measured, seen, moved, search_frame, view, held.view)
    finally:
        summary["base_advance_mm"] = round(float(held.advanced_mm) + float(summary.get("base_advance_mm") or 0.0), 1)
    if outcome.advanced:
        return dataclasses.replace(outcome, advanced=float(held.advanced_mm) + float(outcome.advanced))
    return dataclasses.replace(outcome, advanced=float(held.advanced_mm), anchor=list(held.anchor), axis=held.axis, rate=held.rate)

