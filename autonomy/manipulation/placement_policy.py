# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""placement_policy: what a PLACEMENT POLICY is to the least-motion selector (placement_selector.py), and the pure release
geometry every policy plans with. Pure: it commands nothing, measures nothing, states nothing and never imports the
controller. `ctl` is the controller's namespace (stage_policy.ControllerView, or the module itself in a test), and every
constant and formula below is READ from it, so there is one value of each.

MANY POLICIES (the operator's directive of 2026-09-30: "it is better to have many policies for different
decisions/steps/stages"). A placement policy is a module of this directory that
  - names itself: WORD, the run word that enables it and the policy's name in every record;
  - declares itself: POLICY, a stage_policy.StagePolicy of the placement stage with that one word, unqualified until the
    commit that adds its qualification record, so its module digest names the rule a record was decided with;
  - plans: plan(ctl, situation) -> Plan, the same answer for the same situation;
  - says whether this build executes it: EXECUTES, True only from the commit that adds its post-grasp executor.
The proven policy is the placement stage's own placement_plan, which placement_selector wraps as PROVEN: it is never
replaced, it stays the fallback, and with none of the words a run plans exactly as it did before them.

THE SITUATION. Where the block is (its PAD_FRAME centre, the anchor), the reach axis at it (a unit vector in PAD_FRAME,
"farther out along the arm"), the grasp out in mm beyond the proven grasp radius (the coarse estimate at SEARCH, measured
False; the FK tool out at the close that held once the cube is held, measured True) and the plan-time side drift allowance
SEARCH passes (the controller's PLACE_SIDE_DRIFT_ALLOWANCE_MM). Two limits a run MEASURED narrow it, both absent until the
run has measured them (the in-run fallback of placement_executor.py): out_cap_mm, the out the loaded arm reached when a
carry of this run stopped short of its target (a plan never counts on more: reach_budget), and advance_budget_mm, what is
left of the proven advance budget after this run's measured advance (advance_budget_mm below; never more than the
controller's BASE_MAX_TOTAL_ADVANCE_MM, the cumulative budget of one cycle). A third, min_advance_mm, is the least base
advance a plan may name once the arm alone was tried from this stance and its release refused: the next plan moves the
base (a policy that plans no advance is refused under it). A fourth, proven_admits, says whether the proven policy places
the block (THE LOADED REACH CAP and THE LAGGING ARM AT THE RELEASE; absent at SEARCH until placement_selector.decide has
planned the proven policy).

THE LOADED REACH CAP (the operator's rulings of 2026-10-01, "cap at proven reach", and of 2026-10-02, operator question Q8).
loaded_reach_cap_mm is the one function every binding and every in-run re-plan reads for the largest MEASURED out a policy's
plan may carry the loaded block to (reach_budget), and whether that plan keeps the lagging-arm buffer at the release
(release_lag_mm reads it). Under the cap an additive policy may only plan a loaded carry inside the proven loaded reach,
ctl.ARM_LOADED_REACH_OUT_MM (95 mm out, where the loaded carries on record stopped gaining): no command of that carry
passes it (loaded_command_cap_mm, which reach_carry.carry_cap_mm reads), and the plan counts only on the out such a command
MEASURES under the record (proven_loaded_reach_cap_mm). The loaded arm lags inside that reach too: every loaded carry step
of 2026-09-19/20 commanded inside +95 mm out measured 14.0 to 19.9 mm short (cycle-run-20260920T103136Z: +93.3 commanded,
+77.3 measured), so the cap keeps the worst recorded loaded shortfall (reach_model.LOADED_SHORTFALL_MAX_MM, 24 mm) between
the command cap and the measured one, as the reach model keeps it between its own two caps: 71 mm measured (verify r8
policy MF1: a plan counting on measuring +95 mm under commands capped at +95 stopped short of it on the record's lag, and
the run ended with the cube held). Past the proven loaded reach the two long loaded carries on record sagged, and the way
back from a sagging loaded arm holds it where it stands (reach_carry, THE WAY BACK UNDER LOAD). The cap applies:
  - to a block the proven policy places (the installed path admits it at SEARCH and again on the measured grasp:
    proven_admits), always (2026-10-01): beyond the cap such a block is placed with base motion, the least-advance plan
    whose carry stays inside it, else the proven policy (the selector's least motion);
  - to every other block whenever a plan under the cap fits the advance budget (2026-10-02, Q8: whenever a plan whose
    loaded carry stays inside the proven loaded reach fits the 120 mm driving budget, it is used for every block, the
    blocks the proven policy refuses included): capped_plan_fits, one of the run's additive policies (the words
    ctl.PLACEMENT_ENVELOPE names) admitting the block when it is planned as a block the proven policy places is planned,
    with an advance inside what the situation leaves of the budget. The operator's block of 2026-09-30 07:14Z is such a
    block: SEARCH plans it with 97 mm of base and a carry to about 58 mm out, its commands inside the proven loaded reach,
    where its longer carry planned 53 mm of base and a carry to about 113 mm out (on the World's models of the recorded
    loaded behaviour that run ended with the cube held on the stretched arm).
A block no such plan serves keeps the reach model's measured-out cap and the longer carries, whose runs may end held for
the operator: THE LONG STRETCH (long_stretch). The function reads the situation it is asked about, at every binding and
every re-plan: a run whose plan under the cap no longer fits what is left of the budget (a measured grasp farther out than
SEARCH planned, a base that slipped or drifted on its way) is planned with the longer carry from there. No measured
loaded-sustain value exists in this build: a record of one is what would move the cap. Among several blocks the binding
reads the blocks in tiers (block_selection, THE BINDING TIERS; the lead's decision of 2026-10-03 and the binding loss
accounting of 2026-10-02): a block the proven policy refuses is bound only where no listed block is one the proven policy
places, and a block only the long stretch places only where no listed block has the proven plan or one under the cap.
Among the blocks the proven policy refuses, the binding asks whether a plan under the cap still fits the budget with the
grasp the record allows past SEARCH's charge (capped_plan_robust: the coarse grasp plus reach_model.
GRASP_UNDERREAD_HELD_MAX_MM, 30.5 mm, where SEARCH charges 22): such a block comes before one whose plan under the cap fits
only inside the charge, which a grasp held farther out turns into the long stretch once held.

THE LAGGING ARM AT THE RELEASE (verify r8 policy MF1). The release admission decides on the MEASURED travel after the
lowering, and a loaded arm releases behind its plan: the carry stops within its arrival tolerance short of the planned out,
and the lowering, commanded 6 mm past the carry's measured out, measured 8.9 to 10.7 mm short of its command on the four
proven base placements of 2026-09-20 (cycle-run-20260920T115018Z, T120226Z, T121517Z, T133924Z), which released 8.9 to
11.7 mm behind their grasp. The proven base rule keeps ctl.BASE_TARGET_BUFFER_MM (15 mm) for that lagging arm: its advance
admits a release that far behind the travel it plans. A plan under the loaded reach cap keeps the same buffer on its own
travel (release_lag_mm): worst_on_grid takes the admission's worst margin over every release from the planned FK travel
down to the buffer less, so the release admission takes the block wherever such an arm releases it, where a plan with 0.5
to 3.5 mm of margin over the admission was refused after the lowering's pull-back. A plan with the longer carry plans with
the admission's own margin, as the rulings leave it the longer try.

THE PLAN. One policy's answer: admitted or not; the placement mode the binding and the capability gate read (ARM_ONLY: no
base motion; BASE_ADVANCE: the capability moves the base), which are placement_plan's own two words; the base advance; the
FK travel of the tool from the grasp to the release; the MEASURED out that travel ends at; the release admission's own
worst-case centre margin at the plan; the margin under the capped-loss travel model (reach_model, THE TRAVEL MODELS: the
physical expectation, never what decides); one sentence; and the named values of its limit.

THE RELEASE MARGIN IN AFFINE FORM. The release admission (the controller's release_margin_worst_mm) takes the smaller margin
of the two ends of the block's travel along the reach axis, less the anchor uncertainty. A pad edge's raw margin is affine in
the displacement s along the axis: margin_e(s) = edge_margins_mm(anchor)_e + s * rate_e, with rate_e = -(axis . e) the
placement solver's own rate. worst_margins_mm is that minimum for whole arrays of ends at once, exactly: a test holds it to
release_margin_worst_mm itself on grids of anchors, axes, advances and travels, so a policy that plans on a grid decides
as the gate will.
"""
import dataclasses
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

import placement_envelope
import reach_model

MODE_ARM_ONLY, MODE_BASE_ADVANCE = "ARM_ONLY", "BASE_ADVANCE"  # placement_plan's words (target_binding.MODES spells the same)
PROVEN = "proven"  # the placement stage's own placement_plan, wrapped by placement_selector
GRID_MM = 1.0  # the proven plan's grid, for travels, advances and moves of the block alike
MOVE_SEARCH_MM = 400.0  # how far along the axis a refusal looks for the move that would place the block (the pad is 415 x 295 mm)


def _finite(value) -> bool:
    return isinstance(value, (int, float, np.floating)) and not isinstance(value, bool) and math.isfinite(float(value))


@dataclass(frozen=True)
class Situation:
    """What a policy plans from (module docstring, THE SITUATION). A reading that is not finite, or an axis of no length, is a
    ValueError: it is never planned from."""

    anchor_pad_m: tuple
    direction: tuple
    grasp_out_mm: float
    measured: bool
    side_allowance_mm: float = 0.0
    out_cap_mm: Optional[float] = None
    advance_budget_mm: Optional[float] = None
    min_advance_mm: Optional[float] = None
    proven_admits: Optional[bool] = None

    def __post_init__(self):
        values = (*tuple(self.anchor_pad_m), *tuple(self.direction), self.grasp_out_mm, self.side_allowance_mm)
        if len(tuple(self.anchor_pad_m)) != 2 or len(tuple(self.direction)) != 2 or not all(_finite(value) for value in values):
            raise ValueError("a situation is a finite anchor, a finite reach axis, a finite grasp out and a finite side allowance")
        if math.hypot(float(self.direction[0]), float(self.direction[1])) < 1e-9:
            raise ValueError("the reach axis has no length")
        for name in ("out_cap_mm", "advance_budget_mm", "min_advance_mm"):
            value = getattr(self, name)
            if value is not None and (not _finite(value) or (name != "out_cap_mm" and float(value) < 0.0)):
                raise ValueError(f"{name} is absent or a finite number of mm (a budget or a least advance is never negative)")
            object.__setattr__(self, name, None if value is None else float(value))
        if self.proven_admits is not None and not isinstance(self.proven_admits, (bool, np.bool_)):
            raise ValueError("proven_admits is absent or whether the proven policy places the block")
        object.__setattr__(self, "proven_admits", None if self.proven_admits is None else bool(self.proven_admits))
        object.__setattr__(self, "anchor_pad_m", (float(self.anchor_pad_m[0]), float(self.anchor_pad_m[1])))
        object.__setattr__(self, "direction", (float(self.direction[0]), float(self.direction[1])))
        object.__setattr__(self, "grasp_out_mm", float(self.grasp_out_mm))
        object.__setattr__(self, "measured", bool(self.measured))
        object.__setattr__(self, "side_allowance_mm", float(self.side_allowance_mm))

    def moved(self, along_mm: float) -> "Situation":
        """The same situation with the block moved along_mm along the reach axis (toward the pad for a positive value): a
        base advance of that much, or the operator's move of the block, as the geometry sees either. The run's measured
        limits, and whether the proven policy places the block, stay what they are."""
        x, y = self.anchor_pad_m
        return Situation((x + self.direction[0] * along_mm / 1000.0, y + self.direction[1] * along_mm / 1000.0), self.direction, self.grasp_out_mm, self.measured,
                         self.side_allowance_mm, self.out_cap_mm, self.advance_budget_mm, self.min_advance_mm, self.proven_admits)


@dataclass(frozen=True)
class Plan:
    """One policy's answer (module docstring, THE PLAN). `values` are the named scalars of its limit (constraint, deficit_mm,
    move_mm, ...), flat, for a refusal record; `raw` is the proven policy's own placement_plan dict, None for any other."""

    policy: str
    admitted: bool
    mode: Optional[str]
    base_advance_mm: Optional[float]
    fk_travel_mm: Optional[float]
    arm_out_mm: Optional[float]
    worst_margin_mm: Optional[float]
    capped_loss_margin_mm: Optional[float]
    sentence: str
    values: dict = field(default_factory=dict)
    raw: Optional[dict] = None

    def record(self) -> dict:
        """The plan as a state's field carries it: flat, every number rounded to 0.1 mm, the sentence as it is."""
        def number(value):
            return None if value is None else round(float(value), 1)

        return {"policy": self.policy, "admitted": bool(self.admitted), "mode": self.mode, "base_advance_mm": number(self.base_advance_mm), "fk_travel_mm": number(self.fk_travel_mm),
                "arm_out_mm": number(self.arm_out_mm), "worst_margin_mm": number(self.worst_margin_mm), "capped_loss_margin_mm": number(self.capped_loss_margin_mm),
                "sentence": self.sentence}


def edge_rates(ctl, direction) -> np.ndarray:
    """mm of raw margin each pad edge (right, left, far, near: ctl.PAD_EDGE_NORMALS) gains per mm of displacement along the
    reach axis: -(axis . e), placement_envelope's rate."""
    return np.array([-(float(direction[0]) * e[0] + float(direction[1]) * e[1]) for e in ctl.PAD_EDGE_NORMALS])


def side_mask(ctl, direction) -> np.ndarray:
    """1.0 on each edge the reach axis runs ALONG (placement_envelope.side_edges, the controller's rule), 0.0 on the others."""
    return np.array([1.0 if side else 0.0 for side in placement_envelope.side_edges(ctl, direction)])


def edge_margins_at(ctl, anchor, direction, displacement_mm, side_allowance_mm: float = 0.0) -> np.ndarray:
    """Each edge's raw margin, less side_allowance_mm on the edges the axis runs along, for the block displaced along the
    reach axis by displacement_mm (a number or an array): shape (..., 4)."""
    margins = np.asarray(ctl.edge_margins_mm(anchor), dtype=float)
    shift = np.asarray(displacement_mm, dtype=float)[..., None]
    return margins + shift * edge_rates(ctl, direction) - float(side_allowance_mm) * side_mask(ctl, direction)


def worst_margins_mm(ctl, anchor, direction, low_mm, high_mm, side_allowance_mm: float = 0.0):
    """The release admission's worst-case centre margin for the block's travel between the displacements low_mm and high_mm
    along the reach axis (numbers or arrays that broadcast): the smaller of the two ends' nearest-edge margins, less the
    anchor uncertainty (ctl.VISUAL_ANCHOR_UNCERTAINTY_MM) and, on the edges the axis runs along, side_allowance_mm. With no
    allowance it is ctl.release_margin_worst_mm exactly (module docstring, THE RELEASE MARGIN IN AFFINE FORM)."""
    low = edge_margins_at(ctl, anchor, direction, low_mm, side_allowance_mm).min(axis=-1)
    high = edge_margins_at(ctl, anchor, direction, high_mm, side_allowance_mm).min(axis=-1)
    worst = np.minimum(low, high) - float(ctl.VISUAL_ANCHOR_UNCERTAINTY_MM)
    return float(worst) if np.ndim(worst) == 0 else worst


def proven_loaded_reach_cap_mm(ctl) -> float:
    """THE LOADED REACH CAP's measured out (module docstring), in mm: the proven loaded reach ctl.ARM_LOADED_REACH_OUT_MM
    less the worst recorded loaded shortfall (reach_model.LOADED_SHORTFALL_MAX_MM), the out a carry whose commands stop at
    the proven loaded reach measures under the record."""
    return float(ctl.ARM_LOADED_REACH_OUT_MM) - reach_model.LOADED_SHORTFALL_MAX_MM


def capped_plan_fits(ctl, situation) -> bool:
    """THE LOADED REACH CAP, the operator's ruling of 2026-10-02 (module docstring): whether one of the run's additive
    placement policies (placement_selector.enabled, the words ctl.PLACEMENT_ENVELOPE names) admits `situation`'s block when
    it is planned as a block the proven policy places is planned (proven_admits True: commands inside the proven loaded
    reach, counting on what they measure under the record, the lagging-arm buffer at the release), its advance inside what
    the situation leaves of the budget (the policy's own plan, admitted). False without a situation or without such a
    policy."""
    if situation is None:
        return False
    import placement_selector  # it imports the policies, which plan with this module: imported when a plan asks, not with it
    capped = dataclasses.replace(situation, proven_admits=True)
    policies = placement_selector.enabled(getattr(ctl, "PLACEMENT_ENVELOPE", ()))
    return any(module.plan(ctl, capped).admitted for module in policies)


def capped_plan_robust(ctl, situation) -> bool:
    """THE LOADED REACH CAP at the binding (module docstring; the lead's decision of 2026-10-03, block_selection THE BINDING
    TIERS): whether capped_plan_fits still holds for `situation`'s block with the grasp the record allows past SEARCH's
    charge: a coarse grasp read reach_model.GRASP_UNDERREAD_HELD_MAX_MM out instead of reach_model.GRASP_UNDERREAD_MM, as if
    held there; a measured grasp as it is. False without a situation."""
    if situation is None:
        return False
    if not situation.measured:
        situation = dataclasses.replace(situation, grasp_out_mm=situation.grasp_out_mm + reach_model.GRASP_UNDERREAD_HELD_MAX_MM, measured=True)
    return capped_plan_fits(ctl, situation)


def loaded_reach_cap_mm(ctl, situation, proven_admits) -> float:
    """THE LOADED REACH CAP (module docstring): the largest MEASURED out a plan for `situation` may carry the loaded block to,
    in mm: proven_loaded_reach_cap_mm for a block the proven policy places (proven_admits), and for any other whenever a plan
    under it fits the advance budget (capped_plan_fits); the reach model's measured-out cap (reach_model.measured_out_cap_mm)
    otherwise. `situation` is also what a measured loaded-sustain record would be read against; no such record exists in
    this build."""
    if proven_admits or capped_plan_fits(ctl, situation):
        return proven_loaded_reach_cap_mm(ctl)
    return reach_model.measured_out_cap_mm(ctl)


def long_stretch(ctl, situation) -> bool:
    """THE LONG STRETCH (module docstring, THE LOADED REACH CAP): whether a policy's plan for `situation` is planned past the
    proven loaded reach, under the reach model's measured-out cap: loaded_reach_cap_mm answers more than
    proven_loaded_reach_cap_mm, for a block the proven policy refuses that no plan of the run's policies under the cap
    serves inside what the situation leaves of the budget."""
    return loaded_reach_cap_mm(ctl, situation, situation.proven_admits) > proven_loaded_reach_cap_mm(ctl) + 1e-9


def loaded_command_cap_mm(ctl, situation, proven_admits) -> float:
    """THE LOADED REACH CAP's bound on the COMMANDS of a carry (module docstring): loaded_reach_cap_mm plus the worst recorded
    loaded shortfall, which the reach model keeps between its command cap and its measured-out cap: the proven loaded reach
    under the cap, the reach model's command cap for the longer carry."""
    return loaded_reach_cap_mm(ctl, situation, proven_admits) + reach_model.LOADED_SHORTFALL_MAX_MM


def release_lag_mm(ctl, situation) -> float:
    """THE LAGGING ARM AT THE RELEASE (module docstring): how much less than its plan's FK travel a loaded arm may release a
    block and the plan still be admitted, in mm: the proven base rule's own lagging-arm buffer (ctl.BASE_TARGET_BUFFER_MM)
    for a plan under the loaded reach cap (loaded_reach_cap_mm answers at most proven_loaded_reach_cap_mm), none for the
    longer carry."""
    capped = loaded_reach_cap_mm(ctl, situation, situation.proven_admits) <= proven_loaded_reach_cap_mm(ctl) + 1e-9
    return float(ctl.BASE_TARGET_BUFFER_MM) if capped else 0.0


def reach_budget(ctl, situation) -> tuple:
    """(the grasp out a plan counts on, the measured-out cap, the FK travel that leaves from the one to the other), in mm:
    reach_model.planning_grasp_out_mm (the coarse grasp plus the worst recorded under-read at SEARCH, the measured one once
    held) and loaded_reach_cap_mm (THE LOADED REACH CAP), or the out this run's loaded arm reached (situation.out_cap_mm)
    when that is less."""
    grasp = reach_model.planning_grasp_out_mm(situation.grasp_out_mm, situation.measured)
    cap = loaded_reach_cap_mm(ctl, situation, situation.proven_admits)
    if situation.out_cap_mm is not None:
        cap = min(cap, situation.out_cap_mm)
    return grasp, cap, cap - grasp


def advance_budget_mm(ctl, situation) -> float:
    """The base advance a plan may name, in mm: the controller's BASE_MAX_TOTAL_ADVANCE_MM, or what is left of it after this
    run's measured advance (situation.advance_budget_mm) when that is less."""
    budget = float(ctl.BASE_MAX_TOTAL_ADVANCE_MM)
    return budget if situation.advance_budget_mm is None else min(budget, situation.advance_budget_mm)


def travel_grid(travel_cap_mm: float) -> np.ndarray:
    """The FK travels 0, 1, 2, ... mm up to travel_cap_mm and never past it; empty when the cap is below 0."""
    if travel_cap_mm < 0.0:
        return np.zeros(0)
    return np.arange(0.0, math.floor(travel_cap_mm + 1e-9) + 0.5, GRID_MM)


def worst_on_grid(ctl, situation, travels, displacements=0.0, model: str = reach_model.ADMISSION, side_allowance_mm: float = 0.0):
    """worst_margins_mm for the block displaced along the axis by `displacements` (a number, or an array on the leading axes)
    and then carried each FK travel of `travels` (the last axis), its low end under `model`; for a situation with a release
    lag (release_lag_mm), the smaller of that and the margin over every travel from the planned one down to the lag less
    (THE LAGGING ARM AT THE RELEASE)."""
    shift, travels = np.asarray(displacements, dtype=float)[..., None], np.asarray(travels, dtype=float)
    anchor, direction = situation.anchor_pad_m, situation.direction
    worst = worst_margins_mm(ctl, anchor, direction, shift + reach_model.travel_low_mm(ctl, travels, model), shift + travels, side_allowance_mm)
    lag = release_lag_mm(ctl, situation)
    if lag <= 0.0:
        return worst
    lagging = travels - lag
    lowest = np.minimum(lagging, reach_model.travel_low_mm(ctl, lagging, model))
    return np.minimum(worst, worst_margins_mm(ctl, anchor, direction, shift + lowest, shift + travels, side_allowance_mm))


def smallest_move_mm(admits, ctl, situation, search_mm: float = MOVE_SEARCH_MM):
    """The move of the block along the reach axis, in whole mm of the smallest size (toward the pad first on a tie), after which
    `admits(ctl, situation, moves)` (an array of booleans for an array of moves) says a policy admits it; None when no move
    within search_mm does."""
    moves = np.arange(-search_mm, search_mm + 0.5, GRID_MM)
    found = moves[np.asarray(admits(ctl, situation, moves), dtype=bool)]
    if found.size == 0:
        return None
    return int(min(found, key=lambda move: (abs(move), -move)))


def edge_worst_margins_mm(ctl, anchor, direction, low_mm: float, high_mm: float, side_allowance_mm: float = 0.0) -> np.ndarray:
    """Each edge's (right, left, far, near) worse margin of one pair of ends, less the anchor uncertainty and, on the edges the
    axis runs along, side_allowance_mm: the four numbers whose minimum is worst_margins_mm."""
    both = np.minimum(edge_margins_at(ctl, anchor, direction, low_mm, side_allowance_mm), edge_margins_at(ctl, anchor, direction, high_mm, side_allowance_mm))
    return both - float(ctl.VISUAL_ANCHOR_UNCERTAINTY_MM)


def short_limit(ctl, anchor, direction, low_mm: float, high_mm: float, side_allowance_mm: float = 0.0) -> tuple:
    """(constraint, edge, deficit) of the limit that keeps one pair of ends from the required centre margin, in the placement
    solver's words: side_edge when an edge the axis runs along is short (no move along the axis grows it), else far_edge
    when an edge the axis runs toward is short (every advance shortens it), else along_budget (the edge the advance gains on
    is short: more advance would place it). The edge is the shortest of that kind; None when nothing is short."""
    margins = edge_worst_margins_mm(ctl, anchor, direction, low_mm, high_mm, side_allowance_mm)
    required = float(ctl.PLACE_REQUIRED_CENTRE_MM)
    sides, rates = placement_envelope.side_edges(ctl, direction), edge_rates(ctl, direction)
    kinds = (("side_edge", [index for index in range(4) if sides[index]]), ("far_edge", [index for index in range(4) if not sides[index] and rates[index] < 0.0]),
             ("along_budget", [index for index in range(4) if not sides[index] and rates[index] >= 0.0]))
    for constraint, edges in kinds:
        short = [index for index in edges if margins[index] < required]
        if short:
            index = min(short, key=lambda edge: margins[edge])
            return constraint, placement_envelope.EDGE_NAMES[index], required - float(margins[index])
    return None


def shaped(*forms) -> str:
    """The first of `forms` in a run record's named text shape (placement_envelope.TEXT_SHAPE: the allowed characters, at most
    TEXT_MAX_CHARS), so no sanitizer strips or cuts it; when none is, the last cut at a space inside the bound."""
    for text in forms:
        if placement_envelope.TEXT_SHAPE.fullmatch(text):
            return text
    text = "".join(character for character in forms[-1] if placement_envelope.TEXT_SHAPE.fullmatch(character)).strip()
    if len(text) <= placement_envelope.TEXT_MAX_CHARS:
        return text
    cut = text.rfind(" ", 0, placement_envelope.TEXT_MAX_CHARS + 1)
    return text[:cut] if cut > 0 else text[:placement_envelope.TEXT_MAX_CHARS]


def mm(value: float) -> str:
    """A length as a sentence states it: 0.1 mm."""
    return f"{float(value):.1f}"
