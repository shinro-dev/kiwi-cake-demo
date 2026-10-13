# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The placement policy arm_reach: the block carried onto the pad by the arm alone, with no base motion.

A placement policy (placement_policy, MANY POLICIES) behind its own word, OFF by default and unqualified: a LIVE run names it
only under the attended label. It never imports the controller: `ctl` is the controller's namespace
(stage_policy.ControllerView, or the module itself in a test), and every constant and formula is read from it.

THE PLAN (plan below). The arm carries the block along the reach axis, from the grasp to a release at most the loaded
reach cap beyond the proven grasp radius (placement_policy.loaded_reach_cap_mm, THE LOADED REACH CAP: for a block the
proven policy places, and for any other whenever a plan of the run under the cap fits the advance budget, the out a carry
whose commands stop at the proven loaded reach ctl.ARM_LOADED_REACH_OUT_MM measures under the record, its release admitted
down to the proven rule's lagging-arm buffer less (THE LAGGING ARM AT THE RELEASE), so that for a block the proven policy
places this policy's band never passes the proven arm-alone plan's own and a tie there goes to the proven policy; for a
block no such plan serves, the reach model's measured-out cap, reach_model.measured_out_cap_mm: the carry target the
controller's own chain and gate admit, less a command margin and the worst loaded shortfall on record). The grasp out a
plan counts on is reach_model.planning_grasp_out_mm: at SEARCH the coarse estimate plus the worst recorded under-read, once
held the measured one. The travel is the smallest FK travel on
the proven plan's 1 mm grid that the controller's own release admission admits, plus AIM_BEYOND_MM when the admission
still admits that (the proven arm-only plan aims 5 mm past its smallest travel in the same way). Nothing else is required of it: the release admission itself, unchanged, is the
hard gate at the release, and the plan is admitted exactly when that gate would admit the planned travel.

PLANNING_TRAVEL_MODEL is the release admission's own model (reach_model.ADMISSION, the block's travel between t /
FK_TRAVEL_OVERSTATEMENT and t). The capped-loss model (reach_model.CAPPED_LOSS) is better evidenced by the recorded carries,
and each plan reports its margin under it (capped_loss_margin_mm) as the physical expectation; it does not admit a plan,
because a plan the gate cannot admit at its travel would carry the loaded arm out and back for nothing.

A REFUSAL names its limit: constraint arm_reach, the best worst-case margin the arm alone reaches inside the cap, the
deficit to the required centre margin, and move_mm, the smallest move of the block along the reach axis (whole mm, toward
the pad when positive) after which this policy admits it, or None when no move along the axis does.

THE BLOCK OF 2026-09-30 07:14Z, AND WHY THE ARM ALONE DOES NOT PLACE IT (the operator's live case; verify r6 policy MF1).
Its PAD_FRAME centre (-35.8, -213.0) mm lies 65.5 mm outside the pad's near edge. Along the reach axis back-solved from
the installed refusal (either sign), the release admission this policy keeps as its hard gate (the block's travel between
t / FK_TRAVEL_OVERSTATEMENT, 1.6, and t; the worst-case centre margin at least 37.96 mm after the 6 mm anchor uncertainty)
first admits a release at 175.5 mm of FK travel. The controller's chain admits carry targets to R_gate 152.5 mm beyond the
proven grasp radius (its 5 mm lateral drift bound refuses 152.75), so from the grasp the coarse estimate put at -5.2 mm the
arm alone has at most 157.7 mm of FK travel, where the best worst-case margin is about 26.9 mm. No arm-alone release the
kept gate admits exists at any legal reach: under that gate the kinematics are the limit, and this policy's own caps
refuse it first (under the reach model's cap, at SEARCH a 104 mm travel cap, the 126 mm measured-out cap less the coarse
grasp clamped at 0 and the 22 mm under-read charge, short even if FK travel were the block's real travel, about 109.6 mm;
once held at -5.2 a 131.2 mm cap; under the proven loaded reach, its cap whenever advance_then_reach is named, less still).
What would change the answer needs two changes, not one: an FK travel ratio qualified at long reach near 1.2 (the 131.2 mm
cap admits the block for a ratio at most 1.19, the chain's whole reach for at most 1.43; the two loaded carries on record
measured about 1.57 and 1.08), AND a smaller SEARCH charge, so that SEARCH binds the arm alone. Under these words, with
advance_then_reach named, it is planned for advance_then_reach inside the proven loaded reach (the operator's ruling of
2026-10-02, Q8, placement_policy THE LOADED REACH CAP): 97 mm of base at SEARCH and 75 to 103 mm once held over measured
grasps -13 to +30.5 mm, a carry to about 58 mm out, no loaded command past +95 mm. Before that ruling it was planned for a
longer carry (53 mm of base at SEARCH, a carry to about 113 mm out), which on the World's models of the recorded loaded
behaviour ended with the cube held on the stretched arm; under the cap the committed World places it under the recorded
loaded sequences and the recorded lag inside the proven loaded reach. No loaded run of this block is on record.

THE EXECUTOR (execute below, EXECUTES True). Once the cube is held and the placement executor (placement_executor.py) has
chosen this policy from the measured grasp: PLACEMENT_PLAN, then the closed-loop carry on the measured out from the held
pose to the planned out (reach_carry, THE CARRY), then the settle (the arm-only check, the proven lowering kept where the
carry was commanded and predicted clear of the executor's floor, the hold check and the proven release admission,
reach_carry THE SETTLE), then, admitted, the proven release and PLACED. Not admitted, the loaded arm comes back to the
carry pose holding the block where every step stays inside what the records show the loaded arm doing (reach_carry, THE WAY
BACK UNDER LOAD), and the attempt hands back where it ended: the out the loaded arm reached becomes the run's measured out
cap, and the placement executor goes on with the next policy that moves the base, or refuses with the cube held. Where the
way back cannot step, it holds the arm where it stands (reach_way_back_held) and the run ends there for the operator, the
in-run fallback not run (the lead's ruling of 2026-10-02 on operator question Q7); on the recorded loaded behaviour that
is the expected ending of a release refused after a lowering. The base never moves under this policy. Every predicate it states carries
its rule (POLICY declares them).
"""
import numpy as np

import placement_policy
import reach_carry
import reach_model
import stage_policy

WORD = "arm_reach"
WORDS = (WORD,)
QUALIFIED = frozenset()  # joins only in the commit that adds its qualification record
# The predicates this policy's executor decides (reach_carry states them inside execute, which carries this rule).
PREDICATES = ("placement_plan", "reach_carry", "arm_only_sufficient", "release_hold", "release_margin", "release_height", "release_admitted", "placed", "clearance_target_met")
POLICY = stage_policy.StagePolicy("placement", __file__, PREDICATES, WORDS, QUALIFIED)
EXECUTES = True  # the post-grasp executor below (module docstring, THE EXECUTOR)

# ---- parameters: D a design choice, with its reason ----
AIM_BEYOND_MM = 5.0  # D: the planned travel aims this far past the smallest travel the admission takes, as the proven arm-only plan does
PLANNING_TRAVEL_MODEL = reach_model.ADMISSION  # the release admission's own model (module docstring)


def admitting_moves(ctl, situation, moves) -> np.ndarray:
    """For each move of the block along the reach axis (mm, an array), whether this policy admits the block moved by it."""
    travels = placement_policy.travel_grid(placement_policy.reach_budget(ctl, situation)[2])
    if travels.size == 0:
        return np.zeros(np.shape(moves), dtype=bool)
    worst = placement_policy.worst_on_grid(ctl, situation, travels, np.asarray(moves, dtype=float), PLANNING_TRAVEL_MODEL)
    return (worst >= ctl.PLACE_REQUIRED_CENTRE_MM).any(axis=-1)


def plan(ctl, situation) -> placement_policy.Plan:
    """arm_reach's answer for `situation` (module docstring, THE PLAN and A REFUSAL)."""
    grasp, cap, travel_cap = placement_policy.reach_budget(ctl, situation)
    travels = placement_policy.travel_grid(travel_cap)
    required = float(ctl.PLACE_REQUIRED_CENTRE_MM)
    worst = placement_policy.worst_on_grid(ctl, situation, travels, 0.0, PLANNING_TRAVEL_MODEL) if travels.size else np.zeros(0)
    admitted = worst >= required
    anchor, axis = situation.anchor_pad_m, situation.direction
    if admitted.any():
        smallest = float(travels[int(np.argmax(admitted))])
        travel = min(smallest + AIM_BEYOND_MM, travel_cap)
        if placement_policy.worst_on_grid(ctl, situation, np.array([travel]), 0.0, PLANNING_TRAVEL_MODEL)[0] < required:  # the high end would leave the pad: keep the smallest
            travel = smallest
        margin = ctl.release_margin_worst_mm(anchor, axis, travel)
        capped = placement_policy.worst_margins_mm(ctl, anchor, axis, reach_model.travel_low_mm(ctl, travel, reach_model.CAPPED_LOSS), travel)
        sentence = placement_policy.shaped(f"arm_reach carries the block {placement_policy.mm(travel)} mm to {placement_policy.mm(grasp + travel)} mm out with no base motion")
        return placement_policy.Plan(WORD, True, placement_policy.MODE_ARM_ONLY, 0.0, travel, grasp + travel, margin, capped, sentence,
                                     {"grasp_out_planned_mm": grasp, "reach_cap_mm": cap, "fk_travel_cap_mm": travel_cap})
    best = float(worst.max()) if worst.size else float(ctl.release_margin_worst_mm(anchor, axis, 0.0))
    deficit = required - best
    sentence = placement_policy.shaped(
        f"arm_reach leaves the block {placement_policy.mm(best)} mm from the nearest pad edge at its {placement_policy.mm(travel_cap)} mm travel cap against {required:.2f} mm",
        f"arm_reach falls {placement_policy.mm(deficit)} mm short of {required:.2f} mm at its travel cap")
    return placement_policy.Plan(WORD, False, None, None, None, None, None, None, sentence,
                                 {"constraint": "arm_reach", "deficit_mm": round(deficit, 1), "best_with_the_arm_alone_mm": best, "grasp_out_planned_mm": grasp,
                                  "reach_cap_mm": cap, "fk_travel_cap_mm": travel_cap, "move_mm": placement_policy.smallest_move_mm(admitting_moves, ctl, situation)})


@POLICY.decides
def execute(ctx, ctl, summary: dict, held: reach_carry.Held, plan: placement_policy.Plan) -> reach_carry.Attempt:
    """THE EXECUTOR (module docstring): `plan` is this policy's plan from `held`, admitted."""
    reach_carry.state_plan(ctx, ctl, WORD, plan, held)
    ctx.state("CARRY", mode=placement_policy.MODE_ARM_ONLY, policy=WORD, tool=ctl.fmt_tool(held.view["tool"]))
    carried = reach_carry.carry(ctx, ctl, WORD, held.view, held.grasp_out_mm, held.grasp_out_mm + float(plan.fk_travel_mm), reach_carry.carry_cap_mm(ctl, held))
    settled = reach_carry.settle(ctx, ctl, WORD, carried, held.grasp_out_mm, held.anchor, held.direction, held.advanced_mm, held.seen)
    if settled.admitted:
        placement = reach_carry.release_and_place(ctx, ctl, summary, settled, held.anchor, held.direction, held.advanced_mm, held.rate, held.axis)
        return reach_carry.Attempt(placement, held, None)
    reached = settled.reached_out_mm if held.out_cap_mm is None else min(settled.reached_out_mm, held.out_cap_mm)
    return reach_carry.Attempt(None, held.but(view=settled.view, out_cap_mm=reached, tried=held.tried + (WORD,)), settled.why, settled.refusal)
