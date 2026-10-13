#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""holonomic_placement: the PLANNER of T6 step 3b (docs/autonomous-demo/one-block-demo-2026-09-26.md), pure and numpy-only.

The LeKiwi base is holonomic (plan section 8.10; +y.vel confirmed through the gateway clamp on 2026-09-15). Where the
forward-only advance of the proven controller cannot bring the cube centre to the required margin on every pad edge, a
lateral move along the LEFT normal of the reach axis can grow the side margins the advance cannot touch. This module
answers ONE question: the smallest (advance, lateral) pair, on a 1 mm grid of advance, under a given per-edge rule, or
None. It commands nothing, measures nothing and imports nothing of the executor: the executor hands it the rule built
from its own constants (a test keeps the two equal), and takes the answer only under the envelope word base_lateral,
which no LIVE run may name before a ruling on the command path and a qualification record (T6 step 3b).

Conventions. PAD_FRAME, metres in, millimetres out. `direction` is the unit reach direction d; the lateral is signed along
the left normal n = (-d_y, d_x), positive = to the LEFT of the reach axis. The rule is the tuple of required raw margins
for the four edges in the order (right, left, far, near), the outward normals (+x, -x, +y, -y): the same order as the
executor's edge_margins_mm and required_edge_margins_mm.
"""
import math

import numpy as np

EDGE_NORMALS = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))  # right, left, far, near


def edge_margins_mm(point_pad_m, pad_size_m) -> tuple:
    """Raw distance from a pad point to each edge (right, left, far, near), negative outside."""
    x, y = float(point_pad_m[0]) * 1000.0, float(point_pad_m[1]) * 1000.0
    half_x, half_y = float(pad_size_m[0]) * 500.0, float(pad_size_m[1]) * 500.0
    return (half_x - x, half_x + x, half_y - y, half_y + y)


def _side_edges(direction) -> tuple:
    """True for each edge the reach axis runs ALONG (the left normal projects on it more than the direction does)."""
    n = (-float(direction[1]), float(direction[0]))
    return tuple(abs(n[0] * e[0] + n[1] * e[1]) > abs(float(direction[0]) * e[0] + float(direction[1]) * e[1]) for e in EDGE_NORMALS)


def lateral_interval_mm(point_pad_m, direction, rule, pad_size_m, sides_only: bool = False):
    """The interval of signed lateral moves (along the left normal, from `point`) after which EVERY edge meets its rule, or
    None when no lateral can (an edge the normal does not reach is short of its rule). Moving `lateral` mm along n changes
    the margin to edge e by -lateral * (n . e). sides_only: the edges the axis runs along, for a refusal's numbers."""
    n = (-float(direction[1]), float(direction[0]))
    low, high = -math.inf, math.inf
    for margin, need, e, side in zip(edge_margins_mm(point_pad_m, pad_size_m), rule, EDGE_NORMALS, _side_edges(direction)):
        if sides_only and not side:
            continue
        gain = -(n[0] * e[0] + n[1] * e[1])  # mm of margin on this edge per mm of lateral
        if abs(gain) < 1e-9:
            if margin < float(need):
                return None
            continue
        bound = (float(need) - margin) / gain
        if gain > 0:
            low = max(low, bound)
        else:
            high = min(high, bound)
    return (low, high) if low <= high else None


def holonomic_need(anchor_pad_m, direction, rule, pad_size_m, advance_max_mm: float, lateral_max_mm: float):
    """The smallest (advance_mm, lateral_mm) that brings the anchor to the rule on every edge, minimising
    advance + |lateral| over advances on the 1 mm grid from 0 to advance_max_mm and the exact lateral each advance then
    needs (the value of the admitting interval closest to 0), with |lateral| <= lateral_max_mm. None when no pair does.
    A pair with lateral 0 is what the forward-only rule would plan, so the proven starts come out unchanged."""
    best = None
    for advance in np.arange(0.0, float(advance_max_mm) + 0.5, 1.0):
        moved = (float(anchor_pad_m[0]) + float(direction[0]) * advance / 1000.0, float(anchor_pad_m[1]) + float(direction[1]) * advance / 1000.0)
        interval = lateral_interval_mm(moved, direction, rule, pad_size_m)
        if interval is None:
            continue
        low, high = interval
        lateral = 0.0 if low <= 0.0 <= high else (low if low > 0.0 else high)
        if abs(lateral) > float(lateral_max_mm) + 1e-9:
            continue
        cost = float(advance) + abs(lateral)
        if best is None or cost < best[0] - 1e-9:
            best = (cost, float(advance), round(float(lateral), 1))
    return None if best is None else (best[1], best[2])


def holonomic_limits(anchor_pad_m, direction, rule, pad_size_m, advance_max_mm: float, lateral_max_mm: float) -> dict:
    """Why holonomic_need answered None, in numbers: the advance the along edges need with the bound ignored, and the
    lateral the side edges need at that advance with its bound ignored, each against its bound."""
    margins = edge_margins_mm(anchor_pad_m, pad_size_m)
    along = 0.0
    for margin, need, e, side in zip(margins, rule, EDGE_NORMALS, _side_edges(direction)):
        rate = -(float(direction[0]) * e[0] + float(direction[1]) * e[1])
        if not side and rate > 1e-9:
            along = max(along, max(float(need) - margin, 0.0) / rate)
    moved = (float(anchor_pad_m[0]) + float(direction[0]) * along / 1000.0, float(anchor_pad_m[1]) + float(direction[1]) * along / 1000.0)
    interval = lateral_interval_mm(moved, direction, rule, pad_size_m, sides_only=True)  # the side edges' own shortfall: what a lateral is for
    lateral = None if interval is None else (0.0 if interval[0] <= 0.0 <= interval[1] else (interval[0] if interval[0] > 0.0 else interval[1]))
    return {"advance_needed_unbounded_mm": round(along, 1), "advance_max_mm": float(advance_max_mm), "advance_within_bound": along <= float(advance_max_mm) + 1e-9,
            "lateral_needed_unbounded_mm": None if lateral is None else round(lateral, 1), "lateral_max_mm": float(lateral_max_mm),
            "lateral_within_bound": lateral is not None and abs(lateral) <= float(lateral_max_mm) + 1e-9}
