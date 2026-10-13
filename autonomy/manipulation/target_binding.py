#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""target_binding: which cube a mission target IS, decided from measurements, and the two files that carry one mission
target between the runtime and the single-cube controller.

WHY IT EXISTS. The proven single-cube cycle carries "the" cube: SEARCH takes the one verified proposal the recognizer
ranked highest, and a cube whose centre is already inside the pad by its half-diagonal ends the run as
DONE_ALREADY_PLACED, a clean exit. A mission run must instead pick up a block that is NOT on the pad and be credited
only with the block it put there itself. This module is that association: pure functions over what one view measured,
plus the file schemas that carry the answer across the process boundary.

THE PAD POLICY (operator ruling 2026-09-29, docs/autonomous-demo/decision-log.md; it supersedes the two-cube claim rules
and the rulings (a) and (b) of 2026-09-28): "Human will only place blocks off the pad. Human can subtract blocks from
the pad. Robot should not care about the items on the pad after dropping them; the objective is to search outside the
pad for all blocks and place them within the pad." So nothing on the pad enters a decision except the blocks at THIS
run's own release, which PLACED credits whatever was there before (ruling (ii), THE RELEASE AGREEMENT below): a block on
the pad (a real one, or the pad's print read as one) is never a candidate, never occupied, never an earlier target to
keep undisturbed, and never weighed in a selection. THREE WAYS a block on the pad still fails a run
remain, all named residuals: THE LISTING BUDGET (below, at LISTING_ON_PAD_EXEMPT_PASSES), and two in the motion path
this module does not decide, which the ruling's change left
untouched: the SEARCH SWEEP (policy_search.search_sweep, under the word search_sweep) runs only when the frozen record of
the low-pose frame verifies no cube, and a block on the pad in that frame (the printed logo, an earlier block) is such a
cube, so the sweep does not run and SEARCH, listing only blocks on the pad, refuses mission_binding_no_candidate where the
sweep would have found the block off the pad; and ALIGN (run_single_cube_cycle Cycle._mission_one_cube_in_view) refuses
align_more_than_one_cube_verified on the recognizer's count of verified cubes in the pregrasp view, which includes blocks
on the pad. Both are pinned on the scripted World (autonomy/tests/test_pad_policy_20260929.py); whether a recorded scene
reaches either is inferred from recorded frames and geometry, never observed on a run.

THE PREDICATES, all in PAD_FRAME, which is fixed to the world (the base moves, the pad does not):
  placed(c)     ON THE PAD: the centre is at least the proven PLACED margin inside the pad. That margin is the
                controller's placed_margin_mm against PLACED_MIN_EDGE_MARGIN_MM, the predicate PLACED has always used.
                The threshold is positive, so a placed centre is also inside the outline, which is the other half of the
                proven PLACED;
  candidate(c)  OFF THE PAD: it has a PAD_FRAME centre and it is not placed. An EDGE block, inside the outline by less
                than the half-diagonal, is off the pad by that rule: it is not PLACED, so the robot picks it up and
                places it (and the arm-only block of the recorded scenes normally lies there);
  mode(c)       what placement_plan decided from THIS view's own SEARCH inputs: ARM_ONLY, BASE_ADVANCE, or None when no
                bounded placement exists. It is measured per block, never assumed from the declaration.

WHAT IS DECIDED WITH THEM:
  - bind_targets: the FIRST execution's SEARCH binds EVERY declared target. A target that needs no capability binds to
    the one ARM_ONLY candidate, a target that needs it to the one BASE_ADVANCE candidate. None, or more than one, is a
    refusal before anything is picked up. Two targets declared with the same mode cannot be told apart by any
    measurement here, so a mission declaring them is refused rather than bound by guesswork. An AUTO target (below)
    takes the NEAREST FEASIBLE candidate the earlier targets left (THE SELECTION RULE);
  - select_target: every SEARCH, the first one included, carries the single candidate within the match radius
    (Rules.r_match_mm) of the target's bound position, and only when that block's measured mode is the target's: the
    declared one, or for an auto target the one its binding measured (its EFFECTIVE mode). Nothing else is a fallback:
    a scene that shows only blocks on the pad is a refusal, never DONE_ALREADY_PLACED;
  - attribute_placement: PLACED holds when the EXIT view shows a block ON THE PAD within RELEASE_AGREEMENT_MM of THIS
    run's own release segment (THE RELEASE AGREEMENT), whatever was there before: what SEARCH listed on the pad is not
    consulted and no region of the pad (the printed logo's included) is excluded. Other blocks on the pad are ignored:
    no uniqueness among them, and a human taking any of them away never fails the run. No block on the pad agrees with
    the release: PLACED_NO_CUBE_AT_RELEASE, whatever else the view shows.

THE SELECTION RULE (an AUTO target, several blocks off the pad): among the candidates the earlier targets left, the
FEASIBLE ones are those with a bounded placement mode (ARM_ONLY or BASE_ADVANCE, measured as above); the one taken is the
feasible candidate NEAREST THE PAD, the largest signed placed margin (an edge block before one outside the outline, a
block 30 mm out before one 45 mm out), and among equal margins the one the listing names first (the recognizer's order).
No feasible candidate: REFUSED_CANDIDATE_NOT_PLACEABLE, naming the nearest candidate's limit when it carries an
explanation. The respawn that carries the capability is a fresh first execution and takes the same block again, since
the rule reads nothing but the view. RESIDUAL: the listing stops at cube_candidates.MAX_PASSES verified blocks per frame
and refuses a frame it did not run out of, so a view with more blocks than that is refused, not narrowed. Passes spent on
blocks already on the pad do not count, up to LISTING_ON_PAD_EXEMPT_PASSES (9) of them; a frame with more blocks on the
pad than that is refused as mission_candidates_incomplete (one of the THREE WAYS of THE PAD POLICY above, with the SEARCH
SWEEP and align_more_than_one_cube_verified). Many blocks
across the room are the next increment (docs/autonomous-demo/autonomous-single-cube-plan.md).

THE RELEASE AGREEMENT. The release segment is the one the RELEASE_ADMISSION takes its worst margin over
(run_single_cube_cycle.release_segment_pad): the admission's anchor moved by the FK travel, and by the travel over
FK_TRAVEL_OVERSTATEMENT, along the reach direction (for a sideways release offset, the offset's own point and direction,
and the tolerance grows by its uncertainty). RELEASE_AGREEMENT_MM is the largest distance RECORDED between that segment
and the block PLACED then read on the pad, 20.0 mm over the nine runs of RELEASE_AGREEMENT_RECORDED_MM, plus the anchor
uncertainty the admission itself charges (VISUAL_ANCHOR_UNCERTAINTY_MM, 6.0 mm): 26.0 mm. It is a chosen tolerance on
recorded evidence (nine runs, one pad, every one a BASE_ADVANCE release; no arm-only release recorded both numbers), not
a calibrated confidence interval. The block credited is the on-pad block nearest the segment within the tolerance,
whatever was there before (operator ruling (ii) of 2026-09-29, answering the open questions Q1 and Q2: "(ii) credit
any block on the pad near this run's release point, whatever was there before"). What this run's SEARCH listed on the
pad is not consulted, and no region of the pad is excluded, so removing blocks from the pad can never fail a run and
this run's block landing where a block stood before, or on the printed logo, is credited.
THE ACCEPTED RESIDUAL (accepted by the operator in ruling (ii)): a false PLACED when this run's block is not on the pad
at EXIT for any reason after OBJECT_SECURED, given an admitted release (the RELEASE_ADMISSION predicted a worst-case
centre margin of at least PLACE_REQUIRED_CENTRE_MM): it left the pad after the release, it was lost between
OBJECT_SECURED and the release, or it stayed in the opened jaws. The RELEASE_ADMISSION does not observe the held block:
it is computed from the SEARCH anchor, the measured base displacement and the FK travel, and the gripper reading at
RELEASED is recorded, not checked, so nothing between OBJECT_SECURED and the EXIT view tells these cases apart. In each
of them, when another block, or the pad's printed logo read as one (listed at pad (-61, -112) mm in 3 of the 5 SEARCH
views of 2026-09-28 by the offline listing; a release within about 22 mm of x -61 mm at the recorded depth puts it inside
the tolerance), stands within the tolerance of the release point, that block or the logo is credited as this run's.
Ruling (ii) credits any block on the pad near this run's release point and sets no condition on how this run's block
came to be missing, so all three cases are read as inside it. On the record, no recorded run has lost a block after an
admitted release, and the nine runs above did not lose theirs before it either: all nine read their block on the pad at
EXIT. That is recorded evidence, not a bound. Tests pin the residual (test_pad_policy_20260929.py). Two blocks within the
tolerance are not an ambiguity either: the nearest is credited.

THE RADII. Rules has NO default for either, and the mission target file carries both with their basis (THE CHOSEN RULE
below). r_match_mm decides which candidate IS the bound target at a later SEARCH (select_target: exactly one candidate
within it of the bound position, or a refusal); it enters no PLACED decision. r_claim_mm entered the claim predicates
the ruling of 2026-09-29 retired (claimed, occupied, the undisturbed earlier targets); it is still carried and checked
so that the target file keeps its version, and it enters no predicate.
  THE CHOSEN RULE (radii_from_cube_geometry, CUBE_HALF_EDGE_MM): half a cube edge, 12.7 mm, for BOTH radii, carried
  with its basis. It is a CHOSEN association rule, not a measured localization bound: two distinct blocks' centres are
  never closer than one edge (25.4 mm, touching), so no two candidates lie within 12.7 mm of one bound position, and
  every recorded re-reading of an unmoved block falls inside it (5.2 mm across the advanced and returned viewpoints,
  0.3 mm from the same viewpoint, 1.6 mm hem effect, base return residual at most 1.8 mm along the reach axis and 2.7 mm
  sideways). A selection the rule cannot settle is REFUSED, never guessed.

WHY THE PROVEN CONTROLLER IS NOT IMPORTED HERE. This module takes the proven functions' ANSWERS as inputs (a margin, a
mode, the PLACED threshold) instead of importing run_single_cube_cycle to compute them:
  - the controller runs as __main__. Importing it by name from a module it imports would execute a SECOND copy, with a
    second CycleFailed class that the running cycle's `except CycleFailed` does not catch;
  - the runtime daemon imports this module for the file schemas, and must not load numpy, OpenCV and the controller's
    sys.path edits with it (importing the controller pulls in cv2 and numpy; measured 2026-09-23, about 0.3 s);
  - the caller already holds those answers: SEARCH computes placed_margin_mm and placement_plan for its cube, and the
    same two calls on every enumerated cube are what the caller passes here.
The tests feed the REAL controller functions through these inputs, so what is pinned is the proven predicate itself.
Standard library only, no robot, no network, no file opened at import.

THE TWO FILES. KC_MISSION_TARGET names the MISSION TARGET file: written by the runtime into the run directory before the
spawn, read by the controller once, at start. With the variable ABSENT nothing here is consulted and the controller is
the legacy controller, byte for byte. The controller answers in the MISSION RESULT file at the path the target file
names, echoing mission_id, target_id and run_id; a result that does not echo all three belongs to some other run and is
refused. The runtime (from the checkout) and the child (from a pinned bundle) may hold different copies of this module,
so both sides check the schema name and version and refuse anything else (the result is version 3 since its placement
carries THE RELEASE AGREEMENT; see MISSION_RESULT_VERSION). Both loaders are strict: unknown keys are
refused, types are checked exactly (a boolean is not a number, a number is not a boolean), NaN and duplicate keys are
refused, and every path is absolute and normalized. A refusal names a reason code and a field, never a value: these
files hold paths, and a message is shown.

THE DECLARED CAPABILITY AND THE CARRIED ONE are two things. `requires_capability` is the target's IDENTITY: True is
the mode BASE_ADVANCE its cube must measure (the one out of reach), False the mode ARM_ONLY. The `capability` endpoints
are what THIS run carries. The runtime runs a first-execution capability target WITHOUT them, because nothing has
measured its cube yet; its SEARCH then measures BASE_ADVANCE and refuses mission_capability_required before anything is
picked up (run_single_cube_cycle.py), and the runtime decides from that measurement. So the file accepts a target
declared as needing the capability and run without it, and refuses the reverse: a run never carries a capability its
target does not declare.

THE AUTO TARGET (requires_capability "auto", the operator's ruling of 2026-09-26: one block, from anywhere, the robot
decides): the target declares no mode. Its candidates are every block that is off the pad and not bound by a target
declared before it (ONE CUBE, ONE TARGET, in bind_targets); none is REFUSED_NO_CANDIDATE, and among several it takes the
nearest feasible one (THE SELECTION RULE; REFUSED_CANDIDATE_NOT_PLACEABLE when none has a bounded placement). The mode
the binding MEASURED is the target's EFFECTIVE
mode: the binding records it, the target file carries it (`effective_mode`, None until bound, and never on a declared
target), and every later SEARCH of that target selects in it exactly as a declared target selects in its declaration.
A measured ARM_ONLY runs the proven arm-only cycle; a measured BASE_ADVANCE in a run that carries no capability is the
same SEARCH refusal as a declared capability target's, and the respawn that carries the capability is, like every
respawn, a fresh first execution that binds again. An auto run may therefore carry the capability or not. The claim on
PLACED follows the mode the run measured (result_claims_placement): a BASE_ADVANCE run is placed only through the
capability's own applied output, an ARM_ONLY run needs none.

Nothing here is a velocity, a pose or anything else a robot could replay. Software coverage is not physical
qualification.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

#: The variable that names the mission target file. Absent: the legacy controller, unchanged. Present: the controller
#: runs for exactly that target or refuses before any motion (an empty or relative value is a refusal, not an absence).
MISSION_TARGET_ENV = "KC_MISSION_TARGET"

#: placement_plan's own two words, which is why they are spelled exactly as it returns them (a test pins that).
MODE_ARM_ONLY, MODE_BASE_ADVANCE = "ARM_ONLY", "BASE_ADVANCE"
MODES = (MODE_ARM_ONLY, MODE_BASE_ADVANCE)
#: The third value of requires_capability (THE AUTO TARGET): the target takes whichever mode its own SEARCH measures. It
#: is the declared "mode" of such a target until its binding measured one, and it is never a measured mode (not in MODES).
#: demo_runtime.mission.REQUIRES_CAPABILITY_AUTO spells the same word for the runtime's records (a test keeps them equal).
AUTO = "auto"
REQUIRES_CAPABILITY_VALUES = (True, False, AUTO)

#: The bound on a radius the file accepts: a typo guard (a metre), never a qualified value. The same bound as
#: demo_runtime.config.RADIUS_MAX_MM (a test keeps the two equal).
RADIUS_MAX_MM = 1000.0
#: Half a cube edge: run_single_cube_cycle.CUBE_EDGE_MM (25.4 mm, measured) over two, spelled here because this module
#: never imports the controller (a test keeps the two equal). The one distance the cubes' own geometry fixes for the
#: two radii (THE TWO RADII, THE DERIVED VALUE).
CUBE_HALF_EDGE_MM = 12.7
#: THE RELEASE AGREEMENT (module docstring): the recorded distance, in mm, from each run's release segment
#: (run_single_cube_cycle.release_segment_pad of its RELEASE_ADMISSION anchor and FK travel along its PLACEMENT_PLAN reach
#: direction) to the block its PLACED then read on the pad, for every recorded run that states both. The first eight are
#: the runs' own PLACED cube_pad_m; the ninth (the live case of 2026-09-28, whose PLACED carries no position) is the
#: offline reproduction of its EXIT view on the console's rendition of the frame (autonomy/tests/test_pad_policy_20260929.py).
RELEASE_AGREEMENT_RECORDED_MM = (
    ("cycle-run-20260920T115018Z", 5.2), ("cycle-run-20260920T120226Z", 4.5), ("cycle-run-20260920T121517Z", 20.0),
    ("cycle-run-20260920T133924Z", 5.3), ("console-20260922T083804Z-zxkydauq", 10.5), ("console-20260922T180557Z-5xeetlmj", 6.2),
    ("console-20260926T201540Z-mb3c1048", 2.3), ("console-20260926T203744Z-m2476137", 2.6), ("console-20260928T174207Z-m915d3e9", 12.3),
)
#: The anchor uncertainty the RELEASE_ADMISSION charges: run_single_cube_cycle.VISUAL_ANCHOR_UNCERTAINTY_MM, spelled here
#: because this module never imports the controller (a test keeps the two equal).
RELEASE_AGREEMENT_ANCHOR_UNCERTAINTY_MM = 6.0
#: The worst recorded release error plus the admission's anchor uncertainty: 20.0 + 6.0 = 26.0 mm. A chosen tolerance on
#: nine recorded runs, not a calibrated confidence interval (THE RELEASE AGREEMENT, with its named residuals).
RELEASE_AGREEMENT_MM = max(value for _run, value in RELEASE_AGREEMENT_RECORDED_MM) + RELEASE_AGREEMENT_ANCHOR_UNCERTAINTY_MM
#: The basis the operator states for the radii names WHERE their qualification is written down. The same alphabet as
#: demo_runtime.config's mission.radii.basis (a test keeps the two equal): plain text, never a path or an address.
RADII_BASIS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:#()-]{0,159}")
_DOTTED_QUAD = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")  # demo_runtime.config's, spelled here

# ---- refusal and attribution reason codes (lower case, contract reason_code shape) --------------------------------
REFUSED_TARGETS_SHARE_A_MODE = "mission_targets_share_a_mode"
REFUSED_NO_CANDIDATE = "mission_binding_no_candidate"
REFUSED_CANDIDATE_NOT_UNIQUE = "mission_binding_candidate_not_unique"
REFUSED_CANDIDATE_NOT_PLACEABLE = "mission_binding_candidate_not_placeable"
REFUSED_TARGET_NOT_FOUND = "mission_target_not_found"
REFUSED_TARGET_NOT_UNIQUE = "mission_target_not_unique"
REFUSED_MODE_MISMATCH = "mission_target_mode_mismatch"
#: THE RELEASE AGREEMENT's one refusal: no block on the pad lies within the tolerance of this run's release segment (none on
#: the pad at all, or only ones farther away). It replaced the three codes of the retired claim rules (the ruling of
#: 2026-09-29): mission_placed_no_unclaimed_cube, mission_placed_more_than_one_unclaimed_cube and
#: mission_earlier_target_disturbed, which nothing writes any more.
PLACED_NO_CUBE_AT_RELEASE = "mission_placed_no_cube_at_release_point"


class TargetRefused(Exception):
    """A binding or a selection that does not hold. `reason_code` is what the controller's failed() records, `values`
    are the named scalars that decided it (numbers named with their unit suffix, and when the refusal is about one cube
    with no bounded placement that carries an explanation, the explanation's values and texts: constraint, deficit_mm,
    hint, explanation), and the message is the sentence an operator reads."""

    def __init__(self, reason_code: str, message: str, **values):
        super().__init__(message)
        self.reason_code = reason_code
        self.values = values


# =====================================================================================================================
# What one view measured, and the four predicates.
# =====================================================================================================================
def _explanation_pairs(explanation) -> tuple:
    """A placement explanation as sorted (name, value) pairs of scalars (text, number, bool or None); anything else is a
    ValueError, so a SeenCube stays hashable and a refusal's values stay scalars. The texts are kept as given: that they fit
    a run record's named text value is placement_envelope's to keep (its module docstring, THE TEXT), not this check's."""
    items = explanation.items() if isinstance(explanation, Mapping) else explanation
    pairs = []
    for name, value in items:
        if not isinstance(name, str) or not (value is None or isinstance(value, (str, bool, int, float))):
            raise ValueError("a placement explanation is named scalars only")
        pairs.append((name, value))
    return tuple(sorted(pairs))


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pair(value) -> Optional[Tuple[float, float]]:
    """(x, y) as two finite floats, or None when `value` is not exactly two finite numbers."""
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(_finite(v) for v in value):
        return None
    return (float(value[0]), float(value[1]))


@dataclass(frozen=True)
class SeenCube:
    """ONE verified cube of one view, as the caller measured it with the proven controller's own functions:
      center_pad_m  cube_by_template's cube_center_pad_m, or None when the view gave the cube no PAD_FRAME position;
      margin_mm     placed_margin_mm(center_pad_m), signed (negative outside the pad); None exactly when the centre is;
      mode          placement_plan(center, reach direction, grasp radius)["mode"] from this view's SEARCH inputs, None
                    when placement_plan refused (no bounded placement) or when the view is not a SEARCH view.
      explanation   why a SEARCH view's cube has no bounded placement, when the caller knows it: the scalar values of
                    policy_placement.classify_for_binding's answer (constraint, deficit_mm, hint, the side and along
                    numbers), kept as sorted (name, value) pairs; None otherwise. Only a cube with a centre and mode None
                    carries one, and the not-placeable refusal carries it beside its counts.
    A malformed reading is a programming error and raises ValueError: it is never read as "no cube"."""

    center_pad_m: Optional[Tuple[float, float]]
    margin_mm: Optional[float] = None
    mode: Optional[str] = None
    explanation: Optional[tuple] = None

    def __post_init__(self):
        if self.explanation is not None:
            if self.center_pad_m is None or self.mode is not None:
                raise ValueError("only a cube with a PAD_FRAME centre and no placement mode carries a placement explanation")
            object.__setattr__(self, "explanation", _explanation_pairs(self.explanation))
        if self.center_pad_m is None:
            if self.margin_mm is not None or self.mode is not None:
                raise ValueError("a cube without a PAD_FRAME centre has neither a margin nor a mode")
            return
        centre = _pair(self.center_pad_m)
        if centre is None:
            raise ValueError("center_pad_m must be two finite numbers")
        if not _finite(self.margin_mm):
            raise ValueError("margin_mm must be a finite number when the centre is known")
        if self.mode is not None and self.mode not in MODES:
            raise ValueError("mode must be ARM_ONLY, BASE_ADVANCE or None")
        object.__setattr__(self, "center_pad_m", centre)
        object.__setattr__(self, "margin_mm", float(self.margin_mm))


@dataclass(frozen=True)
class Rules:
    """The three distances the file and the predicates carry, and NONE has a default. `placed_min_margin_mm` is the
    controller's PLACED_MIN_EDGE_MARGIN_MM and is passed from there, so there is one value of it, not two. The two radii
    come from the mission target file (THE RADII, in the module docstring): a value nobody justified is never the value a
    mission runs with because it was written here first. r_claim_mm is carried and checked and enters no predicate since
    the ruling of 2026-09-29."""

    placed_min_margin_mm: float
    r_claim_mm: float
    r_match_mm: float

    def __post_init__(self):
        for name in ("placed_min_margin_mm", "r_claim_mm", "r_match_mm"):
            value = getattr(self, name)
            if not _finite(value) or value <= 0.0:
                # A positive PLACED threshold is also what makes a placed centre lie inside the pad outline.
                raise ValueError(f"{name} must be a positive finite number")


def declared_mode(requires_capability) -> str:
    """The mode a declared target must MEASURE. A target that needs the capability is the one out of reach; the one that
    does not is carried by the arm alone, and one that measures BASE_ADVANCE is refused rather than driven (D5). An
    AUTO target declares none: its word is AUTO until its binding measured one (DeclaredTarget.mode)."""
    if requires_capability == AUTO:
        return AUTO
    return MODE_BASE_ADVANCE if requires_capability else MODE_ARM_ONLY


def distance_mm(a, b) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1])) * 1000.0


def is_placed(cube: SeenCube, rules: Rules) -> bool:
    return cube.center_pad_m is not None and cube.margin_mm >= rules.placed_min_margin_mm


def is_candidate(cube: SeenCube, rules: Rules) -> bool:
    """OFF THE PAD (THE PREDICATES): a PAD_FRAME centre that is not placed. Nothing on the pad is ever a candidate."""
    return cube.center_pad_m is not None and not is_placed(cube, rules)


def _segment_distance_mm(point, ends) -> float:
    """The distance, in mm, from `point` to the segment between the two `ends` (all PAD_FRAME metres)."""
    (ax, ay), (bx, by) = ends
    vx, vy = bx - ax, by - ay
    length = vx * vx + vy * vy
    u = 0.0 if length == 0.0 else max(0.0, min(1.0, ((float(point[0]) - ax) * vx + (float(point[1]) - ay) * vy) / length))
    return math.hypot(float(point[0]) - ax - u * vx, float(point[1]) - ay - u * vy) * 1000.0


# =====================================================================================================================
# Binding (first execution), selection (every SEARCH), attribution (PLACED).
# =====================================================================================================================
@dataclass(frozen=True)
class DeclaredTarget:
    """One target as the mission declared it, with what earlier executions established: where it was BOUND (every target
    after the first execution's SEARCH), where it was PLACED (every target before the current one) and, for an AUTO
    target once bound, the mode its binding MEASURED (`effective_mode`; None for a declared target, always)."""

    target_id: str
    requires_capability: object  # True, False or AUTO (REQUIRES_CAPABILITY_VALUES)
    bound_pad_m: Optional[Tuple[float, float]] = None
    placed_pad_m: Optional[Tuple[float, float]] = None
    effective_mode: Optional[str] = None

    @property
    def auto(self) -> bool:
        return self.requires_capability == AUTO

    @property
    def mode(self) -> str:
        """The mode this target's cube must measure: the declaration, or for an auto target the effective mode once
        bound and AUTO before that."""
        if self.auto and self.effective_mode is not None:
            return self.effective_mode
        return declared_mode(self.requires_capability)


@dataclass(frozen=True)
class TargetBinding:
    target_id: str
    bound_pad_m: Tuple[float, float]
    mode: str
    index: int  # into the cubes of the binding view


@dataclass(frozen=True)
class Selection:
    index: int  # into the cubes of this SEARCH view
    seen_pad_m: Tuple[float, float]
    bound_pad_m: Tuple[float, float]
    distance_mm: float
    mode: str


@dataclass(frozen=True)
class Placement:
    """THE RELEASE AGREEMENT's answer for one EXIT view. `on_pad_count` counts every block on the pad in that view (they
    decide nothing but are stated); `release_distance_mm` is the nearest on-pad block's distance to the release segment
    (None when no block is on the pad, or when nothing could be attributed); `release_tolerance_mm` is what it was held to
    (None when nothing could be attributed)."""

    placed: bool
    placed_index: Optional[int]
    placed_pad_m: Optional[Tuple[float, float]]
    on_pad_count: int
    release_distance_mm: Optional[float]
    release_tolerance_mm: Optional[float]
    reason_code: Optional[str]  # None exactly when placed


def _explained(cube: SeenCube) -> tuple:
    """A not-placeable cube's explanation (SeenCube.explanation) as a refusal carries it: (the clause the message gains, the
    values). With it the refusal names the binding limit, its deficit and the operator's hint; without one the clause is
    empty and the values are none, so the refusal is the sentence and the counts it always was. The controller's own
    sentence is not a value: the explanation's sentence replaces it."""
    explained = dict(cube.explanation or ())
    why = f": {explained['explanation']}; {explained['hint']}" if explained.get("explanation") and explained.get("hint") else ""
    explained.pop("sentence", None)
    return why, explained


def _nearest_feasible(target: DeclaredTarget, candidates: Sequence, bound_earlier: int, cubes_seen: int, placed_cubes: int) -> tuple:
    """THE SELECTION RULE for an AUTO target: (index, block) of the feasible candidate nearest the pad (the largest signed
    placed margin), the listing order among equal margins, or TargetRefused: none off the pad, or none feasible."""
    counts = dict(candidates=len(candidates), bound_earlier=bound_earlier, cubes_seen=cubes_seen, placed_cubes=placed_cubes)
    if not candidates:
        raise TargetRefused(REFUSED_NO_CANDIDATE, f"BINDING: target {target.target_id} needs a block off the pad and this SEARCH measured none "
                            "(every block it saw is on the pad, or it saw none); nothing was picked up", matching_candidates=0, **counts)
    feasible = [(index, cube) for index, cube in candidates if cube.mode is not None]
    if not feasible:
        nearest = max(candidates, key=lambda entry: (entry[1].margin_mm, -entry[0]))[1]
        why, explained = _explained(nearest)
        if len(candidates) == 1:  # the sentence and the values the one-block refusal always had
            raise TargetRefused(REFUSED_CANDIDATE_NOT_PLACEABLE, f"BINDING: the one cube for target {target.target_id} has no bounded placement from this SEARCH "
                                f"(neither the arm alone nor the bounded base advance reaches the pad from it){why}; nothing was picked up", **explained, **counts)
        raise TargetRefused(REFUSED_CANDIDATE_NOT_PLACEABLE, f"BINDING: none of the {len(candidates)} blocks off the pad has a bounded placement from this SEARCH for "
                            f"target {target.target_id} (neither the arm alone nor the bounded base advance reaches the pad from any of them; the one nearest the "
                            f"pad{why or ' carries no explanation'}); nothing was picked up", **explained, feasible_candidates=0, **counts)
    return max(feasible, key=lambda entry: (entry[1].margin_mm, -entry[0]))


def bind_targets(targets: Sequence[DeclaredTarget], cubes: Sequence[SeenCube], rules: Rules) -> Tuple[TargetBinding, ...]:
    """The first execution's SEARCH binds every declared target, in declaration order, or refuses before PREGRASP.

    A block on the pad is nobody's candidate (THE PAD POLICY). ONE CUBE, ONE TARGET: a block an earlier target (in
    declaration order) bound is no later target's candidate, so two bindings never record one position, whatever the
    targets declare. For a DECLARED target, a candidate whose measured mode no target declares, or that has no bounded
    placement, binds to nothing and is left where it lies. An AUTO target takes the nearest feasible candidate the earlier
    targets left (THE SELECTION RULE, in the module docstring; an auto target declared first therefore chooses among
    every block off the pad), and the binding records the mode it MEASURED (THE AUTO TARGET)."""
    modes = [target.mode for target in targets]
    if len(set(modes)) != len(modes):
        raise TargetRefused(REFUSED_TARGETS_SHARE_A_MODE, "BINDING: two declared targets share a placement mode, so no measurement tells their cubes apart; "
                            "nothing was picked up", declared_targets=len(targets))
    candidates = [(index, cube) for index, cube in enumerate(cubes) if is_candidate(cube, rules)]
    placed_cubes = sum(1 for cube in cubes if is_placed(cube, rules))
    bindings = []
    for target, mode in zip(targets, modes):
        # `candidates` is what the earlier targets left: a bound cube is taken out below, for every kind of target.
        if mode == AUTO:
            index, cube = _nearest_feasible(target, candidates, len(bindings), len(cubes), placed_cubes)
            bindings.append(TargetBinding(target.target_id, cube.center_pad_m, cube.mode, index))
            candidates = [entry for entry in candidates if entry[0] != index]
            continue
        matching = [(index, cube) for index, cube in candidates if cube.mode == mode]
        if len(matching) != 1:
            code = REFUSED_NO_CANDIDATE if not matching else REFUSED_CANDIDATE_NOT_UNIQUE
            wanted = f"exactly one {mode} candidate"
            # A declared target that found no cube of its mode when the one candidate left has no bounded placement: that
            # cube is the one the refusal is about, so it names that cube's limit and shortfall beside the counts.
            lone = candidates[0][1] if len(candidates) == 1 else None
            why, explained = _explained(lone) if not matching and lone is not None and lone.mode is None else ("", {})
            about = f"; the one cube left has no bounded placement{why}" if why else ""
            raise TargetRefused(code, f"BINDING: target {target.target_id} needs {wanted} and this SEARCH measured {len(matching)}{about}; nothing was picked up",
                                **explained, matching_candidates=len(matching), candidates=len(candidates), bound_earlier=len(bindings), cubes_seen=len(cubes),
                                placed_cubes=placed_cubes)
        index, cube = matching[0]
        bindings.append(TargetBinding(target.target_id, cube.center_pad_m, cube.mode, index))
        candidates = [entry for entry in candidates if entry[0] != index]
    return tuple(bindings)


def select_target(bound_pad_m, wanted_mode: str, cubes: Sequence[SeenCube], rules: Rules) -> Selection:
    """The ONE candidate within the match radius of the bound position, and only when its measured mode is `wanted_mode`:
    the declared mode of a declared target, the effective (measured at binding) mode of an auto target.

    Strict on purpose: the cube is found by where it was bound first, and then the mode must agree. A second candidate
    inside the radius is ambiguity, not a choice, even when only one of the two has the right mode."""
    bound = _pair(bound_pad_m)
    if bound is None or wanted_mode not in MODES:
        raise ValueError("select_target needs a bound position and a measured mode (ARM_ONLY or BASE_ADVANCE)")
    candidates = [(index, cube, distance_mm(cube.center_pad_m, bound)) for index, cube in enumerate(cubes) if is_candidate(cube, rules)]
    near = [entry for entry in candidates if entry[2] <= rules.r_match_mm]
    nearest = min((entry[2] for entry in candidates), default=None)
    if len(near) != 1:
        code = REFUSED_TARGET_NOT_FOUND if not near else REFUSED_TARGET_NOT_UNIQUE
        raise TargetRefused(code, f"SEARCH: {len(near)} candidate cubes lie within {rules.r_match_mm:.0f} mm of where this target was bound, and exactly one is needed; "
                            "nothing was picked up", within_r_match=len(near), candidates=len(candidates), cubes_seen=len(cubes),
                            placed_cubes=sum(1 for cube in cubes if is_placed(cube, rules)), nearest_candidate_mm=nearest, r_match_mm=rules.r_match_mm)
    index, cube, distance = near[0]
    if cube.mode != wanted_mode:
        raise TargetRefused(REFUSED_MODE_MISMATCH, f"SEARCH: the bound cube measures {cube.mode or 'no bounded placement'} and the target's mode is {wanted_mode}; nothing was picked up",
                            distance_to_binding_mm=distance, r_match_mm=rules.r_match_mm)
    return Selection(index, cube.center_pad_m, bound, distance, cube.mode)


def _release_ends(release) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    ends = tuple(_pair(point) for point in release) if isinstance(release, (list, tuple)) and len(release) == 2 else None
    if ends is None or any(point is None for point in ends):
        raise ValueError("the release is the two PAD_FRAME ends of this run's release segment, each two finite numbers")
    return ends


def attribute_placement(cubes: Sequence[SeenCube], release, rules: Rules, release_uncertainty_mm: float = 0.0) -> Placement:
    """PLACED for THIS run, from the view the controller judges PLACED on (its EXIT view): THE RELEASE AGREEMENT. The block
    credited is the block ON THE PAD nearest this run's release segment (`release`, run_single_cube_cycle.release_segment_pad
    of its RELEASE_ADMISSION), and only within RELEASE_AGREEMENT_MM plus `release_uncertainty_mm` (a sideways release
    offset's uncertainty, 0 otherwise), whatever was there before (operator ruling (ii) of 2026-09-29; THE ACCEPTED
    RESIDUAL in the module docstring). Every other block on the pad is ignored. `release_distance_mm` is the credited
    block's distance, or on a refusal the nearest on-pad block's. ValueError for a malformed release."""
    ends = _release_ends(release)
    if not _finite(release_uncertainty_mm) or release_uncertainty_mm < 0.0:
        raise ValueError("release_uncertainty_mm must be a finite number, 0 or more")
    tolerance = RELEASE_AGREEMENT_MM + float(release_uncertainty_mm)
    on_pad = [(_segment_distance_mm(cube.center_pad_m, ends), index, cube) for index, cube in enumerate(cubes) if is_placed(cube, rules)]
    if not on_pad:
        return Placement(False, None, None, 0, None, tolerance, PLACED_NO_CUBE_AT_RELEASE)
    distance, index, cube = min(on_pad, key=lambda entry: (entry[0], entry[1]))
    if distance > tolerance:
        return Placement(False, None, None, len(on_pad), distance, tolerance, PLACED_NO_CUBE_AT_RELEASE)
    return Placement(True, index, cube.center_pad_m, len(on_pad), distance, tolerance, None)


def select_for_execution(target: "MissionTarget", cubes: Sequence[SeenCube], rules: Rules):
    """What one SEARCH decides for the target this run carries: (the bindings, or None when this is not the first
    execution, and the Selection). On a first execution the selection is held to the binding just made, in the mode it
    measured (for a declared target that is its declaration); later, to the carried binding in the target's mode, which
    for an auto target is the effective mode the file carries. Refuses with TargetRefused; nothing in it moves anything."""
    bindings = None
    bound, wanted = target.bound_pad_m, target.mode
    if target.first_execution:
        bindings = bind_targets(target.targets, cubes, rules)
        bound, wanted = bindings[target.index].bound_pad_m, bindings[target.index].mode
    return bindings, select_target(bound, wanted, cubes, rules)


# =====================================================================================================================
# The mission target file (runtime -> controller) and the mission result file (controller -> runtime).
# =====================================================================================================================
#: The target file is version 3: requires_capability takes the third value AUTO and every declared target carries
#: `effective_mode` (THE AUTO TARGET). Version 2 carried the radii (THE TWO RADII) and a run declared as needing the
#: capability without carrying it (THE DECLARED CAPABILITY AND THE CARRIED ONE). Both sides refuse any other version: a
#: version 2 file is not read as an auto-less one, and the child bundle is built from the same release as the runtime.
MISSION_TARGET_SCHEMA, MISSION_TARGET_VERSION = "kc.mission_target", 3
#: The result file is version 3: its placement carries THE RELEASE AGREEMENT (on_pad_count, release_distance_mm,
#: release_tolerance_mm) in place of the retired claim rules' unclaimed_placed_count and earlier_targets_undisturbed (the
#: ruling of 2026-09-29). Version 2 added the capability evidence's `applied` (commands the motion gateway APPLIED) beside
#: `accepted` (commands the relay accepted and submitted); a placement is credited to a capability target on applied.
#: Both loaders refuse an unknown key and a missing one, so no version is readable as another: a version 2 result is
#: refused as result_version_not_known on this side, and a version 3 result on a version 2 side.
MISSION_RESULT_SCHEMA, MISSION_RESULT_VERSION = "kc.mission_result", 3
STAGE_BOUND, STAGE_FINISHED = "bound", "finished"  # bound: written after SEARCH selected, before the first motion
#: event.schema.json's run_outcome, the words the controller's _terminal_outcome returns (a test pins both).
OUTCOMES = ("placed_new", "already_placed_noop", "stopped", "refused", "failed", "unknown")
PLACEMENT_PREDICATES = ("object_secured", "placed", "base_returned")  # the controller's _PLACEMENT_PREDICATES

#: The shapes. A mission id is what demo_runtime.mission.new_mission_id mints; a target id is mission_service's
#: allowlist; a run id and an observation id are one ObservationStore path component (the run id IS the store session).
MISSION_ID_RE = re.compile(r"mission-[0-9a-f]{12}")
TARGET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
OBSERVATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
STATE_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
REASON_CODE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_HEX32, _HEX64 = re.compile(r"[0-9a-f]{32}"), re.compile(r"[0-9a-f]{64}")
MAX_TARGETS = 8  # mission_service._MAX_TARGETS
#: THE LISTING BUDGET (verify r1 M2 of the pad policy): the cube listing stops at cube_candidates.MAX_PASSES verified blocks
#: and a list with proposals left is refused (mission_candidates_incomplete). A pass spent on a block ALREADY ON THE PAD
#: does not count toward that budget, up to this many: every block a mission can have placed (MAX_TARGETS) plus the pad's
#: printed logo read as one. A frame with more blocks on the pad than that is still refused under the same code (a named
#: residual); blocks off the pad spend the budget as before.
LISTING_ON_PAD_EXEMPT_PASSES = MAX_TARGETS + 1
MAX_FILE_BYTES = 64 * 1024
MAX_PATH_CHARS = 1024
MAX_PAD_COORD_M = 2.0  # no cube this far from the pad centre is in the low-pose view; a larger number is not a reading
MAX_DROP_REASONS = 32
PID_MAX = 4_194_304  # cake_capability_adapter.PID_MAX_LIMIT
_IPC = "ipc://"

_TARGET_FILE_KEYS = ("schema", "v", "mission_id", "target_id", "run_id", "requires_capability", "targets", "capability", "radii", "result_path")
_RADII_KEYS = ("r_claim_mm", "r_match_mm", "basis")
_DECLARED_KEYS = ("target_id", "requires_capability", "effective_mode", "bound_pad_m", "placed_pad_m")
_CAPABILITY_KEYS = ("admin_probe", "admin_socket", "slot_uuid", "capsule", "observation_endpoint", "command_endpoint")
_RESULT_KEYS = ("schema", "v", "mission_id", "target_id", "run_id", "stage", "bindings", "selection", "outcome", "final_state", "reason_code",
                "predicates", "placement", "capability")
_BINDING_KEYS = ("target_id", "bound_pad_m", "mode", "observation_id")
_SELECTION_KEYS = ("observation_id", "seen_pad_m", "bound_pad_m", "distance_mm", "mode")
_PLACEMENT_KEYS = ("placed", "placed_pad_m", "on_pad_count", "release_distance_mm", "release_tolerance_mm", "reason_code", "observation_id")
_EVIDENCE_KEYS = ("generation", "pid", "accepted", "applied", "dropped")


class MissionFileRefused(ValueError):
    """A mission target or mission result file that is not exactly what the schema says. The message is the reason code
    and the field; it never echoes a value."""

    def __init__(self, reason_code: str, field: Optional[str] = None):
        super().__init__(reason_code if field is None else f"{reason_code}: {field}")
        self.reason_code = reason_code
        self.field = field


@dataclass(frozen=True)
class CapabilityEndpoints:
    """Where the capability target's relay finds the Cake-managed capability. Only a run of a target that requires the
    capability carries these (and a first execution of one does not: THE DECLARED CAPABILITY AND THE CARRIED ONE), and
    only the relay reads them: the controller itself never builds a forward command."""

    admin_probe: str
    admin_socket: str
    slot_uuid: str
    capsule: str
    observation_endpoint: str
    command_endpoint: str


@dataclass(frozen=True)
class Radii:
    """The claim and match radii a mission runs with, and the basis stated for them (THE TWO RADII): the operator's own
    text for configured numbers, or CUBE_HALF_EDGE_BASIS for the derived pair."""

    r_claim_mm: float
    r_match_mm: float
    basis: str

    def to_document(self) -> dict:
        return {"r_claim_mm": self.r_claim_mm, "r_match_mm": self.r_match_mm, "basis": self.basis}


#: The basis that travels with the derived radii, in the mission target file's basis alphabet (RADII_BASIS_RE: at most 160
#: characters, no comma, no slash), so it says what the value IS and where it comes from. The evidence it rests on is
#: CUBE_HALF_EDGE_EVIDENCE, which is not on the wire.
CUBE_HALF_EDGE_BASIS = ("chosen rule half a cube edge (25.4 over 2) not a measured bound: cube "
                        "centres never closer than one edge and retained re-reads within 5.2 mm")
#: What was measured, in the order THE TWO RADII gives it. Recorded evidence, not a calibrated confidence interval; a test
#: checks each figure against the predicates with the value.
CUBE_HALF_EDGE_EVIDENCE = (
    "two distinct cubes' centres are never closer than one edge, 25.4 mm (CUBE_EDGE_MM), when touching: no recorded position can be "
    "claimed by two cubes, and a distinct cube staged against a placed one reads outside 12.7 mm of the placed one's position",
    "every recorded re-reading of an unmoved cube falls inside 12.7 mm: 5.2 mm across the advanced and returned viewpoints, 0.3 mm from "
    "the same viewpoint, 1.6 mm hem effect, base return residual at most 1.8 mm along the reach axis and 2.7 mm sideways",
    "in the two-cube scene a placed cube is at least 17.96 mm inside the pad (PLACED_MIN_EDGE_MARGIN_MM) and a BASE_ADVANCE cube's "
    "recorded starts were 37 to 45 mm outside it: about 55 mm apart (17.96 + 37 = 54.96 at the nearest), more than four radii (50.8 mm), "
    "so mode and radius leave no ambiguity",
)


def radii_from_cube_geometry() -> Radii:
    """The chosen radii (THE TWO RADII, THE CHOSEN RULE): half a cube edge for both, with CUBE_HALF_EDGE_BASIS as the
    basis. demo_runtime.config resolves the configuration word "cube_half_edge" through this function, so the numbers
    and the basis have one spelling. A pure constructor: nothing is read and nothing is measured here."""
    return Radii(r_claim_mm=CUBE_HALF_EDGE_MM, r_match_mm=CUBE_HALF_EDGE_MM, basis=CUBE_HALF_EDGE_BASIS)


@dataclass(frozen=True)
class MissionTarget:
    mission_id: str
    target_id: str
    run_id: str
    requires_capability: object  # DECLARED: True, False or AUTO (REQUIRES_CAPABILITY_VALUES)
    targets: Tuple[DeclaredTarget, ...]
    capability: Optional[CapabilityEndpoints]  # CARRIED: None when this run carries no capability
    result_path: str
    radii: Optional[Radii] = None  # None: the radii are not qualified, and the controller refuses the file

    @property
    def carries_capability(self) -> bool:
        return self.capability is not None

    def rules(self, placed_min_margin_mm: float) -> Rules:
        """The predicates' distances for this target: the controller's PLACED margin and this file's radii. ValueError when
        the file carries no radii: an unqualified seam is refused, never filled in."""
        if self.radii is None:
            raise ValueError("the mission target carries no radii")
        return Rules(placed_min_margin_mm=placed_min_margin_mm, r_claim_mm=self.radii.r_claim_mm, r_match_mm=self.radii.r_match_mm)

    @property
    def index(self) -> int:
        return [target.target_id for target in self.targets].index(self.target_id)

    @property
    def first_execution(self) -> bool:
        return self.index == 0

    @property
    def auto(self) -> bool:
        return self.requires_capability == AUTO

    @property
    def mode(self) -> str:
        """The declared mode, or for an auto target the effective one the file carries (AUTO on a first execution, whose
        own SEARCH measures it: run_mode is the word for what that run drives)."""
        return self.targets[self.index].mode

    def run_mode(self, selection: Selection) -> str:
        """The placement mode THIS run drives, always one of MODES: a declared target's declaration, or for an auto target
        the mode its binding measured, which select_target held the selection to (Selection.mode)."""
        return selection.mode if self.auto else self.mode

    @property
    def bound_pad_m(self) -> Optional[Tuple[float, float]]:
        return self.targets[self.index].bound_pad_m

    @property
    def earlier_placed(self) -> Tuple[Tuple[float, float], ...]:
        """Where the targets before this one were placed, as the file carries them (the mission record's positions). Since
        the ruling of 2026-09-29 no predicate reads them: a block on the pad is never looked at again."""
        return tuple(target.placed_pad_m for target in self.targets[:self.index])

    def to_document(self) -> dict:
        def point(value):
            return None if value is None else [value[0], value[1]]

        return {"schema": MISSION_TARGET_SCHEMA, "v": MISSION_TARGET_VERSION, "mission_id": self.mission_id, "target_id": self.target_id,
                "run_id": self.run_id, "requires_capability": self.requires_capability,
                "targets": [{"target_id": t.target_id, "requires_capability": t.requires_capability, "effective_mode": t.effective_mode,
                             "bound_pad_m": point(t.bound_pad_m), "placed_pad_m": point(t.placed_pad_m)} for t in self.targets],
                "capability": None if self.capability is None else {name: getattr(self.capability, name) for name in _CAPABILITY_KEYS},
                "radii": None if self.radii is None else self.radii.to_document(), "result_path": self.result_path}


# ---- field checks -------------------------------------------------------------------------------------------------
def _refuse(code: str, field: Optional[str] = None):
    raise MissionFileRefused(code, field)


def _object(value, keys: Sequence[str], code: str, field: str) -> dict:
    if not isinstance(value, dict):
        _refuse(f"{code}_not_an_object", field)
    if set(value) - set(keys):
        _refuse(f"{code}_unknown_key", field)
    if set(keys) - set(value):
        _refuse(f"{code}_missing_key", field)
    return value


def _text(value, pattern, code: str, field: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _refuse(code, field)
    return value


def _optional_text(value, pattern, code: str, field: str) -> Optional[str]:
    return None if value is None else _text(value, pattern, code, field)


def _flag(value, code: str, field: str) -> bool:
    if not isinstance(value, bool):
        _refuse(code, field)
    return value


def _requires(value, field: str):
    """requires_capability: exactly True, False or AUTO (a number is not a flag, and "Auto" is not the word)."""
    if not (isinstance(value, bool) or value == AUTO):
        _refuse("binding_requires_capability_not_allowed", field)
    return value


def _effective_mode(value, requires_capability, bound, field: str) -> Optional[str]:
    """None, or one of MODES, and only where THE AUTO TARGET says: on an auto target exactly when it is bound, never on a
    declared one."""
    if value is not None and value not in MODES:
        _refuse("binding_effective_mode_not_allowed", field)
    expected_present = requires_capability == AUTO and bound is not None
    if (value is not None) != expected_present:
        _refuse("binding_effective_mode_inconsistent", field)
    return value


def _count(value, code: str, field: str, low: int = 0, high: Optional[int] = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < low or (high is not None and value > high):
        _refuse(code, field)
    return value


def _optional_distance(value, field: str) -> Optional[float]:
    """None, or a finite distance in mm from 0 to RADIUS_MAX_MM (a typo guard, as for the radii)."""
    if value is None:
        return None
    if not _finite(value) or not 0.0 <= value <= RADIUS_MAX_MM:
        _refuse("result_distance_not_allowed", field)
    return float(value)


def _point(value, code: str, field: str) -> Tuple[float, float]:
    point = _pair(value)
    if point is None or max(abs(point[0]), abs(point[1])) > MAX_PAD_COORD_M:
        _refuse(code, field)
    return point


def _optional_point(value, code: str, field: str) -> Optional[Tuple[float, float]]:
    return None if value is None else _point(value, code, field)


def is_absolute_path(value) -> bool:
    """Absolute and already normalized: no '.', '..', empty component or trailing separator, no NUL, bounded."""
    return (isinstance(value, str) and 1 < len(value) <= MAX_PATH_CHARS and value.startswith("/") and not value.startswith("//")
            and "\x00" not in value and os.path.normpath(value) == value)


def _path(value, code: str, field: str) -> str:
    if not is_absolute_path(value):
        _refuse(code, field)
    return value


def _ipc_endpoint(value, code: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(_IPC) or not is_absolute_path(value[len(_IPC):]):
        _refuse(code, field)
    return value


def _schema(document: dict, name: str, version: int, code: str) -> None:
    if document["schema"] != name:
        _refuse(f"{code}_schema_not_known", "schema")
    if not isinstance(document["v"], int) or isinstance(document["v"], bool) or document["v"] != version:
        _refuse(f"{code}_version_not_known", "v")


def _same_point(a, b) -> bool:
    return a is not None and b is not None and all(math.isclose(x, y, rel_tol=0.0, abs_tol=1e-9) for x, y in zip(a, b))


# ---- the mission target file --------------------------------------------------------------------------------------
def parse_mission_target(document) -> MissionTarget:
    """The mission target a document describes, or MissionFileRefused.

    Beyond the shapes it checks the SEQUENCE the coordinator guarantees, because the binding rules rest on it: targets
    are carried in declaration order and a mission ends at its first target that is not PLACED, so the target at index i
    runs with every target before it PLACED (and none after it), and with every target BOUND unless i is 0, the first
    execution, whose own SEARCH binds them all."""
    document = _object(document, _TARGET_FILE_KEYS, "binding", "document")
    _schema(document, MISSION_TARGET_SCHEMA, MISSION_TARGET_VERSION, "binding")
    mission_id = _text(document["mission_id"], MISSION_ID_RE, "binding_mission_id_not_allowed", "mission_id")
    target_id = _text(document["target_id"], TARGET_ID_RE, "binding_target_id_not_allowed", "target_id")
    run_id = _text(document["run_id"], RUN_ID_RE, "binding_run_id_not_allowed", "run_id")
    requires_capability = _requires(document["requires_capability"], "requires_capability")
    raw = document["targets"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_TARGETS:
        _refuse("binding_targets_not_a_list", "targets")
    targets, seen = [], set()
    for item in raw:
        item = _object(item, _DECLARED_KEYS, "binding_target", "targets")
        declared_id = _text(item["target_id"], TARGET_ID_RE, "binding_target_id_not_allowed", "targets.target_id")
        if declared_id in seen:
            _refuse("binding_target_id_repeated", "targets.target_id")
        seen.add(declared_id)
        requires = _requires(item["requires_capability"], "targets.requires_capability")
        bound_at = _optional_point(item["bound_pad_m"], "binding_position_not_allowed", "targets.bound_pad_m")
        targets.append(DeclaredTarget(declared_id, requires, bound_at,
                                      _optional_point(item["placed_pad_m"], "binding_position_not_allowed", "targets.placed_pad_m"),
                                      _effective_mode(item["effective_mode"], requires, bound_at, "targets.effective_mode")))
    if target_id not in seen:
        _refuse("binding_target_not_declared", "target_id")
    index = [target.target_id for target in targets].index(target_id)
    if targets[index].requires_capability != requires_capability:
        _refuse("binding_requires_capability_disagrees", "requires_capability")
    bound = [target.bound_pad_m is not None for target in targets]
    if (index == 0 and any(bound)) or (index > 0 and not all(bound)):
        _refuse("binding_bound_positions_inconsistent", "targets.bound_pad_m")
    if any((target.placed_pad_m is not None) != (position < index) for position, target in enumerate(targets)):
        _refuse("binding_placed_positions_inconsistent", "targets.placed_pad_m")
    capability = document["capability"]
    if capability is not None and requires_capability is False:
        # The one direction refused: a run never carries a capability its target does not declare. Declared and not
        # carried is a first-execution capability target, whose own SEARCH measures and refuses (module docstring); an
        # auto target's runs carry it or not (THE AUTO TARGET).
        _refuse("binding_capability_presence_disagrees", "capability")
    endpoints = None
    if capability is not None:
        capability = _object(capability, _CAPABILITY_KEYS, "binding_capability", "capability")
        endpoints = CapabilityEndpoints(
            admin_probe=_path(capability["admin_probe"], "binding_path_not_absolute", "capability.admin_probe"),
            admin_socket=_path(capability["admin_socket"], "binding_path_not_absolute", "capability.admin_socket"),
            slot_uuid=_text(capability["slot_uuid"], _HEX32, "binding_slot_uuid_not_allowed", "capability.slot_uuid"),
            capsule=_text(capability["capsule"], _HEX64, "binding_capsule_not_allowed", "capability.capsule"),
            observation_endpoint=_ipc_endpoint(capability["observation_endpoint"], "binding_endpoint_not_allowed", "capability.observation_endpoint"),
            command_endpoint=_ipc_endpoint(capability["command_endpoint"], "binding_endpoint_not_allowed", "capability.command_endpoint"))
        if endpoints.observation_endpoint == endpoints.command_endpoint:
            _refuse("binding_endpoints_identical", "capability")
    radii = _radii(document["radii"])
    result_path = _path(document["result_path"], "binding_path_not_absolute", "result_path")
    return MissionTarget(mission_id, target_id, run_id, requires_capability, tuple(targets), endpoints, result_path, radii)


def _radius(value, field: str) -> float:
    if not _finite(value) or not 0.0 < value <= RADIUS_MAX_MM:
        _refuse("binding_radius_not_allowed", field)
    return float(value)


def _radii(raw) -> Optional[Radii]:
    """None (the radii are not qualified), or all three fields: two radii in (0, RADIUS_MAX_MM] and the stated basis."""
    if raw is None:
        return None
    raw = _object(raw, _RADII_KEYS, "binding_radii", "radii")
    basis = raw["basis"]
    if not isinstance(basis, str) or RADII_BASIS_RE.fullmatch(basis) is None or _DOTTED_QUAD.search(basis):
        _refuse("binding_radii_basis_not_allowed", "radii.basis")
    return Radii(_radius(raw["r_claim_mm"], "radii.r_claim_mm"), _radius(raw["r_match_mm"], "radii.r_match_mm"), basis)


def declared_targets(mission_targets: Sequence[Mapping]) -> list:
    """The binding file's `targets` for a first execution, from the mission's own target records: the id and the
    capability flag (True, False or AUTO, as the record carries it), nothing bound, nothing placed, no effective mode.
    The label and the reachability note do not enter the file."""
    return [{"target_id": target["target_id"], "requires_capability": target["requires_capability"], "effective_mode": None,
             "bound_pad_m": None, "placed_pad_m": None} for target in mission_targets]


def mission_target_document(*, mission_id: str, target_id: str, run_id: str, targets: Sequence[Mapping], capability: Optional[Mapping],
                            result_path: str, radii: Optional[Mapping] = None) -> dict:
    """The document the runtime writes, checked by the same parser the controller runs on it. `capability` is what this
    run CARRIES (None for a first-execution capability target that has not measured yet); `radii` is the configuration's
    mission.radii, or None, which the controller refuses."""
    by_id = {target.get("target_id"): target for target in targets if isinstance(target, Mapping)}
    if target_id not in by_id:
        _refuse("binding_target_not_declared", "target_id")
    document = {"schema": MISSION_TARGET_SCHEMA, "v": MISSION_TARGET_VERSION, "mission_id": mission_id, "target_id": target_id, "run_id": run_id,
                "requires_capability": by_id[target_id].get("requires_capability"),
                "targets": [dict(target) if isinstance(target, Mapping) else target for target in targets],
                "capability": dict(capability) if isinstance(capability, Mapping) else capability,
                "radii": dict(radii) if isinstance(radii, Mapping) else radii, "result_path": result_path}
    return parse_mission_target(document).to_document()


def load_mission_target(path) -> MissionTarget:
    if not is_absolute_path(path):
        _refuse("binding_path_not_absolute", MISSION_TARGET_ENV)
    target = parse_mission_target(_load_json(path, "binding"))
    if target.result_path == path:
        _refuse("binding_result_path_is_the_binding_file", "result_path")
    return target


def mission_target_from_environ(environ: Mapping) -> Optional[MissionTarget]:
    """None when KC_MISSION_TARGET is ABSENT: the legacy controller, nothing read. Otherwise the target, or a refusal
    (MissionFileRefused) that the controller turns into a refusal before any motion."""
    if MISSION_TARGET_ENV not in environ:
        return None
    return load_mission_target(environ[MISSION_TARGET_ENV])


def write_mission_target(path: str, document: dict) -> MissionTarget:
    """Checked, then written atomically: the runtime writes it into the run directory before the spawn."""
    target = parse_mission_target(document)
    if not is_absolute_path(path):
        _refuse("binding_path_not_absolute", "path")
    if target.result_path == path:
        _refuse("binding_result_path_is_the_binding_file", "result_path")
    write_json_atomic(path, target.to_document())
    return target


# ---- the mission result file --------------------------------------------------------------------------------------
def parse_result(document, expected: MissionTarget) -> dict:
    """The result the controller wrote for `expected`, normalized, or MissionFileRefused. The echo of mission_id,
    target_id and run_id is checked first: a result for another run is not read any further."""
    document = _object(document, _RESULT_KEYS, "result", "document")
    _schema(document, MISSION_RESULT_SCHEMA, MISSION_RESULT_VERSION, "result")
    for name in ("mission_id", "target_id", "run_id"):
        if not isinstance(document[name], str) or document[name] != getattr(expected, name):
            _refuse(f"result_{name.split('_')[0]}_mismatch", name)
    stage = document["stage"]
    if stage not in (STAGE_BOUND, STAGE_FINISHED):
        _refuse("result_stage_not_known", "stage")

    bindings = document["bindings"]
    if bindings is not None:
        if not expected.first_execution:
            _refuse("result_bindings_on_a_later_execution", "bindings")
        if not isinstance(bindings, list) or len(bindings) != len(expected.targets):
            _refuse("result_bindings_do_not_cover_the_targets", "bindings")
        normalized = []
        for entry, target in zip(bindings, expected.targets):
            entry = _object(entry, _BINDING_KEYS, "result_binding", "bindings")
            # A declared target binds in its declared mode; an auto target (unbound on a first execution) in whichever
            # measured mode its binding recorded, and that mode is carried as the entry says it.
            allowed = MODES if target.mode == AUTO else (target.mode,)
            if entry["target_id"] != target.target_id or entry["mode"] not in allowed:
                _refuse("result_binding_does_not_match_the_declaration", "bindings")
            normalized.append({"target_id": target.target_id, "mode": entry["mode"],
                               "bound_pad_m": list(_point(entry["bound_pad_m"], "result_position_not_allowed", "bindings.bound_pad_m")),
                               "observation_id": _optional_text(entry["observation_id"], OBSERVATION_ID_RE, "result_observation_id_not_allowed", "bindings.observation_id")})
        # ONE CUBE, ONE TARGET (bind_targets): two bindings at one position are one cube bound twice, whatever child wrote them.
        positions = [tuple(entry["bound_pad_m"]) for entry in normalized]
        if len(set(positions)) != len(positions):
            _refuse("result_bindings_share_a_cube", "bindings.bound_pad_m")
        bindings = normalized

    selection = document["selection"]
    if selection is not None:
        selection = _object(selection, _SELECTION_KEYS, "result_selection", "selection")
        distance = selection["distance_mm"]
        if not _finite(distance) or distance < 0.0:
            _refuse("result_distance_not_allowed", "selection.distance_mm")
        bound = _point(selection["bound_pad_m"], "result_position_not_allowed", "selection.bound_pad_m")
        # The selection is the binding's: its position, and its mode (the declaration, or the mode the binding measured).
        if expected.first_execution:
            if bindings is None:
                _refuse("result_selection_without_bindings", "selection")
            wanted, wanted_mode = bindings[expected.index]["bound_pad_m"], bindings[expected.index]["mode"]
        else:
            wanted, wanted_mode = expected.bound_pad_m, expected.mode
        if selection["mode"] != wanted_mode:
            _refuse("result_selection_mode_not_declared", "selection.mode")
        if not _same_point(bound, wanted):
            _refuse("result_selection_not_the_binding", "selection.bound_pad_m")
        selection = {"observation_id": _optional_text(selection["observation_id"], OBSERVATION_ID_RE, "result_observation_id_not_allowed", "selection.observation_id"),
                     "seen_pad_m": list(_point(selection["seen_pad_m"], "result_position_not_allowed", "selection.seen_pad_m")),
                     "bound_pad_m": list(bound), "distance_mm": float(distance), "mode": wanted_mode}

    outcome = document["outcome"]
    if (stage == STAGE_BOUND and outcome is not None) or (stage == STAGE_FINISHED and outcome not in OUTCOMES):
        _refuse("result_outcome_not_allowed", "outcome")
    final_state = _optional_text(document["final_state"], STATE_NAME_RE, "result_state_not_allowed", "final_state")
    reason_code = _optional_text(document["reason_code"], REASON_CODE_RE, "result_reason_code_not_allowed", "reason_code")
    predicates = _object(document["predicates"], PLACEMENT_PREDICATES, "result_predicates", "predicates")
    for name in PLACEMENT_PREDICATES:
        if predicates[name] is not None and not isinstance(predicates[name], bool):
            _refuse("result_predicate_not_boolean", f"predicates.{name}")
    predicates = {name: predicates[name] for name in PLACEMENT_PREDICATES}

    placement = document["placement"]
    if placement is not None:
        placement = _object(placement, _PLACEMENT_KEYS, "result_placement", "placement")
        placed = _flag(placement["placed"], "result_flag_not_boolean", "placement.placed")
        placement = {"placed": placed,
                     "placed_pad_m": None if placement["placed_pad_m"] is None else list(_point(placement["placed_pad_m"], "result_position_not_allowed", "placement.placed_pad_m")),
                     "on_pad_count": _count(placement["on_pad_count"], "result_count_not_allowed", "placement.on_pad_count", 0, 64),
                     "release_distance_mm": _optional_distance(placement["release_distance_mm"], "placement.release_distance_mm"),
                     "release_tolerance_mm": _optional_distance(placement["release_tolerance_mm"], "placement.release_tolerance_mm"),
                     "reason_code": _optional_text(placement["reason_code"], REASON_CODE_RE, "result_reason_code_not_allowed", "placement.reason_code"),
                     "observation_id": _optional_text(placement["observation_id"], OBSERVATION_ID_RE, "result_observation_id_not_allowed", "placement.observation_id")}
        distance, tolerance = placement["release_distance_mm"], placement["release_tolerance_mm"]
        consistent = ((placement["placed_pad_m"] is not None and placement["on_pad_count"] >= 1 and placement["reason_code"] is None
                       and distance is not None and tolerance is not None and distance <= tolerance)
                      if placed else (placement["placed_pad_m"] is None and placement["reason_code"] is not None
                                      and (distance is None or tolerance is None or distance > tolerance or placement["on_pad_count"] == 0)))
        if not consistent:
            _refuse("result_placement_inconsistent", "placement")

    capability = document["capability"]
    if capability is not None:
        if not expected.carries_capability:  # evidence of a relay this run never had
            _refuse("result_capability_evidence_without_capability", "capability")
        capability = _object(capability, _EVIDENCE_KEYS, "result_capability", "capability")
        dropped = capability["dropped"]
        if not isinstance(dropped, dict) or len(dropped) > MAX_DROP_REASONS:
            _refuse("result_capability_dropped_not_allowed", "capability.dropped")
        dropped = {_text(reason, REASON_CODE_RE, "result_reason_code_not_allowed", "capability.dropped"):
                   _count(count, "result_count_not_allowed", "capability.dropped") for reason, count in dropped.items()}
        capability = {"generation": _count(capability["generation"], "result_count_not_allowed", "capability.generation"),
                      "pid": _count(capability["pid"], "result_count_not_allowed", "capability.pid", 2, PID_MAX),
                      "accepted": _count(capability["accepted"], "result_count_not_allowed", "capability.accepted"),
                      "applied": _count(capability["applied"], "result_count_not_allowed", "capability.applied"),
                      "dropped": dict(sorted(dropped.items()))}
        if capability["applied"] > capability["accepted"]:
            # A command is APPLIED by the gateway only after the relay accepted and submitted it, so a count that says
            # otherwise is not the relay's evidence.
            _refuse("result_capability_applied_exceeds_accepted", "capability.applied")

    if stage == STAGE_BOUND:
        # Written after SEARCH selected and before the first motion: a selection and nothing that only an ending knows.
        if selection is None or placement is not None or any(value is not None for value in predicates.values()) or final_state is not None or reason_code is not None:
            _refuse("result_bound_stage_inconsistent", "stage")
    return {"schema": MISSION_RESULT_SCHEMA, "v": MISSION_RESULT_VERSION, "mission_id": expected.mission_id, "target_id": expected.target_id,
            "run_id": expected.run_id, "stage": stage, "bindings": bindings, "selection": selection, "outcome": outcome, "final_state": final_state,
            "reason_code": reason_code, "predicates": predicates, "placement": placement, "capability": capability}


def result_document(expected: MissionTarget, *, stage: str, bindings: Optional[Sequence[TargetBinding]] = None, selection: Optional[Selection] = None,
                    search_observation_id: Optional[str] = None, outcome: Optional[str] = None, final_state: Optional[str] = None,
                    reason_code: Optional[str] = None, predicates: Optional[Mapping] = None, placement: Optional[Placement] = None,
                    exit_observation_id: Optional[str] = None, capability: Optional[Mapping] = None) -> dict:
    """The document the controller writes, built from this module's own answers and checked by the runtime's parser.
    `predicates` may be the controller's whole predicate table: only the three that make a placement are carried, and
    clearance_target_met, a measured quality beside them, stays in the run record where it already is."""
    document = {
        "schema": MISSION_RESULT_SCHEMA, "v": MISSION_RESULT_VERSION, "mission_id": expected.mission_id, "target_id": expected.target_id,
        "run_id": expected.run_id, "stage": stage,
        "bindings": None if bindings is None else [{"target_id": b.target_id, "bound_pad_m": list(b.bound_pad_m), "mode": b.mode,
                                                    "observation_id": search_observation_id} for b in bindings],
        "selection": None if selection is None else {"observation_id": search_observation_id, "seen_pad_m": list(selection.seen_pad_m),
                                                     "bound_pad_m": list(selection.bound_pad_m), "distance_mm": selection.distance_mm, "mode": selection.mode},
        "outcome": outcome, "final_state": final_state, "reason_code": reason_code,
        "predicates": {name: (predicates or {}).get(name) for name in PLACEMENT_PREDICATES},
        "placement": None if placement is None else {"placed": placement.placed, "placed_pad_m": None if placement.placed_pad_m is None else list(placement.placed_pad_m),
                                                     "on_pad_count": placement.on_pad_count, "release_distance_mm": placement.release_distance_mm,
                                                     "release_tolerance_mm": placement.release_tolerance_mm,
                                                     "reason_code": placement.reason_code, "observation_id": exit_observation_id},
        "capability": None if capability is None else dict(capability),
    }
    return parse_result(document, expected)


def write_result(expected: MissionTarget, document: dict) -> dict:
    """Checked, then written atomically to the path the mission target named. A second write (bound, then finished)
    replaces the first whole: a reader sees one complete document or the other."""
    result = parse_result(document, expected)
    write_json_atomic(expected.result_path, result)
    return result


def load_result(expected: MissionTarget) -> dict:
    return parse_result(_load_json(expected.result_path, "result"), expected)


def result_claims_placement(result: dict, expected: MissionTarget) -> bool:
    """The file's half of PLACED for a mission target (the runtime adds the exit status): a finished placed_new run
    whose three placement predicates are all true, whose selection is recorded, whose attribution found a block on the pad
    at its own release (THE RELEASE AGREEMENT; the MEASURED predicates decide placement and completion, nothing below
    stands in for them), and, for a target that requires the capability, whose relay had at
    least one command from it APPLIED by the motion gateway (a necessary condition beside them, never a sufficient one:
    APPLIED says the gateway dispatched a velocity, not that a cube was placed) (`applied`, not `accepted`: the relay counts a command as
    accepted when it submits it, and a pulse whose every command the gateway rejected as expired or replayed still
    counts accepted ones): a capability target placed without the capability's own output having moved the base is not
    the mission's claim. For an AUTO target the condition follows the mode the run MEASURED (its selection's): a
    BASE_ADVANCE run is placed only through the capability's applied output, an ARM_ONLY run has no relay and needs none."""
    placement = result.get("placement") or {}
    if expected.auto:
        needs_relay = (result.get("selection") or {}).get("mode") == MODE_BASE_ADVANCE
    else:
        needs_relay = bool(expected.requires_capability)
    capability_ok = not needs_relay or ((result.get("capability") or {}).get("applied") or 0) >= 1
    return (result.get("stage") == STAGE_FINISHED and result.get("outcome") == "placed_new"
            and all(result.get("predicates", {}).get(name) is True for name in PLACEMENT_PREDICATES)
            and result.get("selection") is not None and (result.get("bindings") is not None or not expected.first_execution)
            and placement.get("placed") is True and placement.get("placed_pad_m") is not None and capability_ok)


def targets_after_placement(expected: MissionTarget, result: dict) -> list:
    """The `targets` of the NEXT target's file once this one is PLACED: every bound position (from this run's bindings on
    the first execution, carried over after it), every auto target's effective mode (the mode its binding measured, from
    the same bindings on the first execution, carried over after it) and this target's placed position added to the
    earlier ones."""
    if not result_claims_placement(result, expected):
        _refuse("result_does_not_claim_placement", "result")
    if expected.first_execution:
        bound = {entry["target_id"]: entry["bound_pad_m"] for entry in result["bindings"]}
        effective = {entry["target_id"]: entry["mode"] for entry in result["bindings"]}
    else:
        bound = {target.target_id: list(target.bound_pad_m) for target in expected.targets}
        effective = {target.target_id: target.effective_mode for target in expected.targets}
    placed = {target.target_id: list(target.placed_pad_m) for target in expected.targets if target.placed_pad_m is not None}
    placed[expected.target_id] = list(result["placement"]["placed_pad_m"])
    return [{"target_id": target.target_id, "requires_capability": target.requires_capability,
             "effective_mode": effective[target.target_id] if target.auto else None,
             "bound_pad_m": list(bound[target.target_id]), "placed_pad_m": placed.get(target.target_id)} for target in expected.targets]


# ---- file I/O -----------------------------------------------------------------------------------------------------
def _load_json(path: str, code: str):
    """One bounded read of a regular file (no FIFO that could block, no symlink at the last component), then a strict
    parse: a duplicate key or a NaN / Infinity constant is refused instead of silently taking one reading of it."""
    if not is_absolute_path(path):
        _refuse(f"{code}_path_not_absolute", "path")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        _refuse(f"{code}_file_missing", "path")
    except OSError:
        _refuse(f"{code}_file_unreadable", "path")
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _refuse(f"{code}_not_a_regular_file", "path")
        chunks, size = [], 0
        while size <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, MAX_FILE_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > MAX_FILE_BYTES:
            _refuse(f"{code}_file_too_large", "path")
    finally:
        os.close(descriptor)

    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                _refuse(f"{code}_duplicate_key", "document")
            out[key] = value
        return out

    def no_constant(_name):
        _refuse(f"{code}_number_not_finite", "document")

    try:
        return json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=unique, parse_constant=no_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _refuse(f"{code}_not_json", "document")


def write_json_atomic(path: str, document: dict) -> None:
    """Temporary sibling, fsync, rename, fsync of the directory: a reader never sees half a document, and a document
    written before a motion is on disk before that motion."""
    if not is_absolute_path(path):
        _refuse("path_not_absolute", "path")
    data = (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    directory_descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
