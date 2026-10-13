# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""placement_envelope: the placement envelope SOLVER (track P of docs/autonomous-demo/overnight-plan-2026-09-26.md), pure.

One question, answered for a block at `anchor` (PAD_FRAME, metres) reached along the unit `direction` from a grasp
`grasp_out_mm` beyond the proven radius: where can it be released, with which base vector and arm travel, and when it
cannot, WHICH limit binds and by how many millimetres. It commands nothing, measures nothing, states nothing and never
imports the controller: `ctl` is the controller's namespace (stage_policy.ControllerView, or the module itself in a test),
from which the solver reads the constants and the pure geometry functions (edge_margins_mm, release_margin_worst_mm,
release_offset_plan) so that there is one value of each.

THE CONFIGURATIONS. `words` selects them; a word this module does not know is ignored (the controller refuses unknown words
before any of this runs).
  none                   placement_plan exactly: the ARM_ONLY carry, else the smallest forward advance after which the
                         anchor meets 58.96 mm on every edge (the proven spelling, bit for bit), within 120 mm.
  projected              the same with the per-edge projected requirement and the plan-time side drift allowance.
  reach_after_advance    after the advance the loaded arm travels further along the axis: the smallest advance for which
                         some FK travel (at most ARM_LOADED_REACH_OUT_MM - grasp_out) puts BOTH ends of the travel
                         segment (travel and travel / FK_TRAVEL_OVERSTATEMENT) at the requirement the advance plan uses.
                         A model: no controller path takes it.
  side_offset            after the advance, a pan offset of the release inside the cap and the return bound, planned by
                         the controller's own release_offset_plan with the plan-time side drift allowance.
With no word the plan is placement_plan's dict, key for key (a test sweeps anchors and axes against it).

THE EXPLANATION. An infeasible answer names its binding `constraint`, the `deficit_mm` and an operator `hint`:
  pickup_inside_grasp_radius  the coarse cube_out lies nearer than the pickup band's inner limit (when the caller passes
                              cube_out_mm);
  lateral_needs_capability    only a lateral base move places it (holonomic_placement finds a pair), which the forward
                              only capability cannot make; the deficit is that lateral;
  side_edge                   an edge the reach axis runs along stays short at the best bounded advance;
  along_budget                the edges the axis crosses need more advance than the 120 mm budget;
  far_edge                    an edge the axis crosses is short before any advance and every advance shortens it (the
                              edge the reach axis runs toward; the block may lie inside it or past it);
  arm_reach                   the arm alone falls short and the caller allows no base motion.
The first that holds, in that order, is the constraint; `constraints` lists every one that holds. When every limit is met
in the continuum and still no advance on the 1 mm grid places the block, the constraint is the one whose edge is short at
the grid advance that leaves the smallest shortfall, and the sentence says it is the grid.

THE HINT. When the base may move and the block is not inside the pickup band, the hint is placing_move's move of the block
(sideways to the left or right as seen from the robot looking along the reach axis, the sign of the checked move and not
the side the pad's middle lies on, and along the reach axis toward the pad or back toward the robot), CHECKED against
the plan under the same rule on the same reach axis: followed, the plan places the block. When placing_move finds no such
move (both side edges short at once, the exactly diagonal axis, extreme tilts) the hint is the binding constraint's own
move and the record says so (hint_places_block False). The pickup band and arm_reach hints are their constraint's own move.

THE TEXT. Every hint and every sentence is a run record's named text value (TEXT_SHAPE: [A-Za-z0-9_.+ -], at most 96
characters), so the controller's sanitizer neither strips nor cuts them; a test sweeps anchors and axes for it.

SIDE EDGES, TOTAL. side_edges() marks an edge as one the axis runs ALONG when the left normal projects on it at least as
much as the direction does. On an exactly diagonal axis (|d_x| == |d_y|) the tie marks every edge as a side edge (none
is crossed), the conservative reading, so a min() or max() over the side edges always has something to read. The
controller's _side_edges uses the same rule since the overnight integration of 2026-09-27; before it, its strict
comparison marked no edge on that axis.
"""
import math
import re

import numpy as np

import holonomic_placement

CONSTRAINTS = ("pickup_inside_grasp_radius", "lateral_needs_capability", "side_edge", "along_budget", "far_edge", "arm_reach")
CONFIGURATION_WORDS = ("projected", "side_offset", "reach_after_advance")
EDGE_NAMES = ("right", "left", "far", "near")  # the order of PAD_EDGE_NORMALS and of every four-margin tuple
TEXT_MAX_CHARS = 96
TEXT_SHAPE = re.compile(r"[A-Za-z0-9_.+ -]{1,96}")  # a run record's named text value (common#/$defs/named_value.value)
_EPS = 1e-9


def _dot(a, b) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1])


def _moved(point, direction, mm: float) -> tuple:
    return (float(point[0]) + float(direction[0]) * mm / 1000.0, float(point[1]) + float(direction[1]) * mm / 1000.0)


def side_edges(ctl, direction) -> tuple:
    """True for each edge (right, left, far, near) the reach axis runs ALONG; an exact tie counts as along (module docstring)."""
    left = (-float(direction[1]), float(direction[0]))
    return tuple(abs(_dot(left, e)) >= abs(_dot(direction, e)) for e in ctl.PAD_EDGE_NORMALS)


def required_margins_mm(ctl, direction, projected: bool, side_allowance_mm: float = 0.0) -> tuple:
    """What an anchor's raw margin must reach on each edge for a planned advance: the proven 58.96 on every edge, or under
    'projected' the buffer times |d . e| per edge plus the allowance on the side edges (side_edges() above)."""
    if not projected:
        return tuple(ctl.PLACE_REQUIRED_CENTRE_MM + ctl.BASE_TARGET_BUFFER_MM + ctl.VISUAL_ANCHOR_UNCERTAINTY_MM for _ in ctl.PAD_EDGE_NORMALS)
    return tuple(ctl.PLACE_REQUIRED_CENTRE_MM + ctl.VISUAL_ANCHOR_UNCERTAINTY_MM + ctl.BASE_TARGET_BUFFER_MM * abs(_dot(direction, e)) + (float(side_allowance_mm) if side else 0.0)
                 for e, side in zip(ctl.PAD_EDGE_NORMALS, side_edges(ctl, direction)))


def side_margin_mm(ctl, anchor, direction) -> float:
    """The anchor's smallest raw margin to the edges the axis runs along. Never empty: side_edges() marks at least two."""
    return min(margin for margin, side in zip(ctl.edge_margins_mm(anchor), side_edges(ctl, direction)) if side)


def axis_advance_needed_unbounded_mm(ctl, anchor, direction, required):
    """The smallest advance along the axis after which the edges the axis CROSSES meet `required`, the bound ignored; None
    when the axis crosses no edge it gains on."""
    needs = []
    for margin, need, e, side in zip(ctl.edge_margins_mm(anchor), required, ctl.PAD_EDGE_NORMALS, side_edges(ctl, direction)):
        rate = -_dot(direction, e)  # mm of margin gained on this edge per mm of advance
        if side or rate <= _EPS:
            continue
        needs.append(max(need - margin, 0.0) / rate)
    return max(needs) if needs else None


def refusal_values(ctl, anchor, direction, side_allowance_mm: float = 0.0, projected=None) -> dict:
    """The four numbers the controller's placement_refusal_values states, with the same keys and, for every axis that is
    not exactly diagonal, the same values; it never raises on an axis (module docstring, SIDE EDGES, TOTAL). `projected`
    None reads the run's words (ctl.PLACEMENT_ENVELOPE) as the controller does."""
    projected = ("projected" in tuple(ctl.PLACEMENT_ENVELOPE)) if projected is None else bool(projected)
    required, sides = required_margins_mm(ctl, direction, projected, side_allowance_mm), side_edges(ctl, direction)
    along = [need for need, side in zip(required, sides) if not side]
    return {"side_margin_mm": side_margin_mm(ctl, anchor, direction), "required_side_margin_mm": max(need for need, side in zip(required, sides) if side),
            "required_axis_margin_mm": max(along) if along else None, "axis_advance_needed_unbounded_mm": axis_advance_needed_unbounded_mm(ctl, anchor, direction, required)}


def _arm_only(ctl, anchor, direction, grasp_out_mm: float):
    """placement_plan's first half, spelled as it is: the ARM_ONLY plan, or None."""
    reach = max(ctl.ARM_LOADED_REACH_OUT_MM - grasp_out_mm, 0.0)
    for travel in np.arange(0.0, reach + 0.5, 1.0):
        if ctl.release_margin_worst_mm(anchor, direction, float(travel)) >= ctl.PLACE_REQUIRED_CENTRE_MM:
            return {"mode": "ARM_ONLY", "fk_travel_mm": float(min(travel + 5.0, max(reach, travel))), "base_advance_mm": 0.0}
    return None


def _advance_only(ctl, anchor, direction, projected: bool, side_allowance_mm: float):
    """base_advance_needed_mm with the configuration explicit instead of read from the run's words, spelled as it is."""
    if not projected:
        for advance in np.arange(0.0, ctl.BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0):
            moved = (float(anchor[0]) + direction[0] * advance / 1000.0, float(anchor[1]) + direction[1] * advance / 1000.0)
            if ctl.release_margin_worst_mm(moved, direction, 0.0) >= ctl.PLACE_REQUIRED_CENTRE_MM + ctl.BASE_TARGET_BUFFER_MM:
                return float(advance)
        return None
    required = required_margins_mm(ctl, direction, True, side_allowance_mm)
    for advance in np.arange(0.0, ctl.BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0):
        moved = (float(anchor[0]) + direction[0] * advance / 1000.0, float(anchor[1]) + direction[1] * advance / 1000.0)
        if all(margin >= need for margin, need in zip(ctl.edge_margins_mm(moved), required)):
            return float(advance)
    return None


def _segment_meets(ctl, point, direction, travel: float, required) -> bool:
    ends = (travel, travel / ctl.FK_TRAVEL_OVERSTATEMENT)
    return all(margin >= need for t in ends for margin, need in zip(ctl.edge_margins_mm(_moved(point, direction, t)), required))


def _reach_after_advance(ctl, anchor, direction, grasp_out_mm: float, required):
    """The smallest advance, then the smallest FK travel, whose travel segment meets `required` on every edge; or None."""
    reach = max(ctl.ARM_LOADED_REACH_OUT_MM - grasp_out_mm, 0.0)
    for advance in np.arange(0.0, ctl.BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0):
        moved = _moved(anchor, direction, float(advance))
        for travel in np.arange(0.0, reach + 0.5, 1.0):
            if _segment_meets(ctl, moved, direction, float(travel), required):
                return float(advance), float(travel)
    return None


def _side_offset(ctl, anchor, direction, grasp_out_mm: float, side_allowance_mm: float, pans, travels):
    """The smallest advance (then travel, then |delta|) whose release, offset by a pan inside the cap and the return bound,
    the controller's release_offset_plan admits; or None. `pans` (grasp, frame, home) in degrees; None assumes the grasp at
    the home pan and the frame at the grasp pan."""
    grasp_pan, frame_pan, home_pan = (0.0, 0.0, 0.0) if pans is None else (float(pans[0]), float(pans[1]), float(pans[2]))
    r = ctl.GRASP_TOOL_RANGE_M * 1000.0 + grasp_out_mm
    for advance in np.arange(0.0, ctl.BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0):
        moved = _moved(anchor, direction, float(advance))
        for travel in travels:
            plan = ctl.release_offset_plan(moved, direction, grasp_pan, frame_pan, home_pan, r, side_allowance_mm, fk_travel_mm=float(travel))
            if plan is not None:
                return float(advance), float(travel), plan
    return None


def _shaped(*forms) -> str:
    """The first of `forms` that is a run record's named text value (TEXT_SHAPE: the allowed characters, at most
    TEXT_MAX_CHARS), so no sanitizer strips or cuts it. The forms are written longest first from the allowed characters
    only, and the last is short; should even it be too long it is cut at a space inside the bound, never mid-number."""
    for text in forms:
        if TEXT_SHAPE.fullmatch(text):
            return text
    text = forms[-1]
    cut = text.rfind(" ", 0, TEXT_MAX_CHARS + 1)
    return text[:cut] if cut > 0 else text[:TEXT_MAX_CHARS]


def _mm(value: float) -> str:
    """A deficit as the sentence states it: 0.1 mm, or 0.01 mm under 0.05 mm so a small one does not read as 0.0."""
    return f"{value:.1f}" if value >= 0.05 else f"{value:.2f}"


def _rate(direction, e) -> float:
    return -_dot(direction, e)  # mm of margin gained on edge e per mm of advance along the axis


def _base_entries(ctl, anchor, direction, projected: bool, side_allowance_mm: float) -> list:
    """The limits a forward-only base advance meets for a block at `anchor`, most binding first: side_edge (an edge the
    axis runs along, short at the best bounded advance), along_budget (the crossed edges the advance gains on need more
    than the budget), far_edge (a crossed edge the advance loses on is short already), each {constraint, deficit_mm, ...}
    with the edge index and the sign of the move that closes it. Empty when every limit is met in the continuum."""
    found = []
    budget = float(ctl.BASE_MAX_TOTAL_ADVANCE_MM)
    required, sides = required_margins_mm(ctl, direction, projected, side_allowance_mm), side_edges(ctl, direction)
    along_need = axis_advance_needed_unbounded_mm(ctl, anchor, direction, required)
    at = budget if along_need is None else min(along_need, budget)  # the best the bounded advance does for the crossed edges
    moved_margins, static = ctl.edge_margins_mm(_moved(anchor, direction, at)), ctl.edge_margins_mm(anchor)
    short = [(need - margin, index) for index, (margin, need, side) in enumerate(zip(moved_margins, required, sides)) if side and need - margin > _EPS]
    if short:
        deficit, index = max(short)
        found.append({"constraint": "side_edge", "deficit_mm": deficit, "edge": EDGE_NAMES[index], "edge_index": index, "edge_margin_mm": static[index],
                      "edge_margin_after_advance_mm": moved_margins[index], "required_edge_margin_mm": required[index], "advance_mm": at})
    if along_need is not None and along_need > budget + _EPS:
        found.append({"constraint": "along_budget", "deficit_mm": along_need - budget, "axis_advance_needed_unbounded_mm": along_need, "advance_budget_mm": budget})
    losing = [(need - margin, index) for index, (margin, need, side, e) in enumerate(zip(static, required, sides, ctl.PAD_EDGE_NORMALS))
              if not side and _rate(direction, e) < -_EPS and need - margin > _EPS]
    if losing:
        deficit, index = max(losing)
        found.append({"constraint": "far_edge", "deficit_mm": deficit, "edge": EDGE_NAMES[index], "edge_index": index, "edge_margin_mm": static[index],
                      "required_edge_margin_mm": required[index]})
    return found


def _grid_entry(ctl, anchor, direction, projected: bool, side_allowance_mm: float) -> dict:
    """When every limit is met in the continuum and still no advance on the 1 mm grid places the block: the edge that is
    short at the grid advance that leaves the smallest shortfall, named as the limit it belongs to."""
    required, sides = required_margins_mm(ctl, direction, projected, side_allowance_mm), side_edges(ctl, direction)
    best = None
    for advance in np.arange(0.0, ctl.BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0):
        margins = ctl.edge_margins_mm(_moved(anchor, direction, float(advance)))
        worst = max((need - margin, index) for index, (margin, need) in enumerate(zip(margins, required)))
        if best is None or worst[0] < best[0][0] - _EPS:
            best = (worst, float(advance), margins)
    (deficit, index), advance, margins = best
    deficit = max(deficit, 0.0)
    e = ctl.PAD_EDGE_NORMALS[index]
    constraint = "side_edge" if sides[index] else ("along_budget" if _rate(direction, e) > 0.0 else "far_edge")
    return {"constraint": constraint, "deficit_mm": deficit, "edge": EDGE_NAMES[index], "edge_index": index, "edge_margin_mm": ctl.edge_margins_mm(anchor)[index],
            "edge_margin_after_advance_mm": margins[index], "required_edge_margin_mm": required[index], "advance_mm": advance, "on_the_grid": True}


def _places(ctl, point, direction, grasp_out_mm: float, projected: bool, side_allowance_mm: float) -> bool:
    return _arm_only(ctl, point, direction, grasp_out_mm) is not None or _advance_only(ctl, point, direction, projected, side_allowance_mm) is not None


HINT_ROUNDS = 6


def placing_move(ctl, anchor, direction, grasp_out_mm: float, projected: bool, side_allowance_mm: float):
    """The move of the block, on the same reach axis, after which the plan under the same rule places it: (sideways_mm, along_mm),
    sideways signed along the axis's left normal (-d_y, d_x) and along signed along the axis (positive toward the pad), both
    whole millimetres. Found by closing the binding limits in turn (at most HINT_ROUNDS rounds) and CHECKED against the plan;
    None when no such move is found (for example when both side edges are short at once)."""
    normal = (-float(direction[1]), float(direction[0]))
    sideways, along = 0, 0
    for _ in range(HINT_ROUNDS):
        point = _moved(_moved(anchor, normal, sideways), direction, along)
        if _places(ctl, point, direction, grasp_out_mm, projected, side_allowance_mm):
            return (sideways, along) if (sideways, along) != (0, 0) else None
        entries = _base_entries(ctl, point, direction, projected, side_allowance_mm) or [_grid_entry(ctl, point, direction, projected, side_allowance_mm)]
        for entry in entries:
            deficit = max(entry["deficit_mm"], 0.0)
            if entry["constraint"] == "along_budget" and "edge_index" not in entry:
                along += max(math.ceil(deficit - _EPS), 1)
                continue
            e = ctl.PAD_EDGE_NORMALS[entry["edge_index"]]
            if entry["constraint"] == "side_edge":
                across = _dot(normal, e)
                sideways += int(-math.copysign(max(math.ceil(deficit / max(abs(across), _EPS) - _EPS), 1), across))
            else:  # a crossed edge: along_budget on the grid gains with advance, far_edge loses with it
                rate = _rate(direction, e)
                along += int(math.copysign(max(math.ceil(deficit / max(abs(rate), _EPS) - _EPS), 1), rate))
    point = _moved(_moved(anchor, normal, sideways), direction, along)
    return (sideways, along) if _places(ctl, point, direction, grasp_out_mm, projected, side_allowance_mm) else None


def move_hint(sideways: int, along: int) -> str:
    """The operator's words for a move of the block (placing_move's signs), in the record's text shape. The side is the
    move's own: positive `sideways` runs along the reach axis's left normal (-d_y, d_x), which in PAD_FRAME (+x toward the
    right edge, +y toward the far edge, right-handed seen from above) is the robot's left as it looks along the reach
    axis, so the words say "to the left as seen from the robot", negative "to the right". They never say which way the
    pad's middle lies: at a pad corner the placing side move can point away from it."""
    side = "" if not sideways else f"{abs(sideways)} mm to the {'left' if sideways > 0 else 'right'}"
    ahead = "" if not along else (f"{along} mm toward the pad" if along > 0 else f"{-along} mm back toward the robot")
    both = " and ".join(part for part in ((f"{side} as seen from the robot" if side else ""), ahead) if part)
    short = " and ".join(part for part in (side, ahead) if part)
    return _shaped(f"move the block {both}", f"move the block {short}", f"move it {short}")


def _explain(ctl, anchor, direction, grasp_out_mm: float, projected: bool, side_allowance_mm: float, cube_out_mm, base_allowed: bool) -> list:
    """Every constraint that holds, most binding first, each {constraint, deficit_mm, hint, sentence, ...}; each hint is that
    constraint's own move and each sentence is in the record's text shape."""
    found = []
    budget = float(ctl.BASE_MAX_TOTAL_ADVANCE_MM)
    if cube_out_mm is not None and float(cube_out_mm) < ctl.PREGRASP_MIN_CUBE_OUT_MM:
        move = ctl.PREGRASP_OUTWARD_BIAS_MM - float(cube_out_mm)  # the pickup band refusal's own move_block_at_least_mm
        nearer = ctl.PREGRASP_MIN_CUBE_OUT_MM - float(cube_out_mm)
        found.append({"constraint": "pickup_inside_grasp_radius", "deficit_mm": nearer,
                      "hint": _shaped(f"move the block at least {math.ceil(move - _EPS):d} mm away from the robot"),
                      "sentence": _shaped(f"the block stands {_mm(nearer)} mm nearer than the pickup band inner limit of {ctl.PREGRASP_MIN_CUBE_OUT_MM:.0f} mm",
                                          f"the block stands {_mm(nearer)} mm nearer than the pickup band")})
    if not base_allowed:
        reach = max(ctl.ARM_LOADED_REACH_OUT_MM - grasp_out_mm, 0.0)
        best = max(ctl.release_margin_worst_mm(anchor, direction, float(t)) for t in np.arange(0.0, reach + 0.5, 1.0))
        deficit = ctl.PLACE_REQUIRED_CENTRE_MM - best
        if deficit > _EPS:
            found.append({"constraint": "arm_reach", "deficit_mm": deficit, "best_with_the_arm_alone_mm": best,
                          "hint": _shaped(f"move the block {math.ceil(deficit - _EPS):d} mm toward the pad or run with the base capability",
                                          f"move the block {math.ceil(deficit - _EPS):d} mm toward the pad"),
                          "sentence": _shaped(f"the arm alone leaves the block {best:.1f} mm from the nearest pad edge against {ctl.PLACE_REQUIRED_CENTRE_MM:.2f} mm required",
                                              f"the arm alone falls {_mm(deficit)} mm short of {ctl.PLACE_REQUIRED_CENTRE_MM:.2f} mm")})
        return found
    entries = _base_entries(ctl, anchor, direction, projected, side_allowance_mm)
    normal = (-float(direction[1]), float(direction[0]))
    for entry in entries:
        deficit = entry["deficit_mm"]
        if entry["constraint"] == "side_edge":
            across = max(abs(_dot(normal, ctl.PAD_EDGE_NORMALS[entry["edge_index"]])), _EPS)
            rule = tuple(required_margins_mm(ctl, direction, projected, side_allowance_mm))
            lateral = holonomic_placement.holonomic_need(anchor, direction, rule, ctl.PAD_SIZE_M, budget, ctl.LATERAL_MAX_TOTAL_MM)
            if lateral is not None and abs(lateral[1]) > _EPS:
                found.append({"constraint": "lateral_needs_capability", "deficit_mm": abs(lateral[1]), "lateral_mm": lateral[1], "advance_mm": lateral[0],
                              "hint": move_hint(int(math.copysign(math.ceil(abs(lateral[1]) - _EPS), lateral[1])), 0),  # the lateral is along the left normal
                              "sentence": _shaped(f"only a lateral base move of {lateral[1]:+.1f} mm with {lateral[0]:.0f} mm of advance places it and the base moves forward only",
                                                  f"only a lateral base move of {lateral[1]:+.1f} mm with {lateral[0]:.0f} mm of advance places it")})
            entry["hint"] = move_hint(int(-math.copysign(math.ceil(deficit / across - _EPS), _dot(normal, ctl.PAD_EDGE_NORMALS[entry["edge_index"]]))), 0)
            entry["sentence"] = _shaped(f"the {entry['edge']} edge along the reach axis is {_mm(deficit)} mm short of {entry['required_edge_margin_mm']:.2f} mm after {entry['advance_mm']:.0f} mm of advance",
                                        f"the {entry['edge']} edge along the reach axis is {_mm(deficit)} mm short")
        elif entry["constraint"] == "along_budget":
            entry["hint"] = move_hint(0, math.ceil(deficit - _EPS))
            entry["sentence"] = _shaped(f"the block needs {entry['axis_advance_needed_unbounded_mm']:.1f} mm of base advance {_mm(deficit)} mm past the along-axis budget of {budget:.0f} mm",
                                        f"the block needs {_mm(deficit)} mm past the along-axis budget of {budget:.0f} mm")
        else:
            rate = abs(_rate(direction, ctl.PAD_EDGE_NORMALS[entry["edge_index"]]))
            entry["hint"] = move_hint(0, -math.ceil(deficit / rate - _EPS))
            entry["sentence"] = _shaped(f"the block is {_mm(deficit)} mm short of {entry['required_edge_margin_mm']:.2f} mm on the {entry['edge']} edge ahead and advancing only shortens it",
                                        f"the block is {_mm(deficit)} mm short on the {entry['edge']} edge ahead")
        found.append(entry)
    if not found:  # every limit met in the continuum, and still no grid advance places the block
        entry = _grid_entry(ctl, anchor, direction, projected, side_allowance_mm)
        entry["hint"] = move_hint(0, 1)
        entry["sentence"] = _shaped(f"on the 1 mm advance grid the {entry['edge']} edge is {_mm(entry['deficit_mm'])} mm short of {entry['required_edge_margin_mm']:.2f} mm at {entry['advance_mm']:.0f} mm of advance",
                                    f"on the 1 mm advance grid the {entry['edge']} edge is {_mm(entry['deficit_mm'])} mm short")
        found.append(entry)
    return found


def solve(ctl, anchor, direction, grasp_out_mm: float, words=(), side_allowance_mm=None, cube_out_mm=None, base_allowed: bool = True, pans=None) -> dict:
    """The release target, the base vector and the arm travel under the configuration `words` selects, or why none exists.

    Returns {feasible, configuration, mode, plan, base_vector_mm, fk_travel_mm, release_pan_offset_deg, release_target_pad_m,
    constraint, deficit_mm, hint, hint_move, sentence, constraints, values}. `plan` is what placement_plan returns under the
    same words (mode, fk_travel_mm, base_advance_mm), or None when it would refuse, with one exception: on an exactly
    diagonal axis under 'projected' the solver's total side edges charge the side allowance on every edge where the
    controller's strict ones charge it on none, so there the solver may refuse what placement_plan places (it is the
    stricter of the two). `mode` is ARM_ONLY, BASE_ADVANCE or None under the FULL configuration (a model word may place what
    placement_plan refuses).

    A refusal's `hint` is, when the base may move and the block is not inside the pickup band, placing_move's CHECKED move
    in words (`hint_move` the (sideways_mm, along_mm) it states): followed on the same reach axis, the plan under the same
    rule places the block. When no such move is found the hint is the binding constraint's own move and `hint_move` is
    None. `values` is flat and scalar and every text in it is in the record's text shape: what a refusal record carries
    (refusal_values() plus constraint, deficit_mm and every other constraint's deficit at 0.1 mm, hint and, when the base
    may move, hint_places_block), empty when
    feasible. `side_allowance_mm` None is the plan-time PLACE_SIDE_DRIFT_ALLOWANCE_MM, charged only under 'projected'."""
    configuration = tuple(word for word in CONFIGURATION_WORDS if word in tuple(words))
    projected = "projected" in configuration
    allowance = ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM if side_allowance_mm is None else float(side_allowance_mm)
    anchor = (float(anchor[0]), float(anchor[1]))
    answer = {"feasible": False, "configuration": configuration, "mode": None, "plan": None, "base_vector_mm": None, "fk_travel_mm": None,
              "release_pan_offset_deg": None, "release_target_pad_m": None, "constraint": None, "deficit_mm": None, "hint": None, "hint_move": None,
              "sentence": None, "constraints": (), "values": {}}
    plan = _arm_only(ctl, anchor, direction, grasp_out_mm)
    if plan is None and base_allowed:
        advance = _advance_only(ctl, anchor, direction, projected, allowance)
        plan = None if advance is None else {"mode": "BASE_ADVANCE", "fk_travel_mm": 0.0, "base_advance_mm": advance}
    answer["plan"] = plan
    pickup_short = cube_out_mm is not None and float(cube_out_mm) < ctl.PREGRASP_MIN_CUBE_OUT_MM
    if plan is not None and not pickup_short:
        target = _moved(anchor, direction, plan["base_advance_mm"] + (plan["fk_travel_mm"] if plan["mode"] == "ARM_ONLY" else 0.0))
        answer.update(feasible=True, mode=plan["mode"], base_vector_mm=(plan["base_advance_mm"], 0.0), fk_travel_mm=plan["fk_travel_mm"], release_pan_offset_deg=0.0,
                      release_target_pad_m=[target[0], target[1]])
        return answer
    if base_allowed and not pickup_short:
        required = required_margins_mm(ctl, direction, projected, allowance)
        reach = max(ctl.ARM_LOADED_REACH_OUT_MM - grasp_out_mm, 0.0)
        if "reach_after_advance" in configuration:
            found = _reach_after_advance(ctl, anchor, direction, grasp_out_mm, required)
            if found is not None:
                target = _moved(anchor, direction, found[0] + found[1])
                answer.update(feasible=True, mode="BASE_ADVANCE" if found[0] > 0.0 else "ARM_ONLY", base_vector_mm=(found[0], 0.0), fk_travel_mm=found[1],
                              release_pan_offset_deg=0.0, release_target_pad_m=[target[0], target[1]])
                return answer
        if "side_offset" in configuration:
            travels = [float(t) for t in np.arange(0.0, reach + 0.5, 5.0)] if "reach_after_advance" in configuration else [0.0]
            found = _side_offset(ctl, anchor, direction, grasp_out_mm, allowance, pans, travels)
            if found is not None:
                advance, travel, offset = found
                answer.update(feasible=True, mode="BASE_ADVANCE" if advance > 0.0 else "ARM_ONLY", base_vector_mm=(advance, 0.0), fk_travel_mm=travel,
                              release_pan_offset_deg=offset["delta_deg"], release_target_pad_m=list(offset["release_point_pad_m"]))
                return answer
    constraints = _explain(ctl, anchor, direction, grasp_out_mm, projected, allowance, cube_out_mm, base_allowed)
    primary = constraints[0]
    hint, move = primary["hint"], None
    if base_allowed and not pickup_short:
        move = placing_move(ctl, anchor, direction, grasp_out_mm, projected, allowance)
        hint = hint if move is None else move_hint(*move)
    public = tuple({k: v for k, v in entry.items() if k != "edge_index"} for entry in constraints)
    answer.update(constraint=primary["constraint"], deficit_mm=primary["deficit_mm"], hint=hint, hint_move=move, sentence=primary["sentence"], constraints=public)
    values = refusal_values(ctl, anchor, direction, allowance, projected)
    values.update(constraint=primary["constraint"], deficit_mm=round(primary["deficit_mm"], 1), hint=hint)  # 0.1 mm, as the sentence and the hint state it
    if base_allowed and not pickup_short:
        values["hint_places_block"] = move is not None
    if "edge" in primary:
        values["binding_edge"] = primary["edge"]
    for entry in constraints[1:]:
        values[f"{entry['constraint']}_deficit_mm"] = round(entry["deficit_mm"], 1)
    answer["values"] = values
    return answer


# The configurations a refusal's model line tries, smallest first: which unqualified words the model says would place it.
MODEL_CONFIGURATIONS = (("projected",), ("projected", "reach_after_advance"), ("projected", "side_offset"), ("projected", "reach_after_advance", "side_offset"))


NOT_FINITE_EXPLANATION = "no limit is named because the anchor or the reach axis or the grasp radius is not finite"


def explain_refusal(ctl, anchor, direction, grasp_out_mm: float, side_allowance_mm=None, words=None, cube_out_mm=None) -> dict:
    """What a placement refusal carries beside its sentence, flat and scalar: refusal_values() under the run's own rule, the
    binding constraint, deficit_mm and hint of the configuration the run actually plans with ('projected' when the run
    names it, else the proven rule), and model_admits_under, the first MODEL_CONFIGURATIONS entry under which the solver
    places the block ('+'-joined words, None when none does), and explanation, the binding constraint's sentence. Every
    text is in the record's text shape (module docstring, THE TEXT). `words` None reads the run's words
    (ctl.PLACEMENT_ENVELOPE). Never raises on an axis. A reading that is not finite (the anchor, the axis or the grasp
    radius) has no limit to name: the answer is NOT_FINITE_EXPLANATION alone, with no hint and no number."""
    readings = (anchor[0], anchor[1], direction[0], direction[1], grasp_out_mm)
    if not all(isinstance(v, (int, float, np.floating)) and math.isfinite(float(v)) for v in readings):
        return {"model_admits_under": None, "explanation": NOT_FINITE_EXPLANATION}
    words = tuple(ctl.PLACEMENT_ENVELOPE) if words is None else tuple(words)
    active = ("projected",) if "projected" in words else ()
    answer = solve(ctl, anchor, direction, grasp_out_mm, active, side_allowance_mm, cube_out_mm)
    values = dict(answer["values"]) if not answer["feasible"] else refusal_values(ctl, anchor, direction, ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM if side_allowance_mm is None else side_allowance_mm, bool(active))
    admits = None
    for configuration in MODEL_CONFIGURATIONS:
        if solve(ctl, anchor, direction, grasp_out_mm, configuration, side_allowance_mm, cube_out_mm)["feasible"]:
            admits = "+".join(configuration)
            break
    values["model_admits_under"] = admits
    values["explanation"] = answer["sentence"]
    return values


def release_edge_margins(ctl, anchor, direction, travel_mm: float, extra_uncertainty_mm: float = 0.0) -> dict:
    """The release admission per edge: for each edge the worst of the two ends of the FK travel segment (travel and travel /
    FK_TRAVEL_OVERSTATEMENT) less the anchor uncertainty and `extra_uncertainty_mm`; their minimum is exactly the admission's
    worst-case centre margin (release_margin_worst_mm less the extra). Also the binding edge and the worst side and crossed
    edge (side_edges()), flat and scalar."""
    ends = (float(travel_mm), float(travel_mm) / ctl.FK_TRAVEL_OVERSTATEMENT)
    per_edge = [min(ctl.edge_margins_mm(_moved(anchor, direction, t))[index] for t in ends) - ctl.VISUAL_ANCHOR_UNCERTAINTY_MM - float(extra_uncertainty_mm) for index in range(4)]
    sides = side_edges(ctl, direction)
    crossed = [value for value, side in zip(per_edge, sides) if not side]
    binding = min(range(4), key=lambda index: per_edge[index])
    out = {f"release_margin_{name}_mm": value for name, value in zip(EDGE_NAMES, per_edge)}
    out.update(release_binding_edge=EDGE_NAMES[binding], release_side_margin_mm=min(value for value, side in zip(per_edge, sides) if side),
               release_along_margin_mm=min(crossed) if crossed else None)
    return out
