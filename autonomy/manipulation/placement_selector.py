# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""placement_selector: the placement policies a run names, planned side by side, and the least-motion choice among them.

It commands nothing and never imports the controller: `ctl` is the controller's namespace (stage_policy.ControllerView, or
the module itself in a test), and the proven plan is the controller's own placement_plan, handed in (`proven`), so that the
proven policy is that function and never a copy of it. Its one raise is refuse_without_executor's, before anything moves.

THE POLICIES. The additive placement policies (POLICIES; placement_policy, MANY POLICIES) each sit behind their own word,
declared by their own stage policy, OFF by default and unqualified: arm_reach (policy_arm_reach) and advance_then_reach
(policy_advance_then_reach). The proven policy (PROVEN) is always planned beside them: placement_plan exactly as the placement
stage decides it under this run's placement-track words (the proven rule when the run names none of them). With none of the
additive words a run's SEARCH is the proven one and this module is not consulted at all.

THE LOADED REACH CAP (placement_policy). decide plans the proven policy first and, for a situation that does not say yet
whether the proven policy places the block (SEARCH's), plans the additive policies with that answer (proven_admits): a block
the proven policy places is planned inside the proven loaded reach, which no additive arm-alone plan passes beyond the
proven arm-alone plan's own reach (a tie at no base motion goes to the proven policy). Any other block is planned inside it
too whenever a plan of the run's policies under it fits the advance budget (the operator's ruling of 2026-10-02:
placement_policy.capped_plan_fits, which reads the run's words, ctl.PLACEMENT_ENVELOPE, as the controller names the
policies it hands this module). The Decision keeps the situation it planned, so whatever reads SEARCH's decision afterwards
plans with the same cap, and says whether its chosen plan is THE LONG STRETCH (long_stretch: an additive plan under the
reach model's cap, placement_policy.long_stretch) and, for a block the proven policy refuses whose chosen plan stays under the
cap at SEARCH, whether a plan under the cap still fits with the grasp the record allows past SEARCH's charge (capped_robust:
placement_policy.capped_plan_robust), which the bindings over several blocks read (block_selection, THE BINDING TIERS).

THE CHOICE (select). The admitted plan with the least base motion. A plan whose advance lies within the advance loop's own
stopping tolerance (ctl.BASE_ADVANCE_TOLERANCE_MM) of the least is the same motion to the robot, and among such ties the
proven policy wins; otherwise the least advance, then the policies' order (ORDER: arm_reach, advance_then_reach, proven).
The operator's directive of 2026-09-30: "Driving may be involved, but should be avoided when unnecessary."

A REFUSAL (explain). When no policy admits the block, the refusal names the BEST NEAR-MISS: the policy the smallest move of the
block places (each additive policy's move along the reach axis, move_mm; the proven policy's checked placing move, sideways
plus along, from the placement solver), with that policy's own limit (constraint, deficit_mm), its sentence as the
explanation, and a hint that is CHECKED: the named policy admits the block once moved so (hint_places_block). When no move
places it for any policy, the hint is the proven solver's constraint hint, unchecked, as the proven refusal has it. The
proven refusal's side and along numbers stay beside it, every policy's own deficit is named (policy_<word>_deficit_mm),
and every text is in the record's text shape.

THE SEARCH ADAPTER (Portfolio). placement_plan's own signature, so that SEARCH asks it exactly where it asks placement_plan:
the selected plan as placement_plan's dict (the proven dict itself when the proven policy is chosen, else mode,
fk_travel_mm, base_advance_mm and the policy), or PlacementRefused (a CycleFailed) carrying the refusal's explanation. It
plans with the coarse grasp of SEARCH (measured False), and keeps its last decision for the executor guard.

THE EXECUTOR GUARD (refuse_without_executor). A policy plans in this build before it executes in it (its EXECUTES flag).
SEARCH refuses, before anything moves, a least-motion plan whose policy this build does not execute:
search_placement_policy_without_executor. Nothing is picked up that the placement stage could not then place.
"""
import dataclasses
from dataclasses import dataclass
from typing import Optional

import placement_envelope
import placement_policy
import policy_advance_then_reach
import policy_arm_reach
from placement_policy import PROVEN, Plan, Situation
from stage_policy import CycleFailed

POLICIES = (policy_arm_reach, policy_advance_then_reach)  # the additive placement policies, least base motion first
WORDS = tuple(module.WORD for module in POLICIES)
ORDER = WORDS + (PROVEN,)
EXECUTOR_REFUSAL = "search_placement_policy_without_executor"  # refuse_without_executor's code, spelled there as a literal (the console's code table reads raise sites)
# The proven refusal's side and along numbers a refusal keeps beside the near-miss (placement_envelope.refusal_values).
_PROVEN_KEPT = ("side_margin_mm", "required_side_margin_mm", "required_axis_margin_mm", "axis_advance_needed_unbounded_mm", "model_admits_under")


def enabled(words) -> tuple:
    """The additive placement policies `words` names, in ORDER."""
    words = tuple(words or ())
    return tuple(module for module in POLICIES if module.WORD in words)


def executes(policy: str) -> bool:
    """Whether this build carries the post-grasp executor of `policy`: the proven one always, an additive one per its EXECUTES."""
    return policy == PROVEN or any(module.WORD == policy and bool(module.EXECUTES) for module in POLICIES)


def _moved(anchor, direction, mm: float) -> tuple:
    return (float(anchor[0]) + float(direction[0]) * mm / 1000.0, float(anchor[1]) + float(direction[1]) * mm / 1000.0)


def proven_plan(ctl, proven, situation: Situation, explained: bool = False) -> Plan:
    """The proven policy's plan: `proven` (the controller's placement_plan) called with the situation's anchor, axis, grasp out
    and side allowance, exactly as SEARCH and the placement stage call it. Its dict is kept whole (raw). Its refusal is the
    controller's sentence, and with `explained` also the placement solver's explanation (placement_envelope.explain_refusal)
    and its checked placing move: the solver's model search takes about a second for a block near a side edge, so it is
    asked only when a refusal is to be explained."""
    anchor, axis, grasp, allowance = situation.anchor_pad_m, situation.direction, situation.grasp_out_mm, situation.side_allowance_mm
    try:
        raw = proven(anchor, axis, grasp, allowance)
    except CycleFailed as refused:
        if not explained:  # the controller's sentence, in the record's text shape (its parentheses would be stripped)
            sentence = f"proven places no release with the arm alone nor with at most {float(ctl.BASE_MAX_TOTAL_ADVANCE_MM):.0f} mm of base advance"
            return Plan(PROVEN, False, None, None, None, None, None, None, placement_policy.shaped(sentence, "proven places no bounded release"), {"refusal": str(refused)})
        values = placement_envelope.explain_refusal(ctl, anchor, axis, grasp, allowance)
        active = ("projected",) if "projected" in tuple(ctl.PLACEMENT_ENVELOPE) else ()
        move = placement_envelope.solve(ctl, anchor, axis, grasp, active, allowance)["hint_move"]
        sentence = values.get("explanation") or placement_policy.shaped("proven places no bounded release")
        return Plan(PROVEN, False, None, None, None, None, None, None, sentence,
                    {**values, "hint_move_sideways_mm": None if move is None else move[0], "hint_move_along_mm": None if move is None else move[1]})
    advance, travel = float(raw["base_advance_mm"]), float(raw["fk_travel_mm"])
    start = _moved(anchor, axis, advance)
    margin = ctl.release_margin_worst_mm(start, axis, travel)
    capped = placement_policy.worst_margins_mm(ctl, start, axis, placement_policy.reach_model.travel_low_mm(ctl, travel, placement_policy.reach_model.CAPPED_LOSS), travel)
    if raw["mode"] == placement_policy.MODE_ARM_ONLY:
        sentence = placement_policy.shaped(f"proven carries the block {placement_policy.mm(travel)} mm with the arm alone")
    else:
        sentence = placement_policy.shaped(f"proven advances the base {placement_policy.mm(advance)} mm and releases at about the grasp radius")
    return Plan(PROVEN, True, raw["mode"], advance, travel, grasp + travel, margin, capped, sentence, {}, raw)


def select(ctl, plans) -> Optional[Plan]:
    """THE CHOICE (module docstring), or None when no plan is admitted."""
    admitted = [plan for plan in plans if plan.admitted]
    if not admitted:
        return None
    least = min(plan.base_advance_mm for plan in admitted)
    tied = [plan for plan in admitted if plan.base_advance_mm <= least + float(ctl.BASE_ADVANCE_TOLERANCE_MM) + 1e-9]
    proven = [plan for plan in tied if plan.policy == PROVEN]
    if proven:
        return proven[0]
    return min(tied, key=lambda plan: (plan.base_advance_mm, ORDER.index(plan.policy) if plan.policy in ORDER else len(ORDER)))


def move_size(plan: Plan):
    """How far the block must move for `plan`'s policy to admit it (mm): an additive policy's move along the axis, the proven
    policy's checked placing move (sideways plus along); None when no move is known to place it."""
    if plan.admitted:
        return 0
    if plan.policy == PROVEN:
        if plan.values.get("hint_places_block") is not True or plan.values.get("hint_move_along_mm") is None:
            return None
        return abs(int(plan.values["hint_move_sideways_mm"])) + abs(int(plan.values["hint_move_along_mm"]))
    move = plan.values.get("move_mm")
    return None if move is None else abs(int(move))


def explain(ctl, situation: Situation, plans) -> dict:
    """A REFUSAL (module docstring): the flat, scalar values a refusal record carries."""
    proven = next(plan for plan in plans if plan.policy == PROVEN)
    ranked = sorted((move_size(plan), ORDER.index(plan.policy), plan) for plan in plans if move_size(plan) is not None)
    near = ranked[0][2] if ranked else proven
    values = {name: proven.values.get(name) for name in _PROVEN_KEPT}
    values.update(near_miss_policy=near.policy, constraint=near.values.get("constraint"), deficit_mm=near.values.get("deficit_mm"), explanation=near.sentence,
                  policies_evaluated="+".join(plan.policy for plan in plans))
    if near.values.get("binding_edge"):
        values["binding_edge"] = near.values["binding_edge"]
    if near.policy == PROVEN:
        values.update(hint=proven.values.get("hint"), hint_places_block=proven.values.get("hint_places_block"), move_along_axis_mm=proven.values.get("hint_move_along_mm"),
                      move_sideways_mm=proven.values.get("hint_move_sideways_mm"))
    else:
        move = int(near.values["move_mm"])
        module = next(module for module in POLICIES if module.WORD == near.policy)
        checked = module.plan(ctl, situation.moved(move)).admitted
        values.update(hint=placement_envelope.move_hint(0, move), hint_places_block=bool(checked), move_along_axis_mm=move, move_sideways_mm=0)
    for plan in plans:
        values[f"policy_{plan.policy}_deficit_mm"] = plan.values.get("deficit_mm")
    return values


@dataclass(frozen=True)
class Decision:
    """Every evaluated policy's plan (in ORDER), the chosen one (None when none is admitted), then the refusal values, the
    situation they were planned for (with proven_admits, THE LOADED REACH CAP), whether the chosen plan is the long stretch
    (an additive plan under the reach model's measured-out cap: placement_policy.long_stretch), for a SEARCH decision of
    a block the proven policy refuses whose chosen additive plan stays under the loaded reach cap, whether a plan under the
    cap still fits at the record's largest grasp under-read (placement_policy.capped_plan_robust; None where it is not
    asked)."""

    plans: tuple
    selected: Optional[Plan]
    explanation: Optional[dict]
    situation: Optional[Situation] = None
    long_stretch: bool = False
    capped_robust: Optional[bool] = None

    def record(self) -> dict:
        """The decision as TARGET_SELECTED states it: the chosen policy, its plan, whether this build executes it, and every
        policy's plan."""
        return {"placement_policy": None if self.selected is None else self.selected.policy, "placement_plan": None if self.selected is None else self.selected.record(),
                "placement_executes": None if self.selected is None else executes(self.selected.policy), "placement_policies": [plan.record() for plan in self.plans]}


def decide(ctl, situation: Situation, policies, proven) -> Decision:
    """Every policy of `policies` and the proven one, planned for `situation`, and the choice (or the refusal). The proven
    policy is planned first: a situation that does not say whether it places the block is planned with its answer
    (module docstring, THE LOADED REACH CAP); a chosen additive plan says whether it is the long stretch, and at SEARCH (a
    situation not measured), for a block the proven policy refuses, whether its plan under the cap is robust."""
    proven_answer = proven_plan(ctl, proven, situation)
    if situation.proven_admits is None:
        situation = dataclasses.replace(situation, proven_admits=proven_answer.admitted)
    plans = tuple(module.plan(ctl, situation) for module in policies) + (proven_answer,)
    selected = select(ctl, plans)
    if selected is not None:
        long = selected.policy != PROVEN and placement_policy.long_stretch(ctl, situation)
        asked = selected.policy != PROVEN and not long and not situation.measured and not proven_answer.admitted
        return Decision(plans, selected, None, situation, long, placement_policy.capped_plan_robust(ctl, situation) if asked else None)
    plans = plans[:-1] + (proven_plan(ctl, proven, situation, explained=True),)  # a refusal to explain: the proven one with the solver's reasons
    return Decision(plans, None, explain(ctl, situation, plans), situation)


class PlacementRefused(CycleFailed):
    """No policy of the run places the block: the CycleFailed placement_plan raises, carrying the refusal's values."""

    def __init__(self, message: str, explanation: dict):
        super().__init__(message)
        self.explanation = explanation


class Portfolio:
    """THE SEARCH ADAPTER (module docstring). `proven` is the controller's placement_plan as it was before any rebinding;
    `policies` the additive policies the run names; `last` the decision of the last call and `last_situation` the situation
    it was planned for, with whether the proven policy places the block (None for a reading that is not finite)."""

    def __init__(self, ctl, proven, policies):
        self.ctl, self.proven, self.policies, self.last, self.last_situation = ctl, proven, tuple(policies), None, None

    @staticmethod
    def situation(anchor, direction, grasp_out_mm, side_allowance_mm=0.0) -> Optional[Situation]:
        """The Situation of a block seen at SEARCH (the coarse grasp), or None for a reading that is not finite."""
        try:
            return Situation(tuple(anchor), tuple(direction), grasp_out_mm, False, side_allowance_mm)
        except (TypeError, ValueError):
            return None

    def decide(self, anchor, direction, grasp_out_mm, side_allowance_mm=0.0) -> Decision:
        """The decision for a block seen at SEARCH (the coarse grasp). A reading that is not finite has no plan and the
        placement solver's own explanation of that (placement_envelope.NOT_FINITE_EXPLANATION)."""
        situation = self.situation(anchor, direction, grasp_out_mm, side_allowance_mm)
        if situation is None:
            return Decision((), None, {"explanation": placement_envelope.NOT_FINITE_EXPLANATION, "model_admits_under": None, "near_miss_policy": None})
        return decide(self.ctl, situation, self.policies, self.proven)

    def __call__(self, anchor, direction, grasp_out_mm, side_allowance_mm=0.0) -> dict:
        decision = self.last = self.decide(anchor, direction, grasp_out_mm, side_allowance_mm)
        self.last_situation = decision.situation
        plan = decision.selected
        if plan is None:
            explained = decision.explanation
            names = "+".join(plan.policy for plan in decision.plans) or "no policy"
            hint = f"; {explained['hint']}" if explained.get("hint") else ""
            raise PlacementRefused(f"no placement policy of this run ({names}) places the block: {explained.get('explanation')}{hint}", explained)
        if plan.policy == PROVEN:
            return plan.raw
        return {"mode": plan.mode, "fk_travel_mm": plan.fk_travel_mm, "base_advance_mm": plan.base_advance_mm, "policy": plan.policy}


def refuse_without_executor(ctx, portfolio, seen) -> None:
    """THE EXECUTOR GUARD (module docstring): after SEARCH chose, before anything moves. Nothing without a portfolio, without
    a choice, or when this build executes the chosen policy."""
    decision = None if portfolio is None else portfolio.last
    if decision is None or decision.selected is None or executes(decision.selected.policy):
        return
    plan = decision.selected
    raise ctx.failed("search_placement_policy_without_executor", f"SEARCH: the least-motion placement is {plan.policy} ({plan.sentence}), and this build plans that policy without executing it; "
                     "nothing was picked up", evidence=seen, placement_policy=plan.policy, base_advance_mm=plan.base_advance_mm, fk_travel_mm=plan.fk_travel_mm,
                     arm_out_mm=plan.arm_out_mm, worst_case_centre_margin_mm=plan.worst_margin_mm, explanation=plan.sentence)
