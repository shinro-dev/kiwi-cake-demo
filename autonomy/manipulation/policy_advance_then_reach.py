# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The placement policy advance_then_reach: the smallest measured base advance, then the arm reaches the block onto the pad.

A placement policy (placement_policy, MANY POLICIES) behind its own word, OFF by default and unqualified: a LIVE run names it
only under the attended label. It never imports the controller: `ctl` is the controller's namespace
(stage_policy.ControllerView, or the module itself in a test), and every constant and formula is read from it.

WHY IT EXISTS. The proven base rule brings the block's anchor ITSELF to the release requirement plus a buffer and then
releases at about the grasp radius: it never uses the arm's reach after an advance, which is what refused the block of
2026-09-30 07:14Z (124.7 mm of advance against the 120 mm budget) although the arm can reach the pad from a stance about
70 mm nearer. This policy advances the base only as far as the arm's reach then needs.

THE PLAN (plan below). An advance of 0 when the arm alone places the block: the same carry an arm-only plan makes (the
loaded reach cap from the grasp out a plan counts on, placement_policy THE LOADED REACH CAP, the controller's release
admission at the planned travel, AIM_BEYOND_MM past the smallest travel it takes), an ARM_ONLY plan with no base motion.
Otherwise the smallest
advance on the 1 mm grid, from the first one the frozen advance loop pulses for (more than ctl.BASE_ADVANCE_TOLERANCE_MM:
the loop stops once the remaining need is within that tolerance, so an advance inside it is never driven) up to the proven
budget ctl.BASE_MAX_TOTAL_ADVANCE_MM (or what is left of it after this run's measured advance, placement_policy.
advance_budget_mm), after which an FK travel on the 1 mm grid places the block with two charges:
  THE RESERVE   the loop may stop a whole tolerance short, so the travel leaves reserve_mm of the cap unused: the FK travel
                that makes up that shortfall under the admission's own model (FK_TRAVEL_OVERSTATEMENT x the tolerance).
                From wherever the loop stops, the arm alone then places the block (a test holds every admitted plan to it);
  THE ALLOWANCE the plan-time side drift allowance SEARCH passes comes off the edges the reach axis runs along, for the
                sideways drift of the advance that the loop measures and the plan cannot (the projected rule's reason).
A BASE_ADVANCE plan: the binding and the capability gate read it as the proven base advance is read (the capability before
anything is picked up). The advance is measured, then the arm's travel is planned again from the measured anchor. For a
block the proven policy places, and for any other whenever a plan of the run under it fits the advance budget (the
operator's rulings of 2026-10-01 and 2026-10-02), the cap is the proven loaded reach (placement_policy THE LOADED REACH CAP:
its commands stop there and its plan counts only on the out they measure under the record) and its release keeps the proven
rule's lagging-arm buffer (THE LAGGING ARM AT THE RELEASE), so its least-advance plan is the least advance whose carry stays
inside the cap and whose release a lagging arm still has admitted, and the selector takes the proven policy when that is
no less motion. The operator's block of 2026-09-30 07:14Z is planned so: 97 mm of base at SEARCH, a carry to about 58 mm
out (its longer carry planned 53 mm and a carry to about 113 mm out).

PLANNING_TRAVEL_MODEL is the release admission's own model, as for arm_reach and for the same reason; each plan reports its
margin under the capped-loss model as the physical expectation (capped_loss_margin_mm).

A REFUSAL names its limit: along_budget with the along-axis need (the smallest advance past the budget that would place the
block, axis_advance_needed_mm) and the deficit past the budget; or, when no advance along this axis places it, the edge
that binds at the best advance (side_edge for an edge the axis runs along, far_edge for one it crosses), with its deficit;
and move_mm, the smallest move of the block along the axis after which this policy admits it (None when none does).

THE EXECUTOR (execute below, EXECUTES True). Once the cube is held and the placement executor (placement_executor.py) has
chosen this policy from the measured grasp (or goes on with it after arm_reach's release was refused). A plan whose advance
is 0 (the arm alone places the block) is carried from the held pose as arm_reach carries it: no rise, no pulse. Otherwise:
  THE ADVANCE   the controller's own frozen advance (Cycle.advance_base: HOLD_OBSERVE, then bounded pulses through the base
                primitive the run carries, the capability's for a mission target, each stopped, confirmed at rest and
                MEASURED against the pad), with the placement rule it pulses on, base_advance_needed_mm, rebound by the
                controller for the length of this advance only (ctl._advance_rule) to this policy's remaining need: this
                policy's plan from the measured anchor with no side allowance (the loop measures the sideways drift), the
                run's measured out cap, and what is left of the 120 mm budget after every advance of this run, the base's
                path charged as the loop sums it (the budget is one cycle's, cumulative: remaining_need_rule, THE RUN'S
                BUDGET). The need is 0 once the arm alone places the block, which ends the loop; None past the budget,
                which the loop refuses (base_advance_exceeds_bound, the cube held at the low scan pose); and a pulse of a
                further advance (this policy asked again) that carries the run's advance past the budget ends it right
                there (reach_further_advance_past_bound, the cube held at the low scan pose), the last pulse included.
                Going on after the arm alone was refused from this stance (the held state's min_advance_mm), the need is
                never 0 before the base has moved that least advance;
  THE APPROACH  as the proven stage approaches after an advance: PLACE_APPROACH over the grasp radius plus PLACE_LEAD_MM at
                the grasp pan (the proven pregrasp poses, at most GRASP_POSE_MAX_FORWARD_M out) and the descent to the carry
                height, which is the carry pose;
  THE REACH     the arm's travel planned again from the MEASURED anchor (arm_reach's plan with the loaded reach cap read for
                that situation, what is left of the run's budget included, the measured out cap and no allowance:
                REACH_REPLAN; the carry's commands keep the cap that situation reads, reach_carry.carry_cap_mm), then the
                closed-loop carry and the settle of arm_reach (reach_carry),
                then, admitted, the proven release and PLACED. Not admitted, the loaded arm comes back to the carry pose
                (reach_carry, THE WAY BACK UNDER LOAD) and the attempt hands back where it ended; the placement executor
                asks this policy again, once, from where the run stands (its re-plan with a further pulsed advance), then
                the proven policy from the moved anchor, or refuses with the cube held (placement_executor, THE IN-RUN
                FALLBACK). Where the way back cannot step it holds the arm where it stands (reach_way_back_held) and the
                run ends there for the operator, the in-run fallback not run (the lead's ruling of 2026-10-02, Q7).
Every predicate it states carries its rule (POLICY declares them); the pinned base primitives' own carry none.
"""
import numpy as np

import placement_policy
import policy_arm_reach
import reach_carry
import reach_model
import stage_policy

WORD = "advance_then_reach"
WORDS = (WORD,)
QUALIFIED = frozenset()  # joins only in the commit that adds its qualification record
# The predicates this policy's executor decides (reach_carry states them inside execute, which carries this rule).
PREDICATES = ("placement_plan", "reach_carry", "arm_only_sufficient", "release_hold", "release_margin", "release_height", "release_admitted", "placed", "clearance_target_met")
POLICY = stage_policy.StagePolicy("placement", __file__, PREDICATES, WORDS, QUALIFIED)
EXECUTES = True  # the post-grasp executor below (module docstring, THE EXECUTOR)
APPROACH_CLEARANCE_M = 0.070  # P: the proven stage's PLACE_APPROACH height above the proven grasp (policy_placement.run_placement)
APPROACH_STEP_MM = 5.0  # P: the proven stage's own step back when the non-contact guard refuses the approach

# ---- parameters: D a design choice, with its reason ----
AIM_BEYOND_MM = 5.0  # D: the planned travel aims this far past the smallest travel the admission takes, as the proven arm-only plan does
ADVANCE_SEARCH_MM = 520.0  # D: how far along the axis a refusal looks for the advance that would place the block (its along-axis need)
PLANNING_TRAVEL_MODEL = reach_model.ADMISSION  # the release admission's own model (module docstring)


def reserve_mm(ctl) -> float:
    """THE RESERVE: the FK travel that makes up an advance stopped a whole loop tolerance short, under the admission's model."""
    return float(ctl.FK_TRAVEL_OVERSTATEMENT) * float(ctl.BASE_ADVANCE_TOLERANCE_MM)


def first_advance_mm(ctl) -> float:
    """The smallest advance on the grid the frozen advance loop pulses for: one grid step past its tolerance."""
    return float(ctl.BASE_ADVANCE_TOLERANCE_MM) + placement_policy.GRID_MM


def least_advance_mm(ctl, situation) -> float:
    """The smallest advance a plan of this situation may name, other than 0: first_advance_mm, or the situation's least
    advance (min_advance_mm) on the grid when that is more."""
    least = situation.min_advance_mm or 0.0
    return max(first_advance_mm(ctl), float(np.ceil(least / placement_policy.GRID_MM - 1e-9)) * placement_policy.GRID_MM)


def _no_advance_allowed(situation) -> bool:
    return not situation.min_advance_mm


def _arm_alone(ctl, situation, displacements):
    """The release admission's worst margins for the arm alone (the full cap, no allowance): (the travels, the margins)."""
    travels = placement_policy.travel_grid(placement_policy.reach_budget(ctl, situation)[2])
    return travels, (placement_policy.worst_on_grid(ctl, situation, travels, displacements, PLANNING_TRAVEL_MODEL) if travels.size else None)


def travel_cap_after_advance_mm(ctl, situation) -> float:
    """The largest FK travel an advance's plan may name: the arm alone's travel cap on the grid, less THE RESERVE rounded up to
    the grid, so that the travel that makes up a loop stopped short lies on the arm alone's grid as well."""
    return float(np.floor(placement_policy.reach_budget(ctl, situation)[2] + 1e-9) - np.ceil(reserve_mm(ctl) / placement_policy.GRID_MM - 1e-9) * placement_policy.GRID_MM)


def _after_advance(ctl, situation, displacements):
    """The worst margins after an advance (the reserve kept, the allowance charged): (the travels, the margins)."""
    travels = placement_policy.travel_grid(travel_cap_after_advance_mm(ctl, situation))
    if travels.size == 0:
        return travels, None
    return travels, placement_policy.worst_on_grid(ctl, situation, travels, displacements, PLANNING_TRAVEL_MODEL, situation.side_allowance_mm)


def _admitted(margins, ctl, shape) -> np.ndarray:
    return np.zeros(shape, dtype=bool) if margins is None else (margins >= ctl.PLACE_REQUIRED_CENTRE_MM).any(axis=-1)


def plan_at(ctl, situation, advance_mm: float) -> bool:
    """Whether this policy places the block with exactly advance_mm of advance: the arm alone for 0, the reserve and the
    allowance for an advance the loop pulses for; never for an advance inside the loop's tolerance."""
    advance = float(advance_mm)
    if advance == 0.0:
        return _no_advance_allowed(situation) and bool(_admitted(_arm_alone(ctl, situation, np.zeros(1))[1], ctl, (1,))[0])
    if advance < least_advance_mm(ctl, situation):
        return False
    return bool(_admitted(_after_advance(ctl, situation, np.array([advance]))[1], ctl, (1,))[0])


def admitting_moves(ctl, situation, moves) -> np.ndarray:
    """For each move of the block along the reach axis (mm, an array on the 1 mm grid), whether this policy admits the block
    moved by it with some advance of the grid: 0, or one from first_advance_mm to the budget."""
    moves = np.asarray(moves, dtype=float)
    alone = _admitted(_arm_alone(ctl, situation, moves)[1], ctl, moves.shape) if _no_advance_allowed(situation) else np.zeros(moves.shape, dtype=bool)
    first, budget = least_advance_mm(ctl, situation), placement_policy.advance_budget_mm(ctl, situation)
    if moves.size == 0 or budget < first:
        return alone
    low = float(moves.min()) + first
    totals = np.arange(low, float(moves.max()) + budget + 0.5, placement_policy.GRID_MM)
    placed = _admitted(_after_advance(ctl, situation, totals)[1], ctl, totals.shape).astype(int)
    counts = np.concatenate(([0], np.cumsum(placed)))
    start = np.round((moves + first - low) / placement_policy.GRID_MM).astype(int)
    stop = np.round((moves + budget - low) / placement_policy.GRID_MM).astype(int) + 1
    return alone | (counts[stop] - counts[start] > 0)


def _aimed(ctl, situation, travels, admitted, advance: float, cap: float, side_allowance_mm: float) -> float:
    """The smallest admitted travel, AIM_BEYOND_MM past it when the admission (with the same allowance) still takes that."""
    smallest = float(travels[int(np.argmax(admitted))])
    travel = min(smallest + AIM_BEYOND_MM, cap)
    return travel if placement_policy.worst_on_grid(ctl, situation, np.array([travel]), advance, PLANNING_TRAVEL_MODEL, side_allowance_mm)[0] >= ctl.PLACE_REQUIRED_CENTRE_MM else smallest


def plan(ctl, situation) -> placement_policy.Plan:
    """advance_then_reach's answer for `situation` (module docstring, THE PLAN and A REFUSAL)."""
    grasp, cap, travel_cap = placement_policy.reach_budget(ctl, situation)
    required, budget, allowance = float(ctl.PLACE_REQUIRED_CENTRE_MM), placement_policy.advance_budget_mm(ctl, situation), situation.side_allowance_mm
    budget_values = {"grasp_out_planned_mm": grasp, "reach_cap_mm": cap, "fk_travel_cap_mm": travel_cap, "reserve_mm": reserve_mm(ctl), "side_allowance_mm": allowance}
    travels, alone = _arm_alone(ctl, situation, 0.0)
    if _no_advance_allowed(situation) and alone is not None and (alone >= required).any():
        travel = _aimed(ctl, situation, travels, alone >= required, 0.0, travel_cap, 0.0)
        return _admitted_plan(ctl, situation, 0.0, travel, grasp, budget_values)
    first = least_advance_mm(ctl, situation)
    advances = np.arange(first, ADVANCE_SEARCH_MM + 0.5, placement_policy.GRID_MM)
    travels, after = _after_advance(ctl, situation, advances)
    placed = _admitted(after, ctl, advances.shape)
    inside = placed & (advances <= budget + 1e-9)
    if inside.any():
        index = int(np.argmax(inside))
        advance = float(advances[index])
        travel = _aimed(ctl, situation, travels, after[index] >= required, advance, travel_cap_after_advance_mm(ctl, situation), allowance)
        return _admitted_plan(ctl, situation, advance, travel, grasp, budget_values)
    move = placement_policy.smallest_move_mm(admitting_moves, ctl, situation)
    if placed.any():
        need = float(advances[int(np.argmax(placed))])
        sentence = placement_policy.shaped(f"advance_then_reach needs {placement_policy.mm(need)} mm of base advance {placement_policy.mm(need - budget)} mm past the {budget:.0f} mm budget",
                                           f"advance_then_reach needs {placement_policy.mm(need - budget)} mm past the {budget:.0f} mm advance budget")
        return placement_policy.Plan(WORD, False, None, None, None, None, None, None, sentence,
                                     {"constraint": "along_budget", "deficit_mm": round(need - budget, 1), "axis_advance_needed_mm": need, "advance_budget_mm": budget,
                                      **budget_values, "move_mm": move})
    if after is None:  # no travel is left once the reserve is kept: nothing after an advance can place it
        best_advance, best_travel = 0.0, 0.0
    else:
        row, column = divmod(int(np.argmax(after)), after.shape[-1])
        best_advance, best_travel = float(advances[row]), float(travels[column])
    low = best_advance + float(reach_model.travel_low_mm(ctl, best_travel, PLANNING_TRAVEL_MODEL))
    limit = placement_policy.short_limit(ctl, situation.anchor_pad_m, situation.direction, low, best_advance + best_travel, allowance)
    constraint, edge, deficit = limit if limit is not None else ("along_budget", None, 0.0)
    where = f"on the {edge} edge " if edge else ""
    sentence = placement_policy.shaped(f"advance_then_reach leaves the block {placement_policy.mm(deficit)} mm short {where}at its best advance of {best_advance:.0f} mm",
                                       f"advance_then_reach is {placement_policy.mm(deficit)} mm short {where}".strip())
    return placement_policy.Plan(WORD, False, None, None, None, None, None, None, sentence,
                                 {"constraint": constraint, "deficit_mm": round(deficit, 1), "binding_edge": edge, "best_advance_mm": best_advance, **budget_values, "move_mm": move})


def _admitted_plan(ctl, situation, advance: float, travel: float, grasp: float, budget_values: dict) -> placement_policy.Plan:
    anchor = situation.moved(advance).anchor_pad_m
    axis = situation.direction
    margin = ctl.release_margin_worst_mm(anchor, axis, travel)
    capped = placement_policy.worst_margins_mm(ctl, anchor, axis, reach_model.travel_low_mm(ctl, travel, reach_model.CAPPED_LOSS), travel)
    if advance == 0.0:
        sentence = placement_policy.shaped(f"advance_then_reach needs no advance and the arm carries the block {placement_policy.mm(travel)} mm to {placement_policy.mm(grasp + travel)} mm out")
        mode = placement_policy.MODE_ARM_ONLY
    else:
        sentence = placement_policy.shaped(f"advance_then_reach advances the base {advance:.0f} mm then the arm carries the block {placement_policy.mm(travel)} mm to {placement_policy.mm(grasp + travel)} mm out",
                                           f"advance_then_reach advances {advance:.0f} mm then carries {placement_policy.mm(travel)} mm")
        mode = placement_policy.MODE_BASE_ADVANCE
    return placement_policy.Plan(WORD, True, mode, advance, travel, grasp + travel, margin, capped, sentence, dict(budget_values))


def along_mm(ctl_direction, start, anchor) -> float:
    """How far `anchor` lies from `start` along the unit `ctl_direction`, in mm (PAD_FRAME metres in)."""
    return ((float(anchor[0]) - float(start[0])) * float(ctl_direction[0]) + (float(anchor[1]) - float(start[1])) * float(ctl_direction[1])) * 1000.0


def remaining_need_rule(ctx, ctl, held: reach_carry.Held):
    """THE ADVANCE's rule (module docstring): base_advance_needed_mm's signature, this policy's remaining need from the
    measured anchor the advance loop hands it, held to the budget this run has left.

    THE RUN'S BUDGET. The frozen loop (Cycle._advance_base_held) reads the rule by name before every pulse and, through the
    capability's approach state (Cycle._approach_state), again right after every pulse with the anchor that pulse was
    measured to. The base's path since this advance began is the sum of the moves between the anchors it is read with, from
    held.anchor, in order: the loop's own total, pulse for pulse, wherever the base drifted (its progress along the axis
    alone would charge a drifting base less than it travelled). Before a pulse the plan is held to what is left of the
    budget after the run's advance before this one (held.advanced_mm) and that path, so no need it answers carries the run
    past ctl.BASE_MAX_TOTAL_ADVANCE_MM: None when none fits, which the loop refuses (base_advance_exceeds_bound). The loop's
    own check after a pulse counts its own total, the run's whole advance only when nothing was advanced before (then that
    check ends the run, base_advanced_past_bound, as with the proven rule). A further advance (this policy asked again,
    placement_executor THE IN-RUN FALLBACK) starts after `prior` mm, and on the loop's last pulse nothing else would stop it:
    so after a pulse that carries the run's advance (the advance before plus the path) past the budget the rule raises
    reach_further_advance_past_bound, before the loop reads anything else, as placement_executor._cumulative_rule does for
    the proven policy going on. The loop ends there: at the low scan pose, the cube held, nothing further commanded."""
    start, prior, budget = held.anchor, float(held.advanced_mm), float(ctl.BASE_MAX_TOTAL_ADVANCE_MM)
    trail = {"at": (float(start[0]), float(start[1])), "path_mm": 0.0}

    def need(anchor, axis, side_allowance_mm=0.0):
        del axis, side_allowance_mm  # the arm reaches along the SEARCH direction, and the loop measures the drift
        here = (float(anchor[0]), float(anchor[1]))
        trail["path_mm"] += float(np.hypot(here[0] - trail["at"][0], here[1] - trail["at"][1])) * 1000.0
        trail["at"] = here
        advanced = prior + trail["path_mm"]
        if prior > 0.0 and advanced > budget + 1e-9:
            raise ctx.failed("reach_further_advance_past_bound", f"advance_then_reach asked again: the base has advanced {advanced:.0f} mm in this run ({prior:.0f} mm before "
                             f"this further advance, {trail['path_mm']:.0f} mm in it), more than the bounded total ({budget:.0f} mm); nothing further is commanded, the cube stays held",
                             advanced_mm=advanced, advanced_before_mm=prior, advanced_since_mm=trail["path_mm"], bounded_total_advance_mm=budget, policy=WORD)
        remaining = budget - advanced
        if remaining < 0.0:
            return None
        moved = max(along_mm(held.direction, start, anchor), 0.0)
        least = None if not held.min_advance_mm or moved >= held.min_advance_mm else held.min_advance_mm - moved
        situation = placement_policy.Situation(anchor, held.direction, held.grasp_out_mm, True, 0.0, held.out_cap_mm, remaining, least, held.proven_admits)
        answer = plan(ctl, situation)
        return float(answer.base_advance_mm) if answer.admitted else None

    need.advance_rule = WORD  # the controller's rule window refuses to nest one rule inside another
    return need


def replan_situation(ctl, held: reach_carry.Held) -> placement_policy.Situation:
    """THE REACH's situation (module docstring): the arm's travel planned again from the measured anchor `held` holds after
    the advance, with no side allowance (the loop measured the drift), the run's measured out cap, what is left of the run's
    budget after every measured advance (the loaded reach cap reads it: placement_policy.capped_plan_fits) and whether the
    proven policy places the block; no least advance (from the moved stance the arm alone has not been refused yet)."""
    left = max(float(ctl.BASE_MAX_TOTAL_ADVANCE_MM) - float(held.advanced_mm), 0.0)
    return placement_policy.Situation(held.anchor, held.direction, held.grasp_out_mm, True, 0.0, held.out_cap_mm, left, proven_admits=held.proven_admits)


def approach(ctx, ctl, held: reach_carry.Held) -> dict:
    """THE APPROACH (module docstring): the proven stage's PLACE_APPROACH and its descent to the carry height, from the low
    scan pose the advance ended at. Returns the carry pose's view."""
    wanted_mm = max(held.grasp_out_mm + ctl.PLACE_LEAD_MM, 0.0)
    forward_mm, poses = min(wanted_mm, ctl.GRASP_POSE_MAX_FORWARD_M * 1000.0), None
    while poses is None:  # as at PREGRASP: the unchanged non-contact guard may refuse the farther target
        try:
            poses = ctl.cli.pregrasp_poses(ctx.pose(), held.grasp_pan_deg, forward_mm / 1000.0, APPROACH_CLEARANCE_M)
        except ctl.ScanRefused as refusal:
            if forward_mm < APPROACH_STEP_MM:
                raise ctx.failed("reach_approach_no_pose", f"PLACE_APPROACH: after the measured advance no pose above the release point passes the guards: {refusal}; the cube stays held",
                                 forward_mm=forward_mm, wanted_mm=wanted_mm, base_advance_measured_mm=held.advanced_mm, policy=WORD) from None
            forward_mm -= APPROACH_STEP_MM
    over = ctx.run_pose(poses, True, ctl.cli.PREGRASP_MIN_LINK_HEIGHT_M, manipulation_step=False, label="PLACE_APPROACH over the release point (cube held)")
    return ctx.run_pose(ctl.proven_orientation_pose(over["measured"], wanted_mm / 1000.0, ctl.CARRY_HEIGHT_MM / 1000.0)[0], True, ctl.CONTACT_FLOOR_M,
                        label="PLACE descend to the carry height")


@POLICY.decides
def execute(ctx, ctl, summary: dict, held: reach_carry.Held, plan_: placement_policy.Plan) -> reach_carry.Attempt:
    """THE EXECUTOR (module docstring): `plan_` is this policy's plan from `held`, admitted, with a base advance."""
    reach_carry.state_plan(ctx, ctl, WORD, plan_, held)
    if not plan_.base_advance_mm:  # THE PLAN's advance of 0: the arm alone, carried from the held pose as arm_reach carries it
        ctx.state("CARRY", mode=placement_policy.MODE_ARM_ONLY, policy=WORD, tool=ctl.fmt_tool(held.view["tool"]))
        carried = reach_carry.carry(ctx, ctl, WORD, held.view, held.grasp_out_mm, held.grasp_out_mm + float(plan_.fk_travel_mm), reach_carry.carry_cap_mm(ctl, held))
        return _settled(ctx, ctl, summary, held, carried)
    ctx.state("CARRY", mode=placement_policy.MODE_BASE_ADVANCE, policy=WORD, tool=ctl.fmt_tool(held.view["tool"]))
    prior = float(held.advanced_mm)
    summary["base_advance_mm"] = round(prior, 1)
    with ctl._advance_rule(lambda proven: remaining_need_rule(ctx, ctl, held)):
        anchor, advanced, rate, axis = ctx.advance_base(held.anchor, held.direction)
    total = prior + float(advanced)
    summary["base_advance_mm"] = round(total, 1)
    held = held.but(anchor=tuple(float(v) for v in anchor), axis=tuple(float(v) for v in axis), advanced_mm=total, rate=float(rate))
    replanned = replan_situation(ctl, held)
    arm = policy_arm_reach.plan(ctl, replanned)
    ctx.state("REACH_REPLAN", policy=WORD, base_advance_mm=round(total, 1), anchor_pad_m=[round(v, 4) for v in held.anchor], anchor_margin_mm=round(ctl.placed_margin_mm(held.anchor), 1),
              admitted=arm.admitted, fk_travel_mm=None if arm.fk_travel_mm is None else round(arm.fk_travel_mm, 1), arm_out_mm=None if arm.arm_out_mm is None else round(arm.arm_out_mm, 1),
              worst_case_centre_margin_mm=None if arm.worst_margin_mm is None else round(arm.worst_margin_mm, 1), sentence=arm.sentence)
    held = held.but(view=approach(ctx, ctl, held))
    if not arm.admitted:
        return reach_carry.Attempt(None, held.but(tried=held.tried + (WORD,)), "from where the measured advance ended the arm alone does not place the block",
                                   reach_carry.REPLAN_SHORT)
    carried = reach_carry.carry(ctx, ctl, WORD, held.view, held.grasp_out_mm, held.grasp_out_mm + float(arm.fk_travel_mm), reach_carry.carry_cap_mm(ctl, held, replanned))
    return _settled(ctx, ctl, summary, held, carried)


def _settled(ctx, ctl, summary: dict, held: reach_carry.Held, carried: reach_carry.Carry) -> reach_carry.Attempt:
    """The settle after this policy's carry, then the proven release and PLACED, or the attempt handed back retracted."""
    settled = reach_carry.settle(ctx, ctl, WORD, carried, held.grasp_out_mm, held.anchor, held.direction, held.advanced_mm, held.seen)
    if settled.admitted:
        return reach_carry.Attempt(reach_carry.release_and_place(ctx, ctl, summary, settled, held.anchor, held.direction, held.advanced_mm, held.rate, held.axis), held, None)
    reached = settled.reached_out_mm if held.out_cap_mm is None else min(settled.reached_out_mm, held.out_cap_mm)
    return reach_carry.Attempt(None, held.but(view=settled.view, out_cap_mm=reached, tried=held.tried + (WORD,)), settled.why, settled.refusal)
