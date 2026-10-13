# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""reach_carry: the steps the additive placement policies execute once the cube is held (arm_reach, advance_then_reach).

It has no word of its own and decides no placement: each policy's executor calls these steps inside its own procedure
(policy_arm_reach.execute, policy_advance_then_reach.execute, each decorated with its own StagePolicy), so every predicate
stated here carries the rule of the policy that ran it. It never imports the controller: ctx is the Cycle and ctl the
controller's namespace (stage_policy's module docstring), and every motion goes through the controller's own primitives,
unchanged: ctx.run_pose with a target from ctl.proven_orientation_pose (the proven gate with the corrected elbow guard, the
per-step tool travel bound and the interpolated path), ctx.grip, ctx.return_to_low_pose.

THE CARRY (carry). A closed loop on the MEASURED FK out, at the proven carry height. Each step commands the planned out led
by the loaded shortfall (reach_model.lead_command_out_mm: the shortfall the last step measured, 0 to the worst recorded 24
mm, the first send's recorded 18 mm before any step, never past the reach model's command cap, and for a plan under the
loaded reach cap never past the proven loaded reach, carry_cap_mm, read for the situation its plan was planned for: a block
the proven policy places, and any other whenever a plan of the run under the cap fits the budget, the operator's rulings of
2026-10-01 and 2026-10-02, placement_policy THE LOADED REACH CAP, so that no loaded command of such a carry leaves the
proven loaded reach, while its plan counts only on the out such a command measures under the record), and never more than one
bounded step past the measured out (STEP_MAX_MM, and less when the step also changes height, so that the whole tool
travel stays inside proven_orientation_pose's own bound). It stops, and says why (STOPS), when:
  arrived        the measured out is within ARRIVAL_TOLERANCE_MM of the planned out (or past it);
  regression     a step measured the tool nearer than the step before it: the recorded loaded re-sends went back 6.0 and
                 5.4 mm while the shoulder_lift error grew (cycle-run-20260920T093143Z and T103136Z), so nothing is re-sent;
  stalled        a step toward an unreached out gained less than MIN_GAIN_MM;
  joint_error    a lift joint measured more than JOINT_ERROR_MAX_DEG from its target: the recorded loaded re-sends reached
                 4.07 deg of shoulder_lift error. The executor's own arrival check aborts a step where the arm stands at
                 ARRIVAL_TOLERANCE_DEG (5 deg), and this stop acts only between steps: an error that stays under 5 deg ends
                 the carry here, but one that grows past 5 deg within one step (one send grew it by 1.85 deg on record) is
                 the executor's abort, which leaves the loaded arm where it stands, extended (a named residual);
  low            the tool height is under the carry's floor (floor_mm: the proven release height, ctl.PLACE_HEIGHT_MM,
                 above the pad, which is about 3 mm thick): the held block would stand at the release height while it still
                 moves out, so nothing further is sent. Two checks, both before anything is sent for the next step:
                   PREDICTED  the height the next step would measure (predicted_up_mm): the controller's own FK of its
                              target with every lift joint missing it by the error the last step measured (none before
                              the first step) and the shoulder_lift's error grown by LIFT_GROWTH_DEG, the largest growth
                              one send showed on record. A step predicted under the floor is never sent;
                   MEASURED   the height the last step measured (or the held pose, before the first step), for a carry
                              that has not arrived.
                 Why the measured height alone is not enough: the loaded arm sags on both lift joints, each under the
                 joint-error stop, the same errors put the tool LOWER THE FARTHER OUT it is commanded (about 5 mm between
                 +100 and +150 commanded at 3.54 and 2.67 deg), and the errors grow from one send to the next
                 (cycle-run-20260920T093143Z: 1.69 and 2.85 deg put the tool at +14.2 mm; the next send, 3.54 and 2.67 deg,
                 at +6.3; a third at +4.7). One step can therefore start above the floor and end at the pad's top. Nothing
                 else bounds the height: the gate checks the target and its modelled path, and run_pose's CONTACT_FLOOR_M
                 lies 15 mm under the grasp height. The prediction is a model on thin evidence (two recorded runs): an error
                 that grows past it within one step (never on record) is only seen after that step, at its measured height;
  gate_refusal   the next target is refused by the reach model's endpoint check, by proven_orientation_pose, or by the
                 executor's own planner (run_pose refuses before anything moves): nothing is sent for it;
  cap            the next command would re-send the last one: the lead is at the worst recorded shortfall, or the command
                 at its cap (the reach model's command cap, or the proven loaded reach of a plan under the loaded reach cap);
  steps          MAX_STEPS steps were taken.
Every step states REACH_CARRY (its commanded and measured out, the shortfall, the lead, the gain, the measured height and
the height it was predicted at before it was sent, the joint error), the end REACH_CARRY_STOPPED (with the measured height,
the floor, and the predicted height of a step that was not sent), and the predicate reach_carry says whether it arrived. Whether the block may be released is never the
carry's to say: the release admission decides on the measured out.

THE SETTLE (settle). What follows the carry, in the proven stage's order, stated in the proven stage's own states:
  - the arm-only check at the carry height: the proven admission's margin (ctl.release_margin_worst_mm) at the measured
    travel; short of it, ARM_ONLY_INSUFFICIENT and the arm-only predicate, as the proven stage states them, and THE WAY
    BACK UNDER LOAD (no lowering, nothing released);
  - the lowering, as the proven stage lowers: out + LOWER_OUT_MM, down to ctl.PLACE_HEIGHT_MM; never after a carry that
    ended with the tool under its floor (the block already stands at the release height, and a lowering commanded from
    there under the same sag would press it into the pad: RELEASE_HOLD says lower_skipped). Two bounds keep it where the
    carry has been. Without them, an arm that had arrived where it was commanded was lowered 6 mm past that out; a loaded
    arm that sags only once commanded past an out no carry step reached (the World's SaggingWorld) sagged there and
    measured its tool under the executor's contact floor, and the executor stopped all motion with the loaded arm extended
    (verify r3 replay MUST-FIX 1).
      THE OUT     lower_out_mm: never farther out than the carry's last command (Carry.last_pose: the 6 mm lead makes up
                  an arm that lags its command, and an arm that lags it by less is lowered at the out it was last commanded
                  to), nor past the plan's own lowering (the planned out plus the same lead: the first send's 18 mm lead can
                  overshoot a short plan, and the lowering then brings the block back toward the travel the plan was
                  admitted at as it goes down);
      THE HEIGHT  each height is PREDICTED (lowering_errors): the controller's FK of the target with every lift joint
                  missed as THE CARRY predicts a step (the error the arm measures against the carry's last command, none
                  without one, the shoulder_lift's grown by LIFT_GROWTH_DEG: a lowering after a carry is one more send),
                  and by at least the largest loaded error on record for that joint (LOADED_ERROR_RECORD_DEG: 4.07 deg of
                  shoulder_lift, 2.95 of elbow_flex). The floor is needed because the lowering's errors can appear in the
                  lowering alone, after a carry that measured none (the World's DroopingWorld). Those recorded errors take
                  the tool 23 to 29 mm lower from +100 to +150 mm out: near the release height that reaches the floor,
                  while at the carry height they leave the lowest link more than 15 mm above it (so no carry step needs
                  this floor; every step of the way back predicts its own). A height whose predicted LOWEST LINK stands under ctl.CONTACT_FLOOR_M is
                  never sent (predicted_lowest_link_m). That floor is what run_pose is handed and where the executor's
                  measured-height check stops all motion, which would end the run where the arm stands. On hardware the
                  pad takes the held block well above it; the World has no pad. Far out this asks a height above the
                  proven one (the recorded errors keep 8 mm clear of the floor to about +98 mm out, 10 mm to +116, 12 mm
                  to +133, past every out a plan names);
    Far out, the executor's own planner can refuse a height before anything moves (its sag margin: a 5 deg shoulder_lift
    sag must still leave the lowest link above the floor bound, and at +130 mm out it drops the tool about 26 mm). A height
    predicted under the floor or refused is asked again LOWER_RAISE_STEP_MM higher, never higher than LOWER_HEIGHT_MARGIN_MM
    under ctl.RELEASE_MAX_HEIGHT_MM (lower_heights_mm). None sent, PLACE_LOWER_REFUSED names the heights refused, those
    predicted under the floor and their predicted lowest link: the cube stays where the carry left it, and the admission
    decides there (its height bound refuses a block still at the carry height). The prediction rests on the same thin
    evidence as the carry's. An error within the lowering past both the last step's grown error and the record (never on
    record) can still take the lowest link under the floor. That is the executor's abort, the loaded arm left where it stands
    (a named residual, as for a carry step);
  - THE HOLD CHECK: the arm held still HOLD_CHECK_S, then the pose read again. Steady when the gripper still reads its
    holding band and the tool moved less than HOLD_DRIFT_MAX_MM out and up (RELEASE_HOLD, the predicate release_hold). A
    gripper outside its holding band is reach_release_hold_lost, the run ends where the arm stands: the controller's
    run_pose refuses every arm move whose gripper is not in its declared phase, so no way back can be commanded then;
  - THE RELEASE ADMISSION: the proven admission, on the pose the hold check read: the worst-case centre margin at the
    measured travel at least ctl.PLACE_REQUIRED_CENTRE_MM and the measured release height at most
    ctl.RELEASE_MAX_HEIGHT_MM, and a steady hold (RELEASE_ADMISSION and the predicates release_margin, release_height,
    release_admitted, as the proven stage states them). Refused, THE WAY BACK UNDER LOAD.
THE WAY BACK UNDER LOAD (way_back; the lead's decision A of 2026-10-01, settling the operator question of verify r4
safety MF1 and MF2 and verify r5 safety MF1). A settle that does not admit the release brings the loaded arm back to the
carry pose, the cube held, or HOLDS it where it stands; it sends no step whose bounds the recorded loaded behaviour below
would break (the bounds are models of that record, A NAMED RESIDUAL). What the records show (the evidence root's loaded
moves, LOADED meaning the gripper in its holding band):
  - loaded raises of the shoulder_lift at extension are on record, four of them, and none rose: at +99.6 to +112.2 mm out
    (cycle-run-20260920T093143Z CARRY steps 3 to 5, cycle-run-20260920T103136Z CARRY step 3) they asked 1.41 to 4.06 deg
    and realized -109% to 0%, ending 2.90 to 4.06 deg off: two stood still or nearly (09:31 steps 4 and 5, -15% and 0%) and
    two sagged FURTHER from where the lift stood (09:31 step 3 by 1.85 deg, 10:31 step 3 by 1.49 deg, -109% and -106%);
  - loaded raises nearer the grasp radius (+45.5 mm out and less) realized 22% to 86% and ended 2.6 to 3.7 deg off: the
    loaded lift settles a few degrees off whatever it is asked;
  - unloaded raises of the extended arm realized 47% to 100%: a straight rise of 10.67 deg at +83.9 mm out realized 47% and
    the executor aborted it (cycle-run-20260919T212637Z, the tool 16.8 mm back), the RETREAT back of
    cycle-run-20260920T103136Z realized 75% of 11.44 deg; the proven cycle's own lesson is that the lift joint cannot raise
    the fully extended arm, so its retreat goes back first.
With a lift that does not rise, any move whose other joints follow their targets swings the held block back and DOWN (the
rise this replaces moved it 24 mm back and 12 mm down under such a lift, pressing it onto the pad), and the executor
checks arrival and height only after a whole move has been sent. So the way back is a sequence of steps, each one planned
from the MEASURED tool (way_back_base, verify r7 safety MF1): never farther out than the tool measures (nor than the LAST
COMMAND, the target that put the arm where it stands: the lowering, the carry's last step, or the step before), and never
more than WAY_BACK_SAG_MM above the height it measures (nor above the last command's), so that no step is a re-send of the
far carry target nor a raise at extension. A loaded arm that lags its last command (every sagging far carry: the carry
leads each command by the measured shortfall) used to be stepped from that command, its first steps commanded 6 to 17 mm
OUT of the measured tool and 7 to 16 mm UP, to within 1 to 3 mm of the far carry target at its height: the move the
recorded loaded re-sends at extension made, and both sagged further. In order:
  BACK  toward the body at the height the tool measures (a lowering's low height, or the carry height), until the tool
        measures within RETRACT_WITHIN_MM of the grasp radius; then
  UP    to the carry height at the out the tool measures, near the body, where the records show the loaded lift rising.
Each step is the largest candidate (WAY_BACK_BACK_MM, WAY_BACK_UP_MM) that passes every bound before it is sent
(way_back_bounds):
  - the lead's: the shoulder_lift asked at most WAY_BACK_LIFT_STEP_MAX_DEG (8 deg, below the largest recorded raise of
    the extended arm), every other lift joint within the largest change recorded in one raise of the extended arm
    (WAY_BACK_JOINT_STEP_MAX_DEG), each change counted from the measured pose, as the executor plans it;
  - the executor's arrival abort, should the loaded arm not follow at all: each lift joint's predicted error, the larger of
    the error it measures against the last command and its whole change (a lift that does not move ends as far off as it
    was asked), the shoulder_lift's grown by LIFT_GROWTH_DEG (THE CARRY's growth, the -109% record), under
    EXECUTOR_ARRIVAL_DEG;
  - the models of the loaded lift (way_back_models, WAY_BACK_MODELS), each the joints a step is predicted to end at:
      REALIZED  every lift joint at its target with the error it measures against the last command, the shoulder_lift's
                grown by LIFT_GROWTH_DEG (THE CARRY's own prediction);
      STALLED   the shoulder_lift where it stands, as the loaded raises at extension on record that did not rise, the other
                lift joints at their targets with their errors;
      FURTHER   STALLED with the shoulder_lift sagged LIFT_GROWTH_DEG further from where it stands, as the two recorded
                raises above that sagged further did;
  - the executor's measured-height floor: every model's lowest link predicted above ctl.CONTACT_FLOOR_M;
  - the pad: under every model the held block is predicted never under the pad's top (PAD_THICKNESS_MM), at every out, and
    under STALLED the tool at most WAY_BACK_STALL_DROP_MAX_MM lower than it measures. No step is sent whose any model
    predicts pressing the held block onto the pad (the lead's ruling of 2026-10-02 on verify r10 safety MF1). Where the
    record shows these behaviours: the two raises that sagged further started at +99.6 and +112.2 mm out (verify r6 safety
    MF1: a step from a block lowered to the release height ended it pressed into the pad and dragged under that
    realization); the loaded raises nearer the grasp radius (+45.5 mm out and nearer) rose by 22 to 86%; and no loaded raise
    between +45.5 and +95 mm out is on record at all, so there the record supports no model over another, and none is
    dropped. Verify r7 replay MF2 had the sag-further bound applied past the proven loaded reach only, so that a release
    refused after a lowering inside it could reach the in-run fallback; from such a lowered block the first step was then
    sent with the other two models putting the block under the pad's top (verify r10 safety MF1);
  - the gate and the executor's planner (target_legal, proven_orientation_pose, run_pose before anything moves).
After each step the measured state is read (REACH_RETRACT_STEP states the step, its bounds' numbers and what it measured),
and the arm HOLDS where it stands, the gripper closed, when that step measured:
  joint_error  a lift joint more than JOINT_ERROR_MAX_DEG from its target, or the shoulder_lift's error grown by more
               than LIFT_GROWTH_DEG (the lift did not follow);
  sag          the tool more than WAY_BACK_SAG_MM lower than before the step;
  regression   back: the tool farther out than it measured before the step (no step is commanded past it), or neither back
               nor nearer its target by WAY_BACK_MIN_GAIN_MM; up: raised less than WAY_BACK_MIN_GAIN_MM.
A hold is reach_way_back_held (held_by: joint_error, sag, regression, or no_step when no candidate passes the bounds: a
loaded arm that lags its last command, a lift already as far off its command as the recorded growth allows, a held block
near the pad's top, a gate that refuses every target), with a plain sentence of why: the run ends with the cube held for
the operator to take, never in the executor's abort, and a step is sent only where every model of the loaded lift (a lift
that realizes it with its errors and the recorded growth, one that does not follow it, one that sags further within it by
the recorded growth) is predicted to leave the held block above the pad's top (A NAMED RESIDUAL says what the prediction
does not cover). The hold's sentence and its record (in_run_fallback: FALLBACK_NOT_RUN) also say why the in-run fallback
does not run (FALLBACK_NOT_RUN_WHY): it starts only from the carry pose, and the lead ruled on 2026-10-02 (operator question
Q7, A: keep the hold) that a loaded arm the way back cannot step back inside these bounds holds where it stands rather than
be moved in a way the records do not show. A step the executor's planner refuses is reach_retract_refused, and
WAY_BACK_MAX_STEPS steps that leave the arm short of the carry pose reach_retract_incomplete: there too the arm stands where
it was. REACH_RETRACT states a way back that came back. A carry that never left the grasp radius at or above the carry
height commands nothing.
What it costs. Far out the stall bound allows small steps (about 1.5 mm at +120 mm out, 5 mm at +60), so a way back from
+114 mm takes about 33 steps; and a refused attempt whose loaded arm lags its last command (from about 1 deg on the lift
joints: a step back at the measured height asks the lift to take up the lag and re-orient the tool, which a loaded lift
that does not move turns into a drop past WAY_BACK_STALL_DROP_MAX_MM, or the wrist to change more than any recorded raise of
the extended arm), whose loaded arm already measures a lift error within LIFT_GROWTH_DEG of the arrival abort, or whose held
block stands within WAY_BACK_STALL_DROP_MAX_MM of the pad's top, is held where it stands before any step instead of going
on with the in-run fallback: the lead's decision 3 holds only where the way back comes back, from an arm that stands where
it was commanded, and the lead ruled on 2026-10-02 (operator question Q7, A) that the hold is the answer elsewhere until a
loaded session is measured: no loaded move outside the recorded envelope, the run ending for the operator with its record
saying why the fallback did not run. A loaded arm lags its command inside the proven loaded reach too, and the record measures by how much
(verify r8 policy MF1 and replay MUST-FIX 1): every loaded carry step of 2026-09-19/20 commanded inside +95 mm out measured
14.0 to 19.9 mm short (shoulder_lift 0.68 to 1.78 deg off, elbow_flex 2.28 to 3.73), and the descent to the carry height
and the lowering of the four proven base placements of 2026-09-20 each missed its commanded out by 8.9 to 15.8 mm
(elbow_flex 1.37 to 2.23 deg), more than the lag at which a refused attempt is held before any step (from about 0.5 deg
on both lift joints at the proven loaded reach). On hardware a refused additive release is therefore expected to end held, the
cube on the arm, and the in-run fallback to run only from a refusal that leaves the arm where it was commanded (a lowering
the executor's planner refuses). That is why a block the proven policy places is planned so that the record's lag is not
refused: its plan counts only on the out its capped commands measure under the record, and its release keeps the proven
rule's lagging-arm buffer (placement_policy, THE LOADED REACH CAP and THE LAGGING ARM AT THE RELEASE). A lift sagging
further by the recorded growth takes the tool 3.3 to 4.8 mm lower per degree from +40 to +126 mm out (6 to 9 mm for the
1.85 deg), and so does a lift realizing a step with that growth, so a block lowered for a release is held before any step
unless it stands well above the pad: lowered to the proven release height (8 mm up), it is held at every out even from an
arm that realized its lowering exactly; from such an arm a block lowered to 12 mm takes its way back inside the proven
loaded reach, and one at 14 mm at every out; an arm lagging its lowering by 0.3 deg on both lift joints is held at 12 mm
from about +60 mm out, and one lagging it as the record shows inside the proven loaded reach (0.8 and 2.2 deg) at every
lowering height and every out. A release refused after a lowering therefore ends with the cube held where it stands, as
the installed controller ends its own refused release (release_not_admitted), and the in-run fallback runs after a refusal
at the carry height (a carry short of the admission, or a lowering the executor's planner refused) from an arm that stands
where it was commanded.
A NAMED RESIDUAL. The bounds are models on thin evidence (four loaded raises at extension, two of which sagged further, by
1.85 and 1.49 deg; no loaded raise between +45.5 and +95 mm out). A step back asks a different joint motion than the
recorded re-sends of one far target; how the loaded lift realizes it is not measured, and a lift that sags further than the
recorded growth within one step can still take the held block lower than the bound before the sag it measured holds the
arm. Every bound reads a step's end pose, not the path between. Only an attended measurement of loaded steps back, at
extension and inside the proven loaded reach, can narrow it, in either direction.

THE RELEASE (release_and_place). The proven stage's release, retreat, EXIT and PLACED (policy_placement.run_placement),
stated again here because the proven stage is one procedure that cannot be called in part: the gripper opens, RELEASED,
the retreat back and up, the EXIT to the low scan pose, PLACED from the EXIT view (a mission target's block by the release
agreement), and what RETURN rests on (policy_placement.PlacementOutcome).

THE HELD STATE (Held) is what a policy's executor starts from and hands back when it did not place the block: the arm's
view, the measured grasp out and pan, the SEARCH anchor moved by every measured advance of this run, the SEARCH reach
direction (the release axis, as the proven admission uses it), the axis and the rate the last advance ended on (RETURN's),
the run's measured advance so far, the out the loaded arm reached when a carry of this run stopped short (out_cap_mm), the
SEARCH record, the policies tried, the least advance the next plan must name once the arm alone was refused from this
stance (min_advance_mm), and whether the proven policy places the block (proven_admits: at SEARCH and again on the measured
grasp, placement_executor THE CHOICE ONCE HELD). situation() is the placement policies' Situation of it: measured, the
plan-time side allowance, the measured out cap, what is left of the advance budget, the least advance and proven_admits,
which every re-plan of the run reads its loaded reach cap with. An Attempt is one executor's answer: the
placement (policy_placement.PlacementOutcome) or, not placed, the held state it ended in (the arm back at the carry pose:
an attempt whose way back held the arm raised reach_way_back_held instead) and
why, as a sentence and as one of REFUSALS, which a refusal record names (refused_by): the carry ended short of every travel
the admission takes (carry_short: the arm-only check); the release admission refused a lowered cube whose hold was not
steady (hold_not_steady), that stood too high (release_too_high) or whose margin was short (margin_short); after a measured
advance the arm alone was not planned to place it (replan_short, advance_then_reach's REACH_REPLAN). state_plan states PLACEMENT_PLAN and its predicate for the policy that takes over, in the proven stage's own fields
plus the policy's.
"""
import dataclasses
import math
from dataclasses import dataclass
from typing import Optional

import placement_envelope
import placement_policy
import policy_placement
import reach_model
from stage_policy import CycleFailed

# ---- the stops of a carry ----
ARRIVED, REGRESSION, STALLED, JOINT_ERROR, LOW, GATE_REFUSAL, CAP, STEPS = "arrived", "regression", "stalled", "joint_error", "low", "gate_refusal", "cap", "steps"
STOPS = (ARRIVED, REGRESSION, STALLED, JOINT_ERROR, LOW, GATE_REFUSAL, CAP, STEPS)

# ---- parameters, each with its provenance: M measured on recorded runs (thin evidence), D a design choice, P the proven stage's own ----
MAX_STEPS = 8  # D: the closed loop of the 2026-09-30 reach design (D8); the installed carry takes at most 6
STEP_MAX_MM = 55.0  # P: the installed carry's own step bound (policy_placement.carry_arm_only: at most 55 mm past the measured out)
TRAVEL_BOUND_MARGIN_MM = 2.0  # D: a step's whole tool travel stays this far inside proven_orientation_pose's CARRY_MAX_TOOL_TRAVEL_M
ARRIVAL_TOLERANCE_MM = 3.0  # D: inside the AIM_BEYOND_MM (5) a plan aims past its smallest admitted travel; the admission decides on the measured out
MIN_GAIN_MM = 2.0  # D: a step toward an unreached out that gains less than this is not re-sent (the recorded re-sends went back, not forward)
JOINT_ERROR_MAX_DEG = 4.0  # M/D: the recorded loaded re-sends reached 4.07 deg of shoulder_lift error; under the executor's 5 deg arrival abort
# M: the largest growth of the loaded shoulder_lift error from one send to the next on record: 1.69 to 3.54 deg re-sending
# one target (cycle-run-20260920T093143Z; 1.41 to 2.90 on cycle-run-20260920T103136Z). The elbow_flex error did not grow on
# either (2.85 to 2.67, 2.95 to 2.95), so no growth is predicted for it.
LIFT_GROWTH_DEG = 1.85
# M: the largest loaded error on record for each lift joint that sags: shoulder_lift 4.07 deg (cycle-run-20260920T093143Z,
# the second and third send of one target), elbow_flex 2.95 deg (cycle-run-20260920T103136Z, both sends). THE SETTLE's
# lowering is predicted with at least these (THE HEIGHT).
LOADED_ERROR_RECORD_DEG = {"shoulder_lift": 4.07, "elbow_flex": 2.95}
HOLD_CHECK_S = 1.0  # D: how long the lowered arm is held still before the pose is read again
HOLD_DRIFT_MAX_MM = 3.0  # D: a lowered arm whose tool moved more than this out or up while held still is not released
LOWER_OUT_MM = 6.0  # P: the proven lowering's own outward lead (policy_placement.run_placement, "PLACE lower": out + 0.006 m)
LOWER_RAISE_STEP_MM = 2.0  # D: how much higher a lowering the planner refused is asked again
LOWER_HEIGHT_MARGIN_MM = 3.0  # D: a lowering target stays this far under the release admission's height bound (RELEASE_MAX_HEIGHT_MM)
RETRACT_WITHIN_MM = 15.0  # P: the proven fallback's "CARRY back to the grasp radius": the way back ends this near the grasp radius
# ---- THE WAY BACK UNDER LOAD (module docstring): what bounds every move of it ----
# The lead's decision A of 2026-10-01: a step's commanded shoulder_lift change, below the largest recorded raise of the
# extended arm (11.44 deg, unloaded, the RETREAT back of cycle-run-20260920T103136Z).
WAY_BACK_LIFT_STEP_MAX_DEG = 8.0
# M: the largest commanded change of each other lift joint in one recorded move that raised the shoulder_lift of the
# extended arm (the tool past +60 mm out): elbow_flex +28.26 and wrist_flex -13.09 deg, the RETREAT back of
# cycle-run-20260919T214827Z.
WAY_BACK_JOINT_STEP_MAX_DEG = {"elbow_flex": 28.26, "wrist_flex": 13.09}
EXECUTOR_ARRIVAL_DEG = 5.0  # P: the executor's arrival check (scan_world.ARRIVAL_TOLERANCE_DEG): a joint farther than this from its target after a move aborts the run where the arm stands
PAD_THICKNESS_MM = 3.0  # M: the pad is about 3 mm thick (the controller's RELEASE_MAX_HEIGHT_MM note): a held block under this height stands on the pad
WAY_BACK_STALL_DROP_MAX_MM = 3.0  # D: how much lower a step may leave the tool should the loaded lift not move at all
WAY_BACK_SAG_MM = 1.0  # D: a step after which the tool measures more than this lower than before it sagged
WAY_BACK_MIN_GAIN_MM = 0.5  # D: a step that brought the tool less than this toward its aim (back, or up) did not move it
WAY_BACK_BACK_MM = (40.0, 30.0, 20.0, 15.0, 10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.5, 2.0, 1.5, 1.0)  # D: the back travels a step considers, the largest first
WAY_BACK_UP_MM = (20.0, 15.0, 10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.5, 2.0, 1.5, 1.0)  # D: the rises a step considers, the largest first
WAY_BACK_MAX_STEPS = 60  # D: the steps of one way back (the smallest steps are far out, where the stall bound is tightest)
BACK, UP = "back", "up"  # the two phases of the way back, in this order
# The models of the loaded lift every step of the way back is bounded under (way_back_models; the module docstring, the pad).
REALIZED, STALLED, FURTHER = "realized", "stalled", "further"
WAY_BACK_MODELS = (REALIZED, STALLED, FURTHER)
HELD_NO_STEP, HELD_JOINT_ERROR, HELD_SAG, HELD_REGRESSION = "no_step", "joint_error", "sag", "regression"  # why the way back held the arm (held_by)
HELD = (HELD_NO_STEP, HELD_JOINT_ERROR, HELD_SAG, HELD_REGRESSION)
# The lead's ruling of 2026-10-02 on operator question Q7 (A, keep the hold): a way back that holds the loaded arm says why the
# in-run fallback (placement_executor, THE IN-RUN FALLBACK) does not run, in its sentence and in its record (in_run_fallback).
FALLBACK_NOT_RUN = "not_run"
FALLBACK_NOT_RUN_WHY = ("the in-run fallback (the next placement policy, which moves the base and tries again) does not run: it starts only from the carry "
                        "pose, and the loaded arm is never moved outside what the records show to bring it there")

# ---- why an attempt did not place the block (THE HELD STATE): what a refusal record names as refused_by ----
CARRY_SHORT, HOLD_NOT_STEADY, RELEASE_TOO_HIGH, MARGIN_SHORT, REPLAN_SHORT = "carry_short", "hold_not_steady", "release_too_high", "margin_short", "replan_short"
REFUSALS = (CARRY_SHORT, HOLD_NOT_STEADY, RELEASE_TOO_HIGH, MARGIN_SHORT, REPLAN_SHORT)


@dataclass(frozen=True)
class Carry:
    """A carry's end: the last view, why it stopped (STOPS) and in words, the planned and the measured out, every step, and
    the target of the last step sent (None when none was): the out THE SETTLE's lowering never goes past, the command its
    prediction reads the arm's errors against, and the last command THE WAY BACK UNDER LOAD steps back from."""

    view: dict
    stop: str
    detail: str
    wanted_out_mm: float
    out_mm: float
    steps: tuple
    last_pose: object = None

    @property
    def arrived(self) -> bool:
        return self.stop == ARRIVED


@dataclass(frozen=True)
class Lowering:
    """THE SETTLE's lowering: the view after it (the carry's end when none was sent), the height asked for and the target sent
    (None when none was), the out it was asked at, the heights refused (height, why: the gate, the executor's planner, or
    the prediction), the heights THE HEIGHT's prediction put under the executor's floor with the lowest link predicted at
    each, the lowest link predicted for the height sent, and the lift errors predicted with (lowering_errors)."""

    view: dict
    height_mm: Optional[float]
    pose: object
    out_mm: float
    refused: tuple
    predicted_under: tuple
    predicted_lowest_link_m: Optional[float] = None
    errors: dict = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class Settled:
    """A settle's end: whether the release is admitted; the view (the held, lowered pose when admitted, the retracted one
    otherwise); the FK travel and the worst-case margin the admission decided on (at the carry height when the arm-only
    check refused); why it was not admitted (None when it was), as a sentence and as one of REFUSALS; the out the loaded
    arm reached before any retract."""

    admitted: bool
    view: dict
    travel_mm: float
    worst_mm: float
    why: Optional[str]
    reached_out_mm: float
    refusal: Optional[str] = None


@dataclass(frozen=True)
class Held:
    """THE HELD STATE (module docstring)."""

    view: dict
    grasp_out_mm: float
    grasp_pan_deg: float
    anchor: tuple
    direction: tuple
    axis: tuple
    advanced_mm: float
    rate: float
    out_cap_mm: Optional[float]
    seen: Optional[dict]
    tried: tuple = ()
    min_advance_mm: Optional[float] = None
    proven_admits: Optional[bool] = None

    def situation(self, ctl, side_allowance_mm=None) -> placement_policy.Situation:
        """The placement policies' Situation of this held state (module docstring, THE HELD STATE)."""
        allowance = float(ctl.PLACE_SIDE_DRIFT_ALLOWANCE_MM) if side_allowance_mm is None else float(side_allowance_mm)
        remaining = max(float(ctl.BASE_MAX_TOTAL_ADVANCE_MM) - float(self.advanced_mm), 0.0)
        return placement_policy.Situation(self.anchor, self.direction, self.grasp_out_mm, True, allowance, self.out_cap_mm, remaining, self.min_advance_mm,
                                          self.proven_admits)

    def but(self, **changes) -> "Held":
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True)
class Attempt:
    """One executor's answer (module docstring, THE HELD STATE)."""

    placement: Optional[policy_placement.PlacementOutcome]
    held: Held
    why: Optional[str]
    refusal: Optional[str] = None


def _mm(value) -> float:
    return round(float(value), 1)


def state_plan(ctx, ctl, policy: str, plan, held: Held) -> None:
    """PLACEMENT_PLAN for the policy that takes over (module docstring, THE HELD STATE): the proven stage's fields (mode,
    fk_travel_mm, base_advance_mm, the anchor and its margin, the reach direction, the required margin, and the mode the
    mission bound) and the policy's (its plan, the measured grasp out, the advance so far, the measured out cap), then the
    predicate placement_plan, as the proven stage states it."""
    anchor = held.anchor
    bound = {} if ctx.target is None else {"bound_mode": ctx.target.run_mode(ctx._mission["selection"])}
    ctx.state("PLACEMENT_PLAN", mode=plan.mode, fk_travel_mm=_mm(plan.fk_travel_mm), base_advance_mm=_mm(plan.base_advance_mm), policy=policy, arm_out_mm=_mm(plan.arm_out_mm),
              worst_case_centre_margin_mm=_mm(plan.worst_margin_mm), capped_loss_margin_mm=_mm(plan.capped_loss_margin_mm), sentence=plan.sentence,
              anchor_pad_m=[round(float(v), 4) for v in anchor], anchor_margin_mm=_mm(ctl.placed_margin_mm(anchor)), reach_direction_pad=[round(float(v), 3) for v in held.direction],
              required_centre_margin_mm=_mm(ctl.PLACE_REQUIRED_CENTRE_MM), grasp_out_mm=_mm(held.grasp_out_mm), base_advance_so_far_mm=_mm(held.advanced_mm),
              out_cap_mm=None if held.out_cap_mm is None else _mm(held.out_cap_mm), evidence=held.seen, **bound)
    ctx.events.predicate("placement_plan", True, measured=ctl.placed_margin_mm(anchor), threshold=ctl.PLACE_REQUIRED_CENTRE_MM, unit="mm", anchor=held.seen,
                         scalars=("anchor_margin_mm", "grasp_out_mm"), empirical=("VISUAL_ANCHOR_UNCERTAINTY_MM", "FK_TRAVEL_OVERSTATEMENT"), stamp_of=held.view["measured"],
                         measurements={"policy": policy, "base_advance_mm": plan.base_advance_mm, "fk_travel_mm": plan.fk_travel_mm, "arm_out_mm": plan.arm_out_mm,
                                       "worst_case_centre_margin_mm": plan.worst_margin_mm, "capped_loss_margin_mm": plan.capped_loss_margin_mm,
                                       "grasp_out_mm": held.grasp_out_mm, "base_advance_so_far_mm": held.advanced_mm, "out_cap_mm": held.out_cap_mm})


def floor_mm(ctl) -> float:
    """The carry's floor (module docstring, THE CARRY, low): the MEASURED tool height above the proven grasp height under
    which the loaded block is not carried any farther, the proven release height ctl.PLACE_HEIGHT_MM."""
    return float(ctl.PLACE_HEIGHT_MM)


def _missed(ctl, pose, errors: dict) -> dict:
    """`pose`'s target joints with every lift joint missed by the error in `errors` and the shoulder_lift's grown by
    LIFT_GROWTH_DEG: the pose THE CARRY's prediction measures."""
    joints = {name: float(value) for name, value in pose.joints.items()}
    for name in ctl.LIFT_JOINTS:
        joints[name] += float(errors.get(name, 0.0)) + (LIFT_GROWTH_DEG if name == "shoulder_lift" else 0.0)
    return joints


def predicted_up_mm(ctl, pose, errors: dict) -> float:
    """THE CARRY, low, PREDICTED (module docstring): the tool height, mm above the proven grasp, at which the next step would
    be measured: the controller's FK (ctl.tool_state) of `pose`'s target joints with every lift joint missed by the error in
    `errors` (measured minus commanded, the last step's; none before the first step) and the shoulder_lift's grown by
    LIFT_GROWTH_DEG."""
    return float(ctl.tool_state(_missed(ctl, pose, errors))["up_mm"])


def lowering_errors(ctl, errors: dict) -> dict:
    """THE SETTLE, THE HEIGHT (module docstring): the lift joints' errors (degrees, measured minus commanded) a lowering is
    predicted with. Each is THE CARRY's (the error in `errors`, the one the arm measures against the carry's last command,
    the shoulder_lift's grown by LIFT_GROWTH_DEG), and at least the largest loaded error on record for its joint
    (LOADED_ERROR_RECORD_DEG)."""
    grown = {name: float(errors.get(name, 0.0)) + (LIFT_GROWTH_DEG if name == "shoulder_lift" else 0.0) for name in ctl.LIFT_JOINTS}
    return {name: max(value, float(LOADED_ERROR_RECORD_DEG[name])) if name in LOADED_ERROR_RECORD_DEG else value for name, value in grown.items()}


def predicted_lowest_link_m(ctl, pose, errors: dict) -> float:
    """THE SETTLE, THE HEIGHT (module docstring): the height, m, of the arm's lowest link, as the executor's own
    measured-height check reads it (ctl.tool_state's lowest_link_m), for `pose`'s target joints with each lift joint missed
    by its error in `errors` (lowering_errors)."""
    joints = {name: float(value) for name, value in pose.joints.items()}
    for name, value in errors.items():
        joints[name] += float(value)
    return float(ctl.tool_state(joints)["lowest_link_m"])


def _commanded_out_mm(ctl, view: dict, pose) -> float:
    """The out `pose`'s target names (the controller's FK of its joints, the rest of the pose `view` measured)."""
    return float(ctl.tool_state({**view["measured"], **{name: float(value) for name, value in pose.joints.items()}})["out_mm"])


def _errors_against(ctl, view: dict, pose) -> dict:
    """The lift joints' errors (measured minus commanded) of the pose `view` measured against the target `pose` put it at;
    none without a target."""
    if pose is None:
        return {}
    return {name: float(view["measured"][name]) - float(pose.joints[name]) for name in ctl.LIFT_JOINTS}


def _room_mm(ctl, view: dict, dz_mm: float, wanted_mm: float) -> float:
    """How far out (or back) one step may go from the measured pose so that its whole tool travel, out and up, stays
    TRAVEL_BOUND_MARGIN_MM inside proven_orientation_pose's per-step bound: at most wanted_mm."""
    bound = float(ctl.CARRY_MAX_TOOL_TRAVEL_M) * 1000.0 - TRAVEL_BOUND_MARGIN_MM
    rise = float(dz_mm) - float(view["tool"]["up_mm"])
    return min(float(wanted_mm), math.sqrt(max(bound * bound - rise * rise, 0.0)))


def carry_cap_mm(ctl, held: Held, situation=None) -> Optional[float]:
    """THE CARRY's cap on its commands for `held`'s block (module docstring), read for the situation the carried plan was
    planned for (held.situation(ctl) when none is named): placement_policy.loaded_command_cap_mm when that cap lies under the
    reach model's command cap (a plan under the loaded reach cap: no loaded command passes the proven loaded reach), None
    otherwise (the reach model's command cap). Either leads the measured out a plan counts on
    (placement_policy.loaded_reach_cap_mm) by the worst recorded loaded shortfall."""
    cap = placement_policy.loaded_command_cap_mm(ctl, held.situation(ctl) if situation is None else situation, held.proven_admits)
    return cap if cap < reach_model.command_cap_mm(ctl) - 1e-9 else None


def carry(ctx, ctl, policy: str, view: dict, grasp_out_mm: float, wanted_out_mm: float, cap_mm: Optional[float] = None) -> Carry:
    """THE CARRY (module docstring): from the held `view` to the planned measured out `wanted_out_mm`, no command past
    cap_mm when one is named (carry_cap_mm)."""
    steps, previous, floor = [], None, floor_mm(ctl)
    cap = reach_model.command_cap_mm(ctl) if cap_mm is None else min(reach_model.command_cap_mm(ctl), float(cap_mm))
    stop, detail, wanted = STEPS, f"{MAX_STEPS} steps were taken", float(wanted_out_mm)
    errors, not_sent, sent = {}, None, None  # the lift joints' errors the last step measured; the height predicted for a step not sent; the last target sent
    for step in range(MAX_STEPS):
        out = float(view["tool"]["out_mm"])
        if out >= wanted - ARRIVAL_TOLERANCE_MM:
            stop, detail = ARRIVED, f"the measured out is within {ARRIVAL_TOLERANCE_MM:.0f} mm of the planned out"
            break
        if float(view["tool"]["up_mm"]) < floor:
            stop, detail = LOW, _low_detail(view, floor)
            break
        led = reach_model.lead_command_out_mm(ctl, wanted, previous, cap)
        command = min(led, out + _room_mm(ctl, view, ctl.CARRY_HEIGHT_MM, STEP_MAX_MM))
        if previous is not None and command <= previous[0] + 0.5:
            stop = CAP
            detail = (f"the next command would re-send {previous[0]:.1f} mm: the lead is at the worst recorded loaded shortfall ({reach_model.LOADED_SHORTFALL_MAX_MM:.0f} mm)"
                      f" or the command at the cap ({cap:.1f} mm)")
            break
        legal, reason = reach_model.target_legal(ctl, command, ctl.CARRY_HEIGHT_MM)
        if not legal:
            stop, detail = GATE_REFUSAL, str(reason)[:200]
            break
        try:
            pose = ctl.proven_orientation_pose(view["measured"], command / 1000.0, ctl.CARRY_HEIGHT_MM / 1000.0)[0]
        except ctl.ScanRefused as refusal:
            stop, detail = GATE_REFUSAL, str(refusal)[:200]
            break
        predicted = predicted_up_mm(ctl, pose, errors)
        if predicted < floor:
            stop, not_sent = LOW, predicted
            detail = (f"the next step, commanded to {command:.1f} mm out, is predicted to measure the tool at {predicted:+.1f} mm above the grasp height, under the "
                      f"carry's floor of {floor:.0f} mm (the last step's joint errors, the shoulder_lift's grown by {LIFT_GROWTH_DEG:.2f} deg): it is not sent")
            break
        try:  # the executor's own planner refuses before anything moves (run_pose raises ScanRefused only then)
            after = ctx.run_pose(pose, True, ctl.CONTACT_FLOOR_M, label=f"REACH_CARRY {policy} step {step}")
        except ctl.ScanRefused as refusal:
            stop, detail = GATE_REFUSAL, f"the executor's planner refuses this carry step: {str(refusal)[:160]}"
            break
        measured_out = float(after["tool"]["out_mm"])
        errors = {name: float(after["measured"][name]) - float(pose.joints[name]) for name in ctl.LIFT_JOINTS}
        worst_error = max(abs(value) for value in errors.values())
        record = {"policy": policy, "step": step, "wanted_out_mm": _mm(wanted), "commanded_out_mm": _mm(command), "measured_out_mm": _mm(measured_out),
                  "shortfall_mm": _mm(command - measured_out), "lead_mm": _mm(led - wanted), "gain_mm": _mm(measured_out - out), "up_mm": _mm(after["tool"]["up_mm"]),
                  "predicted_up_mm": _mm(predicted), "joint_error_deg": round(worst_error, 2), "shoulder_lift_error_deg": round(errors["shoulder_lift"], 2),
                  "tool": ctl.fmt_tool(after["tool"])}
        ctx.state("REACH_CARRY", **record)
        steps.append(record)
        view, previous, sent = after, (command, measured_out), pose
        if worst_error > JOINT_ERROR_MAX_DEG:
            stop, detail = JOINT_ERROR, f"a lift joint measured {worst_error:.2f} deg from its target (at most {JOINT_ERROR_MAX_DEG:.1f})"
            break
        if measured_out < wanted - ARRIVAL_TOLERANCE_MM and float(after["tool"]["up_mm"]) < floor:
            stop, detail = LOW, _low_detail(after, floor)
            break
        if measured_out < wanted - ARRIVAL_TOLERANCE_MM and measured_out < out:
            stop, detail = REGRESSION, f"the step measured the tool {out - measured_out:.1f} mm nearer than the step before it"
            break
        if measured_out < wanted - ARRIVAL_TOLERANCE_MM and measured_out - out < MIN_GAIN_MM:
            stop, detail = STALLED, f"the step gained {measured_out - out:.1f} mm (under {MIN_GAIN_MM:.0f})"
            break
    out = float(view["tool"]["out_mm"])
    if stop == STEPS and out >= wanted - ARRIVAL_TOLERANCE_MM:
        stop, detail = ARRIVED, f"the measured out is within {ARRIVAL_TOLERANCE_MM:.0f} mm of the planned out"
    ctx.state("REACH_CARRY_STOPPED", policy=policy, reason=stop, wanted_out_mm=_mm(wanted), measured_out_mm=_mm(out), short_mm=_mm(wanted - out), steps=len(steps),
              fk_travel_from_grasp_mm=_mm(out - grasp_out_mm), measured_up_mm=_mm(view["tool"]["up_mm"]), floor_mm=floor,
              predicted_up_mm=None if not_sent is None else _mm(not_sent), lift_growth_deg=LIFT_GROWTH_DEG, detail=detail)
    ctx.events.predicate("reach_carry", stop == ARRIVED, measured=out, threshold=wanted - ARRIVAL_TOLERANCE_MM, unit="mm", scalars=("measured_out_mm", "wanted_out_mm"),
                         empirical=("FK_TRAVEL_OVERSTATEMENT",), stamp_of=view["measured"],
                         measurements={"wanted_out_mm": wanted, "measured_out_mm": out, "steps": len(steps), "stop": stop, "grasp_out_mm": grasp_out_mm,
                                       "loaded_shortfall_max_mm": reach_model.LOADED_SHORTFALL_MAX_MM, "command_cap_mm": cap, "measured_up_mm": float(view["tool"]["up_mm"]),
                                       "floor_mm": floor, "predicted_up_mm": not_sent, "lift_growth_deg": LIFT_GROWTH_DEG})
    return Carry(view, stop, detail, wanted, out, tuple(steps), sent)


def _low_detail(view: dict, floor: float) -> str:
    return (f"the tool measured {float(view['tool']['up_mm']):+.1f} mm above the grasp height, under the carry's floor of {floor:.0f} mm: the held block stands at the "
            "release height, so it is carried no farther")


@dataclass(frozen=True)
class WayBackStep:
    """One planned step of THE WAY BACK UNDER LOAD (module docstring): the target to send (None when no candidate passed),
    its phase, its commanded out and height, the change it asks of each lift joint, the error each is predicted to end at,
    the height the tool is predicted at should the loaded lift not move, why each larger candidate was not taken, and the
    held block's height under every model of the loaded lift (WAY_BACK_MODELS)."""

    pose: object
    phase: str
    out_mm: float
    up_mm: float
    change: dict
    predicted: dict
    stall_up_mm: Optional[float]
    refused: tuple
    model_up_mm: dict = dataclasses.field(default_factory=dict)


def _commanded_tool(ctl, view: dict, pose) -> dict:
    """The tool state `pose`'s target names (the controller's FK of its joints, the rest of the pose `view` measured); the
    measured tool state without a target."""
    if pose is None:
        return dict(view["tool"])
    return ctl.tool_state({**view["measured"], **{name: float(value) for name, value in pose.joints.items()}})


def way_back_models(ctl, measured: dict, target: dict, missed: dict) -> dict:
    """THE WAY BACK UNDER LOAD's models of the loaded lift (module docstring, the pad; WAY_BACK_MODELS): the joints each
    predicts a step to `target`'s lift joints ends at, from the pose the arm `measured` and the error each lift joint
    measures against the last command (`missed`): REALIZED, every lift joint at its target with its error, the
    shoulder_lift's grown by LIFT_GROWTH_DEG; STALLED, the shoulder_lift where it stands, the other lift joints at their
    targets with their errors; FURTHER, STALLED with the shoulder_lift sagged LIFT_GROWTH_DEG further from where it stands."""
    realized = {**measured, **{name: float(target[name]) + float(missed.get(name, 0.0)) + (LIFT_GROWTH_DEG if name == "shoulder_lift" else 0.0) for name in ctl.LIFT_JOINTS}}
    stalled = {**measured, **{name: float(target[name]) + float(missed.get(name, 0.0)) for name in ctl.LIFT_JOINTS if name != "shoulder_lift"}}
    further = {**stalled, "shoulder_lift": float(measured["shoulder_lift"]) + LIFT_GROWTH_DEG}
    return {REALIZED: realized, STALLED: stalled, FURTHER: further}


def way_back_bounds(ctl, view: dict, last_pose, out_mm: float, up_mm: float) -> tuple:
    """THE WAY BACK UNDER LOAD, the bounds of one step (module docstring): (why the proven-orientation target at out_mm and
    up_mm is not taken from the pose `view` measured, or None; the change it asks of each lift joint; the error each is
    predicted to end at; the held block's height, mm above the grasp height, under every model of the loaded lift,
    way_back_models). `last_pose` is the last command, whose errors the arm measures (none without one)."""
    legal, reason = reach_model.target_legal(ctl, out_mm, up_mm)
    if not legal:
        return str(reason)[:160], None, None, None
    measured = view["measured"]
    target = reach_model.joints_at(ctl, out_mm, up_mm)
    missed = _errors_against(ctl, view, last_pose)
    change = {name: float(target[name]) - float(measured[name]) for name in ctl.LIFT_JOINTS}
    if abs(change["shoulder_lift"]) > WAY_BACK_LIFT_STEP_MAX_DEG:
        return f"it asks the shoulder_lift to change {change['shoulder_lift']:+.2f} deg, more than {WAY_BACK_LIFT_STEP_MAX_DEG:.0f}", change, None, None
    for name, bound in WAY_BACK_JOINT_STEP_MAX_DEG.items():
        if abs(change[name]) > bound:
            return f"it asks the {name} to change {change[name]:+.2f} deg, more than the {bound:.2f} recorded in one raise of the extended arm", change, None, None
    predicted = {name: max(abs(float(missed.get(name, 0.0))), abs(change[name])) + (LIFT_GROWTH_DEG if name == "shoulder_lift" else 0.0) for name in ctl.LIFT_JOINTS}
    worst = max(predicted, key=predicted.get)
    if predicted[worst] >= EXECUTOR_ARRIVAL_DEG:
        return (f"the {worst} could end {predicted[worst]:.2f} deg from its target (its last error {float(missed.get(worst, 0.0)):+.2f} deg or the whole change "
                f"{change[worst]:+.2f} deg, missed{f' and grown by {LIFT_GROWTH_DEG:.2f} as on record' if worst == 'shoulder_lift' else ''}), at the executor's "
                f"{EXECUTOR_ARRIVAL_DEG:.0f} deg arrival abort"), change, predicted, None
    models = way_back_models(ctl, measured, target, missed)
    states = {name: ctl.tool_state(joints) for name, joints in models.items()}
    heights = {name: float(state["up_mm"]) for name, state in states.items()}
    lowest = min(float(state["lowest_link_m"]) for state in states.values())
    if lowest < float(ctl.CONTACT_FLOOR_M):
        return f"its lowest link is predicted at {lowest:+.4f} m, under the executor's floor {float(ctl.CONTACT_FLOOR_M):+.4f} m", change, predicted, heights
    floor = max(PAD_THICKNESS_MM, float(view["tool"]["up_mm"]) - WAY_BACK_STALL_DROP_MAX_MM)
    if heights[STALLED] < floor:
        return (f"should the loaded lift not move (the records at extension), the tool would end {heights[STALLED]:+.1f} mm above the grasp height, under "
                f"{floor:+.1f} (at most {WAY_BACK_STALL_DROP_MAX_MM:.0f} mm lower, never under the pad's top)"), change, predicted, heights
    if heights[FURTHER] < PAD_THICKNESS_MM:
        return (f"should the loaded lift sag further as it did on record ({LIFT_GROWTH_DEG:.2f} deg from where it stands), the held block would end "
                f"{heights[FURTHER]:+.1f} mm above the grasp height, under the pad's top ({PAD_THICKNESS_MM:.0f} mm)"), change, predicted, heights
    if heights[REALIZED] < PAD_THICKNESS_MM:
        return (f"should the loaded arm realize the step with the errors it measures and the shoulder_lift's grown by {LIFT_GROWTH_DEG:.2f} deg as on record, the held "
                f"block would end {heights[REALIZED]:+.1f} mm above the grasp height, under the pad's top ({PAD_THICKNESS_MM:.0f} mm)"), change, predicted, heights
    return None, change, predicted, heights


def way_back_base(ctl, view: dict, last_pose) -> tuple:
    """THE WAY BACK UNDER LOAD plans every step from the MEASURED tool (module docstring): (out, up), mm, the point its
    candidates are taken from: never farther out than the tool measures (nor than the last command `last_pose` names), and
    never more than WAY_BACK_SAG_MM above the height the tool measures (nor above the last command's)."""
    commanded, measured = _commanded_tool(ctl, view, last_pose), view["tool"]
    return (min(float(commanded["out_mm"]), float(measured["out_mm"])), min(float(commanded["up_mm"]), float(measured["up_mm"]) + WAY_BACK_SAG_MM))


def _way_back_candidates(ctl, view: dict, last_pose, phase: str, grasp_out_mm: float) -> list:
    """THE WAY BACK UNDER LOAD's candidate targets for the next step, the largest first, each from way_back_base (the
    measured tool, never past the last command): back toward the body at that height, never past the grasp radius, or up
    to the carry height at that out."""
    (out, up), carry = way_back_base(ctl, view, last_pose), float(ctl.CARRY_HEIGHT_MM)
    if phase == BACK:
        wanted = [(max(float(grasp_out_mm), out - back), up) for back in WAY_BACK_BACK_MM]
    else:
        wanted = [(out, min(carry, up + rise)) for rise in WAY_BACK_UP_MM]
    unique = []
    for candidate in wanted:
        if all(abs(candidate[0] - other[0]) > 1e-6 or abs(candidate[1] - other[1]) > 1e-6 for other in unique):
            unique.append(candidate)
    return unique


def plan_way_back_step(ctl, view: dict, last_pose, phase: str, grasp_out_mm: float) -> WayBackStep:
    """THE WAY BACK UNDER LOAD's next step (module docstring): the largest candidate that passes way_back_bounds and that
    proven_orientation_pose takes, or none."""
    refused = []
    for out, up in _way_back_candidates(ctl, view, last_pose, phase, grasp_out_mm):
        why, change, predicted, heights = way_back_bounds(ctl, view, last_pose, out, up)
        if why is None:
            try:
                pose = ctl.proven_orientation_pose(view["measured"], out / 1000.0, up / 1000.0)[0]
            except ctl.ScanRefused as refusal:
                why = str(refusal)[:160]
            else:
                return WayBackStep(pose, phase, out, up, change, predicted, heights[STALLED], tuple(refused), dict(heights))
        refused.append((round(out, 1), round(up, 1), why))
    commanded = _commanded_tool(ctl, view, last_pose)
    return WayBackStep(None, phase, float(commanded["out_mm"]), float(commanded["up_mm"]), {}, {}, None, tuple(refused))


def _hold(ctx, view: dict, policy: str, held_by: str, why: str, grasp_out_mm: float, steps: list, **values):
    """THE WAY BACK UNDER LOAD holds the loaded arm where it stands (module docstring): reach_way_back_held, with why, and why
    the in-run fallback does not run (FALLBACK_NOT_RUN_WHY, the lead's ruling of 2026-10-02 on operator question Q7)."""
    out, up = float(view["tool"]["out_mm"]), float(view["tool"]["up_mm"])
    raise ctx.failed("reach_way_back_held", f"REACH_RETRACT: the way back holds the loaded arm where it stands, {out - float(grasp_out_mm):.0f} mm beyond the grasp radius "
                     f"and {up:+.0f} mm above the grasp height: {why}; the gripper stays closed and the cube held, for the operator to take; {FALLBACK_NOT_RUN_WHY}",
                     policy=policy, held_by=held_by, measured_out_mm=out, measured_up_mm=up, grasp_out_mm=float(grasp_out_mm), steps_taken=len(steps),
                     in_run_fallback=FALLBACK_NOT_RUN, **values)


def _after_step(ctl, before: dict, after: dict, pose, last_pose, phase: str) -> tuple:
    """What the measured state after a step of the way back says (module docstring): (held_by, why, values) when it holds
    the arm, else None."""
    errors = {name: float(after["measured"][name]) - float(pose.joints[name]) for name in ctl.LIFT_JOINTS}
    worst = max(errors, key=lambda name: abs(errors[name]))
    grew = abs(errors["shoulder_lift"]) - abs(float(_errors_against(ctl, before, last_pose).get("shoulder_lift", 0.0)))
    if abs(errors[worst]) > JOINT_ERROR_MAX_DEG or grew > LIFT_GROWTH_DEG:
        return HELD_JOINT_ERROR, (f"the last step measured the {worst} {errors[worst]:+.2f} deg from its target and the shoulder_lift's error grown by {grew:+.2f} deg "
                                  f"(at most {JOINT_ERROR_MAX_DEG:.1f} deg, grown at most {LIFT_GROWTH_DEG:.2f} as in one send on record)"), {
            "joint_error_deg": round(abs(errors[worst]), 2), "lift_error_growth_deg": round(grew, 2)}
    drop = float(before["tool"]["up_mm"]) - float(after["tool"]["up_mm"])
    if drop > WAY_BACK_SAG_MM:
        return HELD_SAG, f"the last step measured the tool {drop:.1f} mm lower than before it (more than {WAY_BACK_SAG_MM:.0f} mm): the loaded arm sagged", {"sag_mm": round(drop, 1)}
    if phase == BACK:  # out past where it measured before the step, which no step is commanded past
        stood = float(before["tool"]["out_mm"])
        if float(after["tool"]["out_mm"]) > stood + WAY_BACK_MIN_GAIN_MM:
            return HELD_REGRESSION, (f"the last step measured the tool {float(after['tool']['out_mm']) - stood:.1f} mm farther out than it stood: the loaded arm went back out"), {
                "regression_mm": round(float(after["tool"]["out_mm"]) - stood, 1)}
        aim = _commanded_tool(ctl, after, pose)
        gain = float(before["tool"]["out_mm"]) - float(after["tool"]["out_mm"])
        closer = math.hypot(float(before["tool"]["out_mm"]) - float(aim["out_mm"]), float(before["tool"]["up_mm"]) - float(aim["up_mm"])) - math.hypot(
            float(after["tool"]["out_mm"]) - float(aim["out_mm"]), float(after["tool"]["up_mm"]) - float(aim["up_mm"]))
        if gain < WAY_BACK_MIN_GAIN_MM and closer < WAY_BACK_MIN_GAIN_MM:
            return HELD_REGRESSION, (f"the last step brought the tool {gain:+.1f} mm back and {closer:+.1f} mm nearer its target (at least {WAY_BACK_MIN_GAIN_MM:.1f} mm either way): "
                                     "the loaded arm did not follow it"), {"gain_mm": round(gain, 1)}
        return None
    gain = float(after["tool"]["up_mm"]) - float(before["tool"]["up_mm"])
    if gain < WAY_BACK_MIN_GAIN_MM:
        return HELD_REGRESSION, f"the last step raised the tool {gain:+.1f} mm (less than {WAY_BACK_MIN_GAIN_MM:.1f} mm): the loaded arm did not follow it", {"gain_mm": round(gain, 1)}
    return None


def way_back(ctx, ctl, policy: str, view: dict, grasp_out_mm: float, last_pose=None) -> dict:
    """THE WAY BACK UNDER LOAD (module docstring): the loaded arm from where a refused attempt left it (`view`, with
    `last_pose` the last command that put it there) to the carry pose, each step from the measured tool: back toward the body
    at the height the tool measures, then up near it, in bounded steps read one by one; it holds the arm where it stands, the
    cube held, when a step would leave the recorded envelope or measured a joint error, a sag or no progress. Returns the
    view at the carry pose; the view itself when the arm never left the grasp radius at or above the carry height."""
    before_out, steps, command, carry = float(view["tool"]["out_mm"]), [], last_pose, float(ctl.CARRY_HEIGHT_MM)
    while True:
        height = float(_commanded_tool(ctl, view, command)["up_mm"])
        phase = BACK if float(view["tool"]["out_mm"]) - float(grasp_out_mm) > RETRACT_WITHIN_MM else UP if height < carry - 0.05 else None
        if phase is None:
            break
        if len(steps) >= WAY_BACK_MAX_STEPS:
            out, up = float(view["tool"]["out_mm"]), float(view["tool"]["up_mm"])
            raise ctx.failed("reach_retract_incomplete", f"REACH_RETRACT: after {len(steps)} steps the loaded arm stands {out - float(grasp_out_mm):.0f} mm beyond the grasp radius "
                             f"and {up:+.0f} mm above the grasp height, not yet back at the carry pose; the cube stays held where the arm stands", policy=policy, measured_out_mm=out,
                             measured_up_mm=up, grasp_out_mm=float(grasp_out_mm), retract_within_mm=RETRACT_WITHIN_MM, steps_taken=len(steps))
        planned = plan_way_back_step(ctl, view, command, phase, grasp_out_mm)
        if planned.pose is None:
            aim = "back toward the body" if phase == BACK else "up to the carry height near it"
            binding = planned.refused[-1] if planned.refused else (None, None, "no candidate")
            _hold(ctx, view, policy, HELD_NO_STEP, f"no step {aim} stays inside what the records show the loaded arm doing (the smallest, to {binding[0]} mm out at "
                  f"{binding[1]} mm: {binding[2]})", grasp_out_mm, steps, phase=phase, candidates_refused=len(planned.refused))
        label = f"REACH_RETRACT {policy} back toward the body" if phase == BACK else f"REACH_RETRACT {policy} up to the carry height"
        try:  # the executor's own planner refuses before anything moves (run_pose raises ScanRefused only then)
            after = ctx.run_pose(planned.pose, True, ctl.CONTACT_FLOOR_M, label=label)
        except ctl.ScanRefused as refusal:
            raise ctx.failed("reach_retract_refused", f"REACH_RETRACT: the executor's planner refuses the next step of the way back to the carry pose ({str(refusal)[:160]}); "
                             "the cube stays held where the arm stands", policy=policy, measured_out_mm=float(view["tool"]["out_mm"]), grasp_out_mm=float(grasp_out_mm),
                             retract_within_mm=RETRACT_WITHIN_MM) from None
        errors = {name: float(after["measured"][name]) - float(planned.pose.joints[name]) for name in ctl.LIFT_JOINTS}
        record = {"policy": policy, "step": len(steps), "phase": phase, "before_out_mm": _mm(view["tool"]["out_mm"]), "before_up_mm": _mm(view["tool"]["up_mm"]),
                  "commanded_out_mm": _mm(planned.out_mm), "commanded_up_mm": _mm(planned.up_mm), "measured_out_mm": _mm(after["tool"]["out_mm"]),
                  "measured_up_mm": _mm(after["tool"]["up_mm"]), **{f"{name}_change_deg": round(value, 2) for name, value in planned.change.items()},
                  "predicted_error_deg": round(max(planned.predicted.values()), 2), "stall_predicted_up_mm": _mm(planned.stall_up_mm),
                  "realized_predicted_up_mm": _mm(planned.model_up_mm[REALIZED]), "further_predicted_up_mm": _mm(planned.model_up_mm[FURTHER]),
                  "joint_error_deg": round(max(abs(value) for value in errors.values()), 2), "candidates_refused": len(planned.refused)}
        ctx.state("REACH_RETRACT_STEP", **record)
        steps.append(record)
        held = _after_step(ctl, view, after, planned.pose, command, phase)
        if held is not None:
            held_by, why, values = held
            _hold(ctx, after, policy, held_by, why, grasp_out_mm, steps, phase=phase, **values)
        view, command = after, planned.pose
    if not steps:
        return view
    ctx.state("REACH_RETRACT", policy=policy, out_before_mm=_mm(before_out), out_after_mm=_mm(view["tool"]["out_mm"]), up_after_mm=_mm(view["tool"]["up_mm"]),
              commanded_up_mm=_mm(_commanded_tool(ctl, view, command)["up_mm"]), steps=len(steps), within_mm=RETRACT_WITHIN_MM)
    return view


def lower_heights_mm(ctl) -> tuple:
    """The release heights a lowering asks for, in order (module docstring, THE SETTLE): the proven one first."""
    heights, height = [], float(ctl.PLACE_HEIGHT_MM)
    while height <= float(ctl.RELEASE_MAX_HEIGHT_MM) - LOWER_HEIGHT_MARGIN_MM + 1e-9:
        heights.append(height)
        height += LOWER_RAISE_STEP_MM
    return tuple(heights) or (float(ctl.PLACE_HEIGHT_MM),)


def lower_out_mm(ctl, carried: Carry) -> float:
    """THE SETTLE, THE OUT (module docstring): the proven lowering's LOWER_OUT_MM past the carry's measured out, never farther
    out than the out the carry's last command named (Carry.last_pose) nor than the planned out plus the same lead."""
    view = carried.view
    outs = [float(view["tool"]["out_mm"]) + LOWER_OUT_MM, float(carried.wanted_out_mm) + LOWER_OUT_MM]
    if carried.last_pose is not None:
        outs.append(_commanded_out_mm(ctl, view, carried.last_pose))
    return min(outs)


def lower(ctx, ctl, carried: Carry) -> Lowering:
    """The lowering (module docstring, THE SETTLE): at lower_out_mm, the lowest height of lower_heights_mm whose lowest link
    is predicted (predicted_lowest_link_m, with lowering_errors of the errors the arm measures against the carry's last
    command) at or above the executor's floor ctl.CONTACT_FLOOR_M and that the gate and the executor's planner take."""
    view, floor = carried.view, float(ctl.CONTACT_FLOOR_M)
    out, errors = lower_out_mm(ctl, carried), lowering_errors(ctl, _errors_against(ctl, carried.view, carried.last_pose))
    refused, under = [], []
    for height in lower_heights_mm(ctl):
        try:
            pose = ctl.proven_orientation_pose(view["measured"], out / 1000.0, height / 1000.0)[0]
        except ctl.ScanRefused as note:
            refused.append((height, str(note)[:160]))
            continue
        lowest = predicted_lowest_link_m(ctl, pose, errors)
        if lowest < floor:  # THE HEIGHT: never sent
            under.append((height, lowest))
            refused.append((height, f"predicted to measure the lowest link at {lowest:+.4f} m, under the executor's floor {floor:+.4f} m (lift errors "
                                    f"{', '.join(f'{name} {value:+.2f}' for name, value in errors.items())} deg)"))
            continue
        try:  # the executor's own planner refuses before anything moves (run_pose raises ScanRefused only then)
            lowered = ctx.run_pose(pose, True, ctl.CONTACT_FLOOR_M, label="PLACE lower")
        except ctl.ScanRefused as note:
            refused.append((height, str(note)[:160]))
            continue
        return Lowering(lowered, height, pose, out, tuple(refused), tuple(under), lowest, errors)
    ctx.state("PLACE_LOWER_REFUSED", reason=refused[-1][1] if refused else "no height to ask for", height_mm=_mm(view["tool"]["up_mm"]),
              heights_refused_mm=[height for height, _reason in refused], lower_out_mm=_mm(out), heights_predicted_under_floor_mm=[height for height, _lowest in under],
              predicted_lowest_link_m=[round(lowest, 4) for _height, lowest in under], contact_floor_m=round(floor, 4),
              lower_errors_deg={name: round(value, 2) for name, value in errors.items()}, lift_growth_deg=LIFT_GROWTH_DEG)
    return Lowering(view, None, None, out, tuple(refused), tuple(under), None, errors)


def hold_check(ctx, ctl, policy: str, view: dict, lowering: Optional[Lowering] = None, lower_skipped: bool = False) -> tuple:
    """THE HOLD CHECK (module docstring): (the view the hold ended on, steady). RELEASE_HOLD also says which lowering height
    was asked for, at which out and with which predicted lowest link, and which were refused before it (by the prediction,
    listed again on their own, or by the planner), or that no lowering was asked for (lower_skipped: the carry ended under
    its floor)."""
    ctl.time.sleep(HOLD_CHECK_S)
    measured = ctx.pose()
    tool = ctl.tool_state(measured)
    holding = ctl.gripper_refusal(measured, True) is None
    drift, rise = float(view["tool"]["out_mm"]) - float(tool["out_mm"]), float(tool["up_mm"]) - float(view["tool"]["up_mm"])
    steady = holding and abs(drift) <= HOLD_DRIFT_MAX_MM and abs(rise) <= HOLD_DRIFT_MAX_MM
    asked = None if lowering is None else lowering.height_mm
    ctx.state("RELEASE_HOLD", policy=policy, hold_s=HOLD_CHECK_S, out_before_mm=_mm(view["tool"]["out_mm"]), out_after_mm=_mm(tool["out_mm"]), drift_mm=_mm(drift),
              rise_mm=_mm(rise), gripper=round(float(measured["gripper"]), 3), holding=holding, steady=steady, max_drift_mm=HOLD_DRIFT_MAX_MM,
              lower_height_mm=None if asked is None else _mm(asked), lower_heights_refused_mm=[] if lowering is None else [height for height, _reason in lowering.refused],
              lower_skipped=bool(lower_skipped), lower_out_mm=None if lowering is None else _mm(lowering.out_mm),
              lower_heights_predicted_under_floor_mm=[] if lowering is None else [height for height, _lowest in lowering.predicted_under],
              lower_predicted_lowest_link_m=None if lowering is None or lowering.predicted_lowest_link_m is None else round(lowering.predicted_lowest_link_m, 4),
              lower_errors_deg=None if lowering is None else {name: round(value, 2) for name, value in lowering.errors.items()})
    ctx.events.predicate("release_hold", steady, measured=max(abs(drift), abs(rise)), threshold=HOLD_DRIFT_MAX_MM, unit="mm", scalars=("drift_mm", "rise_mm", "gripper_readback"),
                         stamp_of=measured, measurements={"drift_mm": drift, "rise_mm": rise, "gripper": float(measured["gripper"]), "holding": holding, "hold_s": HOLD_CHECK_S})
    if not holding:
        raise ctx.failed("reach_release_hold_lost", f"RELEASE_HOLD: the gripper measures {float(measured['gripper']):.2f}, outside its holding band, after the arm was held still; "
                         "nothing is released and no arm move can be commanded with the gripper out of its declared phase", policy=policy, gripper=float(measured["gripper"]),
                         holding_band_min=ctl.GRIPPER_HOLDING_BAND[0], holding_band_max=ctl.GRIPPER_HOLDING_BAND[1], measured_out_mm=float(tool["out_mm"]))
    return {"measured": measured, "tool": tool, "perception": view.get("perception") or []}, steady


def settle(ctx, ctl, policy: str, carried: Carry, grasp_out_mm: float, anchor, direction, advanced_mm: float, seen) -> Settled:
    """THE SETTLE (module docstring): after `carried`, from `anchor` (the SEARCH anchor moved by every measured advance) along
    the reach `direction`."""
    view, required = carried.view, float(ctl.PLACE_REQUIRED_CENTRE_MM)
    travel = float(view["tool"]["out_mm"]) - float(grasp_out_mm)
    margin = float(ctl.release_margin_worst_mm(anchor, direction, travel))
    if margin < required:
        ctx.state("ARM_ONLY_INSUFFICIENT", policy=policy, fk_travel_mm=_mm(travel), worst_case_centre_margin_mm=_mm(margin), carry_stop=carried.stop)
        ctx.events.predicate("arm_only_sufficient", False, measured=margin, threshold=required, unit="mm", anchor=seen, scalars=("fk_travel_from_grasp_mm",),
                             empirical=("VISUAL_ANCHOR_UNCERTAINTY_MM", "FK_TRAVEL_OVERSTATEMENT"), stamp_of=view["measured"])
        return Settled(False, way_back(ctx, ctl, policy, view, grasp_out_mm, carried.last_pose), travel, margin, f"the carry stopped ({carried.stop}) short of the release admission",
                       carried.out_mm, CARRY_SHORT)
    skipped = float(view["tool"]["up_mm"]) < floor_mm(ctl)  # the carry ended under its floor: never lowered further (module docstring)
    lowered = None if skipped else lower(ctx, ctl, carried)
    view, steady = hold_check(ctx, ctl, policy, view if lowered is None else lowered.view, lowered, skipped)
    travel = float(view["tool"]["out_mm"]) - float(grasp_out_mm)
    worst = float(ctl.release_margin_worst_mm(anchor, direction, travel))
    height = float(view["tool"]["up_mm"])
    admitted = worst >= required and height <= ctl.RELEASE_MAX_HEIGHT_MM and steady
    per_edge = placement_envelope.release_edge_margins(ctl, anchor, direction, travel, 0.0)
    side_after = placement_envelope.side_margin_mm(ctl, anchor, direction)
    ctx.state("RELEASE_ADMISSION", admitted=admitted, worst_case_centre_margin_mm=_mm(worst), required_mm=_mm(required), anchor_pad_m=[round(float(v), 4) for v in anchor],
              fk_travel_from_grasp_mm=_mm(travel), base_advance_measured_mm=_mm(advanced_mm), release_height_mm=_mm(height), max_release_height_mm=ctl.RELEASE_MAX_HEIGHT_MM,
              tool=ctl.fmt_tool(view["tool"]), policy=policy, hold_steady=steady, side_margin_after_advance_mm=_mm(side_after),
              **{name: (round(value, 1) if isinstance(value, float) else value) for name, value in per_edge.items()}, evidence=seen)
    ctx.events.predicate("release_margin", worst >= required, measured=worst, threshold=required, unit="mm", anchor=seen, pad_measurements=ctx.events.pad_measurements(True),
                         scalars=("fk_travel_from_grasp_mm", "base_advance_measured_mm"), empirical=("VISUAL_ANCHOR_UNCERTAINTY_MM", "FK_TRAVEL_OVERSTATEMENT"), stamp_of=view["measured"],
                         measurements={"side_margin_after_advance_mm": side_after, **per_edge})
    ctx.events.predicate("release_height", height <= ctl.RELEASE_MAX_HEIGHT_MM, measured=height, threshold=ctl.RELEASE_MAX_HEIGHT_MM, unit="mm", scalars=("tool_up_mm_vs_proven_grasp",),
                         stamp_of=view["measured"])
    ctx.events.predicate("release_admitted", admitted, scalars=("release_margin", "release_height", "release_hold"))
    if admitted:
        return Settled(True, view, travel, worst, None, float(view["tool"]["out_mm"]))
    why, refusal = (("the hold was not steady", HOLD_NOT_STEADY) if not steady
                    else (f"the release height {height:.1f} mm is above {ctl.RELEASE_MAX_HEIGHT_MM:.0f}", RELEASE_TOO_HIGH) if height > ctl.RELEASE_MAX_HEIGHT_MM
                    else (f"the worst-case centre margin {worst:.1f} mm is under {required:.2f}", MARGIN_SHORT))
    last_pose = lowered.pose if lowered is not None and lowered.pose is not None else carried.last_pose  # the way back reads its errors against the last command
    return Settled(False, way_back(ctx, ctl, policy, view, grasp_out_mm, last_pose), travel, worst, f"the release admission refused: {why}", float(view["tool"]["out_mm"]), refusal)


def release_and_place(ctx, ctl, summary: dict, settled: Settled, anchor, direction, advanced_mm: float, rate: float, axis) -> policy_placement.PlacementOutcome:
    """THE RELEASE (module docstring): after an admitted settle. Fills summary's placed, footprint_clearance_mm and
    clearance_target_met, as the proven stage does."""
    travel, worst = settled.travel_mm, settled.worst_mm
    released = ctx.grip(ctl.GRIPPER_OPEN, "open")
    ctx.state("RELEASED", gripper=round(released["gripper"], 3), fk_travel_from_grasp_mm=_mm(travel), tool=ctl.fmt_tool(settled.view["tool"]))
    try:  # back first: the lift joint cannot raise the fully extended arm
        ctx.run_pose(ctl.cartesian_step_pose(released["measured"], -0.025, 0.012, False)[0], False, ctl.CONTACT_FLOOR_M, label="RETREAT back")  # back AND up: never press the pad
    except CycleFailed:  # a refused desktop perception answer holds the arm HERE: it is never a skipped retreat followed by the exit move
        raise
    except (ctl.ScanRefused, RuntimeError) as note:
        print(f"RETREAT back skipped: {note}")
    final = ctx.return_to_low_pose()
    placed = (final["perception"][-1].get("cube_by_template") or {}) if final.get("perception") else {}
    margin = ctl.placed_margin_mm(placed["cube_center_pad_m"]) if placed.get("cube_center_pad_m") else float("nan")
    summary["placed"] = bool(placed.get("inside_pad_outline")) and margin >= ctl.PLACED_MIN_EDGE_MARGIN_MM
    if ctx.target is not None:  # the frame may hold other blocks on the pad: PLACED is the block at THIS run's release
        placed, margin, summary["placed"] = ctx._mission_placed(final, ctl.release_segment_pad(anchor, direction, travel), 0.0)
    summary["footprint_clearance_mm"] = None if math.isnan(margin) else round(margin - ctl.PLACED_MIN_EDGE_MARGIN_MM, 1)
    summary["clearance_target_met"] = summary["placed"] and margin >= ctl.PLACE_REQUIRED_CENTRE_MM
    ctx.state("PLACED", value=summary["placed"], cube_pad_m=placed.get("cube_center_pad_m"), centre_to_edge_mm=None if math.isnan(margin) else round(margin, 1),
              footprint_margin_any_yaw_mm=summary["footprint_clearance_mm"], clearance_target_mm=ctl.PLACE_CLEARANCE_TARGET_MM, clearance_target_met=summary["clearance_target_met"],
              predicted_worst_case_centre_margin_mm=round(worst, 1), **ctx._mission_placed_fields(), evidence=final)
    ctx.events.predicate("placed", summary["placed"], measured=margin, threshold=ctl.PLACED_MIN_EDGE_MARGIN_MM, unit="mm", frames=final, scalars=("cube_centre_to_pad_edge_mm", "inside_pad_outline"))
    ctx.events.predicate("clearance_target_met", summary["clearance_target_met"], measured=margin, threshold=ctl.PLACE_REQUIRED_CENTRE_MM, unit="mm", frames=final, scalars=("cube_centre_to_pad_edge_mm",))
    return policy_placement.PlacementOutcome(advanced=advanced_mm, anchor=anchor, axis=axis, rate=rate, final=final, worst=worst, margin=margin, placed=placed)
