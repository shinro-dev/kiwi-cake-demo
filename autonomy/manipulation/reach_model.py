# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""reach_model: how far the loaded arm can carry a block from the proven grasp, and where the block then is. Pure.

It commands nothing, measures nothing, states nothing and never imports the controller. `ctl` is the controller's namespace
(stage_policy.ControllerView, or the module itself in a test); every joint limit, length, gate and bound below is READ from
it, so the model is the controller's own chain and never a second copy of it:
  - the URDF transcription and its proven-orientation solver (ctl.cartesian_lift, solve_cartesian_forward_and_z from the
    proven grasp ctl.KNOWN_GOOD_GRASP_POSE_DEG, exactly as ctl.proven_orientation_pose calls it);
  - ctl.gate_step: the proven gate (joint limits less the margin, the lateral drift bound) with the corrected elbow guard;
  - the tilt bound ctl.MAX_TILT_ERROR_DEG and the carry bounds (forward -30 mm to ctl.CARRY_MAX_FORWARD_M, height 0 to 60 mm).
Its users are the placement policies arm_reach and advance_then_reach (placement_selector.py), which plan with it, and the
executor of those policies, which leads its commands with lead_command_out_mm.

THE UNITS. "out" is what the controller's tool_state calls out_mm: the tool's range from the pan axis less the proven grasp
radius (GRASP_TOOL_RANGE_M, 179.0 mm), in mm; the carry target's forward shift from the proven grasp is the same number to
0.1 mm on the proven-orientation family (the 2026-09-30 reach analysis). "dz" is the height above the proven grasp, in mm.

THE REACH (R_gate). gate_reach_mm(ctl, dz) is the largest out, on a REACH_STEP_MM grid, whose proven-orientation carry target
passes every endpoint check proven_orientation_pose applies (target_legal). Computed from the chain, not written down: at the
carry height (30 mm) and the release height (8 mm) it is 152.5 mm, and the first refusal past it (153.0) is the gate's 5 mm
lateral drift bound, not a joint limit and not the elbow guard (the elbow stands 23 deg or more from the straight arm up to
there). A model only: unloaded, no collision geometry, joint limits whose provenance the repository does not state.

THE CAPS. command_cap_mm keeps every command COMMAND_MARGIN_MM inside the smaller reach of the two heights (150.0 mm). The
loaded arm realizes less than it is commanded: measured_out_cap_mm subtracts the worst recorded loaded shortfall (126.0 mm).
That is the largest MEASURED out a plan may count on. lead_command_out_mm is the shortfall compensation: a command leads the
wanted measured out by the shortfall the last step measured (the first send's recorded shortfall before any step), never by
more than the worst recorded one, and never past the command cap (nor past a lower cap its caller names: the proven loaded reach,
for a block the proven policy places, placement_policy THE LOADED REACH CAP).

THE TRAVEL MODELS. For an FK travel t from the grasp, the block's real travel lies between a low end and t; the release margin
is the smaller of the two ends' margins (the margin is concave along the segment). Two low ends are modelled:
  ADMISSION    t / FK_TRAVEL_OVERSTATEMENT: the release admission's own segment (release_margin_worst_mm), read from ctl;
  CAPPED_LOSS  max(t / FK_TRAVEL_OVERSTATEMENT, t - TRAVEL_LOSS_MAX_MM): the same ratio up to 64 mm of travel, then a loss
               that no longer grows. Every recorded loaded carry lies above both (TRAVEL_RECORDS): the largest loss on record
               is 23.8 mm (65.8 FK, 42.0 real), and the farthest carry lost the least (about 3 mm), so the records show no
               loss growing with reach. Beyond 78 mm of FK travel neither model has a measurement behind it.
The release admission itself is the controller's, unchanged, and it decides with ADMISSION.

THE PLANNING GRASP OUT. At SEARCH the grasp out is the coarse low-pose estimate, and every grasp that held in the extraction
below closed farther out than it (GRASP_UNDERREAD_RECORDS: 1.5 to 21.7 mm, 11 grasps): planning_grasp_out_mm charges
GRASP_UNDERREAD_MM to a coarse value and nothing to a measured one. The extraction left out one recorded grasp that held
farther out still, at the third close of the grasp stage's bounded retry (GRASP_UNDERREAD_RETRY_RECORDS: 30.5 mm past its
coarse estimate), beyond that charge. SEARCH's plans keep the charge; a binding among several blocks reads a plan under the
loaded reach cap at that larger under-read too (GRASP_UNDERREAD_HELD_MAX_MM: placement_policy.capped_plan_robust,
block_selection THE BINDING TIERS), so that a block whose plan fits only while the grasp holds inside the charge is not taken
before one whose plan fits however far out the record's grasps held.
"""
import numpy as np

# ---- parameters, each with its provenance: K kinematic, M measured on recorded runs (thin evidence), D a design choice ----
REACH_STEP_MM = 0.5  # D: the grid R_gate is found on
_COARSE_STEP_MM = 10.0  # D: the scan that brackets the first refusal before the bisection
COMMAND_MARGIN_MM = 2.5  # D: no command within this of the last legal carry target (the gate refuses 0.5 mm past it)
# M: commanded out minus measured out while holding a block, on the only two long loaded carries on record
# (cycle-run-20260920T093143Z and cycle-run-20260920T103136Z; the 2026-09-30 reach critique, section 1).
LOADED_SHORTFALL_RECORDS = (
    ("09-20 09:31 first send", 129.3, 112.2), ("09-20 09:31 re-send 1", 129.3, 106.2), ("09-20 09:31 re-send 2", 129.3, 105.4),
    ("09-20 10:31 first send", 116.6, 99.6), ("09-20 10:31 re-send 1", 116.6, 94.2),
)
LOADED_SHORTFALL_MAX_MM = 24.0  # M: the worst recorded (23.9 over the 17 far loaded pairs of the reach analysis), rounded up
LOADED_SHORTFALL_FIRST_MM = 18.0  # M: the first send's recorded shortfall (17.1 and 17.0), rounded up
# M: (FK travel, travel of the block measured by perception) of every recorded arm-only carry (the 2026-09-30 evidence
# report, section 3b). The first two are FK_TRAVEL_OVERSTATEMENT's own samples; the last two had an agent action or relative steps.
TRAVEL_RECORDS = (
    ("09-20 10:31 cycle-run-20260920T103136Z, released at FK +94.8", 65.8, 42.0),
    ("09-20 09:31 cycle-run-20260920T093143Z, agent release", 59.5, 55.0),
    ("09-19 21:48 cycle-run-20260919T214827Z, relative steps, pitch 91.6", 78.0, 55.9),
    ("09-19 single actions, pitch 85.8", 75.6, 67.5),
)
TRAVEL_LOSS_MAX_MM = 24.0  # M: the largest recorded loss, 65.8 - 42.0 = 23.8, rounded up
ADMISSION, CAPPED_LOSS = "admission", "capped_loss"
TRAVEL_MODELS = (ADMISSION, CAPPED_LOSS)
# M: (coarse cube_out at PREGRASP, FK tool out at the close that held) of every recorded grasp that held (Builder A's
# extraction over the 2026-09-30 evidence root: the controller logs of 2026-09-20, the state files of 2026-09-22, the runs
# of 2026-09-26 and 2026-09-28).
GRASP_UNDERREAD_RECORDS = (
    ("cycle-run-20260920T093143Z", 35.0, 45.5), ("cycle-run-20260920T103136Z", 7.3, 29.0), ("cycle-run-20260920T115018Z", 3.6, 14.0),
    ("cycle-run-20260920T120226Z", 13.5, 27.2), ("cycle-run-20260920T121517Z", 9.2, 29.0), ("cycle-run-20260920T133924Z", 15.1, 23.7),
    ("console-20260922T083804Z-zxkydauq", 7.9, 12.2), ("console-20260922T180557Z-5xeetlmj", -16.0, 1.9),
    ("console-20260926T201540Z-mb3c1048", 7.1, 23.4), ("console-20260926T203744Z-m2476137", 34.1, 48.0), ("console-20260928T174207Z-m915d3e9", 37.5, 39.0),
)
GRASP_UNDERREAD_MM = 22.0  # M: the largest recorded difference, 21.7, rounded up
# M: (coarse cube_out at PREGRASP, FK tool out at the close that held) of a recorded grasp that held past that charge, which
# the extraction above left out: the 2026-09-20 09:55 acceptance attempt, whose first two closes were empty, each followed by
# the grasp stage's built-in GRASP_RETRY_ADVANCE, and whose third close held (the run then stopped before the lift on the
# base-zero guard, the cube gripped; its controller log in the 2026-09-30 evidence root).
GRASP_UNDERREAD_RETRY_RECORDS = (("cycle-run-20260920T095543Z", 3.8, 34.3),)
GRASP_UNDERREAD_HELD_MAX_MM = 30.5  # M: the largest recorded difference of any grasp that held, both tuples above (34.3 - 3.8)

_CARRY_DZ_MM = (0.0, 60.0)  # proven_orientation_pose's height bounds
_CARRY_FORWARD_MIN_MM = -30.0  # proven_orientation_pose's lower forward bound
_REACH_CACHE = {}


def _grasp(ctl) -> dict:
    return {**{name: float(value) for name, value in ctl.KNOWN_GOOD_GRASP_POSE_DEG.items()}, "wrist_roll": 0.0}


def _solve(ctl, out_mm: float, dz_mm: float) -> dict:
    return ctl.cartesian_lift.solve_cartesian_forward_and_z(_grasp(ctl), float(out_mm) / 1000.0, float(dz_mm) / 1000.0)


def joints_at(ctl, out_mm: float, dz_mm: float) -> dict:
    """The lift joints (shoulder_lift, elbow_flex, wrist_flex, degrees) of the proven-orientation carry target at out_mm and
    dz_mm, as proven_orientation_pose would command them (rounded to 0.001 deg); the solver's answer even where the gate
    refuses it."""
    solved = _solve(ctl, out_mm, dz_mm)
    return {name: round(float(solved["joint_deg"][name]), 3) for name in ctl.LIFT_JOINTS}


def target_legal(ctl, out_mm: float, dz_mm: float) -> tuple:
    """(legal, reason): whether proven_orientation_pose's ENDPOINT checks accept the carry target out_mm beyond the proven
    grasp radius and dz_mm above the proven grasp height: the carry bounds, the gate (with the corrected elbow guard) and the
    tilt bound. The per-step travel bound and the interpolated path are the executor's per step, not the target's."""
    forward, dz = float(out_mm), float(dz_mm)
    if not (_CARRY_DZ_MM[0] <= dz <= _CARRY_DZ_MM[1] and _CARRY_FORWARD_MIN_MM <= forward <= float(ctl.CARRY_MAX_FORWARD_M) * 1000.0):
        return False, f"carry target outside its bounds: forward {forward:+.1f} mm, up {dz:+.1f} mm"
    solved = _solve(ctl, forward, dz)
    accepted, reason = ctl.gate_step(solved, _grasp(ctl), {"delta_forward_m": forward / 1000.0, "delta_z_m": dz / 1000.0})
    if not accepted:
        return False, f"the gate refuses this carry target: {reason}"
    if solved["orientation_error_deg"] > ctl.MAX_TILT_ERROR_DEG:
        return False, f"the proven orientation cannot be kept at this carry target (tilt error {solved['orientation_error_deg']:.2f} deg)"
    return True, reason


def _reach_key(ctl, dz_mm: float) -> tuple:
    """What R_gate depends on, so that a value patched on the controller is never answered from a stale cache."""
    return (float(dz_mm), REACH_STEP_MM, tuple(sorted(ctl.KNOWN_GOOD_GRASP_POSE_DEG.items())), float(ctl.CARRY_MAX_FORWARD_M), float(ctl.MAX_TILT_ERROR_DEG),
            float(ctl.ELBOW_STRAIGHT_ARM_DEG), float(ctl.TRUE_SINGULARITY_MARGIN_DEG), float(ctl.JOINT_LIMIT_MARGIN_DEG), tuple(sorted(ctl.JOINT_LIMITS_DEG.items())),
            id(ctl.gate_step), id(ctl.cartesian_lift.solve_cartesian_forward_and_z))


def gate_reach_mm(ctl, dz_mm: float) -> float:
    """R_gate(dz): the largest out, on the REACH_STEP_MM grid from 0, whose carry target is legal (target_legal), found by a
    scan every _COARSE_STEP_MM up to the carry bound and a bisection inside the first refused interval. The targets below
    it are legal on the grid (a test sweeps them). ValueError for a height outside the carry bounds, or when the grasp
    itself is refused. Cached on everything it reads."""
    dz = float(dz_mm)
    if not _CARRY_DZ_MM[0] <= dz <= _CARRY_DZ_MM[1]:
        raise ValueError(f"a carry height lies in {_CARRY_DZ_MM[0]:.0f}..{_CARRY_DZ_MM[1]:.0f} mm above the proven grasp, got {dz:+.1f}")
    key = _reach_key(ctl, dz)
    if key in _REACH_CACHE:
        return _REACH_CACHE[key]
    if not target_legal(ctl, 0.0, dz)[0]:
        raise ValueError(f"the proven grasp shifted {dz:+.1f} mm up is itself refused: the model has no reach")
    bound = float(ctl.CARRY_MAX_FORWARD_M) * 1000.0
    legal, refused = 0.0, None
    probe = _COARSE_STEP_MM
    while probe <= bound + 1e-9:
        if not target_legal(ctl, probe, dz)[0]:
            refused = probe
            break
        legal, probe = probe, probe + _COARSE_STEP_MM
    if refused is None:
        refused = bound + REACH_STEP_MM
        if bound - legal > 1e-9 and target_legal(ctl, bound, dz)[0]:
            legal = bound
    steps_low, steps_high = int(round(legal / REACH_STEP_MM)), int(round(refused / REACH_STEP_MM))
    while steps_high - steps_low > 1:  # invariant: steps_low legal, steps_high refused (or past the bound)
        middle = (steps_low + steps_high) // 2
        if middle * REACH_STEP_MM <= bound + 1e-9 and target_legal(ctl, middle * REACH_STEP_MM, dz)[0]:
            steps_low = middle
        else:
            steps_high = middle
    _REACH_CACHE[key] = steps_low * REACH_STEP_MM
    return _REACH_CACHE[key]


def command_cap_mm(ctl) -> float:
    """The largest out any carry command may name: the smaller R_gate of the carry and the release height, less
    COMMAND_MARGIN_MM."""
    return min(gate_reach_mm(ctl, ctl.CARRY_HEIGHT_MM), gate_reach_mm(ctl, ctl.PLACE_HEIGHT_MM)) - COMMAND_MARGIN_MM


def measured_out_cap_mm(ctl) -> float:
    """The largest MEASURED out a plan may count on: the command cap less the worst recorded loaded shortfall."""
    return command_cap_mm(ctl) - LOADED_SHORTFALL_MAX_MM


def lead_command_out_mm(ctl, target_out_mm: float, previous=None, cap_mm=None) -> float:
    """THE LOADED-SHORTFALL COMPENSATION. The out to command so that the loaded arm MEASURES target_out_mm: the target plus
    the shortfall the last step measured (`previous` = (commanded out, measured out) of that step), bounded to
    0..LOADED_SHORTFALL_MAX_MM, or plus LOADED_SHORTFALL_FIRST_MM before any step; never past command_cap_mm, nor past cap_mm
    when one is named."""
    if previous is None:
        lead = LOADED_SHORTFALL_FIRST_MM
    else:
        commanded, measured = (float(value) for value in previous)
        lead = min(max(commanded - measured, 0.0), LOADED_SHORTFALL_MAX_MM)
    cap = command_cap_mm(ctl) if cap_mm is None else min(command_cap_mm(ctl), float(cap_mm))
    return min(float(target_out_mm) + lead, cap)


def travel_low_mm(ctl, fk_travel_mm, model: str = ADMISSION):
    """The low end of the block's real travel for an FK travel (a number or a numpy array) under `model` (THE TRAVEL
    MODELS), with the controller's own arithmetic: travel / FK_TRAVEL_OVERSTATEMENT, and under CAPPED_LOSS no lower than
    travel - TRAVEL_LOSS_MAX_MM. A negative travel, a lagging arm, has the admission's low end under both models."""
    ratio = fk_travel_mm / float(ctl.FK_TRAVEL_OVERSTATEMENT)
    if model == ADMISSION:
        return ratio
    if model == CAPPED_LOSS:
        return np.maximum(ratio, fk_travel_mm - TRAVEL_LOSS_MAX_MM)
    raise ValueError(f"the travel models are {', '.join(TRAVEL_MODELS)}")


def travel_ends_mm(ctl, fk_travel_mm: float, model: str = ADMISSION) -> tuple:
    """(fk_travel_mm, the low end of the block's real travel) under `model`: the two ends of the segment the release margin
    is taken over."""
    travel = float(fk_travel_mm)
    return (travel, float(travel_low_mm(ctl, travel, model)))


def planning_grasp_out_mm(grasp_out_mm: float, measured: bool) -> float:
    """The grasp out a plan counts on: a MEASURED one as it is, a coarse one plus GRASP_UNDERREAD_MM."""
    return float(grasp_out_mm) + (0.0 if measured else GRASP_UNDERREAD_MM)

